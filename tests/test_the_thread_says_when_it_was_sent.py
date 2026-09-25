"""The thread prints when a message was SENT, not when the box happened to read it.

Found 2026-09-24 in the launch sweep: every message in a thread showed the same minute. The
thread printed `inbox_messages.created_at`, the moment the box STORED the message, while the time
each channel hands over (Zernio's sentAt, the mail's Date header, a comment's time) was kept only
inside a dedupe key. A buyer who connects a mailbox today would see every older email stamped at
today's connect minute.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  · the send time stops being kept, or the thread stops printing it first;
  · a time with no zone, a future one, or garbage gets printed as if it were true;
  · a re-read of the same message moves it in time;
  · `created_at` starts meaning "sent": it is what the report's counts, the thread's order and
    waiting-on-reply read, and this change must not move any of them;
  · a message stored before this existed (no send-time row) stops rendering.

Run: python tests/test_the_thread_says_when_it_was_sent.py
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "sent.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state                                                   # noqa: E402

state.init_db()

from core import dash, notify, spaces                                    # noqa: E402
from marketing.customer_voice.inbox import store                         # noqa: E402
from core.dispatch import app                                            # noqa: E402

state.init_db()

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


SPACE = spaces.DEFAULT
notify.buyer_timezone = lambda: "UTC"          # print in UTC, so the expected strings are plain


def thread(zcid: str) -> str:
    c = app.test_client()
    c.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
    return c.get(f"/inbox/inbox/{zcid}").get_data(as_text=True)


def created_at(zmid: str) -> str:
    with state.connect() as c:
        return c.execute("SELECT created_at FROM inbox_messages WHERE zernio_message_id = ?",
                         (zmid,)).fetchone()["created_at"]


print("test_an_old_email_shows_the_day_it_was_sent")
store.upsert_conversation(space=SPACE, zcid="<old@x.com>", platform="email",
                          participant="Dana Wells", last_inbound_at="2026-09-20T08:01:00+00:00")
store.record_message(space=SPACE, zcid="<old@x.com>", zmid="<old@x.com>", direction="in",
                     sent_by="dana@x.com", body="Can you quote the boiler service?",
                     sent_at="2026-09-20T08:01:00+00:00")
html = thread("<old@x.com>")
ok("the thread prints the day and minute it was sent", "Sun 20 Sep, 8:01" in html,
   html[html.find("Dana"):][:200])
ok("...and created_at still says when the box stored it, for the counts",
   not created_at("<old@x.com>").startswith("2026-09-20"), created_at("<old@x.com>"))
rows = store.messages_for(SPACE, "<old@x.com>")
ok("messages_for carries sent_at", rows and rows[0].get("sent_at") == "2026-09-20T08:01:00+00:00",
   str(rows and rows[0].get("sent_at")))

print("\ntest_a_reread_never_moves_a_message")
store.record_message(space=SPACE, zcid="<old@x.com>", zmid="<old@x.com>", direction="in",
                     sent_by="dana@x.com", body="Can you quote the boiler service?",
                     sent_at="2026-09-22T09:00:00+00:00")
ok("the first send time stands", store.messages_for(SPACE, "<old@x.com>")[0]["sent_at"]
   == "2026-09-20T08:01:00+00:00")

print("\ntest_a_time_that_cannot_be_trusted_is_not_printed")
for zmid, bad, why in (("m-naive", "2026-09-20T08:01:00", "no zone"),
                       ("m-future", "2099-01-01T00:00:00+00:00", "in the future"),
                       ("m-junk", "yesterday-ish", "unreadable")):
    store.upsert_conversation(space=SPACE, zcid=zmid, platform="instagram", participant="sam")
    store.record_message(space=SPACE, zcid=zmid, zmid=zmid, direction="in", sent_by="contact",
                         body="hi", sent_at=bad)
    got = store.messages_for(SPACE, zmid)
    ok(f"{why}: no send time kept, and the message still renders from created_at",
       got and got[0].get("sent_at") is None and "hi" in thread(zmid), str(got and got[0]))
ok("epoch milliseconds from a vendor are read as UTC",
   store._sent_utc("1758362460000") == "2025-09-20T10:01:00+00:00", store._sent_utc("1758362460000"))
ok("an offset is normalised to UTC",
   store._sent_utc("2026-09-20T01:01:00-07:00") == "2026-09-20T08:01:00+00:00")

print("\ntest_a_message_stored_before_this_still_renders")
store.upsert_conversation(space=SPACE, zcid="legacy", platform="messenger", participant="lee")
store.record_message(space=SPACE, zcid="legacy", zmid="legacy-1", direction="in",
                     sent_by="contact", body="are you open saturday")
got = store.messages_for(SPACE, "legacy")
ok("no send-time row: sent_at is None and the thread renders", got and got[0].get("sent_at") is None
   and "are you open saturday" in thread("legacy"))

print("\ntest_the_email_channel_hands_over_the_date_header")
_src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "marketing", "customer_voice", "inbox", "email_channel.py")).read()
_call = _src[_src.find("store.record_message(space=space, zcid=zcid, zmid=mid"):][:600]
ok("email_channel passes sent_at=_sent_at(msg) to record_message", "sent_at=_sent_at(msg)" in _call)

print("\n— and this file cannot silently fall out of CI —")
ROOT = pathlib.Path(__file__).resolve().parents[1]
if (ROOT / ".github").is_dir():                  # a buyer's box has no repository
    ok("test_the_thread_says_when_it_was_sent is in the workflow's suite list",
       "test_the_thread_says_when_it_was_sent" in (ROOT / ".github/workflows/tests.yml").read_text())

print("\nALL OK" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
