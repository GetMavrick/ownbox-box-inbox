"""Matching a business's own Airtable fields (core/airtable/fields.py). Owner, 2026-10-01: "Go, build the field
matching", after the AEO Machine made him rename "Seed" to "Seed Idea".

  · his real WRITTEN table matches the AEO Machine's three roles with no change, and so does his ORIGINAL
    table, where the field was called "Seed";
  · the exact name beats a likely one; a likely one beats a name that only contains it; the wrong kind of
    field never matches; one field serves one role;
  · what can't be matched is listed with its plain label, and the dropdown still offers every field that
    could hold it;
  · his Status options map to the machine's own states;
  · the map is saved per machine in the box's settings, and machine code reads names through it;
  · a strange table never raises.

Run: python tests/test_airtable_fields.py
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
T = pathlib.Path(tempfile.mkdtemp())
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")

from core import state  # noqa: E402
from core.airtable import fields as af  # noqa: E402

state.init_db()
FAILS: list[str] = []


def ok(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


# THE AEO MACHINE'S THREE ROLES, the way it will declare them.
QUESTION = af.Role("question", "the question each article answers", "long_text",
                   ("Seed Idea", "Seed", "Question", "Idea", "Topic", "Prompt"))
STATUS = af.Role("status", "where each article is", "status", ("Status", "Stage", "State"))
URL = af.Role("url", "the published article's address", "url", ("URL", "Link", "Article URL", "Published URL"))
AEO = (QUESTION, STATUS, URL)


def F(*pairs):
    return [{"name": n, "type": t} for n, t in pairs]


# THE OWNER'S REAL WRITTEN TABLE (read 2026-10-01; names and types only, never its id: tests ship to boxes).
WRITTEN = F(("ID", "autoNumber"), ("Priority", "number"), ("Title", "singleLineText"),
            ("Seed Idea", "multilineText"), ("Body", "multilineText"), ("Type", "singleSelect"),
            ("Status", "singleSelect"), ("Images", "multipleAttachments"), ("Keyword", "singleLineText"),
            ("URL", "url"), ("Category", "singleSelect"), ("SEO Keyword", "singleLineText"),
            ("Slug", "singleLineText"), ("X URL", "url"), ("LinkedIn URL", "url"), ("Threads URL", "url"),
            ("Bluesky URL", "url"), ("Date Posted", "dateTime"))

print("\nhis tables —")
m = af.match(AEO, WRITTEN)
ok("his WRITTEN table matches all three with no change",
   m == {"question": "Seed Idea", "status": "Status", "url": "URL"} and af.missing(AEO, m) == [], str(m))
original = [dict(f, name="Seed") if f["name"] == "Seed Idea" else f for f in WRITTEN]
ok("...and so does the table as it was, with \"Seed\": nobody renames anything",
   af.match(AEO, original)["question"] == "Seed", str(af.match(AEO, original)))

print("\nhow it chooses —")
ok("the exact name beats a likely one",
   af.match(AEO, F(("Idea", "multilineText"), ("Question", "multilineText")))["question"] == "Question")
ok("a likely name beats one that only contains it",
   af.match(AEO, F(("Article Idea Notes", "multilineText"), ("Topic", "singleLineText")))["question"] == "Topic")
ok("...but a name that contains one still matches when nothing better is there",
   af.match(AEO, F(("Blog Topic", "multilineText"), ("Status", "singleSelect"), ("Link", "url")))
   == {"question": "Blog Topic", "status": "Status", "url": "Link"})
ok("the wrong kind of field never matches, whatever its name",
   af.match(AEO, F(("Status", "checkbox"), ("URL", "number")))["status"] is None
   and af.match(AEO, F(("URL", "number")))["url"] is None)
TITLE = af.Role("title", "the article's title", "text", ("Title", "Headline", "Seed"))
both = af.match((QUESTION, TITLE), F(("Seed", "multilineText")))
ok("one field serves one role", list(both.values()).count("Seed") == 1, str(both))

print("\nwhat can't be matched —")
site = F(("Website", "url"), ("Notes", "multilineText"), ("Status", "singleSelect"))
mm = af.match(AEO, site)
ok("an unmatched role is listed with its plain label, for the screen to offer a fix",
   ("url", "the published article's address", "url") in af.missing(AEO, mm) and mm["status"] == "Status", str(mm))
ok("...and the dropdown still offers every field that could hold it", af.candidates(URL, site) == ["Website"])
ok("an optional role is never listed as missing",
   af.missing((af.Role("perf", "performance", "long_text", required=False),), {}) == [])

print("\nhis statuses —")
STATES = {"to_write": ("Create Article", "To Write", "Ready to Write", "Approved"),
          "published": ("Posted", "Published", "Live"), "draft": ("Draft", "Idea", "Backlog")}
OPTIONS = ["Draft", "Create Article", "Create Carousel", "Create Social", "Ready to Post", "Posted", "Archived"]
ok("his Status options map to the machine's own states",
   af.match_options(STATES, OPTIONS) == {"to_write": "Create Article", "published": "Posted", "draft": "Draft"},
   str(af.match_options(STATES, OPTIONS)))

print("\nthe saved map —")
af.save_map("aeo_machine", {"question": "Seed", "status": "Status", "url": "URL", "unused": None}, by="owner")
ok("the map is saved per machine, and machine code reads names through it",
   af.field("aeo_machine", "question") == "Seed" and af.load_map("aeo_machine") == {
       "question": "Seed", "status": "Status", "url": "URL"})
ok("another machine on the same base has its own map", af.load_map("lead_magnet") == {}
   and af.field("lead_magnet", "keyword", "Keyword") == "Keyword")

print("\nstrange tables —")
for weird in (None, [], [None, "x", {"type": "url"}, {"name": "", "type": "url"}]):
    try:
        got = af.match(AEO, weird)
        ok(f"a strange table ({weird!r:.30}) never raises", all(v is None for v in got.values()))
    except Exception as e:  # noqa: BLE001
        ok(f"a strange table ({weird!r:.30}) never raises", False, repr(e))

print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    sys.exit(1)
print("all passed")
