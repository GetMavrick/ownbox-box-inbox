"""The buyer's email signature: written once, and at the end of every email reply the box drafts or sends.

Owner, 2026-10-02: *"I see that the machine is writing drafts and putting them in my drafts on Gmail and that's
great. But I always want to finish with a signature that includes a link to my website."* Asked whether that meant
a sign-off on every reply, he chose "Yes, on every reply", then: *"Yes, perfect add that open field please."*

WHY THE BOX ADDS IT, NOT GMAIL. Gmail adds a signature when a person composes in Gmail. A draft the box places in
the Drafts folder over IMAP (mailbox_drafts.py) and a reply the box sends over SMTP (reply.py) never pass through
that, so without this every one of them went out unsigned.

WHERE IT IS ADDED, and nowhere else:
  * a drafted reply, as it goes into the buyer's own Gmail Drafts (mailbox_drafts.sweep);
  * every email reply sent through `reply.send_reply`: typed on the thread, ticked on the Drafts tab, or approved
    from the owner's AI. One door, so the sent mail and the thread's own copy of it say the same thing.
  Email only: an Instagram or Messenger message has no signature, and one would read as spam there.

NEVER TWICE. A reply that already ends with the signature (pasted by hand, or a draft signed on its way to Gmail
and then sent from the box) is left as it is. Compared on words and line breaks, not spacing.

THE BUYER'S WORDS, NOT OURS: one plain-text field per Space, owner-only, up to MAX_CHARS. Nothing about any
particular owner ships in the product.
"""
from __future__ import annotations

import re

from core.logging import get_logger

log = get_logger(__name__)

MACHINE = "customer_voice"
MAX_CHARS = 600
MAX_LINES = 8
_SEP = "\n\n"


def _key(space: str) -> str:
    return f"email_signature:{space}"


def _norm(text: str) -> str:
    """Words and line breaks, nothing else: how two signatures are compared."""
    lines = [" ".join(line.split()) for line in str(text or "").replace("\r\n", "\n").split("\n")]
    return "\n".join(line for line in lines if line)


def clean(text: str) -> str:
    """The signature as stored: plain text, trailing spaces gone, blank-line runs folded, bounded.
    Raises ValueError with a sentence the Settings page shows."""
    t = str(text or "").replace("\r\n", "\n").strip()
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    if len(t) > MAX_CHARS:
        raise ValueError(f"A signature can be up to {MAX_CHARS} characters; this one is {len(t)}.")
    if t.count("\n") + 1 > MAX_LINES:
        raise ValueError(f"Keep the signature to {MAX_LINES} rows or fewer.")
    return t


def get(space: str) -> str:
    """This Space's signature, or "". Never raises: a reply never waits on a setting."""
    try:
        from core import box_settings
        got = box_settings.get(MACHINE, _key(space))
    except Exception as e:                                # noqa: BLE001
        log.warning("inbox.signature_unreadable", extra={"error": type(e).__name__})
        return ""
    return got if isinstance(got, str) else ""


def put(space: str, text: str, *, by: str | None = None) -> str:
    """Save it ("" clears it). Returns what was stored."""
    from core import box_settings
    t = clean(text)
    if t:
        box_settings.put(MACHINE, _key(space), t, set_by=by)
    else:
        box_settings.clear(MACHINE, _key(space))
    return t


def apply(space: str, body: str) -> str:
    """`body` with the signature at its end, once. Unchanged when there is no signature or it is already there."""
    sig = get(space)
    b = str(body or "").rstrip()
    if not sig or not b:
        return b
    if _norm(b).endswith(_norm(sig)):
        return b
    return b + _SEP + sig
