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

from . import channels, store

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
    _note_poll_success(space, ch.key)
    return (scanned, stored, True)


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


def _inbound_msgs(msgs: list) -> list:
    """INBOUND messages in a page (tolerant of asc/desc ordering)."""
    return [m for m in msgs
            if str(_f(m, "direction", "type") or "").lower() in ("in", "inbound", "received")
            or _f(m, "fromMe", "from_me") is False]


def _newest_inbound(msgs: list) -> dict | None:
    """Newest INBOUND message in a page (tolerant of asc/desc ordering)."""
    inbound = _inbound_msgs(msgs)
    if not inbound:
        return None
    return max(inbound, key=lambda m: str(_f(m, "createdAt", "created_at", "timestamp") or ""))


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
        inbound_at = str(_f(target, "createdAt", "created_at", "timestamp") or "")
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
        store.record_message(space=space, zcid=zcid, zmid=target_id,
                             direction="in", sent_by="contact", body=body)
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
        z = zernio.client(sp) if sp.get("zernio_key") else None
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
                continue
            _note_poll_success(space, ch.key)   # clears throttle + logs recovery
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
