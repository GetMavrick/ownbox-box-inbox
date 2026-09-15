"""The restore drill is itself proven, with a stubbed litestream — no network, no bucket.

A backup nobody has restored is not a backup. The drill exists so a box owner can prove the
off-box replica comes back; this suite proves the DRILL would notice if it did not. Each case
swaps in a stub binary that produces a specific kind of restored file.
"""
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile

os.environ.setdefault("AIOS_HERMETIC_TEST", "1")
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DRILL = ROOT / "scripts" / "restore_drill.py"

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  — {detail}"))
    if not cond:
        _failed += 1


def _db(path, rows=3, table="widgets"):
    with sqlite3.connect(path) as c:
        c.execute(f"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY, v TEXT)")
        c.executemany(f"INSERT INTO {table} (v) VALUES (?)", [(f"r{i}",) for i in range(rows)])
    return path


def _stub(tmp, body):
    """A fake `litestream` that reads `-o <path>` and writes whatever the case needs."""
    p = tmp / "fake_litestream.py"
    p.write_text('import sys, pathlib\n'
                 'out = sys.argv[sys.argv.index("-o") + 1]\n' + body)
    sh = tmp / "fake_litestream"
    sh.write_text(f'#!/bin/sh\nexec {sys.executable} {p} "$@"\n')
    sh.chmod(0o755)
    return str(sh)


def _run(tmp, stub_body, cfg=None, live=None, root=None):
    env = dict(os.environ)
    env.update({k: "set" for k in ("OBJECT_STORE_BUCKET", "OBJECT_STORE_ENDPOINT",
                                   "OBJECT_STORE_KEY", "OBJECT_STORE_SECRET")})
    return subprocess.run(
        [sys.executable, str(DRILL), "--config", str(cfg or (tmp / "ls.yml")),
         "--db", str(live or (tmp / "live.db")), "--litestream", _stub(tmp, stub_body)]
        + (["--root", str(root)] if root else []),
        capture_output=True, text=True, env=env, timeout=180)


tmp = pathlib.Path(tempfile.mkdtemp())
(tmp / "ls.yml").write_text("dbs:\n  - path: live.db\n")
_db(tmp / "live.db", rows=3)

# 1. the happy path: the copy is the database
r = _run(tmp, 'import shutil; shutil.copy(pathlib.Path(__file__).parent / "live.db", out)\n')
ok("a faithful copy PASSES the drill (exit 0, integrity ok, counts matched)",
   r.returncode == 0 and "RESTORE DRILL PASSED" in r.stdout, (r.stdout + r.stderr)[-300:])
ok("…and the drill cleans up after itself (no copy left behind)",
   not list(pathlib.Path(tempfile.gettempdir()).glob("aios-restore-drill-*/restored.db")),
   "a restored copy was left on disk")

# 2. a copy that is short of rows — the failure this drill exists to catch
r = _run(tmp, 'import sqlite3, shutil; shutil.copy(pathlib.Path(__file__).parent / "live.db", out)\n'
              'c = sqlite3.connect(out); c.execute("DELETE FROM widgets WHERE id=1"); c.commit(); c.close()\n')
ok("a copy MISSING ROWS fails, naming the table and both counts",
   r.returncode == 1 and "DRIFT widgets: live=3 restored=2" in r.stderr, (r.stdout + r.stderr)[-300:])

# 3. a copy missing a whole table
r = _run(tmp, 'import sqlite3; c = sqlite3.connect(out); c.execute("CREATE TABLE other (id INTEGER)"); c.commit(); c.close()\n')
ok("a copy missing a TABLE fails and says which one is absent",
   r.returncode == 1 and "widgets" in r.stdout and "NOT in the copy" in r.stdout, (r.stdout + r.stderr)[-300:])

# 4. a corrupt file
r = _run(tmp, 'pathlib.Path(out).write_bytes(b"this is not a database")\n')
ok("a CORRUPT copy fails, and says it is not a database",
   r.returncode == 1 and "NOT A DATABASE" in r.stderr.upper(), (r.stdout + r.stderr)[-300:])

# 5. litestream itself failing / producing nothing
r = _run(tmp, 'import sys; print("no generation found", file=sys.stderr); sys.exit(1)\n')
ok("a restore that produces NOTHING fails loudly, echoing the tool",
   r.returncode == 1 and "RESTORE FAILED" in r.stderr and "no generation" in r.stderr, (r.stdout + r.stderr)[-300:])

# 6. refusals that are not the backup's fault
r = _run(tmp, 'pass\n', cfg=tmp / "nope.yml")
ok("no replication config → says this box does not replicate off-box, exit 1",
   r.returncode == 1 and "does not replicate" in r.stderr, (r.stdout + r.stderr)[-200:])
env = {k: v for k, v in os.environ.items() if not k.startswith("OBJECT_STORE_")}
r2 = subprocess.run([sys.executable, str(DRILL), "--config", str(tmp / "ls.yml"), "--db", str(tmp / "live.db")],
                    capture_output=True, text=True, env=env, timeout=120)
ok("no credentials in the environment → names them AND gives the exact command to use",
   r2.returncode == 1 and "OBJECT_STORE_BUCKET" in r2.stderr and "EnvironmentFile=" in r2.stderr,
   (r2.stdout + r2.stderr)[-260:])

# 7. the tenant overlay (core/box_config_backup.py): the drill reads the rows BACK and hashes them
import hashlib  # noqa: E402
ov = tmp / "ovroot"
(ov / "my").mkdir(parents=True, exist_ok=True)
_overlay = "# the owner's own note, kept by the backup\ngtm:\n  daily_target: 20\nvoice:\n  greeting: \"ça va\"\n"
(ov / "my" / "settings.yaml").write_bytes(_overlay.encode("utf-8"))
_db(tmp / "live_ov.db", rows=2)
with sqlite3.connect(tmp / "live_ov.db") as c:
    # Only the columns the drill reads: its contract, not a copy of core's full schema.
    c.execute("CREATE TABLE box_config_backup (id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT, "
              "body TEXT, verified_at TEXT, valid INTEGER)")
    c.execute("INSERT INTO box_config_backup (path, body, verified_at, valid) VALUES (?, ?, ?, 1)",
              ("my/settings.yaml", _overlay, "2026-09-10T06:00:00+00:00"))
_copy_ov = 'import shutil; shutil.copy(pathlib.Path(__file__).parent / "live_ov.db", out)\n'

r = _run(tmp, _copy_ov, live=tmp / "live_ov.db", root=ov)
ok("the overlay row comes BACK: the restored body hashes identical to the file on disk, and PASSES",
   r.returncode == 0 and "RESTORE DRILL PASSED" in r.stdout and "identical to my/settings.yaml on disk" in r.stdout,
   (r.stdout + r.stderr)[-400:])

r = _run(tmp, _copy_ov + 'import sqlite3; c = sqlite3.connect(out); '
                        'c.execute("UPDATE box_config_backup SET body = body || ?", ("# tampered",)); '
                        'c.commit(); c.close()\n',
         live=tmp / "live_ov.db", root=ov)
ok("the right NUMBER of overlay rows with the WRONG BYTES fails, naming the path",
   r.returncode == 1 and "DRIFT config backup my/settings.yaml" in r.stderr, (r.stdout + r.stderr)[-400:])

(ov / "my" / "settings.yaml").write_bytes((_overlay + "machines:\n  enabled: true\n").encode("utf-8"))
r = _run(tmp, _copy_ov, live=tmp / "live_ov.db", root=ov)
ok("an overlay edited AFTER its last capture still PASSES (the capture interval is not a replication fault), and says so",
   r.returncode == 0 and "has changed since that capture" in r.stdout, (r.stdout + r.stderr)[-400:])

r = _run(tmp, 'import shutil; shutil.copy(pathlib.Path(__file__).parent / "live.db", out)\n', root=ov)
ok("an overlay on disk that was never captured is said out loud, and an older box still PASSES",
   r.returncode == 0 and "NOT in the backup table" in r.stdout, (r.stdout + r.stderr)[-400:])

print(f"{_failed} FAILED" if _failed else "all ok")
sys.exit(1 if _failed else 0)
