"""The box learns the business from the business's own sent mail.

THE GAP THIS CLOSES, measured on the owner's box 2026-09-22. Every draft it wrote read like this:

    "I'd be happy to help! Could you tell me what service you're interested in?"
    "Thanks for reaching out. What can we help you with?"

Not a weak model — a blind one. `draft.py` calls `brain.think(..., isolated=True)`, and
`brain._with_knowledge` returns early on `isolated`, so the drafter has never received one word
about the business it answers for. And `my/knowledge/` on a fresh box holds a README and nothing
else. A product sold as "it answers your customers" could not answer a single real question: not
the hours, not the prices, not what is sold, not where.

THE ANSWER IS ALREADY IN THEIR MAILBOX. A business that has been trading has years of its own
replies in `[Gmail]/Sent Mail` — every price it has quoted, every "we're open till six", every
way it phrases a refund. So the box reads its own owner's sent mail once and writes down what it
learns. No questionnaire, no onboarding form, nothing for the buyer to type: on the first morning
the box already knows the answers, because the buyer wrote them himself, years ago.

WHY IT LIVES IN `drafter/`. Only this package may think (tests/test_customer_voice.py), and this
has to think — the extraction is one model call. It therefore may not import `inbox/`, so it
opens the mailbox itself, READ-ONLY, with `BODY.PEEK` so nothing is marked read.

WHAT IT WILL NOT WRITE DOWN. Sent mail is full of customers: their names, addresses, order
numbers, what they bought. None of that may end up in `my/knowledge/`, because that folder rides
on EVERY model call this box makes, for every machine. The prompt says so, and `_looks_personal`
refuses a result that carries an address or a phone number anyway — a prompt is an instruction,
not a guarantee.

IT NEVER OVERWRITES THE BUYER'S OWN WORDS. It writes exactly one file, named for itself, and
leaves every other `.md` in that folder alone.
"""
from __future__ import annotations

import re

from core import brain
from core.logging import get_logger

log = get_logger(__name__)

OUT_NAME = "learned-from-your-sent-mail.md"

# A prompt is an instruction, not a guarantee. Refuse a result that carries what it was told to
# leave out, rather than writing a customer's details into every future call this box makes.
_AN_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_A_PHONE = re.compile(r"(?:\+?\d[\d\-.\s()]{8,}\d)")

SYSTEM = (
    "You are reading a small business's own outgoing email, to write down what a new employee "
    "would need on their first day. Everything you write will be shown to an assistant that "
    "drafts replies to this business's customers.\n"
    "Write markdown under these headings, and leave a heading out entirely if the mail does not "
    "say: What we do · What we sell and what it costs · Hours and location · How we answer the "
    "questions we get most · Things we never say.\n"
    "Rules:\n"
    "- Only write down what the mail actually says. Never guess a price, an hour, or a policy. "
    "If they quoted different prices at different times, say the range and that it varies.\n"
    "- NEVER include any customer's name, email address, phone number, street address, order "
    "number or anything else personal. You are recording facts about the BUSINESS, not about "
    "the people who wrote to it.\n"
    "- Prefer what they say often over what they said once.\n"
    "- Be compact. Under 400 words. This text is attached to every reply the box ever drafts.\n"
    "- If the mail is too thin to say anything useful, reply with exactly NOTHING_LEARNED."
)

NOTHING = "NOTHING_LEARNED"




def _looks_personal(text: str) -> str:
    """A customer's detail that must never ride on every call. Returns what it found, or ""."""
    if _AN_EMAIL.search(text):
        return "an email address"
    if _A_PHONE.search(text):
        return "a phone number"
    return ""


def summarise(sent: list[str]) -> dict:
    """Turn the business's own sent mail into what a new employee would need. Thinks; no I/O.

    The caller does the reading and the writing — see `inbox/sent_mail.py` and the periodic in
    `marketing/customer_voice/__init__.py`. This half may call a model and may touch nothing else.
    """
    if len(sent) < 10:
        return {"status": "too_little", "read": len(sent)}
    prompt = ("Here is what this business has sent recently, newest first.\n\n"
              "--- their sent mail ---\n" + "\n\n---\n\n".join(sent) +
              "\n--- end ---\n\nWrite what a new employee would need on day one.")
    try:
        text = brain.think(task="inbox_draft", prompt=prompt, system=SYSTEM,
                           max_tokens=900, isolated=True)
    except Exception as e:                           # noqa: BLE001
        log.warning("learn.think_failed", extra={"error": f"{type(e).__name__}: {e}"[:140]})
        return {"status": "think_failed", "read": len(sent)}
    text = str(text or "").strip()
    if not text or text.upper().startswith(NOTHING):
        return {"status": "nothing_learned", "read": len(sent)}
    found = _looks_personal(text)
    if found:
        # NOT RETURNED, AND SAID OUT LOUD. `my/knowledge/` rides on every call this box makes, so
        # a customer's address in it would be repeated to every future customer. A prompt is an
        # instruction; this is the guarantee.
        log.warning("learn.refused_personal", extra={"found": found, "read": len(sent)})
        return {"status": "refused_personal", "found": found, "read": len(sent)}
    return {"status": "ok", "read": len(sent), "text": text}
