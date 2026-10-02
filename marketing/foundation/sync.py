"""The daily sync (plan §4): once a day, after 06:00 in the buyer's timezone, yesterday for every site; a 28-day
backfill when a site is first connected; idempotent per (site, day). A failure is kept for the Data Sources page
and the Morning Review's watch line, and never stops the worker.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from core.logging import get_logger

from . import posthog, settings, store

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
    """One site's day, from PostHog into the store. Raises posthog.Refused."""
    store.write_day(
        site, d,
        traffic=posthog.traffic(conn, site, d, tz),
        conversions=posthog.conversions(conn, site, d, tz, settings.conversions(site)),
        pages=posthog.top(conn, site, d, tz, "properties.$pathname"),
        utms=posthog.top(conn, site, d, tz, "properties.utm_source"),
        referrers=posthog.referrers(conn, site, d, tz))


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


def _wanted(site: str, upto: date) -> list[date]:
    """The days to sync for a site, oldest first: a backfill for a new site, a catch-up for a stale one."""
    newest = store.newest_day(site)
    if newest is None:
        return [upto - timedelta(days=i) for i in range(BACKFILL_DAYS - 1, -1, -1)]
    gap = (upto - newest).days
    if gap <= 0:
        return []
    return [newest + timedelta(days=i) for i in range(1, min(gap, CATCH_UP_DAYS) + 1)]


def sync(upto: date | None = None, *, force: bool = False) -> dict:
    """Bring every site up to `upto` (yesterday, local, by default). Returns a summary and records it."""
    tz = settings.tz()
    upto = upto or (_local_today(tz) - timedelta(days=1))
    conn = settings.posthog()
    out: dict = {"upto": upto.isoformat(), "synced": {}, "error": ""}
    if conn is None:
        settings.set_sync_state(error="", last_run=_now_iso(), note="no_posthog")
        return out
    for site in settings.sites():
        days = [upto] if force else _wanted(site, upto)
        try:
            for d in days:
                sync_site_day(conn, site, d, tz)
                _search_day(site, d)
            out["synced"][site] = len(days)
        except posthog.Refused as e:
            out["error"] = str(e)
            log.warning("website.sync_failed", site=site, why=str(e)[:160])
            break
    store.prune()
    settings.set_sync_state(error=out["error"], last_run=_now_iso(), upto=upto.isoformat(),
                            synced=out["synced"], note="")
    return out


def _now_iso() -> str:
    return datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds")


def tick() -> None:
    """The worker's hourly pass: sync once the local day has turned and the hour has come. Never raises."""
    try:
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
