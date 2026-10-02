"""Comments, private replies and buttons through the inbox (the Lead Magnet port's seams; OSDev1 approved
2026-10-02 on the wall: "Seams: APPROVED as proposed, as one PR before step 3").

What is measured, on the real store and the real send path, with Zernio stubbed:
  m.send_dm(buttons=, quick_replies=)
    * a link button and a quick reply reach the vendor in its own shape; titles are cut at 20 characters
    * a button without an https link, or too many, is refused before anything is sent
  m.comments()
    * reads the posts with comment activity, then each post's own comments (never a post as a comment), and
      returns each with its post, account, Space, author and time
  m.reply_to_comment(comment, text, key=, quick_replies=)
    * sends the private reply with its quick reply, and is ONE per comment across every machine and key
    * refuses: an opted-out commenter, a stopped box, a comment over 7 days old or with no time, the hourly
      cap, and a comment not passed back as m.comments returned it
    * a reply that may have landed is never sent again
  m.conversation_for(comment): the commenter's DM thread, by id or @handle (never a display name), or None
  m.follows_you(conversation): True / False / None when the platform didn't say
  the seam itself: a provider lacking the new calls is refused, and the calls are promised in sdk.SEAMS
  OSDev1's review of #1789: a STOP is found by the commenter's id or @handle (his "Jane Doe" probe), a string or a
  dict is never iterated as a list, and the three reads never raise (per Space)

Run: python tests/test_conversation_seams.py
"""
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
T = pathlib.Path(tempfile.mkdtemp(prefix="seams_"))
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

    threads = [
        {"id": "thr-dana", "participants": [{"id": "U1", "username": "@Dana.R", "instagramProfile": {"isFollower": True}}]},
        {"id": "thr-sam", "participants": [{"id": "u2", "username": "sam"}]},
        {"id": "thr-lee", "participants": [{"id": "u3", "username": "lee", "instagramProfile": {"isFollower": False}}]},
    ]

    pages = None                       # when set: the platform's list, one page per entry (50 a page, live)
    archived: list = []                # conversations the platform lists only under status="archived"

    def list(self, *, status="active", cursor=None, limit=50, platform=None):
        if status == "archived":
            return {"conversations": self.archived}
        if self.pages is None:
            return {"conversations": self.threads}
        i = int(cursor or 0)
        return {"conversations": self.pages[i], "next_cursor": str(i + 1) if i + 1 < len(self.pages) else None}

    def messages(self, cid, account_id, *, limit=50):
        return {"messages": []}

    def send(self, cid, account_id, text, *, tag=None, quick_replies=None, buttons=None):
        self.sent.append({"cid": cid, "text": text, "quick_replies": quick_replies, "buttons": buttons})
        return {"message_id": f"out-{len(self.sent)}"}


class FakeComments:
    def __init__(self):
        self.replies = []
        self.fail = None
        self.posts = [{"platformPostId": "post-1", "commentCount": 2}, {"platformPostId": "post-2", "commentCount": 0}]
        self.by_post = {"post-1": [
            {"id": "c-1", "text": "GUIDE please", "from": {"id": "u1", "username": "dana.r", "name": "Dana"},
             "createdAt": (NOW - timedelta(hours=2)).isoformat()},
            {"id": "c-2", "text": "nice", "from": {"id": "u2", "username": "sam"},
             "createdAt": (NOW - timedelta(days=9)).isoformat()},
            # the account's own reply and a reply under a comment: conversation, never a person asking
            {"id": "c-own", "text": "Check your DMs!", "from": {"id": "ig-self", "username": "MyShop"},
             "createdAt": (NOW - timedelta(hours=1)).isoformat()},
            {"id": "c-sub", "text": "same", "isReply": True, "from": {"id": "u8", "username": "kim"},
             "createdAt": (NOW - timedelta(hours=1)).isoformat()},
        ]}
        self.post_fetches = []

    def list(self, account_id, *, since=None, limit=50, cursor=None):
        return {"items": self.posts}

    def list_post_comments(self, post_id, account_id, *, limit=50, cursor=None):
        self.post_fetches.append(post_id)
        return {"items": self.by_post.get(post_id, [])}

    def send_private_reply(self, post_id, comment_id, account_id, message, *, quick_replies=None):
        if self.fail:
            raise self.fail
        self.replies.append({"post": post_id, "comment": comment_id, "account": account_id, "text": message,
                             "quick_replies": quick_replies})
        return {"message_id": f"pr-{len(self.replies)}"}


class FakeAccounts:
    def discover(self):
        return {"instagram": "ig-acct"}

    def identities(self):
        return {"instagram": {"ig-self", "myshop"}}


INBOX, COMMENTS = FakeInbox(), FakeComments()
zernio.client = lambda space: type("S", (), {"inbox": INBOX, "comments": COMMENTS, "accounts": FakeAccounts()})()
zernio.is_configured = lambda key: bool(key)

from core import spaces as _spaces, state  # noqa: E402

state.init_db()
_spaces.space_by_name = lambda name, allow_default_alias=True: {"name": name, "key": "k"}
_spaces.all_spaces = lambda: [{"name": _spaces.DEFAULT, "zernio_key": "k"}]
SPACE = _spaces.DEFAULT

import marketing.customer_voice  # noqa: E402,F401 — registers the machine's tables and the provider
from marketing.customer_voice import claims  # noqa: E402
from marketing.customer_voice.inbox import reply, store  # noqa: E402
from core import conversations, pause, sdk  # noqa: E402
from core.config import get_config  # noqa: E402
from marketing.customer_voice.inbox import conversations as _conv  # noqa: E402

state.init_db()
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


m = sdk.machine("lead-magnet")

# ── send_dm with buttons and quick replies ───────────────────────────────────────────────────────────────────
print("m.send_dm(buttons=, quick_replies=)")
at = (NOW - timedelta(hours=1)).isoformat()
store.upsert_conversation(space=SPACE, zcid="d1", platform="instagram", participant="Dana", last_inbound_at=at,
                          account_id="ig-acct")
store.record_message(space=SPACE, zcid="d1", zmid="in-d1", direction="in", sent_by="contact", body="hi", sent_at=at)
m.claim("d1", title="Lead Magnet")
r = m.send_dm("d1", "Here it is", key="deliver",
              buttons=[{"title": "Get the guide right now please", "url": "https://example.com/guide"}],
              quick_replies=["I just followed you!"])
sent = INBOX.sent[-1] if INBOX.sent else {}
ok("a link button and a quick reply reach the vendor in its own shape", r["status"] == "sent"
   and sent.get("buttons") == [{"type": "url", "title": "Get the guide right", "url": "https://example.com/guide"}]
   and sent.get("quick_replies") == [{"content_type": "text", "title": "I just followed you!",
                                      "payload": "I just followed you!"}], (r, sent))
n = len(INBOX.sent)
r = m.send_dm("d1", "x", key="bad-button", buttons=[{"title": "Go", "url": "http://plain.example"}])
ok("a button without an https link is refused, and nothing is sent", r["status"] == "refused"
   and len(INBOX.sent) == n, r)
r = m.send_dm("d1", "x", key="many", buttons=[{"title": str(i), "url": "https://a.example"} for i in range(4)])
ok("more than 3 buttons is refused", r["status"] == "refused" and "3 buttons" in r["reason"], r)
r = m.send_dm("d1", "Plain words", key="plain")
ok("a message with neither still sends as before", r["status"] == "sent" and INBOX.sent[-1]["buttons"] is None
   and INBOX.sent[-1]["quick_replies"] is None, INBOX.sent[-1])

# ── comments ─────────────────────────────────────────────────────────────────────────────────────────────────
print("m.comments()")
got = m.comments()
ok("the posts first, then each active post's own comments (a post with none is not fetched)",
   COMMENTS.post_fetches == ["post-1"] and [c["id"] for c in got] == ["c-1", "c-2"], (COMMENTS.post_fetches, got))
c1 = got[0]
ok("each comment carries its post, account, Space, author and time",
   c1["post"] == "post-1" and c1["account"] == "ig-acct" and c1["space"] == SPACE
   and c1["author"]["username"] == "dana.r" and c1["text"] == "GUIDE please" and c1["at"], c1)
ok("one post's comments when asked", [c["id"] for c in m.comments(post="post-1")] == ["c-1", "c-2"])
ok("the account's own comments and replies under a comment are left out",
   not {"c-own", "c-sub"} & {c["id"] for c in got}, [c["id"] for c in got])
ok("since= keeps only newer comments (compared as times)",
   [c["id"] for c in m.comments(since=(NOW - timedelta(days=1)).isoformat().replace("+00:00", "Z"))] == ["c-1"])

# ── reply_to_comment ─────────────────────────────────────────────────────────────────────────────────────────
print("m.reply_to_comment()")
from marketing.customer_voice.inbox import conversations as _fillmod  # noqa: E402
_r0 = m.reply_to_comment(c1, "Too early", key="dm0")
ok("BEFORE THE BOX HAS READ EVERY PAST CONVERSATION, a stranger gets no private reply: it waits, it doesn't guess",
   _r0["status"] == "refused" and "still reading" in _r0["reason"] and not COMMENTS.replies, _r0)
ok("the one-time fill reads every conversation, active and archived, and marks the Space done",
   _fillmod.fill_participant_ids() == {SPACE: len(INBOX.threads)} and _fillmod.filled(SPACE)
   and _fillmod.fill_participant_ids() == {})
r = m.reply_to_comment(c1, "Want the guide? Tap below.", key="dm1", quick_replies=["Send me the link"])
pr = COMMENTS.replies[-1] if COMMENTS.replies else {}
ok("the private reply goes to that comment, with its quick reply", r["status"] == "sent"
   and pr.get("comment") == "c-1" and pr.get("post") == "post-1" and pr.get("account") == "ig-acct"
   and pr.get("quick_replies") == [{"content_type": "text", "title": "Send me the link",
                                    "payload": "Send me the link"}], (r, pr))
r2 = m.reply_to_comment(c1, "Again", key="dm1")
r3 = sdk.machine("other-one").reply_to_comment(c1, "Me too", key="another-step")
ok("ONE private reply per comment, whatever the key or the machine", r2["status"] == "duplicate"
   and r3["status"] == "duplicate" and len(COMMENTS.replies) == 1, (r2, r3))

old = got[1]
r = m.reply_to_comment(old, "Late", key="dm1")
ok("a comment over 7 days old is refused (Instagram's window)", r["status"] == "refused" and "7 days" in r["reason"]
   and len(COMMENTS.replies) == 1, r)
r = m.reply_to_comment({**c1, "id": "c-9", "at": ""}, "No time", key="dm1")
ok("a comment with no time is refused, not guessed", r["status"] == "refused" and "time" in r["reason"], r)
r = m.reply_to_comment({"id": "c-10", "text": "hand-made"}, "Hi", key="dm1")
ok("a comment not passed back as m.comments returned it is refused", r["status"] == "refused", r)

store.upsert_conversation(space=SPACE, zcid="stopper", platform="instagram", participant="@Sam.K",
                          last_inbound_at=at, account_id="ig-acct")
with state.connect() as c:
    c.execute("UPDATE inbox_conversations SET opted_out = 1 WHERE zernio_conversation_id = 'stopper'")
r = m.reply_to_comment({**c1, "id": "c-11", "author": {"id": "u9", "username": "sam.k", "name": "Sam"}}, "Hi",
                       key="dm1")
ok("a commenter who opted out on this box is never written to (matched on their handle)",
   r["status"] == "refused" and "opted out" in r["reason"], r)
store.upsert_conversation(space=SPACE, zcid="harbor", platform="instagram", participant="Harbor Light Studio",
                          last_inbound_at=at, account_id="ig-acct")
with state.connect() as c:
    c.execute("UPDATE inbox_conversations SET opted_out = 1 WHERE zernio_conversation_id = 'harbor'")
r = m.reply_to_comment({**c1, "id": "c-15", "author": {"id": "u7", "username": "harborlight", "name": "Harbor Light Studio"}},
                       "Hi", key="dm1")
ok("...or on their display name, the shape a live box's inbox actually stores",
   r["status"] == "refused" and "opted out" in r["reason"], r)

_paused = pause.is_paused
pause.is_paused = lambda: True
r = m.reply_to_comment({**c1, "id": "c-12"}, "Hi", key="dm1")
pause.is_paused = _paused
ok("nothing is sent while the box is stopped", r["status"] == "refused" and "stopped" in r["reason"], r)

_cfg = dict(get_config().get("inbox") or {})
get_config().setdefault("inbox", {})["hourly_send_cap"] = store.sends_last_hour(SPACE)
r = m.reply_to_comment({**c1, "id": "c-13"}, "Hi", key="dm1")
get_config()["inbox"] = _cfg
ok("the hourly cap every send shares is respected", r["status"] == "refused" and "this hour" in r["reason"], r)

COMMENTS.fail = zernio.ZernioError("gateway timeout after send", indeterminate=True)
r = m.reply_to_comment({**c1, "id": "c-14"}, "Hi", key="dm1")
COMMENTS.fail = None
r2 = m.reply_to_comment({**c1, "id": "c-14"}, "Hi", key="dm1")
ok("a reply that may have landed is 'unknown', and never sent again", r["status"] == "unknown"
   and r2["status"] == "unknown" and not any(x["comment"] == "c-14" for x in COMMENTS.replies), (r, r2))

# ── who a commenter is, and whether they follow ──────────────────────────────────────────────────────────────
print("m.conversation_for() and m.follows_you()")
ok("a commenter's DM conversation is found by their id or @handle, any case",
   m.conversation_for(c1) == "thr-dana", m.conversation_for(c1))
ok("no conversation yet is None", m.conversation_for({**c1, "author": {"id": "u404", "username": "nobody"}}) is None)
ok("a display name alone never matches anyone (two people can share one)",
   m.conversation_for({**c1, "author": {"name": "Dana"}}) is None)
ok("follows: True, False, or None when Instagram didn't say",
   (m.follows_you("thr-dana"), m.follows_you("thr-lee"), m.follows_you("thr-sam"), m.follows_you("thr-gone"))
   == (True, False, None, None))

# ── OSDev1's review of #1789: a STOP found by who the person is, input shapes, reads that never raise ──────────
print("review of #1789")
# 1. His probe: "Jane Doe" opted out (the inbox row keeps her display name); her comment carries only her @handle.
INBOX.threads.append({"id": "thr-jane", "participants": [{"id": "u55", "username": "janedoe"}]})
store.upsert_conversation(space=SPACE, zcid="thr-jane", platform="instagram", participant="Jane Doe",
                          last_inbound_at=at, account_id="ig-acct")
with state.connect() as c:
    c.execute("UPDATE inbox_conversations SET opted_out = 1 WHERE zernio_conversation_id = 'thr-jane'")
n = len(COMMENTS.replies)
r = m.reply_to_comment({**c1, "id": "c-jane", "author": {"username": "janedoe", "name": ""}}, "Hi", key="dm1")
ok("a person who said STOP gets no private reply when her comment carries only her @handle",
   r["status"] == "refused" and "opted out" in r["reason"] and len(COMMENTS.replies) == n, r)
_list = FakeInbox.list
FakeInbox.list = lambda self, **k: (_ for _ in ()).throw(zernio.ZernioError("503 from the inbox list"))
r = m.reply_to_comment({**c1, "id": "c-unsure"}, "Hi", key="dm1")
ok("...and if the box can't look up her conversation, nothing is sent", r["status"] == "refused"
   and "STOP" in r["reason"] and len(COMMENTS.replies) == n, r)
ok("the reads say None instead of raising while the inbox list is down",
   m.conversation_for(c1) is None and m.follows_you("thr-dana") is None)
FakeInbox.list = _list

# 1b. His page-2 probe: the STOP holds however busy the inbox is.
from marketing.customer_voice.inbox import poller as _poller  # noqa: E402
filler = [{"id": f"thr-f{i}", "participants": [{"id": f"f{i}", "username": f"filler{i}"}]} for i in range(50)]
store.upsert_conversation(space=SPACE, zcid="thr-p2", platform="instagram", participant="Page Two",
                          last_inbound_at=at, account_id="ig-acct")
with state.connect() as c:
    c.execute("UPDATE inbox_conversations SET opted_out = 1 WHERE zernio_conversation_id = 'thr-p2'")
INBOX.pages = [filler, [{"id": "thr-p2", "participants": [{"id": "u77", "username": "pagetwo"}]}]]
n = len(COMMENTS.replies)
r = m.reply_to_comment({**c1, "id": "c-p2", "author": {"id": "u77", "username": "pagetwo", "name": ""}}, "Hi", key="dm1")
ok("A STOP ON PAGE 2 OF THE INBOX HOLDS: every page is read", r["status"] == "refused" and "opted out" in r["reason"]
   and len(COMMENTS.replies) == n, r)
INBOX.pages = [filler] * (_conv.MAX_PAGES + 1)
r = m.reply_to_comment({**c1, "id": "c-p3", "author": {"id": "u88", "username": "nobody"}}, "Hi", key="dm1")
ok("...and a list longer than the box can read is refused, not half-read", r["status"] == "refused"
   and "STOP" in r["reason"] and len(COMMENTS.replies) == n, r)
INBOX.pages = None
_ch = type("Ch", (), {"key": "instagram"})()
_poller._sweep_channel({"name": SPACE, "zernio_key": "k"}, zernio.client({}), _ch,
                       {"conversations": [{"id": "thr-p2", "accountId": "ig-acct",
                                           "participants": [{"id": "u77", "username": "@PageTwo"}]}]})
_list = FakeInbox.list
FakeInbox.list = lambda self, **k: (_ for _ in ()).throw(zernio.ZernioError("503"))
r = m.reply_to_comment({**c1, "id": "c-p4", "author": {"id": "", "username": "pagetwo", "name": ""}}, "Hi", key="dm1")
FakeInbox.list = _list
ok("...and once the inbox has read that conversation, the box answers from its own record, list or no list",
   r["status"] == "refused" and "opted out" in r["reason"] and len(COMMENTS.replies) == n, r)

# 1c. His archived probe: a STOP on a conversation archived on the platform and never polled since this shipped.
store.upsert_conversation(space=SPACE, zcid="thr-arch", platform="instagram", participant="Stone Studio",
                          last_inbound_at=at, account_id="ig-acct")
with state.connect() as c:
    c.execute("UPDATE inbox_conversations SET opted_out = 1 WHERE zernio_conversation_id = 'thr-arch'")
FakeInbox.archived = [{"id": "thr-arch", "participants": [{"id": "u99", "username": "archie"}]}]
n = len(COMMENTS.replies)
r = m.reply_to_comment({**c1, "id": "c-arch", "author": {"id": "u99", "username": "archie", "name": ""}}, "Hi", key="dm1")
ok("A STOP ON AN ARCHIVED, NEVER-POLLED CONVERSATION HOLDS: the lookup reads archived conversations too",
   r["status"] == "refused" and "opted out" in r["reason"] and len(COMMENTS.replies) == n, r)
from core import box_settings as _bs  # noqa: E402
_bs.put("inbox", _fillmod._filled_key(SPACE), False, set_by="test")
_fillmod.fill_participant_ids()
_list = FakeInbox.list
FakeInbox.list = lambda self, **k: (_ for _ in ()).throw(zernio.ZernioError("503"))
r = m.reply_to_comment({**c1, "id": "c-arch2", "author": {"id": "u99", "username": "", "name": ""}}, "Hi", key="dm1")
FakeInbox.list = _list
FakeInbox.archived = []
ok("...and once the fill has read it, the box's own record holds the STOP with the platform down",
   r["status"] == "refused" and "opted out" in r["reason"] and len(COMMENTS.replies) == n, r)

# 1d. OSDev1's re-review of #1790: a takeover is reopened only by a new comment the box can vouch for.
from marketing.customer_voice import claims as _cl  # noqa: E402
from marketing.customer_voice.inbox import reply as _reply  # noqa: E402
store.upsert_conversation(space=SPACE, zcid="thr-dana", platform="instagram", participant="Dana",
                          last_inbound_at=at, account_id="ig-acct")
store.record_message(space=SPACE, zcid="thr-dana", zmid="in-dana-1", direction="in", sent_by="contact", body="hi",
                     sent_at=at)
_fillmod.fill_participant_ids()
store.remember_participant(SPACE, "thr-dana", ["u1", "dana.r"])
m.claim("thr-dana", title="Lead Magnet")
_U = state.add_user("owner-seams@example.com", name="Owner")["id"]
_reply.send_reply(space=SPACE, zcid="thr-dana", text="I'll take it from here", user_id=_U, nonce="tk-1")
ok("the owner took Dana's conversation over", _cl.taken_over(SPACE, "thr-dana"))
ok("...a time is not a trigger", not m.claim("thr-dana", title="Lead Magnet", trigger={"id": NOW.isoformat()}))
ok("...a made-up comment id opens nothing", not m.claim("thr-dana", title="Lead Magnet", trigger={"id": "c-fake"}))
ok("...her comment from BEFORE the takeover opens nothing", not m.claim("thr-dana", title="Lead Magnet", trigger=c1))
_sam = next(c for c in m.comments() if c["id"] == "c-2")
ok("...someone else's comment opens nothing", not m.claim("thr-dana", title="Lead Magnet", trigger=_sam))
COMMENTS.by_post["post-1"].append({"id": "c-again", "text": "GUIDE again please", "from": {"id": "u1", "username": "dana.r"},
                                   "createdAt": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()})
COMMENTS.by_post["post-1"].append({"id": "c-sam-new", "text": "GUIDE", "from": {"id": "u2", "username": "sam"},
                                   "createdAt": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()})
_got = {c["id"]: c for c in m.comments()}
ok("...someone else's NEW comment opens nothing", not m.claim("thr-dana", title="Lead Magnet", trigger=_got["c-sam-new"]))
_new = _got["c-again"]
ok("...her NEW comment, which the box read itself, reopens it", m.claim("thr-dana", title="Lead Magnet", trigger=_new)
   and not _cl.taken_over(SPACE, "thr-dana"))
m.release("thr-dana")
COMMENTS.by_post["post-1"] = [x for x in COMMENTS.by_post["post-1"] if x["id"] not in ("c-again", "c-sam-new")]

# 2. Shapes that would reach a real person wrongly.
n = len(INBOX.sent)
r = m.send_dm("d1", "x", key="qs-string", quick_replies="Yes")
ok('quick_replies="Yes" is refused (never "Y", "e", "s"), with the fix named', r["status"] == "refused"
   and "list" in r["reason"] and len(INBOX.sent) == n, r)
r = m.send_dm("d1", "x", key="btn-dict", buttons={"title": "Go", "url": "https://a.example"})
ok("a single button dict is refused, not crashed on", r["status"] == "refused" and "list" in r["reason"], r)
r = m.send_dm("d1", "x", key="bare", buttons=[{"title": "Go", "url": "https://"}])
ok("a bare https:// is not a link", r["status"] == "refused" and len(INBOX.sent) == n, r)
r = m.reply_to_comment("c-1", "Hi", key="dm1")
ok("a bare comment id is refused, not crashed on", r["status"] == "refused" and "m.comments()" in r["reason"], r)
ok("conversation_for of a bare id is None", m.conversation_for("c-1") is None)
r = m.send_dm("d1", "Pick one", key="thirteen", quick_replies=[f"Option {i}" for i in range(13)])
ok("13 quick replies send", r["status"] == "sent" and len(INBOX.sent[-1]["quick_replies"]) == 13, r)
r = m.send_dm("d1", "Pick one", key="fourteen", quick_replies=[f"Option {i}" for i in range(14)])
ok("14 are refused", r["status"] == "refused" and "13" in r["reason"], r)

# 3. Reads never raise, and one Space's bad key never breaks the others.
_all, _client = _spaces.all_spaces, zernio.client


class _Broken:
    class accounts:
        @staticmethod
        def discover():
            raise zernio.ZernioError("401 revoked key")


_spaces.all_spaces = lambda: [{"name": "broken", "zernio_key": "bad"}, {"name": _spaces.DEFAULT, "zernio_key": "k"}]
zernio.client = lambda sp: _Broken() if sp.get("name") == "broken" else _client(sp)
got2 = m.comments()
ok("a Space with a revoked key is skipped; the other Space's comments still come back",
   [c["id"] for c in got2] == ["c-1", "c-2"], [c["id"] for c in got2])
_spaces.all_spaces, zernio.client = _all, _client
_clist = FakeComments.list
FakeComments.list = lambda self, *a, **k: (_ for _ in ()).throw(zernio.ZernioError("502"))
ok("a comment list that fails is empty, not an exception", m.comments() == [])
FakeComments.list = _clist

# ── the seam ─────────────────────────────────────────────────────────────────────────────────────────────────
print("the seam")
old_provider = conversations.provider()
try:
    conversations.provide(type("P", (), {n: staticmethod(lambda **k: None)
                                         for n in ("claim", "release", "holder", "messages", "send")}))
    refused = False
except ValueError as e:
    refused = "comments" in str(e)
conversations.provide(old_provider)
ok("a provider without the comment calls is refused", refused)
ok("the new calls are promised seams of SDK 1",
   {"comments", "reply_to_comment", "conversation_for", "follows_you"} <= set(sdk.SEAMS))

print("ALL CONVERSATION SEAM CHECKS PASS" if not _failed else f"{_failed} CONVERSATION SEAM CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
