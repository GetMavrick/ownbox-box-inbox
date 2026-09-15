#!/usr/bin/env python3
"""Give me my data. Every table that holds YOUR rows, as CSV and JSON, under data/export-<ts>/.

Trust, portability, and the answer to "what if I leave": the database is SQLite and it is
yours, but a folder of CSVs is what a person can open. Kernel bookkeeping (jobs, heartbeats,
sessions, litestream) is skipped; everything else is written, including suppressions — an
opt-out list is part of your data and your obligation.

SECRETS ARE NEVER IN IT, AND THAT IS ENFORCED BY RULE RATHER THAN BY REMEMBERING. This file
used to be a DENY-LIST alone: a table nobody thought to name was exported in full, so the day
`box_secrets` was added the buyer's own Anthropic key went into `box_secrets.json` and `.csv`
— and this export is customer-facing. It gets emailed. It gets dropped in Dropbox. Measured,
not supposed: seeding a key and running this printed it in two files, and `box_claim`'s
password hash came with it.

Adding those two names would have fixed today and nothing else. So there are two rules, and
the second is the one that matters:

  1. TABLES declared secret-bearing (`core.state.SECRET_TABLES`) are skipped whole.
  2. COLUMNS whose NAME reads like a credential are redacted in EVERY table, using the exact
     pattern `core.logging` already uses on the journal. A table nobody classified, added by
     someone who never read this file, is covered on the day it appears.

`[redacted]` rather than omitted: a column that vanishes makes an export look corrupt and
sends a person hunting for the data they were promised. A marked one says what happened.
"""
import csv, datetime as dt, json, pathlib, re, sqlite3, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core.config import settings, ROOT
from core.state import SECRET_TABLES

SKIP = {"jobs", "job_checkpoints", "heartbeats", "sessions", "alert_state", "airtable_sync_baseline",
        "airtable_trigger_baseline", "company_cache", "email_format_cache", "reel_field_snapshots",
        "gtm_settings", "inbox_state"} | SECRET_TABLES

# THIS IS NOT `core.logging._KEYISH`, AND THE DIFFERENCE WAS MEASURED, NOT ARGUED. The first
# version of this file imported that pattern, on the reasoning that one rule cannot drift from
# itself. It is the wrong rule for this job and the export proved it: `_KEYISH` contains a bare
# `token`, so exporting a single seeded `spend_ledger` row returned
#
#     "input_tokens": "[redacted]",  "output_tokens": "[redacted]",
#     "cache_write_tokens": "[redacted]",  "cache_read_tokens": "[redacted]"
#
# — token COUNTS, the buyer's own cost history, four columns of it, silently replaced in the
# folder handed to him as all of his data. `_KEYISH` scrubs log LINES, where over-matching costs
# a shrug; here it destroys the thing the export exists to deliver. OSDev4 hit the same false
# positive in #1192 and narrowed it away; that PR was closed unmerged, so the note lives here.
#
# SO THE COLUMN RULE IS WRITTEN FOR COLUMN NAMES: a credential word standing as its own
# underscore-separated part, and `token` only where something makes it a credential rather than
# a count (`access_token`, `auth_token`, `api_token`). It is still a NAME rule — it reads no
# values and guesses nothing about content.
#
# AND IT IS STILL NOT ENOUGH ALONE, which is why there are two rules. `box_claim.pw_hash` is
# caught here only because `pw` is listed explicitly; the general point stands that a table
# whose whole purpose is credentials (`box_secrets`) is covered by being declared, not by its
# columns happening to be well named. The table rule covers what the column rule cannot name;
# the column rule covers the table nobody classified.
_SECRET_COL = re.compile(r"""
      (^|_) (pw | password | passwd | secret | credential | api[_-]?key | apikey) (_|$)
    | (^|_) (access | auth | refresh | bearer | session | api) [_-]? tokens? (_|$)
    | (^|_) (signature | private[_-]?key) (_|$)
    | ^ (key | token) $
""", re.I | re.X)
REDACTED = "[redacted]"


def _scrub(row: dict) -> tuple[dict, list[str]]:
    """Blank any column whose NAME reads like a credential, and say which ones."""
    hit = [k for k in row if _SECRET_COL.search(k)]
    if not hit:
        return row, []
    return ({k: (REDACTED if v not in (None, "") and k in set(hit) else v)
             for k, v in row.items()}, hit)


def main() -> int:
    db = pathlib.Path(getattr(settings, "db_path", ROOT / "aios.db"))
    if not db.exists():
        print(f"no database at {db}"); return 1
    out = ROOT / "data" / f"export-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    out.mkdir(parents=True)
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    tables = [r[0] for r in c.execute("select name from sqlite_master where type='table' and name not like '_litestream%' and name not like 'sqlite_%'")]
    manifest, redacted, withheld = {}, {}, {}
    for t in sorted(tables):
        if t in SECRET_TABLES:
            # NAMED, NOT SILENTLY ABSENT. "Give me my data" cannot quietly return less than
            # everything: a person who knows this table exists and cannot find it has to assume
            # the export is broken. Saying it was withheld, and why, is the difference between a
            # deliberate omission and a missing file.
            withheld[t] = "holds credentials, not your records — keys you already have and can replace"
            continue
        if t in SKIP:
            continue
        rows, hit = [], set()
        for r in c.execute(f'select * from "{t}"'):
            row, cols = _scrub(dict(r))
            rows.append(row); hit.update(cols)
        manifest[t] = len(rows)
        if hit:
            redacted[t] = sorted(hit)
        with open(out / f"{t}.json", "w") as f:
            json.dump(rows, f, indent=1, default=str)
        if rows:
            with open(out / f"{t}.csv", "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    (out / "MANIFEST.json").write_text(json.dumps(
        {"exported": dt.datetime.now(dt.timezone.utc).isoformat(), "database": str(db),
         "tables": manifest, "tables_withheld": withheld, "columns_redacted": redacted,
         "note": ("Everything here is yours. Credentials are the one thing left out — a stored "
                  "key is not a record of your business, and an export gets forwarded. Whatever "
                  "is listed under tables_withheld or columns_redacted is a secret you already "
                  "hold and can replace, never data about your customers.")}, indent=1))
    print(f"exported {sum(manifest.values())} rows across {len(manifest)} tables → {out}")
    for t, cols in sorted(redacted.items()):
        print(f"  redacted  {t}.{','.join(cols)}")
    for t in sorted(withheld):
        print(f"  withheld  {t} (credentials)")
    for t, n in sorted(manifest.items(), key=lambda x: -x[1])[:8]:
        print(f"  {t:<28} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
