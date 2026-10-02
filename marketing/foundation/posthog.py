"""PostHog, read the one way: the HogQL of scripts/cro_daily_report.py, through core.net.

docs/PLAN_ANALYTICS_FOUNDATION_PHASE1.md §3: the CRO report, the store, the AEO Machine and the website report
coworker must never disagree, so the queries here are the report's, parameterised only by the site, the day and
the buyer's timezone. CHANGE A QUERY IN BOTH PLACES OR IN NEITHER.

Read-only. The key is the business's own PostHog personal key with `query:read`, held in box_secrets and never
logged. A PostHog that is down or refuses raises `Refused` with the sentence the Data Sources page shows.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from core import net

# The one permission the key needs, in PostHog's own name.
SCOPE = "query:read"

BAD_KEY = "PostHog did not accept that key. Copy it again from your PostHog settings."
NO_ACCESS = ("That key cannot read this project. In PostHog, edit the key, give it the "
             f"{SCOPE} scope, and give it access to this project.")
NO_PROJECT = "PostHog has no project with that ID. The ID is in your project's settings."
UNREACHABLE = "PostHog did not answer. Check the region or address, then try again in a minute."
UNREADABLE = "PostHog's answer could not be read. Try again in a minute."


class Refused(RuntimeError):
    """PostHog said no, or nothing. str(e) is the sentence for the page."""


@dataclass(frozen=True)
class Conn:
    host: str        # https://us.posthog.com, https://eu.posthog.com, or a self-hosted address
    project: str     # the numeric project id
    key: str         # the personal API key; in memory for the one call, never logged


def q(conn: Conn, hogql: str) -> list:
    """One HogQL query -> its rows. Raises Refused."""
    try:
        status, body = net.post_public(
            f"{conn.host.rstrip('/')}/api/projects/{conn.project}/query/",
            headers={"Authorization": f"Bearer {conn.key}", "Content-Type": "application/json"},
            json={"query": {"kind": "HogQLQuery", "query": hogql}}, timeout=90)
    except net.PostRefused:
        raise Refused(UNREACHABLE) from None
    problem = {200: None, 401: BAD_KEY, 403: NO_ACCESS, 404: NO_PROJECT}.get(
        status, f"PostHog answered with an error ({status}). Try again in a minute.")
    if problem:
        raise Refused(problem)
    try:
        rows = json.loads(body or "{}").get("results")
    except (ValueError, AttributeError):
        raise Refused(UNREADABLE) from None
    if not isinstance(rows, list):
        raise Refused(UNREADABLE)
    return rows


def _lit(s: str) -> str:
    """A string literal for HogQL: hosts and timezones are our own settings, quoted all the same."""
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"


def day_filter(d: date, tz: str) -> str:
    return f"toDate(toTimeZone(timestamp, {_lit(tz)})) = toDate('{d.isoformat()}')"


def traffic(conn: Conn, host: str, d: date, tz: str) -> tuple[int, int, int]:
    """(page views, visitors, sessions) for the site's day."""
    r = q(conn, f"select count(), count(distinct person_id), count(distinct $session_id) from events "
                f"where event = '$pageview' and properties.$host = {_lit(host)} and {day_filter(d, tz)}")
    return tuple(int(x or 0) for x in r[0]) if r else (0, 0, 0)


def conversions(conn: Conn, host: str, d: date, tz: str, defs: list[tuple[str, str]]) -> list[tuple[str, int, int]]:
    """[(name, events, sessions)] for each conversion definition (name, HogQL condition)."""
    out = []
    for name, cond in defs:
        r = q(conn, f"select count(), count(distinct $session_id) from events "
                    f"where properties.$host = {_lit(host)} and ({cond}) and {day_filter(d, tz)}")
        out.append((name, int(r[0][0] or 0) if r else 0, int(r[0][1] or 0) if r else 0))
    return out


def top(conn: Conn, host: str, d: date, tz: str, prop: str, n: int = 10) -> list[tuple[str, int]]:
    """The top values of an event property by sessions: landing pages (properties.$pathname) or UTM sources."""
    rows = q(conn, f"select {prop} p, count(distinct $session_id) s from events where event = '$pageview' "
                   f"and properties.$host = {_lit(host)} and {day_filter(d, tz)} group by p order by s desc limit {int(n)}")
    return [(str(p), int(s or 0)) for p, s in rows if p not in (None, "")]


def referrers(conn: Conn, host: str, d: date, tz: str, n: int = 25) -> list[tuple[str, int, list[tuple[str, int]]]]:
    """Each referring site, its visits, and the pages those visits viewed. A visit is attributed to the site that
    referred its first page view, so later clicks inside the visit count under it too (the CRO report's rule)."""
    f = day_filter(d, tz)
    rows = q(conn, f"""
        select ref, count(distinct sid) visits, groupArray((path, views)) pages from (
          select s.ref ref, e.$session_id sid, e.properties.$pathname path, count() views
          from events e
          join (select $session_id sid, argMin(properties.$referring_domain, timestamp) ref
                from events where event = '$pageview' and properties.$host = {_lit(host)} and {f}
                group by sid) s on e.$session_id = s.sid
          where e.event = '$pageview' and e.properties.$host = {_lit(host)} and {f.replace('timestamp', 'e.timestamp')}
          group by ref, sid, path)
        group by ref order by visits desc limit {int(n)}""")
    out = []
    for ref, visits, pages in rows:
        totals: dict[str, int] = {}
        for path, views in pages or []:
            totals[str(path)] = totals.get(str(path), 0) + int(views or 0)
        ranked = sorted(totals.items(), key=lambda kv: -kv[1])[:8]
        out.append((str(ref or "$direct"), int(visits or 0), ranked))
    return out


def check(conn: Conn, host: str, tz: str) -> int:
    """One real query, the one the sync runs: page views in the last 30 days. Raises Refused; 0 means the key
    works but the PostHog snippet is not on the site yet, which no key can fix."""
    r = q(conn, f"select count() from events where event = '$pageview' and properties.$host = {_lit(host)} "
                f"and timestamp >= now() - INTERVAL 30 DAY")
    return int(r[0][0] or 0) if r else 0
