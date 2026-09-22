"""Reading a mailbox and sending from it are two permissions on one password.

§1.2 of docs/SCOPE_EMAIL_SEND_AND_MOBILE_NOTIFICATIONS.md, the set-up half. A Google Workspace
administrator can leave IMAP on and turn SMTP off, so the app password that opens a buyer's
mailbox perfectly may be unable to send a single message. Nothing asked, and they find out at the
worst possible moment: a customer is waiting, they press send, it fails.

THE PROPERTY THIS SUITE EXISTS TO HOLD, and it is the one an eager version gets wrong: a failed
SEND check must never cost a buyer a working mailbox. Reading is most of what this box does today
and all of what it did yesterday. Refusing to store a credential that demonstrably reads, because
a feature that has not shipped yet might not work, breaks the thing that works to protect the
thing that does not.

The second is `unknown` vs `refused`. "We could not reach your server" and "your server said no"
look identical to a lazy reader and mean opposite things to a buyer: one waits a minute, the
other goes and argues with an administrator about a setting that was never off.

Run: python tests/test_the_password_can_send_too.py
"""
from __future__ import annotations

import imaplib
import os
import smtplib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
# THIS SUITE IS THE ONE THAT DRIVES THE SEND CHECK, so it turns the check back on — the same
# opt-in-first shape `core.slack` uses. Every socket below still lands in the fake server.
os.environ["AIOS_ALLOW_SMTP_CHECK"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "send.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state  # noqa: E402

state.init_db()

from core import box_secrets as bs  # noqa: E402
from core.vendors.mailbox import verify as mv  # noqa: E402

FAILS: list[str] = []
OWNER = "owner@acme.com"
APP_PW = "abcdefghijklmnop"


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


def quiet(fn, *a, **k):
    import io
    from contextlib import redirect_stderr, redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        return fn(*a, **k)


class FakeIMAP:
    """A mailbox that opens. The read half is held by tests/test_inbox_email_channel.py."""

    def __init__(self, host, **kw):
        self.host = host

    def login(self, user, password):
        return ("OK", [b""])

    def select(self, folder, readonly=False):
        assert readonly is True, "verification must never mark the buyer's mail as read"
        return ("OK", [b"1"])

    def logout(self):
        return ("BYE", [b""])


class FakeSMTP:
    """A submission server. Records the exchange, and NEVER accepts a message.

    `sendmail` raises rather than returning: a verifier that sent anything — even to the buyer's
    own address — would put mail in a stranger's inbox every time somebody pasted a password.
    """

    last: "FakeSMTP | None" = None

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.steps: list[str] = []
        self.raise_on_login: Exception | None = None
        FakeSMTP.last = self

    def ehlo(self, *a):
        self.steps.append("EHLO")
        return (250, b"ok")

    def starttls(self, *a, **k):
        self.steps.append("STARTTLS")
        return (220, b"ready")

    def login(self, user, password):
        self.steps.append("LOGIN")
        if self.raise_on_login:
            raise self.raise_on_login
        return (235, b"accepted")

    def sendmail(self, *a, **k):
        raise AssertionError("a verifier sent a message — nothing may leave the box here")

    def quit(self):
        self.steps.append("QUIT")
        return (221, b"bye")


def install(*, smtp_error: Exception | None = None, smtp_ctor: Exception | None = None) -> None:
    imaplib.IMAP4_SSL = lambda host, **kw: FakeIMAP(host, **kw)   # type: ignore[assignment]

    def factory(host, port, timeout=None):
        if smtp_ctor:
            raise smtp_ctor
        f = FakeSMTP(host, port, timeout)
        f.raise_on_login = smtp_error
        return f
    smtplib.SMTP = factory                                        # type: ignore[assignment]


# ── THE HAPPY PATH, AND WHAT IT PROVES ABOUT THE WIRE ───────────────────────────────────────
print("\n— A PASSWORD THAT READS AND SENDS —")
install()
quiet(bs.put_email, host="imap.gmail.com", user=OWNER, password=APP_PW)
st = bs.email_state()
ok("the mailbox is connected", st["status"] == "connected", str(st))
ok("...and the box knows it can send", st["send"] == "can_send", str(st))
ok("...with no leftover sentence under it", not st["send_detail"], str(st["send_detail"]))

f = FakeSMTP.last
ok("it went to the SUBMISSION host and port, derived from the mailbox host",
   f.host == "smtp.gmail.com" and f.port == 587, f"{f.host}:{f.port}")
ok("STARTTLS ran, and EHLO ran again AFTER it — the capability list is read on the encrypted "
   "channel, never the one in the clear",
   f.steps[:4] == ["EHLO", "STARTTLS", "EHLO", "LOGIN"], str(f.steps))
ok("...and it authenticated and stopped: no message, no recipient, nothing sent",
   "sendmail" not in f.steps and f.steps[-1] == "QUIT", str(f.steps))

# ── THE ONE THAT MATTERS ────────────────────────────────────────────────────────────────────
print("\n— A MAILBOX THAT CANNOT SEND IS STILL A CONNECTED MAILBOX —")
quiet(bs.clear_email)
install(smtp_error=smtplib.SMTPAuthenticationError(
    534, b"5.7.9 Application-specific password required / SMTP disabled by administrator"))
quiet(bs.put_email, host="imap.gmail.com", user=OWNER, password=APP_PW)
st = bs.email_state()
ok("the credential IS STORED — a send check never costs a buyer a working mailbox",
   bool(bs.email_credential()), str(bs.email_credential().get("user")))
ok("...and it still reads Connected, because it demonstrably reads",
   st["status"] == "connected", str(st["status"]))
ok("...while sending is recorded as refused", st["send"] == "refused", str(st["send"]))
ok("...and the sentence names the administrator, not the password the box just used to log in",
   "administrator" in st["send_detail"].lower(), st["send_detail"])

# ── REFUSED IS NOT THE SAME AS UNREACHABLE ──────────────────────────────────────────────────
print("\n— 'WE COULD NOT ASK' IS NOT 'YOU CANNOT SEND' —")
quiet(bs.clear_email)
install(smtp_ctor=OSError("Name or service not known"))
quiet(bs.put_email, host="imap.gmail.com", user=OWNER, password=APP_PW)
st = bs.email_state()
ok("a box that could not reach the server says unknown, never refused",
   st["send"] == "unknown", str(st["send"]))
ok("...and says so in words that do not send anybody to their administrator",
   "not a problem with your password" in st["send_detail"].lower(), st["send_detail"])
ok("...and the mailbox is connected regardless", st["status"] == "connected", str(st["status"]))

# A SERVER THAT WILL NOT DO STARTTLS. That is an answer, so it is a refusal — and the box does
# not fall back to sending a password in the clear, which is the only other way to "succeed".
quiet(bs.clear_email)
install(smtp_error=smtplib.SMTPNotSupportedError("SMTP AUTH extension not supported by server"))
quiet(bs.put_email, host="imap.gmail.com", user=OWNER, password=APP_PW)
ok("a server that will not take an encrypted sign-in is refused, not worked around",
   bs.email_state()["send"] == "refused", str(bs.email_state()["send"]))

# ── WHAT A SCREEN GETS, AND WHAT DISCONNECTING TAKES AWAY ───────────────────────────────────
print("\n— THE VERDICT LIVES AND DIES WITH THE CREDENTIAL —")
quiet(bs.clear_email)
install()
quiet(bs.put_email, host="imap.gmail.com", user=OWNER, password=APP_PW)
ok("connected and able to send, to start with", bs.email_state()["send"] == "can_send")
quiet(bs.clear_email)
st = bs.email_state()
ok("disconnecting clears the send verdict too — a box that reads no mailbox claims no sending",
   st["status"] == "not_connected" and st["send"] == "unknown", str(st))
ok("...and every row is gone, not just the ones a screen remembered to name",
   not any(bs.get(n) for n in (bs.EMAIL, bs.EMAIL_STATUS, bs.EMAIL_DETAIL,
                               bs.EMAIL_SEND, bs.EMAIL_SEND_DETAIL)),
   str([n for n in (bs.EMAIL, bs.EMAIL_STATUS, bs.EMAIL_DETAIL, bs.EMAIL_SEND,
                    bs.EMAIL_SEND_DETAIL) if bs.get(n)]))
# THIS USED TO READ marketing/customer_voice/app.py AND CI CAUGHT IT — on the EXPORTED-BOX run,
# not here. This suite imports only `core`, so export_box.sh ships it to every box type, and a
# Lead or Content box has no customer_voice directory at all: FileNotFoundError, on a box whose
# owner has no inbox screen to check. A core suite reaching into a machine is the bug, not the
# missing file.
#
# SO IT ASSERTS THE PROPERTY THAT IS CORE'S. The point of `clear_email` was never "one particular
# screen calls it" — it was that the store knows what a mailbox connection is made of, so nothing
# else has to keep a list in step. That is measured by DERIVING the list rather than writing one:
# every EMAIL row `box_secrets` defines must be cleared, so the next row added is covered the day
# it is added, by a check that cannot go stale. The hand-written version would have passed while
# forgetting exactly the row this PR introduces.
_email_names = sorted(n for n in dir(bs)
                      if n.startswith("EMAIL") and isinstance(getattr(bs, n), str))
ok("box_secrets defines more than one EMAIL row, so a list IS a thing to keep in step",
   len(_email_names) >= 4, str(_email_names))
for _n in _email_names:
    bs.put(getattr(bs, _n), "x" if _n not in ("EMAIL",) else '{"host":"h","user":"u@x.co",'
           '"password":"p"}')
quiet(bs.clear_email)
_left = [n for n in _email_names if bs.get(getattr(bs, n))]
ok("...and clear_email clears EVERY one of them, derived from the store rather than listed here",
   not _left, str(_left))

# A BOX CONNECTED BEFORE THIS CHECK EXISTED has no row, and "unknown" is the honest reading of
# that — not "no". Nothing migrates; the next reconnect answers it.
quiet(bs.clear_email)
import json as _json  # noqa: E402
bs.put(bs.EMAIL, _json.dumps({"host": "imap.gmail.com", "user": OWNER, "password": APP_PW}))
ok("a mailbox connected before this shipped reads unknown, and is not called unable to send",
   bs.email_state()["send"] == "unknown", str(bs.email_state()["send"]))

# ── THE HOST RULE, SAID OUT LOUD ────────────────────────────────────────────────────────────
print("\n— THE SUBMISSION HOST IS DERIVED, AND THE RULE IS NAMED —")
ok("imap.gmail.com -> smtp.gmail.com", mv.smtp_host_for("imap.gmail.com") == "smtp.gmail.com")
ok("...the same convention on any provider that follows it",
   mv.smtp_host_for("imap.fastmail.com") == "smtp.fastmail.com",
   mv.smtp_host_for("imap.fastmail.com"))
ok("an empty host falls back to Gmail, which is what every box ships with",
   mv.smtp_host_for("") == "smtp.gmail.com")
ok("a host that does not follow it is left alone rather than mangled",
   mv.smtp_host_for("mail.example.com") == "mail.example.com",
   mv.smtp_host_for("mail.example.com"))
ok("587 with STARTTLS, not 465 — submission, per RFC 6409", mv.SMTP_PORT == 587)

# ── AND IT NEVER REACHES A REAL SERVER FROM A TEST RUNNER ───────────────────────────────────
#
# `find_linkedin.py` records what this costs when nobody guards it: a suite reached a real vendor
# and bought a credit. This one buys nothing, but it waits — and because an unreachable server is
# recorded as `unknown`, which is where a box already starts, it waits SILENTLY. Measured before
# it shipped: tests/test_mailbox_screen.py went from 1.7s to 68s and stayed green throughout.
print("\n— A TEST RUNNER NEVER OPENS A SOCKET TO A MAIL SERVER —")
ok("hermetic, CI and GitHub Actions each stop the check on their own",
   not mv._may_ask({"AIOS_HERMETIC_TEST": "1"})
   and not mv._may_ask({"CI": "true"})
   and not mv._may_ask({"GITHUB_ACTIONS": "true"}))
ok("...and the opt-in comes FIRST, so a suite that means to drive it can",
   mv._may_ask({"AIOS_HERMETIC_TEST": "1", "CI": "true", "AIOS_ALLOW_SMTP_CHECK": "1"}))
ok("a real box, with none of those set, asks normally", mv._may_ask({}))

# WHAT A BLOCKED CHECK ANSWERS, and it matters: `unknown` with NO sentence. A box that never
# asked must not show a buyer an explanation for something that did not happen.
_blocked = mv.verify_send.__wrapped__ if hasattr(mv.verify_send, "__wrapped__") else mv.verify_send
_saved = os.environ.pop("AIOS_ALLOW_SMTP_CHECK")
try:
    got = _blocked("imap.gmail.com", OWNER, APP_PW)
finally:
    os.environ["AIOS_ALLOW_SMTP_CHECK"] = _saved
ok("a blocked check answers unknown with no sentence — it did not happen, so it explains nothing",
   got == (False, "unknown", ""), str(got))

print()
if FAILS:
    print(f"FAILED {len(FAILS)}: " + "; ".join(FAILS))
    sys.exit(1)
print("all ok")
