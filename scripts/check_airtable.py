#!/usr/bin/env python
"""Prove Airtable access is live — ONE PAT, every base.

Reads AIRTABLE_API_KEY and checks READ (and, with --write, WRITE) access to each base this
deployment touches: the default VIDEOS base, every Space's base (config `spaces:`), the GTM
contacts base (if set), plus any --base/--table you pass. The write check creates an EMPTY
throwaway record and immediately deletes it — it never leaves anything behind (and only its
own row, so it honors "never delete the owner's content").

Usage:
  python scripts/check_airtable.py                       # read-only probe of every known base
  python scripts/check_airtable.py --write               # also prove write (create+delete a row)
  python scripts/check_airtable.py --base appXXX --table Contacts [--write]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.airtable import client                       # noqa: E402
from core.config import get_config, settings           # noqa: E402


def _targets(args) -> list[tuple]:
    """(label, base, table) tuples to probe, deduped, in a sensible order."""
    out, seen = [], set()

    def add(label, base, table):
        if base and table and (base, table) not in seen:
            seen.add((base, table))
            out.append((label, base, table))

    if args.base:                                       # explicit override → just that one
        add("cli", args.base, args.table or settings.airtable_videos_table or "VIDEOS")
        return out
    add("default", settings.airtable_base_id, settings.airtable_videos_table)
    for sp in (get_config().get("spaces") or []):
        add(sp.get("name") or "space", sp.get("airtable_base"), sp.get("airtable_table"))
    add("gtm-contacts", getattr(settings, "airtable_gtm_base_id", ""),
        getattr(settings, "airtable_gtm_contacts_table", "") or "Contacts")
    return out


def _probe(base: str, table: str, do_write: bool) -> tuple[str, str]:
    try:
        client.list_records(table=table, base=base, max_records=1)
        read = "ok"
    except Exception as e:                              # noqa: BLE001
        return f"FAIL ({str(e)[:70]})", "—"
    if not do_write:
        return read, "—"
    try:
        rec = client.create_record({}, table=table, base=base)   # empty throwaway row
        client.delete_record(rec["id"], table=table, base=base)  # …removed immediately
        return read, "ok"
    except Exception as e:                              # noqa: BLE001
        return read, f"FAIL ({str(e)[:70]})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="also prove write (create+delete a row)")
    ap.add_argument("--base", help="probe just this base")
    ap.add_argument("--table", help="table name for --base (default: AIRTABLE_VIDEOS_TABLE)")
    args = ap.parse_args()

    if not settings.airtable_api_key:
        print("AIRTABLE_API_KEY is not set — nothing to check.")
        return 1
    targets = _targets(args)
    if not targets:
        print("No bases configured. Set AIRTABLE_BASE_ID (+ spaces / GTM) or pass --base.")
        return 1

    print(f"Airtable access — PAT …{settings.airtable_api_key[-4:]}  (write={bool(args.write)})\n")
    any_fail = False
    for label, base, table in targets:
        read, write = _probe(base, table, args.write)
        any_fail = any_fail or "FAIL" in read or "FAIL" in str(write)
        print(f"  {label:14} {base}/{table:16}  read={read:8}  write={write}")
    print("\n" + ("Some checks FAILED — fix the PAT scopes / base access above."
                  if any_fail else "All good — one key reaches every base. ✅"))
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
