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

from core.connector import prompts, tools
from core.connector import words as say
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
    # ASK THE BRAIN, NOT THE HEARTBEAT (the connector strike's audit, 2026-10-02). The watchdog's beat is
    # minutes old at best and was wrong for a whole box until #1820: a box signed in to Claude read "no AI
    # key" there, so this said the AI account was missing while the box was drafting. `can_think()` is the
    # brain's own answer (the saved sign-in, a key, or ChatGPT), and costs nothing. The beat still counts
    # when it saw the AI actually FAIL, which can_think() cannot know (an expired sign-in, say).
    try:
        from core import brain as _brain
        can, why = _brain.can_think()
    except Exception as e:                              # noqa: BLE001 — unknown is not ready
        can, why = False, f"the box couldn't check its AI account ({type(e).__name__})"
    # One failed check of the sign-in (`unchecked`, #1820) is not a failure: its ok is None, and the next check decides.
    failing = brain.get("state") == "fail" or brain.get("ok") is False
    ai_ready = can and not failing
    if not can:
        need.insert(0, "a signed-in AI account (System Settings → AI account)")
    elif failing:
        need.insert(0, "an AI account that answers: " + (brain.get("note") or "the box's last check of it failed"))
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
        "ai": ({"state": "ok", "note": f"ready ({why})"} if ai_ready else
               {"state": brain.get("state") or "not_ready", "note": brain.get("note") or why}),
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


# ── ANSWERS IN WORDS (core/connector/words.py) ────────────────────────────────────────────────────
# Owner, 2026-10-02, handed his own Morning Review as stored fields by his AI: *"This is not an AI business
# machine. This is a dumb box."* Each read below answers the way its screen reads, with the screen's full link,
# then what to ask next and what the machine can start. An article's number stays in its line, in brackets,
# because the AI needs it to read or retry that article and some apps hand the AI only this text.
_WAITING_ON_YOU = ("refused", "failed")


def _page(name: str) -> str:
    """The screen's full address, read from app.py when an answer is written: app.py is the web process's (its
    menu rows register on import), and this file loads in the worker too."""
    from . import app
    return say.link(getattr(app, name))


def _topic_name(a: dict) -> str:
    return say.quoted(a.get("title") or a.get("topic") or a.get("question") or "Untitled", 90)


def _article_line(a: dict) -> str:
    st = a.get("status")
    bits = [_topic_name(a)]
    if st == "published":
        when = say.day_words(str(a.get("published_at") or "")[:10]) if a.get("published_at") else ""
        bits.append("live" + (f" since {when}" if when else "") + (f" at {a['url']}" if a.get("url") else ""))
    elif st in _WAITING_ON_YOU:
        bits.append(f"{a.get('state')}: {say.plain(a.get('why') or 'no reason was kept').rstrip('.')}. "
                    f"{say.plain(a.get('what_to_do') or '')}".strip())
    else:
        bits.append(str(a.get("state") or st or ""))
    line = ", ".join(b for b in bits if b)
    return line + (f" (article {a['id']})" if a.get("id") is not None else "")


def _render_status(r: dict) -> str:
    if r.get("ready"):
        head = "Your AEO Machine is ready, and publishes on its own within its weekly number."
    elif r.get("paused"):
        head = "Your AEO Machine is paused: articles a week is set to 0."
    else:
        head = "Your AEO Machine can't publish yet."
    missing = [say.plain(m) for m in r.get("missing") or []]
    cap = int(r.get("articles_a_week") or 0)
    lines = []
    if cap:
        lines.append(f"Published in the last 7 days: {say.n(r.get('published_last_7_days') or 0)} of {cap} a week, "
                     f"{say.n(r.get('left_this_week') or 0)} left this week.")
    topics = r.get("topics") or {}
    words = {"planned": "planned", "writing": "being written now", "published": "live",
             "refused": "held back", "failed": "did not publish"}
    counted = [f"{say.n(topics[k])} {w}" for k, w in words.items() if topics.get(k)]
    if counted:
        lines.append("Topics: " + ", ".join(counted) + ".")
    waiting = int(r.get("waiting_on_you") or 0)
    if waiting:
        lines.append(f"{say.plural(waiting, 'article waits', 'articles wait')} on you: {_page('TOPICS')}")
    asked = int(r.get("suggestions_waiting_for_ok") or 0)
    if asked:
        lines.append(f"{say.plural(asked, 'suggestion waits', 'suggestions wait')} for your OK on Approvals: "
                     f"{say.link(say.APPROVALS)}")
    starts = [(f"{MACHINE}.propose_retry", say.approve_line("Try again an article that stopped"))] if waiting else []
    starts.append((f"{MACHINE}.propose_topic", say.approve_line("Add a topic for the next article")))
    if r.get("paused"):
        starts.insert(0, (f"{MACHINE}.propose_setting", say.approve_line("Set how many articles a week to write")))
    return say.answer(
        head, say.section("It needs:", missing), say.bullets(lines),
        say.ask_next((f"{MACHINE}.articles", "Which articles have we published?"),
                     (f"{MACHINE}.performance", "How is my website doing?"),
                     (f"{MACHINE}.searches", "What should we write next?")),
        say.can_start(*starts))


def _render_articles(r: dict) -> str:
    if r.get("error"):
        return say.answer(say.plain(r["error"]) + ".", say.ask_next((f"{MACHINE}.articles", "Show me every article")))
    arts = [a for a in r.get("articles") or [] if isinstance(a, dict)]
    total = int(r.get("total") or len(arts))
    stuck = [a for a in arts if a.get("status") in _WAITING_ON_YOU]
    rest = [a for a in arts if a.get("status") not in _WAITING_ON_YOU]
    parts = [f"{say.plural(total, 'topic')} in the plan" + (f", showing {len(arts)}." if len(arts) < total else ".")
             if total else "No topics are in the plan yet.",
             say.section("Waiting on you:", [_article_line(a) for a in stuck]),
             say.section("The rest, newest first:" if stuck else "Newest first:", [_article_line(a) for a in rest]),
             f"Every article: {_page('TOPICS')}"]
    starts = [(f"{MACHINE}.propose_retry", say.approve_line(f"Try again {_topic_name(stuck[0])}"))] if stuck else []
    starts.append((f"{MACHINE}.propose_topic", say.approve_line("Add a topic for the next article")))
    first = (stuck or rest or [{}])[0]
    return say.answer(
        *parts,
        say.ask_next((f"{MACHINE}.article", f"Tell me more about {_topic_name(first)}" if first else
                      "Tell me more about one article"),
                     (f"{MACHINE}.performance", "Which articles are read most?"),
                     (f"{MACHINE}.status", "Is the AEO Machine ready to publish?")),
        say.can_start(*starts))


def _render_article(r: dict) -> str:
    if r.get("error"):
        return say.answer(say.plain(r["error"]) + ".", say.ask_next((f"{MACHINE}.articles", "Show me every article")))
    lines = [_article_line(r)]
    if r.get("question"):
        lines.append(f"The question it answers: {say.quoted(r['question'], 200)}")
    if r.get("added_at"):
        lines.append(f"Added {say.day_words(str(r['added_at'])[:10])}.")
    stuck = r.get("status") in _WAITING_ON_YOU
    return say.answer(
        say.bullets(lines), f"On the Articles screen: {_page('TOPICS')}",
        say.ask_next((f"{MACHINE}.articles", "Which other articles are waiting?"),
                     (f"{MACHINE}.performance", "Which articles are read most?")),
        say.can_start((f"{MACHINE}.propose_retry", say.approve_line("Try it again"))) if stuck else "")


def _render_performance(r: dict) -> str:
    site = str(r.get("site") or "your website")
    if not r.get("available"):
        body = f"Your website numbers could not be read just now. {say.plain(r.get('why') or '').rstrip('.')}".strip()
    elif not r.get("visits_recorded"):
        body = (f"PostHog is connected and has no page views from {site} in the last 14 days. The PostHog snippet is "
                f"probably missing from the site: {_page('SOURCES')}")
    else:
        def line(value, noun, pct):
            moved = say.change(pct)
            return f"{say.n(value or 0)} {noun}" + (f", {moved}" if moved else "")
        lines = [line(r.get("visitors"), "people visited", r.get("visitors_change")),
                 line(r.get("views"), "page views", r.get("views_change")),
                 line(r.get("article_views"), "article views", r.get("article_views_change"))]
        if r.get("ai_visits"):
            lines.append(line(r.get("ai_visits"), "visits came from AI answers such as ChatGPT",
                              r.get("ai_visits_change")))
        host = site.removeprefix("https://").removeprefix("http://").rstrip("/")
        top = [f"{host}{a.get('path')}: {say.plural(a.get('views') or 0, 'view')}"
               for a in r.get("top_articles") or [] if isinstance(a, dict) and a.get("path")]
        sources = [f"{s.get('site')}: {say.plural(s.get('views') or 0, 'view')}"
                   for s in r.get("top_sources") or [] if isinstance(s, dict) and s.get("site")]
        body = say.answer(f"{host}, the last 7 days against the 7 before:", say.bullets(lines),
                          say.section("Most read articles:", top), say.section("Sites that sent visitors:", sources),
                          f"The Performance screen: {_page('PERFORMANCE')}")
    return say.answer(
        body,
        say.ask_next((f"{MACHINE}.searches", "What are people searching to find my website?"),
                     (f"{MACHINE}.articles", "Which articles have we published?"),
                     ("morning_review.report_day", "What happened on my box yesterday?")),
        say.can_start((f"{MACHINE}.propose_topic", say.approve_line("Write an article on your top search"))))


def _render_searches(r: dict) -> str:
    if not r.get("available"):
        body = f"Your searches could not be read just now. {say.plain(r.get('why') or '').rstrip('.')}".strip()
        return say.answer(body, say.ask_next((f"{MACHINE}.sources", "Which data sources are connected?")))

    def line(q: dict, clicks: bool) -> str:
        bits = [say.quoted(q.get("query"), 80)]
        if clicks:
            bits.append(say.plural(q.get("clicks") or 0, "click"))
        bits.append(f"seen {say.plural(q.get('impressions') or 0, 'time')}")
        if q.get("position"):
            bits.append(f"average position {say.n(round(float(q['position']), 1))}")
        return ", ".join(bits)

    top = [line(q, True) for q in r.get("top_searches") or [] if isinstance(q, dict)]
    opps = [line(q, False) for q in r.get("opportunities") or [] if isinstance(q, dict)]
    span = f"{say.day_words(r.get('from'))} to {say.day_words(r.get('to'))}" if r.get("from") else "the last 28 days"
    best = next((q.get("query") for q in r.get("opportunities") or [] if isinstance(q, dict) and q.get("query")), "")
    return say.answer(
        f"From Google Search Console, {span} (Google reports a few days late).",
        say.section("Searches that brought clicks:", top) or "No search brought a click in that time.",
        say.section("Searches one article could win, where the site already shows up on page one or two:", opps),
        say.ask_next((f"{MACHINE}.performance", "How is my website doing?"),
                     (f"{MACHINE}.articles", "Which articles have we published?")),
        say.can_start((f"{MACHINE}.propose_topic",
                       say.approve_line(f"Write an article answering {say.quoted(best, 80)}" if best else
                                        "Write an article on your top search"))))


def _render_settings(r: dict) -> str:
    from .proposals import LISTS, SCALARS
    lines = [f"{SCALARS['site_url']}: {r.get('site_url') or 'not set yet'}",
             f"{SCALARS['weekly_cap']}: {say.n(r.get('weekly_cap') or 0)}"]
    for key, label in LISTS.items():
        items = [str(x) for x in r.get(key) or []]
        lines.append(f"{label}: " + ("; ".join(items[:10]) + (f"; and {len(items) - 10} more" if len(items) > 10 else "")
                                     if items else "none yet"))
    return say.answer(
        say.section("What your AEO Machine writes by:", lines),
        f"These are the only claims, numbers and words the writer may use. Change them on {_page('SETTINGS')}",
        say.ask_next((f"{MACHINE}.status", "Is the AEO Machine ready to publish?"),
                     (f"{MACHINE}.sources", "Which data sources are connected?")),
        say.can_start((f"{MACHINE}.propose_setting", say.approve_line("Change any of these"))))


def _render_sources(r: dict) -> str:
    def on(d) -> str:
        return "connected" if (d or {}).get("connected") else "not connected"
    san, air, ph = r.get("sanity") or {}, r.get("airtable") or {}, r.get("posthog") or {}
    gsc = r.get("google_search_console") or {}
    lines = [f"Sanity, where articles are published (needed): {on(san)}"
             + (f", project {san['project']}" if san.get("project") else ""),
             f"Airtable, the plan's table (needed): {on(air)}",
             f"PostHog, your website's visits (recommended): {on(ph)}",
             f"Google Search Console, what people search: {on(gsc)}"
             + (f", for {gsc['site']}" if gsc.get("site") else ""),
             "Google Analytics: coming soon"]
    return say.answer(
        say.section("Your AEO Machine's data sources:", lines),
        f"Keys are never shown. Connect each one on {_page('SOURCES')}",
        say.ask_next((f"{MACHINE}.status", "Is the AEO Machine ready to publish?"),
                     (f"{MACHINE}.performance", "How is my website doing?"),
                     (f"{MACHINE}.searches", "What are people searching to find my website?")))


tools.register(
    "status", title="See whether your AEO Machine is ready to publish",
    fn=status, machine=MACHINE, min_role="read", capability=CAPABILITY, render=_render_status,
    description="Whether the AEO Machine can publish right now and, if not, exactly what is "
                "missing (AI account, website address, Sanity). Also the weekly number, how many "
                "went out in the last 7 days, and how many topics wait on the owner.")
tools.register(
    "articles", title="See your AEO articles",
    fn=articles, machine=MACHINE, min_role="read", capability=CAPABILITY, render=_render_articles,
    description="Every topic the AEO Machine plans, is writing, published or stopped, with the live "
                "address of each published one and, for any that stopped, why and what to do.",
    args={"status": {"type": "string", "required": False,
                     "description": "Only one status: planned, writing, published, refused or failed."},
          "limit": {"type": "integer", "required": False,
                    "description": "How many, 1-100. Defaults to 25."}})
tools.register(
    "article", title="Read one AEO article's details",
    fn=article, machine=MACHINE, min_role="read", capability=CAPABILITY, render=_render_article,
    description="One topic in full: its question, title, status, live address, and why it stopped "
                "if it did.",
    args={"id": {"type": "integer", "required": True,
                 "description": "The article's id, from aeo.articles."}})
tools.register(
    "performance", title="See how your website is doing",
    fn=performance, machine=MACHINE, min_role="read", capability=CAPABILITY, render=_render_performance,
    description="The last 7 days against the 7 before, from the owner's PostHog: visitors, page "
                "views, article views, visits from AI answer engines, the most read articles and "
                "the sites that sent visitors.")
tools.register(
    "searches", title="See what people search to find your website",
    fn=searches, machine=MACHINE, min_role="read", capability=CAPABILITY, render=_render_searches,
    description="From Google Search Console, the 28 days ending 3 days ago: the top searches that "
                "brought clicks, and the opportunities, searches where the site already shows up at "
                "position 4 to 20.")
tools.register(
    "settings", title="See your AEO Machine's settings",
    fn=current_settings, machine=MACHINE, min_role="read", capability=CAPABILITY, render=_render_settings,
    description="The website address, articles a week, the facts and numbers the writer may use, "
                "and the words and competitors it must never use.")
tools.register(
    "sources", title="See your AEO Machine's connected data sources",
    fn=sources, machine=MACHINE, min_role="read", capability=CAPABILITY, render=_render_sources,
    description="Whether Sanity, Airtable, PostHog and Google Search Console are connected, and "
                "which table and site. Never a credential.")
# THE PROPOSALS. Each asks; only a person's Approve on Waiting for you runs it (proposals.py).
from . import proposals  # noqa: E402


def _render_proposal(r: dict) -> str:
    """What now waits on Approvals, or why nothing was asked, in the same words as every machine's proposals."""
    return say.proposal(r, (f"{MACHINE}.articles", "Which articles are planned?"),
                        (f"{MACHINE}.status", "Is the AEO Machine ready to publish?"))


tools.register(
    "propose_topic", title="Suggest an AEO topic for you to approve",
    fn=proposals.propose_topic, machine=MACHINE, min_role="act", capability="write:proposals",
    render=_render_proposal,
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
    render=_render_proposal,
    wants_seat=True,
    description="Ask the owner to try again an article that was held back or did not publish. Nothing "
                "is retried until the owner approves. Fix the reason first if it was held back.",
    args={"id": {"type": "integer", "required": True,
                 "description": "The article's id, from aeo.articles."}})
tools.register(
    "propose_setting", title="Suggest an AEO settings change",
    fn=proposals.propose_setting, machine=MACHINE, min_role="act", capability="write:proposals",
    render=_render_proposal,
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


# ── WHAT THE AEO MACHINE ADDS TO THE MORNING REVIEW'S ANSWER, AND TO THE READY-MADE ASKS ────────────────
# core renders the review and names no machine, so this machine says what its segment lets the owner ask next
# and start (core/connector/words.py `review_offers`), under the key report.py registers it as.
def _review_offers(segment: dict) -> dict:
    stuck = bool(segment.get("needs_you"))
    asks = [(f"{MACHINE}.performance", "How is my website doing?"),
            (f"{MACHINE}.searches", "What should we write next?")]
    starts = ([(f"{MACHINE}.propose_retry", say.approve_line("Try again the article that stopped"))] if stuck else
              [(f"{MACHINE}.propose_topic", say.approve_line("Write the next article on your top search"))])
    return {"ask": asks, "start": starts}


from .report import MACHINE as _SEGMENT  # noqa: E402 — the review's key for this machine, "aeo_machine"

say.review_offers(_SEGMENT, _review_offers)

prompts.register(
    "what_to_write_next", title="What should we write next", machine=MACHINE, prefer=("core.ask",),
    description="The next article worth writing: the searches you nearly win, what is already planned, and why.",
    ask="What should we write next? Pick the one or two articles most worth writing, from the searches my site "
        "nearly wins and what is already planned or published, and say why each would bring people.",
    uses=[(f"{MACHINE}.searches", "the searches that bring clicks, and the ones one article could win"),
          (f"{MACHINE}.articles", "what is already planned, written or live, so nothing is written twice"),
          (f"{MACHINE}.performance", "which articles people read most")])
prompts.use("websites", f"{MACHINE}.performance", "visitors, page views, AI answer visits and the most read "
                                                  "articles on the site, the last 7 days against the 7 before")
prompts.use("websites", f"{MACHINE}.searches", "what people search when the site shows up")
prompts.use("morning_brief", f"{MACHINE}.status", "whether the AEO Machine is publishing, and what waits on me")
prompts.use("what_needs_me", f"{MACHINE}.articles", "articles that stopped, why, and what each needs")
