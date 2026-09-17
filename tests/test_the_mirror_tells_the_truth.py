"""The inbox mirror records BOTH directions, and records each message exactly once.

THE BUG THIS SUITE EXISTS FOR, measured on the live box by OSDev1 (2026-09-17). `inbox_messages`
held 72 rows and every one said "in", because `poller._sweep_channel` recorded ONE message per
sweep — the target — with `direction="in"` hardcoded. `store.awaiting_reply` calls a conversation
waiting when its newest message is inbound, so all 72 counted as waiting, including threads the
owner had already answered inside Instagram. The 8 AM notifier (#1293) would have opened with
"72 people are waiting" when two had an inbound that week.

A NOTIFICATION THAT WRONG IS WORSE THAN NO NOTIFICATION: it is read once, trusted once, and never
again. The count is the one number the product is for.

AND THE KEY THE MIRROR LEANS ON WAS NOT A KEY. `zernio_message_id` is UNIQUE, which breaks in both
directions when it is falsy, and both were live:
  · None  — NULLs are DISTINCT in SQLite, so the same message re-mirrored on every poll
            (measured by OSDev5: three polls, three rows).
  · ""    — the opposite. `_header(msg, "Message-ID")` returns "" for a mail with no such header,
            and "" is not null, so the FIRST header-less email claims the key and every later one
            from anybody is dropped in silence.

Run: python tests/test_the_mirror_tells_the_truth.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "mirror.db")
os.environ.pop("ZERNIO_API_KEY", None)

from core import state  # noqa: E402

state.init_db()

from marketing.customer_voice.inbox import channels, poller, store  # noqa: E402

FAILS = []
SPACE = "acme"
CH = next(c for c in channels.POLLED if c.key in ("instagram", "messenger"))
SP = {"name": SPACE, "key": "k"}


def ok(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        FAILS.append(label)


def rows(zcid):
    with state.connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT direction, sent_by, body, zernio_message_id FROM inbox_messages "
            "WHERE space = ? AND zernio_conversation_id = ? ORDER BY created_at, rowid",
            (SPACE, zcid)).fetchall()]


def sweep(zcid, msgs, activity):
    """Drive the REAL _sweep_channel, like test_dead_watermarks_release does."""
    class _Inbox:
        def messages(self, zcid_, acctid, limit=None):
            return {"messages": msgs}

    class _Z:
        inbox = _Inbox()

    page = {"conversations": [{"id": zcid, "accountId": "acc-x",
                               "updatedTime": activity, "participant": "Priya"}]}
    return poller._sweep_channel(SP, _Z(), CH, page)


def msg(mid, way, body, at):
    """The vendor's real shape (#1287): direction incoming/outgoing, sentAt, no fromMe."""
    return {"_id": mid, "accountId": "acc-x", "direction": way,
            "message": body, "sentAt": at}


# ── 1. the live bug, and it is gone ──────────────────────────────────────────────────────
print("\ntest_a_thread_the_owner_answered_on_his_phone_is_not_waiting")

Z1 = "conv-answered"
sweep(Z1, [msg("m1", "incoming", "Are you open Sunday?", "2026-09-16T22:09:00Z"),
           msg("m2", "outgoing", "Yes, 9 to 4.", "2026-09-16T22:40:00Z")],
      "2026-09-16T22:41:00Z")
got = rows(Z1)
ok("BOTH directions are mirrored, not just the inbound one",
   [r["direction"] for r in got] == ["in", "out"], str([r["direction"] for r in got]))
ok("...and the reply is attributed to a person, not to the machine",
   [r["sent_by"] for r in got] == ["contact", "human"], str([r["sent_by"] for r in got]))
ok("THE THREAD IS NOT WAITING — the bug that would have said '72 people are waiting'",
   store.awaiting_reply(SPACE) == 0, f"awaiting_reply={store.awaiting_reply(SPACE)}")

Z2 = "conv-really-waiting"
sweep(Z2, [msg("m3", "outgoing", "Thanks for calling.", "2026-09-16T10:00:00Z"),
           msg("m4", "incoming", "One more question", "2026-09-16T11:00:00Z")],
      "2026-09-16T11:01:00Z")
ok("...and a thread whose NEWEST message is the customer's still counts",
   store.awaiting_reply(SPACE) == 1, f"awaiting_reply={store.awaiting_reply(SPACE)}")


# ── 2. re-polling writes nothing twice ───────────────────────────────────────────────────
print("\ntest_the_same_page_polled_again_adds_no_rows")

before = len(rows(Z1))
sweep(Z1, [msg("m1", "incoming", "Are you open Sunday?", "2026-09-16T22:09:00Z"),
           msg("m2", "outgoing", "Yes, 9 to 4.", "2026-09-16T22:40:00Z")],
      "2026-09-16T22:59:00Z")          # new activity stamp, same messages
ok("a second sweep of the same messages mirrors nothing again",
   len(rows(Z1)) == before, f"{before} -> {len(rows(Z1))}")


# ── 3. an unreadable direction is skipped, never guessed ─────────────────────────────────
print("\ntest_a_message_we_cannot_read_is_not_quietly_called_answered")

# THE WORST GUESS IS "out": it makes a thread look ANSWERED while a real person waits, and the
# box drops them in silence. `_direction_of` returns None instead and the caller records nothing.
ok("an unreadable message classifies as None, not as outbound",
   poller._direction_of({"_id": "x", "message": "hi"}) is None,
   str(poller._direction_of({"_id": "x", "message": "hi"})))
ok("...while the vendor's real words still classify",
   (poller._direction_of(msg("a", "incoming", "b", "c")),
    poller._direction_of(msg("a", "outgoing", "b", "c"))) == ("in", "out"))
ok("...and the older fromMe shape still works in BOTH directions",
   (poller._direction_of({"fromMe": True}), poller._direction_of({"fromMe": False}))
   == ("out", "in"))

# "outgoing" CONTAINS "in" — matched by equality, never substring. The two-way classifier says so
# in a comment; the three-way one must obey it too or every reply becomes a customer message.
ok("'outgoing' is not read as inbound by a substring match",
   poller._direction_of(msg("a", "outgoing", "b", "c")) == "out")

Z3 = "conv-unreadable"
sweep(Z3, [msg("m5", "incoming", "Hello?", "2026-09-16T09:00:00Z"),
           {"_id": "m6", "accountId": "acc-x", "message": "???", "sentAt": "2026-09-16T09:30:00Z"}],
      "2026-09-16T09:31:00Z")
ok("the unreadable message is not mirrored at all",
   [r["zernio_message_id"] for r in rows(Z3)] == ["m5"], str(rows(Z3)))
ok("...so the customer still reads as waiting rather than silently answered",
   store.awaiting_reply(SPACE) == 2, f"awaiting_reply={store.awaiting_reply(SPACE)}")


# ── 4. the key the mirror leans on is a real key ─────────────────────────────────────────
print("\ntest_a_message_with_no_vendor_id_is_still_stored_exactly_once")

# OSDev5 measured the NULL half: same message, three polls, three rows. Inverted here.
for _ in range(3):
    store.record_message(space=SPACE, zcid="conv-noid", zmid=None, direction="in",
                         sent_by="contact", body="no id on me", sent_at="2026-09-16T12:00:00Z")
ok("the same id-less message recorded three times makes ONE row",
   len(rows("conv-noid")) == 1, f"{len(rows('conv-noid'))} rows")
ok("...and an empty string is treated the same as None, not as a key",
   (store.record_message(space=SPACE, zcid="conv-noid", zmid="", direction="in",
                         sent_by="contact", body="no id on me",
                         sent_at="2026-09-16T12:00:00Z"),
    len(rows("conv-noid")))[1] == 1, f"{len(rows('conv-noid'))} rows")

# THE OTHER HALF, AND IT IS THE ONE THAT LOSES A CUSTOMER'S MESSAGE. Two different header-less
# mails both used to hash to "" — the first claimed the key and the second vanished.
store.record_message(space=SPACE, zcid="conv-mail", zmid="", direction="in",
                     sent_by="a@x.com", body="First enquiry", sent_at="2026-09-16T08:00:00Z")
store.record_message(space=SPACE, zcid="conv-mail", zmid="", direction="in",
                     sent_by="b@y.com", body="A different person entirely",
                     sent_at="2026-09-16T08:05:00Z")
ok("TWO DIFFERENT id-less messages are TWO rows — neither is dropped in silence",
   len(rows("conv-mail")) == 2, str(rows("conv-mail")))

# The same words twice in one thread are two messages, and only the clock separates them.
store.record_message(space=SPACE, zcid="conv-twice", zmid=None, direction="in",
                     sent_by="contact", body="yes", sent_at="2026-09-16T08:00:00Z")
store.record_message(space=SPACE, zcid="conv-twice", zmid=None, direction="in",
                     sent_by="contact", body="yes", sent_at="2026-09-16T08:09:00Z")
ok('a customer who says "yes" twice has said it twice',
   len(rows("conv-twice")) == 2, str(rows("conv-twice")))

# A REAL VENDOR ID STILL WINS, so nothing about this changes the normal path.
store.record_message(space=SPACE, zcid="conv-real", zmid="m-real", direction="in",
                     sent_by="contact", body="one", sent_at="2026-09-16T08:00:00Z")
store.record_message(space=SPACE, zcid="conv-real", zmid="m-real", direction="in",
                     sent_by="contact", body="one", sent_at="2026-09-16T09:99:99Z")
ok("a real vendor id is still the key, and still exactly once",
   [r["zernio_message_id"] for r in rows("conv-real")] == ["m-real"], str(rows("conv-real")))


# ── 5. the machine's own words are never relabelled as the owner's ───────────────────────
print("\ntest_the_box_does_not_take_credit_for_what_the_owner_wrote_or_the_reverse")

# The box mirrors its own sends as sent_by="ai" at send time. When the poller later sees that
# same message on the page it must not overwrite the attribution — INSERT OR IGNORE keeps the
# first writer, and the first writer knew who sent it.
store.record_message(space=SPACE, zcid="conv-ai", zmid="m-ai", direction="out",
                     sent_by="ai", body="Thanks for reaching out!", sent_at="2026-09-16T07:00:00Z")
sweep("conv-ai", [msg("m-in", "incoming", "hi", "2026-09-16T06:00:00Z"),
                  msg("m-ai", "outgoing", "Thanks for reaching out!", "2026-09-16T07:00:00Z")],
      "2026-09-16T07:01:00Z")
ok("a message the BOX sent keeps sent_by='ai' after the poller sees it on the page",
   [r["sent_by"] for r in rows("conv-ai") if r["zernio_message_id"] == "m-ai"] == ["ai"],
   str(rows("conv-ai")))


print("\n— and this file cannot silently fall out of CI —")
import pathlib  # noqa: E402

_wf = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml"
if _wf.is_file():
    ok("test_the_mirror_tells_the_truth is in the workflow's suite list",
       "test_the_mirror_tells_the_truth" in _wf.read_text())
else:
    print("  --   no workflow file here (a box, not the repo) — nothing to check")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:")
    [print("   -", f) for f in FAILS]
    sys.exit(1)
print("ALL OK")
