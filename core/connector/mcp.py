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

from core.connector import manifest, prompts, tools, words
from core.logging import get_logger

log = get_logger(__name__)

blueprint = Blueprint("connector_mcp", __name__)

SERVER_NAME = "ownbox"

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
    """What this server does: `tools` and `prompts`. No resources, and no `listChanged` on either.

    `listChanged: true` would promise notifications we cannot send: there is no subscription
    stream here (see the GET note above), so advertising it is advertising a doorbell with no
    wire behind it.

    PROMPTS ARE THE READY-MADE ASKS (core/connector/prompts.py; the owner's ruling D3 of
    docs/SCOPE_SMART_BOX_ANSWERS.md, 2026-10-02): *Morning brief*, *What needs me today* and the
    rest, in the menu of the buyer's own AI.
    """
    return {"tools": {}, "prompts": {}}


# WHAT EVERY CONNECTED AI IS TOLD, in `server/discover` and in `initialize` (a client built before the
# current spec reads it there). VENDOR-FACING TEXT, so it must stay true after C1 landed the first
# propose_* tool. The first draft said "every tool is read-only", a sentence with an expiry date, and the
# kind that is never revisited because nothing breaks when it goes stale. This says the thing that does not
# change: a tool may PROPOSE, a person approves, nothing on this box sends or spends by itself. (OSDev1, gate
# review of #1122.)
#
# AND HOW TO ANSWER. Owner, 2026-10-02, handed raw stored numbers by his own AI: *"This is not an AI business
# machine. This is a dumb box."* Every tool's text is now written to be read (core/connector/words.py), and this
# asks the AI to keep it that way: lead with what matters and why, never show raw fields, and always offer what
# to ask next and what the box can start.
INSTRUCTIONS = ("This is one business's own Ownbox. tools/list says what it can answer. Every tool is typed: "
                "reads return the box's state, and actions are proposals a person on the box approves; no tool "
                "sends, publishes or spends on its own. Answer the person in plain words: lead with what matters "
                "most and why, with the box's real numbers and the full link to the page each comes from. Never "
                "show raw fields, ids or JSON: every answer carries `in_words`, already written for the person, so show "
                "that as it is, and use the rest of the data for your own work. Always end by offering what to ask next (questions this box can "
                "answer) and what the box can start for them to approve on Approvals. Ready-made asks are in "
                "prompts/list: a morning brief, what needs them today, who to follow up with, how their websites "
                "are doing and what to write next.")


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
    # THE AI APPS' OWN PAGES, BY NAME (core/connector/cors.py): ChatGPT asks from chatgpt.com. Every other origin is
    # still refused, which is what the spec's DNS-rebinding rule protects.
    from core.connector import cors
    return origin.rstrip("/") == req.host_url.rstrip("/") or cors.trusted(origin)


def _version_ok(req) -> tuple[bool, str]:
    """(ok, version). An unsupported MCP-Protocol-Version MUST be a 400, per the spec."""
    got = req.headers.get("MCP-Protocol-Version")
    if not got:
        return True, _ASSUMED_VERSION
    return (got in SUPPORTED_VERSIONS), got


def _ok(rpc_id, result: dict) -> dict:
    """Every success response this server sends. The ONLY place a JSON-RPC `result` is built.

    `resultType: "complete"` IS STAMPED HERE AND NOWHERE ELSE, because the spec makes it mandatory
    and the earlier arrangement proved how that goes wrong: each method wrote its own envelope,
    two remembered the field and two did not. One of the two was `initialize` — the HANDSHAKE — so
    a client that enforces the field never connected at all, and the buyer saw "cannot connect to
    your box" with no reason given. Claude does not enforce it, which is why nobody noticed until
    the owner relayed it on 2026-09-23 ahead of connecting Grok.

    A field that has to be on every response belongs in the one function that builds every
    response; a field repeated across four dict literals is a field waiting for the fifth.
    tests/test_every_mcp_result_says_complete.py fails if any other function builds a `result`.

    Stamped LAST, so a method cannot overwrite it by accident — and additive, so it never
    disturbs the payload beside it. Clients that predate the field ignore an unknown key.
    """
    return {"jsonrpc": "2.0", "id": rpc_id, "result": {**result, "resultType": "complete"}}


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
    if isinstance(spec.get("input_schema"), dict):          # a connected app's own schema, untouched
        return {**spec["input_schema"], "type": "object"}   # never an app's own other type
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
    # THE TITLE IS WHAT THE OWNER READS ON HIS AI'S PERMISSION SCREEN (owner, 2026-10-01). Sent in
    # both places the spec has had one: `title` on the tool (2025-06-18) and `annotations.title`
    # (2025-03-26), so a client on either version shows the plain words, not the id.
    entry = {"name": spec["name"],
             "title": spec["title"],
             "description": spec["description"],
             "inputSchema": _input_schema(spec),
             "annotations": {"title": spec["title"], **tools.annotations_for(spec["capability"])}}
    if spec.get("output"):
        entry["outputSchema"] = spec["output"]
    # EVERY TOOL SAYS IT NEEDS THE OWNER'S SIGN-IN. ChatGPT reads `securitySchemes` per tool to decide that a tool
    # wants OAuth and to offer its sign-in (developers.openai.com/apps-sdk/build/auth, 2026-10-03). Every tool here
    # does: there is no anonymous tool on a box. Other clients ignore a field they don't know.
    entry["securitySchemes"] = [{"type": "oauth2", "scopes": []}]
    return entry


ERROR_META = "io.ownbox/error"


def _words(spec: dict | None, result, seat: dict | None) -> str | None:
    """The tool's answer in words (its `render`), or None to send the result as JSON, as before.

    A RENDER THAT FAILS NEVER COSTS THE ANSWER. It raised, or wrote nothing: the AI gets the JSON it always
    got, and the box says which tool's words broke, never the result (a result is a customer's data).
    """
    render = (spec or {}).get("render")
    if render is None or not isinstance(result, dict):
        return None
    try:
        with words.seat_scope(seat):
            text = render(result)
    except Exception as e:                                  # noqa: BLE001 — the JSON still answers
        log.warning("connector.render_failed", tool=(spec or {}).get("name"), error=type(e).__name__)
        return None
    if not isinstance(text, str) or not text.strip():
        log.warning("connector.render_empty", tool=(spec or {}).get("name"))
        return None
    return text


IN_WORDS = "in_words"                                   # the answer in words, first in structuredContent


def _tool_result(payload: dict, status: int, spec: dict | None = None, seat: dict | None = None) -> dict:
    """Map one `tools.call()` answer onto an MCP result.

    THE TEXT IS THE ANSWER IN WORDS when the tool has a `render`, and the result still travels whole in
    `structuredContent` for the AI to work with. Owner, 2026-10-02, after his own AI handed him the Morning
    Review as the serialised record this used to send: *"This is not an AI business machine. This is a dumb
    box."* A tool without a render (a connected app's, a machine of the owner's own) answers as it always did.

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
        text = _words(spec, result, seat)
        data = result
        if text is None:
            text = json.dumps(result, default=str)
        elif isinstance(result, dict):
            # THE WORDS RIDE IN THE DATA TOO, FIRST. Measured on the owner's box on 2026-10-02 (release .12): Claude's
            # apps hand the model `structuredContent` and drop the text beside it, so the words never reached the AI
            # and the daily check graded every answer "raw JSON". No tool declares an outputSchema, so the extra key
            # breaks no contract; `in_words` is a name no result uses (core.ask already has an `answer`).
            data = {IN_WORDS: text, **{k: v for k, v in result.items() if k != IN_WORDS}}
        return {"content": [{"type": "text", "text": text}],
                "structuredContent": data,
                # A CONNECTED APP'S OWN "that failed" (core/connections/gateway.py) stays a tool error, so the
                # coworker reads the app's words and can correct itself. Opt-in by a key no shipped tool uses.
                "isError": isinstance(result, dict) and result.get("app_error") is True}
    if status == 200 and payload.get("not_configured"):
        # A shipped box's most common state: the machine is here, the credential is not.
        return {"content": [{"type": "text", "text": payload.get("message", "not connected yet")}],
                "isError": True, "_meta": {ERROR_META: "not_configured"}}
    # THE ERROR'S CODE TRAVELS WITH ITS WORDS, in `_meta` (which the spec keeps for exactly this), so a program can
    # tell a tool that answered "no such id" from one that crashed ("tool_failed") without reading the sentence.
    # The box's own self-test (core/key_features.py) is the first reader; a model reads the text as before.
    return {"content": [{"type": "text",
                         "text": payload.get("message") or payload.get("error") or "failed"}],
            "isError": True, "_meta": {ERROR_META: str(payload.get("error") or "failed")[:60]}}


def _handle(method: str, params: dict, rpc_id, seat: dict) -> dict | None:
    """One JSON-RPC method. Returns a response body, or None for a notification."""
    if method == "server/discover":
        # The current spec makes this mandatory. One request gets a client our identity, our
        # capabilities and every version we speak, so it never has to probe.
        return _ok(rpc_id, {
            "supportedVersions": list(SUPPORTED_VERSIONS),
            "capabilities": _capabilities(),
            "_meta": {"io.modelcontextprotocol/serverInfo": {
                "name": SERVER_NAME, "version": tools.CONTRACT_VERSION}},
            "instructions": INSTRUCTIONS,
        })

    if method == "initialize":
        # The older handshake, answered from the same facts. A client that speaks only this still
        # works, which is what neutrality means in practice.
        want = (params or {}).get("protocolVersion")
        agreed = want if want in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
        return _ok(rpc_id, {
            "protocolVersion": agreed,
            "capabilities": _capabilities(),
            "serverInfo": {"name": SERVER_NAME, "version": tools.CONTRACT_VERSION},
            # Optional in InitializeResult since 2025-03-26, and the only place a client built before
            # server/discover looks for it.
            "instructions": INSTRUCTIONS,
        })

    if method == "initialized" or method.startswith("notifications/"):
        # EVERY NOTIFICATION, not only the handshake's: a client also sends notifications/cancelled and
        # notifications/progress. Each answered "unknown method" in a 200 body until 2026-10-02.
        return None                                   # a notification: accepted, nothing to say

    if method == "ping":
        # THE SPEC'S LIVENESS CHECK: a client may send it at any time, and it MUST get an empty result.
        # It answered "unknown method" until 2026-10-02 (OSDev4's audit for the connector strike, the
        # night the owner's own Claude app said "Couldn't reload tools from the server").
        return _ok(rpc_id, {})

    if method in ("tools/list", "tools/call"):
        # THE OWNER'S CONNECTED APPS, current as of this request (core/connections/gateway.py): a connection
        # added or turned off on Data sources is seen here without a restart. Never raises.
        try:
            from core.connections import gateway as _gateway
            _gateway.refresh()
        except Exception:                                   # noqa: BLE001
            log.exception("connections.refresh_failed")

    if method == "tools/list":
        # CAPABILITY-FILTERED, which the spec explicitly permits: the tool set "MAY vary by the
        # authorization presented on the request ... since credentials are per-request input, not
        # connection state." A seat never sees a tool it cannot call.
        #
        # visible_to() returns them sorted. The spec asks for a stable order because clients cache
        # the list; iterating a dict and hoping is how that silently stops being true.
        entries = [_tool_entry(s) for s in tools.visible_to(seat)]
        return _ok(rpc_id, {"tools": entries})

    if method == "tools/call":
        return _call_tool(params or {}, rpc_id, seat)

    # AN EMPTY LIST, NEVER "UNKNOWN METHOD", for the lists a client asks for on every reload. We offer no resources (see
    # _capabilities; prompts are the ready-made asks, below), but some clients ask anyway, and a JSON-RPC error there can fail the whole reload:
    # with release .10 (ping fixed) the owner's Claude app still said "Couldn't reload tools from the server" while
    # listing all 31 tools (2026-10-02). An empty list is a true answer; an error reads as a broken server.
    if method in _EMPTY_LISTS:
        return _ok(rpc_id, {_EMPTY_LISTS[method]: []})

    if method == "prompts/list":
        # THE READY-MADE ASKS THIS SEAT CAN ANSWER, sorted (core/connector/prompts.py). One page: there are a
        # handful, so `nextCursor` is never sent and a cursor a client sends back is simply the first page.
        return _ok(rpc_id, {"prompts": prompts.listed(seat)})

    if method == "prompts/get":
        return _get_prompt(params or {}, rpc_id, seat)

    return _err(rpc_id, _METHOD_NOT_FOUND, f"unknown method: {method}")


_EMPTY_LISTS = {"resources/list": "resources", "resources/templates/list": "resourceTemplates"}


def _get_prompt(params: dict, rpc_id, seat: dict) -> dict:
    """One ready-made ask as its message. The spec's own error for a name it doesn't know is -32602 (Invalid
    params), and an ask this seat can't answer is one it doesn't know: listing hides it, so getting it does too."""
    name = params.get("name")
    if not name or not isinstance(name, str):
        return _err(rpc_id, _INVALID_PARAMS, "prompts/get needs a prompt name")
    args = params.get("arguments")
    if args is not None and not isinstance(args, dict):
        return _err(rpc_id, _INVALID_PARAMS, "arguments must be an object")
    got = prompts.message(name, seat)
    if got is None:
        return _err(rpc_id, _INVALID_PARAMS, f"unknown prompt: {name}", data={"prompt": name})
    # NONE OF THESE ASKS TAKES ARGUMENTS, and an unknown one is refused rather than ignored, as a tool's is
    # (core/connector/tools.py validate): ignoring it is how a caller believes it asked for something it didn't.
    if args:
        return _err(rpc_id, _INVALID_PARAMS, f"{name} takes no arguments", data={"prompt": name})
    return _ok(rpc_id, got)


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

    # THE SPEC `call()` RAN, by the name the client sent (old spelling included), so its `render` writes the
    # answer; and the seat, so the answer offers only what this connection may ask for.
    return _ok(rpc_id, _tool_result(payload, status, tools.lookup(name), seat))


@blueprint.get("/mcp")
@blueprint.get("/api/v1/mcp")
def mcp_get():
    """No server-initiated stream. 405 is the spec's own answer for exactly this case."""
    return jsonify({"error": "method_not_allowed",
                    "message": "this MCP endpoint has no server-initiated stream; POST "
                               "JSON-RPC instead"}), 405


# THE SAME HANDLER AT THE SHORT ADDRESS. Owner, 2026-09-21: the address a buyer hands their
# assistant should read `https://<slug>.ownbox.app/mcp`. Deliberately NOT a redirect — an MCP
# client POSTs JSON-RPC, and a 307 across a POST is honoured inconsistently and drops the
# Authorization header in some stacks. One handler, two paths, one gate: `core/dispatch`
# `_SEAT_PATHS` carries `/mcp`, so this is refused without a seat exactly as `/api/v1/mcp` is.
@blueprint.post("/mcp")
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
