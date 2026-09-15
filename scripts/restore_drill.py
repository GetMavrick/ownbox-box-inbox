"""Prove the off-box backup can come back.

A backup nobody has restored is not a backup, it is a hope. This restores the replica into a
TEMPORARY file, checks its integrity, compares every shared table's row count against the live
database, reads the tenant overlay's backup rows BACK and hashes them (core/box_config_backup.py),
and deletes the copy. The live database is never touched or overwritten.

Litestream expands ${ENV} in its config at load time, so it needs the same environment the
service reads. On the box:

    systemd-run --quiet --wait --pipe --collect \\
      -p EnvironmentFile=/opt/aios/.env -p WorkingDirectory=/opt/aios \\
      /opt/aios/.venv/bin/python scripts/restore_drill.py

Exit 0 only when the copy restored, passed integrity_check, and matched every shared count.
Run it after any change to replication, and once before you hand a box to anybody.
"""
import argparse
import hashlib
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
_ENV_NEEDED = ("OBJECT_STORE_BUCKET", "OBJECT_STORE_ENDPOINT", "OBJECT_STORE_KEY", "OBJECT_STORE_SECRET")


def _tables(db: pathlib.Path) -> set:
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as c:
        return {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def _count(db: pathlib.Path, table: str) -> int:
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as c:
        return c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


# Mirrors core/box_config_backup.PATHS. This script imports nothing from core on purpose (it has
# to run on a half-built box), so the one overlay path it expects to find is named here as well.
_OVERLAYS = ("my/settings.yaml",)


def _newest_overlays(db: pathlib.Path) -> dict:
    """{path: {sha, bytes, at}} for the newest VALID captured version of each path, hashed from the
    body itself rather than trusted from the sha256 column. {} when the table is absent: a box from
    before the overlay backup existed."""
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                         "AND name='box_config_backup'").fetchone():
            return {}
        out = {}
        for path, body, at in c.execute("SELECT path, body, verified_at FROM box_config_backup "
                                        "WHERE valid = 1 ORDER BY verified_at, id"):
            data = (body or "").encode("utf-8")
            out[path] = {"sha": hashlib.sha256(data).hexdigest(), "bytes": len(data), "at": at}
        return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "deploy" / "litestream.yml"))
    ap.add_argument("--db", default=str(ROOT / "aios.db"))
    ap.add_argument("--litestream", default="litestream", help="the binary (a test injects a stub)")
    ap.add_argument("--keep", action="store_true", help="leave the restored copy on disk")
    ap.add_argument("--root", default=str(ROOT),
                    help="the install root the overlay paths are relative to (a test points it at a fixture)")
    a = ap.parse_args(argv)

    cfg, live = pathlib.Path(a.config), pathlib.Path(a.db)
    if not cfg.exists():
        print(f"no replication config at {cfg} — this box does not replicate off-box", file=sys.stderr)
        return 1
    if not live.exists():
        print(f"no live database at {live}", file=sys.stderr)
        return 1
    missing = [k for k in _ENV_NEEDED if not (os.environ.get(k) or "").strip()]
    if missing:
        print("the replication credentials are not in this environment: " + ", ".join(missing)
              + "\nrun it with the service's own env:\n"
                "  systemd-run --quiet --wait --pipe --collect -p EnvironmentFile=/opt/aios/.env"
                " -p WorkingDirectory=/opt/aios /opt/aios/.venv/bin/python scripts/restore_drill.py",
              file=sys.stderr)
        return 1

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="aios-restore-drill-"))
    out = tmp / "restored.db"
    print(f"restoring {live} from its replica into {out} …")
    try:
        r = subprocess.run([a.litestream, "restore", "-config", str(cfg), "-o", str(out), str(live)],
                           capture_output=True, text=True, timeout=1800)
        if r.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            print("RESTORE FAILED — the off-box backup did not come back:", file=sys.stderr)
            print((r.stderr or r.stdout or "").strip()[-600:], file=sys.stderr)
            return 1

        try:
            with sqlite3.connect(f"file:{out}?mode=ro", uri=True) as c:
                verdict = c.execute("PRAGMA integrity_check;").fetchone()[0]
        except sqlite3.DatabaseError as e:
            print(f"RESTORED FILE IS NOT A DATABASE: {e}", file=sys.stderr)
            return 1
        print(f"  integrity_check: {verdict}")
        if verdict != "ok":
            print("RESTORED COPY IS CORRUPT", file=sys.stderr)
            return 1

        live_t, rest_t = _tables(live), _tables(out)
        shared = sorted(live_t & rest_t)
        drift, checked = [], 0
        for t in shared:
            lc, rc = _count(live, t), _count(out, t)
            checked += 1
            if lc != rc:
                drift.append(f"{t}: live={lc} restored={rc}")
        only_live = sorted(live_t - rest_t)
        print(f"  tables compared: {checked}   rows matched: {'yes' if not drift else 'NO'}")
        if only_live:
            # A table written since the last WAL sync is expected to be young, not absent —
            # say it out loud rather than quietly comparing nothing.
            print(f"  present live but NOT in the copy: {', '.join(only_live)}")
        for d in drift:
            print(f"  DRIFT {d}", file=sys.stderr)

        # THE TENANT OVERLAY (core/box_config_backup.py). The counts above say its table came back;
        # this reads the rows BACK and hashes the bodies, because a copy can hold the right number of
        # rows and the wrong bytes. Copy and live disagreeing is a failed backup. The file on disk
        # moving after its last capture is not: that is the capture interval, said out loud.
        live_o, rest_o = _newest_overlays(live), _newest_overlays(out)
        overlay_drift = []
        for path in sorted(live_o):
            lv, rv = live_o[path], rest_o.get(path)
            if not rv or rv["sha"] != lv["sha"]:
                got = rv["sha"][:12] if rv else "absent"
                overlay_drift.append(f"{path}: live newest {lv['sha'][:12]}, restored newest {got}")
                continue
            print(f"  config backup {path}: the copy returns {rv['bytes']} bytes, "
                  f"sha256 {rv['sha'][:12]}, captured {rv['at']}")
            disk = pathlib.Path(a.root) / path
            if disk.is_file():
                if hashlib.sha256(disk.read_bytes()).hexdigest() == rv["sha"]:
                    print(f"    …identical to {path} on disk")
                else:
                    print(f"    …{path} on disk has changed since that capture; the next one takes it"
                          " (daily, and at every worker start)")
        for path in _OVERLAYS:
            if (pathlib.Path(a.root) / path).is_file() and path not in live_o:
                print(f"  config backup {path}: present on disk, NOT in the backup table yet")
        for d in overlay_drift:
            print(f"  DRIFT config backup {d}", file=sys.stderr)
        if drift or only_live or overlay_drift:
            print("RESTORE DRILL FAILED — the copy is not the database", file=sys.stderr)
            return 1
        print("RESTORE DRILL PASSED — the backup came back, intact, complete.")
        return 0
    finally:
        if a.keep:
            print(f"  copy kept at {out}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
