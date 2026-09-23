"""The business's own sent mail, in its own words. Reads; never thinks, never sends.

THE SPLIT IS THE ARCHITECTURE, not tidiness. Only `drafter/` may call a model, and it may not
touch `imaplib`, `email` or the filesystem — measured: tests/test_customer_voice.py refused this
file when it lived there, listing exactly those three imports. So reading is here, where IMAP
already lives, the thinking is in `drafter/learn_business.py`, and the machine root wires the two
together. Neither half can do the other's job, which is the point.

READ-ONLY AND `BODY.PEEK`, the same two guards `email_channel` keeps: a box that marked its
owner's sent mail as unread while learning from it would be a bug he could see.
"""
from __future__ import annotations

import email
import imaplib
import re

from core import box_secrets, state
from core.logging import get_logger

log = get_logger(__name__)

_MAX_MESSAGES = 150          # a trading business says everything it knows in a hundred and fifty
_MAX_CHARS_EACH = 1200       # a quoted thread is mostly the other person; the top is the answer
_MAX_CHARS_TOTAL = 90_000    # one model call, bounded
_TIMEOUT_S = 60.0

# A reply's own words end where the quoted original begins.
_QUOTE = re.compile(r"^(On .{0,120}wrote:|-{2,}\s*Original Message|From:\s)", re.M)
_SIG = re.compile(r"^--\s*$", re.M)


def _folder(conn) -> str:
    """The Sent folder by its RFC 6154 \\Sent attribute — never a hard-coded name, which is
    localised ([Gmail]/Sent Mail in English and something else entirely in French)."""
    typ, boxes = conn.list()
    if typ != "OK":
        return ""
    for raw in boxes or []:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        if "\\Sent" in line:
            return line.split(' "/" ')[-1].strip() or ""
    return ""


def _own_words(msg) -> str:
    """What the business actually typed, without the thread it quoted back."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                body = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
                break
    else:
        body = (msg.get_payload(decode=True) or b"").decode("utf-8", "replace")
    body = _QUOTE.split(body)[0]
    body = _SIG.split(body)[0]
    return re.sub(r"\n{3,}", "\n\n", body).strip()[:_MAX_CHARS_EACH]


def _our_own_drafts() -> set:
    """Every reply this box wrote, normalised. Read from `inbox_drafts`, which is where they live.

    THE BOX MUST NOT LEARN FROM ITSELF. A draft the buyer sends lands in his Sent folder like
    anything else, so tomorrow's read would take the box's own words as "how this business
    writes" — and the day after it would learn from that. Measured on the owner's box: the three
    newest things in his Sent folder were the box's own generic drafts. Left alone, the learner
    would have taught itself its own worst habits, which is the Morning Review loop wearing
    another face (`who_wrote.BULK_HEADERS` carries `x-ownbox` for the same reason).
    """
    try:
        with state.connect() as c:
            return {" ".join(str(r["body"] or "").split()).strip().casefold()
                    for r in c.execute("SELECT body FROM inbox_drafts")}
    except Exception:                                # noqa: BLE001 — a box without the table yet
        return set()


def read_sent(limit: int = _MAX_MESSAGES) -> list[str]:
    """The newest things this business sent, in its own words. Inert with no mailbox."""
    cred = box_secrets.email_credential()
    if not cred:
        return []
    conn = imaplib.IMAP4_SSL(cred.get("host") or "imap.gmail.com", timeout=_TIMEOUT_S)
    try:
        conn.login(cred["user"], cred["password"])
        folder = _folder(conn)
        if not folder:
            log.info("sent.no_folder")
            return []
        conn.select(folder, readonly=True)
        typ, data = conn.uid("SEARCH", None, "ALL")
        uids = (data[0] or b"").split()[-limit:] if typ == "OK" else []
        ours = _our_own_drafts()
        out, total, skipped = [], 0, 0
        for uid in reversed(uids):
            typ, d = conn.uid("FETCH", uid, "(BODY.PEEK[])")
            if typ != "OK" or not d or not isinstance(d[0], tuple):
                continue
            words = _own_words(email.message_from_bytes(d[0][1]))
            if len(words) < 40:                      # "thanks!" teaches nothing
                continue
            if " ".join(words.split()).strip().casefold() in ours:
                skipped += 1                         # the box's own draft, sent back to itself
                continue
            out.append(words)
            total += len(words)
            if total >= _MAX_CHARS_TOTAL:
                break
        if skipped:
            log.info("sent.skipped_our_own", extra={"skipped": skipped, "kept": len(out)})
        return out
    finally:
        try:
            conn.logout()
        except Exception:                            # noqa: BLE001
            pass
