"""Email as an inbox channel (marketing/customer_voice/inbox/email_channel.py) — spec #1226 §7.

THE FIRST TEST IS THE ONE THAT MATTERS. `lead_machine/reply_ingest.py` polls UNSEEN and sets the
`\\Seen` flag after processing, which is right for the dedicated GTM mailbox it owns. Copied onto a
customer's real inbox it marks every message the box reads as READ in their Gmail — they open their
phone and their own inbox has been silently marked up by software they just bought. The fake server
below fails the run if this code ever issues a STORE, or opens the mailbox writable.

Run: python tests/test_inbox_email_channel.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "email.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state  # noqa: E402

state.init_db()

import imaplib  # noqa: E402

from core import box_secrets as bs  # noqa: E402
from marketing.customer_voice.inbox import email_channel as ec  # noqa: E402
email_channel_sweep = ec.sweep
from marketing.customer_voice.inbox import channels, store, window  # noqa: E402

FAILS: list[str] = []
SPACE = "acme"
OWNER = "owner@acme.com"
APP_PW = "abcdefghijklmnop"


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


def mail(mid: str, *, refs: str = "", frm: str = "Jane Roe <jane@buyer.com>",
         body: str = "hello") -> bytes:
    ref_line = f"References: {refs}\n" if refs else ""
    return (f"From: {frm}\nTo: {OWNER}\nSubject: Quote\nMessage-ID: {mid}\n{ref_line}"
            f"Date: Tue, 15 Sep 2026 10:00:00 +0000\nContent-Type: text/plain\n\n{body}\n"
            ).encode()


class FakeIMAP:
    """Records every command. Refuses to implement STORE at all — see the module docstring."""

    instances: list["FakeIMAP"] = []

    def __init__(self, host):
        self.host = host
        self.commands: list[str] = []
        self.readonly = None
        self.uidvalidity = b"100"
        self.messages: dict[int, bytes] = {}
        self.fail_login: str | None = None
        FakeIMAP.instances.append(self)

    def login(self, user, password):
        self.commands.append("LOGIN")
        if self.fail_login:
            raise imaplib.IMAP4.error(self.fail_login)
        return ("OK", [b""])

    def select(self, folder, readonly=False):
        self.commands.append(f"SELECT readonly={readonly}")
        self.readonly = readonly
        return ("OK", [b"1"])

    def response(self, name):
        return (name, [self.uidvalidity])

    def uid(self, cmd, *args):
        self.commands.append(f"UID {cmd}")
        if cmd == "SEARCH":
            return ("OK", [b" ".join(str(u).encode() for u in sorted(self.messages))])
        if cmd == "FETCH":
            u = int(args[0])
            # THE ASSERTION THAT PROTECTS THE BUYER'S INBOX: a real server sets \Seen for BODY[]
            # and leaves it alone for BODY.PEEK[]. Anything else here is the bug.
            if "PEEK" not in args[1]:
                raise AssertionError(f"fetched without PEEK: {args[1]}")
            return ("OK", [(b"1 (UID x)", self.messages[u])])
        return ("NO", [b""])

    def store(self, *a):
        raise AssertionError("STORE issued — this would mark the buyer's own mail as read")

    def logout(self):
        self.commands.append("LOGOUT")
        return ("BYE", [b""])


def install(**kw) -> FakeIMAP:
    FakeIMAP.instances.clear()
    holder = {}

    def factory(host):
        f = FakeIMAP(host)
        for k, v in kw.items():
            setattr(f, k, v)
        holder["f"] = f
        return f
    imaplib.IMAP4_SSL = factory                                   # type: ignore[assignment]
    return holder


def quiet(fn, *a, **k):
    import io
    from contextlib import redirect_stderr, redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        out = fn(*a, **k)
    return out


quiet(bs.put_email, host="imap.gmail.com", user=OWNER, password=APP_PW)

print("\n— THE BUYER'S OWN INBOX IS NEVER MARKED READ —")
h = install(messages={1: mail("<a@x.com>")})
scanned, stored = quiet(ec.sweep, SPACE)
f = h["f"]
ok("every fetch used BODY.PEEK, so no message is flagged as read", scanned == 1 and stored == 1)
ok("...and the mailbox was opened READ-ONLY, a second independent guard",
   f.readonly is True, str(f.readonly))
ok("...and no STORE command was ever issued", not any(c.startswith("STORE") for c in f.commands),
   str(f.commands))

print("\n— exactly-once, by the RFC Message-ID —")
h = install(messages={1: mail("<a@x.com>")})
quiet(ec.sweep, SPACE)                          # same message, fresh sweep, watermark reset below
store.set_watermark(SPACE, "__mailbox__", last_seen_msg_id=None, last_activity=None)
h = install(messages={1: mail("<a@x.com>")})
quiet(ec.sweep, SPACE)
rows = store.messages_for(SPACE, "<a@x.com>")
ok("re-reading the same message writes one row, not two", len(rows) == 1, f"{len(rows)} rows")

print("\n— threading: a chain is one conversation —")
store.set_watermark(SPACE, "__mailbox__", last_seen_msg_id=None, last_activity=None)
h = install(messages={
    10: mail("<root@x.com>"),
    11: mail("<second@x.com>", refs="<root@x.com>"),
    12: mail("<third@x.com>", refs="<root@x.com> <second@x.com>"),
})
quiet(ec.sweep, SPACE)
ok("three messages in a chain land as ONE conversation, keyed on the References root",
   len(store.messages_for(SPACE, "<root@x.com>")) == 3,
   str(len(store.messages_for(SPACE, "<root@x.com>"))))

print("\n— UIDVALIDITY —")
store.set_watermark(SPACE, "__mailbox__", last_seen_msg_id="100:99", last_activity=None)
h = install(messages={5: mail("<afterreset@x.com>")}, uidvalidity=b"777")
scanned, _ = quiet(ec.sweep, SPACE)
ok("a changed UIDVALIDITY resets the position rather than skipping every message",
   scanned == 1, f"scanned={scanned}")
store.set_watermark(SPACE, "__mailbox__", last_seen_msg_id="100:99", last_activity=None)
h = install(messages={5: mail("<x@x.com>")}, uidvalidity=b"100")
scanned, _ = quiet(ec.sweep, SPACE)
ok("...and an UNCHANGED one keeps it, so old mail is not re-ingested", scanned == 0,
   f"scanned={scanned}")

print("\n— what Google's refusals mean, told to the buyer —")
h = install(fail_login="[AUTHENTICATIONFAILED] Invalid credentials (Failure)")
try:
    quiet(ec.sweep, SPACE)
    raised = None
except ec.EmailAuthError as e:
    raised = e
ok("a revoked app password is needs_reauth, not a generic failure",
   raised is not None and raised.status == "needs_reauth", str(raised))
h = install(fail_login="app passwords are disabled for your domain by your administrator")
try:
    quiet(ec.sweep, SPACE)
    raised = None
except ec.EmailAuthError as e:
    raised = e
ok("...and an administrator switching them off is its OWN state, which is actionable advice",
   raised is not None and raised.status == "admin_disabled", str(raised))

print("\n— nothing sends on email until somebody writes the rule —")
d = window.decide("email", "2026-09-15T10:00:00+00:00")
ok("window.decide refuses email: ingest works, sending does not (spec section 5)",
   d["decision"] == window.BLOCKED, str(d))

print("\n— an unconnected mailbox is an ordinary state, not an error —")
with state.connect() as _c:
    _c.execute("DELETE FROM box_secrets WHERE name = ?", (bs.EMAIL,))
ok("a box with no credential reads nothing and raises nothing", quiet(ec.sweep, SPACE) == (0, 0))

print("\n— built, and deliberately not switched on —")
# THE MACHINE IS READY AND THE CHANNEL IS NOT POLLED, ON PURPOSE. test_inbox_instagram requires a
# send rule beside every polled channel so that auto-reply can never arrive by accident; the email
# rule is OSDev1's half. Adding Channel(IMAP, "email") to POLLED is the whole activation, and this
# pins the pairing so the two land together rather than one shipping without the other.
ok("email is NOT polled yet, because window._RULES has no email rule",
   not any(c.key == "email" for c in channels.POLLED) and "email" not in window._RULES)
ok("...and the vendor constant is named once, for the poller and the suites to share",
   channels.IMAP == "imap")
ok("...while the reader itself is proven above, so activation is one line",
   callable(email_channel_sweep))

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
