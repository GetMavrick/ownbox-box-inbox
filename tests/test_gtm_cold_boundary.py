#!/usr/bin/env python3
"""Cold prospect data must never be shaped for GoHighLevel. No cost, no network.

WHY THIS TEST EXISTS — it is a scar, not a hypothetical. AIOS produced a lead export named
`ghl_import_sc_200.csv` carrying GoHighLevel's exact import column names. Nothing pushed it
anywhere; the filename and column shape were the whole problem. It read as "import me into
GHL", someone did, and ~200 cold records had to be deleted out of GHL by hand because they
clogged the pipeline that is supposed to hold people who actually raised a hand.

THE BOUNDARY, owner-set: GHL holds WARM leads only — someone who filled out a form. Cold
prospects live in the AIOS prospect store and the Airtable Contacts mirror, and nowhere else.

WHAT THIS ENFORCES, mechanically:
  1. No committed data file is named to invite a GHL import.
  2. No cold-prospect CSV carries GHL's import column signature.
  3. No code POSTs to a GHL / LeadConnector endpoint.

Rule 3 passes today with nothing to remove — there has never been a GHL webhook in this repo.
It is here so that the day someone adds one, this fails instead of a person discovering it in
production the way the CSV was discovered.

INBOUND IS FINE and deliberately still allowed: `scripts/import_gtm_contacts.py` reads a GHL
export INTO Airtable. That direction carries warm data toward AIOS and is the opposite of the
failure. Only cold data flowing OUT toward GHL is the hazard.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

# What makes a file GHL-shaped rather than merely person-shaped. This distinction cost a
# first draft of this test: 'First Name / Last Name / Business Name' is ALSO the Airtable
# Contacts vocabulary, which is exactly where cold prospects are SUPPOSED to go, so matching
# on those flagged the legitimate mirror export and would have trained everyone to ignore it.
# The real tells are GHL's own columns — 'Contact Id' is its export key, and 'Tags' is its
# native segmentation field. Neither appears in the Airtable mirror.
_GHL_KEY_COLS = {"contact id"}
_GHL_NATIVE = {"tags"}
_PERSON_COLS = {"first name", "last name"}

_ENDPOINT = re.compile(r"leadconnectorhq|services\.leadconnector|gohighlevel\.com/v\d|"
                       r"rest\.gohighlevel\.com", re.I)

_failures = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail and not ok else ''}")
    if not ok:
        _failures.append(label)


def _data_files() -> list:
    out = []
    for dirpath, _dirs, files in os.walk(DATA):
        for f in files:
            if f.lower().endswith((".csv", ".json", ".xlsx")):
                out.append(os.path.join(dirpath, f))
    return out


def test_no_ghl_named_data_files() -> None:
    bad = [os.path.relpath(p, ROOT) for p in _data_files()
           if re.search(r"\bghl\b|gohighlevel", os.path.basename(p), re.I)]
    check("no committed data file is named for a GHL import", not bad, ", ".join(bad))


def test_no_cold_csv_carries_the_ghl_column_signature() -> None:
    offenders = []
    for path in _data_files():
        if not path.lower().endswith(".csv"):
            continue
        try:
            with open(path, newline="", encoding="utf-8") as fh:
                header = (fh.readline() or "").strip()
        except OSError:
            continue
        cols = {c.strip().strip('"').lower() for c in header.split(",")}
        ghl_shaped = (cols & _GHL_KEY_COLS) or ((cols & _GHL_NATIVE) and (cols & _PERSON_COLS))
        if ghl_shaped:
            offenders.append(os.path.relpath(path, ROOT))
    check("no cold-prospect CSV is shaped for a GHL import", not offenders,
          ", ".join(offenders))


def test_no_code_posts_to_ghl() -> None:
    hits = []
    for dirpath, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs
                   if d not in {".git", "__pycache__", "node_modules", ".venv", "data", "docs"}]
        for f in files:
            if not f.endswith((".py", ".sh", ".yaml", ".yml")):
                continue
            p = os.path.join(dirpath, f)
            if os.path.samefile(p, os.path.abspath(__file__)):
                continue
            try:
                with open(p, encoding="utf-8", errors="ignore") as fh:
                    body = fh.read()
            except OSError:
                continue
            if _ENDPOINT.search(body):
                hits.append(os.path.relpath(p, ROOT))
    check("no code reaches a GoHighLevel / LeadConnector endpoint", not hits, ", ".join(hits))


def main() -> int:
    print("cold-data / GHL boundary")
    test_no_ghl_named_data_files()
    test_no_cold_csv_carries_the_ghl_column_signature()
    test_no_code_posts_to_ghl()
    if _failures:
        print(f"\nFAILED — {len(_failures)}: {', '.join(_failures)}")
        print("Cold prospects belong in the AIOS store and the Airtable mirror. GHL is warm only.")
        return 1
    print("\nALL COLD-BOUNDARY CHECKS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
