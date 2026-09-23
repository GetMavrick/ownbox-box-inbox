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
import email.message
import email.utils
import html as html_mod
import re
import imaplib
import smtplib
from datetime import datetime, timezone

from core import box_mail, box_secrets
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


# ── IS A MACHINE WRITING TO US ──────────────────────────────────────────────────────────────────
#
# MEASURED ON THE OWNER'S BOX (OSDev1, 2026-09-22): 35 of 62 email drafts were addressed to
# automated senders — four LinkedIn job alerts, system@, alert@, noreply@, invitations@, and the
# box's own Morning Review. Pressing "send the ones I ticked" would have mailed his business
# address to 35 robots. The model had also been paid to write every one of those 35 replies.
#
# THE HEADERS ARE THE ANSWER AND THE ADDRESS IS THE FALLBACK, in that order, because that is the
# order of confidence. `Auto-Submitted` is RFC 3834 §5 and exists precisely so software can say
# "do not reply to this"; `List-Id` (RFC 2919) and `List-Unsubscribe` (RFC 2369) mark bulk mail.
# A sender that sets any of them has TOLD us. Only when none is present do we look at the address,
# and then conservatively: a blacklist alone would silently refuse to answer a real business whose
# enquiries arrive from `alerts@` or `info@`, and never answering a customer is the more expensive
# mistake of the two.
#
# WHAT IS DELIBERATELY NOT HERE: `Precedence: bulk`. It is not a standard, plenty of ordinary
# mailers set it on perfectly personal mail, and it would cost real conversations.

_AUTO_SUBMITTED_OK = "no"          # RFC 3834: the ONLY value meaning a person sent it

# THE LINE IS "HAS THIS NAME ANY PLAUSIBLE CUSTOMER-FACING USE", and it is drawn on purpose.
# Every local part below is a machine function word no business puts on mail it wants answered.
#
# DELIBERATELY ABSENT, and each one would cost a real customer: `info`, `sales`, `hello`,
# `contact`, `support`, `admin`, `billing`, `accounts` — and `alert` / `alerts`, which reads like
# a robot and is a perfectly ordinary address for a security or monitoring firm. OSDev1's
# `alert@spaceship.com` is caught anyway, by its `Auto-Submitted` header, which is the whole
# reason the headers are asked first: they catch the senders an address list would have to guess
# at, and guessing wrong here means a customer is never answered and nobody finds out.
_ROBOT_LOCALS = (
    "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply", "do_not_reply",
    "mailer-daemon", "postmaster", "bounce", "bounces", "notification", "notifications",
    "invitations", "invitation", "automated", "auto-confirm", "mailer",
    "system", "daemon", "root", "cron", "bot", "robot", "automailer",
)
_ROBOT_PREFIXES = ("noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon",
                   "bounce", "notification", "invitations")


def is_automated(msg, sender: str = "") -> bool:
    """Did a machine send this, on its own account? Never raises.

    THE QUESTION IS NOT "IS THIS UNIMPORTANT". A shipping notice matters; it just has nobody at
    the other end to read a reply. This decides only whether the box drafts an answer and offers
    it for sending — the message is still ingested, still shown, still searchable.
    """
    try:
        auto = _header(msg, "Auto-Submitted").strip().lower() if msg is not None else ""
        if auto and not auto.startswith(_AUTO_SUBMITTED_OK):
            return True                                  # RFC 3834: auto-generated, auto-replied…
        if msg is not None and (_header(msg, "List-Id") or _header(msg, "List-Unsubscribe")):
            return True                                  # bulk, RFC 2919 / RFC 2369
        if msg is not None and _header(msg, "X-Auto-Response-Suppress"):
            return True                                  # Microsoft's, widely set by ticketing
    except Exception:                                    # noqa: BLE001 — a bad header is not fatal
        pass
    addr = str(sender or "").strip().lower()
    if "@" not in addr:
        return False
    local = addr.split("@", 1)[0]
    if local in _ROBOT_LOCALS or any(local.startswith(p) for p in _ROBOT_PREFIXES):
        return True
    # `jobs-listings@linkedin.com`, `jobalerts-noreply@…` — the marker is a WORD in the local
    # part, not the whole of it. Split on the separators a local part may legally contain so
    # "noreplyable@" (a real word containing one) cannot match.
    return any(part in _ROBOT_LOCALS
               for part in re.split(r"[.\-_+]", local) if part)


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


# ── THE DRAFT, IN THE BUYER'S OWN DRAFTS FOLDER ──────────────────────────────────────────────────
#
# WHY THIS EXISTS AND WHY IT IS FIRST. Owner, 2026-09-22: *"If we can't auto draft emails and then
# actually go ahead and send them, this is a completely worthless app."* Sending is coming; this is
# the half that needs NO SMTP AT ALL and reaches him where he already is. The box holds an
# authenticated IMAP session, and RFC 3501 APPEND writes a message into a folder. So the reply the
# box wrote appears in his Gmail, inside the customer's thread, already written. He reads the
# customer's email where he always reads it, sees the answer waiting under it, and taps Send —
# from any client he already has, with nothing installed and no permission asked for.
#
# GMAIL SENDS IT, NOT US. That is the point on day one: no new credential, no send policy to
# settle, no deliverability of ours involved. §1.2 adds SMTP so the box can send from its own
# screen; this works before any of that, and keeps working after it for the buyer who lives in
# the Gmail app and never opens ours.
#
# THE FOLDER IS FOUND, NEVER NAMED. `[Gmail]/Drafts` is LOCALISED — a French account has
# `[Gmail]/Brouillons` — so a hard-coded name works on exactly the accounts the author tested and
# silently fails on the rest. RFC 6154 gives every well-behaved server a `\Drafts` attribute on
# LIST, which is what this asks for. No attribute, no append: the box's own Drafts tab still has
# it, and a missing folder must never cost a draft.
#
# NO `X-Ownbox` MARK ON A DRAFT, unlike every message the box itself sends (`core.box_mail`).
# The mark exists so ingest can skip the box's own mail; a draft is not the box's mail — the
# moment the buyer taps Send it becomes THEIRS, and marking it would tell the sweep to ignore
# the one outbound that matters most.

_DRAFT_TIMEOUT_S = 20        # nobody is standing in front of this; shorter than the sweep's 30


def _drafts_folder(conn) -> str:
    """The mailbox flagged `\Drafts` (RFC 6154), or "" when the server offers none."""
    try:
        typ, boxes = conn.list()
    except Exception:                                    # noqa: BLE001 — a draft never dies here
        return ""
    if typ != "OK":
        return ""
    for raw in boxes or []:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        if "\\Drafts" not in line:
            continue
        # LIST answers `(\HasNoChildren \Drafts) "/" "[Gmail]/Drafts"` — the name is the last
        # quoted run, and it is quoted precisely because it may contain the delimiter.
        parts = line.split('"')
        if len(parts) >= 2:
            return parts[-2]
        return line.rsplit(" ", 1)[-1].strip()
    return ""


# AN IMAP SEARCH KEY IS A COMMAND, AND A MESSAGE-ID IS A STRANGER'S HEADER.
#
# `in_reply_to` is the customer's own `Message-ID:`, copied off the wire by the sweep. Interpolated
# into a search key it is INJECTION, found by OSDev1 on #1434 before it landed: a sender whose
# Message-ID contains a double quote closes ours and appends search keys of their choosing. One
# that matches the whole mailbox, plus a reader that took the newest hit, would put an UNRELATED
# customer's subject line on a draft addressed to this one — and the buyer taps Send.
#
# Two answers, and this holds with either one alone:
#
#   1. QUOTE IT PROPERLY. RFC 3501's quoted string escapes `"` and `\` with a backslash, and
#      admits no CR, LF, NUL or 8-bit byte at all — those need a literal, so a value holding one
#      is refused rather than smuggled. `_imap_quoted` is the only way this file builds a key.
#   2. CHECK WHAT CAME BACK. The fetch asks for MESSAGE-ID beside SUBJECT and the subject is used
#      only if the message is the one we asked for. A search that ever goes wrong again returns
#      nothing usable instead of somebody else's mail.

_UNQUOTABLE = re.compile(r"[^\x01-\x7f]")   # NUL and every 8-bit byte; CR and LF are checked by name


def _imap_quoted(value: str) -> str:
    """`value` as an RFC 3501 quoted string, or "" when it cannot legally be one.

    RETURNS A VALUE THE CALLER MUST CHECK, rather than raising or quietly sanitising. A
    Message-ID we cannot ask about is not an error — it costs a Subject line and nothing else —
    but a half-escaped one sent anyway is the bug this function exists to make impossible.
    """
    value = str(value or "")
    if not value or "\r" in value or "\n" in value or _UNQUOTABLE.search(value):
        return ""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _subject_of(conn, message_id: str) -> str:
    """The Subject of the message we are answering, so the draft reads as a reply.

    FETCHED RATHER THAN STORED. The sweep keeps no subject, and adding a column would not help the
    threads already on the box. One header fetch on an open connection is cheaper than a migration
    that backfills nothing.

    A THREAD IS THREADED BY `References`, NOT BY ITS SUBJECT — so every path out of here that
    returns "" costs a nicety, not the feature, and the caller carries on with a plain one. That
    is what makes the two guards above cheap enough to be absolute.
    """
    needle = _imap_quoted(message_id)
    if not needle:
        return ""
    try:
        typ, data = conn.uid("SEARCH", None, f"HEADER Message-ID {needle}")
        if typ != "OK" or not (data and data[0]):
            return ""
        uid = (data[0] or b"").split()[-1]
        typ, fetched = conn.uid("FETCH", uid,
                                "(BODY.PEEK[HEADER.FIELDS (SUBJECT MESSAGE-ID)])")
        if typ != "OK" or not fetched or not isinstance(fetched[0], tuple):
            return ""
        got = email_mod.message_from_bytes(fetched[0][1])
        # THE SECOND GUARD, AND IT IS THE ONE THAT HOLDS WHEN THE FIRST IS WRONG. The subject is
        # only ever taken from the message we actually asked about.
        if _header(got, "Message-ID").strip() != str(message_id).strip():
            log.info("email.subject_mismatch", extra={"asked": str(message_id)[:120]})
            return ""
        return _header(got, "Subject")
    except Exception:                                    # noqa: BLE001 — a nicety, never the feature
        return ""


def _recipient(space: str, zcid: str, in_reply_to: str) -> str:
    """Who this reply goes to: whoever wrote the message being answered.

    READ OFF THE MESSAGE, NOT OFF THE CONVERSATION. A thread with several participants must
    answer the one who actually asked, and `participant` on the conversation row is only ever
    the most recent writer the sweep happened to see.
    """
    for m in store.messages_for(space, zcid):
        if str(m.get("direction")) == "in" and str(m.get("zernio_message_id") or "") == in_reply_to:
            return str(m.get("sent_by") or "")
    return ""


def append_draft(*, space: str, zcid: str, in_reply_to: str, body: str,
                 conn=None) -> bool | None:
    """Put one drafted reply into the buyer's own Drafts folder, inside the thread. Never raises.

    THREE ANSWERS, AND THE THIRD IS THE ONE THAT MATTERS:

      True   it landed.
      False  not this time — no credential, no `\Drafts` folder, a server having a bad minute.
             The rail un-claims the row and tries again, and nothing is lost: the box's own
             Drafts tab still holds the draft, which is the surface this is a convenience on.
      None   never, for this row. Something about it cannot be put in a mailbox on any attempt.
             The rail keeps the claim so the row leaves the queue for good — otherwise a handful
             of them fill every sweep's limit and starve the drafts that would have worked.

    `conn` IS AN ALREADY-AUTHENTICATED SESSION, passed by the mirror so a sweep of three drafts
    is one login rather than three. Gmail throttles logins, not APPENDs. Left None, this opens
    and closes its own — which is what a single call from a screen wants.

    NEVER RAISES, because the caller is the drafting rail. A mail server having a bad minute must
    not lose a reply the box already paid a model to write.
    """
    # NO MAILBOX IS THE ONE TRANSIENT REFUSAL HERE: a buyer who has not connected one yet, or is
    # part-way through reconnecting, will have one shortly, and the draft should be waiting.
    cred = box_secrets.email_credential()
    if not cred:
        return False

    # EVERYTHING BELOW IS A FACT ABOUT THE ROW, SO IT IS `None` — never, not not-yet.
    #
    # OSDev1 found the shape of this bug in the DRAFTER an hour before I found it here
    # (DEVSTATE, 2026-09-22): `needs_a_draft` kept handing back the same five rows that could
    # never be drafted, so nothing behind them was ever reached, and every signal read green.
    # This rail has the identical shape — `waiting()` is LIMIT 5 and a `False` releases the claim
    # — so five conversations with an unreadable sender would quietly starve every real draft on
    # the box, forever, while `appended: 0` looked like a quiet day.
    #
    # None of these three can change by trying again. The body is fixed on the row, the platform
    # is fixed on the conversation, and `_recipient` reads a stored message that nothing rewrites.
    if not str(body or "").strip():
        return None
    conv = store.get_conversation(space, zcid) or {}
    if str(conv.get("platform") or "") != "email":
        return None                                      # this is the mailbox's trick, nobody else's
    to = _recipient(space, zcid, in_reply_to)
    if "@" not in to:
        log.info("email.draft_no_recipient", extra={"space": space, "conversation": zcid})
        return None
    # A MESSAGE-ID THAT CANNOT BE A HEADER IS REFUSED HERE, and refused FOREVER.
    #
    # Python's email policy rejects CR and LF in a header value — correctly, since they would let
    # a stranger's Message-ID write a `Bcc:` into a message the buyer is about to send. But it
    # rejects them by raising, three calls down, which this function would turn into "try again".
    # A header that is malformed now is malformed for good, and five such rows would fill the
    # rail's LIMIT every sweep and starve every real draft behind them. So: None, not False.
    if any(c in in_reply_to for c in "\r\n") or any(c in to for c in "\r\n"):
        log.warning("email.draft_unheaderable", extra={"space": space, "conversation": zcid})
        return None

    own, opened = conn, None
    try:
        if own is None:
            own = opened = imaplib.IMAP4_SSL(cred.get("host") or "imap.gmail.com",
                                             timeout=_DRAFT_TIMEOUT_S)
            own.login(cred["user"], cred["password"])
            # READ-ONLY, for the same reason the sweep is: `_subject_of` searches INBOX, and a
            # session that cannot set a flag cannot mark the buyer's mail as read by accident.
            own.select(_FOLDER, readonly=True)
        folder = _drafts_folder(own)
        if not folder:
            log.info("email.no_drafts_folder", extra={"space": space})
            return False
        subject = _subject_of(own, in_reply_to)
        if subject and not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        msg = email.message.EmailMessage()
        msg["From"] = cred["user"]
        msg["To"] = to
        msg["Subject"] = subject or "Re: your message"
        # BOTH HEADERS. `In-Reply-To` names the parent and `References` carries the thread's root
        # (`zcid` IS that root — `thread_key` resolves it), which is what every client threads on.
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = f"{zcid} {in_reply_to}" if zcid != in_reply_to else in_reply_to
        msg["Date"] = email.utils.formatdate(localtime=True)
        msg.set_content(str(body))

        # `\Draft` IS WHAT MAKES IT A DRAFT rather than a message sitting in a folder: without
        # the flag Gmail shows it but will not open it in the composer, which is the whole point.
        typ, _ = own.append(folder, "\\Draft", None, msg.as_bytes())
        if typ != "OK":
            log.warning("email.draft_refused", extra={"space": space, "conversation": zcid,
                                                      "folder": folder, "reply": str(typ)})
            return False
        log.info("email.draft_appended", extra={"space": space, "conversation": zcid,
                                                "folder": folder})
        return True
    except Exception as e:                               # noqa: BLE001 — see the docstring
        log.warning("email.draft_append_failed",
                    extra={"space": space, "conversation": zcid,
                           "error": f"{type(e).__name__}: {e}"[:160]})
        return False
    finally:
        if opened is not None:
            try:
                opened.logout()
            except Exception:                            # noqa: BLE001
                pass


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
            # THE BOX NEVER READS ITS OWN NOTICE AS A CUSTOMER. Skipped whole — no conversation,
            # no message row, no watermark opinion — because a notice is not a thing that happened
            # in this business's inbox, it is this box talking to its own owner.
            #
            # BEFORE `upsert_conversation`, which is the point. The existing `inbound` check files
            # an exact self-match as outbound and so never drafts a reply to it — real protection,
            # and it still CREATES a conversation whose participant is the buyer's own address.
            # A row per notice, twice a day, in the list of people who wrote to the business.
            if _header(msg, box_mail.ORIGIN_HEADER):
                continue
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
            # JUDGED HERE, WHERE THE HEADERS STILL EXIST, and only on INBOUND: whether a machine
            # is writing to this business is a fact about the other end, and the box's own
            # outbound tells us nothing about them.
            if inbound:
                store.mark_automated(space, zcid, is_automated(msg, addr))
            stored += 1

        # AND THE ROWS THAT WERE ALREADY THERE. The 35 robot drafts on the owner's box all predate
        # migration 56, so a column that only judged new mail would not have removed one of them.
        # Bounded, idempotent (it only ever reads rows still NULL), and on the sweep rather than a
        # new timer because it is finished after one pass on any real box.
        _judge_the_backlog(space)

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


# ── SENDING IT, FROM THE BUYER'S OWN ADDRESS ────────────────────────────────────────────────────
#
# Owner, 2026-09-22: *"There's gotta be a way to send email as well. Something is not built
# correctly."* He was right, and the gap was never technical — `window.py` has carried the reason
# as data since the channel shipped: *"the owner has not ruled on an email send policy."* He has
# now, so this is the transport that reason was waiting for.
#
# THEIR GOOGLE ACCOUNT, THEIR ADDRESS, THEIR SENDING REPUTATION. `smtplib` is stdlib and the
# credential is the app password already stored for reading, so this adds no vendor, no key, no
# spend and nothing to meter. A reply arrives from the business, in the customer's own thread,
# and Gmail files a copy in the buyer's Sent folder as if they had typed it.
#
# THE MESSAGE ID IS OURS, GENERATED BEFORE THE SEND, and that is load-bearing in two places.
# SMTP returns nothing to identify a message by, so without this there is no id for the ledger or
# the mirror. And because the mirror stores the same id the header carries, a copy that later
# comes back round through INBOX (a self-cc, a mailing list, a Workspace journal rule) is filed by
# `record_message`'s INSERT-OR-IGNORE as the message it already has, rather than as a second one.

_SEND_TIMEOUT_S = 25         # somebody IS standing in front of this one; longer than the draft's
_SUBJECT_TIMEOUT_S = 8       # a nicety on a person's click — it may not hold up their reply


class EmailSendIndeterminate(RuntimeError):
    """The message MAY have gone. Invariant 4: a timeout is never "it didn't land"."""


class EmailSendRefused(RuntimeError):
    """The server answered, and the answer was no. Nothing was queued.

    ITS OWN TYPE BECAUSE THE CALLER RESOLVES A LEDGER ROW ON IT, and "determinate" has to be
    carried by something the caller can catch. The first cut raised a plain RuntimeError here,
    which fell through `reply.py`'s last-resort `except Exception` — the arm that exists to treat
    an UNEXPECTED raise as unknown — and a 550 "no such user" was recorded as "may have landed".
    That is the wrong half of invariant 4: it is safe to be unsure, and expensive to be unsure
    when the server has already told you. The suite drives a refused recipient to hold this.
    """


def _smtp_host(cred: dict) -> str:
    from core.vendors.mailbox import smtp_host_for
    return smtp_host_for(cred.get("host") or "")


def subject_for(cred: dict, in_reply_to: str) -> str:
    """`Re: ` + the subject of the message being answered, or a plain fallback. NEVER RAISES.

    ITS OWN SHORT-LIVED CONNECTION, AND ITS OWN SHORT TIMEOUT. A person has clicked send, so this
    is the one place in the file where latency is a feature of the product rather than a detail of
    a worker. Eight seconds, and a mail server having a slow minute costs a subject line rather
    than the reply.

    A THREAD IS THREADED BY `References`, NOT BY ITS SUBJECT, which is what makes that trade safe.
    """
    if not in_reply_to:
        return "Re: your message"
    conn = None
    try:
        conn = imaplib.IMAP4_SSL(cred.get("host") or "imap.gmail.com", timeout=_SUBJECT_TIMEOUT_S)
        conn.login(cred["user"], cred["password"])
        conn.select(_FOLDER, readonly=True)              # read-only, like every other reader here
        got = _subject_of(conn, in_reply_to)
    except Exception:                                    # noqa: BLE001 — a nicety, never the reply
        got = ""
    finally:
        if conn is not None:
            try:
                conn.logout()
            except Exception:                            # noqa: BLE001
                pass
    if not got:
        return "Re: your message"
    return got if got.lower().startswith("re:") else f"Re: {got}"


def send(cred: dict, *, to: str, subject: str, body: str,
         in_reply_to: str = "", references: str = "") -> str:
    """Send one reply from the buyer's own mailbox. -> the Message-ID it went out with.

    RAISES, AND THE TYPE IS THE WHOLE CONTRACT, because the caller has a ledger row claimed and
    has to resolve it to exactly the right thing (invariant 4):

      EmailSendIndeterminate  it MAY have gone. A timeout, a disconnect, a 4xx — anything where
                              the server's answer never arrived. NEVER report these as failed:
                              "a timeout is INDETERMINATE, never assume it didn't land."
      EmailAuthError          Google refused the credential. Determinate: nothing was sent, and
                              the status it carries is the one the set-up row already renders.
      EmailSendRefused        the server said no, in a 5xx, about this message. Determinate.

    THE RECIPIENT IS REFUSED AT THE DOOR, not by the server. `sendmail` would happily take a
    header-injected address; an address with a newline in it is refused here, before a connection
    is opened, because the only thing downstream of that is somebody else's inbox.
    """
    to = str(to or "").strip()
    if "@" not in to or any(c in to for c in "\r\n"):
        raise EmailSendRefused("that conversation has no address to reply to")
    if any(c in str(in_reply_to) for c in "\r\n") or any(c in str(references) for c in "\r\n"):
        raise EmailSendRefused("that conversation's message id cannot be put in a header")

    sender = str(cred.get("user") or "")
    # THE DOMAIN COMES FROM THE SENDER so the id is plausibly theirs, which is what a receiving
    # server's heuristics expect. `make_msgid` supplies the uniqueness.
    mid = email.utils.make_msgid(domain=sender.rsplit("@", 1)[-1] or None)

    msg = email.message.EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject or "Re: your message"
    msg["Message-ID"] = mid
    msg["Date"] = email.utils.formatdate(localtime=True)
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = references or in_reply_to
    # NO `X-Ownbox` MARK, deliberately, and for the same reason the appended draft carries none:
    # that mark tells ingest to skip the BOX's own mail, and this is the buyer's. Marking it would
    # make the sweep discard the one outbound in the thread that matters most.
    msg.set_content(str(body))

    conn = None
    try:
        conn = smtplib.SMTP(_smtp_host(cred), 587, timeout=_SEND_TIMEOUT_S)
        conn.ehlo()
        conn.starttls()
        conn.ehlo()                                      # capabilities, re-read on the encrypted
        conn.login(cred["user"], cred["password"])       # channel — never the list read in clear
        conn.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise _classify_auth_failure(str(e)) from e
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused,
            smtplib.SMTPDataError, smtplib.SMTPNotSupportedError) as e:
        # THE SERVER ANSWERED, AND THE ANSWER WAS NO. Determinate: nothing was queued.
        raise EmailSendRefused(f"your mail server refused the message: {str(e)[:160]}") from e
    except Exception as e:                               # noqa: BLE001 — see the docstring
        # EVERYTHING ELSE IS UNKNOWN. A timeout, a reset, a disconnect after DATA — the message
        # may be queued, may be delivered, may be nowhere. The caller records "may have landed"
        # and never resends on its own.
        raise EmailSendIndeterminate(f"{type(e).__name__}: {str(e)[:160]}") from e
    finally:
        if conn is not None:
            try:
                conn.quit()
            except Exception:                            # noqa: BLE001 — a failed QUIT after a
                pass                                     # successful send is not a failed send
    log.info("email.sent", extra={"to": to[:80], "message_id": mid})
    return mid


def thread_tail(space: str, zcid: str) -> dict:
    """What a reply to this conversation has to be addressed and threaded with.

    -> {"to": ..., "in_reply_to": ..., "references": ...}, any of them "" when unknown.

    THE NEWEST INBOUND IS THE ONE BEING ANSWERED, not the newest message: replying to our own
    last outbound would address the business to itself, and on a thread with several people it
    would answer whoever spoke last rather than whoever asked.

    `references` PUTS THE THREAD ROOT FIRST and the parent last, which is the ordering RFC 5322
    §3.6.4 describes and every client threads on. `zcid` IS the root here — `thread_key` resolves
    a message to the first id in its own References chain — so the two are the whole chain we
    need to carry.
    """
    newest = None
    for m in store.messages_for(space, zcid):
        if str(m.get("direction")) == "in" and str(m.get("zernio_message_id") or ""):
            newest = m
    if not newest:
        return {"to": "", "in_reply_to": "", "references": ""}
    parent = str(newest.get("zernio_message_id") or "")
    return {"to": str(newest.get("sent_by") or ""), "in_reply_to": parent,
            "references": f"{zcid} {parent}" if zcid and zcid != parent else parent}


def _judge_the_backlog(space: str) -> int:
    """Mark the email threads that existed before anything looked. -> how many were marked.

    ADDRESS ONLY, because the headers are long gone — see `store.unjudged_email_senders`. Never
    raises: this is housekeeping attached to the sweep, and a customer's mail being mirrored must
    not depend on it.
    """
    try:
        rows = store.unjudged_email_senders(space)
    except Exception as e:                               # noqa: BLE001 — a box without the column
        log.info("email.backlog_unreadable", extra={"error": type(e).__name__})
        return 0
    marked = 0
    for r in rows:
        try:
            store.mark_automated(space, r["zcid"], is_automated(None, r["sender"]))
            marked += 1
        except Exception:                                # noqa: BLE001 — one row never stops the rest
            continue
    if marked:
        log.info("email.backlog_judged", extra={"space": space, "rows": marked})
    return marked
