"""The AEO Machine's proposals: what a person's own AI may ask for, held until that person taps Approve.

OWNER, 2026-10-02: full command of the machine from Claude (#1793 §1.1, "Agree. go"). OSDev1's seat
ruling the same morning: a person's own-AI seat is `act` by default, so it can "read, draft, propose;
never send, publish or charge (no seat holds act:, every proposal is a tap)."

ONE QUEUE, NOT A NEW ONE. Each proposal goes into core/approvals.py, the queue connected apps already
use: it waits on Approvals and the owner's mobile app is told; Approve runs it once, Decline
changes nothing, and after a week it expires and never runs. Nothing here writes the plan or a setting
on its own: `_run` is reached only from `approvals.decide`, which needs a person.

WHAT IS SHOWN IS WHAT RUNS. The approvals screen lists a proposal's `arguments` row by row, and `_run`
reads those same rows. Nothing is rebuilt at approval, so the owner approves the words he reads.

THE SAME CHECKS AS THE SCREENS, TWICE. A proposal is checked when it is made, so the AI hears at once
that a topic is a duplicate or a number is out of range, and again when it runs, because a week may
have passed and the plan or the settings may have changed.

SIX ACTIONS:
  * add a topic (AEO → Articles, "Add a topic"), optionally next up ("Write and publish now");
  * try a stopped article again (AEO → Articles, "Try again now");
  * change one setting (AEO → Settings): the website, articles a week, or one entry added to or removed
    from a list. Connections and their keys are never proposable: those stay on Data sources;
  * unpublish a live article: off the website, kept as a draft in Sanity, never deleted (owner, 2026-10-04);
  * rewrite a live or unpublished article from today's facts, at its own address (owner, 2026-10-04,
    after two articles went out with an empty fact list);
  * add the facts and numbers the box drafted from the buyer's own website (facts_draft.py; owner, 2026-10-04:
    "where would a business owner put these facts in?"). Drafting runs in the worker; its result waits here.
"""
from __future__ import annotations

import json as _json

from core import approvals, box_settings, state
from core.logging import get_logger

from . import plan, settings

log = get_logger(__name__)

KIND = "aeo_change"
MACHINE = "aeo"
APP = "AEO Machine"
SET_BY = "an approved suggestion"

# What can be proposed, in the Settings screen's words. Connections and keys are left out on purpose.
SCALARS = {"site_url": "Your website", "weekly_cap": "Articles a week"}
LISTS = {"facts": "Facts the writer may state", "allowed_numbers": "Numbers it may use",
         "never_words": "Words it must never use", "never_phrases": "Phrases it must never use",
         "competitors": "Competitors it must never name"}


class Refused(ValueError):
    """The proposal was not made, or could not run. The message says what to fix, in plain words."""


def _who(seat) -> str:
    seat = seat or {}
    return str(seat.get("label") or seat.get("id") or "")


def _short(text: str, most: int = 70) -> str:
    text = str(text)
    return text if len(text) <= most else text[:most - 1].rstrip() + "…"


def _ask(title: str, detail: dict, seat) -> dict:
    a = approvals.propose(KIND, machine=MACHINE, title=title, detail=detail, seat_id=_who(seat))
    repeat = bool(a.get("repeat"))
    log.info("aeo.proposed", approval=a["id"], do=detail.get("do"), repeat=repeat)
    return {"asked": True, "approval": a["id"], "repeat": repeat,
            "text": (f"Already waiting: \"{a['title']}\" is waiting for the owner's OK. Nothing has changed yet."
                     if repeat else
                     f"Asked: \"{a['title']}\" is waiting for the owner, who approves or declines on "
                     "Approvals. Nothing has changed yet. Don't ask again for the same thing.")}


# ── checks shared by proposing and running ─────────────────────────────────────────────────────────

def _check_topic(topic, question) -> tuple[str, str]:
    try:
        t = plan._clean(topic, plan.TOPIC_MAX, "topic")
        q = plan._clean(question, plan.QUESTION_MAX, "question")
    except plan.Rejected as e:
        raise Refused(str(e)) from None
    if not t:
        raise Refused("Give the topic the article should be about.")
    waiting = {(r.get("topic") or "").lower() for r in plan.rows() if r.get("status") in ("planned", "writing")}
    if t.lower() in waiting:
        raise Refused("That topic is already waiting to be written.")
    return t, q


def _stopped(plan_id) -> dict:
    try:
        row = plan.get(int(plan_id))
    except (TypeError, ValueError):
        raise Refused("Give the article's id, from aeo.articles.") from None
    if not row:
        raise Refused(f"There is no article with id {plan_id}.")
    if row.get("status") not in ("refused", "failed"):
        raise Refused("Only an article that was held back or did not publish can be tried again. "
                      f"This one is {row.get('status')}.")
    return row


def _list_now(name: str) -> list[str]:
    return list(settings._as_list(name, settings.get().get(name)))


def _check_setting(name: str, how: str, value) -> dict:
    """-> the arguments to show and run. Uses the Settings screen's own checks (app.py)."""
    from . import app
    try:
        if name == "site_url":
            if how != "set":
                raise Refused("The website is changed with change=set.")
            site, _host = app._site(str(value or ""))
            if not site:
                raise Refused("Give the website's address, like https://example.com.")
            from core import business_context               # the business's website, kept in core (#1957 C5)
            try:
                site = business_context.clean_website(site)
            except ValueError as e:
                raise Refused(str(e)) from e
            return {"setting": SCALARS[name], "change": "set to", "value": site}
        if name == "weekly_cap":
            if how != "set":
                raise Refused("Articles a week is changed with change=set.")
            cap = app._cap(str(value if value is not None else "").strip())
            if cap == "":
                raise Refused(f"Articles a week is a whole number from 0 to {app._CAP_MAX}.")
            return {"setting": SCALARS[name], "change": "set to", "value": cap}
        if name in LISTS:
            if how not in ("add", "remove"):
                raise Refused(f"{LISTS[name]} is changed with change=add or change=remove, one entry at a time.")
            items = app._lines(str(value or ""))
            if len(items) != 1:
                raise Refused("Give exactly one entry. Propose each entry on its own.")
            item, now = items[0], _list_now(name)
            there = item.lower() in {x.lower() for x in now}
            if how == "add" and there:
                raise Refused(f"\"{item}\" is already on {LISTS[name]}.")
            if how == "remove" and not there:
                raise Refused(f"\"{item}\" is not on {LISTS[name]}.")
            if how == "add" and len(now) >= app._LIST_MAX:
                raise Refused(f"{LISTS[name]} is full: it holds {app._LIST_MAX} entries.")
            return {"setting": LISTS[name], "change": how, "value": item}
    except app._Refused as e:
        raise Refused(str(e)) from None
    raise Refused(f"{name!r} can't be suggested. Choose one of: {', '.join([*SCALARS, *LISTS])}.")


# ── the three tools ────────────────────────────────────────────────────────────────────────────────

def propose_topic(question=None, topic=None, now=False, seat=None):
    """Ask the owner to add a topic. A topic left out is the question itself, as on the screen."""
    try:
        t, q = _check_topic(topic or question, question if topic else "")
    except Refused as e:
        return {"asked": False, "error": str(e)}
    first = bool(now) and str(now).lower() not in ("false", "0", "no")
    args = {"topic": t, "question": q or "(none)", "write now": "yes" if first else "no"}
    title = f"Add an AEO topic{', write it now' if first else ''}: {_short(t)}"
    return _ask(title, {"app": APP, "do": "add_topic", "arguments": args}, seat)


def propose_retry(id=None, seat=None):
    """Ask the owner to try a held-back or failed article again."""
    try:
        row = _stopped(id)
    except Refused as e:
        return {"asked": False, "error": str(e)}
    args = {"article": int(row["id"]), "topic": row.get("topic"), "why it stopped": row.get("refusal") or "-"}
    return _ask(f"Try this AEO article again: {_short(row.get('topic') or '')}",
                {"app": APP, "do": "retry", "arguments": args}, seat)


def _article_row(plan_id) -> dict:
    try:
        row = plan.get(int(plan_id))
    except (TypeError, ValueError):
        raise Refused("Give the article's id, from aeo.articles.") from None
    if not row:
        raise Refused(f"There is no article with id {plan_id}.")
    return row


def _live(plan_id) -> dict:
    row = _article_row(plan_id)
    if row.get("status") != "published" or not row.get("slug"):
        raise Refused(f"Only a live article can be unpublished. This one is {row.get('status')}.")
    return row


def _rewritable(plan_id) -> dict:
    row = _article_row(plan_id)
    if not (row.get("slug") and (row.get("status") == "published" or plan.is_unpublished(row))):
        raise Refused("Only a live or unpublished article can be rewritten. "
                      f"This one is {row.get('status')}.")
    if not settings.facts():
        raise Refused("There are no facts for the writer yet, so a rewrite would say no more than the "
                      "first one. Add them on AEO Settings first.")
    return row


def propose_unpublish(id=None, seat=None):
    """Ask the owner to take a live article off the website. Kept as a draft in Sanity, never deleted."""
    try:
        row = _live(id)
    except Refused as e:
        return {"asked": False, "error": str(e)}
    args = {"article": int(row["id"]), "title": row.get("title") or row.get("topic"),
            "address": row.get("url") or "-", "what happens": "Off your website. Kept as a draft in Sanity."}
    return _ask(f"Unpublish this AEO article: {_short(row.get('title') or row.get('topic') or '')}",
                {"app": APP, "do": "unpublish", "arguments": args}, seat)


def propose_rewrite(id=None, seat=None):
    """Ask the owner to have a live or unpublished article written again from today's facts."""
    try:
        row = _rewritable(id)
    except Refused as e:
        return {"asked": False, "error": str(e)}
    args = {"article": int(row["id"]), "title": row.get("title") or row.get("topic"),
            "address": row.get("url") or "-",
            "what happens": "Written again from today's facts and published at the same address."}
    return _ask(f"Rewrite this AEO article from your facts: {_short(row.get('title') or row.get('topic') or '')}",
                {"app": APP, "do": "rewrite", "arguments": args}, seat)


# ── facts drafted from the buyer's website ─────────────────────────────────────────────────────────

DRAFT_INTENT = "aeo_facts_draft"
DRAFT_NS, DRAFT_KEY = "aeo_machine", "facts_draft"     # the last draft's outcome, for the Settings screen


def drafting() -> bool:
    """Is a draft queued or running? Read from the queue itself, so a restarted worker never leaves it stuck."""
    with state.connect() as c:
        return c.execute("SELECT 1 FROM jobs WHERE intent = ? AND status IN ('queued', 'running') LIMIT 1",
                         (DRAFT_INTENT,)).fetchone() is not None


def start_draft(*, by: str = "") -> tuple[bool, str]:
    """Queue one draft. (queued, what to tell the person). Refused while one is running or with no website."""
    import uuid
    site = str(settings.get().get("site_url") or "")
    if not site:
        return False, "Add your website's address on AEO Settings first, so the box knows which site to read."
    if drafting():
        return False, "The box is already reading your website. The facts will wait on Approvals when it's done."
    from core.queue import queue
    queue.enqueue(idempotency_key=f"{DRAFT_INTENT}:{uuid.uuid4().hex[:12]}", intent=DRAFT_INTENT,
                  agent_name="aeo", raw_text=_json.dumps({"by": str(by or "")[:80]}))
    box_settings.put(DRAFT_NS, DRAFT_KEY, {"status": "reading", "site": site}, set_by=by or "aeo")
    return True, (f"Reading {site} now. In a minute or two, the facts it found wait on Approvals for your OK, "
                  "each word for word from your own pages.")


def last_draft() -> dict:
    got = box_settings.get(DRAFT_NS, DRAFT_KEY, default={}) or {}
    return got if isinstance(got, dict) else {}


def do_draft(job: dict) -> dict:
    """The worker's half: read the site, draft, and put what it found on Approvals as one proposal."""
    from . import facts_draft
    by = (_json.loads(job.get("raw_text") or "{}") or {}).get("by") or ""
    site = str(settings.get().get("site_url") or "")
    got = facts_draft.draft(site)
    have = {x.lower() for x in _list_now("facts")}
    new = [f for f in got.get("facts") or [] if f["text"].lower() not in have]
    if not new:
        said = got.get("why") or "Every fact the box found on your website is already on your list."
        box_settings.put(DRAFT_NS, DRAFT_KEY, {"status": "nothing", "site": site, "said": said}, set_by="aeo")
        return {"ok": True, "proposed": 0}
    nums = [n for n in got.get("numbers") or [] if n not in _list_now("allowed_numbers")]
    args = {f"fact {i}": f["text"] for i, f in enumerate(new, 1)}
    if nums:
        args["numbers it may use"] = ", ".join(nums)
    args["read from"] = ", ".join(got.get("pages") or [site])
    a = _ask(f"Add {len(new)} facts from your website for the AEO Machine",
             {"app": APP, "do": "add_facts", "arguments": args}, {"label": by or "the box"})
    box_settings.put(DRAFT_NS, DRAFT_KEY, {"status": "waiting", "site": site, "approval": a["approval"],
                                           "count": len(new)}, set_by="aeo")
    return {"ok": True, "proposed": len(new)}


def draft_failed(job: dict, error: Exception) -> None:
    try:
        box_settings.put(DRAFT_NS, DRAFT_KEY, {"status": "nothing", "said": "Reading your website stopped before it "
                                               "finished, so nothing was drafted. Try again in a minute."},
                         set_by="aeo")
    except Exception:                                         # noqa: BLE001
        pass


def propose_facts(seat=None):
    """Ask the box to draft the facts from the buyer's own website. They wait on Approvals; nothing is saved first."""
    queued, said = start_draft(by=_who(seat))
    return {"asked": queued, "text": said} if queued else {"asked": False, "error": said}


def _add_facts(args: dict) -> dict:
    from . import app
    facts = [str(v) for k, v in args.items() if str(k).startswith("fact ")]
    now = _list_now("facts")
    have = {x.lower() for x in now}
    add = [f for f in facts if f.lower() not in have][:max(0, app._LIST_MAX - len(now))]
    if add:
        box_settings.put(settings.MACHINE, "facts", now + add, set_by=SET_BY)
    nums_now = _list_now("allowed_numbers")
    nums = [n.strip() for n in str(args.get("numbers it may use") or "").split(",") if n.strip()]
    nums_add = [n for n in nums if n not in nums_now]
    if nums_add:
        box_settings.put(settings.MACHINE, "allowed_numbers", nums_now + nums_add, set_by=SET_BY)
    return {"ok": True, "text": f"Added {len(add)} facts and {len(nums_add)} numbers from your website. Change any "
                                "of them on AEO Settings; the AEO Machine uses them from its next article."}


def propose_setting(name=None, value=None, change=None, seat=None):
    """Ask the owner to change one setting: set the website or articles a week, or add or remove one entry."""
    key = str(name or "").strip().lower()
    how = str(change or ("set" if key in SCALARS else "add")).strip().lower()
    try:
        args = _check_setting(key, how, value)
    except Refused as e:
        return {"asked": False, "error": str(e)}
    current = settings.get().get(key) if key in SCALARS else None
    args["now"] = (current if current not in (None, "") else "not set") if key in SCALARS else \
        f"{len(_list_now(key))} entries"
    verb = {"set to": "to", "add": "add", "remove": "remove"}[args["change"]]
    title = (f"Change {args['setting'].lower()} {verb} {_short(args['value'], 50)}" if key in SCALARS else
             f"{args['setting']}: {verb} \"{_short(args['value'], 50)}\"")
    return _ask(title, {"app": APP, "do": "setting", "name": key, "arguments": args}, seat)


# ── what an Approve runs ───────────────────────────────────────────────────────────────────────────

def _run(detail: dict) -> dict:
    """An approved proposal, carried out once. The only place a proposal changes anything."""
    do, args = detail.get("do"), dict(detail.get("arguments") or {})
    try:
        if do == "add_topic":
            question = "" if args.get("question") == "(none)" else args.get("question")
            t, q = _check_topic(args.get("topic"), question)
            pid = plan.add(t, q)
            if args.get("write now") == "yes" and plan.request_now(pid):
                return {"ok": True, "text": f"Added \"{t}\" as next up. Writing starts within a minute or so."}
            return {"ok": True, "text": f"Added \"{t}\" to the plan. It is written in its turn."}
        if do == "retry":
            row = _stopped(args.get("article"))
            if not plan.request_now(int(row["id"])):
                raise Refused("It is no longer stopped, so nothing was changed.")
            return {"ok": True, "text": f"\"{row.get('topic')}\" is next up again."}
        if do == "add_facts":
            return _add_facts(args)
        if do == "unpublish":
            from datetime import datetime, timezone

            from . import publisher
            row = _live(args.get("article"))
            got = publisher.unpublish(row["slug"])
            plan.mark_unpublished(int(row["id"]), datetime.now(timezone.utc).strftime("%b %-d, %Y"))
            if not got["unpublished"]:
                return {"ok": True, "text": f"\"{row.get('title') or row.get('topic')}\" was already off your "
                                            "website, so nothing changed there. The plan now says so."}
            return {"ok": True, "text": f"\"{row.get('title') or row.get('topic')}\" is off your website. "
                                        "It is kept as a draft in Sanity, and can be rewritten from your facts."}
        if do == "rewrite":
            row = _rewritable(args.get("article"))
            if not plan.rewrite(int(row["id"])):
                raise Refused("It can't be rewritten any more, so nothing was changed.")
            return {"ok": True, "text": f"\"{row.get('title') or row.get('topic')}\" is next up to be written again "
                                        "from your facts, at the same address. It takes a few minutes."}
        if do == "setting":
            name = str(detail.get("name") or "")
            how = {"set to": "set"}.get(args.get("change"), args.get("change"))
            checked = _check_setting(name, how, args.get("value"))
            if name == "site_url":
                from . import app
                site, host = app._site(checked["value"])
                settings.store("site_url", site, by=SET_BY)
                settings.store("host", host, by=SET_BY)
            elif name == "weekly_cap":
                settings.store(name, checked["value"], by=SET_BY)
            else:
                now = _list_now(name)
                item = checked["value"]
                new = now + [item] if how == "add" else [x for x in now if x.lower() != item.lower()]
                settings.store(name, new, by=SET_BY)
            return {"ok": True, "text": f"{checked['setting']}: {checked['change']} {checked['value']}. "
                                        "The AEO Machine uses it from its next article."}
    except (Refused, plan.Rejected) as e:
        return {"ok": False, "text": str(e)}
    return {"ok": False, "text": "This box doesn't know that kind of AEO change."}


def waiting() -> int:
    """How many AEO proposals wait for the owner."""
    return sum(1 for a in approvals.waiting() if a.get("machine") == MACHINE)


approvals.register_kind(KIND, run=_run)
