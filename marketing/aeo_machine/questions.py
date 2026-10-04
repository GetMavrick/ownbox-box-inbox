"""The common questions: what this business's customers ask, counted, and whether an article answers it yet.

OWNER, 2026-10-04 (to WebDev1, relayed by OSDev1; "yes, go" in OSDev6's session): feed the website's survey answers
into the AEO Machine and keep a table of common customer questions. It is #1793 Phase 2's fourth kind of evidence,
"questions your visitors asked", and the table every other kind lands in.

WHERE QUESTIONS COME FROM, each counted on its own:
  * the website's own surveys (PostHog `survey sent` events, the last 90 days, this website only): every text
    answer that is a question;
  * Google Search Console: searches the site showed up for that are phrased as questions, by impressions;
  * the Unified Inbox (D1, ruled yes): the `inbox` column is ready, and the inbox machine provides the questions
    (#1793: "OSDev4 the inbox evidence (his machine)"); until it does, the column stays 0.

ONLY THE QUESTION TRAVELS, NEVER WHO ASKED. Nothing that names a person is read or kept: no distinct id, no address,
no name; an answer with an email address or a long run of digits in it is not a question this table keeps. The
website's /privacy promises survey answers are counted, not traced, and this keeps that promise.

REBUILT, NOT ACCUMULATED. Each refresh replaces the counts with the window's totals, so running it twice never
double-counts, and a question nobody asks any more drops to zero instead of living on.

NOTHING REASONS. Deciding what is a question is a rule (a question mark, or a question word first), not a model call,
so the table costs nothing and the same answers always make the same table.

BEHIND THE LABS SWITCH `aeo_questions` (core/labs.py): off, nothing is read and the tool says so.
"""
from __future__ import annotations

import json as _json
import re
from datetime import datetime, timezone

from core import labs, state
from core.logging import get_logger

from . import plan

log = get_logger(__name__)

LABS = "aeo_questions"
SURVEY_DAYS = 90
SURVEY_LIMIT = 5000
MIN_WORDS, MAX_WORDS, MAX_CHARS = 3, 30, 300
# A question's first word, when it has no question mark (searches never do).
WORDS = frozenset(("how", "what", "whats", "why", "can", "could", "do", "does", "did", "is", "are", "will",
                   "would", "should", "which", "where", "when", "who", "whose", "am", "has", "have"))
_PRIVATE = re.compile(r"@|\d{6,}|https?://", re.I)
_NONWORD = re.compile(r"[^a-z0-9 ]+")


def key(text) -> str:
    """The question, normalised: lowercase words, no punctuation. Two wordings that differ only in case or a mark
    are one question."""
    return " ".join(_NONWORD.sub(" ", str(text or "").lower().replace("'", "")).split())


def is_question(text) -> bool:
    """A question this table keeps: 3 to 30 words, a question mark or a question word first, nothing private."""
    s = " ".join(str(text or "").split())
    if not s or len(s) > MAX_CHARS or _PRIVATE.search(s):
        return False
    words = key(s).split()
    if not (MIN_WORDS <= len(words) <= MAX_WORDS):
        return False
    return s.endswith("?") or words[0] in WORDS


def clean(text) -> str:
    s = " ".join(str(text or "").split())[:MAX_CHARS].rstrip(" .!")
    s = s[:1].upper() + s[1:]
    return s if s.endswith("?") else s + "?"


def from_surveys() -> tuple[dict, str | None]:
    """({key: [question, times]}, why not). Every text answer to this website's surveys that is a question."""
    from core import box_secrets

    from . import posthog
    st = posthog.state()
    if not st["connected"]:
        return {}, "PostHog is not connected"
    hogql = (f"SELECT properties FROM events WHERE event = 'survey sent' AND {posthog._where(SURVEY_DAYS)} "
             f"LIMIT {SURVEY_LIMIT}")
    try:
        rows = posthog._one(posthog.api_host(st["host"]), st["project"], box_secrets.get(posthog.KEY) or "", hogql)
    except Exception as e:                                    # noqa: BLE001 — a source, never the whole refresh
        return {}, str(e)[:200] or type(e).__name__
    out: dict = {}
    for row in rows:
        props = row[0] if isinstance(row, (list, tuple)) and row else row
        if isinstance(props, str):
            try:
                props = _json.loads(props)
            except ValueError:
                continue
        if not isinstance(props, dict):
            continue
        for k, v in props.items():
            # $survey_response, $survey_response_1, $survey_response_<question id>: every answer in the event.
            if str(k).startswith("$survey_response") and isinstance(v, str) and is_question(v):
                got = out.setdefault(key(v), [clean(v), 0])
                got[1] += 1
    return out, None


def from_searches() -> tuple[dict, str | None]:
    """({key: [question, impressions]}, why not). Searches the site showed up for that are phrased as questions."""
    from . import searches
    s = searches.searches()
    if not s.get("ok"):
        return {}, str(s.get("why") or "Search Console is not connected")
    out: dict = {}
    for r in list(s.get("top") or []) + list(s.get("opportunities") or []):
        q = (r or {}).get("query") if isinstance(r, dict) else None
        if q and is_question(q):
            got = out.setdefault(key(q), [clean(q), 0])
            got[1] = max(got[1], int(r.get("impressions") or 0))   # the same search in both lists counts once
    return out, None


def _answering(rows: list) -> dict:
    """{question key: the plan row that answers it}. A live article beats a planned one."""
    rank = {"published": 0, "writing": 1, "planned": 2}
    found: dict = {}
    for r in rows:
        if r.get("status") not in rank:
            continue
        for text in (r.get("question"), r.get("topic"), r.get("title")):
            k = key(text)
            if k and (k not in found or rank[r["status"]] < rank[found[k]["status"]]):
                found[k] = r
    return found


def refresh(*, now: str | None = None) -> dict:
    """Rebuild the table from every source this box can read. Never raises. {"off": True} while labs is off."""
    if not labs.on(LABS):
        return {"off": True}
    survey, why_survey = from_surveys()
    search, why_search = from_searches()
    now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    answering = _answering(plan.rows())
    keys = set(survey) | set(search)
    with state.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute("UPDATE aeo_questions SET survey = 0, searched = 0, asked = inbox")
        for k in keys:
            q = (survey.get(k) or search.get(k))[0]
            n_survey, n_search = (survey.get(k) or [q, 0])[1], (search.get(k) or [q, 0])[1]
            row = answering.get(k)
            c.execute("INSERT INTO aeo_questions (key, question, asked, survey, inbox, searched, plan_id, first_seen, "
                      "last_seen) VALUES (?,?,?,?,0,?,?,?,?) ON CONFLICT(key) DO UPDATE SET question = excluded.question, "
                      "survey = excluded.survey, asked = aeo_questions.inbox + excluded.survey, searched = "
                      "excluded.searched, plan_id = excluded.plan_id, last_seen = excluded.last_seen",
                      (k, q, n_survey, n_survey, n_search, row["id"] if row else None, now, now))
        # A question whose article was planned since it was last seen still links to it.
        for k, r in answering.items():
            c.execute("UPDATE aeo_questions SET plan_id = ? WHERE key = ?", (r["id"], k))
    out = {"questions": len(keys), "from_surveys": len(survey), "from_searches": len(search)}
    if why_survey:
        out["surveys_unavailable"] = why_survey
    if why_search:
        out["searches_unavailable"] = why_search
    log.info("aeo.questions_refreshed", **{k: v for k, v in out.items() if isinstance(v, int)})
    return out


def periodic() -> dict:
    """The worker's daily rebuild."""
    try:
        return refresh()
    except Exception as e:                                    # noqa: BLE001 — a missed day, never a dead worker
        log.warning("aeo.questions_failed", error=f"{type(e).__name__}: {str(e)[:160]}")
        return {"failed": True}


_STATE = {"published": "answered", "writing": "being written", "planned": "planned"}


def table(limit: int = 50) -> list[dict]:
    """The common questions, most asked first, then most searched. Each with where it came from and whether an
    article answers it yet."""
    plans = {r["id"]: r for r in plan.rows()}
    with state.connect() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM aeo_questions WHERE asked > 0 OR searched > 0 ORDER BY asked DESC, searched DESC, key "
            "LIMIT ?", (max(1, min(int(limit), 200)),))]
    out = []
    for r in rows:
        p = plans.get(r.get("plan_id"))
        sources = [name for name, n in (("website survey", r["survey"]), ("inbox", r["inbox"]),
                                        ("Google search", r["searched"])) if n]
        out.append({
            "question": r["question"], "times_asked": r["asked"], "seen_in_search": r["searched"], "sources": sources,
            "status": _STATE.get((p or {}).get("status"), "open"),
            "article": ({"id": p["id"], "title": p.get("title") or p.get("topic"),
                         "url": p.get("url") if p.get("status") == "published" else None} if p else None),
        })
    return out


def ideas(limit: int = 3) -> list[dict]:
    """The open questions most worth an article: nobody has planned one, ranked by how often people asked, then
    how often the site showed up for it in search."""
    return [q for q in table(200) if q["status"] == "open"][:max(1, int(limit))]
