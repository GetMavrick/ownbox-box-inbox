"""intent="inbox" — Phase 1: the instant TEMPLATE OPENER (spec §6, tier `opener`).

Model proposes nothing here — Phase 1 is fully deterministic (no Claude, zero
model cost): a new ad conversation gets ONE fixed opener, guarded five ways:

  kill switch  (config inbox.autonomy: off → nothing sends, instantly)
  opt-out      (a STOP inbound marks the conversation opted_out, forever)
  24h window   (window.py decides; fail closed — never the caller's judgment)
  rate cap     (hourly, per space — over-cap DEFERS via RateCapped/not_before to
                when capacity frees; no retry attempt burned, truly "sends later")
  exactly-once (atomic opener claim + idem-keyed send ledger — a requeue,
                double poll, or second worker can never double-message a human)

An INDETERMINATE send (timeout after the request left) keeps the claim and is
recorded as such — a DM that may have reached a human is never blind-retried.
"""
import json
from datetime import datetime, timedelta, timezone

from core.config import get_config
from core.exceptions import ParseError, RateCapped, RetryableError
from core.logging import get_logger

from core.vendors import zernio

from . import store, window

log = get_logger(__name__)

_DEFAULT_OPENER = ("Hey — thanks for reaching out! I got your message and I'm on it. "
                   "What's the biggest thing you're trying to solve right now?")
from core.vendors.zernio import optout as _optout

_STOP_WORDS = _optout._STOP_WORDS          # shared source (W1.4) — one wordlist, every surface


def _cfg() -> dict:
    return get_config().get("inbox", {}) or {}


def _space(name: str) -> dict | None:
    from core import spaces as reel_spaces
    # allow_default_alias=False: an unknown/"default" stamp must NOT alias onto the global
    # Zernio key on this SEND path (cross-tenant DM). Paired with the gateway's fail-closed
    # key guard (core/vendors/zernio: a falsy per-Space key refuses to build a client) and its
    # isolation backstop, a mis-keyed Space refuses rather than opens from the wrong account.
    return reel_spaces.space_by_name(name, allow_default_alias=False)


def _opener_text(sp: dict) -> str:
    return (sp.get("opener_template")
            or _cfg().get("opener_template")
            or _DEFAULT_OPENER)


def _is_stop(text: str) -> bool:
    return _optout.is_stop(text)


def _notify(job: dict, text: str) -> None:
    from core import slack
    channel = job.get("slack_channel_id")
    if channel:
        slack.post(channel, text)


def handle(job: dict) -> dict:
    """Worker entry for one new inbound Messenger message."""
    try:
        req = json.loads(job.get("raw_text") or "{}")
    except ValueError as e:
        raise ParseError(f"inbox job payload is not JSON: {e}") from e
    space_name, zcid = req.get("space"), req.get("zcid")
    if not space_name or not zcid:
        raise ParseError("inbox job missing space/zcid")
    account_id = req.get("account_id")
    inbound_text = req.get("inbound_text") or ""
    ad_bit = f" (ad: {req['ad_title']})" if req.get("ad_title") else (
        f" (ad {req['ad_meta_id']})" if req.get("ad_meta_id") else "")

    # ── STOP: opt-out law runs before anything else, kill switch included ──────
    if _is_stop(inbound_text):
        store.set_opted_out(space_name, zcid)
        _notify(job, f":no_bell: Messenger contact opted out{ad_bit} — "
                     "conversation muted for automation.")
        return {"status": "opted_out", "conversation": zcid}

    # ── kill switch (spec §6), FAIL-CLOSED: only an explicit active tier auto-sends.
    # Everything else OBSERVES — "off", the YAML-boolean form of it (YAML 1.1 coerces a
    # bare `off` to False, so str()→"false", NOT "off"), a typo, "on"/True, or an unknown
    # future tier. A gate that DMs real people must never default open on a value it does
    # not recognize; the old `== "off"` check let a bare `autonomy: off` slip straight through.
    # ABSENT key is also inert: a mis-indented/missing `inbox:` block must not silently
    # re-enable auto-DMs — the most likely config mistake must not yield the most
    # permissive behavior. Sending requires an EXPLICIT `autonomy: "opener"`.
    _ACTIVE_TIERS = {"opener"}          # Phase 1. Phase 2 (AI replies) registers its tier here.
    autonomy = str(_cfg().get("autonomy", "unset")).strip().lower()
    if autonomy not in _ACTIVE_TIERS:
        _notify(job, f":speech_balloon: New Messenger message{ad_bit} — autonomy is "
                     f"'{autonomy}' (not an active send tier), observed only, no auto-reply "
                     f"sent. “{inbound_text[:140]}”")
        return {"status": "observed", "conversation": zcid}

    conv = store.get_conversation(space_name, zcid) or store.upsert_conversation(
        space=space_name, zcid=zcid, last_inbound_at=req.get("inbound_at"))
    if conv.get("opted_out"):
        return {"status": "opted_out", "conversation": zcid}

    # ── 24h window: fail closed (a stale requeue must not message anyone) ─────
    if window.allowed_send(conv.get("last_inbound_at") or req.get("inbound_at")) != "freeform":
        log.warning("inbox.window_blocked", space=space_name, conversation=zcid)
        return {"status": "window_blocked", "conversation": zcid}

    # ── account_id is REQUIRED to send (F-1) and is the tenant boundary under the
    # one-key model (docs §5). Missing it → surface, never auto-reply (fail-closed);
    # do NOT claim (a claim we can't fulfill would loop). ────────────────────────
    if not account_id:
        log.warning("inbox.no_account_id", space=space_name, conversation=zcid)
        _notify(job, f":warning: New Messenger message{ad_bit} but I can't resolve the "
                     "connected account to reply from — surfaced, not auto-replied. "
                     f"“{inbound_text[:140]}”")
        return {"status": "no_account", "conversation": zcid}

    # ── rate cap BEFORE the claim: over-cap DEFERS (not_before = when the oldest
    # counted send ages out of the rolling hour), burning neither the claim nor the
    # worker's retry budget. RetryableError here terminally failed burst leads ~15
    # minutes into a 60-minute cap window — the contract promises "sends later".
    cap = int(_cfg().get("hourly_send_cap", 40))
    if store.sends_last_hour(space_name) >= cap:
        oldest = store.oldest_counted_send(space_name)
        if oldest:
            nb = (datetime.fromisoformat(oldest)
                  + timedelta(hours=1, seconds=90)).isoformat()
        else:   # cap set to 0 / rows aged out mid-check — short fixed defer
            nb = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
        raise RateCapped(f"inbox hourly send cap {cap} reached — deferred",
                         not_before=nb)

    # ── the ONE-opener claim (atomic; requeues and re-polls no-op here) ───────
    if not store.claim_opener(space_name, zcid):
        # Already opened — normally. But a crash between claim and send leaves a
        # claim with NO ledger row: say so honestly instead of telling the owner a
        # thread was opened when no opener ever reached the prospect. (Detection
        # only — auto-resend policy is the flagged indeterminate-reconcile item.)
        if not store.get_send(space_name, f"opener:{space_name}:{zcid}"):
            log.error("inbox.opener_claimed_unsent",
                      space=space_name, conversation=zcid)
            _notify(job, f":warning: Opener for this Messenger thread{ad_bit} was "
                         "claimed but no send is recorded (crash mid-send?) — check "
                         "the thread and reply manually. "
                         f"“{inbound_text[:140]}”")
            return {"status": "already_opened", "conversation": zcid}
        # Phase 1 takes no further automated action; surface the follow-up message
        # to the owner instead (a human continues the thread).
        _notify(job, f":speech_balloon: Reply on an opened Messenger thread{ad_bit}: "
                     f"“{inbound_text[:140]}” — take it away.")
        return {"status": "already_opened", "conversation": zcid}

    sp = _space(space_name) or {}
    text = _opener_text(sp)
    idem = f"opener:{space_name}:{zcid}"
    try:
        # The gateway send is scoped to this Space (key + profile) and enforces the
        # isolation backstop internally: the target account must belong to THIS Space's
        # Zernio Profile (no-op in two-key mode), else it raises determinate → the claim
        # is released and the worker retries (never a cross-tenant DM).
        sent = zernio.client(sp).inbox.send(zcid, account_id, text)
    except zernio.ZernioError as e:
        if e.indeterminate:
            # May have landed: KEEP the claim, record it, tell the owner — never resend.
            store.record_send(space=space_name, zcid=zcid, idem_key=idem,
                              kind="opener", status="indeterminate", error=str(e))
            _notify(job, f":warning: Messenger opener{ad_bit} is INDETERMINATE "
                         "(timeout after send) — check the thread before replying "
                         "manually; automation will not resend.")
            log.error("inbox.opener_indeterminate", space=space_name,
                      conversation=zcid, error=str(e)[:160])
            return {"status": "indeterminate", "conversation": zcid}
        # Determinately did NOT send: release the claim and retry with backoff.
        store.release_opener(space_name, zcid)
        raise RetryableError(f"opener send failed (will retry): {str(e)[:160]}") from e

    store.record_send(space=space_name, zcid=zcid, idem_key=idem, kind="opener",
                      status="ok", zernio_message_id=sent["message_id"])
    store.record_message(space=space_name, zcid=zcid, zmid=sent["message_id"],
                         direction="out", sent_by="ai", body=text)
    _notify(job, f":mega: *New Messenger ad lead*{ad_bit} — they said "
                 f"“{inbound_text[:140]}” · opener auto-sent. Thread is yours "
                 "when you want it.")
    log.info("inbox.opener_sent", space=space_name, conversation=zcid,
             message_id=sent["message_id"])
    return {"status": "opened", "conversation": zcid,
            "message_id": sent["message_id"]}
