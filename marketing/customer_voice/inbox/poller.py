"""poll_sweep — the inbox's only intake (poll-only, NO webhook receiver; §11-4).

Per Space (tenant): list active Messenger conversations with that Space's key,
skip the unchanged ones via the activity watermark, fetch messages only for the
changed few, and enqueue ONE `intent="inbox"` job per new inbound message
(idempotency_key = space:conversation:message — the queue dedupes re-polls).

Ad attribution (`meta_ad_id` / `ad_title` from `metadata.referral` /
conversation metadata) rides along into the conversation row and the job, so
every lead is tied to the exact ad that produced it. Inert without a key.
"""
import json
import time

from core import state
from core.logging import get_logger
from core.queue import queue

from core.vendors import zernio

from . import store

log = get_logger(__name__)

_MAX_CONVS = 50           # per space per sweep; new inbound beyond this waits one sweep
_MSG_PAGE = 10            # newest messages fetched per changed conversation

# Throttle the per-space poll-list failure warning. A tenant can fail every 45s sweep for a
# structural reason (e.g. Zernio Messenger PLATFORM_NOT_SUPPORTED) — logging 2/space every sweep
# = ~2.9k lines/day that buries real signal. Log on the first failure + on any change of error,
# then at most once per window while it persists, carrying a consecutive-failure count; log one
# recovery line on the transition back to success.
_POLL_WARN_THROTTLE_S = 600
_poll_fail: dict = {}     # space -> {"sig", "since", "last_warn", "count"}


def _now_s() -> float:
    return time.time()


def _note_poll_failure(space: str, err: str) -> None:
    sig = err[:160]
    now = _now_s()
    st = _poll_fail.get(space)
    same = bool(st and st["sig"] == sig)
    if same and (now - st["last_warn"]) < _POLL_WARN_THROTTLE_S:
        st["count"] += 1           # suppressed repeat — still counted for the next log line
        return
    count = (st["count"] + 1) if same else 1
    _poll_fail[space] = {"sig": sig, "since": st["since"] if same else now,
                         "last_warn": now, "count": count}
    log.warning("inbox.poll_list_failed", space=space, error=sig,
                consecutive=count, throttled_s=_POLL_WARN_THROTTLE_S)


def _note_poll_success(space: str) -> None:
    st = _poll_fail.pop(space, None)
    if st:
        log.info("inbox.poll_recovered", space=space, after_failures=st["count"])


def _spaces() -> list[dict]:
    """Spaces with a Zernio key — reuse the proven reel resolver (per-Space
    key-or-nothing; the isolation contract test_airtable_sync already covers)."""
    from core import spaces as reel_spaces
    return [s for s in reel_spaces.all_spaces() if s.get("zernio_key")]


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
    ok_spaces = 0
    for sp in spaces:
        space = sp["name"]
        # The gateway client is scoped to this Space's key + Zernio Profile, so the
        # poll is profile-scoped (the primary isolation under a shared key — a shared
        # key would otherwise surface every Space's conversations). No-op in two-key mode.
        z = zernio.client(sp)
        try:
            page = z.inbox.list(limit=_MAX_CONVS)
        except zernio.ZernioError as e:
            _note_poll_failure(space, str(e))   # throttled warning — one tenant never blocks others
            continue
        _note_poll_success(space)               # clears throttle + logs recovery on transition
        ok_spaces += 1
        for conv in page["conversations"]:
            zcid = str(_f(conv, "id", "_id", "conversation_id") or "")
            if not zcid:
                continue
            scanned += 1
            # account_id owns the thread (F-1) — required to fetch messages and to
            # send the opener; it rides into the job payload for the handler. A
            # conversation with no accountId can't be operated on → skip (defensive;
            # every real Facebook conversation carries it).
            acctid = str(_f(conv, "accountId", "account_id", "account") or "")
            if not acctid:
                log.warning("inbox.conv_missing_account", space=space, conversation=zcid)
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
                log.warning("inbox.poll_msgs_failed", space=space,
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
            store.upsert_conversation(
                space=space, zcid=zcid, ad_meta_id=ad_id, ad_title=ad_title,
                participant=name, last_inbound_at=inbound_at or None,
                account_id=acctid or None)
            store.record_message(space=space, zcid=zcid, zmid=target_id,
                                 direction="in", sent_by="contact", body=body)
            # WS2: de-anonymize the CTM-ad lead into the People lake. OBSERVE-SAFE —
            # identity capture at poll time, independent of inbox.autonomy (capturing a
            # psid is not an auto-DM). Idempotent by psid → a re-poll never double-lakes.
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
                    raw_text=json.dumps({"space": space, "zcid": zcid,
                                         "account_id": acctid,
                                         "inbound_msg_id": target_id,
                                         "inbound_text": body[:500],
                                         "inbound_at": inbound_at,
                                         "ad_meta_id": ad_id, "ad_title": ad_title}),
                    slack_channel_id=sp.get("slack_channel"))
            except Exception as e:  # noqa: BLE001 — one conv's enqueue failure must
                # neither abort the sweep nor advance this watermark (retry next tick).
                log.warning("inbox.enqueue_failed", space=space,
                            conversation=zcid, error=str(e)[:120])
                continue
            store.set_watermark(space, zcid, last_seen_msg_id=newest_id,
                                last_activity=activity or None)
            if created:
                enqueued += 1
    # Watchdog-visible poll health: a revoked key / PLATFORM_NOT_SUPPORTED that fails
    # EVERY keyed space used to live only in throttled journal warnings nobody tails —
    # intake could be silently dead forever. 'fail' only when no keyed space listed.
    state.heartbeat("inbox_poll", "ok" if ok_spaces else "fail")
    if enqueued:
        log.info("inbox.polled", scanned=scanned, enqueued=enqueued)
    return {"scanned": scanned, "enqueued": enqueued}
