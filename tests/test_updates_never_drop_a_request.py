"""An update never leaves the connector dark, and the box can say what its AI last asked (OSDev1 ASSIGNED 2026-10-07).

OSDev1, 2026-10-07, owner-approved, for the owner's "Couldn't reload tools" on his own box: the box checked out clean
on every replay, and the likely cause was scripts/box_update.sh RESTARTING aios-dispatch, which closes the port for
10-20 seconds per update. "(1) Zero-downtime updates for every box: a graceful gunicorn reload (HUP or USR2), never a
dead window; prove /mcp answers throughout an update. (2) A connector log in /health and core.health: the last ~10 MCP
requests (time, method, client name, ok or error code; no params)."

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * a real gunicorn, started with the box's own flags, drops a single request while its code changes and it reloads,
    or keeps serving the old code after the reload;
  * the unit loses its HUP reload, or gains --preload (then a reload would never load the new code);
  * box_update.sh restarts the web service on an update that did not change its unit, or on a rollback;
  * a connector request goes unrecorded (a refusal at the gate included), records a parameter, or the log grows
    without bound, or is missing from /health or core.health.

The first check starts gunicorn on 127.0.0.1 only.

Run: python tests/test_updates_never_drop_a_request.py
"""
from __future__ import annotations

import os
import pathlib
import re
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "zd.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -- {str(detail)[:500]}" if not cond and detail else ""))
    if not cond:
        _failed += 1


UNIT = (ROOT / "deploy" / "aios-dispatch.service").read_text()
UPDATE = (ROOT / "scripts" / "box_update.sh").read_text()

print("test_the_unit_reloads_in_place")
start = re.search(r"^ExecStart=(.*?)(?<!\\)$", UNIT.replace("\\\n", " "), re.M).group(1)
ok("the web service reloads with gunicorn's HUP", re.search(r"^ExecReload=/bin/kill -s HUP \$MAINPID$", UNIT, re.M))
ok("...and never preloads, or a reload would keep the old code", "--preload" not in start, start)

print("\ntest_a_real_reload_drops_nothing")
flags = shlex.split(start)[1:]
flags = [f for f in flags if not f.endswith(":app")]
i = flags.index("--bind")
s = socket.socket()
s.bind(("127.0.0.1", 0))
port = s.getsockname()[1]
s.close()
flags[i + 1] = f"127.0.0.1:{port}"
flags = [f for f in flags if f not in ("--access-logfile", "--error-logfile", "-")]
app_dir = pathlib.Path(tempfile.mkdtemp(prefix="zd_app_"))
(app_dir / "VERSION").write_text("old\n")
(app_dir / "zdapp.py").write_text(
    "import pathlib\n"
    "VERSION = pathlib.Path(__file__).with_name('VERSION').read_text().strip()   # read once, as the box reads its"
    " commit\n"
    "def app(environ, start_response):\n"
    "    start_response('200 OK', [('Content-Type', 'text/plain')])\n"
    "    return [VERSION.encode()]\n")
proc = subprocess.Popen([sys.executable, "-m", "gunicorn", *flags, "--chdir", str(app_dir), "zdapp:app"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
url = f"http://127.0.0.1:{port}/"


def get() -> str:
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.read().decode()


up = False
for _ in range(100):
    try:
        up = get() == "old"
        break
    except Exception:                            # noqa: BLE001 — still starting
        time.sleep(0.1)
ok("gunicorn starts with the box's own flags", up, flags)
seen, errors, stop = [], [], threading.Event()


def hammer():
    while not stop.is_set():
        try:
            seen.append(get())
        except Exception as e:                   # noqa: BLE001 — every failure is the finding
            errors.append(f"{type(e).__name__}: {e}")
        time.sleep(0.01)


threads = [threading.Thread(target=hammer) for _ in range(4)]
for t in threads:
    t.start()
time.sleep(0.5)
(app_dir / "VERSION").write_text("new\n")         # the update: new code on disk
os.kill(proc.pid, signal.SIGHUP)                  # what `systemctl reload aios-dispatch` sends
deadline = time.time() + 30
while time.time() < deadline and seen[-1:] != ["new"]:
    time.sleep(0.1)
time.sleep(1.0)                                   # and keep asking while the old workers finish
stop.set()
for t in threads:
    t.join()
proc.send_signal(signal.SIGTERM)
try:
    proc.wait(timeout=20)
except subprocess.TimeoutExpired:
    proc.kill()
ok(f"not one of {len(seen) + len(errors)} requests failed across the reload", up and not errors and len(seen) > 50,
   errors[:3])
ok("...the old code answered before it, the new code after it", "old" in seen and seen[-1:] == ["new"], seen[-3:])

print("\ntest_the_update_reloads_and_restarts_only_when_it_must")
fn = re.search(r"restart_or_reload\(\) \{(.*?)\n\}", UPDATE, re.S)
ok("an update reloads the web service", fn and "systemctl reload aios-dispatch" in fn.group(1))
ok("...unless this run changed its unit (a reload never re-reads ExecStart), or it isn't running",
   fn and "changed_units" in fn.group(1) and "is-active" in fn.group(1) and 'changed_units="$changed_units$n "' in UPDATE)
ok("...and if the reload fails, it restarts rather than stay on the old code",
   fn and "systemctl restart" in fn.group(1).split("systemctl reload aios-dispatch", 1)[1])
ok("the update and the rollback both go through it; nothing restarts aios-dispatch directly",
   len(re.findall(r"restart_or_reload(?! *\(\))", UPDATE.replace(fn.group(0) if fn else "", ""))) == 2
   and not re.search(r"systemctl restart (\"\$svc\"|aios-dispatch)", UPDATE.replace(fn.group(0) if fn else "", "")))
r = subprocess.run(["bash", "-n", str(ROOT / "scripts" / "box_update.sh")], capture_output=True, text=True)
ok("the script still parses", r.returncode == 0, r.stderr)

print("\ntest_the_connector_log")
from core import state  # noqa: E402

state.init_db()
from core import box_tools  # noqa: E402
from core.connector import requests_log, seats  # noqa: E402
from core.dispatch import app  # noqa: E402

c = app.test_client()
_, secret = seats.mint("Claude", "act")
auth = {"Authorization": f"Bearer {secret}", "User-Agent": "claude-ai/1.0 (+https://claude.ai)"}
c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2025-03-26", "clientInfo": {"name": "Claude", "version": "1"}}},
       headers=auth)
c.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=auth)
c.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                     "params": {"name": "inbox.reply", "arguments": {"secret_words": "do not keep me"}}},
       headers=auth)
c.post("/mcp", json={"jsonrpc": "2.0", "id": 4, "method": "tools/list"}, headers={"User-Agent": "ChatGPT-User/1.0"})
got = requests_log.recent()
ok("each request is one line: method, client, how it went, newest first",
   [(r["method"], r["client"], r["outcome"]) for r in got] == [
       ("tools/list", "ChatGPT-User", "http 401"), ("tools/call", "claude-ai", "error -32602"),
       ("tools/list", "claude-ai", "ok"), ("initialize", "Claude", "ok")], got)
ok("...the gate's own refusal included, and every line has its time", all(r["at"] for r in got))
ok("...and never a parameter, an argument or a credential",
   not any(w in str(got) for w in ("secret_words", "do not keep me", secret, "inbox.reply")), got)
for i in range(70):
    requests_log.record(method="ping", client="t", outcome="ok")
with state.connect() as conn:
    kept = conn.execute("SELECT COUNT(*) n FROM connector_requests").fetchone()["n"]
ok(f"it keeps the newest {requests_log.KEEP}, never more", kept == requests_log.KEEP, kept)
h = c.get("/health").get_json() or {}
ok("/health shows the newest ten", len(h.get("connector") or []) == requests_log.SHOWN
   and h["connector"][0]["method"] == "ping", h.get("connector", [])[:1])
ok("core.health shows them too", len(box_tools.health().get("connector") or []) == requests_log.SHOWN)

print("\nALL NO-DARK-WINDOW CHECKS PASS" if not _failed else f"\n{_failed} NO-DARK-WINDOW CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
