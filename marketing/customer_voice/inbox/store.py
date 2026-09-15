"""Unified Inbox state — conversations, message mirror, send ledger, poll watermark.

Every row carries `space` (the tenant boundary, rubric #1). The two guarantees
that live HERE, at the SQL layer, not in caller discipline:

  - ONE opener per conversation: `claim_opener` is an atomic UPDATE on
    `opener_sent_at IS NULL` — a requeue, a second worker, or a double poll can
    never send a prospect two openers (the reel `claim_for_produce` pattern).
  - Exactly-once sends: `inbox_send_ledger.idem_key UNIQUE` — every send is recorded
    once, with status sending|ok|indeterminate|failed for reconcile. A send with a
    human behind it CLAIMS its key first (`claim_send`) and resolves it after, so a
    double-click cannot produce two vendor calls.
"""
import uuid

from core import state
from core.logging import get_logger

log = get_logger(__name__)


def upsert_conversation(*, space: str, zcid: str, platform: str = "messenger",
                        ad_meta_id: str | None = None, ad_title: str | None = None,
                        participant: str | None = None,
                        last_inbound_at: str | None = None,
                        account_id: str | None = None) -> dict:
    """Insert-or-refresh one conversation; returns the row. Ad attribution is
    written once (first non-null wins — the ad that STARTED the thread).

    `account_id` IS WHAT LETS A PERSON REPLY LATER. The poller has it on every sweep and used to
    pass it only into the job payload, so the automated opener could send and the SCREEN could
    not — a human opening the thread an hour later had no account to send from. Newest non-null
    wins rather than first, unlike the ad fields: if the vendor ever re-homes a thread, the
    current owner is the one that can send, while the ad that started it is history."""
    now = state._now()
    with state.connect() as c:
        c.execute(
            "INSERT INTO inbox_conversations (id, space, zernio_conversation_id, "
            "platform, ad_meta_id, ad_title, participant, last_inbound_at, "
            "account_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(space, zernio_conversation_id) DO UPDATE SET "
            "  ad_meta_id = COALESCE(inbox_conversations.ad_meta_id, excluded.ad_meta_id), "
            "  ad_title   = COALESCE(inbox_conversations.ad_title, excluded.ad_title), "
            "  participant = COALESCE(excluded.participant, inbox_conversations.participant), "
            "  last_inbound_at = COALESCE(excluded.last_inbound_at, inbox_conversations.last_inbound_at), "
            "  account_id = COALESCE(excluded.account_id, inbox_conversations.account_id), "
            "  updated_at = excluded.updated_at",
            (str(uuid.uuid4()), space, zcid, platform, ad_meta_id, ad_title,
             participant, last_inbound_at, account_id, now, now))
        row = c.execute(
            "SELECT * FROM inbox_conversations WHERE space = ? AND "
            "zernio_conversation_id = ?", (space, zcid)).fetchone()
    return dict(row)


def get_conversation(space: str, zcid: str) -> dict | None:
    with state.connect() as c:
        row = c.execute(
            "SELECT * FROM inbox_conversations WHERE space = ? AND "
            "zernio_conversation_id = ?", (space, zcid)).fetchone()
    return dict(row) if row else None


def list_conversations(space: str, *, limit: int = 50, offset: int = 0,
                       platform: str | None = None) -> list[dict]:
    """The conversations in one Space, newest inbound first — the inbox screen.

    THE STORE HAD NO READER A SCREEN COULD USE. `get_conversation` answers about ONE, by id, and
    everything else here is a write path or a claim. OSDev4 counted this as uncounted work in
    docs/SPEC_CUSTOMER_VOICE_PHONE_APP.md §5.5, and he was right: "just reuse what is there" was
    never the whole story for the one screen the product exists for.

    ORDERED BY `last_inbound_at`, NOT `updated_at`. `updated_at` moves when WE touch a row — an
    opener claim, an opt-out, a watermark — so ordering on it floats conversations to the top
    because the machine did something, not because a person said something. The question this
    screen answers is "who has spoken to me most recently", and only the inbound clock answers it.
    NULLs last, so a row we know about but have never heard from does not outrank a live one.

    SPACE IS NOT OPTIONAL and there is no all-Spaces variant on purpose (rubric #1, the tenant
    boundary). A phone screen that could be talked into listing another client's conversations is
    the single worst bug available in this file, and the way that arrives is a convenience default.

    COUNTS THE MESSAGES IN THE SAME PASS, because the alternative is one query per row on a page
    of fifty — the N+1 that makes a phone screen feel broken on a slow connection.
    """
    # `platform` IS A PREDICATE, NEVER AN INTERPOLATION. It arrives from a query string, so it
    # goes in as a bound parameter like the Space does. A chip row that built SQL from its own
    # label would be the one place on this screen an outsider chooses a fragment of the query.
    where, args = "k.space = ?", [space]
    if platform:
        where += " AND k.platform = ?"
        args.append(str(platform))
    args += [int(limit), int(offset)]
    with state.connect() as c:
        rows = c.execute(
            "SELECT k.*, "
            "       (SELECT COUNT(*) FROM inbox_messages m "
            "         WHERE m.space = k.space "
            "           AND m.zernio_conversation_id = k.zernio_conversation_id) AS message_count "
            "  FROM inbox_conversations k "
            f" WHERE {where} "
            " ORDER BY (k.last_inbound_at IS NULL), k.last_inbound_at DESC, k.id ASC "
            " LIMIT ? OFFSET ?", tuple(args)).fetchall()
    return [dict(r) for r in rows]


def platforms_present(space: str) -> list[dict]:
    """Which channels this box ACTUALLY HAS ROWS FOR, with a count each. Never a menu of hopes.

    THE CHIP ROW IS BUILT FROM THIS AND FROM NOTHING ELSE. A hardcoded list of every channel we
    would like to support renders five dead chips on a box with only Messenger connected, which
    is the empty-section failure `marketing/customer_voice/rails.py` refuses by design:
    "ownership is declared, never inferred". A chip that filters to nothing is a control that
    cannot succeed, and this codebase keeps deleting those by name.
    """
    with state.connect() as c:
        rows = c.execute(
            "SELECT platform, COUNT(*) AS n FROM inbox_conversations "
            " WHERE space = ? GROUP BY platform "
            " ORDER BY n DESC, platform ASC", (space,)).fetchall()
    return [{"platform": r["platform"], "n": int(r["n"])} for r in rows]


def messages_for(space: str, zcid: str, *, limit: int = 200) -> list[dict]:
    """One conversation's messages, oldest first — the way a person reads a thread.

    OLDEST FIRST, unlike the list above, and the difference is not an inconsistency: a LIST answers
    "what is new", a THREAD answers "what was said", and a thread read newest-first is unreadable.

    SCOPED ON SPACE AS WELL AS THE CONVERSATION ID, even though `zernio_conversation_id` is
    vendor-unique. The id arrives from a URL, so scoping on it alone would make a Space boundary
    depend on a vendor's uniqueness guarantee and on nobody ever guessing an id. Two predicates
    cost nothing and the boundary stops resting on an external promise.
    """
    # WHICH HUMAN, not merely "a human". `sent_by` separates `ai` from `human`; the day a second
    # person signs in, "human" stops being an answer and the screen starts lying quietly. The
    # ledger recorded who hit send (§3.3) and both tables carry `zernio_message_id`, so the link
    # already exists — LEFT JOINs, because a message the machine sent, or one that arrived
    # inbound, has no ledger row and must still render. SQLite will not match NULL to NULL, so
    # rows with no vendor id cannot accidentally join to each other.
    with state.connect() as c:
        rows = c.execute(
            "SELECT m.*, l.user_id AS sender_user_id, u.name AS sender_name, "
            "       u.email AS sender_email "
            "  FROM inbox_messages m "
            "  LEFT JOIN inbox_send_ledger l "
            "         ON l.space = m.space AND l.zernio_message_id = m.zernio_message_id "
            "  LEFT JOIN users u ON u.id = l.user_id "
            " WHERE m.space = ? AND m.zernio_conversation_id = ? "
            " ORDER BY m.created_at ASC, m.id ASC LIMIT ?",
            (space, zcid, int(limit))).fetchall()
    return [dict(r) for r in rows]


def set_opted_out(space: str, zcid: str) -> None:
    with state.connect() as c:
        c.execute("UPDATE inbox_conversations SET opted_out = 1, updated_at = ? "
                  "WHERE space = ? AND zernio_conversation_id = ?",
                  (state._now(), space, zcid))
    log.info("inbox.opted_out", space=space, conversation=zcid)


def claim_opener(space: str, zcid: str) -> bool:
    """Atomic one-opener claim. True = we won (proceed to send); False = an
    opener was already sent/claimed for this conversation (no-op)."""
    with state.connect() as c:
        cur = c.execute(
            "UPDATE inbox_conversations SET opener_sent_at = ?, updated_at = ? "
            "WHERE space = ? AND zernio_conversation_id = ? AND opener_sent_at IS NULL",
            (state._now(), state._now(), space, zcid))
        return cur.rowcount == 1


def release_opener(space: str, zcid: str) -> None:
    """Undo a claim whose send DETERMINATELY failed (nothing left the box) so a
    retry can re-claim. Guarded on 'no ok/indeterminate/sending ledger row exists' —
    a send that may have landed is never released (no double-send window). `sending`
    joined that list with `claim_send`: an unresolved claim is a call that may be in
    flight or may have died after the vendor took it, so it reads as 'may have landed'
    everywhere this table is asked that question."""
    with state.connect() as c:
        c.execute(
            "UPDATE inbox_conversations SET opener_sent_at = NULL, updated_at = ? "
            "WHERE space = ? AND zernio_conversation_id = ? AND NOT EXISTS ("
            "  SELECT 1 FROM inbox_send_ledger l WHERE l.space = ? "
            "  AND l.zernio_conversation_id = ? "
            "  AND l.status IN ('ok','indeterminate','sending'))",
            (state._now(), space, zcid, space, zcid))


def record_send(*, space: str, zcid: str, idem_key: str, kind: str, status: str,
                zernio_message_id: str | None = None, error: str | None = None,
                user_id: str | None = None) -> bool:
    """Ledger write, exactly-once by idem_key. Returns False if already recorded.

    `user_id` is WHICH HUMAN hit send; NULL is the machine, which is what every row written
    before per-person login is. The thread screen claims to say who said every line, and once a
    second person can sign in, `sent_by='human'` stops being an answer on its own."""
    with state.connect() as c:
        # A FAILED ROW IS THE ONE THING A LATER ATTEMPT MAY OVERWRITE, and nothing else is.
        #
        # `INSERT OR IGNORE` alone made the ledger lie: a determinate failure wrote `failed`
        # under the key, and if that exact attempt was retried and SUCCEEDED, the row stayed
        # `failed` forever — so the ledger said a message never went out while the customer was
        # reading it. `ok` and `indeterminate` are still absolutely immutable: one records a send
        # that happened, the other a send that MIGHT have, and re-writing either is how a
        # double-send gets authorised. The WHERE on DO UPDATE is what draws that line, and
        # rowcount stays honest — 1 when this call decided the outcome, 0 when it was absorbed.
        cur = c.execute(
            "INSERT INTO inbox_send_ledger (id, space, "
            "zernio_conversation_id, idem_key, kind, zernio_message_id, status, "
            "error, user_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(idem_key) DO UPDATE SET "
            "  status = excluded.status, "
            "  zernio_message_id = excluded.zernio_message_id, "
            "  error = excluded.error, "
            "  user_id = excluded.user_id, "
            "  created_at = excluded.created_at "
            "WHERE inbox_send_ledger.status = 'failed'",
            (str(uuid.uuid4()), space, zcid, idem_key, kind, zernio_message_id,
             status, (error or "")[:300] or None, user_id, state._now()))
        return cur.rowcount > 0


def sends_last_hour(space: str) -> int:
    """Sends in the rolling hour — the deliverability rate-cap input.

    COUNTS `sending` TOO. The cap exists to stop a box hammering a platform, and a claimed
    attempt either is on the wire right now or died on it; either way the platform may have
    seen it. Leaving it out would let a burst of in-flight sends read as zero and slip the cap."""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with state.connect() as c:
        return c.execute(
            "SELECT COUNT(*) AS n FROM inbox_send_ledger WHERE space = ? "
            "AND status IN ('ok','indeterminate','sending') AND created_at > ?",
            (space, cutoff)).fetchone()["n"]


def oldest_counted_send(space: str) -> str | None:
    """created_at of the OLDEST send still counted by the rolling-hour cap — the
    moment it ages out is when a capacity slot frees. None when nothing counts
    (cap misconfigured to 0, or the rows aged out between check and query).

    THE STATUS LIST HERE MUST MATCH `sends_last_hour` EXACTLY — this answers "when does a slot
    free" about the very set that one counts, so a status in one and not the other quietly
    reports a slot that has not freed."""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with state.connect() as c:
        row = c.execute(
            "SELECT MIN(created_at) AS oldest FROM inbox_send_ledger WHERE space = ? "
            "AND status IN ('ok','indeterminate','sending') AND created_at > ?",
            (space, cutoff)).fetchone()
    return row["oldest"] if row and row["oldest"] else None


def claim_send(*, space: str, zcid: str, idem_key: str, kind: str,
               user_id: str | None = None) -> bool:
    """Take the idempotency key BEFORE the vendor call. True = you own this attempt.

    THE RACE THIS CLOSES IS A DOUBLE-CLICK, and it is OSDev1's finding on #1145. The send path
    used to read the ledger, call the vendor, and write the row only afterwards. The compose form
    has no submit guard, so two POSTs carrying the same nonce both reached the read, both saw
    nothing, and **the customer got the message twice**. gthread workers run them concurrently,
    so this is not theoretical. A read followed by a write is not a claim; one statement is.

    `INSERT … ON CONFLICT DO UPDATE … WHERE status='failed'` is that one statement. The insert
    wins when the key is new. The update wins ONLY over a row that provably never reached anyone,
    which is what lets a person retry a failed send without rewording. Every other prior state —
    `ok`, `indeterminate`, and another request's live `sending` — leaves rowcount 0, and the
    caller re-reads the row to say which it was. This is `claim_for_post`
    (`marketing/content_machine/reel/scripts_store.py:130`) applied to the ledger's own key: the
    repo's law-of-the-land pattern for an irreversible act, not a new invention.

    `sending` IS A FOURTH STATUS AND IT IS NOT A SYNONYM FOR "in progress". It is the only status
    its owning request may overwrite (`resolve_send`), which is what keeps `ok` and
    `indeterminate` absolutely immutable. A `sending` row that outlives its request — the process
    died mid-call — is INDETERMINATE by invariant 4: we cannot know whether the vendor took it,
    so it is never resent automatically, it counts against the deliverability cap, and the
    watchdog pages for it exactly as it pages for an `indeterminate` row.
    """
    with state.connect() as c:
        cur = c.execute(
            "INSERT INTO inbox_send_ledger (id, space, "
            "zernio_conversation_id, idem_key, kind, status, user_id, created_at) "
            "VALUES (?,?,?,?,?,'sending',?,?) "
            "ON CONFLICT(idem_key) DO UPDATE SET "
            "  status = 'sending', "
            "  zernio_message_id = NULL, "
            "  error = NULL, "
            "  user_id = excluded.user_id, "
            "  created_at = excluded.created_at "
            "WHERE inbox_send_ledger.status = 'failed'",
            (str(uuid.uuid4()), space, zcid, idem_key, kind, user_id, state._now()))
        return cur.rowcount > 0


def resolve_send(*, space: str, idem_key: str, status: str,
                 zernio_message_id: str | None = None,
                 error: str | None = None) -> bool:
    """Write the outcome onto a row THIS request claimed. False = it was not ours to resolve.

    Guarded on `status='sending'`, which is the half of the claim that makes it safe. `record_send`
    deliberately keeps its own narrower contract (`ok` and `indeterminate` immutable, `failed`
    overwritable) because the OPENER path shares it and never claims — widening that WHERE would
    have let the opener path stamp over a reply another request is mid-flight on.
    """
    with state.connect() as c:
        cur = c.execute(
            "UPDATE inbox_send_ledger SET status = ?, zernio_message_id = ?, error = ? "
            "WHERE space = ? AND idem_key = ? AND status = 'sending'",
            (status, zernio_message_id, (error or "")[:300] or None, space, idem_key))
        return cur.rowcount > 0


def get_send(space: str, idem_key: str) -> dict | None:
    """The ledger row for one idempotency key, or None — lets the handler tell a
    genuinely-opened thread apart from a claim whose send never happened (crash
    between claim and send)."""
    with state.connect() as c:
        row = c.execute(
            "SELECT * FROM inbox_send_ledger WHERE space = ? AND idem_key = ?",
            (space, idem_key)).fetchone()
    return dict(row) if row else None


def record_message(*, space: str, zcid: str, zmid: str | None, direction: str,
                   sent_by: str, body: str | None) -> None:
    """Mirror one message (idempotent by the vendor message id)."""
    with state.connect() as c:
        c.execute(
            "INSERT OR IGNORE INTO inbox_messages (id, space, "
            "zernio_conversation_id, zernio_message_id, direction, sent_by, body, "
            "created_at) VALUES (?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), space, zcid, zmid, direction, sent_by,
             (body or "")[:2000], state._now()))


def get_watermark(space: str, zcid: str) -> dict | None:
    with state.connect() as c:
        row = c.execute("SELECT * FROM inbox_state WHERE space = ? AND "
                        "zernio_conversation_id = ?", (space, zcid)).fetchone()
    return dict(row) if row else None


def set_watermark(space: str, zcid: str, *, last_seen_msg_id: str | None,
                  last_activity: str | None) -> None:
    with state.connect() as c:
        c.execute(
            "INSERT INTO inbox_state (space, zernio_conversation_id, "
            "last_seen_msg_id, last_activity, updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(space, zernio_conversation_id) DO UPDATE SET "
            "last_seen_msg_id = excluded.last_seen_msg_id, "
            "last_activity = excluded.last_activity, updated_at = excluded.updated_at",
            (space, zcid, last_seen_msg_id, last_activity, state._now()))
