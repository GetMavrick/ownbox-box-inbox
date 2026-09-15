#!/usr/bin/env python
"""Ensure the GTM **Contacts** table exists with the GoHighLevel-modeled schema (idempotent).

- If the table is MISSING, create it with the full schema (`Contact Id` is the primary/dedup key).
- If it ALREADY EXISTS, add only the fields it's missing — every existing field is PRESERVED
  untouched, including any Airtable AI "company research" fields you've added to it.

The first fields use GoHighLevel's EXACT export column names, so a GHL CSV export imports 1:1
and a future GHL<->Airtable sync lines up by name; the rest are GHL-common standard fields and
AIOS GTM-engine outreach fields. One PAT covers it, but this script needs the PAT's
`schema.bases:write` scope (you can drop that scope again afterward).

Usage:
  python scripts/create_gtm_contacts_table.py                  # ensure (create or extend)
  python scripts/create_gtm_contacts_table.py --base appXXXX   # target a specific base
  python scripts/create_gtm_contacts_table.py --print          # show schema, write nothing
  (--base defaults to AIRTABLE_GTM_BASE_ID, --name to AIRTABLE_GTM_CONTACTS_TABLE / "Contacts")

After it exists: `python scripts/import_gtm_contacts.py --csv <export.csv>`.
"""
import argparse
import json
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import settings                       # noqa: E402

_META = "https://api.airtable.com/v0/meta/bases"
_DT = {"timeZone": "client", "dateFormat": {"name": "iso"}, "timeFormat": {"name": "24hour"}}
_D = {"dateFormat": {"name": "iso"}}

# If an existing table already has an equivalent field under a different name, treat the
# canonical field as satisfied so we don't add a confusing duplicate.
ALIASES = {"Owner": ("Assignee",)}


def _sel(*names):
    return {"choices": [{"name": n} for n in names]}


# ── GoHighLevel-native FIRST (exact export column names → clean CSV import + sync) ──
# ── then GHL-common standard fields, then AIOS GTM-engine outreach fields. ──
FIELDS = [
    {"name": "Contact Id",    "type": "singleLineText"},   # GHL id — primary, the sync/dedup key
    {"name": "First Name",    "type": "singleLineText"},
    {"name": "Last Name",     "type": "singleLineText"},
    {"name": "Email",         "type": "email"},
    {"name": "Phone",         "type": "phoneNumber"},
    {"name": "Business Name", "type": "singleLineText"},
    {"name": "Created",       "type": "dateTime", "options": _DT},
    {"name": "Last Activity", "type": "dateTime", "options": _DT},
    {"name": "Tags",          "type": "multipleSelects",
     "options": _sel("meta lead", "subscriber", "trial-member", "new-lead", "cold-outreach")},
    # GHL-common standard fields (so a fuller GHL export still maps by name):
    {"name": "Source",        "type": "singleSelect",
     "options": _sel("Meta Lead", "Cold Outreach", "Referral", "Inbound", "Skool", "Import")},
    {"name": "Website",       "type": "url"},
    {"name": "City",          "type": "singleLineText"},
    {"name": "State",         "type": "singleLineText"},
    {"name": "Country",       "type": "singleLineText"},
    {"name": "Postal Code",   "type": "singleLineText"},
    {"name": "DND",           "type": "checkbox", "options": {"icon": "check", "color": "redBright"}},
    # AIOS GTM-engine outreach fields:
    {"name": "Status",        "type": "singleSelect",
     "options": _sel("New", "Researching", "Contacted", "Opened", "Replied", "Qualified",
                     "Booked", "Customer", "Not a Fit", "Suppressed", "Bounced")},
    {"name": "Owner",         "type": "singleLineText"},
    {"name": "Last Contacted", "type": "date", "options": _D},
    {"name": "Next Follow-Up", "type": "date", "options": _D},
    {"name": "Sequence",      "type": "singleLineText"},
    {"name": "Sequence Step", "type": "number", "options": {"precision": 0}},
    {"name": "Lead Score",    "type": "number", "options": {"precision": 0}},
    {"name": "Title",         "type": "singleLineText"},
    {"name": "LinkedIn",      "type": "url"},
    {"name": "Notes",         "type": "multilineText"},
    {"name": "AIOS Lead Id",  "type": "singleLineText"},   # back-link to gtm_leads (two-way sync)
]


def _find_table(base, name, key):
    """Return (table_id, {field names}) for the table, or (None, set()) if it doesn't exist."""
    r = requests.get(f"{_META}/{base}/tables",
                     headers={"Authorization": f"Bearer {key}"}, timeout=30)
    r.raise_for_status()
    for t in r.json().get("tables", []):
        if t["name"] == name or t["id"] == name:
            return t["id"], {f["name"] for f in t.get("fields", [])}
    return None, set()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=getattr(settings, "airtable_gtm_base_id", ""))
    ap.add_argument("--name", default=(getattr(settings, "airtable_gtm_contacts_table", "")
                                       or "Contacts"))
    ap.add_argument("--print", action="store_true", dest="dry", help="print the schema, write nothing")
    args = ap.parse_args()

    if args.dry:
        print(f"GTM '{args.name}' — {len(FIELDS)} canonical fields:")
        print(json.dumps(FIELDS, indent=2))
        return 0
    if not settings.airtable_api_key:
        print("AIRTABLE_API_KEY not set.")
        return 1
    if not args.base:
        print("Pass --base appXXXX (or set AIRTABLE_GTM_BASE_ID).")
        return 1

    key = settings.airtable_api_key
    H = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        tid, existing = _find_table(args.base, args.name, key)
    except requests.RequestException as e:
        print(f"Could not read the base schema: {str(e)[:200]}")
        print("→ the PAT needs scope `schema.bases:read` + access to this base.")
        return 1

    # ── CREATE: table doesn't exist yet ──
    if tid is None:
        r = requests.post(f"{_META}/{args.base}/tables", headers=H,
                          json={"name": args.name, "fields": FIELDS}, timeout=30)
        if r.ok:
            print(f"Created '{args.name}' ({r.json().get('id')}) in {args.base} — "
                  f"{len(FIELDS)} fields. ✅")
            print("Next: python scripts/import_gtm_contacts.py --csv <export.csv>")
            return 0
        print(f"FAILED {r.status_code}: {r.text[:300]}")
        if r.status_code in (401, 403):
            print("→ the PAT needs scope `schema.bases:write` + access to this base.")
        return 1

    # ── EXTEND: table exists — add only the missing fields, preserve everything else ──
    print(f"'{args.name}' exists ({tid}) with {len(existing)} fields — adding only what's missing.")
    added = skipped = failed = 0
    for f in FIELDS:
        nm = f["name"]
        if nm in existing or any(a in existing for a in ALIASES.get(nm, ())):
            skipped += 1
            continue
        r = requests.post(f"{_META}/{args.base}/tables/{tid}/fields", headers=H, json=f, timeout=30)
        if r.ok:
            print(f"  + added {nm}")
            added += 1
        else:
            print(f"  ! FAILED {nm}: {r.status_code} {r.text[:140]}")
            failed += 1
    print(f"\nadded {added}, skipped {skipped} (already present), failed {failed}")
    if added and failed == 0:
        print("Next: python scripts/import_gtm_contacts.py --csv <export.csv>")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
