"""The AEO Machine's proposals: what a person's own AI may ask for, held until that person taps Approve.

OWNER, 2026-10-02: full command of the machine from Claude (#1793 §1.1, "Agree. go"). OSDev1's seat
ruling the same morning: a person's own-AI seat is `act` by default, so it can "read, draft, propose;
never send, publish or charge (no seat holds act:, every proposal is a tap)."

ONE QUEUE, NOT A NEW ONE. Each proposal goes into core/approvals.py, the queue connected apps already
use: it waits on Waiting for you and the owner's mobile app is told; Approve runs it once, Decline
changes nothing, and after a week it expires and never runs. Nothing here writes the plan or a setting
on its own: `_run` is reached only from `approvals.decide`, which needs a person.

WHAT IS SHOWN IS WHAT RUNS. The approvals screen lists a proposal's `arguments` row by row, and `_run`
reads those same rows. Nothing is rebuilt at approval, so the owner approves the words he reads.

THE SAME CHECKS AS THE SCREENS, TWICE. A proposal is checked when it is made, so the AI hears at once
that a topic is a duplicate or a number is out of range, and again when it runs, because a week may
have passed and the plan or the settings may have changed.

THREE ACTIONS, THE SCREENS' OWN:
  * add a topic (AEO → Articles, "Add a topic"), optionally next up ("Write and publish now");
  * try a stopped article again (AEO → Articles, "Try again now");
  * change one setting (AEO → Settings): the website, articles a week, or one entry added to or removed
    from a list. Connections and their keys are never proposable: those stay on Data sources.
"""
from __future__ import annotations

from core import approvals, box_settings
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
                     "Waiting for you. Nothing has changed yet. Don't ask again for the same thing.")}


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
        if do == "setting":
            name = str(detail.get("name") or "")
            how = {"set to": "set"}.get(args.get("change"), args.get("change"))
            checked = _check_setting(name, how, args.get("value"))
            if name == "site_url":
                from . import app
                site, host = app._site(checked["value"])
                box_settings.put(settings.MACHINE, "site_url", site, set_by=SET_BY)
                box_settings.put(settings.MACHINE, "host", host, set_by=SET_BY)
            elif name == "weekly_cap":
                box_settings.put(settings.MACHINE, name, checked["value"], set_by=SET_BY)
            else:
                now = _list_now(name)
                item = checked["value"]
                new = now + [item] if how == "add" else [x for x in now if x.lower() != item.lower()]
                if new:
                    box_settings.put(settings.MACHINE, name, new, set_by=SET_BY)
                else:
                    box_settings.clear(settings.MACHINE, name)
            return {"ok": True, "text": f"{checked['setting']}: {checked['change']} {checked['value']}. "
                                        "The AEO Machine uses it from its next article."}
    except (Refused, plan.Rejected) as e:
        return {"ok": False, "text": str(e)}
    return {"ok": False, "text": "This box doesn't know that kind of AEO change."}


def waiting() -> int:
    """How many AEO proposals wait for the owner."""
    return sum(1 for a in approvals.waiting() if a.get("machine") == MACHINE)


approvals.register_kind(KIND, run=_run)
