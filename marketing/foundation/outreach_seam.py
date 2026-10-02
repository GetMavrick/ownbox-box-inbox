"""Instantly's numbers, read from the outreach store: `campaigns()`, `outreach_week(end_day)`, `outreach_day(day)`.

DORMANT, LIKE EVERYTHING INSTANTLY IN THIS PACKAGE (owner, 2026-10-02: Instantly is off on every box; a business that
uses it connects it by MCP). Out of the foundation's seam (seam.py), which machines call, so the package says what it
is: nothing on a box reads these. The dormant Outreach card (outreach_card.py) and its suite do, once
`wire_outreach()` has been called, which nothing on a box does (OSDev1's follow-up on #1830).
"""
from __future__ import annotations

from datetime import date, timedelta

from . import outreach_store, settings


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
