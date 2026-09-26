"""The clockwork: the tick that starts shifts, the runner that works one, and the report after.

docs/SCOPE_SHIFTS.md §3 and §4. Three entry points, each a process of its own on a box:

  tick()     every minute (aios-shifts.timer → scripts/shift_tick.py). Closes runs that died,
             marks what was MISSED, queues what is due, starts the head of the queue in its own
             unit, tells box updates when the next window opens, retries unsent reports, and
             writes the heartbeat /health shows. It never waits for a run, and it never raises.
  run(slot)  in that unit (scripts/run_coworker.py). Holds the shift lock for the whole run:
             preflight, before-steps, the AI on a run seat in the sandbox, after-steps, receipt.
  report()   one email and one app notification per run, whatever it ended as. A run never ends
             in silence (§0).

THE ORDER OF A RUN IS THE SAFETY ARGUMENT (§4.2). Steps are machine tools the box calls itself,
before and after the AI, and they are the only thing that may act. The AI in between holds a seat
that can read and draft, nothing else, and it runs in OSDev1's sandbox (#1613) with no keys and no
shell. A step failing stops the run there and names the machine and tool; an after-step never
runs on a shift whose AI part failed.

EVERY WAY A RUN ENDS HAS A SENTENCE. The receipt's reason is what the owner reads first, so each is
written for them: what happened, and where that is not obvious, what to do about it.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import time
from datetime import datetime, timedelta, timezone

from core import state
from core.coworkers import contract, locks, runs, schedule
from core.exceptions import VendorError
from core.logging import get_logger

log = get_logger(__name__)

ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPONENT = "shift_tick"
COMPONENT_OK = "shift_tick_ok"             # the last tick that finished: what "since" is planned from
MCP_URL = "http://127.0.0.1:8000/mcp"      # the box's MCP, as the sandboxed CLI reaches it
PYTHON = "/opt/aios/.venv/bin/python"
RETRY_AFTER_S = 60                         # the one retry, a minute after a failure before work
RETRY_WITHIN_S = 120                       # ...and only if the first attempt ended this soon
REPORT_BUDGET_S = 20                       # the tick's time for reports; the rest wait a minute
STOP_TIMEOUT_S = 20                        # stopping a dead run's AI never outlasts the tick
STEPS_ALLOWANCE_MIN = 15                   # a run unit's budget for its steps, beyond the AI's
START_GRACE_S = 120                        # a unit that is still coming up is not a crash
RUN_NOW_WINDOW = timedelta(minutes=10)     # how long a Run now request waits for its turn
REPORT_GIVE_UP = timedelta(days=1)         # an email that cannot go out for a day stops retrying
PUSH_BODY = "A coworker has finished a shift. Open Shifts to see what it did."


# ── the box ──────────────────────────────────────────────────────────────────────────────────────

def coworkers_dir() -> pathlib.Path:
    """my/coworkers/, which updates never touch. An env override exists for tests only."""
    return pathlib.Path(os.environ.get("AIOS_MY_COWORKERS") or (ROOT / "my" / "coworkers"))


def box_tz():
    """The owner's timezone (notify.buyer_timezone), where their 07:30 is."""
    from zoneinfo import ZoneInfo
    try:
        from core import notify
        return ZoneInfo(notify.buyer_timezone())
    except Exception:                               # noqa: BLE001 — an unknown zone is UTC
        return timezone.utc


NOT_IN_PLAN = "Coworkers come with Pro."


def allowed() -> tuple:
    """(True, "") when this box's plan includes coworkers; else (False, one plain sentence).

    THE ONE GATE IS core.tiers.allows("coworkers") (docs/SCOPE_TIERS.md §0): code asks about a
    feature, never about a tier. This only adds the sentence the owner reads. A feature switch,
    not a lock (§2.4).
    """
    from core import tiers
    return (True, "") if tiers.allows("coworkers") else (False, NOT_IN_PLAN)


def discover() -> tuple:
    """(coworkers, [(slug, reasons)]): every valid coworker folder, and why the others are not."""
    good, bad = [], []
    try:
        folders = sorted(p for p in coworkers_dir().iterdir()
                         if p.is_dir() and not p.name.startswith((".", "_")))
    except OSError:
        return good, bad
    for f in folders:
        cw, why = contract.load(f)
        (good.append(cw) if cw else bad.append((f.name, why)))
    return good, bad


def _iso(d: datetime) -> str:
    return d.isoformat(timespec="seconds")


def _parse(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _hm(d: datetime) -> str:
    return d.strftime("%H:%M")


# ── receipts ─────────────────────────────────────────────────────────────────────────────────────

def _receipt(row: dict, outcome: str, reason: str, *, source: str = "my", started=None,
             attempts: int = 1, usage: dict | None = None, failed=None, steps=(), tools_used=(),
             outputs=(), now=None) -> dict:
    """A v1 receipt for this row. If it would not check, a plainer FAILED one that says so."""
    end = now or datetime.now(timezone.utc)
    u = usage or {}
    # A run that ended before it formally started (it crashed in its first second, or the box could
    # not launch it) still began: its row's creation is the honest start. Only MISSED has none.
    st = None if outcome == "MISSED" else (started or row.get("started_at") or row["created_at"])
    kw = dict(run_id=row["run_id"], slot=row["slot"], outcome=outcome, reason=reason,
              window={"start": row["window_start"], "latest": row["window_latest"]},
              started_at=st, ended_at=_iso(end), attempts=attempts,
              minutes=round(float(u.get("minutes") or 0), 3), turns=int(u.get("turns") or 0),
              cost_usd=u.get("cost_usd"), failed=failed, steps=list(steps),
              tools_used=list(tools_used), outputs=list(outputs))
    cw = _Who(row["coworker"], source)
    try:
        return contract.receipt(cw=cw, **kw)
    except ValueError as e:
        log.error("coworker.receipt_invalid", slot=row["slot"], error=str(e)[:300])
        why = f"{reason} (and its receipt could not be written in full: {str(e)[:120]})"
    # A plainer FAILED: it needs a start and an attempt, which a MISSED one never had.
    plain = {**kw, "outcome": "FAILED", "failed": None, "reason": why, "steps": [],
             "tools_used": [], "outputs": [], "attempts": max(attempts, 1),
             "started_at": kw["started_at"] or row.get("started_at") or row["created_at"]}
    try:
        return contract.receipt(cw=cw, **plain)
    except ValueError as e:
        # THE ROW STILL CLOSES. A receipt that cannot be v1 (a row naming no valid coworker) is
        # written as the bare minimum, because a row the tick cannot close would be retried every
        # minute and stop every shift behind it.
        log.error("coworker.receipt_bare", slot=row["slot"], error=str(e)[:300])
        # It still carries every field a report reads, so the owner is told about it too.
        return {"receipt": contract.RECEIPT_VERSION, "run_id": row["run_id"],
                "coworker": str(row["coworker"])[:60], "from": source, "slot": row["slot"],
                "outcome": "FAILED", "reason": why, "window": kw["window"],
                "started_at": plain["started_at"], "ended_at": kw["ended_at"], "attempts": 1,
                "usage": {"minutes": 0, "turns": 0, "cost_usd": None}, "failed": None,
                "steps": [], "tools": [], "outputs": []}


class _Who:
    """The two fields of a Coworker a receipt needs, for a row whose file may be gone."""
    def __init__(self, slug, source):
        self.slug, self.source = slug, source


# ── the tick ─────────────────────────────────────────────────────────────────────────────────────

def _unit_active(unit: str) -> bool:
    """Seam: is this run's unit still alive?"""
    r = subprocess.run(["systemctl", "is-active", "--quiet", unit], stdin=subprocess.DEVNULL,
                       capture_output=True, timeout=15)
    return r.returncode == 0


def _launch(slot: str, unit: str, minutes: int) -> None:
    """Seam: start the runner for `slot` in its own unit, and return at once."""
    cmd = ["systemd-run", "--quiet", "--collect", "--no-block", f"--unit={unit}",
           "-p", "WorkingDirectory=/opt/aios", "-p", "EnvironmentFile=/opt/aios/.env",
           "-p", f"RuntimeMaxSec={_runner_seconds(minutes)}",
           "--", PYTHON, str(ROOT / "scripts" / "run_coworker.py"), slot]
    subprocess.run(cmd, check=True, stdin=subprocess.DEVNULL, capture_output=True, timeout=30)


def _runner_seconds(minutes: int) -> int:
    """The runner unit's limit: the AI's minutes, its steps, and room for the one retry."""
    return ((minutes + STEPS_ALLOWANCE_MIN) * 60 + RETRY_AFTER_S + RETRY_WITHIN_S
            + START_GRACE_S)


def _runner_unit(row: dict) -> str:
    tail = row["run_id"].replace("_", "-")
    return f"aios-shift-{tail}"


def _last_tick():
    """When the last tick FINISHED. Kept apart from the pulse, which an error tick overwrites:
    reading the pulse, one failed minute would make the next tick think it had never run, and
    every window that closed in between would go unreported."""
    try:
        for h in state.get_heartbeats():
            if h.get("component") == COMPONENT_OK and h.get("status") == "ok":
                return _parse(h["ts"])
    except Exception:                               # noqa: BLE001 — no heartbeat is a first tick
        pass
    return None


def tick(now: datetime | None = None, *, launch=None, unit_active=None) -> dict:
    """One minute of clockwork. Never raises; returns what it did, for the log and the tests."""
    launch = launch or _launch
    unit_active = unit_active or _unit_active
    tz = box_tz()
    now = (now or datetime.now(tz)).astimezone(tz)
    did = {"closed": [], "missed": [], "queued": [], "started": None, "waiting": None,
           "reported": 0}
    try:
        ok, why = allowed()
        if not ok:
            locks.write_next(None)
            state.heartbeat(COMPONENT, "off")
            state.heartbeat(COMPONENT_OK, "off")      # re-enabled later: no MISSED for the gap
            did["off"] = why
            return did
        since = _last_tick()
        cws, bad = discover()
        for slug, reasons in bad:
            log.warning("coworker.file_refused", coworker=slug, reasons=reasons[:5])
        by_slug = {c.slug: c for c in cws}

        # 1. A run whose unit is gone without reporting: closed FAILED, never left "running".
        for row in runs.by_status(*runs.ACTIVE):
            # A unit started this minute may not be up yet. Measured from the CLAIM, not from when
            # the slot was queued: a shift that waited ten minutes for its turn is new at its start.
            claimed = row.get("claimed_at")
            if row["status"] == "starting" and claimed and \
                    (now - _parse(claimed)).total_seconds() < START_GRACE_S:
                continue
            if row.get("unit") and unit_active(row["unit"]):
                continue
            try:
                rc = _receipt(row, "FAILED", "The run stopped without reporting: the box "
                                             "restarted, or the run crashed. Nothing after that "
                                             "point ran.", source=_source(by_slug, row))
                if runs.finish(row["slot"], rc):
                    did["closed"].append(row["slot"])
                    _clean_up(row)
            except Exception as e:                          # noqa: BLE001 — one row, not the tick
                log.error("coworker.close_failed", slot=row["slot"], error=str(e)[:200])

        # 2. What the clock says.
        floor = now - schedule.LOOKBACK - timedelta(days=1)
        plan = schedule.plan(cws, now=now, since=since, taken=runs.taken_keys(_iso(floor)))

        # 3. MISSED: a queued slot whose window closed, then slots the tick never saw open.
        for row in runs.by_status("queued"):
            if _parse(row["window_latest"]) >= now:
                continue
            try:
                why = row.get("note") or "It could not start before its window closed."
                rc = _receipt(row, "MISSED", f"It could not start between "
                                             f"{_hm(_parse(row['window_start']))} and "
                                             f"{_hm(_parse(row['window_latest']))}: {why}",
                              source=_source(by_slug, row))
                if (runs.miss if rc["outcome"] == "MISSED" else runs.drop)(row["slot"], rc):
                    did["missed"].append(row["slot"])
            except Exception as e:                          # noqa: BLE001 — one row, not the tick
                log.error("coworker.miss_failed", slot=row["slot"], error=str(e)[:200])
        for s in plan.missed:
            if runs.queue(s.key, coworker=s.coworker, start=_iso(s.start), latest=_iso(s.latest),
                          now=_iso(now)):
                row = runs.get(s.key)
                gap = (f"the box was off, or not running shifts, from "
                       f"{_hm(since.astimezone(tz))} to {_hm(now)}" if since else
                       "the box was not running shifts then")
                rc = _receipt(row, "MISSED", f"It could not start between {_hm(s.start)} and "
                                             f"{_hm(s.latest)}: {gap}.",
                              source=by_slug[s.coworker].source)
                if runs.miss(s.key, rc):
                    did["missed"].append(s.key)

        # 4. Queue what is due. The key is the slot, so this is at most once however often it runs.
        for s in plan.due:
            if runs.queue(s.key, coworker=s.coworker, start=_iso(s.start), latest=_iso(s.latest),
                          now=_iso(now)):
                did["queued"].append(s.key)

        # 4b. A SCHEDULED SHIFT RUNS ONLY WHILE IT IS STILL SCHEDULED. A queued row whose slot is no
        # longer due (the owner switched the coworker off, moved its time, or its file can no longer
        # be used) is closed MISSED now, never run from the queue on the old schedule.
        due_keys = {s.key for s in plan.due}
        for row in runs.by_status("queued"):
            if _is_run_now(row["slot"]) or row["slot"] in due_keys \
                    or _parse(row["window_latest"]) < now:
                continue
            try:
                rc = _receipt(row, "MISSED", "It did not start: the coworker was switched off, "
                                             "its time was changed, or its file can no longer be "
                                             "used, before its shift began.",
                              source=_source(by_slug, row))
                if (runs.miss if rc["outcome"] == "MISSED" else runs.drop)(row["slot"], rc):
                    did["missed"].append(row["slot"])
            except Exception as e:                          # noqa: BLE001 — one row, not the tick
                log.error("coworker.unscheduled_close_failed", slot=row["slot"],
                          error=str(e)[:200])

        # 5. Start the head of the queue, if nothing is running and no update is.
        waiting = [r for r in runs.by_status("queued") if _parse(r["window_latest"]) >= now]
        waiting.sort(key=lambda r: (_parse(r["window_latest"]), _parse(r["window_start"]),
                                    r["slot"]))
        active = runs.by_status(*runs.ACTIVE)
        if waiting:
            if active:
                cause = f"it waited for {active[0]['coworker']}'s shift to finish"
            elif locks.update_running():
                cause = "it waited for a box update to finish"
            elif locks._busy(locks.SHIFT_LOCK):
                cause = "it waited for another shift to finish"
            else:
                cause = None
            if cause:
                for r in waiting:
                    runs.note(r["slot"], cause.capitalize() + ".")
                did["waiting"] = cause
            else:
                # THE FIRST ONE THAT CAN RUN. A slot whose coworker file is gone or broken waits
                # with that reason (the owner may fix it before its window closes), and never
                # holds up the shifts behind it.
                head, cw = None, None
                for r in waiting:
                    cw = by_slug.get(r["coworker"])
                    if cw is not None:
                        head = r
                        break
                    runs.note(r["slot"], "Its coworker file is missing or cannot be used.")
                unit = _runner_unit(head) if head else None
                if head and runs.claim(head["slot"], unit, now=_iso(now)):
                    try:
                        launch(head["slot"], unit, cw.minutes)
                        did["started"] = head["slot"]
                    except Exception as e:                  # noqa: BLE001 — said, not swallowed
                        rc = _receipt(runs.get(head["slot"]), "FAILED",
                                      f"The box could not start the run ({type(e).__name__}).",
                                      source=cw.source, started=_iso(now))
                        runs.finish(head["slot"], rc)

        # 6. When the next window opens, so a box update waits for it (OSDev1, #1613). NEVER IN THE
        # PAST while a shift is due or waiting: box_update.sh only waits for a time after now, so a
        # past time would let an update in and the waiting shift would go MISSED.
        nxt = plan.next_start
        epoch = int(nxt.timestamp()) if nxt else None
        if runs.by_status("queued") or plan.due:
            epoch = max(epoch or 0, int(now.timestamp()) + 60)
        locks.write_next(epoch)

        # THE TICK HAS FINISHED ITS CLOCKWORK: the pulse and "since" are written BEFORE the reports,
        # so a slow mail server can never make /health stale or the next tick forget what it missed.
        state.heartbeat(COMPONENT, "ok", ts=_iso(now))
        state.heartbeat(COMPONENT_OK, "ok", ts=_iso(now))

        # 7. Reports that have not gone out yet, within REPORT_BUDGET_S. The tick has a 50 s limit
        # and one email can take 20 s or more, so what does not fit waits for the next minute.
        t0 = time.monotonic()
        for row in runs.unreported():
            if time.monotonic() - t0 > REPORT_BUDGET_S:
                did["reports_deferred"] = True
                break
            did["reported"] += report(row)                  # never raises
    except Exception as e:                                  # noqa: BLE001 — never raises
        log.error("coworker.tick_failed", error=f"{type(e).__name__}: {e}"[:300])
        try:
            state.heartbeat(COMPONENT, f"error: {type(e).__name__}")
        except Exception:                                   # noqa: BLE001
            pass
        did["error"] = f"{type(e).__name__}: {e}"[:300]
    return did


def _is_run_now(slot: str) -> bool:
    return str(slot).endswith("+now")


def _clean_up(row: dict) -> None:
    """After a runner died: stop its AI, revoke its seat, remove its private files.

    A runner killed by its unit's time limit never reaches its own `finally`, so its AI can still
    be working on a live seat while the next shift starts. Each part is attempted on its own.
    """
    import shutil

    from core.connector import seats
    from core.coworkers import sandbox
    try:
        _stop_agent(sandbox.stop_argv(sandbox.unit_name(row["coworker"], row["run_id"])))
    except Exception as e:                                  # noqa: BLE001
        log.warning("coworker.cleanup_stop_failed", slot=row["slot"], error=str(e)[:200])
    try:
        label = f"coworker {row['coworker']} {row['run_id']}"
        for seat in seats.all_seats(include_revoked=False, include_runs=True):
            if seat.get("label") == label:
                seats.revoke(seat["id"])
    except Exception as e:                                  # noqa: BLE001
        log.warning("coworker.cleanup_seat_failed", slot=row["slot"], error=str(e)[:200])
    shutil.rmtree(os.path.join(sandbox.RUNS, str(row["run_id"])), ignore_errors=True)


def _stop_agent(cmd: list) -> None:
    """Seam: stop a run's AI unit."""
    subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=STOP_TIMEOUT_S)


def _source(by_slug: dict, row: dict) -> str:
    cw = by_slug.get(row["coworker"])
    return cw.source if cw else "my"


def start_now(slug: str, *, dry_run: bool = True, now: datetime | None = None) -> str:
    """Run now: queue a run of this coworker for the next tick. Returns its slot key.

    A dry run by default, which every step MUST honour (contract: dry_run does nothing outward).
    It queues like any shift, so it waits its turn and never runs beside another.
    """
    ok, why = allowed()
    if not ok:
        raise ValueError(why)
    ok_name = isinstance(slug, str) and bool(contract._SLUG.match(slug))
    cw, why = contract.load(coworkers_dir() / slug) if ok_name else (None, ["no such name"])
    if cw is None:
        raise ValueError(f"There is no coworker called {slug} that can run: "
                         + "; ".join(why[:2]))
    tz = box_tz()
    now = (now or datetime.now(tz)).astimezone(tz)
    slot = f"{contract.slot_key(cw.slug, now.date(), _hm(now))}+now"
    if not runs.queue(slot, coworker=cw.slug, start=_iso(now), latest=_iso(now + RUN_NOW_WINDOW),
                      dry_run=dry_run, now=_iso(now)):
        raise ValueError("that coworker already has a Run now queued for this minute")
    return slot


def health(now: datetime | None = None) -> dict | None:
    """The tick's pulse for /health: {"tick": "ok"|"stale"|"off"|"error", "age_s": n}, or None
    on a box where no tick has ever run.

    PUBLIC, SO IT SAYS ONLY THIS. /health is unauthenticated: no coworker, schedule or run is
    named here. "stale" means no tick for three minutes, which is a timer that stopped.
    """
    try:
        beat = next((h for h in state.get_heartbeats() if h.get("component") == COMPONENT), None)
        if beat is None:
            return None
        age = int(((now or datetime.now(timezone.utc)) - _parse(beat["ts"])).total_seconds())
        status = str(beat.get("status") or "")
        word = ("error" if status.startswith("error") else "off" if status == "off"
                else "stale" if age > 180 else "ok")
        return {"tick": word, "age_s": max(age, 0)}
    except Exception:                                       # noqa: BLE001 — /health never breaks
        return None


# ── the run ──────────────────────────────────────────────────────────────────────────────────────

def _load_machines() -> None:
    """Import what registers tools, as the worker does (core/worker.py), each import isolated.

    A machine that fails to import here fails only its own steps, by name, in the preflight.
    """
    import importlib

    from core.config import get_config
    from core.connector import tools as _tools
    cfg = get_config()
    for path in list(cfg.get("modules", [])) + list(cfg.get("web_modules", [])):
        try:
            importlib.import_module(path)
        except Exception as e:                              # noqa: BLE001
            _tools.note_absent(path, f"{type(e).__name__}: {e}")
    try:
        from core import packs
        for m in packs.discover():
            if m.get("module"):
                try:
                    importlib.import_module(m["module"])
                except Exception as e:                      # noqa: BLE001
                    _tools.note_absent(m["module"], f"{type(e).__name__}: {e}")
    except Exception as e:                                  # noqa: BLE001
        log.warning("coworker.packs_unreadable", error=str(e)[:200])
    from core import custom_machines
    custom_machines.load("runner")


def _prompt(cw, row: dict, tz) -> str:
    start, latest = _parse(row["window_start"]), _parse(row["window_latest"])
    lines = [f"Today is {start.strftime('%A %d %B %Y')}. This is your shift that starts at "
             f"{_hm(start)} ({getattr(tz, 'key', 'UTC')}).",
             "Work through the box's tools, and keep any notes in your working folder, where the "
             "steps after you will read them. You can read and draft; you cannot send, publish "
             "or pay for anything, and nothing you draft goes out until a person approves it.",
             "When you have finished, reply with one short paragraph saying what you did. That "
             "paragraph is the report the owner reads."]
    if row.get("dry_run"):
        lines.insert(1, "This is a trial run the owner started by hand. Do the work as you would "
                        "on a real shift.")
    return "\n\n".join(lines)


def _step(phase: str, name: str, reg: dict, ctx: dict) -> tuple:
    """Call one step. (receipt step entry, outputs, failed-or-None)."""
    spec = reg[contract.tool_name(name)]
    machine = spec.get("machine") or name.split(".", 1)[0]
    try:
        r = spec["fn"](**ctx)
    except Exception as e:                                  # noqa: BLE001 — named, not swallowed
        why = f"{name} raised {type(e).__name__}"
        return ({"phase": phase, "tool": name, "outcome": "failed", "reason": why}, [],
                {"machine": machine, "tool": name})
    problems = contract.check_step_result(r)
    if problems:
        why = f"{name} returned something a step may not: {problems[0]}"
        return ({"phase": phase, "tool": name, "outcome": "failed", "reason": why}, [],
                {"machine": machine, "tool": name})
    return ({"phase": phase, "tool": name, "outcome": "ok", "reason": r["summary"][:300]},
            list(r["outputs"]), None)


def _workspace(slug: str) -> str:
    from core.coworkers import sandbox
    ws = sandbox.workspace(slug, root=str(ROOT))
    os.makedirs(ws, exist_ok=True)
    try:
        import shutil
        shutil.chown(ws, user=sandbox.USER, group=sandbox.USER)
    except (LookupError, PermissionError, OSError):
        pass                                                # a laptop without the user: tests
    return ws


def run(slot: str, *, run_agent=None, sleep=time.sleep) -> dict | None:
    """Work one shift that the tick claimed. Returns its receipt, or None if it was not ours."""
    row = runs.get(slot)
    if not row or row["status"] != "starting":
        log.warning("coworker.run_not_claimed", slot=slot, status=row and row["status"])
        return None
    with locks.shift() as held:
        if not held:
            # An update started between the tick's check and this unit starting. The window still
            # counts: the tick tries again next minute, and says MISSED if the window closes.
            runs.requeue(slot, "It waited for a box update to finish.")
            return None
        if not runs.start(slot):
            return None
        row = runs.get(slot)
        try:
            rc = _work(row, run_agent=run_agent, sleep=sleep)
        except Exception as e:                              # noqa: BLE001 — never silent
            log.error("coworker.run_crashed", slot=slot, error=f"{type(e).__name__}: {e}"[:300])
            rc = _receipt(row, "FAILED", f"The runner itself failed ({type(e).__name__}). "
                                         f"Nothing after that point ran.")
        runs.finish(slot, rc)
    report(runs.get(slot))
    return rc


def _work(row: dict, *, run_agent=None, sleep=time.sleep) -> dict:
    from core import brain
    from core.connector import seats, tools
    from core.coworkers import sandbox
    from core.exceptions import BudgetExceeded, RetryableError

    run_agent = run_agent or brain.run_agent
    started = row["started_at"]
    t0 = time.monotonic()
    tz = box_tz()

    def fail(reason, **kw):
        return _receipt(row, "FAILED", reason, source=kw.pop("source", "my"), started=started,
                        usage={"minutes": (time.monotonic() - t0) / 60, **kw.pop("usage", {})},
                        **kw)

    folder = coworkers_dir() / row["coworker"]
    cw, why = contract.load(folder)
    if cw is None:
        return fail("Its coworker file cannot be used: " + "; ".join(why[:3]))
    from core.coworkers import hire
    if not hire.acknowledged(cw):
        # §4.3: reading the box's data and reaching the web together waits for the owner's OK,
        # given for this exact grant. Said every shift until given, never run without it.
        return fail("It can read your box's data and also reach the web, and that needs your OK "
                    "before it runs: on the Shifts screen, or on the server with "
                    f"python3 scripts/coworker_ok.py {cw.slug}", source=cw.source)
    ready, why = brain.can_think()
    if not ready:
        return fail(f"The AI account is not ready: {why}.", source=cw.source)

    _load_machines()
    reg = tools.registry()
    problems = contract.unresolved(cw, reg)
    if problems:
        first = next(s for s in cw.before + cw.after
                     if contract.tool_name(s) not in reg
                     or contract.step_signature(reg[contract.tool_name(s)]))
        return fail("It cannot start: " + "; ".join(problems[:3]), source=cw.source,
                    failed={"machine": first.split(".", 1)[0], "tool": first})

    ws = _workspace(cw.slug)
    ctx = contract.step_context(run_id=row["run_id"], cw=cw, slot=row["slot"], workspace=ws,
                                dry_run=bool(row["dry_run"]))
    steps, outputs = [], []
    for name in cw.before:
        entry, outs, failed = _step("before", name, reg, ctx)
        steps.append(entry)
        outputs += outs
        if failed:
            return fail(f"{entry['reason']}. The AI did not run.", source=cw.source,
                        failed=failed, steps=steps, outputs=outputs)

    # THE JOB IS READ BEFORE THE SEAT EXISTS: a seat minted and then abandoned by a failed read
    # would stay live with the run's grant. From the mint on, only the `finally` below ends it.
    try:
        job = (folder / cw.job).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return fail(f"Its job file cannot be read ({type(e).__name__}). The AI did not run.",
                    source=cw.source, steps=steps, outputs=outputs)
    seat_id, credential = seats.mint(f"coworker {cw.slug} {row['run_id']}", "act",
                                     capabilities=[c for c in cw.may if c not in contract.WEB])
    web = [w.split(":", 1)[1] for w in cw.may if w in contract.WEB]
    kw = dict(run_id=row["run_id"], coworker=cw.slug, workspace=ws, system=job,
              mcp={"url": MCP_URL, "credential": credential}, web=web, max_turns=cw.turns,
              max_minutes=cw.minutes)
    attempts = 1
    result = None
    try:
        a0 = time.monotonic()
        try:
            result = run_agent(_prompt(cw, row, tz), **kw)
        except RetryableError as e:
            # ONE RETRY, AND ONLY BEFORE ANY WORK (§3): the seat made no call, the attempt ended
            # within RETRY_WITHIN_S (web reads and notes leave no seat call, so a long attempt
            # may have worked), and the window is still open. A failure after work began is
            # FAILED, because retrying it could do the work twice.
            spent_s = time.monotonic() - a0
            if runs.seat_calls(seat_id) or spent_s > RETRY_WITHIN_S:
                raise
            # THE RETRY MUST START INSIDE THE WINDOW, not merely be asked for inside it: it
            # starts RETRY_AFTER_S from now, and a shift is never run after its latest.
            if datetime.now(tz) + timedelta(seconds=RETRY_AFTER_S) > _parse(row["window_latest"]):
                raise RetryableError(f"{str(e)[:180]} (not retried: its window closes before "
                                     f"a retry could start)") from e
            log.warning("coworker.retrying", slot=row["slot"], error=str(e)[:200])
            sleep(RETRY_AFTER_S)
            attempts = 2
            # The second attempt gets what is left of the run's minutes, never a fresh set.
            left = cw.minutes - int((time.monotonic() - t0) // 60)
            result = run_agent(_prompt(cw, row, tz), **{**kw, "max_minutes": max(1, left)})
    except (brain.AgentLimit, BudgetExceeded, RetryableError, RuntimeError, ValueError,
            sandbox.SandboxError) as e:
        reason = {brain.AgentLimit: "It was stopped",
                  BudgetExceeded: "The AI account's limit was reached",
                  RetryableError: "The AI was busy or could not be reached",
                  sandbox.SandboxError: "The sandbox could not start it safely"}
        head = next((v for k, v in reason.items() if isinstance(e, k)), "The AI run failed")
        rc = fail(f"{head}: {str(e)[:240]}", source=cw.source, attempts=attempts, steps=steps,
                  outputs=outputs, tools_used=_tools_used(seat_id))
        return rc
    finally:
        seats.revoke(seat_id)

    usage = {"minutes": (time.monotonic() - t0) / 60, "turns": result["turns"],
             "cost_usd": result["cost_usd"] if result["backend"] == "api" else None}
    for name in cw.after:
        entry, outs, failed = _step("after", name, reg, ctx)
        steps.append(entry)
        outputs += outs
        if failed:
            return fail(entry["reason"] + ".", source=cw.source, attempts=attempts, usage=usage,
                        failed=failed, steps=steps, outputs=outputs,
                        tools_used=_tools_used(seat_id))
    said = " ".join((result.get("text") or "").split())[:400] or "It finished its shift."
    return _receipt(row, "DONE", said, source=cw.source, started=started, attempts=attempts,
                    usage=usage, steps=steps, tools_used=_tools_used(seat_id), outputs=outputs)


def _tools_used(seat_id: str) -> list:
    out = []
    for t in runs.seat_calls(seat_id):
        name = t["tool"][5:] if t["tool"].startswith("aios.") else t["tool"]
        if contract._STEP.match(name):
            out.append({**t, "tool": name})
    return out


# ── the report ───────────────────────────────────────────────────────────────────────────────────

_SAID = {"DONE": "shift done", "FAILED": "shift failed", "MISSED": "shift missed"}


def _title(slug: str) -> str:
    cw, _ = contract.load(coworkers_dir() / slug)
    return cw.title if cw else slug


def report_text(row: dict) -> tuple:
    """(subject, text body) for a finished run. Plain words, the owner's own wall-clock times."""
    rc = json.loads(row["receipt"])
    title = _title(row["coworker"])
    start = _parse(rc["window"]["start"])
    lines = [f"{title}: {_SAID[rc['outcome']]}.", "", rc["reason"], "",
             f"Shift: {start.strftime('%A %d %B')}, {_hm(start)}."]
    if row.get("dry_run"):
        lines.append("This was a trial run started by hand. Its steps did nothing outward.")
    u = rc["usage"]
    if rc["outcome"] != "MISSED":
        lines.append(f"It took {u['minutes']:.0f} min and {u['turns']} turns"
                     + (f", and cost ${u['cost_usd']:.2f}." if u["cost_usd"] else "."))
    if rc.get("failed"):
        f = rc["failed"]
        lines.append(f"What failed: {f['tool'] or f['machine']}.")
    for s in rc["steps"]:
        lines.append(f"Step {s['tool']} ({s['phase']}): {s['outcome']}. {s['reason']}".rstrip())
    if rc["outputs"]:
        lines.append("It made: " + ", ".join(o.get("title") or f"{o['type']} {o['id']}"
                                             for o in rc["outputs"][:10]) + ".")
    return f"{title}: {_SAID[rc['outcome']]}", "\n".join(lines)


def report(row: dict | None) -> int:
    """Send this run's report on each channel not yet done. Returns channels sent. Never raises.

    ONE EMAIL PER RUN, BY ITS RUN ID: box_mail's idem_key makes a retry after a timeout the same
    message, not a second one. The app notification has no such key, so it is marked sent the
    moment it has been attempted, and never retried: a missing notification is a smaller harm
    than the same one twice.
    """
    if not row or row.get("status") not in runs.FINAL:
        return 0
    sent = 0
    try:
        owner = state.owner_user()
    except Exception as e:                                  # noqa: BLE001
        log.error("coworker.report_no_owner", error=str(e)[:200])
        return 0
    if not row.get("emailed_at"):
        try:
            from core import box_mail, notify
            people = box_mail.to_box_people(user_id=owner["id"]) if box_mail.is_configured() else []
            to = next((p["email"] for p in people if p.get("email")), "")
            if not to:
                runs.mark_reported(row["slot"], "emailed", "n/a: no email set up")
            else:
                subject, text = report_text(row)
                box_mail.send(to, subject, text, notify._plain_html(text),
                              idem_key=f"coworker-run:{row['run_id']}",
                              sender_name=notify._sender_name(), note="coworker_run")
                runs.mark_reported(row["slot"], "emailed")
                sent += 1
        except VendorError as e:
            # REFUSED OR INDETERMINATE, THE SEND WAS CLAIMED: a retry under the same key would
            # return at once and be marked sent, though no email arrived. Settled as what it is.
            # Anything that failed before the claim (sign-in, settings) is retried next tick.
            if e.status in ("refused", "indeterminate"):
                log.error("coworker.report_email_" + str(e.status), slot=row["slot"],
                          error=str(e)[:200])
                runs.mark_reported(row["slot"], "emailed",
                                   "not sent: the mail server refused it" if e.status == "refused"
                                   else "unknown: the connection dropped while sending")
            else:
                _email_retry_later(row, e)
        except Exception as e:                              # noqa: BLE001 — retried next tick
            _email_retry_later(row, e)
    # CLAIMED BEFORE IT IS SENT. The runner reports as it finishes and the tick reports what is
    # unreported, so both can reach this at once; exactly one wins the claim and sends.
    if not row.get("pushed_at") and runs.claim_report(row["slot"], "pushed"):
        try:
            from core import push
            for sub in push.subscriptions_for(owner["id"]):
                push.send(sub, title="Shifts", body=PUSH_BODY, navigate="/shifts/")
            sent += 1
        except Exception as e:                              # noqa: BLE001
            log.warning("coworker.report_push_failed", slot=row["slot"],
                        error=f"{type(e).__name__}: {e}"[:200])
    return sent


def _email_retry_later(row: dict, e: Exception) -> None:
    log.warning("coworker.report_email_failed", slot=row["slot"],
                error=f"{type(e).__name__}: {e}"[:200])
    ended = row.get("ended_at")
    if ended and datetime.now(timezone.utc) - _parse(ended) > REPORT_GIVE_UP:
        runs.mark_reported(row["slot"], "emailed", "gave up after a day")
