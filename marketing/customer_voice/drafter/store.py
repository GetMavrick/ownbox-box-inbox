"""Draft rows. Reads and writes only — nothing here reaches a network."""
from __future__ import annotations

import uuid

from core import state
from core.logging import get_logger

# A CONVERSATION AN AUTOMATION HAS CLAIMED (customer_voice/claims.py) IS NOT DRAFTED. The predicate is COPIED,
# not imported, because this directory may think and so may import only core and stdlib
# (tests/test_customer_voice.py: reasoning and sending never meet). tests/test_inbox_claims.py holds the
# copy equal to claims.UNCLAIMED, so the drafter and the Inbox can never disagree about who holds one.
_UNCLAIMED = ("NOT EXISTS (SELECT 1 FROM inbox_claims c WHERE c.space = k.space "
              "AND c.zernio_conversation_id = k.zernio_conversation_id AND c.released_at IS NULL "
              "AND c.expires_at > strftime('%Y-%m-%dT%H:%M:%S','now'))")


log = get_logger(__name__)


KEEP = "kept"          # the rules mark of a draft a person's own AI wrote: never rewritten by the box


def put(*, space: str, zcid: str, in_reply_to: str, body: str, rules: str = "") -> bool:
    """Record one draft. Returns False if this inbound already had one.

    EXACTLY ONE DRAFT PER INBOUND MESSAGE, enforced by `UNIQUE (space, in_reply_to)` rather
    than by the caller remembering. A sweep that runs twice — a restart, an overlapping timer —
    must not pay twice for the same answer, and money spent is the one mistake a retry cannot
    take back.
    """
    body = str(body or "").strip()
    if not body:
        return False
    did, now = str(uuid.uuid4()), state._now()
    with state.connect() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO inbox_drafts (id, space, zernio_conversation_id, "
            "in_reply_to, body, created_at) VALUES (?,?,?,?,?,?)",
            (did, space, zcid, in_reply_to, body, now))
        if cur.rowcount > 0 and rules:
            c.execute("INSERT OR REPLACE INTO inbox_draft_rules (space, draft_id, rules, written_at) "
                      "VALUES (?,?,?,?)", (space, did, str(rules), now))
        return cur.rowcount > 0


def rewrite(*, space: str, draft_id: str, body: str, rules: str) -> bool:
    """Replace a waiting draft's words in place (same row, same id) and record the rules that wrote them.
    Never a new row: `UNIQUE (space, in_reply_to)` means one draft per message, and the screen, the lessons
    and the Waiting list all hold the id. A dismissed draft is left alone."""
    body = str(body or "").strip()
    if not body:
        return False
    now = state._now()
    with state.connect() as c:
        cur = c.execute("UPDATE inbox_drafts SET body = ?, created_at = ? "
                        " WHERE space = ? AND id = ? AND dismissed_at IS NULL", (body, now, space, str(draft_id)))
        if cur.rowcount > 0:
            c.execute("INSERT OR REPLACE INTO inbox_draft_rules (space, draft_id, rules, written_at) "
                      "VALUES (?,?,?,?)", (space, str(draft_id), str(rules), now))
        return cur.rowcount > 0


def mark_rules(space: str, draft_id: str, rules: str) -> None:
    """Record which rules a draft now answers to, without touching its words (a rewrite that chose no reply)."""
    with state.connect() as c:
        c.execute("INSERT OR REPLACE INTO inbox_draft_rules (space, draft_id, rules, written_at) VALUES (?,?,?,?)",
                  (space, str(draft_id), str(rules), state._now()))


def stale_waiting(space: str, *, email_rules: str, dms_rules: str, limit: int = 5) -> list[dict]:
    """Drafts still waiting on a person that were written under other rules than the box runs now, oldest
    first: no rules row (written before the box kept one), or a fingerprint that no longer matches the
    conversation's channel. Never one marked KEEP, never one dismissed or already answered. The `limit`
    is a spend bound: each row is one model call."""
    with state.connect() as c:
        rows = c.execute(
            "SELECT d.id, d.zernio_conversation_id AS zcid, d.body, d.in_reply_to, k.participant, k.platform,"
            "       m.body AS asked, m.created_at AS asked_at, r.rules "
            "  FROM inbox_drafts d "
            "  JOIN inbox_conversations k ON k.space = d.space "
            "   AND k.zernio_conversation_id = d.zernio_conversation_id "
            "  JOIN inbox_messages m ON m.space = d.space AND m.zernio_message_id = d.in_reply_to "
            "  LEFT JOIN inbox_draft_rules r ON r.space = d.space AND r.draft_id = d.id "
            " WHERE d.space = ? AND d.dismissed_at IS NULL AND k.opted_out = 0 "
            "   AND k.automated IS NOT 1 "
            f"   AND {_UNCLAIMED} "
            "   AND (r.rules IS NULL OR (r.rules <> ? AND "
            "        r.rules <> CASE WHEN k.platform = 'email' THEN ? ELSE ? END)) "
            "   AND NOT EXISTS (SELECT 1 FROM inbox_messages o "
            "                    WHERE o.space = d.space "
            "                      AND o.zernio_conversation_id = d.zernio_conversation_id "
            "                      AND o.direction = 'out' AND o.created_at > m.created_at) "
            " ORDER BY m.created_at ASC LIMIT ?",
            (space, KEEP, str(email_rules), str(dms_rules), int(limit))).fetchall()
    return [dict(r) for r in rows]


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


def started_by(space: str, zcid: str) -> str:
    """Who wrote the first message this box holds for the conversation: "business", "them", or "" when it holds none.
    The drafter says it, because "Them" writing first does not make them the customer: a reply to the owner's own job
    application, order or support ticket starts with their message too (owner, 2026-10-04)."""
    with state.connect() as c:
        row = c.execute("SELECT direction FROM inbox_messages WHERE space = ? AND zernio_conversation_id = ? "
                        "ORDER BY created_at ASC, id ASC LIMIT 1", (space, str(zcid))).fetchone()
    if row is None:
        return ""
    return "business" if str(row["direction"]) == "out" else "them"


def dismiss(space: str, draft_id: str) -> None:
    """He said no. NEVER a DELETE — standing owner rule, and a draft he rejected is the most
    useful record this table holds: it is the evidence that the machine got one wrong."""
    with state.connect() as c:
        c.execute("UPDATE inbox_drafts SET dismissed_at = ? WHERE space = ? AND id = ?",
                  (state._now(), space, str(draft_id)))


def needs_a_draft(space: str, *, limit: int = 5, pitch_back: bool = False) -> list[dict]:
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
            # WHO WROTE IT, so the sweep can refuse a machine before it spends a model call
            # (`inbox/who_wrote.py`). It was always on the row and never carried up.
            "       m.sent_by AS sender, "
            "       m.created_at AS inbound_at "
            "  FROM inbox_conversations k "
            "  JOIN inbox_messages m "
            "    ON m.space = k.space "
            "   AND m.zernio_conversation_id = k.zernio_conversation_id "
            "   AND m.direction = 'in' "
            "   AND m.zernio_message_id IS NOT NULL "
            # ROBOTS ARE EXCLUDED IN SQL, NOT FILTERED AFTER, and that is not a style choice.
            # `limit` is a SPEND BOUND, so the rows come back capped — 35 of the 62 email
            # threads on the owner's box were automated senders (OSDev1, 2026-09-22), and a
            # Python filter applied after LIMIT 5 would have returned the same five robots every
            # sweep and starved every real customer behind them. That is the head-block shape
            # #1436 and #1437 both cost us a day on.
            #
            # `IS NOT 1` RATHER THAN `= 0`, because NULL means nobody has looked yet and an
            # unjudged thread must still get its draft. Only a thread PROVEN automated is skipped.
            " WHERE k.space = ? AND k.opted_out = 0 AND k.automated IS NOT 1 "
            # ONLY A LIST HEADER (2) IS A COLD PITCH'S SHAPE: drafted only while pitch-back is on (OSDev4's F4 #1852,
            # plan #1857 H7), and kept out of the capped query otherwise, so pitches can't starve a real customer.
            "   AND (k.automated IS NOT 2 OR ?) "
            # A CONVERSATION AN AUTOMATION IS RUNNING IS NOT DRAFTED (customer_voice/claims.py, owner
            # 2026-10-01, decision 2): it would be a model call for a reply nobody should send.
            f"   AND {_UNCLAIMED} "
            "   AND m.created_at = (SELECT MAX(m2.created_at) FROM inbox_messages m2 "
            "                        WHERE m2.space = k.space "
            "                          AND m2.zernio_conversation_id = k.zernio_conversation_id "
            "                          AND m2.direction = 'in') "
            "   AND NOT EXISTS (SELECT 1 FROM inbox_drafts d "
            "                    WHERE d.space = k.space AND d.in_reply_to = m.zernio_message_id) "
            # A MESSAGE WITH NO WORDS IS DRAFTED TOO, AND LAST (owner, 2026-10-07, asked whether a photo or a shared
            # post with no words should get a draft: "Yes, draft the photo ones too"). Until then it was left out here,
            # because of what it once did: MEASURED ON THE OWNER'S BOX 2026-09-22, fifteen media-only Instagram
            # messages arrived at 06:48 with empty bodies; this query is newest-first and the sweep takes the first
            # three, so for SEVEN HOURS every sweep picked the same empty rows, `draft_one` returned None on each, and
            # 72 conversations with real text could never be reached. Two things keep that from coming back:
            #   * every message that HAS words comes first, whatever its age, so photos can never stand in front of
            #     a question;
            #   * `draft_one` now answers a wordless one (what was sent, from its attachment), so each is drafted or
            #     settled once and leaves the queue, and never comes back to the head of it.
            # SQLite's one-argument TRIM strips SPACES ONLY — a body of "\n\t" survives it. The second argument is the
            # set of characters to strip, so this is tab, newline and return too.
            " ORDER BY (TRIM(COALESCE(m.body, ''), ' ' || char(9) || char(10) || char(13)) = '') ASC, "
            "          m.created_at DESC LIMIT ?", (space, 1 if pitch_back else 0, int(limit))).fetchall()
    return [dict(r) for r in rows]


_SENT = {"image": "a photo", "photo": "a photo", "video": "a video", "audio": "a voice note",
         "voice": "a voice note", "share": "a shared post", "story": "a story", "story_mention": "a story they "
         "tagged you in", "story_reply": "a reply to your story", "reel": "a reel", "ig_reel": "a reel",
         "sticker": "a sticker", "gif": "a GIF", "file": "a file", "document": "a file"}


def what_was_sent(space: str, in_reply_to: str) -> str:
    """A message with no words, said as what it was: "a photo", "a video and a photo", from what the platform told
    the box about its attachments. "something with no words" when the box was never told (a message mirrored before
    it kept attachments). Never raises."""
    import json
    try:
        with state.connect() as c:
            row = c.execute("SELECT x.attachments FROM inbox_messages m JOIN inbox_message_extras x "
                            "  ON x.message_id = m.id WHERE m.space = ? AND m.zernio_message_id = ?",
                            (space, str(in_reply_to))).fetchone()
        kinds = []
        for a in json.loads((row["attachments"] if row else None) or "[]"):
            mime = str((a or {}).get("mimeType") or "").split("/")[0]
            said = _SENT.get(str((a or {}).get("type") or "").lower()) or _SENT.get(mime) or "a file"
            if said not in kinds:
                kinds.append(said)
        if kinds:
            return " and ".join(kinds[:3])
    except Exception:                                    # noqa: BLE001 — a description never costs a draft
        pass
    return "something with no words (a photo, a video or a shared post)"


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
            # AND THE SCREEN DOES NOT OFFER ONE EITHER. The drafter above stops paying for these;
            # this is what stops the buyer being able to TICK one. Two gates, because a draft
            # written before the column existed is still sitting in that table.
            " WHERE d.space = ? AND d.dismissed_at IS NULL AND k.opted_out = 0 "
            "   AND k.automated IS NOT 1 "
            # A draft written before an automation claimed the conversation isn't offered either.
            f"   AND {_UNCLAIMED} "
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


# ── cold pitches turned around (draft.py `PITCH_BACK`) ─────────────────────────────────────────────────────────────
# THE RECORD OF "NO REPLY NEEDED" (draft.py, NO_REPLY): a draft row with this body, dismissed as it is written.
NO_REPLY_BODY = "(the box judged that this message needs no reply)"


def judged_not_for_a_person(space: str) -> set:
    """Conversations the box already judged need no person's answer: its newest message from them was judged to need
    no reply, or any of theirs was a cold pitch (inbox_pitch_backs). For the Morning Review's "Who to answer first"
    (OSDev1, 2026-10-05: "anything the inbox already treats as a pitch"). Never raises."""
    try:
        with state.connect() as c:
            rows = c.execute(
                "SELECT d.zernio_conversation_id FROM inbox_drafts d WHERE d.space = ? AND d.body = ? "
                "   AND d.in_reply_to = (SELECT m.zernio_message_id FROM inbox_messages m WHERE m.space = d.space "
                "        AND m.zernio_conversation_id = d.zernio_conversation_id AND m.direction = 'in' "
                "        ORDER BY m.created_at DESC, m.id DESC LIMIT 1) "
                "UNION SELECT m.zernio_conversation_id FROM inbox_pitch_backs p JOIN inbox_messages m "
                "   ON m.space = p.space AND m.zernio_message_id = p.in_reply_to WHERE p.space = ?",
                (space, NO_REPLY_BODY, space)).fetchall()
        return {r[0] for r in rows}
    except Exception:                                    # noqa: BLE001 — a filter, never the review
        return set()


# ASKED AGAIN ONCE, WHEN THE RULES CHANGE (owner, 2026-10-06: "I think I've seen some come through that should've had a
# draft, but they didn't get drafted. Please slightly error on the side of drafting too many"). A "no reply needed"
# decision made under other rules than the box runs now is put to the drafter again, once: only on the message the
# conversation still waits on, only from the last RECHECK_DAYS, never a robot, an automation's or an opted-out
# conversation, and never a draft a PERSON dismissed (only the box's own no-reply mark is ever asked again).
RECHECK_DAYS = 30


def stale_no_reply(space: str, *, email_rules: str, dms_rules: str, limit: int = 3,
                   pitch_back: bool = False) -> list[dict]:
    """The box's own "no reply needed" decisions made under other rules than it runs now, newest first. The `limit`
    is a spend bound: each row is one model call."""
    from datetime import datetime, timedelta, timezone
    cut = (datetime.now(timezone.utc) - timedelta(days=RECHECK_DAYS)).isoformat()
    with state.connect() as c:
        rows = c.execute(
            "SELECT d.id, d.zernio_conversation_id AS zcid, d.in_reply_to, k.participant, k.platform, "
            "       m.body AS asked, m.sent_by AS sender, m.created_at AS asked_at, r.rules "
            "  FROM inbox_drafts d "
            "  JOIN inbox_conversations k ON k.space = d.space "
            "   AND k.zernio_conversation_id = d.zernio_conversation_id "
            "  JOIN inbox_messages m ON m.space = d.space AND m.zernio_message_id = d.in_reply_to "
            "  LEFT JOIN inbox_draft_rules r ON r.space = d.space AND r.draft_id = d.id "
            " WHERE d.space = ? AND d.body = ? AND k.opted_out = 0 AND k.automated IS NOT 1 "
            "   AND (k.automated IS NOT 2 OR ?) "
            f"   AND {_UNCLAIMED} "
            "   AND m.created_at >= ? "
            "   AND (r.rules IS NULL OR r.rules <> CASE WHEN k.platform = 'email' THEN ? ELSE ? END) "
            "   AND m.created_at = (SELECT MAX(m2.created_at) FROM inbox_messages m2 WHERE m2.space = d.space "
            "        AND m2.zernio_conversation_id = d.zernio_conversation_id AND m2.direction = 'in') "
            "   AND NOT EXISTS (SELECT 1 FROM inbox_messages o WHERE o.space = d.space "
            "        AND o.zernio_conversation_id = d.zernio_conversation_id "
            "        AND o.direction = 'out' AND o.created_at > m.created_at) "
            " ORDER BY m.created_at DESC LIMIT ?",
            (space, NO_REPLY_BODY, 1 if pitch_back else 0, cut, str(email_rules), str(dms_rules),
             int(limit))).fetchall()
    return [dict(r) for r in rows]


def revive(*, space: str, draft_id: str, body: str, rules: str) -> bool:
    """A "no reply needed" the drafter now answers: its words go on the same row, which waits again like any draft.
    Only ever the box's own no-reply mark; a draft a person dismissed is never brought back."""
    body = str(body or "").strip()
    if not body:
        return False
    now = state._now()
    with state.connect() as c:
        cur = c.execute("UPDATE inbox_drafts SET body = ?, created_at = ?, dismissed_at = NULL "
                        " WHERE space = ? AND id = ? AND body = ?", (body, now, space, str(draft_id), NO_REPLY_BODY))
        if cur.rowcount > 0:
            c.execute("INSERT OR REPLACE INTO inbox_draft_rules (space, draft_id, rules, written_at) "
                      "VALUES (?,?,?,?)", (space, str(draft_id), str(rules), now))
        return cur.rowcount > 0


def mark_pitch_back(space: str, in_reply_to: str) -> None:
    """Note that the draft answering `in_reply_to` turns a cold pitch around. Never raises."""
    try:
        with state.connect() as c:
            c.execute("INSERT OR IGNORE INTO inbox_pitch_backs (space, in_reply_to, created_at) VALUES (?,?,?)",
                      (space, str(in_reply_to), state._now()))
    except Exception as e:                               # noqa: BLE001 — a label never costs a draft
        log.warning("drafter.pitch_back_unmarked", extra={"error": type(e).__name__})


def pitch_backs(space: str) -> set:
    """The inbound ids whose draft turns a cold pitch around."""
    try:
        with state.connect() as c:
            return {r[0] for r in c.execute("SELECT in_reply_to FROM inbox_pitch_backs WHERE space = ?",
                                            (space,)).fetchall()}
    except Exception:                                    # noqa: BLE001
        return set()
