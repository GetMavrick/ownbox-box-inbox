"""A restart must not re-run a periodic that already ran: the loop seeds `last` from the beat.

Measured 2026-09-10: every worker start re-ran every daily walker with a fresh per_day budget, so
finder_sweep made 22 paid calls after one start, 11 after the next and began again after a third.
These cases prove a completed beat inside its interval delays the first pass, and that everything
that should still run at start does: no beat, an old beat, a future or unreadable beat, a dormant
beat, and an unreadable heartbeats table.
"""
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("AIOS_HERMETIC_TEST", "1")
_TMP = pathlib.Path(tempfile.mkdtemp())
os.environ["AIOS_DB_PATH"] = str(_TMP / "periodic_seed.db")
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import state  # noqa: E402

state.init_db()
from core import worker  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  — {detail}"))
    if not cond:
        _failed += 1


INF = float("-inf")
DAY = 86400.0
NOW = datetime(2026, 9, 10, 18, 30, tzinfo=timezone.utc)
MONO = 1_000_000.0


def beat(component, status, ago_s, now=NOW):
    with state.connect() as c:
        c.execute("INSERT INTO heartbeats (component, ts, status) VALUES (?,?,?) "
                  "ON CONFLICT(component) DO UPDATE SET ts = excluded.ts, status = excluded.status",
                  (component, (now - timedelta(seconds=ago_s)).isoformat(), status))


def task(name, interval=DAY, beat_name=None):
    return {"fn": lambda: {"status": "ok"}, "interval": interval, "name": name, "last": INF, "beat": beat_name}


def due_at_start(t):
    return MONO - t["last"] >= t["interval"]


beat("walker_ran", "ok:ok", 3600)
t1 = task("walker", beat_name="walker_ran")
seeded = worker._seed_from_beats([t1], now_wall=NOW, now_mono=MONO)
ok("a daily walker that finished an hour ago is NOT due when the worker restarts",
   not due_at_start(t1) and seeded == ["walker"], str((t1["last"], seeded)))
ok("...and it is due again exactly when its interval since that beat has passed",
   abs((MONO + DAY - 3600) - t1["last"] - DAY) < 1e-6, str(t1["last"]))

beat("old", "ok:ok", DAY + 60)
t2 = task("old_walker", beat_name="old")
worker._seed_from_beats([t2], now_wall=NOW, now_mono=MONO)
ok("a beat older than the interval leaves it due at start, so a box that was down catches up",
   due_at_start(t2) and t2["last"] == INF)

t3, t3b = task("never_beat", beat_name="no_such_component"), task("beatless")
worker._seed_from_beats([t3, t3b], now_wall=NOW, now_mono=MONO)
ok("no beat on record: due at start, as on a brand-new box", t3["last"] == INF)
ok("a periodic registered without beat= is untouched", t3b["last"] == INF)

for word in ("disabled", "off", "unconfigured", "skipped", "quiet", "paused", "no_operator"):
    beat(f"dormant_{word}", f"ok:{word}", 3600)
    t = task(f"dormant_{word}", beat_name=f"dormant_{word}")
    worker._seed_from_beats([t], now_wall=NOW, now_mono=MONO)
    ok(f"a beat that says it did no work (ok:{word}) stays due at start, so a machine a deploy enables runs then",
       t["last"] == INF)

beat("plain", "ok", 600)
beat("capped", "ok:capped", 600)
t5, t5b = task("plain", beat_name="plain"), task("capped", beat_name="capped")
worker._seed_from_beats([t5, t5b], now_wall=NOW, now_mono=MONO)
ok("a plain 'ok' beat (a periodic that returns no dict) counts as a completed run", not due_at_start(t5))
ok("a run that stopped at a vendor cap still counts: re-running it at restart is the spend this prevents",
   not due_at_start(t5b))

beat("future", "ok:ok", -600)
with state.connect() as c:
    c.execute("INSERT INTO heartbeats (component, ts, status) VALUES ('garbled', 'not a time', 'ok:ok')")
    c.execute("INSERT INTO heartbeats (component, ts, status) VALUES ('foreign', ?, 'FAIL')",
              ((NOW - timedelta(seconds=60)).isoformat(),))
t6, t6b, t6c = task("future", beat_name="future"), task("garbled", beat_name="garbled"), task("foreign", beat_name="foreign")
worker._seed_from_beats([t6, t6b, t6c], now_wall=NOW, now_mono=MONO)
ok("a beat from the future (clock skew) is ignored: due at start", t6["last"] == INF)
ok("an unreadable timestamp is ignored: due at start", t6b["last"] == INF)
ok("a status this loop never writes is ignored: due at start", t6c["last"] == INF)

t7 = task("running", beat_name="walker_ran")
t7["last"] = MONO - 5
worker._seed_from_beats([t7], now_wall=NOW, now_mono=MONO)
ok("a task that already ran in this process is never moved by a late seed", t7["last"] == MONO - 5)

_saved_get = state.get_heartbeats


def _boom():
    raise RuntimeError("no such table: heartbeats")


state.get_heartbeats = _boom
try:
    t8 = task("t8", beat_name="walker_ran")
    out = worker._seed_from_beats([t8], now_wall=NOW, now_mono=MONO)
    ok("an unreadable heartbeats table seeds nothing and raises nothing: everything runs at start",
       out == [] and t8["last"] == INF, str(out))
finally:
    state.get_heartbeats = _saved_get


class _Stop(Exception):
    pass


ran = []
beat("loop_seeded", "ok:ok", 3600, now=datetime.now(timezone.utc))
tasks = [
    {"fn": lambda: ran.append("seeded") or {"status": "ok"}, "interval": DAY, "name": "loop_seeded",
     "last": INF, "beat": "loop_seeded"},
    {"fn": lambda: ran.append("fresh") or {"status": "ok"}, "interval": DAY, "name": "loop_fresh",
     "last": INF, "beat": None},
]
_saved = (list(worker.PERIODIC), worker.time.sleep, worker._halted)


def _stop(_s):
    raise _Stop()


worker.PERIODIC[:] = tasks
worker.time.sleep = _stop
worker._halted = lambda: False
try:
    worker._periodic_loop()
except _Stop:
    pass
finally:
    worker.PERIODIC[:] = _saved[0]
    worker.time.sleep = _saved[1]
    worker._halted = _saved[2]
ok("THE LOOP SEEDS BEFORE ITS FIRST PASS: after a restart the walker that ran an hour ago is skipped and a new one runs",
   ran == ["fresh"], str(ran))

print(f"{_failed} FAILED" if _failed else "all ok")
sys.exit(1 if _failed else 0)
