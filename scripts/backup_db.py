"""Nightly SQLite backup — the free half of spec §9's durability story.

Uses sqlite's online .backup API (consistent even mid-write under WAL), keeps
the last 7 dailies under <db_dir>/backups/, logs every run. The other half —
off-box replication (Litestream → object storage, ~$5/mo) — is wired the day
the owner approves the spend; until then this bounds data loss to ≤24h for
everything except a full-disk failure.

systemd: aios-backup.timer (daily 03:00 owner time) → this script.
"""
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import settings  # noqa: E402

KEEP = 7


def main() -> None:
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

    backups = sorted(out_dir.glob("aios-*.db"))
    for old in backups[:-KEEP]:
        old.unlink()
        print(f"rotated out {old.name}")
    print(f"backup ok: {dest.name} ({dest.stat().st_size // 1024} KB), "
          f"{min(len(backups), KEEP)} kept")


if __name__ == "__main__":
    main()
