"""Conversation claims: one owner per conversation at a time (docs/PLAN_LEAD_MAGNET_MACHINE.md step 2).

IN THE PACKAGE ROOT, NOT inbox/, because the drafter reads it too and importing inbox/ registers the opener
and the poller as a side effect.

Owner, 2026-10-01, approving the plan: the Inbox's drafting stays off for a conversation an automation runs
(decision 2), and a message off its script goes to a person (decision 3). The question he had asked for a
year, "how will the unified inbox know when it should act?", is answered here: the Inbox doesn't guess what a
message is about. The machine that started the conversation already knows, and it says so by CLAIMING it.

WHILE A CONVERSATION IS CLAIMED, the Inbox:
  * doesn't draft it (drafter/store.needs_a_draft, and the Drafts tab),
  * doesn't send its opener (inbox/handler.py),
  * doesn't count it as waiting on a person (store._WAITING: the header, the filter, the morning count and
    the twice-daily notice all read that one predicate),
  * still shows it, tagged with who is handling it, so nothing on the box is hidden from its owner.

A CLAIM ENDS three ways: the machine RELEASES it (finished, or handing back what it can't answer, with a
note the person reads), it EXPIRES (CLAIM_DAYS, so a stalled automation can never silence a conversation
forever), or the claim is never made. Released or expired, the conversation is ordinary again: the next
message from the person is waiting, drafted and counted like any other.

ONE ROW PER CONVERSATION, holding its current or latest claim. A claim can be made before the Inbox has
mirrored the conversation (an automation may start a DM before the next poll), and applies the moment the
conversation arrives.

TIME IN SQL. `expires_at` is written as `YYYY-MM-DDTHH:MM:SS` in UTC, the shape SQLite's own `strftime`
produces, so "is it still active" is one comparison inside the same query that lists or counts, never a
second pass in Python that a count could disagree with.

NO SPACE IS GUESSED. Every function takes the Space the conversation belongs to; `core/conversations.py`'s
provider resolves it (inbox/conversations.py).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core import state
from core.logging import get_logger

log = get_logger(__name__)

CLAIM_DAYS = 7          # SCOPE_AUTO_REPLY.md §9, decision 4 (recommended): a stalled sequence lets go in 7 days
MAX_DAYS = 30

_NOW = "strftime('%Y-%m-%dT%H:%M:%S','now')"
_ACTIVE = f"c.released_at IS NULL AND c.expires_at > {_NOW}"

# THE PREDICATE EVERY READER SHARES, over a conversation aliased `k`. One string, so the list, the counts,
# the drafter and the notice cannot disagree about whether a conversation is someone else's.
UNCLAIMED = ("NOT EXISTS (SELECT 1 FROM inbox_claims c WHERE c.space = k.space "
             f"AND c.zernio_conversation_id = k.zernio_conversation_id AND {_ACTIVE})")

# WHO HOLDS IT, for the row's tag: the claiming machine's title, or NULL.
HELD_BY = ("(SELECT c.title FROM inbox_claims c WHERE c.space = k.space "
           f"AND c.zernio_conversation_id = k.zernio_conversation_id AND {_ACTIVE})")


def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def claim(space: str, zcid: str, *, machine: str, title: str, days: float = CLAIM_DAYS) -> bool:
    """Claim this conversation for `machine`. -> True when `machine` holds it afterwards.

    ATOMIC: one statement inserts the claim, renews the caller's own, or takes over one that was released or
    has expired. It never takes a conversation another machine holds, so two automations can't both run one.
    Renewing extends the expiry; it is how a machine says it is still working on it."""
    days = max(0.01, min(float(days), MAX_DAYS))
    now = datetime.now(timezone.utc)
    with state.connect() as c:
        c.execute(
            "INSERT INTO inbox_claims (space, zernio_conversation_id, machine, title, claimed_at, "
            "                          expires_at, released_at, note) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL, NULL) "
            "ON CONFLICT(space, zernio_conversation_id) DO UPDATE SET "
            "  machine = excluded.machine, title = excluded.title, "
            "  claimed_at = CASE WHEN inbox_claims.machine = excluded.machine "
            f"                     AND inbox_claims.released_at IS NULL AND inbox_claims.expires_at > {_NOW} "
            "                    THEN inbox_claims.claimed_at ELSE excluded.claimed_at END, "
            "  expires_at = excluded.expires_at, released_at = NULL, note = NULL "
            " WHERE inbox_claims.machine = excluded.machine "
            f"   OR inbox_claims.released_at IS NOT NULL OR inbox_claims.expires_at <= {_NOW}",
            (space, str(zcid), machine, str(title)[:60], _stamp(now), _stamp(now + timedelta(days=days))))
        row = c.execute(
            "SELECT machine FROM inbox_claims c WHERE c.space = ? AND c.zernio_conversation_id = ? "
            f"AND {_ACTIVE}", (space, str(zcid))).fetchone()
    held = bool(row) and row["machine"] == machine
    log.info("inbox.claim", space=space, conversation=zcid, machine=machine, held=held)
    return held


def release(space: str, zcid: str, *, machine: str, note: str = "") -> bool:
    """End `machine`'s claim. -> True if it held one. `note` is what the person reads on the conversation
    ("Asked a question while the guide sequence was waiting for their email"); empty means it finished."""
    with state.connect() as c:
        cur = c.execute(
            "UPDATE inbox_claims SET released_at = ?, note = ? "
            " WHERE space = ? AND zernio_conversation_id = ? AND machine = ? AND released_at IS NULL",
            (_stamp(datetime.now(timezone.utc)), str(note or "").strip()[:280] or None,
             space, str(zcid), machine))
        done = cur.rowcount > 0
    log.info("inbox.claim_released", space=space, conversation=zcid, machine=machine, released=done,
             handed_back=bool(str(note or "").strip()))
    return done


def holder(space: str, zcid: str) -> dict | None:
    """The active claim on this conversation, or None: {"machine", "title", "claimed_at", "expires_at"}."""
    with state.connect() as c:
        row = c.execute(
            "SELECT machine, title, claimed_at, expires_at FROM inbox_claims c "
            f" WHERE c.space = ? AND c.zernio_conversation_id = ? AND {_ACTIVE}", (space, str(zcid))).fetchone()
    return dict(row) if row else None


def handed_back(space: str, zcid: str) -> dict | None:
    """The note an automation left when it handed this conversation back, if it did: {"title", "note", "at"}."""
    with state.connect() as c:
        row = c.execute(
            "SELECT title, note, released_at FROM inbox_claims "
            " WHERE space = ? AND zernio_conversation_id = ? AND released_at IS NOT NULL "
            "   AND note IS NOT NULL AND note != ''", (space, str(zcid))).fetchone()
    return {"title": row["title"], "note": row["note"], "at": row["released_at"]} if row else None
