"""Draft rows. Reads and writes only — nothing here reaches a network."""
from __future__ import annotations

import uuid

from core import state
from core.logging import get_logger


log = get_logger(__name__)


def put(*, space: str, zcid: str, in_reply_to: str, body: str) -> bool:
    """Record one draft. Returns False if this inbound already had one.

    EXACTLY ONE DRAFT PER INBOUND MESSAGE, enforced by `UNIQUE (space, in_reply_to)` rather
    than by the caller remembering. A sweep that runs twice — a restart, an overlapping timer —
    must not pay twice for the same answer, and money spent is the one mistake a retry cannot
    take back.
    """
    body = str(body or "").strip()
    if not body:
        return False
    with state.connect() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO inbox_drafts (id, space, zernio_conversation_id, "
            "in_reply_to, body, created_at) VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4()), space, zcid, in_reply_to, body, state._now()))
        return cur.rowcount > 0


def for_inbound(space: str, in_reply_to: str) -> dict | None:
    with state.connect() as c:
        row = c.execute(
            "SELECT * FROM inbox_drafts WHERE space = ? AND in_reply_to = ?",
            (space, str(in_reply_to))).fetchone()
    return dict(row) if row else None


def latest_for(space: str, zcid: str) -> dict | None:
    """The newest undismissed draft on this conversation, or None.

    SCOPED ON SPACE AS WELL AS THE CONVERSATION, like every other reader here: the id arrives
    from a URL, so the boundary must not rest on a vendor's uniqueness guarantee.
    """
    with state.connect() as c:
        row = c.execute(
            "SELECT * FROM inbox_drafts WHERE space = ? AND zernio_conversation_id = ? "
            "  AND dismissed_at IS NULL ORDER BY created_at DESC, id DESC LIMIT 1",
            (space, str(zcid))).fetchone()
    return dict(row) if row else None


def history_for(space: str, zcid: str, *, limit: int = 12) -> list[dict]:
    """The last few lines of the conversation, oldest first — the transcript the model sees.

    READ HERE RATHER THAN BORROWED FROM `inbox/`. `inbox.store` has an identical reader, and
    importing it would pull this directory's imports into the package that SENDS — which the
    guard in tests/test_customer_voice.py refuses, and rightly: the separation between the part
    that thinks and the part that sends is the whole reason drafting is allowed at all. A
    duplicated SELECT is a cheaper price than a dependency in that direction.
    """
    with state.connect() as c:
        rows = c.execute(
            "SELECT direction, sent_by, body, created_at FROM inbox_messages "
            " WHERE space = ? AND zernio_conversation_id = ? "
            " ORDER BY created_at DESC, id DESC LIMIT ?",
            (space, str(zcid), int(limit))).fetchall()
    return [dict(r) for r in reversed(rows)]


def dismiss(space: str, draft_id: str) -> None:
    """He said no. NEVER a DELETE — standing owner rule, and a draft he rejected is the most
    useful record this table holds: it is the evidence that the machine got one wrong."""
    with state.connect() as c:
        c.execute("UPDATE inbox_drafts SET dismissed_at = ? WHERE space = ? AND id = ?",
                  (state._now(), space, str(draft_id)))


def needs_a_draft(space: str, *, limit: int = 5) -> list[dict]:
    """Conversations whose newest inbound has no draft yet, newest first.

    THE `limit` IS A SPEND BOUND, not a page size. Each row this returns becomes one model call,
    so an unbounded version would let a quiet box that suddenly receives two hundred messages
    spend two hundred times in one sweep. `core.brain` has the $90 ceiling underneath; this
    keeps a single sweep from walking into it.

    An OPTED-OUT conversation is excluded here as well as at the send. Drafting a reply to
    somebody who asked us to stop is not a send, but it is work whose only possible use is a
    send, and paying a model to write it is the sort of thing that reads badly in a log.
    """
    with state.connect() as c:
        rows = c.execute(
            "SELECT k.space, k.zernio_conversation_id AS zcid, k.participant, k.platform, "
            "       m.zernio_message_id AS inbound_id, m.body AS inbound_body, "
            "       m.created_at AS inbound_at "
            "  FROM inbox_conversations k "
            "  JOIN inbox_messages m "
            "    ON m.space = k.space "
            "   AND m.zernio_conversation_id = k.zernio_conversation_id "
            "   AND m.direction = 'in' "
            "   AND m.zernio_message_id IS NOT NULL "
            " WHERE k.space = ? AND k.opted_out = 0 "
            "   AND m.created_at = (SELECT MAX(m2.created_at) FROM inbox_messages m2 "
            "                        WHERE m2.space = k.space "
            "                          AND m2.zernio_conversation_id = k.zernio_conversation_id "
            "                          AND m2.direction = 'in') "
            "   AND NOT EXISTS (SELECT 1 FROM inbox_drafts d "
            "                    WHERE d.space = k.space AND d.in_reply_to = m.zernio_message_id) "
            # A MESSAGE WITH NO WORDS CAN NEVER BE ANSWERED, so it must never take a slot in this
            # queue. MEASURED ON THE OWNER'S BOX 2026-09-22: fifteen media-only Instagram messages
            # arrived at 06:48 with empty bodies. This query is newest-first and the sweep takes
            # the first three, so for SEVEN HOURS every sweep picked the same empty rows,
            # `draft_one` returned None on each (silently — an empty inbound was its first
            # early-out), and `periodic` reported `drafted: 0`. 87 conversations were waiting, 72
            # of them with real text, and not one could ever be reached. /health was green
            # throughout, the worker was alive, and nothing said a word.
            # SQLite's one-argument TRIM strips SPACES ONLY — a body of "\n\t" survives it and the
            # jam comes straight back through a channel that sends a bare newline. The second
            # argument is the set of characters to strip, so this is tab, newline and return too.
            "   AND TRIM(COALESCE(m.body, ''), ' ' || char(9) || char(10) || char(13)) <> '' "
            " ORDER BY m.created_at DESC LIMIT ?", (space, int(limit))).fetchall()
    return [dict(r) for r in rows]


def newest_inbound(space: str, zcid: str) -> dict | None:
    """The message a reply would be answering: the newest INBOUND one in this conversation.

    WHY A CALLER CANNOT NAME `in_reply_to` ITSELF. `put()` keys its uniqueness on that id, so a
    caller that chose it could write a second draft against an older message and get two drafts
    waiting on one conversation — the exact thing `UNIQUE (space, in_reply_to)` exists to stop.
    Resolving it here means "one draft per conversation that is waiting on us" holds no matter
    who is asking, the sweep or a connector seat.

    OUTBOUND IS NOT A CANDIDATE. A draft answers a customer; the newest message in a thread is
    often our own last reply, and treating that as the thing to answer would have the box
    replying to itself.
    """
    with state.connect() as c:
        row = c.execute(
            "SELECT zernio_message_id AS id, body, created_at FROM inbox_messages "
            " WHERE space = ? AND zernio_conversation_id = ? AND direction = 'in' "
            "   AND zernio_message_id IS NOT NULL "
            " ORDER BY created_at DESC LIMIT 1", (space, zcid)).fetchone()
    return dict(row) if row else None


def waiting(space: str, *, limit: int = 50) -> list[dict]:
    """Every draft still waiting on a person: not dismissed, and not already answered.

    "STILL WAITING" IS A JOIN, NOT A COLUMN, and deliberately so. A draft is spent when the
    conversation has an OUTBOUND message newer than the inbound it answers — which is exactly
    what happens when somebody presses send, whether they sent this draft, an edit of it, or
    something else entirely. A `sent_at` column would have to be written by every path that can
    reply, and the one that forgets leaves a draft on the screen forever.

    ORDERED OLDEST FIRST. This is a queue of people waiting on an answer, and the person who has
    waited longest should be the one at the top; a newest-first list quietly buries them.
    """
    with state.connect() as c:
        rows = c.execute(
            "SELECT d.id, d.zernio_conversation_id AS zcid, d.body, d.created_at,"
            "       d.in_reply_to, k.participant, k.platform,"
            "       m.body AS asked, m.created_at AS asked_at "
            "  FROM inbox_drafts d "
            "  JOIN inbox_conversations k ON k.space = d.space "
            "   AND k.zernio_conversation_id = d.zernio_conversation_id "
            "  LEFT JOIN inbox_messages m ON m.space = d.space "
            "   AND m.zernio_message_id = d.in_reply_to "
            " WHERE d.space = ? AND d.dismissed_at IS NULL AND k.opted_out = 0 "
            "   AND NOT EXISTS (SELECT 1 FROM inbox_messages o "
            "                    WHERE o.space = d.space "
            "                      AND o.zernio_conversation_id = d.zernio_conversation_id "
            "                      AND o.direction = 'out' "
            "                      AND o.created_at > COALESCE(m.created_at, d.created_at)) "
            " ORDER BY COALESCE(m.created_at, d.created_at) ASC LIMIT ?",
            (space, int(limit))).fetchall()
    return [dict(r) for r in rows]


def waiting_count(space: str) -> int:
    """How many people are waiting on an answer — for the tab's badge."""
    return len(waiting(space, limit=1000))


def _same(a: str, b: str) -> bool:
    """Equal for the purpose of "did they edit it": whitespace and case at the edges do not count."""
    return " ".join(str(a or "").split()).strip().casefold() == " ".join(str(b or "").split()).strip().casefold()


def learn(space: str, zcid: str, sent_body: str) -> bool:
    """A person just sent `sent_body` on this conversation. If the box had drafted a reply to the
    message they were answering, keep the pair. Returns True if a lesson was written.

    THE DRAFT THAT COUNTS IS THE ONE ANSWERING THE NEWEST INBOUND, dismissed or not. A dismissed
    draft followed by a hand-written reply is the most informative pair there is — the box was
    wrong enough to throw away, and here is what right looked like. A draft against an OLDER
    message is not paired: the person was not answering that, and a lesson built from the wrong
    question would teach the wrong thing.

    NEVER RAISES INTO THE SEND. Called after the reply has left; a failure here is logged and the
    caller's success stands, because a customer's reply going out is never hostage to bookkeeping.
    """
    try:
        sent_body = str(sent_body or "").strip()
        if not sent_body:
            return False
        inbound = newest_inbound(space, zcid)
        if inbound is None:
            return False
        with state.connect() as c:
            d = c.execute(
                "SELECT id, body FROM inbox_drafts WHERE space = ? AND in_reply_to = ?",
                (space, str(inbound["id"]))).fetchone()
            if d is None:
                return False
            cur = c.execute(
                "INSERT OR IGNORE INTO inbox_draft_lessons (id, space, draft_id, "
                "zernio_conversation_id, asked, draft_body, sent_body, edited, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), space, d["id"], zcid,
                 str(inbound.get("body") or "")[:2000], d["body"], sent_body[:2000],
                 0 if _same(d["body"], sent_body) else 1, state._now()))
            wrote = cur.rowcount > 0
        if wrote:
            log.info("drafter.learned", extra={"space": space, "conversation": zcid,
                                               "edited": 0 if _same(d["body"], sent_body) else 1})
        return wrote
    except Exception as e:                        # noqa: BLE001 — bookkeeping never fails a send
        log.warning("drafter.learn_failed", extra={"space": space, "error": type(e).__name__})
        return False


def lessons(space: str, *, limit: int = 4) -> list[dict]:
    """The newest lessons for this space, edited ones first — what the drafter shows the model.

    EDITED FIRST because an edit carries the difference between what the box would say and what
    this business says; an unedited send only confirms. Then newest, so the examples follow the
    business as it changes. SCOPED ON SPACE: one client's replies are never another client's
    examples.
    """
    with state.connect() as c:
        rows = c.execute(
            "SELECT asked, draft_body, sent_body, edited, created_at FROM inbox_draft_lessons "
            " WHERE space = ? ORDER BY edited DESC, created_at DESC LIMIT ?",
            (space, int(limit))).fetchall()
    return [dict(r) for r in rows]
