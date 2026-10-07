"""The Lead Magnet machine (my/machines/lead-magnet), end to end on the box's real seams, with Zernio and the email
transport stubbed. docs/PLAN_LEAD_MAGNET_MACHINE.md step 3.

What is measured, in the order a person meets it:
  * off by default: a box that carries it sends nothing until the owner turns it on
  * NO BACKFILL: a comment made before the keyword went live is never answered
  * a keyword comment (whole word, any case) gets ONE private reply with its tap, however many passes read it
  * their tap opens the conversation: the machine finds it, claims it, asks for the follow; an old DM in an
    existing thread is not their answer
  * "I just followed you!" (Instagram didn't say either way): the guide, with its button, then the email ask; the
    person is recorded (m.person) with guide_sent (m.touch)
  * their email is CONFIRMED before the welcome email goes; a reply with another address corrects it; the welcome
    email has the message they wrote it in as the consent, and the conversation is let go
  * Instagram says they DON'T follow: a nudge at most once an hour, exactly three times, then their next reply
    gets the guide anyway
  * OFF THE SCRIPT, in every stage, goes to a person with what they wrote; a STOP ends it at once
  * NO LEAD STAYS OPEN FOREVER: a quiet day hands the conversation back; so does a closed window
  * wait or stop is decided on the inbox's refusal code, never its wording
  * a person replying takes it over, until they comment the keyword again
  * the owner's controls: the Inbox card, the page (switch, keywords), the messages page, the Morning Review;
    turning it off lets go of every conversation it holds
  * it is built only on what the box promises (ownbox check)

Run: python tests/test_lead_magnet_machine.py
"""
import os
import pathlib
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
T = pathlib.Path(tempfile.mkdtemp(prefix="leadmagnet_"))
shutil.copytree(ROOT / "my" / "machines" / "lead-magnet", T / "machines" / "lead-magnet",
                ignore=shutil.ignore_patterns("data", "__pycache__"))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")
os.environ["AIOS_MY_MACHINES"] = str(T / "machines")
os.environ["AIOS_PAUSE_FILE"] = str(T / "PAUSED")
os.environ.setdefault("DASH_TOKEN", "test-dash-pw")
os.environ.setdefault("DISPATCH_BEARER_TOKEN", "bearer")
os.environ.pop("ZERNIO_API_KEY", None)

from core.vendors import zernio  # noqa: E402

NOW = datetime.now(timezone.utc)


def ago(**kw) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


class FakeInbox:
    def __init__(self):
        self.sent, self.threads = [], []

    def list(self, *, status="active", cursor=None, limit=50, platform=None):
        return {"conversations": self.threads if status == "active" else []}

    def send(self, cid, account_id, text, *, tag=None, quick_replies=None, buttons=None):
        self.sent.append({"cid": cid, "text": text, "quick_replies": quick_replies, "buttons": buttons})
        return {"message_id": f"out-{len(self.sent)}"}


class FakeComments:
    def __init__(self):
        self.replies, self.items = [], []

    def list(self, account_id, *, since=None, limit=50, cursor=None):
        return {"items": [{"platformPostId": "reel-1", "commentCount": len(self.items)}]}

    def list_post_comments(self, post_id, account_id, *, limit=50, cursor=None):
        return {"items": self.items}

    def send_private_reply(self, post_id, comment_id, account_id, message, *, quick_replies=None):
        self.replies.append({"comment": comment_id, "text": message, "quick_replies": quick_replies})
        return {"message_id": f"pr-{len(self.replies)}"}


class FakeAccounts:
    def discover(self):
        return {"instagram": "ig-acct"}

    def identities(self):
        return {"instagram": {"ig-self"}}


INBOX, COMMENTS = FakeInbox(), FakeComments()
zernio.client = lambda space: type("S", (), {"inbox": INBOX, "comments": COMMENTS, "accounts": FakeAccounts()})()
zernio.is_configured = lambda key: bool(key)

from core import spaces as _spaces, state  # noqa: E402

state.init_db()
_spaces.space_by_name = lambda name, allow_default_alias=True: {"name": name, "key": "k"}
_spaces.all_spaces = lambda: [{"name": _spaces.DEFAULT, "zernio_key": "k"}]
SPACE = _spaces.DEFAULT

from core import box_mail, pause  # noqa: E402
from core.config import settings as box  # noqa: E402

pause.MARKER = pathlib.Path(os.environ["AIOS_PAUSE_FILE"])
box.dashboard_base_url = "https://acme.ownbox.app"
box.unsub_signing_key = "unsub-test-key"
MAIL = []


def fake_mail(to, subject, text_body, html_body, *, idem_key, sender_name, note, headers=None):
    MAIL.append({"to": to, "subject": subject, "text": text_body, "note": note})
    return f"mail-{len(MAIL)}"


box_mail.send = fake_mail
box_mail.is_configured = lambda: True

from core.dispatch import app  # noqa: E402 — loads the inbox, the foundation and the owner's machines

state.init_db()
from marketing.customer_voice import claims  # noqa: E402
from marketing.customer_voice.inbox import reply, store  # noqa: E402

from core.config import get_config  # noqa: E402

get_config().setdefault("inbox", {})["hourly_send_cap"] = 10_000     # this test sends more than a real hour would
mod = sys.modules.get("my_machines.lead_magnet")
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


if mod is None:
    from core import custom_machines
    print("the machine did not load:", custom_machines.status())
    sys.exit(1)
m, sweep, lm = mod.m, mod.sweep, mod.store
from marketing.customer_voice.inbox import conversations as _inbox_conv  # noqa: E402

_inbox_conv.fill_participant_ids()     # the inbox's one-time read of past conversations (runs in the worker on a box)


def comment(cid, text, *, user="u1", handle="dana.r", hours=1):
    COMMENTS.items.append({"id": cid, "text": text, "from": {"id": user, "username": handle, "name": ""},
                           "createdAt": ago(hours=hours)})


def thread(zcid, user, handle, *, follower=None):
    """The vendor's conversation list gains the thread (it exists once they write back)."""
    p = {"id": user, "username": handle}
    if follower is not None:
        p["instagramProfile"] = {"isFollower": follower}
    INBOX.threads = [t for t in INBOX.threads if t["id"] != zcid] + [{"id": zcid, "participants": [p]}]


def they_write(zcid, body, mid):
    """What the inbox's poller does when they write: the conversation and their message, mirrored."""
    at = datetime.now(timezone.utc).isoformat()
    store.upsert_conversation(space=SPACE, zcid=zcid, platform="instagram", participant="Dana",
                              last_inbound_at=at, account_id="ig-acct")
    store.record_message(space=SPACE, zcid=zcid, zmid=mid, direction="in", sent_by="contact", body=body, sent_at=at)


def lead_of(cid):
    with lm.connect() as c:
        r = c.execute("SELECT * FROM leads WHERE comment_id = ?", (cid,)).fetchone()
    return dict(r) if r else {}


def backdate(hours: float) -> None:
    """Pretend the machine and every keyword went live `hours` ago, so a test comment can be made after it."""
    m.save_setting("on_at", ago(hours=hours), by="test")
    m.save_setting("keywords", [{**k, "on_at": ago(hours=hours)} for k in mod.settings.keywords(m)], by="test")


def kinds(kind, value):
    from core import people
    pid = people.find(SPACE, kind, value)
    return [e["kind"] for e in people.timeline(pid)] if pid else []


def released_with(zcid) -> str:
    return (claims.handed_back(SPACE, zcid) or {}).get("note", "") if claims.holder(SPACE, zcid) is None else ""


def walk(cid, user, handle, zcid, *, to):
    """Take one person through the sequence up to the stage `to` (follow, email or confirm)."""
    comment(cid, "GUIDE", user=user, handle=handle, hours=0.01)
    sweep.run(m)
    thread(zcid, user, handle)
    they_write(zcid, "Send me the link", f"{zcid}-1")
    sweep.run(m)
    if to in ("email", "confirm"):
        they_write(zcid, "I just followed you!", f"{zcid}-2")
        sweep.run(m)
    if to == "confirm":
        they_write(zcid, f"{handle}@example.com", f"{zcid}-3")
        sweep.run(m)
    return lead_of(cid)


# ── off by default ──────────────────────────────────────────────────────────────────────────────────────────
print("off by default")
comment("c-0", "GUIDE please!", hours=3)
mod.settings.save_keyword(m, word="guide", title="The Second Brain", url="https://example.com/guides/brain", by="t")
got = sweep.run(m)
ok("a box carrying it sends nothing until the owner turns it on", got.get("off") is True and not COMMENTS.replies, got)
mod.settings.set_on(m, True, by="test")
ok("turning it on stamps the time it went live", bool(m.setting("on_at", default="")))
backdate(2)

# ── the first message ───────────────────────────────────────────────────────────────────────────────────────
print("the first message")
comment("c-1", "GUIDE please!", hours=1)
comment("c-2", "this guidebook is great", user="u7", handle="kim")
sweep.run(m)
ok("NO BACKFILL: a comment made before the keyword went live is never answered (Zernio already DMed them)",
   not any(r["comment"] == "c-0" for r in COMMENTS.replies) and not lead_of("c-0"), lead_of("c-0"))
first = [r for r in COMMENTS.replies if r["comment"] == "c-1"]
ok("a keyword comment made after it went live gets a private reply naming the guide, with its tap",
   len(first) == 1 and "The Second Brain" in first[0]["text"]
   and first[0]["quick_replies"][0]["title"] == "Send me the link", first)
ok("a word that only contains the keyword is not the keyword", not any(r["comment"] == "c-2" for r in COMMENTS.replies))
mod.settings.save_keyword(m, word="late", title="The Late Guide", url="https://example.com/guides/late", by="t")
comment("c-late", "LATE", user="u21", handle="lou", hours=1)      # after the machine went live, before LATE did
sweep.run(m)
ok("...and each keyword answers only from when IT went live, not when the machine did",
   not any(r["comment"] == "c-late" for r in COMMENTS.replies) and not lead_of("c-late"), lead_of("c-late"))
mod.settings.remove_keyword(m, "late", by="t")
sweep.run(m)
ok("ONE private reply per comment, however many passes read it",
   len([r for r in COMMENTS.replies if r["comment"] == "c-1"]) == 1 and lead_of("c-1")["stage"] == "asked")

# ── the tap opens the conversation: follow ask ──────────────────────────────────────────────────────────────
print("the conversation")
thread("thr-dana", "u1", "dana.r")
they_write("thr-dana", "Send me the link", "in-1")
n = len(INBOX.sent)
sweep.run(m)
L = lead_of("c-1")
ok("their tap opens the conversation: found, claimed as Lead Magnet",
   L.get("conversation") == "thr-dana" and (claims.holder(SPACE, "thr-dana") or {}).get("title") == "Lead Magnet",
   (L, claims.holder(SPACE, "thr-dana")))
ok("...and they're asked to follow, with the tap", L["stage"] == "follow" and len(INBOX.sent) == n + 1
   and INBOX.sent[-1]["quick_replies"][0]["title"] == "I just followed you!", INBOX.sent[-n:])
sweep.run(m)
ok("nothing more until they answer", len(INBOX.sent) == n + 1)

they_write("thr-olga", "are you open on sunday?", "o-0")      # an old DM, before she ever commented
thread("thr-olga", "u10", "olga")
comment("c-olga", "GUIDE", user="u10", handle="olga", hours=0.01)
sweep.run(m)
n = len(INBOX.sent)
sweep.run(m)
ok("an old DM in an existing thread is not their answer: claimed, and nothing sent until they reply",
   lead_of("c-olga")["stage"] == "asked" and lead_of("c-olga").get("conversation") == "thr-olga"
   and len(INBOX.sent) == n, lead_of("c-olga"))

# ── followed (Instagram didn't say): the guide, then the email ask ──────────────────────────────────────────
they_write("thr-dana", "I just followed you!", "in-2")
sweep.run(m)
guide, ask = INBOX.sent[-2], INBOX.sent[-1]
ok("the guide goes the moment they say they follow, with its button",
   "https://example.com/guides/brain" in guide["text"]
   and guide["buttons"] == [{"type": "url", "title": "Get the guide", "url": "https://example.com/guides/brain"}],
   guide)
ok("...then the email ask, in the same turn", "email" in ask["text"].lower() and lead_of("c-1")["stage"] == "email")
ok("...and the person is recorded, with guide_sent (m.person, m.touch)", "guide_sent" in kinds("instagram", "dana.r")
   and bool(lead_of("c-1").get("person")), kinds("instagram", "dana.r"))

# ── the email: confirmed (and corrected), then welcomed, then let go ────────────────────────────────────────
they_write("thr-dana", "sure, it's Dana@Example.con", "in-3")
sweep.run(m)
L = lead_of("c-1")
ok("their address is read back to them to check, with a tap, and nothing is emailed yet",
   L["stage"] == "confirm" and "dana@example.con" in INBOX.sent[-1]["text"]
   and INBOX.sent[-1]["quick_replies"][0]["title"] == "Yes, that's right" and not MAIL, (L, INBOX.sent[-1:]))
they_write("thr-dana", "oops dana@example.com", "in-4")
sweep.run(m)
L = lead_of("c-1")
ok("a reply with another address corrects it, and is read back again",
   L["stage"] == "confirm" and L["email"] == "dana@example.com" and L["email_message"] == "in-4"
   and "dana@example.com" in INBOX.sent[-1]["text"] and not MAIL, L)
they_write("thr-dana", "Yes, that's right", "in-5")
sweep.run(m)
L = lead_of("c-1")
ok("confirmed: the welcome email goes to the corrected address, through the box's own email, as this machine's",
   MAIL and MAIL[-1]["to"] == "dana@example.com" and "https://example.com/guides/brain" in MAIL[-1]["text"]
   and MAIL[-1]["note"] == "outbound_mail:my_lead_magnet" and "unsubscribe" in MAIL[-1]["text"].lower(), MAIL[-1:])
ok("...the lead is done, and the conversation is the inbox's again",
   L["stage"] == "done" and L["welcome"] == "sent" and claims.holder(SPACE, "thr-dana") is None, L)
ok("...and the address is recorded as a person, with email_captured", "email_captured" in kinds("email",
                                                                                               "dana@example.com"))
sweep.run(m)
ok("exactly one welcome email, however many passes run", len(MAIL) == 1)

# ── not following: a nudge an hour, three times, then the guide anyway ─────────────────────────────────────
print("the follow gate")
comment("c-3", "guide", user="u3", handle="sam", hours=0.01)
sweep.run(m)
thread("thr-sam", "u3", "sam", follower=False)
they_write("thr-sam", "Send me the link", "s-1")
sweep.run(m)
nudges = []
for i in range(4):
    they_write("thr-sam", "I just followed you!", f"s-n{i}")
    n = len(INBOX.sent)
    sweep.run(m)
    nudges.append(INBOX.sent[n:])
    if i == 0:
        they_write("thr-sam", "done", f"s-n{i}b")
        k = len(INBOX.sent)
        sweep.run(m)
        ok("...at most once an hour", len(INBOX.sent) == k)
    lm.note(lead_of("c-3")["id"], nudged_at=ago(hours=2))          # an hour passes
ok("Instagram says they don't follow: a nudge, never the guide", len(nudges[0]) == 1
   and "example.com/guides" not in nudges[0][0]["text"], nudges[0])
ok("exactly three nudges, then their next reply gets the guide anyway (the soft gate's way out)",
   [len(x) for x in nudges[:3]] == [1, 1, 1] and int(lead_of("c-3")["nudges"]) == 3
   and any("example.com/guides" in x["text"] for x in nudges[3]) and lead_of("c-3")["stage"] == "email",
   ([len(x) for x in nudges], lead_of("c-3")))

# ── off the script, in every stage: a person gets it, with what they wrote ─────────────────────────────────
print("off the script")
comment("c-4", "GUIDE", user="u4", handle="max", hours=0.01)
sweep.run(m)
thread("thr-max", "u4", "max")
they_write("thr-max", "How much is a facial?", "x-1")
n = len(INBOX.sent)
sweep.run(m)
ok("asked stage: a question instead of the tap goes to a person, with what they asked, and no follow ask",
   lead_of("c-4")["stage"] == "stopped" and "How much is a facial?" in released_with("thr-max") and len(INBOX.sent) == n,
   (lead_of("c-4"), released_with("thr-max")))
walk("c-f", "u11", "fay", "thr-fay", to="follow")
they_write("thr-fay", "can I get it as a pdf instead", "f-x")
sweep.run(m)
ok("follow stage: anything but 'I followed' goes to a person", lead_of("c-f")["stage"] == "stopped"
   and "as a pdf instead" in released_with("thr-fay"), lead_of("c-f"))
walk("c-e", "u12", "eve", "thr-eve", to="email")
they_write("thr-eve", "I'd rather not give my email", "e-x")
sweep.run(m)
ok("email stage: a reply without an address goes to a person (no lead left waiting forever)",
   lead_of("c-e")["stage"] == "stopped" and "rather not" in released_with("thr-eve"), lead_of("c-e"))
walk("c-k", "u13", "kai", "thr-kai", to="confirm")
they_write("thr-kai", "why do you need it", "k-x")
sweep.run(m)
ok("confirm stage: anything but yes or an address goes to a person", lead_of("c-k")["stage"] == "stopped"
   and "why do you need it" in released_with("thr-kai") and not any(x["to"] == "kai@example.com" for x in MAIL),
   lead_of("c-k"))
walk("c-t", "u22", "tia", "thr-tia", to="email")
they_write("thr-tia", "here you go:", "t-1")
they_write("thr-tia", "tia@example.com", "t-2")
sweep.run(m)
ok("...but a few words and then the address, in one turn, is the answer", lead_of("c-t")["stage"] == "confirm"
   and lead_of("c-t")["email"] == "tia@example.com" and lead_of("c-t")["email_message"] == "t-2", lead_of("c-t"))
walk("c-s", "u14", "sue", "thr-sue", to="email")
they_write("thr-sue", "STOP", "st-x")
sweep.run(m)
ok("a STOP in any stage ends the lead and hands the conversation back at once", lead_of("c-s")["stage"] == "stopped"
   and "STOP" in lead_of("c-s")["note"] and "STOP" in released_with("thr-sue"), lead_of("c-s"))

# ── no lead stays open forever ──────────────────────────────────────────────────────────────────────────────
print("timeouts")
walk("c-q", "u15", "quinn", "thr-quinn", to="email")
with state.connect() as c:
    c.execute("UPDATE inbox_messages SET created_at = ? WHERE zernio_conversation_id = 'thr-quinn'", (ago(hours=30),))
lm.note(lead_of("c-q")["id"], stage_at=ago(hours=30))
sweep.run(m)
ok("a stage that waits a day hands the conversation back, with a note", lead_of("c-q")["stage"] == "stopped"
   and "no reply" in released_with("thr-quinn"), (lead_of("c-q"), released_with("thr-quinn")))
comment("c-gh", "GUIDE", user="u16", handle="ghost", hours=0.01)
sweep.run(m)
lm.note(lead_of("c-gh")["id"], answered_at=ago(days=8))
sweep.run(m)
ok("a private reply nobody answers in a week ends the lead", lead_of("c-gh")["stage"] == "stopped"
   and "7 days" in lead_of("c-gh")["note"], lead_of("c-gh"))
walk("c-w", "u17", "wes", "thr-wes", to="follow")
they_write("thr-wes", "I just followed you!", "w-2")
store.upsert_conversation(space=SPACE, zcid="thr-wes", platform="instagram", participant="Wes",
                          last_inbound_at=ago(hours=30), account_id="ig-acct")
with state.connect() as c:
    c.execute("UPDATE inbox_conversations SET last_inbound_at = ? WHERE zernio_conversation_id = 'thr-wes'",
              (ago(hours=30),))
sweep.run(m)
ok("a closed window ends it and hands the conversation back, never holds it", lead_of("c-w")["stage"] == "stopped"
   and claims.holder(SPACE, "thr-wes") is None and "window" in released_with("thr-wes"), (lead_of("c-w"),))

# ── wait or stop, by the refusal's code ─────────────────────────────────────────────────────────────────────
real_send = m.send_dm
lead = walk("c-r", "u18", "rae", "thr-rae", to="follow")
m.send_dm = lambda *a, **k: {"status": "refused", "message_id": None, "reason": "reworded: too busy", "code": "hourly_cap"}
ok("an hourly-cap refusal waits, whatever its words say", sweep._send(m, lead, "t1", "x") == "wait"
   and lead_of("c-r")["stage"] == "follow")
m.send_dm = lambda *a, **k: {"status": "refused", "message_id": None, "reason": "this hour, window", "code": "opted_out"}
ok("...and an opted-out refusal stops, even worded like a wait", sweep._send(m, lead, "t2", "x") == "stopped"
   and lead_of("c-r")["stage"] == "stopped" and claims.holder(SPACE, "thr-rae") is None)
m.send_dm = real_send
r = m.send_dm("thr-rae", "hello", key="no-claim")
ok("the inbox's refusals carry a code", r["status"] == "refused" and r.get("code") == "not_claimed", r)

# ── a person takes over ─────────────────────────────────────────────────────────────────────────────────────
print("a person takes over")
comment("c-5", "guide", user="u5", handle="ana", hours=0.01)
sweep.run(m)
thread("thr-ana", "u5", "ana")
they_write("thr-ana", "Send me the link", "a-1")
sweep.run(m)
U = state.add_user("owner@example.com", name="Owner")["id"]
reply.send_reply(space=SPACE, zcid="thr-ana", text="Hi Ana, I'll send it myself", user_id=U, nonce="own-1")
they_write("thr-ana", "I just followed you!", "a-2")
n = len(INBOX.sent)
sweep.run(m)
ok("a person replying takes it over: the machine stops and sends nothing more",
   lead_of("c-5")["stage"] == "stopped" and len(INBOX.sent) == n, lead_of("c-5"))
store.remember_participant(SPACE, "thr-ana", ["u5", "ana"])     # what the inbox's poller records as it reads
import time  # noqa: E402
time.sleep(1.1)        # a claim is stamped to the second; her new start comes after the owner's reply, as it would live
comment("c-5b", "GUIDE again please", user="u5", handle="ana", hours=0)
n = len(INBOX.sent)
sweep.run(m)
they_write("thr-ana", "Send me the link", "a-3")
sweep.run(m)
ok("...until she comments the keyword AGAIN: her new comment (read by the box itself) is the new start",
   lead_of("c-5b").get("stage") in ("follow", "email") and (claims.holder(SPACE, "thr-ana") or {}).get("title")
   == "Lead Magnet" and len(INBOX.sent) > n, lead_of("c-5b"))

store.upsert_conversation(space=SPACE, zcid="thr-zoe", platform="instagram", participant="Zoe",
                          last_inbound_at=ago(hours=1), account_id="ig-acct")
with state.connect() as c:
    c.execute("UPDATE inbox_conversations SET opted_out = 1 WHERE zernio_conversation_id = 'thr-zoe'")
thread("thr-zoe", "u6", "zoe")
comment("c-6", "GUIDE", user="u6", handle="zoe", hours=0.01)
n = len(COMMENTS.replies)
sweep.run(m)
ok("someone who said STOP gets nothing, and the lead says why", len(COMMENTS.replies) == n
   and lead_of("c-6")["stage"] == "stopped" and "opted out" in lead_of("c-6")["note"], lead_of("c-6"))

lead = walk("c-x", "u19", "xia", "thr-xia", to="confirm")
lm.note(lead["id"], email_message="not-her-message")
they_write("thr-xia", "yes", "xi-y")
sweep.run(m)
ok("a refused welcome email ends the lead and leaves the reason on the conversation it lets go",
   lead_of("c-x")["stage"] == "stopped" and "welcome email" in released_with("thr-xia"),
   (lead_of("c-x"), released_with("thr-xia")))

# ── the owner's controls ────────────────────────────────────────────────────────────────────────────────────
print("the owner's controls")
from core import panels  # noqa: E402

cardhtml = panels.render("inbox")
ok("the Inbox card says it's on, its keywords and what it has done", "Lead magnets" in cardhtml
   and "GUIDE" in cardhtml and "/my/lead-magnet" in cardhtml, cardhtml[:300])
client = app.test_client()
client.post("/dash/login", data={"token": box.dash_token})
page = client.get("/my/lead-magnet")
body = page.get_data(as_text=True)
ok("its page shows the switch and each keyword's results", page.status_code == 200 and "Turn it off" in body
   and "The Second Brain" in body and "comments answered" in body, page.status_code)
r = client.post("/my/lead-magnet", data={"do": "keyword", "keyword": "SOP", "title": "The SOP", "url": "http://x"})
ok("a guide link that isn't https is refused, in a sentence", "https" in r.headers.get("Location", "")
   and mod.settings.keyword(m, "SOP") is None, r.headers.get("Location"))
client.post("/my/lead-magnet", data={"do": "keyword", "keyword": "SOP", "title": "The SOP",
                                     "url": "https://example.com/sop"})
ok("a keyword with its guide is saved, live, and stamped with when it went live",
   (mod.settings.keyword(m, "sop") or {}).get("on") is True and bool((mod.settings.keyword(m, "sop") or {}).get("on_at")))
client.post("/my/lead-magnet/messages", data={**mod.settings.COPY, "email_ask": "Your email? I'll send the next one.",
                                              "subject": "Here it is", "body": "Enjoy {guide}: {url}"})
ok("the messages page saves the owner's own words", mod.settings.copy(m, "email_ask") == "Your email? I'll send the next one."
   and mod.settings.welcome(m)["subject"] == "Here it is")
held = walk("c-h", "u20", "hal", "thr-hal", to="follow")
ok("(a conversation it holds)", (claims.holder(SPACE, "thr-hal") or {}).get("title") == "Lead Magnet")
r = client.post("/my/lead-magnet", data={"do": "switch", "on": "0"})
ok("turning it off lets go of every conversation it holds, at once, and says so",
   claims.holder(SPACE, "thr-hal") is None and lead_of("c-h")["stage"] == "stopped"
   and "turned off" in released_with("thr-hal") and "yours again" in r.headers.get("Location", "").replace("%20", " "),
   (lead_of("c-h"), r.headers.get("Location")))
n = len(COMMENTS.replies)
comment("c-9", "SOP please", user="u9", handle="ivy", hours=0)
sweep.run(m)
ok("turned off, nothing is sent", not mod.settings.on(m) and len(COMMENTS.replies) == n)
# THE BOX'S DAY, which is the day the Morning Review hands a reporter and the day Lead Magnet counts (through
# m.day_window). `date.today()` was the Mac's day, and the old count was by UTC date: red every evening.
from core import box_settings as _bs, report as _report  # noqa: E402

rev = mod.morning(_report.today())
ok("the Morning Review says what happened today, counting only confirmed emails",
   rev.get("headline", {}).get("value") == 2 and "comments answered" in rev["happened"][0]["text"], rev)

# THE OWNER'S DAY, NOT A UTC DATE (WebDev2's walk, OSDev1 10-07): a capture at 18:30 Pacific is 01:30 UTC the next
# day, and counted by its UTC date it landed in tomorrow's review. Every stamp moved to 18:30 PT on Oct 6, on a
# Pacific box: Oct 6's review counts them, Oct 7's is quiet. On a UTC box the same stamps are Oct 7's.
with mod.store.connect() as _c:
    _c.execute("UPDATE leads SET updated_at = ?, answered_at = CASE WHEN answered_at IS NULL THEN NULL ELSE ? END",
               ("2026-10-07T01:30:00.000000+00:00", "2026-10-07T01:30:00.000000+00:00"))
_bs.put(_report.TZ_NS, _report.TZ_KEY, "America/Los_Angeles")
_eve, _next = mod.morning(date(2026, 10, 6)), mod.morning(date(2026, 10, 7))
ok("an 18:30 Pacific capture lands in that day's Morning Review on a Pacific box, and not in the next",
   (_eve.get("headline") or {}).get("value", 0) >= 1 and _next == {}, (_eve, _next))
_bs.put(_report.TZ_NS, _report.TZ_KEY, "UTC")
ok("...while on a UTC box the same stamps are the next day's (the window follows the box's zone)",
   mod.morning(date(2026, 10, 6)) == {} and (mod.morning(date(2026, 10, 7)).get("headline") or {}).get("value", 0) >= 1,
   (_report.tz_name(), mod.morning(date(2026, 10, 6)), mod.morning(date(2026, 10, 7))))
_bs.put(_report.TZ_NS, _report.TZ_KEY, "")

# ── built on the promise ────────────────────────────────────────────────────────────────────────────────────
from core import sdk_check  # noqa: E402

ok("ownbox check: built only on what the box promises", sdk_check.check(ROOT / "my" / "machines" / "lead-magnet") == [],
   sdk_check.check(ROOT / "my" / "machines" / "lead-magnet"))
ok("its records live in its own folder, never the box's database",
   (T / "machines" / "lead-magnet" / "data" / "lead_magnet.db").is_file())

print("ALL LEAD MAGNET CHECKS PASS" if not _failed else f"{_failed} LEAD MAGNET CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
