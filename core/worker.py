"""Worker: claims queued jobs and runs the right module handler.

Run as its own process/systemd service. Modules register a handler per intent;
until reel-machine/gtm-engine land, an unrouted job fails cleanly with a clear
message rather than hanging.

Routing: an explicit `intent` on the job short-circuits the router (free). Only
when absent do we spend one cheap Haiku call to classify.
"""
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from core import state
from core.brain import think
from core.config import get_config
from core.exceptions import BudgetExceeded, RateCapped, RetryableError
from core.logging import get_logger, scrub_secrets
from core.queue import queue

log = get_logger(__name__)

# How long a soft-budget-paused job stays unclaimable. Must exceed run_forever's pause_interval
# (300s) or the worker wakes and re-claims the same job it just paused on.
_BUDGET_DEFER_S = 600.0

KNOWN_INTENTS = ("reel", "gtm", "brain", "agent", "autopost", "leadmagnet")

# Beat every 60s; the watchdog alarms at 300s, leaving ~5 missed beats of margin.
_HEARTBEAT_INTERVAL = 60.0

# Orphan-reap cadence + staleness. The heartbeat thread touches the in-flight job's
# updated_at every beat, so "stale 30 min" means the worker holding it is DEAD —
# independent of how long a legitimate render runs (H2: liveness, not duration).
_REAP_INTERVAL = 60.0
_REAP_STALE_S = 1800

# The job currently inside process_one(), visible to the heartbeat thread (a single
# reference assignment — atomic under the GIL). None when idle.
_current_job_id: str | None = None


def _max_attempts() -> int:
    """Max times a RetryableError job is requeued before it's failed (config)."""
    return int(get_config().get("worker", {}).get("max_attempts", 5))


def _backoff_seconds(attempts: int) -> float:
    """Exponential retry backoff: base * 2^(attempts-1), capped. attempts=1 → 60s,
    2 → 120s, 3 → 240s ... capped at 600s. Base configurable (worker.retry_backoff_s)."""
    base = float(get_config().get("worker", {}).get("retry_backoff_s", 60))
    return min(base * (2 ** max(attempts - 1, 0)), 600.0)


def load_modules() -> None:
    """Import each enabled department module so its handler self-registers.

    The list lives in config (`modules:`), NOT in code here — the kernel must never
    import a business module directly (that would couple core/ to marketing/). The
    dependency is data: core depends on config, config names the modules, importing
    them triggers their register() call. Adding gtm_engine or a future department is
    a one-line config edit, no kernel change.
    """
    import importlib
    for path in get_config().get("modules", []):
        importlib.import_module(path)
        log.info("worker.module_loaded", module=path)
    # PACKS ARE FOUND, NOT LISTED (plan D3). A pack dropped under a host machine's plugins/
    # declares itself in machine.yaml; the overlay cannot carry a modules: list (lists REPLACE
    # on merge), and editing the tracked config breaks "take our updates forever" — so the
    # kernel discovers them. Still data, not code: a manifest is data. A pack that fails
    # validation is skipped and LOGGED, never silently dropped; the doctor lists it too.
    from core import packs
    for m in packs.discover():
        mod = m.get("module")
        if not mod:
            continue                                   # a config recipe: the host runs it by kind
        importlib.import_module(mod)
        log.info("worker.pack_loaded", slug=m["slug"], module=mod, host=m["host"])


_REGISTRATIONS_IMPORTED = False


def import_registrations() -> list[str]:
    """Import this box's machine modules ONCE, in whichever process is asking, so the things
    they register at import — set-up steps, health checks — exist here too. Returns the modules
    that failed to import.

    THE TRAP THIS EXISTS FOR, measured twice already. Registrations are MODULE-LEVEL GLOBALS and
    a box runs several processes: the worker imports `modules:` (above), the web process imports
    `web_modules:`, and the watchdog is its own `python -m core.watchdog` that imported neither.
    A machine that registers a set-up step in its worker module would render nothing on a screen
    served by the web process, and a probe it registers would never run in the watchdog — with
    no error anywhere, because an empty registry looks exactly like a machine with nothing to
    say. dispatch._load_packs documents the first time this bit (2026-09-06).

    NOT `load_modules()`. The worker is right to crash on a bad import; a web page or a watchdog
    pass is not — one machine's broken import must cost that machine its steps and checks, and
    nothing else. So every failure is logged by name and skipped. Idempotent: importlib caches
    modules, and the flag keeps pack discovery from re-scanning on every screen render.
    """
    global _REGISTRATIONS_IMPORTED
    if _REGISTRATIONS_IMPORTED:
        return []
    _REGISTRATIONS_IMPORTED = True
    import importlib
    paths = [p for p in (get_config().get("modules") or []) if isinstance(p, str)]
    try:
        from core import packs
        paths += [m["module"] for m in packs.discover() if m.get("module")]
    except Exception as e:                          # noqa: BLE001 — discovery must not cost the page
        log.error("worker.registrations_discover_failed", error=type(e).__name__, detail=str(e)[:200])
    failed = []
    for path in paths:
        try:
            importlib.import_module(path)
        except Exception as e:                      # noqa: BLE001 — one machine, not the process
            failed.append(path)
            log.error("worker.registrations_import_failed", module=path, error=type(e).__name__,
                      detail=str(e)[:200])
    return failed
    for path, why in packs.invalid():
        log.warning("worker.pack_invalid", path=path, reason=why)


# Modules register here: intent -> callable(job: dict) -> result: dict
HANDLERS: dict[str, callable] = {}
# Optional terminal-failure hooks: intent -> callable(job, error). Called when a job
# FAILS for good (real bug, or retries exhausted) so the module can reflect the
# failure in its own state (e.g. a dashboard row). Never on requeues/pauses.
FAILURE_HOOKS: dict[str, callable] = {}


def register(intent: str, handler, on_failure=None) -> None:
    """Called by a department module at import/startup to claim its intent."""
    HANDLERS[intent] = handler
    if on_failure:
        FAILURE_HOOKS[intent] = on_failure
    log.info("worker.registered", intent=intent)


# Periodic background tasks: modules register a callable + interval (exactly like
# they register handlers). The cadence loop lives in the kernel so core stays
# generic — the task body (e.g. Airtable sync) lives in the department module.
PERIODIC: list[dict] = []


def register_periodic(fn, *, interval_s: float, name: str, beat: str | None = None) -> None:
    """Register a zero-arg callable to run every interval_s seconds in the worker's
    background thread. Idempotent on name so a module re-import (tests) won't stack
    duplicates.

    `beat` (opt-in): a heartbeats component written ONLY after the callable RETURNS.
    A raising periodic writes nothing, so its heartbeat AGES — and a watchdog probe can
    page on the age. Without this, a periodic that crashes every tick is one throttled
    `worker.periodic_error` WARNING in a journal nobody reads (how gtm_unsub_sweep failed
    unseen for seven days). This generalizes the wrapper carousel_cadence grew for
    exactly that reason; a periodic that writes its own richer beat keeps doing so and
    simply doesn't pass `beat`."""
    for t in PERIODIC:
        if t["name"] == name:
            t.update(fn=fn, interval=float(interval_s), beat=beat)
            return
    # "last" is on the MONOTONIC clock, which counts from SYSTEM boot. 0.0 meant "due when
    # uptime exceeds the interval": instant on the owner's 89-day-old box, but on a VPS created
    # eight minutes ago a daily machine, a 6h recipe sweep and the hourly digest all waited out
    # their full interval — measured 2026-09-06 on a throwaway droplet: pack loaded, tick
    # registered, nothing ran. -inf makes the first pass due on every box, old or new.
    PERIODIC.append({"fn": fn, "interval": float(interval_s), "name": name,
                     "last": float("-inf"), "beat": beat})
    log.info("worker.registered_periodic", name=name, interval_s=interval_s)


def _fire_failure_hook(job: dict, error: Exception) -> None:
    hook = FAILURE_HOOKS.get(job.get("intent") or "")
    if hook:
        try:
            hook(job, error)
        except Exception as e:  # a module hook must never take the worker down
            log.warning("worker.failure_hook_error", job_id=job["id"], error=str(e)[:200])
    _notify_failure(job, error)


def _is_locked_error(e: Exception) -> bool:
    """A transient SQLite contention error (retryable), not a code bug."""
    import sqlite3
    return isinstance(e, sqlite3.OperationalError) and (
        "database is locked" in str(e).lower() or "database is busy" in str(e).lower())


def _friendly_error(error: Exception) -> str:
    """Owner-facing one-liner: name the fixable cause when we recognize it; never
    leak internals beyond a truncated first line."""
    # SCRUB first: the fallback path echoes str(error)'s first line into the owner's
    # Slack thread, so a vendor key embedded in a message (Hunter's ?api_key=) would
    # reach the most human-visible sink even though jobs.error + logs are scrubbed.
    s = scrub_secrets(str(error))
    low = s.lower()
    if isinstance(error, BudgetExceeded) and getattr(error, "hard", False):
        v = (getattr(error, "vendor", None) or "").strip()
        name = v.title() if v else "A vendor"
        topup = v.title() if v else "the vendor"
        return (f"{name} isn't accepting API calls right now — either its monthly quota is used "
                f"up for this billing period, or the account needs attention. I stopped rather "
                f"than retry against a hard limit. Check {topup} (top up or resolve it), then "
                f"re-run this.")
    if "heygen" in low and ("not set" in low or "config" in low):
        return ("video production isn't connected yet (HeyGen key missing) — the "
                "script is saved on the dashboard and can be retried from there.")
    if ("apollo" in low or "instantly" in low) and ("not set" in low or "config" in low):
        # VendorError("<vendor>", "config", msg) → surface msg, it's owner-prose.
        detail = re.sub(r"^\w+ error config:\s*", "", s)
        return f"outreach isn't connected yet — {detail[:200]}"
    if "no module registered" in low:
        return "that capability isn't enabled on this box."
    if "retries exhausted" in low:
        return "I retried several times and couldn't get it through — try again in a bit."
    # An actionable config/setup message ("X is not set / not configured / missing") is
    # owner-prose — surface it so the owner knows EXACTLY what to fix.
    if any(k in low for k in ("not set", "not configured", "not connected", "missing")):
        return s[:200]
    # A raw vendor hiccup (HTTP codes / internals) — don't echo it into the owner's channel
    # (the full error is in the logs via worker.failed). Keep it human on camera.
    if any(v in low for v in ("scrapecreators", "zernio", "airtable", "hunter", "resend")):
        return "a vendor hiccup got in the way — it's logged. Try again in a moment."
    return f"`{s[:160]}`"


def _notify_slow(job: dict, kind: str) -> None:
    """NEVER-SILENT on the SLOW path (the gap reviewers found): the FIRST time an owner-
    threaded job is requeued (vendor slow) or paused (usage cap), post ONE terse heads-up so
    the thread isn't dead while it backs off. Deduped via a checkpoint so repeated retries
    don't spam; best-effort + zero-Claude."""
    channel, thread = job.get("slack_channel_id"), job.get("slack_thread_ts")
    if not channel or not thread:
        return
    if state.load_checkpoint(job["id"], "slow_notified", default=state.MISSING) is not state.MISSING:
        return
    msg = {
        "budget": "Still on it — I'm briefly at my usage limit; I'll finish this shortly.",
        "rate-cap": "Still on it — this hour's send cap is full; I'll send when a slot frees.",
    }.get(kind, "Still working on it — a vendor's being slow. I'll post here when it's done.")
    try:
        from core import slack
        slack.thread_reply(channel, thread, f":hourglass_flowing_sand: {msg}")
        state.save_checkpoint(job["id"], "slow_notified", True)
    except Exception as e:  # noqa: BLE001 — a heads-up must never compound the delay
        log.warning("worker.slow_notify_error", error=str(e)[:200])


def _notify_failure(job: dict, error: Exception) -> None:
    """NEVER-SILENT: a Slack-originated job that dies terminally must say so in
    its thread — the owner's standing mandate ('no three dots and never coming
    back'). Best-effort and zero-Claude; a notifier outage only logs."""
    channel = job.get("slack_channel_id")
    if not channel:
        return
    try:
        from core import slack
        text = f":warning: That didn't go through — {_friendly_error(error)}"
        thread = job.get("slack_thread_ts")
        sent = (slack.thread_reply(channel, thread, text) if thread
                else slack.post(channel, text))
        if not sent:
            log.warning("worker.failure_notice_undelivered", job_id=job["id"])
    except Exception as e:  # noqa: BLE001 — never compound a failure
        log.warning("worker.failure_notify_error", error=str(e)[:200])


def _never_silent(job: dict) -> None:
    """A1 — owner-facing silence is impossible by construction. Called on the SUCCESS
    path (terminal failures already self-report via _notify_failure): a job that
    ORIGINATED from an owner's Slack message (it carries both a channel and a thread to
    reply in) but completed without ANY message reaching Slack gets one terse fallback,
    so the owner is never left on 'three dots'. Zero-Claude + best-effort, and scoped to
    owner-initiated jobs by the thread check: automated sweeps (e.g. the morning scrape
    that intentionally stays quiet on 0 drafts carries no reply thread) are unaffected."""
    from core import slack
    from core.config import settings
    channel = job.get("slack_channel_id")
    thread = job.get("slack_thread_ts")
    # No bot token → the system can't reach Slack at all (a dashboard-only / dev box), so
    # there is no owner-in-thread to leave silent; the failure path noops the same way.
    if (not settings.slack_bot_token or not channel or not thread
            or slack.posted_in_scope()):
        return
    slack.thread_reply(channel, thread,
                       ":white_check_mark: Done — no details to report on that one.")


def route(raw_text: str) -> str:
    """Classify raw text to an intent. Used only when no intent hint was given."""
    prompt = ("Classify the request into exactly one label from "
              f"{KNOWN_INTENTS}. Reply with only the label, lowercase.\n\n"
              f"Request:\n{raw_text}")
    label = think("router", prompt, max_tokens=8, timeout=20).strip().lower()
    if label not in KNOWN_INTENTS:
        log.warning("worker.route_unknown", label=label)
    return label


def process_one():
    """Claim and run one job.

    Returns: True (did work), False (queue empty), or "paused" (budget ceiling hit —
    the job was put back, not failed). A budget pause is NOT a failure and does not
    consume an attempt; the spec's intent is to wait for the next budget window.

    Three error classes, three distinct fates:
      - BudgetExceeded   → pause indefinitely, refund the attempt (wait for budget)
      - RetryableError   → requeue and CONSUME the attempt, up to max_attempts; only
                           then fail. Survives a multi-minute vendor/API outage
                           instead of burning the queue to 'failed'.
      - any other        → fail immediately (a real bug is terminal; don't loop).

    NOTE the requeue paths re-run the handler from the top — handlers MUST be
    resumable (checkpoint costly/side-effecting steps via state.save_checkpoint and
    skip completed ones). See docs/HANDLER_CONTRACT.md.
    """
    global _current_job_id
    job = queue.claim_next()
    if not job:
        return False
    _current_job_id = job["id"]   # heartbeat thread now touches this job's updated_at
    from core import slack
    slack.begin_post_tracking()   # A1: track whether anything reaches the owner this job
    try:
        intent = job.get("intent") or route(job["raw_text"])
        if intent != job.get("intent"):
            state.update_job(job["id"], intent=intent)
        handler = HANDLERS.get(intent)
        if not handler:
            err = RuntimeError(f"no module registered for intent '{intent}'")
            queue.fail(job["id"], str(err))
            _fire_failure_hook(job, err)   # no hook will match; the Slack notice fires
            log.warning("worker.no_handler", job_id=job["id"], intent=intent)
            return True
        result = handler(job)
        queue.complete(job["id"], result or {})
        # A1: the success path is the ONLY never-silent gap — terminal FAILURES already
        # self-report via _notify_failure. If the handler said nothing to the owner's
        # thread, post one terse line so the ask never ends on silence.
        # A5: the job is DONE from here — its (possibly paid) side effects happened. A
        # raise out of this best-effort notifier used to land in the generic except
        # below, overwrite done→failed, fire the failure hook, and tell the owner a
        # SUCCEEDED job failed. Best-effort means best-effort: swallow + log.
        try:
            _never_silent(job)
        except Exception as e:  # noqa: BLE001 — deliberate; see above
            log.warning("worker.never_silent_failed", job_id=job["id"], error=str(e)[:150])
        log.info("worker.done", job_id=job["id"], intent=intent)
        return True
    except BudgetExceeded as e:
        if getattr(e, "hard", False):
            # HARD vendor quota (billing-period / credit exhaustion) — days to reset. Pausing +
            # retrying every 5 min re-burns paid upstream calls (each discover re-runs the paid
            # Apollo sweep) AND freezes the whole worker (return "paused" → 300s sleep), all while
            # the owner reads a misleading "I'll finish shortly." Fail it honestly instead: the
            # terminal path posts a vendor-named, actionable notice via _friendly_error, the loop
            # keeps moving (return True), and the owner re-runs once the quota is restored.
            err = scrub_secrets(str(e))
            queue.fail(job["id"], f"hard vendor quota: {err}")
            _fire_failure_hook(job, e)
            log.warning("worker.budget_hard_fail", job_id=job["id"],
                        vendor=getattr(e, "vendor", None), error=err[:200])
            return True
        # SOFT, self-refreshing window (cost-guard month ceiling / Claude sub cap) — pause + retry.
        _notify_slow(job, "budget")   # heads-up so an owner-threaded job isn't dead-silent
        # not_before is what stops this pausing the WHOLE BOX. Without it the job is instantly
        # re-claimable, so run_forever wakes from its pause and re-claims THIS SAME job (oldest
        # created_at) every cycle, forever — refund_attempt=True means attempts oscillate 1→0 and
        # never exhaust. Jobs needing no reasoning at all (autopost, a produce past its rewrite)
        # never run, while the heartbeat thread keeps the watchdog green and the owner's only
        # message was "I'll finish this shortly". Defer THIS job past the pause window instead;
        # the loop moves on and the rest of the queue drains.
        retry_at = (datetime.now(timezone.utc) + timedelta(seconds=_BUDGET_DEFER_S)).isoformat()
        state.requeue_job(job["id"], refund_attempt=True, not_before=retry_at)
        log.warning("worker.budget_pause", job_id=job["id"], detail=str(e), not_before=retry_at)
        return "paused"
    except RateCapped as e:
        # A full hourly send cap is a DEFERRAL — not an error (no attempt burned;
        # RetryableError's ~15-min budget terminally failed burst jobs inside a
        # 60-min cap window) and not a queue pause (returning "paused" here would
        # stall EVERY module 5 min per capped job). Requeue for when capacity
        # frees, refund the attempt, keep the loop moving.
        _notify_slow(job, "rate-cap")   # one-time heads-up, then quiet
        state.requeue_job(job["id"], refund_attempt=True, not_before=e.not_before)
        log.info("worker.rate_cap_defer", job_id=job["id"], not_before=e.not_before)
        return True
    except RetryableError as e:
        # claim_next already incremented attempts, so job["attempts"] is this run's count.
        # scrub_secrets: defense-in-depth backstop — a vendor client should not put a key in a
        # URL, but if one does (Hunter's ?api_key=), never let it reach jobs.error or a log line.
        attempts = job.get("attempts", 1)
        err = scrub_secrets(str(e))
        if attempts >= _max_attempts():
            queue.fail(job["id"], f"retries exhausted: {err}")
            _fire_failure_hook(job, e)
            log.error("worker.retries_exhausted", job_id=job["id"],
                      attempts=attempts, error=err)
        else:
            _notify_slow(job, "vendor")   # first requeue → one terse heads-up, then quiet
            # Exponential backoff via not_before: without it, a single worker
            # re-claims the job IMMEDIATELY and burns every attempt in seconds
            # during an outage — the opposite of "survive the outage".
            delay = _backoff_seconds(attempts)
            nb = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
            state.requeue_job(job["id"], refund_attempt=False, not_before=nb)
            log.warning("worker.retry_requeue", job_id=job["id"], attempts=attempts,
                        backoff_s=delay, error=err[:200])
        return True
    except Exception as e:
        # A transient SQLite lock (a long deploy-time migration, or contention among the
        # worker/dispatch/watchdog writers exceeding the 5s busy_timeout) is NOT a bug — retry
        # it like any transient failure rather than terminally failing an otherwise-fine job.
        if _is_locked_error(e) and job.get("attempts", 1) < _max_attempts():
            attempts = job.get("attempts", 1)
            delay = _backoff_seconds(attempts)
            nb = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
            state.requeue_job(job["id"], refund_attempt=False, not_before=nb)
            log.warning("worker.db_locked_retry", job_id=job["id"],
                        attempts=attempts, backoff_s=delay)
            return True
        err = scrub_secrets(str(e))
        queue.fail(job["id"], err)
        _fire_failure_hook(job, e)
        log.error("worker.failed", job_id=job["id"], error=err)
        return True
    finally:
        _current_job_id = None


def _heartbeat_loop(interval: float) -> None:
    """Emit worker liveness on a FIXED cadence, independent of job duration.

    First principles: liveness must not be coupled to how long a job runs. A
    composite render legitimately occupies process_one() for minutes (measured
    ~8 min in the HyperFrames spike). If we only beat between jobs, the watchdog
    (300s staleness threshold) would page a false 'worker dead' on every long
    render. So a daemon thread beats regardless of what the main loop is doing.

    This reports process-alive. A job wedged far beyond its expected duration is
    the module handler's own timeout to enforce, not the kernel's heartbeat.
    """
    while True:
        try:
            state.heartbeat("worker", "ok")
            jid = _current_job_id
            if jid:
                # Keep the in-flight job visibly alive so the reaper's staleness
                # check measures worker DEATH, not job duration (H2).
                state.touch_job(jid)
        except Exception as e:  # a transient DB hiccup must never kill liveness reporting
            log.warning("worker.heartbeat_error", error=str(e))
        time.sleep(interval)


def _halted() -> bool:
    """The kill switch (core.pause): data/PAUSED exists → both loops idle. Checked every tick so
    a halt takes effect within seconds and a resume needs no restart. A missing pause module
    (an older foundation) means not halted — the switch can only ever stop, never start."""
    try:
        from core import pause
        return pause.is_paused()
    except Exception:  # noqa: BLE001
        return False


# A RESTART MUST NOT RE-RUN WHAT ALREADY RAN. `last` starts at -inf so a brand-new box runs every
# periodic at once (register_periodic says why), and until 2026-09-10 every RESTART did the same:
# each deploy re-ran every daily walker with a fresh per_day budget. Measured that day on the box:
# finder_sweep made 22 paid calls after a 17:42 start, 11 more after an 18:03 restart and started
# again after 18:20, so the owner's 20 a day was near 50. A periodic registered with `beat=` already
# records when it last finished, so the loop seeds `last` from that beat once, before its first pass.
# Still due at start: no beat, an unreadable or future beat, one older than the interval, and a beat
# whose status says the periodic did no work (a machine a deploy just enabled must run then, not a
# day later).
_DORMANT_BEATS = frozenset({"disabled", "off", "dormant", "unconfigured", "not_configured",
                            "no_roster", "no_operator", "paused", "skipped", "quiet",
                            "not_a_send_day"})


def _seed_from_beats(tasks: list, *, now_wall: datetime | None = None,
                     now_mono: float | None = None) -> list:
    """Seed each never-run task's `last` from its own completion beat. -> names seeded. Never raises."""
    try:
        beats = {b["component"]: b for b in state.get_heartbeats()}
    except Exception as e:  # noqa: BLE001 — no table yet (a fresh box): keep run-at-start
        log.warning("worker.periodic_seed_unavailable", error=str(e)[:160])
        return []
    now_wall = now_wall or datetime.now(timezone.utc)
    now_mono = time.monotonic() if now_mono is None else now_mono
    seeded = []
    for task in tasks:
        b = beats.get(task.get("beat") or "")
        if not b or task.get("last") != float("-inf"):
            continue
        status = str(b.get("status") or "")
        if status != "ok" and not status.startswith("ok:"):
            continue                    # not a completion beat this loop wrote
        if status[3:].split(":", 1)[0] in _DORMANT_BEATS:
            continue
        try:
            age = (now_wall - datetime.fromisoformat(str(b.get("ts")))).total_seconds()
        except (TypeError, ValueError):
            continue
        if 0 <= age < float(task["interval"]):
            task["last"] = now_mono - age
            seeded.append(task["name"])
    return seeded


def _periodic_loop() -> None:
    """Run each registered periodic task when its interval elapses. Best-effort and
    isolated: one task raising never stops the others, and the loop never touches
    the job pipeline — a slow sync can't wedge job processing."""
    seeded = _seed_from_beats(PERIODIC)
    if seeded:
        log.info("worker.periodic_seeded", tasks=seeded)
    while True:
        if _halted():
            time.sleep(5)
            continue
        now = time.monotonic()
        for task in PERIODIC:
            if now - task["last"] >= task["interval"]:
                task["last"] = now
                try:
                    res = task["fn"]()
                except Exception as e:  # a periodic task must never take the worker down
                    log.warning("worker.periodic_error", name=task["name"],
                                error=str(e)[:200])
                    continue        # no beat on a raise — staleness IS the crash signal
                _beat_periodic(task, res)
        time.sleep(5)


def _beat_periodic(task: dict, res) -> None:
    """The completion beat for a periodic registered with beat=... — reached only after
    the callable returned. Carries the result's status so `health` shows what the last
    run did; a beat-write failure is bookkeeping and must never take the loop down (the
    heartbeat then goes stale, which is itself the page)."""
    if not task.get("beat"):
        return
    try:
        status = res.get("status") if isinstance(res, dict) else None
        state.heartbeat(task["beat"], (f"ok:{status}" if status else "ok")[:80])
    except Exception as e:  # noqa: BLE001
        log.warning("worker.beat_failed", name=task.get("name"), error=str(e)[:120])


def reap_orphans() -> None:
    """Reap stale 'running' jobs from THIS process — the one with failure hooks
    loaded. Jobs whose attempts are exhausted are terminally failed by the reaper
    (closing the poison-job loop: a job that crashes the worker never raises, so
    the in-band retry cap can never fire for it) and their module failure hooks run
    here, so e.g. a dashboard row flips to 'failed' instead of spinning forever.
    The watchdog's reap is requeue-only — it has no modules loaded."""
    requeued, failed = state.reap_orphan_jobs(stale_after_s=_REAP_STALE_S,
                                              max_attempts=_max_attempts())
    for job in requeued:
        log.warning("worker.reaped_requeued", job_id=job["id"], attempts=job["attempts"])
    for job in failed:
        err = RuntimeError(f"worker died mid-job; {job['attempts']} attempts exhausted")
        _fire_failure_hook(job, err)
        log.error("worker.reaped_failed", job_id=job["id"], attempts=job["attempts"])


def run_forever(poll_interval: float = 2.0, pause_interval: float = 300.0,
                heartbeat_interval: float = _HEARTBEAT_INTERVAL) -> None:
    log.info("worker.start")
    state.init_db()  # apply pending migrations on startup, so a deploy is just
    #                  pull+restart with no separate migration step to forget (a
    #                  worker-only restart used to run against a stale schema).
    load_modules()  # register department handlers before claiming any job
    # Daemon thread → dies with the process, so stopped beats == dead worker (correct).
    # Start the heartbeat BEFORE the backend probe: the probe runs a real `claude -p`
    # (up to ~60s if the CLI is slow), and we must not delay the worker's first beat, or a
    # watchdog pass in that window would misread "worker no beat yet".
    threading.Thread(target=_heartbeat_loop, args=(heartbeat_interval,),
                     daemon=True, name="worker-heartbeat").start()
    # A2: probe the reasoning backend at startup so a dead-at-boot backend pages in seconds
    # (deduped with the watchdog via the shared alert key) instead of up to a full watchdog
    # interval of silent total failure. Best-effort: a dead backend must NOT stop the worker
    # booting. Also seeds the `brain_backend` heartbeat the `health` pulse reads.
    try:
        from core import watchdog
        from core import cost_digest  # noqa: F401 — registers its own periodic at import
        from core import report  # noqa: F401 — the Morning Review: snapshot, close, send
        watchdog.check_backend_now()
    except Exception as e:  # noqa: BLE001 — a probe hiccup must never block startup
        log.warning("worker.backend_probe_error", error=str(e)[:200])
    try:
        # The tenant overlay's off-box copy (core/box_config_backup.py): a daily periodic, AND a
        # capture now on its own short thread. Every periodic shares one thread and all are due at
        # start; registered last, the first pass waited behind the walkers (measured 2026-09-10:
        # nothing captured four minutes after a deploy), so a box redeployed faster than that
        # queue drains would never have captured at all. Its own try: a fault here must never
        # cost the backend probe above its startup check.
        from core import box_config_backup
        box_config_backup.boot()
    except Exception as e:  # noqa: BLE001
        log.warning("worker.box_config_backup_import_error", error=str(e)[:200])
    if PERIODIC:
        # Daemon thread → dies with the process. Started after load_modules() so
        # every module's register_periodic() has already run.
        threading.Thread(target=_periodic_loop, daemon=True,
                         name="worker-periodic").start()
        log.info("worker.periodic_started", tasks=[t["name"] for t in PERIODIC])
    last_reap = 0.0
    while True:
        if _halted():
            time.sleep(poll_interval)
            continue
        outcome = process_one()
        if outcome == "paused":
            log.warning("worker.paused_for_budget", sleep_s=pause_interval)
            time.sleep(pause_interval)   # back off so we don't hot-loop the ceiling
        elif not outcome:
            # Idle: the cheap moment to reap. After a crash-restart the loop lands
            # here (the stuck job is 'running', not claimable) and unsticks it.
            if time.monotonic() - last_reap >= _REAP_INTERVAL:
                try:
                    reap_orphans()
                except Exception as e:   # reaping must never take the worker down
                    log.warning("worker.reap_error", error=str(e)[:200])
                last_reap = time.monotonic()
            time.sleep(poll_interval)


if __name__ == "__main__":
    # `python -m core.worker` executes THIS FILE as module `__main__`, while the
    # business modules' register() calls import and fill `core.worker.HANDLERS` —
    # a DIFFERENT instance of this same file. Running run_forever() from here would
    # poll with a permanently-empty handler table (bit us live: first systemd job
    # failed "no module registered for intent 'reel'"). Delegate to the canonical
    # module object so there is exactly one HANDLERS dict in the process.
    from core import worker as _canonical
    _canonical.run_forever()
