"""The Morning Review on Slack is the stored brief: the email's words, short, and never the same nag twice.

OSDev1, 2026-10-02, assigning it: the Slack message still sent the old text, "Needs you" / "Yesterday" / "Watch"
with "!!" markers, and repeated every standing item every morning. The owner's ruling for the review: *"Light and
optimistic ... if they're stale information in there that they can't instantly change then we don't continue to
harass and annoy them every day."* So core/report.render reads review_brief.ensure, the brief the email reads:

  1. the date, the day's quote, the good news, then Worth your time today, Already moving and Advice for today;
  2. every item the email lists, in the email's words, and nothing of the old shape: no "!!", no "Needs you",
     no "Watch", no "Yesterday," header;
  3. a standing count is said once: the next morning, unchanged, it is not in the message again;
  4. a morning with nothing to say sends nothing, as the email does;
  5. what was typed into the box reads as words, never as Slack markup.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta

_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "slack.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import state  # noqa: E402

state.init_db()
from core import box_settings, report, review_brief, review_email  # noqa: E402
from core.config import settings  # noqa: E402

settings.dashboard_base_url = "https://box.example"     # a box knows its own address; Slack needs a whole one

_failed = 0


def ok(label: str, cond: bool, detail: str = "") -> None:
    global _failed
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  — {str(detail)[:600]}"))
    if not cond:
        _failed += 1


def wipe() -> None:
    with state.connect() as c:
        c.execute("DELETE FROM daily_reports")
    box_settings.put(review_brief.NS, review_brief.SEEN, {}, set_by="test")
    box_settings.put(review_brief.NS, review_brief.IDEAS_SEEN, {}, set_by="test")


def row(day: date, machine: str, title: str, **parts) -> None:
    with state.connect() as c:
        c.execute("INSERT OR REPLACE INTO daily_reports (day, machine, report_json, written_at, final) "
                  "VALUES (?,?,?,?,1)", (day.isoformat(), machine,
                                         json.dumps({"machine": machine, "title": title, **parts}), state._now()))


ABOUT, T = date(2026, 9, 30), date(2026, 10, 1)
NOW = datetime(2026, 10, 1, 8, 0)

print("\ntest_it_is_the_brief_in_the_emails_words")
wipe()
row(ABOUT, "lead_machine", "Lead Machine", happened=[{"text": "companies found", "value": 7}])
row(T, "inbox", "Unified Inbox", needs_you=[{"text": "2 people would love a reply", "href": "/inbox/inbox"}],
    watch=[{"text": "Mailbox sign-in failing", "state": "fail", "href": "/inbox/settings"},
           {"text": "Sending domain not warmed", "state": "warn"}])
msg = report.render(ABOUT, NOW)
b = review_brief.get(ABOUT)
ok("the message is built from the brief, which is stored once for the morning", b is not None)
ok("it opens on the date and the day's quote", msg.startswith(f"*{b['date_label']}*\n> _{b['quote']}_"), msg[:200])
ok("then Worth your time today, then Already moving",
   0 < msg.index("*Worth your time today*") < msg.index("*Already moving*"), msg)
email = review_email.text(review_email.build(ABOUT, NOW))
titles = [it["title"] for k in ("worth", "moving", "ideas") for it in b.get(k) or []]
ok("every item the email lists is in it, in the email's words",
   titles and all(t in email for t in titles) and all(t in msg for t in titles), (titles, msg))
ok("a link goes to the page on the box, by its whole address", "<https://box.example/inbox/inbox|" in msg, msg)
ok("...and the last line is the full review", msg.rstrip().splitlines()[-1].endswith("|Open the full review>"), msg)
import re  # noqa: E402

ok("NOTHING OF THE OLD SHAPE: no '!!', no 'Needs you', no 'Watch', no 'Yesterday, Wed 30 Sep' header",
   "!!" not in msg and "Needs you" not in msg and "\nWatch" not in msg
   and not re.search(r"^Yesterday, \w{3} \d{2} \w{3}$", msg, re.M), msg)
ok("a warning is a standing nag, so it is not in it; a failing check is",
   "Mailbox sign-in failing" in msg and "Sending domain not warmed" not in msg, msg)

print("\ntest_never_the_same_nag_twice")
wipe()
row(T, "reel", "Reels", needs_you=[{"text": "152 scripts waiting for a look", "href": "/reel"}])
first = report.render(ABOUT, NOW)
ok("first sight: the standing count is in the morning's message", "152 scripts waiting" in first, first)
row(T + timedelta(days=1), "reel", "Reels", needs_you=[{"text": "153 scripts waiting for a look", "href": "/reel"}])
second = report.render(T, NOW + timedelta(days=1))
ok("the next morning, unchanged in all but one, it is not said again", "scripts waiting" not in second, second)

print("\ntest_a_quiet_morning_sends_nothing")
wipe()
ok("nothing happened and nothing waits: no message, as no email", report.render(ABOUT, NOW) == "")
# THROUGH run(): its once-a-day marker in a temp file, no reporter to write today's rows, the owner's Slack only.
import pathlib  # noqa: E402

sent, saved = [], dict(report.REPORTERS)
orig_cfg, orig_path = report._cfg, report._state_path
report._cfg = lambda: {"enabled": True, "hour_local": 8, "email_to": ""}
report._state_path = lambda: pathlib.Path(_T) / "review.json"
report._owner_email = lambda *a, **k: ""
report.REPORTERS.clear()
settings.operator_slack_user_id = "U_OWNER"
try:
    morning = datetime(2026, 10, 1, 9, 0, tzinfo=report.tz())
    r = report.run(morning, lambda t: (sent.append(t) or True))
    ok("run sends no DM on a quiet morning, and counts the morning done",
       not sent and r.get("dm") == "quiet" and r.get("status") == "sent", str(r))
    ok("...so the next tick does not try again", report.run(morning + timedelta(hours=1),
                                                              lambda t: (sent.append(t) or True))["status"] == "quiet"
       and not sent)
finally:
    report._cfg, report._state_path = orig_cfg, orig_path
    report.REPORTERS.update(saved)

print("\ntest_what_was_typed_reads_as_words")
wipe()
row(T, "inbox", "Unified Inbox", needs_you=[{"text": "<!channel> & <https://evil.example|click> waiting",
                                             "href": "/inbox/inbox"}])
msg = report.render(ABOUT, NOW)
ok("Slack's control characters are escaped, so nothing typed can ping the channel or plant a link",
   "<!channel>" not in msg and "<https://evil.example" not in msg and "&lt;!channel&gt; &amp;" in msg, msg)

print("\nall good" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
