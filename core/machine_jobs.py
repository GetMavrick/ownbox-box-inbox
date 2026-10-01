"""Scheduled jobs for the owner's own machines: `m.every(seconds, fn)` (SDK 1).

Owner, 2026-10-01, approving docs/PLAN_LEAD_MAGNET_MACHINE.md (decision 7): scheduled jobs are promised in
the SDK, **with limits**: never more often than every 15 seconds; run in the worker under a time budget; a
broken job is isolated and reported, never stopping the worker or the box; named `my_…`.

WHY NOT `worker.register_periodic` ITSELF. Every periodic on the box shares one thread
(`worker._periodic_loop`), so a periodic that hangs holds up every other one: the box's own sweeps, its
backups, its digests. That is acceptable for our code and not for a company's. So a machine's job is
registered with the worker as a WRAPPER that returns at once: it starts the job on its own thread and
comes back. The shared loop never waits on a machine's code.

THE LIMITS, AND HOW EACH IS KEPT:
  * every 15 seconds at most: `register` refuses less, naming the rule.
  * a time budget (BUDGET_S): Python cannot stop a thread, so a run that is still going past its budget is
    REPORTED, and the next run waits until it finishes. Two runs of one job never overlap, so a slow job
    can't stack up threads or run its own steps twice.
  * isolated and reported: a run that raises is caught on its own thread, logged with the machine's name,
    and recorded where the Add a Machine page reads it. The next run goes ahead as scheduled; when one
    succeeds, the report clears.
  * named `my_…`: the worker knows it as `<machine key>.<job>`, and every machine key starts `my_`.

The Stop everything switch still holds: the worker's loop starts nothing while the box is paused, so no job
starts either.

ONE WRITE PER CHANGE. A 20-second job would otherwise write the box's database three times a minute.
The record is written only when a job starts failing, changes how it fails, or recovers.

NEVER RAISES into the worker. CORE NAMES NO MACHINE: names here are data the machine passed in.
"""
from __future__ import annotations

import math
import re
import threading
import time
from datetime import datetime, timezone

from core.logging import get_logger, scrub_secrets

log = get_logger(__name__)

MIN_SECONDS = 15        # owner, 2026-10-01: "never more often than every 15 seconds"
BUDGET_S = 120          # one run's time budget; past it, the run is reported and the next one waits
NS = "core"             # box_settings namespace: the box's own records
FAILING = "machine_jobs_failing"    # {"<key>.<job>": {"machine", "job", "error", "since", "at"}}

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_RUNS: dict[str, dict] = {}         # "<key>.<job>" -> {"thread", "started"}; this process only
_LOCK = threading.Lock()
_REPORT = threading.Lock()          # the failing record is read, changed and written as one step


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def register(machine_key: str, seconds, fn, *, name: str | None = None) -> str:
    """Schedule `fn()` every `seconds` in the worker, as `<machine_key>.<name>`. -> that name.

    Raises ValueError, naming the rule, for a job more often than every 15 seconds or a name that isn't
    lowercase letters, digits and underscores. A machine raising at import is refused by the loader with
    that sentence, which is where its builder will look."""
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        raise ValueError(f"m.every needs a number of seconds, not {seconds!r}") from None
    if not math.isfinite(seconds):
        raise ValueError(f"m.every needs a finite number of seconds, not {seconds!r}")
    if seconds < MIN_SECONDS:
        raise ValueError(f"a scheduled job runs at most every {MIN_SECONDS} seconds (it asked for every "
                         f"{seconds:g}): the box shares one worker with everything else it does")
    if not callable(fn):
        raise ValueError("m.every needs a function to run")
    job = name if name is not None else getattr(fn, "__name__", "")
    if not _NAME.match(str(job or "")):
        raise ValueError(f"a scheduled job's name {job!r} must be lowercase letters, digits and "
                         f"underscores; pass name= if the function's own name isn't")
    full = f"{machine_key}.{job}"
    from core import worker
    worker.register_periodic(_wrapper(full, machine_key, job, fn), interval_s=seconds, name=full)
    return full


def _wrapper(full: str, machine_key: str, job: str, fn):
    """What the worker's shared loop calls: start a run on its own thread unless one is still going.
    Returns at once, whatever the job does."""
    def start():
        with _LOCK:
            run = _RUNS.get(full)
            if run and run["thread"].is_alive():
                took = time.monotonic() - run["started"]
                if took > BUDGET_S and not run.get("reported"):
                    run["reported"] = True
                    _failed(full, machine_key, job,
                            f"still running after {int(took)} seconds, past its {BUDGET_S}-second budget; "
                            f"the next run waits until this one finishes")
                return {"status": "still_running"}
            t = threading.Thread(target=_run, args=(full, machine_key, job, fn), daemon=True,
                                 name=f"machine-job:{full}")
            _RUNS[full] = {"thread": t, "started": time.monotonic()}
            t.start()
        return {"status": "started"}
    start.__name__ = f"machine_job_{job}"
    return start


def _run(full: str, machine_key: str, job: str, fn) -> None:
    """One run, on its own thread. Nothing it raises leaves this function."""
    try:
        fn()
    except BaseException as e:                       # noqa: BLE001 — a company's code, isolated
        _failed(full, machine_key, job, f"{type(e).__name__}: {str(e)[:200]}")
        return
    with _LOCK:
        run = _RUNS.get(full) or {}
        over = run.get("reported")
    if not over:
        _recovered(full)


def failing() -> dict:
    """Every scheduled job whose last run failed or overran: {"<key>.<job>": {...}}. Never raises."""
    try:
        from core import box_settings
        v = box_settings.get(NS, FAILING, default={})
        return v if isinstance(v, dict) else {}
    except Exception:                                # noqa: BLE001 — a reader must not cost the page
        return {}


def _failed(full: str, machine_key: str, job: str, error: str) -> None:
    # A COMPANY'S EXCEPTION CAN CARRY A KEY, or a URL with one in it, and this text lands in the journal and
    # on the Add a Machine page (OSDev1's review of #1748). Scrubbed before either sees it.
    error = scrub_secrets(str(error))
    log.error("machine_job.failed", job=full, machine=machine_key, error=error[:300])
    try:
        with _REPORT:
            cur = failing()
            was = cur.get(full) or {}
            if was.get("error") == error:
                return                                   # same failure again: nothing new to write
            cur[full] = {"machine": machine_key, "job": job, "error": error,
                         "since": was.get("since") or _now(), "at": _now()}
            _put(cur)
    except Exception as e:                           # noqa: BLE001 — reporting must not raise either
        log.warning("machine_job.report_failed", job=full, error=type(e).__name__)


def _recovered(full: str) -> None:
    try:
        with _REPORT:
            cur = failing()
            if full in cur:
                del cur[full]
                _put(cur)
                log.info("machine_job.recovered", job=full)
    except Exception as e:                           # noqa: BLE001
        log.warning("machine_job.report_failed", job=full, error=type(e).__name__)


def _put(value: dict) -> None:
    from core import box_settings
    box_settings.put(NS, FAILING, value, set_by="machine_jobs")


def failing_for(machine_key: str) -> list[dict]:
    """This machine's failing jobs, for the page that lists the owner's machines."""
    return [v for v in failing().values() if v.get("machine") == machine_key]
