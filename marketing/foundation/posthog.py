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

from . import visitors

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


def q(conn: Conn, hogql: str, *, test_accounts_out: bool = False) -> list:
    """One HogQL query -> its rows. Raises Refused. `test_accounts_out` applies the project's own test-account filters
    where the query says {filters} (visitors.py: the owner's visits are left out by his own PostHog's rule too)."""
    query: dict = {"kind": "HogQLQuery", "query": hogql}
    if test_accounts_out:
        query["filters"] = {"filterTestAccounts": True}
    try:
        status, body = net.post_public(
            f"{conn.host.rstrip('/')}/api/projects/{conn.project}/query/",
            headers={"Authorization": f"Bearer {conn.key}", "Content-Type": "application/json"},
            json={"query": query}, timeout=90)
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


def twin(host: str) -> str:
    """The same site under its other spelling: "www.ownbox.io" for "ownbox.io", and back."""
    h = str(host or "").strip().lower()
    return h[4:] if h.startswith("www.") else "www." + h


def host_is(host: str, col: str = "properties.$host") -> str:
    """A SITE IS ITS HOST AND ITS WWW TWIN, both ways (OSDev1, 2026-10-02): ownbox.io answers with a 308 to
    www.ownbox.io, so every pageview there carries $host = www.ownbox.io, and an exact match on the "ownbox.io" a
    person typed found nothing. Stored under the name as typed."""
    return f"{col} in ({_lit(host)}, {_lit(twin(host))})"


def day_filter(d: date, tz: str) -> str:
    return f"toDate(toTimeZone(timestamp, {_lit(tz)})) = toDate('{d.isoformat()}')"


MAX_VISITS = 10000         # a site-day's visits read for the rule; HogQL answers 100 rows unless asked for more


def visits(conn: Conn, host: str, d: date, tz: str) -> list[dict]:
    """Every visit of the site's day, for visitors.sort: {sid, ip, pageviews, clicks, seconds, internal,
    test_account}. Two queries, all visits then the ones the project's test-account filters keep, so a visit the
    filters leave out is counted as the owner's rather than vanishing. The address is read into memory, never kept."""
    f = day_filter(d, tz)
    rows = q(conn, f"select $session_id, any(properties.$ip), countIf(event = '$pageview') pages, "
                   f"countIf(event = '$autocapture' and properties.$event_type = 'click'), "
                   f"dateDiff('second', min(timestamp), max(timestamp)), "
                   f"max(toString(properties.internal_user) = 'true') from events "
                   f"where {host_is(host)} and {f} group by $session_id having pages > 0 "
                   f"limit {MAX_VISITS}")
    kept = {str(r[0]) for r in q(conn, f"select distinct $session_id from events where event = '$pageview' "
                                       f"and {host_is(host)} and {f} and {{filters}} "
                                       f"limit {MAX_VISITS}", test_accounts_out=True)}
    return [{"sid": str(sid or ""), "ip": ip, "pageviews": int(pages or 0), "clicks": int(clicks or 0),
             "seconds": int(seconds or 0), "internal": bool(internal), "test_account": str(sid) not in kept}
            for sid, ip, pages, clicks, seconds, internal in rows]


def traffic(conn: Conn, host: str, d: date, tz: str, keep: str = "1 = 1") -> tuple[int, int, int]:
    """(page views, visitors, visits) for the site's day: visitors are distinct people, visits are sessions, both over
    the visits `keep` keeps (visitors.keep)."""
    r = q(conn, f"select count(), count(distinct person_id), count(distinct $session_id) from events "
                f"where event = '$pageview' and {host_is(host)} and {day_filter(d, tz)} and {keep}")
    return tuple(int(x or 0) for x in r[0]) if r else (0, 0, 0)


def conversions(conn: Conn, host: str, d: date, tz: str, defs: list[tuple[str, str]],
                keep: str = "1 = 1") -> list[tuple[str, int, int]]:
    """[(name, events, sessions)] for each conversion definition (name, HogQL condition)."""
    out = []
    for name, cond in defs:
        r = q(conn, f"select count(), count(distinct $session_id) from events "
                    f"where {host_is(host)} and ({cond}) and {day_filter(d, tz)} and {keep}")
        out.append((name, int(r[0][0] or 0) if r else 0, int(r[0][1] or 0) if r else 0))
    return out


def top(conn: Conn, host: str, d: date, tz: str, prop: str, n: int = 10, keep: str = "1 = 1") -> list[tuple[str, int]]:
    """The top values of an event property by sessions: landing pages (properties.$pathname) or UTM sources."""
    rows = q(conn, f"select {prop} p, count(distinct $session_id) s from events where event = '$pageview' "
                   f"and {host_is(host)} and {day_filter(d, tz)} and {keep} "
                   f"group by p order by s desc limit {int(n)}")
    return [(str(p), int(s or 0)) for p, s in rows if p not in (None, "")]


def referrers(conn: Conn, host: str, d: date, tz: str, n: int = 25,
              keep: str = "1 = 1") -> list[tuple[str, int, list[tuple[str, int]]]]:
    """Each referring site, its visits, and the pages those visits viewed. A visit is attributed to the site that
    referred its first page view, so later clicks inside the visit count under it too (the CRO report's rule). The
    visits are chosen once, inside, so the kept ones are the only ones joined."""
    f = day_filter(d, tz)
    rows = q(conn, f"""
        select ref, count(distinct sid) visits, groupArray((path, views)) pages from (
          select s.ref ref, e.$session_id sid, e.properties.$pathname path, count() views
          from events e
          join (select $session_id sid, argMin(properties.$referring_domain, timestamp) ref
                from events where event = '$pageview' and {host_is(host)} and {f} and {keep}
                group by sid) s on e.$session_id = s.sid
          where e.event = '$pageview' and {host_is(host, 'e.properties.$host')} and {f.replace('timestamp', 'e.timestamp')}
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
    r = q(conn, f"select count() from events where event = '$pageview' and {host_is(host)} "
                f"and timestamp >= now() - INTERVAL 30 DAY")
    return int(r[0][0] or 0) if r else 0


def hosts(conn: Conn, *, days: int = 30, min_views: int = 50, n: int = 25) -> list[tuple[str, int]]:
    """The hosts this project recorded page views on, busiest first, with at least `min_views` in `days` FROM PEOPLE
    (OSDev1's ruling): views from a data-center address don't count, nor the owner's, so a site only mail scanners
    open is never "found". An address that can't be read counts as a person, as everywhere (visitors.py)."""
    rows = q(conn, f"select properties.$host h, properties.$ip ip, count() c from events where event = '$pageview' "
                   f"and timestamp >= now() - INTERVAL {int(days)} DAY and {{filters}} "
                   f"and not (toString(properties.internal_user) = 'true') "
                   f"group by h, ip order by c desc limit {MAX_VISITS}", test_accounts_out=True)
    views: dict[str, int] = {}
    for h, ip, c in rows:
        if h and not visitors.network(ip):
            key = str(h).strip().lower()
            views[key] = views.get(key, 0) + int(c or 0)
    # ONE SITE, NOT TWO (OSDev1): a host and its www twin are found once, by the spelling people land on, with the
    # views of both, the same match every day query makes (host_is).
    sites: dict[str, int] = {}
    for h, c in sorted(views.items(), key=lambda kv: -kv[1]):
        if twin(h) in sites:
            sites[twin(h)] += c
        else:
            sites[h] = c
    ranked = sorted(sites.items(), key=lambda kv: -kv[1])
    return [(h, c) for h, c in ranked if c >= int(min_views)][:int(n)]
