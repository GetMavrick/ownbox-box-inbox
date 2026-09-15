#!/usr/bin/env python3
"""Stamp licence.json for a buyer, and print what /leadmachine will show on first run.

    python scripts/licence_stamp.py --buyer "Acme Billing LLC" --order "pi_3Q..."

Not copy protection — nothing at $99 is. Attribution: a resold clone carries whose name it
was issued to, which is the realistic goal.
"""
import argparse, json, pathlib, datetime
ap = argparse.ArgumentParser()
ap.add_argument("--buyer", required=True); ap.add_argument("--order", required=True)
ap.add_argument("--out", default="licence.json")
ap.add_argument("--product", default="lead-machine", choices=["lead-machine","content-machine","aios"])
a = ap.parse_args()
doc = {"product": a.product, "licence_version": 1, "buyer": a.buyer, "order": a.order,
       "issued": datetime.date.today().isoformat(), "configs_included": "all"}
pathlib.Path(a.out).write_text(json.dumps(doc, indent=2) + "\n")
print(f"Licensed to {a.buyer} — order {a.order} — issued {doc['issued']}")
