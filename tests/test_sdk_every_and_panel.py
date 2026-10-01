"""Two seams added to SDK 1 for the Lead Magnet Machine: `m.every` and `m.panel`.

docs/PLAN_LEAD_MAGNET_MACHINE.md, approved by the owner 2026-10-01 ("Approve the plan with your
recommendations"), step 1. What is measured:

  m.every, under the owner's limits (decision 7):
    * never more often than every 15 seconds: refused below it, with the rule in the sentence
    * runs in the worker, named `my_…`: registered on the worker's periodic list as `<key>.<job>`
    * the worker's shared loop never waits on it: the call returns at once while the job runs on
    * a run is never doubled: a run still going when the next is due is left to finish
    * a time budget: a run past it is reported, once
    * a broken job is isolated and reported: its error is recorded where the Add a Machine page reads it,
      the next run goes ahead, and a run that succeeds clears the report
    * the report is written once per change, not every 20 seconds
  m.panel, with OSDev1's isolation condition:
    * the panels in a slot each get their own card, in a stable order, under the machine's own key
    * a panel that raises shows "This section couldn't load", and the cards beside it still render
    * a bad slot, an empty title or a render that isn't a function is refused when it registers
  and both are promised: they are in `sdk.SEAMS`, and the guide teaches them.

Run: python tests/test_sdk_every_and_panel.py
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
T = pathlib.Path(tempfile.mkdtemp(prefix="sdk_every_"))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")
os.environ["AIOS_MY_MACHINES"] = str(T / "machines")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

FAILS: list[str] = []


def ok(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(label)


def wait_for(cond, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


from core import state  # noqa: E402

state.init_db()
from core import machine_jobs, panels, sdk, worker  # noqa: E402

m = sdk.machine("job-tracker")

print("— m.every: the limits —")
try:
    m.every(14, lambda: None, name="too_often")
    ok("a job more often than every 15 seconds is refused", False)
except ValueError as e:
    ok("a job more often than every 15 seconds is refused, naming the rule", "15 seconds" in str(e), str(e))
for n in (float("nan"), float("inf")):
    try:
        m.every(n, lambda: None, name="odd")
        ok(f"an interval of {n} is refused", False)
    except ValueError:
        ok(f"AN INTERVAL OF {n} IS REFUSED: nan slips past `< 15`, and inf would never run", True)
for bad in ("Sweep", "sweep-now", ""):
    try:
        m.every(60, lambda: None, name=bad)
        ok(f"a job named {bad!r} is refused", False)
    except ValueError:
        ok(f"a job named {bad!r} is refused", True)

ran = []


def check_orders():
    ran.append(1)


m.every(15, check_orders)
names = {t["name"]: t for t in worker.PERIODIC}
ok("every 15 seconds is allowed, and it is on the worker's list as my_<machine>.<job>",
   "my_job_tracker.check_orders" in names and names["my_job_tracker.check_orders"]["interval"] == 15.0,
   str(sorted(names)))


@m.every(60)
def tidy_up():
    pass


ok("the decorator form registers too, and hands the function back unchanged",
   "my_job_tracker.tidy_up" in {t["name"] for t in worker.PERIODIC} and callable(tidy_up))

print("\n— m.every: the worker never waits, and a run is never doubled —")
release = threading.Event()
slow_runs = []


def slow():
    slow_runs.append(1)
    release.wait(5)


m.every(30, slow)
tick = {t["name"]: t for t in worker.PERIODIC}["my_job_tracker.slow"]["fn"]
t0 = time.monotonic()
first = tick()
took = time.monotonic() - t0
ok("THE WORKER'S LOOP GETS CONTROL BACK AT ONCE while the job is still running",
   took < 0.5 and first == {"status": "started"}, f"{took:.2f}s {first}")
ok("...and the job is running on its own thread", wait_for(lambda: slow_runs == [1]))
second = tick()
ok("A RUN STILL GOING IS LEFT TO FINISH: the next turn starts nothing",
   second == {"status": "still_running"} and slow_runs == [1], f"{second} runs={len(slow_runs)}")

real_budget = machine_jobs.BUDGET_S
machine_jobs.BUDGET_S = 0
tick()
over = machine_jobs.failing().get("my_job_tracker.slow") or {}
ok("A RUN PAST ITS TIME BUDGET IS REPORTED, saying the next run waits for it",
   "budget" in str(over.get("error")) and over.get("machine") == "my_job_tracker", str(over))
machine_jobs.BUDGET_S = real_budget
release.set()
ok("...and when it finishes, the next turn starts a fresh run",
   wait_for(lambda: tick() == {"status": "started"}) and wait_for(lambda: len(slow_runs) == 2))
ok("...which, finishing in time, clears the report",
   wait_for(lambda: "my_job_tracker.slow" not in machine_jobs.failing()))

print("\n— m.every: a broken job is isolated and reported —")
fail = {"on": True}
writes = []
real_put = machine_jobs._put
machine_jobs._put = lambda v: (writes.append(dict(v)), real_put(v))[1]


def fragile():
    if fail["on"]:
        raise RuntimeError("the orders file is missing")


m.every(20, fragile)
ftick = {t["name"]: t for t in worker.PERIODIC}["my_job_tracker.fragile"]["fn"]
ftick()
ok("A JOB THAT RAISES IS RECORDED with its machine, its name and its error",
   wait_for(lambda: "the orders file is missing" in str(
       (machine_jobs.failing().get("my_job_tracker.fragile") or {}).get("error"))),
   str(machine_jobs.failing()))
rec = machine_jobs.failing()["my_job_tracker.fragile"]
ok("...with when it started failing", bool(rec.get("since")) and rec.get("job") == "fragile", str(rec))
before = len(writes)
wait_for(lambda: ftick() == {"status": "started"})
time.sleep(0.2)
ok("THE SAME FAILURE AGAIN WRITES NOTHING: one write per change, not one per run",
   len(writes) == before, f"{len(writes) - before} more writes")
ok("...and the next run went ahead regardless (it is not switched off)",
   "my_job_tracker.fragile" in {t["name"] for t in worker.PERIODIC})

def leaky():
    raise RuntimeError("fetch failed: https://api.example.test/v1?api_key=supersecret12345")


m.every(20, leaky)
ltick = {t["name"]: t for t in worker.PERIODIC}["my_job_tracker.leaky"]["fn"]
ltick()
wait_for(lambda: "my_job_tracker.leaky" in machine_jobs.failing())
rec = str(machine_jobs.failing().get("my_job_tracker.leaky"))
ok("A KEY IN A JOB'S ERROR NEVER REACHES THE RECORD the page shows", "supersecret12345" not in rec
   and "REDACTED" in rec, rec)
_logged = []
_real_log_error = machine_jobs.log.error
machine_jobs.log.error = lambda *a, **kw: _logged.append(kw)
machine_jobs._failed("my_job_tracker.leaky", "my_job_tracker", "leaky", "token=supersecret12345 in the url")
machine_jobs.log.error = _real_log_error
ok("...nor the journal", _logged and "supersecret12345" not in str(_logged), str(_logged))
machine_jobs._recovered("my_job_tracker.leaky")   # its report would otherwise sit on the page checks below

from core.dash import home  # noqa: E402
from core import custom_machines  # noqa: E402

custom_machines._STATUS["job-tracker"] = {"ok": True, "reason": "", "version": "1", "path": str(T)}
row = home._custom_machine_rows()
ok("THE ADD A MACHINE PAGE SAYS SO: running, but which job is failing and why",
   "Running, but its job fragile is failing" in row and "the orders file is missing" in row, row[:400])
ok("...and the row is marked, not shown as fine", 'class="stale"' in row, row[:400])

fail["on"] = False
wait_for(lambda: ftick() == {"status": "started"})
ok("A RUN THAT SUCCEEDS CLEARS THE REPORT", wait_for(lambda: "my_job_tracker.fragile" not in machine_jobs.failing()))
row = home._custom_machine_rows()
ok("...and the page goes back to Running", ">Running<" in row and "fragile" not in row, row[:400])
machine_jobs._put = real_put

print("\n— m.panel —")
m.panel("inbox", title="Lead magnets", render=lambda: "<p>3 keywords live</p>")
other = sdk.machine("bookings")
other.panel("inbox", title="Bookings", render=lambda: "<p>2 today</p>")
other.panel("inbox", title="Broken one", render=lambda: 1 / 0)
html = panels.render("inbox")
ok("each panel is its own card, under its title", html.count('class="card panel"') == 3
   and "<h2>Lead magnets</h2><p>3 keywords live</p>" in html, html)
ok("IN A STABLE ORDER, by title", html.index("Bookings") < html.index("Broken one") < html.index("Lead magnets"))
ok("A PANEL THAT RAISES SHOWS A QUIET CARD, never an error page", "This section couldn't load." in html
   and "ZeroDivision" not in html, html)
ok("...and the cards beside it still render", "2 today" in html and "3 keywords live" in html)
_plog = []
_real_plog = panels.log.error
panels.log.error = lambda *a, **kw: _plog.append(kw)
sdk.machine("leaky-panel").panel("leak", title="Leak",
                                 render=lambda: (_ for _ in ()).throw(RuntimeError("Bearer supersecret12345")))
panels.render("leak")
panels.log.error = _real_plog
ok("A KEY IN A PANEL'S ERROR NEVER REACHES THE JOURNAL", _plog and "supersecret12345" not in str(_plog), str(_plog))
ok("each is registered under its own machine's key",
   {p["machine"] for p in panels.panels("inbox")} == {"my_job_tracker", "my_bookings"})
m.panel("inbox", title="Lead magnets", render=lambda: "<p>4 keywords live</p>")
html = panels.render("inbox")
ok("registering the same title again replaces it (a re-import), never a second card",
   html.count("Lead magnets") == 1 and "4 keywords live" in html)
ok("an empty slot renders nothing", panels.render("settings") == "")
ok("a title is escaped, a card's heading never becomes markup",
   (other.panel("people", title="<b>x</b>", render=lambda: ""), "&lt;b&gt;" in panels.render("people"))[1])
for slot, title, render, why in (("Inbox!", "T", lambda: "", "a bad slot"), ("inbox", "", lambda: "", "no title"),
                                 ("inbox", "T", "not a function", "a render that isn't a function")):
    try:
        m.panel(slot, title=title, render=render)
        ok(f"{why} is refused when it registers", False)
    except ValueError:
        ok(f"{why} is refused when it registers", True)

print("\n— promised —")
ok("both are promised seams of SDK 1", {"every", "panel"} <= set(sdk.SEAMS) and sdk.VERSION == 1)
# The guide sits in docs/box/foundation in the repo and at the root of an exported box (export_box.sh).
_g = ROOT / "docs/box/foundation/BUILD_A_MACHINE.md"
guide = (_g if _g.exists() else ROOT / "BUILD_A_MACHINE.md").read_text()
ok("the guide teaches both", "m.every(seconds, fn)" in guide and "m.panel(slot, title=, render=)" in guide)
ok("the facade still doesn't re-export the worker's own scheduler",
   not any(hasattr(sdk, n) for n in ("register_periodic", "worker")))
for f in ("machine_jobs.py", "panels.py"):
    src = (ROOT / "core" / f).read_text()
    ok(f"core/{f} names no machine", not any(w in src for w in ("lead_magnet", "leadmagnet", "inbox.")), f)

print("\n" + ("ALL OK" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
