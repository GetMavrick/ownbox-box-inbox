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

from core import cost_guard, report, state
from core.config import settings
from core.connector import prompts, tools
from core.connector import words as say
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
    if status == "unchecked":
        # One failed check of the AI sign-in (core/watchdog.CLAUDE_FIRST_MISS): not green, not "no key".
        return {"state": "unchecked", "ok": None, "probed_s_ago": age,
                "note": "the last check of the AI sign-in did not get through; the next one, within "
                        "the hour, decides"}
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
    from core import version
    from core.connector import manifest as _manifest

    out = {"checked_at": datetime.now(timezone.utc).isoformat(),
           "box_id": _manifest.box_id(),
           "box_type": _manifest.box_type(),
           # WHICH RELEASE IS RUNNING, from the same place the box's HTTP /health reads it (core/version.py, the
           # tag box_update.sh wrote before the restart). The daily check of the owner's box, 2026-10-02: /health
           # named the release and this answer did not, so his AI could not say which version he was on. None on
           # a box that never installed a verified release, which is the truth about it.
           "release": version.RUNNING_RELEASE,
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
                            "units_soft_goal": cost_guard.vendor_soft_goal(v),
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


# ── the answers in words (core/connector/words.py) ─────────────────────────────────────────────────
# Owner, 2026-10-02: *"This is not an AI business machine. This is a dumb box."* Each tool below answers in plain
# words over the connector; the fields above still travel beside the words for the AI to work with.

_AI_WORDS = {
    "ok": "Your AI account is signed in and answering.",
    "not_probed": "Your AI account has not been checked yet. The box checks it within the hour.",
    "unchecked": "The last check of your AI account did not get through. The next one, within the hour, decides.",
    "stale_ok": "Your AI account answered at its last check, but that was a while ago.",
}


def _seconds_ago(s) -> str:
    if not isinstance(s, (int, float)) or isinstance(s, bool) or s < 0:
        return ""
    return say.ago((datetime.now(timezone.utc) - timedelta(seconds=s)).isoformat())


def _release_words(tag) -> str:
    """`release/2026.10.02.10` -> `2026.10.02.10`, as a person reads a version."""
    t = str(tag or "").strip()
    return t.split("/", 1)[1] if t.startswith("release/") else t


def _render_health(r: dict) -> str:
    ok = r.get("ok")
    head = ("Your box is running, and everything it checks is working." if ok is True else
            "Your box is running, but something needs attention." if ok is False else
            "Your box is up, but some of what it checks is unknown right now.")
    lines = []
    brain = r.get("brain") or {}
    st = brain.get("state")
    if st == "no_ai_key":
        lines.append(f"No AI account is signed in yet, so nothing on the box can write. Sign in on Settings: "
                     f"{say.link('/settings/ai')}")
    elif st in _AI_WORDS:
        lines.append(_AI_WORDS[st])
    elif st:
        lines.append(f"Your AI account is not answering. Sign in again on Settings: {say.link('/settings/ai')}")
    for key, name in (("worker", "The box's background work"), ("slack", "Slack")):
        d = r.get(key)
        if not isinstance(d, dict):
            continue
        if d.get("state") == "running":
            lines.append(f"{name} is running.")
        elif d.get("state") == "no_beat_yet":
            lines.append(f"{name} has not started yet. A new box takes a few minutes.")
        elif d.get("state") == "silent":
            when = _seconds_ago(d.get("beat_s_ago"))
            lines.append(f"{name} has gone quiet" + (f": last heard {when}." if when else "."))
    q = r.get("queue")
    if isinstance(q, dict):
        bits = [f"{say.n(q.get('queued', 0))} waiting" if q.get("queued") else "",
                f"{say.n(q.get('running', 0))} running" if q.get("running") else "",
                f"{say.n(q.get('failed_24h', 0))} failed in the last day" if q.get("failed_24h") else ""]
        bits = [b for b in bits if b]
        lines.append("Jobs: " + ", ".join(bits) + "." if bits else "No jobs are waiting or failing.")
    failing = (r.get("alerts") or {}).get("failing") or []
    if failing:
        names = ", ".join(str(k).replace("_", " ").replace(":", " ").strip() for k in failing[:5])
        lines.append(f"{say.plural(len(failing), 'check is', 'checks are')} failing: {names}.")
    last = r.get("last_error")
    if isinstance(last, dict):
        when = _seconds_ago(last.get("s_ago"))
        if when:
            lines.append(f"The last job that failed did so {when}.")
    if r.get("degraded"):
        lines.append("Some of the box's records could not be read just now, so this answer is partial.")
    release = _release_words(r.get("release"))
    foot = " ".join(x for x in (f"Running release {release}." if release else "", say.as_of(r.get("checked_at")))
                    if x)
    return say.answer(
        head, say.bullets(lines), foot,
        say.ask_next(("morning_review.report_day", "What happened on my box yesterday?"),
                     ("core.spend", "What has my box spent on AI this month?"),
                     ("core.manifest", "What can my box do for me?")))


def _money(v) -> str:
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return ""


def _render_spend(r: dict) -> str:
    parts = []
    c = r.get("claude")
    if isinstance(c, dict):
        since = say.day_words(str(c.get("cycle_started") or "")[:10])
        line = (f"This billing cycle{f' (since {since})' if since else ''}, your box has spent "
                f"{_money(c.get('cycle_to_date_usd'))} on AI against a ceiling of {_money(c.get('ceiling_usd'))}, "
                f"so {_money(c.get('remaining_usd'))} is left.")
        if c.get("at_ceiling"):
            line += " It has reached its ceiling: the AI stops until the next cycle starts."
        parts.append(line)
    vendors = [v for v in (r.get("vendors") or []) if isinstance(v, dict)]
    # A METER NOTHING HAS USED IS NOT A LINE (owner, 2026-09-29, on the review: "If a line doesn't have data, it
    # should not be displayed"), and a 0-of-0 budget is the same nothing.
    used = [v for v in vendors if (v.get("units_used") or 0) > 0]
    if used:
        parts.append(say.section("Paid services this cycle:", [
            f"{report.vendor_name(v.get('vendor'))}: {say.n(v.get('units_used'))} of {say.n(v.get('units_cap'))} used"
            if v.get("units_cap") else f"{report.vendor_name(v.get('vendor'))}: {say.n(v.get('units_used'))} used"
            for v in used]))
    elif vendors:
        parts.append("None of the paid services the box meters has been used this cycle.")
    else:
        parts.append("No paid service is metered on this box, so AI is the only spend.")
    if r.get("degraded"):
        parts.append("Some of the spend records could not be read just now, so this answer is partial.")
    parts.append(say.as_of(r.get("checked_at")))
    return say.answer(*parts, say.ask_next(
        ("morning_review.report_day", "What happened on my box yesterday?"),
        ("morning_review.report_trend", "How have my numbers changed this month?"),
        ("core.health", "Is my box running?")))


tools.register(
    "health",
    title="See whether your box is running",
    fn=health, machine=MACHINE, min_role="read", render=_render_health,
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
    title="See what your box has spent on AI",
    fn=spend, machine=MACHINE, min_role="act", render=_render_spend,
    # NOT held by a read seat, deliberately and consistently: core/report_tools.py already
    # withholds the meters segment from a read seat because the box's spend is the owner's
    # business. Exposing the same number here under a capability `read` holds would reopen that
    # through a second door, which is how a withheld field becomes a withheld field in one place.
    capability="read:spend",
    description="What this box has spent against its ceiling this billing cycle, with the window "
                "it is measured over, plus metered vendor usage. Not visible to a read seat.",
)


# ── STOP AND START AGAIN, from a chat (docs/SCOPE_INBOX_CONNECTOR.md step 4) ─────────────────────
# The Dashboard's "Stop everything" and "Start again" buttons, as proposals: the AI asks, the owner taps
# Approve in the mobile app, and `core.pause` runs exactly as the buttons run it. Owner, 2026-10-02: a buyer
# runs the box from their own AI ("they need full control capabilities"); for now every change is a tap.
PAUSE_KIND = "box_pause"


def _ask_pause(stop: bool, seat=None) -> dict:
    from core import approvals, pause
    if pause.is_paused() == stop:
        return {"asked": False, "note": f"the box is already {'stopped' if stop else 'running'}"}
    words = ({"Change": "Stop everything", "Means": "no new messages arrive and nothing runs on its own; "
              "a person can still reply by hand"} if stop else
             {"Change": "Start the box again", "Means": "messages arrive and the machines run again"})
    a = approvals.propose(PAUSE_KIND, machine=MACHINE, title=words["Change"],
                          detail={"app": "Your box", "arguments": words, "stop": stop},
                          seat_id=str((seat or {}).get("label") or (seat or {}).get("id") or ""))
    return {"asked": True, "approval": a["id"], "repeat": bool(a.get("repeat")),
            "note": "waiting for the owner, who approves or declines in the mobile app. Nothing has changed yet."}


def propose_stop(seat=None):
    """Ask the owner to stop everything, as the Dashboard's button does."""
    return _ask_pause(True, seat)


def propose_start(seat=None):
    """Ask the owner to start the box again after a stop."""
    return _ask_pause(False, seat)


def _run_pause(detail: dict) -> dict:
    from core import pause
    if (detail or {}).get("stop"):
        from core import approvals
        pause.halt(f"approval:{approvals.decider() or 'owner'}")
        log.warning("box_tools.halt_approved")
        return {"ok": True, "text": "Stopped. Nothing runs on its own until it is started again."}
    pause.resume()
    log.info("box_tools.resume_approved")
    return {"ok": True, "text": "Started again."}


from core import approvals as _approvals  # noqa: E402

_approvals.register_kind(PAUSE_KIND, run=_run_pause)


def _render_pause(r: dict) -> str:
    """What now waits on Approvals, or why nothing was asked (the box is already that way)."""
    return say.proposal(r, ("core.health", "Is my box running?"),
                        ("morning_review.report_day", "What happened on my box yesterday?"))


tools.register(
    "propose_stop",
    title="Ask before stopping your box",
    fn=propose_stop, machine=MACHINE, min_role="act", render=_render_pause,
    capability="write:proposals", wants_seat=True,
    description="Ask the owner to stop everything on this box, as the Dashboard's Stop button does. Nothing "
                "changes until the owner approves.",
)

tools.register(
    "propose_start",
    title="Ask before starting your box again",
    fn=propose_start, machine=MACHINE, min_role="act", render=_render_pause,
    capability="write:proposals", wants_seat=True,
    description="Ask the owner to start this box again after a stop. Nothing changes until the owner approves.",
)

# WHAT NEEDS THE OWNER TODAY includes a box that is not well (core/connector/prompts.py; the ask is registered in
# core/report_tools.py beside the review it reads first).
prompts.use("what_needs_me", "core.health", "whether the box itself is running, and anything on it that is "
                                            "failing or needs signing in again")
