#!/usr/bin/env python3
"""Merge every lead list into ONE master, deduped on phone, then grade each record.

    python scripts/consolidate_leads.py --out data/seeds/master_leads.csv

WHY THIS EXISTS. The leads arrived in waves with different column shapes — an Overture
extract (`company`/`phone`/`email`), a GHL import (`Business Name`/`Phone`/`Email`), and
an owner-name enrichment pass on top of one of them. Same businesses, three vocabularies,
overlapping rows. Counting them separately overstates the pipeline and calling them
separately wastes the call.

THE BAR, set by the owner: a record is MARKETABLE only with all four of contact name,
company name, phone, and email. Website is nice, not required. Anything short of four is
not a lead yet — it is a lead with a known missing field, and this says which field.

DEDUPE KEY IS THE PHONE, last 10 digits. Not the name (spelling drifts: "Smith Plumbing"
vs "Smith Plumbing LLC"), not the place id (the two waves came from different id spaces,
so the same business has two). One business, one phone, and it is the field we are least
willing to be wrong about since it is what gets dialled.

MERGE RULE: for each field, the first NON-EMPTY value across the waves wins, so a name
found by enrichment survives a merge with a row that predates it. That means order the
inputs richest-first — a later wave never overwrites an earlier field with a blank.
"""
import argparse
import collections
import csv
import os
import re
import sys

# Every input dialect maps onto this one shape. Adding a wave means adding aliases here,
# not another bespoke merge script.
# "Contact Name" is REDUNDANT with First/Last on purpose. The CRM wants the name split;
# bulk_import wants one `contact_name` column and has no notion of split names, so a file
# carrying only First/Last imports with EVERY owner name silently dropped — measured on
# the box, 431 rows landed nameless. Emitting both means the same file feeds both consumers
# without either side needing to know about the other.
_FIELDS = ["First Name", "Last Name", "Contact Name", "Business Name", "Phone", "Email",
           "Website", "Address", "City", "State", "Postal Code", "Categories", "Source",
           "Notes", "LinkedIn URL"]
_ALIAS = {
    "company": "Business Name", "phone": "Phone", "email": "Email", "website": "Website",
    "address": "Address", "locality": "City", "region": "State", "postal": "Postal Code",
    "category": "Categories", "Tags": "Categories", "source_id": "Notes",
}


def _digits(v: str) -> str:
    return re.sub(r"[^0-9]", "", v or "")


def phone_key(v: str) -> str:
    """Last 10 digits — collapses +1-803-555-0100, (803) 555-0100 and 8035550100."""
    d = _digits(v)
    return d[-10:] if len(d) >= 10 else ""


def _normalise(row: dict) -> dict:
    out = {f: "" for f in _FIELDS}
    for k, v in row.items():
        f = _ALIAS.get(k, k)
        if f in out and (v or "").strip():
            out[f] = v.strip()
    return out


def _compose(r: dict) -> dict:
    """Derive the fields other consumers need but no input wave supplies."""
    if not r.get("Contact Name"):
        r["Contact Name"] = " ".join(x for x in (r.get("First Name", ""),
                                                 r.get("Last Name", "")) if x).strip()
    return r


def load(path: str) -> list:
    with open(path, newline="", encoding="utf-8") as fh:
        return [_compose(_normalise(r)) for r in csv.DictReader(fh)]


def grade(r: dict) -> tuple:
    """-> (marketable, missing_fields). The owner's four-field bar, in one place."""
    missing = [f for f in ("First Name", "Business Name", "Phone", "Email")
               if not (r.get(f) or "").strip()]
    return (not missing), missing


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--file", action="append", required=True,
                   help="input CSV; repeat. Order matters — richest list FIRST.")
    p.add_argument("--out", required=True)
    p.add_argument("--marketable-out", default=None,
                   help="also write ONLY the records that clear the four-field bar")
    a = p.parse_args(argv)

    merged: dict = collections.OrderedDict()
    no_phone = 0
    for path in a.file:
        if not os.path.exists(path):
            sys.exit(f"no such file: {path}")
        rows = load(path)
        new = 0
        for r in rows:
            k = phone_key(r["Phone"])
            if not k:
                # Unmergeable and uncallable. Counted, never silently dropped.
                no_phone += 1
                continue
            if k not in merged:
                merged[k] = r
                new += 1
            else:
                tgt = merged[k]
                for f in _FIELDS:          # first non-empty wins; never blank a field
                    if not tgt[f] and r[f]:
                        tgt[f] = r[f]
        print(f"{path}: {len(rows):>4} rows · {new:>4} new · {len(rows) - new - 0:>4} merged")

    rows = list(merged.values())
    _write(a.out, rows)

    good = [r for r in rows if grade(r)[0]]
    gap = collections.Counter()
    for r in rows:
        for f in grade(r)[1]:
            gap[f] += 1

    print(f"\n=== {len(rows)} unique businesses (deduped on phone) ===")
    if no_phone:
        print(f"  {no_phone} row(s) had no usable phone and were skipped")
    print(f"\n  MARKETABLE (name+company+phone+email): {len(good)}  "
          f"({len(good) * 100 // (len(rows) or 1)}%)")
    print("\n  what the rest are missing:")
    for f, n in gap.most_common():
        print(f"    {f:<16} missing on {n:>4}")

    # The actionable number: rows one field short, and which field. A row missing ONLY a
    # name is worth enriching; a row missing only an email is worth verifying.
    only = collections.Counter()
    for r in rows:
        m = grade(r)[1]
        if len(m) == 1:
            only[m[0]] += 1
    print("\n  ONE field short — the cheapest wins:")
    for f, n in only.most_common():
        print(f"    {n:>4} need only {f}")

    if a.marketable_out:
        _write(a.marketable_out, good)
        print(f"\nmarketable -> {a.marketable_out}")
    print(f"master     -> {a.out}")
    return 0


def _write(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    sys.exit(main())
