"""Sync now and Check and save, run by the worker, never inside a web request.

OSDev1's review of #1805: both ran in the request, a handful of PostHog queries per site at up to 90 seconds
each. Fine for one or two sites; at fleet scale that holds a web worker for minutes. So the card queues a job
and says so, the worker runs it, and the card shows "Syncing…" or "Checking…" until it is done, then the result.
A second press while one is queued or running is refused, said in a sentence.

WHAT IS RUNNING IS READ FROM THE QUEUE ITSELF (the `jobs` table), never from a flag of our own, so a worker that
restarted mid-job can't leave the card saying "Syncing…" forever: a job the reaper puts back is still queued, a
finished or failed one is not.

THE KEY NEVER GOES INTO THE QUEUE. A job's text is stored in plain sight in `jobs`; a key the owner typed waits
in box_secrets under its own pending name, and moves to the real one only when PostHog accepts it.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from core import box_secrets, box_settings, state
from core.logging import get_logger

from . import posthog, settings

log = get_logger(__name__)

SYNC, CHECK = "website_sync", "website_check"
PENDING_SECRET = "POSTHOG_API_KEY_WEB_PENDING"
RUN, LAST_CHECK = "sync_run", "last_check"


def _now() -> str:
    return datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds")


def running(intent: str) -> list[dict]:
    """The jobs of this kind still queued or running, oldest first."""
    with state.connect() as c:
        rows = c.execute("SELECT id, raw_text, status, created_at FROM jobs WHERE intent = ? "
                         "AND status IN ('queued', 'running') ORDER BY created_at", (intent,)).fetchall()
    return [dict(r) for r in rows]


def _enqueue(intent: str, key: str, payload: dict) -> None:
    from core.queue import queue
    queue.enqueue(idempotency_key=key, intent=intent, agent_name="website", raw_text=json.dumps(payload))


# ── Sync now ───────────────────────────────────────────────────────────────────────────────────────────────────

def start_sync() -> tuple[bool, str]:
    """One job per site, for yesterday. Refused while a sync is queued or running."""
    if running(SYNC):
        return False, "A sync is already running. Its result shows here when it finishes."
    sites = settings.sites()
    run = uuid.uuid4().hex[:10]
    box_settings.put(settings.NS, RUN, {"run": run, "started": _now(), "sites": sites, "done": {}},
                     set_by="website")
    for site in sites:
        _enqueue(SYNC, f"{SYNC}:{run}:{site}", {"run": run, "site": site})
    return True, "Syncing now. The result shows here when it finishes, and you can leave this page."


def sync_run() -> dict:
    r = box_settings.get(settings.NS, RUN)
    return r if isinstance(r, dict) else {}


def _finish_site(run: str, site: str, outcome: str, upto: str, n: int = 0) -> None:
    """Record one site's outcome ("ok" or the sentence); the last site of the run writes the sync state the card
    and the Morning Review read, as the daily sync does."""
    r = sync_run()
    if r.get("run") != run:
        return                                           # an older run; a newer press has replaced it
    done = dict(r.get("done") or {})
    done[site] = {"outcome": outcome, "days": n}
    r["done"] = done
    box_settings.put(settings.NS, RUN, r, set_by="website")
    if set(done) >= set(r.get("sites") or []):
        errors = [v["outcome"] for v in done.values() if v["outcome"] != "ok"]
        settings.set_sync_state(error=errors[0] if errors else "", last_run=_now(), upto=upto,
                                synced={s: v["days"] for s, v in done.items() if v["outcome"] == "ok"}, note="")


def do_sync(job: dict) -> dict:
    """The worker's half of Sync now: one site, yesterday. A refusal is an outcome, said on the card, not a retry."""
    from . import sync
    p = json.loads(job.get("raw_text") or "{}")
    run, site = str(p.get("run") or ""), str(p.get("site") or "")
    upto = sync.yesterday()
    conn = settings.posthog()
    if conn is None:
        _finish_site(run, site, "Connect PostHog first; the sync reads from it.", upto.isoformat())
        return {"site": site, "synced": 0}
    try:
        n = sync.sync_one(conn, site, upto, force=True)
    except posthog.Refused as e:
        log.warning("website.sync_now_refused", site=site, why=str(e)[:160])
        _finish_site(run, site, str(e), upto.isoformat())
        return {"site": site, "synced": 0, "error": str(e)[:200]}
    _finish_site(run, site, "ok", upto.isoformat(), n)
    return {"site": site, "synced": n}


def sync_failed(job: dict, error: Exception) -> None:
    """A sync job the worker gave up on still ends its run, so the card never says "Syncing…" for ever."""
    try:
        p = json.loads(job.get("raw_text") or "{}")
        from . import sync
        _finish_site(str(p.get("run") or ""), str(p.get("site") or ""),
                     "The sync stopped before it finished. Press Sync now to try again.", sync.yesterday().isoformat())
    except Exception as e:                               # noqa: BLE001 — a failure hook never compounds a failure
        log.warning("website.sync_hook_failed", why=type(e).__name__)


# ── Check and save ─────────────────────────────────────────────────────────────────────────────────────────────

def start_check(host: str, project: str, typed_key: str) -> tuple[bool, str]:
    """Queue the one real query per site. A typed key waits under its pending name; blank keeps the saved one."""
    if running(CHECK):
        return False, "A check is already running. Its result shows here when it finishes."
    new_key = bool(typed_key)
    if new_key:
        box_secrets.put(PENDING_SECRET, typed_key, user_id="website")
    else:
        box_secrets.clear(PENDING_SECRET)
    _enqueue(CHECK, f"{CHECK}:{uuid.uuid4().hex[:10]}", {"host": host, "project": project, "new_key": new_key})
    return True, "Checking PostHog now. The result shows here in a minute, and you can leave this page."


def last_check() -> dict:
    r = box_settings.get(settings.NS, LAST_CHECK)
    return r if isinstance(r, dict) else {}


def _said_check(ok: bool, said: str) -> None:
    box_settings.put(settings.NS, LAST_CHECK, {"ok": ok, "said": said, "at": _now()}, set_by="website")


def do_check(job: dict) -> dict:
    """The worker's half of Check and save: one real query per site through what was typed. Saved only when PostHog
    answers; 0 page views says the snippet isn't on that site yet. A refusal saves nothing."""
    p = json.loads(job.get("raw_text") or "{}")
    host, project, new_key = str(p.get("host") or ""), str(p.get("project") or ""), bool(p.get("new_key"))
    key = box_secrets.get(PENDING_SECRET) if new_key else box_secrets.get(settings.SECRET)
    try:
        if not key:
            _said_check(False, f"Paste your PostHog personal API key. It needs the {posthog.SCOPE} scope.")
            return {"ok": False}
        conn = posthog.Conn(host=host, project=project, key=key)
        try:
            seen = [(s, posthog.check(conn, s, settings.tz())) for s in settings.sites()]
        except posthog.Refused as e:
            _said_check(False, f"{e} Nothing was saved.")
            return {"ok": False}
        box_settings.put(settings.NS, "posthog_host", host, set_by="website")
        box_settings.put(settings.NS, "posthog_project", project, set_by="website")
        if new_key:
            box_secrets.put(settings.SECRET, key, user_id="website")
        parts = [f"{s}: {n:,} page views" if n else f"{s}: none yet, so the PostHog snippet isn't on that site yet"
                 for s, n in seen]
        _said_check(True, "PostHog is connected. In the last 30 days, " + "; ".join(parts) + ".")
        return {"ok": True}
    finally:
        box_secrets.clear(PENDING_SECRET)


def check_failed(job: dict, error: Exception) -> None:
    try:
        box_secrets.clear(PENDING_SECRET)
        _said_check(False, "The check stopped before it finished, and nothing was saved. Press Check and save "
                           "to try again.")
    except Exception as e:                               # noqa: BLE001
        log.warning("website.check_hook_failed", why=type(e).__name__)


def register() -> None:
    from core.worker import register as _register
    _register(SYNC, do_sync, on_failure=sync_failed)
    _register(CHECK, do_check, on_failure=check_failed)
