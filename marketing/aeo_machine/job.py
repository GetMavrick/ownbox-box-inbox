"""The writing job: a planned topic becomes a live article, within the box's weekly cap.

ONE ROW, ONE ARTICLE, ONE TICK. `periodic()` publishes at most one article per call. Writing one
is a reasoning call that can take a minute, and periodics share the worker's one background
thread, so four articles in one tick would hold every other machine's clock for four minutes.
The tick itself is cheap when there is nothing to do (one query), so it can run every minute:
"Write and publish now" is then honoured within about a minute, and a day-one box with four
planned topics is fully published within minutes of being set up.

"WRITE AND PUBLISH NOW" IS A REQUEST, NOT A CALL. The Topics screen runs `plan.request_now`,
which moves the row to the front of the queue. The click never does the write itself: a minute
inside a web request would time out on a phone, and two taps would pay for the same article twice.

THE ROW RECORDS EVERY OUTCOME. `planned` → `writing` → `published` | `refused` | `failed`. A
refusal carries the guard's reasons word for word, so the Topics screen can tell the owner exactly
what to change. The loop never retries a `refused` or `failed` row on its own: each attempt costs
a reasoning call, and a person decides whether to spend another one ("try again" on the screen).

WHAT THIS FILE DOES NOT DO. It never calls `brain.think()` (only `writer.py` reasons, and a test
holds that line), never touches `seo_plan` except through `plan.py` (the only code that does),
and never reads a setting except through `settings.py`, so the AEO settings screen is the only
place a value lives. It assumes the box's ONE worker: periodics run one at a time on its thread,
so a row cannot be taken twice.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.logging import get_logger

from . import guard, plan, portable_text, publisher, settings, writer

log = get_logger(__name__)

WEEK = timedelta(days=7)
# A write takes about a minute. A row still `writing` after this long was interrupted, almost
# always by a release restarting the worker mid-write, and nothing else will ever pick it up.
STALE_WRITING = timedelta(minutes=30)
INTERRUPTED = "interrupted, press Write and publish now"
# Each try is one lookup on the site. Past this many articles on one question, stop and say so.
MAX_SLUG_TRIES = 50


def _now() -> datetime:
    return datetime.now(timezone.utc)


def room(*, now: datetime | None = None) -> int:
    """How many more articles this box may publish before the rolling week resets.

    The weekly cap is the owner's number, so an asked-for row waits for room like any other.
    """
    try:
        cap = int(settings.get().get("weekly_cap") or 0)
    except (TypeError, ValueError):
        cap = 0
    return max(0, cap - plan.published_since(((now or _now()) - WEEK).isoformat()))


def _when(iso) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def release_stale(*, now: datetime | None = None) -> int:
    """Fail every row stuck in `writing` past STALE_WRITING, so a person can send it again.

    WHY IT EXISTS. Every release restarts the worker, and we release several times a day. A
    restart mid-write leaves the row at `writing`, which the job never takes and the screen cannot
    request, so without this it would sit there forever. Failing it, rather than re-queueing it,
    keeps the rule that nothing spends a second reasoning call without a click.
    """
    cutoff = (now or _now()) - STALE_WRITING
    n = 0
    for r in plan.rows():
        if r.get("status") != "writing":
            continue
        since = _when(r.get("updated_at"))
        if since is None or since < cutoff:
            plan.mark(r["id"], "failed", refusal=INTERRUPTED)
            n += 1
    if n:
        log.warning("aeo.topic_interrupted", count=n)
    return n


def _slug_for(row: dict, question: str) -> str:
    """The row's slug: the one it already has, or one minted once from the question.

    The slug is the article's identity. The writer derives one from the model's title, which can
    change between attempts, so a retried row would publish at a second URL and orphan the first.
    The job stores it on the row as it claims it, BEFORE the write, and every attempt and every
    later edit lands on that one URL.

    A MINTED SLUG IS NEVER SOMEONE ELSE'S (OSDev1, #1561). The publisher creates-or-PATCHES by
    slug, so a second "How do I start?" (or "How do I start" without the mark) would have rewritten
    the live article under a new row. So a fresh slug skips any slug another row holds, whatever
    its status, and any slug already live on the site (put there by hand, or by a row since
    deleted), taking `-2`, `-3` and so on. A row that already holds a slug keeps it: that live
    article is its own, and landing on it again is the point.
    """
    kept = (row.get("slug") or "").strip()
    if kept:
        return kept
    base = portable_text.article_slug(question)
    taken = {(r.get("slug") or "").strip() for r in plan.rows() if r.get("id") != row["id"]}
    for n in range(1, MAX_SLUG_TRIES + 1):
        slug = base if n == 1 else f"{base}-{n}"
        if slug not in taken and not publisher.find_by_slug(slug):
            return slug
    raise RuntimeError(f"no free address for {base!r} after {MAX_SLUG_TRIES} tries")


def _write_and_publish(row: dict) -> dict:
    """Run one row to its end state. Never raises: every outcome lands on the row."""
    row_id = row["id"]
    question = (row.get("question") or row.get("topic") or "").strip()
    if not question:
        plan.mark(row_id, "failed", refusal="this topic has no question to answer")
        return {"id": row_id, "status": "failed"}

    try:
        slug = _slug_for(row, question)                # may ask the site; nothing spent yet
    except Exception as e:                                   # noqa: BLE001 — recorded, not raised
        why = f"{type(e).__name__}: {str(e)[:300]}"
        plan.mark(row_id, "failed", refusal=why)
        log.warning("aeo.topic_failed", id=row_id, error=why)
        return {"id": row_id, "status": "failed", "refusal": why}

    # Both CLEANED by settings: a list saved as text is a list of entries, never its characters
    # (review item 9).
    lists = settings.lists()

    # THE ADDRESS IS CHECKED BEFORE ANY SPEND (REVIEW F-B, OSDev9). It comes from the owner's
    # question, not from the draft, so a banned word or a competitor in the question is in every
    # draft's web address. Without this, each attempt paid for an article that could never go
    # live, and the refusal named a word the owner could not find in the article.
    bad = publisher.address_refusals(slug, lists=lists)
    if bad:
        found = ", ".join(sorted({f'"{r.found}"' for r in bad}))
        why = (f"{found} would be in this article's web address, which comes from its question. "
               "Reword the question and add it again.")
        plan.mark(row_id, "refused", slug=slug, refusal=why)
        log.info("aeo.topic_refused_address", id=row_id, rules=sorted({r.rule for r in bad}))
        return {"id": row_id, "status": "refused", "refusal": why}

    plan.mark(row_id, "writing", slug=slug)            # the claim, and the URL, before any spend
    try:
        fields = writer.write(question, facts=settings.facts(), lists=lists,
                              job_id=f"aeo:{row_id}", slug=slug)
        fields["slug"] = slug                          # the row's URL wins over the draft's title
        # The publisher runs the guard again, on the same lists. That is not redundant: it is the
        # door every article passes through, whoever calls it.
        result = publisher.publish(lists=lists, **fields)
    except guard.GuardRefused as e:
        reasons = "; ".join(f'{r.rule}: "{r.found}" ({r.why})' for r in e.refusals)
        plan.mark(row_id, "refused", refusal=reasons)
        log.info("aeo.topic_refused", id=row_id, rules=sorted({r.rule for r in e.refusals}))
        return {"id": row_id, "status": "refused", "refusal": reasons}
    except Exception as e:                                   # noqa: BLE001 — recorded, not raised
        why = f"{type(e).__name__}: {str(e)[:300]}"
        plan.mark(row_id, "failed", refusal=why)
        log.warning("aeo.topic_failed", id=row_id, error=why)
        return {"id": row_id, "status": "failed", "refusal": why}

    plan.mark(row_id, "published", slug=result["slug"], url=result["url"])
    # A ping needs an absolute URL. `is_configured` requires one, so this guard is belt and braces.
    # And a ping NEVER undoes a publish: the article is live and the row says so, so a failure
    # here is a slower crawl, logged, not an exception out of the worker's tick.
    if str(result["url"]).startswith("http"):
        try:
            publisher.ping_indexnow([result["url"]])
        except Exception as e:                               # noqa: BLE001 — see above
            log.warning("aeo.indexnow_raised", id=row_id,
                        error=f"{type(e).__name__}: {str(e)[:160]}")
    log.info("aeo.topic_published", id=row_id, slug=result["slug"], created=result["created"])
    return {"id": row_id, "status": "published", "url": result["url"], "slug": result["slug"]}


def periodic() -> dict:
    """Worker entry: publish the next topic (asked-for first, then oldest), if the box is set up
    and has room this week.

    Dark until configured, and honest about it: an unconfigured box returns the reason and
    touches no row, so a fresh box from the snapshot does nothing and reports why.
    """
    release_stale()
    ready, why = publisher.is_configured()
    if not ready:
        return {"skipped": "not_configured", "why": why}
    if room() <= 0:
        return {"skipped": "weekly_cap"}
    nxt = plan.next_up(1)
    if not nxt:
        return {"skipped": "nothing_planned"}
    return _write_and_publish(nxt[0])
