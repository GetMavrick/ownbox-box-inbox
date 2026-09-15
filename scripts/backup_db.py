"""Nightly SQLite backup — the free half of spec §9's durability story.

Uses sqlite's online .backup API (consistent even mid-write under WAL), keeps
the last 7 dailies under <db_dir>/backups/, logs every run. The other half —
off-box replication (Litestream → object storage, ~$5/mo) — is wired the day
the owner approves the spend; until then this bounds data loss to ≤24h for
everything except a full-disk failure.

ONLY A DAILY IS EVER ROTATED. Measured 2026-09-15 on the live box: the rotation
used to sort every `aios-*.db` in backups/, and seven hand-made pre-deploy
copies (`aios-pre-deploy-…`, `aios-predeploy-…`) sort after `aios-2026-…`. So
the newest seven were always those, and every night the rotation deleted the
backup it had just written — then crashed stat-ing it (journal: failed every
night from at least 2026-09-13). A person's pre-deploy copy was only safe by
luck of its name. A daily is now exactly `aios-YYYY-MM-DD.db`; nothing else is touched.

systemd: aios-backup.timer (daily 03:00 owner time) → this script.
"""
import os
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import settings  # noqa: E402

KEEP = 7
DAILY = re.compile(r"^aios-\d{4}-\d{2}-\d{2}\.db$")


def rotate(out_dir: Path, keep: int = KEEP, *, keep_path: Path | None = None) -> list[Path]:
    """Delete the dailies older than the newest `keep`. Returns what was deleted.

    Never a file that is not named like a daily, and never `keep_path` (tonight's).
    """
    dailies = sorted(p for p in out_dir.iterdir() if p.is_file() and DAILY.match(p.name))
    doomed = [p for p in dailies[:-keep] if p != keep_path] if keep > 0 else []
    for old in doomed:
        old.unlink()
    return doomed


def main() -> int:
    db = Path(settings.db_path).resolve()
    out_dir = db.parent / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"aios-{date.today().isoformat()}.db"

    src = sqlite3.connect(str(db))
    dst = sqlite3.connect(str(dest))
    with dst:
        src.backup(dst)
    src.close()
    dst.close()

    for old in rotate(out_dir, keep_path=dest):
        print(f"rotated out {old.name}")
    if not dest.is_file():
        print(f"backup FAILED: {dest} is not on disk after the backup", file=sys.stderr)
        return 1
    kept = sum(1 for p in out_dir.iterdir() if DAILY.match(p.name))
    print(f"backup ok: {dest.name} ({dest.stat().st_size // 1024} KB), {kept} dailies kept")
    return 0


if __name__ == "__main__":
    sys.exit(main())
