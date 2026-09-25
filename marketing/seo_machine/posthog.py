"""PostHog: how the business's website is doing, read from the business's own PostHog project.

OWNER, 2026-09-25, in OSDev6's session: *"post hog is going to be the next data source we connect.
It's going to be the most valuable way to scan our website for performance. Google analytics is the
second choice for that. We could give customers both options, but we should suggest post hog."*
And: *"Customers are going to demand current performance numbers immediately."*

WHY POSTHOG CONNECTS IN ONE STEP AND GOOGLE ANALYTICS DOES NOT. A PostHog personal API key and a
project ID are all it takes, so a buyer is looking at numbers the minute they save. Google Analytics
needs a Google sign-in with the Analytics permission on the shared Ownbox Google app, which has to
be switched on on Google's side first (docs/SCOPE_AEO_PERFORMANCE.md §5).

READ-ONLY, AND IT NEVER REASONS. Every number here is a HogQL query against PostHog's query API. No
model is asked anything (CLAUDE.md non-negotiable 3), and nothing is ever written to PostHog.

THE BOX'S OWN SITE ONLY. A PostHog project often collects several sites (the marketing site, the app,
a docs site). Every query is limited to the host on SEO Settings, so the numbers are this website's.

A CREDENTIAL NEVER APPEARS IN WHAT THIS RETURNS, the same rule as sources.py.
"""
from __future__ import annotations

import json as _json
import re
import threading
import time
from urllib.parse import urlsplit

from core import box_secrets, net
from core.logging import get_logger

from . import settings

log = get_logger(__name__)

KEY = "POSTHOG_API_KEY_SEO"

# PostHog Cloud's two regions. The API lives on these hosts, not on the us.i./eu.i. ingestion hosts
# the website's snippet sends events to. A self-hosted PostHog is its own address.
CLOUD = {"us": "https://us.posthog.com", "eu": "https://eu.posthog.com"}

# The one permission the key needs, in PostHog's own name, so the owner can match it on PostHog's page.
SCOPE = "query:read"

# THE ANSWER ENGINES, by the domain a visitor arrives from. Most AI-referred visits carry one of these
# as the referring domain, and ChatGPT also tags its links utm_source=chatgpt.com. This is the number
# an AEO buyer is paying to move, so it gets its own tile, and only answer engines count toward it:
# DuckDuckGo and Brave Search are ordinary search and would inflate it (OSDev1's review of #1584).
AI_REFERRERS = ("chatgpt.com", "chat.openai.com", "perplexity.ai", "www.perplexity.ai",
                "gemini.google.com", "copilot.microsoft.com", "claude.ai", "you.com")

PROJECT_RE = re.compile(r"^[0-9]{1,12}$")
_HOST_RE = re.compile(r"^[a-z0-9.-]{1,253}$")

BAD_KEY = "PostHog did not accept that key. Copy it again from your PostHog settings."
NO_ACCESS = ("That key cannot read this project. In PostHog, edit the key, give it the "
             f"{SCOPE} scope, and give it access to this project.")
NO_PROJECT = "PostHog has no project with that ID. The ID is in your project's settings."
UNREACHABLE = "PostHog did not answer. Check the region or address, then try again in a minute."
UNREADABLE = "PostHog's answer could not be read. Try again in a minute."

# Numbers are fetched when the page is opened, and kept for a few minutes so a page opened again asks
# PostHog nothing. PostHog limits how often its query API may be called. The cache is per process, so
# a box running two web workers may ask twice in a window; that is well inside PostHog's limit.
# A FAILURE IS KEPT TOO, for a minute, so a PostHog that is down is not asked four more times on
# every page open while it is down.
CACHE_S = 600
FAIL_CACHE_S = 60
_cache: dict = {}
_lock = threading.Lock()


class BadHost(ValueError):
    pass


def api_host(value: str) -> str:
    """The API address from what the owner chose: "us", "eu", or a self-hosted https address."""
    v = str(value or "").strip()
    if v.lower() in CLOUD:
        return CLOUD[v.lower()]
    if v.rstrip("/") in CLOUD.values():
        return v.rstrip("/")
    try:
        parts = urlsplit(v)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        raise BadHost("Type your PostHog address, like https://posthog.example.com.") from None
    if parts.scheme != "https" or "." not in host or parts.path.strip("/") or parts.query:
        raise BadHost("Type your PostHog address, like https://posthog.example.com.")
    if host.endswith(".i.posthog.com"):
        raise BadHost("That is PostHog's address for sending events. Choose US or EU instead.")
    return f"https://{host}" + (f":{port}" if port else "")


def _site_host() -> str:
    h = str(settings.get().get("host") or "").strip().lower()
    return h if _HOST_RE.match(h) else ""


def _query(host: str, project: str, key: str, hogql: str) -> tuple[int, dict]:
    """(status, parsed answer). Status 0 means PostHog could not be reached; {} means no answer."""
    try:
        status, body = net.post_public(
            f"{host}/api/projects/{project}/query/",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"query": {"kind": "HogQLQuery", "query": hogql}})
    except net.PostRefused:
        return 0, {}
    try:
        answer = _json.loads(body or "{}")
    except ValueError:
        answer = {}
    return status, answer if isinstance(answer, dict) else {}


def _problem(status: int) -> str | None:
    if status == 200:
        return None
    if status == 0:
        return UNREACHABLE
    if status == 401:
        return BAD_KEY
    if status == 403:
        return NO_ACCESS
    if status == 404:
        return NO_PROJECT
    return f"PostHog answered with an error ({status}). Try again in a minute."


def _bare() -> str:
    """This website's host without www., or "-" (matching nothing) when SEO Settings has none."""
    return _site_host().removeprefix("www.") or "-"


def _where(days_from: int, days_to: int = 0) -> str:
    """The time window, and this website only when SEO Settings names it."""
    parts = [f"timestamp >= now() - INTERVAL {int(days_from)} DAY"]
    if days_to:
        parts.append(f"timestamp < now() - INTERVAL {int(days_to)} DAY")
    site = _site_host()
    if site:
        # The site with and without www., so example.com and www.example.com agree (OSDev1's review).
        parts.append(f"properties.$host IN ('{_bare()}', 'www.{_bare()}')")
    return " AND ".join(parts)


def check(host: str, project: str, key: str) -> tuple[str | None, int]:
    """(problem or None, page views in the last 30 days). One real query, the one the screen runs.

    A key that passes but finds no page views is CONNECTED, with a warning the screen shows: the
    usual reason is that the PostHog snippet is not on the website yet, which no key can fix.
    """
    status, answer = _query(host, project, key,
                            f"SELECT count() FROM events WHERE event = '$pageview' AND {_where(30)}")
    problem = _problem(status)
    if problem:
        return problem, 0
    try:
        return None, int(answer["results"][0][0])
    except (KeyError, IndexError, TypeError, ValueError):
        return UNREADABLE, 0


def state() -> dict:
    s = settings.get()
    host, project = s.get("posthog_host") or "", s.get("posthog_project") or ""
    saved = box_secrets.is_set(KEY)
    return {"connected": bool(host and project and saved), "host": host, "project": project,
            "key_saved": saved}


def _one(host, project, key, hogql) -> list:
    status, answer = _query(host, project, key, hogql)
    if _problem(status):
        raise RuntimeError(_problem(status))
    rows = answer.get("results")
    if not isinstance(rows, list):
        raise RuntimeError(UNREADABLE)
    return rows


def _pct(now: float, before: float) -> float | None:
    return None if not before else round((now - before) / before * 100)


def performance(*, fresh: bool = False) -> dict:
    """The numbers the Performance screen shows, for the last 7 days against the 7 before.

    Returns {"ok": True, ...numbers} or {"ok": False, "why": sentence}. Never raises: a PostHog that
    is down costs the page its numbers, never the page.
    """
    st = state()
    if not st["connected"]:
        return {"ok": False, "why": "not_connected"}
    ck = (st["host"], st["project"], _site_host())
    with _lock:
        hit = _cache.get(ck)
        if hit and not fresh and time.time() - hit[0] < (CACHE_S if hit[1].get("ok")
                                                         else FAIL_CACHE_S):
            return hit[1]
    out = _fetch(st)
    with _lock:
        _cache[ck] = (time.time(), out)
    return out


def _fetch(st: dict) -> dict:
    key = box_secrets.get(KEY) or ""
    ai = ", ".join(f"'{d}'" for d in AI_REFERRERS)
    try:
        (row,) = _one(st["host"], st["project"], key, f"""
            SELECT
              countIf(timestamp >= now() - INTERVAL 7 DAY),
              countIf(timestamp < now() - INTERVAL 7 DAY),
              uniqIf(distinct_id, timestamp >= now() - INTERVAL 7 DAY),
              uniqIf(distinct_id, timestamp < now() - INTERVAL 7 DAY),
              countIf(timestamp >= now() - INTERVAL 7 DAY AND properties.$pathname LIKE '/articles/%'),
              countIf(timestamp < now() - INTERVAL 7 DAY AND properties.$pathname LIKE '/articles/%'),
              countIf(timestamp >= now() - INTERVAL 7 DAY AND (properties.$referring_domain IN ({ai})
                      OR properties.utm_source = 'chatgpt.com')),
              countIf(timestamp < now() - INTERVAL 7 DAY AND (properties.$referring_domain IN ({ai})
                      OR properties.utm_source = 'chatgpt.com'))
            FROM events WHERE event = '$pageview' AND {_where(14)}""")
        articles = _one(st["host"], st["project"], key, f"""
            SELECT properties.$pathname AS path, count() AS views FROM events
            WHERE event = '$pageview' AND {_where(7)} AND properties.$pathname LIKE '/articles/%'
            GROUP BY path ORDER BY views DESC LIMIT 5""")
        sources = _one(st["host"], st["project"], key, f"""
            SELECT properties.$referring_domain AS d, count() AS views FROM events
            WHERE event = '$pageview' AND {_where(7)} AND d != '$direct' AND d != ''
              AND d NOT IN ('{_bare()}', 'www.{_bare()}')
            GROUP BY d ORDER BY views DESC LIMIT 5""")
        return _shape(row, articles, sources)
    except RuntimeError as e:
        log.info("seo.performance_unavailable", reason=str(e)[:120])
        return {"ok": False, "why": str(e)}
    except (TypeError, ValueError, IndexError, KeyError, AttributeError):
        # A 200 whose rows are not the shape asked for. The page says so; it never 500s.
        log.info("seo.performance_unreadable")
        return {"ok": False, "why": UNREADABLE}


def _shape(row, articles, sources) -> dict:
    """The screen's numbers from the three answers. Raises on any row not shaped as asked."""
    def n(v):
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0


    if not isinstance(row, (list, tuple)) or len(row) != 8:
        raise ValueError("totals")
    views, views_b, people, people_b, art, art_b, ai_n, ai_b = (n(v) for v in row)
    pairs = []
    for rows in (articles, sources):
        if not all(isinstance(r, (list, tuple)) and len(r) == 2 for r in rows):
            raise ValueError("list")
        pairs.append([(str(a), n(b)) for a, b in rows if a])
    return {
        "ok": True, "site": _site_host(),
        # NOTHING RECORDED IN EITHER WEEK is its own state, not four tiles of zero (OSDev1's review):
        # the usual reason is that the PostHog snippet is not on the website.
        "empty": not (views or views_b),
        "views": views, "views_change": _pct(views, views_b),
        "visitors": people, "visitors_change": _pct(people, people_b),
        "article_views": art, "article_views_change": _pct(art, art_b),
        "ai_visits": ai_n, "ai_visits_change": _pct(ai_n, ai_b),
        "top_articles": pairs[0],
        "top_sources": pairs[1],
        "at": time.time(),
    }


def forget() -> None:
    """Drop cached numbers, so a new connection shows its own numbers straight away."""
    with _lock:
        _cache.clear()
