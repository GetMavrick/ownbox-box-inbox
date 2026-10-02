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

def start_sync(*, days: int = 1, rule: str = "") -> tuple[bool, str]:
    """A job per site, for yesterday, ONE AT A TIME: only the first is queued, and each queues the next when it
    finishes (OSDev1's review of #1813), so anything else waiting for the worker, an inbox draft say, waits behind
    one site at most, never all ten. Refused while a sync is queued or running.

    `days` and `rule` are the one-time re-count (sync.recount): the 28 days ending yesterday, and the counting rule
    that, once a site has synced, the sync state then records as in force."""
    if running(SYNC):
        return False, "A sync is already running. Its result shows here when it finishes."
    sites = list(dict.fromkeys(settings.sites()))
    run = uuid.uuid4().hex[:10]
    box_settings.put(settings.NS, RUN, {"run": run, "started": _now(), "sites": sites, "done": {},
                                        "days": max(int(days), 1), "rule": rule}, set_by="website")
    if sites:
        _enqueue(SYNC, f"{SYNC}:{run}:{sites[0]}", {"run": run, "site": sites[0]})
    return True, "Syncing now. The result shows here when it finishes, and you can leave this page."


def _queue_next(run: str, site: str) -> None:
    """The run's next site after `site`, queued before this one's result is written, so a run is never left with
    no job and an unfinished list. An older run (a newer press replaced it) queues nothing."""
    r = sync_run()
    if r.get("run") != run:
        return
    sites = list(dict.fromkeys(r.get("sites") or []))             # a site listed twice is synced once
    if site in sites and sites.index(site) + 1 < len(sites):
        nxt = sites[sites.index(site) + 1]
        _enqueue(SYNC, f"{SYNC}:{run}:{nxt}", {"run": run, "site": nxt})


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
        if r.get("rule") and len(errors) < len(done):
            # THE RE-COUNT IS DONE once any site was re-counted; a run that failed whole is tried again next hour.
            settings.set_sync_state(rule=r["rule"])
        # SYNC NOW REACHES THE REVIEW TOO (report.late): pressed between midnight and 06:00, it is the only sync of
        # that day, since the 06:00 pass then finds the day already done.
        if len(errors) < len(done):                     # at least one site synced: a run that failed whole adds nothing
            try:
                from datetime import date as _date
                from core import report as _report
                from .report import MACHINE as _MACHINE
                _report.late(_MACHINE, _date.fromisoformat(upto))
            except (ValueError, TypeError):
                pass


def do_sync(job: dict) -> dict:
    """The worker's half of Sync now: one site, yesterday. A refusal is an outcome, said on the card, not a retry."""
    from . import sync
    p = json.loads(job.get("raw_text") or "{}")
    run, site = str(p.get("run") or ""), str(p.get("site") or "")
    upto = sync.yesterday()
    conn = settings.posthog()
    if conn is None:
        _queue_next(run, site)
        _finish_site(run, site, "Connect PostHog first; the sync reads from it.", upto.isoformat())
        return {"site": site, "synced": 0}
    try:
        n = sync.sync_one(conn, site, upto, force=True, days=int(sync_run().get("days") or 1))
    except posthog.Refused as e:
        _queue_next(run, site)
        log.warning("website.sync_now_refused", site=site, why=str(e)[:160])
        _finish_site(run, site, str(e), upto.isoformat())
        return {"site": site, "synced": 0, "error": str(e)[:200]}
    _queue_next(run, site)
    _finish_site(run, site, "ok", upto.isoformat(), n)
    return {"site": site, "synced": n}


def sync_failed(job: dict, error: Exception) -> None:
    """A sync job the worker gave up on still ends its run, so the card never says "Syncing…" for ever."""
    try:
        p = json.loads(job.get("raw_text") or "{}")
        from . import sync
        _queue_next(str(p.get("run") or ""), str(p.get("site") or ""))     # one site's failure never ends the run
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
    """The worker's half of Check and save: one real query per site through what was typed, or, with no site yet,
    the sites PostHog sees. Saved only when PostHog answers; 0 page views says the snippet isn't on that site yet. A
    refusal saves nothing."""
    p = json.loads(job.get("raw_text") or "{}")
    host, project, new_key = str(p.get("host") or ""), str(p.get("project") or ""), bool(p.get("new_key"))
    key = box_secrets.get(PENDING_SECRET) if new_key else box_secrets.get(settings.SECRET)
    try:
        if not key:
            _said_check(False, f"Paste your PostHog personal API key. It needs the {posthog.SCOPE} scope.")
            return {"ok": False}
        conn = posthog.Conn(host=host, project=project, key=key)
        sites = settings.sites()
        try:
            if sites:
                seen = [(s, posthog.check(conn, s, settings.tz())) for s in sites]
            else:
                # SETUP FIXES ITSELF (owner, 2026-10-01; OSDev1's #1812): a new buyer connects PostHog before typing a
                # site, so the check asks PostHog which sites it sees, with the sync's own rule for a real site.
                from . import sync
                found = sync.site_hosts(conn)
        except posthog.Refused as e:
            _said_check(False, f"{e} Nothing was saved.")
            return {"ok": False}
        box_settings.put(settings.NS, "posthog_host", host, set_by="website")
        box_settings.put(settings.NS, "posthog_project", project, set_by="website")
        if new_key:
            box_secrets.put(settings.SECRET, key, user_id="website")
        if not sites:
            settings.set_found(found, _now())
            _said_check(True, ("Connected: we see visits on " + _and(found) + "." if found else
                               "Connected, but no visits yet. Add the PostHog snippet to your site, then press Check and save again."))
            return {"ok": True, "found": found}
        parts = [f"{s}: {n:,} page views" if n else f"{s}: none yet, so the PostHog snippet isn't on that site yet"
                 for s, n in seen]
        _said_check(True, "PostHog is connected. In the last 30 days, " + "; ".join(parts) + ".")
        return {"ok": True}
    finally:
        box_secrets.clear(PENDING_SECRET)


def _and(items: list[str]) -> str:
    """"a", "a and b", "a, b and c"."""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


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
