"""The Morning Review's line per site (docs/PLAN_ANALYTICS_FOUNDATION_PHASE1.md §7), from the store only.

One `happened` item per site with a stored week: "ownbox.io: 1,204 visits this week (+12%), 38 from AI answers,
4 conversions." A site with nothing stored says nothing (a fresh box is quiet, not broken). A sync that failed is a
`watch` item that names the fix, once, on the Data Sources page's own words.
"""
from __future__ import annotations

from datetime import date, timedelta

from core.report import register_reporter

from . import seam, settings, visitors

MACHINE = "website"
TITLE = "Website"


def _pct(now: int, before: int) -> str:
    if not before:
        return ""
    return f" ({(now - before) * 100 // before:+d}%)"


def _line(site: str, w: dict) -> str:
    t, prev = w["totals"], w["before"]["totals"]
    # PEOPLE ONLY, AND SAID (OSDev1's ruling, after #1822): the visits counted are people's, and what was left out is
    # named in the same breath, so the number never looks smaller for no reason. "Visits from people", not "people":
    # the store keeps each day's distinct people, and a week of them added up would count a returning visitor twice.
    pct = _pct(t["sessions"], prev["sessions"]).strip(" ()")
    left = visitors.say(w.get("left_out") or [])
    aside = "; ".join(x for x in (pct, f"{left} left out" if left else "") if x)
    parts = [f"{site.removeprefix('www.')}: {t['sessions']:,} visit{'s' if t['sessions'] != 1 else ''} from people "
             f"this week" + (f" ({aside})" if aside else "")]
    ai = w["by_source"].get("ai", 0)
    if ai:
        named = _named(w["by_source"].get("assistants"))
        parts.append(f"{ai:,} from AI answers" + (f" ({named})" if named else ""))
    if t["converted"]:
        # NAMES ARE LABELS, NEVER PLURALISED ("4 book a calls" is nobody's sentence): the count, then the top
        # conversions by name with their own counts.
        named = ", ".join(f"{name} {n:,}" for name, n in w["conversions"][:2] if n)
        parts.append(f"{t['converted']:,} conversion{'s' if t['converted'] != 1 else ''}" + (f" ({named})" if named else ""))
    # NO FULL STOP: the Morning Review joins a machine's lines with commas (review_brief._moving), so a line that
    # ended in one read "...AI answers., brian-macdonald.com: ..." on the owner's first review with websites.
    return ", ".join(parts)


def _named(assistants, n: int = 3) -> str:
    """The AI apps that sent visits, most first: "ChatGPT 21, Perplexity 9"."""
    top = sorted(((str(k), int(v or 0)) for k, v in (assistants or {}).items() if v), key=lambda kv: (-kv[1], kv[0]))
    return ", ".join(f"{name} {v:,}" for name, v in top[:n])


def _sent_by_ai(weeks: list[dict]) -> str:
    """THE AI THAT SENT PEOPLE, NAMED (OSDev1's pick, owner D5 10-03: "reports may name the AI app that sent a
    visitor"). The foundation counts visits per assistant (sources.py); this line said only "N from AI answers". A med
    spa owner reading "ChatGPT sent you 3 visits this week" sees AEO work in their own numbers. '' when none came."""
    total: dict = {}
    for w in weeks:
        for name, v in ((w.get("by_source") or {}).get("assistants") or {}).items():
            total[name] = total.get(name, 0) + int(v or 0)
    top = sorted(((k, v) for k, v in total.items() if v), key=lambda kv: (-kv[1], kv[0]))
    if not top:
        return ""
    (first, n), rest = top[0], top[1:3]
    return (f"{first} sent you {n:,} visit{'s' if n != 1 else ''} this week"
            + "".join(f", {name} {v:,}" for name, v in rest))


def _end(site: str, day: date) -> date | None:
    """THE WEEK ENDS ON THE LAST WHOLE DAY STORED, never past it (OSDev1's finding, 10-04 14:08). The sync stores
    yesterday after 06:00 and never today, so a week ending on the review's own day held six days: brian-macdonald.com
    read 179 on Oct 3's review and 172 on Oct 4's, a fall nobody had, which the box's AI ranked, while the website and
    AEO answers (the week ending yesterday) said 179. None when the site has nothing stored."""
    newest = seam.newest(site)
    return min(day, newest) if newest else None


def _people(site: str, day: date) -> int | None:
    w = seam.week(site, day)
    return int(w["totals"]["sessions"] or 0) if w and w["days"] else None


def report(day: date) -> dict:
    """The day's line per site, or {} when no site has a stored week ending that day.

    THE HEADLINE IS THE WEEK'S VISITS FROM PEOPLE, ACROSS HIS SITES (OSDev1's assignment, 2026-10-02, scope #1839).
    The owner's AI read this segment's headline as label "" and value 0 beside "179 visits from people this week".
    It is the sum of the per-site numbers below it, people only (visitors.py), so the two can never disagree. Each
    site's week ends on its last stored day (`_end`), so today's review, before today is synced, carries the same
    whole week as yesterday's. The delta is that week against the week ending the day before it, from the store, not
    from yesterday's stored row:
    every row stored before this fix says 0, and "+185 vs the day before" would be a number nobody earned. A site
    without a week ending the day before means no change at all: its whole week would read as growth."""
    happened, total, before, sites, weeks = [], 0, 0, 0, []
    for site in seam.sites():
        end = _end(site, day)
        w = seam.week(site, end) if end else None
        if not w or not w["days"]:
            continue
        weeks.append(w)
        happened.append({"text": _line(site, w), "href": "/settings/sources"})
        total += int(w["totals"]["sessions"] or 0)
        sites += 1
        prev = _people(site, end - timedelta(days=1))
        before = None if (before is None or prev is None) else before + prev
    # WHERE PEOPLE CAME FROM (Morning Review V2 step 2): the week's visits from search, AI answers and social, each a
    # figure the page draws alone and the review's AI is handed. Only the ones that sent someone.
    figures = {}
    for key, label in (("search", "visits from search this week"), ("ai", "visits from AI answers this week"),
                       ("social", "visits from social sites this week")):
        n = sum(int((w.get("by_source") or {}).get(key) or 0) for w in weeks)
        if n:
            figures[f"from_{key}"] = {"value": n, "label": label, "href": "/settings/sources"}
    sent = _sent_by_ai(weeks)
    if sent:
        happened.insert(0, {"text": sent, "href": "/settings/sources"})   # first, so the brief carries it too
    out: dict = {}
    if happened:
        label = (f"visit{'s' if total != 1 else ''} from people this week"
                 + (f" across {sites} sites" if sites > 1 else ""))
        out = {"title": TITLE, "happened": happened,
               "headline": {"value": total, "label": label, "delta": (total - before) if before is not None else None,
                            "better": "more", "week": "last"}}
        if figures:
            out["figures"] = figures
    err = settings.sync_state().get("error") or ""
    if err:
        out.setdefault("title", TITLE)
        out["watch"] = [{"text": f"Website numbers could not be read: {err}", "state": "fail", "href": "/settings/sources"}]
    return out


register_reporter(MACHINE, TITLE, report)
