"""The key features gate (core/key_features.py): the box uses each key feature the way a buyer does.

Owner, 2026-10-02: "This is a key feature that can never break again." Measured here, against the box's real
MCP endpoint, approvals and Morning Review, with only the network hop and the AI's answer stood in for:
  * a healthy box passes every check, and says so in counts only
  * each way the connector broke (or could) is named: ping, a listed tool that won't answer, an old name, a
    schema a client can't take, the connector not answering at all
  * the AI, the review and the approval round trip each fail by name when they break
  * the seat it uses is revoked after, and is never on the owner's list of connections
  * the approval round trip tells nobody and leaves no row; it runs exactly once
  * the check-in carries the last result, counts and names only

Run: python tests/test_key_features_gate.py
"""
import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
T = pathlib.Path(tempfile.mkdtemp(prefix="keyfeatures_"))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")
os.environ["AIOS_KEY_FEATURES_LAST"] = str(T / "key_features.json")
os.environ["DISPATCH_BEARER_TOKEN"] = "x"

from core import state  # noqa: E402

state.init_db()
from core import ai_health, approvals, dispatch, key_features  # noqa: E402
from core.connector import seats, tools  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


client = dispatch.app.test_client()


def post(url, body, headers):
    """The box's own /mcp, in process: the same handler, gate and tools a Claude app reaches."""
    r = client.post("/mcp", data=json.dumps(body), headers={**headers, "Content-Type": "application/json"})
    return r.status_code, (r.get_json(silent=True) if r.data else None)


ai_health.test = lambda **kw: {"ok": True, "answer": "ready"}     # the AI's answer is the one thing stood in for
# ...and the two upkeep readings (plan #1857 H14), which only a box has: systemd's upgrade timer, the box's certificate.
import time as _time  # noqa: E402

UPKEEP = {"state": "active", "ago_s": 3600, "days": 60.0}
# WHAT systemd 255 ACTUALLY PRINTS for `systemctl show apt-daily-upgrade.timer -p ActiveState -p LastTriggerUSec`
# (OSDev1, measured on Ubuntu 24.04.4): the stand-in hands back that text, through the check's own parser. No
# LastTriggerUSecRealtime: timers don't have it, and reading it is what paged the owner's box on 2026-10-03.
import datetime as _dt  # noqa: E402


def _show_text() -> str:
    last = ("n/a" if UPKEEP["ago_s"] is None else
            _dt.datetime.fromtimestamp(_time.time() - UPKEEP["ago_s"], _dt.timezone.utc)
            .strftime("%a %Y-%m-%d %H:%M:%S UTC"))
    return f"ActiveState={UPKEEP['state']}\nLastTriggerUSec={last}\n"


key_features._systemctl_show = lambda unit: key_features._parse_show(_show_text())
import tempfile as _tempfile  # noqa: E402

key_features.UPGRADES_STAMP = os.path.join(_tempfile.mkdtemp(), "stamp-apt-daily-upgrade.timer")   # absent by default
import tempfile as _tmp  # noqa: E402

from core import claim as _claim  # noqa: E402

_claim.PROVISION_JSON = os.path.join(_tmp.mkdtemp(), "provision.json")   # a box provisioned 10 days ago, by default
open(_claim.PROVISION_JSON, "w").write("{}")
os.utime(_claim.PROVISION_JSON, (_time.time() - 10 * 86400, _time.time() - 10 * 86400))


def _born(seconds_ago: float) -> None:
    os.utime(_claim.PROVISION_JSON, (_time.time() - seconds_ago, _time.time() - seconds_ago))
key_features._cert_days_left = lambda url=None: UPKEEP["days"]
asked = []
_real_test = ai_health.test

print("\nKey features gate\n")
res = key_features.run(post=post, url="https://box.example")
ok("a healthy box passes every check", res["ok"] is True and not res["failed"], res)
ok("...and says so in counts: one per MCP step and read tool, plus the AI, the review and an approval",
   res["passed"] == res["total"] and res["total"] >= 4 + len([s for s in tools.visible_to(
       {"role": "read"})]) + 3, res)
ok("the result carries only counts, names and a time", set(res) == {"at", "ok", "passed", "total", "failed"})

seat_rows = seats.all_seats(include_revoked=True, include_runs=True)
mine = [s for s in seat_rows if s["label"] == key_features.SEAT_LABEL]
ok("the seat it used is revoked after", mine and all(s["revoked_at"] for s in mine), mine)
ok("...and never on the owner's list of connections", not any(
    s["label"] == key_features.SEAT_LABEL for s in seats.all_seats()))
with state.connect() as c:
    left = c.execute("SELECT COUNT(*) FROM approvals WHERE kind = ?", (key_features.KIND,)).fetchone()[0]
ok("the approval round trip leaves no row on the owner's approvals list", left == 0, left)
told = []
approvals._tell_phone = lambda: told.append(1)
key_features.check_approval()
ok("...and tells nobody's phone", not told, told)
ok("...and ran exactly once (a second approve did nothing)", key_features._RAN.count(key_features._RAN[-1]) == 1)

print("\nEach break is named\n")
import core.connector.mcp as mcp  # noqa: E402

_handle = mcp._handle
mcp._handle = lambda method, params, rpc_id, seat: (
    mcp._err(rpc_id, mcp._METHOD_NOT_FOUND, "unknown method: ping") if method == "ping"
    else _handle(method, params, rpc_id, seat))
res = key_features.run(post=post, url="https://box.example")
ok("ping answered 'unknown method' (tonight's suspect) fails as mcp:ping", "mcp:ping" in res["failed"]
   and res["ok"] is False, res["failed"])
mcp._handle = _handle

spec = tools.registry()["core.health"]
_fn = spec["fn"]
spec["fn"] = lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))
res = key_features.run(post=post, url="https://box.example")
ok("a listed read tool that won't answer is named by its own name", "mcp:core.health" in res["failed"], res["failed"])
spec["fn"] = _fn

_real_resolve = tools.call
import core.connector.tools as _t  # noqa: E402

aliases = getattr(_t, "_OLD_PREFIX", None)
_call = tools.call


def no_old_names(name, *a, **kw):
    if str(name).startswith("aios."):
        raise _t.ToolError("unknown_tool", f"no tool named {name}")
    return _call(name, *a, **kw)


tools.call = no_old_names
res = key_features.run(post=post, url="https://box.example")
ok("an old 'aios.' name a cached client calls is checked by name", f"mcp:{key_features.OLD_NAME}" in res["failed"],
   res["failed"])
tools.call = _call

_entry = mcp._tool_entry
mcp._tool_entry = lambda s: ({**_entry(s), "inputSchema": {"type": "array"}} if s["name"] == "core.manifest"
                             else _entry(s))
res = key_features.run(post=post, url="https://box.example")
ok("a schema a client can't take is a failure, named", "mcp:core.manifest:schema" in res["failed"], res["failed"])
mcp._tool_entry = _entry

res = key_features.run(post=lambda *a: (_ for _ in ()).throw(ConnectionError("refused")), url="https://box.example")
ok("the connector not answering at all is a failure", "mcp:connect" in res["failed"], res["failed"])

ai_health.test = lambda **kw: {"ok": False, "why": "401"}
res = key_features.run(post=post, url="https://box.example")
ok("an AI that doesn't answer fails as 'ai'", "ai" in res["failed"], res["failed"])
ai_health.note(True, "draft", force=True)
calls = []
ai_health.test = lambda **kw: calls.append(1) or {"ok": False}
res = key_features.run(post=post, url="https://box.example")
ok("...but a recent real answer is the proof, and no question is asked", "ai" not in res["failed"]
   and not calls, (res["failed"], calls))
ai_health.test = lambda **kw: {"ok": True, "answer": "ready"}

from core import review_brief  # noqa: E402

_build = review_brief.build
review_brief.build = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("no rows"))
res = key_features.run(post=post, url="https://box.example")
ok("a Morning Review that won't build fails as 'review'", "review" in res["failed"], res["failed"])
review_brief.build = _build

_run_check = key_features._run_check
key_features._run_check = lambda d: key_features._RAN.append(d.get("nonce")) or {"ok": False, "text": "no"}
res = key_features.run(post=post, url="https://box.example")
ok("an approval that doesn't complete fails as 'approval'", "approval" in res["failed"], res["failed"])
key_features._run_check = _run_check

print("\nA listed tool the call refuses is the break (OSDev1's review)\n")
_call2 = tools.call
tools.call = lambda name, *a, **kw: (({"error": "forbidden", "message": "this seat can't use core.health"}, 403)
                                     if name == "core.health" else _call2(name, *a, **kw))
res = key_features.run(post=post, url="https://box.example")
ok("a tool tools/list shows and tools/call refuses (forbidden) fails by name", "mcp:core.health" in res["failed"],
   res["failed"])
tools.call = lambda name, *a, **kw: (({"error": "missing_arg", "message": "query is required"}, 400)
                                     if name == "core.health" else _call2(name, *a, **kw))
res = key_features.run(post=post, url="https://box.example")
ok("...and so does one that refuses the arguments its own schema asked for", "mcp:core.health" in res["failed"],
   res["failed"])
tools.call = _call2
res = key_features.run(post=post, url="https://box.example")
ok("...while 'not connected yet' is the tool working (a fresh box passes)", res["ok"] is True, res["failed"])


def broken_post(url, body, headers):
    name = ((body.get("params") or {}).get("name")) if body.get("method") == "tools/call" else None
    if name == "core.manifest":
        return 200, {"jsonrpc": "2.0", "id": body.get("id"), "error": {"code": -32603, "message": "internal"}}
    if name == "morning_review.report_days":
        return 502, None
    return post(url, body, headers)


res = key_features.run(post=broken_post, url="https://box.example")
ok("a listed tool answered with a JSON-RPC error fails by name", "mcp:core.manifest" in res["failed"], res["failed"])
ok("...and one whose request came back non-200 fails by name", "mcp:morning_review.report_days" in res["failed"], res["failed"])

print("\nIt never calls into his connected apps\n")
ok("the read role holds the connected apps' tools (so leaving them out is a choice, measured)",
   key_features.APPS in tools.held({"role": "read"}))
ok("...and the gate's seat never holds them", key_features.APPS not in key_features._read_capabilities(),
   key_features._read_capabilities())

print("\nA killed run leaves nothing behind\n")
dead_seat, _ = seats.mint(key_features.SEAT_LABEL, "read", capabilities=key_features._read_capabilities())
owners, _ = seats.mint(key_features.SEAT_LABEL, "act")          # his own connection, named the same by chance
approvals.register_kind(key_features.KIND, run=key_features._run_check)
dead_row = approvals.propose(key_features.KIND, machine="core", title="Key features check", detail={"nonce": "x"},
                             quiet=True)
key_features.run(post=post, url="https://box.example")
live = {s["id"] for s in seats.all_seats(include_revoked=False, include_runs=True)}
ok("a seat a killed run left live is revoked at the start of the next run", dead_seat not in live)
ok("...but an owner's own connection with the same name is never touched", owners in live)
with state.connect() as c:
    gone = c.execute("SELECT COUNT(*) FROM approvals WHERE id = ?", (dead_row["id"],)).fetchone()[0] == 0
ok("an approval a killed run left on his screen is removed at the start of the next run", gone)

print("\nThe AI is asked at most once in 6 hours\n")
from datetime import datetime, timedelta, timezone  # noqa: E402

_state = ai_health.state
calls = []
ai_health.test = lambda **kw: calls.append(1) or {"ok": True}
ai_health.state = lambda: {"last_ok": {"at": (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()},
                           "last_fail": None}
key_features.check_ai()
ok("an answer 3 hours ago is the proof: no question (the timer runs hourly)", not calls, calls)
ai_health.state = lambda: {"last_ok": {"at": (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()},
                           "last_fail": None}
key_features.check_ai()
ok("...one 7 hours ago is not, and the box asks", calls == [1], calls)
ai_health.state = _state
ai_health.test = lambda **kw: {"ok": True, "answer": "ready"}

print("\nThe review step sends and stores nothing\n")
from core import box_mail, slack  # noqa: E402

sent = []
_send, _post, _dm, _remember = box_mail.send, slack.post, slack.send_dm, review_brief._remember
box_mail.send = lambda *a, **kw: sent.append("mail")
slack.post = lambda *a, **kw: sent.append("slack")
slack.send_dm = lambda *a, **kw: sent.append("dm")
review_brief._remember = lambda *a, **kw: sent.append("stored")
good = key_features.check_review()
ok("the review builds, and nothing is mailed, posted or stored", good and not sent, sent)
box_mail.send, slack.post, slack.send_dm, review_brief._remember = _send, _post, _dm, _remember

print("\nThe credential goes to one address, on this box\n")
import socket  # noqa: E402
import threading  # noqa: E402
from http.server import BaseHTTPRequestHandler, HTTPServer  # noqa: E402

seen = []


class Elsewhere(BaseHTTPRequestHandler):
    def do_POST(self):
        seen.append(self.headers.get("Authorization"))
        self.send_response(200)
        self.end_headers()

    do_GET = do_POST                     # urllib turned a redirected POST into a GET and kept the header

    def log_message(self, *a):
        pass


other = HTTPServer(("127.0.0.1", 0), Elsewhere)


class Redirects(BaseHTTPRequestHandler):
    def do_POST(self):
        self.send_response(302)
        self.send_header("Location", f"http://127.0.0.1:{other.server_port}/mcp")
        self.end_headers()

    def log_message(self, *a):
        pass


first = HTTPServer(("127.0.0.1", 0), Redirects)
for srv in (other, first):
    threading.Thread(target=srv.serve_forever, daemon=True).start()
status, _ = key_features._http(f"http://127.0.0.1:{first.server_port}/mcp", {"jsonrpc": "2.0"},
                               {"Authorization": "Bearer secret"})
ok("a redirect is never followed with the credential: it comes back as its status", status == 302 and not seen,
   (status, seen))

dialled = []
lis = socket.socket()
lis.bind(("127.0.0.1", 0))
lis.listen(1)


def accept():
    conn, _ = lis.accept()
    dialled.append(conn.recv(512))
    conn.close()


threading.Thread(target=accept, daemon=True).start()
try:
    key_features._http(f"https://box.example.invalid:{lis.getsockname()[1]}/mcp", {}, {"Authorization": "x"})
except Exception:  # noqa: BLE001 — the listener speaks no TLS; reaching it is the point
    pass
ok("https dials this box's own loopback, never what DNS says, with the public name as SNI",
   dialled and b"box.example.invalid" in dialled[0], dialled)

print("\nThe check-in carries it\n")
key_features.save({"at": "2026-10-02T21:00:00+00:00", "ok": False, "passed": 30, "total": 31,
                   "failed": ["mcp:ping"], "secret": "never"})
from core import checkin  # noqa: E402

ready = checkin._ready() or {}
ok("the check-in's ready section carries the last result", ready.get("key_features", {}).get("failed")
   == ["mcp:ping"] and ready["key_features"]["passed"] == 30, ready)
ok("...and only its counts, names, reasons and time, never anything else the file holds",
   set(ready["key_features"]) <= {"at", "ok", "passed", "total", "failed", "why"}, ready.get("key_features"))
ok("it fits the check-in's size limit with every name full", len(json.dumps(
    {"failed": ["mcp:" + "x" * 76] * key_features.FAILED_MAX})) < checkin.MAX_BYTES // 2)

print("\nEach failure says whose it is, so only our own break blocks a release (#1857 H1)\n")
from core.exceptions import BudgetExceeded, RetryableError  # noqa: E402

ok("ai_health.kind: an outage or a busy AI app is the vendor's", ai_health.kind(RetryableError("overloaded")) == "vendor"
   and ai_health.kind(BudgetExceeded("claude subscription limit")) == "vendor")
ok("...a refused or signed-out account is the buyer's step (unset)",
   ai_health.kind(RuntimeError("the AI account refused the request (401: unauthorized)")) == "unset")
ok("...anything else is ours", ai_health.kind(RuntimeError("boom")) == "ours")

res = key_features.run(post=post, url="https://box.example")
ok("a green run carries no reasons", res["ok"] is True and "why" not in res, res)
ai_health.note(False, "test", "x", force=True)               # no fresh real answer, so the gate asks
_signed = key_features._ai_signed_in
key_features._ai_signed_in = lambda: False
ai_health.test = lambda **kw: {"ok": False, "why": "not signed in"}
res = key_features.run(post=post, url="https://box.example")
ok("no AI signed in yet: 'ai' fails as unset, a fresh box by design", res.get("why") == {"ai": "unset"}, res)
key_features._ai_signed_in = lambda: True
ai_health.test = lambda **kw: {"ok": False, "why": "overloaded", "kind": "vendor"}
res = key_features.run(post=post, url="https://box.example")
ok("signed in and the AI app down: 'ai' fails as vendor", res.get("why") == {"ai": "vendor"}, res)
ai_health.test = lambda **kw: {"ok": False, "why": "it answered with nothing"}
res = key_features.run(post=post, url="https://box.example")
ok("signed in and no reason from the test: 'ai' is ours", res.get("why") == {"ai": "ours"}, res)
key_features._ai_signed_in = lambda: (_ for _ in ()).throw(RuntimeError("no backend"))
res = key_features.run(post=post, url="https://box.example")
ok("...and a sign-in question that crashes is ours, never quieter", res.get("why") == {"ai": "ours"}, res)
key_features._ai_signed_in = _signed
ai_health.test = lambda **kw: {"ok": True, "answer": "ready"}
review_brief.build = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("no rows"))
res = key_features.run(post=post, url="https://box.example")
ok("the review, the approval and the connector are the box's own code: always ours",
   res.get("why") == {"review": "ours"}, res)
review_brief.build = _build
key_features.save({**res, "why": {"review": "ours", "ghost": "ours", "ai": "maybe"}, "failed": ["review", "ai"]})
got = key_features.last()
ok("the saved result carries reasons for failed names only, known reasons only", got.get("why") == {"review": "ours"},
   got)
ready = checkin._ready() or {}
ok("...and the check-in carries them", ready.get("key_features", {}).get("why") == {"review": "ours"}, ready)

print("\nA fresh box reports within minutes of boot\n")
import http.server  # noqa: E402
import threading  # noqa: E402

timer = (ROOT / "deploy" / "aios-keyfeatures.timer").read_text()
unit = (ROOT / "deploy" / "aios-keyfeatures.service").read_text()
ok("the timer runs 4 minutes after boot, not 10", "OnBootSec=4min" in timer, timer)
limit = int(unit.split("TimeoutStartSec=")[1].split()[0])
ok("the unit's time limit covers the web wait plus a full run", limit >= key_features.WEB_WAIT_S + 300, limit)
_local = key_features.LOCAL
key_features.LOCAL = "http://127.0.0.1:9"                    # nothing listens there
ok("no web process: the wait gives up at its limit and says so",
   key_features._wait_for_web(limit_s=0, sleep=lambda s: None) is False)


class _Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()

    def log_message(self, *a):
        pass


srv = http.server.HTTPServer(("127.0.0.1", 0), _Health)
threading.Thread(target=srv.serve_forever, daemon=True).start()
key_features.LOCAL = f"http://127.0.0.1:{srv.server_port}"
ok("the web process answering /health ends the wait", key_features._wait_for_web(limit_s=5, sleep=lambda s: None) is True)
srv.shutdown()
key_features.LOCAL = _local

print("\nUpkeep is checked, not assumed (#1857 H14)\n")
ok("security updates ran an hour ago and the certificate has 60 days: both pass",
   key_features.check_upgrades() and key_features.check_certificate())
UPKEEP["ago_s"] = 50 * 3600
ok("UPDATES LAST RAN 50 HOURS AGO: red", key_features.check_upgrades() is False)
UPKEEP["ago_s"], UPKEEP["state"] = 3600, "inactive"
ok("THE UPGRADE TIMER IS OFF: red", key_features.check_upgrades() is False)
ok("...and so is a box with no reading at all", key_features.check_upgrades(show=lambda u: {}) is False)
UPKEEP["state"], UPKEEP["ago_s"] = "active", None
_born(3600)
ok("A BOX PROVISIONED AN HOUR AGO, its timer never fired yet: passes (the release's rehearsal box)",
   key_features.check_upgrades() is True)
_born(3 * 86400)
ok("...provisioned 3 days ago and never fired: red", key_features.check_upgrades() is False)
_born(3600)
UPKEEP["state"] = "inactive"
ok("...provisioned an hour ago with the timer off: red", key_features.check_upgrades() is False)
_born(10 * 86400)
UPKEEP["ago_s"] = 3600
UPKEEP["state"], UPKEEP["days"] = "active", 9.5
ok("THE CERTIFICATE HAS 9.5 DAYS LEFT: red (Caddy renews at 30, so renewal has stopped)",
   key_features.check_certificate() is False)
res = key_features.run(post=post, url="https://box.example")
ok("...and in the gate both are named, as ours, so they block a release",
   res["failed"] == ["certificate"] and res["why"] == {"certificate": "ours"}, res)
UPKEEP["days"] = 60.0

print("\nThe last run is read as systemd keeps it (OSDev1: the owner's box paged 'red upgrades' on 2026-10-03)\n")
REAL = "ActiveState=active\nLastTriggerUSec=Sat 2026-10-03 06:29:24 UTC\n"     # systemd 255's output, verbatim
got = key_features._parse_show(REAL)
ok("THE REAL OUTPUT HAS NO REALTIME LINE, and its formatted stamp is read as 06:29:24 UTC that day",
   "LastTriggerUSecRealtime" not in got and key_features._last_upgrade_run(got)
   == _dt.datetime(2026, 10, 3, 6, 29, 24, tzinfo=_dt.timezone.utc).timestamp(), got)
UPKEEP["ago_s"] = None                                   # systemd says n/a; only the stamp file can say it ran
open(key_features.UPGRADES_STAMP, "w").close()
os.utime(key_features.UPGRADES_STAMP, (_time.time() - 3600, _time.time() - 3600))
ok("A STAMP FILE AN HOUR OLD: the timer ran, green", key_features.check_upgrades() is True)
os.utime(key_features.UPGRADES_STAMP, (_time.time() - 50 * 3600, _time.time() - 50 * 3600))
ok("...a stamp 50 hours old on a box 10 days old: red", key_features.check_upgrades() is False)
os.remove(key_features.UPGRADES_STAMP)
UPKEEP["ago_s"] = 3600

print("\nALL KEY FEATURES GATE CHECKS PASS" if not _failed else f"\n{_failed} KEY FEATURES GATE CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
