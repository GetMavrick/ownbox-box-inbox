"""What the box knows about ITSELF, offered to the connector: is it alive, and what is it spending.

THIS FILE EXISTS BECAUSE OF THE BASE MACHINE. ownbox.io sells a box with no add-on machine on it
and names the MCP server as one of four reasons to buy it. On such a box every tool that answers
with DATA comes from a machine, so a buyer who bought the base alone connects an agent, asks it
what is going on, and gets a manifest and three Morning Review tools that all say "nothing stored
yet". Technically non-empty; practically an endpoint with nothing to say on day one.

Health and spend are the two questions core can answer from the moment the box boots, before any
machine is installed, before the worker's first snapshot, and WITHOUT REASONING — pure DB and
config reads. That is the whole of this file.

WHY NOT REUSE core/health.py's report(). It returns a Slack string: `:white_check_mark:` markers,
line bullets, and a hard-coded product name in its heading. A connector answer is read by a model
and rendered by somebody else's client, so it has to be typed fields, and the product name in that
heading is not the name this box is sold under. The QUERIES are the valuable part and they are
duplicated here deliberately rather than refactored on launch eve — health.py's contract is "the
pulse answers even when everything else is down", and threading a second output format through it
is how that stops being true.

NEVER-BEATEN IS NOT DEAD, AND IT IS THE COMMON CASE HERE. A box that booted four minutes ago has
no worker heartbeat because the worker has not started yet, not because it died. Reported as its
own state, because a red light on a box that is merely new is how a buyer learns to ignore red
lights — the same reasoning docs/box/customer_voice/README.md records for the four rail states.

SPEND IS NOT IN HEALTH, AND THAT IS THE POINT OF SPLITTING THEM. core/report_tools.py already
withholds the meters segment from a `read` seat, loudly, because the box's spend is the owner's
business. A health tool carrying a budget line — which core/health.py's does — would hand that
same number to every read seat through a different door. So health carries no money at all and
spend is its own tool behind its own capability, which `read` does not hold.

NOTHING HERE REASONS. No brain.think, no vendor call, no spend of its own. CLAUDE.md section 11-6.
"""
from datetime import datetime, timedelta, timezone

from core import cost_guard, state
from core.config import settings
from core.connector import tools
from core.logging import get_logger

log = get_logger(__name__)

MACHINE = "core"

# Same thresholds core/health.py uses, and for the same reasons written there: continuous daemons
# beat about every 60s so silence past five minutes means the process is gone, while the backend
# is PROBED periodically, so an "ok" older than roughly two watchdog passes is not evidence the
# backend is up now.
_HEARTBEAT_STALE_S = 300
_BACKEND_STALE_S = 3900
_BACKEND_COMPONENTS = ("brain_backend", "probe:claude_code", "probe:anthropic_api")


def _age_s(iso: str | None) -> int | None:
    """Seconds since `iso`, or None. None means UNKNOWN AGE — never 0 and never -1.

    core/health.py returns -1 here and renders it as a number of seconds. That is fine for a line
    of Slack text a human reads; in a typed field a caller does arithmetic on, a sentinel integer
    is a bug waiting to be averaged.
    """
    try:
        return int((datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds())
    except Exception:                                # noqa: BLE001 — a bad timestamp is not fatal
        return None


def _fresh(age: int | None, limit: int) -> bool:
    return age is not None and 0 <= age <= limit


def _brain(hbs: dict) -> dict:
    """The reasoning backend, from the freshest probe of any kind.

    FOUR STATES, and three of them are not failures. Never probed, no AI key yet, a stale ok, and
    a real status. "No key yet" in particular is the state a box ships in — every machine here is
    inert until its keys are set — so reporting it as an outage would make a correctly configured
    new box look broken.
    """
    probes = [hbs[c] for c in _BACKEND_COMPONENTS if c in hbs]
    if not probes:
        return {"state": "not_probed", "ok": None,
                "note": "no probe has run yet on this box"}
    newest = max(probes, key=lambda h: h["ts"])
    age = _age_s(newest["ts"])
    status = newest["status"]
    if status == "unset":
        return {"state": "no_ai_key", "ok": None, "probed_s_ago": age,
                "note": "no AI key is set, so nothing on this box can draft yet"}
    if status == "ok" and not _fresh(age, _BACKEND_STALE_S):
        return {"state": "stale_ok", "ok": None, "probed_s_ago": age,
                "note": "the last probe succeeded but is too old to be evidence the backend is "
                        "up now"}
    return {"state": status, "ok": status == "ok", "probed_s_ago": age}


def _daemon(hbs: dict, component: str) -> dict:
    h = hbs.get(component)
    if not h:
        return {"state": "no_beat_yet", "ok": None,
                "note": "this process has not beaten since the box started"}
    age = _age_s(h["ts"])
    alive = h["status"] == "ok" and _fresh(age, _HEARTBEAT_STALE_S)
    return {"state": "running" if alive else "silent", "ok": alive, "beat_s_ago": age}


def health():
    """Is this box alive. Pure DB and config reads; carries no money — see the module docstring.

    BEST EFFORT PER SECTION. One failing query degrades its own section and names the failure; it
    never takes the answer down. A pulse that cannot answer while something is wrong is a pulse
    that only works when you do not need it.
    """
    from core.connector import manifest as _manifest

    out = {"checked_at": datetime.now(timezone.utc).isoformat(),
           "box_id": _manifest.box_id(),
           "box_type": _manifest.box_type(),
           "degraded": []}

    try:
        hbs = {h["component"]: h for h in state.get_heartbeats()}
    except Exception as e:                           # noqa: BLE001
        hbs = {}
        out["degraded"].append(f"heartbeats unreadable: {type(e).__name__}")

    out["brain"] = _brain(hbs)
    out["worker"] = _daemon(hbs, "worker")
    # Only reported when the box is actually configured for Slack. A box that was never given a
    # Slack token has no Slack daemon to be down, and a permanently red line for a feature the
    # buyer did not buy is the nag this codebase keeps refusing to ship.
    if settings.slack_app_token:
        out["slack"] = _daemon(hbs, "slack_socket")

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
        out["queue"] = {"queued": counts.get("queued", 0),
                        "running": counts.get("running", 0),
                        "failed_24h": failed_24h}
        out["alerts"] = {"failing": failing, "ok": not failing}
        out["last_error"] = None
        if last and (last["error"] or "").strip():
            out["last_error"] = {"error": last["error"][:160],
                                 "s_ago": _age_s(last["updated_at"])}
    except Exception as e:                           # noqa: BLE001
        out["degraded"].append(f"queue and alerts unreadable: {type(e).__name__}")

    # `ok` is withheld — None, not True — whenever any input was unreadable or any component is
    # itself unknown. A green light computed from data we could not read is the one output of this
    # tool that would be worse than no tool.
    parts = [out["brain"].get("ok"), out["worker"].get("ok"),
             out.get("alerts", {}).get("ok")]
    if out["degraded"] or any(p is None for p in parts):
        out["ok"] = None
        out["ok_note"] = ("withheld: something here is unknown rather than good or bad — read "
                          "the sections")
    else:
        out["ok"] = all(parts)
    return out


def spend():
    """What this box has spent this cycle against its ceiling, and its metered vendor usage.

    THE WINDOW IS NAMED, not implied. CLAUDE.md section 5 requires the in-app month-to-date window
    to be pinned to the same billing cycle and timezone as the console; a caller handed a number
    with no window cannot tell a month-to-date from a rolling 30 days, and those differ by the
    most expensive week of the month.
    """
    out = {"checked_at": datetime.now(timezone.utc).isoformat(), "degraded": []}
    try:
        spent, cap = cost_guard.month_to_date_spend(), cost_guard.ceiling()
        out["claude"] = {
            "cycle_to_date_usd": round(spent, 4),
            "ceiling_usd": cap,
            "remaining_usd": round(max(0.0, cap - spent), 4),
            "at_ceiling": spent >= cap,
            "cycle_started": cost_guard._cycle_start().isoformat(),
        }
    except Exception as e:                           # noqa: BLE001
        out["degraded"].append(f"claude spend unreadable: {type(e).__name__}")

    vendors = []
    try:
        for v in cost_guard.metered_vendors():
            vendors.append({"vendor": v,
                            "units_used": cost_guard.vendor_usage(v),
                            "units_cap": cost_guard.vendor_cap(v),
                            "usd_per_unit": cost_guard.vendor_usd_rate(v)})
    except Exception as e:                           # noqa: BLE001
        out["degraded"].append(f"vendor meters unreadable: {type(e).__name__}")
    out["vendors"] = vendors
    # Said out loud. An empty list here means NO METERED VENDOR IS CONFIGURED, which is the normal
    # state of a base machine and reads identically to "nothing was spent" if nobody says so.
    out["vendors_note"] = (None if vendors else
                           "no metered vendor is configured on this box, so there is no vendor "
                           "usage to report — this is the base machine's normal state")
    return out


tools.register(
    "health",
    fn=health, machine=MACHINE, min_role="read",
    # Its own capability, not read:manifest. The manifest describes CAPABILITY and never changes
    # between two calls a second apart; this is live state. A seat allowed to ask what the box can
    # do is not automatically a seat allowed to watch whether it is up.
    capability="read:health",
    description="Whether this box is alive: the reasoning backend, the worker, the job queue and "
                "any failing alerts. Carries no spend figures. Answers even when the worker and "
                "the model are both down.",
)

tools.register(
    "spend",
    fn=spend, machine=MACHINE, min_role="act",
    # NOT held by a read seat, deliberately and consistently: core/report_tools.py already
    # withholds the meters segment from a read seat because the box's spend is the owner's
    # business. Exposing the same number here under a capability `read` holds would reopen that
    # through a second door, which is how a withheld field becomes a withheld field in one place.
    capability="read:spend",
    description="What this box has spent against its ceiling this billing cycle, with the window "
                "it is measured over, plus metered vendor usage. Not visible to a read seat.",
)
