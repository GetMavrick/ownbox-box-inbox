"""The nightly backup keeps tonight's copy and rotates only dailies.

Measured 2026-09-15 on the live box: seven hand-made pre-deploy copies named `aios-pre-…`/`aios-predeploy-…`
sort after `aios-2026-…`, and the old rotation kept "the newest seven aios-*.db" by name — so every night it
deleted the backup it had just written, then crashed. This builds exactly that directory and runs the real script.

Run: python tests/test_backup_db.py
"""
import importlib.util
import os
import pathlib
import sqlite3
import sys
import tempfile
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_TMP = pathlib.Path(tempfile.mkdtemp())
os.environ.setdefault("AIOS_HERMETIC_TEST", "1")
os.environ["AIOS_DB_PATH"] = str(_TMP / "aios.db")

FAILS: list = []


def ok(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


spec = importlib.util.spec_from_file_location("backup_db", ROOT / "scripts" / "backup_db.py")
backup_db = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup_db)

live = sqlite3.connect(os.environ["AIOS_DB_PATH"])
live.execute("CREATE TABLE marker (v TEXT)")
live.execute("INSERT INTO marker VALUES ('tonight')")
live.commit()
live.close()

backups = _TMP / "backups"
backups.mkdir()
by_hand = ["aios-pre-deploy-20260915T072305.db", "aios-pre-deploy-20260915T072412.db",
           "aios-pre-deploy-20260915T072543.db", "aios-pre-e54ea3b8-20260912T204121.db",
           "aios-pre-fa7da55c-20260913T014826.db", "aios-predeploy-146-20260706-053449.db",
           "aios-predeploy-20260703-163029.db", "pre-release-20260915-184209.db"]
for name in by_hand:
    (backups / name).write_bytes(b"a person made this")
old_dailies = [f"aios-2000-01-{d:02d}.db" for d in range(1, 9)]          # eight, all older than tonight
for name in old_dailies:
    (backups / name).write_bytes(b"an old daily")

print("\n— the live box's backups directory, as measured —")
rc = backup_db.main()
tonight = backups / f"aios-{date.today().isoformat()}.db"
ok("the run succeeds", rc == 0)
ok("tonight's backup is on disk", tonight.is_file())
got = sqlite3.connect(str(tonight)).execute("SELECT v FROM marker").fetchone() if tonight.is_file() else None
ok("...and it is a real copy of the live database", got == ("tonight",), str(got))
ok("every file a person made is untouched", all((backups / n).read_bytes() == b"a person made this" for n in by_hand))
dailies = sorted(p.name for p in backups.iterdir() if backup_db.DAILY.match(p.name))
ok("exactly seven dailies are kept: tonight and the six newest before it",
   dailies == sorted(old_dailies[-6:] + [tonight.name]), str(dailies))

print("\n— the same night twice —")
ok("a second run the same day keeps tonight's copy", backup_db.main() == 0 and tonight.is_file())

print("\n— rotate() on its own —")
d = pathlib.Path(tempfile.mkdtemp())
for n in ["aios-2000-01-01.db", "aios-2000-01-02.db", "aios-notes.db", "aios-2000-01-03.db.bak"]:
    (d / n).write_bytes(b"x")
gone = backup_db.rotate(d, keep=1, keep_path=d / "aios-2000-01-01.db")
ok("it never deletes the path it is told to keep, even when that sorts oldest",
   (d / "aios-2000-01-01.db").is_file() and [p.name for p in gone] == [], str([p.name for p in gone]))
gone = backup_db.rotate(d, keep=1)
ok("...and without it, only the older daily goes; names that are not dailies stay",
   [p.name for p in gone] == ["aios-2000-01-01.db"] and (d / "aios-notes.db").is_file()
   and (d / "aios-2000-01-03.db.bak").is_file(), str([p.name for p in gone]))

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
