"""website.detail: one site's week in full, for the owner's own AI (#1857 F1).

The foundation keeps every site's days in the box's own store, people only (visitors.py), and the Morning Review and
the website card read a line of it. This is the whole week behind that line: top pages, the sites that sent people,
where they came from (AI answers by name, per D5 on #1857), conversions, and what was left out as not a person. From
the store only (`seam.week`), so it answers whatever PostHog is doing.

A read anyone the owner connected may make: `read:website`, granted to every role, as read:aeo is.
"""
from __future__ import annotations

from datetime import timedelta

from core.connector import tools
from core.connector import words as say

from . import seam
from . import sync as fsync

CAPABILITY = "read:website"
WEEKS_BACK = 12
_SOURCES = (("search", "search engines"), ("ai", "AI answers"), ("social", "social sites"), ("email", "email"),
            ("direct", "typed in or bookmarked"), ("other", "other sites"))


def _pct(now: int, before: int):
    return None if not before else round((now - before) * 100.0 / before, 1)


def _pick(site) -> tuple[str | None, list[str]]:
    sites = seam.sites()
    if not site:
        return (sites[0] if sites else None), sites
    want = str(site).strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0].removeprefix("www.")
    return next((s for s in sites if s.removeprefix("www.") == want), None), sites


def detail(site=None, week=None) -> dict:
    """The 7 days to yesterday (week 0) or that many weeks before (up to 12), against the 7 before that."""
    try:
        back = max(0, min(int(week or 0), WEEKS_BACK))
    except (TypeError, ValueError):
        back = 0
    s, sites = _pick(site)
    if not sites:
        return tools.NotConfigured("No website is being counted on this box yet. " + say.could_find_out("website"))
    if s is None:
        return {"available": False, "asked": str(site)[:80], "sites": sites}
    wk = seam.week(s, fsync.yesterday() - timedelta(days=7 * back))
    if not wk:
        return {"available": False, "site": s, "sites": sites, "week": back,
                "note": "Nothing is stored for that week yet; the box fills it in each morning."}
    t, b = wk.get("totals") or {}, (wk.get("before") or {}).get("totals") or {}
    src = wk.get("by_source") or {}
    return {
        "available": True, "site": s, "sites": sites, "week": back, "from": wk.get("start"), "to": wk.get("end"),
        "visitors": int(t.get("visitors") or 0), "visitors_change": _pct(int(t.get("visitors") or 0),
                                                                     int(b.get("visitors") or 0)),
        "pageviews": int(t.get("pageviews") or 0), "pageviews_change": _pct(int(t.get("pageviews") or 0),
                                                                          int(b.get("pageviews") or 0)),
        "converted": int(t.get("converted") or 0),
        "by_source": {k: int(src.get(k) or 0) for k, _ in _SOURCES},
        "assistants": dict(src.get("assistants") or {}),
        "top_pages": [{"path": p, "views": n} for p, n in wk.get("pages") or []],
        "referrers": [{"site": r, "visits": n} for r, n in wk.get("referrers") or []],
        "conversions": [{"name": n, "count": c} for n, c, *_ in wk.get("conversions") or []],
        "left_out": [{"why": w, "visits": n} for w, n in wk.get("left_out") or []],
        "unchecked": int(wk.get("unchecked") or 0),
    }


def _render(r: dict) -> str:
    if not r.get("available"):
        if r.get("asked"):
            body = (f"This box doesn't count {say.quoted(r['asked'], 60)}. It counts "
                    + ", ".join(r.get("sites") or []) + ".")
        else:
            body = f"{r.get('site') or 'That site'}: {r.get('note') or 'nothing is stored for that week yet.'}"
        return say.answer(body, say.ask_next(("website.detail", "How did my website do last week?"),
                                             ("core.brief", "What needs me today?")))
    host = str(r["site"]).removeprefix("www.")
    span = f"{say.day_words(r.get('from'))} to {say.day_words(r.get('to'))}"
    head = [f"{say.n(r['visitors'])} people visited" + (f", {say.change(r['visitors_change'])}"
                                                      if say.change(r.get("visitors_change")) else ""),
            f"{say.n(r['pageviews'])} page views" + (f", {say.change(r['pageviews_change'])}"
                                                    if say.change(r.get("pageviews_change")) else "")]
    if r.get("converted"):
        head.append(f"{say.plural(r['converted'], 'visit')} ended in a conversion")
    came = [f"{label}: {say.n(r['by_source'][k])}" for k, label in _SOURCES if r["by_source"].get(k)]
    if r.get("assistants"):
        came.append("AI answers by name: " + ", ".join(f"{a} {say.n(v)}" for a, v in
                                                       sorted(r["assistants"].items(), key=lambda kv: -kv[1])))
    pages = [f"{host}{p['path']}: {say.plural(p['views'], 'view')}" for p in r.get("top_pages") or []]
    refs = [f"{x['site']}: {say.plural(x['visits'], 'visit')}" for x in r.get("referrers") or []]
    conv = [f"{c['name']}: {say.n(c['count'])}" for c in r.get("conversions") or []]
    out = [f"{x['why'].replace('_', ' ')}: {say.n(x['visits'])}" for x in r.get("left_out") or []]
    others = [x for x in r.get("sites") or [] if x != r["site"]]
    return say.answer(
        f"{host}, {span}, against the 7 days before. People only: the box's own numbers.",
        say.bullets(head),
        say.section("Where people came from:", came),
        say.section("Most visited pages:", pages),
        say.section("Sites that sent people:", refs),
        say.section("Conversions:", conv),
        say.section("Left out as not people:", out),
        say.ask_next(("website.detail", "And the week before?"),
                     *((("website.detail", f"How did {others[0].removeprefix('www.')} do?"),) if others else ()),
                     ("aeo.searches", "What are people searching to find my website?")))


tools.register(
    "detail", title="See your website's week in full",
    fn=detail, machine="website", min_role="read", capability=CAPABILITY, render=_render,
    description="One website's week from the box's own numbers (people only): visitors and page views against the "
                "week before, where people came from (AI answers by name), the most visited pages, the sites that "
                "sent them, conversions, and what was left out as not people. `site` picks one of the box's sites; "
                "`week` 0 is the 7 days to yesterday, 1 the week before, up to 12.",
    args={"site": {"type": "string", "required": False, "description": "One of the box's sites, e.g. example.com."},
          "week": {"type": "integer", "required": False, "description": "0 for the last 7 days, 1 the week before."}})
tools.grant(CAPABILITY)
