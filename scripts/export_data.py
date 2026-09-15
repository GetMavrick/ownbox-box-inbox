#!/usr/bin/env python3
"""Give me my data. Every table that holds YOUR rows, as CSV and JSON, under data/export-<ts>/.

Trust, portability, and the answer to "what if I leave": the database is SQLite and it is
yours, but a folder of CSVs is what a person can open. Kernel bookkeeping (jobs, heartbeats,
sessions, litestream) is skipped; everything else is written, including suppressions — an
opt-out list is part of your data and your obligation.
"""
import csv, datetime as dt, json, pathlib, sqlite3, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core.config import settings, ROOT

SKIP = {"jobs", "job_checkpoints", "heartbeats", "sessions", "alert_state", "airtable_sync_baseline",
        "airtable_trigger_baseline", "company_cache", "email_format_cache", "reel_field_snapshots", "gtm_settings", "inbox_state"}


def main() -> int:
    db = pathlib.Path(getattr(settings, "db_path", ROOT / "aios.db"))
    if not db.exists():
        print(f"no database at {db}"); return 1
    out = ROOT / "data" / f"export-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    out.mkdir(parents=True)
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    tables = [r[0] for r in c.execute("select name from sqlite_master where type='table' and name not like '_litestream%' and name not like 'sqlite_%'")]
    manifest = {}
    for t in sorted(tables):
        if t in SKIP:
            continue
        rows = [dict(r) for r in c.execute(f'select * from "{t}"')]
        manifest[t] = len(rows)
        with open(out / f"{t}.json", "w") as f:
            json.dump(rows, f, indent=1, default=str)
        if rows:
            with open(out / f"{t}.csv", "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    (out / "MANIFEST.json").write_text(json.dumps({"exported": dt.datetime.now(dt.timezone.utc).isoformat(), "database": str(db), "tables": manifest}, indent=1))
    print(f"exported {sum(manifest.values())} rows across {len(manifest)} tables → {out}")
    for t, n in sorted(manifest.items(), key=lambda x: -x[1])[:8]:
        print(f"  {t:<28} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
