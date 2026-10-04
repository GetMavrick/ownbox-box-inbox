"""The AEO Machine in the Morning Review: what it published, and what is waiting on the owner.

OSDev1, 2026-09-29, relaying the owner through WebDev2: the Morning Review shows the most important
information from every active machine on each client box, and the AEO Machine reported nothing.
The owner's rule for every line of it: *"If a line doesn't have data, it should not be displayed."*

So this reports OUTCOMES ONLY, and only when there are some:

  * **Published**: each article that went live that day, by its title, with the search it answers
    (the question it was written for). A row published before titles were kept is named by its
    question alone.
  * **Needs you**: every article the machine could not publish and that is still waiting for the
    owner, refused by the guard or failed on the way, with a link to Articles, where the reason is
    and where "Try again now" lives.
  * **What an article brought** (#1793 Phase 2.4): the live article that brought the most people in
    the seven days ending that day, and how many came from AI answers, from the box's own website
    store. No line when none brought anyone, or the box keeps no website numbers.

A QUIET DAY IS AN EMPTY REPORT: no headline number, no lines. There is no "0 published", no "has not
run yet", and no line for settings a new box has not filled in; a machine with nothing to say says
nothing, and the review leaves its section out.

LOCAL DATABASE READS ONLY, like every reporter (core/report.py `register_reporter`): no network, no
model, never slow. The website numbers come from the box's own store (marketing/foundation/seam.py),
never from PostHog or Google directly.
"""
from __future__ import annotations

from core import state
from core.report import register_reporter, window

MACHINE = "aeo_machine"
TITLE = "AEO Machine"
ARTICLES = "/aeo/topics"          # the Articles screen: every row, its reason, and "Try again now"

# The plan table keeps its original name (OSDev1's ruling on #1595: storage names stay).
_TABLE = "seo_plan"


def _has_table(c) -> bool:
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                          (_TABLE,)).fetchone())


def _columns(c) -> set:
    return {r[1] for r in c.execute(f"PRAGMA table_info({_TABLE})")}


def _quoted(text) -> str:
    return "“" + " ".join(str(text or "").split()) + "”"


def _published_line(r: dict) -> str:
    title = " ".join(str(r.get("title") or "").split())
    question = " ".join(str(r.get("question") or r.get("topic") or "").split())
    if title and question and title.lower() != question.lower():
        return f"Published {_quoted(title)}, answering {_quoted(question)}"
    return f"Published {_quoted(title or question)}"


def _stuck_line(r: dict) -> str:
    what = r.get("question") or r.get("topic")
    if r.get("status") == "refused":
        return f"{_quoted(what)} was stopped before publishing. Articles says why"
    return f"{_quoted(what)} could not be published. Articles says why"


def _top_article(live: list[dict], day) -> str:
    """The article that brought the most people in the seven days ending `day`, from the box's own website store
    (#1793 Phase 2.4): "“How much does Botox cost” brought 14 people this week, 3 from ChatGPT". "" when none did,
    or the box keeps no website numbers."""
    try:
        from . import brought
        got = brought.top_of_week(live, day)
    except Exception:                                     # noqa: BLE001 — a reporter never raises
        return ""
    if not got:
        return ""
    r, counts = got
    name = " ".join(str(r.get("title") or r.get("question") or r.get("topic") or "").split())
    return f"{_quoted(name)} brought {brought.words(counts, 'this week')}" if name else ""


def report(day) -> dict:
    """The day's AEO outcomes, or {} when there are none (a fresh box, or a quiet day)."""
    start, end = window(day)
    with state.connect() as c:
        if not _has_table(c):
            return {}
        cols = _columns(c)
        title = "title" if "title" in cols else "NULL AS title"
        published = [dict(r) for r in c.execute(
            f"SELECT {title}, question, topic, url FROM {_TABLE} WHERE status = 'published' "
            "AND published_at >= ? AND published_at < ? ORDER BY published_at, id", (start, end))]
        # WAITING ON THE OWNER, AS OF THE END OF THIS DAY: every row still refused or failed that
        # reached that state before the day closed. It stays until he acts on it, which is what
        # "needs you" means; a row he has since retried or published has left these states.
        stuck = [dict(r) for r in c.execute(
            f"SELECT question, topic, status FROM {_TABLE} WHERE status IN ('refused', 'failed') "
            "AND updated_at < ? ORDER BY updated_at DESC, id DESC", (end,))]
        live = [dict(r) for r in c.execute(
            f"SELECT id, status, {title}, question, topic, url, published_at FROM {_TABLE} "
            "WHERE status = 'published' AND url IS NOT NULL AND published_at < ?", (end,))]
    top = _top_article(live, day)
    if not published and not stuck and not top:
        return {}
    out: dict = {"title": TITLE}
    if published:
        n = len(published)
        out["headline"] = {"value": n, "label": "article published" if n == 1 else "articles published"}
        out["happened"] = [{"text": _published_line(r)} for r in published]
    if top:
        out.setdefault("happened", []).append({"text": top, "href": ARTICLES})
    if stuck:
        out["needs_you"] = [{"text": _stuck_line(r), "href": ARTICLES} for r in stuck]
    return out


register_reporter(MACHINE, TITLE, report)
