"""Did a person write this, or a machine?

IT LIVES BESIDE THE DRAFTER, NOT IN `inbox/`, and the guard is why. `drafter/draft.py` may THINK
and must import nothing from the package that SENDS — tests/test_customer_voice.py holds that
line and refused this file when it sat in `inbox/`. Nothing here does any I/O: it is a judgement
about one address and a handful of headers, so it belongs with the thing that has to make it.

THE NIGHT THIS COST. 2026-09-22: the drafter answered everything that arrived. The owner opened
his own Gmail and found fifty drafts the box had written into it — replies to LinkedIn job
alerts, Google security alerts, uspto.gov, Spaceship, DigitalOcean's referral robot, and to the
box's OWN Morning Review. Every one cost a model call on his own subscription. His words:
"None of these needed drafts. And it's just wasting my tokens. Turn them off until you get this
right." Drafting has been off on his box since.

FITTED TO HIS REAL MAILBOX, NOT TO A GUESS. Measured over the newest 120 messages in it:

    refused by header   55      (46%)
    refused by address  37      (31%)
    refused as our own   5       (4%)
    ------------------------------------
    drafted             23      (19%)

and the 23 that survive are people — Colin Tervo, Kamila Manning, Yvonne Bell, Bale Do, and his
own live DigitalOcean support thread.

THREE LAYERS, IN THIS ORDER, AND THE ORDER IS THE ARGUMENT.

1. THE HEADERS, because they are the standard and the sender sets them honestly. `List-Unsubscribe`
   and `List-Id` (RFC 2369/2919) mark a mailing list; `Auto-Submitted` (RFC 3834) marks a message
   generated without a human; `Precedence: bulk|list|junk` is the old convention every bulk mailer
   still sets. This is the highest-precision signal there is — but measured on his box it catches
   only 46%, and a rule built on it alone would have let `noreply@skool.com`,
   `alert@spaceship.com` and `no-reply@referrals.digitalocean.com` straight through. Headers alone
   are not enough, and that is why the next layer exists.

2. THE ADDRESS, for the robots that set no header. `no-reply`, `notifications`, `jobalerts`,
   `invitations`, `verify` — an address that announces it will not be read.

   WHAT IS DELIBERATELY *NOT* HERE: `support@`, `info@`, `hello@`, `contact@`, `sales@`. A small
   business IS its `info@` address, and the whole product is answering small businesses. His own
   open DigitalOcean ticket arrives from `support@digitalocean.com`, and refusing that would make
   the box useless on exactly the threads that matter. A role address is a person until it proves
   otherwise; a `no-reply` address has already told us.

3. OURSELVES. The box's own mailbox and the address its own reports come from. Answering our own
   Morning Review is the loop that has no bottom.

THIS IS A REFUSAL, NOT A SILENCE. `why()` returns the reason, the drafter records it, and the
screen can say "17 messages were from automated senders". A sweep that quietly does nothing is
the seven-hour head-block all over again (`test_the_drafter_cannot_be_head_blocked`).
"""
from __future__ import annotations

import re

# RFC 2369 / 2919 / 3834, plus the `Precedence` convention. Presence is the signal; the value of
# List-Unsubscribe and List-Id is never parsed, because any value at all means a list.
# `x-ownbox` IS OURS, and it is here rather than in a second self-check. #1432 stamps it on
# everything this box originates — its Morning Review, its notices — so the one header already
# answers "did we send this" without the drafter reading an environment it is not allowed to
# touch. Measured on the owner's mailbox: his own Morning Review was the last thing leaking
# through, and this is the line that stops it.
BULK_HEADERS = ("list-unsubscribe", "list-id", "auto-submitted", "x-auto-response-suppress",
                "x-ownbox")
_BULK_PRECEDENCE = {"bulk", "list", "junk", "auto_reply"}

# Anchored to a word boundary inside the LOCAL PART so `notify@` matches and `denotify@example`
# does not, and so a customer called `Alerta` or a domain like `noreply-hosting.com` is untouched.
_NEVER_WRITES_BACK = re.compile(
    r"(?:^|[.\-_+])("
    r"no-?reply|do-?not-?reply|donotreply|noreply|reply-?to-?this-?email-?and-?nothing-?happens"
    r"|mailer-?daemon|postmaster|bounces?|delivery|failure"
    r"|alert|alerts|notice|notices|notify|notification|notifications"
    r"|jobalerts?|invitations?|digest|newsletter|unsubscribe"
    r"|verify|verification|confirm|confirmation|otp"
    r"|automated|auto-?confirm|system|daemon"
    r")(?:$|[.\-_+])", re.I)


def _local(addr: str) -> str:
    a = str(addr or "").strip().lower()
    return a.split("@", 1)[0] if "@" in a else a


def why(sender: str, headers: dict | None = None, *, ours: set[str] | None = None) -> str:
    """The reason this message must not be answered, or "" when a person wrote it.

    `headers` is a plain mapping of the inbound's headers, lower-cased keys. `ours` is every
    address this box sends as — its own mailbox and the address its reports come from.
    """
    addr = str(sender or "").strip().lower()
    if not addr:
        return ""                                    # nothing to judge; other guards decide
    if ours and addr in {str(o).strip().lower() for o in ours if o}:
        return "this box sent it"
    h = {str(k).lower(): str(v or "") for k, v in (headers or {}).items()}
    for name in BULK_HEADERS:
        if h.get(name):
            return f"an automated sender ({name})"
    if h.get("precedence", "").strip().lower() in _BULK_PRECEDENCE:
        return f"an automated sender (precedence: {h['precedence'].strip().lower()})"
    if _NEVER_WRITES_BACK.search(_local(addr)):
        return "an address that does not accept replies"
    return ""


def is_a_person(sender: str, headers: dict | None = None, *, ours: set[str] | None = None) -> bool:
    """True when a reply would reach a human who might read it."""
    return not why(sender, headers, ours=ours)


def our_addresses() -> set:
    """The address this box reads mail as. `core` only — no environment, no filesystem.

    ONE ADDRESS, NOT THREE. An earlier version also read OPERATOR_EMAIL, GTM_FROM_EMAIL and
    GTM_REPLY_TO so the box would not answer its own Morning Review. Two problems with that: the
    drafter may not touch `os` (tests/test_customer_voice.py refused it, correctly — the thinking
    path gets `core` and inert stdlib and nothing else), and it was a SECOND mechanism for
    something #1432 already does properly. Everything this box originates now carries `X-Ownbox`
    and the sweep skips it whole, before a row is ever written. A message the box sent itself
    never reaches this function, so duplicating that judgement here would only give us two places
    to keep in step.
    """
    try:
        from core import box_secrets
        cred = box_secrets.email_credential() or {}
        user = str(cred.get("user") or "").strip().lower()
        return {user} if user else set()
    except Exception:                                # noqa: BLE001 — never break a sweep over this
        return set()
