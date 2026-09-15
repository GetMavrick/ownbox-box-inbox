#!/usr/bin/env python3
"""NOTHING IS PUBLISHED ON ANY PAGE (MACHINE_SHOP_SPEC §8 rule 1) — as a test, not a sentence.

`data/machines/<slug>.json` is what a proof page renders. It carries AGGREGATES ONLY: counts,
plate tallies, a run log. Per OSDev2's review of the build spec against MACHINE_SHOP_SPEC rule 1,
DOSSIERS ARE OUT ENTIRELY — block 10 replaced sample records with a rejection ladder, so a
`dossiers` array is itself the violation, before anything inside it is inspected. The NPI /
phone / named-individual scans stay as defence in depth for whatever else lands in the file. The spec
says so; the investor-wall gap taught us today what a sentence is worth. This fails the build.

Self-proves on fixtures first: a scanner that matches nothing reports PASS forever.
"""
import json, re, sys, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
FLOOR = ROOT / "data" / "machines"   # was data/floor; "floor" is retired with "engine"
_fails = []

NPI = re.compile(r"(?<!\d)\d{10}(?!\d)")
PHONE = re.compile(r"(?:\+?1[\s\-.]?)?\(?\d{3}\)?[\s\-.]\d{3}[\s\-.]\d{4}")
PERSON_KEYS = {"name", "first_name", "last_name", "official_name", "authorized_official",
               "provider_name", "contact_name"}


def ok(n, c, d=""):
    print(f"  {'ok  ' if c else 'FAIL'} {n}" + (f"  — {d}" if d and not c else ""))
    if not c: _fails.append(n)


def violations(doc) -> list:
    """Walk any JSON; return every leak with its path."""
    out = []
    def walk(x, path):
        # A plate is named "poured" and a stat is named "solo practitioners" — `name` is a
        # person only inside a per-record dossier. The first draft flagged every plate.
        in_dossier = ".dossiers[" in path
        if isinstance(x, dict):
            for k, v in x.items():
                if in_dossier and k in PERSON_KEYS and isinstance(v, str) and v.strip():
                    out.append(f"{path}.{k}: named individual")
                walk(v, f"{path}.{k}")
        elif isinstance(x, list):
            for i, v in enumerate(x): walk(v, f"{path}[{i}]")
        elif isinstance(x, str):
            if NPI.search(x): out.append(f"{path}: NPI-shaped number")
            if PHONE.search(x): out.append(f"{path}: phone number")
        elif isinstance(x, int) and 1_000_000_000 <= x <= 9_999_999_999 and not path.endswith(("count", "total", "read", "records")):
            out.append(f"{path}: NPI-shaped integer")
    if isinstance(doc, dict) and "dossiers" in doc:
        out.append("$.dossiers: a dossiers array is not allowed — aggregates only (rule 1)")
    walk(doc, "$"); return out


def test_the_scanner_can_see():
    bad = {"pulled_at": "2026-09-03", "dossiers": [
        {"org": "Everwell Residential Llc", "npi": "1386567824", "official_name": "M. Jones",
         "phone": "(503) 555-1234"}]}
    good = {"pulled_at": "2026-09-03", "stats": [{"label": "solo practitioners", "value": 4294}],
            "plates": [{"name": "poured", "count": 105139}, {"name": "worth a call", "count": 5935}],
            "run_log": [{"plate": "poured", "count": 105139, "at": "2026-09-03T14:00:00Z"}],
            "register": "NPPES weekly — https://npiregistry.cms.hhs.gov"}
    orgs_only = {"pulled_at": "x", "dossiers": [{"org": "Everwell Residential Llc"}]}
    v = violations(bad)
    ok("catches an NPI", any("NPI" in x for x in v))
    ok("catches a phone number", any("phone" in x for x in v))
    ok("catches a named individual", any("individual" in x for x in v))
    ok("clears an aggregates-only machine file", violations(good) == [], str(violations(good)))
    ok("an orgs-only dossiers array is STILL a violation — aggregates only means no records",
       any("dossiers" in x for x in violations(orgs_only)))


def test_every_floor_file_is_aggregates_only():
    files = sorted(FLOOR.glob("*.json")) if FLOOR.is_dir() else []
    print(f"  ({len(files)} floor file(s) present)")
    for f in files:
        try: doc = json.loads(f.read_text())
        except Exception as e:
            ok(f"{f.name} parses", False, str(e)); continue
        ok(f"{f.name} carries pulled_at", bool(doc.get("pulled_at")))
        v = violations(doc)
        ok(f"{f.name} publishes nothing about an individual", not v, "; ".join(v[:4]))


def main():
    test_the_scanner_can_see()
    test_every_floor_file_is_aggregates_only()
    print(f"\n{'ALL FLOOR-PRIVACY CHECKS PASS' if not _fails else str(len(_fails)) + ' FAILED'}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
