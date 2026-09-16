"""Email as an inbox channel — IMAP read, into the same store every other channel writes.

SPEC #1226. The buyer pastes a Google app password (core/box_secrets.put_email); this reads their
mailbox and mirrors it into `inbox_conversations` / `inbox_messages` so email lands in the same
queue as a Messenger thread, with the same drafter and the same screen.

WHY THIS IS NOT `lead_machine/reply_ingest.py` EVEN THOUGH IT LOOKS LIKE IT. That module polls
UNSEEN and sets `\\Seen` after processing, which is correct there — it owns a dedicated GTM reply
mailbox no person reads by hand. Pointed at a customer's REAL inbox, that same pattern marks every
message the box reads as read in their Gmail: they open their phone and their inbox has been
silently marked up by software they just bought. That is a refund, and it is one line away from
happening to anyone who copies the working module.

SO: the mailbox is opened READ-ONLY and every fetch is `BODY.PEEK[]`. Two independent guards for
one mistake, because the cost of it is the customer's own inbox.

POSITION IS A UID WATERMARK, NOT A FLAG. `UIDVALIDITY` is stored beside the UID and checked on
every sweep: if the server changes it, every UID we hold is meaningless, and a poller that did not
notice would either silently stop ingesting forever or re-ingest the entire mailbox. Neither fails
loudly, so it is checked rather than assumed.
"""
from __future__ import annotations

import email as email_mod
import email.header
import email.utils
import html as html_mod
import re
import imaplib
from datetime import datetime, timezone

from core import box_secrets
from core.vendors import mailbox as _core_mailbox
from core.logging import get_logger

from . import store

log = get_logger(__name__)

# The watermark row is per conversation for every other channel. A mailbox needs ONE position for
# the whole folder, so it takes a reserved key that cannot collide with a Message-ID (which always
# contains "@").
_MAILBOX_KEY = "__mailbox__"
_MAX_PER_SWEEP = 50          # a first sweep on an old mailbox must not run for an hour
_FOLDER = "INBOX"


class EmailAuthError(RuntimeError):
    """Google refused the credential. Carries which of the two reasons, for the screen."""

    def __init__(self, status: str, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _classify_auth_failure(msg: str) -> EmailAuthError:
    """Turn an opaque IMAP refusal into something a buyer can act on.

    THE SENTENCES LIVE IN `core.vendors.mailbox` AND THERE IS ONE COPY. They are read in two very
    different moments — by the person pasting a password into the connect screen, and by this
    poller weeks later when Google revokes it — and two copies would drift, with the drifted half
    being whichever one somebody is reading while they try to fix something. This keeps the
    machine's own exception type (the sweep catches it by name) and takes the wording from core."""
    err = _core_mailbox.classify_auth_failure(msg)
    return EmailAuthError(err.status, err.detail)


def _header(msg, name: str) -> str:
    raw = msg.get(name)
    if not raw:
        return ""
    try:
        parts = email.header.decode_header(raw)
        return "".join(p.decode(enc or "utf-8", "replace") if isinstance(p, bytes) else p
                       for p, enc in parts).strip()
    except Exception:                                    # noqa: BLE001 — a bad header is not fatal
        return str(raw).strip()


def thread_key(msg) -> str:
    """The conversation a message belongs to: RFC 5322 threading, not a vendor's thread id.

    The root of `References` is the id of the message that started the thread, which is stable for
    every participant and every client. `In-Reply-To` is the fallback for a mailer that sends one
    and not the other; a message that is itself a thread start has neither and keys on its own id.

    DELIBERATELY NOT Gmail's `X-GM-THRID`, which is authoritative and provider-specific — taking it
    would tie this channel to one mailbox provider for a threading answer the standard headers
    already give us on any IMAP server."""
    refs = _header(msg, "References").split()
    if refs:
        return refs[0]
    return _header(msg, "In-Reply-To") or _header(msg, "Message-ID") or ""


def _body_text(msg) -> str:
    """The plain-text body. Prefers text/plain; falls back to stripping a text/html part."""
    def decode(part) -> str:
        try:
            return part.get_payload(decode=True).decode(
                part.get_content_charset() or "utf-8", "replace")
        except Exception:                                # noqa: BLE001
            return ""

    if not msg.is_multipart():
        return decode(msg).strip()
    html = ""
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if part.get_filename():                          # an attachment is not the body
            continue
        if part.get_content_type() == "text/plain":
            got = decode(part).strip()
            if got:
                return got
        elif part.get_content_type() == "text/html" and not html:
            html = decode(part)
    if not html:
        return ""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html_mod.unescape(text)).strip()


def _sent_at(msg) -> str:
    """The Date header as ISO-8601 UTC, or now if it is missing or unparseable."""
    try:
        dt = email.utils.parsedate_to_datetime(msg.get("Date"))
        if dt is not None:
            return (dt.astimezone(timezone.utc) if dt.tzinfo
                    else dt.replace(tzinfo=timezone.utc)).isoformat()
    except Exception:                                    # noqa: BLE001
        pass
    return datetime.now(timezone.utc).isoformat()


# HOW LONG THE SWEEP WAITS ON A SILENT MAIL SERVER. Without a timeout, `IMAP4_SSL` inherits the
# socket default, which is NO timeout at all: a server that completes the TLS handshake and then
# never answers holds this call open forever. That is not a hypothetical shape — it is what a
# firewalled port, a hung server or a dropped NAT mapping looks like from the client side, and it
# would hang `poll_sweep` itself, so Messenger and Instagram intake stop too. One buyer's mail
# provider having a bad afternoon would silently take the whole box's inbox down.
#
# 30 SECONDS, CHOSEN AGAINST THE SWEEP CADENCE, not picked because it is a round number. The poll
# runs every 45s (`inbox/__init__.py`, config `inbox.poll_interval_s`), so a timeout under that
# means a stuck mailbox costs one sweep and the next one starts clean. Longer than the verifier's
# 20s (`core.vendors.mailbox`) on purpose: nobody is standing in front of this one, so it can
# afford more patience than a person pasting a password can.
_SWEEP_TIMEOUT_S = 30


def _connect(cred: dict):
    """An authenticated, READ-ONLY connection. Raises EmailAuthError when Google refuses."""
    conn = imaplib.IMAP4_SSL(cred.get("host") or "imap.gmail.com", timeout=_SWEEP_TIMEOUT_S)
    try:
        conn.login(cred["user"], cred["password"])
    except imaplib.IMAP4.error as e:
        try:
            conn.logout()
        except Exception:                                # noqa: BLE001
            pass
        raise _classify_auth_failure(str(e)) from None
    # READONLY IS THE FIRST OF THE TWO GUARDS: the server itself refuses to change a flag on this
    # session, so even a future bug that asked for one could not mark the buyer's mail as read.
    conn.select(_FOLDER, readonly=True)
    return conn


def _resume_from(space: str, uidvalidity: str) -> int:
    """The last UID we ingested, or 0 when this mailbox is new or the server reset its UIDs."""
    wm = store.get_watermark(space, _MAILBOX_KEY) or {}
    held = str(wm.get("last_seen_msg_id") or "")
    if ":" not in held:
        return 0
    seen_validity, _, seen_uid = held.partition(":")
    if seen_validity != uidvalidity:
        # UIDVALIDITY CHANGED, so every UID we hold now names a different message (or none). The
        # only safe reading is that we have no position at all.
        log.info("email.uidvalidity_reset", space=space, was=seen_validity, now=uidvalidity)
        return 0
    return int(seen_uid or 0)


def sweep(space: str) -> tuple[int, int]:
    """Read new mail for one Space into the inbox store. Returns (scanned, stored).

    Inert with no credential — "not connected" is an ordinary state, not a failure."""
    cred = box_secrets.email_credential()
    if not cred:
        return (0, 0)

    conn = _connect(cred)
    try:
        uidvalidity = (conn.response("UIDVALIDITY")[1][0] or b"").decode() or "0"
        since = _resume_from(space, uidvalidity)
        typ, data = conn.uid("SEARCH", None, f"UID {since + 1}:*")
        if typ != "OK":
            return (0, 0)
        uids = [u for u in (data[0] or b"").split() if int(u) > since][-_MAX_PER_SWEEP:]

        scanned = stored = 0
        newest = since
        for uid in uids:
            # BODY.PEEK IS THE SECOND GUARD. Plain BODY[] sets \Seen as a side effect of reading.
            typ, fetched = conn.uid("FETCH", uid, "(BODY.PEEK[])")
            if typ != "OK" or not fetched or not isinstance(fetched[0], tuple):
                continue
            scanned += 1
            newest = max(newest, int(uid))
            msg = email_mod.message_from_bytes(fetched[0][1])
            zcid = thread_key(msg)
            mid = _header(msg, "Message-ID")
            if not zcid or not mid:
                continue                                 # unthreadable and unidentifiable; skip
            frm = _header(msg, "From")
            name, addr = email.utils.parseaddr(frm)
            own = (cred.get("user") or "").lower()
            inbound = addr.lower() != own
            store.upsert_conversation(
                space=space, zcid=zcid, platform="email",
                participant=(name or addr or frm)[:200],
                last_inbound_at=_sent_at(msg) if inbound else None,
                account_id=cred.get("user"))
            # record_message is INSERT OR IGNORE on the message id, so the RFC Message-ID IS the
            # exactly-once key — re-reading a UID after a crash writes nothing twice.
            store.record_message(space=space, zcid=zcid, zmid=mid,
                                 direction="in" if inbound else "out",
                                 sent_by=(addr or frm)[:200], body=_body_text(msg))
            stored += 1

        if newest > since:
            store.set_watermark(space, _MAILBOX_KEY,
                                last_seen_msg_id=f"{uidvalidity}:{newest}",
                                last_activity=datetime.now(timezone.utc).isoformat())
        return (scanned, stored)
    finally:
        try:
            conn.logout()
        except Exception:                                # noqa: BLE001
            pass
