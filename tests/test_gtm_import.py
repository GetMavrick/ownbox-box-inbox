#!/usr/bin/env python
"""Offline regression checks for the GoHighLevel contact importer (no network).

Guards the pure mapping logic in scripts/import_gtm_contacts.py:
  - date normalization (GHL ISO 'Created' + human 'Last Activity'),
  - Source derivation from tags,
  - schema-aware field mapping (only emit fields the table actually has; mirror
    Business Name -> Company only when a 'Company' field exists).
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
_spec = importlib.util.spec_from_file_location("imp", os.path.join(HERE, "..", "scripts", "import_gtm_contacts.py"))
imp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(imp)

_FULL = {"Name", "First Name", "Last Name", "Email", "Phone", "Contact Id", "Created",
         "Last Activity", "Business Name", "Company", "Tags", "Source"}


def test_to_iso():
    assert imp.to_iso("May 10 2026 06:57 PM") == "2026-05-10T18:57:00"
    assert imp.to_iso("Mar 09 2026 01:59 PM") == "2026-03-09T13:59:00"
    assert imp.to_iso("2025-06-27T16:56:26-07:00") == "2025-06-27T16:56:26-07:00"
    assert imp.to_iso("") == ""
    assert imp.to_iso("   ") == ""
    assert imp.to_iso("not a date") == "not a date"   # passthrough → Airtable typecast tries


def test_source_from_tags():
    assert imp.source_from_tags("meta lead, aibotactive") == "Meta Lead"
    assert imp.source_from_tags("setupclaw-cold-outreach") == "Cold Outreach"
    assert imp.source_from_tags("subscriber, skool, trial-member") == "Skool"
    assert imp.source_from_tags("subscriber") == "Inbound"
    assert imp.source_from_tags("trial-member") == "Inbound"
    assert imp.source_from_tags("credit repair") == "Import"


def test_row_mapping_with_company():
    row = {"Contact Id": "abc", "First Name": "Brian", "Last Name": "Mac", "Email": "b@x.co",
           "Phone": "+15551112222", "Business Name": "Acme",
           "Created": "2025-06-27T16:56:26-07:00", "Last Activity": "May 10 2026 06:57 PM",
           "Tags": "meta lead, vide"}
    f = imp._row_to_fields(row, _FULL)
    assert f["Name"] == "Brian Mac"
    assert f["First Name"] == "Brian" and f["Last Name"] == "Mac"
    assert f["Business Name"] == "Acme" and f["Company"] == "Acme"   # mirror → AI enrichment
    assert f["Tags"] == ["meta lead", "vide"] and f["Source"] == "Meta Lead"
    assert f["Last Activity"] == "2026-05-10T18:57:00"
    assert f["Created"] == "2025-06-27T16:56:26-07:00"


def test_row_mapping_schema_aware():
    # fresh-clone schema: no 'Company', no 'Name' primary → those keys must be dropped
    row = {"Contact Id": "abc", "First Name": "Brian", "Last Name": "Mac",
           "Business Name": "Acme", "Email": "b@x.co"}
    known = {"First Name", "Last Name", "Contact Id", "Business Name"}
    f = imp._row_to_fields(row, known)
    assert "Company" not in f and "Name" not in f and "Email" not in f
    assert f["Business Name"] == "Acme" and f["Contact Id"] == "abc"


def test_no_phone_no_tags():
    row = {"Contact Id": "x", "First Name": "Thec", "Last Name": "Dany",
           "Phone": "", "Email": "", "Tags": ""}
    f = imp._row_to_fields(row, _FULL)
    assert f["Name"] == "Thec Dany"
    assert "Phone" not in f and "Email" not in f and "Tags" not in f and "Source" not in f


def test_empty_row():
    assert imp._row_to_fields({}, _FULL) == {}


if __name__ == "__main__":
    passed = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print("ok", _name)
            passed += 1
    print(f"all {passed} GTM import checks passed")
