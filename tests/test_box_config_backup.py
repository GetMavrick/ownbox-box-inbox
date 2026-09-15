"""The tenant overlay's off-box copy: byte-exact, one row per distinct version, and wired.

`my/settings.yaml` is gitignored by design, so the only way it leaves the box is as rows in the
database Litestream already replicates. These cases prove the rows ARE the file (bytes, comments
and all), that an unchanged file costs no new row, that history survives a revert, that a broken
or unreadable file is handled without lying, and that the periodic and its watchdog beat are
actually wired rather than merely defined.
"""
import hashlib
import os
import pathlib
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("AIOS_HERMETIC_TEST", "1")
_TMP = pathlib.Path(tempfile.mkdtemp())
os.environ["AIOS_DB_PATH"] = str(_TMP / "box_config_backup.db")
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import state  # noqa: E402

state.init_db()
from core import box_config_backup as bcb  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  — {detail}"))
    if not cond:
        _failed += 1


def rows(path="my/settings.yaml"):
    with state.connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM box_config_backup WHERE path = ? ORDER BY id", (path,))]


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


T = [f"2026-09-1{i}T06:00:00+00:00" for i in range(8)]
box = _TMP / "box"
(box / "my").mkdir(parents=True)
f = box / "my" / "settings.yaml"

A = ("# The owner's own note: comments are exactly what a re-serialised YAML would lose.\n"
     "vendors:\n  email_verify:\n    monthly_unit_cap: 1500   # raised on the owner's word\n"
     "customer_voice:\n  site_url: \"https://example.com\"\n"
     "dash:\n  labels: {health-and-wellness: [\"job:\", \"cf:\", \"li:\"]}\n"
     "voice:\n  greeting: \"Bonjour, ça va\"\n")
f.write_bytes(A.encode("utf-8"))

# 1. the first capture is the file, byte for byte
r = bcb.capture(root=box, now=T[0])
R = rows()
ok("the first capture writes one row", r["captured"] == 1 and len(R) == 1, str(r))
ok("...whose body is the file BYTE FOR BYTE (stored text, the file and the sha256 column all agree)",
   bool(R) and sha(R[0]["body"].encode("utf-8")) == sha(f.read_bytes()) == R[0]["sha256"], str(R[:1]))
ok("...comments survive: the text is stored, never a re-serialised YAML",
   bool(R) and "comments are exactly what" in R[0]["body"] and "# raised on the owner's word" in R[0]["body"])
ok("...non-ASCII survives", bool(R) and "ça va" in R[0]["body"])
ok("...bytes, valid and both stamps are recorded",
   bool(R) and R[0]["bytes"] == len(A.encode("utf-8")) and R[0]["valid"] == 1
   and R[0]["captured_at"] == T[0] == R[0]["verified_at"], str(R[:1]))

# 2. unchanged: no new row, verified_at advances, captured_at holds
r = bcb.capture(root=box, now=T[1])
R = rows()
ok("an UNCHANGED file writes no new row", r["unchanged"] == 1 and r["captured"] == 0 and len(R) == 1, str(r))
ok("...but advances verified_at and keeps captured_at",
   R[0]["verified_at"] == T[1] and R[0]["captured_at"] == T[0], str(R[0]))

# 3. changed: a second row, the first kept as history
B = A.replace("1500", "2000")
f.write_bytes(B.encode("utf-8"))
r = bcb.capture(root=box, now=T[2])
ok("a CHANGED file writes a second row and keeps the first as history",
   r["captured"] == 1 and len(rows()) == 2, str(r))
n = bcb.newest()
ok("...and newest() returns the changed text", bool(n) and n["body"] == B, str(n))

# 4. revert A -> B -> A: no duplicate, newest is A again with its original captured_at
f.write_bytes(A.encode("utf-8"))
r = bcb.capture(root=box, now=T[3])
ok("REVERTING to an earlier version writes no duplicate: one row per distinct content",
   len(rows()) == 2 and r["unchanged"] == 1, str(r))
n = bcb.newest()
ok("...newest() is the reverted text, carrying its ORIGINAL captured_at",
   bool(n) and n["body"] == A and n["captured_at"] == T[0] and n["verified_at"] == T[3], str(n))

# 5. unparseable: stored as what is on disk, marked, and skipped by newest()
bad = "gtm: [unclosed\n  - nope: {\n"
f.write_bytes(bad.encode("utf-8"))
r = bcb.capture(root=box, now=T[4])
R = rows()
ok("a file that does NOT parse is still stored, since it is what is on disk, marked valid=0",
   len(R) == 3 and any(x["valid"] == 0 and x["body"] == bad for x in R) and r["invalid"] == 1, str(r))
ok("...newest() skips it by default, so a restorer never takes a broken overlay",
   (bcb.newest() or {}).get("body") == A)
ok("...while newest(valid_only=False) still shows it", (bcb.newest(valid_only=False) or {}).get("body") == bad)

# 6. the recovery query in the module docstring returns the newest valid bytes, exactly
with sqlite3.connect(os.environ["AIOS_DB_PATH"]) as c:
    body = c.execute("SELECT body FROM box_config_backup WHERE path = 'my/settings.yaml' AND valid = 1 "
                     "ORDER BY verified_at DESC, id DESC LIMIT 1").fetchone()[0]
recovered = _TMP / "recovered.yaml"
recovered.write_bytes(body.encode("utf-8"))
ok("the RECOVERY query restores the newest valid overlay byte for byte",
   sha(recovered.read_bytes()) == sha(A.encode("utf-8")))
ok("...and the docstring carries that same query", "ORDER BY verified_at DESC, id DESC LIMIT 1" in (bcb.__doc__ or ""))

# 7. not UTF-8: refused rather than stored as a lossy copy
before = len(rows())
f.write_bytes(b"gtm:\n  x: \xff\xfe\n")
r = bcb.capture(root=box, now=T[5])
ok("a file that is not UTF-8 is REFUSED rather than stored lossily, and says so",
   r["unreadable"] == 1 and r["status"] == "unreadable" and len(rows()) == before, str(r))
f.write_bytes(A.encode("utf-8"))

# 8. no overlay on this box: skipped, nothing written, no exception
bare = _TMP / "bare_box"
(bare / "my").mkdir(parents=True)
before = len(rows())
r = bcb.capture(root=bare, now=T[6])
ok("a box with NO overlay reports 'skipped', which the watchdog reads as dormant, and writes nothing",
   r["status"] == "skipped" and r["missing"] == 1 and len(rows()) == before, str(r))

# 9. wired: registered daily with a beat, due at start, and imported by the worker
from core import worker  # noqa: E402
t = next((t for t in worker.PERIODIC if t["name"] == "box_config_backup"), None)
ok("registered as a DAILY periodic with its own completion beat",
   bool(t) and t["interval"] == 86400 and t.get("beat") == "box_config_backup", str(t))
ok("...whose first pass is due at worker start (last = -inf), so every deploy captures",
   bool(t) and t["last"] == float("-inf"), str(t))
ok("...and the worker imports it at startup, since registering at import means nothing if nobody imports it",
   "from core import box_config_backup" in (ROOT / "core" / "worker.py").read_text(encoding="utf-8"))

# 10. the watchdog pages on a stale beat and stays quiet when there is nothing to back up
from core import watchdog  # noqa: E402


def beat(status: str, age_s: float) -> None:
    ts = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).isoformat()
    with state.connect() as c:
        c.execute("INSERT INTO heartbeats (component, ts, status) VALUES ('box_config_backup', ?, ?) "
                  "ON CONFLICT(component) DO UPDATE SET ts = excluded.ts, status = excluded.status", (ts, status))


good, msg = watchdog._probe_box_config_backup()
ok("no beat yet is not a page (a fresh box has not run it)", good and "no run yet" in msg, msg)
beat("ok:ok", 3 * 86400)
good, msg = watchdog._probe_box_config_backup()
ok("a beat three days old PAGES, and says what is at stake", not good and "no longer being copied off-box" in msg, msg)
beat("ok:skipped", 3 * 86400)
good, msg = watchdog._probe_box_config_backup()
ok("...but a box with no overlay stays quiet however old the beat", good and "dormant" in msg, msg)
beat("ok:ok", 3600)
good, msg = watchdog._probe_box_config_backup()
ok("a fresh beat is healthy", good, msg)
ok("...and the probe is actually in run_once's table",
   '"box_config_backup":     _probe_box_config_backup(),' in (ROOT / "core" / "watchdog.py").read_text(encoding="utf-8"))

# 11. boot(): the capture a deploy needs, without waiting for the shared periodic queue
f.write_bytes((A + "machines:\n  enabled: true\n").encode("utf-8"))
before = len(rows())
th = bcb.boot(root=box)
th.join(15)
ok("boot() captures on its own thread, without waiting for the periodic queue",
   not th.is_alive() and len(rows()) == before + 1, f"alive={th.is_alive()} rows {before} -> {len(rows())}")
with state.connect() as c:
    hb = c.execute("SELECT status, ts FROM heartbeats WHERE component = 'box_config_backup'").fetchone()
age = (datetime.now(timezone.utc) - datetime.fromisoformat(hb["ts"])).total_seconds() if hb else None
ok("...and beats like the periodic, so the watchdog sees a boot capture as fresh",
   bool(hb) and hb["status"] == "ok:ok" and age is not None and age < 60, str(dict(hb) if hb else None))
ok("the worker CALLS boot() at start, not only imports the module",
   "box_config_backup.boot()" in (ROOT / "core" / "worker.py").read_text(encoding="utf-8"))
f.write_bytes(A.encode("utf-8"))

print(f"{_failed} FAILED" if _failed else "all ok")
sys.exit(1 if _failed else 0)
