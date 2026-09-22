"""Ticking a draft and pressing Send — through the SCREEN, not the function underneath it.

WHY THIS EXISTS, AND IT IS NOT A HAPPY-PATH TEST. tests/test_the_box_sends_the_email.py drives
`reply.send_reply` directly and passes on every exit of the transport. The owner pressed Send on
the Drafts tab of his own box and read:

    Dr. Mercola did not go — ReplyRefused.

Two bugs, both in the twelve lines of screen BETWEEN that button and the tested function, and
neither reachable from a unit test on the function:

  1. THE REASON WAS THROWN AWAY. The handler recorded `type(e).__name__`, so the sentence that
     says what to do — "this box is not connected to a mailbox", "there is no address on this
     thread to reply to" — never reached the page. The buyer saw a class name, and so did we:
     nobody could tell which refusal it was. The thread's own compose box has always shown
     `str(e)`; this screen never did.
  2. A SUCCESSFUL SEND WAS COUNTED AS A FAILURE. The handler asked for status in
     ("sent", "queued"). `send_reply` has only ever returned "ok" — there is no "sent" and no
     "queued" anywhere in inbox/reply.py — so a reply that HAD left the box was reported as
     "did not go — ok", inviting a re-tick that only the ledger claim stopped becoming a second
     message to a customer.

THE LESSON IS THE FILE, NOT THE FIX. A send feature was called proven while the one surface a
person actually uses had never been run end to end. Everything below goes through the HTTP POST
the button makes.

Run: python tests/test_the_drafts_tab_actually_sends.py
"""
from __future__ import annotations

import imaplib
import io
import os
import smtplib
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "draftstab.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

FAILS: list[str] = []
OWNER = "owner@acme.com"
APP_PW = "abcdefghijklmnop"


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


def quiet(fn, *a, **k):
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        return fn(*a, **k)


class FakeSMTP:
    last: "FakeSMTP | None" = None

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.messages: list = []
        self.raise_on_send = None
        FakeSMTP.last = self

    def ehlo(self, *a):
        return (250, b"ok")

    def starttls(self, *a, **k):
        return (220, b"go")

    def login(self, u, p):
        return (235, b"ok")

    def send_message(self, msg):
        if self.raise_on_send:
            raise self.raise_on_send
        self.messages.append(msg)
        return {}

    def quit(self):
        return (221, b"bye")


class FakeIMAP:
    """Answers a Subject fetch and nothing else. Read-only, like every reader in this channel."""

    def __init__(self, host, **kw):
        pass

    def login(self, u, p):
        return ("OK", [b""])

    def select(self, folder, readonly=False):
        assert readonly is True, "a subject fetch must never mark the buyer's mail as read"
        return ("OK", [b"1"])

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            return ("OK", [b"1"])
        if cmd == "FETCH":
            return ("OK", [(b"1 (UID x)",
                            b"Subject: Bulk order\r\nMessage-ID: <q1@buyer.com>\r\n\r\n")])
        return ("NO", [b""])

    def logout(self):
        return ("BYE", [b""])


def install(*, on_send=None) -> None:
    def factory(host, port, timeout=None):
        f = FakeSMTP(host, port, timeout)
        f.raise_on_send = on_send
        return f
    smtplib.SMTP = factory                                        # type: ignore[assignment]
    imaplib.IMAP4_SSL = lambda host, **kw: FakeIMAP(host, **kw)   # type: ignore[assignment]


install()


def _client():
    import core.config as cfg
    from core.dispatch import app
    base = cfg.get_config()
    d = dict(base.get("dash") or {})
    d["app_token"] = ""
    merged = dict(base)
    merged["dash"] = d
    cfg.get_config = lambda m=merged: m
    return app.test_client()


def _seed(*, connect_mailbox: bool, with_address: bool = True):
    """A box with one email draft waiting, exactly as the drafter leaves it."""
    from core import box_secrets as bs
    from core import spaces, state
    from core.config import settings
    from marketing.customer_voice.drafter import store as drafts
    from marketing.customer_voice.inbox import store

    c = _client()
    sp = spaces.DEFAULT
    with state.connect() as conn:
        for t in ("inbox_drafts", "inbox_messages", "inbox_conversations",
                  "inbox_draft_lessons", "inbox_send_ledger"):
            try:
                conn.execute(f"DELETE FROM {t}")
            except Exception:                                     # noqa: BLE001
                pass
    quiet(bs.clear_email)
    if connect_mailbox:
        quiet(bs.put_email, host="imap.gmail.com", user=OWNER, password=APP_PW)

    zc = "<q1@buyer.com>"
    store.upsert_conversation(space=sp, zcid=zc, platform="email", participant="Dr. Mercola",
                              account_id=OWNER,
                              last_inbound_at=(datetime.now(timezone.utc)
                                               - timedelta(hours=2)).isoformat())
    store.record_message(space=sp, zcid=zc, zmid=zc, direction="in",
                         sent_by="mercola@buyer.com" if with_address else "not-an-address",
                         body="Do you do bulk orders?")
    drafts.put(space=sp, zcid=zc, in_reply_to=zc,
               body="We do — how many were you thinking?")
    c.post("/dash/login", data={"token": settings.dash_token})
    waiting = drafts.waiting(sp, limit=10)
    assert len(waiting) == 1, f"seed did not land: {len(waiting)} drafts waiting"
    return c, sp, waiting[0]


def _press_send(c, zcid: str) -> str:
    """The exact POST the Send button makes."""
    return c.post("/inbox/waiting", data={"pick": zcid}).get_data(as_text=True)


# ── THE FAILURE THE OWNER SAW: THE SCREEN MUST SAY WHY ──────────────────────────────────────
print("\n— a refusal says what to do about it, never a class name —")
c, sp, d = _seed(connect_mailbox=False)
page = quiet(_press_send, c, d["zcid"])
ok("a draft that cannot send still names the person", "Dr. Mercola" in page)
ok("...and NEVER shows the exception class name", "ReplyRefused" not in page,
   page[page.find("did not go") - 40:page.find("did not go") + 90])
ok("...it shows the sentence that says what to fix",
   "mailbox" in page.lower(), page[page.find("did not go"):page.find("did not go") + 140])

# THE OTHER REFUSAL A REAL BOX HITS, for the same reason: a thread whose inbound sender is
# unreadable. Same screen, same requirement — words, not a class.
c, sp, d = _seed(connect_mailbox=True, with_address=False)
page = quiet(_press_send, c, d["zcid"])
ok("an unaddressable thread says so in words too",
   "ReplyRefused" not in page and "address" in page.lower(),
   page[page.find("did not go"):page.find("did not go") + 140])

# ── AND A SEND THAT WORKS SAYS SO ───────────────────────────────────────────────────────────
print("\n— a reply that goes is reported as gone —")
install()
c, sp, d = _seed(connect_mailbox=True)
page = quiet(_press_send, c, d["zcid"])
ok("the screen says it sent one", "Sent 1" in page,
   page[page.find('<div class="card"><p>'):][:160])
ok("...and does NOT say it did not go", "did not go" not in page,
   page[page.find("did not go"):page.find("did not go") + 120])

f = FakeSMTP.last
ok("...because it really did go, out of the buyer's own mailbox", len(f.messages) == 1,
   str(len(f.messages)))
m = f.messages[-1]
ok("...to the customer who asked", m["To"] == "mercola@buyer.com", str(m["To"]))
ok("...from the buyer's own address", m["From"] == OWNER, str(m["From"]))
ok("...threaded onto their message", m["In-Reply-To"] == "<q1@buyer.com>",
   str(m["In-Reply-To"]))
ok("...carrying what the box drafted",
   "how many were you thinking" in m.get_content(), m.get_content()[:70])

# ── AND THE BOX LEARNS FROM IT, WHICH IS WHY THE SEND MATTERS ───────────────────────────────
#
# OSDev1, 2026-09-22: `inbox_draft_lessons` is still 0 on the owner box "because nothing has ever
# been sent, so the drafter has never learned anything". A lesson is written at the one moment
# both the draft and what a person actually sent exist side by side — this moment, and only here.
print("\n— and the drafter learns what he actually sends —")
from core import state  # noqa: E402

with state.connect() as conn:
    lessons = conn.execute("SELECT draft_body, sent_body, edited FROM inbox_draft_lessons"
                           ).fetchall()
ok("sending through the screen writes the lesson", len(lessons) == 1, str(len(lessons)))
ok("...pairing what the box wrote with what went out",
   lessons and "how many were you thinking" in (lessons[0]["sent_body"] or ""),
   str(dict(lessons[0])) if lessons else "no row")
ok("...and marks it unedited, because he sent it as drafted",
   lessons and lessons[0]["edited"] == 0, str(lessons[0]["edited"]) if lessons else "")

# ── THE ROW LEAVES THE QUEUE, SO IT CANNOT BE SENT TWICE BY HAND ────────────────────────────
print("\n— and it is gone from the queue —")
from marketing.customer_voice.drafter import store as _drafts  # noqa: E402

ok("the draft no longer waits once it has been sent",
   not _drafts.waiting(sp, limit=10), str(len(_drafts.waiting(sp, limit=10))))
page2 = quiet(c.post, "/inbox/waiting", data={"pick": d["zcid"]})
body2 = page2.get_data(as_text=True)
ok("...and pressing Send again on a stale page sends nothing, silently",
   len(FakeSMTP.last.messages) == 0 or "Sent 1" not in body2, str(len(FakeSMTP.last.messages)))

# ── AN INDETERMINATE NEVER READS AS SENT ────────────────────────────────────────────────────
print("\n— a send that may have landed is never called sent —")
install(on_send=TimeoutError("timed out"))
c, sp, d = _seed(connect_mailbox=True)
page = quiet(_press_send, c, d["zcid"])
ok("a timeout is not reported as Sent", "Sent 1" not in page, page[:160])
ok("...and the words do not tell him it failed either",
   "ReplyIndeterminate" not in page, page[page.find("did not go"):][:140])

print()
if FAILS:
    print(f"FAILED {len(FAILS)}: " + "; ".join(FAILS))
    sys.exit(1)
print("all ok")
