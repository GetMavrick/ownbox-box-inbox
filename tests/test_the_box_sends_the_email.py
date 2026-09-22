"""The box sends the reply itself, from the buyer's own address, into the customer's thread.

§1.2 of docs/SCOPE_EMAIL_SEND_AND_MOBILE_NOTIFICATIONS.md. Owner, 2026-09-22: *"If we can't auto
draft emails and then actually go ahead and send them. This is a completely worthless app."* and
*"There's gotta be a way to send email as well. Something is not built correctly."*

The gap was never technical. `window.py` carried the reason as data from the day the channel
shipped — *"the owner has not ruled on an email send policy"* — and it was waiting on him.

WHAT THIS SUITE IS FOR, AND IT IS NOT "DOES IT SEND". Three things are easy to get wrong here and
every one of them is expensive:

  1. A TIMEOUT IS NOT A FAILURE (invariant 4). SMTP can accept a message and then lose the
     connection before it answers. Recorded as failed, the screen invites a retry and the
     customer gets the same reply twice. It is INDETERMINATE, and this suite drives every exit of
     `email_channel.send` to prove which is which.
  2. THE REPLY MUST LAND IN THE THREAD, addressed to whoever actually asked — not to the last
     person who spoke, and not to the business itself.
  3. NOTHING MAY BE METERED. Email leaves through the buyer's own mailbox, so a vendor charge for
     it is a charge for a message no vendor carried.

AND NOTHING LEAVES THE BOX HERE. The fake server records what it is handed and never connects to
anything; the real `smtplib` is replaced before the first test.

Run: python tests/test_the_box_sends_the_email.py
"""
from __future__ import annotations

import email as email_mod
import email.policy
import imaplib
import io
import os
import smtplib
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "sendmail.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state  # noqa: E402

state.init_db()

from core import box_secrets as bs  # noqa: E402
from marketing.customer_voice.inbox import email_channel as ec  # noqa: E402
from marketing.customer_voice.inbox import reply, store  # noqa: E402

FAILS: list[str] = []
SPACE = "acme"
OWNER = "owner@acme.com"
APP_PW = "abcdefghijklmnop"
U = "usr_sender"


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


def quiet(fn, *a, **k):
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        return fn(*a, **k)


class FakeSMTP:
    """A submission server that records the message and can fail in each way a real one does."""

    last: "FakeSMTP | None" = None

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.steps: list[str] = []
        self.messages: list = []
        self.raise_on_login = None
        self.raise_on_send = None
        FakeSMTP.last = self

    def ehlo(self, *a):
        self.steps.append("EHLO")
        return (250, b"ok")

    def starttls(self, *a, **k):
        self.steps.append("STARTTLS")
        return (220, b"go")

    def login(self, user, password):
        self.steps.append("LOGIN")
        if self.raise_on_login:
            raise self.raise_on_login
        return (235, b"ok")

    def send_message(self, msg):
        self.steps.append("SEND")
        if self.raise_on_send:
            raise self.raise_on_send
        self.messages.append(msg)
        return {}

    def sendmail(self, *a, **k):
        raise AssertionError("send_message is the path; sendmail bypasses header handling")

    def quit(self):
        self.steps.append("QUIT")
        return (221, b"bye")


class FakeIMAP:
    """Only ever asked for a Subject here. Read-only, like every other reader in this channel."""

    def __init__(self, host, **kw):
        self.subject = "Quote for the back garden"

    def login(self, u, p):
        return ("OK", [b""])

    def select(self, folder, readonly=False):
        assert readonly is True, "a subject fetch must never mark the buyer's mail as read"
        return ("OK", [b"1"])

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            return ("OK", [b"1"])
        if cmd == "FETCH":
            assert "PEEK" in args[1]
            return ("OK", [(b"1 (UID x)",
                            f"Subject: {self.subject}\r\nMessage-ID: <q1@buyer.com>\r\n\r\n"
                            .encode())])
        return ("NO", [b""])

    def logout(self):
        return ("BYE", [b""])


def install(*, on_login=None, on_send=None, imap=True) -> None:
    def smtp_factory(host, port, timeout=None):
        f = FakeSMTP(host, port, timeout)
        f.raise_on_login, f.raise_on_send = on_login, on_send
        return f
    smtplib.SMTP = smtp_factory                                   # type: ignore[assignment]
    imaplib.IMAP4_SSL = ((lambda host, **kw: FakeIMAP(host, **kw)) if imap
                         else _boom)                              # type: ignore[assignment]


def _boom(host, **kw):
    raise OSError("no mailbox server")


install()
quiet(bs.put_email, host="imap.gmail.com", user=OWNER, password=APP_PW)


def seed(zcid: str, *, mid: str, frm: str = "jane@buyer.com") -> None:
    store.upsert_conversation(space=SPACE, zcid=zcid, platform="email",
                              participant=frm, account_id=OWNER,
                              last_inbound_at=datetime.now(timezone.utc).isoformat())
    store.record_message(space=SPACE, zcid=zcid, zmid=mid, direction="in",
                         sent_by=frm, body="Are you free Tuesday?")


# ── IT GOES, AND IT GOES INTO THE THREAD ────────────────────────────────────────────────────
print("\n— the reply leaves from their address, in the customer's thread —")
install()
seed("<q1@buyer.com>", mid="<q1@buyer.com>")
res = quiet(reply.send_reply, space=SPACE, zcid="<q1@buyer.com>",
            text="Tuesday at 10 works — see you then.", user_id=U, nonce=reply.new_nonce())
ok("the send reports ok", res.get("status") == "ok", str(res))
f = FakeSMTP.last
ok("...to the SUBMISSION host and port", (f.host, f.port) == ("smtp.gmail.com", 587),
   f"{f.host}:{f.port}")
ok("...with STARTTLS, and EHLO again after it so AUTH is read on the encrypted channel",
   f.steps[:5] == ["EHLO", "STARTTLS", "EHLO", "LOGIN", "SEND"], str(f.steps))

m = f.messages[-1]
ok("From: is the buyer's own address", m["From"] == OWNER, str(m["From"]))
ok("To: is the customer who asked, read off the message being answered",
   m["To"] == "jane@buyer.com", str(m["To"]))
ok("In-Reply-To: names that message", m["In-Reply-To"] == "<q1@buyer.com>", str(m["In-Reply-To"]))
ok("References: carries the thread", "<q1@buyer.com>" in str(m["References"]),
   str(m["References"]))
ok("Subject: is the thread's own, fetched and prefixed",
   m["Subject"] == "Re: Quote for the back garden", str(m["Subject"]))
ok("the body is what was typed", "Tuesday at 10 works" in m.get_content(), m.get_content()[:60])
# THE MARK WOULD BE A BUG HERE, exactly as on the appended draft: `X-Ownbox` tells ingest to skip
# the BOX's own mail, and this is the buyer's. Marked, the sweep would discard their own reply.
ok("no X-Ownbox mark — the buyer's own send must not be skipped by ingest",
   m.get("X-Ownbox") is None, str(m.get("X-Ownbox")))

# THE MESSAGE ID IS OURS AND IT IS THE LEDGER'S. SMTP returns nothing to identify a message by.
mid = res.get("message_id")
ok("the reply carries the id the send reported", m["Message-ID"] == mid, f"{m['Message-ID']} vs {mid}")
ok("...and it is a real RFC 5322 id on the buyer's own domain",
   mid.startswith("<") and mid.endswith(">") and "acme.com" in mid, str(mid))

# AND THE THREAD READS RIGHT WITHOUT WAITING FOR A POLL.
_msgs = store.messages_for(SPACE, "<q1@buyer.com>")
_out = [x for x in _msgs if x["direction"] == "out"]
ok("the thread shows the reply at once, as a human's", len(_out) == 1
   and _out[0]["sent_by"] == "human", str(_out))
ok("...filed under the same id the header carries, so a copy coming back is not a second message",
   _out[0]["zernio_message_id"] == mid, str(_out[0]["zernio_message_id"]))

# ── A TIMEOUT IS INDETERMINATE, NEVER FAILED ────────────────────────────────────────────────
print("\n— a timeout may have landed, and is never called a failure —")
install(on_send=TimeoutError("timed out waiting for 250"))
seed("<q2@buyer.com>", mid="<q2@buyer.com>")
_n2 = reply.new_nonce()
try:
    quiet(reply.send_reply, space=SPACE, zcid="<q2@buyer.com>", text="On our way.",
          user_id=U, nonce=_n2)
    ok("a timeout is reported as indeterminate", False, "it returned ok")
except reply.ReplyIndeterminate:
    ok("a timeout is reported as indeterminate", True)
except Exception as e:                                   # noqa: BLE001
    ok("a timeout is reported as indeterminate", False, f"{type(e).__name__}: {e}")
_led = store.get_send(SPACE, reply.idem_for(SPACE, "<q2@buyer.com>", U, _n2)) or {}
ok("...and the ledger says so, so nothing resends it on our behalf",
   _led.get("status") == "indeterminate", str(_led.get("status")))
ok("...and no outbound was mirrored, because we do not know that it went",
   not [x for x in store.messages_for(SPACE, "<q2@buyer.com>") if x["direction"] == "out"])

# A DISCONNECT MID-DATA IS THE SAME CLASS OF UNKNOWN.
install(on_send=smtplib.SMTPServerDisconnected("connection closed"))
seed("<q3@buyer.com>", mid="<q3@buyer.com>")
try:
    quiet(reply.send_reply, space=SPACE, zcid="<q3@buyer.com>", text="Hello.",
          user_id=U, nonce=reply.new_nonce())
    ok("a disconnect is indeterminate too", False, "it returned ok")
except reply.ReplyIndeterminate:
    ok("a disconnect is indeterminate too", True)
except Exception as e:                                   # noqa: BLE001
    ok("a disconnect is indeterminate too", False, f"{type(e).__name__}: {e}")

# ── A 5xx IS DETERMINATE, AND SAYS SO ───────────────────────────────────────────────────────
print("\n— a server that says no is a failure, and a retry is honest —")
install(on_send=smtplib.SMTPRecipientsRefused({"jane@buyer.com": (550, b"No such user")}))
seed("<q4@buyer.com>", mid="<q4@buyer.com>")
_n4 = reply.new_nonce()
try:
    quiet(reply.send_reply, space=SPACE, zcid="<q4@buyer.com>", text="Hello.",
          user_id=U, nonce=_n4)
    ok("a refused recipient is a determinate failure", False, "it returned ok")
except reply.ReplyRefused:
    ok("a refused recipient is a determinate failure", True)
except Exception as e:                                   # noqa: BLE001
    ok("a refused recipient is a determinate failure", False, f"{type(e).__name__}: {e}")
_led = store.get_send(SPACE, reply.idem_for(SPACE, "<q4@buyer.com>", U, _n4)) or {}
ok("...recorded as failed, which is what lets them try again", _led.get("status") == "failed",
   str(_led.get("status")))

# ── A REVOKED PASSWORD IS SAID IN WORDS, AND MOVES THE SET-UP ROW ───────────────────────────
print("\n— a revoked app password tells the buyer where to fix it —")
install(on_login=smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted"))
seed("<q5@buyer.com>", mid="<q5@buyer.com>")
try:
    quiet(reply.send_reply, space=SPACE, zcid="<q5@buyer.com>", text="Hello.",
          user_id=U, nonce=reply.new_nonce())
    ok("a refused credential is a determinate failure", False, "it returned ok")
except reply.ReplyRefused as e:
    ok("a refused credential is a determinate failure", True)
    ok("...in words about the password, not a class name",
       "password" in str(e).lower(), str(e)[:100])
except Exception as e:                                   # noqa: BLE001
    ok("a refused credential is a determinate failure", False, f"{type(e).__name__}: {e}")
# THE BUYER CANNOT FIX THIS FROM THE THREAD, so the mailbox row learns it here rather than
# waiting for the poller to notice on its own schedule.
ok("...and the set-up row now says the mailbox needs attention",
   bs.email_state()["status"] in ("needs_reauth", "admin_disabled"),
   str(bs.email_state()["status"]))

# ── THE SUBJECT IS A NICETY, NEVER THE REPLY ────────────────────────────────────────────────
print("\n— a mailbox that will not answer costs a subject line, not the message —")
install(imap=False)
bs.put(bs.EMAIL_STATUS, "connected")
seed("<q6@buyer.com>", mid="<q6@buyer.com>")
res6 = quiet(reply.send_reply, space=SPACE, zcid="<q6@buyer.com>", text="Yes, Tuesday.",
             user_id=U, nonce=reply.new_nonce())
ok("the reply still goes when the subject cannot be read", res6.get("status") == "ok", str(res6))
ok("...with a plain reply line rather than nothing",
   FakeSMTP.last.messages[-1]["Subject"] == "Re: your message",
   str(FakeSMTP.last.messages[-1]["Subject"]))

print()
if FAILS:
    print(f"FAILED {len(FAILS)}: " + "; ".join(FAILS))
    sys.exit(1)
print("all ok")
