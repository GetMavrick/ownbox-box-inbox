"""Telling the box's owner that somebody is waiting on him.

THE PRODUCT IS INVISIBLE UNTIL SOMEBODY OPENS A SCREEN, and nobody opens a screen. The box polls
every forty-five seconds, records a customer's message, drafts a reply — and then waits for a
person who has no reason to look. This module is the part that makes him look.

IT SAYS THE NUMBER AND NOTHING ELSE. No customer name, no message text, no draft. A notification
lands on a lock screen, gets read over a shoulder on a bus, and sits in whatever mail app the
person uses; the message itself belongs on the box, behind a login, which is where the homepage
says it stays. "Three people are waiting for a reply" is all the owner needs to decide to open
his inbox, and it is the most a notification should ever carry off the box.

TWICE A DAY, AND ONLY WHEN SOMEBODY IS WAITING. Owner, 2026-09-17, relayed by OSDev1: "For right
now an 8 AM and a 5 PM email will work." So the trigger is the CLOCK plus the backlog, not the
poll — the first version mailed on arrival behind a thirty-minute gap, which was a cadence a
developer picked, and cadence is his word.

THE BACKLOG WAS THE QUESTION UNTIL 2026-10-05, and it turned out to be the wrong one: asked twice a day it gave
the same number twice a day, for as long as nobody cleared it. `awaiting_reply` still excludes opted-out rows, so a
STOP never becomes a number he is asked to act on.

ONLY SOMEBODY NEW, AND NEVER BY EMAIL. Owner, 2026-10-05, on the 8 AM and 5 PM mails saying "72 people are
waiting for a reply" every day: "These are very annoying emails that I get every day. Can you please turn them off.
We shouldn't ever annoy and nag people with redundant messages." So the notice no longer carries the backlog, which
repeats the same number until somebody clears it, and it no longer goes by email; the Morning Review already says
who is waiting, once a day. What is left goes to the mobile app, and only when somebody has written since the last
one: it counts the threads whose newest message from them came after the box last looked, and says nothing if
that is none. The first look on a box starts from now, so an existing backlog is never announced as news.

NO MODEL IN THIS PATH. Counting rows and writing a sentence about the count is deterministic work
(spec §11-6), so none of it goes near `brain.think` — and `tests/test_customer_voice.py` enforces
that for this file as for every other one here.
"""
from __future__ import annotations

from core import box_settings, notify
from core.logging import get_logger

from . import store

log = get_logger(__name__)

NOTICE_KEY = "inbox_waiting"


def _sentence(n: int) -> str:
    """What the mobile app shows. Singular and plural written out rather than pluralised with an 's', because
    "1 messages" on a lock screen is the whole impression a person forms of the product."""
    if n == 1:
        return "1 new person is waiting for a reply"
    return f"{n} new people are waiting for a reply"


def _seen_key(space: str) -> str:
    return f"notice.seen_through.{space}"


def tick(now=None) -> dict:
    """Worker periodic. NEVER RAISES — a notifier that throws must not take a rail down with it.

    Returns the notifier's own answer so a caller (and a test) can see WHY nothing was sent, which
    is the difference between a notifier that is off, one outside its window, and one that is
    broken. `notify.send_notice` does the slot and marker work; this only asks the question.

    CHEAP WHEN THERE IS NOTHING TO DO, because it runs on a 1-vCPU box: `due_slot` is arithmetic on
    the clock and returns None for sixteen hours of the day, before anything touches the database.
    """
    try:
        if notify.due_slot(now) is None:
            return {"sent": 0, "skipped": "outside_slot"}
        from datetime import datetime, timezone
        stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        sent = 0
        details = []
        for space in _spaces():
            since = box_settings.get("inbox", _seen_key(space), default=None)
            if not since:
                # THE FIRST LOOK STARTS FROM NOW. A box that already has seventy-two people waiting is not news to
                # anyone; announcing them would be the very email this replaced.
                box_settings.put("inbox", _seen_key(space), stamp, set_by="inbox notice")
                details.append(f"{space}: starting from now")
                continue
            new = store.awaiting_reply(space, since=since)
            if new <= 0:
                # NOBODY NEW, SO NOTHING. The people still waiting are on the inbox and in the Morning Review.
                details.append(f"{space}: nobody new")
                continue
            got = notify.send_notice(NOTICE_KEY, _sentence(new), _sentence(new), now=now,
                                     waiting=new, by_mail=False, push_body=_sentence(new))
            delivered = int(got.get("sent") or 0) + int(got.get("notified") or 0)
            sent += delivered
            if delivered or got.get("skipped") == "off":
                # LOOKED, SO THE NEXT NOTICE COUNTS FROM HERE. "off" (nobody has the app) moves it too: installing
                # the app later must not open with weeks of old arrivals.
                box_settings.put("inbox", _seen_key(space), stamp, set_by="inbox notice")
            details.append(f"{space}: {got.get('skipped') or 'sent'}")
        return {"sent": sent, "skipped": "" if sent else "nothing_sent",
                "detail": "; ".join(details)}
    except Exception as e:                     # noqa: BLE001 — see the docstring
        log.warning("inbox.notice_failed", error=str(e)[:120])
        return {"sent": 0, "skipped": "error", "detail": str(e)[:120]}


def _spaces() -> list[str]:
    """The Spaces this box serves. ONE QUERY'S WORTH OF CAUTION: a box with no Spaces configured
    must answer "none" rather than raise inside a periodic."""
    try:
        from core import spaces as _sp
        return [s["name"] for s in _sp.all_spaces()]
    except Exception:                          # noqa: BLE001
        return ["default"]
