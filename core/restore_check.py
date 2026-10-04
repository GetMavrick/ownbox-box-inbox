"""The box proves its own backup, every week (plan #1857 launch bar 9, last step).

Nobody can shell into a sold box (by design: the provisioner holds no key), so the restore drill runs ON the box:
aios-restore-drill.timer, once a week, runs `scripts/restore_drill.sh --last-night --record`. The drill restores last
night's copy beside the live database, compares them and records its verdict here, as the heartbeat `backup_restore`:
"green", or "red: <the drill's one-line reasons>". Never a row's contents: the drill prints none.

Everything that says it reads `last()`: core.health and the Dashboard in words, the check-in as `restore_drill` so
HQ sees the fleet. FOUR STATES, and never-run is its own, never red:
  green   the last check passed;
  red     the last check failed, with its reasons;
  stale   it passed once but has not run for over two weeks (the weekly check itself stopped);
  never   it has not run on this box yet.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

COMPONENT = "backup_restore"
STALE_AFTER = timedelta(days=15)          # two weekly runs missed, plus a day
REASONS_MAX = 300
SUPPORT = "support@ownbox.io"


def record(green: bool, reasons: list[str] | None = None) -> None:
    """The drill's verdict, as the heartbeat `backup_restore`. Reasons are the drill's own, already free of row data."""
    from core import state
    said = "; ".join(" ".join(str(r).split()) for r in (reasons or []) if str(r).strip())
    state.heartbeat(COMPONENT, "green" if green else ("red: " + (said or "the check failed"))[:REASONS_MAX])


def last(now: datetime | None = None) -> dict:
    """{"state": green|red|stale|never, "at": iso or None, "reasons": [..]}. Never raises: unreadable is never."""
    try:
        from core import state
        hb = next((h for h in state.get_heartbeats() if h.get("component") == COMPONENT), None)
    except Exception:                                     # noqa: BLE001 — a reading, never an error
        hb = None
    if not hb or not hb.get("ts"):
        return {"state": "never", "at": None, "reasons": []}
    status, at = str(hb.get("status") or ""), str(hb["ts"])
    if status.startswith("red"):
        reasons = [r.strip() for r in status.partition(":")[2].split(";") if r.strip()]
        return {"state": "red", "at": at, "reasons": reasons or ["the check failed"]}
    if status != "green":
        return {"state": "never", "at": None, "reasons": []}
    try:
        then = datetime.fromisoformat(at.replace("Z", "+00:00"))
        then = then if then.tzinfo else then.replace(tzinfo=timezone.utc)
        if (now or datetime.now(timezone.utc)) - then > STALE_AFTER:
            return {"state": "stale", "at": at, "reasons": []}
    except ValueError:
        return {"state": "never", "at": None, "reasons": []}
    return {"state": "green", "at": at, "reasons": []}


def day_words(at: str | None) -> str:
    """"Sun 4 Oct", in the box's own time; "" when there is no time."""
    if not at:
        return ""
    try:
        from core import report
        then = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
        then = then if then.tzinfo else then.replace(tzinfo=timezone.utc)
        return then.astimezone(report.tz()).strftime("%a %-d %b")
    except Exception:                                     # noqa: BLE001
        return ""


def sentence(r: dict) -> str:
    """The reading in words, for a person: core.health's answer and the Dashboard. "" for never. The drill's reasons
    stay out of it (they name tables and row ids, which mean nothing to a buyer); they travel to HQ in the check-in.
    A buyer cannot fix a backup that did not come back, so the fix it names is ours, and where to reach us."""
    day = day_words(r.get("at"))
    st = r.get("state")
    if st == "green":
        return f"Your backup was restored and checked on {day}: everything came back."
    if st == "red":
        return (f"Your backup was restored and checked on {day}, and part of it did not come back. Ownbox fixes "
                f"this for you: write to {SUPPORT}.")
    if st == "stale":
        return (f"Your backup was last restored and checked on {day}, over two weeks ago: the weekly check has "
                f"stopped. Ownbox fixes this for you: write to {SUPPORT}.")
    return ""
