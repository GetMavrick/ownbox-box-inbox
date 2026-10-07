"""Replies on their own: Instagram and Messenger DMs answered with nobody pressing send (owner, 2026-10-07).

Owner, 2026-10-07: "We also should build something that allows automatic sending of messages per channel. Like I would
like automatic instant responses to any Instagram or messenger DM's that come in. I don't wanna have to approve those
and I trust that sonnet will keep the conversation rolling. I think we need to build a listener on a 20 second
interval, like we did with the lead magnet machine."

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * a box sends anything on its own before its owner switches a channel on, or on a channel he left off, or by email;
  * switching it on answers the backlog (only what arrives after the switch is answered);
  * a reply goes that the box judged nobody needs, that turns a cold pitch around, that a person dismissed, or that
    answers anything but the newest message;
  * it talks over a person chatting on the thread, an automation holding it, someone who opted out, a stopped box, a
    closed window, or past the hourly cap;
  * one message is answered twice, or a conversation already answered is answered again;
  * the listener is not every 20 seconds, or does any work while every switch is off;
  * the Sending screen doesn't let the owner (and only the owner) switch each channel.

No network: the vendor's inbox is stood in for.

Run: python tests/test_dms_answer_themselves.py
"""
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
T = pathlib.Path(tempfile.mkdtemp(prefix="auto_"))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")
os.environ["AIOS_MY_MACHINES"] = str(T / "machines")
os.environ.setdefault("DASH_TOKEN", "test-dash-pw")
os.environ.pop("ZERNIO_API_KEY", None)

from core.vendors import zernio  # noqa: E402


class FakeInbox:
    def __init__(self):
        self.sent = []

    def send(self, cid, account_id, text, *, tag=None):
        self.sent.append((cid, text))
        return {"message_id": f"out-{len(self.sent)}"}


INBOX = FakeInbox()
zernio.client = lambda space: type("S", (), {"inbox": INBOX})()

from core import box_settings, pause, spaces as _spaces, state  # noqa: E402

state.init_db()
_spaces.space_by_name = lambda name, allow_default_alias=True: {"name": name, "key": "k"}
_spaces.all_spaces = lambda: [{"name": _spaces.DEFAULT}]
SPACE = _spaces.DEFAULT

import marketing.customer_voice as cv  # noqa: E402 — registers the machine's tables and its periodics
from marketing.customer_voice import claims  # noqa: E402
from marketing.customer_voice.drafter import store as drafts  # noqa: E402
from marketing.customer_voice.inbox import autosend, sending, store  # noqa: E402

state.init_db()
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {str(detail)[:400]}" if not cond and detail else ""))
    if not cond:
        _failed += 1


n = [0]


def dm(zcid, said="Do you have anything Saturday?", *, platform="instagram", mins_ago=0.0, reply="Yes! 10 or 2?"):
    """A message from them and the reply the box wrote for it. -> the inbound's id."""
    n[0] += 1
    zmid = f"in-{zcid}-{n[0]}"
    at = (datetime.now(timezone.utc) - timedelta(minutes=mins_ago)).isoformat()
    store.upsert_conversation(space=SPACE, zcid=zcid, platform=platform, participant="Dana", last_inbound_at=at,
                              account_id="acct-1")
    store.record_message(space=SPACE, zcid=zcid, zmid=zmid, direction="in", sent_by="contact", body=said, sent_at=at)
    with state.connect() as c:
        c.execute("UPDATE inbox_messages SET created_at = ? WHERE zernio_message_id = ?", (at, zmid))
    if reply is not None:
        drafts.put(space=SPACE, zcid=zcid, in_reply_to=zmid, body=reply)
    return zmid


def sent_to():
    return [cid for cid, _ in INBOX.sent]


def tick():
    INBOX.sent.clear()
    return autosend.tick()


print("test_off_until_the_owner_turns_a_channel_on")
dm("before", mins_ago=60)
ok("every channel is off on a new box", sending.get()["auto_reply"] == {"instagram": "off", "messenger": "off"})
out = tick()
ok("...and nothing sends", out["status"] == "off" and not INBOX.sent, out)
try:
    sending.put(auto_reply={"email": "on"})
    email_refused = False
except ValueError:
    email_refused = True
ok("email has no switch: replies by email always wait for a person", email_refused)
sending.put(auto_reply={"instagram": "on"}, by="owner")
since = sending.auto_reply_since("instagram")
ok("switched on for Instagram, it records since when", since and sending.auto_reply_since("messenger") == "")
out = tick()
ok("THE BACKLOG IS NEVER ANSWERED: a message from before the switch waits for a person", not INBOX.sent, out)
sending.put(auto_reply={"instagram": "on"})
ok("...and saving it on again does not move 'since'", sending.auto_reply_since("instagram") == since)

print("\ntest_a_new_dm_is_answered_at_once")
dm("dana", "Hi! Do you have anything open Saturday?", reply="Hey Dana! Yes, 10am or 2pm. Which works?")
out = tick()
ok("a new Instagram message gets the reply the box wrote, with nobody pressing send",
   out["sent"] == 1 and INBOX.sent == [("dana", "Hey Dana! Yes, 10am or 2pm. Which works?")], (out, INBOX.sent))
with state.connect() as c:
    who = c.execute("SELECT sent_by FROM inbox_messages WHERE zernio_conversation_id = 'dana' AND direction = 'out'"
                    ).fetchone()
ok("...mirrored as the machine's, never as a person's", who and who["sent_by"] == "ai", dict(who or {}))
ok("...so the conversation is no longer waiting", "dana" not in {r["zernio_conversation_id"] for r in
                                                                  store.list_conversations(SPACE, waiting=True)})
out = tick()
ok("ONCE: the next tick sends nothing to her", not INBOX.sent, (out, INBOX.sent))
dm("dana", "Great, 2pm please!", reply="Perfect, you're booked in for 2pm Saturday. See you then!")
out = tick()
ok("...and her next message is answered too: the conversation keeps rolling",
   INBOX.sent == [("dana", "Perfect, you're booked in for 2pm Saturday. See you then!")], INBOX.sent)

print("\ntest_only_what_should_go")
dm("mess", platform="messenger")
dm("mail", platform="email")
dm("noreply", reply=autosend.NO_REPLY_BODY)
z = dm("pitch", reply="Thanks! Here's what we do: ownbox.io")
drafts.mark_pitch_back(SPACE, z)
z = dm("dismissed")
drafts.dismiss(SPACE, drafts.for_inbound(SPACE, z)["id"])
dm("older", reply="An answer to the first message")
dm("older", "and another thing", reply=None)
out = tick()
ok("never Messenger while it is off, never email, never a no-reply, a pitch, a dismissed reply, or an answer to an "
   "older message", INBOX.sent == [], (out, INBOX.sent))
ok("the box's no-reply mark is the drafter's own words", autosend.NO_REPLY_BODY == drafts.NO_REPLY_BODY)

print("\ntest_it_never_talks_over_anyone")
dm("chatting")
store.record_message(space=SPACE, zcid="chatting", zmid="human-1", direction="out", sent_by="human", body="omw!",
                     sent_at=(datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat())
dm("chatting", "cool see you soon", reply="See you soon!")
dm("stopped")
store.set_opted_out(SPACE, "stopped")
dm("held")
claims.claim(SPACE, "held", machine="my_lead_magnet", title="Lead magnets")
out = tick()
ok("not while a person chatted on it in the last 30 minutes, not to someone who opted out, not on a conversation an "
   "automation holds", INBOX.sent == [] and out["held"].get("person_active") == 1, (out, INBOX.sent))
claims.release(SPACE, "held", machine="my_lead_magnet")

dm("paused")
pause.is_paused, real = (lambda: True), pause.is_paused
out = tick()
pause.is_paused = real
ok("not while the box is stopped", "paused" not in sent_to() and out["held"].get("box_stopped", 0) >= 1, out)
INBOX.sent.clear()

box_settings.put("inbox", sending.KEY_CAP, 1, set_by="t")
dm("cap-a")
dm("cap-b")
out = tick()
ok("never past the hourly cap: what can't go now waits", len(INBOX.sent) <= 1 and out["held"].get("hourly_cap") == 1,
   (out, INBOX.sent))
box_settings.put("inbox", sending.KEY_CAP, 40, set_by="t")

print("\ntest_never_a_loop_between_two_machines")
# OSDev1's review: another business's auto-responder answers every reply at once. Three of ours an hour, then a person.
ours = 0
for i in range(4):
    dm("bot", f"Thanks for your message! Reply 1 for prices. ({i})", reply=f"Happy to help! ({i})")
    out = tick()
    ours += sum(1 for cid, _ in INBOX.sent if cid == "bot")
ok("three automatic replies on one conversation in an hour, then the next waits for a person",
   ours == 3 and out["held"].get("conversation_cap") == 1, (ours, out))

print("\ntest_switched_off_it_stops")
sending.put(auto_reply={"instagram": "off"})
dm("after-off")
out = tick()
ok("off again: nothing more sends", out["status"] == "off" and not INBOX.sent, out)

print("\ntest_the_listener")
reg = {t["name"]: t for t in __import__("core.worker", fromlist=["PERIODIC"]).PERIODIC}
ok("a listener every 20 seconds, the lead magnet's cadence", reg.get("inbox_auto_reply", {}).get("interval") == 20.0,
   reg.get("inbox_auto_reply"))
calls = []
from marketing.customer_voice.inbox import poller  # noqa: E402
import marketing.customer_voice.inbox as inbox_pkg  # noqa: E402
real = (poller.poll_sweep, cv._drafter.periodic, autosend.tick, getattr(inbox_pkg, "_sdk_ok", False))
poller.poll_sweep = lambda **kw: calls.append(("poll", kw)) or {}
cv._drafter.periodic = lambda **kw: calls.append(("draft", kw)) or {}
autosend.tick = lambda: calls.append(("send", {})) or {"status": "ok"}
inbox_pkg._sdk_ok = True
ok("with every switch off it does nothing at all: no poll, no model, no send",
   cv._auto_reply() == {"status": "off"} and not calls, calls)
sending.put(auto_reply={"instagram": "on"})
cv._auto_reply()
ok("switched on, it polls only that channel, drafts only NEW messages on it, then sends (OSDev1's review: the "
   "re-checks and rewrites stay on the drafter's own two minutes)",
   calls == [("poll", {"only": ("instagram",)}), ("draft", {"new_only": True, "platforms": ("instagram",)}),
             ("send", {})], calls)
poller.poll_sweep, cv._drafter.periodic, autosend.tick, inbox_pkg._sdk_ok = real
sending.put(auto_reply={"instagram": "off"})

from marketing.customer_voice.drafter import draft  # noqa: E402
from core import brain  # noqa: E402
asked, extra = [], []
brain.think, real_think = (lambda task, prompt, **kw: asked.append(prompt) or "Sure thing!"), brain.think
real_stale = (drafts.stale_no_reply, drafts.stale_waiting)
drafts.stale_no_reply = lambda *a, **k: extra.append("recheck") or []
drafts.stale_waiting = lambda *a, **k: extra.append("rewrite") or []
dm("new-ig", "hey are you open sunday", reply=None)
dm("new-mail", "Do you open Sunday?", platform="email", reply=None)
out = draft.sweep(SPACE, new_only=True, platforms=("instagram",))
ok("the listener's drafter writes the new Instagram message and nothing else: no email, no re-check, no rewrite",
   drafts.for_inbound(SPACE, f"in-new-ig-{n[0] - 1}") is not None and drafts.for_inbound(SPACE, f"in-new-mail-{n[0]}")
   is None and not extra, (out, extra))
brain.think = real_think
drafts.stale_no_reply, drafts.stale_waiting = real_stale

polled = []
FakeZ = type("Z", (), {"inbox": type("I", (), {"list": staticmethod(lambda **kw: polled.append(kw.get("platform"))
                                                                     or {"conversations": []})})(),
                       "comments": type("C", (), {"list_inbox_comments": staticmethod(
                           lambda **kw: polled.append("comments") or {"data": []})})()})()
real_poll = (poller._spaces, zernio.client, poller._vendor_intake_allowed, poller._sweep_email)
poller._spaces = lambda: [{"name": SPACE, "zernio_key": "k"}]
zernio.client = lambda sp: FakeZ
poller._vendor_intake_allowed = lambda: True
poller._sweep_email = lambda *a, **k: (polled.append("email") or (0, 0, True))
beats = []
real_beat, state.heartbeat = state.heartbeat, lambda comp, st, **k: beats.append(comp)
poller.poll_sweep(only=("instagram",))
ok("the listener's poll reads Instagram alone: one Zernio call, no mailbox, no other channel, and no heartbeat (the "
   "full poll owns it)", polled == ["instagram"] and "inbox_poll" not in beats, (polled, beats))
state.heartbeat = real_beat
poller._spaces, zernio.client, poller._vendor_intake_allowed, poller._sweep_email = real_poll

print("\ntest_the_sending_screen")
from core import dash  # noqa: E402
from core.dispatch import app  # noqa: E402
o = app.test_client()
o.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
page = o.get("/inbox/sending").get_data(as_text=True)
ok("the owner sees a switch for Instagram and one for Messenger", 'name="auto_instagram"' in page
   and 'name="auto_messenger"' in page and "Email always waits for you" in page)
r = o.post("/inbox/sending", data={"auto_instagram": "on", "auto_messenger": "off", "first_message": "off",
                                   "text": "", "hourly_cap": "40"})
ok("...and switching Instagram on there works", r.status_code == 303 and sending.get()["auto_reply"]["instagram"] == "on",
   r.status_code)
m = app.test_client()
m.set_cookie(dash.COOKIE, dash.new_session(state.add_user("sam@example.com", name="Sam", role="member")["id"]))
ok("a member reads it and can't change it", "Instagram replies on their own: On" in m.get("/inbox/sending")
   .get_data(as_text=True) and m.post("/inbox/sending", data={"auto_instagram": "off"}).status_code == 403
   and sending.get()["auto_reply"]["instagram"] == "on")

print("\nALL AUTO-REPLY CHECKS PASS" if not _failed else f"\n{_failed} AUTO-REPLY CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
