"""The daily sync (plan §4): once a day, after 06:00 in the buyer's timezone, yesterday for every site; a 28-day
backfill when a site is first connected; idempotent per (site, day). A failure is kept for the Data Sources page
and the Morning Review's watch line, and never stops the worker.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from core.logging import get_logger

from . import posthog, settings, store, visitors

log = get_logger(__name__)

BACKFILL_DAYS = 28
HOUR_LOCAL = 6            # the day is complete in the buyer's timezone by then; PostHog has caught up
CATCH_UP_DAYS = 7         # a box that was off for a while re-syncs up to a week, not a quarter


def _local_today(tz: str) -> date:
    try:
        return datetime.now(ZoneInfo(tz)).date()
    except Exception:                                   # noqa: BLE001 — an unknown zone is UTC
        return datetime.utcnow().date()


def sync_site_day(conn: posthog.Conn, site: str, d: date, tz: str) -> None:
    """One site's day, from PostHog into the store, counting people only (visitors.py): the day's visits are sorted
    first, and every number after that reads the ones kept. Raises posthog.Refused."""
    out, why, unchecked = visitors.sort(posthog.visits(conn, site, d, tz))
    keep = visitors.keep(out)
    store.write_day(
        site, d,
        traffic=posthog.traffic(conn, site, d, tz, keep),
        conversions=posthog.conversions(conn, site, d, tz, settings.conversions(site), keep),
        pages=posthog.top(conn, site, d, tz, "properties.$pathname", keep=keep),
        utms=posthog.top(conn, site, d, tz, "properties.utm_source", keep=keep),
        referrers=posthog.referrers(conn, site, d, tz, keep=keep),
        left_out=why, unchecked=unchecked)


def _search_day(site: str, d: date) -> None:
    """Search Console, when this box's chosen property is this site. Never raises: Google's absence is not news."""
    try:
        from core.vendors import google_search_console as gsc
        st = gsc.status()
        prop = str(st.get("property") or "")
        if not st.get("connected") or not prop:
            return
        bare = site.removeprefix("www.")
        if bare not in prop and site not in prop:
            return
        rows = gsc.search_analytics(d.isoformat(), d.isoformat(), dimensions=("date",), limit=10)
        for r in rows:
            store.write_search(site, d, r.get("clicks", 0), r.get("impressions", 0), r.get("position"))
    except Exception as e:                              # noqa: BLE001
        log.info("website.search_skipped", site=site, why=type(e).__name__)


FIND_EVERY_DAYS = 7
MAX_FOUND = 5
_NOT_A_SITE = re.compile(r"(^localhost$|^127\.|^0\.0\.0\.0$|^\[|^[0-9.]+$|\.local$|\.test$|\.localhost$|"
                         r"\.vercel\.app$|\.ownbox\.app$|\.ngrok(-free)?\.(app|io|dev)$)")


def site_hosts(conn: posthog.Conn) -> list[str]:
    """The real sites this PostHog project sees, busiest first. Raises posthog.Refused. Used by the weekly find and by
    the card's first connection (marketing/foundation/jobs.py), so both call the same hosts a site."""
    # A HOST WITH A PORT OR A TRAILING DOT IS NOT A PUBLIC SITE (localhost:3000, 192.168.1.20:3000, example.com.):
    # PostHog records $host with the port, and a day query matches $host exactly, so it is dropped, not cleaned.
    # EACH HOST ONCE (OSDev1's review of #1815): hosts() lower-cases, so "Ownbox.io" and "ownbox.io" are one site,
    # and a site listed twice would end the one-at-a-time sync chain at its second appearance.
    seen = (str(h or "").strip().lower() for h, _ in posthog.hosts(conn))
    return list(dict.fromkeys(h for h in seen
                              if h and ":" not in h and not h.endswith(".") and not _NOT_A_SITE.search(h)))[:MAX_FOUND]


def _find_sites(conn: posthog.Conn) -> None:
    """SETUP FIXES ITSELF: with no sites typed on the card, find them in PostHog, once a week. A real site is a host
    with page views; a preview, a laptop or the box itself is not. Never raises: the typed list, the last list
    found or the AEO Machine's site stands when PostHog doesn't answer."""
    if settings.sites_source() == "yours":
        return
    last = settings.found_at()
    try:
        if last and (datetime.now(ZoneInfo("UTC")) - datetime.fromisoformat(last)).days < FIND_EVERY_DAYS:
            return
    except ValueError:
        pass
    try:
        hosts = site_hosts(conn)
    except posthog.Refused as e:
        log.info("website.find_sites_skipped", why=str(e)[:120])
        return
    settings.set_found(hosts, _now_iso())
    log.info("website.sites_found", n=len(hosts))


def _wanted(site: str, upto: date) -> list[date]:
    """The days to sync for a site, oldest first: a backfill for a new site, a catch-up for a stale one."""
    newest = store.newest_day(site)
    if newest is None:
        return [upto - timedelta(days=i) for i in range(BACKFILL_DAYS - 1, -1, -1)]
    gap = (upto - newest).days
    if gap <= 0:
        return []
    return [newest + timedelta(days=i) for i in range(1, min(gap, CATCH_UP_DAYS) + 1)]


def yesterday() -> date:
    """Yesterday on the buyer's clock: the last whole day."""
    return _local_today(settings.tz()) - timedelta(days=1)


def sync_one(conn: posthog.Conn, site: str, upto: date, *, force: bool = False, days: int = 1) -> int:
    """One site up to `upto`: the `days` ending there when forced (Sync now: 1; a new counting rule: 28), else the days
    it is missing. Returns how many days were synced. Raises posthog.Refused. Idempotent per (site, day): a re-sync
    replaces the day."""
    tz = settings.tz()
    days = [upto - timedelta(days=i) for i in range(max(int(days), 1) - 1, -1, -1)] if force else _wanted(site, upto)
    for d in days:
        sync_site_day(conn, site, d, tz)
        _search_day(site, d)
    return len(days)


def sync(upto: date | None = None, *, force: bool = False) -> dict:
    """Bring every site up to `upto` (yesterday, local, by default). Returns a summary and records it."""
    upto = upto or yesterday()
    conn = settings.posthog()
    out: dict = {"upto": upto.isoformat(), "synced": {}, "error": ""}
    if conn is None:
        settings.set_sync_state(error="", last_run=_now_iso(), note="no_posthog")
        return out
    _find_sites(conn)
    for site in settings.sites():
        try:
            out["synced"][site] = sync_one(conn, site, upto, force=force)
        except posthog.Refused as e:
            out["error"] = str(e)
            log.warning("website.sync_failed", site=site, why=str(e)[:160])
            break
    store.prune()
    settings.set_sync_state(error=out["error"], last_run=_now_iso(), upto=upto.isoformat(),
                            synced=out["synced"], note="")
    # THE MORNING REVIEW CARRIES THE NUMBERS: yesterday closed at midnight, before this sync could know its visits, so
    # the website row for the day just synced is re-reported while that day's review hasn't been started
    # (report.late). AFTER the sync state is written, so the row reads this run's outcome, never the last one's.
    if out["synced"]:
        from core import report as _report
        from .report import MACHINE as _MACHINE
        _report.late(_MACHINE, upto)
    return out


def _now_iso() -> str:
    return datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds")


def recount() -> bool:
    """THE NUMBERS ARE RE-COUNTED ONCE WHEN THE RULE CHANGES (OSDev1's ruling, after #1822): the last 28 days of every
    site, through the worker one site at a time (jobs.start_sync), so this week and last both count people and the
    change between them means something. True while it is due or running, so the daily sync waits for it.

    A box with nothing stored, or no PostHog, has nothing counted the old way, so the rule is simply taken up."""
    if settings.sync_state().get("rule") == visitors.RULE:
        return False
    if settings.posthog() is None or not store.stored_sites():
        settings.set_sync_state(rule=visitors.RULE)
        return False
    from . import jobs
    if not jobs.running(jobs.SYNC):
        jobs.start_sync(days=BACKFILL_DAYS, rule=visitors.RULE)
        log.info("website.recount_started", rule=visitors.RULE)
    return True


def tick() -> None:
    """The worker's hourly pass: re-count once if the rule changed, else sync once the local day has turned and the
    hour has come. Never raises."""
    try:
        if recount():
            return
        tz = settings.tz()
        now = datetime.now(ZoneInfo(tz))
        if now.hour < HOUR_LOCAL:
            return
        target = now.date() - timedelta(days=1)
        if settings.sync_state().get("upto") == target.isoformat() and not settings.sync_state().get("error"):
            return
        sync(target)
    except Exception as e:                              # noqa: BLE001 — the worker's tick must not die here
        log.warning("website.tick_failed", why=f"{type(e).__name__}: {str(e)[:120]}")
