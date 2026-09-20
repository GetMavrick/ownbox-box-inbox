"""The one door a box uses to email ITS OWN OWNER. Never a customer, never a prospect.

WHY THIS IS NOT `lead_machine.resend_client`. That module is the COLD OUTREACH door and it is
right to be: it runs the outbound lane gate, checks the prospect suppression list, injects an
unsubscribe footer and a List-Unsubscribe header, and charges the owner's daily new-prospect
quota. Every one of those is correct for a stranger and wrong for the person who bought the box —
and the first of them is decisive, because the cold lane is currently OFF (`from_email` blanked
2026-09-03, on the wall). A notification routed through it would not be a notification with an
unsubscribe link on it; it would be NOTHING, silently, forever. That is the failure this repo
found three times tonight, and it is the reason this file exists rather than an extra flag there.

WHAT MAKES IT SAFE IS THE RECIPIENT SET, NOT A COMMENT. `to_box_people` resolves addresses ONLY
from this box's own users table — the rows a person signs in with. There is no path from a
conversation, a prospect, a scraped page or a caller's string literal to an envelope. A machine
that cannot name an outsider cannot mail one, which is the property `marketing/customer_voice`'s
send ban protects and the reason a notifier does not belong inside that package at all.

IT IS TRANSACTIONAL, DELIBERATELY. A message from a box to its owner about his own box carries no
unsubscribe footer, no tracking and no marketing copy — the same standing `review_email` has had
since 2026-09-10. Turning it off is a SETTING on the box (`notify.enabled`), not an entry on the
cold-prospect suppression list: those two lists are about different relationships and reading one
for the other is how a man who once unsubscribed from a newsletter stops being told his inbox is
full.

EXTRACTED FROM `review_email`, NOT REWRITTEN. The transport below is that module's `send` moved
here verbatim but for its two hardcoded strings (sender name, ledger note), which are now
arguments. `review_email.send` delegates to it and its suite is what proves the move was faithful.
"""
from __future__ import annotations

import json as _json

from core import cost_guard, net, state
from core.config import settings
from core.exceptions import RetryableError, VendorError
from core.logging import get_logger

log = get_logger(__name__)

_URL = "https://api.resend.com/emails"
# RESEND SITS BEHIND CLOUDFLARE, and Cloudflare refuses urllib's default signature. Measured on the
# box 2026-09-10: core.net.post_public with no User-Agent got 403 "error code: 1010"; the identical
# call with one got Resend's own 401. core.net sets none by design (each caller names itself), so
# this caller must, or no mail leaves the box at all. CARRIED OVER WITH THE TRANSPORT — dropping
# it in the move would have cost every box its morning email and found nothing in a test, because
# a fake transport does not sit behind Cloudflare.
_UA = "AIOS-MorningReview/1.0"
# 529 IS IN HERE ON PURPOSE and is not a typo for 529-as-5xx: it is the overloaded code this box
# has actually seen. Retryable, not a failure.
_TRANSIENT = {408, 429, 500, 502, 503, 504, 529}


def to_box_people(user_id: str | None = None) -> list[dict]:
    """The people this box may email: its own signed-in users, and nobody else.

    THE WHOLE SAFETY PROPERTY OF THIS MODULE IS THIS FUNCTION. Callers pass a user id or nothing;
    they never pass an address. An address that is not on a row in this box's users table cannot
    be reached from here, so no caller — present or future, careful or not — can turn a box
    notification into an outbound email to a stranger.
    """
    rows = [state.get_user(user_id)] if user_id else state.list_users()
    out = []
    for r in rows or []:
        if not r:
            continue
        addr = str(r.get("email") or "").strip()
        if _deliverable(addr):
            out.append({"id": r.get("id"), "email": addr, "name": r.get("name") or ""})
    return out


def _deliverable(addr: str) -> bool:
    """A floor, not validation: can mail conceivably reach this at all?

    EVERY BOX SHIPS WITH ONE OF THESE. `state.init_db` seeds `owner@localhost` so a fresh box has
    an owner row to sign in as, and that address can never receive anything. Without this line
    every notice on every un-personalised box spends a Resend call to be rejected, logs a failure,
    counts as nobody-reached, and tries again on the next tick — forever, on a box whose owner
    would never see any of it.

    The rule is only that the domain could exist: an `@` and a dot after it. Anything more is
    address validation, which is a thing mail servers do and regexes do badly.
    """
    addr = str(addr or "").strip()
    if addr.count("@") != 1:
        return False
    domain = addr.rsplit("@", 1)[1]
    return "." in domain and not domain.startswith(".") and not domain.endswith(".")


def from_address() -> str:
    """The verified sender this box mails from. Empty = this box cannot send, and that is a
    configuration state rather than an error — a box whose owner never set one simply does not
    email, and every caller here is expected to check `is_configured` and carry on quietly."""
    from core.config import get_config          # per call: config bound at import defeats a patch
    cfg = dict(get_config().get("review") or {})
    return str(cfg.get("email_from") or getattr(settings, "gtm_from_email", "") or "").strip()


def is_configured() -> bool:
    """A key AND a verified from-address. Resend rejects an unverified sender, so half-configured
    is not a lesser state of working — it is not working, and a caller must be able to ask."""
    return bool((getattr(settings, "resend_api_key", "") or "").strip() and from_address())


def send(to: str, subject: str, text_body: str, html_body: str, *, idem_key: str,
         sender_name: str, note: str) -> str:
    """One email through Resend. -> the Resend message id. Raises; the caller decides what a
    failure costs. Metered before the call, recorded after it, exactly once per message id.

    `note` is what the ledger row says this spend WAS. It must never be the cold-send note: the
    owner's daily number is a floor on new conversations, and a box telling him his inbox is full
    must not spend a slot of it.
    """
    key = (getattr(settings, "resend_api_key", "") or "").strip()
    if not key:
        raise VendorError("resend", "config", "RESEND_API_KEY is not set")
    sender = from_address()
    if not sender:
        raise VendorError("resend", "config",
                          "no from-address: set review.email_from or GTM_FROM_EMAIL")
    to = (to or "").strip()
    if "@" not in to:
        raise VendorError("resend", "config", f"not an address: {to!r}")
    cost_guard.check_vendor("resend", 1)
    payload = {"from": f"{sender_name} <{sender}>", "to": [to], "subject": subject,
               "text": text_body, "html": html_body}
    headers = {"Authorization": f"Bearer {key}", "Idempotency-Key": idem_key, "User-Agent": _UA}
    try:
        status, body = net.post_public(_URL, json=payload, headers=headers, timeout=30)
    except net.PostRefused as e:
        raise RetryableError(f"resend transport: {str(e)[:160]}") from e
    if status in _TRANSIENT:
        raise RetryableError(f"resend HTTP {status}: {body[:160]}")
    if not 200 <= status < 300:
        raise VendorError("resend", status, body[:200])
    try:
        ref = (_json.loads(body) or {}).get("id")
    except ValueError as e:
        raise VendorError("resend", status, f"non-JSON: {e}") from e
    if not ref:
        raise VendorError("resend", "shape", f"no message id: {body[:160]}")
    state.record_vendor_usage("resend", 1, idem_key=f"resend:{ref}", note=note)
    return ref
