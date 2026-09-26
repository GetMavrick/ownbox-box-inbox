"""Upgrading this box to Pro: what the provisioner tells it, and the lock it holds for the restart.

docs/SCOPE_UPGRADE_TO_PRO.md §2.3. The provisioner runs the upgrade; the box does two small things.

  1. KEEPS THE STAGE it is told (paid, restarting, done, held, failed), so the dashboard can say
     where the upgrade is. Only those words are stored. Anything else is refused.
  2. PREPARES FOR THE RESTART. Before the provisioner shuts the box down to resize it, the box takes
     the update lock and holds it, so no update can start installing in the middle. It answers
     "ready" only once it holds the lock, and "wait" while a coworker works or an update installs:
     the upgrade never cuts either off.

THE LOCK IS HELD BY A SEPARATE UNIT, not by the web process. A web worker can be restarted at any
moment, and a lock held by it would vanish with it. `aios-upgrade-hold` is a transient unit running
`flock <deploy lock> sleep 1800`: it holds the lock until the box goes down, and if the shutdown
never comes (the upgrade failed before it), it lets go by itself after 30 minutes.

THE SAME TWO-LOCK RULE AS EVERYWHERE ELSE (core/coworkers/locks.py): take ours, then check theirs. If
a coworker started between the check and the lock, let go and say "wait".

WHAT THE DEPLOY TOKEN CAN NOW DO HERE (core/dispatch.py documents each widening): write a stage from
a fixed list, and delay updates by up to 30 minutes, and only while an upgrade is under way. It can
read nothing and send nothing.
"""
from __future__ import annotations

import subprocess
import time
from datetime import datetime, timedelta, timezone

from core.coworkers import locks

STAGES = ("paid", "restarting", "done", "held", "failed")
_MACHINE, _KEY = "core", "upgrade_status"
HOLD_UNIT = "aios-upgrade-hold"
HOLD_SECONDS = 1800
UNDER_WAY_FOR = timedelta(hours=3)     # a "paid" older than this is not an upgrade under way


def status() -> dict:
    """{"stage", "detail", "at"} as the provisioner last said, or {} if it never has."""
    try:
        from core import box_settings
        got = box_settings.get(_MACHINE, _KEY, default={})
        return got if isinstance(got, dict) else {}
    except Exception:                                    # noqa: BLE001 — unknown is "nothing said"
        return {}


def set_status(stage: str, detail: str = "", *, now: datetime | None = None) -> dict:
    if stage not in STAGES:
        raise ValueError(f"unknown upgrade stage {stage!r}; it is one of {', '.join(STAGES)}")
    row = {"stage": stage, "detail": str(detail or "")[:200],
           "at": (now or datetime.now(timezone.utc)).isoformat()}
    from core import box_settings
    box_settings.put(_MACHINE, _KEY, row, set_by="provisioner")
    return row


def _under_way(now: datetime) -> bool:
    st = status()
    if st.get("stage") not in ("paid", "restarting"):
        return False
    try:
        at = datetime.fromisoformat(str(st.get("at")))
    except ValueError:
        return False
    return now - at <= UNDER_WAY_FOR


def _holding(run) -> bool:
    r = run(["systemctl", "is-active", "--quiet", HOLD_UNIT], capture_output=True)
    return r.returncode == 0


def _start_holder(run) -> None:
    run(["systemd-run", f"--unit={HOLD_UNIT}", "--collect", "--quiet",
         "-p", f"RuntimeMaxSec={HOLD_SECONDS + 60}",
         "flock", locks.DEPLOY_LOCK, "sleep", str(HOLD_SECONDS)],
        capture_output=True, stdin=subprocess.DEVNULL)


def _stop_holder(run) -> None:
    run(["systemctl", "stop", HOLD_UNIT], capture_output=True)


def prepare_restart(*, run=subprocess.run, now: datetime | None = None,
                    settle: float = 5.0, sleep=time.sleep) -> dict:
    """Hold the update lock until the box goes down.

    {"ready": True} once the lock is held, {"ready": False, "wait": why} while something must finish
    first, or {"ready": False, "refused": why} when no upgrade is under way.
    """
    now = now or datetime.now(timezone.utc)
    if not _under_way(now):
        return {"ready": False, "refused": "no upgrade is under way on this box"}
    if _holding(run):
        return {"ready": True}                           # asked twice: already holding
    if locks._busy(locks.SHIFT_LOCK):
        return {"ready": False, "wait": "a coworker is working"}
    if locks.update_running():
        return {"ready": False, "wait": "an update is installing"}
    _start_holder(run)
    waited = 0.0
    while not locks.update_running() and waited < settle:
        sleep(0.25); waited += 0.25
    if not locks.update_running():
        _stop_holder(run)
        return {"ready": False, "wait": "the update lock could not be taken yet"}
    if locks._busy(locks.SHIFT_LOCK):                    # a coworker started in between: let go
        _stop_holder(run)
        return {"ready": False, "wait": "a coworker is working"}
    set_status("restarting", now=now)
    return {"ready": True}
