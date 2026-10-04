"""The Morning Review reads the same on the box, in the email and on Slack, and carries yesterday's numbers.

Owner, 2026-10-04, ahead of an investor demo: "make sure that it's pulling all the best data and displaying it on
the box and in the email equally." Measured on a seeded box before this change: the email named the machine under
every item and the page did not; "Already moving" read machine-first on the page and Slack and title-first in the
email; the good-news line with no AI was "Already moving" item 01 word for word; a site's name was capitalised
("Www.example.com"); and each machine's headline number (the big figure on the numbers page) reached no surface of
the brief at all. Measured here, with no network and no model:
  * the brief carries "numbers": each machine's real headline from yesterday, never a zero, never an unlabelled one
  * the page, the email (html and text) and Slack all show the same numbers
  * every "Worth your time" item names its machine on the page, as in the email
  * "Already moving" reads machine first in the email too
  * with no AI, the good news names up to three machines, never repeating item 01 word for word
  * a site's name keeps its own case

Run: python tests/test_the_review_reads_the_same_everywhere.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "same.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import dash, report, review_brief, review_email, state  # noqa: E402

state.init_db()
from core.dash import review as page  # noqa: E402
from core.dispatch import app  # noqa: E402,F401

FAILS: list[str] = []
ABOUT, NOW = date(2026, 10, 3), datetime(2026, 10, 4, 8, 0)
T = date(2026, 10, 4)


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:500]}"))
    if not cond:
        FAILS.append(label)


def row(day, machine, title, **parts):
    with state.connect() as c:
        c.execute("INSERT OR REPLACE INTO daily_reports (day, machine, report_json, written_at, final) "
                  "VALUES (?,?,?,?,1)", (day.isoformat(), machine,
                                         json.dumps({"machine": machine, "title": title, **parts}), state._now()))


row(ABOUT, "customer_voice", "Unified Inbox", headline={"value": 4, "label": "waiting on you"},
    happened=[{"text": "messages came in", "value": 15}, {"text": "replies written for you", "value": 6}])
row(ABOUT, "lead_machine", "Lead Machine", headline={"value": 10, "label": "emails sent"},
    happened=[{"text": "companies found", "value": 23}, {"text": "first emails sent", "value": 10}])
row(ABOUT, "website", "Website", headline={"value": 163, "label": "visits from people this week"},
    happened=[{"text": "www.example-medspa.com: 163 visits from people this week, up 7%"}])
row(ABOUT, "aeo_machine", "AEO Machine", headline={"value": 0, "label": ""}, happened=[])          # a quiet day
row(ABOUT, "reels", "Reels", headline={"value": 0, "label": "reels published"},
    happened=[{"text": "reels published", "value": 0}])                                        # a real zero
row(T, "customer_voice", "Unified Inbox",
    needs_you=[{"text": "4 people waiting on your reply", "href": "/inbox/waiting", "person": True}])
row(T, "lead_machine", "Lead Machine",
    needs_you=[{"text": "3 emails drafted and waiting for your approval", "href": "/lead/approvals"}])

print("\nThe brief carries yesterday's numbers\n")
b = review_brief.ensure(ABOUT, NOW)
nums = b.get("numbers") or []
ok("each machine's real headline, in report order", [(n["machine"], n["value"], n["label"]) for n in nums] == [
   ("Unified Inbox", 4, "waiting on you"), ("Lead Machine", 10, "emails sent"),
   ("Website", 163, "visits from people this week")], nums)
ok("...never a zero, never one with no label", not any(n["value"] == 0 for n in nums)
   and all(n["label"] for n in nums))
ok("stored once: the page reads the same numbers", review_brief.get(ABOUT)["numbers"] == nums)

print("\nWith no AI, the good news\n")
g = b["good_news"]
ok("names up to three machines, one phrase each", g == "Yesterday, Unified Inbox: 15 messages came in; Lead Machine: "
   "23 companies found; Website: www.example-medspa.com: 163 visits from people this week.", g)
ok("...never Already moving item 01 word for word", g != f"Yesterday, {b['moving'][0]['machine']}: {b['moving'][0]['title']}.")
ok("a site's name keeps its own case", any(m["title"].startswith("www.example-medspa.com") for m in b["moving"]),
   [m["title"] for m in b["moving"]])
ok("one machine alone still reads as it always did",
   review_brief._plain_good_news([{"machine": "Reels", "title": "4 reels published"}])
   == "Yesterday, Reels: 4 reels published.")

print("\nThe same numbers on every surface\n")
html, _ = page.render(ABOUT.isoformat(), NOW)
e = review_email.build(ABOUT, NOW)
mail_html, mail_text = review_email.html(e), review_email.text(e)
slack = report.render(ABOUT, NOW)
for n in nums:
    v, lab = report._fmt_value(n["value"]), n["label"]
    ok(f"{n['machine']}: {v} {lab} on the page, in both emails and on Slack",
       f"<b>{v}</b><small>{lab}</small>" in html and f">{v}</div>" in mail_html and lab in mail_html
       and f"{v} {lab} ({n['machine']})" in mail_text and f"{v} {lab} ({n['machine']})" in slack,
       (html.count("mr-num"), mail_text[:400]))
ok("under one heading, on the page and in the email", "By the numbers" in html
   and "By the numbers" in mail_html and "By the numbers: " in mail_text)
ok("the email puts two to a row, never four across a mobile", mail_html.count('width:50%') == len(nums)
   and mail_html.count("<tr><td style=\"padding:14px 16px 0 0") == 2, mail_html.count("width:50%"))

print("\nThe machine is named the same way everywhere\n")
ok("every Worth your time item names its machine on the page, as in the email",
   '<span class="mr-m">Unified Inbox</span>' in html and '<span class="mr-m">Lead Machine</span>' in html, html[-3000:])
moving = mail_text.split("ALREADY MOVING", 1)[1].split("\n\n", 1)[0]
ok("Already moving reads machine first in the email text", "01  Unified Inbox\n      15 messages came in" in moving,
   moving)
mv = mail_html.split("Already moving", 1)[1]
ok("...and in the email html", mv.index('font-weight:600">Unified Inbox</div>') < mv.index("15 messages came in"),
   mv[:400])
ok("...as on Slack", "01  Unified Inbox: 15 messages came in" in slack, slack)

print("\nNothing to show, nothing drawn\n")
ok("no numbers: no heading on the page, in the email or on Slack",
   page._numbers_html([]) == "" and review_email.numbers_line({"numbers": []}) == ""
   and review_email._numbers_html({"numbers": []}) == "")
ok("a junk entry is skipped, not drawn", page._numbers_html([{"value": 0, "label": "x"}, {"value": 5, "label": ""},
                                                            "nonsense"]) == "")
EVIL = '<script>alert(1)</script>'
ok("a machine's words are escaped on the page and in the email", EVIL not in page._numbers_html(
   [{"value": 3, "label": EVIL, "machine": EVIL}]) and EVIL not in review_email._numbers_html(
   {"numbers": [{"value": 3, "label": EVIL, "machine": EVIL}]}))
c = app.test_client()
c.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
r = c.get(f"/app/review/{ABOUT.isoformat()}")
ok("the day's own page serves it", r.status_code == 200 and "By the numbers" in r.get_data(as_text=True))

print("\nOne is not plural (owner 10-04: the live review read \"1 messages came in\")\n")
one = report._normalize("x", "X", {"headline": {"value": 1, "label": "articles published"},
                                   "happened": [{"text": "messages came in", "value": 1},
                                                {"text": "people wrote for the first time", "value": 1},
                                                {"text": "replies written for you", "value": 1},
                                                {"text": "companies found", "value": 1},
                                                {"text": "first emails sent", "value": 1},
                                                {"text": "messages came in", "value": 2},
                                                {"text": "Published \u201cWhat is AEO?\u201d"}],
                                   "figures": {"d": {"value": 1, "label": "drafts ready to send"},
                                               "w": {"value": 1, "label": "waiting on you"},
                                               "o": {"value": "1d", "label": "oldest waiting"}}})
ok("a count of one reads singular, on the line, the headline and the figures",
   [h["text"] for h in one["happened"]] == ["message came in", "person wrote for the first time",
                                            "reply written for you", "company found", "first email sent",
                                            "messages came in", "Published \u201cWhat is AEO?\u201d"]
   and one["headline"]["label"] == "article published" and one["figures"]["d"]["label"] == "draft ready to send"
   and one["figures"]["w"]["label"] == "waiting on you" and one["figures"]["o"]["label"] == "oldest waiting",
   (one["happened"], one["headline"], one["figures"]))
ok("...and the brief says it so", review_brief._phrase(one["happened"][0]) == "1 message came in")
ok("words that only look plural are left alone", report._one("class booked") == "class booked"
   and report._one("analysis sent") == "analysis sent" and report._one("business hours") == "business hour"
   and report._one("status changed") == "status changed" and report._one("this week") == "this week")

print("\nThe review skips threads older than 30 days (owner 10-04)\n")
from datetime import timedelta, timezone  # noqa: E402
from marketing.customer_voice.inbox import store as inbox_store  # noqa: E402
from marketing.customer_voice import report as cv_report  # noqa: E402
SP = cv_report._space_name()
now = datetime.now(timezone.utc)
for zcid, days in (("fresh", 2), ("month", 29), ("old", 31), ("ancient", 319)):
    at = (now - timedelta(days=days)).isoformat()
    inbox_store.upsert_conversation(space=SP, zcid=zcid, platform="email", participant=zcid, last_inbound_at=at,
                                    account_id="me@example.com")
    with state.connect() as c:
        c.execute("INSERT INTO inbox_messages (id, space, zernio_conversation_id, zernio_message_id, direction, "
                  "sent_by, body, created_at) VALUES (?,?,?,?,?,?,?,?)",
                  (f"id-{zcid}", SP, zcid, f"m-{zcid}", "in", "x@example.com", "hello?", at))
ok("the inbox screen still counts every waiting thread", inbox_store.awaiting_reply(SP) == 4,
   inbox_store.awaiting_reply(SP))
ok("the review counts the two from the last 30 days", inbox_store.awaiting_reply(SP, within_days=30) == 2,
   inbox_store.awaiting_reply(SP, within_days=30))
rep = cv_report.report(date.today(), SP)
fig = rep.get("figures") or {}
ok("...says 2 waiting, and the oldest is 29 days, never 319",
   fig.get("inbox_waiting", {}).get("value") == 2 and fig.get("inbox_oldest", {}).get("value") == "29d"
   and any(n.get("text") == "2 conversations are waiting on your reply" for n in rep.get("needs_you") or []),
   (fig, rep.get("needs_you")))

print("\nALL SAME-EVERYWHERE CHECKS PASS" if not FAILS else f"\n{len(FAILS)} SAME-EVERYWHERE CHECK(S) FAILED")
sys.exit(1 if FAILS else 0)
