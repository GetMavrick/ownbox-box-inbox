"""The HTTP transport. One blueprint under /api/, which core/dispatch.py's gate already shuts.

MOUNTED AS CORE, directly, the way the dash shell is — not through `web_modules:`. So no shipped
box needs a config edit for the door to exist, and there is no box where somebody has to remember
to turn the connector on before it can refuse people properly. It is shut by the seat gate until
somebody deliberately mints a seat.

THIS FILE HOLDS NO POLICY. Authentication happened in the gate; authorisation, validation, the
audit row and the not_configured state all live in tools.call(), because the MCP adapter is a
second transport over the same functions and anything decided here would have to be decided twice.
If the MCP adapter ever needs a function to change shape, the shape was wrong — that is the design
check, and it is why both adapters land against one definition.
"""
from flask import Blueprint, abort, g, jsonify, request

from core.connector import manifest, tools
from core.logging import get_logger

log = get_logger(__name__)

blueprint = Blueprint("connector", __name__)


def _seat() -> dict:
    """The seat the gate already verified. Never re-derived, and NEVER invented.

    A transport that re-reads the credential is a transport that can disagree with the gate about
    who is calling, and the gate is the one with the audit trail.

    NO FALLBACK SEAT — this refuses instead, and that is the whole point of the function. The
    first version returned `{"id": "unknown", "role": "read"}` when `g.seat` was missing, which
    FABRICATED A VALID READ SEAT: a read seat may call every Tier 1 tool, so a missing identity
    became a working one. Unreachable today, because `_auth_gate` covers the whole /api/ prefix —
    and latent exactly until somebody mounts a connector route outside that prefix or narrows it,
    at which point read access is granted silently and no test goes red.

    It is also the inverse of step 1's stated principle, "a door with no lock fitted yet stays
    closed", in the file that exists to serve that door. And it poisoned the ledger: a row
    attributed to `unknown` is not an answer to the only question an audit trail is ever asked.

    A missing `g.seat` is an invariant violation, not a default. Caught by OSDev1 reviewing #1101.
    """
    seat = getattr(g, "seat", None)
    if not seat:
        # The gate is the only thing that may create a seat. If it did not run, nothing here may
        # proceed on a guess.
        log.error("connector.seat_missing_on_request", path=request.path)
        abort(401)
    return seat


@blueprint.get("/api/v1/manifest")
def get_manifest():
    """What this box can answer. Capability, never data — safe for the lowest role.

    A THIN SHELL OVER THE REGISTRY, not a second way to run the same tool. The first version
    called `manifest.build()` directly and hand-wrote its own audit row, which gave one tool two
    execution paths with different authorisation and different auditing — the exact drift the
    registry exists to prevent, reintroduced one file below it. This module's own docstring says
    it holds no policy because anything decided here would have to be decided twice; audit is
    policy. Caught by OSDev1 reviewing #1101.
    """
    payload, status = tools.call(manifest.TOOL, None, _seat())
    # THROUGH the registry for authorisation and audit, but the GET returns the manifest ITSELF
    # rather than call()'s {"tool", "result"} envelope. This is a discovery document fetched at
    # its own URL: a client asking "what is this box" should get the box, not a wrapper it has to
    # unwrap. POST /api/v1/call with tool=manifest still returns the envelope, because there the
    # envelope IS the contract — same function, same audit row, each transport in its own shape.
    if status == 200 and "result" in payload:
        return jsonify(payload["result"]), status
    return jsonify(payload), status


@blueprint.get("/api/v1/tools")
def list_tools():
    """The tool list alone, for a client that does not want the whole manifest.

    Also through `call()`. Enumerating a box's capabilities is precisely the probe a ledger
    should show, and the first version wrote no row at all.
    """
    payload, status = tools.call(manifest.TOOL, None, _seat())
    if status != 200 or "result" not in payload:
        return jsonify(payload), status
    return jsonify({"tools": payload["result"]["tools"]}), status


@blueprint.post("/api/v1/call")
def post_call():
    """Invoke one named tool with typed arguments. Never free text — that is the whole design."""
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "bad_body", "message": "send a JSON object"}), 400
    name = body.get("tool")
    if not name or not isinstance(name, str):
        return jsonify({"error": "no_tool",
                        "message": "name the tool to call; GET /api/v1/manifest lists them"}), 400
    payload, status = tools.call(name, body.get("args"), _seat())
    return jsonify(payload), status
