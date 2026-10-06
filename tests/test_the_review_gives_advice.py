"""Advice for today: the Morning Review's advisor (Morning Review V2 step 3, docs/PLAN_MORNING_REVIEW_ADVISOR.md §3, §6).

Owner, 2026-10-05: "Agree with all of your recommendations. Yes it's a go for phase 2." Decision 1: Sonnet for the one
daily advice call. Decision 4: "Ideas to try" becomes "Advice for today". Each piece in three short parts: what we saw
(a number the box measured), why it matters for you (a line of the business's own website, word for word), and today's
step (on a screen this box has).

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED (each guardrail of §6 is a check here):
  1. a number in advice that is not in the facts it was handed;
  2. a "why it matters" that is not a profile line word for word, or advice kept without one when the box has a profile;
  3. a step that links nowhere, or anywhere but a screen this box has or Approvals;
  6. advice that leads with a problem, or good news that does;
  7. a bad answer, a timeout or no AI costing the review its morning;
  * more than one call, on anything but the review task, or the review task on anything but Sonnet;
  * a customer's name or words reaching the model;
  * the three parts missing from the page, either email or Slack, or the section still called "Ideas to try".

No network, no model: the model is a function handed in.

Run: python tests/test_the_review_gives_advice.py
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
from datetime import date, datetime
from html import escape as _esc

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "advice.db")
os.environ["DASH_TOKEN"] = "pw"

from core import state  # noqa: E402

state.init_db()
from core import box_settings, report, review_advisor, review_brief, review_email  # noqa: E402
from core import business_context as bc  # noqa: E402
from core.dash import review as page  # noqa: E402
from core.dispatch import app  # noqa: E402,F401  (applies every machine's schema, registers the box's sections)

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -- {str(detail)[:500]}" if not cond and detail else ""))
    if not cond:
        _failed += 1


made = date.fromisoformat(str(state.owner_user()["created_at"])[:10])
ABOUT = date(made.year - 1, 9, 30)                 # long before the owner existed: an ordinary review, never a Welcome
NOW = datetime(ABOUT.year, 10, 1, 8, 0)
T = date(ABOUT.year, 10, 1)


def row(day, machine, title, **parts):
    with state.connect() as c:
        c.execute("INSERT OR REPLACE INTO daily_reports (day, machine, report_json, written_at, final) "
                  "VALUES (?,?,?,?,1)", (day.isoformat(), machine,
                                         json.dumps({"machine": machine, "title": title, **parts}), state._now()))


row(ABOUT, "inbox", "Inbox", happened=[{"text": "11 messages came in on Saturday", "value": 11}],
    figures={"new_week": {"value": 27, "label": "new conversations, last 7 days"}})
row(T, "inbox", "Inbox", answer_first=[{"text": "Dana Whitfield", "why": "Waiting 3d: “Do you have Saturday?”",
                                         "href": "/inbox/inbox/z-dana"}])
QUOTE = "Saturday appointments book out a week ahead."
bc.put("profile", [{"line": QUOTE, "source": "https://glow.example/booking", "field": "hours"}], by="full scan")
SCREENS = review_brief._installed() + [{"title": "Approvals", "href": "/approvals"}]
INBOX = next((i for i, s in enumerate(SCREENS, 1) if s["href"].startswith("/inbox")), len(SCREENS))
APPROVALS = len(SCREENS)
seen = []


def answer(*advice, good="27 people started a conversation last week."):
    def think(task, prompt, **kw):
        seen.append({"task": task, "prompt": prompt, "kw": kw})
        return json.dumps({"good_news": good, "advice": list(advice)})
    return think


def piece(title, saw, because=1, today="Answer Friday's messages first.", screen=None):
    return {"title": title, "saw": saw, "because": because, "today": today, "screen": INBOX if screen is None else screen}


GOOD = piece("Saturday is your busiest day. Answer Friday's first", "11 of your 27 new messages came in on Saturday.")
b = review_brief.build(ABOUT, NOW, think=answer(GOOD))

print("test_one_piece_in_three_parts")
a = (b.get("ideas") or [{}])[0]
ok("the advice keeps its title and what we saw", a.get("title") == GOOD["title"] and a.get("saw") == GOOD["saw"], a)
ok("why it matters is the business's own line, word for word, by its number", a.get("because") == QUOTE, a)
ok("the step links to the screen it named, one this box has",
   a.get("today") == GOOD["today"] and a.get("href") == SCREENS[INBOX - 1]["href"]
   and a.get("where") == SCREENS[INBOX - 1]["title"], a)
ok("one call, on the review task, with a bounded wait", len(seen) == 1 and seen[0]["task"] == "review"
   and seen[0]["kw"].get("timeout") == 90, seen[:1])
from core.config import get_config  # noqa: E402
ok("the review task is Sonnet (owner, 2026-10-05, decision 1)", get_config()["models"]["review"] == "sonnet")
handed = json.loads(seen[0]["prompt"])
ok("the model is handed the profile lines and the screens, numbered",
   handed["profile"] == [{"n": 1, "line": QUOTE}] and handed["screens"][INBOX - 1]["n"] == INBOX, handed.get("screens"))
ok("...and never a customer's name or words", "Dana" not in seen[0]["prompt"] and "Do you have Saturday" not in
   seen[0]["prompt"])

print("\ntest_the_guardrails")
for bad, why in ((piece("Aim for 40 messages", "An invented goal of 40."), "a number nobody measured (§6.1)"),
                 (piece("Run a $49 flash sale", "Nobody said $49."), "a price the business never published (§6.4)"),
                 (piece("Reply faster", "40 people wrote."), "a number nobody measured in what we saw (§6.1)"),
                 (piece("Reply faster", "11 came in.", today="Answer 15 of them by noon."),
                  "a number nobody measured in today's step (§6.1)"),
                 (piece("Reply faster", "11 came in.", because=7), "a why that is no line of the profile (§6.2)"),
                 (piece("Reply faster", "11 came in.", because=None), "no why, when the box has a profile (§6.2)"),
                 (piece("Reply faster", "11 came in.", screen=99), "a step on a screen this box doesn't have (§6.3)"),
                 (piece("Reply faster", "11 came in.", screen=None) | {"screen": None}, "a step with no screen (§6.3)"),
                 (piece("You are losing Saturday bookings", "11 came in."), "advice that leads with a problem (§6.6)"),
                 ({"title": "Half a piece"}, "a piece missing its parts")):
    box_settings.put(review_brief.NS, review_brief.IDEAS_SEEN, {}, set_by="test")
    got = review_brief.build(ABOUT, NOW, think=answer(bad)).get("ideas")
    ok(f"dropped: {why}", got == [], got)
box_settings.put(review_brief.NS, review_brief.IDEAS_SEEN, {}, set_by="test")
ok("a step on Approvals is a real place too", review_brief.build(ABOUT, NOW, think=answer(
   piece("Approve what is waiting", "11 came in.", screen=APPROVALS)))["ideas"][0]["href"] == "/approvals")
ok("good news that leads with a problem falls back to the plain true line",
   review_brief.build(ABOUT, NOW, think=answer(GOOD, good="You are losing 11 messages."))["good_news"] !=
   "You are losing 11 messages.")
box_settings.put(review_brief.NS, review_brief.IDEAS_SEEN, {}, set_by="test")
b2 = review_brief.build(ABOUT, NOW, think=lambda *x, **k: (_ for _ in ()).throw(TimeoutError()))
ok("an AI that times out still gives an on-time review, with no advice (§6.7)", b2["ideas"] == [] and b2["moving"], b2)
ok("with no AI in a test, nothing is called", review_advisor.advise(
   {"figures": {"x": 1}}, business=None, quotes=[], screens=SCREENS, recent=[]) == ("", []))
bc.put("profile", [], by="test")
box_settings.put(review_brief.NS, review_brief.IDEAS_SEEN, {}, set_by="test")
got = review_brief.build(ABOUT, NOW, think=answer(piece("Reply faster on Saturday", "11 came in.", because=None)))
ok("a box with no profile yet still gets advice, without the why", got["ideas"] and got["ideas"][0]["because"] == "",
   got["ideas"])

print("\ntest_every_surface")
bc.put("profile", [{"line": QUOTE, "source": "https://glow.example/booking", "field": "hours"}], by="full scan")
box_settings.put(review_brief.NS, review_brief.IDEAS_SEEN, {}, set_by="test")
b = {**review_brief.build(ABOUT, NOW, think=answer(GOOD)), "hidden": []}
html = page.brief_html(b, live=False)
ok("the page calls it Advice for today", "Advice for today" in html and "Ideas to try" not in html)
ok("...with its three labelled parts, the step linked to its screen",
   "What we saw:" in html and "Why it matters for you:" in html and "Today:" in html
   and f'href="{SCREENS[INBOX - 1]["href"]}">{_esc(GOOD["today"])}</a>' in html, html[-1500:])
ok("...and says where it came from", "The advice comes from its AI" in html)
text = review_email.text(b)
ok("the plain email: the section and its three parts", "ADVICE FOR TODAY" in text and "What we saw: 11 of your 27" in
   text and f"Why it matters for you: “{QUOTE}”" in text and "Today: Answer Friday's" in text, text)
mail = review_email.html(b)
ok("the HTML email: the three parts, the step linked", "What we saw:" in mail and "Today:</b>" in mail
   and _esc(GOOD["today"]) + "</a>" in mail)
slack = report.slack_text(b)
ok("Slack: the advice and its step", "Advice for today" in slack and "Today: Answer Friday's" in slack, slack[-600:])

print("\nALL ADVICE CHECKS PASS" if not _failed else f"\n{_failed} ADVICE CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
