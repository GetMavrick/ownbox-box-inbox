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
                "x-ownbox", "x-autoreply", "x-autorespond")
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
    # THE INBOX'S OWN WORDS, MERGED (plan #1857 H7: one classifier, not two). email_channel.is_automated kept its own
    # list beside this one; it now asks this file, so the words it had and this lacked come here.
    r"|mailer|root|cron|bot|robot|automailer"
    r")(?=$|[.\-_+])", re.I)                             # a lookahead, so `alerts-noreply` yields both words
# A BUSINESS CAN WRITE FROM THESE (the inbox's rule since 2026-09-22): a security or monitoring firm's `alerts@`.
# The drafter still won't pay to answer one on its address alone; "waiting" won't hide one (strict=False).
_MAYBE_A_BUSINESS = {"alert", "alerts"}


def _local(addr: str) -> str:
    a = str(addr or "").strip().lower()
    return a.split("@", 1)[0] if "@" in a else a


def why(sender: str, headers: dict | None = None, *, ours: set[str] | None = None, strict: bool = True) -> str:
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
    if h.get("content-type", "").strip().lower().startswith("multipart/report"):
        return "a delivery report"                      # RFC 6522: a bounce or a read receipt, never a person
    for name in BULK_HEADERS:
        # `Auto-Submitted: no` is the one value that means a person sent it (RFC 3834).
        if h.get(name) and not (name == "auto-submitted" and h[name].strip().lower() == "no"):
            return f"an automated sender ({name})"
    if h.get("precedence", "").strip().lower() in (_BULK_PRECEDENCE if strict else {"auto_reply"}):
        return f"an automated sender (precedence: {h['precedence'].strip().lower()})"
    words = {w.lower() for w in _NEVER_WRITES_BACK.findall(_local(addr))}
    if words if strict else (words - _MAYBE_A_BUSINESS):
        return "an address that does not accept replies"
    return ""


PERSON, AUTOMATED, LIST_ONLY = 0, 1, 2


def level(sender: str, headers: dict | None = None, *, ours: set[str] | None = None, strict: bool = True) -> int:
    """PERSON (0), AUTOMATED (1), or LIST_ONLY (2): the only sign of a machine is a `List-Unsubscribe` header.

    LIST_ONLY IS THE SHAPE OF A COLD PITCH (plan #1857 H7, OSDev4's F4 #1852): most cold-email tools add
    List-Unsubscribe to every send, so a rule that skipped it would silence the pitch-back before it could answer.
    It is not waiting on the owner and it is not drafted, unless pitch-back is on (drafter/store.needs_a_draft)."""
    if not why(sender, headers, ours=ours, strict=strict):
        return PERSON
    h = {str(k).lower(): v for k, v in (headers or {}).items()}
    if h.get("list-unsubscribe") and not why(sender, {k: v for k, v in h.items() if k != "list-unsubscribe"},
                                             ours=ours, strict=strict):
        return LIST_ONLY
    return AUTOMATED


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
        # AND YOUR BUSINESS'S ADDRESSES (Mailbox settings; inbox/store.business_addresses, read here by its key
        # because the drafter may not import inbox/): a CC'd or forwarded copy of the business's own mail is never
        # answered.
        from core import box_settings
        named = box_settings.get("inbox", "mailbox.business_addresses", default=None) or []
        mine = {str(a).strip().lower() for a in named if isinstance(named, list) and "@" in str(a or "")}
        return ({user} if user else set()) | mine
    except Exception:                                # noqa: BLE001 — never break a sweep over this
        return set()
