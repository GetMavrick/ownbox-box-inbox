"""Draft rows. Reads and writes only — nothing here reaches a network."""
from __future__ import annotations

import uuid

from core import state


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
            " ORDER BY m.created_at DESC LIMIT ?", (space, int(limit))).fetchall()
    return [dict(r) for r in rows]
