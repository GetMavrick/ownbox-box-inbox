"""A watermark written by the broken classifier seals the fix out. This proves it is opened.

WHAT HAPPENED, measured by OSDev1 on the live box on 2026-09-17. #1287 fixed the inbound
classifier and deployed, and the box still showed zero conversations. All 91 `inbox_state`
watermarks carried `last_seen_msg_id` NULL — written when every message read as outbound, so
`_newest_inbound` returned None — and a `last_activity` equal to the thread's CURRENT activity,
which stored fine because it comes off the conversation rather than off a message.

THAT PAIR IS SELF-SEALING, and it is the whole reason this file exists. `_sweep_channel` skips a
conversation whose stored activity matches the page's: the optimisation that stops a poll
fetching messages for threads that have not moved. So the corrected classifier could not reach a
single thread that already had a watermark. The box would have shown a conversation only after
its NEXT message — on a quiet account, never.

WHY EVERY TEST WAS GREEN THROUGH ALL OF IT. A fresh database has no watermarks, so a test that
polls a new box exercises the path where the bug cannot appear. The state that breaks it only
exists on a box that ran the broken code first. That is what the migration test below builds on
purpose, and why it drives the REAL `_sweep_channel` rather than asserting on a SQL string.

Run: python tests/test_dead_watermarks_release.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "wm.db")
os.environ.pop("ZERNIO_API_KEY", None)

from core import state  # noqa: E402

state.init_db()

from marketing.customer_voice.inbox import channels, poller, store  # noqa: E402

FAILS = []
SPACE = "acme"
ZCID = "conv-live-1"
ACTIVITY = "2026-09-16T22:10:00Z"


def ok(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        FAILS.append(label)


# The vendor's real message shape (#1287): direction incoming/outgoing, sentAt, no fromMe.
MSGS = [
    {"_id": "m-1", "accountId": "acc-x", "conversationId": ZCID, "direction": "incoming",
     "senderId": "cust-1", "message": "Are you open Sunday?", "sentAt": "2026-09-16T22:09:00Z"},
]
PAGE = {"conversations": [{"id": ZCID, "accountId": "acc-x", "updatedTime": ACTIVITY,
                           "participant": "Priya"}]}


class FakeInbox:
    def __init__(self):
        self.message_calls = 0

    def messages(self, zcid, acctid, limit=None):
        self.message_calls += 1
        return {"messages": MSGS}


class FakeZ:
    def __init__(self):
        self.inbox = FakeInbox()


CH = next(c for c in channels.POLLED if c.key in ("instagram", "messenger"))
SP = {"name": SPACE, "key": "k"}


def _sweep():
    z = FakeZ()
    scanned, enqueued = poller._sweep_channel(SP, z, CH, PAGE)
    return z.inbox.message_calls, scanned, enqueued


# ── 1. reproduce the live box exactly ────────────────────────────────────────────────────
print("\ntest_the_broken_classifier_left_a_watermark_that_seals_the_fix_out")

# EXACTLY WHAT THE BROKEN POLLER WROTE: the activity stamp landed, the message id did not.
store.set_watermark(SPACE, ZCID, last_seen_msg_id=None, last_activity=ACTIVITY)
wm = store.get_watermark(SPACE, ZCID)
ok("the live box's state is reproduced: an activity stamp with no message id",
   wm["last_activity"] == ACTIVITY and wm["last_seen_msg_id"] is None, str(wm))

calls, scanned, enqueued = _sweep()
ok("THE FIXED POLLER NEVER FETCHES A MESSAGE — this is why the live box showed nothing",
   calls == 0, f"{calls} message fetches")
ok("...and records no conversation at all",
   store.get_conversation(SPACE, ZCID) is None)
ok("...while still counting the thread as scanned, so nothing looks wrong from outside",
   scanned == 1 and enqueued == 0, f"scanned={scanned} enqueued={enqueued}")


# ── 2. the migration opens it ─────────────────────────────────────────────────────────────
print("\ntest_migration_50_releases_them")

with state.connect() as c:
    state._migration_50(c)

wm = store.get_watermark(SPACE, ZCID)
ok("the row is INVALIDATED, NOT DELETED — the audit of the thread survives",
   wm is not None and wm["last_activity"] is None, str(wm))

calls, scanned, enqueued = _sweep()
ok("the next poll fetches the messages it was skipping", calls == 1, f"{calls} fetches")
conv = store.get_conversation(SPACE, ZCID)
ok("AND THE CONVERSATION IS FINALLY RECORDED", conv is not None)
ok("...with the inbound clock the reply window depends on, not an empty string",
   bool(conv and conv.get("last_inbound_at")), str(conv and conv.get("last_inbound_at")))


# ── 3. it costs exactly one extra fetch, and only once ───────────────────────────────────
print("\ntest_it_is_one_time_and_the_optimisation_still_works")

calls, _, _ = _sweep()
ok("an unchanged thread is skipped again straight after — the optimisation is intact",
   calls == 0, f"{calls} fetches")

with state.connect() as c:
    state._migration_50(c)
calls, _, _ = _sweep()
ok("RUNNING THE MIGRATION AGAIN CHANGES NOTHING — a healed row has an id now",
   calls == 0, f"{calls} fetches")

# The threads where NULL is CORRECT: every message outbound, so there is no newest inbound. They
# pay one fetch here and must not be re-fetched forever — which is why this is a migration and
# not a runtime rule keyed on the same condition.
OUT_ONLY = "conv-out-only"
store.set_watermark(SPACE, OUT_ONLY, last_seen_msg_id=None, last_activity="2026-09-16T10:00:00Z")
with state.connect() as c:
    state._migration_50(c)
ok("a legitimately-NULL watermark is invalidated too, which is correct and costs one fetch",
   store.get_watermark(SPACE, OUT_ONLY)["last_activity"] is None)


# ── 4. a new box was never affected, which is why CI stayed green ────────────────────────
print("\ntest_a_new_box_is_untouched")

with state.connect() as c:
    before = c.execute("SELECT COUNT(*) FROM inbox_state").fetchone()[0]
    state._migration_50(c)
    after = c.execute("SELECT COUNT(*) FROM inbox_state").fetchone()[0]
ok("the migration never removes a row", before == after, f"{before} -> {after}")
ok("and it is reachable from the kernel at SCHEMA_VERSION",
   state.MIGRATIONS.get(50) is state._migration_50 and state.SCHEMA_VERSION >= 50)
ok("...tagged customer_voice, because a Lead or Content box has no inbox_state",
   state._MIGRATION_OWNER.get(50) == "customer_voice")

# ── 5. releasing them must not wake the handler 91 times ─────────────────────────────────
print("\ntest_a_backfilled_thread_is_stored_but_never_enqueued")

# OSDev1 HELD THIS PR OVER EXACTLY THIS, and he was right. Migration 50 lets the fixed classifier
# finally see the threads — and every one would have been enqueued at once. `handler.py` checks
# the kill switch at :112 and the send window at :143, IN THAT ORDER, so a box on `autonomy: off`
# posts ":speech_balloon: New Instagram message … observed only" to Slack for each job BEFORE it
# discovers the window is shut. Ninety-one Slack posts, all labelled New, about months-old
# conversations. Buyer boxes have no Slack, so it lands on ours — which is how it ships.
from core import state as _state  # noqa: E402

OLD = "2026-06-01T09:00:00Z"                       # months outside any window here
FRESH = None                                       # filled below, minutes old


def _jobs_for(zcid):
    with _state.connect() as c:
        return c.execute("SELECT COUNT(*) FROM jobs WHERE idempotency_key LIKE ?",
                         (f"inbox:{SPACE}:{zcid}:%",)).fetchone()[0]


def _sweep_one(zcid, when, body="Are you open Sunday?", mid="m-x"):
    page = {"conversations": [{"id": zcid, "accountId": "acc-x",
                               "updatedTime": when, "participant": "Sam"}]}
    msgs = [{"_id": mid, "accountId": "acc-x", "conversationId": zcid, "direction": "incoming",
             "senderId": "cust", "message": body, "sentAt": when, "createdAt": when}]

    class _Z:
        class inbox:
            @staticmethod
            def messages(zcid_, acctid, limit=None):
                return {"messages": msgs}
    poller._sweep_channel(SP, _Z(), CH, page)


_sweep_one("c-old", OLD)
ok("an OLD thread is STORED — the buyer still reads it on his screen",
   store.get_conversation(SPACE, "c-old") is not None)
_old_msgs = store.messages_for(SPACE, "c-old")
ok("...with its message, so the thread has its history and not just a row",
   len(_old_msgs) == 1 and "Sunday" in str(_old_msgs[0].get("body") or ""), str(_old_msgs))
ok("AND NO JOB IS MADE — nothing wakes the handler, so nothing posts to Slack",
   _jobs_for("c-old") == 0, f"{_jobs_for('c-old')} jobs")
ok("...and its watermark advanced, so it is not re-fetched every poll",
   (store.get_watermark(SPACE, "c-old") or {}).get("last_activity") == OLD)

from datetime import datetime, timedelta, timezone  # noqa: E402

FRESH = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
_sweep_one("c-fresh", FRESH, mid="m-fresh")
ok("A FRESH INBOUND STILL ENQUEUES — this must not have turned intake off",
   _jobs_for("c-fresh") == 1, f"{_jobs_for('c-fresh')} jobs")

# THE CARVE-OUT THAT MATTERS. A STOP sent a month ago is still a STOP. The handler is the only
# thing that normally honours one, and the handler is exactly what we are declining to wake.
_sweep_one("c-old-stop", OLD, body="STOP", mid="m-stop")
ok("a STOP in an old thread makes NO job either", _jobs_for("c-old-stop") == 0)
ok("...AND THE OPT-OUT IS STILL HONOURED, because the handler will never see it",
   bool((store.get_conversation(SPACE, "c-old-stop") or {}).get("opted_out")),
   str(store.get_conversation(SPACE, "c-old-stop")))

# The gate must ask the window the SAME question the handler asks, or the two drift: stricter and
# a sendable thread is never queued, looser and the noise comes straight back.
import inspect  # noqa: E402

psrc = inspect.getsource(poller._sweep_channel)
hsrc = inspect.getsource(__import__("marketing.customer_voice.inbox.handler",
                                    fromlist=["handle"]))
ok("both sides ask `window.allowed_send` with an explicit platform, so they cannot disagree",
   "window.allowed_send" in psrc and "allowed_send" in hsrc
   and "platform=" in psrc.split("window.allowed_send")[1][:60], psrc.split("window.allowed_send")[1][:60])

print("\n— and this file cannot silently fall out of CI —")
import pathlib  # noqa: E402

_wf = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml"
if _wf.is_file():
    ok("test_dead_watermarks_release is in the workflow's suite list",
       "test_dead_watermarks_release" in _wf.read_text())
else:
    print("  --   no workflow file here (a box, not the repo) — nothing to check")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
