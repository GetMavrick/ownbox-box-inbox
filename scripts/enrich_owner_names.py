#!/usr/bin/env python3
"""Find the OWNER'S NAME for a local business, via Google → LinkedIn profile titles.

    SCRAPECREATORS_API_KEY=... python scripts/enrich_owner_names.py \
        --file data/seeds/ghl_import_sc_200.csv --out data/seeds/ghl_import_sc_200_named.csv

WHY THIS EXISTS. Overture is a PLACES dataset — it has no person names, ever. Neither do
the contact databases: local trade owners barely appear in them, because those index
white-collar professionals with LinkedIn presence. Measured, all of them dead ends for
this ICP: Overture (no name column), MyEmailVerifier Lead-Gen (returns companies and
info@ inboxes), Apollo (401), SC Secretary of State (CAPTCHA), RDAP/WHOIS (GDPR-redacted
to the REGISTRAR), homepage and /about crawls (0/16 real names), BBB (principals are not
on search pages, and few of these businesses are listed at all).

WHAT ACTUALLY WORKS, and it was already keyed: Google indexes LinkedIn profiles, and a
LinkedIn result's TITLE is literally "First Last - Job Title - Company". So searching for
the company name and reading the title off the result gets the person without ever
touching LinkedIn itself. Measured on 14 real SC businesses: 28% with one query, 57% with
the LinkedIn-scoped query added.

COST: 2 searches per business, 1 credit each, ~$0.002/credit → about $0.004 a name.

HONEST BOUND, and it must be carried into the CRM rather than lost here: a hit means
SOMEONE AT THAT COMPANY, usually the owner, not provably the owner. The LinkedIn-scoped
query is tried FIRST because its title format is structured; a generic web result only
counts when an owner/founder word sits next to the name. Roughly 40% will have no name
at all and that is the floor for this segment — no source tested reaches 100%.

RESUMABLE: rows are written as they resolve, so a timeout or a rate-limit keeps the work
already paid for. Re-running skips anything already named.
"""
import argparse
import csv
import os
import re
import sys
import concurrent.futures as cf

import requests

_ENDPOINT = "https://api.scrapecreators.com/v1/google/search"
_TIMEOUT = 60

_NAME = re.compile(r"\b([A-Z][A-Za-z'’\-]{1,15}(?:\s+[A-Z]\.)?\s+[A-Z][A-Za-z'’\-]{2,20})\b")
_TITLE = re.compile(r"(owner|founder|co-?founder|president|proprietor|principal|ceo)", re.I)
# Words that start a capitalised phrase which is NOT a person. Without this the scraper
# "finds" things like "Our Team" and "South Carolina" and writes them into a CRM.
_STOP = re.compile(
    r'^(The|Our|This|That|Your|We|Us|Home|About|Contact|Service|Services|Free|Get|Call|New|'
    r'All|South|North|East|West|Best|Top|Google|Facebook|LinkedIn|United|Better|Business|'
    r'Heating|Air|Pest|Roof|Plumb|Quality|Professional|Local|Family|Licensed)\b', re.I)


def _search(key: str, query: str) -> list:
    try:
        r = requests.get(_ENDPOINT, headers={"x-api-key": key},
                         params={"query": query}, timeout=_TIMEOUT)
        if r.status_code != 200:
            return []
        return (r.json() or {}).get("results") or []
    except Exception:  # noqa: BLE001 — one failed search is not a failed run
        return []


def _is_company_not_person(name: str, company: str) -> bool:
    """A LinkedIn /in/ page can be a company wearing a person's URL shape.

    'Waste Pro' and 'Nex Move' both came back as people because the profile IS the brand.
    The tell is total token overlap with the company name — a real owner shares at most a
    surname with their company ('Powell Electric' → Daniel Powell keeps its first name).
    """
    tokens = name.lower().split()
    haystack = set(re.sub(r"[^a-z ]", " ", company.lower()).split())
    return bool(tokens) and all(t in haystack for t in tokens)


_SUFFIX = re.compile(r"\b(LLC|L\.L\.C|Inc|Incorporated|Co|Corp|Company|Ltd|LLP|PA|PLLC)\b",
                     re.I)


def _legal_stripped(company: str) -> str:
    """'New Riverside Pest Control, LLC.' -> 'New Riverside Pest Control'.

    The search is an EXACT phrase match, so the registered legal name is the worst
    possible query: nobody writes 'Bell Built Construction Inc.' in a LinkedIn headline,
    they write 'Bell Built Construction'. Punctuation and the entity suffix are what make
    the quoted phrase fail to match.
    """
    return " ".join(_SUFFIX.sub("", re.sub(r"[,\.]", " ", company)).split())


def find_owner(key: str, company: str, city: str) -> tuple:
    """-> (name, linkedin_url, source) — any may be None.

    LINKEDIN ONLY, and that is measured, not a shortcut. A generic
    '"{company}" {city} SC owner' web query ran as a fallback on the first 200-business
    batch: it fired ~100 times, returned 2 names, and BOTH were garbage ('Landowner
    Liability', "I'm Josh"). Open-web pages put capitalised phrases beside the word
    'owner' constantly; a LinkedIn result's title is an actual structured field,
    'First Last - Job Title - Company'. 0% precision is not worth any price, so it is gone.

    TWO QUERIES, and they are genuinely different rather than a retry: the exact legal
    name, then the name with punctuation and entity suffix stripped. The second recovers
    businesses the first cannot match at all — measured on 8 known misses of the first
    query, it found a name on one ('Outdoor Empire LLC' -> Manuel Rydz).

    WHY TRYING TWICE IS ALMOST FREE, and it is the fact that makes this shape correct: a
    search with no results returns HTTP 404 and is NOT CHARGED ('credits_charged: 0').
    Only a hit costs a credit. So a miss can be retried in a different shape for nothing,
    and the only real cost is wall-clock.
    """
    seen = set()
    for cand in (company, _legal_stripped(company)):
        if not cand or cand.lower() in seen:
            continue
        seen.add(cand.lower())
        for res in _search(key, f'site:linkedin.com/in "{cand}"')[:6]:
            link = res.get("link") or res.get("url") or ""
            if "linkedin.com/in" not in link:
                continue
            # Take ONLY the name segment of the title — everything past the first ' - '
            # is job title and employer.
            blob = (res.get("title") or "").split(" - ")[0].split(" | ")[0]
            for m in _NAME.finditer(blob):
                name = m.group(1).strip()
                if _STOP.match(name) or len(name.split()) < 2:
                    continue
                if _is_company_not_person(name, company):
                    continue
                return name, link, "linkedin"
    return None, None, None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--file", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--limit", type=int, default=0)
    a = p.parse_args(argv)

    key = (os.environ.get("SCRAPECREATORS_API_KEY") or "").strip()
    if not key:
        sys.exit("SCRAPECREATORS_API_KEY is not set")

    rows = list(csv.DictReader(open(a.file, newline="", encoding="utf-8")))
    if a.limit:
        rows = rows[:a.limit]

    # Resume: keep any name already resolved so a re-run never re-buys a search.
    done = {}
    if os.path.exists(a.out):
        for r in csv.DictReader(open(a.out, newline="", encoding="utf-8")):
            if r.get("First Name"):
                done[r.get("Business Name", "")] = r
    if done:
        print(f"resuming — {len(done)} already named")

    todo = [r for r in rows if r.get("Business Name") not in done]
    print(f"{len(todo)} to search · ~{len(todo) * 2} credits · ~{len(todo) * 2 / 60:.0f} min\n")

    fields = list(rows[0].keys())
    for extra in ("LinkedIn URL", "Name Source"):
        if extra not in fields:
            fields.append(extra)

    out, hits = [], 0

    def work(r):
        name, li, src = find_owner(key, r.get("Business Name", ""), r.get("City", ""))
        rec = dict(r)
        if name:
            parts = name.split()
            rec["First Name"] = parts[0]
            rec["Last Name"] = " ".join(parts[1:])
            rec["LinkedIn URL"] = li or ""
            rec["Name Source"] = src
        else:
            rec.setdefault("First Name", "")
            rec.setdefault("Last Name", "")
            rec["LinkedIn URL"] = ""
            rec["Name Source"] = ""
        return rec

    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        for i, rec in enumerate(ex.map(work, todo), 1):
            out.append(rec)
            if rec["First Name"]:
                hits += 1
            if i % 25 == 0:
                print(f"  …{i}/{len(todo)} · {hits} named", flush=True)
                _write(a.out, fields, list(done.values()) + out)   # checkpoint

    allrows = list(done.values()) + out
    _write(a.out, fields, allrows)
    named = sum(1 for r in allrows if r.get("First Name"))
    total = len(allrows) or 1
    print(f"\n=== {total} businesses · {named} with an owner name ({named * 100 // total}%) ===")
    print(f"  from LinkedIn: {sum(1 for r in allrows if r.get('Name Source') == 'linkedin')}")
    print(f"  from web     : {sum(1 for r in allrows if r.get('Name Source') == 'web')}")
    print(f"  with LinkedIn URL: {sum(1 for r in allrows if r.get('LinkedIn URL'))}")
    print(f"\n-> {a.out}")
    return 0


def _write(path, fields, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    sys.exit(main())
