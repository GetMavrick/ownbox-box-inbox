"""A notice the box sends is never read back as a customer writing in.

REQUIRED BY OSDev1 BEFORE THE BOX MAY NOTIFY A BUYER AT THEIR OWN ADDRESS (M1, 2026-09-22).
The plan's §2.3 is that the box emails the buyer, from the buyer's own address, when an Instagram
DM lands — because their mail app already alerts them and no install or permission is needed. The
poller reads that same mailbox. Without a mark, the box can read its own notice as a customer.

WHAT THE EXISTING CODE ALREADY DOES, measured rather than assumed, because the fix has to be
aimed at what is actually missing: `sweep` computes `inbound = addr.lower() != own`, so a message
whose From is EXACTLY the connected mailbox is filed as outbound and is therefore never counted
as waiting and never drafted for. That is real protection and this suite asserts it stays.

WHAT IT DOES NOT DO, which is why the header exists:
  · it still calls `upsert_conversation`, so every notice leaves a row in the list of people who
    wrote to the business — the buyer's own address, twice a day, forever
  · it is an EQUALITY on one address, and four ordinary shapes break it: a Gmail alias or
    `+suffix`, a notice to a MEMBER whose address is not the mailbox's, a shared or forwarded
    mailbox, and a From rewritten by a relay. In each of those the notice reads as a customer.

So `core.box_mail` marks everything it originates and the sweep skips anything wearing the mark —
whole, before any row is written. This suite drives the real round trip: build the exact bytes
box_mail puts on the wire, hand them to the real `sweep` over a fake IMAP server, and assert the
box learned nothing.

Run: python tests/test_the_box_never_answers_itself.py
"""
from __future__ import annotations

import email.message
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "loop.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"

from core import state                                                   # noqa: E402

state.init_db()

from core import box_mail                                                # noqa: E402
from marketing.customer_voice.inbox import email_channel, store          # noqa: E402

SPACE = "default"
MAILBOX = "owner@acme.co"
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def as_bytes(*, frm: str, to: str, subject: str, body: str, mark: bool) -> bytes:
    """The bytes a mail server would hold. `mark` builds it the way `box_mail.send` asks Resend to."""
    m = email.message.EmailMessage()
    m["From"], m["To"], m["Subject"] = frm, to, subject
    m["Message-ID"] = f"<{abs(hash((frm, subject, body)))}@acme.co>"
    if mark:
        m[box_mail.ORIGIN_HEADER] = "notice"
    m.set_content(body)
    return m.as_bytes()


class FakeIMAP:
    """Enough of imaplib for `sweep`: login, select, uid SEARCH/FETCH, logout."""

    def __init__(self, messages):
        self.messages = messages                      # [(uid, raw_bytes)]
        self.peeked = []

    def login(self, user, password):
        return ("OK", [b""])

    def select(self, folder, readonly=True):
        return ("OK", [b"1"])

    def response(self, name):
        # `sweep` reads UIDVALIDITY off the SELECT response, not with a STATUS call. Copied from
        # what the code actually does rather than from what an IMAP client usually does.
        return (name, [b"42"])

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            return ("OK", [b" ".join(str(u).encode() for u, _ in self.messages)])
        if cmd == "FETCH":
            want = int(args[0])
            self.peeked.append(args[1])
            for u, raw in self.messages:
                if u == want:
                    return ("OK", [(b"1 (BODY[] {%d}" % len(raw), raw)])
            return ("OK", [None])
        return ("NO", [b""])

    def logout(self):
        return ("BYE", [b""])

    def close(self):
        return ("OK", [b""])


def sweep_with(messages):
    """Run the REAL sweep against those bytes. Returns (stored, conversations, messages)."""
    real_connect, real_cred = email_channel._connect, email_channel.box_secrets.email_credential
    fake = FakeIMAP(messages)
    email_channel._connect = lambda cred: fake
    email_channel.box_secrets.email_credential = lambda: {"host": "imap.gmail.com", "user": MAILBOX,
                                                     "password": "x" * 16}
    try:
        email_channel.sweep(SPACE)
    finally:
        email_channel._connect, email_channel.box_secrets.email_credential = real_connect, real_cred
    with state.connect() as c:
        convs = c.execute("SELECT * FROM inbox_conversations WHERE space=?", (SPACE,)).fetchall()
        msgs = c.execute("SELECT * FROM inbox_messages WHERE space=?", (SPACE,)).fetchall()
    return [dict(r) for r in convs], [dict(r) for r in msgs]


# ── 1. the round trip: a real notice, through the real sweep ─────────────────────────────
print("\ntest_a_notice_the_box_sent_teaches_the_box_nothing")

notice = as_bytes(frm=MAILBOX, to=MAILBOX, subject="1 Instagram message waiting",
                  body="1 person is waiting for a reply.\nOpen your inbox.", mark=True)
convs, msgs = sweep_with([(1, notice)])
ok("no conversation was created by the box's own notice", not convs,
   str([c.get("zernio_conversation_id") for c in convs]))
ok("...and no message row either", not msgs, str(len(msgs)))
ok("...so nothing is waiting for a reply", store.awaiting_reply(SPACE) == 0,
   str(store.awaiting_reply(SPACE)))

# THE MARK IS THE REASON, not the address. Same bytes, same From, mark removed: the box files it.
unmarked = as_bytes(frm=MAILBOX, to=MAILBOX, subject="1 Instagram message waiting",
                    body="1 person is waiting for a reply.\nOpen your inbox.", mark=False)
convs2, _ = sweep_with([(2, unmarked)])
ok("...and it is the MARK that did it, not the address — unmarked, the same mail is filed",
   len(convs2) == 1, f"{len(convs2)} conversations")


# ── 2. the alias case, which the address check cannot catch ──────────────────────────────
print("\ntest_the_mark_survives_an_alias_the_address_check_would_miss")

# `inbound = addr.lower() != own` is an EQUALITY. `owner+notice@` is not `owner@`, so without the
# mark this notice reads as a CUSTOMER — inbound, counted waiting, and drafted for.
alias = as_bytes(frm="owner+notice@acme.co", to=MAILBOX, subject="2 waiting",
                 body="2 people are waiting.", mark=True)
before = store.awaiting_reply(SPACE)
convs3, _ = sweep_with([(3, alias)])
ok("a notice from an alias of the mailbox is still skipped",
   len(convs3) == 1, f"{len(convs3)} conversations (1 is the unmarked one from above)")
ok("...and nothing new is waiting because of it", store.awaiting_reply(SPACE) == before,
   f"{before} -> {store.awaiting_reply(SPACE)}")


# ── 3. a real customer still gets through, which is the whole product ────────────────────
print("\ntest_a_real_customer_is_untouched_by_any_of_this")

real = as_bytes(frm="dana@example.com", to=MAILBOX, subject="Boiler leaking",
                body="Can someone come Tuesday?", mark=False)
convs4, msgs4 = sweep_with([(4, real)])
ok("a customer's email is ingested", any(c.get("platform") == "email" for c in convs4))
ok("...and it is INBOUND, so it counts and can be drafted for",
   any(m.get("direction") == "in" for m in msgs4),
   str([m.get("direction") for m in msgs4]))
ok("...and it is waiting for a reply", store.awaiting_reply(SPACE) >= 1,
   str(store.awaiting_reply(SPACE)))


# ── 4. the guards that were already there stay there ─────────────────────────────────────
print("\ntest_the_existing_protections_are_not_traded_away")

_src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "marketing/customer_voice/inbox/email_channel.py"),
            encoding="utf-8").read()
# THE EXACT SELF-MATCH CHECK IS REAL PROTECTION and the header does not replace it: two cheap
# guards on the same hazard is the shape this repo already uses for BODY.PEEK plus readonly.
ok("the sender check is still there", 'inbound = addr.lower() != own' in _src)
ok("the mailbox is still opened read-only", "readonly=True" in _src)
ok("...and still peeked, so reading never marks a buyer's mail as read", "BODY.PEEK" in _src)
ok("the skip happens BEFORE any row is written",
   _src.index("ORIGIN_HEADER") < _src.index("store.upsert_conversation"))


# ── 5. and this file cannot silently fall out of CI ──────────────────────────────────────
print("\ntest_ci_actually_runs_this_file")
import pathlib                                                           # noqa: E402

_wf = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml"
if _wf.exists():
    ok("registered in the suite list", "test_the_box_never_answers_itself" in _wf.read_text())

print()
if _failed:
    print(f"{_failed} FAILED")
    sys.exit(1)
print("ALL OK")
