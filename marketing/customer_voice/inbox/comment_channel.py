"""Comments on the business's own posts, read into the same inbox as its DMs.

WHY THIS CHANNEL, AND WHY FIRST. The owner asked for more connections on go-to-market day. I had
proposed Google reviews first, because a local service business cares about those most — and then
flipped my own order on a measurement rather than a preference. OSDev2 asked the live vendor on
2026-09-16: `comments.list_inbox_comments` returned **22 real rows** (Instagram and LinkedIn),
while reviews returned n=0 because no Google Business Profile is connected anywhere yet. Reviews
would have shipped unverifiable on the day the first customer arrives; comments ships something
there is real data to prove against. Reviews is next, and the shape here is the shape it takes.

WHAT A CONVERSATION IS HERE, WHICH IS THE ONE DESIGN DECISION THAT MATTERS.

The vendor's API is two levels: `list_inbox_comments` returns the POSTS that have comments, and
`get_inbox_post_comments` returns the comments on one post. The obvious mapping — one post, one
conversation — is wrong for this product, and wrong in a way that would quietly break the thing
the box is sold on. A post's comments come from many different people; a "conversation" here is
with ONE person, `participant` names them, and `awaiting_reply` counts how many of them are
waiting on a human. Pour twelve strangers into one thread and that number stops meaning anything.

So a conversation is **(post, commenter)**: one person's comments on one post, and whether the
business has answered them. That is the same unit as a DM thread, it makes `participant` true,
and it makes "three people are waiting on a reply" mean three people.

IT READS AND DRAFTS. IT DOES NOT SEND. The vendor offers two reply paths — `reply_to_inbox_post`
(public) and `send_private_reply_to_comment` (a DM) — and this module calls neither. Send policy
is the owner's word (CLAUDE.md), and `window._RULES` refuses any platform it has no rule for by
design: "adding a platform string to the poller must never be enough, by itself, to authorise a
send on it." Publishing a reply under a business's own post is not a thing to infer from a
config change. The comment arrives, the drafter writes a suggestion, a person sends it.

NO CLAUDE ANYWHERE IN THIS FILE — spec §11-6. Fetching, mapping and storing are deterministic;
the only reasoning about a comment happens later, in the drafter, through `brain.think()`.
"""
from __future__ import annotations

import hashlib

from core.logging import get_logger
from core.vendors import zernio

from . import store

log = get_logger(__name__)

# A FIRST SWEEP ON A BUSY ACCOUNT MUST NOT RUN FOR AN HOUR — the same rule `email_channel` sets
# for an old mailbox, and the same reason: the first poll after connecting is the one most likely
# to meet years of history at once.
_MAX_POSTS = 25
_MAX_COMMENTS = 25

# THE PLATFORMS WE ASK FOR. Deliberately not "everything the vendor has": these are the two the
# box already names, polls and can show a channel chip for. LinkedIn comments come back from the
# same call (OSDev2 measured them), and they are left out of this list on purpose — the inbox has
# no LinkedIn send lane, no LinkedIn chip and no window rule, so ingesting them would put rows on
# the screen the rest of the product cannot act on.
_PLATFORMS = ("instagram", "facebook")


def _f(d, *names):
    """First present, non-empty value among `names`. The vendor spells ids several ways across
    resources and this file must not care which — the same helper the poller uses, for the same
    reason."""
    for n in names:
        v = (d or {}).get(n)
        if v not in (None, "", [], {}):
            return v
    return None


def conversation_id(post_id: str, author_id: str) -> str:
    """The stable id for one person's comments on one post.

    SYNTHETIC, AND DERIVED RATHER THAN INVENTED. There is no vendor id for "this commenter on this
    post" — the pair is the identity — so it is hashed into one, the same way `store._mirror_key`
    already derives a surrogate for a message the vendor gave no id. Deriving it means a re-sweep
    of the same comment lands on the same row instead of making a second conversation, which is
    what an incrementing id or a timestamp would have done.

    The prefix is what tells a human reading the table where the row came from.
    """
    raw = f"{post_id}\x00{author_id}".encode()
    return "cmt:" + hashlib.sha256(raw).hexdigest()[:40]


def _author(c) -> tuple[str, str]:
    """(id, display name) for whoever wrote a comment. The id is what threads it; the name is what
    a person reads. Either may be missing and the other still works."""
    frm = _f(c, "from", "author", "user") or {}
    if not isinstance(frm, dict):
        frm = {}
    aid = str(_f(frm, "id", "_id", "user_id") or _f(c, "authorId", "author_id", "fromId") or "")
    name = str(_f(frm, "name", "username", "displayName")
               or _f(c, "authorName", "author_name") or "")
    return aid, name


def _parent_author(c, by_id: dict) -> str:
    """(id, name) of whoever this comment replies to, or ("", "") when it replies to nothing.

    AN OUTBOUND COMMENT IS FILED UNDER THE PERSON IT ANSWERS, not under the business that wrote
    it. The vendor spells the link several ways across platforms, so this reads all of them and
    falls back to an author carried inline when the payload gives one.
    """
    pid = str(_f(c, "parentId", "parent_id", "parent_cid", "replyTo", "reply_to") or "")
    if pid and pid in by_id:
        return by_id[pid]
    parent = _f(c, "parent", "repliedTo") or {}
    if isinstance(parent, dict):
        frm = parent.get("from") or parent.get("author") or {}
        if isinstance(frm, dict):
            return (str(_f(frm, "id", "_id", "user_id") or ""),
                    str(_f(frm, "name", "username", "displayName") or ""))
    return ("", "")


def sweep(space: str, sp: dict) -> tuple[int, int]:
    """Read new comments for one Space into the inbox store. Returns (scanned, stored).

    INERT WITHOUT A KEY OR A PROFILE, and that is an ordinary state rather than a failure — the
    same contract `email_channel.sweep` keeps for a mailbox nobody has connected. A box whose
    buyer has not linked a social account simply reads no comments.
    """
    if not sp.get("zernio_key") or not sp.get("zernio_profile_id"):
        return (0, 0)
    z = zernio.client(sp)
    scanned = stored = 0

    for platform in _PLATFORMS:
        try:
            page = z.comments.list_inbox_comments(
                profile_id=sp["zernio_profile_id"], platform=platform, limit=_MAX_POSTS)
        except zernio.ZernioError as e:
            # EACH PLATFORM FAILS ALONE, like each channel does in the poller above this. A box
            # with no Instagram account connected errors on every sweep, and that must not cost
            # the Facebook comments on the same box.
            log.warning("inbox.comments_list_failed", space=space, platform=platform,
                        error=str(e)[:120])
            continue
        for post in (page or {}).get("posts") or (page or {}).get("data") or []:
            post_id = str(_f(post, "id", "_id", "postId", "post_id") or "")
            acctid = str(_f(post, "accountId", "account_id", "account") or "")
            if not post_id or not acctid:
                # Defensive, and it skips rather than guesses: a comment we cannot attribute to an
                # account is one no reply path could ever act on.
                continue
            try:
                got = z.comments.get_inbox_post_comments(post_id, acctid, limit=_MAX_COMMENTS)
            except zernio.ZernioError as e:
                log.warning("inbox.comments_fetch_failed", space=space, platform=platform,
                            post=post_id, error=str(e)[:120])
                continue
            scanned_here, stored_here = _record_post(space, platform, post, post_id, acctid, got)
            scanned += scanned_here
            stored += stored_here
    return (scanned, stored)


def _record_post(space: str, platform: str, post: dict, post_id: str, acctid: str,
                 got: dict) -> tuple[int, int]:
    """One post's comments → conversations, one per commenter. Returns (scanned, stored)."""
    scanned = stored = 0
    # WHAT THE POST WAS, CARRIED ONTO EVERY THREAD FROM IT. A comment with no context is
    # unanswerable: "great, when do you open?" needs to sit under the post it answers. The
    # caption is the cheapest true context there is, and it is already on the row the vendor
    # returned, so this costs no extra call.
    caption = str(_f(post, "caption", "message", "text", "title") or "").strip()
    # comment id -> its author, built as we go, so a reply can find whose thread it belongs in
    # without a second fetch. The vendor returns a post's comments oldest-first, so a parent is
    # always already in here by the time its reply is read; a reply whose parent is missing falls
    # through to the skip below rather than guessing.
    by_id: dict[str, tuple[str, str]] = {}
    for c in (got or {}).get("comments") or (got or {}).get("data") or []:
        cid = str(_f(c, "id", "_id", "commentId", "comment_id") or "")
        body = str(_f(c, "message", "text", "body", "comment") or "").strip()
        if not cid or not body:
            continue
        scanned += 1
        aid, name = _author(c)
        # OUR OWN REPLIES ARE MIRRORED TOO, NOT SKIPPED. A comment written by the account that
        # owns the post is the business answering, and recording it is what stops
        # `awaiting_reply` counting somebody who has already been answered — the exact defect
        # #1334 fixed for DMs, and it would have come straight back on a new channel that only
        # recorded inbound. `_author` is compared to the account id the post came with.
        inbound = bool(aid) and aid != acctid
        # WHOSE THREAD DOES IT BELONG TO — and for an outbound reply that is NOT its own author.
        # Keying every comment on its writer put the business's own answer in a third thread of
        # its own, named after the business, sitting next to the two customers it was replying
        # to. Caught by the suite on the first run: two commenters and one reply produced THREE
        # conversations. A reply belongs in the thread of the person it answers, which is its
        # parent's author.
        who, who_name = (aid, name) if inbound else _parent_author(c, by_id)
        if not inbound and not who:
            # THE BUSINESS COMMENTING UNDER ITS OWN POST, ANSWERING NOBODY. That is a caption, not
            # a conversation, and inventing a thread for it would put a row on the screen with
            # nobody on the other side of it.
            continue
        by_id[cid] = (aid, name)
        zcid = conversation_id(post_id, who or cid)
        at = str(_f(c, "createdTime", "created_time", "createdAt", "timestamp") or "") or None
        # THE THREAD IS NAMED FOR THE CUSTOMER, NEVER FOR US. `upsert_conversation` refreshes
        # `participant` on every write, so passing the comment's own author renamed Dana's thread
        # "The Business" the moment we answered her — caught by the suite, one assertion after
        # the threading bug it also caught. For an outbound reply the person is the PARENT's
        # author, which `_parent_author` now returns alongside the id for exactly this.
        store.upsert_conversation(
            space=space, zcid=zcid, platform="comment",
            participant=((name if inbound else who_name) or who or "Someone")[:200],
            last_inbound_at=at if inbound else None,
            account_id=acctid)
        # INSERT OR IGNORE ON THE VENDOR'S COMMENT ID, so re-sweeping a post writes nothing twice.
        store.record_message(space=space, zcid=zcid, zmid=cid,
                             direction="in" if inbound else "out",
                             sent_by="contact" if inbound else "human",
                             body=(f"{body}\n\n— on your post: {caption[:120]}"
                                   if caption and inbound else body),
                             sent_at=at)
        stored += 1
    return (scanned, stored)
