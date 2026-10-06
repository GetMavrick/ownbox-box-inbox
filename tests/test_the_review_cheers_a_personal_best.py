"""The Morning Review cheers a personal best, in one line (#1953 Phase 1, step 1.4).

The plan (docs/PLAN_MORNING_REVIEW_ADVISOR.md §0, 1.4): "One cheerful line when a headline beats its own 30-day record.
Arithmetic on stored history, no AI." Under the good news, on the page, in both emails and on Slack, and hideable per
person like a section.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * a headline that beats its record for the last 30 days is not cheered, or says the wrong number or machine;
  * a headline where more is worse (people waiting on you), or one whose machine never said, is cheered;
  * a tie, a zero, a failed report or a box with under a week of history is called a record;
  * when two records fall on one day, the smaller jump wins;
  * the line is missing from the page, either email or Slack; Hide does not hide it, on the page and in the email;
  * the contract drops `headline.better`, or keeps a value it does not know.

No network, no model.

Run: python tests/test_the_review_cheers_a_personal_best.py
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
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "best.db")
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


T = report.today()
ABOUT = T - timedelta(days=1)
NOW = datetime.combine(T, datetime.min.time()).replace(hour=8)


def store(day: date, machine: str, title: str, value, label: str, better=None, error=None):
    head = {"value": value, "label": label, **({"better": better} if better else {})}
    rep = report._normalize(machine, title, {"title": title, "headline": head})
    if error:                                    # as report.snapshot marks a reporter that failed
        rep["error"] = error
    with state.connect() as c:
        c.execute("INSERT OR REPLACE INTO daily_reports (day, machine, report_json, written_at, final) "
                  "VALUES (?,?,?,?,1)", (day.isoformat(), machine, json.dumps(rep), state._now()))


def series(machine, title, label, past, today_v, better=None, error=None):
    for i, v in enumerate(past):                 # oldest first, ending the day before ABOUT
        store(ABOUT - timedelta(days=len(past) - i), machine, title, v, label, better)
    store(ABOUT, machine, title, today_v, label, better, error)


def clear():
    with state.connect() as c:
        c.execute("DELETE FROM daily_reports")


def best() -> str:
    return review_brief._compose(ABOUT, NOW, ai=False)[0].get("best") or ""


print("test_a_record_is_cheered")
series("lead_machine", "Lead Machine", "emails sent", [5, 8, 12, 20, 9, 11, 14, 10, 7, 13], 30, "more")
ok("a headline past its 30-day record gets one cheerful line, with the number, the words and the machine",
   best() == "New personal best: 30 emails sent, the most in 30 days (Lead Machine).", best())
clear()
series("lead_machine", "Lead Machine", "emails sent", [5, 8, 12, 20, 9, 11, 14, 10, 7, 13], 20, "more")
ok("matching the record is not beating it", best() == "", best())
clear()
series("lead_machine", "Lead Machine", "emails sent", [5, 8, 12], 30, "more")
ok("under a week of history is no record (a new box would break one every morning)", best() == "", best())
clear()
series("lead_machine", "Lead Machine", "emails sent", [0] * 10, 0, "more")
ok("a zero is never a best", best() == "", best())
clear()
series("lead_machine", "Lead Machine", "emails sent", [5] * 10, 30, "more", error="could not report")
ok("a report that failed is never cheered", best() == "", best())
clear()
series("lead_machine", "Lead Machine", "emails sent", [5] * 40, 30, "more")
store(ABOUT - timedelta(days=35), "lead_machine", "Lead Machine", 99, "emails sent", "more")
ok("only the last 30 days count: a bigger day five weeks ago does not stop it", best().startswith(
   "New personal best: 30 emails sent"), best())

print("\ntest_only_good_news_is_cheered")
clear()
series("customer_voice", "Inbox Machine", "waiting on you", [3, 4, 2, 5, 6, 4, 3, 5], 40, "less")
ok("a record number of people waiting is never a personal best", best() == "", best())
series("aeo_machine", "AEO Machine", "articles published", [1, 0, 1, 1, 0, 1, 1, 0], 4)
ok("nor is a headline whose machine never said which way is good", best() == "", best())

print("\ntest_the_biggest_jump_wins")
clear()
series("lead_machine", "Lead Machine", "emails sent", [10] * 10, 15, "more")
series("foundation", "Website", "visits from people this week", [100] * 10, 300, "more")
ok("two records on one day: the one furthest past its old record is named",
   best() == "New personal best: 300 visits from people this week, the most in 30 days (Website).", best())

print("\ntest_it_reaches_every_surface")
b = {**review_brief._compose(ABOUT, NOW, ai=False)[0], "hidden": []}
line = b["best"]
ok("on the page, under the good news", line in page.brief_html(b, live=False))
ok("...with its own Hide when someone is signed in",
   'name="section" value="best"' in page.brief_html(b, live=False, back="/app/review"))
ok("...and gone when they hid it, back with See everything",
   line not in page.brief_html(b, live=False, hidden={"best"}, back="/app/review")
   and line in page.brief_html(b, live=False, hidden={"best"}, show_all=True, back="/app/review"))
ok("in the plain email and the HTML email", line in review_email.text(b) and line in review_email.html(b))
ok("...and not in the email of someone who hid it",
   line not in review_email.text({**b, "hidden": ["best"]}) and line not in review_email.html({**b, "hidden": ["best"]}))
ok("on Slack", line in report.slack_text(b), report.slack_text(b)[:400])
ok("a person can hide it like a section", "best" in review_brief.section_keys()
   and review_brief.set_hidden(state.owner_user()["id"], "best", True)
   and "best" in review_brief.hidden_for(state.owner_user()["id"]))
ok("a morning with only a personal best still has something to say", not review_brief._compose(
   ABOUT, NOW, ai=False)[0]["empty"])

print("\ntest_the_contract_carries_which_way_is_good")
ok("`better` is kept", report._normalize("m", "M", {"headline": {"value": 1, "label": "x", "better": "more"}})
   ["headline"]["better"] == "more")
ok("...and a value it does not know is dropped", report._normalize(
   "m", "M", {"headline": {"value": 1, "label": "x", "better": "up"}})["headline"]["better"] is None)

print("\nALL PERSONAL-BEST CHECKS PASS" if not _failed else f"\n{_failed} PERSONAL-BEST CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
