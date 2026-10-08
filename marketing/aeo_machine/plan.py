"""The plan: every topic this box means to write about, and what became of each.

THE ONLY CODE THAT TOUCHES `seo_plan`. The Topics screen adds rows, puts one next, moves one up or
takes one off; an approved plan sets the order; the writing job takes the next row (QUEUE) and records
what happened. Both go through here, so a status the
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
    from . import site as _site                         # the site it is planned for (#1793 F5 1.2, migration 58)
    host = _site.site() or None
    with state.connect() as c:
        dup = c.execute("SELECT id FROM seo_plan WHERE lower(topic) = lower(?) "
                        "AND status IN ('planned', 'writing')", (topic,)).fetchone()
        if dup:
            raise Rejected("That topic is already waiting to be written.")
        cur = c.execute("INSERT INTO seo_plan (topic, question, status, created_at, created_by, "
                        "updated_at, site) VALUES (?,?,'planned',?,?,?,?)",
                        (topic, question or None, now, user_id, now, host))
        return int(cur.lastrowid)


def get(plan_id: int) -> dict | None:
    with state.connect() as c:
        r = c.execute("SELECT * FROM seo_plan WHERE id = ?", (int(plan_id),)).fetchone()
    return dict(r) if r else None


# THE WRITING ORDER, in one place, so the job, the screen and the connector can never disagree about what is next.
# Asked-for rows first ("Write this next", a rewrite), oldest ask first; then the owner's own order (`queue_order`,
# migration 59: a topic moved up, or a plan he approved); then everything else, oldest first.
QUEUE = "requested_at IS NULL, requested_at, queue_order IS NULL, queue_order, id"


def rows() -> list[dict]:
    """Every row, for the screen: the one being written, then the planned ones in the order they will be written,
    then the rest newest first."""
    with state.connect() as c:
        got = c.execute(
            "SELECT * FROM seo_plan ORDER BY "
            "CASE status WHEN 'writing' THEN 0 WHEN 'planned' THEN 1 ELSE 2 END, "
            "CASE WHEN status = 'planned' THEN requested_at IS NULL END, "
            "CASE WHEN status = 'planned' THEN requested_at END, "
            "CASE WHEN status = 'planned' THEN queue_order IS NULL END, "
            "CASE WHEN status = 'planned' THEN queue_order END, "
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


# AN UNPUBLISHED ARTICLE IS A STOPPED ROW WITH THIS REASON (owner, 2026-10-04: unpublish, never delete).
# Not a status of its own: `status`'s CHECK is in the table, and changing it is a table rebuild, hard to
# undo, while migration 58 is already promised to F5. The reason is written only by `mark_unpublished`
# and read only by `is_unpublished`, so the day a migration adds the status, these two change and
# nothing else does.
UNPUBLISHED = "Unpublished at your request"


def mark_unpublished(plan_id: int, when: str) -> None:
    """The article is off the website, kept as a draft in Sanity. Its slug and URL stay on the row, so a
    rewrite lands at the same address."""
    mark(plan_id, "refused", refusal=f"{UNPUBLISHED} on {when}. It is kept as a draft in Sanity.")


def is_unpublished(row: dict | None) -> bool:
    return bool(row) and row.get("status") == "refused" and str(row.get("refusal") or "").startswith(UNPUBLISHED)


def rewrite(plan_id: int) -> str | None:
    """Send a live (or unpublished) article back to be written again from today's facts, next up, at its
    own address. Returns the request's time, or None when the row isn't one: a second tap changes nothing.

    The slug and URL stay: the publisher patches the article at that address, and the date it first went
    live is kept (publisher.publish never sends publishedAt on a patch)."""
    now = state._now()
    with state.connect() as c:
        cur = c.execute("UPDATE seo_plan SET status = 'planned', refusal = NULL, requested_at = ?, updated_at = ? "
                        "WHERE id = ? AND slug IS NOT NULL AND (status = 'published' OR "
                        "(status = 'refused' AND refusal LIKE ?))",
                        (now, now, int(plan_id), UNPUBLISHED + "%"))
    return now if cur.rowcount > 0 else None


def is_rewrite(row: dict | None) -> bool:
    """A planned row that has been live before: it replaces an article, it does not add one."""
    return bool(row) and row.get("status") == "planned" and bool(row.get("url"))


def next_up(limit: int = 1, *, requested_only: bool = False) -> list[dict]:
    """The rows the writing job should take next, in the writing order (QUEUE)."""
    where = "status = 'planned'" + (" AND requested_at IS NOT NULL" if requested_only else "")
    with state.connect() as c:
        got = c.execute(f"SELECT * FROM seo_plan WHERE {where} ORDER BY {QUEUE} LIMIT ?",
                        (max(0, int(limit)),)).fetchall()
    return [dict(r) for r in got]


# ── the owner's order ────────────────────────────────────────────────────────────────────────────────
# A REWRITE IS NEVER PLACED. It fixes an article already live, so it stays first in line (`rewrite`), and moving
# or removing applies only to topics that have never been an article: planned, with no address.

def _queue(c) -> list[int]:
    """The planned new topics, in the order they will be written."""
    return [int(r["id"]) for r in c.execute(
        f"SELECT id FROM seo_plan WHERE status = 'planned' AND url IS NULL ORDER BY {QUEUE}")]


def _renumber(c, ids: list[int]) -> None:
    """Write `ids` as the queue, first to last. Their asks are folded into the order, so the order is all there is:
    a topic asked for next and then moved down stays where it was put."""
    now = state._now()
    for n, pid in enumerate(ids, 1):
        c.execute("UPDATE seo_plan SET queue_order = ?, requested_at = NULL, updated_at = ? "
                  "WHERE id = ? AND status = 'planned' AND url IS NULL", (n, now, int(pid)))


def queue() -> list[dict]:
    """Every planned row, in the order the writing job will take them (rewrites first)."""
    return next_up(100_000)


def move_up(plan_id: int) -> bool:
    """Swap a planned topic with the one written before it. False when it is already first among new topics, or
    isn't a planned new topic at all, so a second tap on a stale page changes nothing it shouldn't."""
    with state.connect() as c:
        ids = _queue(c)
        try:
            i = ids.index(int(plan_id))
        except ValueError:
            return False
        if i == 0:
            return False
        ids[i - 1], ids[i] = ids[i], ids[i - 1]
        _renumber(c, ids)
    return True


def place(first: list[int]) -> list[int]:
    """Put these planned new topics first, in this order; every other planned topic follows in its current order.
    Returns the whole queue. Raises Rejected, changing nothing, when one isn't a planned new topic."""
    first = [int(x) for x in first]
    if len(set(first)) != len(first):
        raise Rejected("A topic is listed twice.")
    with state.connect() as c:
        ids = _queue(c)
        stray = [x for x in first if x not in ids]
        if stray:
            raise Rejected(f"Article {stray[0]} isn't a planned topic any more, so the order wasn't changed.")
        order = first + [x for x in ids if x not in first]
        _renumber(c, order)
    return order


def remove(plan_id: int) -> dict | None:
    """Take a topic off the plan. Only one that never became an article: planned, held back or failed, with no
    address. A live or unpublished article is never removed here (it is unpublished, never deleted: owner,
    2026-10-04), and a topic from the owner's Airtable is changed there, or the sync would bring it back.
    Returns the removed row, or None when nothing was removed."""
    with state.connect() as c:
        r = c.execute("SELECT * FROM seo_plan WHERE id = ? AND status IN ('planned', 'refused', 'failed') "
                      "AND url IS NULL AND COALESCE(airtable_id, '') = ''", (int(plan_id),)).fetchone()
        if not r:
            return None
        c.execute("DELETE FROM seo_plan WHERE id = ?", (int(plan_id),))
    return dict(r)


def published_since(iso: str) -> int:
    """How many went live since `iso`. The weekly cap counts these."""
    with state.connect() as c:
        return int(c.execute("SELECT COUNT(*) n FROM seo_plan WHERE status = 'published' "
                             "AND published_at >= ?", (iso,)).fetchone()["n"])


def published_times_since(iso: str) -> list[str]:
    """When each one counted by `published_since` went live, oldest first. The job works out from these when the
    week next has room."""
    with state.connect() as c:
        return [str(r["published_at"]) for r in c.execute(
            "SELECT published_at FROM seo_plan WHERE status = 'published' AND published_at >= ? "
            "ORDER BY published_at", (iso,))]


def last_published_at() -> str | None:
    """When the newest article first went live, whatever became of it since: the pace counts from it. A rewrite keeps
    its first date (`mark`), so rewriting never holds back the next new article."""
    with state.connect() as c:
        r = c.execute("SELECT MAX(published_at) t FROM seo_plan WHERE published_at IS NOT NULL").fetchone()
    return str(r["t"]) if r and r["t"] else None


def mark(plan_id: int, status: str, *, slug: str | None = None, url: str | None = None,
         refusal: str | None = None, title: str | None = None) -> None:
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
    if title is not None and str(title).strip():         # the published article's own title
        sets.append("title = ?")
        args.append(str(title).strip()[:300])
    if status == "published":
        # THE DATE IT FIRST WENT LIVE IS KEPT: a rewrite republishes at the same address, and the week's
        # count (published_since) must not take it for a new article. Sanity keeps its publishedAt too.
        sets += ["published_at = COALESCE(published_at, ?)", "requested_at = NULL", "refusal = NULL"]
        args.append(now)
    elif status in ("refused", "failed"):
        sets += ["refusal = ?", "requested_at = NULL"]
        args.append(str(refusal).strip()[:500])
    with state.connect() as c:
        c.execute(f"UPDATE seo_plan SET {', '.join(sets)} WHERE id = ?", (*args, int(plan_id)))
