#!/usr/bin/env python
"""Import a GoHighLevel contact CSV export into the GTM 'Contacts' table.

- Maps GHL columns by name (Contact Id / First Name / Last Name / Phone / Email /
  Business Name / Created / Last Activity / Tags) so it lines up 1:1 with the table schema.
- SCHEMA-AWARE: only writes fields that actually exist on the table, so it works whether the
  table has just the canonical schema or extra fields. If an AI-linked 'Company' field exists,
  'Business Name' is mirrored into it so Airtable's company-research AI fields enrich on import.
- Composes a 'Name' primary from First + Last when the table has a 'Name' field.
- Derives 'Source' from the GHL tags. Splits comma-separated 'Tags' into a list.
- Dedups by 'Contact Id' (idempotent / re-runnable). create_record() sends typecast=True, so
  unknown Tag/Source options auto-create and human date strings parse.

Usage:
  python scripts/import_gtm_contacts.py --csv path/to/export.csv
  python scripts/import_gtm_contacts.py --csv export.csv --dry    # parse + map, write nothing
  (--base defaults to AIRTABLE_GTM_BASE_ID, --table to AIRTABLE_GTM_CONTACTS_TABLE / "Contacts")
"""
import argparse
import csv
import os
import sys
import time
from datetime import datetime

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.airtable import client                       # noqa: E402
from core.config import settings                       # noqa: E402

_META = "https://api.airtable.com/v0/meta/bases"


def to_iso(s):
    """GHL 'Created' is ISO already; 'Last Activity' is 'May 10 2026 06:57 PM' — normalize both."""
    s = (s or "").strip()
    if not s:
        return ""
    if "T" in s and "-" in s[:11]:
        return s
    for fmt in ("%b %d %Y %I:%M %p", "%b %d %Y %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).isoformat()
        except ValueError:
            continue
    return s                                            # last resort: let Airtable typecast try


def source_from_tags(tags):
    tl = tags.lower()
    if "meta lead" in tl:
        return "Meta Lead"
    if "cold-outreach" in tl or "cold outreach" in tl:
        return "Cold Outreach"
    if "skool" in tl:
        return "Skool"
    if "subscriber" in tl or "trial" in tl:
        return "Inbound"
    return "Import"


def _table_field_names(base, table, key):
    r = requests.get(f"{_META}/{base}/tables",
                     headers={"Authorization": f"Bearer {key}"}, timeout=30)
    r.raise_for_status()
    for t in r.json().get("tables", []):
        if t["name"] == table or t["id"] == table:
            return {f["name"] for f in t.get("fields", [])}
    return set()


def _row_to_fields(row, known):
    """Map one GHL CSV row → Airtable fields, restricted to fields the table actually has."""
    def put(k, v):
        if v and k in known:
            fields[k] = v

    fields = {}
    first = (row.get("First Name") or "").strip()
    last = (row.get("Last Name") or "").strip()
    put("Name", (first + " " + last).strip())
    put("First Name", first)
    put("Last Name", last)
    put("Email", (row.get("Email") or "").strip())
    put("Phone", (row.get("Phone") or "").strip())
    put("Contact Id", (row.get("Contact Id") or "").strip())
    put("Created", to_iso(row.get("Created")))
    put("Last Activity", to_iso(row.get("Last Activity")))
    biz = (row.get("Business Name") or "").strip()
    put("Business Name", biz)     # GHL-named field (sync)
    put("Company", biz)           # AI-researcher input — only set if the table has 'Company'
    tags = (row.get("Tags") or "").strip()
    if tags and "Tags" in known:
        fields["Tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    if tags:
        put("Source", source_from_tags(tags))
    return fields


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="path to the GoHighLevel contact export")
    ap.add_argument("--base", default=getattr(settings, "airtable_gtm_base_id", ""))
    ap.add_argument("--table", default=(getattr(settings, "airtable_gtm_contacts_table", "")
                                        or "Contacts"))
    ap.add_argument("--dry", action="store_true", help="parse + map, write nothing")
    args = ap.parse_args()

    if not os.path.exists(args.csv):
        print(f"CSV not found: {args.csv}")
        return 1
    if not settings.airtable_api_key:
        print("AIRTABLE_API_KEY not set.")
        return 1
    if not args.base:
        print("Pass --base appXXXX (or set AIRTABLE_GTM_BASE_ID).")
        return 1

    try:
        known = _table_field_names(args.base, args.table, settings.airtable_api_key)
    except requests.RequestException as e:
        print(f"Could not read the table schema: {str(e)[:200]}")
        return 1
    if not known:
        print(f"Table '{args.table}' not found in {args.base}. "
              f"Run scripts/create_gtm_contacts_table.py first.")
        return 1

    with open(args.csv, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    print(f"{len(rows)} rows in CSV; table '{args.table}' has {len(known)} fields. "
          f"Company-mirror: {'on' if 'Company' in known else 'off (no Company field)'}.")

    if args.dry:
        for row in rows[:3]:
            print("\n", _row_to_fields(row, known))
        print(f"\n(dry run — would import up to {len(rows)} rows, dedup by Contact Id)")
        return 0

    existing = set()
    for r in client.list_records(table=args.table, base=args.base):
        cid = (r.get("fields") or {}).get("Contact Id")
        if cid:
            existing.add(cid)

    created = skipped = failed = 0
    for row in rows:
        cid = (row.get("Contact Id") or "").strip()
        if cid and cid in existing:
            skipped += 1
            continue
        fields = _row_to_fields(row, known)
        if not fields:
            skipped += 1
            continue
        try:
            client.create_record(fields, table=args.table, base=args.base)
            created += 1
            print("  +", fields.get("Name") or cid)
        except Exception as e:
            failed += 1
            print("  ! FAILED", repr(fields.get("Name") or cid), str(e)[:160])
        time.sleep(0.2)

    total = len(client.list_records(table=args.table, base=args.base))
    print(f"\nimported {created}, skipped {skipped} (dup/empty), failed {failed}")
    print(f"total rows in '{args.table}' now: {total}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
