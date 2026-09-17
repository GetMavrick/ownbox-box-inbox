"""poll_sweep — the inbox's only intake (poll-only, NO webhook receiver; §11-4).

Per Space (tenant) and per CHANNEL: list that channel's active conversations with
that Space's key, skip the unchanged ones via the activity watermark, fetch messages
only for the changed few, and enqueue ONE `intent="inbox"` job per new inbound message
(idempotency_key = space:conversation:message — the queue dedupes re-polls).

TWO CHANNELS, TWO CALLS, AND THAT IS THE VENDOR'S SHAPE, NOT A CHOICE. Zernio returns
Instagram DMs only under platform="instagram" and Messenger DMs only under "facebook"
— one list call cannot see both (LIVE-VERIFIED both ways; see inbox/channels.py). So
the sweep walks `channels.POLLED`, and each channel FAILS ALONE: a box with a Facebook
page and no Instagram account gets a hard vendor error on the IG call every 45s, and
that must never cost this Space its Messenger intake. The failure throttle is keyed per
(Space, channel) for the same reason — one permanently-broken channel used to be able to
mask the other channel's first real failure behind its own throttle window.

Ad attribution (`meta_ad_id` / `ad_title` from `metadata.referral` /
conversation metadata) rides along into the conversation row and the job, so
every lead is tied to the exact ad that produced it. Inert without a key.
"""
import json
import time

from core import box_secrets, state
from core.logging import get_logger
from core.queue import queue

from core.vendors import zernio

from . import channels, store, window

log = get_logger(__name__)

_MAX_CONVS = 50           # per CHANNEL per space per sweep; more waits one sweep
_MSG_PAGE = 10            # newest messages fetched per changed conversation

# Throttle the poll-list failure warning. A tenant can fail every 45s sweep for a structural
# reason (e.g. no Instagram account connected to this Space) — logging one line per channel per
# sweep = thousands/day that bury real signal. Log on the first failure + on any change of error,
# then at most once per window while it persists, carrying a consecutive-failure count; log one
# recovery line on the transition back to success.
#
# KEYED PER (SPACE, CHANNEL), NOT PER SPACE. A Space with no IG account fails the IG call on
# every sweep forever; under a per-Space key that permanent failure holds the throttle, and the
# FIRST Messenger failure — the one that means live intake just died — is silently suppressed as
# "same tenant, already warned". The channel that is always broken must not be able to hide the
# channel that just broke.
_POLL_WARN_THROTTLE_S = 600
_poll_fail: dict = {}     # (space, channel) -> {"sig", "since", "last_warn", "count"}


def _now_s() -> float:
    return time.time()


def _note_poll_failure(space: str, channel: str, err: str) -> None:
    sig = err[:160]
    now = _now_s()
    k = (space, channel)
    st = _poll_fail.get(k)
    same = bool(st and st["sig"] == sig)
    if same and (now - st["last_warn"]) < _POLL_WARN_THROTTLE_S:
        st["count"] += 1           # suppressed repeat — still counted for the next log line
        return
    count = (st["count"] + 1) if same else 1
    _poll_fail[k] = {"sig": sig, "since": st["since"] if same else now,
                     "last_warn": now, "count": count}
    log.warning("inbox.poll_list_failed", space=space, channel=channel, error=sig,
                consecutive=count, throttled_s=_POLL_WARN_THROTTLE_S)


def _note_poll_success(space: str, channel: str) -> None:
    st = _poll_fail.pop((space, channel), None)
    if st:
        log.info("inbox.poll_recovered", space=space, channel=channel,
                 after_failures=st["count"])


def _sweep_email(space: str, ch) -> tuple[int, int, bool]:
    """One Space's mailbox. Returns (scanned, stored, listed_ok).

    FAILS ALONE, like every other channel here. A revoked app password must not stop Messenger
    intake on the same box, so an auth refusal is recorded for the screen and the sweep moves on.
    An unconfigured mailbox is not a failure and is not counted as a success either — it is simply
    a channel this buyer has not connected."""
    from . import email_channel
    try:
        scanned, stored = email_channel.sweep(space)
    except email_channel.EmailAuthError as e:
        # THE BUYER IS TOLD, NOT THE JOURNAL. An opaque "authentication failed" in a log nobody
        # tails is how a box stops reading mail for a month without anyone noticing.
        box_secrets.note_email_status(e.status, e.detail)
        _note_poll_failure(space, ch.key, f"{e.status}: {e.detail}")
        return (0, 0, False)
    except OSError as e:                      # network, TLS, DNS — transient, not the credential
        _note_poll_failure(space, ch.key, str(e))
        return (0, 0, False)
    if not box_secrets.email_credential():
        return (0, 0, False)                  # not connected: nothing read, nothing broken
    # A MAILBOX THAT STARTED WORKING AGAIN HAS TO BE ALLOWED TO SAY SO (OSDev1, 2026-09-17, on the
    # wall). Every failure arm above writes a status the Settings screen quotes back to the buyer,
    # and nothing has ever written one after a sweep that WORKED: the row was written on the way
    # down and never on the way up. `note_email_status` was built with a "connected" arm for
    # exactly this and its own docstring says the success path "has no caller yet" — this is it.
    #
    # THE STUCK CASE IS THE ONE WHERE NOBODY RE-PASTES ANYTHING. `put_email` already clears the
    # status when a buyer pastes a new app password, so that path recovers. It is the credential
    # that was REFUSED BUT NEVER REVOKED that sticks: a Workspace administrator switches app
    # passwords back on, the stored password starts working on its own, and the box reads mail
    # perfectly while Settings still tells its owner their administrator has turned app passwords
    # off — and sends them off to make a new password they do not need.
    #
    # READ BEFORE WRITE. This runs on every poll and the answer is already "connected" almost
    # every time; a write per cycle for a value that did not change is one we can skip.
    if (box_secrets.get(box_secrets.EMAIL_STATUS) or "connected") != "connected":
        box_secrets.note_email_status("connected")
    _note_poll_success(space, ch.key)
    return (scanned, stored, True)


def _note_zernio_health(ok: bool, detail: str = "") -> None:
    """Keep the CONNECT SCREEN honest about the buyer's own social account.

    WITHOUT THIS THE SCREEN LIES BY DEFAULT. `zernio_state()` reports "connected" for as long as a
    key is stored, so a revoked key or an account that hit its plan limit leaves Settings saying
    "Connected. New messages arrive on their own." while nothing arrives at all — the exact silent
    failure the connect surface was built to end, moved one screen over.

    ONLY WHEN THE BUYER'S OWN KEY IS IN PLAY. A box whose Space names its key in config (the
    owner's) has no stored key, and a status row about a credential the screen does not manage
    would be a fact nobody can act on.

    402 IS ITS OWN ANSWER, not an auth failure. The vendor caps an account until a payment method
    is on it and says so with a 402; a buyer told "authentication failed" re-pastes a perfectly
    good key forever."""
    if not box_secrets.zernio_key():
        return
    try:
        if ok:
            if (box_secrets.zernio_state().get("status") or "connected") != "connected":
                box_secrets.note_zernio_status("connected")
            return
        low = detail.lower()
        if "402" in detail or "payment" in low or "billing" in low:
            box_secrets.note_zernio_status("payment_required", detail)
        elif "401" in detail or "403" in detail or "unauthor" in low or "invalid api key" in low:
            box_secrets.note_zernio_status("needs_reauth", detail)
        # ANYTHING ELSE IS LEFT ALONE, DELIBERATELY. A 500 or a timeout is the vendor having a bad
        # minute, not the buyer's account being wrong, and telling them to re-connect over a blip
        # would send them to redo something that was never broken.
    except Exception:                            # noqa: BLE001 — a status row is never worth
        pass                                     # breaking a sweep over


def _spaces() -> list[dict]:
    """Spaces this sweep has anything to read for — reuse the proven reel resolver.

    A ZERNIO KEY IS NO LONGER THE ONLY REASON TO SWEEP. Email arrives over IMAP with a credential
    of its own (core/box_secrets.email_credential), so a buyer who connects Gmail and nothing else
    is a box with no Zernio key at all. Gating the whole sweep on that key would have meant email
    silently never polling on exactly the box that bought it for email."""
    from core import spaces as reel_spaces
    has_email = bool(box_secrets.email_credential())
    return [s for s in reel_spaces.all_spaces() if s.get("zernio_key") or has_email]


def _f(obj, *names):
    for n in names:
        if isinstance(obj, dict) and obj.get(n) is not None:
            return obj[n]
        v = getattr(obj, n, None)
        if v is not None:
            return v
    return None


def _ad_attribution(conv) -> tuple[str | None, str | None]:
    meta = _f(conv, "metadata") or {}
    referral = _f(meta, "referral") or meta
    return (_f(referral, "meta_ad_id", "ad_id"),
            _f(referral, "meta_ad_title", "ad_title"))


def _body(m) -> str:
    return str(_f(m, "body", "text", "message") or "")


# WHAT THE VENDOR ACTUALLY SENDS (OSDev1, live read-only probe, 2026-09-16): `direction` is
# "incoming" or "outgoing", and there is NO `fromMe` field at all. Neither word was in the list
# below, and the `fromMe` fallback evaluated `None is False` — so this function kept 0 of 22 real
# messages, the live box showed zero conversations, and every STOP sent by DM went unseen because
# the opt-out scan reads this same list.
#
# BOTH DIRECTIONS ARE NAMED, AND OUTBOUND WINS. The tempting one-line fix adds "incoming" to the
# inbound tuple and stops. That leaves the function with no opinion about "outgoing" beyond "not
# in the inbound list" — so any future `or` clause, or any vendor that sends a contradictory pair
# like direction=outgoing with fromMe=false, silently turns OUR OWN replies into customer
# messages. The box would then answer itself, and `_is_stop` would scan our words for a STOP.
#
# MATCHED BY EQUALITY, NEVER BY SUBSTRING, and this is not a style preference: "outgoing" contains
# "in". Any `"in" in direction` here reads every outbound message as inbound.
# WHEN THE VENDOR SAYS IT HAPPENED.
#
# CORRECTION, AND IT IS MINE (OSDev4, 2026-09-17). The first version of this comment claimed the
# live vendor sends `sentAt` and NOT `createdAt`, so that every sort key on real data was the empty
# string, `_newest_inbound` picked whichever message the vendor listed first, and `last_inbound_at`
# stored empty — which `window.decide` reads as "no inbound on record" and refuses. None of that
# happened. OSDev1 measured the live account straight after: `createdAt` is present on ALL 58
# messages, ISO-8601 Z, and equals `sentAt` on every one. The sort key was never empty, the clock
# was never stored empty, and no reply was ever blocked by this. I inferred a consequence from a
# key list that was a subset and did not measure it; the sentence is corrected here rather than
# quietly deleted, because a wrong "measured" claim in a comment outlives the pull request.
#
# THE ORDER IS STILL RIGHT, for a smaller reason than the one I gave. `sentAt` is the field the
# vendor documents and sends, so it leads; the rest are a fallback chain, not dead weight, because
# a message that reached here with NO readable key would sort on "" — and that failure IS the one
# described above. It has never fired. Keeping the chain is what keeps it that way.
_SENT_AT_KEYS = ("sentAt", "sent_at", "createdAt", "created_at", "timestamp")

_INBOUND_WORDS = frozenset({"in", "inbound", "incoming", "received"})
_OUTBOUND_WORDS = frozenset({"out", "outbound", "outgoing", "sent"})


def _is_inbound(m) -> bool:
    """Did the CUSTOMER send this? Fail closed: anything we cannot read is not inbound."""
    direction = str(_f(m, "direction", "type") or "").strip().lower()
    if direction in _OUTBOUND_WORDS:
        return False                      # authoritative — no later hint may override it
    if direction in _INBOUND_WORDS:
        return True
    # NO DIRECTION WE RECOGNISE. `fromMe` is the older shape and still the right fallback, but only
    # here, where the vendor told us nothing usable. An unreadable message is NOT inbound: a false
    # negative delays one reply, a false positive makes the box talk to itself.
    return _f(m, "fromMe", "from_me") is False


def _direction_of(m) -> str | None:
    """'in', 'out', or None when the vendor said nothing we can read.

    WHY THIS IS NOT `_is_inbound`, AND WHY IT MUST NOT BE. That function answers "may we reply to
    this?" and so it FAILS CLOSED: anything unreadable is not inbound, because a false positive
    makes the box talk to itself. Recording is a different question with a different worst case,
    and reusing the two-way answer would write every unreadable message down as OUTBOUND.

    What that costs: `store.awaiting_reply` asks whether a conversation's newest message came from
    the customer. A message wrongly recorded as "out" makes a thread look ANSWERED when a real
    person is still waiting — the box quietly drops someone. Wrongly "in" is the opposite and is
    what #1293 was held for: the owner told "72 people are waiting" when two were.

    So there is no safe guess, and this does not guess. Unreadable is None and the caller records
    nothing, leaving the thread's newest RECORDED message the newest one we actually understood.
    Measured: the live vendor sends "incoming"/"outgoing" on every message (OSDev1, 58 of 58), so
    None is the case that should never fire — and the log line is how we would find out it did.
    """
    direction = str(_f(m, "direction", "type") or "").strip().lower()
    if direction in _OUTBOUND_WORDS:
        return "out"
    if direction in _INBOUND_WORDS:
        return "in"
    from_me = _f(m, "fromMe", "from_me")
    if from_me is True:
        return "out"
    if from_me is False:
        return "in"
    return None


def _inbound_msgs(msgs: list) -> list:
    """INBOUND messages in a page (tolerant of asc/desc ordering)."""
    return [m for m in msgs if _is_inbound(m)]


def _newest_inbound(msgs: list) -> dict | None:
    """Newest INBOUND message in a page (tolerant of asc/desc ordering)."""
    inbound = _inbound_msgs(msgs)
    if not inbound:
        return None
    return max(inbound, key=lambda m: str(_f(m, *_SENT_AT_KEYS) or ""))


def _lake_enqueue_messenger(zcid: str, psid: str, name: str | None,
                            ad_id: str | None, ad_title: str | None) -> None:
    """WS2 — capture a Messenger contact into the People lake (docs/PROSPECTS_CONTRACT
    §9-step-4; docs/tasks/prospect-lake-buildout-plan.md). A CTM-ad lead's psid +
    ad attribution become a `cold` lake row so the prospect gets de-anonymized.

    OBSERVE-SAFE: this is identity capture at POLL time, deliberately independent of
    `inbox.autonomy` (a psid is not an auto-DM). Idempotent by psid → a re-poll never
    double-lakes. Best-effort: the lake is local; a bug here must never break the poll.
    Coordinate the `touch.utm` shape with WS3 (shared schema)."""
    if not psid:
        return
    from core.vendors.prospect_lake import outbox as _outbox
    from core.vendors.prospect_lake.touch_utm import messenger_utm
    try:
        _outbox.enqueue(
            key=f"messenger-create:{psid}",
            identities=[{"kind": "messenger_psid", "value": psid}],
            person={"full_name": name or ""},
            touch={"channel": "messenger", "kind": "dm",
                   "utm": messenger_utm(ad_id),
                   "meta": {"meta_ad_id": ad_id, "ad_title": ad_title,
                            "conversation_id": zcid}},
            stage_hint="cold")
    except Exception as e:  # noqa: BLE001
        log.warning("inbox.lake_enqueue_failed", conversation=zcid, error=str(e)[:120])


def _sweep_channel(sp: dict, z, ch: channels.Channel, page: dict) -> tuple[int, int]:
    """One channel's page of conversations for one Space → (scanned, enqueued).

    Split out of `poll_sweep` when Instagram became the second channel. The split is
    not cosmetic: the per-channel list call is the ONLY part that can fail in a way
    that must skip just that channel, and keeping it in the caller is what lets each
    channel fail alone while this body stays channel-agnostic apart from `ch`.
    """
    space = sp["name"]
    scanned = enqueued = 0
    for conv in page["conversations"]:
        zcid = str(_f(conv, "id", "_id", "conversation_id") or "")
        if not zcid:
            continue
        scanned += 1
        # account_id owns the thread (F-1) — required to fetch messages and to send
        # the opener; it rides into the job payload for the handler. A conversation
        # with no accountId can't be operated on → skip (defensive; every real
        # conversation on either channel carries it).
        #
        # THIS IS ALSO WHAT KEEPS THE SEND ON THE RIGHT CHANNEL. `inbox.send` takes a
        # conversation + the account that owns it and no platform at all, so an IG
        # thread answers from the IG account for one reason only: the account_id
        # stored here came off the IG conversation itself. There is no platform
        # default anywhere on the send path to fall back to, and both send paths
        # (handler.py, reply.py) refuse outright without one.
        acctid = str(_f(conv, "accountId", "account_id", "account") or "")
        if not acctid:
            log.warning("inbox.conv_missing_account", space=space,
                        channel=ch.key, conversation=zcid)
            continue
        # Activity watermark: the real field is `updatedTime` (A-5) — the
        # earlier `updatedAt`/`lastMessageAt` never matched, so the
        # skip-unchanged optimization never fired. Tolerant of alternates.
        activity = str(_f(conv, "updatedTime", "updatedAt", "updated_at",
                          "lastMessageAt", "last_message_at") or "")
        wm = store.get_watermark(space, zcid)
        if wm and activity and wm.get("last_activity") == activity:
            continue                  # unchanged → zero message fetches
        try:
            msgs = z.inbox.messages(zcid, acctid, limit=_MSG_PAGE)["messages"]
        except zernio.ZernioError as e:
            log.warning("inbox.poll_msgs_failed", space=space, channel=ch.key,
                        conversation=zcid, error=str(e)[:120])
            continue
        newest = _newest_inbound(msgs)
        newest_id = str(_f(newest, "id", "_id", "message_id") or "") if newest else None
        if not newest or not newest_id or (wm and wm.get("last_seen_msg_id") == newest_id):
            store.set_watermark(space, zcid, last_seen_msg_id=(wm or {}).get(
                "last_seen_msg_id"), last_activity=activity or None)
            continue
        # OPT-OUT LAW: a STOP anywhere in the fetched page is honored OVER the newest inbound.
        # Otherwise the opener targets a later message and this STOP is never processed (the
        # handler only inspects the message it's handed). Route the STOP through as the target;
        # the watermark still advances to the actual newest so nothing is reprocessed.
        from .handler import _is_stop
        stop = next((m for m in _inbound_msgs(msgs) if _is_stop(_body(m))), None)
        target = stop or newest
        target_id = str(_f(target, "id", "_id", "message_id") or "") or newest_id
        ad_id, ad_title = _ad_attribution(conv)
        psid = str(_f(conv, "participantId", "participant_id", "psid") or "")
        name = str(_f(conv, "participantName", "participant",
                      "contactName", "name") or "") or None
        inbound_at = str(_f(target, *_SENT_AT_KEYS) or "")
        body = _body(target)
        # `account_id` is STORED, not only carried in the job. Until it was, the automated
        # opener could send and the screen could not: a person opening this thread later had
        # no account to send from. Same value, one row, no extra vendor call.
        #
        # `platform` IS STORED FOR THE SAME REASON AND IS NOT DECORATION. It is what the
        # screen filters the chip row on, and — because `window._RULES` is keyed by exactly
        # this value — it is what decides whether a reply on this thread is legal at all.
        # A row that reached the DB without it would be judged by Messenger's clock. It is
        # written once, at INSERT: `upsert_conversation` deliberately does not update it on
        # conflict, because a conversation does not migrate between channels, and a row that
        # could have its channel rewritten by a later sweep is a row whose send rule can
        # change underneath a draft a human is already looking at.
        store.upsert_conversation(
            space=space, zcid=zcid, platform=ch.key,
            ad_meta_id=ad_id, ad_title=ad_title,
            participant=name, last_inbound_at=inbound_at or None,
            account_id=acctid or None)
        # MIRROR THE WHOLE PAGE, IN BOTH DIRECTIONS. Until now this recorded ONE message — the
        # target — and hardcoded direction="in". The page at `msgs` above already carries our own
        # replies and `_direction_of` already classifies them, so the box FETCHED the outbound
        # side and threw it away.
        #
        # WHAT THAT COST, measured on the live box by OSDev1 (2026-09-17): inbox_messages held 72
        # rows and every one of them said "in". `store.awaiting_reply` calls a thread waiting when
        # its newest message is inbound, so all 72 counted as waiting — including threads the
        # owner had already answered inside Instagram (the probe saw 15 outgoing across 3). The
        # 8 AM email would have opened with "72 people are waiting" when 2 had an inbound that
        # week. A notification that wrong is worse than no notification: it is read once, trusted
        # once, and never again.
        #
        # REPLIES SENT THROUGH THE BOX WERE ALREADY MIRRORED (reply.py, handler.py). This is for
        # the ones sent anywhere else — the phone, the Instagram app, a laptop at midnight — which
        # is how a small business actually answers its customers, and the only place the box can
        # learn about them is this page.
        #
        # IDEMPOTENT, so re-polling a conversation rewrites nothing: `record_message` keys on the
        # vendor's message id. Cheap, too — this page was already fetched and is capped at
        # `_MSG_PAGE`; the loop adds no vendor call.
        mirrored = 0
        for m in msgs:
            way = _direction_of(m)
            if way is None:
                log.warning("inbox.message_direction_unreadable", space=space,
                            channel=ch.key, conversation=zcid)
                continue
            store.record_message(
                space=space, zcid=zcid,
                zmid=str(_f(m, "id", "_id", "message_id") or "") or None,
                # `sent_by` IS THE DOCUMENTED SET — contact | ai | human — not a fourth word.
                # An outbound message we learn about HERE was sent by a person, from the phone or
                # the Instagram app, so "human" is what it is. The thread renders that by looking
                # the sender up on the send ledger, finds no row (there was no send through the
                # box) and falls back to "you", which is exactly right. A message the box itself
                # sent is already mirrored with sent_by="ai" and wins on INSERT OR IGNORE, so
                # this can never relabel the machine's own words as the owner's.
                direction=way, sent_by="contact" if way == "in" else "human",
                body=_body(m), sent_at=str(_f(m, *_SENT_AT_KEYS) or "") or None)
            mirrored += 1
        log.info("inbox.page_mirrored", space=space, channel=ch.key,
                 conversation=zcid, messages=mirrored, of=len(msgs))
        # WS2: de-anonymize the CTM-ad lead into the People lake. OBSERVE-SAFE —
        # identity capture at poll time, independent of inbox.autonomy (capturing a
        # psid is not an auto-DM). Idempotent by psid → a re-poll never double-lakes.
        #
        # MESSENGER ONLY, ON PURPOSE. An Instagram contact is a DIFFERENT IDENTITY in the
        # lake — `instagram_user_id`, channel `ig_dm` (docs/PROSPECTS_CONTRACT.md §match
        # keys + the source table) — and `messenger_psid` is a match key, so laking an IG
        # id under it would merge two strangers into one prospect and there is no clean
        # way back from a bad merge. The IG identity + its `touch.utm` vocabulary belong to
        # WS2/WS4, whose builders (`touch_utm.ig_utm`) are written for Boosend automations,
        # not for an inbound DM. Ingestion does not need the lake to work; inventing a
        # token for someone else's contract does need their say-so. Flagged, not guessed.
        if ch.key == "messenger":
            _lake_enqueue_messenger(zcid, psid, name, ad_id, ad_title)
        # CRASH-SAFE ORDER: the watermark advances ONLY after the job row durably
        # exists. Advancing first opened a window where a crash between the two
        # commits skipped this inbound FOREVER on re-poll (including a routed
        # STOP — an unhonored opt-out). Every write above is idempotent (upsert /
        # INSERT OR IGNORE / deduped enqueue), so replaying after a crash is a
        # harmless no-op; a lost enqueue is retried next sweep instead of lost.
        # ── A JOB THE HANDLER IS CERTAIN TO REFUSE IS NOT WORTH MAKING ───────────────────
        #
        # OSDev1 HELD #1296 OVER THIS AND WAS RIGHT (2026-09-17). Migration 50 clears the dead
        # watermarks so the fixed classifier can finally see 91 real threads — and every one of
        # them would have been enqueued at once. `handler.py` checks the kill switch at :112 and
        # the send window at :143, IN THAT ORDER, so a box on `autonomy: off` posts
        # ":speech_balloon: New Instagram message … observed only" to Slack for each job BEFORE
        # discovering the window is shut. Ninety-one Slack posts, every one labelled New, about
        # conversations that are in some cases months old. Buyer boxes have no Slack channel, so
        # this lands on ours — which makes it the kind of bug that gets shipped.
        #
        # THE SAME FUNCTION THE HANDLER USES, WITH THE SAME PLATFORM, and that is the whole
        # design. If this asked the window a different question than `handler.py` asks, the two
        # would drift: ask a stricter one and a sendable thread is silently never queued, ask a
        # looser one and the noise comes straight back. `allowed_send` fails closed on an unknown
        # platform exactly as it does there.
        #
        # STORED EITHER WAY. The conversation, the message and the watermark are already written
        # above, so an old thread still appears in the inbox with its history — the buyer reads
        # it on the screen, which is where he reads his mail. What he does not get is a job that
        # exists only to be refused.
        sendable = window.allowed_send(inbound_at, platform=ch.key) == window.FREEFORM
        if not sendable:
            # OPT-OUT LAW OUTRANKS THE WINDOW, and it has to be honoured HERE because the handler
            # is the only thing that normally honours it — and the handler is exactly what we are
            # declining to wake. A STOP sent thirty days ago is still a STOP; dropping it because
            # the reply window shut would leave somebody opted out in the customer's mind and
            # available for automation in ours. `set_opted_out` is an idempotent UPDATE.
            if stop is not None:
                store.set_opted_out(space, zcid)
                log.info("inbox.opted_out_on_backfill", space=space, channel=ch.key,
                         conversation=zcid)
            store.set_watermark(space, zcid, last_seen_msg_id=newest_id,
                                last_activity=activity or None)
            continue
        try:
            _, created = queue.enqueue(
                idempotency_key=f"inbox:{space}:{zcid}:{target_id}",
                intent="inbox",
                # `platform` rides in the payload as well as the row. The handler reads
                # the row first, but it falls back to creating one when the row is gone
                # (a restored DB, a pruned table) — and that fallback used to default to
                # Messenger, which on an IG thread is a 24h clock applied from the wrong
                # rulebook. Carrying it here means the fallback cannot invent a channel.
                raw_text=json.dumps({"space": space, "zcid": zcid,
                                     "account_id": acctid,
                                     "platform": ch.key,
                                     "inbound_msg_id": target_id,
                                     "inbound_text": body[:500],
                                     "inbound_at": inbound_at,
                                     "ad_meta_id": ad_id, "ad_title": ad_title}),
                slack_channel_id=sp.get("slack_channel"))
        except Exception as e:  # noqa: BLE001 — one conv's enqueue failure must
            # neither abort the sweep nor advance this watermark (retry next tick).
            log.warning("inbox.enqueue_failed", space=space, channel=ch.key,
                        conversation=zcid, error=str(e)[:120])
            continue
        store.set_watermark(space, zcid, last_seen_msg_id=newest_id,
                            last_activity=activity or None)
        if created:
            enqueued += 1
    return scanned, enqueued


_VENDOR_OK: bool | None = None


def _vendor_intake_allowed() -> bool:
    """May we poll ZERNIO this sweep? Email never asks — see `poll_sweep`.

    Computed once and kept. `verify_sdk` imports the SDK, compares a pinned version and inspects
    three signatures — no network, but not free at 45-second intervals either, and the answer
    cannot change without a restart because it is a property of what is installed on disk.
    """
    global _VENDOR_OK
    if _VENDOR_OK is None:
        _VENDOR_OK, detail = zernio.verify_sdk()
        if not _VENDOR_OK:
            # ONCE, AT THE FIRST SWEEP, AND LOUD. A box quietly not polling Instagram is the
            # failure this whole gate exists to make visible; the department's own import logs
            # the same fact, and this is the line that proves the poller honoured it.
            log.error("inbox.vendor_intake_disabled", detail=detail)
    return bool(_VENDOR_OK)


def poll_sweep() -> dict:
    """Worker periodic. Cheap no-op when nothing changed or nothing is keyed."""
    spaces = _spaces()
    if not spaces:
        # Explicit 'disabled' (not silence): a box that WAS keyed leaves a stale row
        # behind when the key is blanked — the watchdog treats disabled/missing as OK
        # regardless of age, so dormant boxes never page. (Probe: _probe_inbox_poll.)
        state.heartbeat("inbox_poll", "disabled")
        return {"skipped": "unconfigured"}
    enqueued = scanned = 0
    ok_channels = 0
    for sp in spaces:
        space = sp["name"]
        # The gateway client is scoped to this Space's key + Zernio Profile, so the
        # poll is profile-scoped (the primary isolation under a shared key — a shared
        # key would otherwise surface every Space's conversations). No-op in two-key mode.
        # BUILT ONLY IF SOMETHING NEEDS IT. An email-only box has no Zernio key, and the transport
        # is fail-closed on a falsy key by design — constructing it here would raise before the
        # email channel ever got its turn.
        #
        # AND ONLY IF THE VENDOR SDK VERIFIES. This sweep now runs whether or not it does, so that
        # a buyer's MAILBOX does not wait on an unrelated vendor (see `inbox/__init__`). Zernio
        # itself stays fail-closed: a drifted SDK leaves `z` None and every Zernio channel takes
        # the `continue` below — the same path an email-only box has always taken, which is why
        # this needs no new branch.
        z = (zernio.client(sp)
             if sp.get("zernio_key") and _vendor_intake_allowed() else None)
        for ch in channels.POLLED:
            if ch.vendor == channels.IMAP:
                ch_scanned, ch_stored, ch_ok = _sweep_email(space, ch)
                scanned += ch_scanned
                enqueued += ch_stored
                ok_channels += 1 if ch_ok else 0
                continue
            if z is None:
                continue
            try:
                # `platform` is PASSED, never left to the client default. The default is
                # Messenger, and a sweep that relied on it saw exactly one channel no
                # matter how many it walked — which is the bug this loop exists to fix.
                page = z.inbox.list(limit=_MAX_CONVS, platform=ch.vendor)
            except zernio.ZernioError as e:
                # Throttled warning — one channel never blocks the other, and one
                # tenant never blocks another.
                _note_poll_failure(space, ch.key, str(e))
                _note_zernio_health(False, str(e))
                continue
            _note_poll_success(space, ch.key)   # clears throttle + logs recovery
            _note_zernio_health(True)
            ok_channels += 1
            ch_scanned, ch_enqueued = _sweep_channel(sp, z, ch, page)
            scanned += ch_scanned
            enqueued += ch_enqueued
    # Watchdog-visible poll health: a revoked key / PLATFORM_NOT_SUPPORTED that fails
    # EVERY keyed space used to live only in throttled journal warnings nobody tails —
    # intake could be silently dead forever. 'fail' ONLY when NOTHING listed anywhere.
    #
    # ANY ONE CHANNEL LISTING IS 'ok', DELIBERATELY. Most boxes will have Messenger and
    # no Instagram, so a heartbeat that demanded every channel succeed would page the
    # operator forever about a channel the client never connected — and a pager that
    # cries every 45 minutes is a pager nobody reads when Messenger actually dies. A
    # channel that is configured but broken is the throttled warning's job, not a page.
    state.heartbeat("inbox_poll", "ok" if ok_channels else "fail")
    if enqueued:
        log.info("inbox.polled", scanned=scanned, enqueued=enqueued)
    return {"scanned": scanned, "enqueued": enqueued}
