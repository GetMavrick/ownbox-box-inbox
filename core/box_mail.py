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


def _configured_from() -> str:
    """The operator's sender: `review.email_from`, else GTM_FROM_EMAIL. Used with the operator key."""
    from core.config import get_config          # per call: config bound at import defeats a patch
    cfg = dict(get_config().get("review") or {})
    return str(cfg.get("email_from") or getattr(settings, "gtm_from_email", "") or "").strip()


# ── the owner's own way to send (owner, 2026-09-23) ───────────────────────────────────────────────
#
# A SOLD BOX SHIPS WITH NO WAY TO SEND EMAIL, DELIBERATELY: our Resend key must never sit on a
# buyer's box (OSDev1, 16:52 on the wall). So the box's owner brings their own — a Resend key, or an
# SMTP login from whatever service they already send through — on /settings/email. Stored as ONE
# row in box_secrets, so switching service can never leave half of each.
#
# THE OPERATOR'S ENVIRONMENT STILL COMES FIRST. The owner's own box has RESEND_API_KEY in .env and
# a verified from-address in config; that path is unchanged, byte for byte, and a buyer box never
# has it.

OWN_KINDS = ("resend", "smtp")
# THE ROW'S NAME IN box_secrets, kept HERE because that file is frozen against new credentials by
# tests/test_core_boundary.py. The value is compact JSON, base64-encoded, so the store's generic
# rules (no whitespace, not empty) hold for any password a service hands out, spaces and all.
MAIL_OWN = "mail_own"


def _pack(row: dict) -> str:
    import base64
    return base64.b64encode(_json.dumps(row, separators=(",", ":")).encode()).decode()


def _unpack(raw: str) -> dict:
    import base64
    try:
        got = _json.loads(base64.b64decode(raw.encode(), validate=True).decode())
    except (ValueError, UnicodeDecodeError):
        return {}
    return got if isinstance(got, dict) else {}


def own() -> dict:
    """The owner's own sending details as a dict, or {} when none are set. Never raises."""
    from core import box_secrets
    raw = box_secrets.get(MAIL_OWN)
    got = _unpack(raw) if raw else {}
    return got if got.get("kind") in OWN_KINDS else {}


def _own_complete(o: dict) -> bool:
    if not _deliverable(o.get("from") or ""):
        return False
    if o.get("kind") == "resend":
        return bool(o.get("key"))
    if o.get("kind") == "smtp":
        return bool(o.get("host") and o.get("port") and o.get("user") and o.get("password"))
    return False


def put_own(details: dict, *, user_id: str | None = None) -> None:
    """Store the owner's own sending details, replacing whatever was there. Raises ValueError with a
    sentence written for the person looking at the form. THE VALUES ARE NEVER LOGGED."""
    from core import box_secrets
    kind = str(details.get("kind") or "")
    sender = str(details.get("from") or "").strip()
    if kind not in OWN_KINDS:
        raise ValueError("Choose Resend or another email service.")
    if not _deliverable(sender) or any(ch.isspace() for ch in sender):
        raise ValueError("Put in the full address the email should come from, like "
                         "hello@yourbusiness.com.")
    row = {"kind": kind, "from": sender}
    if kind == "resend":
        key = str(details.get("key") or "").strip()
        if any(ch.isspace() for ch in key):
            raise ValueError("That key has a space in it — paste the key on its own.")
        if not key.startswith("re_") or len(key) < 12:
            raise ValueError("That does not look like a Resend key. They start with re_ — "
                             "create one under API Keys in your Resend account.")
        row["key"] = key
    else:
        host = str(details.get("host") or "").strip().lower()
        user = str(details.get("user") or "").strip()
        password = str(details.get("password") or "")
        try:
            port = int(str(details.get("port") or "587").strip())
        except ValueError:
            port = 0
        if not host or " " in host or "." not in host:
            raise ValueError("Put in the SMTP server your email service gives you, like "
                             "smtp.gmail.com.")
        if port not in (465, 587, 2525):
            # 25 IS LEFT OUT ON PURPOSE: it is server-to-server delivery, usually blocked on cloud
            # servers and never where a login is meant to be sent. The three here are the ones a
            # sending service hands out for exactly this.
            raise ValueError("Use the port your service gives you for sending with a login — "
                             "587 almost always, sometimes 465 or 2525.")
        if not user or not password.strip():
            raise ValueError("Put in the username and password your email service gave you.")
        if host in ("smtp.gmail.com", "smtp.googlemail.com"):
            # A GOOGLE APP PASSWORD IS SHOWN IN FOUR GROUPS OF FOUR WITH SPACES; Google accepts it
            # without them. The same cleaning the inbox's mailbox credential already does.
            password = "".join(password.split())
        row.update(host=host, port=port, user=user, password=password)
    box_secrets.put(MAIL_OWN, _pack(row), user_id=user_id)
    log.info("box_mail.own_set", kind=kind, user=user_id)


def clear_own(*, user_id: str | None = None) -> bool:
    from core import box_secrets
    return box_secrets.clear(MAIL_OWN, user_id=user_id)


def _route() -> tuple[str, dict]:
    """Which way this box sends right now: ("resend", {...}) / ("smtp", {...}) / ("", {})."""
    key = (getattr(settings, "resend_api_key", "") or "").strip()
    if key and _configured_from():
        return "resend", {"key": key, "from": _configured_from()}
    o = own()
    if _own_complete(o):
        return o["kind"], o
    return "", {}


def from_address() -> str:
    """The sender this box mails from. Empty = this box cannot send, and that is a
    configuration state rather than an error — a box whose owner never set one simply does not
    email, and every caller here is expected to check `is_configured` and carry on quietly."""
    kind, r = _route()
    return r.get("from", "") if kind else _configured_from()


def is_configured() -> bool:
    """A complete way to send: the operator's key and verified from-address, or the owner's own
    Resend key or SMTP login with a from-address. Half-configured is not a lesser state of working
    — it is not working, and a caller must be able to ask."""
    return bool(_route()[0])


def describe() -> dict:
    """What the settings screen shows: which way, from which address. NEVER a key or a password."""
    kind, r = _route()
    operator = bool(kind == "resend" and r.get("key") == (getattr(settings, "resend_api_key", "")
                                                          or "").strip())
    out = {"kind": kind, "from": r.get("from", ""), "operator": operator}
    if kind == "smtp":
        out.update(host=r.get("host", ""), port=r.get("port", ""), user=r.get("user", ""))
    return out


# THE ONE NAME FOR "THIS BOX SENT IT". Read by `inbox/email_channel.sweep` on the way back in, so
# it lives here — beside the only function that puts it on the wire — and is imported there rather
# than spelled twice. Two copies of a header name is one copy being wrong after the next edit.
ORIGIN_HEADER = "X-Ownbox"


def send(to: str, subject: str, text_body: str, html_body: str, *, idem_key: str,
         sender_name: str, note: str) -> str:
    """One email through Resend. -> the Resend message id. Raises; the caller decides what a
    failure costs. Metered before the call, recorded after it, exactly once per message id.

    `note` is what the ledger row says this spend WAS. It must never be the cold-send note: the
    owner's daily number is a floor on new conversations, and a box telling him his inbox is full
    must not spend a slot of it.
    """
    kind, route = _route()
    if not kind:
        raise VendorError("resend", "config",
                          "this box has no way to send email: no RESEND_API_KEY with a from-address, "
                          "and nothing set on /settings/email")
    sender = route["from"]
    to = (to or "").strip()
    if "@" not in to:
        raise VendorError(kind, "config", f"not an address: {to!r}")
    if kind == "smtp":
        return _send_smtp(route, to, subject, text_body, html_body, idem_key=idem_key,
                          sender_name=sender_name, note=note)
    key = route["key"]
    cost_guard.check_vendor("resend", 1)
    # EVERY MESSAGE THIS BOX ORIGINATES CARRIES A MARK, and the inbox's sweep skips anything
    # wearing it (`inbox/email_channel.py`). Required by OSDev1 before the box may notify a buyer
    # at their own address (M1, 2026-09-22), because the poller reads that same mailbox.
    #
    # WHY THE FROM-ADDRESS CHECK ALREADY THERE IS NOT ENOUGH. The sweep computes
    # `inbound = addr.lower() != own`, so an EXACT self-match is already filed as outbound and
    # never drafted. That check is real protection and it is fragile in four ordinary shapes:
    #   · a Gmail alias or +suffix — `info+notice@` is not equal to `info@`
    #   · a notice to a MEMBER, whose address is not the mailbox's
    #   · a shared or forwarded mailbox, where the box reads mail the buyer did not send
    #   · a From rewritten by a relay
    # In each of those the equality fails, the notice reads as a customer, and the box answers
    # itself. A header is not a second guess at the same question — it is the box saying so.
    #
    # `X-` AND ITS OWN NAME. A custom header is carried verbatim by Resend and by SMTP; no mail
    # system rewrites one it does not know. It says only that we sent it — it carries no count,
    # no address and no customer's words, so a copy landing anywhere is harmless.
    payload = {"from": f"{sender_name} <{sender}>", "to": [to], "subject": subject,
               "text": text_body, "html": html_body,
               "headers": {ORIGIN_HEADER: "notice"}}
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


_SMTP_TIMEOUT_S = 20


def _send_smtp(route: dict, to: str, subject: str, text_body: str, html_body: str, *,
               idem_key: str, sender_name: str, note: str) -> str:
    """One email through the owner's own SMTP service. -> a reference for the ledger row.

    AT MOST ONCE, BY CLAIMING BEFORE SENDING. SMTP has no idempotency key, so the ledger is the
    record of intent: the row is written after the login succeeds and BEFORE the message is handed
    over. Everything that fails before that point certainly sent nothing and is retryable; anything
    that fails after it is INDETERMINATE (invariant 4) and is never sent a second time — a repeated
    idem_key returns without touching the network.

    NEVER IN THE CLEAR. Port 465 is TLS from the first byte; every other port must upgrade with
    STARTTLS or the login is not sent at all.
    """
    import email.message
    import email.utils
    import smtplib
    import ssl

    ref = f"smtp:{idem_key}"
    cost_guard.check_vendor("smtp", 1)
    msg = email.message.EmailMessage()
    msg["From"] = email.utils.formataddr((sender_name, route["from"]))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = email.utils.make_msgid(domain=route["from"].rsplit("@", 1)[-1])
    msg[ORIGIN_HEADER] = "notice"
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    host, port = route["host"], int(route["port"])
    ctx = ssl.create_default_context()
    try:
        if port == 465:
            conn = smtplib.SMTP_SSL(host, port, timeout=_SMTP_TIMEOUT_S, context=ctx)
        else:
            conn = smtplib.SMTP(host, port, timeout=_SMTP_TIMEOUT_S)
            conn.ehlo()
            if not conn.has_extn("starttls"):
                conn.close()
                raise VendorError("smtp", "config", f"{host} will not encrypt the connection "
                                  "(no STARTTLS), so the login was not sent")
            conn.starttls(context=ctx)
            conn.ehlo()
        conn.login(route["user"], route["password"])
    except smtplib.SMTPAuthenticationError as e:
        raise VendorError("smtp", "auth", f"{host} refused the username or password "
                          f"({e.smtp_code})") from e
    except VendorError:
        raise
    except (OSError, smtplib.SMTPException) as e:
        raise RetryableError(f"smtp transport: {type(e).__name__}: {str(e)[:120]}") from e
    try:
        if not state.record_vendor_usage("smtp", 1, idem_key=ref, note=note):
            log.info("box_mail.smtp_already_sent", ref=ref)
            return ref
        try:
            conn.send_message(msg)
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused,
                smtplib.SMTPDataError) as e:
            # REFUSED OUTRIGHT, so certainly not delivered — but the claim stands: a refusal is an
            # answer, and the next attempt is a new idem_key from the caller, not this one again.
            raise VendorError("smtp", "refused", str(e)[:200]) from e
        except (OSError, smtplib.SMTPException) as e:
            # INDETERMINATE. The server may have taken it. Recorded, never resent.
            log.warning("box_mail.smtp_indeterminate", ref=ref, error=type(e).__name__)
            raise VendorError("smtp", "indeterminate",
                              f"the connection dropped while sending; not resent ({type(e).__name__})") from e
    finally:
        try:
            conn.quit()
        except Exception:                                # noqa: BLE001
            pass
    return ref


def send_test(user_id: str) -> tuple[bool, str]:
    """Send one message to this person's own sign-in address. -> (delivered_to_the_service, sentence).

    THE ONLY PROOF THAT COUNTS IS AN EMAIL IN AN INBOX (OSDev1, 2026-09-23: seven claim emails were
    "sent" with a key set and a verified domain, and every one was silently dropped). So the screen
    never says "connected" — it says "sent, now look for it".
    """
    import time as _time
    people = to_box_people(user_id)
    if not people:
        return False, ("Your sign-in address cannot receive email, so there is nowhere to send a "
                       "test. Change it under your account first.")
    to = people[0]["email"]
    try:
        send(to, "Test from your box",
             "This is the test you asked for. If you are reading it, your box can send email.\n",
             "<p>This is the test you asked for. If you are reading it, your box can send "
             "email.</p>",
             idem_key=f"mail-test:{user_id}:{int(_time.time())}",
             sender_name="Your box", note="mail_test")
    except VendorError as e:
        return False, _say(e)
    except RetryableError as e:
        return False, f"Your email service could not be reached just now ({str(e)[:120]}). Try again."
    except Exception as e:                               # noqa: BLE001 — shown, not raised
        return False, f"That did not send: {type(e).__name__}."
    return True, f"Sent to {to}. Look for it — if it is not there in a minute, check spam."


def _say(e: Exception) -> str:
    """A vendor refusal as a sentence the owner can act on."""
    text = str(e)
    low = text.lower()
    if "not verified" in low or "verify a domain" in low or "domain" in low and "403" in low:
        return ("Resend has not verified the domain of your from-address. Add the domain under "
                "Domains in Resend, finish its DNS steps, then test again.")
    if "401" in low or "api key is invalid" in low:
        return "Resend did not accept that key. Create a new one under API Keys and paste it again."
    if "auth" in low and "smtp" in low:
        return ("Your email service refused the username or password. Many services need an app "
                "password rather than your normal one.")
    return f"That did not send: {text[:200]}"
