"""On Mondays the Morning Review shows your week against the week before (Morning Review V2 step 2).

docs/PLAN_MORNING_REVIEW_ADVISOR.md §4: "On Mondays, your week: seven days against the seven before, and the one
biggest change." Step 2: "No model". Arithmetic on the stored headlines (report.history), each machine added up the way
its own headline says (headline.week): a day's count is summed, a running total is read as it stood.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * the block shows on a morning that is not a Monday, or not on a Monday;
  * a day's count is not summed over the week, or a running total is summed (counted seven times);
  * a machine whose headline never said how a week adds up is shown anyway;
  * a week with too few days stored is compared;
  * the biggest change is not first, or the words say up for down;
  * the block is missing from the page, either email or Slack, or cannot be hidden;
  * the contract drops `headline.week`, or keeps a value it does not know.

No network, no model.

Run: python tests/test_the_review_shows_your_week.py
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
from datetime import date, datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "week.db")
os.environ["DASH_TOKEN"] = "pw"

from core import state  # noqa: E402

state.init_db()
from core import report, review_brief, review_email  # noqa: E402
from core.dash import review as page  # noqa: E402
from core.dispatch import app  # noqa: E402,F401  (applies every machine's schema)

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -- {str(detail)[:500]}" if not cond and detail else ""))
    if not cond:
        _failed += 1


ABOUT = date(2026, 10, 4)                        # a Sunday: the week ending it is read on Monday morning
MONDAY = datetime(2026, 10, 5, 8, 0)
TUESDAY = datetime(2026, 10, 6, 8, 0)


def store(day, machine, title, value, label, week=None, better=None):
    head = {"value": value, "label": label, **({"week": week} if week else {}), **({"better": better} if better else {})}
    rep = report._normalize(machine, title, {"title": title, "headline": head})
    with state.connect() as c:
        c.execute("INSERT OR REPLACE INTO daily_reports (day, machine, report_json, written_at, final) "
                  "VALUES (?,?,?,?,1)", (day.isoformat(), machine, json.dumps(rep), state._now()))


def fortnight(machine, title, label, before, this, week=None, better=None):
    """`before` and `this`: the daily values of the two weeks, oldest first, ending ABOUT."""
    vals = list(before) + list(this)
    for i, v in enumerate(vals):
        store(ABOUT - timedelta(days=len(vals) - 1 - i), machine, title, v, label, week, better)


def week(now=MONDAY):
    return review_brief._compose(ABOUT, now, ai=False)[0].get("your_week") or []


fortnight("lead_machine", "Lead Machine", "emails sent", [5] * 7, [10] * 7, "sum", "more")
fortnight("foundation", "Website", "visits from people this week", [120, 130, 140, 145, 148, 150, 150],
          [155, 160, 165, 170, 175, 178, 180], "last", "more")
fortnight("customer_voice", "Inbox Machine", "waiting on you", [6, 7, 8, 8, 9, 8, 8], [7, 6, 5, 5, 4, 4, 4],
          "last", "less")
fortnight("aeo_machine", "AEO Machine", "articles published", [1] * 7, [2] * 7)     # never said how a week adds up
fortnight("content_machine", "Content", "published", [3] * 7, [None, None, None, None, 1, 2, 3], "sum", "more")

print("test_your_week_on_mondays")
w = week()
ok("on Monday, each machine that says how its week adds up, the biggest change first",
   [(i["machine"], i["title"]) for i in w] == [("Lead Machine", "70 emails sent"), ("Inbox Machine", "4 waiting on you"),
                                              ("Website", "180 visits from people this week")], w)
ok("a day's count is summed over the week (70 against 35)",
   w[0]["why"] == "35 the week before, up 100%, the biggest change", w[0])
ok("a running total is read as it stood, never summed (180 against 150)",
   w[2]["why"] == "150 the week before, up 20%", w[2])
ok("down reads as down", w[1]["why"] == "8 the week before, down 50%", w[1])
ok("a machine that never said how its week adds up is left out", "AEO Machine" not in {i["machine"] for i in w})
ok("a week with too few days stored is not compared", "Content" not in {i["machine"] for i in w})
ok("not on a Tuesday", week(TUESDAY) == [], week(TUESDAY))

print("\ntest_it_reaches_every_surface")
b = {**review_brief._compose(ABOUT, MONDAY, ai=False)[0], "hidden": []}
ok("on the page, under its heading", "Your week" in page.brief_html(b, live=False)
   and "70 emails sent" in page.brief_html(b, live=False))
ok("in the plain email and the HTML email", "YOUR WEEK" in review_email.text(b) and "70 emails sent" in
   review_email.html(b))
ok("on Slack", "70 emails sent" in report.slack_text(b), report.slack_text(b)[:400])
ok("a person can hide it like any section", "your_week" in review_brief.section_keys()
   and "70 emails sent" not in page.brief_html(b, live=False, hidden={"your_week"}, back="/app/review")
   and "70 emails sent" not in review_email.text({**b, "hidden": ["your_week"]}))
_best = review_brief._best
review_brief._best = lambda rows, about: ""     # so the week is the only thing this Monday has to say
ok("a Monday with only its week still has something to say",
   not review_brief._compose(ABOUT, MONDAY, ai=False)[0]["empty"])
review_brief._best = _best

print("\ntest_the_contract_carries_how_a_week_adds_up")
ok("`week` is kept", report._normalize("m", "M", {"headline": {"value": 1, "label": "x", "week": "sum"}})
   ["headline"]["week"] == "sum")
ok("...and a value it does not know is dropped", report._normalize(
   "m", "M", {"headline": {"value": 1, "label": "x", "week": "average"}})["headline"]["week"] is None)

print("\nALL YOUR-WEEK CHECKS PASS" if not _failed else f"\n{_failed} YOUR-WEEK CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
