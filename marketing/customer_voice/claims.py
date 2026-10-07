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
# A RESERVATION LASTS MINUTES, NOT DAYS (OSDev1's review of #2041). It only has to cover the gap until the machine's
# next sweep, 20 seconds away; if that sweep has died, everyone the machine wrote to would otherwise go unanswered for
# a week. Thirty minutes is ninety sweeps.
RESERVE_MINUTES = 30

_NOW = "strftime('%Y-%m-%dT%H:%M:%S','now')"
_ACTIVE = f"c.released_at IS NULL AND c.expires_at > {_NOW}"

# RESERVED: a machine started this conversation with a private reply to the person's comment, and hasn't claimed it
# yet (`reserve`). Matched on the person's own id or @handle, never a display name.
RESERVED = ("EXISTS (SELECT 1 FROM inbox_reservations r JOIN inbox_participant_ids p "
            "  ON p.space = r.space AND p.ident = r.ident "
            "  WHERE r.space = k.space AND p.zernio_conversation_id = k.zernio_conversation_id "
            f"   AND r.expires_at > {_NOW})")
# THE PREDICATE EVERY READER SHARES, over a conversation aliased `k`. One string, so the list, the counts,
# the drafter, the replies sent on their own and the notice cannot disagree about whether a conversation is someone
# else's: claimed, or reserved by the machine that started it.
UNCLAIMED = ("NOT EXISTS (SELECT 1 FROM inbox_claims c WHERE c.space = k.space "
             f"AND c.zernio_conversation_id = k.zernio_conversation_id AND {_ACTIVE}) AND NOT {RESERVED}")

# WHO HOLDS IT, for the row's tag: the claiming machine's title, or NULL.
HELD_BY = ("(SELECT c.title FROM inbox_claims c WHERE c.space = k.space "
           f"AND c.zernio_conversation_id = k.zernio_conversation_id AND {_ACTIVE})")


def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def claim(space: str, zcid: str, *, machine: str, title: str, days: float = CLAIM_DAYS,
          trigger: str | None = None) -> bool:
    """Claim this conversation for `machine`. -> True when `machine` holds it afterwards.

    NEVER BACK AFTER A PERSON TOOK IT OVER (OSDev1's reviews of #1790). Once a person's reply ended a machine's claim,
    no machine claims the conversation again, however the conversation goes on: the contact answering the owner is
    the owner's conversation, not a new start. ENFORCED, NOT TRUSTED: there is no argument a machine can pass to
    reopen it. Two things can: the owner handing it back (`hand_back`), or a NEW START the box itself can vouch for:
    `trigger`, the id of a comment the box read from the platform (`inbox_comments_seen`), written by this
    conversation's person (their id or @handle on `inbox_participant_ids`), newer than the takeover. A time, an id
    the box never read, someone else's comment or an older one opens nothing.

    ATOMIC: one statement inserts the claim, renews the caller's own, or takes over one that was released or
    has expired. It never takes a conversation another machine holds, so two automations can't both run one.
    Renewing extends the expiry; it is how a machine says it is still working on it."""
    days = max(0.01, min(float(days), MAX_DAYS))
    now = datetime.now(timezone.utc)
    # THE MACHINE HAS LOOKED: whether it takes the conversation or not, its reservation has done its job.
    _unreserve(space, zcid, machine)
    if taken_over(space, zcid) and not _new_start(space, zcid, trigger):
        log.info("inbox.claim_refused_taken_over", space=space, conversation=zcid, machine=machine)
        return False
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


def reserve(space: str, idents, *, machine: str, minutes: float = RESERVE_MINUTES) -> int:
    """THE CONVERSATION IS THE MACHINE'S FROM ITS FIRST MESSAGE (owner, 2026-10-07: "If I create a custom machine called
    the lead magnet machine ... how are they gonna play nice together"). A machine's private reply to a comment starts
    a conversation; the person writes back; until the machine's next sweep finds and claims it, the inbox would see an
    ordinary new message, and draft it, or answer it on its own. So the private reply reserves the person, by their id
    and @handle, and every reader of UNCLAIMED leaves the conversation alone until that machine claims it (or looks
    and declines: `claim` clears it either way), or the reservation expires (RESERVE_MINUTES). -> rows written.
    Never raises."""
    rows = sorted({str(i).strip().lower().lstrip("@") for i in (idents or ()) if str(i or "").strip()})
    if not rows:
        return 0
    now = datetime.now(timezone.utc)
    minutes = max(1.0, min(float(minutes), RESERVE_MINUTES))
    try:
        with state.connect() as c:
            c.executemany("INSERT OR REPLACE INTO inbox_reservations (space, ident, machine, created_at, expires_at) "
                          "VALUES (?, ?, ?, ?, ?)",
                          [(space, i, machine, _stamp(now), _stamp(now + timedelta(minutes=minutes))) for i in rows])
        log.info("inbox.reserved", space=space, machine=machine, idents=len(rows))
        return len(rows)
    except Exception as e:                          # noqa: BLE001 — the reply already went; this is bookkeeping
        log.warning("inbox.reserve_failed", space=space, machine=machine, error=type(e).__name__)
        return 0


def reserved(space: str, zcid: str) -> dict | None:
    """The machine that started this conversation and hasn't claimed it yet, or None: {"machine"}."""
    with state.connect() as c:
        row = c.execute(
            "SELECT r.machine FROM inbox_reservations r JOIN inbox_participant_ids p "
            "  ON p.space = r.space AND p.ident = r.ident "
            f" WHERE r.space = ? AND p.zernio_conversation_id = ? AND r.expires_at > {_NOW} LIMIT 1",
            (space, str(zcid))).fetchone()
    return {"machine": row["machine"]} if row else None


def expire_reservations() -> int:
    """Clear reservations that lapsed unclaimed, each said in the log as `inbox.reservation_expired`: a machine wrote
    to someone and never took the conversation, which means its sweep stopped or it looked and let them be. Already
    ignored by every reader (RESERVED compares the time), so this only tidies and tells. -> rows cleared. Never
    raises; a periodic in customer_voice/__init__.py calls it."""
    try:
        with state.connect() as c:
            gone = c.execute("SELECT space, machine, COUNT(*) AS n FROM inbox_reservations "
                             f"WHERE expires_at <= {_NOW} GROUP BY space, machine").fetchall()
            c.execute(f"DELETE FROM inbox_reservations WHERE expires_at <= {_NOW}")
    except Exception as e:                          # noqa: BLE001 — a box without the table yet
        log.warning("inbox.reservation_sweep_failed", error=type(e).__name__)
        return 0
    for r in gone:
        log.warning("inbox.reservation_expired", space=r["space"], machine=r["machine"], idents=r["n"])
    return sum(r["n"] for r in gone)


def _unreserve(space: str, zcid: str, machine: str) -> None:
    with state.connect() as c:
        c.execute("DELETE FROM inbox_reservations WHERE space = ? AND machine = ? AND ident IN "
                  "(SELECT ident FROM inbox_participant_ids WHERE space = ? AND zernio_conversation_id = ?)",
                  (space, machine, space, str(zcid)))


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


TAKEN_OVER = "You replied, so {title} stopped. It's yours from here."


def take_over(space: str, zcid: str) -> bool:
    """A PERSON ANSWERED, so the automation stops (OSDev1's review of #1753). Ends whatever claim is active, with
    a note saying why, whichever machine holds it. -> True if one was ended. Never raises: the person's reply
    has already gone, and bookkeeping must not undo it."""
    try:
        held = holder(space, zcid)
        if not held:
            # A PERSON ANSWERED BEFORE THE MACHINE CLAIMED IT: the conversation is theirs, so the machine that started
            # it never takes it now (its claim is refused as taken over, and it stops that lead).
            pending = reserved(space, zcid)
            if not pending:
                return False
            with state.connect() as c:
                c.execute("INSERT OR REPLACE INTO inbox_takeovers (space, zernio_conversation_id, taken_at) "
                          "VALUES (?, ?, ?)", (space, str(zcid), _stamp(datetime.now(timezone.utc))))
            _unreserve(space, zcid, pending["machine"])
            return True
        with state.connect() as c:
            c.execute("INSERT OR REPLACE INTO inbox_takeovers (space, zernio_conversation_id, taken_at) "
                      "VALUES (?, ?, ?)", (space, str(zcid), _stamp(datetime.now(timezone.utc))))
        return release(space, zcid, machine=held["machine"], note=TAKEN_OVER.format(title=held["title"]))
    except Exception as e:                          # noqa: BLE001
        log.warning("inbox.take_over_failed", space=space, conversation=zcid, error=type(e).__name__)
        return False


def _when(v):
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _new_start(space: str, zcid: str, trigger: str | None) -> bool:
    """Does `trigger` (a comment id) prove a new start from this conversation's person, after the takeover? If so the
    takeover is lifted, once."""
    if not trigger:
        return False
    with state.connect() as c:
        took = c.execute("SELECT taken_at FROM inbox_takeovers WHERE space = ? AND zernio_conversation_id = ?",
                         (space, str(zcid))).fetchone()
        seen = c.execute("SELECT author_id, author_handle, at FROM inbox_comments_seen WHERE space = ? "
                         "AND comment_id = ?", (space, str(trigger))).fetchone()
        if not took or not seen:
            return False
        theirs = {r["ident"] for r in c.execute("SELECT ident FROM inbox_participant_ids WHERE space = ? "
                                                "AND zernio_conversation_id = ?", (space, str(zcid))).fetchall()}
        author = {v for v in (seen["author_id"], seen["author_handle"]) if v}
        new, taken = _when(seen["at"]), _when(took["taken_at"])
        if not (author & theirs) or new is None or taken is None or new <= taken:
            return False
        c.execute("DELETE FROM inbox_takeovers WHERE space = ? AND zernio_conversation_id = ?", (space, str(zcid)))
    log.info("inbox.takeover_lifted_by_new_comment", space=space, conversation=zcid, comment=str(trigger))
    return True


def taken_over(space: str, zcid: str) -> bool:
    with state.connect() as c:
        return c.execute("SELECT 1 FROM inbox_takeovers WHERE space = ? AND zernio_conversation_id = ?",
                         (space, str(zcid))).fetchone() is not None


def hand_back(space: str, zcid: str) -> bool:
    """The OWNER gives a conversation he took over back to the machines. -> True if it was taken over."""
    with state.connect() as c:
        cur = c.execute("DELETE FROM inbox_takeovers WHERE space = ? AND zernio_conversation_id = ?",
                        (space, str(zcid)))
        return cur.rowcount > 0


def person_wrote_since(space: str, zcid: str, since: str) -> bool:
    """Has a person written on this conversation since `since` (a claim's `claimed_at`)? Counts a reply sent
    from the box AND one typed in the platform's own app, which the poller mirrors as sent_by='human'."""
    from datetime import datetime, timezone

    def _at(v):
        try:
            d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except ValueError:
            return None
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

    start = _at(since)
    if start is None:
        return False
    with state.connect() as c:
        # WHEN IT WAS SENT, NOT WHEN THE BOX MIRRORED IT: a poll that catches up on an old reply must not read
        # as a person answering just now. The platform's own stamp where there is one, else the mirror's.
        rows = c.execute("SELECT COALESCE(s.sent_at, m.created_at) AS at FROM inbox_messages m "
                         "LEFT JOIN inbox_message_sent s ON s.message_id = m.id "
                         "WHERE m.space = ? AND m.zernio_conversation_id = ? AND m.direction = 'out' "
                         "AND m.sent_by = 'human'", (space, str(zcid))).fetchall()
    return any((_at(r["at"]) or start) > start for r in rows)

