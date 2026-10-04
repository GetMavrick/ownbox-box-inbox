"""What each article brought (#1793 Phase 2.4): visits from people and visits from AI answers, per article, for the
week and since it went live. *"'How much does Botox cost' brought 14 people this week, 3 from AI answers."* The first
half of *"How many leads are you getting from ChatGPT?"*, answered with the buyer's own numbers.

THE BOX'S OWN STORE ONLY (marketing/foundation/seam.py `pages`): local reads, no PostHog, never slow, never raises.
A box with no website store, or no site the AEO Machine writes for, brings nothing, and every reader shows nothing.
Conversions per article (#1793 Phase 2.4b): the visits that read the article and converted that day, by any of the
site's conversions (the sync keeps them per article from the day it learned to; earlier days count none).

BEHIND THE LABS SWITCH `article_results` (core/labs.py; owner 10-04: step 3 reaches buyers once H2's score bar is
met). Off, every reader gets nothing from here, so aeo.articles, aeo.article, the Articles screen and the Morning
Review are exactly what they were before.
"""
from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import urlparse

from core import labs
from core.logging import get_logger

log = get_logger(__name__)

PREFIX = "/articles/"
LABS = "article_results"


def site() -> str | None:
    """The AEO site as the store knows it (www. or not), or None."""
    try:
        from marketing.foundation import seam

        from . import posthog
        own = posthog._site_host().removeprefix("www.")
        return next((x for x in seam.sites() if x.removeprefix("www.") == own), None) if own else None
    except Exception as e:                                # noqa: BLE001 — no store is no store, never an error
        log.warning("aeo.store_unreadable", error=f"{type(e).__name__}: {str(e)[:120]}")
        return None


def last_day() -> date | None:
    """The last whole day on the buyer's clock, or None with no store."""
    try:
        from marketing.foundation import sync as fsync
        return fsync.yesterday()
    except Exception:                                     # noqa: BLE001
        return None


def path_of(url) -> str:
    """An article's path on its site, the way the store keeps pages: "/articles/botox-cost"."""
    try:
        p = urlparse(str(url or "")).path
    except ValueError:
        return ""
    p = p.rstrip("/")
    return p if p.startswith(PREFIX) else ""


def _pages(s: str, start: date, end: date) -> dict:
    try:
        from marketing.foundation import seam
        return seam.pages(s, start, end, PREFIX) if start <= end else {}
    except Exception as e:                                # noqa: BLE001 — a count, never the answer
        log.warning("aeo.brought_unreadable", error=f"{type(e).__name__}: {str(e)[:120]}")
        return {}


def _counts(got: dict | None) -> dict:
    got = got or {}
    named = sorted((got.get("assistants") or {}).items(), key=lambda kv: (-kv[1], kv[0]))
    return {"people": int(got.get("people") or 0), "from_ai": int(got.get("ai") or 0),
            "assistants": {k: v for k, v in named if v}, "converted": int(got.get("converted") or 0)}


def _live_day(row: dict) -> date | None:
    try:
        return date.fromisoformat(str(row.get("published_at") or "")[:10])
    except ValueError:
        return None


def for_articles(rows: list[dict], end: date | None = None) -> dict:
    """{article id: {"through", "this_week", "since_live"}} for each published row whose page the store has seen.
    `end` is the last day counted (the last whole day when left out); the week is the seven days ending there."""
    if not labs.on(LABS):
        return {}
    s, end = site(), end or last_day()
    live = [r for r in rows if r.get("status") == "published" and path_of(r.get("url")) and _live_day(r)]
    if not s or not end or not live:
        return {}
    week = _pages(s, end - timedelta(days=6), end)
    out = {}
    for r in live:
        p = path_of(r["url"])
        since = _pages(s, _live_day(r), end).get(p)
        if not since or not since.get("people"):
            continue
        out[r["id"]] = {"through": end.isoformat(), "this_week": _counts(week.get(p)), "since_live": _counts(since)}
    return out


def top_of_week(rows: list[dict], end: date) -> tuple[dict, dict] | None:
    """(row, counts) for the published article that brought the most people in the seven days ending `end`, or None
    when none brought anyone."""
    if not labs.on(LABS):
        return None
    s = site()
    live = {path_of(r.get("url")): r for r in rows if r.get("status") == "published" and path_of(r.get("url"))}
    if not s or not live:
        return None
    week = _pages(s, end - timedelta(days=6), end)
    best = max(((p, _counts(got)) for p, got in week.items() if p in live), default=None,
               key=lambda pc: (pc[1]["people"], pc[1]["from_ai"], pc[0]))
    if not best or not best[1]["people"]:
        return None
    return live[best[0]], best[1]


def words(c: dict, when: str) -> str:
    """"14 people this week, 3 from AI answers (ChatGPT 2, Perplexity 1), 1 conversion"."""
    people = int(c.get("people") or 0)
    out = f"{people:,} {'person' if people == 1 else 'people'} {when}"
    ai, named = int(c.get("from_ai") or 0), c.get("assistants") or {}
    if ai and len(named) == 1:
        out += f", {ai:,} from {next(iter(named))}"
    elif ai:
        out += f", {ai:,} from AI answers (" + ", ".join(f"{k} {v:,}" for k, v in list(named.items())[:3]) + ")"
    won = int(c.get("converted") or 0)
    if won:
        out += f", {won:,} conversion{'' if won == 1 else 's'}"
    return out
