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


def claim(*, machine: str, title: str, conversation: str, days: float = _claims.CLAIM_DAYS) -> bool:
    return _claims.claim(_space_of(conversation), conversation, machine=machine, title=title, days=days)


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


def send(*, machine: str, conversation: str, text: str, key: str) -> dict:
    """One message from `machine`. Never raises for an outcome: the answer says what happened."""
    try:
        space = _space_of(conversation)
        r = reply.send_for_machine(space=space, zcid=conversation, text=text, machine=machine, key=key)
    except reply.ReplyIndeterminate as e:
        return {"status": "unknown", "message_id": None, "reason": f"it may have been sent: {e}"}
    except (reply.ReplyRefused, ValueError) as e:
        return {"status": "refused", "message_id": None, "reason": str(e)}
    return {"status": "duplicate" if r.get("duplicate") else "sent", "message_id": r.get("message_id"),
            "reason": ""}
