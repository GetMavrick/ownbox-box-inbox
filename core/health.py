"""Zero-LLM operator pulse (A3).

The whole point: this answers *even when the model or the worker is down*. It is pure
DB + config reads, and it is posted by whichever process is asked (the Slack socket
daemon), NOT enqueued as a job — so a dead worker or a dead reasoning backend cannot
silence it. No reasoning, no vendor calls, no network beyond the one Slack post the
caller makes.
"""
from datetime import datetime, timedelta, timezone

from core import cost_guard, state
from core.config import settings

# Continuous heartbeats (worker, slack_socket) beat every ~60s, so staleness past this
# means the process is dead. Backend *probes* are periodic (startup + the ~30-min
# watchdog) — reported by last status, but an "ok" older than _BACKEND_STALE_S (≥2 missed
# watchdog passes) can't be trusted, so it shows AMBER rather than a confident green.
_HEARTBEAT_STALE_S = 300
_BACKEND_STALE_S = 3900
_BACKEND_COMPONENTS = ("brain_backend", "probe:claude_code", "probe:anthropic_api")


def _age_s(iso: str | None) -> int:
    try:
        return int((datetime.now(timezone.utc)
                    - datetime.fromisoformat(iso)).total_seconds())
    except Exception:  # noqa: BLE001 — a malformed/empty ts must never break the pulse
        return -1


def _mark(ok: bool) -> str:
    return ":white_check_mark:" if ok else ":red_circle:"


def report() -> str:
    """A compact Slack-formatted health snapshot. Best-effort per line: one failing
    lookup degrades that line, never the whole report."""
    lines = [":stethoscope: *Mavrick health*"]
    hbs = {h["component"]: h for h in state.get_heartbeats()}

    # ── reasoning backend: freshest of the A2 startup probe / watchdog probes ──
    backend = max((hbs[c] for c in _BACKEND_COMPONENTS if c in hbs),
                  key=lambda h: h["ts"], default=None)
    if backend is None:
        lines.append("• brain: :grey_question: no probe yet")
    else:
        age = _age_s(backend["ts"])
        if backend["status"] == "ok" and not (0 <= age <= _BACKEND_STALE_S):
            # An "ok" older than ~2 watchdog passes can't be trusted — the backend may have
            # died since. Don't let `health` show a confident green off a stale probe.
            lines.append(f"• brain: :large_yellow_circle: last probe ok but STALE ({age}s ago)")
        else:
            lines.append(f"• brain: {_mark(backend['status'] == 'ok')} {backend['status']} "
                         f"(probed {age}s ago)")

    # ── continuous daemons: stale beat == dead process ──
    for label, comp in (("worker", "worker"), ("slack", "slack_socket")):
        if comp == "slack_socket" and not settings.slack_app_token:
            continue
        h = hbs.get(comp)
        if not h:
            lines.append(f"• {label}: :grey_question: no beat yet")
            continue
        age = _age_s(h["ts"])
        ok = h["status"] == "ok" and 0 <= age <= _HEARTBEAT_STALE_S
        lines.append(f"• {label}: {_mark(ok)} beat {age}s ago")

    # ── queue depth, recent failures, failing alerts (pure DB) ──
    day_ago = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    try:
        with state.connect() as c:
            counts = {r["status"]: r["c"] for r in
                      c.execute("SELECT status, COUNT(*) c FROM jobs GROUP BY status")}
            failed_24h = c.execute(
                "SELECT COUNT(*) c FROM jobs WHERE status='failed' AND updated_at > ?",
                (day_ago,)).fetchone()["c"]
            last = c.execute(
                "SELECT error, updated_at FROM jobs WHERE status='failed' "
                "ORDER BY updated_at DESC LIMIT 1").fetchone()
            failing = [r["key"] for r in c.execute(
                "SELECT key FROM alert_state WHERE state='FAIL' ORDER BY key")]
        lines.append(f"• queue: {counts.get('queued', 0)} queued · "
                     f"{counts.get('running', 0)} running · {failed_24h} failed (24h)")
        lines.append("• alerts: " + (f"{_mark(False)} " + ", ".join(failing)
                                      if failing else f"{_mark(True)} none failing"))
        if last and (last["error"] or "").strip():
            lines.append(f"• last error ({_age_s(last['updated_at'])}s ago): "
                         f"`{last['error'][:160]}`")
    except Exception as e:  # noqa: BLE001 — the pulse must answer even if a query trips
        lines.append(f"• (db read degraded: {str(e)[:80]})")

    # ── budget headroom ──
    try:
        spent, cap = cost_guard.month_to_date_spend(), cost_guard.ceiling()
        budget = [f"claude ${spent:.2f}/${cap:.0f}"]
        for v in cost_guard.metered_vendors():
            budget.append(f"{v} {cost_guard.vendor_usage(v):g}/{cost_guard.vendor_cap(v):g}u")
        lines.append("• budget: " + " · ".join(budget))
    except Exception as e:  # noqa: BLE001
        lines.append(f"• (budget read degraded: {str(e)[:80]})")

    return "\n".join(lines)
