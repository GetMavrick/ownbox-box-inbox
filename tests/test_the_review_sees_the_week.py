"""The Morning Review's AI sees the last seven days, not one (#1953 Phase 1, step 1.2).

"Up three days running" needs the week; yesterday alone can only say yesterday. The review hands its AI each
machine's stored headline for the week ending yesterday, from the history the box already keeps.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * the AI is not given the week, or gets it out of order, or is not told what it is;
  * a machine with a single day, or a day with no report, is drawn as a week or as a zero;
  * a date's day-of-month slips into the facts, so a number nobody measured passes the check;
  * a number from the week cannot be quoted back, or an invented one can.

No network, no model.

Run: python tests/test_the_review_sees_the_week.py
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import tempfile
from datetime import datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "review_week.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state  # noqa: E402

state.init_db()
from datetime import date  # noqa: E402

from core import review_brief  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -- {str(detail)[:500]}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def row(day, machine, title, **parts):
    with state.connect() as c:
        c.execute("INSERT OR REPLACE INTO daily_reports (day, machine, report_json, written_at, final) "
                  "VALUES (?,?,?,?,1)", (day.isoformat(), machine,
                                         json.dumps({"machine": machine, "title": title, **parts}), state._now()))


# BEFORE THE OWNER EXISTED, so this is an ordinary review and never the Welcome edition.
made = date.fromisoformat(str(state.owner_user()["created_at"])[:10])
ABOUT = made - timedelta(days=20)
NOW = datetime.combine(ABOUT + timedelta(days=1), datetime.min.time()).replace(hour=8)
T = ABOUT + timedelta(days=1)
SERIES = [3, 5, None, 7, 9, 11, 12]                 # oldest first; None is a day the box did not report
for back, v in zip(range(6, -1, -1), SERIES):
    if v is not None:
        d = ABOUT - timedelta(days=back)
        row(d, "inbox", "Inbox", headline={"value": v, "label": "messages came in"},
            happened=[{"text": "messages came in", "value": v}])
row(ABOUT - timedelta(days=9), "inbox", "Inbox", headline={"value": 99, "label": "messages came in"})   # outside
row(ABOUT, "gtm", "Lead Machine", headline={"value": 23, "label": "emails sent"},
    happened=[{"text": "emails sent", "value": 23}])                                                  # one day only
row(T, "inbox", "Inbox", needs_you=[{"text": "2 people would love a reply", "href": "/inbox"}])

seen = []


def think(task, prompt, **kw):
    seen.append({"prompt": json.loads(prompt), "system": kw.get("system") or ""})
    # THE ADVISOR'S SHAPE (V2 step 3): a step on a screen this box has.
    return json.dumps({"good_news": "Messages came in for the third day running, 12 yesterday.", "advice": [
        {"title": "Keep Friday's pace: 11 then 12", "saw": "Your week climbed from 3.", "because": None,
         "today": "Answer today's first.", "screen": 1},
        {"title": "Aim for 28 messages", "saw": "An invented goal.", "because": None, "today": "Push.",
         "screen": 1}]})


print("test_the_ai_sees_the_week")
b = review_brief.build(ABOUT, NOW, think=think)
week = (seen[0]["prompt"]["facts"] if seen else {}).get("last_7_days") or {}
inbox = week.get("Inbox: messages came in") or {}
ok("the AI is handed each machine's headline for the last seven days", bool(inbox), seen[:1])
ok("...oldest first, ending with yesterday, by weekday",
   list(inbox.values()) == [3, 5, 7, 9, 11, 12] and list(inbox)[-1] == ABOUT.strftime("%A"), inbox)
ok("a day with no report is absent, never a zero", len(inbox) == 6 and 0 not in inbox.values(), inbox)
ok("a day outside the week is left out", 99 not in inbox.values(), inbox)
ok("a machine with one day is not drawn as a week", not any(k.startswith("Lead Machine") for k in week), week)
ok("no date's day-of-month rides in with the week", not re.search(r"\d", "".join(week) + "".join(inbox)), week)
ok("the prompt says what the week is and to read it as one",
   "last_7_days" in seen[0]["system"] and "streak" in seen[0]["system"], seen[0]["system"][-400:])

print("\ntest_the_week_grounds_ideas")
ok("a sentence using the week's numbers is kept", b["good_news"] == "Messages came in for the third day running, "
   "12 yesterday.", b["good_news"])
titles = [i["title"] for i in b["ideas"]]
ok("an idea quoting the week (11, 12, 3) is kept", "Keep Friday's pace: 11 then 12" in titles, titles)
ok("an idea with a number nobody measured is dropped", not any("28" in t for t in titles), titles)

print("\ntest_a_single_day_is_the_old_review")
with state.connect() as c:
    c.execute("DELETE FROM daily_reports WHERE day < ?", (ABOUT.isoformat(),))
seen.clear()
review_brief.build(ABOUT, NOW, think=think)
ok("with no week behind it, the AI is asked exactly as before", "last_7_days" not in seen[0]["prompt"]["facts"],
   seen[0]["prompt"]["facts"])

print("\nALL REVIEW-WEEK CHECKS PASS" if not _failed else f"\n{_failed} REVIEW-WEEK CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
