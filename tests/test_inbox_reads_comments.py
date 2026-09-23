"""Comments on the business's own posts arrive in the same inbox as its DMs.

WHY THIS CHANNEL WENT FIRST, on a measurement rather than a preference. The owner asked for more
connections on go-to-market day and I had proposed Google reviews first, because a local service
business cares about those most. OSDev2 asked the live vendor on 2026-09-16:
`comments.list_inbox_comments` returned 22 REAL rows across Instagram and LinkedIn, while reviews
returned n=0 — no Google Business Profile is connected anywhere to answer about. Reviews would
have shipped unverifiable on the day the first customer arrives. Reviews is next.

WHAT THIS SUITE HOLDS, and the first one is the design decision that matters:

  · a conversation is (post, commenter) — NOT one per post. A post's comments come from many
    people; `participant` names one, and `awaiting_reply` counts people waiting. Pour twelve
    strangers into one thread and the number this product is sold on stops meaning anything.
  · the business's OWN replies are mirrored too, so a commenter who has been answered stops
    counting as waiting — the #1334 defect, which would have walked straight back in on a new
    channel that only recorded inbound
  · re-sweeping writes nothing twice, and lands on the same rows rather than making new ones
  · NOTHING AUTO-SENDS. `window.decide` refuses `comment` outright, and this suite asserts the
    refusal rather than trusting that nobody wired one up
  · one platform failing never costs the other, and an unconnected box is not a failure

NO NETWORK. The vendor is stubbed; a suite that reached Zernio would cost quota on every CI run
and could not run in a sandbox at all.

Run: python tests/test_inbox_reads_comments.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "comments.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
for _k in [k for k in list(os.environ) if k.startswith("ZERNIO_API_KEY")]:
    os.environ.pop(_k, None)

from core import state                                                   # noqa: E402

state.init_db()

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


HAS_INBOX = os.path.isdir(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                       "marketing", "customer_voice"))
if not HAS_INBOX:
    print("this box does not carry the Unified Inbox — no comment channel to hold")
    sys.exit(0)

from marketing.customer_voice.inbox import channels, comment_channel, store, window  # noqa: E402

SPACE = "default"
SP = {"name": SPACE, "zernio_key": "k", "zernio_profile_id": "p1"}

# THE VENDOR, STUBBED. Two people commented on one post and the business answered one of them.
POST = {"id": "post_1", "accountId": "acct_ig", "caption": "New opening hours this week"}
COMMENTS = {"comments": [
    {"id": "c1", "message": "Are you open Saturday?", "createdTime": "2026-09-18T09:00:00Z",
     "from": {"id": "u_dana", "name": "Dana Okoro"}},
    # THE BUSINESS ANSWERING DANA — and `parentId` is what says so. Without it this reply became
    # a third conversation named after the business, sitting beside the two customers it was
    # written to. Real payloads carry the link; the first version of this fixture did not, which
    # is how the bug surfaced on the first run rather than on a customer's box.
    {"id": "c2", "message": "Yes — 9 to 4.", "createdTime": "2026-09-18T09:05:00Z",
     "parentId": "c1", "from": {"id": "acct_ig", "name": "The Business"}},
    {"id": "c3", "message": "Do you take walk-ins?", "createdTime": "2026-09-18T09:10:00Z",
     "from": {"id": "u_raj", "name": "Raj Patel"}},
]}


class FakeComments:
    def __init__(self, fail_on=()):
        self.fail_on, self.listed, self.fetched = set(fail_on), [], []

    def list_inbox_comments(self, **kw):
        self.listed.append(kw.get("platform"))
        if kw.get("platform") in self.fail_on:
            from core.vendors import zernio
            raise zernio.ZernioError("no account connected for this platform")
        return {"posts": [POST]} if kw.get("platform") == "instagram" else {"posts": []}

    def get_inbox_post_comments(self, post_id, account_id, **kw):
        self.fetched.append((post_id, account_id))
        return COMMENTS


class FakeZ:
    def __init__(self, fail_on=()):
        self.comments = FakeComments(fail_on)


def with_vendor(fail_on=()):
    """Swap `zernio.client` for the length of a call. No network, in any environment."""
    from core.vendors import zernio
    fake = FakeZ(fail_on)
    real = zernio.client
    zernio.client = lambda sp: fake
    try:
        return fake, comment_channel.sweep(SPACE, SP)
    finally:
        zernio.client = real


def wipe():
    with state.connect() as c:
        c.execute("DELETE FROM inbox_messages")
        c.execute("DELETE FROM inbox_conversations")


# ── 1. the design decision: one conversation per COMMENTER, not per post ─────────────
print("test_a_conversation_is_one_person_not_one_post")
wipe()
fake, (scanned, stored) = with_vendor()
ok("every comment on the post was read", scanned == 3, f"scanned={scanned}")
ok("...and every one was stored", stored == 3, f"stored={stored}")
convs = store.list_conversations(SPACE, limit=50)
ok("two commenters and the business's own reply make TWO threads, not one per post",
   len(convs) == 2, f"{len(convs)} conversations: {[c.get('participant') for c in convs]}")
names = sorted(str(c.get("participant") or "") for c in convs)
ok("...and each thread is named for the person in it, not for the post",
   names == ["Dana Okoro", "Raj Patel"], str(names))
ok("...on the comment channel", all(c.get("platform") == "comment" for c in convs),
   str([c.get("platform") for c in convs]))
# THE POST IS THE CONTEXT AND IT RIDES ALONG. "Are you open Saturday?" is unanswerable without
# knowing which post it sits under.
dana = [c for c in convs if c.get("participant") == "Dana Okoro"][0]
msgs = store.messages_for(SPACE, dana["zernio_conversation_id"])
ok("the comment carries the post it was written under",
   any("New opening hours" in str(m.get("body") or "") for m in msgs),
   str([str(m.get("body"))[:60] for m in msgs]))


# ── 2. the #1334 defect must not walk back in on a new channel ───────────────────────
print("\ntest_answering_someone_stops_them_counting_as_waiting")
# THE BUSINESS ANSWERED DANA AND NOT RAJ. A channel that recorded only inbound would count both,
# which is exactly what made "who is waiting" say 72 when it was a handful.
waiting = store.awaiting_reply(SPACE)
ok("only the person who has NOT been answered is waiting", waiting == 1, f"awaiting={waiting}")
ok("...and the business's own reply was mirrored as outbound, not dropped",
   any(str(m.get("direction")) == "out" for m in msgs), str([m.get("direction") for m in msgs]))


# ── 3. re-sweeping is free and lands on the same rows ────────────────────────────────
print("\ntest_a_second_sweep_writes_nothing_twice")
before = len(store.list_conversations(SPACE, limit=50))
_, (scanned2, stored2) = with_vendor()
after = store.list_conversations(SPACE, limit=50)
ok("the same comments are seen again", scanned2 == 3, f"scanned={scanned2}")
ok("...and no second conversation is created for the same person and post",
   len(after) == before, f"{before} -> {len(after)}")
total = sum(len(store.messages_for(SPACE, c["zernio_conversation_id"])) for c in after)
ok("...and no message is stored twice", total == 3, f"{total} messages for 3 comments")

# THE BUSINESS ANSWERING NOBODY IS NOT A CONVERSATION. A top-level comment from the account that
# owns the post is a caption, and inventing a thread for it puts a row on the screen with nobody
# on the other side of it.
_orphan = {"comments": [{"id": "c9", "message": "Booking link in bio",
                         "createdTime": "2026-09-18T10:00:00Z",
                         "from": {"id": "acct_ig", "name": "The Business"}}]}
_before = len(store.list_conversations(SPACE, limit=50))
comment_channel._record_post(SPACE, "instagram", POST, "post_1", "acct_ig", _orphan)
ok("the business commenting under its own post makes no thread",
   len(store.list_conversations(SPACE, limit=50)) == _before,
   f"{_before} -> {len(store.list_conversations(SPACE, limit=50))}")
# DERIVED, NOT INVENTED — which is why the second sweep lands on the first sweep's rows.
ok("the conversation id is derived from (post, commenter) and is stable",
   comment_channel.conversation_id("post_1", "u_dana")
   == comment_channel.conversation_id("post_1", "u_dana"))
ok("...and two commenters on one post do not collide",
   comment_channel.conversation_id("post_1", "u_dana")
   != comment_channel.conversation_id("post_1", "u_raj"))


# ── 4. nothing auto-sends, and the refusal is asserted rather than assumed ───────────
print("\ntest_nothing_auto_sends_on_a_comment")
d = window.decide("comment", "2026-09-18T09:00:00Z")
ok("the send path REFUSES a comment outright", d["decision"] == "blocked", str(d))
ok("...because it has no send lane, not because a clock ran out",
   d.get("no_send_lane") is True, str(d))
ok("...and says why in words a person can act on",
   "owner has not ruled" in str(d.get("reason") or ""), str(d.get("reason"))[:120])
# THE CHANNEL MODULE CALLS NEITHER REPLY PATH. The vendor offers a public reply and a private
# one; publishing under a business's own post is not a thing to infer from a config change.
#
# ON THE CALLS, NOT ON THE PROSE. The first version of this grepped the module source for the
# method names — and failed, because the module's own docstring NAMES both paths while explaining
# that it does not call them. An assertion that reads its own documentation is the same fault the
# slug scanner keeps a dedicated guard against. Walking the AST for attribute calls cannot
# self-match: a name in a string or a comment is not a call node.
import ast as _ast                                                       # noqa: E402
import inspect as _inspect                                               # noqa: E402

_called = {n.func.attr for n in _ast.walk(_ast.parse(_inspect.getsource(comment_channel)))
           if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Attribute)}
ok("the channel module never CALLS the public reply path",
   "reply_to_inbox_post" not in _called, str(sorted(_called)))
ok("...nor the private one", "send_private_reply_to_comment" not in _called, str(sorted(_called)))
ok("...and the guard is not reading prose — it sees the calls it does make",
   "get_inbox_post_comments" in _called and "list_inbox_comments" in _called,
   str(sorted(_called)))


# ── 5. one platform failing never costs the other ────────────────────────────────────
print("\ntest_each_platform_fails_alone")
wipe()
fake, (scanned3, stored3) = with_vendor(fail_on=("instagram",))
ok("both platforms were asked", sorted(fake.comments.listed) == ["facebook", "instagram"], str(fake.comments.listed))
ok("...and an Instagram refusal did not raise", True)
ok("...and the sweep still returned a result rather than dying", scanned3 == 0 and stored3 == 0,
   f"{scanned3}/{stored3}")
# AN UNCONNECTED BOX IS AN ORDINARY STATE, not a failure.
got = comment_channel.sweep(SPACE, {"name": SPACE})
ok("a box with no social account connected reads nothing and breaks nothing", got == (0, 0),
   str(got))


# ── 6. the channel is registered where the rest of the product looks ─────────────────
print("\ntest_the_channel_is_registered_not_special_cased")
ok("comments are in the polled list", any(c.key == "comment" for c in channels.POLLED),
   str([c.key for c in channels.POLLED]))
ok("...marked as a resource rather than a platform, like IMAP",
   any(c.key == "comment" and c.vendor == channels.COMMENTS for c in channels.POLLED))
ok("...and the product already had a human name for it",
   channels.NAMES.get("comment") == "Comments", str(channels.NAMES.get("comment")))
ok("...which the send-window explainer uses too, rather than deriving 'Comment'",
   window.pretty_platform("comment") == "Comments", window.pretty_platform("comment"))

print("\nall ok" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
