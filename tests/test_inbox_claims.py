"""The Inbox's two seams for a machine that runs conversations (Lead Magnet plan, step 2).

docs/PLAN_LEAD_MAGNET_MACHINE.md, approved by the owner 2026-10-01 ("Approve the plan with your
recommendations"): decision 2, the Inbox's drafting stays off for a conversation an automation runs; decision 3,
what it can't answer goes to a person; and OSDev1's rule that the Inbox is never edited for one machine, so the
machine's controls live in a slot the Inbox offers (`m.panel("inbox", ...)`).

What is measured, on the real store, the real send path and the real screens:
  claims
    * a claim is held by one machine: another can't take it, the holder can renew it, and only the holder can
      release it; a released or expired claim can be taken by anyone
  while claimed, the Inbox
    * doesn't count it as waiting (header, filter, morning count, notice: one predicate), doesn't offer it for
      a draft or show its old draft, and doesn't send its opener
    * still lists it, tagged "Handled by <title>", and says so above the thread
  handed back with a note
    * it is waiting again, drafted again, and the note is above the thread
  a machine's message (`m.send_dm`)
    * goes only on a conversation it holds, never to someone who opted out, never while the box is stopped,
      never outside the channel's window, never past the hourly cap, never by email
    * goes once per key: the same step again is a duplicate, and one that may have landed is never resent
    * is mirrored as the machine's, not a person's
  `m.messages` reads the conversation, and `since` keeps only what is new
  the panel slot: a machine's card renders under the conversations, and a broken one never breaks the Inbox
  a box with no inbox says so in a sentence, and the Inbox behaves as before when nothing claims anything

Run: python tests/test_inbox_claims.py
"""
import json
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
T = pathlib.Path(tempfile.mkdtemp(prefix="claims_"))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")
os.environ["AIOS_MY_MACHINES"] = str(T / "machines")
os.environ.setdefault("DASH_TOKEN", "test-dash-pw")
os.environ.pop("ZERNIO_API_KEY", None)

from core.vendors import zernio  # noqa: E402


class FakeInbox:
    def __init__(self):
        self.sent = []
        self.fail = None

    def send(self, cid, account_id, text, *, tag=None):
        if self.fail:
            raise self.fail
        self.sent.append((cid, text))
        return {"message_id": f"out-{len(self.sent)}"}


INBOX = FakeInbox()
zernio.client = lambda space: type("S", (), {"inbox": INBOX})()

from core import spaces as _spaces, state  # noqa: E402

state.init_db()
_spaces.space_by_name = lambda name, allow_default_alias=True: {"name": name, "key": "k"}
SPACE = _spaces.DEFAULT

import marketing.customer_voice  # noqa: E402,F401 — registers the machine's tables
from marketing.customer_voice import claims  # noqa: E402
from marketing.customer_voice.drafter import store as drafts  # noqa: E402
from marketing.customer_voice.inbox import handler, reply, store  # noqa: E402
from core import conversations, panels, sdk  # noqa: E402

state.init_db()
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


NOW = datetime.now(timezone.utc)


def conv(zcid, *, platform="instagram", inbound_ago_h=1.0, body="hi there"):
    at = (NOW - timedelta(hours=inbound_ago_h)).isoformat()
    store.upsert_conversation(space=SPACE, zcid=zcid, platform=platform, participant="Dana Reyes",
                              last_inbound_at=at, account_id="acct-1")
    store.record_message(space=SPACE, zcid=zcid, zmid=f"in-{zcid}", direction="in", sent_by="contact",
                         body=body, sent_at=at)


def waiting_ids():
    return {r["zernio_conversation_id"] for r in store.list_conversations(SPACE, waiting=True)}


print("— claims —")
ok("a machine claims a conversation", claims.claim(SPACE, "c1", machine="my_a", title="Lead magnets"))
ok("ANOTHER MACHINE CAN'T TAKE IT", not claims.claim(SPACE, "c1", machine="my_b", title="Other"))
ok("...the holder can renew it", claims.claim(SPACE, "c1", machine="my_a", title="Lead magnets"))
ok("...and only the holder can release it", not claims.release(SPACE, "c1", machine="my_b"))
ok("released, anyone may claim it",
   claims.release(SPACE, "c1", machine="my_a") and claims.claim(SPACE, "c1", machine="my_b", title="Other"))
with state.connect() as c:
    c.execute("UPDATE inbox_claims SET expires_at = '2000-01-01T00:00:00' WHERE zernio_conversation_id = 'c1'")
ok("AN EXPIRED CLAIM HOLDS NOTHING: a stalled machine can't silence a conversation for good",
   claims.holder(SPACE, "c1") is None and claims.claim(SPACE, "c1", machine="my_a", title="Lead magnets"))
claims.release(SPACE, "c1", machine="my_a")

print("\n— while claimed, the Inbox leaves it alone —")
conv("c2")
drafts.put(space=SPACE, zcid="c2", in_reply_to="in-c2", body="An old draft")
conv("c3")
ok("before any claim: waiting, counted, offered for a draft, its draft shown",
   "c2" in waiting_ids() and store.awaiting_reply(SPACE) == 2
   and "c3" in {r["zcid"] for r in drafts.needs_a_draft(SPACE)}
   and "c2" in {r["zcid"] for r in drafts.waiting(SPACE)})
claims.claim(SPACE, "c2", machine="my_a", title="Lead magnets")
claims.claim(SPACE, "c3", machine="my_a", title="Lead magnets")
ok("CLAIMED: NOT WAITING ON A PERSON, in the filter and every count",
   not ({"c2", "c3"} & waiting_ids()) and store.awaiting_reply(SPACE) == 0
   and store.inbox_counts(SPACE)["waiting"] == 0)
ok("NOT OFFERED FOR A DRAFT, and its old draft isn't offered either",
   "c3" not in {r["zcid"] for r in drafts.needs_a_draft(SPACE)}
   and "c2" not in {r["zcid"] for r in drafts.waiting(SPACE)} and drafts.waiting_count(SPACE) == 0)
rows = {r["zernio_conversation_id"]: r for r in store.list_conversations(SPACE)}
ok("STILL LISTED, with who is handling it", rows.get("c2", {}).get("held_by") == "Lead magnets", str(rows.get("c2")))

handler._cfg = lambda: {"autonomy": "opener", "hourly_send_cap": 40}
before = len(INBOX.sent)
res = handler.handle({"raw_text": json.dumps({"space": SPACE, "zcid": "c2", "account_id": "acct-1",
                                              "platform": "instagram", "inbound_text": "hi"})})
ok("THE INBOX'S OWN OPENER ISN'T SENT on a claimed conversation",
   res.get("status") == "claimed" and len(INBOX.sent) == before, str(res))

print("\n— handed back with a note —")
claims.release(SPACE, "c3", machine="my_a", note="Asked about refunds while waiting for their email")
ok("IT IS WAITING AGAIN and offered for a draft",
   "c3" in waiting_ids() and "c3" in {r["zcid"] for r in drafts.needs_a_draft(SPACE)})
ok("...with the note kept for whoever answers it",
   (claims.handed_back(SPACE, "c3") or {}).get("note", "").startswith("Asked about refunds"))

print("\n— a machine's message, through the Inbox's own send path —")
m = sdk.machine("lead-magnet")
conv("d1")
r = m.send_dm("d1", "Want the guide?", key="ask")
ok("NOT ON A CONVERSATION IT DOESN'T HOLD", r["status"] == "refused" and "claimed" in r["reason"], str(r))
ok("claimed through the SDK, under the machine's own key",
   m.claim("d1", title="Lead magnets") and claims.holder(SPACE, "d1")["machine"] == "my_lead_magnet")
r = m.send_dm("d1", "Want the guide?", key="ask")
ok("SENT, through the vendor", r["status"] == "sent" and INBOX.sent[-1] == ("d1", "Want the guide?"), str(r))
mine = [x for x in store.messages_for(SPACE, "d1") if x.get("direction") == "out"]
ok("...and mirrored as THE MACHINE'S, never a person's", mine and mine[-1]["sent_by"] == "ai", str(mine))
n = len(INBOX.sent)
r = m.send_dm("d1", "Want the guide?", key="ask")
ok("THE SAME STEP AGAIN IS A DUPLICATE, never a second message",
   r["status"] == "duplicate" and len(INBOX.sent) == n, str(r))

INBOX.fail = zernio.ZernioError("gateway timeout after send", indeterminate=True)
r = m.send_dm("d1", "Here it is", key="guide")
INBOX.fail = None
r2 = m.send_dm("d1", "Here it is", key="guide")
ok("A SEND THAT MAY HAVE LANDED SAYS SO, and the same step is never resent",
   r["status"] == "unknown" and r2["status"] == "unknown" and len(INBOX.sent) == n, f"{r} {r2}")

conv("d2")
m.claim("d2", title="Lead magnets")
store.set_opted_out(SPACE, "d2")
ok("NEVER TO SOMEONE WHO OPTED OUT", m.send_dm("d2", "x", key="k")["status"] == "refused")

conv("d3", inbound_ago_h=30)
m.claim("d3", title="Lead magnets")
r = m.send_dm("d3", "x", key="k")
ok("NEVER OUTSIDE THE CHANNEL'S WINDOW (Instagram: 24 hours after their own message)",
   r["status"] == "refused" and "window" in r["reason"], str(r))

conv("d4")
m.claim("d4", title="Lead magnets")
from core import pause  # noqa: E402
_real_paused = pause.is_paused
pause.is_paused = lambda: True
r = m.send_dm("d4", "x", key="k")
pause.is_paused = _real_paused
ok("NEVER WHILE THE BOX IS STOPPED", r["status"] == "refused" and "stopped" in r["reason"], str(r))

_real_cap = store.sends_last_hour
store.sends_last_hour = lambda space: 10_000
r = m.send_dm("d4", "x", key="k2")
store.sends_last_hour = _real_cap
ok("NEVER PAST THE HOURLY CAP", r["status"] == "refused" and "hour" in r["reason"], str(r))

conv("e1", platform="email")
m.claim("e1", title="Lead magnets")
ok("NEVER BY EMAIL on this path", m.send_dm("e1", "x", key="k")["status"] == "refused")

conv("d5")
claims.claim(SPACE, "d5", machine="my_other", title="Other")
ok("NEVER ON A CONVERSATION ANOTHER MACHINE HOLDS",
   not m.claim("d5", title="Lead magnets") and m.send_dm("d5", "x", key="k")["status"] == "refused")

msgs = m.messages("d1")
ok("m.messages reads the conversation, oldest first, in plain fields",
   [x["direction"] for x in msgs] == ["in", "out"] and msgs[0]["body"] == "hi there"
   and set(msgs[0]) == {"id", "direction", "sent_by", "body", "at"}, str(msgs))
ok("...and `since` keeps only what is new", m.messages("d1", since=msgs[0]["at"]) == msgs[1:])
ok("m.release hands it back", m.release("d1", note="Asked a question") and claims.holder(SPACE, "d1") is None)

print("\n— the screens —")
from core.config import settings  # noqa: E402
from core.dispatch import app  # noqa: E402

client = app.test_client()
client.post("/dash/login", data={"token": settings.dash_token})
m.panel("inbox", title="Lead magnets", render=lambda: "<p>3 keywords live</p>")
sdk.machine("broken-one").panel("inbox", title="Broken", render=lambda: 1 / 0)
page = client.get("/inbox/inbox")
body = page.get_data(as_text=True)
ok("THE INBOX RENDERS A MACHINE'S CARD in its slot", page.status_code == 200 and "3 keywords live" in body,
   str(page.status_code))
ok("...below the conversations", body.find("3 keywords live") > body.find("Dana Reyes") > 0)
ok("A BROKEN CARD NEVER BREAKS THE INBOX", "This section couldn't load." in body and "ZeroDivision" not in body)
ok("a claimed row says who is handling it", "Handled by Lead magnets" in body)
thread = client.get("/inbox/inbox/c2").get_data(as_text=True)
ok("ABOVE A CLAIMED THREAD: who is handling it", "is handling this conversation" in thread, thread[:300])
thread = client.get("/inbox/inbox/c3").get_data(as_text=True)
ok("ABOVE A HANDED-BACK THREAD: the note", "Handed back by" in thread and "Asked about refunds" in thread)

print("\n— with nothing claiming anything —")
ok("a conversation nobody claimed behaves exactly as before: waiting and drafted",
   "c3" in waiting_ids() and "held_by" in rows.get("c2", {}))
_real = conversations._PROVIDER
conversations._PROVIDER = None
try:
    m.claim("x", title="T")
    ok("a box with no inbox says so", False)
except conversations.NoProvider as e:
    ok("A BOX WITH NO INBOX SAYS SO, in a sentence a builder can act on", "Unified Inbox" in str(e))
conversations._PROVIDER = _real
ok("THE DRAFTER'S COPY OF THE CLAIM PREDICATE IS THE INBOX'S, character for character",
   drafts._UNCLAIMED == claims.UNCLAIMED, drafts._UNCLAIMED)
ok("the conversation calls are promised seams of SDK 1",
   {"claim", "release", "messages", "send_dm"} <= set(sdk.SEAMS))
src = (ROOT / "core" / "conversations.py").read_text()
ok("core/conversations.py names no machine package", "customer_voice" not in src and "marketing" not in src)

print("\n" + ("ALL OK" if not _failed else f"{_failed} FAILED"))
sys.exit(1 if _failed else 0)
