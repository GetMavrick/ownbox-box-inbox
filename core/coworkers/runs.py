"""The run ledger: one row per shift slot, from queued to its receipt (docs/SCOPE_SHIFTS.md §3).

The only reader and writer of `coworker_runs`. Every state change is ONE conditional UPDATE, so
two processes (a tick and a runner, or two ticks that overlap) can never both move a row: the one
whose WHERE still matches wins, and the other sees a rowcount of 0 and stands down.

    queued ──claim──▶ starting ──runner──▶ running ──▶ DONE | FAILED
       │                 │  └─(update in progress)──▶ queued
       └─(window closed)─┴──────────────────────────▶ MISSED

A slot is taken the moment it has a row. `queued` is the one status that is not yet a start: the
tick still owns it, and turns it into MISSED if its window closes first.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone

from core import state

ACTIVE = ("starting", "running")
FINAL = ("DONE", "FAILED", "MISSED")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_run_id() -> str:
    return "run_" + secrets.token_hex(8)


def get(slot: str) -> dict | None:
    with state.connect() as c:
        row = c.execute("SELECT * FROM coworker_runs WHERE slot = ?", (slot,)).fetchone()
    return dict(row) if row else None


def by_status(*statuses) -> list:
    marks = ",".join("?" * len(statuses))
    with state.connect() as c:
        return [dict(r) for r in c.execute(
            f"SELECT * FROM coworker_runs WHERE status IN ({marks}) ORDER BY window_latest, slot",
            statuses)]


def taken_keys(since_iso: str) -> set:
    """Every slot with a row that is past `queued` (a queued slot is still the tick's to plan).

    Bounded by window, because the ledger grows forever and the tick only ever plans a few days.
    """
    with state.connect() as c:
        return {r["slot"] for r in c.execute(
            "SELECT slot FROM coworker_runs WHERE status != 'queued' AND window_latest >= ?",
            (since_iso,))}


def queue(slot: str, *, coworker: str, start: str, latest: str, dry_run: bool = False,
          now: str | None = None) -> bool:
    """Take the slot. False if it already had a row: at most once, by the primary key."""
    with state.connect() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO coworker_runs (slot, run_id, coworker, window_start, "
            "window_latest, status, dry_run, created_at) VALUES (?,?,?,?,?,'queued',?,?)",
            (slot, new_run_id(), coworker, start, latest, int(dry_run), now or _now()))
        return cur.rowcount == 1


def _move(slot: str, frm: tuple, **cols) -> bool:
    sets = ", ".join(f"{k} = ?" for k in cols)
    marks = ",".join("?" * len(frm))
    with state.connect() as c:
        cur = c.execute(f"UPDATE coworker_runs SET {sets} WHERE slot = ? AND status IN ({marks})",
                        (*cols.values(), slot, *frm))
        return cur.rowcount == 1


def note(slot: str, text: str) -> None:
    """Why a queued slot is still waiting. Becomes its MISSED reason if the window closes."""
    _move(slot, ("queued",), note=text)


def claim(slot: str, unit: str, now: str | None = None) -> bool:
    """queued → starting, by the tick, just before it starts the runner. Exactly one tick wins."""
    return _move(slot, ("queued",), status="starting", unit=unit, claimed_at=now or _now())


def requeue(slot: str, why: str) -> bool:
    """starting → queued, by a runner that found an update in progress. The window still counts."""
    return _move(slot, ("starting",), status="queued", note=why, unit=None, claimed_at=None)


def start(slot: str) -> bool:
    """starting → running, by the runner that holds the shift lock."""
    return _move(slot, ("starting",), status="running", started_at=_now())


def finish(slot: str, receipt: dict) -> bool:
    """running|starting → DONE|FAILED, with the receipt. Only an active run can finish."""
    return _move(slot, ACTIVE, status=receipt["outcome"], ended_at=receipt["ended_at"],
                 receipt=json.dumps(receipt))


def drop(slot: str, receipt: dict) -> bool:
    """queued → FAILED, for a queued slot whose MISSED receipt could not be written."""
    return _move(slot, ("queued",), status="FAILED", ended_at=receipt["ended_at"],
                 receipt=json.dumps(receipt))


def miss(slot: str, receipt: dict) -> bool:
    """queued → MISSED, with the receipt."""
    return _move(slot, ("queued",), status="MISSED", ended_at=receipt["ended_at"],
                 receipt=json.dumps(receipt))


def unreported() -> list:
    """Ended runs whose report has not gone out on some channel yet (the tick retries them)."""
    with state.connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM coworker_runs WHERE status IN ('DONE','FAILED','MISSED') "
            "AND (emailed_at IS NULL OR pushed_at IS NULL) ORDER BY ended_at")]


def mark_reported(slot: str, channel: str, value: str | None = None) -> None:
    """Record that `channel` ("emailed" or "pushed") went out, or is settled as not applicable."""
    if channel not in ("emailed", "pushed"):
        raise ValueError(channel)
    with state.connect() as c:
        c.execute(f"UPDATE coworker_runs SET {channel}_at = ? WHERE slot = ?",
                  (value or _now(), slot))


def claim_report(slot: str, channel: str) -> bool:
    """Take the right to send `channel` for this run, once. False if someone already has."""
    if channel not in ("emailed", "pushed"):
        raise ValueError(channel)
    with state.connect() as c:
        cur = c.execute(f"UPDATE coworker_runs SET {channel}_at = ? WHERE slot = ? "
                        f"AND {channel}_at IS NULL", (_now(), slot))
        return cur.rowcount == 1


def seat_calls(seat_id: str) -> list:
    """The run seat's MCP calls, per tool: [{tool, calls, failed}], for the receipt."""
    with state.connect() as c:
        rows = c.execute(
            "SELECT tool, COUNT(*) AS calls, SUM(outcome != 'ok') AS failed FROM seat_actions "
            "WHERE seat_id = ? GROUP BY tool ORDER BY tool", (seat_id,)).fetchall()
    return [{"tool": r["tool"], "calls": int(r["calls"]), "failed": int(r["failed"] or 0)}
            for r in rows]
