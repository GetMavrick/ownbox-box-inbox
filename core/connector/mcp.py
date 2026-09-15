"""The MCP transport — a second front over the same registry, never a second brain.

WHY A SECOND TRANSPORT AT ALL. `http.py` is our own shape and it is fine for a script. The
coworkers a buyer actually owns — Merit, and any AI coworker with a "connect an MCP server" box —
speak Model Context Protocol and nothing else. This file is the adapter, and it is deliberately
thin: authorisation, validation, the audit row and the not_configured state all stay in
`tools.call()`, exactly as `http.py`'s docstring promised when it said a second transport would
land against one definition. **If this file ever needs a tool to change shape, the shape was
wrong.** That is the design check.

THREE GENERATIONS OF THIS PROTOCOL ARE IN THE MARKET, and being neutral means answering all of
them (docs/SPEC_AIOS_MCP_SERVER.md §3). Vendors shipped clients against whichever spec was current
when they built:

    2024-11-05    HTTP+SSE, two endpoints, `initialize`      deprecated, but it is what a UI
                                                             placeholder reading `/sse` implies
    2025-03-26 →  Streamable HTTP, one endpoint, `initialize` superseded
    2025-11-25
    2026-07-28    Streamable HTTP, `server/discover`,         CURRENT
                  version per-request in `_meta`

So this answers BOTH `server/discover` (mandatory in the current spec) and `initialize` (every
client built before it) from the same facts. A client that can only do one of them still works.

NO SESSIONS, AND THAT IS A DECISION. `Mcp-Session-Id` is optional for a server. Every tool here is
idempotent and stateless, so a session would buy nothing and add a 404-and-reinitialise failure
mode. Stated here so nobody adds one later for symmetry.

GET RETURNS 405, HONESTLY. The spec lets a server either open an SSE stream on GET or answer 405
Method Not Allowed. We have no server-initiated messages to push, and a stream that never carries
one is a socket a client holds open forever waiting for news that is not coming.
"""
import json

from flask import Blueprint, g, jsonify, request

from core.connector import manifest, tools
from core.logging import get_logger

log = get_logger(__name__)

blueprint = Blueprint("connector_mcp", __name__)

SERVER_NAME = "aios"

# Every generation we actually serve, newest first. A client negotiates DOWN to one of these
# rather than failing, which is the whole reason the list is not a single string.
#
# 2024-11-05 IS DELIBERATELY ABSENT UNTIL A2. That generation is not merely an older handshake —
# it is the HTTP+SSE transport, two endpoints, and this box serves one. Listing it would tell a
# 2024 client to speak a shape we do not answer, which is worse than not listing it: it fails
# after connecting rather than before. It goes back in the same commit that lands `/sse`.
# (OSDev1, gate review of #1122.)
SUPPORTED_VERSIONS = ("2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26")

# The spec's own fallback: with no MCP-Protocol-Version header and no other way to know, assume
# this. Not the newest — assuming the newest would silently promise a client features it never
# asked for.
_ASSUMED_VERSION = "2025-03-26"

# JSON-RPC codes, from the spec. Protocol errors only: a tool that fails for a business reason is
# a RESULT with isError, never one of these (see _tool_result).
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602


def _capabilities() -> dict:
    """What this server does. `tools` only — no resources, no prompts, and no `listChanged`.

    `listChanged: true` would promise notifications we cannot send: there is no subscription
    stream here (see the GET note above), so advertising it is advertising a doorbell with no
    wire behind it.
    """
    return {"tools": {}}


def _origin_ok(req) -> bool:
    """Reject a cross-origin browser request — the spec REQUIRES this.

    *"Servers MUST validate the Origin header on all incoming connections to prevent DNS
    rebinding attacks."* Our boxes are internet-facing, so this is not theoretical: without it a
    page a buyer visits could drive their box through their own browser.

    NO Origin AT ALL IS ALLOWED, and that is not a hole. Origin is set by browsers; the clients
    this transport exists for are servers — Merit, a coworker's backend, curl — and none of them
    send one. A same-origin request is fine; a cross-origin one is refused.

    THE SCHEME COMPARISON IS CORRECT BEHIND CADDY, and this note exists so nobody loosens it.
    The worry is reasonable on its face — gunicorn is reached over http, a browser's Origin is
    https, so `host_url` looks like it would disagree. It does not: `core/dispatch.py:37` applies
    `ProxyFix(..., x_proto=1)`, so `host_url` is built from X-Forwarded-Proto and reads `https`.
    MEASURED 2026-09-13 with the proxy headers Caddy sends: same-origin https → 200,
    cross-origin → 403. Both cases are asserted in tests/test_connector_mcp.py.

    So if this ever appears to reject a legitimate request, the bug is upstream — a proxy not
    sending X-Forwarded-Proto — and the fix is there. Comparing only the host, or dropping the
    check, turns a working guard into the DNS-rebinding hole the spec requires it to close.
    """
    origin = req.headers.get("Origin")
    if not origin:
        return True
    return origin.rstrip("/") == req.host_url.rstrip("/")


def _version_ok(req) -> tuple[bool, str]:
    """(ok, version). An unsupported MCP-Protocol-Version MUST be a 400, per the spec."""
    got = req.headers.get("MCP-Protocol-Version")
    if not got:
        return True, _ASSUMED_VERSION
    return (got in SUPPORTED_VERSIONS), got


def _err(rpc_id, code: int, message: str, data=None) -> dict:
    body = {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}
    if data is not None:
        body["error"]["data"] = data
    return body


def _input_schema(spec: dict) -> dict:
    """The registry's small arg dict, as JSON Schema.

    A tool with NO arguments gets `additionalProperties: false` rather than a bare object — the
    spec recommends it, and it is the difference between "takes nothing" and "takes anything",
    which is exactly the ambiguity that makes a model invent a parameter.
    """
    props, required = {}, []
    for arg, decl in (spec.get("args") or {}).items():
        props[arg] = {"type": decl["type"]}
        if decl.get("description"):
            props[arg]["description"] = decl["description"]
        if decl.get("required"):
            required.append(arg)
    schema = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        schema["required"] = sorted(required)
    return schema


def _tool_entry(spec: dict) -> dict:
    """One registry spec as an MCP tool definition.

    ANNOTATIONS ARE NOT DECORATION. Unspecified, `destructiveHint` and `openWorldHint` both
    default to TRUE, so an unannotated read-only report reads to every client as a destructive
    open-world call and earns a confirmation prompt on the most common thing we serve. They are
    derived from the capability rather than written per tool, so one tool cannot disagree with
    its neighbour.

    They are also UX, never enforcement: the spec says clients must treat annotations from an
    untrusted server as untrusted. What actually stops a seat is `visible_to()` and `tools.call()`.
    """
    entry = {"name": spec["name"],
             "description": spec["description"],
             "inputSchema": _input_schema(spec),
             "annotations": tools.annotations_for(spec["capability"])}
    if spec.get("output"):
        entry["outputSchema"] = spec["output"]
    return entry


def _tool_result(payload: dict, status: int) -> dict:
    """Map one `tools.call()` answer onto an MCP result.

    THE ERROR SPLIT IS THE POINT, and getting it backwards is how a coworker tells a customer the
    wrong thing. The spec draws it by who can act on the failure:

      · a PROTOCOL error is for the client — unknown tool, malformed request. The caller is
        handled by `_call_tool`, not here.
      · a TOOL EXECUTION error is `isError: true` with readable text, because it *"contains
        actionable feedback that language models can use to self-correct."*

    So a rail that is not connected comes back as a normal result saying so. Returned as a
    protocol error it would read to the coworker as "this server is broken"; returned this way it
    reads as "connect Google in Settings", which is a thing the customer can do.
    """
    if status == 200 and "result" in payload:
        result = payload["result"]
        return {"content": [{"type": "text", "text": json.dumps(result, default=str)}],
                "structuredContent": result,
                "isError": False}
    if status == 200 and payload.get("not_configured"):
        # A shipped box's most common state: the machine is here, the credential is not.
        return {"content": [{"type": "text", "text": payload.get("message", "not connected yet")}],
                "isError": True}
    return {"content": [{"type": "text",
                         "text": payload.get("message") or payload.get("error") or "failed"}],
            "isError": True}


def _handle(method: str, params: dict, rpc_id, seat: dict) -> dict | None:
    """One JSON-RPC method. Returns a response body, or None for a notification."""
    if method == "server/discover":
        # The current spec makes this mandatory. One request gets a client our identity, our
        # capabilities and every version we speak, so it never has to probe.
        return {"jsonrpc": "2.0", "id": rpc_id, "result": {
            "resultType": "complete",
            "supportedVersions": list(SUPPORTED_VERSIONS),
            "capabilities": _capabilities(),
            "_meta": {"io.modelcontextprotocol/serverInfo": {
                "name": SERVER_NAME, "version": tools.CONTRACT_VERSION}},
            # VENDOR-FACING TEXT, so it must stay true after C1 lands the first propose_* tool.
            # The first draft said "every tool is read-only" — a sentence with an expiry date, and
            # the kind that is never revisited because nothing breaks when it goes stale. This says
            # the thing that does not change: a tool may PROPOSE, a human approves, nothing on this
            # box sends or spends by itself. (OSDev1, gate review of #1122.)
            "instructions": "One AIOS box. Call tools/list for what this box can answer. Every "
                            "tool is typed: reads return the box's state, and actions are "
                            "proposals a human on the box approves — no tool sends, publishes or "
                            "spends on its own.",
        }}

    if method == "initialize":
        # The older handshake, answered from the same facts. A client that speaks only this still
        # works, which is what neutrality means in practice.
        want = (params or {}).get("protocolVersion")
        agreed = want if want in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
        return {"jsonrpc": "2.0", "id": rpc_id, "result": {
            "protocolVersion": agreed,
            "capabilities": _capabilities(),
            "serverInfo": {"name": SERVER_NAME, "version": tools.CONTRACT_VERSION},
        }}

    if method in ("notifications/initialized", "initialized"):
        return None                                   # a notification: accepted, nothing to say

    if method == "tools/list":
        # CAPABILITY-FILTERED, which the spec explicitly permits: the tool set "MAY vary by the
        # authorization presented on the request ... since credentials are per-request input, not
        # connection state." A seat never sees a tool it cannot call.
        #
        # visible_to() returns them sorted. The spec asks for a stable order because clients cache
        # the list; iterating a dict and hoping is how that silently stops being true.
        entries = [_tool_entry(s) for s in tools.visible_to(seat)]
        return {"jsonrpc": "2.0", "id": rpc_id,
                "result": {"resultType": "complete", "tools": entries}}

    if method == "tools/call":
        return _call_tool(params or {}, rpc_id, seat)

    return _err(rpc_id, _METHOD_NOT_FOUND, f"unknown method: {method}")


def _call_tool(params: dict, rpc_id, seat: dict) -> dict:
    name = params.get("name")
    if not name or not isinstance(name, str):
        return _err(rpc_id, _INVALID_PARAMS, "tools/call needs a tool name")
    args = params.get("arguments")
    if args is not None and not isinstance(args, dict):
        return _err(rpc_id, _INVALID_PARAMS, "arguments must be an object")

    payload, status = tools.call(name, args, seat)

    # UNKNOWN TOOL IS THE ONE PROTOCOL ERROR HERE. The spec names it as such, and it is the
    # honest split: a model cannot self-correct its way onto a tool this box does not have, so
    # handing it back as a retryable result would invite exactly that.
    if status == 404 and payload.get("error") == "unknown_tool":
        return _err(rpc_id, _INVALID_PARAMS, payload.get("message", f"unknown tool: {name}"),
                    data={"tool": name})

    return {"jsonrpc": "2.0", "id": rpc_id, "result": _tool_result(payload, status)}


@blueprint.get("/api/v1/mcp")
def mcp_get():
    """No server-initiated stream. 405 is the spec's own answer for exactly this case."""
    return jsonify({"error": "method_not_allowed",
                    "message": "this MCP endpoint has no server-initiated stream; POST "
                               "JSON-RPC instead"}), 405


@blueprint.post("/api/v1/mcp")
def mcp_post():
    """One JSON-RPC message in, one out. The seat was verified by the /api/ gate.

    `g.seat` is set by `core/dispatch.py`'s gate, which already refused anything without a valid
    seat credential before this function ran. It is NEVER re-derived here and NEVER invented — a
    transport that re-reads the credential is one that can disagree with the gate about who is
    calling, and the gate is the one holding the audit trail.
    """
    if not _origin_ok(request):
        return jsonify({"error": "forbidden_origin",
                        "message": "cross-origin requests are refused"}), 403

    ok, version = _version_ok(request)
    if not ok:
        return jsonify({"error": "unsupported_protocol_version",
                        "message": f"this box speaks {', '.join(SUPPORTED_VERSIONS)}",
                        "supported": list(SUPPORTED_VERSIONS)}), 400

    seat = getattr(g, "seat", None)
    if not seat:
        # Belt and braces behind the gate. Never a fallback seat: refusing is the point.
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        return jsonify(_err(None, _PARSE_ERROR, "send one JSON-RPC object")), 400
    if body.get("jsonrpc") != "2.0":
        return jsonify(_err(body.get("id"), _INVALID_REQUEST, "jsonrpc must be '2.0'")), 400
    method = body.get("method")
    if not method or not isinstance(method, str):
        return jsonify(_err(body.get("id"), _INVALID_REQUEST, "method is required")), 400

    rpc_id = body.get("id")
    out = _handle(method, body.get("params") or {}, rpc_id, seat)
    if out is None:
        # A JSON-RPC notification. The spec: the server MUST return 202 Accepted with no body.
        return "", 202
    log.info("connector.mcp", method=method, seat=seat["id"], version=version)
    return jsonify(out), 200
