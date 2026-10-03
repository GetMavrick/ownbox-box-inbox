"""The Inbox as the box's conversation provider (core/conversations.py, docs/PLAN_LEAD_MAGNET_MACHINE.md step 2).

Core may not name this machine, so the Inbox registers itself with core at import and a machine of the owner's
own reaches it through the SDK (`m.claim`, `m.release`, `m.messages`, `m.send_dm`). Everything here is a thin
translation: claims live in customer_voice/claims.py, sending in inbox/reply.py, reading in inbox/store.py.

WHICH SPACE. A machine names a conversation by its id and nothing else, because a single-tenant box has one
Space and its builder shouldn't have to learn the word. The id is looked up across the box's Spaces: found in
exactly one, that is its Space; found in none (an automation may claim a conversation before the next poll
mirrors it), the box's own default Space; found in several, refused rather than guessed, because a guess
there is one client's automation writing into another client's inbox.
"""
from __future__ import annotations

from core import state
from core.logging import get_logger

from marketing.customer_voice import claims as _claims

from . import reply, store

log = get_logger(__name__)


class Ambiguous(ValueError):
    pass


def _space_of(conversation: str) -> str:
    conversation = str(conversation or "").strip()
    if not conversation:
        raise ValueError("a conversation id is needed")
    with state.connect() as c:
        rows = c.execute("SELECT DISTINCT space FROM inbox_conversations WHERE zernio_conversation_id = ?",
                         (conversation,)).fetchall()
    if len(rows) > 1:
        raise Ambiguous("that conversation id belongs to more than one Space on this box")
    if rows:
        return rows[0]["space"]
    from core import spaces
    return spaces.DEFAULT


def claim(*, machine: str, title: str, conversation: str, days: float = _claims.CLAIM_DAYS,
          trigger: str | None = None) -> bool:
    return _claims.claim(_space_of(conversation), conversation, machine=machine, title=title, days=days,
                         trigger=trigger)


def release(*, machine: str, conversation: str, note: str = "") -> bool:
    return _claims.release(_space_of(conversation), conversation, machine=machine, note=note)


def holder(*, conversation: str) -> dict | None:
    h = _claims.holder(_space_of(conversation), conversation)
    return {"machine": h["machine"], "title": h["title"], "until": h["expires_at"]} if h else None


def messages(*, conversation: str, since: str | None = None, limit: int = 200) -> list[dict]:
    """The conversation's messages, oldest first: {"id", "direction", "sent_by", "body", "at"}. `since` keeps
    only those after that time (an ISO stamp a previous call returned as `at`)."""
    space = _space_of(conversation)
    out = []
    for m in store.messages_for(space, conversation, limit=int(limit)):
        at = str(m.get("created_at") or "")
        if since and at <= str(since):
            continue
        out.append({"id": m.get("zernio_message_id") or m.get("id"), "direction": m.get("direction"),
                    "sent_by": m.get("sent_by"), "body": m.get("body") or "", "at": at})
    return out


def _outcome(fn) -> dict:
    """Run one send, and say what happened. Never raises for an outcome."""
    try:
        r = fn()
    except reply.ReplyIndeterminate as e:
        return {"status": "unknown", "message_id": None, "reason": f"it may have been sent: {e}", "code": "unknown"}
    except (reply.ReplyRefused, ValueError) as e:
        return _refused(str(e), getattr(e, "code", "refused"))
    return {"status": "duplicate" if r.get("duplicate") else "sent", "message_id": r.get("message_id"),
            "reason": "", "code": ""}


def _refused(reason: str, code: str = "refused") -> dict:
    """Nothing went. `code` says why for a program (reply.ReplyRefused lists them); `reason` says it for a person."""
    return {"status": "refused", "message_id": None, "reason": reason, "code": code}


def send(*, machine: str, conversation: str, text: str, key: str, buttons=None, quick_replies=None) -> dict:
    """One message from `machine`, with optional link buttons and quick replies."""
    return _outcome(lambda: reply.send_for_machine(space=_space_of(conversation), zcid=conversation, text=text,
                                                   machine=machine, key=key, buttons=buttons,
                                                   quick_replies=quick_replies))


# ── comments: reading them, and the private reply that opens a conversation ──────────────────────────────────
# A READ NEVER RAISES (OSDev1's review of #1789). One Space with a revoked key must not break comments for every
# Space, and a machine's poll must not crash on a vendor hiccup: each Space, and each post, is read on its own, and
# a failure is logged with its reason and skipped. The one place a failed read is NOT treated as "nothing there" is
# the STOP check before a private reply (`_conversation_strict`): there, "couldn't tell" refuses the send.
def _instagram_spaces(only: str | None = None, *, strict: bool = False) -> list[tuple[dict, object, str]]:
    """(space row, its Zernio client, its Instagram account id) for every Space with a key and an account."""
    from core import spaces
    from core.vendors import zernio
    out = []
    for sp in spaces.all_spaces():
        if only and (sp.get("name") or "") != only:
            continue
        if not zernio.is_configured(sp.get("zernio_key")):
            continue
        try:
            z = zernio.client(sp)
            account = (z.accounts.discover() or {}).get("instagram")
        except Exception as e:                           # noqa: BLE001 — one Space never breaks the rest
            if strict:
                raise
            log.warning("inbox.instagram_unreadable", space=sp.get("name"), error=f"{type(e).__name__}: {e}"[:160])
            continue
        if account:
            out.append((sp, z, account))
    return out


def _own_idents(z) -> set:
    """The account's own id and @handle, so its own comments (its replies under a post) are never treated as a
    person's. Best effort: an account that doesn't say only loses this filter, never the read."""
    try:
        return set(z.accounts.identities().get("instagram") or ())
    except Exception:                                    # noqa: BLE001
        return set()


def _comment(raw, *, space: str, account: str, post: str) -> dict | None:
    from core.vendors.zernio import model as zm
    cid = zm.field(raw, "id", "commentId", "comment_id")
    if not cid:
        return None
    author = zm.field(raw, "author", "from", "commenter", "user") or {}
    if not isinstance(author, dict):
        author = {"name": str(author)}
    return {"id": str(cid), "post": str(post), "account": str(account), "space": space,
            "text": str(zm.field(raw, "text", "message") or ""),
            "author": {k: str(zm.field(author, k) or "") for k in ("id", "username", "name")},
            "at": str(zm.field(raw, "createdAt", "created_time", "timestamp", "created_at") or "")}


def _after(at: str, since: str) -> bool:
    """Is `at` later than `since`? Compared as times, not strings, so "Z" and "+00:00" agree. A time that can't
    be read is kept: a comment is never hidden because its time was written differently."""
    from datetime import datetime, timezone

    def t(v):
        d = datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    try:
        return t(at) > t(since)
    except (TypeError, ValueError):
        return True


def comments(*, post: str | None = None, since: str | None = None, posts: int = 20,
             per_post: int = 50) -> list[dict]:
    """Recent Instagram comments on the box's account, newest posts first, read-only. Each is {"id", "post",
    "account", "space", "text", "author": {"id", "username", "name"}, "at"}: pass one back unchanged to
    `reply_to_comment`. Two steps, because the vendor's comment list is a list of POSTS with comment activity
    (the dead-funnel lesson, leadmagnet/sweep.py W1.4): the posts first, then each post's own comments.
    `since` keeps only comments after that time (an `at` a previous call returned), so a poll doesn't re-read
    everything. Replies under a comment, and the account's own comments, are left out: they are conversation,
    not a person asking. Never raises: a Space or a post that can't be read is logged and skipped."""
    from core.vendors.zernio import model as zm
    out = []
    for sp, z, account in _instagram_spaces():
        name = sp.get("name") or ""
        own = _own_idents(z)
        refs = [post] if post else []
        if not post:
            try:
                listed = z.comments.list(account, since=since, limit=int(posts)).get("items") or []
            except Exception as e:                       # noqa: BLE001 — see the block comment above
                log.warning("inbox.comments_unreadable", space=name, error=f"{type(e).__name__}: {e}"[:160])
                continue
            for p in listed:
                if zm.field(p, "commentCount") == 0:
                    continue
                ref = zm.field(p, "platformPostId", "id")
                if ref:
                    refs.append(str(ref))
        for ref in refs:
            try:
                items = z.comments.list_post_comments(ref, account, limit=int(per_post)).get("items") or []
            except Exception as e:                       # noqa: BLE001 — one post never stalls the rest
                log.warning("inbox.post_comments_unreadable", space=name, post=str(ref)[:60],
                            error=f"{type(e).__name__}: {e}"[:160])
                continue
            for raw in items:
                if zm.field(raw, "isReply") is True:
                    continue
                c = _comment(raw, space=name, account=account, post=ref)
                if not c:
                    continue
                if own and {_ident(c["author"]["id"]), _ident(c["author"]["username"])} & own:
                    continue
                if since and not _after(c["at"], since):
                    continue
                try:
                    store.remember_comment(name, c)
                except Exception as e:                   # noqa: BLE001 — the read still answers
                    log.warning("inbox.comment_remember_failed", error=type(e).__name__)
                out.append(c)
    return out


# ── who a commenter is in the inbox, and whether they follow the account ─────────────────────────────────────
# The join the old funnel used (leadmagnet/sweep.py `_identity_index`), on the vendor's own conversation list,
# because the box's mirror keeps only a display handle: a commenter's id and @handle against each conversation's
# OTHER party (participant id and handle; never the conversation's own id, never the account's).
_PERSON_FIELDS = ("id", "_id", "userId", "username", "igId", "igsid", "participantId", "participantUsername")


def _ident(v) -> str | None:
    s = str(v or "").strip().lower().lstrip("@")
    return s or None


def _idents(obj, fields) -> set:
    from core.vendors.zernio import model as zm
    return {i for i in (_ident(zm.field(obj, f)) for f in fields) if i}


def _thread_idents(convo) -> set:
    from core.vendors.zernio import model as zm
    out = _idents(convo, ("participantId", "participantUsername"))
    parts = zm.field(convo, "participants", "members")
    for p in (parts if isinstance(parts, (list, tuple)) else []):
        out |= _idents(p, _PERSON_FIELDS)
    return out


def _thread_id(convo) -> str | None:
    from core.vendors.zernio import model as zm
    v = zm.field(convo, "field_id", "_id", "id", "conversationId", "conversation_id")
    return str(v) if v else None


MAX_PAGES = 40                    # 40 pages of the platform's list; a box with more is told, never half-read


class Unread(RuntimeError):
    """The platform's whole conversation list couldn't be read."""


def _threads(space: str | None = None, *, strict: bool = False, statuses=("active",)) -> list:
    """EVERY page of each Space's Instagram conversations, for each of `statuses` (OSDev1's review of #1789: page 1
    alone is 50, and a STOP on page 2 was missed). `strict`: a page that fails, or more pages than MAX_PAGES,
    raises, because a STOP check must know it saw them all. Otherwise a Space that fails is logged and skipped."""
    out = []
    for sp, z, _account in _instagram_spaces(space, strict=strict):
        got = []
        try:
            for status in statuses:
                cursor = None
                for _ in range(MAX_PAGES):
                    page = z.inbox.list(platform="instagram", status=status, cursor=cursor) or {}
                    got += list(page.get("conversations") or [])
                    cursor = page.get("next_cursor")
                    if not cursor:
                        break
                else:
                    raise Unread(f"more than {MAX_PAGES} pages of {status} conversations")
        except Exception as e:                           # noqa: BLE001 — one Space never breaks the rest
            if strict:
                raise
            log.warning("inbox.threads_unreadable", space=sp.get("name"), error=f"{type(e).__name__}: {e}"[:160])
            got = got if not strict else []
        out += got
    return out


# ── the one-time fill: who is on every conversation the platform holds, archived included ─────────────────────
# The poller keeps participant ids as it reads, but only for what it reads from now on (one page of ACTIVE
# conversations a sweep). A person who said STOP whose conversation is archived, and that the poller hasn't read
# since this shipped, would be in neither that record nor the active list (OSDev1's re-review of #1789). So each
# Space is read once, every page, active AND archived, and marked filled; until it is, a private reply to anyone
# the box hasn't seen is refused rather than guessed.
_FILLED_NS = "inbox"


def _filled_key(space: str) -> str:
    return f"participant_ids_filled:{space}"


def filled(space: str) -> bool:
    from core import box_settings
    return bool(box_settings.get(_FILLED_NS, _filled_key(space), default=False))


def fill_participant_ids() -> dict:
    """Read every Space's whole Instagram conversation list once (active and archived) into the participant-id
    record. A Space already filled is skipped; one that fails is tried again next time. Never raises."""
    from core import box_settings
    done = {}
    for sp, _z, _account in _instagram_spaces():
        name = sp.get("name") or ""
        if filled(name):
            continue
        try:
            convos = _threads(name, strict=True, statuses=("active", "archived"))
        except Exception as e:                           # noqa: BLE001 — not filled: replies to strangers wait
            log.warning("inbox.participant_fill_failed", space=name, error=f"{type(e).__name__}: {e}"[:160])
            continue
        for convo in convos:
            zcid = _thread_id(convo)
            if zcid:
                store.remember_participant(name, zcid, _thread_idents(convo))
        box_settings.put(_FILLED_NS, _filled_key(name), True, set_by="inbox")
        done[name] = len(convos)
        log.info("inbox.participant_fill_done", space=name, conversations=len(convos))
    return done


def _conversation_strict(comment: dict) -> str | None:
    """conversation_for, but a read that fails RAISES instead of answering None: the STOP check needs to know the
    difference between "they have no conversation" and "the box couldn't look"."""
    author = dict(comment.get("author") or {}) if isinstance(comment.get("author"), dict) else {}
    want = {i for i in (_ident(author.get("id")), _ident(author.get("username"))) if i}
    if not want:
        return None
    for convo in _threads(comment.get("space") or None, strict=True, statuses=("active", "archived")):
        if want & _thread_idents(convo):
            return _thread_id(convo)
    return None


def conversation_for(*, comment: dict) -> str | None:
    """The DM conversation with the person who wrote `comment`, or None while there isn't one yet (it appears
    once they write back, or tap a quick reply). Matched on their id and @handle, never a display name.
    Never raises: a comment that isn't one, or a read that fails, is None (logged with its reason)."""
    if not isinstance(comment, dict):
        log.warning("inbox.conversation_for_not_a_comment", got=type(comment).__name__)
        return None
    try:
        return _conversation_strict(comment)
    except Exception as e:                               # noqa: BLE001
        log.warning("inbox.conversation_for_unreadable", error=f"{type(e).__name__}: {e}"[:160])
        return None


def follows_you(*, conversation: str) -> bool | None:
    """Does the person on this conversation follow the account? True / False, or None when Instagram didn't
    say, which on a polled inbox is common (measured 2026-07-08 on the owner's account: present only on webhooks).
    Never raises: a read that fails is None, the same "didn't say"."""
    from core.vendors.zernio import model as zm
    try:
        space = _space_of(conversation)
    except ValueError:
        space = None
    try:
        for convo in _threads(space):
            if _thread_id(convo) == str(conversation):
                return zm.is_follower(convo)
    except Exception as e:                               # noqa: BLE001
        log.warning("inbox.follows_you_unreadable", error=f"{type(e).__name__}: {e}"[:160])
    return None


def opted_out(*, conversation: str) -> bool:
    """Did this person say STOP on this box? The Inbox's own flag (handler.py sets it on a STOP). An unknown
    conversation is NOT opted out by this answer; a caller that needs the person to exist checks that itself."""
    space = _space_of(conversation)
    row = store.get_conversation(space, str(conversation))
    return bool(row and row.get("opted_out"))


def reply_to_comment(*, machine: str, comment: dict, text: str, key: str, quick_replies=None) -> dict:
    """A private reply to one comment `comments()` returned: the message that opens a conversation.

    THE PERSON'S OWN STOP, FOUND BY WHO THEY ARE (OSDev1's review of #1789). The inbox's conversation row keeps a
    display name, and an Instagram comment carries an @handle, so comparing the two let a person who said STOP
    get a private reply. Their conversation is found the way `conversation_for` finds it, by id or @handle on
    the platform's own list, and its opt-out is read from the inbox. If the box can't look, nothing is sent."""
    from core import spaces
    if not isinstance(comment, dict):
        return _refused("pass the comment exactly as m.comments() returned it (the whole dict, not its id)")
    space = str(comment.get("space") or spaces.DEFAULT)
    author = comment.get("author") if isinstance(comment.get("author"), dict) else {}
    ids = (author.get("id"), author.get("username"))
    if store.opted_out_by_ident(space, ids):
        # FROM THE BOX'S OWN RECORD of who is on each conversation, every page it has ever read (OSDev1's review).
        return _refused("this person has opted out — nothing is sent to them", "opted_out")
    if not filled(space) and not store.known_ident(space, ids):
        # THE BOX HASN'T READ EVERY CONVERSATION YET (the one-time fill above), so it can't be sure this person
        # never said STOP on an archived one. It waits rather than guesses.
        return _refused("the box is still reading its past conversations to check who said STOP; try again soon",
                        "not_ready")
    try:
        theirs = _conversation_strict(comment)
        stopped = bool(theirs) and opted_out(conversation=theirs)
    except Exception as e:                               # noqa: BLE001 — can't tell whether they said STOP: don't
        log.warning("inbox.private_reply_stop_unchecked", error=f"{type(e).__name__}: {e}"[:160])
        return _refused("the box couldn't check whether this person said STOP, so nothing is sent", "unchecked")
    if stopped:
        return _refused("this person has opted out — nothing is sent to them", "opted_out")
    return _outcome(lambda: reply.reply_to_comment(space=space, machine=machine, comment=comment, text=text,
                                                   key=key, quick_replies=quick_replies))
