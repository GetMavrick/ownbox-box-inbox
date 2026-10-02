"""The AEO Machine, offered to the connector: READ ONLY, everything the screens show.

OWNER, 2026-10-02, in OSDev6's session, looking at his connector's eight tools, none of them this
machine's: *"If it doesn't expose enough for a user to actually be in full command of that machine,
then we need to upgrade the MCP immediately."* He agreed to the plan's first step the same morning
(`docs/PLAN_AEO_MACHINE_UPGRADE.md` §1.1, #1793): *"Agree. go."*

SEVEN READS AND THREE PROPOSALS. A buyer's own AI can see what the machine is doing, what it needs,
what it published, why anything stopped, and how the site is doing, the same answers the AEO screens
give. It can also ASK for a topic, a retry or a settings change: each waits in the box's approvals
queue until a person taps Approve (proposals.py). The proposals are `write:proposals`, `act` and up,
under OSDev1's 2026-10-02 seat ruling: a person's own-AI seat is `act` by default.

`read:aeo` IS GRANTED TO EVERY ROLE by the one `tools.grant` line at the bottom (OSDev1's ruling,
#1798): core never names a machine, and an ungranted capability stays invisible to every seat.

NO SECRET EVER LEAVES. Each reader builds its answer from named fields: the Sanity token, the Airtable
and PostHog keys and the Google sign-in are reported as connected or not, never as values.

A MACHINE THAT ISN'T SET UP SAYS SO. PostHog and Search Console not connected answer with the
connector's `NotConfigured`, a typed state, never a row of zeros an assistant would read as fact.

NOTHING HERE REASONS OR CALLS A VENDOR OF ITS OWN. The performance and search readers use the same
cached reads the Performance screen uses, so asking through the connector costs nothing extra.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.connector import tools
from core.logging import get_logger

log = get_logger(__name__)

MACHINE = "aeo"
CAPABILITY = "read:aeo"
MAX_LIMIT = 100

# What each plan status means, in the words the Articles screen uses.
_STATE = {"planned": "planned", "writing": "being written now", "published": "live",
          "refused": "held back", "failed": "did not publish"}


def _clamp(value, default: int, high: int = MAX_LIMIT) -> int:
    try:
        n = int(value if value is not None else default)
    except (TypeError, ValueError):
        return default
    return max(1, min(n, high))


def _article(row: dict) -> dict:
    """One topic as the connector sees it. NAMED FIELDS, never the row, so a column added later for
    another reason is never published by accident."""
    st = row.get("status")
    out = {
        "id": row.get("id"),
        "status": st,
        "state": "next up" if st == "planned" and row.get("requested_at") else _STATE.get(st, st),
        "topic": row.get("topic"),
        "question": row.get("question"),
        "title": row.get("title"),
        "url": row.get("url") if st == "published" else None,
        "published_at": row.get("published_at"),
        "added_at": row.get("created_at"),
    }
    if st in ("refused", "failed"):
        out["why"] = row.get("refusal")
        out["what_to_do"] = ("Reword the question, or remove the topic, on AEO → Articles."
                             if st == "refused" else "Press Try again now on AEO → Articles.")
    return out


def status():
    """Can the machine publish right now, and if not, exactly what is missing."""
    from core import box_tools

    from . import plan, settings
    from .app import missing, writer_installed
    need = list(missing())
    try:
        brain = (box_tools.health() or {}).get("brain") or {}
    except Exception:                                   # noqa: BLE001 — a section, never the answer
        brain = {}
    ai_ready = brain.get("state") == "ok" or brain.get("ok") is True
    # One failed check of the sign-in (`unchecked`) is not a missing account: the next check decides.
    if not ai_ready and brain.get("state") != "unchecked":
        need.insert(0, "a signed-in AI account (System Settings → AI account)")
    try:
        cap = max(0, int(settings.get().get("weekly_cap") or 0))
    except (TypeError, ValueError):
        cap = 0
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    published_week = plan.published_since(since)
    rows = plan.rows()
    count = {s: sum(1 for r in rows if r.get("status") == s) for s in _STATE}
    return {
        "ready": not need and writer_installed() and cap > 0,
        "missing": need,
        "ai": {"state": brain.get("state") or "unknown", "note": brain.get("note")},
        "articles_a_week": cap,
        "paused": cap == 0,
        "published_last_7_days": published_week,
        "left_this_week": max(0, cap - published_week),
        "topics": count,
        "waiting_on_you": count["refused"] + count["failed"],
        "suggestions_waiting_for_ok": _suggestions_waiting(),
        "note": ("The machine publishes on its own, within the weekly number, once nothing is "
                 "missing. Articles that stopped are listed by aeo.articles with the reason."),
    }


def _suggestions_waiting() -> int:
    try:
        from . import proposals
        return proposals.waiting()
    except Exception:                                   # noqa: BLE001 — a count, never the answer
        return 0


def articles(status=None, limit=None):
    """Every topic, waiting ones first, with why any of them stopped."""
    from . import plan
    want = str(status or "").strip().lower()
    if want and want not in _STATE:
        return {"error": f"status must be one of {', '.join(_STATE)}, or left out for all"}
    rows = [r for r in plan.rows() if not want or r.get("status") == want]
    lim = _clamp(limit, 25)
    return {"articles": [_article(r) for r in rows[:lim]], "total": len(rows),
            "note": "waiting topics first (the one asked for now leading), then the rest newest first"}


def article(id=None):
    """One topic in full."""
    from . import plan
    try:
        row = plan.get(int(id))
    except (TypeError, ValueError):
        return {"error": "give the article's id, from aeo.articles"}
    if not row:
        return {"error": f"there is no article with id {id}"}
    return _article(row)


def performance():
    """How the website did over the last 7 days, against the 7 before, from the owner's PostHog."""
    from . import posthog
    p = posthog.performance()
    if not p.get("ok"):
        if p.get("why") == "not_connected":
            return tools.NotConfigured("PostHog is not connected. The owner connects it on AEO → "
                                       "Data sources → PostHog.")
        return {"available": False, "why": p.get("why")}
    if p.get("empty"):
        return {"available": True, "site": p.get("site"), "visits_recorded": False,
                "note": "PostHog is connected and has no page views from this website in the last "
                        "14 days. The PostHog snippet is probably missing from the site."}
    keep = ("site", "visitors", "visitors_change", "views", "views_change", "article_views",
            "article_views_change", "ai_visits", "ai_visits_change")
    out = {"available": True, "visits_recorded": True, **{k: p.get(k) for k in keep}}
    out["top_articles"] = [{"path": a, "views": n} for a, n in p.get("top_articles") or []]
    out["top_sources"] = [{"site": d, "views": n} for d, n in p.get("top_sources") or []]
    out["note"] = ("Changes are percentages against the 7 days before; null means there was nothing "
                   "the week before to compare with. ai_visits are visits referred by AI answer "
                   "engines.")
    return out


def searches():
    """What people search for when the site shows up, and the searches one article could win."""
    from . import searches as sc
    s = sc.searches()
    if not s.get("ok"):
        if s.get("why") == "not_connected":
            return tools.NotConfigured("Google Search Console is not connected. The owner "
                                       "connects it on Settings → Google Search Console.")
        if s.get("why") == "no_property":
            return tools.NotConfigured("Google Search Console is connected but no site is chosen. "
                                       "The owner chooses it on Settings → Google Search Console.")
        return {"available": False, "why": s.get("why")}
    lo, hi = sc.OPPORTUNITY
    return {"available": True, "from": s["start"], "to": s["end"],
            "top_searches": s["top"], "opportunities": s["opportunities"],
            "note": (f"Opportunities are searches where the site shows up at average position "
                     f"{lo:.0f} to {hi:.0f}, most seen first. Google reports about "
                     f"{sc.LAG_DAYS} days late.")}


def current_settings():
    """What the machine writes by: the website, the weekly number, and the buyer's own lists."""
    from . import settings
    s = settings.get()
    out = {k: s.get(k) for k in ("site_url", "weekly_cap")}
    out.update({k: list(v) for k, v in settings.lists().items()})
    out["facts"] = list(settings.facts())
    out["note"] = ("These are the only claims, numbers and words the writer may use, and the words "
                   "and competitors it must never use. The owner changes them on AEO → Settings.")
    return out


def sources():
    """Each data source: connected or not, and for Airtable which table. Never a credential."""
    from core.vendors import google_search_console as gsc

    from . import posthog, sources as src
    san, air, ph = src.sanity_state(), src.airtable_state(), posthog.state()
    try:
        google = gsc.status()
    except Exception:                                   # noqa: BLE001
        google = {}
    return {
        "sanity": {"connected": san["connected"], "required": True, "project": san["project"],
                   "dataset": san["dataset"]},
        "airtable": {"connected": air["connected"], "required": True, "table": air["url"] or None},
        "posthog": {"connected": ph["connected"], "required": False, "recommended": True},
        "google_search_console": {"connected": bool(google.get("connected")),
                                  "site": google.get("property") or None},
        "google_analytics": {"connected": False, "note": "coming soon"},
        "note": "Credentials are never shown. The owner connects each on AEO → Data sources.",
    }


tools.register(
    "status", title="See whether your AEO Machine is ready to publish",
    fn=status, machine=MACHINE, min_role="read", capability=CAPABILITY,
    description="Whether the AEO Machine can publish right now and, if not, exactly what is "
                "missing (AI account, website address, Sanity). Also the weekly number, how many "
                "went out in the last 7 days, and how many topics wait on the owner.")
tools.register(
    "articles", title="See your AEO articles",
    fn=articles, machine=MACHINE, min_role="read", capability=CAPABILITY,
    description="Every topic the AEO Machine plans, is writing, published or stopped, with the live "
                "address of each published one and, for any that stopped, why and what to do.",
    args={"status": {"type": "string", "required": False,
                     "description": "Only one status: planned, writing, published, refused or failed."},
          "limit": {"type": "integer", "required": False,
                    "description": "How many, 1-100. Defaults to 25."}})
tools.register(
    "article", title="Read one AEO article's details",
    fn=article, machine=MACHINE, min_role="read", capability=CAPABILITY,
    description="One topic in full: its question, title, status, live address, and why it stopped "
                "if it did.",
    args={"id": {"type": "integer", "required": True,
                 "description": "The article's id, from aeo.articles."}})
tools.register(
    "performance", title="See how your website is doing",
    fn=performance, machine=MACHINE, min_role="read", capability=CAPABILITY,
    description="The last 7 days against the 7 before, from the owner's PostHog: visitors, page "
                "views, article views, visits from AI answer engines, the most read articles and "
                "the sites that sent visitors.")
tools.register(
    "searches", title="See what people search to find your website",
    fn=searches, machine=MACHINE, min_role="read", capability=CAPABILITY,
    description="From Google Search Console, the 28 days ending 3 days ago: the top searches that "
                "brought clicks, and the opportunities, searches where the site already shows up at "
                "position 4 to 20.")
tools.register(
    "settings", title="See your AEO Machine's settings",
    fn=current_settings, machine=MACHINE, min_role="read", capability=CAPABILITY,
    description="The website address, articles a week, the facts and numbers the writer may use, "
                "and the words and competitors it must never use.")
tools.register(
    "sources", title="See your AEO Machine's connected data sources",
    fn=sources, machine=MACHINE, min_role="read", capability=CAPABILITY,
    description="Whether Sanity, Airtable, PostHog and Google Search Console are connected, and "
                "which table and site. Never a credential.")
# THE PROPOSALS. Each asks; only a person's Approve on Waiting for you runs it (proposals.py).
from . import proposals  # noqa: E402

tools.register(
    "propose_topic", title="Suggest an AEO topic for you to approve",
    fn=proposals.propose_topic, machine=MACHINE, min_role="act", capability="write:proposals",
    wants_seat=True,
    description="Ask the owner to add a topic to the AEO Machine's plan. It is NOT added: it waits on "
                "Waiting for you until the owner approves or declines. With now=true, an approved topic "
                "goes next and is written and published within minutes.",
    args={"question": {"type": "string", "required": True,
                       "description": "The question the article answers, as a customer would ask it."},
          "topic": {"type": "string", "required": False,
                    "description": "A short topic name. Defaults to the question."},
          "now": {"type": "boolean", "required": False,
                  "description": "Ask for it to be written first, once approved. Defaults to false."}})
tools.register(
    "propose_retry", title="Suggest trying an AEO article again",
    fn=proposals.propose_retry, machine=MACHINE, min_role="act", capability="write:proposals",
    wants_seat=True,
    description="Ask the owner to try again an article that was held back or did not publish. Nothing "
                "is retried until the owner approves. Fix the reason first if it was held back.",
    args={"id": {"type": "integer", "required": True,
                 "description": "The article's id, from aeo.articles."}})
tools.register(
    "propose_setting", title="Suggest an AEO settings change",
    fn=proposals.propose_setting, machine=MACHINE, min_role="act", capability="write:proposals",
    wants_seat=True,
    description="Ask the owner to change one AEO setting: the website (site_url) or articles a week "
                "(weekly_cap) with change=set, or one entry added to or removed from facts, "
                "allowed_numbers, never_words, never_phrases or competitors. Nothing changes until the "
                "owner approves. Connections and keys can't be changed this way.",
    args={"name": {"type": "string", "required": True,
                   "description": "site_url, weekly_cap, facts, allowed_numbers, never_words, "
                                  "never_phrases or competitors."},
          "value": {"type": "string", "required": True,
                    "description": "The new value, or the one entry to add or remove."},
          "change": {"type": "string", "required": False,
                     "description": "set (site_url, weekly_cap), add or remove (the lists). "
                                    "Defaults to set or add."}})

# Every role sees the reads above. Explicit, so a reviewer sees it (core/connector/tools.py grant()).
tools.grant(CAPABILITY)
