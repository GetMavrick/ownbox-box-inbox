"""The plan: every topic this box means to write about, and what became of each.

THE ONLY CODE THAT TOUCHES `seo_plan`. The Topics screen adds rows and asks for one to go now; the
writing job takes the next row and records what happened. Both go through here, so a status the
table's CHECK would refuse is refused here first, with a sentence, instead of as an IntegrityError
inside a worker nobody is watching.

NOTHING HERE REASONS OR SPENDS. Deterministic work uses no Claude (CLAUDE.md, non-negotiable 3).
"""
from __future__ import annotations

from core import state

STATUSES = ("planned", "writing", "published", "refused", "failed")

# Long enough for a real topic or question, short enough that a pasted article is refused.
TOPIC_MAX = 160
QUESTION_MAX = 300


class Rejected(ValueError):
    """The topic was not added. The message is shown to the buyer, so it says what to fix."""


def _clean(text, most: int, what: str) -> str:
    text = " ".join(str(text or "").split())
    if len(text) > most:
        raise Rejected(f"That {what} is {len(text)} characters. Keep it under {most}.")
    return text


def add(topic: str, question: str = "", *, user_id: str | None = None) -> int:
    """Add a topic to the plan. Returns its id."""
    topic = _clean(topic, TOPIC_MAX, "topic")
    question = _clean(question, QUESTION_MAX, "question")
    if not topic:
        raise Rejected("Type the topic the article should be about.")
    now = state._now()
    with state.connect() as c:
        dup = c.execute("SELECT id FROM seo_plan WHERE lower(topic) = lower(?) "
                        "AND status IN ('planned', 'writing')", (topic,)).fetchone()
        if dup:
            raise Rejected("That topic is already waiting to be written.")
        cur = c.execute("INSERT INTO seo_plan (topic, question, status, created_at, created_by, "
                        "updated_at) VALUES (?,?,'planned',?,?,?)",
                        (topic, question or None, now, user_id, now))
        return int(cur.lastrowid)


def get(plan_id: int) -> dict | None:
    with state.connect() as c:
        r = c.execute("SELECT * FROM seo_plan WHERE id = ?", (int(plan_id),)).fetchone()
    return dict(r) if r else None


def rows() -> list[dict]:
    """Every row, for the screen: waiting ones first (the one asked for now leading), then the
    rest newest first."""
    with state.connect() as c:
        got = c.execute(
            "SELECT * FROM seo_plan ORDER BY "
            "CASE status WHEN 'writing' THEN 0 WHEN 'planned' THEN 1 ELSE 2 END, "
            "CASE WHEN status = 'planned' AND requested_at IS NOT NULL THEN 0 ELSE 1 END, "
            "requested_at, "
            "CASE WHEN status IN ('planned', 'writing') THEN id ELSE -id END").fetchall()
    return [dict(r) for r in got]


def request_now(plan_id: int) -> str | None:
    """"Write and publish now": this row goes ahead of the weekly queue.

    Returns the request's time, or None when the row cannot be asked for: already asked for, being
    written, or live. A second tap therefore changes nothing. The loop takes asked-for rows first
    (`next_up`); the screen never runs the write itself.

    A refused or failed row is sent back to planned, which is what "Try again now" means.
    """
    now = state._now()
    with state.connect() as c:
        cur = c.execute("UPDATE seo_plan SET status = 'planned', refusal = NULL, requested_at = ?, "
                        "updated_at = ? WHERE id = ? AND (status IN ('refused', 'failed') "
                        "OR (status = 'planned' AND requested_at IS NULL))",
                        (now, now, int(plan_id)))
    return now if cur.rowcount > 0 else None


def next_up(limit: int = 1, *, requested_only: bool = False) -> list[dict]:
    """The rows the writing job should take next: asked-for first, oldest first, then the queue."""
    where = "status = 'planned'" + (" AND requested_at IS NOT NULL" if requested_only else "")
    with state.connect() as c:
        got = c.execute(f"SELECT * FROM seo_plan WHERE {where} "
                        "ORDER BY requested_at IS NULL, requested_at, id LIMIT ?",
                        (max(0, int(limit)),)).fetchall()
    return [dict(r) for r in got]


def published_since(iso: str) -> int:
    """How many went live since `iso`. The weekly cap counts these."""
    with state.connect() as c:
        return int(c.execute("SELECT COUNT(*) n FROM seo_plan WHERE status = 'published' "
                             "AND published_at >= ?", (iso,)).fetchone()["n"])


def mark(plan_id: int, status: str, *, slug: str | None = None, url: str | None = None,
         refusal: str | None = None) -> None:
    """Record what the job did with a row.

    `published` stamps `published_at` and clears the request; `refused` and `failed` need a reason,
    because the screen has nothing else to tell the buyer.
    """
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    if status in ("refused", "failed") and not (refusal or "").strip():
        raise ValueError(f"a {status} article needs its reason")
    now = state._now()
    sets = ["status = ?", "updated_at = ?"]
    args: list = [status, now]
    if slug is not None:
        sets.append("slug = ?")
        args.append(slug)
    if url is not None:
        sets.append("url = ?")
        args.append(url)
    if status == "published":
        sets += ["published_at = ?", "requested_at = NULL", "refusal = NULL"]
        args.append(now)
    elif status in ("refused", "failed"):
        sets += ["refusal = ?", "requested_at = NULL"]
        args.append(str(refusal).strip()[:500])
    with state.connect() as c:
        c.execute(f"UPDATE seo_plan SET {', '.join(sets)} WHERE id = ?", (*args, int(plan_id)))
