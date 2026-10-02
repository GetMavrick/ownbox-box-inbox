"""The seam machines call, and nothing else (plan §6): `sites()`, `day(site, day)`, `week(site, end_day)`.

Local DB reads only, from the store. No machine calls PostHog or Google directly: a machine that needs
a number the seam doesn't give asks for the seam to grow, in review.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from . import settings, sources, store


def sites() -> list[str]:
    """The sites this box watches (settings.sites): the ones a person typed, or, with none typed, the ones it found
    (setup fixes itself). ONLY THOSE (OSDev1, 2026-10-02): a site found once and since replaced by a typed list stays
    in the store, but never again gets a line on the review or the card."""
    return list(settings.sites())


def _totals(rows: list[dict]) -> dict:
    return {k: sum(int(r.get(k) or 0) for r in rows) for k in ("pageviews", "visitors", "sessions", "converted")}


def day(site: str, d: date) -> dict | None:
    """Everything stored for one site's day, or None."""
    row = store.day_row(site, d)
    if not row:
        return None
    refs = [(r["name"], int(r["n"]), json.loads(r["detail"] or "[]")) for r in store.items(site, d, "referrer")]
    return {
        "site": site, "day": d.isoformat(),
        "totals": {k: int(row.get(k) or 0) for k in ("pageviews", "visitors", "sessions", "converted")},
        "conversions": [(r["name"], int(r["n"]), int(r["detail"] or 0)) for r in store.items(site, d, "conversion")],
        "pages": [(r["name"], int(r["n"])) for r in store.items(site, d, "page")],
        "utms": [(r["name"], int(r["n"])) for r in store.items(site, d, "utm")],
        "referrers": refs,
        "by_source": sources.by_source([(ref, v) for ref, v, _ in refs]),
        "search": store.search_between(site, d, d),
        # PEOPLE ONLY (visitors.py): what was left out, by reason, and the kept visits whose address wasn't checked
        "left_out": [(r["name"], int(r["n"])) for r in store.items(site, d, "left_out")],
        "unchecked": sum(int(r["n"]) for r in store.items(site, d, "unchecked")),
    }


def _span(site: str, start: date, end: date) -> dict:
    rows = store.days(site, start, end)
    refs = store.items_between(site, start, end, "referrer")
    return {
        "start": start.isoformat(), "end": end.isoformat(), "days": len(rows),
        "totals": _totals(rows),
        "by_source": sources.by_source(refs),
        "referrers": refs[:10],
        "pages": store.items_between(site, start, end, "page")[:10],
        "conversions": store.items_between(site, start, end, "conversion"),
        "utms": store.items_between(site, start, end, "utm")[:10],
        "search": store.search_between(site, start, end),
        "left_out": store.items_between(site, start, end, "left_out"),
        "unchecked": sum(n for _, n in store.items_between(site, start, end, "unchecked")),
    }


def week(site: str, end_day: date) -> dict | None:
    """The seven days ending `end_day`, against the seven before. None when nothing is stored for either."""
    this = _span(site, end_day - timedelta(days=6), end_day)
    before = _span(site, end_day - timedelta(days=13), end_day - timedelta(days=7))
    if not this["days"] and not before["days"]:
        return None
    return {"site": site, **this, "before": before}
