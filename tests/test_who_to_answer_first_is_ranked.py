"""Who to answer first: ranked by speed to lead, right below the quote, as cards (owner, 2026-10-06).

Owner, 2026-10-06, after the Morning Review research: "Let's keep the daily quote at the top, they are good so far.
Let's move who to answer first right below the quote and let's use some intelligence and inference to make that a good
section. You're right that's gonna be the most valuable thing for business owners, speed to lead is where the money is
at. So put like two or three people on that list. And then let's just polish the dashboard and polish the email."

THE INFERENCE is plain patterns on the words the box already holds (marketing/customer_voice/report.py `wants`), never
a model: what they ask for (help soon, a booking, a price), an ad that brought them, a first message, writing again
while unanswered, and how fresh it is. Who is left out (the box itself, pitches, robots) is
tests/test_who_to_answer_first_is_a_person.py.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * a leak this morning, a booking or a price question loses its place to a "thanks!" or a stale hello;
  * moving one's own booking reads as "wants to book";
  * more than three are named, or the count of everyone waiting is wrong;
  * a reply the box wrote is not offered as ready, or one the box judged needs no answer is;
  * the section is not right below the quote on the page and in both emails, or yesterday's good news and numbers
    don't follow it, or a hidden section takes them with it;
  * "N waiting on your reply" is said twice, in this section and in Worth your time;
  * a card has no 48px button to the thread at the foot, or the page is not mobile first;
  * a customer's name or words reach Slack or the review's AI.

No network, no model: the review's AI is a function handed in.

Run: python tests/test_who_to_answer_first_is_ranked.py
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "ranked.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state  # noqa: E402

state.init_db()
from core import report, review_brief, review_email  # noqa: E402
from core.dash import review as page  # noqa: E402
from core.dispatch import app  # noqa: E402,F401  (applies every machine's schema)
from marketing.customer_voice import report as cv_report  # noqa: E402
from marketing.customer_voice.drafter import store as drafts  # noqa: E402
from marketing.customer_voice.inbox import store  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -- {str(detail)[:500]}" if not cond and detail else ""))
    if not cond:
        _failed += 1


print("test_what_they_want")
for said, first_reason in (("Our water heater is leaking everywhere, can you come out?", "Needs help soon"),
                           ("Do you have any Saturday openings for a facial?", "Wants to book"),
                           ("How much for a full set?", "Asking about price"),
                           ("Is the 20% off still running?", "Asking about price"),
                           ("Can I reschedule my appointment to Friday?", "About their booking")):
    got = [c for c, _, _ in cv_report.wants(said)]
    ok(f"{said!r}: {first_reason}", got[:1] == [first_reason], got)
ok("moving one's own booking never reads as wanting to book",
   "Wants to book" not in [c for c, _, _ in cv_report.wants("Can I move my appointment?")])
ok("a thank-you asks for nothing", cv_report.wants("Thanks so much, see you then!") == [])

now = datetime.now(timezone.utc)
SP = cv_report._space_name()


def thread(z, who, hours, said, *, ad=False, answered=False, platform="email", again=""):
    at = (now - timedelta(hours=hours)).isoformat()
    store.upsert_conversation(space=SP, zcid=z, platform=platform, participant=who, account_id="me@example.com",
                              last_inbound_at=at, ad_meta_id="ad-1" if ad else None)
    with state.connect() as c:
        if answered:
            c.execute("INSERT INTO inbox_messages (id, space, zernio_conversation_id, zernio_message_id, direction, "
                      "sent_by, body, created_at) VALUES (?,?,?,?,?,?,?,?)",
                      (f"o-{z}", SP, z, f"om-{z}", "out", "human", "Happy to help.",
                       (now - timedelta(days=20)).isoformat()))
        if again:
            c.execute("INSERT INTO inbox_messages (id, space, zernio_conversation_id, zernio_message_id, direction, "
                      "sent_by, body, created_at) VALUES (?,?,?,?,?,?,?,?)",
                      (f"a-{z}", SP, z, f"am-{z}", "in", "x@example.com", again,
                       (now - timedelta(hours=hours + 2)).isoformat()))
        c.execute("INSERT INTO inbox_messages (id, space, zernio_conversation_id, zernio_message_id, direction, "
                  "sent_by, body, created_at) VALUES (?,?,?,?,?,?,?,?)",
                  (f"i-{z}", SP, z, f"m-{z}", "in", "x@example.com", said, at))
    return f"m-{z}"


thread("z-thanks", "Tom Avery", 0.5, "Thanks so much, see you then!", answered=True)
thread("z-leak", "Rosa Diaz", 3, "Our water heater is leaking everywhere, can you come out?")
m_book = thread("z-book", "Dana Whitfield", 20, "Do you have anything open Saturday?", platform="instagram")
thread("z-ad", "Marcus Cole", 50, "Saw your ad, how much is it?", ad=True)
thread("z-again", "Priya Raman", 30, "Hello? Anyone there?", answered=True, again="Hello")
drafts.put(space=SP, zcid="z-book", in_reply_to=m_book, body="Hi Dana! Saturday at 10 or 2 works. Which suits?")

print("\ntest_ranked_by_speed_to_lead")
rep = cv_report.report(date.today(), SP)
first = rep.get("answer_first") or []
ok("three people: the leak this morning, the ad lead asking a price, the booking; never the thank-you or the hello",
   [f["text"] for f in first] == ["Rosa Diaz", "Marcus Cole", "Dana Whitfield"], [f["text"] for f in first])
by = {f["text"]: f for f in first}
ok("each says why, strongest first, three at most",
   by["Rosa Diaz"]["reasons"] == ["Needs help soon", "Wants to book", "New"]
   and by["Marcus Cole"]["reasons"] == ["Asking about price", "From your ad", "New"], [f["reasons"] for f in first])
ok("...how long they have waited, and on which channel",
   by["Rosa Diaz"]["waited"] == "3h" and by["Dana Whitfield"]["channel"] == "Instagram"
   and by["Rosa Diaz"]["channel"] == "Email", first)
ok("...their own words, whole when short", by["Dana Whitfield"]["said"] == "Do you have anything open Saturday?")
ok("a reply the box already wrote is offered as ready; none is pretended", by["Dana Whitfield"]["ready"] is True
   and by["Rosa Diaz"]["ready"] is False)
ok("everyone waiting is counted, so the review can say how many", all(f["of"] == 5 for f in first), first)
alone = cv_report.answer_first(cv_report.real_people(SP, [r for r in store.list_conversations(SP, waiting=True)
                                                          if r["zernio_conversation_id"] == "z-again"]), "")
ok("writing again while unanswered counts, said as a person says it", alone and alone[0]["reasons"] == ["Wrote twice"],
   alone)
fresh = {"zernio_conversation_id": "z-f", "participant": "Fresh", "preview": "Do you have Saturday?", "first_time": True,
         "last_inbound_at": (now - timedelta(hours=1)).isoformat()}
stale = {**fresh, "zernio_conversation_id": "z-s", "participant": "Stale", "ad_meta_id": "ad-1",
         "last_inbound_at": (now - timedelta(days=10)).isoformat()}
ok("speed to lead: an hour-old booking beats the same ask from an ad ten days ago",
   [f["text"] for f in cv_report.answer_first([stale, fresh], "")] == ["Fresh", "Stale"])
ok("a reply that is no reply (the box judged it needs none) is never offered as ready",
   drafts.put(space=SP, zcid="z-thanks", in_reply_to="m-z-thanks", body=drafts.NO_REPLY_BODY)
   and "z-thanks" not in {r["zernio_conversation_id"] for r in cv_report.real_people(
       SP, store.list_conversations(SP, waiting=True))})

print("\ntest_right_below_the_quote")
T = date.today()
ABOUT = T - timedelta(days=1)
NOW = datetime.combine(T, datetime.min.time()).replace(hour=8)
with state.connect() as c:
    c.execute("INSERT OR REPLACE INTO daily_reports (day, machine, report_json, written_at, final) VALUES (?,?,?,?,1)",
              (T.isoformat(), "customer_voice", json.dumps(report._normalize("customer_voice", "Unified Inbox", rep)),
               state._now()))
    c.execute("INSERT OR REPLACE INTO daily_reports (day, machine, report_json, written_at, final) VALUES (?,?,?,?,1)",
              (ABOUT.isoformat(), "reels", json.dumps(report._normalize("reels", "Reels", {
                  "headline": {"value": 4, "label": "reels published today"},
                  "happened": [{"text": "reels published", "value": 4}]})), state._now()))
seen = []


def think(task, prompt, **kw):
    seen.append(prompt)
    return json.dumps({"good_news": "4 reels went out yesterday.", "advice": []})


b = review_brief.ensure(ABOUT, NOW, think=think)          # stored once, as the send does: the emails read it
ok("the brief carries the three with their cards' parts",
   [f["title"] for f in b["first"]] == ["Rosa Diaz", "Marcus Cole", "Dana Whitfield"]
   and b["first"][2]["ready"] is True and b["first"][0]["reasons"][0] == "Needs help soon", b["first"])
ok("'N waiting on your reply' is said once, here, and not again in Worth your time",
   not any("waiting on your reply" in w["title"] for w in b["worth"]), b["worth"])
ok("yesterday's numbers say yesterday, never 'today'", b["numbers"] and b["numbers"][0]["label"] == "reels published",
   b["numbers"])
ok("the review's AI never reads a customer's name or words",
   seen and not any(w in seen[0] for w in ("Rosa", "Marcus", "Dana", "water heater", "Saturday?")), seen[:1])

html = page.brief_html({**b, "worth": [{"title": "1 topic waiting for your OK", "why": "", "href": "/aeo",
                                        "machine": "AEO"}]}, live=True)
at = {k: html.find(k) for k in ("mr-quote", "Who to answer first", "4 reels went out yesterday", "Worth your time today")}
ok("the page: the quote, then who to answer first, then yesterday's good news, then the rest",
   -1 not in at.values() and at["mr-quote"] < at["Who to answer first"] < at["4 reels went out yesterday"]
   < at["Worth your time today"], at)
ok("...says how many wait, and why to start here",
   "5 people are waiting on a reply. Start with these three: fast replies win customers." in html)
ok("...each card ends in one button to the thread: the ready reply, or a reply by first name",
   'class="mr-p-go is-ready" href="/inbox/inbox/z-book">See the reply ready to send</a>' in html
   and 'href="/inbox/inbox/z-leak">Reply to Rosa</a>' in html, re.findall(r'class="mr-p-go[^>]*>[^<]*', html))
ok("...with the reasons as tags and their words in quotes",
   "<li>Needs help soon</li>" in html and "&ldquo;Do you have anything open Saturday?&rdquo;" in html)
css = page.BRIEF_CSS
ok("mobile first: a full-width 48px button, narrowed only once the screen is wide, never a max-width rule",
   re.search(r"\.mr-p-go\{display:flex;[^}]*min-height:var\(--tap,48px\)", css)
   and re.search(r"@media \(min-width:720px\)\{[^@]*\.mr-p-go\{display:inline-flex\}", css)
   and "@media (max-width" not in css)
hid = page.brief_html(b, live=True, hidden={"first"}, back="/app/review")
ok("hiding the section leaves yesterday's good news in place",
   "Who to answer first" not in hid and "4 reels went out yesterday" in hid)
old = page.brief_html({**b, "first": [{"title": "Sam Lee", "why": "Waiting 2d: “Quote for a deck?”",
                                       "href": "/inbox/inbox/z-sam", "machine": "Unified Inbox"}]}, live=True)
ok("a review stored before the cards still draws its people", "Sam Lee" in old and "Quote for a deck?" in old
   and 'href="/inbox/inbox/z-sam">Reply to Sam</a>' in old)

e = review_email.build(ABOUT, NOW)
mail, text = review_email.html(e), review_email.text(e)
ok("the HTML email: the quote, then who to answer first, then the good news",
   mail.index(e["quote"]) < mail.index("Who to answer first") < mail.index("4 reels went out yesterday"))
ok("...a light card each, ending in a full-width button to the thread (never a black one)",
   "See the reply ready to send</a>" in mail and "Reply to Rosa</a>" in mail
   and "/inbox/inbox/z-leak" in mail and "background:#000" not in mail and "background:#111" not in mail)
ok("the plain email: the same order, each person with why, their words and a link",
   text.index(e["quote"]) < text.index("WHO TO ANSWER FIRST") < text.index("4 reels went out yesterday")
   and "  01  Rosa Diaz · Email · waiting 3h" in text and "      Needs help soon · Wants to book · New" in text
   # THE LINK IS THE REVIEW'S OWN ADDRESS'S BASE: absolute on a box with a public address, a path on one without
   # (CI has none, so a check that wanted "https://" went red there and green on a configured box).
   and f"Reply to Rosa: {review_email._href('/inbox/inbox/z-leak', e['link'].split('/app/review')[0])}" in text,
   text[:900])

slack = report.render(ABOUT, NOW)
ok("never on Slack: no names, no words, no heading", "Who to answer first" not in slack
   and not any(w in slack for w in ("Rosa", "Marcus", "Dana", "water heater")), slack)

print("\nALL RANKED CHECKS PASS" if not _failed else f"\n{_failed} RANKED CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
