"""The AEO Machine's section of the Morning Review: outcomes only, and nothing on a quiet day.

OSDev1, 2026-09-29, relaying the owner: the review shows the most important information from every
active machine, and the AEO Machine reported nothing. The owner's rule for every line: "If a line
doesn't have data, it should not be displayed."

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  · a fresh box, or a quiet day, gets an AEO section (any headline number, any line);
  · an article published that day is missing, or is named by something other than its title and
    the search it answers; one published another day is counted;
  · an article that is waiting on the owner (refused or failed) is not in "needs you", or has no
    way to the Articles screen; one he has since retried still is;
  · the title the writer gave an article is not kept on its row, so the review cannot name it.

Run: python tests/test_aeo_morning_report.py
"""
from __future__ import annotations

import datetime as dt
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "aeo_report.db")

from core import state                                                   # noqa: E402

state.init_db()

from core import report as core_report                                   # noqa: E402
from marketing.aeo_machine import plan, report                           # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


TODAY = core_report.today()
YESTERDAY = TODAY - dt.timedelta(days=1)
IN_TODAY = core_report.window(TODAY)[0]
IN_YESTERDAY = core_report.window(YESTERDAY)[0]


def stamp(plan_id, **cols):
    sets = ", ".join(f"{k} = ?" for k in cols)
    with state.connect() as c:
        c.execute(f"UPDATE seo_plan SET {sets} WHERE id = ?", (*cols.values(), plan_id))


def empty(rep) -> bool:
    """What the review draws nothing for: no headline number and no line (core/report.py rules)."""
    n = core_report._normalize(report.MACHINE, report.TITLE, rep)
    return (not n["headline"]["value"]) and not (n["happened"] or n["needs_you"] or n["watch"]
                                                 or n["notes"] or n["figures"])


print("test_registered")
ok("the AEO Machine registers its section, titled AEO Machine",
   core_report.REPORTERS.get("aeo_machine", {}).get("title") == "AEO Machine")
with state.connect() as c:
    cols = {r[1] for r in c.execute("PRAGMA table_info(seo_plan)")}
ok("the plan table keeps an article's title (migration 57)", "title" in cols, sorted(cols))

print("\ntest_a_fresh_box_shows_nothing")
ok("no rows at all: an empty report", report.report(TODAY) == {}, report.report(TODAY))
ok("...which the review draws nothing for", empty(report.report(TODAY)))
planned = plan.add("Retainers", "How do retainers work?")
ok("a topic only planned is not an outcome: still nothing", report.report(TODAY) == {})

print("\ntest_the_days_outcomes")
a = plan.add("Scope", "What should a statement of work include?")
plan.mark(a, "published", slug="sow", url="https://northwind.example/articles/sow",
          title="What a statement of work should include")
stamp(a, published_at=IN_TODAY)
b = plan.add("Pricing", "Fixed fee or hourly?")
plan.mark(b, "published", slug="fee", url="https://northwind.example/articles/fee")
stamp(b, published_at=IN_TODAY)                                # before titles were kept
old = plan.add("Old", "An article from yesterday?")
plan.mark(old, "published", slug="old", url="https://northwind.example/articles/old", title="Old one")
stamp(old, published_at=IN_YESTERDAY)
r = report.report(TODAY)
ok("the headline counts the day's articles", r.get("headline") == {"value": 2, "label": "articles published", "better": "more"},
   r.get("headline"))
lines = [x["text"] for x in r.get("happened", [])]
ok("each article by its title, with the search it answers",
   "Published “What a statement of work should include”, answering "
   "“What should a statement of work include?”" in lines, lines)
ok("...and one published before titles were kept, by its question",
   "Published “Fixed fee or hourly?”" in lines, lines)
ok("yesterday's article is not today's", not any("Old one" in t for t in lines), lines)
ok("...but it is yesterday's", report.report(YESTERDAY).get("headline", {}).get("value") == 1)
ok("nothing waits on the owner, so there is no needs-you line", "needs_you" not in r)

one = report.report(YESTERDAY)
ok("one article reads in the singular", one["headline"]["label"] == "article published", one["headline"])

print("\ntest_what_waits_on_the_owner")
cut = plan.add("Rivals", "Is Acme better than us?")
plan.mark(cut, "refused", refusal='competitor: "Acme" (a rival is never named)')
stamp(cut, updated_at=IN_TODAY)
broke = plan.add("Tools", "Which tools do we use?")
plan.mark(broke, "failed", refusal="HTTPError: 401 from Sanity")
stamp(broke, updated_at=IN_TODAY)
r = report.report(TODAY)
needs = r.get("needs_you", [])
ok("a refused article waits on the owner, with the way to Articles",
   {"text": "“Is Acme better than us?” was stopped before publishing. Articles says why",
    "href": "/aeo/topics"} in needs, needs)
ok("...and so does one that failed", any("Which tools do we use?" in x["text"]
                                         and "could not be published" in x["text"] for x in needs), needs)
ok("...never with a raw error in the line", not any("HTTPError" in x["text"] for x in needs))
ok("the day's articles are still there beside them", r["headline"]["value"] == 2)
plan.request_now(broke)
ok("an article the owner has retried no longer waits on him",
   not any("Which tools" in x["text"] for x in report.report(TODAY).get("needs_you", [])))
ok("a day with nothing published but something waiting still reports that",
   not report.report(YESTERDAY + dt.timedelta(days=-5)).get("happened")
   and report.report(TODAY).get("needs_you"))
ok("...and a past day never shows what went wrong after it ended",
   not any("Acme" in x["text"] for x in report.report(YESTERDAY).get("needs_you", [])))

print("\ntest_the_title_is_kept_when_it_publishes")
src = (ROOT / "marketing/aeo_machine/job.py").read_text()
ok("the writing job hands the article's title to the plan when it publishes",
   'plan.mark(row_id, "published", slug=result["slug"], url=result["url"], title=fields.get("title"))'
   in src)
t = plan.add("Blank", "A question with a blank title?")
plan.mark(t, "published", slug="blank", url="https://northwind.example/articles/blank", title="   ")
stamp(t, published_at=IN_TODAY)
ok("a blank title is never shown: the article is named by its question",
   "Published \u201cA question with a blank title?\u201d" in [x["text"] for x in report.report(TODAY)["happened"]])

print("\ntest_the_snapshot_row")
out = core_report.snapshot(TODAY)
row = core_report.read(TODAY, "aeo_machine")
ok("the worker's snapshot stores the AEO section", out and "aeo_machine" in out["written"]
   and row and row[0]["title"] == "AEO Machine", out)

print("\n— and this file cannot silently fall out of CI —")
if (ROOT / ".github").is_dir():                  # a buyer's box has no repository
    ok("test_aeo_morning_report is in the workflow's suite list",
       "test_aeo_morning_report" in (ROOT / ".github/workflows/tests.yml").read_text())

print("\nALL OK" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
