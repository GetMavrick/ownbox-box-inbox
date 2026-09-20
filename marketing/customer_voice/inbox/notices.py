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

THE BACKLOG IS THE RIGHT QUESTION NOW, where under the old shape it was the wrong one. "Anyone
waiting?" asked continuously would mail every half hour forever about a thread he has decided to
leave; asked twice a day it is exactly the question he wants answered. `awaiting_reply` already
excludes opted-out rows, so a STOP never becomes a number he is asked to act on.

NO MODEL IN THIS PATH. Counting rows and writing a sentence about the count is deterministic work
(spec §11-6), so none of it goes near `brain.think` — and `tests/test_customer_voice.py` enforces
that for this file as for every other one here.
"""
from __future__ import annotations

from core import notify
from core.logging import get_logger

from . import store

log = get_logger(__name__)

NOTICE_KEY = "inbox_waiting"


def _sentence(n: int) -> tuple[str, str]:
    """(subject, body). Singular and plural written out rather than pluralised with an 's', because
    "1 messages" on a lock screen is the whole impression a person forms of the product."""
    if n == 1:
        return ("1 person is waiting for a reply",
                "Someone messaged your business and is waiting for a reply.\n"
                "Open your inbox to read it and send an answer.")
    return (f"{n} people are waiting for a reply",
            f"{n} people messaged your business and are waiting for a reply.\n"
            "Open your inbox to read them and send answers.")


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
        sent = 0
        details = []
        for space in _spaces():
            waiting = store.awaiting_reply(space)
            if waiting <= 0:
                # SKIPPED WHEN NOBODY IS WAITING (OSDev1's default, the owner may overrule). An
                # email that says "nothing needs you" twice a day is how a person learns to stop
                # opening them, which costs the one that matters.
                details.append(f"{space}: nobody waiting")
                continue
            subject, body = _sentence(waiting)
            got = notify.send_notice(NOTICE_KEY, subject, body, now=now)
            sent += int(got.get("sent") or 0)
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
