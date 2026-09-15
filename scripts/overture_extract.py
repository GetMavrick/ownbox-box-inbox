#!/usr/bin/env python3
"""Extract home-services businesses from Overture Places → a CSV our importer already reads.

    # 1. enumerate what categories actually exist (writes a CANDIDATE file for review)
    python scripts/overture_extract.py --enumerate --regions NC,SC,TN

    # 2. extract, using the reviewed allowlist
    python scripts/overture_extract.py --regions NC,SC,TN
    python scripts/import_contacts.py --file data/imports/<out>.csv --list overture-nc-sc-tn --commit

WHY THIS RUNS OFFLINE AND NOT ON THE DROPLET
  The box is a $12 1-vCPU with the renderer already sequential. A streaming scan of remote
  Parquet is the wrong tenant for it. This script produces a CSV artifact; the Droplet only
  ever runs `import_contacts.py`. Nothing here becomes a runtime dependency of a clone.

WHY IT COSTS NOTHING AND NEEDS NO ACCOUNT
  Overture Places is public GeoParquet on S3, read anonymously. No key, no signup, no vendor.
  Deterministic work — spec §11-6 — so it never touches `brain.think()`.

THE TWO THINGS THAT MAKE THIS QUERY CORRECT (both learned the hard way, see the spec §4):
  · Filter on `bbox` (top-level struct, HAS row-group statistics) so the predicate actually
    pushes down. Filtering on `addresses[1].region` alone reads the planet — it is a nested
    field inside an array and carries no usable stats.
  · Then filter on `addresses[1].region` anyway, for CORRECTNESS: a lat/long rectangle around
    NC/SC/TN leaks ~35% out-of-state rows (measured). The bbox is the speed key; the region
    is the truth key. You need both.
"""
import argparse
import csv
import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import csv_safe  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
ALLOWLIST_PATH = REPO / "config" / "overture_categories.json"
OUT_DIR = REPO / "data" / "imports"

# PINNED ON PURPOSE. Overture publishes monthly and the bucket root is not listable
# anonymously, so "resolve the latest" would mean guessing date strings and silently
# reading a release nobody chose. An explicit pin fails loudly instead of drifting;
# bump it deliberately (current releases: https://docs.overturemaps.org/release/).
DEFAULT_RELEASE = "2026-07-22.0"
S3_ROOT = "s3://overturemaps-us-west-2/release"

# Rectangles are the PUSHDOWN key only — the region filter is what makes the result correct,
# so these are deliberately generous. Add a state by adding its bounding box.
BBOX = {
    "NC": (-84.4, -75.4, 33.7, 36.7),
    "SC": (-83.4, -78.5, 32.0, 35.3),
    "TN": (-90.4, -81.6, 34.9, 36.7),
}


def _connect(threads: int):
    try:
        import duckdb
    except ImportError:
        sys.exit("duckdb is not installed — `pip install duckdb` (offline tool, not a clone dep)")
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("SET s3_region='us-west-2';")
    # The agent proxy injects bogus S3 credentials, which turns an anonymous public read
    # into `InvalidAccessKeyId ... "proxy-injected"` — a failure that reads like an Overture
    # outage but is purely local. Forcing empty credentials restores the anonymous path.
    con.execute("SET s3_access_key_id=''; SET s3_secret_access_key='';")
    con.execute(f"SET threads={threads};")
    return con


def _source(release: str) -> str:
    return f"{S3_ROOT}/{release}/theme=places/type=place/*"


def _bbox_union(regions: list[str]) -> tuple[float, float, float, float]:
    missing = [r for r in regions if r not in BBOX]
    if missing:
        sys.exit(f"no bounding box for {', '.join(missing)} — add it to BBOX in this file")
    boxes = [BBOX[r] for r in regions]
    return (min(b[0] for b in boxes), max(b[1] for b in boxes),
            min(b[2] for b in boxes), max(b[3] for b in boxes))


def _where(regions: list[str], release: str) -> tuple[str, list]:
    w, e, s, n = _bbox_union(regions)
    marks = ", ".join("?" * len(regions))
    sql = (f"FROM read_parquet('{_source(release)}', hive_partitioning=1) "
           " WHERE bbox.xmin BETWEEN ? AND ? AND bbox.ymin BETWEEN ? AND ? "
           f"   AND addresses[1].region IN ({marks}) "
           "   AND addresses[1].country = 'US' "
           "   AND (operating_status IS NULL OR operating_status = 'open')")
    return sql, [w, e, s, n, *regions]


def cmd_enumerate(con, regions, release, out_path):
    """Emit every category present, with counts, for a HUMAN to curate.

    Deliberately not an inline regex: substring matching over-captures in ways that are
    obvious only to a person. `dry_cleaning` matches "clean" and is not a home service —
    that is the whole reason this is a reviewed, checked-in artifact.
    """
    where, args = _where(regions, release)
    rows = con.execute(
        "SELECT coalesce(categories.primary, basic_category) AS category, count(*) AS n "
        + where + " GROUP BY 1 HAVING n >= 25 ORDER BY n DESC", args).fetchall()
    payload = {
        "_comment": "CANDIDATE — review by hand, then save as overture_categories.json.",
        "_release": release, "_regions": regions,
        "candidates": {c: n for c, n in rows if c},
    }
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"{len(rows)} categories ≥25 rows → {out_path}")
    for c, n in rows[:40]:
        print(f"  {n:>7,}  {c}")
    return 0


def _load_allowlist() -> list[str]:
    if not ALLOWLIST_PATH.exists():
        sys.exit(f"missing {ALLOWLIST_PATH} — run --enumerate first, then curate it by hand")
    data = json.loads(ALLOWLIST_PATH.read_text())
    cats = [c for c in data.get("include", []) if c]
    if not cats:
        sys.exit(f"{ALLOWLIST_PATH} has an empty 'include' list — nothing to extract")
    return cats


# Column names are our IMPORTER's field names on purpose: the sniffer then detects them at
# confidence 1.0 and the dry-run map needs no human confirmation.
SELECT = """
  'overture:' || id                            AS source_id,
  names.primary                                AS company,
  coalesce(categories.primary, basic_category)  AS category,
  phones[1]                                    AS phone,
  websites[1]                                  AS website,
  emails[1]                                    AS email,
  addresses[1].freeform                        AS address,
  addresses[1].locality                        AS locality,
  addresses[1].region                          AS region,
  addresses[1].postcode                        AS postal
"""


def cmd_extract(con, regions, release, out_path, limit):
    cats = _load_allowlist()
    where, args = _where(regions, release)
    marks = ", ".join("?" * len(cats))
    sql = (f"SELECT {SELECT} " + where +
           f" AND coalesce(categories.primary, basic_category) IN ({marks})")
    if limit:
        sql += f" LIMIT {int(limit)}"

    print(f"release={release} regions={','.join(regions)} categories={len(cats)}")
    print("scanning Overture (remote parquet — first run pulls metadata, be patient)…")
    cur = con.execute(sql, [*args, *cats])
    cols = [d[0] for d in cur.description]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    per_state: dict[str, int] = {}
    with_email = with_phone = 0
    # Stream to disk in batches — the point of the CSV artifact is that neither this script
    # nor the Droplet ever holds 39k rows in memory.
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        while True:
            batch = cur.fetchmany(5000)
            if not batch:
                break
            for r in batch:
                # CWE-1236. Overture is open and user-contributed, which is exactly the
                # threat model `core.csv_safe` was written for — and this file gets opened
                # in a spreadsheet, because reviewing lead quality by eye is the point of
                # producing an artifact instead of writing straight to the DB.
                # MEASURED on this extract: one company literally named
                # '======================' and 39 junk '-' addresses. Real, and junk anyway.
                # Safe for the machine consumer too: the leading apostrophe lands only in
                # the CSV, and `phone_util.to_e164` strips non-digits, so a defanged
                # '+1865… still normalises to +1865… on import.
                # safe_row() takes a dict; this writer streams positional tuples, so the
                # per-cell primitive is the right one.
                w.writerow([csv_safe.safe_cell("" if v is None else v) for v in r])
                n += 1
                rec = dict(zip(cols, r))
                per_state[rec["region"]] = per_state.get(rec["region"], 0) + 1
                with_email += bool(rec["email"])
                with_phone += bool(rec["phone"])

    print(f"\n{n:,} businesses → {out_path}")
    for st in sorted(per_state):
        print(f"  {st}  {per_state[st]:,}")
    if n:
        print(f"  phone {with_phone:,} ({with_phone*100//n}%)   "
              f"email {with_email:,} ({with_email*100//n}%)")
    print(f"\nnext:  python scripts/import_contacts.py --file {out_path} "
          f"--list overture-{'-'.join(r.lower() for r in regions)} --dry-run")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--regions", default="NC,SC,TN", help="comma-separated, e.g. NC,SC,TN")
    p.add_argument("--release", default=DEFAULT_RELEASE)
    p.add_argument("--enumerate", action="store_true",
                   help="list categories with counts for human curation, then exit")
    p.add_argument("--out", default=None)
    p.add_argument("--limit", type=int, default=0, help="cap rows (smoke-testing only)")
    p.add_argument("--threads", type=int, default=4)
    a = p.parse_args(argv)

    regions = [r.strip().upper() for r in a.regions.split(",") if r.strip()]
    if not regions:
        sys.exit("--regions is required")
    slug = "-".join(r.lower() for r in regions)
    con = _connect(a.threads)

    if a.enumerate:
        out = pathlib.Path(a.out) if a.out else (
            REPO / "config" / "overture_categories.candidate.json")
        return cmd_enumerate(con, regions, a.release, out)

    out = pathlib.Path(a.out) if a.out else (
        OUT_DIR / f"overture_{slug}_{a.release}.csv")
    return cmd_extract(con, regions, a.release, out, a.limit)


if __name__ == "__main__":
    sys.exit(main())
