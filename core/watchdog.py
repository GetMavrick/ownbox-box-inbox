"""Watchdog (spec §5): health probes + transition-based Slack alerts + spend line.

Spends NO reasoning tokens. The Anthropic probe uses models.list (free) to confirm
the key works, never a messages.create. Run on a ~30-min systemd timer.

Probes: Anthropic auth, disk, /dispatch reachability (critical-path), and worker
liveness via the heartbeats table (so a dead worker actually pages you). Alerts fire
on OK->FAIL and FAIL->OK edges, plus a 'still down' reminder every N hours.
"""
import os
import shutil
from datetime import datetime, timedelta, timezone

import requests

from core import cost_guard, slack, state
from core.config import as_bool, get_config, settings
from core.logging import get_logger

log = get_logger(__name__)

REMINDER_HOURS = 4                        # base 'still down' reminder gap (first ~12h of an outage)
REMINDER_CAP_HOURS = 24                   # a standing outage taps at most once/day


def _reminder_interval_h(since_ts: str | None) -> float:
    """Exponential backoff for 'still down' reminders (owner 2026-07-13 — a stuck OAuth token
    beeped his phone every 4h for days). The FAIL edge still pages IMMEDIATELY; only the repeat
    nag tapers: the required gap DOUBLES every 12h the failure persists, from REMINDER_HOURS up to
    REMINDER_CAP_HOURS. So a transient real outage still nags a few times in the first hours, but an
    owner-fixable standing outage decays to one ping/day instead of 6+."""
    if not since_ts:
        return REMINDER_HOURS
    try:
        down_h = (datetime.now(timezone.utc) - datetime.fromisoformat(since_ts)).total_seconds() / 3600
    except (ValueError, TypeError):
        return REMINDER_HOURS
    return min(REMINDER_CAP_HOURS, REMINDER_HOURS * 2 ** int(max(0, down_h) // 12))
WORKER_HEARTBEAT_MAX_AGE_S = 300          # worker beats every loop; stale => dead
DISPATCH_HEALTH_URL = os.environ.get("DISPATCH_HEALTH_URL", "http://127.0.0.1:8000/health")


def _probe_anthropic() -> tuple[bool, str]:
    try:
        from anthropic import Anthropic
        # Tight timeout + one retry: a probe that can hang stalls the WHOLE watchdog
        # pass — the thing that's supposed to detect hangs.
        Anthropic(api_key=settings.anthropic_api_key,
                  timeout=10.0, max_retries=1).models.list(limit=1)
        return True, "auth ok"
    except Exception as e:
        return False, str(e)[:200]


def _oauth_token() -> str:
    """The claude_code token, re-read from the SOURCE OF TRUTH so a stale import-time cache never
    false-pages. `settings.claude_code_oauth_token` is read once at config import; a watchdog run
    (fresh process on a timer) that imported during a transient env gap — e.g. mid-`.env` rewrite —
    would otherwise report 'not set' while the brain is perfectly fine, which spammed
    CLAUDE_CODE_OAUTH_TOKEN pages all day (owner 2026-07-08). Fall back: settings → live env → the
    `.env` FILE. Only a token genuinely absent from all three is a real 'not set'.

    RETRY before declaring absence (owner 2026-07-13): the token IS reliably in `.env`, yet the
    watchdog still false-paged 'not set' intermittently on the 1-vCPU box — a transient env/IO blip
    that 20/20 manual runs could not reproduce. Since an empty read is ~always a blip (not a real
    deletion), retry a few times over ~1s before paging; a token genuinely gone survives all
    retries and pages honestly (logged loud so it's diagnosable)."""
    import time as _time
    for attempt in range(4):
        tok = (settings.claude_code_oauth_token or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()
        if tok:
            return tok
        try:
            from core.config import ROOT
            for line in (ROOT / ".env").read_text().splitlines():
                if line.startswith("CLAUDE_CODE_OAUTH_TOKEN="):
                    val = line.split("=", 1)[1].strip().strip("'").strip('"')
                    if val:
                        return val
        except Exception as e:  # a read blip must not itself page — retry, don't give up yet
            log.warning("watchdog.oauth_token_read_blip", attempt=attempt, error=str(e)[:120])
        if attempt < 3:
            _time.sleep(0.25)
    log.error("watchdog.oauth_token_absent_after_retries")  # survived all retries → the page is real
    return ""


def _probe_claude_code() -> tuple[bool, str]:
    """Reasoning-path health on the subscription backend — by REASONING (defect
    B5: binary-exists + token-present stayed green through a revoked token, a CLI
    auto-update, or an account block, while every real think() died). One tiny
    tool-less haiku call per watchdog pass: $0 marginal on subscription, and the
    probe now exercises the exact path the workload uses. An exhausted usage
    window will show here as a FAIL with the limit text — which IS the right
    page: reels are silently paused until it resets."""
    import json as _json
    import subprocess
    binary = shutil.which("claude")
    if not binary:
        return False, "claude CLI not installed (scripts/install_claude_code.sh)"
    token = _oauth_token()
    if not token:
        return False, "CLAUDE_CODE_OAUTH_TOKEN not set in /opt/aios/.env"
    os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = token   # ensure the `claude -p` subprocess inherits it
    try:
        out = subprocess.run(
            [binary, "-p", "Reply with exactly: ok", "--model", "haiku",
             "--output-format", "json", "--tools", ""],
            capture_output=True, text=True, timeout=60)
        if out.returncode != 0 or not (out.stdout or "").strip():
            return False, f"rc={out.returncode}: {(out.stderr or out.stdout)[:120]}"
        data = _json.loads(out.stdout[out.stdout.index("{"):])
        if data.get("is_error"):
            return False, str(data.get("result"))[:120]
        return True, "reasoning ok (live call)"
    except Exception as e:
        return False, str(e)[:120]


def _probe_disk(min_gb: float = 2.0) -> tuple[bool, str]:
    free_gb = shutil.disk_usage("/").free / 1e9
    return free_gb >= min_gb, f"{free_gb:.1f} GB free"


def _probe_dispatch() -> tuple[bool, str]:
    try:
        r = requests.get(DISPATCH_HEALTH_URL, timeout=5)
        return r.ok, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)[:120]


def _probe_worker() -> tuple[bool, str]:
    """Worker liveness from its heartbeat row — catches a crashed worker process."""
    return _probe_heartbeat("worker")


def _probe_heartbeat(component: str) -> tuple[bool, str]:
    hb = next((row for row in state.get_heartbeats()
               if row["component"] == component), None)
    if not hb:
        return False, f"no {component} heartbeat yet"
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(hb["ts"])).total_seconds()
    return age <= WORKER_HEARTBEAT_MAX_AGE_S, f"last beat {int(age)}s ago"


def _probe_heygen() -> tuple[bool, str]:
    """HeyGen auth health. An expired/rotated key makes every reel job fail terminally
    and SILENTLY (terminal-fail is the right per-job decision) — this probe is what
    turns that into one loud, deduped 'heygen down' page instead of N quiet failures.

    Uses /v2/user/remaining_quota: a probe must be CHEAPER than what it guards —
    the avatar listing this used to call is ~1,300 items on the owner's account
    and blew the 10s timeout (live-found: valid key, failing probe). Bonus: the
    detail carries the real remaining credit count, so every alert about HeyGen
    arrives with the number that usually explains it."""
    try:
        r = requests.get(f"{settings.heygen_api_base}/v2/user/remaining_quota",
                         headers={"X-Api-Key": settings.heygen_api_key}, timeout=10)
        if not r.ok:
            return False, f"HTTP {r.status_code}"
        quota = (r.json().get("data") or {}).get("remaining_quota")
        return True, f"auth ok, quota {quota}"
    except Exception as e:
        return False, str(e)[:120]


def _alert(key: str, ok: bool, detail: str) -> bool:
    """Fire transition/reminder Slack alerts. Returns False iff a send was ATTEMPTED
    and failed (so run_once can withhold the dead-man's-switch ping); True if the
    send succeeded or nothing needed sending."""
    row = state.get_alert_row(key)
    prev = row["state"] if row else "OK"
    dm = settings.operator_slack_user_id

    if ok:
        if detail.startswith("skip ("):
            # The probe COULDN'T EVALUATE (missing table on an old box, or a transient DB
            # error), which is NOT a genuine recovery. Hold the prior state so a one-pass
            # hiccup can't flip a real standing FAIL to a false ':white_check_mark: recovered'
            # DM. A real recovery carries a substantive detail, not a "skip (...)".
            return True
        if prev == "FAIL":
            sent = slack.send_dm(dm, f":white_check_mark: {key} recovered ({detail})")
            state.set_alert(key, "OK", bump_alert_ts=True)
            return sent
        state.set_alert(key, "OK")
        return True  # nothing to send

    # not ok
    if prev != "FAIL":
        if not dm:
            # No operator to page → the Slack DM is a silent no-op. Make the FAIL edge LOUD in
            # structured logs so a probe failure is never invisible on an operator-unset box.
            log.error("watchdog.probe_failed_operator_unset", key=key, detail=detail[:200])
        sent = slack.send_dm(dm, f":warning: {key} FAILED: {detail}")
        state.set_alert(key, "FAIL", bump_alert_ts=True)
        return sent

    # STILL FAILING. The per-key reminder used to fire here on its own exponential backoff.
    # It no longer does — `_standing_failure_digest()` reports every standing failure in ONE
    # daily message instead (see its docstring for why). The FAIL EDGE above is untouched:
    # a new break still pages instantly and on its own.
    state.set_alert(key, "FAIL")  # keep state; the digest owns the repeat cadence now
    return True  # nothing sent from here


_DIGEST_KEY = "digest:standing"
_DIGEST_INTERVAL_H = 24

# What a failing probe COSTS, in the owner's words rather than the probe's name. A key with
# no entry falls back to its own name + detail, so a new probe is never silently unreportable
# — it just reads more technical until someone adds a line here.
_CONSEQUENCE = {
    "leadmagnet_throughput": "Instagram capture funnel — no DMs going out, no leads captured",
    "leadmagnet_owed":       "Leads gave us their email and got nothing — no guide, no welcome",
    "leadmagnet_armed":      "A keyword is advertised but has no listener — commenters get silence",
    "leadmagnet_guides":     "A keyword promises a guide that does not exist — commenters get a homepage",
    "airtable_fields":       "A column AIOS reads was renamed — the feature behind it is silently off",
    "reel_coverage":         "A Space posts to fewer networks than intended — no account connected",
    "carousel_cadence":      "Carousel publishing — decks are missing their posting slots",
    "written_writer_gate":   "WRITTEN gate 1 — 'Write' flips produce no drafts",
    "written_approve_cascade": "WRITTEN gate 2 — approved pieces are not publishing",
    "leadmagnet_keyword_intake": "Keyword intake — 'Go Live' ticks arm nothing",
    "gtm_intake":            "Cold outbound — no new prospects entering the funnel",
    "gtm_mev_harvest":       "MEV LinkedIn jobs — submitted profiles are never collected, paid credits sit idle",
    "gtm_promote_discovered": "Discovered businesses are never promoted to leads — the table only fills by hand",
    "gtm_verify_sweep":      "Addresses are offered without a verdict — the sending domain is the thing at risk",
    "gtm_archivist":         "Dead ends are not leaving the working list — the owner is working rows the machine gave up on",
    "gtm_employers_sync":    "Verified leads are not reaching the Employers table the owner works",
    "box_config_backup":     "The tenant overlay is no longer copied off-box — a lost droplet would lose my/settings.yaml",
    "gtm_xray":              "The nightly X-ray stopped — no new owners are entering the LinkedIn channel",
    "gtm_domain_resolver":   "The daily domain search stopped — hiring leads with no domain stay unreachable",
    "gtm_person_finder":     "The daily person search stopped — hiring companies stay without a human to reach",
    "gtm_probe_sweep":       "The daily pattern probe stopped — named people with a domain get no address",
    "gtm_finder_sweep":      "The daily Finder search stopped — named people at known companies get no address",
    "gtm_halted":            "Cold outbound — approved leads are not being emailed",
    "vendor_at_cap":         "A vendor is refusing calls — whatever it feeds has stopped",
    "meter:apollo":          "Apollo quota — cold discovery cannot pull new prospects",
    "meter:hunter":          "Hunter quota — emails cannot be verified, discovery stalls",
    "meter:heygen":          "HeyGen quota — reels cannot render",
    "meter:resend":          "Resend quota — no email can be sent",
    "meter:claude":          "Claude budget — reasoning calls will start refusing",
    "queue_drain":           "The job queue is not draining — work is sitting unclaimed",
    "send_failure_rate":     "Email is bouncing above the safe rate — sender reputation at risk",
    "inbox_poll":            "Inbox polling is down — replies are not being read",
    "gtm_reply_ingest":      "Reply ingestion is down — prospect replies are not being seen",
    "airtable_sync":         "Airtable sync is down — the board and the box disagree",
    "zernio_sdk":            "Zernio SDK drift — posting and inbox are inert",
    "worker":                "The worker is down — nothing is being processed at all",
    "dispatch":              "The dispatch endpoint is down — nothing can come in",
}


def _days_down(since_ts: str | None) -> float:
    if not since_ts:
        return 0.0
    try:
        return max(0.0, (datetime.now(timezone.utc)
                         - datetime.fromisoformat(since_ts)).total_seconds() / 86400)
    except (ValueError, TypeError):
        return 0.0


def _standing_failure_digest() -> bool:
    """ONE daily message for everything that is still broken, worst-first.

    WHY THIS REPLACED THE PER-KEY NAG (owner-approved 2026-07-31). The reminders worked
    exactly as designed and that was the problem. Four probes were failing at once, two of
    them for over a week — Apollo's quota since Jul 19, the Instagram funnel since Jul 22 —
    each tapering to about one ping a day on its own backoff, each arriving looking like
    every other ping. Nothing was broken in the alerting; the signal was simply
    indistinguishable from the noise it was mixed into, and a dead revenue engine read the
    same as a stuck token.

    So the repeat nag is now one message that answers the only question that matters when
    something has been down a while: WHAT IS BROKEN, HOW LONG, AND WHAT IS IT COSTING.
    Sorted by days down, because the thing that has been broken longest is the thing that
    has been ignored longest — and stated as consequence, since "Instagram capture funnel —
    no DMs going out" is actionable in a way "leadmagnet_throughput FAILED" is not.

    THE FAIL EDGE IS UNTOUCHED. A new break still pages immediately, by itself, loudly.
    This only replaces the "still down" repeat, which is the part that became wallpaper.
    """
    dm = settings.operator_slack_user_id
    try:
        failing = state.failing_alerts()
    except Exception as e:   # noqa: BLE001 — the digest must never crash the pass
        log.warning("watchdog.digest_unreadable", error=str(e)[:160])
        return True
    if not failing:
        return True

    row = state.get_alert_row(_DIGEST_KEY)
    last = (row or {}).get("last_alert_ts")
    if last:
        try:
            if datetime.now(timezone.utc) - datetime.fromisoformat(last) < timedelta(
                    hours=_DIGEST_INTERVAL_H):
                return True      # already sent today
        except (ValueError, TypeError):
            pass

    ranked = sorted(failing, key=lambda r: _days_down(r.get("since_ts")), reverse=True)
    lines = []
    for r in ranked:
        key, d = r["key"], _days_down(r.get("since_ts"))
        age = "today" if d < 1 else f"{int(d)} day{'s' if int(d) != 1 else ''}"
        lines.append(f"• *{age}* — {_CONSEQUENCE.get(key, key)}  _({key})_")
    worst = _days_down(ranked[0].get("since_ts"))
    head = (f":rotating_light: *{len(ranked)} still down* — worst has been broken for "
            f"{int(worst)} day{'s' if int(worst) != 1 else ''}")
    if not dm:
        log.error("watchdog.digest_operator_unset", failing=len(ranked))
    sent = slack.send_dm(dm, head + "\n" + "\n".join(lines))
    state.set_alert(_DIGEST_KEY, "OK", bump_alert_ts=True)
    return sent


def probe_backend() -> tuple[str, bool, str]:
    """(alert_key, ok, detail) for the CONFIGURED reasoning backend, so the periodic
    pass and the worker's startup probe (A2) page on the SAME key — one dedup/recovery
    edge, never a double page. claude_code is probed by a real tool-less haiku call; the
    api backend by a free models.list. An api-key probe on a subscription box (or vice
    versa) would page a permanent false FAIL, so we pick by config."""
    from core.config import get_config
    backend = (get_config().get("brain") or {}).get("backend", "api")
    if backend == "claude_code":
        return ("claude_code", *_probe_claude_code())
    if settings.anthropic_api_key:
        return ("anthropic_api", *_probe_anthropic())
    return "anthropic_api", False, "no ANTHROPIC_API_KEY configured"


def check_backend_now() -> bool:
    """A2 startup probe: page IMMEDIATELY (deduped with the periodic pass via the shared
    alert key) if the reasoning backend is dead at boot — instead of up to one watchdog
    interval of silent total failure — and record a `brain_backend` heartbeat the
    `health` pulse reads. Returns ok. The caller treats this as best-effort: a dead
    backend should page and let the daemon keep running (so it recovers when the backend
    returns), never crash-loop."""
    key, ok, detail = probe_backend()
    state.heartbeat("brain_backend", "ok" if ok else "fail")
    _alert(key, ok, detail)
    return ok


_METER_WARN_FRACTION = 0.8   # proactive alert at 80% of any cap — top up BEFORE a
                             # silent pause ships zero content on the owner's day off


def _retire_old_raws() -> None:
    """Opt-in disk hygiene (defect B3): reel.raw_retention_days > 0 retires raw
    HeyGen MP4s (~70MB each) older than N days. Finals are NEVER auto-deleted,
    and the default (0) keeps everything — deletion is an explicit owner choice."""
    from pathlib import Path

    from core.config import get_config
    days = int(get_config().get("reel", {}).get("raw_retention_days", 0) or 0)
    if days <= 0:
        return
    import time as _time
    cutoff = _time.time() - days * 86400
    renders = Path(settings.db_path).resolve().parent / "renders"
    for f in renders.glob("*.raw.mp4"):
        try:
            if f.stat().st_mtime < cutoff:
                size_mb = round(f.stat().st_size / 1e6)
                f.unlink()
                log.info("watchdog.raw_retired", file=f.name, mb=size_mb,
                         older_than_days=days)
        except OSError as e:
            log.warning("watchdog.raw_retire_error", file=f.name, error=str(e)[:120])


_BACKEND_ALERT_KEYS = {"claude_code", "anthropic_api"}


def resolve_deconfigured_backend_alerts(configured_key: str) -> None:
    """A DECONFIGURED backend's alert (e.g. anthropic_api on a claude_code box) is never
    probed again, so a stale FAIL would be stuck forever and show red in `health`. Quietly
    resolve any backend key that isn't the configured one — no "recovered" DM, because it
    didn't recover, it's simply not in use on this box."""
    for stale in _BACKEND_ALERT_KEYS - {configured_key}:
        row = state.get_alert_row(stale)
        if row and row["state"] == "FAIL":
            state.set_alert(stale, "OK")
            log.info("watchdog.cleared_deconfigured_backend_alert", key=stale)


_INDETERMINATE_AGE_H = 1     # a checkpointed-but-unreconciled irreversible op older than this pages


def _probe_stuck_indeterminate() -> tuple[bool, str]:
    """Re-page on an INDETERMINATE post/opener that no reconcile loop resolves. The checkpoint
    half of the exactly-once contract is met (never a double post/DM), but the reconcile half is
    a pending design decision — so a single missed Slack notice would otherwise silently orphan an
    irreversible action forever. Pure read-only COUNT of aging indeterminate rows, deduped by
    _alert. A machine this box does not carry degrades to zero for ITS OWN count only."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=_INDETERMINATE_AGE_H)).isoformat()

    def _count(sql: str) -> int | None:
        """None = this box does not carry the table. Never raises into the pass."""
        try:
            with state.connect() as c:
                return c.execute(sql, (cutoff,)).fetchone()["n"]
        except Exception:   # noqa: BLE001 — a schemaless/old box must not sink the pass
            return None

    # ONE try PER MACHINE, AND THAT IS THE WHOLE POINT OF THE REWRITE. Both counts used to sit
    # inside a single try, so a box missing EITHER table returned "skip" for the probe entire.
    # A Customer Voice box carries no `reel_scripts` (`scripts/export_box.sh`: its manifest ships
    # one package), so the reel query raised on every pass and the inbox count — the half that
    # box exists for — was never reached. The probe has therefore never paged on the very boxes
    # that sell the Unified Inbox, which is exactly the silent orphan its docstring promises to
    # prevent. Counting each machine independently is the fix; nothing else here changed.
    #
    # `sending` COUNTS AS INDETERMINATE ONCE IT AGES. A human reply claims its ledger key before
    # the vendor call and resolves it milliseconds later, so a `sending` row this old means the
    # process died mid-call: we cannot know whether the customer got it, it is never resent
    # (invariant 4), and without it nothing would ever tell a person to go look.
    inbox = _count("SELECT COUNT(*) AS n FROM inbox_send_ledger "
                   "WHERE status IN ('indeterminate','sending') AND created_at < ?")
    posts = _count("SELECT COUNT(*) AS n FROM reel_scripts "
                   "WHERE error LIKE 'post indeterminate%' AND updated_at < ?")
    if inbox is None and posts is None:
        return True, "skip (no inbox or reel tables on this box)"
    n = (inbox or 0) + (posts or 0)
    return (n == 0, f"{n} indeterminate op(s) unreconciled >{_INDETERMINATE_AGE_H}h "
                    f"(inbox {inbox if inbox is not None else '-'}, "
                    f"posts {posts if posts is not None else '-'}) "
                    "— verify the vendor + resolve the row")


def _probe_prospect_lake_parked() -> tuple[bool, str]:
    """Re-page on prospect_lake outbox rows that PARKED at the attempt cap (8) — a permanently
    undelivered prospect event that is otherwise only visible in the row's free-text error column.
    Read-only COUNT; cap mirrors core.vendors.prospect_lake.sync._MAX_ATTEMPTS."""
    try:
        with state.connect() as c:
            n = c.execute("SELECT COUNT(*) AS n FROM prospects_outbox "
                          "WHERE sent_at IS NULL AND attempts >= 8").fetchone()["n"]
    except Exception as e:   # noqa: BLE001
        return True, f"skip ({type(e).__name__})"
    return (n == 0, f"{n} parked prospect outbox row(s) — undelivered, need a human to unpark")


_LAKE_STALL_AGE_H = 2      # ~60 failed 2-min sweeps: an auth/secret stall, not a blip


def _probe_prospect_lake_stalled() -> tuple[bool, str]:
    """Page when the lake is CONFIGURED but its outbox is stalled — the gap #117 opened:
    a wrong/rotated ingest secret makes run_sync retry the batch-head row every sweep with
    count=False, so `attempts` never reaches the park cap and _probe_prospect_lake_parked
    never fires. A row unsent >2h WITH a recorded error is that stall (never-attempted
    backlog rows carry no error, so a post-dormancy drain doesn't false-page). Gate on the
    settings directly — NEVER import the marketing package here (an import error would sink
    the whole watchdog pass, i.e. the alarm itself); an unconfigured/dormant box never pages."""
    if not (settings.mav_ingest_url and settings.aios_ingest_secret):
        # DETERMINISTIC 'deconfigured' — NOT the 'skip (' cannot-evaluate prefix: a box
        # that was FAILING and is then deconfigured (operator blanks the bad secret to
        # remediate) must self-CLEAR via _alert's normal FAIL→OK path, not get held FAIL
        # forever by the skip-hold. (_probe_inbox_poll uses this same no-prefix pattern.)
        return True, "lake unconfigured — dormant"
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=_LAKE_STALL_AGE_H)).isoformat()
    try:
        with state.connect() as c:
            n = c.execute("SELECT COUNT(*) AS n FROM prospects_outbox WHERE sent_at IS NULL "
                          "AND error IS NOT NULL AND created_at < ?", (cutoff,)).fetchone()["n"]
    except Exception as e:   # noqa: BLE001
        return True, f"skip ({type(e).__name__})"
    return (n == 0, f"{n} prospect outbox row(s) stalled >{_LAKE_STALL_AGE_H}h with errors — "
                    "likely a wrong/rotated AIOS_INGEST_SECRET; the lake is silently not draining")


_INBOX_POLL_MAX_AGE_S = 900      # 15 min; the poller cadence is 45s


def _probe_inbox_poll() -> tuple[bool, str]:
    """Page when Messenger intake is structurally dead — a revoked Zernio key or the
    PLATFORM_NOT_SUPPORTED landmine makes every sweep fail, invisible in throttled journal
    warnings while ad spend keeps producing leads that get no opener. The poller writes
    heartbeats('inbox_poll', ok|fail|disabled). Missing OR 'disabled' → OK (unkeyed/dormant
    box never pages, even with a stale row after a key is blanked). 'fail' (every keyed space
    failed), or a stale 'ok' (poller wedged mid-sweep), → page."""
    try:
        with state.connect() as c:
            row = c.execute("SELECT status, ts FROM heartbeats WHERE component='inbox_poll'"
                            ).fetchone()
    except Exception as e:   # noqa: BLE001
        return True, f"skip ({type(e).__name__})"
    if not row or row["status"] == "disabled":
        return True, "inbox poll inactive (no keyed space)"
    if row["status"] == "fail":
        return False, ("inbox poll FAILED for every keyed Space (revoked key / platform gap?) "
                       "— Messenger ad leads are getting no opener")
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["ts"])).total_seconds()
    if age > _INBOX_POLL_MAX_AGE_S:
        return False, f"inbox poll heartbeat stale ({int(age)}s) — the poller may be wedged"
    return True, "inbox poll healthy"


# ---------------------------------------------------------------------------
# BUSINESS-OUTCOME probes (W1.1). The pre-GTM audit's core lesson: we alerted on
# infrastructure (process up, backend reachable) while a funnel failed 4,848x/day
# and an engine sat halted for weeks — both behind green dashboards. These probes
# watch OUTCOMES, derived from real DB rows + live config ONLY — never log-line
# counts (logs are throttled, rotated, and unqueryable from here) and never
# marketing-package imports (an import error would sink the whole watchdog pass —
# the alarm itself; see _probe_prospect_lake_stalled).

_GTM_BACKLOG_QUIET_H = 72     # 3 days rides out weekends (the drip is weekday-only)


def _probe_gtm_halted() -> tuple[bool, str]:
    """Page when GTM is configured to send but structurally silent — the B4 outage:
    one blank env var (GTM_IMAP_*) made the drip skip every cycle for weeks, with a
    log line nobody reads as the only trace. Two signals:
    (1) drip enabled + require_reply_ingest + blank IMAP creds → the drip is (correctly)
        refusing to send cold email it couldn't hear replies to. Correct gate, MUST page.
        Mirrors reply_ingest._is_configured() on settings directly — same three fields.
    (2) approved leads waiting + zero metered sends in 72h → halted for any other reason.
    """
    drip = get_config().get("gtm", {}).get("daily_drip")
    drip = drip if isinstance(drip, dict) else {}
    if not as_bool(drip.get("enabled"), default=False):
        return True, "drip disabled"
    if as_bool(drip.get("require_reply_ingest", True), default=True) and not (
            getattr(settings, "gtm_imap_host", "") and getattr(settings, "gtm_imap_user", "")
            and getattr(settings, "gtm_imap_password", "")):
        return False, ("GTM drip is silently halted: reply ingestion unconfigured (blank "
                       "GTM_IMAP_* on the box) and require_reply_ingest is on — NO cold email "
                       "sends until the IMAP creds are set")
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=_GTM_BACKLOG_QUIET_H)).isoformat()
    try:
        with state.connect() as c:
            backlog = c.execute("SELECT COUNT(*) AS n FROM gtm_leads WHERE status='approved'"
                                ).fetchone()["n"]
            sends = c.execute("SELECT COALESCE(SUM(units),0) AS n FROM vendor_ledger "
                              "WHERE vendor='resend' AND note='send' AND ts > ?",
                              (cutoff,)).fetchone()["n"]
    except Exception as e:   # noqa: BLE001
        return True, f"skip ({type(e).__name__})"
    if backlog > 0 and sends == 0:
        return False, (f"{backlog} approved lead(s) waiting and 0 sends in "
                       f"{_GTM_BACKLOG_QUIET_H}h — the drip is enabled but not sending")
    return True, f"drip live ({sends} send(s)/{_GTM_BACKLOG_QUIET_H}h, {backlog} approved)"


_QUEUE_DRAIN_MAX_AGE_MIN = 45


def _probe_queue_drain() -> tuple[bool, str]:
    """Page when CLAIMABLE work sits unclaimed — the #239 freeze class, generalized.
    The worker heartbeat proves the process is alive, not that the queue moves: the
    soft-budget re-claim loop starved every job for hours while the heartbeat stayed
    green. Oldest status='queued' row whose backoff (not_before) has passed, older
    than 45 min → not draining, whatever the mechanism. A genuine multi-hour
    reasoning outage pages too — deliberately: hours of stalled jobs is exactly what
    the operator must hear about, once + backoff reminders (_alert handles dedup)."""
    now = datetime.now(timezone.utc)
    try:
        with state.connect() as c:
            row = c.execute("SELECT MIN(created_at) AS oldest, COUNT(*) AS n FROM jobs "
                            "WHERE status='queued' AND (not_before IS NULL OR not_before <= ?)",
                            (now.isoformat(),)).fetchone()
    except Exception as e:   # noqa: BLE001
        return True, f"skip ({type(e).__name__})"
    if not row or not row["oldest"]:
        return True, "queue empty"
    age_min = (now - datetime.fromisoformat(row["oldest"])).total_seconds() / 60
    if age_min > _QUEUE_DRAIN_MAX_AGE_MIN:
        return False, (f"{row['n']} queued job(s), oldest claimable {int(age_min)}m old "
                       f"(>{_QUEUE_DRAIN_MAX_AGE_MIN}m) — the worker is alive but the "
                       "queue is not draining")
    return True, f"{row['n']} queued, oldest {int(age_min)}m"


_SEND_FAIL_MIN_SAMPLE = 10        # below this, one bounce would swamp the ratio
_SEND_FAIL_MAX_FRACTION = 0.15


def _probe_send_failure_rate() -> tuple[bool, str]:
    """Page when bounces/complaints spike relative to sends — the aggregate behind the
    'no bounce breaker' gap (C10). Alert-only (the enforcing circuit breaker is Wave 2):
    suppression adds (reason bounce|complaint) vs metered resend sends, both 24h, both
    real rows. A bad list burns the sending domain fastest exactly when volume ramps."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    try:
        with state.connect() as c:
            fails = c.execute("SELECT COUNT(*) AS n FROM gtm_suppression "
                              "WHERE reason IN ('bounce','complaint') AND added_at > ?",
                              (cutoff,)).fetchone()["n"]
            # Both touches: follow-up bounces are in `fails`, so follow-up sends belong here too.
            sends = c.execute("SELECT COALESCE(SUM(units),0) AS n FROM vendor_ledger "
                              "WHERE vendor='resend' AND note IN ('send','followup') AND ts > ?",
                              (cutoff,)).fetchone()["n"]
    except Exception as e:   # noqa: BLE001
        return True, f"skip ({type(e).__name__})"
    if sends >= _SEND_FAIL_MIN_SAMPLE and fails / sends > _SEND_FAIL_MAX_FRACTION:
        return False, (f"{fails} bounce/complaint vs {sends} sends in 24h "
                       f"(>{int(_SEND_FAIL_MAX_FRACTION * 100)}%) — STOP the ramp and check "
                       "list quality/deliverability before another send")
    return True, f"{fails} bounce/complaint / {sends} sends (24h)"


_REPLY_INGEST_MAX_AGE_S = 2700     # 3 sweep intervals (15 min cadence)


_MEV_HARVEST_STALE_S = 3 * 900   # three missed 15-minute ticks


def _probe_gtm_mev_harvest() -> tuple[bool, str]:
    """Page when the MEV LinkedIn harvester has stopped beating. The vendor parks jobs for
    hours and this periodic is the only thing that ever collects them; 36 free jobs sat on
    the box the day it shipped (OSDev1, 2026-09-09). A harvester that raises every tick
    writes no beat, so the beat AGES and this pages, instead of one throttled WARNING in a
    journal nobody reads. Reads the key from the environment the way the client does and
    never imports the marketing package (see _probe_prospect_lake_stalled)."""
    if not (os.environ.get("GTM_VERIFY_API_KEY") or os.environ.get("MEV_API_KEY") or "").strip():
        return True, "MEV unconfigured — dormant"
    try:
        with state.connect() as c:
            row = c.execute("SELECT status, ts FROM heartbeats "
                            "WHERE component='gtm_mev_harvest'").fetchone()
    except Exception as e:   # noqa: BLE001
        return True, f"skip ({type(e).__name__})"
    if not row:
        return True, "no harvest yet"
    ts = datetime.fromisoformat(row["ts"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    if age > _MEV_HARVEST_STALE_S:
        return False, (f"harvest heartbeat stale ({int(age)}s > {_MEV_HARVEST_STALE_S}s) — "
                       "parked LinkedIn jobs are not being collected")
    return True, f"last harvest {int(age)}s ago ({row['status']})"


_PROMOTE_DISCOVERED_STALE_S = 2 * 86400 + 3600   # two missed daily runs, plus drift


def _probe_gtm_promote_discovered() -> tuple[bool, str]:
    """Page when the daily promoter (gtm_businesses -> gtm_leads, free) stops beating.
    It costs nothing and needs no key, so there is no dormant state: a box that has
    never run it reads 'no run yet' and is fine until the first day has passed."""
    try:
        with state.connect() as c:
            row = c.execute("SELECT status, ts FROM heartbeats "
                            "WHERE component='gtm_promote_discovered'").fetchone()
    except Exception as e:   # noqa: BLE001
        return True, f"skip ({type(e).__name__})"
    if not row:
        return True, "no run yet"
    ts = datetime.fromisoformat(row["ts"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    if age > _PROMOTE_DISCOVERED_STALE_S:
        return False, (f"promoter heartbeat stale ({int(age)}s > {_PROMOTE_DISCOVERED_STALE_S}s) — "
                       "discovered businesses are not becoming leads")
    return True, f"last run {int(age)}s ago ({row['status']})"


_VERIFY_SWEEP_STALE_S = 2 * 86400 + 3600   # two missed daily runs, plus drift


def _probe_gtm_verify_sweep() -> tuple[bool, str]:
    """Page when the daily verification sweep stops beating. Dormant without the verify
    key (the same env the client reads); the marketing package is never imported here."""
    if not (os.environ.get("GTM_VERIFY_API_KEY") or "").strip():
        return True, "verify unconfigured — dormant"
    try:
        with state.connect() as c:
            row = c.execute("SELECT status, ts FROM heartbeats "
                            "WHERE component='gtm_verify_sweep'").fetchone()
    except Exception as e:   # noqa: BLE001
        return True, f"skip ({type(e).__name__})"
    if not row:
        return True, "no sweep yet"
    ts = datetime.fromisoformat(row["ts"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    if age > _VERIFY_SWEEP_STALE_S:
        return False, (f"verify sweep heartbeat stale ({int(age)}s > {_VERIFY_SWEEP_STALE_S}s) — "
                       "new addresses are going unjudged")
    return True, f"last sweep {int(age)}s ago ({row['status']})"


def _probe_gtm_archivist() -> tuple[bool, str]:
    """Page when the daily archivist stops beating. Free, no key, so no dormant state."""
    try:
        with state.connect() as c:
            row = c.execute("SELECT status, ts FROM heartbeats "
                            "WHERE component='gtm_archivist'").fetchone()
    except Exception as e:   # noqa: BLE001
        return True, f"skip ({type(e).__name__})"
    if not row:
        return True, "no run yet"
    ts = datetime.fromisoformat(row["ts"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    if age > _VERIFY_SWEEP_STALE_S:
        return False, (f"archivist heartbeat stale ({int(age)}s > {_VERIFY_SWEEP_STALE_S}s) — "
                       "dead ends are staying on the working list")
    return True, f"last run {int(age)}s ago ({row['status']})"


# A DORMANT PERIODIC SAYS SO IN ITS OWN BEAT. The worker writes `ok:<status>` after the
# callable returns, so a periodic that is disabled, unconfigured or has no table to write
# beats "ok:disabled" / "ok:unconfigured" / "ok:no_table" and needs no config read here.
# That matters: core/watchdog ships to every box, and a Content box carries no `gtm`
# section at all, so a config read of the gtm section in this file fails the export check
# (the scanner reads comments too, so it is not even named here).
_DORMANT_STATUSES = ("disabled", "unconfigured", "no_table", "off", "skipped")


def _probe_daily_beat(component: str, consequence: str) -> tuple[bool, str]:
    try:
        with state.connect() as c:
            row = c.execute("SELECT status, ts FROM heartbeats WHERE component=?",
                            (component,)).fetchone()
    except Exception as e:   # noqa: BLE001
        return True, f"skip ({type(e).__name__})"
    if not row:
        return True, "no run yet"
    last = str(row["status"] or "")
    if any(last.endswith(":" + d) or last == d for d in _DORMANT_STATUSES):
        return True, f"dormant ({last})"
    ts = datetime.fromisoformat(row["ts"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    if age > _VERIFY_SWEEP_STALE_S:
        return False, f"{component} heartbeat stale ({int(age)}s > {_VERIFY_SWEEP_STALE_S}s) — {consequence}"
    return True, f"last run {int(age)}s ago ({last})"


def _probe_gtm_employers_sync() -> tuple[bool, str]:
    """Page when the daily Employers sync stops beating; dormant by its own last status."""
    return _probe_daily_beat("gtm_employers_sync", "verified leads are not reaching the owner's table")


def _probe_box_config_backup() -> tuple[bool, str]:
    """Page when the overlay's daily off-box copy stops beating; a box with no overlay is dormant."""
    return _probe_daily_beat("box_config_backup", "my/settings.yaml is no longer being copied off-box")


def _probe_gtm_xray() -> tuple[bool, str]:
    """Page when the nightly X-ray stops beating; dormant by its own last status."""
    return _probe_daily_beat("gtm_xray", "no new owners are entering the LinkedIn channel")


def _probe_gtm_domain_resolver() -> tuple[bool, str]:
    """Page when the daily domain search stops beating; dormant by its own last status."""
    return _probe_daily_beat("gtm_domain_resolver", "hiring leads with no domain stay unreachable")


def _probe_gtm_person_finder() -> tuple[bool, str]:
    """Page when the daily person search stops beating; dormant by its own last status."""
    return _probe_daily_beat("gtm_person_finder", "hiring companies stay without a human to reach")


def _probe_gtm_probe_sweep() -> tuple[bool, str]:
    """Page when the daily pattern probe stops beating; dormant by its own last status."""
    return _probe_daily_beat("gtm_probe_sweep", "named people with a domain get no address")


def _probe_gtm_finder_sweep() -> tuple[bool, str]:
    """Page when the daily Finder search stops beating; dormant by its own last status."""
    return _probe_daily_beat("gtm_finder_sweep", "named people at known companies get no address")


def _probe_gtm_reply_ingest() -> tuple[bool, str]:
    """Page when reply ingestion is configured but NOT WORKING — the corrected B4: creds
    present-but-invalid failed auth 13x/3h for weeks, visible only in throttled logs,
    while the drip's config-presence gate passed. The sweep now writes a heartbeat
    (ok|fail|disabled); this pages on fail/stale. Settings checked directly — never
    import the marketing package here (see _probe_prospect_lake_stalled)."""
    if not (getattr(settings, "gtm_imap_host", "") and getattr(settings, "gtm_imap_user", "")
            and getattr(settings, "gtm_imap_password", "")):
        return True, "reply ingest unconfigured — dormant"
    try:
        with state.connect() as c:
            row = c.execute("SELECT status, ts FROM heartbeats "
                            "WHERE component='gtm_reply_ingest'").fetchone()
    except Exception as e:   # noqa: BLE001
        return True, f"skip ({type(e).__name__})"
    if not row:
        return True, "no sweep yet"
    if row["status"] == "fail":
        return False, ("GTM reply ingestion cannot connect (IMAP auth failing — wrong "
                       "password?) — replies incl. opt-outs are invisible; the drip is "
                       "failing closed and no cold email sends until this is fixed")
    if str(row["status"]).startswith("error: "):
        # CONNECTED BUT READING NOTHING. The nastier half: auth succeeds, so every
        # liveness signal is green, while the search matches no mail at all — a
        # forwarder rewriting the recipient header is enough to cause it. Replies and
        # opt-outs pile up unread while the campaign keeps sending.
        return False, f"GTM reply ingestion is connected but blind — {row['status'][7:]}"
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["ts"])).total_seconds()
    if age > _REPLY_INGEST_MAX_AGE_S:
        return False, f"reply-ingest heartbeat stale ({int(age)}s) — the sweep may be wedged"
    return True, "reply ingest healthy"


_AIRTABLE_SYNC_MAX_AGE_S = 1800     # well past any sane sync interval


def _probe_airtable_sync() -> tuple[bool, str]:
    """Page when Airtable sync is structurally dead — found live 2026-07-21: the monthly
    API quota exhausted (PUBLIC_API_BILLING_LIMIT_EXCEEDED), EVERY binding failed every
    sweep, and nothing paged because per-binding isolation swallowed the outage into
    warnings. run_sync now heartbeats ok (>=1 binding synced) | fail (all failed);
    this pages on fail or stale. Unkeyed/dormant boxes never page."""
    if not getattr(settings, "airtable_api_key", ""):
        return True, "airtable unconfigured — dormant"
    try:
        with state.connect() as c:
            row = c.execute("SELECT status, ts FROM heartbeats "
                            "WHERE component='airtable_sync'").fetchone()
    except Exception as e:   # noqa: BLE001
        return True, f"skip ({type(e).__name__})"
    if not row:
        return True, "no sweep yet"
    if row["status"] == "fail":
        return False, ("Airtable sync: EVERY binding is failing (monthly API quota "
                       "exhausted? PAT revoked?) — Airtable edits are NOT flowing to the box")
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["ts"])).total_seconds()
    if age > _AIRTABLE_SYNC_MAX_AGE_S:
        return False, f"airtable-sync heartbeat stale ({int(age)}s) — the sweep may be wedged"
    return True, "airtable sync healthy"


def _probe_leadmagnet_throughput() -> tuple[bool, str]:
    """Page when the lead-magnet funnel is armed (enabled + active keywords) yet produced
    NOTHING for a full day — the dead-funnel class (B1): 4,848 failures/day were invisible
    because health only measured process liveness.

    IT MUST COUNT BOTH OUTCOMES, because which one exists depends on the MODE (found
    2026-08-17 while auditing what this probe would have caught):

      private_reply — WE send message 1, so a `leadmagnet_comment_dm` row per DM. The
                      original metric, and correct there.
      automation    — ZERNIO sends message 1. We write a comment_dm row only for the rare
                      repeat commenter Zernio SKIPPED and we backfilled. This is the mode
                      the box actually runs, so counting comment_dm alone made a healthy day
                      of first-time commenters read as "producing nothing" (alarm fatigue on
                      a working funnel), while the seed path could be stone dead and pass on
                      one unrelated backfill. That is not hypothetical: the mirror bug fixed
                      this morning silently skipped EVERY seed in production and would not
                      have moved this probe by a single row.

    So the outcome is leads SEEDED or message-1 DMs sent — either one means the capture path
    is alive. Both zero, with keywords armed, is the real dead funnel."""
    if not as_bool((get_config().get("leadmagnet") or {}).get("enabled"), default=False):
        return True, "leadmagnet disabled"
    try:
        with state.connect() as c:
            kws = c.execute("SELECT COUNT(*) AS n FROM leadmagnet_keywords WHERE active=1"
                            ).fetchone()["n"]
            if not kws:
                return True, "no active keywords"
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            dms = c.execute("SELECT COUNT(*) AS n FROM leadmagnet_comment_dm WHERE sent_at > ?",
                            (cutoff,)).fetchone()["n"]
            seeded = c.execute("SELECT COUNT(*) AS n FROM leadmagnet_leads WHERE created_at > ?",
                               (cutoff,)).fetchone()["n"]
    except Exception as e:   # noqa: BLE001
        return True, f"skip ({type(e).__name__})"
    if dms == 0 and seeded == 0:
        # NAME THE CAUSE when the sweep has recorded one. The Jul 22–Aug 4 outage paged
        # this exact line for 13 days while the real cause (Zernio 500s) sat in journal
        # WARNINGs nobody reads — the three-way guess below is what a probe says when
        # the sweep keeps its failures to itself. The sweep now beats its last error
        # into `heartbeats`, so a vendor degradation is named on the first digest.
        cause = ""
        hb = next((r for r in state.get_heartbeats()
                   if r["component"] == "leadmagnet_sweep"), None)
        if hb and str(hb.get("status", "")).startswith("error: "):
            cause = f" — LAST SWEEP ERROR: {hb['status'][7:]}"
        return False, (f"{kws} active keyword(s), 0 leads seeded AND 0 message-1 DMs in 24h "
                       f"— the capture funnel is producing nothing"
                       + (cause or " (dead endpoint, dead audience, or a silent failure loop)"))
    return True, f"{seeded} lead(s) + {dms} DM(s)/24h across {kws} keyword(s)"


# A captured lead is OWED two things the machine promised: the welcome email and the guide DM.
# Both are self-healing retry sets (a failure leaves the stamp NULL for the next sweep), so a
# few minutes unfilled is the design working. Hours unfilled means the retry itself is failing
# every pass — silently, because each attempt logs a warning and moves on.
_LEADMAGNET_OWED_AGE_H = 2




# Field names AIOS reads off a live VIDEOS table. A name that does not exist there is not a
# crash — Airtable simply omits it — so the feature behind it goes quiet and stays quiet.
_PINNED_VIDEO_FIELDS = ("airtable_posting_time_field", "airtable_platforms_field",
                        "airtable_posted_time_field", "airtable_status_field",
                        "airtable_caption_text_field", "airtable_assets_field")



def _probe_reel_coverage() -> tuple[bool, str]:
    """Page when a Space's CONNECTED accounts cannot cover the platforms it is set to post to.

    THE SILENT SHORTFALL THIS EXISTS FOR (2026-08-19): default's ceiling is Instagram +
    LinkedIn, and its Zernio profile had only Facebook and a paid ad account connected. A reel
    there would have published to nothing on the ceiling — or, before the ceiling was
    per-Space, to Facebook alone — and NOTHING would have said so. An empty per-row picker
    means "no preference", and no preference over one connected account looks exactly like a
    healthy post: `autopost.done` logs a platform list and the row stamps `Date Posted`. The
    only way to notice was to go and check four networks by hand.

    A row that names an unconnected platform is already reported by `_apply_row_platforms`
    ("I posted where you asked" must never mean "I posted to fewer"). This is the case that
    guard cannot see: the row names NOTHING, so there is no pick to refuse, and the shortfall
    is between the Space's ceiling and what the vendor has connected.

    Read-only. An unreadable Zernio is skipped, never reported as a shortfall — the vendor
    being down is what the zernio_sdk probe is for, and a probe that pages on an API blip is
    one the operator learns to ignore.
    """
    if not as_bool((get_config().get("autopost") or {}).get("enabled"), default=False):
        return True, "autopost disabled"
    try:
        from core.vendors import zernio
        from marketing.content_machine.poster import handler as ap
        from core import spaces as _spaces
        allow = ap._publishable_platforms()
    except Exception as e:                                     # noqa: BLE001
        return True, f"skip ({type(e).__name__})"

    gaps, checked = [], 0
    for sp in _spaces.all_spaces():
        if not zernio.is_configured(sp.get("zernio_key")):
            continue                                           # a dormant Space is not a gap
        try:
            connected = set(zernio.client(sp).accounts.discover() or {})
        except Exception:                                      # noqa: BLE001
            continue                                           # unreadable != unconnected
        if not connected:
            continue                                           # ambiguous, not proof
        checked += 1
        want = ap.reel_platforms_for(sp) & allow
        missing = sorted(want - connected)
        if missing:
            reach = sorted(want & connected)
            gaps.append(f"{sp.get('name')}: no {', '.join(missing)} account"
                        + (f" (reaches only {', '.join(reach)})" if reach
                           else " — a reel there would reach NOTHING"))
    if not checked:
        return True, "skip (no Space with a readable Zernio)"
    if gaps:
        return False, ("a Space cannot reach the platforms it is set to post to — "
                       + "; ".join(gaps)
                       + ". Connect the account in Zernio, or narrow that Space's "
                         "reel_platforms so the config matches reality.")
    return True, f"{checked} Space(s) can reach every platform they target"

def _probe_airtable_fields() -> tuple[bool, str]:
    """Page when a CONFIGURED Airtable field name does not exist on the live table.

    THE BUG THIS EXISTS FOR (2026-08-18): `AIRTABLE_POSTING_TIME_FIELD` was set to "Scheduled"
    on the box. Neither VIDEOS nor WRITTEN has a column by that name — both use "Posting Time".
    So `schedule_for()` read a field that was not there, got None, and every post fell through
    to `mode=now`. The owner asked for scheduling on 2026-08-13; it never fired once, and
    nothing said so, because a MISSING Airtable field reads exactly like an EMPTY one.

    The unit suite cannot catch this. It is hermetic, so it pins the CODE DEFAULT while the
    box's `.env` is what actually decides — and `.env` silently beats the default and outlives
    the commit that justified it. Only a check against the live schema, resolved through the
    same settings the worker uses, can see the mismatch.

    Checks every Space's base, because a rename lands on one client's board at a time.
    """
    try:
        from core import airtable
        from core.config import settings
        from core import spaces as _spaces
        if not airtable.is_configured():
            return True, "airtable not configured"
        bases = {}
        for sp in _spaces.all_spaces():
            b = sp.get("airtable_base")
            if b:
                bases.setdefault(b, sp.get("name") or b)
        bases.setdefault(settings.airtable_base_id, "default")
    except Exception as e:                                     # noqa: BLE001
        return True, f"skip ({type(e).__name__})"

    want = {getattr(settings, a, None): a for a in _PINNED_VIDEO_FIELDS}
    want.pop(None, None)
    missing, checked = [], 0
    for base_id, label in bases.items():
        try:
            tables = (airtable.get_base_schema(base=base_id) or {}).get("tables") or []
        except Exception:                                      # noqa: BLE001
            continue                                           # unreadable != renamed
        vids = next((t for t in tables if str(t.get("name", "")).upper() == "VIDEOS"), None)
        if not vids:
            continue
        names = {f.get("name") for f in (vids.get("fields") or [])}
        if not names:
            # An empty field list is ambiguous (permissions, a partial response), NOT proof
            # every column vanished — reporting it as such would page naming the whole config.
            continue
        checked += 1
        for field_name, attr in want.items():
            if field_name not in names:
                missing.append(f"{field_name!r} ({attr}) on {label}")
    if not checked:
        return True, "skip (no VIDEOS schema readable)"
    if missing:
        return False, ("configured field name(s) do NOT exist on the live board: "
                       + "; ".join(sorted(missing))
                       + " — whatever reads them is silently inert. Fix the name or the board.")
    return True, f"{len(want)} field name(s) verified on {checked} VIDEOS table(s)"

def _probe_leadmagnet_armed() -> tuple[bool, str]:
    """Page when a keyword is ARMED in our DB but has NO automation on the platform.

    Every other lead-magnet probe measures traffic, so all of them stay green on a keyword
    nobody has commented yet — which is precisely the state a dead keyword sits in. `SCALE`
    was active=1 against an automation id that had been deleted platform-side: our board read
    "armed", the caption told people to comment it, and every viewer who did got silence.
    Nothing could see it, because "no traffic yet" and "no listener at all" produce identical
    rows. This probe asks the only question that separates them — does the listener exist.

    Read-only, and a definite absence is the ONLY finding: an unreadable vendor returns green
    (zernio_sdk and throughput own that failure), because a probe that pages on every API blip
    is a probe the operator learns to ignore.
    """
    if not as_bool((get_config().get("leadmagnet") or {}).get("enabled"), default=False):
        return True, "leadmagnet disabled"
    try:
        from core.vendors import zernio
        from core import spaces as _spaces
        from core.vendors.zernio.client import automation_id as _aid
        with state.connect() as c:
            rows = c.execute("SELECT space, keyword, zernio_automation_id AS aid "
                             "FROM leadmagnet_keywords WHERE active=1").fetchall()
    except Exception as e:                                    # noqa: BLE001
        return True, f"skip ({type(e).__name__})"
    if not rows:
        return True, "no active keywords"

    by_space: dict[str, list] = {}
    for r in rows:
        by_space.setdefault(r["space"], []).append(r)

    dead, checked = [], 0
    for space_name, krows in by_space.items():
        try:
            sp = _spaces.space_by_name(space_name, allow_default_alias=True)
            if not sp or not zernio.is_configured(sp.get("zernio_key")):
                continue                                       # not our failure to report
            live = {str(_aid(a)) for a in (zernio.client(sp).comment_automations.list() or [])}
        except Exception:                                      # noqa: BLE001
            continue                                           # unreadable != absent
        if not live:
            # An empty list is ambiguous (no automations, or a scope/permission change),
            # so it is NOT treated as "everything is dead" — the exact over-read that would
            # turn one bad API response into a page naming every keyword we own.
            continue
        checked += len(krows)
        for r in krows:
            if not r["aid"] or str(r["aid"]) not in live:
                dead.append(f"{r['keyword']} ({space_name})")

    if dead:
        return False, ("armed in the board but NO automation on Instagram: "
                       + ", ".join(sorted(dead))
                       + " — anyone commenting these keywords gets silence. Re-arm them.")
    return True, f"{checked} armed keyword(s) all have a live automation"

def _probe_leadmagnet_guides() -> tuple[bool, str]:
    """Page when an ARMED keyword has no guide behind it — the promise with nothing on the
    other end.

    LIVE-FOUND 2026-08-28, going into mass-marketing volume: `SCALE` was active, its
    automation healthy, and its DM read "Shooting you the full Scale Without Adding
    Headcount setup" — but no such guide exists. `_guide_url_for` then does the only sane
    thing left and degrades to the guides INDEX, so the viewer who did exactly what the
    caption asked receives a homepage instead of the thing they were promised. It logs the
    degrade and nothing reads that log.

    This is the exact inverse of `_probe_leadmagnet_armed`, and neither can see the other's
    failure: that one asks whether a LISTENER exists on Instagram, this one asks whether the
    DELIVERABLE exists on our side. Every other lead-magnet probe measures traffic, so all of
    them stay green on a keyword nobody has commented yet — which is precisely the state a
    broken promise sits in until the first person takes it up.

    Resolved through the SAME call the delivery path makes, so the check cannot disagree with
    what a real viewer would receive. Read-only; an unreadable Sanity is a skip, never a
    finding, because a vendor blip must not page as a missing guide.
    """
    if not as_bool((get_config().get("leadmagnet") or {}).get("enabled"), default=False):
        return True, "leadmagnet disabled"
    try:
        from marketing.content_machine.leadmagnet import sanity_client
        if not sanity_client.is_configured():
            return True, "sanity unconfigured — cannot judge, and that is not a fault"
        with state.connect() as c:
            rows = c.execute("SELECT space, keyword FROM leadmagnet_keywords WHERE active=1"
                             ).fetchall()
    except Exception as e:   # noqa: BLE001 — an old/schemaless box must not sink the pass
        return True, f"skip ({type(e).__name__})"
    if not rows:
        return True, "no active keywords"

    missing, checked = [], 0
    for r in rows:
        kw = (r["keyword"] or "").strip()
        if not kw:
            continue
        try:
            g = sanity_client.find_by_keyword(kw) or {}
        except Exception:   # noqa: BLE001 — one unreadable lookup never indicts the rest
            continue
        checked += 1
        if not (g.get("slug") or "").strip():
            missing.append(f"{kw} ({r['space']})")
    if not checked:
        return True, "skip (no keyword could be resolved)"
    if missing:
        return False, ("armed keyword(s) with NO guide behind them: " + ", ".join(sorted(missing))
                       + " — anyone who comments these is promised a guide and receives the "
                         "guides index instead. Publish the guide, or deactivate the keyword.")
    return True, f"{checked} armed keyword(s) all have a published guide"


def _probe_leadmagnet_owed() -> tuple[bool, str]:
    """Page when leads have been CAPTURED but not served for hours — the machine's own debt.

    Why this is a separate probe from throughput, and why it is the one that would have
    caught the worst funnel bugs of the last month: throughput asks "is anyone coming in",
    which stays green while everyone who arrives is quietly dropped on the way out. This
    asks "did we do what we promised the people who already converted" — a strictly
    stronger question, because they gave us an email and got nothing back.

    It deliberately measures ONLY work the machine owes (welcome email, guide DM), never
    what a viewer owes. A lead parked at awaiting_engage because a stranger never replied
    is normal audience behavior and paging on it would train the operator to ignore this
    channel — which is exactly how gtm_unsub_sweep failed unseen for seven days.
    """
    if not as_bool((get_config().get("leadmagnet") or {}).get("enabled"), default=False):
        return True, "leadmagnet disabled"
    try:
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(hours=_LEADMAGNET_OWED_AGE_H)).isoformat()
        with state.connect() as c:
            row = c.execute(
                "SELECT COUNT(*) AS n,"
                "       SUM(CASE WHEN welcome_sent_at IS NULL THEN 1 ELSE 0 END) AS no_welcome,"
                "       SUM(CASE WHEN delivery_dm_sent_at IS NULL THEN 1 ELSE 0 END) AS no_guide"
                " FROM leadmagnet_leads WHERE stage='captured' AND captured_at < ?"
                " AND (welcome_sent_at IS NULL OR delivery_dm_sent_at IS NULL)",
                (cutoff,)).fetchone()
    except Exception as e:   # noqa: BLE001 — an old/schemaless box must not sink the pass
        return True, f"skip ({type(e).__name__})"
    n = int(row["n"] or 0)
    if not n:
        return True, "every captured lead has its welcome + guide"
    return False, (f"{n} captured lead(s) unserved >{_LEADMAGNET_OWED_AGE_H}h "
                   f"({int(row['no_welcome'] or 0)} missing the welcome email, "
                   f"{int(row['no_guide'] or 0)} missing the guide DM) — they gave us an "
                   f"email and got nothing back; the retry loop is failing every sweep")


# A vendor at 100% of its cap is REFUSING calls; at 90% it is about to. Only the first
# pages — the near-cap number rides along in the detail so the operator sees it coming.
_VENDOR_CAP_WARN_PCT = 0.90


def _probe_vendor_at_cap() -> tuple[bool, str]:
    """Page when a metered vendor has reached its cycle cap — the engine behind it is
    REFUSING, not idle.

    LIVE-FOUND 2026-07-31, and the ten days it cost are the argument for this probe:
    Apollo hit its 500-unit cap on Jul 21, and GTM discovery gathered ZERO prospects from
    then until it was found by hand. Nothing was broken in any way the box could see —
    all three services active, zero errors, every other probe green. The refusal is an
    INFO line (`apollo cap reached: 500 of 500 units this cycle, gathered: 0`), by design,
    because refusing to overspend is CORRECT behaviour. What was missing is that correct
    behaviour still had a business outcome nobody was told about.

    `_probe_gtm_halted` could not have caught it. That probe wants a CLOG — leads waiting
    with nothing sending — and an exhausted intake produces no backlog at all, so it read
    "drip live (0 sends/72h, 0 approved)" the whole time. An engine with nothing coming in
    is indistinguishable from an engine with nothing to do, unless something watches the
    intake side. This is that something.

    Deliberately generic across every metered vendor, not a GTM special case: the identical
    silence follows a capped HeyGen (no renders), a capped Resend (no email), a capped
    Places or Tomba (no discovery). The cap is the one fact that separates "quiet because
    there is no work" from "quiet because we are not allowed to do the work".
    """
    try:
        vendors = cost_guard.metered_vendors()
    except Exception as e:   # noqa: BLE001 — a config error must not crash the pass
        return True, f"skip ({type(e).__name__})"
    if not vendors:
        return True, "no metered vendors"

    at_cap, near_cap = [], []
    for v in sorted(vendors):
        # A silenced vendor is silenced HERE TOO. Routing the same fact through a second
        # probe would defeat the opt-out and hand the operator the identical message under
        # a different name — which is exactly the noise he silenced it to stop.
        if not cost_guard.vendor_alerts_enabled(v):
            continue
        try:
            cap, used = cost_guard.vendor_cap(v), cost_guard.vendor_usage(v)
        except Exception:   # noqa: BLE001 — one unreadable vendor never hides the others
            continue
        if not cap:
            continue
        # `>=`, matching check_vendor's refusal point: at exactly the cap the NEXT unit is
        # already refused, so the engine is dark from here, not from cap+1.
        if used >= cap:
            at_cap.append(f"{v} {used:g}/{cap:g}")
        elif used >= cap * _VENDOR_CAP_WARN_PCT:
            near_cap.append(f"{v} {used:g}/{cap:g}")

    if at_cap:
        tail = f"; approaching: {', '.join(near_cap)}" if near_cap else ""
        return False, (
            f"vendor cap reached — {', '.join(at_cap)}. Every call to these is being "
            f"REFUSED until the billing cycle rolls, so whatever they feed has stopped "
            f"producing (Apollo → cold discovery, HeyGen → renders, Resend → email). "
            f"Either raise the cap in `vendors:` if the plan allows it, or accept the "
            f"engine is dark until the cycle resets{tail}")
    if near_cap:
        return True, f"approaching cap: {', '.join(near_cap)}"
    return True, f"{len(vendors)} metered vendor(s), all under cap"


_GTM_INTAKE_QUIET_H = 48


def _probe_gtm_intake() -> tuple[bool, str]:
    """Page when GTM autopilot is ON but the funnel took in NOTHING — the empty-pipe half
    of `_probe_gtm_halted`, which only ever watched the outflow.

    Config says autopilot "fills the prospect lake + verified backlog every day". A run of
    two full days with no new `gtm_leads` row means it is not doing that, whatever the
    cause — an exhausted vendor cap, a dead API key, an ICP that matches nobody, or a
    silent exception loop. The outcome is what gets measured here precisely because the
    cause varies; `_probe_vendor_at_cap` then names the cause when the cause is a cap.
    """
    gtm_cfg = get_config().get("gtm", {}) or {}
    ap_cfg = gtm_cfg.get("autopilot")
    ap_cfg = ap_cfg if isinstance(ap_cfg, dict) else {}
    drip_cfg = gtm_cfg.get("daily_drip")
    drip_cfg = drip_cfg if isinstance(drip_cfg, dict) else {}
    autopilot_on = as_bool(ap_cfg.get("enabled"), default=False)
    # THE SEND SIDE BEING ON IS ALSO A STATEMENT OF INTENT (2026-08-29). Gating this probe
    # on autopilot ALONE left a hole the moment the drip went live without it: an empty
    # approved queue produces no backlog, so `_probe_gtm_halted` reads "drip live (0 sends,
    # 0 approved)" and stays green, while this one reads "autopilot disabled" and stays
    # green too. Cold outbound would then be switched on, starving, and completely silent —
    # the same shape as the Apollo cap outage, where an engine with nothing coming in was
    # indistinguishable from an engine with nothing to do.
    drip_on = as_bool(drip_cfg.get("enabled"), default=False)
    if not (autopilot_on or drip_on):
        return True, "autopilot and drip both disabled"
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=_GTM_INTAKE_QUIET_H)).isoformat()
    try:
        with state.connect() as c:
            fresh = c.execute("SELECT COUNT(*) AS n FROM gtm_leads WHERE created_at > ?",
                              (cutoff,)).fetchone()["n"]
            approved = c.execute("SELECT COUNT(*) AS n FROM gtm_leads WHERE status='approved'"
                                 ).fetchone()["n"]
    except Exception as e:   # noqa: BLE001
        return True, f"skip ({type(e).__name__})"
    # A QUIET INTAKE IS ONLY A FAULT WHEN THERE IS ALSO NOTHING TO SEND. Leads arrive in
    # batches and the drip works through them at the ramp's pace, so "no new rows for two
    # days" while a full queue drains is the system working exactly as designed — paging on
    # that would train the operator to ignore this key.
    if fresh == 0 and approved == 0:
        who = ("GTM autopilot is enabled but took in" if autopilot_on
               else "The GTM drip is enabled but the funnel took in")
        return False, (f"{who} 0 new leads in {_GTM_INTAKE_QUIET_H}h and the approved queue "
                       f"is EMPTY — the top of the funnel is dry, so cold outbound is "
                       f"switched on and starving and will send nothing no matter how "
                       f"healthy every other check looks"
                       + ("" if autopilot_on else
                          " (autopilot is off, so nothing is loading leads at all)"))
    return True, f"{fresh} new lead(s)/{_GTM_INTAKE_QUIET_H}h, {approved} approved waiting"


def _probe_written_seed() -> tuple[bool, str]:
    """Page when the daily content engine ran and produced NOTHING.

    LIVE-FOUND 2026-08-04: `daily_seed` attempted both of the day's topics, both timed out
    on a reasoning call sized for a faster box, both were caught per-seed and logged, and
    the tick returned "ok" having written zero rows. Worker healthy, no crash, nothing
    paged — the owner's daily writing engine simply made nothing, and the only trace was a
    WARNING no probe reads. The job now records its outcome in `heartbeats`; this surfaces
    it with the cause attached instead of leaving another silent zero-output day.

    Deliberately quiet when the recorder says "ok" or has never run: a box that has not
    swept yet, or one where the module is dormant, must not page.
    """
    try:
        hb = next((r for r in state.get_heartbeats()
                   if r["component"] == "written_daily_seed"), None)
    except Exception as e:   # noqa: BLE001
        return True, f"skip ({type(e).__name__})"
    if not hb:
        return True, "no daily-seed run recorded yet"
    status = str(hb.get("status") or "")
    if status.startswith("error: "):
        return False, (f"the daily content engine produced NOTHING on its last run — "
                       f"{status[7:]}")
    return True, "last run produced content"


# ≥3× the interval of every sweep measured with it (120–300s), and ≥ the watchdog's own
# cadence, so one slow tick or one missed pass never pages.
_SWEEP_BEAT_MAX_AGE_S = 1800


def _probe_sweep_beat(component: str, *, what: str) -> tuple[bool, str]:
    """Freshness of a worker-written completion beat (`register_periodic(beat=...)`).

    The worker only writes the beat AFTER the periodic RETURNS, so a sweep raising every
    tick — previously one throttled `worker.periodic_error` WARNING, the exact shape of
    the seven-day gtm_unsub_sweep outage — shows up here as an AGING heartbeat. Missing
    is OK (fresh box; first tick can lag the first pass). Stale while the WORKER's own
    beat is also stale defers to the worker page — a dead worker already pages once, and
    repeating it under three more names is how the digest becomes wallpaper."""
    try:
        hbs = {r["component"]: r for r in state.get_heartbeats()}
    except Exception as e:   # noqa: BLE001 — an old/schemaless box must not sink the pass
        return True, f"skip ({type(e).__name__})"
    hb = hbs.get(component)
    if not hb:
        return True, "no run recorded yet"
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(hb["ts"])).total_seconds()
    except (ValueError, TypeError):
        return True, "skip (unparseable beat)"
    if age <= _SWEEP_BEAT_MAX_AGE_S:
        return True, f"running ({hb.get('status')}, {int(age)}s ago)"
    wk = hbs.get("worker")
    try:
        wk_age = ((datetime.now(timezone.utc)
                   - datetime.fromisoformat(wk["ts"])).total_seconds() if wk else None)
    except (ValueError, TypeError):
        wk_age = None
    if wk_age is None or wk_age > WORKER_HEARTBEAT_MAX_AGE_S:
        return True, "worker itself is down — that page owns this"
    return False, (f"{what} — its heartbeat is {int(age)}s old while the worker is alive: "
                   "the sweep is registered but never completes (raising every tick? see "
                   "worker.periodic_error in the journal)")


def _probe_machines() -> tuple[bool, str]:
    """Every ENABLED plug-in machine (marketing/lead_machine/plugins/<slug>/), by name.

    Machines register one periodic each with their own `beat`, and the worker writes a
    beat only after the callable RETURNS, so a machine raising every tick shows up as an
    aging heartbeat. Nothing watched those beats until now: the isolation the machine
    contract promises ("a crash-looping machine cannot hide behind a healthy worker")
    was true about the BEAT and false about the PAGE, because every other probe here is
    hand-written per component and no machine had one.

    This is generic on purpose. A probe per machine is a core edit per plug-in, which is
    the exact coupling the contract exists to prevent: the dependency stays data (the
    config block names the machine), never code.

    Allowance is twice the machine's own interval plus an hour, because a machine ticks
    daily where a sweep ticks in minutes, and _SWEEP_BEAT_MAX_AGE_S would page every
    box every day. A DISABLED machine is skipped entirely: dark is the shipped posture
    and a page for a machine nobody turned on is how a digest becomes wallpaper."""
    machines = (get_config() or {}).get("machines") or {}
    enabled = {s: (c or {}) for s, c in machines.items() if (c or {}).get("enabled")}
    if not enabled:
        return True, "no machines enabled"
    try:
        hbs = {r["component"]: r for r in state.get_heartbeats()}
    except Exception as e:  # noqa: BLE001 , an old/schemaless box must not sink the pass
        return True, f"skip ({type(e).__name__})"
    worker = hbs.get("worker")
    try:
        worker_age = ((datetime.now(timezone.utc)
                       - datetime.fromisoformat(worker["ts"])).total_seconds()
                      if worker else None)
    except (ValueError, TypeError):
        worker_age = None
    if worker_age is None or worker_age > WORKER_HEARTBEAT_MAX_AGE_S:
        return True, "worker itself is down , that page owns this"
    stale, ok = [], []
    for slug, conf in sorted(enabled.items()):
        hb = hbs.get(f"machine.{slug}")
        if not hb:
            ok.append(f"{slug} (no run recorded yet)")
            continue
        try:
            age = (datetime.now(timezone.utc)
                   - datetime.fromisoformat(hb["ts"])).total_seconds()
        except (ValueError, TypeError):
            ok.append(f"{slug} (unparseable beat)")
            continue
        allowance = 2 * float(conf.get("interval_s") or 86400) + 3600
        if age > allowance:
            stale.append(f"{slug} {int(age // 3600)}h stale")
        else:
            ok.append(f"{slug} ({hb.get('status')}, {int(age // 60)}m ago)")
    if stale:
        return False, ("; ".join(stale) + " , registered but not completing (raising "
                       "every tick? see worker.periodic_error in the journal)")
    return True, "; ".join(ok)


def _probe_written_writer_gate() -> tuple[bool, str]:
    return _probe_sweep_beat(
        "written_writer_gate",
        what="gate 1 of the written machine is not running — 'Write' flips on the "
             "WRITTEN board produce nothing")


def _probe_written_approve_cascade() -> tuple[bool, str]:
    return _probe_sweep_beat(
        "written_approve_cascade",
        what="gate 2 of the written machine is not running — approved pieces are not "
             "publishing anywhere")


def _probe_leadmagnet_keyword_intake() -> tuple[bool, str]:
    return _probe_sweep_beat(
        "leadmagnet_keyword_intake",
        what="lead-magnet keyword intake is not running — a 'Go Live' tick on the board "
             "arms nothing")


_CADENCE_STALE_MIN_S = 1800     # ≥ the watchdog's own cadence, so one missed tick never pages
_CADENCE_STALE_TICKS = 3


def _probe_carousel_cadence() -> tuple[bool, str]:
    """Page when the carousel cadence is switched ON but not delivering — the three ways it
    dies while the worker heartbeat stays green:

      STALE — the periodic raises every tick. The worker only logs `worker.periodic_error`
      at WARNING for a raising periodic, so before this probe a crash-looping cadence was
      silent forever. Its heartbeat is written only when a tick COMPLETES; age is therefore
      the crash-loop signal.
      stuck_due — a deck's Posting Time passed over an hour ago and it still cannot publish
      (no mirror to build from, or a build that fails every attempt). The tick heals what
      it can and says so in its beat; a deck stuck past an hour has out-lived the healing
      and needs a human — before this it was one info log line per tick, forever.
      blocked — cadence_enabled on a box whose `reel.carousel_media_source` is `airtable`:
      every scheduled post would be refused at fire time, so the switch is on and nothing
      will ever go out. A standing misconfiguration, not a blip.

    A MISSING heartbeat is OK, not a page: a fresh box's first tick can lag the first
    watchdog pass, and the dangerous variant (reel_machine never registered at all) takes
    the airtable_sync registration down with it — THAT probe pages on its stale beat.
    NO Airtable reads here: this runs every ~30min against a MONTHLY API quota, and quota
    exhaustion has already taken the sync down once (2026-07-21)."""
    try:
        from marketing.content_machine.reel import carousel_cadence as cc
        if not cc.enabled():
            # Deterministic 'off' (not the 'skip (' cannot-evaluate prefix), so a standing
            # FAIL self-clears when the owner revokes the switch — the same shape as
            # _probe_prospect_lake_stalled's deconfigured path.
            return True, "cadence disabled"
        if not cc._spaces():
            return True, "no Space binds a carousel table"
        max_age = max(_CADENCE_STALE_MIN_S, _CADENCE_STALE_TICKS * cc.interval_s())
        hb = next((r for r in state.get_heartbeats()
                   if r["component"] == "carousel_cadence"), None)
    except Exception as e:   # noqa: BLE001 — a config/import error must not sink the pass
        return True, f"skip ({type(e).__name__})"
    if not hb:
        return True, "enabled; no tick recorded yet"
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(hb["ts"])).total_seconds()
    except (ValueError, TypeError):
        return True, "skip (unparseable heartbeat)"
    if age > max_age:
        return False, (f"cadence heartbeat stale ({int(age)}s > {int(max_age)}s) — the "
                       "periodic is registered but never completes a tick (raising every "
                       "run? see worker.periodic_error) — no carousel will publish until "
                       "it does")
    status = str(hb.get("status") or "")
    if status.startswith("stuck_due:"):
        return False, ("a deck's posting slot passed over an hour ago and it still cannot "
                       f"publish — {status[10:]} — it will miss every slot until the deck "
                       "is fixed or its Posting Time cleared")
    if status.startswith("blocked:"):
        return False, ("the cadence is enabled but every post it schedules would be refused "
                       f"({status[8:]}) — set reel.carousel_media_source to r2, or turn "
                       "cadence_enabled off")
    if status.startswith("dry:"):
        return False, (f"the next posting slot ({status[4:]}) has NOTHING queued — no deck "
                       "is Ready, so that slot will pass with no carousel published. Flip "
                       "one to Ready and the cadence takes it automatically.")
    return True, f"ticking ({status}, {int(age)}s ago)"


def _probe_zernio_sdk() -> tuple[bool, str]:
    """Page when the pinned zernio-sdk has DRIFTED (version/signature) on a box that carries
    Zernio keys. The departments now fail closed (register inert on drift), so this is the
    operator's only signal that posting/inbox went dark. Pure inspection, no network. Wrapped
    so a config/import error degrades to skip — the probe must never crash the whole pass."""
    try:
        # SPACES COMES FROM CORE, and after Customer Voice Stage B that is not a tidiness point.
        # This probe is the operator's ONLY signal that the inbox has gone dark on SDK drift, and
        # the inbox now lives in Customer Voice. Resolving Spaces through content_machine meant
        # that on a Customer Voice box — no content_machine shipped — this import raised, the
        # except below returned "skip", and the probe went quiet on the one box type that now
        # owns the thing it is watching. It would not have crashed; it would have said nothing,
        # which is worse. Found by OSDev1 reviewing #1103.
        from core import spaces
        if not any(s.get("zernio_key") for s in spaces.all_spaces()):
            # Deterministic 'deconfigured' (no 'skip (' prefix) so a prior FAIL clears
            # when the operator removes the keys — see _probe_prospect_lake_stalled.
            return True, "no Zernio keys on this box"
        from core.vendors import zernio
        return zernio.verify_sdk()
    except Exception as e:   # noqa: BLE001
        return True, f"skip ({type(e).__name__})"


def _probe_reel_failed_unpaged() -> tuple[bool, str]:
    """Escalate a terminally-FAILED reel whose operator DM never reached Slack. A 'failed'
    row is invisible to the produce/approved probes; on_job_failure marks such rows
    'UNPAGED: ...' when the page send fails (e.g. Slack down during a delivery-exhausted
    failure — a correlated outage). The watchdog's dead-man-withhold then escalates even
    while Slack stays down. Read-only COUNT; missing table degrades to OK."""
    try:
        with state.connect() as c:
            n = c.execute("SELECT COUNT(*) AS n FROM reel_scripts "
                          "WHERE status='failed' AND error LIKE 'UNPAGED:%'").fetchone()["n"]
    except Exception as e:   # noqa: BLE001
        return True, f"skip ({type(e).__name__})"
    return (n == 0, f"{n} failed reel(s) whose failure DM never reached the operator — "
                    "check /dash; Slack was likely down when they died")


_PRODUCE_STUCK_AGE_H = 2      # a reel wedged in producing/approved past this pages the operator


def _stuck_produce_ids() -> list:
    """Script ids of reels wedged mid-production: 'producing' >2h (a render that never
    completed / a worker death between the produce checkpoint and the ready flip), OR
    'approved' >2h with NO live produce job — a genuinely LOST enqueue, not one still
    queued behind a legitimate overnight render backlog on the 1-vCPU box. Produce jobs
    carry the script id in raw_text; jobs statuses are queued|running|done|failed."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=_PRODUCE_STUCK_AGE_H)).isoformat()
    with state.connect() as c:
        rows = c.execute(
            "SELECT id FROM reel_scripts WHERE updated_at < ? AND ("
            "status='producing' OR (status='approved' AND NOT EXISTS ("
            "SELECT 1 FROM jobs j WHERE j.status IN ('queued','running') "
            "AND j.raw_text LIKE '%' || reel_scripts.id || '%')))",
            (cutoff,)).fetchall()
    return [r["id"] for r in rows]


def _probe_stuck_produce() -> tuple[bool, str]:
    """Re-page on a reel wedged mid-production. The produce path has NO indeterminate-reconcile
    sweep of its own, and the generic orphan reap only unsticks jobs when the worker is entirely
    dead — so without this a stuck reel sits silent on the dashboard. Read-only, deduped by
    _alert; a missing table on an older box degrades to OK. A 'producing' row wedged >2h is a
    real stall at ~8-min renders; an 'approved' row is stuck ONLY when no queued/running produce
    job exists for it — otherwise it's just waiting behind the sequential overnight backlog."""
    try:
        ids = _stuck_produce_ids()
    except Exception as e:   # noqa: BLE001 — an old/schemaless box must not sink the pass
        return True, f"skip ({type(e).__name__})"
    n = len(ids)
    return (n == 0, f"{n} reel(s) stuck in producing/approved >{_PRODUCE_STUCK_AGE_H}h "
                    f"(lost enqueue / dead render) — check + re-dispatch. ids="
                    f"{','.join(i[:8] for i in ids) or '—'}")


def _page_new_stuck_produce(was_failing: bool) -> tuple[set, bool]:
    """Per-reel fresh-edge page. The aggregate 'stuck_produce' alert dedups by state, so a
    SECOND reel going stuck while the first is already alerting would otherwise wait up to
    REMINDER_HOURS. Page immediately on any stuck id not seen last pass — but only while
    ALREADY failing (was_failing), so the OK->FAIL transition page isn't doubled. The id set
    is stored in an alert_state row ('stuck_produce:ids'); health.py matches state='FAIL'
    only, so this bookkeeping row never trips it. Overwrites the set every pass so a
    resolved-then-re-wedged reel re-pages. Returns (ids_paged, notifier_ok)."""
    try:
        cur_ids = set(_stuck_produce_ids())
    except Exception:   # noqa: BLE001 — skip this pass; leave the baseline UNTOUCHED
        # Do NOT persist an empty set here: wiping 'stuck_produce:ids' on a transient DB
        # lock would make an already-known-stuck reel look BRAND NEW next pass and fire a
        # spurious ':rotating_light: NEW reel stuck' page — the exact lock event the
        # skip-hold was added to survive. Return cleanly, baseline intact.
        return set(), True
    prev_ids = {i for i in (state.get_alert("stuck_produce:ids") or "").split(",") if i}
    new_ids = cur_ids - prev_ids
    sent_ok = True
    if new_ids and was_failing:
        dm = settings.operator_slack_user_id
        if not dm:
            log.error("watchdog.new_stuck_reel_operator_unset",
                      ids=sorted(i[:8] for i in new_ids))
        # DM before persisting the new set: a crash between yields a duplicate page next
        # pass (correct at-least-once), never a missed one.
        sent_ok = slack.send_dm(dm, ":rotating_light: NEW reel(s) stuck in production: "
                                f"{', '.join(sorted(i[:8] for i in new_ids))} — another is "
                                "already flagged; check + re-dispatch.")
        state.set_alert("stuck_produce", "FAIL", bump_alert_ts=True)   # reset the 4h clock
    state.set_alert("stuck_produce:ids", ",".join(sorted(cur_ids)))
    return (new_ids if (new_ids and was_failing) else set(), bool(sent_ok))


_OWNBOX_ORDERS_KEY = "/var/lib/aios/ownbox_orders_key"
_OWNBOX_ORDERS_KNOWN_HOSTS = "/var/lib/aios/ownbox_orders_known_hosts"
_OWNBOX_ORDERS_MISSES = 3


def _probe_ownbox_orders(run=None) -> dict[str, tuple[bool, str]]:
    """AN OWNBOX ORDER THAT NEEDS A PERSON pages the operator, one page per order.

    Measured 2026-09-15: the first real test order failed at the boot deadline and nobody was told, because
    the provisioner has no way to reach a person (no mail sender, no Slack). This box does. It reads the
    provisioner's open orders over ssh, with a key the provisioner's authorized_keys pins to ONE read-only
    command (`python -m provisioner.run --status --json`), so this box holds no way to change an order.

    EACH ORDER IS ITS OWN ALERT KEY, so a second customer's failure pages on its own instead of hiding behind
    the first's standing alert. An order drops off the moment a person records what they did
    (`--resolve <id> --note ...`) or it is delivered, and reports recovered once.
    An unreadable provisioner pages only after three passes in a row, so one dropped connection is not a page.
    Inert unless OWNBOX_ORDERS_SSH is set, which only the operator's own box does.
    """
    import json
    import subprocess
    target = (settings.ownbox_orders_ssh or "").strip()
    if not target:
        return {}
    cmd = ["ssh", "-i", _OWNBOX_ORDERS_KEY, "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
           "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={_OWNBOX_ORDERS_KNOWN_HOSTS}",
           "-o", "ConnectTimeout=15", target]
    orders, why = None, "no order list"
    try:
        res = (run or subprocess.run)(cmd, capture_output=True, text=True, timeout=60)
        if res.returncode != 0:
            why = f"ssh exit {res.returncode}: {(res.stderr or '').strip()[-160:]}"
        else:
            data = json.loads(res.stdout or "")
            orders = data.get("orders") if isinstance(data, dict) else None
    except subprocess.TimeoutExpired:
        why = "ssh timed out"
    except (OSError, ValueError) as e:
        why = type(e).__name__
    if not isinstance(orders, list):
        misses = int(state.get_alert("ownbox_orders:misses") or 0) + 1
        state.set_alert("ownbox_orders:misses", str(misses))
        if misses < _OWNBOX_ORDERS_MISSES:
            return {"ownbox_orders_readable": (True, f"skip (miss {misses}: {why})")}
        return {"ownbox_orders_readable": (False, f"cannot read Ownbox's orders from the provisioner for "
                                                  f"{misses} passes: {why}")}
    state.set_alert("ownbox_orders:misses", "0")
    need = {str(o["id"]): o for o in orders if isinstance(o, dict) and o.get("id") and o.get("needs_person")}
    paged = {i for i in (state.get_alert("ownbox_orders:ids") or "").split(",") if i}
    probes = {"ownbox_orders_readable": (True, f"{len(orders)} open order(s), {len(need)} need a person")}
    for oid, o in need.items():
        key = " (its update key needs a person)" if o.get("key_state") == "needs_human" else ""
        probes[f"ownbox_order:{oid}"] = (False, (
            f"{o.get('host') or 'no address yet'} is {o.get('state')}{key}: "
            f"{str(o.get('last_error') or o.get('key_error') or '')[:160]} | customer {o.get('email') or '?'} | "
            # A BOX THAT IS UP BUT COULD NOT BE ANNOUNCED (no mail sender yet) is handed over by a person, and the
            # one thing they need is its claim link; without it here they would have to ssh in and look it up.
            + (f"claim link to send them: {o['claim_url']} | " if o.get("claim_url") and o.get("state") == "needs_human" else "")
            + f"once handled: python -m provisioner.run --resolve {oid} --note '<what you did>'"))
    for oid in sorted(paged - set(need)):
        probes[f"ownbox_order:{oid}"] = (True, "resolved or delivered")
    state.set_alert("ownbox_orders:ids", ",".join(sorted(need)))
    return probes


def run_once() -> None:
    # Self-initialize like the other entrypoints (worker/dispatch/slack_socket): the state.py
    # migration contract already promises the watchdog boots + calls init_db, and its probes read
    # jobs/heartbeats — on a fresh box that runs before the others, this avoids a schemaless crash.
    state.init_db()
    # Capture the pre-pass stuck-produce state so the per-reel fresh-edge page (below)
    # only fires on a NEW id while ALREADY failing — never doubling the OK->FAIL page.
    _stuck_was_failing = state.get_alert("stuck_produce") == "FAIL"
    probes = {
        "disk":     _probe_disk(),
        "dispatch": _probe_dispatch(),
        "worker":   _probe_worker(),
        "stuck_indeterminate":   _probe_stuck_indeterminate(),
        "stuck_produce":         _probe_stuck_produce(),
        "reel_failed_unpaged":   _probe_reel_failed_unpaged(),
        "prospect_lake_parked":  _probe_prospect_lake_parked(),
        "prospect_lake_stalled": _probe_prospect_lake_stalled(),
        "inbox_poll":            _probe_inbox_poll(),
        "zernio_sdk":            _probe_zernio_sdk(),
        # business-outcome probes (W1.1) — throughput/halt signals, not process liveness
        "gtm_halted":            _probe_gtm_halted(),
        "gtm_intake":            _probe_gtm_intake(),
        "written_seed":          _probe_written_seed(),
        "vendor_at_cap":         _probe_vendor_at_cap(),
        "gtm_reply_ingest":      _probe_gtm_reply_ingest(),
        "gtm_mev_harvest":       _probe_gtm_mev_harvest(),
        "gtm_promote_discovered": _probe_gtm_promote_discovered(),
        "gtm_verify_sweep":      _probe_gtm_verify_sweep(),
        "gtm_archivist":         _probe_gtm_archivist(),
        "gtm_employers_sync":    _probe_gtm_employers_sync(),
        "box_config_backup":     _probe_box_config_backup(),
        "gtm_xray":              _probe_gtm_xray(),
        "gtm_domain_resolver":   _probe_gtm_domain_resolver(),
        "gtm_person_finder":     _probe_gtm_person_finder(),
        "gtm_probe_sweep":       _probe_gtm_probe_sweep(),
        "gtm_finder_sweep":      _probe_gtm_finder_sweep(),
        "airtable_sync":         _probe_airtable_sync(),
        "queue_drain":           _probe_queue_drain(),
        "send_failure_rate":     _probe_send_failure_rate(),
        "leadmagnet_throughput": _probe_leadmagnet_throughput(),
        "leadmagnet_owed":       _probe_leadmagnet_owed(),
        "leadmagnet_armed":      _probe_leadmagnet_armed(),
        "leadmagnet_guides":     _probe_leadmagnet_guides(),
        "airtable_fields":       _probe_airtable_fields(),
        "reel_coverage":         _probe_reel_coverage(),
        "carousel_cadence":      _probe_carousel_cadence(),
        "written_writer_gate":   _probe_written_writer_gate(),
        "written_approve_cascade":   _probe_written_approve_cascade(),
        "leadmagnet_keyword_intake": _probe_leadmagnet_keyword_intake(),
        # Plug-in machines, generic: one probe for all of them, never one per machine.
        "machines":              _probe_machines(),
    }
    # Probe the reasoning path that is actually CONFIGURED (shared with the A2 startup
    # probe so both page on one key).
    probes.update(_probe_ownbox_orders())
    _bkey, _bok, _bdetail = probe_backend()
    probes[_bkey] = (_bok, _bdetail)
    resolve_deconfigured_backend_alerts(_bkey)
    if settings.heygen_api_key:   # probe only on boxes where HeyGen is configured
        probes["heygen_api"] = _probe_heygen()
    # Gate on BOTH tokens — the daemon's own run_forever requires app AND bot token, so a
    # half-configured box (app set, bot unset) never beats; probing on app_token alone would
    # pin a false perpetual FAIL and page forever. Probe exactly when the daemon actually runs.
    if settings.slack_app_token and settings.slack_bot_token:
        probes["slack_socket"] = _probe_heartbeat("slack_socket")

    # Safety-net reap, REQUEUE-ONLY (max_attempts=None): terminal-failing a job must
    # fire its module failure hook, and only the worker process has modules loaded —
    # its idle-loop reap (worker.reap_orphans) owns that. This pass just unsticks
    # jobs when the worker is entirely dead. Staleness measures worker death, not
    # job duration: the worker heartbeat touches the in-flight job every 60s.
    requeued, _ = state.reap_orphan_jobs(stale_after_s=1800)
    if requeued:
        log.warning("watchdog.reaped_orphans", count=len(requeued))
    _retire_old_raws()

    notifier_ok = True
    for key, (ok, detail) in probes.items():
        state.heartbeat(f"probe:{key}", "ok" if ok else "fail")
        if not _alert(key, ok, detail):
            notifier_ok = False  # an alert we NEEDED to send did not reach Slack

    _paged, _fresh_sent_ok = _page_new_stuck_produce(_stuck_was_failing)
    if not _fresh_sent_ok:
        notifier_ok = False

    # Spend visibility: enforce in native units, REPORT in dollars (the USD overlay).
    # One line = claude $ + each vendor's units (+ est $), + the cross-vendor total.
    spent, cap = cost_guard.month_to_date_spend(), cost_guard.ceiling()
    parts, total_est = [f"claude ${spent:.2f}/${cap:.0f}"], spent
    if not _alert("meter:claude", spent < _METER_WARN_FRACTION * cap,
                  f"${spent:.2f} of ${cap:.0f} used this cycle"):
        notifier_ok = False
    for vendor in cost_guard.metered_vendors():
        used, vcap = cost_guard.vendor_usage(vendor), cost_guard.vendor_cap(vendor)
        total_est += used * cost_guard.vendor_usd_rate(vendor)
        parts.append(f"{vendor} {used:g}/{vcap:g}u")
        # The usage above is ALWAYS reported in the spend line. Only the page is optional:
        # a vendor the operator has silenced (`vendors.<v>.alerts: false`) still meters and
        # still refuses at its cap — it just stops asking him about a decision he's made.
        if not cost_guard.vendor_alerts_enabled(vendor):
            state.clear_alert(f"meter:{vendor}")
            continue
        if not _alert(f"meter:{vendor}", used < _METER_WARN_FRACTION * vcap,
                      f"{used:g} of {vcap:g} units used this cycle"):
            notifier_ok = False
    log.info("watchdog.spend", detail=" | ".join(parts),
             total_est_usd=round(total_est, 2))

    # LAST, so it sees the state every probe above just wrote — including any FAIL edge
    # raised in this very pass. It carries the repeat cadence that `_alert` no longer does.
    if not _standing_failure_digest():
        notifier_ok = False

    # External dead-man's switch. Ping ONLY when the notifier is healthy. If an alert
    # failed to reach Slack, WITHHOLD the ping on purpose — healthchecks.io then sees
    # silence and escalates through its own channel (email/SMS). That makes a broken
    # notifier — the case that would otherwise hide "everything is down" — detectable.
    if settings.healthcheck_url:
        if notifier_ok:
            try:
                requests.get(settings.healthcheck_url, timeout=10)
            except Exception:
                pass
        else:
            log.error("watchdog.notifier_down_withholding_deadman_ping")


if __name__ == "__main__":
    # Same dual-namespace guard as core.worker: `python -m core.watchdog` runs this
    # file as `__main__`; delegate to the canonical module so any state shared via
    # module globals (alert dedup, future registries) has exactly one instance.
    from core import watchdog as _canonical
    _canonical.run_once()
