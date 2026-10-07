"""Replies on their own: the DM channels the owner switched on answer themselves (Inbox Settings, Channels).

Owner, 2026-10-07: *"We also should build something that allows automatic sending of messages per channel. Like I
would like automatic instant responses to any Instagram or messenger DM's that come in. I don't wanna have to approve
those and I trust that sonnet will keep the conversation rolling. I think we need to build a listener on a 20 second
interval, like we did with the lead magnet machine."*

THE BOX STILL WRITES IN ONE PLACE AND SENDS IN ANOTHER. The drafter (`drafter/`, which may think and may not send)
writes the reply exactly as it does for every message, in the DM voice the owner set the same day; this file (which
may send and may not think) reads the replies waiting in `inbox_drafts` and sends the ones whose channel the owner
switched on, through `reply.send_automatic` and every gate it holds. A 20-second listener in
`customer_voice/__init__.py` runs the three in order: fetch new messages, write their replies, send them.

WHAT IS NEVER SENT ON ITS OWN, read straight off the table: a reply the box judged nobody needs (`NO_REPLY_BODY`), a
cold pitch turned around (it waits for a person, as it always has), a reply to anything but the newest message from
them, anything on a conversation already answered, a robot's (`automated`), an opted-out person's, or one an
automation holds. The rest of the gates (the switch and since when, a person talking, the window, the cap, the stop
switch, exactly once) are `reply.send_automatic`'s.
"""
from __future__ import annotations

from core import state
from core.logging import get_logger

from marketing.customer_voice import claims

from . import sending

log = get_logger(__name__)

# The drafter's own marks, by value: this directory may not import the drafter (it thinks), and these two strings are
# the contract between them, held by tests/test_dms_answer_themselves.py.
NO_REPLY_BODY = "(the box judged that this message needs no reply)"
PER_TICK = 10                    # a spend-free bound: sends per listener tick, inside the hourly cap anyway


def ready(space: str) -> list[dict]:
    """Replies waiting to be sent on their own in this Space, oldest message first. Never raises."""
    on = [ch for ch, _ in sending.AUTO_CHANNELS if sending.auto_reply_since(ch)]
    if not on:
        return []
    try:
        with state.connect() as c:
            rows = c.execute(
                "SELECT d.id, d.zernio_conversation_id AS zcid, d.body, d.in_reply_to, k.platform, "
                "       m.created_at AS asked_at "
                "  FROM inbox_drafts d "
                "  JOIN inbox_conversations k ON k.space = d.space "
                "   AND k.zernio_conversation_id = d.zernio_conversation_id "
                "  JOIN inbox_messages m ON m.space = d.space AND m.zernio_message_id = d.in_reply_to "
                " WHERE d.space = ? AND d.dismissed_at IS NULL AND d.body <> ? "
                "   AND k.opted_out = 0 AND COALESCE(k.automated, 0) = 0 "
                f"   AND LOWER(k.platform) IN ({','.join('?' * len(on))}) "
                "   AND NOT EXISTS (SELECT 1 FROM inbox_pitch_backs p WHERE p.space = d.space "
                "        AND p.in_reply_to = d.in_reply_to) "
                f"   AND {claims.UNCLAIMED} "
                "   AND m.created_at = (SELECT MAX(m2.created_at) FROM inbox_messages m2 WHERE m2.space = d.space "
                "        AND m2.zernio_conversation_id = d.zernio_conversation_id AND m2.direction = 'in') "
                "   AND NOT EXISTS (SELECT 1 FROM inbox_messages o WHERE o.space = d.space "
                "        AND o.zernio_conversation_id = d.zernio_conversation_id AND o.direction = 'out' "
                "        AND o.created_at > m.created_at) "
                " ORDER BY m.created_at ASC LIMIT ?",
                (space, NO_REPLY_BODY, *on, PER_TICK * 3)).fetchall()
    except Exception as e:                       # noqa: BLE001 — a box without the tables yet
        log.warning("inbox.auto_unreadable", extra={"error": f"{type(e).__name__}: {e}"[:120]})
        return []
    out = []
    for r in rows:
        r = dict(r)
        since = sending.auto_reply_since(r.get("platform"))
        if since and str(r.get("asked_at") or "") >= since:   # only what came in after it was switched on
            out.append(r)
    return out[:PER_TICK]


def tick() -> dict:
    """Send every reply that is ready, in every Space on this box. -> {"status", "sent", "held"}. Never raises: a
    refusal is a reason the reply waits for a person, said in the log, and the next tick looks again."""
    if not sending.auto_reply_any():
        return {"status": "off", "sent": 0, "held": {}}
    from core import spaces as _spaces
    from . import reply
    sent, held = 0, {}
    try:
        names = [(sp or {}).get("name") if isinstance(sp, dict) else str(sp) for sp in _spaces.all_spaces() or []]
    except Exception as e:                       # noqa: BLE001 — unconfigured is not broken
        log.info("inbox.auto_no_spaces", extra={"error": type(e).__name__})
        return {"status": "unconfigured", "sent": 0, "held": {}}
    for space in [n for n in names if n]:
        for row in ready(space):
            try:
                res = reply.send_automatic(space=space, zcid=row["zcid"], text=row["body"],
                                           in_reply_to=row["in_reply_to"], asked_at=row["asked_at"])
                sent += 0 if res.get("duplicate") else 1
            except reply.ReplyRefused as e:
                code = getattr(e, "code", "refused") or "refused"
                held[code] = held.get(code, 0) + 1
                if code == "hourly_cap":
                    break                        # nothing more goes this hour in this Space
            except reply.ReplyIndeterminate:
                held["indeterminate"] = held.get("indeterminate", 0) + 1
            except Exception as e:               # noqa: BLE001 — one conversation never stops the rest
                held["error"] = held.get("error", 0) + 1
                log.warning("inbox.auto_failed", extra={"space": space, "conversation": row.get("zcid"),
                                                        "error": type(e).__name__})
    if sent or held:
        log.info("inbox.auto_tick", extra={"sent": sent, "held": held})
    return {"status": "ok", "sent": sent, "held": held}
