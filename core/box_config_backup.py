"""An off-box copy of the tenant overlay, carried by the backup that already works.

WHY THIS EXISTS. `my/settings.yaml` is per-box tenant config and gitignored by design: a sold
box must not carry the owner's list ids or spend ceilings into source. That is right, and it
meant the file lived on exactly one disk. Litestream (deploy/litestream.yml) replicates
`aios.db` and nothing else, and the `.bak-*` copies sit on the same disk as the file they copy.
Measured 2026-09-09: 3,056 bytes, 86 lines, 22 of them comments, holding dash labels, gtm
campaign routing, waalaxy list ids, machines, voice, customer_voice and the vendor spend
ceilings. Rebuilding the droplet meant reconstructing all of it from memory.

NOT A SECOND BACKUP. The file's TEXT becomes a row of `box_config_backup`, and Litestream
carries the row off-box inside its 10-second sync, on the path `scripts/restore_drill.py`
already proves comes back. One mechanism, already drilled, rather than a second one that has
never been restored.

WHAT IS STORED. The file's exact bytes as text, never a re-serialised YAML (that would drop
every comment written into it), with the sha256 of those bytes. A row is written only when the
content is new for that path; an unchanged file advances `verified_at` on the row it already
has. The table is therefore the history of every DISTINCT version, bounded by how often the
file really changes, and never pruned. The current overlay is the newest `verified_at`. A file
that does not parse is still stored, with valid = 0, because it is what is on disk; `newest()`
and the recovery query both skip it. A file that is not UTF-8 is refused outright rather than
stored as a lossy copy that would restore different bytes.

WHEN. Daily, and once at every worker start through `boot()`, which is also when most overlay
edits take effect. The periodic alone was not enough: every periodic shares one thread and all
of them are due at start, so this one, registered last, waited behind the walkers. Measured
2026-09-10 on the box: nothing captured four minutes after a deploy while the thread worked
through 37 site harvests, and a box redeployed faster than that queue drains would never have
captured at all. `boot()` runs on its own short thread and beats exactly as the periodic does.

RECOVERY on a rebuilt box, from a restored copy of the database:

    sqlite3 restored.db "SELECT writefile('my/settings.yaml', CAST(body AS BLOB))
      FROM box_config_backup WHERE path = 'my/settings.yaml' AND valid = 1
      ORDER BY verified_at DESC, id DESC LIMIT 1"

REDACTS NOTHING. The overlay holds configuration, not credentials; secrets live in `.env`,
which nothing here reads. Scanned 2026-09-10 before the first capture: no credential-named key,
and the only token-shaped values are four 24-character Waalaxy list ids, which are identifiers
rather than keys and are exactly what belongs in the overlay instead of shipped config.
"""
from __future__ import annotations

import hashlib
import pathlib
import threading
from datetime import datetime, timezone

import yaml

from core import state
from core.logging import get_logger

log = get_logger(__name__)

ROOT = pathlib.Path(__file__).resolve().parents[1]
# WHAT LEAVES THE BOX IS DECIDED IN REVIEW, NOT BY A GLOB. `my/` also holds runtime markers and
# data exports, and a pattern would sweep up whatever lands there next. Add a path on purpose.
PATHS = ("my/settings.yaml",)
INTERVAL_S = 86400

_UPSERT = (
    "INSERT INTO box_config_backup "
    "  (path, sha256, body, bytes, valid, source_mtime, captured_at, verified_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(path, sha256) DO UPDATE SET "
    "  verified_at = excluded.verified_at, source_mtime = excluded.source_mtime"
)


def _parses(text: str) -> bool:
    """A YAML mapping, or empty: the shapes core.config accepts as an overlay."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return False
    return data is None or isinstance(data, dict)


def capture(*, root=None, paths=PATHS, now: str | None = None) -> dict:
    """Record each path's current content. -> a report. Never raises for a file it cannot use."""
    base = pathlib.Path(root) if root else ROOT
    stamp = now or state._now()
    out = {"status": "ok", "captured": 0, "unchanged": 0, "missing": 0, "unreadable": 0,
           "invalid": 0, "paths": {}}
    for rel in paths:
        p = base / rel
        if not p.is_file():
            out["missing"] += 1
            out["paths"][rel] = "missing"
            continue
        try:
            raw = p.read_bytes()
            text = raw.decode("utf-8")
            mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()
        except (OSError, UnicodeDecodeError) as e:
            out["unreadable"] += 1
            out["paths"][rel] = f"unreadable:{type(e).__name__}"
            log.warning("box_config_backup.unreadable", path=rel, error=type(e).__name__)
            continue
        sha = hashlib.sha256(raw).hexdigest()
        valid = _parses(text)
        with state.connect() as c:
            # IMMEDIATE, so a manual run and the periodic cannot both count one new version.
            c.execute("BEGIN IMMEDIATE")
            seen = c.execute("SELECT 1 FROM box_config_backup WHERE path = ? AND sha256 = ?",
                             (rel, sha)).fetchone()
            c.execute(_UPSERT, (rel, sha, text, len(raw), 1 if valid else 0, mtime, stamp, stamp))
        kind = "unchanged" if seen else "captured"
        out[kind] += 1
        if not valid:
            out["invalid"] += 1
            log.warning("box_config_backup.invalid_yaml", path=rel, sha256=sha[:12])
        out["paths"][rel] = f"{kind}:{sha[:12]}"
    if out["missing"] == len(paths):
        out["status"] = "skipped"          # nothing to back up on this box: dormant, not a fault
    elif out["unreadable"]:
        out["status"] = "unreadable"
    log.info("box_config_backup.capture", **{k: v for k, v in out.items() if k != "paths"})
    return out


def newest(path: str = PATHS[0], *, valid_only: bool = True) -> dict | None:
    """The overlay as the backup holds it now: the newest `verified_at` for `path`."""
    q = ("SELECT * FROM box_config_backup WHERE path = ?" + (" AND valid = 1" if valid_only else "")
         + " ORDER BY verified_at DESC, id DESC LIMIT 1")
    with state.connect() as c:
        row = c.execute(q, (path,)).fetchone()
    return dict(row) if row else None


def boot(*, root=None) -> threading.Thread:
    """Capture once, now, on a short thread of its own. -> the thread (a test joins it).

    The worker calls this at start. It writes the same completion beat the periodic writes, so
    the watchdog cannot tell which path last proved the copy fresh, and does not need to."""
    def _run() -> None:
        try:
            res = capture(root=root)
            state.heartbeat("box_config_backup", f"ok:{res.get('status')}"[:80])
        except Exception as e:  # noqa: BLE001 — a boot capture must never take the worker down
            log.warning("box_config_backup.boot_error", error=f"{type(e).__name__}: {str(e)[:160]}")
    t = threading.Thread(target=_run, daemon=True, name="box-config-backup-boot")
    t.start()
    return t


def periodic() -> dict:
    return capture()


try:
    from core.worker import register_periodic
    register_periodic(periodic, interval_s=INTERVAL_S, name="box_config_backup",
                      beat="box_config_backup")
except Exception as e:  # noqa: BLE001 — importable without a worker (dispatch, tests, scripts)
    log.info("box_config_backup.not_registered", why=type(e).__name__)
