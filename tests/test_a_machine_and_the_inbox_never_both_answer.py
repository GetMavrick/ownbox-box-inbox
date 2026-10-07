"""A machine and the inbox never both answer one person (owner, 2026-10-07).

Owner, 2026-10-07: "all those replies from customers are gonna come into the unified inbox machine and it's gonna draft
responses. And if we set it to automatic, then there's gonna be duplicate double responses and it'll be a mess ... If I
create a custom machine called the lead magnet machine ... how are they gonna play nice together".

THE ANSWER IS A CLAIM, AND NOW A RESERVATION BEFORE IT (customer_voice/claims.py). A machine that runs a conversation
claims it, and while it does the inbox neither drafts it, nor answers it on its own, nor counts it as waiting. The gap
was the first reply: the machine's private reply to a comment starts a conversation the inbox hasn't seen; the person
writes back; the inbox reads it before the machine's next 20-second sweep claims it, and with replies on their own
switched on, both would answer. So the private reply RESERVES the person, by their id and @handle, from the moment it
goes.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * the person's reply to a machine's private reply is drafted, answered on its own, or counted as waiting before the
    machine claims it;
  * once claimed, the inbox speaks on it; once the machine lets it go, the inbox stays silent;
  * a person who answers first is talked over by the machine afterwards;
  * a reservation outlives its half hour (OSDev1's review: a dead sweep must not silence everyone the machine wrote
    to for a week), lapses without saying so in the log, or holds back anyone the machine never wrote to.

No network: the vendor is stood in for.

Run: python tests/test_a_machine_and_the_inbox_never_both_answer.py
"""
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
T = pathlib.Path(tempfile.mkdtemp(prefix="both_"))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")
os.environ["AIOS_MY_MACHINES"] = str(T / "machines")
os.environ.setdefault("DASH_TOKEN", "test-dash-pw")
os.environ.pop("ZERNIO_API_KEY", None)

from core.vendors import zernio  # noqa: E402

NOW = datetime.now(timezone.utc)


class FakeInbox:
    def __init__(self):
        self.sent = []

    def list(self, *, status="active", cursor=None, limit=50, platform=None):
        return {"conversations": []}            # nobody on the platform's list said STOP

    def send(self, cid, account_id, text, *, tag=None, quick_replies=None, buttons=None):
        self.sent.append((cid, text))
        return {"message_id": f"out-{cid}-{len(self.sent)}"}


class FakeComments:
    def __init__(self):
        self.replies = []

    def send_private_reply(self, post_id, comment_id, account_id, message, *, quick_replies=None):
        self.replies.append((comment_id, message))
        return {"message_id": f"pr-{len(self.replies)}"}


class FakeAccounts:
    def discover(self):
        return {"instagram": "ig-acct"}

    def identities(self):
        return {"instagram": {"ig-self", "myshop"}}


INBOX, COMMENTS = FakeInbox(), FakeComments()
zernio.client = lambda space: type("S", (), {"inbox": INBOX, "comments": COMMENTS, "accounts": FakeAccounts()})()
zernio.is_configured = lambda key: bool(key)

from core import cost_guard, spaces as _spaces, state  # noqa: E402

state.init_db()
cost_guard.check_vendor = lambda *a, **k: None
_spaces.space_by_name = lambda name, allow_default_alias=True: {"name": name, "key": "k"}
_spaces.all_spaces = lambda: [{"name": _spaces.DEFAULT, "zernio_key": "k"}]
SPACE = _spaces.DEFAULT

import marketing.customer_voice  # noqa: E402,F401 — registers the machine's tables and the provider
from marketing.customer_voice import claims  # noqa: E402
from marketing.customer_voice.drafter import store as drafts  # noqa: E402
from marketing.customer_voice.inbox import autosend, reply, sending, store  # noqa: E402
from core import sdk  # noqa: E402

state.init_db()
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {str(detail)[:400]}" if not cond and detail else ""))
    if not cond:
        _failed += 1


m = sdk.machine("lead-magnet")
from core import box_settings  # noqa: E402
from marketing.customer_voice.inbox import conversations as _conv  # noqa: E402
box_settings.put(_conv._FILLED_NS, _conv._filled_key(SPACE), True, set_by="test")   # its past DMs already read
sending.put(auto_reply={"instagram": "on"})       # the owner's replies on their own, switched on before any of this
n = [0]


def comment(cid, uid, handle):
    return {"id": cid, "post": "post-1", "account": "ig-acct", "space": SPACE, "text": "GUIDE",
            "author": {"id": uid, "username": handle, "name": handle.title()},
            "at": (NOW - timedelta(minutes=5)).isoformat()}


def writes_back(zcid, idents, said="Yes please! Send it over", draft="Here you go! What are you working on?"):
    """The person writes back on the DM the private reply started: the poll mirrors it, remembers who it is, and
    the drafter writes its reply, exactly as the inbox does for every message."""
    n[0] += 1
    zmid = f"in-{zcid}-{n[0]}"
    at = datetime.now(timezone.utc).isoformat()
    store.upsert_conversation(space=SPACE, zcid=zcid, platform="instagram", participant=idents[-1], last_inbound_at=at,
                              account_id="ig-acct")
    store.record_message(space=SPACE, zcid=zcid, zmid=zmid, direction="in", sent_by="contact", body=said, sent_at=at)
    store.remember_participant(SPACE, zcid, idents)
    drafts.put(space=SPACE, zcid=zcid, in_reply_to=zmid, body=draft)
    return zmid


def inbox_would_draft(zcid):
    return zcid in {r["zcid"] for r in drafts.needs_a_draft(SPACE, limit=50)}


def waiting(zcid):
    return zcid in {r["zernio_conversation_id"] for r in store.list_conversations(SPACE, waiting=True)}


print("test_the_first_reply_is_the_machines")
r = m.reply_to_comment(comment("c-dana", "u1", "dana.r"), "Here's the guide! Reply YES and I'll send it.", key="answer")
ok("the machine answers the comment with a private reply", r["status"] == "sent" and COMMENTS.replies, r)
writes_back("dm-dana", ["u1", "dana.r"])
INBOX.sent.clear()
out = autosend.tick()
ok("THE RACE: she writes back before the machine's sweep, and the inbox does NOT answer her on its own",
   not INBOX.sent, (out, INBOX.sent))
ok("...nor offers a draft for it, nor counts her as waiting on you",
   not inbox_would_draft("dm-dana") and not waiting("dm-dana"))
ok("...because the conversation is reserved for the machine that started it",
   (claims.reserved(SPACE, "dm-dana") or {}).get("machine") == m.key, claims.reserved(SPACE, "dm-dana"))

print("\ntest_the_machine_takes_it_and_the_inbox_stays_out")
ok("the machine's sweep claims it", m.claim("dm-dana", title="Lead Magnet"))
ok("...which settles the reservation: it is a claim now", claims.reserved(SPACE, "dm-dana") is None
   and (claims.holder(SPACE, "dm-dana") or {}).get("machine") == m.key)
writes_back("dm-dana", ["u1", "dana.r"], said="I just followed you!")
out = autosend.tick()
ok("while the machine runs it, her messages are the machine's alone", not INBOX.sent
   and not inbox_would_draft("dm-dana") and not waiting("dm-dana"), (out, INBOX.sent))
r = m.send_dm("dm-dana", "Thanks for the follow! Here's your guide: https://example.com/guide", key="deliver")
ok("...and the machine answers her, once", r["status"] == "sent" and len(INBOX.sent) == 1, (r, INBOX.sent))

print("\ntest_handed_back_it_is_the_inbox_again")
m.release("dm-dana")
INBOX.sent.clear()
writes_back("dm-dana", ["u1", "dana.r"], said="Got it, thanks! Do you do 1:1 coaching?",
            draft="Glad it helped! Yes, I do. What would you want help with first?")
out = autosend.tick()
ok("finished, the machine lets go, and her next message is answered by the inbox, once",
   INBOX.sent == [("dm-dana", "Glad it helped! Yes, I do. What would you want help with first?")], (out, INBOX.sent))

print("\ntest_a_person_who_answers_first_keeps_it")
m.reply_to_comment(comment("c-sam", "u2", "sam.k"), "Here's the guide! Reply YES.", key="answer")
writes_back("dm-sam", ["u2", "sam.k"])
store.record_message(space=SPACE, zcid="dm-sam", zmid="owner-1", direction="out", sent_by="human",
                     body="Hey Sam! Here it is.", sent_at=datetime.now(timezone.utc).isoformat())
claims.take_over(SPACE, "dm-sam")                  # what the owner's own reply does (reply.send_reply)
ok("the owner answered Sam himself before the machine's sweep: the machine never takes it now",
   not m.claim("dm-sam", title="Lead Magnet") and claims.holder(SPACE, "dm-sam") is None
   and claims.reserved(SPACE, "dm-sam") is None)

print("\ntest_only_who_the_machine_wrote_to_and_only_for_half_an_hour")
INBOX.sent.clear()
writes_back("dm-stranger", ["u3", "lee.p"], said="Do you have openings?", draft="I do! Tuesday or Thursday?")
autosend.tick()
ok("someone the machine never wrote to is answered as always", INBOX.sent == [("dm-stranger", "I do! Tuesday or Thursday?")],
   INBOX.sent)
m.reply_to_comment(comment("c-old", "u4", "old.one"), "Here's the guide!", key="answer")
with state.connect() as c:
    span = c.execute("SELECT MAX((julianday(expires_at) - julianday(created_at)) * 1440) FROM inbox_reservations "
                     "WHERE ident = 'u4'").fetchone()[0]
ok("a reservation lasts half an hour, not a week: only long enough to cover the machine's 20-second sweep",
   span is not None and 29.9 <= span <= 30.1, span)
with state.connect() as c:
    c.execute("UPDATE inbox_reservations SET expires_at = '2000-01-01T00:00:00' WHERE ident IN ('u4', 'old.one')")
INBOX.sent.clear()
writes_back("dm-old", ["u4", "old.one"], said="Hey, still have that guide?", draft="I do! Sending it now.")
autosend.tick()
ok("a reservation the machine never acted on lets go after its half hour",
   INBOX.sent == [("dm-old", "I do! Sending it now.")], INBOX.sent)
said = []
claims.log = type("L", (), {"warning": lambda self, ev, **kw: said.append((ev, kw)), "info": lambda self, *a, **k: None})()
gone = claims.expire_reservations()
ok("...and the lapse is said in the log, by machine, then cleared",
   gone == 2 and ("inbox.reservation_expired", {"space": SPACE, "machine": m.key, "idents": 2}) in said, (gone, said))
with state.connect() as c:
    ok("...leaving the reservations still running alone",
       c.execute("SELECT COUNT(*) FROM inbox_reservations WHERE ident IN ('u4', 'old.one')").fetchone()[0] == 0
       and claims.expire_reservations() == 0)

print("\nALL NEVER-BOTH CHECKS PASS" if not _failed else f"\n{_failed} NEVER-BOTH CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
