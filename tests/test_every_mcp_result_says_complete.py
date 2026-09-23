"""Every MCP result this box sends carries `resultType: "complete"` — the handshake included.

THE DEFECT THIS EXISTS FOR, relayed by the owner and measured by OSDev1 on 2026-09-23:
core/connector/mcp.py stamped the field on `server/discover` and `tools/list` but NOT on
`initialize` or `tools/call` — inconsistent with its own comment, which says the current spec
makes it mandatory. `initialize` is the HANDSHAKE. A client that enforces the field never
connects at all, and the buyer sees "cannot connect to your box" with no reason given. Claude
does not enforce it, which is exactly why nobody saw it; the owner connects Grok tomorrow.

WHY TWO KINDS OF CHECK, and the second one is the point:

  BEHAVIOURAL — drive every method a real client calls, over HTTP, with a real seat, and read the
  field off each response. This catches today's defect. It cannot catch tomorrow's: a method
  added next month is not in this list, so it is not checked, so it can ship without the field
  exactly the way `initialize` did.

  STRUCTURAL — every success envelope in mcp.py is built by ONE function, and the parsed source
  is checked for any other place that builds a JSON-RPC `result`. A new method that hand-writes
  its own `{"result": ...}` fails HERE, by name and line, before it can reach a client. That is
  what "the next path cannot forget" has to mean; a list of paths can only remember the ones
  somebody thought of.

Asserted on the AST, never on text: this repo has a guard that matched its own docstring and
passed vacuously three times.

Run: python tests/test_every_mcp_result_says_complete.py
"""
import ast
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = tempfile.mkdtemp() + "/mcp_complete.db"
os.environ["DISPATCH_BEARER_TOKEN"] = "the-wide-token"

from core import state  # noqa: E402

state.init_db()

from core.connector import mcp, seats, tools  # noqa: E402
from core import dispatch  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


# One tool per branch of the tools/call mapping, so every way a call can come back is exercised:
# a real answer, a machine that is here but not connected, and a call that fails validation.
tools.register("complete_probe_ok", fn=lambda city: {"city": city}, machine="test",
               capability="read:reports", description="Answers.",
               args={"city": {"type": "string", "required": True, "description": "City."}})
tools.register("complete_probe_unplugged", machine="test", capability="read:reports",
               description="Not connected.",
               fn=lambda: tools.NotConfigured("no key for this on the box"))

_sid, _cred = seats.mint("resultType probe", "read")
app = dispatch.app.test_client()
AUTH = {"Authorization": f"Bearer {_cred}"}


def rpc(method, params=None):
    body = {"jsonrpc": "2.0", "id": 7, "method": method}
    if params is not None:
        body["params"] = params
    return app.post("/api/v1/mcp", json=body, headers=AUTH).get_json()


def stamped(label, resp):
    result = (resp or {}).get("result")
    ok(label, isinstance(result, dict) and result.get("resultType") == "complete",
       f"result={result!r}")


# ── STRUCTURAL FIRST, because a behavioural failure above it would abort before it ran ────
print("\n— the structure: one builder, and nothing builds a result around it —")

_tree = ast.parse((ROOT / "core/connector/mcp.py").read_text())
_builder = getattr(mcp, "_ok", None)
ok("mcp.py has ONE function that builds every success response", callable(_builder))

# Every dict literal carrying a "result" key, and the function it sits in. The builder is the
# only function allowed to hold one.
_owner_of = {}
for fn in ast.walk(_tree):
    if isinstance(fn, ast.FunctionDef):
        for node in ast.walk(fn):
            if isinstance(node, ast.Dict) and any(
                    isinstance(k, ast.Constant) and k.value == "result" for k in node.keys):
                _owner_of.setdefault(fn.name, []).append(node.lineno)
_elsewhere = {f: lines for f, lines in _owner_of.items() if f != "_ok"}
ok("NO OTHER FUNCTION hand-builds a {\"result\": ...} envelope — a new method cannot skip the stamp",
   not _elsewhere, f"built outside _ok: {_elsewhere}")
ok("...and the builder really does build one, so the check above is not passing on nothing",
   bool(_owner_of.get("_ok")), str(_owner_of))


# ── BEHAVIOURAL: every method a real client sends ─────────────────────────────────────────
print("\n— the handshake, which is the one that decides whether a client connects at all —")

for v in mcp.SUPPORTED_VERSIONS:
    stamped(f"initialize agreeing on {v}", rpc("initialize", {"protocolVersion": v}))
stamped("initialize with a version we do not speak (falls back, still stamped)",
        rpc("initialize", {"protocolVersion": "1999-01-01"}))
stamped("initialize with no params at all", rpc("initialize"))
stamped("server/discover", rpc("server/discover"))

print("\n— listing and calling —")
stamped("tools/list", rpc("tools/list"))
stamped("tools/call — a real answer",
        rpc("tools/call", {"name": "aios.test.complete_probe_ok", "arguments": {"city": "Oslo"}}))
stamped("tools/call — a machine that is here but not connected",
        rpc("tools/call", {"name": "aios.test.complete_probe_unplugged", "arguments": {}}))
stamped("tools/call — a call that fails validation (missing argument)",
        rpc("tools/call", {"name": "aios.test.complete_probe_ok", "arguments": {}}))

# THE FIELD SITS BESIDE THE ANSWER, IT DOES NOT REPLACE IT. A stamp that clobbered the payload
# would pass every check above and break every client.
r = rpc("tools/call", {"name": "aios.test.complete_probe_ok", "arguments": {"city": "Oslo"}})["result"]
ok("the stamp does not disturb the tool's own answer",
   r.get("isError") is False and r.get("structuredContent") == {"city": "Oslo"}, str(r))
r = rpc("initialize", {"protocolVersion": mcp.SUPPORTED_VERSIONS[0]})["result"]
ok("...nor the handshake's own fields",
   r.get("protocolVersion") == mcp.SUPPORTED_VERSIONS[0] and "capabilities" in r
   and r.get("serverInfo", {}).get("name") == mcp.SERVER_NAME, str(r))

# AN ERROR IS NOT A RESULT and must not grow one. The spec keeps the two apart; a stamped error
# body would read to a client as a success with odd contents.
e = rpc("no/such/method")
ok("a protocol error carries no result, stamped or otherwise",
   "error" in e and "result" not in e, str(e))
e = rpc("tools/call", {"name": "aios.test.not_a_tool"})
ok("...and neither does an unknown tool", "error" in e and "result" not in e, str(e))

print("\nALL MCP RESULTS SAY COMPLETE" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
