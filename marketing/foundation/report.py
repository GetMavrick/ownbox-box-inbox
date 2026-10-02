"""The Morning Review's line per site (docs/PLAN_ANALYTICS_FOUNDATION_PHASE1.md §7), from the store only.

One `happened` item per site with a stored week: "ownbox.io: 1,204 visits this week (+12%), 38 from AI answers,
4 conversions." A site with nothing stored says nothing (a fresh box is quiet, not broken). A sync that failed is a
`watch` item that names the fix, once, on the Data Sources page's own words.
"""
from __future__ import annotations

from datetime import date

from core.report import register_reporter

from . import seam, settings

MACHINE = "website"
TITLE = "Website"


def _pct(now: int, before: int) -> str:
    if not before:
        return ""
    return f" ({(now - before) * 100 // before:+d}%)"


def _line(site: str, w: dict) -> str:
    t, prev = w["totals"], w["before"]["totals"]
    parts = [f"{site.removeprefix('www.')}: {t['sessions']:,} visit{'s' if t['sessions'] != 1 else ''} this week"
             f"{_pct(t['sessions'], prev['sessions'])}"]
    ai = w["by_source"].get("ai", 0)
    if ai:
        parts.append(f"{ai:,} from AI answers")
    if t["converted"]:
        # NAMES ARE LABELS, NEVER PLURALISED ("4 book a calls" is nobody's sentence): the count, then the top
        # conversions by name with their own counts.
        named = ", ".join(f"{name} {n:,}" for name, n in w["conversions"][:2] if n)
        parts.append(f"{t['converted']:,} conversion{'s' if t['converted'] != 1 else ''}" + (f" ({named})" if named else ""))
    # NO FULL STOP: the Morning Review joins a machine's lines with commas (review_brief._moving), so a line that
    # ended in one read "...AI answers., brian-macdonald.com: ..." on the owner's first review with websites.
    return ", ".join(parts)


def report(day: date) -> dict:
    """The day's line per site, or {} when no site has a stored week ending that day."""
    happened = []
    for site in seam.sites():
        w = seam.week(site, day)
        if not w or not w["days"]:
            continue
        happened.append({"text": _line(site, w), "href": "/settings/sources"})
    out: dict = {}
    if happened:
        out = {"title": TITLE, "happened": happened}
    err = settings.sync_state().get("error") or ""
    if err:
        out.setdefault("title", TITLE)
        out["watch"] = [{"text": f"Website numbers could not be read: {err}", "state": "fail", "href": "/settings/sources"}]
    return out


register_reporter(MACHINE, TITLE, report)
