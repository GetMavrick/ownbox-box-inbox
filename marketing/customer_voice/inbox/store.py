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
import hashlib
import uuid

from core import state
from core.logging import get_logger

log = get_logger(__name__)


# WAITING ON A PERSON, AS ONE SQL FRAGMENT SHARED BY EVERY READER. Written once because they must
# agree: a morning page saying four conversations are waiting, over a screen that tags three, is
# worse than neither — he stops trusting the number, and then he stops reading the page.
#
# CORRELATED, NOT A GROUP-BY JOIN. It reads as the sentence it means — "the newest message on this
# conversation came IN" — and it walks the (space, conversation, created_at) shape the messages
# index already serves. `m.id DESC` breaks a tie on identical timestamps so the answer is stable
# between two runs rather than whichever row the planner happened to reach first.
# COALESCE BECAUSE A CONVERSATION WITH NO MESSAGES ANSWERS NULL, NOT 0. The poller knows about
# rows it has never heard a word on; the subquery returns NULL there, `NULL = 'in'` is NULL, and a
# screen handed NULL for a yes/no column has to know that NULL means no. It is falsy either way in
# Python, which is exactly why it would have survived review and then surprised somebody writing
# `if row["awaiting_reply"] is False`. The column answers 0 or 1, always.
# THE NEWEST MESSAGE ITSELF — what the row has been missing since it was built.
#
# The list said "1 message". The one thing a person opens an inbox to read was the one thing the
# list would not tell them, so every row had to be opened to learn whether it mattered. Owner,
# 2026-09-16: "Make sure that the messages are readable" — a row that shows a COUNT of messages
# is not readable at any contrast.
#
# SAME PASS, SAME REASON AS THE TWO ABOVE. Fifty rows must cost one query, not fifty-one, and a
# correlated subquery over the (space, conversation, created_at) index is what the other two
# already do. It is ordered identically to `_NEWEST_IS_INBOUND` — same ORDER BY, same tie-break —
# so the preview and the "waiting on you" flag can never describe two different messages.
#
# TRIMMED IN SQL, NOT IN THE SCREEN. A pasted email can be tens of kilobytes; fifty of them is a
# megabyte crossing the wire to render one truncated line on a phone. 240 is far more than a row
# can show and small enough that the page stays a page.
_NEWEST_BODY = (
    "(SELECT SUBSTR(COALESCE(m.body, ''), 1, 240) FROM inbox_messages m "
    "  WHERE m.space = k.space "
    "    AND m.zernio_conversation_id = k.zernio_conversation_id "
    "  ORDER BY m.created_at DESC, m.id DESC LIMIT 1)")

_NEWEST_IS_INBOUND = (
    "COALESCE((SELECT m.direction FROM inbox_messages m "
    "           WHERE m.space = k.space "
    "             AND m.zernio_conversation_id = k.zernio_conversation_id "
    "           ORDER BY m.created_at DESC, m.id DESC LIMIT 1) = 'in', 0)")

# HAS THE CUSTOMER EVER WRITTEN ON THIS THREAD? THE FACT, NOT THE CLOCK COLUMN.
#
# `k.last_inbound_at` is an INFERENCE a poller fills, and two paths leave it NULL on a thread the
# customer plainly did write on: `upsert_conversation` only ever COALESCEs a non-null value in, and
# `email_channel` passes None whenever the message it is mirroring happens to be outbound. The
# messages themselves are the fact — the screen was hiding its reply box on the inference while
# rendering the customer's own words directly above the sentence saying they had never written.
#
# DELIBERATELY NOT `_NEWEST_IS_INBOUND`. That one asks "is this thread waiting on a reply", which
# goes false the moment somebody answers — and a thread you have already answered is the most
# ordinary thing in an inbox to answer again. This asks whether there is anything to reply TO,
# ever, which is the question the reply box and the row's Reply item both actually have.
#
# EXISTS, NOT COUNT: it stops at the first inbound row, and it is selected in the same pass as the
# other two subselects for the reason stated above — fifty rows must cost one query, not fifty-one.
_HAS_INBOUND = (
    "EXISTS(SELECT 1 FROM inbox_messages m "
    "        WHERE m.space = k.space "
    "          AND m.zernio_conversation_id = k.zernio_conversation_id "
    "          AND m.direction = 'in')")

# HAS ANYTHING ARRIVED SINCE HE LAST OPENED THIS CONVERSATION? The one fact that lets the row put
# an unread message in bold and a read one in regular — owner, 2026-09-17: "Just like a normal
# inbox."
#
# INBOUND ONLY. Our own reply appearing in bold would mean the box was telling him he had not read
# something he wrote himself, and the drafter mirrors machine sends through this same table.
#
# `read_at IS NULL` IS UNREAD, NOT READ. Null means never opened, which is true of every row that
# predates the column. Defaulting those to read would hide customers who are genuinely waiting
# behind a column invented after they wrote in.
#
# STRICTLY NEWER THAN `read_at`, so opening a thread clears it and the next arrival sets it again.
# Comparing to a message's `created_at` — MIRROR time, not send time — is right here for the exact
# reason it was wrong for the send window in #1304: the question is what has reached this box since
# he looked, and mirror time is when it reached the box.
#
# SAME PASS, SAME REASON AS THE THREE ABOVE: fifty rows must cost one query, not fifty-one.
_UNREAD = (
    "EXISTS(SELECT 1 FROM inbox_messages m "
    "        WHERE m.space = k.space "
    "          AND m.zernio_conversation_id = k.zernio_conversation_id "
    "          AND m.direction = 'in' "
    "          AND (k.read_at IS NULL OR m.created_at > k.read_at))")


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


# WAITING ON A PERSON, AS A PREDICATE. `awaiting_reply()` counts these and the two readers below
# filter by them, and the count and the list MUST agree: a header that says "3 waiting on you" over
# a filtered list of five is a screen nobody trusts again. So both are this one string, including
# the opted-out carve-out — somebody who said STOP is not waiting for a reply.
_WAITING = f"k.opted_out = 0 AND {_NEWEST_IS_INBOUND}"

# CAME FROM AN AD HE PAID FOR. `ad_meta_id` and `ad_title` are written by the poller at INSERT for
# a click-to-message conversation and by nothing else; the row already wears a "From <ad_title>"
# tag because attribution is the one thing on a row that says what the conversation is WORTH. This
# turns that existing fact into a filter — it invents no new idea of a lead.
#
# TRIMMED, NOT JUST NOT-NULL. A vendor that sends an empty string for an ad id is not an ad, and
# the difference between NULL and "" is exactly the kind of thing that puts every conversation in
# the box behind a filter labelled "from an ad".
_FROM_AD = "TRIM(COALESCE(k.ad_meta_id, '')) <> ''"


def list_conversations(space: str, *, limit: int = 50, offset: int = 0,
                       platform: str | None = None, waiting: bool = False,
                       from_ad: bool = False) -> list[dict]:
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
    # NO BOUND PARAMETER, BECAUSE THERE IS NO VALUE — `waiting` is a Python bool the route already
    # reduced a query string to, and what it appends is a fixed fragment written in this file.
    if waiting:
        where += f" AND {_WAITING}"
    if from_ad:
        where += f" AND {_FROM_AD}"
    args += [int(limit), int(offset)]
    with state.connect() as c:
        rows = c.execute(
            "SELECT k.*, "
            "       (SELECT COUNT(*) FROM inbox_messages m "
            "         WHERE m.space = k.space "
            "           AND m.zernio_conversation_id = k.zernio_conversation_id) AS message_count, "
            # THE ROW TAG THE SCREEN ASKED FOR IN A COMMENT. app.py could not compute "waiting on
            # you" without the direction of the newest message and would not pay a query per row,
            # so it left the note for this file. Selected in the same pass as the count, for the
            # same reason: fifty rows must cost one query, not fifty-one. BOTH READERS GET IT —
            # the list and the search — because a row that is waiting does not stop waiting
            # because somebody typed a name into a box.
            f"       ({_NEWEST_IS_INBOUND}) AS awaiting_reply, "
            f"       ({_NEWEST_BODY}) AS preview, "
            f"       ({_HAS_INBOUND}) AS has_inbound, "
            f"       ({_UNREAD}) AS unread "
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


def search_conversations(space: str, query: str, *, limit: int = 50,
                         offset: int = 0, platform: str | None = None,
                         waiting: bool = False, from_ad: bool = False) -> list[dict]:
    """Conversations whose MESSAGES or participant match `query`, newest inbound first.

    THE CARD SELLS THIS AND THE BOX DID NOT HAVE IT. `$499` bullet 6 is "Search everything", and
    until now the only retrieval on this screen was `list_conversations` — fifty most-recent rows
    filtered by channel (docs/AUDIT_499_CARD.md §6). A person looking for what a customer said
    last month had no way to ask.

    SEARCHES THE MESSAGE BODIES, NOT JUST THE HEADER. Matching only `participant` would answer
    "who" and never "what", and "what did they say about the leak" is the question somebody
    actually has with an inbox open. So this joins to `inbox_messages` and matches `body`, with
    the participant name as a second way in for when the name IS the thing remembered.

    ONE ROW PER CONVERSATION. The join can match many messages in one thread; `EXISTS` keeps the
    result a list of conversations rather than a list of hits, so the screen renders the same
    shape `list_conversations` gives it and a thread with forty matches does not bury nine others.

    THE QUERY IS A BOUND PARAMETER AND THE WILDCARDS ARE OURS. It arrives from a query string, so
    it is never interpolated — and the `%` and `_` a person might type are escaped with an
    explicit ESCAPE clause, because unescaped they are wildcards: a search for `100%` would
    otherwise match every row in the box and read as "search is broken". `\` is escaped first,
    or escaping the others would double-escape it.

    SPACE IS NOT OPTIONAL, for the same reason `list_conversations` says so: a reader that could
    be talked into searching another client's conversations is the worst bug available in this
    file, and it arrives as a convenience default. There is no all-Spaces variant.

    AN EMPTY QUERY RETURNS NOTHING, not everything. A blank box is a person who has not asked yet;
    answering it with the whole inbox would make the screen flicker between two meanings of empty.
    """
    q = str(query or "").strip()
    if not q:
        return []
    # `\` FIRST. Escaping `%` and `_` before `\` would turn the escape characters this function
    # just inserted into literals on the next pass.
    esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like = f"%{esc}%"
    where, args = "k.space = ?", [space]
    if platform:
        where += " AND k.platform = ?"
        args.append(str(platform))
    # SEARCH KEEPS THE FILTER IT WAS GIVEN. Typing a name while looking at the unanswered list is
    # narrowing that list, not leaving it — dropping the filter here would quietly hand back
    # conversations already dealt with, among them the one he was trying NOT to see.
    if waiting:
        where += f" AND {_WAITING}"
    if from_ad:
        where += f" AND {_FROM_AD}"
    args += [like, space, like, int(limit), int(offset)]
    with state.connect() as c:
        rows = c.execute(
            "SELECT k.*, "
            "       (SELECT COUNT(*) FROM inbox_messages m "
            "         WHERE m.space = k.space "
            "           AND m.zernio_conversation_id = k.zernio_conversation_id) AS message_count, "
            # THE ROW TAG THE SCREEN ASKED FOR IN A COMMENT. app.py could not compute "waiting on
            # you" without the direction of the newest message and would not pay a query per row,
            # so it left the note for this file. Selected in the same pass as the count, for the
            # same reason: fifty rows must cost one query, not fifty-one. BOTH READERS GET IT —
            # the list and the search — because a row that is waiting does not stop waiting
            # because somebody typed a name into a box.
            f"       ({_NEWEST_IS_INBOUND}) AS awaiting_reply, "
            f"       ({_NEWEST_BODY}) AS preview, "
            f"       ({_HAS_INBOUND}) AS has_inbound, "
            f"       ({_UNREAD}) AS unread "
            "  FROM inbox_conversations k "
            f" WHERE {where} "
            "   AND ( COALESCE(k.participant, '') LIKE ? ESCAPE '\\' "
            "      OR EXISTS (SELECT 1 FROM inbox_messages m "
            "                  WHERE m.space = ? "
            "                    AND m.zernio_conversation_id = k.zernio_conversation_id "
            "                    AND COALESCE(m.body, '') LIKE ? ESCAPE '\\') ) "
            " ORDER BY (k.last_inbound_at IS NULL), k.last_inbound_at DESC, k.id ASC "
            " LIMIT ? OFFSET ?", tuple(args)).fetchall()
    return [dict(r) for r in rows]


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


def mark_read(space: str, zcid: str) -> None:
    """He has looked at this conversation. Called when the thread is opened.

    A GET THAT WRITES, DELIBERATELY. It is what every inbox does — opening the thread IS the act
    that clears the bold — and the alternative is a "mark as read" control nobody would ever tap.

    READ IS PER BOX, NOT PER PERSON. This box can have several people signed in, and a
    per-user read state would need a row per person per conversation and would still leave the
    question of what the list shows when two of them disagree. A shared inbox where one person
    handling a customer clears it for everyone is the behaviour a small team actually wants:
    the list answers "has anyone here dealt with this", which is the question being asked.
    Worth revisiting the day a box has enough staff for that to chafe.

    NOT CONDITIONAL ON THERE BEING ANYTHING TO READ. Stamping a conversation he opened is true
    whatever was in it, and `_UNREAD` needs inbound mail to light up anyway.
    """
    with state.connect() as c:
        c.execute("UPDATE inbox_conversations SET read_at = ?, updated_at = ? "
                  "WHERE space = ? AND zernio_conversation_id = ?",
                  (state._now(), state._now(), space, zcid))


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


def _mirror_key(space: str, zcid: str, direction: str, sent_by: str,
                body: str | None, sent_at: str | None) -> str:
    """A stable id for a message the vendor gave no id for. Same message → same key, forever.

    WHY THIS HAD TO EXIST. `inbox_messages.zernio_message_id` is UNIQUE, and the mirror leans on
    that for exactly-once. A key that is missing breaks it in BOTH directions at once, and both
    were live:

      * `None` — SQLite treats NULLs as DISTINCT in a unique index, so INSERT OR IGNORE never
        fires and the same message mirrors again on every poll. Measured by OSDev5, 2026-09-17:
        the same message over three polls made three rows.
      * `""` — the opposite, and worse. `_header(msg, "Message-ID")` returns "" when a mail
        carries no Message-ID (email_channel.py:214), and "" is NOT null, so the FIRST
        header-less email on the box claims the empty key and EVERY LATER header-less email
        from anybody is silently dropped by INSERT OR IGNORE. A real customer's message would
        simply never appear.

    So neither falsy value is stored. The key is derived from what makes the message itself:
    `sent_at` is in it because a customer who writes "yes" twice in one thread has sent two
    messages, and without a clock they would hash the same and the second would vanish — which
    is the `""` bug again with extra steps.
    """
    raw = "|".join(("v1", space, zcid, str(direction), str(sent_by or ""),
                    str(sent_at or ""), (body or "")[:2000]))
    return "syn:" + hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:40]


def record_message(*, space: str, zcid: str, zmid: str | None, direction: str,
                   sent_by: str, body: str | None, sent_at: str | None = None) -> None:
    """Mirror one message, exactly once.

    Idempotent by the VENDOR's message id when there is one, and by `_mirror_key` when there is
    not — see there for why a missing id is not a small problem. `sent_at` is optional only so
    that callers which never had it keep working; pass it wherever the vendor gives one.
    """
    key = (zmid or "").strip() or _mirror_key(space, zcid, direction, sent_by, body, sent_at)
    with state.connect() as c:
        c.execute(
            "INSERT OR IGNORE INTO inbox_messages (id, space, "
            "zernio_conversation_id, zernio_message_id, direction, sent_by, body, "
            "created_at) VALUES (?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), space, zcid, key, direction, sent_by,
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


def awaiting_reply(space: str) -> int:
    """How many conversations are WAITING ON A PERSON — their message was the last one.

    THE ONE NUMBER THE PRODUCT IS FOR, and until now nothing in the box could answer it. The
    inbox screen wanted it as a row tag and said so in a comment rather than computing it,
    because asking per row is the N+1 its own docstring refuses by name; the morning page wanted
    it as the only line on the segment that is an instruction. One query answers both.

    "WAITING" IS ABOUT DIRECTION, NOT ABOUT A CLOCK. A conversation whose newest message is
    inbound is waiting whether that arrived a minute or a month ago — an old one is worse, not
    resolved. So this counts the newest message per conversation and asks which way it was going,
    rather than comparing `last_inbound_at` to anything.

    OPTED-OUT ROWS ARE EXCLUDED. Somebody who said STOP is not waiting for a reply, and counting
    them would put a number on the morning page that a person cannot act on and must not act on.

    A CONVERSATION WITH NO MESSAGES IS NOT WAITING EITHER. The poller knows about rows it has
    never heard a word on; `MAX(created_at)` over an empty set is NULL and the join drops them,
    which is the right answer rather than a lucky one.
    """
    with state.connect() as c:
        row = c.execute(
            "SELECT COUNT(*) n FROM inbox_conversations k "
            f" WHERE k.space = ? AND {_WAITING}", (space,)).fetchone()
    return int(row["n"]) if row else 0


def inbox_counts(space: str) -> dict:
    """Both numbers the inbox header and its filter row need, in ONE pass.

    TWO COUNTS, NOT TWO QUERIES. Each one is a scan that evaluates a correlated subquery per row;
    asking separately doubles that on every single render of the screen a person looks at most.
    Conditional sums over the same scan cost one.

    THE SAME PREDICATES THE LISTS USE, by name. A header that says "3 waiting" over a filtered
    list of five is a screen nobody trusts twice, and the only durable way to prevent it is for
    the number and the list to be the same string of SQL — `awaiting_reply()` keeps its own name
    because the morning report reads it, but it is the same `_WAITING`.
    """
    with state.connect() as c:
        row = c.execute(
            f"SELECT SUM(CASE WHEN {_WAITING} THEN 1 ELSE 0 END) AS waiting, "
            f"       SUM(CASE WHEN {_FROM_AD} THEN 1 ELSE 0 END) AS from_ad "
            "  FROM inbox_conversations k WHERE k.space = ?", (space,)).fetchone()
    # SUM OVER NO ROWS IS NULL, NOT 0 — an empty box would otherwise hand the screen a None it
    # would render as a filter it cannot use.
    return {"waiting": int((row["waiting"] if row else 0) or 0),
            "from_ad": int((row["from_ad"] if row else 0) or 0)}


def day_counts(space: str, lo: str, hi: str) -> dict:
    """What the inbox did in one day — for the morning page. One connection, four counts.

    COUNTED OVER `created_at`, WHICH IS THE VENDOR'S CLOCK for a message and ours for a draft.
    That is the honest reading for both: "messages that arrived today" means the customer wrote
    today, not that we polled today, and a box that was off overnight must not report its catch-up
    sweep as a busy morning.

    `new_people` IS CONVERSATIONS THAT STARTED TODAY, not contacts that are new to the business —
    the box cannot know the second, and reporting the first as the second is the sort of claim
    this segment exists not to make.
    """
    with state.connect() as c:
        def n(sql: str, args: tuple) -> int:
            r = c.execute(sql, args).fetchone()
            return int(r["n"]) if r else 0

        inbound = n("SELECT COUNT(*) n FROM inbox_messages "
                    " WHERE space = ? AND direction = 'in' "
                    "   AND created_at >= ? AND created_at < ?", (space, lo, hi))
        replied = n("SELECT COUNT(*) n FROM inbox_messages "
                    " WHERE space = ? AND direction = 'out' "
                    "   AND created_at >= ? AND created_at < ?", (space, lo, hi))
        new_people = n("SELECT COUNT(*) n FROM inbox_conversations "
                       " WHERE space = ? AND created_at >= ? AND created_at < ?", (space, lo, hi))
        # DISMISSED DRAFTS ARE NOT READY. He said no to those, and counting them as waiting would
        # send him back to a queue he has already been through.
        drafts = n("SELECT COUNT(*) n FROM inbox_drafts "
                   " WHERE space = ? AND dismissed_at IS NULL "
                   "   AND created_at >= ? AND created_at < ?", (space, lo, hi))
    return {"inbound": inbound, "replied": replied, "new_people": new_people, "drafts": drafts}
