"""The seam machines call, and nothing else (plan §6): `sites()`, `day(site, day)`, `week(site, end_day)`, and for
outreach, `campaigns()`, `outreach_week(end_day)` and `outreach_day(day)`.

Local DB reads only, from the store. No machine calls PostHog, Google or Instantly directly: a machine that needs
a number the seam doesn't give asks for the seam to grow, in review.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from . import outreach_store, settings, sources, store


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


# ── outreach (Instantly): campaigns(), outreach_week(end_day), outreach_day(day) ─────────────────────────────────

_OUT = ("sent", "contacted", "opened", "clicked", "replied", "bounced", "unsubscribed", "opportunities", "booked")
_STEP = ("sent", "opened", "clicked", "replied", "booked")


def campaigns() -> list[dict]:
    """Every campaign the box has read: [{"id", "name", "status"}], by name. The id is Instantly's and stays put
    when a campaign is renamed; the name is its label."""
    return [{"id": r["campaign_id"], "name": r["name"], "status": r["status"]} for r in outreach_store.campaigns()]


def _step_sort(step: str):
    return (0, int(step)) if str(step).isdigit() else (1, str(step))


def _number(step: str) -> int | None:
    """The step as a person counts it, the first email being 1, whichever way this workspace's Instantly numbers it
    (outreach_sync.learn_step_base). None for a step Instantly couldn't name."""
    if not str(step).isdigit():
        return None
    return int(step) + (1 if settings.pull_state().get("step_base") == 0 else 0)


def _week_of(start: date, end: date) -> dict | None:
    rows = outreach_store.week_rows(start, end)
    if not rows:
        return None
    names = {c["id"]: c["name"] for c in campaigns()}
    by: dict[str, dict] = {}
    for r in rows:
        c = by.setdefault(r["campaign_id"], {"id": r["campaign_id"], "name": names.get(r["campaign_id"], ""),
                                             **{k: 0 for k in _OUT}, "steps": []})
        if r["step"] == "":
            c.update({k: int(r.get(k) or 0) for k in _OUT})
        else:
            c["steps"].append({"step": r["step"], "number": _number(r["step"]),
                               **{k: int(r.get(k) or 0) for k in _STEP}})
    out = sorted(by.values(), key=lambda c: (-c["sent"], c["name"]))
    for c in out:
        c["steps"].sort(key=lambda s: _step_sort(s["step"]))
    return {"start": start.isoformat(), "end": end.isoformat(), "campaigns": out,
            "totals": {k: sum(c[k] for c in out) for k in _OUT}}


def outreach_week(end_day: date) -> dict | None:
    """The seven days ending `end_day`, by campaign and by step, against the seven before. None when neither was read.

    A WEEK IS READ AS A WEEK (OSDev1's review): its opens, clicks and replies are Instantly's unique counts over the
    seven days, never the days' counts added up, so they match Instantly's own dashboard for the same dates. A
    campaign's `booked` is its steps' meetings booked. Each step carries `step`, as Instantly sent it, and `number`,
    as a person counts it (the first email is 1)."""
    this = _week_of(end_day - timedelta(days=6), end_day)
    before = _week_of(end_day - timedelta(days=13), end_day - timedelta(days=7))
    if this is None and before is None:
        return None
    return {**(this or {"start": (end_day - timedelta(days=6)).isoformat(), "end": end_day.isoformat(),
                        "campaigns": [], "totals": {k: 0 for k in _OUT}}),
            "before": (before or {}).get("totals")}


def outreach_day(d: date) -> dict | None:
    """One of the buyer's days, by campaign and by step. A campaign's day is its steps added together, each step's
    counts unique within that step and that day. None when nothing was read for the day."""
    rows = outreach_store.day_rows(d)
    if not rows:
        return None
    names = {c["id"]: c["name"] for c in campaigns()}
    by: dict[str, dict] = {}
    for r in rows:
        c = by.setdefault(r["campaign_id"], {"id": r["campaign_id"], "name": names.get(r["campaign_id"], ""),
                                             **{k: 0 for k in _STEP}, "steps": []})
        for k in _STEP:
            c[k] += int(r.get(k) or 0)
        c["steps"].append({"step": r["step"], "number": _number(r["step"]), **{k: int(r.get(k) or 0) for k in _STEP}})
    out = sorted(by.values(), key=lambda c: (-c["sent"], c["name"]))
    for c in out:
        c["steps"].sort(key=lambda s: _step_sort(s["step"]))
    return {"day": d.isoformat(), "campaigns": out, "totals": {k: sum(c[k] for c in out) for k in _STEP}}
