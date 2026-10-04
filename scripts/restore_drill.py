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

ONE COMMAND, ONE VERDICT (plan #1857 launch bar 9): `bash /opt/aios/scripts/restore_drill.sh` does the above with the
service's own environment, and `--copy FILE` compares a copy already restored (last night's) instead. It prints, per
table, rows live and in the copy, the newest row time in each and what changed since the copy's moment; then rows read
back from the copy and matched against live by hash; then RESTORE DRILL: GREEN, or RED with each reason. Never a
row's contents. Rows live gained after the copy (above its highest rowid) and rows deleted since are said, not failed:
a box keeps writing while the drill runs. RED: not a database, corrupt, a different schema version, a table missing
or empty, a row live had at the copy's moment missing from it, or a copy more than 26 hours behind live.

Exit 0 only when GREEN. Run it after any change to replication, and once before you hand a box to anybody.

EVERY WEEK, ON THE BOX ITSELF (launch bar 9's last step): nobody can shell into a sold box, so aios-restore-drill.timer
runs `restore_drill.sh --last-night --record`. `--last-night` restores the newest nightly copy (scripts/backup_db.py,
<db dir>/backups/aios-YYYY-MM-DD.db) into a temporary file beside live and compares that; `--record` keeps the verdict
as the heartbeat `backup_restore` (core/restore_check.py), which core.health, the Dashboard and the check-in read.
"""
import argparse
import hashlib
import io
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


# THE COPY'S OWN MOMENT (plan #1857 launch bar 9, OSDev1): a box keeps writing while the drill runs, so live has rows
# the copy cannot have. Exact counts would read red on any busy box. Instead, per table, the copy's newest row time is
# its moment, and every row live had AT OR BEFORE that moment must be in the copy. Rows live gained since are said, and
# rows deleted since (a nightly prune) are said, neither is a failure. A copy older than this is stale.
MAX_LAG_H = 26.0
_TIME_COLUMNS = ("created_at", "ts", "at", "sent_at", "received_at", "claimed_at", "written_at", "set_at")
SAMPLE_TABLES, SAMPLE_ROWS = 5, 3


def _time_column(c, table: str) -> str:
    cols = {r[1] for r in c.execute(f'PRAGMA table_info("{table}")')}
    return next((t for t in _TIME_COLUMNS if t in cols), "")


def _age_h(newer: str, older: str) -> float | None:
    from datetime import datetime
    try:
        a, b = datetime.fromisoformat(str(newer)), datetime.fromisoformat(str(older))
        if (a.tzinfo is None) != (b.tzinfo is None):
            return None
        return (a - b).total_seconds() / 3600
    except (TypeError, ValueError):
        return None


def _row_hash(c, table: str, rowid: int) -> str:
    row = c.execute(f'SELECT * FROM "{table}" WHERE rowid = ?', (rowid,)).fetchone()
    return "" if row is None else hashlib.sha256(repr(tuple(row)).encode()).hexdigest()[:12]


def _overlay_report(live: pathlib.Path, out: pathlib.Path, a) -> list[str]:
    """THE TENANT OVERLAY (core/box_config_backup.py). The counts say its table came back; this reads the rows BACK
    and hashes the bodies, because a copy can hold the right number of rows and the wrong bytes. Copy and live
    disagreeing is a failed backup. The file on disk moving after its last capture is not: that is the capture
    interval, said out loud. -> the reds."""
    live_o, rest_o = _newest_overlays(live), _newest_overlays(out)
    reds = []
    for path in sorted(live_o):
        lv, rv = live_o[path], rest_o.get(path)
        if not rv or rv["sha"] != lv["sha"]:
            got = rv["sha"][:12] if rv else "absent"
            reds.append(f"config backup {path}: live newest {lv['sha'][:12]}, restored newest {got}")
            print(f"  DRIFT config backup {path}: live newest {lv['sha'][:12]}, restored newest {got}", file=sys.stderr)
            continue
        print(f"  config backup {path}: the copy returns {rv['bytes']} bytes, sha256 {rv['sha'][:12]}, "
              f"captured {rv['at']}")
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
    return reds


def _verdict(live: pathlib.Path, out: pathlib.Path, a) -> int:
    """Compare the copy with the live database and print GREEN or RED, with everything compared. Never prints a
    row's contents: table names, counts, times, row ids and hash prefixes only."""
    reds: list[str] = []
    try:
        with sqlite3.connect(f"file:{out}?mode=ro", uri=True) as c:
            integrity = c.execute("PRAGMA integrity_check;").fetchone()[0]
            ver_out = c.execute("PRAGMA user_version;").fetchone()[0]
    except sqlite3.DatabaseError as e:
        print(f"RESTORED FILE IS NOT A DATABASE: {e}", file=sys.stderr)
        print("RESTORE DRILL: RED — the copy is not a database")
        return 1
    print(f"  integrity_check: {integrity}")
    if integrity != "ok":
        print("RESTORED COPY IS CORRUPT", file=sys.stderr)
        print("RESTORE DRILL: RED — the copy is corrupt")
        return 1
    with sqlite3.connect(f"file:{live}?mode=ro", uri=True) as lc, sqlite3.connect(f"file:{out}?mode=ro", uri=True) as oc:
        lc.execute("ATTACH DATABASE ? AS cp", (f"file:{out}?mode=ro",))      # the copy, beside live, read-only
        ver_live = lc.execute("PRAGMA user_version;").fetchone()[0]
        print(f"  schema version: live {ver_live}, copy {ver_out}")
        if ver_out != ver_live:
            reds.append(f"schema version {ver_out} in the copy, {ver_live} live")
        live_t, out_t = _tables(live), _tables(out)
        if live_t - out_t:
            print(f"  present live but NOT in the copy: {', '.join(sorted(live_t - out_t))}")
        for t in sorted(live_t - out_t):
            reds.append(f"{t} is missing from the copy")
        print(f"  {'table':<34} {'live':>9} {'copy':>9}  {'copy newest':<26} {'live newest':<26} said")
        sizes = []
        for t in sorted(live_t & out_t):
            n_live = lc.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            n_out = oc.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            col = _time_column(oc, t)
            said, newest_out, newest_live = "", "", ""
            if col:
                newest_out = oc.execute(f'SELECT max("{col}") FROM "{t}"').fetchone()[0] or ""
                newest_live = lc.execute(f'SELECT max("{col}") FROM "{t}"').fetchone()[0] or ""
            # THE COPY'S MOMENT IS ITS HIGHEST ROWID: rows only ever get higher ids as they are added, so every row live
            # has at or below it was there when the copy was taken, and must be in it.
            # Compared BY ROW ID, not by count: a prune in live and a row lost from the copy cancel out in a count.
            try:
                top = oc.execute(f'SELECT max(rowid) FROM "{t}"').fetchone()[0] or 0
                missing = lc.execute(f'SELECT count(*) FROM "{t}" WHERE rowid <= ? AND rowid NOT IN '
                                     f'(SELECT rowid FROM cp."{t}")', (top,)).fetchone()[0]
                deleted = lc.execute(f'SELECT count(*) FROM cp."{t}" WHERE rowid NOT IN '
                                     f'(SELECT rowid FROM main."{t}")').fetchone()[0]
                gained = lc.execute(f'SELECT count(*) FROM "{t}" WHERE rowid > ?', (top,)).fetchone()[0]
            except sqlite3.OperationalError:                 # a WITHOUT ROWID table: counts, said so
                missing, deleted, gained = max(0, n_live - n_out) if n_out < n_live else 0, 0, 0
            if n_live and not n_out:
                reds.append(f"{t} came back empty ({n_live} rows live)")
                said = "EMPTY"
            elif missing:
                reds.append(f"{t}: the copy has {n_out} rows and lacks {missing} live had at the copy's moment")
                print(f"  DRIFT {t}: live={n_out + missing - deleted} restored={n_out}", file=sys.stderr)
                said = f"MISSING {missing}"
            else:
                said = ", ".join(x for x in (f"+{gained} since" if gained else "",
                                            f"{deleted} deleted since" if deleted else "") if x) or "same"
            lag = _age_h(newest_live, newest_out) if (col and newest_live and newest_out) else None
            if lag is not None and lag > a.max_lag_hours:
                reds.append(f"{t}: the copy's newest row is {lag:.0f} hours behind live")
                said += f", STALE {lag:.0f}h"
            print(f"  {t:<34} {n_live:>9} {n_out:>9}  {str(newest_out)[:26]:<26} {str(newest_live)[:26]:<26} {said}")
            sizes.append((n_out, t))
        # READ BACK: rows from the biggest tables, out of the copy, matched by hash against live. Information,
        # not a verdict: a row a person has edited since the copy is "changed since", and that is fine.
        print("  read back (row id: copy hash / live hash):")
        for n, t in sorted(sizes, reverse=True)[:SAMPLE_TABLES]:
            if not n:
                continue
            ids = [r[0] for r in oc.execute(f'SELECT rowid FROM "{t}" ORDER BY rowid')]
            pick = sorted({ids[0], ids[len(ids) // 2], ids[-1]})[:SAMPLE_ROWS]
            got = []
            for rid in pick:
                h_out, h_live = _row_hash(oc, t, rid), _row_hash(lc, t, rid)
                got.append(f"{rid}: {h_out} / {h_live or '-'} "
                           + ("same" if h_out == h_live else "changed since" if h_live else "gone since"))
            print(f"    {t}: " + "; ".join(got))
    overlay_red = _overlay_report(live, out, a)
    reds += overlay_red
    if reds:
        print("RESTORE DRILL: RED — " + "; ".join(reds[:8]))
        print("RESTORE DRILL FAILED — the copy is not the database", file=sys.stderr)
        return 1
    print("RESTORE DRILL: GREEN — the backup came back, intact, with every row live had at the copy's moment.")
    print("RESTORE DRILL PASSED — the backup came back, intact, complete.")
    return 0


class _Tee(io.TextIOBase):
    """Everything printed goes where it always went, and is kept, so --record can read the verdict line."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for st in self.streams:
            st.write(text)
        return len(text)

    def flush(self):
        for st in self.streams:
            st.flush()


def _reasons(said: str) -> list[str]:
    """The RED line's reasons, or the last thing said when the drill stopped before a verdict."""
    lines = [x.strip() for x in said.splitlines() if x.strip()]
    red = [x for x in lines if x.startswith("RESTORE DRILL: RED")]
    if red:
        return [r.strip() for r in red[-1].split("—", 1)[-1].split(";") if r.strip()]
    return [lines[-1][:160]] if lines else []


def _record(rc: int, said: str) -> None:
    """Keep the verdict as the heartbeat `backup_restore` (core/restore_check.py). Never fails the drill."""
    try:
        sys.path.insert(0, str(ROOT))
        from core import restore_check
        restore_check.record(rc == 0, [] if rc == 0 else _reasons(said))
    except Exception as e:                               # noqa: BLE001 — the verdict printed stands either way
        print(f"  the verdict could not be recorded: {type(e).__name__}", file=sys.stderr)


def _last_night(live: pathlib.Path) -> pathlib.Path | None:
    """The newest nightly copy beside the live database (scripts/backup_db.py), or None."""
    found = sorted((live.resolve().parent / "backups").glob("aios-????-??-??.db"))
    return found[-1] if found else None


def main(argv=None) -> int:
    if "--record" not in (sys.argv[1:] if argv is None else argv):
        return _main(argv)
    keep = io.StringIO()
    out, err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = _Tee(out, keep), _Tee(err, keep)
    try:
        rc = _main(argv)
    except Exception as e:                               # noqa: BLE001 — a drill that crashed is a red drill
        print(f"RESTORE DRILL: RED — the drill stopped: {type(e).__name__}", file=sys.stderr)
        rc = 1
    finally:
        sys.stdout, sys.stderr = out, err
    _record(rc, keep.getvalue())
    return rc


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "deploy" / "litestream.yml"))
    ap.add_argument("--db", default=str(ROOT / "aios.db"))
    ap.add_argument("--litestream", default="litestream", help="the binary (a test injects a stub)")
    ap.add_argument("--keep", action="store_true", help="leave the restored copy on disk")
    ap.add_argument("--copy", default="", help="compare THIS already-restored copy instead of restoring the replica")
    ap.add_argument("--max-lag-hours", type=float, default=MAX_LAG_H,
                    help=f"red when the copy's newest row is older than live's by more than this (default {MAX_LAG_H})")
    ap.add_argument("--last-night", action="store_true",
                    help="restore the newest nightly copy (<db dir>/backups/aios-YYYY-MM-DD.db) and compare that")
    ap.add_argument("--record", action="store_true",
                    help="keep the verdict as the heartbeat backup_restore (core.health, the Dashboard, the check-in)")
    ap.add_argument("--root", default=str(ROOT),
                    help="the install root the overlay paths are relative to (a test points it at a fixture)")
    a = ap.parse_args(argv)

    cfg, live = pathlib.Path(a.config), pathlib.Path(a.db)
    if not live.exists():
        print(f"no live database at {live}", file=sys.stderr)
        return 1
    if a.last_night:
        night = _last_night(live)
        if not night:
            print("RESTORE DRILL: RED — no nightly copy to restore (the nightly backup has not run)", file=sys.stderr)
            return 1
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="aios-restore-drill-"))
        try:
            out = tmp / "restored.db"
            print(f"restoring last night's copy {night.name} into {out} …")
            shutil.copy2(night, out)                     # the copy is never opened where it is kept
            return _verdict(live, out, a)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    if a.copy:
        copy = pathlib.Path(a.copy)
        if not copy.is_file() or copy.stat().st_size == 0:
            print(f"RESTORE DRILL: RED — no copy at {copy}", file=sys.stderr)
            return 1
        return _verdict(live, copy, a)
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

        return _verdict(live, out, a)
    finally:
        if a.keep:
            print(f"  copy kept at {out}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
