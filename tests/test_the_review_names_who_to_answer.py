"""The Morning Review names who to answer first (#1953 Phase 1, step 1.5).

"72 waiting" is a number; "Dana, waiting 3d: 'Do you have Saturday openings?'" is a reply sent before breakfast. The
Inbox Machine's own reporter names three people from the last 30 days, by speed to lead (owner, 2026-10-06; until then
ads first, then first-time writers, OSDev1 10-05), with the first words of what they asked and a link to the
conversation. How the three are chosen, and the cards they are drawn on, is tests/test_who_to_answer_first_is_ranked.py. Who is left out (the box itself, pitches, robots) is tests/test_who_to_answer_first_is_a_person.py. Core names no machine: any reporter may
carry `answer_first`, and the review shows it.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * the order is not speed to lead; a thread older than 30 days is named; more than three are;
  * what they asked is not cut short, or the link does not open their conversation;
  * the names are missing from the page or either email;
  * a customer's name or words reach Slack (OSDev1: page and owner email only, never Slack);
  * a box whose reporters name nobody grows the section anyway.

No network, no model.

Run: python tests/test_the_review_names_who_to_answer.py
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "answer_first.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state  # noqa: E402

state.init_db()
from core import report, review_brief, review_email  # noqa: E402
from core.dash import review as page  # noqa: E402
from core.dispatch import app  # noqa: E402,F401  (applies every machine's schema)
from marketing.customer_voice import report as cv_report  # noqa: E402
from marketing.customer_voice.inbox import store  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -- {str(detail)[:500]}" if not cond and detail else ""))
    if not cond:
        _failed += 1


now = datetime.now(timezone.utc)
SP = cv_report._space_name()
PEOPLE = [  # zcid, name, hours waiting, from an ad, what they asked
    ("z-dana", "Dana Whitfield", 70, False, "Do you have any Saturday openings for a facial?"),
    ("z-marcus", "Marcus Cole", 5, True, "Saw your spring package ad, what does it include and how much is it for two?"),
    ("z-priya", "Priya Raman", 30, False, "Can I move my appointment?"),
    ("z-len", "Len Okafor", 2, False, "Is the 20% off still running?"),
    ("z-old", "Old Thread", 24 * 45, False, "Hello from last month"),
    ("z/odd id", "Reed Ashby", 1, False, "Gift cards?"),
]
for z, who, h, ad, said in PEOPLE:
    at = (now - timedelta(hours=h)).isoformat()
    store.upsert_conversation(space=SP, zcid=z, platform="email", participant=who, account_id="me@example.com",
                              last_inbound_at=at, ad_meta_id="ad-1" if ad else None)
    with state.connect() as c:
        c.execute("INSERT INTO inbox_messages (id, space, zernio_conversation_id, zernio_message_id, direction, "
                  "sent_by, body, created_at) VALUES (?,?,?,?,?,?,?,?)",
                  (f"id-{z}", SP, z, f"m-{z}", "in", "x@example.com", said, at))
        if z in ("z-len", "z/odd id"):           # answered once before, so not writing for the first time
            c.execute("INSERT INTO inbox_messages (id, space, zernio_conversation_id, zernio_message_id, direction, "
                      "sent_by, body, created_at) VALUES (?,?,?,?,?,?,?,?)",
                      (f"out-{z}", SP, z, f"o-{z}", "out", "human", "Thanks!",
                       (now - timedelta(days=20)).isoformat()))

print("test_the_inbox_names_who_to_answer_first")
rep = cv_report.report(date.today(), SP)
first = rep.get("answer_first") or []
# SPEED TO LEAD (owner, 2026-10-06): the ad lead asking a price, then the first-time writer who wants to book, then
# the returning customer asking about the 20% off two hours ago; Priya moving her own booking waits behind them.
ok("three people, by speed to lead; a month-old thread is never named",
   [f["text"] for f in first] == ["Marcus Cole", "Dana Whitfield", "Len Okafor"], first)
ok("each says how long, why them, and the first words of what they asked",
   first[0]["why"].startswith("Waiting 5h · asking about price · from your ad · first message: “Saw your spring")
   and first[0]["why"].endswith("…”") and first[1]["why"] == "Waiting 2d · wants to book · first message: “Do you "
   "have any Saturday openings for a facial?”", [f["why"] for f in first])
ok("each links to its conversation", first[1]["href"] == "/inbox/inbox/z-dana", first)
ok("an id with odd characters is escaped in its link",
   cv_report.answer_first([{"participant": "R", "zernio_conversation_id": "z/odd id",
                            "last_inbound_at": now.isoformat()}], "")[0]["href"] == "/inbox/inbox/z%2Fodd%20id")
ok("the shared report shape keeps it, three at most",
   len(report._normalize("x", "X", {"answer_first": [{"text": str(i)} for i in range(5)]})["answer_first"]) == 3)

print("\ntest_the_review_shows_it_on_the_page_and_in_the_email")
T = date.today()
ABOUT = T - timedelta(days=1)
NOW = datetime.combine(T, datetime.min.time()).replace(hour=8)
with state.connect() as c:
    c.execute("INSERT OR REPLACE INTO daily_reports (day, machine, report_json, written_at, final) VALUES (?,?,?,?,1)",
              (T.isoformat(), "customer_voice", json.dumps(report._normalize("customer_voice", "Unified Inbox", rep)),
               state._now()))
b = review_brief.ensure(ABOUT, NOW)
ok("the brief carries the three, in order", [f["title"] for f in b["first"]] == ["Marcus Cole", "Dana Whitfield",
                                                                                "Len Okafor"], b.get("first"))
html, _ = page.render(ABOUT.isoformat(), NOW)
e = review_email.build(ABOUT, NOW)
mail, text = review_email.html(e), review_email.text(e)
ok("on the page, with the link", "Who to answer first" in html and "Dana Whitfield" in html
   and "/inbox/inbox/z-dana" in html, html[-2000:])
ok("in both emails", "WHO TO ANSWER FIRST" in text and "Marcus Cole" in text
   and "Who to answer first" in mail and "z-dana" in mail, text)
ok("right below the quote, before anything else (owner, 2026-10-06)", "WHO TO ANSWER FIRST" in text
   and text.index(b["quote"]) < text.index("WHO TO ANSWER FIRST")
   and all(text.index("WHO TO ANSWER FIRST") < text.index(h) for h in ("WORTH YOUR TIME", "ALREADY MOVING")
           if h in text), text[:600])

print("\ntest_never_on_slack")
slack = report.render(ABOUT, NOW)
ok("no customer's name or words on Slack, and no heading for them (OSDev1)",
   "Who to answer first" not in slack and not any(n in slack for n in ("Marcus Cole", "Dana Whitfield", "Saturday")),
   slack)

print("\ntest_no_names_no_section")
with state.connect() as c:
    c.execute("DELETE FROM daily_reports")
quiet = review_brief.build(ABOUT - timedelta(days=3), NOW - timedelta(days=3), remember=False)
ok("a morning whose reporters name nobody has no such section", quiet["first"] == [], quiet.get("first"))

print("\nALL ANSWER-FIRST CHECKS PASS" if not _failed else f"\n{_failed} ANSWER-FIRST CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
