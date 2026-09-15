#!/usr/bin/env python3
"""Mirror consolidated leads into the Airtable Contacts table. Diff-driven, --apply to write.

    python scripts/airtable_push_leads.py --master data/seeds/master_leads.csv \
        --marketable data/seeds/marketable_leads.csv          # plan only
    ... --apply                                                # actually write

WHY A SCRIPT AND NOT THE EXISTING SWEEP. `airtable_sync` mirrors `gtm_leads` and is
deliberately READ-ONLY since the Prospect Lake cutover. Re-enabling that writeback to land
one batch would switch a deprecated path back on for every future sweep — a much bigger
change than the task needs. This writes the batch and touches nothing else.

TWO OPERATIONS, both idempotent:
  BACKFILL — an existing Contact whose phone matches a lead we now have a name for gets
             First/Last Name filled. This is the actual complaint: every Contact in the
             table had a blank name, because nothing upstream had ever produced one.
  CREATE   — a marketable lead with no Contact at that phone becomes a new Contact.

NEVER CLOBBERS. A field is written only when the Airtable side is EMPTY, so an owner edit,
or anything the table's AI enrichment filled in, always wins over our value. Re-running is
therefore safe and converges: the second run finds nothing to do.

MATCHES ON PHONE, last 10 digits — same key the consolidator dedupes on, so "already in
Airtable" means the same thing in both places. Note the table can hold SEVERAL rows with one
phone (it does today), so a backfill patches EVERY row sharing that phone rather than the
first one found — patching one of a pair looks like a partial failure to the person reading
the table.
"""
import argparse
import collections
import csv
import os
import re
import sys
import time

import requests

_API = "https://api.airtable.com/v0"
_BATCH = 10                      # Airtable's hard per-request record limit
_PACE_S = 0.25                   # 5 req/s limit; stay well under it


def phone_key(v: str) -> str:
    d = re.sub(r"[^0-9]", "", v or "")
    return d[-10:] if len(d) >= 10 else ""


def _headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def fetch_all(key: str, base: str, table: str) -> list:
    recs, offset = [], None
    while True:
        params = {"pageSize": 100}
        if offset:
            params["offset"] = offset
        r = requests.get(f"{_API}/{base}/{table}", headers=_headers(key),
                         params=params, timeout=60)
        r.raise_for_status()
        d = r.json()
        recs += d.get("records", [])
        offset = d.get("offset")
        if not offset:
            return recs


def _blank(rec: dict, field: str) -> bool:
    v = rec.get("fields", {}).get(field)
    return not (v.strip() if isinstance(v, str) else v)


# master column -> Airtable field. The Airtable NAMES are the surprising half and the
# reason the LinkedIn profiles never appeared in the table: the field is called "LinkedIn",
# not "LinkedIn URL", and a records-API write to a field that does not exist is not an
# error — it is silently ignored. So a mapping that quietly omitted the column and one
# that named it wrong looked exactly the same from here: a successful write with a blank
# column. Anything the master carries belongs in this map; a mirror that mirrors a subset
# is not a mirror.
_FIELD_MAP = {
    "First Name": "First Name",
    "Last Name": "Last Name",
    "Contact Name": "Full Name",
    "Business Name": "Company",
    "Phone": "Phone",
    "Email": "Email",
    "Website": "Website",
    "LinkedIn URL": "LinkedIn",
    "City": "City",
    "State": "State",
    "Postal Code": "Postal Code",
}
# Written on create only — these describe our handling of the lead, not the lead, so a
# re-sync must never walk back a Status the owner moved on.
_ON_CREATE = {"Source": "Cold Outreach", "Status": "New"}


def _contact_fields(row: dict, *, creating: bool = False) -> dict:
    out = {dest: (row.get(src) or "").strip() for src, dest in _FIELD_MAP.items()}
    # 'Company' and 'Business Name' are separate fields in the table and both are in use;
    # fill both from the one real source rather than leaving a half-populated view.
    if out.get("Company"):
        out["Business Name"] = out["Company"]
    if creating:
        out.update(_ON_CREATE)
    return {k: v for k, v in out.items() if v}


def _send(key: str, base: str, table: str, method: str, payload: list) -> int:
    done = 0
    for i in range(0, len(payload), _BATCH):
        chunk = payload[i:i + _BATCH]
        r = requests.request(method, f"{_API}/{base}/{table}", headers=_headers(key),
                             json={"records": chunk, "typecast": True}, timeout=60)
        if r.status_code >= 400:
            # Stop rather than plough on: a schema or option error repeats on every chunk,
            # and a half-written table is worse than a clearly failed run.
            sys.exit(f"Airtable {method} failed {r.status_code}: {r.text[:400]}")
        done += len(r.json().get("records", []))
        time.sleep(_PACE_S)
    return done


# SQL is the source of truth for cold prospects; Airtable is a ONE-WAY mirror of it. The
# store column -> the shape the field map expects. A CSV path is kept only so an ad-hoc
# file can still be pushed, but nothing routine should use it: a mirror with two possible
# sources is two lists that happen to agree until the day they don't.
_STORE_SQL = """
    SELECT name_places, contact_name, contact_email, phone, website,
           locality, region, postal, linkedin_url
      FROM gtm_businesses
     WHERE COALESCE(contact_name,'') <> '' AND COALESCE(phone,'') <> ''
       AND COALESCE(name_places,'') <> '' AND merged_into IS NULL
"""


def _from_store() -> list:
    import sqlite3
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core import state
    with state.connect() as c:
        c.row_factory = sqlite3.Row
        cols = {r[1] for r in c.execute("PRAGMA table_info(gtm_businesses)")}
        sql = _STORE_SQL
        if "linkedin_url" not in cols:      # column not migrated yet — select without it
            sql = sql.replace(", linkedin_url", "")
        recs = [dict(r) for r in c.execute(sql)]
    out = []
    for r in recs:
        name = (r.get("contact_name") or "").strip()
        first, _, last = name.partition(" ")
        out.append({
            "First Name": first, "Last Name": last, "Contact Name": name,
            "Business Name": r.get("name_places") or "",
            "Phone": r.get("phone") or "", "Email": r.get("contact_email") or "",
            "Website": r.get("website") or "", "City": r.get("locality") or "",
            "State": r.get("region") or "", "Postal Code": r.get("postal") or "",
            "LinkedIn URL": r.get("linkedin_url") or "",
        })
    return out


def _from_csv(path: str) -> list:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _delete(key: str, base: str, table: str, ids: list) -> int:
    done = 0
    for i in range(0, len(ids), _BATCH):
        chunk = ids[i:i + _BATCH]
        r = requests.delete(f"{_API}/{base}/{table}", headers=_headers(key),
                            params=[("records[]", x) for x in chunk], timeout=60)
        if r.status_code >= 400:
            sys.exit(f"Airtable DELETE failed {r.status_code}: {r.text[:400]}")
        done += sum(1 for x in r.json().get("records", []) if x.get("deleted"))
        time.sleep(_PACE_S)
    return done


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--master", default="", help="(legacy) CSV source; omit to read SQL")
    p.add_argument("--marketable", default="", help="(legacy) CSV source; omit to read SQL")
    p.add_argument("--base", default=os.environ.get("AIRTABLE_GTM_BASE_ID", ""))
    p.add_argument("--table", default="tblREPLACEME")
    p.add_argument("--apply", action="store_true", help="write; otherwise plan only")
    p.add_argument("--mirror", action="store_true",
                   help="DELETE Contacts absent from --master, so the table matches it exactly")
    p.add_argument("--max-delete", type=int, default=150,
                   help="refuse a mirror that would delete more than this (runaway guard)")
    a = p.parse_args(argv)

    key = (os.environ.get("AIRTABLE_API_KEY") or "").strip()
    if not key or not a.base:
        sys.exit("AIRTABLE_API_KEY and a base id are required")

    rows = _from_csv(a.master) if a.master else _from_store()
    master = {}
    for r in rows:
        k = phone_key(r.get("Phone", ""))
        if k:
            master[k] = r
    marketable = list(master.values())

    recs = fetch_all(key, a.base, a.table)
    by_phone = collections.defaultdict(list)
    by_email = collections.defaultdict(list)
    for x in recs:
        k = phone_key(x.get("fields", {}).get("Phone", ""))
        if k:
            by_phone[k].append(x)
        e = (x.get("fields", {}).get("Email") or "").strip().lower()
        if e:
            by_email[e].append(x)
    print(f"Airtable: {len(recs)} records, {len(by_phone)} distinct phones")
    print(f"leads   : {len(master)} master, {len(marketable)} marketable\n")

    # BACKFILL — fill only what is empty, on every row sharing the phone.
    patches = []
    for k, rows in by_phone.items():
        src = master.get(k)
        if not src or not src.get("First Name", "").strip():
            continue
        for rec in rows:
            # Every mapped field, not a hand-picked subset — but only where Airtable is
            # EMPTY, so an owner edit or an AI-enriched value always wins over ours.
            upd = {f: v for f, v in _contact_fields(src).items() if _blank(rec, f)}
            if upd:
                patches.append({"id": rec["id"], "fields": upd})

    # A lead is "already in the table" if EITHER key matches. Phone-only matching is how
    # the table grew a with-phone twin next to every phone-less row: the email was there
    # all along, but nothing looked at it. Email-matched rows get the backfill instead.
    for r in marketable:
        if phone_key(r.get("Phone", "")) in by_phone:
            continue
        e = (r.get("Email") or "").strip().lower()
        for rec in by_email.get(e, []):
            upd = {f: v for f, v in _contact_fields(r).items() if _blank(rec, f)}
            if upd:
                patches.append({"id": rec["id"], "fields": upd})

    creates = [{"fields": _contact_fields(r, creating=True)} for r in marketable
               if phone_key(r["Phone"]) not in by_phone
               and (r.get("Email") or "").strip().lower() not in by_email]

    # MIRROR — the table should hold exactly what the master holds, nothing else. This is
    # the only destructive path here, so it is opt-in, capped, and it never touches a row a
    # human added by hand: no phone means it did not come from an import, so it is left be
    # rather than deleted for failing to match a key it never had.
    stale = []
    if a.mirror:
        for k, rows_ in by_phone.items():
            if k not in master:
                stale += [x["id"] for x in rows_]

    filled = collections.Counter()
    for x in patches:
        for f in x["fields"]:
            filled[f] += 1
    print(f"PLAN\n  backfill {len(patches)} existing record(s)")
    for f, n in filled.most_common():
        print(f"      {f:<14} on {n}")
    print(f"  create   {len(creates)} new record(s)")
    if a.mirror:
        print(f"  DELETE   {len(stale)} record(s) not present in the master")
    print(f"  -> table goes {len(recs)} to {len(recs) + len(creates) - len(stale)} records")

    if a.mirror and len(stale) > a.max_delete:
        sys.exit(f"REFUSING — mirror would delete {len(stale)} records, cap is {a.max_delete}. "
                 f"A delete count this large usually means the master is wrong, not the table. "
                 f"Re-run with --max-delete if it is genuinely intended.")

    if not a.apply:
        print("\nplan only — re-run with --apply to write")
        return 0

    n_p = _send(key, a.base, a.table, "PATCH", patches) if patches else 0
    n_c = _send(key, a.base, a.table, "POST", creates) if creates else 0
    n_d = _delete(key, a.base, a.table, stale) if stale else 0
    print(f"\nwrote: {n_p} patched, {n_c} created, {n_d} deleted")

    after = fetch_all(key, a.base, a.table)
    named = sum(1 for x in after if not _blank(x, "First Name"))
    print(f"verified: {len(after)} records, {named} now carry a first name")
    return 0


if __name__ == "__main__":
    sys.exit(main())
