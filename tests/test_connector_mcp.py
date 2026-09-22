"""The MCP transport — stage A1 of the coworker box.

A1's proof is that two different coworkers can connect and read. This file is the mechanical half
of that: it drives the endpoint the way a real client does, over a real Flask app, with a real
minted seat.

WHAT THIS IS GUARDING, each a specific way an adapter fails quietly:

  · THE ADAPTER GROWING A BRAIN. Authorisation, validation and the audit row live in
    tools.call(). An adapter that calls a tool's function directly bypasses all three, and the
    bypass is invisible — the tool still answers. Asserted structurally, on the parsed AST.
  · ONE GENERATION ONLY. Clients were built against three different specs. If the box answers
    `server/discover` but not `initialize`, every client older than 2026-07-28 sees a dead server.
  · A BUSINESS FAILURE RETURNED AS A PROTOCOL ERROR. "Google is not connected yet" arriving as
    JSON-RPC -32603 reads to a coworker as "this server is broken" — so the customer is told the
    wrong thing about their own box. It must be a result with isError.
  · AN UNKNOWN TOOL RETURNED AS A RESULT. The mirror of the above: a model cannot self-correct
    onto a tool that does not exist, so handing it back as retryable invites retries forever.
  · THE ORIGIN CHECK MISSING. The spec REQUIRES it; without it a page a buyer visits can drive
    their box through their own browser.
  · A TOOL REGISTERED WITH NO CAPABILITY. The tool written in a hurry is the one that reaches
    personal data. Refused at import, with the tool's name in the error.

Run: python tests/test_connector_mcp.py
"""
import ast
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = tempfile.mkdtemp() + "/mcp.db"
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


# ── fixtures ──────────────────────────────────────────────────────────────────────────────
def _weather(city: str):
    return {"city": city, "sky": "clear"}


def _unplugged():
    return tools.NotConfigured("no key for the weather service on this box")


tools.register("weather", fn=_weather, description="The sky over one city.", machine="test",
               capability="read:reports",
               args={"city": {"type": "string", "required": True, "description": "City name."}})
tools.register("unplugged", fn=_unplugged, description="A machine that is not connected.",
               machine="test", capability="read:reports")

_seat_id, _cred = seats.mint("MCP test coworker", "read")
app = dispatch.app.test_client()
AUTH = {"Authorization": f"Bearer {_cred}"}


def rpc(method, params=None, rpc_id=1, headers=None, **kw):
    body = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
    if params is not None:
        body["params"] = params
    h = dict(AUTH)
    h.update(headers or {})
    return app.post("/api/v1/mcp", json=body, headers=h, **kw)


print("\nMCP transport — stage A1\n")

# ── THE STRUCTURAL ONE RUNS FIRST, DELIBERATELY ───────────────────────────────────────────
# It was last when this file was written, and a behavioural failure above it aborted the run
# before it ever executed — so the guard that matters most was the one that could silently
# not run. Measured, 2026-09-13: with the adapter deliberately cheating, these two lines
# printed nothing at all. Cheapest and most important check goes first.
# Asserted on the parsed AST, not on the text. A previous suite of mine matched its own prose in
# a docstring and passed vacuously; three times. Never again by substring.
_tree = ast.parse(pathlib.Path(ROOT / "core/connector/mcp.py").read_text())
_called = set()
for node in ast.walk(_tree):
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Attribute):
            _called.add(f.attr)
ok("the adapter invokes tools through tools.call()", "call" in _called)
ok("  and never reaches into a tool's own function",
   "fn" not in _called and not any(
       isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) and n.slice.value == "fn"
       for n in ast.walk(_tree)))


# ── the gate ──────────────────────────────────────────────────────────────────────────────
r = app.post("/api/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
ok("no seat → 401, the /api/ gate shuts this door too", r.status_code == 401, str(r.status_code))
r = app.post("/api/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
             headers={"Authorization": "Bearer the-wide-token"})
ok("the WIDE dispatch token does not open it — a seat or nothing", r.status_code == 401,
   str(r.status_code))

# ── both handshakes, because three generations are in the market ──────────────────────────
r = rpc("server/discover")
d = r.get_json()["result"]
ok("server/discover answers (mandatory in the current spec)", r.status_code == 200)
ok("  it lists every version we speak, not one",
   len(d["supportedVersions"]) >= 3 and "2026-07-28" in d["supportedVersions"],
   str(d.get("supportedVersions")))
ok("  it names the server", d["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "aios")
ok("  its instructions survive C1 — proposals, not 'read-only', which expires",
   "read-only" not in d["instructions"] and "propos" in d["instructions"],
   d["instructions"][:90])
ok("  it declares tools and does NOT promise listChanged we cannot send",
   "tools" in d["capabilities"] and not d["capabilities"]["tools"].get("listChanged"))

r = rpc("initialize", {"protocolVersion": "2025-03-26"})
i = r.get_json()["result"]
ok("initialize answers too — a 2025 client is not a dead client", r.status_code == 200)
ok("  it agrees to the version the client asked for", i["protocolVersion"] == "2025-03-26",
   str(i.get("protocolVersion")))
r = rpc("initialize", {"protocolVersion": "1999-01-01"})
ok("  an unknown asked-for version falls back to ours rather than failing",
   r.get_json()["result"]["protocolVersion"] == mcp.SUPPORTED_VERSIONS[0])

r = rpc("notifications/initialized", rpc_id=None)
ok("a notification gets 202 and an empty body, per the spec",
   r.status_code == 202 and not r.data, f"{r.status_code} {r.data[:40]!r}")

# ── tools/list ────────────────────────────────────────────────────────────────────────────
r = rpc("tools/list")
lst = r.get_json()["result"]["tools"]
names = [t["name"] for t in lst]
ok("tools/list returns the registry",
   "aios.test.weather" in names and "aios.core.manifest" in names, str(names))
ok("  sorted, so a client can cache it", names == sorted(names), str(names))
w = next(t for t in lst if t["name"] == "aios.test.weather")
ok("  a tool carries a JSON Schema for its arguments",
   w["inputSchema"]["properties"]["city"]["type"] == "string")
ok("  a required argument is marked required", w["inputSchema"]["required"] == ["city"])
u = next(t for t in lst if t["name"] == "aios.test.unplugged")
ok("  a no-argument tool says 'takes nothing', not 'takes anything'",
   u["inputSchema"]["additionalProperties"] is False and not u["inputSchema"]["properties"])

# ── tools/call ────────────────────────────────────────────────────────────────────────────
r = rpc("tools/call", {"name": "aios.test.weather", "arguments": {"city": "Laguna"}})
res = r.get_json()["result"]
ok("tools/call runs the tool", res["isError"] is False and res["structuredContent"]["city"] == "Laguna")
ok("  and mirrors it as text for a weaker model", "Laguna" in res["content"][0]["text"])

r = rpc("tools/call", {"name": "aios.test.unplugged"})
res = r.get_json()["result"]
ok("NOT CONNECTED is a result with isError, never a protocol error",
   "error" not in r.get_json() and res["isError"] is True)
ok("  and it says what to do, not that we broke",
   "not connected" in res["content"][0]["text"].lower(), res["content"][0]["text"])

r = rpc("tools/call", {"name": "aios.test.nope"})
body = r.get_json()
ok("an UNKNOWN tool is a protocol error — a model cannot self-correct onto it",
   "error" in body and body["error"]["code"] == -32602, str(body)[:120])

r = rpc("tools/call", {"name": "aios.test.weather", "arguments": {"nope": 1}})
res = r.get_json()["result"]
ok("a bad ARGUMENT is a result with isError — that one a model CAN fix",
   "error" not in r.get_json() and res["isError"] is True)

r = rpc("tools/call", {})
ok("tools/call with no name is invalid params",
   r.get_json()["error"]["code"] == -32602)
r = rpc("nonsense/method")
ok("an unknown method is method-not-found", r.get_json()["error"]["code"] == -32601)

# ── transport rules the spec states as MUST ───────────────────────────────────────────────
r = app.get("/api/v1/mcp", headers=AUTH)
ok("GET is 405 — we have no stream, and pretending otherwise strands a client",
   r.status_code == 405, str(r.status_code))

ok("the version list never claims the HTTP+SSE generation while /sse is absent — a client "
   "told to speak a transport we do not answer fails AFTER connecting",
   "2024-11-05" not in mcp.SUPPORTED_VERSIONS, str(mcp.SUPPORTED_VERSIONS))

r = rpc("tools/list", headers={"MCP-Protocol-Version": "1999-01-01"})
ok("an unsupported MCP-Protocol-Version is a 400, as the spec requires",
   r.status_code == 400, str(r.status_code))
r = rpc("tools/list", headers={"MCP-Protocol-Version": "2026-07-28"})
ok("  a supported one passes", r.status_code == 200)
r = rpc("tools/list")
ok("  and no header at all is fine (the spec's own fallback)", r.status_code == 200)

r = rpc("tools/list", headers={"Origin": "https://evil.example"})
ok("a cross-origin request is refused — the spec REQUIRES Origin validation",
   r.status_code == 403, str(r.status_code))

# BEHIND THE REAL PROXY, both ways. Raised at gate review as a possible false-reject: gunicorn is
# reached over http while a browser's Origin is https, so the comparison looks like it must
# disagree. It does not — ProxyFix(x_proto=1) at core/dispatch.py:37 builds host_url from
# X-Forwarded-Proto. Asserted rather than argued, and it is the pair that matters: if only the
# 403 were tested, loosening the check to host-only would still pass.
_PROXY = {"X-Forwarded-Proto": "https", "Host": "box.example.com"}
r = rpc("tools/list", headers={**_PROXY, "Origin": "https://box.example.com"})
ok("behind Caddy, a SAME-origin https browser request is allowed",
   r.status_code == 200, str(r.status_code))
r = rpc("tools/list", headers={**_PROXY, "Origin": "https://evil.example"})
ok("behind Caddy, a cross-origin one is still refused", r.status_code == 403, str(r.status_code))

r = app.post("/api/v1/mcp", data="not json", headers=AUTH, content_type="application/json")
ok("a non-JSON body is a parse error, not a stack trace",
   r.status_code == 400 and r.get_json()["error"]["code"] == -32700)
r = app.post("/api/v1/mcp", json={"id": 1, "method": "tools/list"}, headers=AUTH)
ok("a missing jsonrpc version is an invalid request", r.get_json()["error"]["code"] == -32600)

# ── the capability, required at import ────────────────────────────────────────────────────
try:
    tools.register("nocap", fn=lambda: 1, description="d", machine="test")
    ok("a tool with no capability is refused at import", False, "it registered")
except TypeError as e:
    ok("a tool with no capability is refused at import, by name", "capability" in str(e))
try:
    tools.register("badcap", fn=lambda: 1, description="d", machine="test", capability="stuff")
    ok("a free-text capability is refused", False, "it registered")
except ValueError as e:
    ok("a free-text capability is refused, so the vocabulary cannot drift", "capability" in str(e))

# ── A2: names, annotations, outputSchema, and the capability filter ────────────────────────
print("\nA2 — names, annotations, capability filter\n")

r = rpc("tools/list")
lst = r.get_json()["result"]["tools"]
names = [t["name"] for t in lst]
ok("every tool is aios.<machine>.<verb>", all(n.startswith("aios.") and n.count(".") >= 2
   for n in names), str(names))
ok("  NO bare-name alias survives — zero customers, so the rename is hard",
   not any(n in ("weather", "manifest", "report_day") for n in names), str(names))

w = next(t for t in lst if t["name"] == "aios.test.weather")
a = w["annotations"]
ok("a read tool is annotated read-only and NOT destructive",
   a["readOnlyHint"] is True and a["destructiveHint"] is False, str(a))
ok("  and closed-world — MCP defaults both to the hostile value if omitted",
   a["openWorldHint"] is False)
ok("outputSchema appears only where a tool declares one",
   "outputSchema" not in w, str(w.get("outputSchema")))

# THE A2 PROOF: the filter is closed before the stakes exist.
tools.register("queued", fn=lambda: {"queued": True}, description="Queues work.",
               machine="test", capability="write:proposals", min_role="act")
tools.register("leads_export", fn=lambda: {"rows": []}, description="Contact details.",
               machine="test", capability="read:leads_pii")
for role in ("read", "act", "service"):
    seen = [s["name"] for s in tools.visible_to({"id": "x", "role": role})]
    ok(f"a {role} seat never sees a read:leads_pii tool — no role grants it",
       "aios.test.leads_export" not in seen, str(seen))

r = rpc("tools/list")
ok("  and tools/list does not carry it either",
   "aios.test.leads_export" not in [t["name"] for t in r.get_json()["result"]["tools"]])

# THE BYPASS: the manifest must filter too, or the filter above is one call from useless.
r = rpc("tools/call", {"name": "aios.core.manifest"})
mnames = [t["name"] for t in r.get_json()["result"]["structuredContent"]["tools"]]
ok("the MANIFEST is filtered too — otherwise a hidden tool is one call away, named",
   "aios.test.leads_export" not in mnames, str(mnames))

# ── THE ONE THIS SUITE WAS MISSING, AND IT IS THE WHOLE POINT ─────────────────────────────
# Hiding a tool is not authorising it. The first version of A2 filtered tools/list and the
# manifest and stopped there, so a read seat that could not SEE this tool got 200 AND THE ROWS
# by naming it. Caught in review of #1127 by OSDev1, reproduced, then fixed in tools.call().
# A visibility test alone would still pass on the broken build — this is the one that would not.
for role in ("read", "act", "service"):
    body, status = tools.call("aios.test.leads_export", None, {"id": "probe", "role": role})
    ok(f"a {role} seat calling the hidden tool BY NAME is refused, not served",
       status == 403 and body.get("error") == "forbidden", f"{status} {body}")
ok("  and the refusal names the capability, so an operator knows what to grant",
   "read:leads_pii" in tools.call("aios.test.leads_export", None,
                                  {"id": "probe", "role": "read"})[0]["message"])

# min_role still bites independently — capability is power over WHAT, role is how MUCH.
r = rpc("tools/call", {"name": "aios.test.queued"})
res = r.get_json()["result"]
ok("a read seat is still refused an act tool it CAN see — neither check subsumes the other",
   res["isError"] is True, str(res)[:110])

print("\n— the short address a buyer is given is gated exactly like the long one —")
# THE ADDRESS ON THE SCREEN IS `https://<slug>.ownbox.app/mcp` (owner, 2026-09-21), and the whole
# seat gate hangs on a path test. `core/dispatch` says it about itself: this is "latent exactly
# until somebody mounts a connector route outside that prefix" — mounting /mcp IS that, and an
# ungated MCP endpoint is every customer message on the box handed to whoever finds the hostname.
#
# MATCHED EXACTLY, NEVER BY PREFIX: `startswith("/mcp")` would wave through a future `/mcpfoo`.
from core.dispatch import _needs_a_seat  # noqa: E402

for _path, _want in (("/mcp", True), ("/api/v1/mcp", True), ("/api/v1/tools", True),
                     ("/mcpanything", False), ("/mcp/anything", False),
                     ("/inbox/", False), ("/dash/login", False)):
    ok(f"{'needs a seat' if _want else 'not gated   '}: {_path}",
       _needs_a_seat(_path) is _want,
       "an ungated connector path is the whole inbox, unauthenticated")

# AND BOTH PATHS REACH THE SAME HANDLER. Read off the app's own URL map, so this goes red if
# somebody drops a decorator — `app` in this file is a TEST CLIENT, hence `application`.
_rules = {r.rule for r in app.application.url_map.iter_rules()}
ok("/mcp is mounted", "/mcp" in _rules, str(sorted(r for r in _rules if "mcp" in r)))
ok("...and /api/v1/mcp still is", "/api/v1/mcp" in _rules)
# THE SHORT PATH SERVES THE SAME ENDPOINTS AS THE LONG ONE — both of them, because /mcp has a
# GET (405, the spec's answer for "no server-initiated stream") as well as the JSON-RPC POST.
_by_path = {}
for _r in app.application.url_map.iter_rules():
    if _r.rule in ("/mcp", "/api/v1/mcp"):
        _by_path.setdefault(_r.rule, set()).add(_r.endpoint)
ok("...and the short path serves exactly the endpoints the long one does",
   _by_path.get("/mcp") == _by_path.get("/api/v1/mcp") and bool(_by_path.get("/mcp")),
   str(_by_path))

print("\n— a buyer is shown ONE address, everywhere —")
# THE OWNER HIT THIS, 2026-09-22: the screen that mints a key printed `/api/v1/mcp` while the
# screen linking to it printed `/mcp`. Both work — same handler, same gate — but somebody given
# two addresses for one thing reasonably concludes one is wrong, and that doubt lands at the
# moment they are pasting a secret into a third-party assistant.
#
# THE LONG FORM STAYS MOUNTED (anything already configured keeps working); it is simply never
# what we PRINT.
# THIS SCAN READS THE WHOLE TREE, NOT ONE FILE, and that is the repair. It used to name
# `marketing/customer_voice/app.py`, so the day the address moved to `core/dash/box_settings.py`
# with the box steps, the scan found no `{root}/mcp` anywhere it was looking and went red —
# reporting a buyer-facing regression that had not happened. A guard that names the file it
# guards fails every time that file is refactored, which trains everyone to relax it.
#
# WHAT IT PROVES AND WHAT IT DOES NOT. It proves no shipped module BUILDS the long form into a
# string for a person, and that at least one still builds the short one. It is a source scan, so
# it cannot prove the screen holding that line is reachable — `/mcp is mounted` above and
# tests/test_box_connect_surface.py are what answer that.
import pathlib as _pl3  # noqa: E402
import re as _re3  # noqa: E402

_root_dir = _pl3.Path(__file__).resolve().parents[1]
_printed, _short = [], []
for _f in sorted((*_root_dir.glob("core/**/*.py"), *_root_dir.glob("marketing/**/*.py"))):
    try:
        _src = _f.read_text()
    except OSError:                                 # noqa: PERF203 — a scan never dies on one file
        continue
    _rel = _f.relative_to(_root_dir).as_posix()
    # `core/connector/mcp.py` MOUNTS the long form and must keep doing so — anything already
    # configured against it keeps working. Route decorators are not printing, so skip them.
    _printed += [f"{_rel}: {ln.strip()[:60]}" for ln in _src.splitlines()
                 if "/api/v1/mcp" in ln
                 and not ln.strip().startswith(("#", "@blueprint", "@app"))]
    if _re3.search(r'f"\{root\}/mcp"', _src):
        _short.append(_rel)

ok("no screen prints the long form", not _printed, str(_printed)[:160])
ok("...and the short one is what they get", bool(_short),
   "nothing anywhere in core/ or marketing/ renders <root>/mcp")
print(f"       (rendered by: {', '.join(_short) or 'nothing'})")

print()
if _failed:
    print(f"{_failed} FAILED")
    sys.exit(1)
print("all green")
