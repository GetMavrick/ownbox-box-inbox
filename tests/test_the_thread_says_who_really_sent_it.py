"""The thread shows the address, the details behind a caret, and the whole message.

Owner, 2026-09-23, on our inbox next to Gmail's copy of the same email: *"Right now our inbox
doesn't even show what the email address is of the sender... things need to be much more compact
as far as line spacing on these emails... study Gmail and how they have a little drop-down."*

THREE THINGS, AND THE FIRST IS THE ONE THAT MATTERS. A display name is the one part of an email
anybody can set to anything, and it was all this screen showed. "Morning Review" identifies
nobody. The address under it, and the rest behind Gmail's caret, is the difference between a
screen you can check a sender on and one that can be dressed up by whoever wrote in.

THE MESSAGE IS NO LONGER A PREVIEW. `inbox_messages.body` is capped at 2000 characters and was
the only copy kept, so long mail was cut off mid-sentence on this screen with nothing saying so.
`messages_for` now joins `inbox_message_detail` and the thread renders `full_text` when it is
there — which is also why this suite asserts the FALLBACK as hard as the happy path: every
message stored before 2026-09-23, and every Messenger and Instagram message ever, has no detail
row and must render exactly as it did before.

WHAT IS DELIBERATELY NOT IMITATED. Gmail's "security: standard encryption (TLS)" describes the
hop into Google's own servers, which Google made and Google wrote the header for. This box did
not make that hop. It prints the SPF/DKIM/DMARC verdicts the mail actually carries instead.

Run: python tests/test_the_thread_says_who_really_sent_it.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "thread.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state                                                   # noqa: E402

state.init_db()

from core import dash, spaces                                            # noqa: E402
from marketing.customer_voice import app as cv                           # noqa: E402
from marketing.customer_voice.inbox import store                         # noqa: E402
from core.dispatch import app                                            # noqa: E402

state.init_db()

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


SPACE = spaces.DEFAULT
HEADERS = {"From": "Morning Review <review@brian-macdonald.com>",
           "To": "brian@nlvl.co",
           "Date": "Sun, 20 Sep 2026 08:01:00 +0000",
           "Subject": "Morning review, Sun 20 Sep: 4 things need you",
           "Return-Path": "<bounce@mail.nlvl.co>",
           "DKIM-Signature": "v=1; a=rsa-sha256; d=brian-macdonald.com; s=s1;",
           "Authentication-Results": "mx.google.com; spf=pass; dkim=pass; dmarc=fail"}
LONG = "Morning review\n" + "\n".join(f"  counter {i}: 0" for i in range(400))


def owner():
    c = app.test_client()
    c.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
    return c


def thread(zcid: str) -> str:
    return owner().get(f"/inbox/inbox/{zcid}").get_data(as_text=True)


def seed(zcid: str, *, participant: str, body: str, detail=None):
    store.upsert_conversation(space=SPACE, zcid=zcid, platform="email",
                              participant=participant, last_inbound_at=state._now(),
                              account_id="brian@nlvl.co")
    store.record_message(space=SPACE, zcid=zcid, zmid=f"<{zcid}@x>", direction="in",
                         sent_by="review@brian-macdonald.com", body=body, detail=detail)


# ── 1. the pieces, before any page is drawn ─────────────────────────────────────────────────
print("\ntest_the_details_are_read_off_the_headers_not_guessed")

ok("the address comes out of From", cv._addr_of(HEADERS) == "review@brian-macdonald.com",
   cv._addr_of(HEADERS))
ok("mailed-by is the envelope sender's DOMAIN, not the whole path",
   cv._domain_of(HEADERS["Return-Path"]) == "mail.nlvl.co",
   cv._domain_of(HEADERS["Return-Path"]))
ok("signed-by is the DKIM d= tag", cv._signed_by(HEADERS) == "brian-macdonald.com",
   cv._signed_by(HEADERS))

checks = cv._checks(HEADERS)
ok("the checks are read verbatim, pass AND fail", "SPF pass" in checks and "DMARC fail" in checks,
   checks)
# A FAIL MUST SURVIVE. A panel that only ever prints "pass" is decoration, and this is the one
# row on it whose whole job is to be believed when it says no.
ok("...so a failing DMARC is reported rather than smoothed over", "fail" in checks, checks)
# NO TLS CLAIM ANYWHERE. See the docstring: that is Google's hop, not ours.
ok("no TLS or encryption claim is invented", "TLS" not in checks and "ncryption" not in checks,
   checks)

# NOTHING KEPT MEANS NOTHING DRAWN. A details panel with empty rows invites a tap that answers
# nothing, which is worse than no caret at all.
ok("a message with no headers draws no panel", cv._details({}) == "")
ok("...and no address line either", cv._addr_of({}) == "")
ok("a header the mail did not carry prints no empty row",
   "mailed-by" not in cv._details({"From": "a@b.test"}), cv._details({"From": "a@b.test"}))


# ── 2. on the page ──────────────────────────────────────────────────────────────────────────
print("\ntest_the_screen_shows_the_address_and_the_caret")

seed("t-mail", participant="Morning Review", body=LONG,
     detail={"body_html": "<p>framed me</p>", "body_text": LONG, "headers": HEADERS})
html = thread("t-mail")

ok("the display name is still the heading", "Morning Review" in html)
ok("THE ADDRESS IS ON THE SCREEN — the thing that was missing",
   "review@brian-macdonald.com" in html)
ok("the caret is there", "<details" in html and "Details" in html)
for label in ("from", "to", "date", "subject", "mailed-by", "signed-by", "security"):
    ok(f"...with a {label} row", f'>{label}<' in html, label)
ok("...and the checks in it", "SPF pass" in html, "")

# THE WHOLE MESSAGE, NOT THE PREVIEW. `body` is capped at 2000; the seeded text is far longer,
# and its last line is the one that proves nothing was cut.
ok("the message is rendered in full, past the 2000-character preview cap",
   "counter 399: 0" in html, "the body is still being truncated on screen")

# THE SENDER'S MARKUP IS ON THE PAGE NOW, AND ONLY INSIDE THE SANDBOX. This asserted that it
# never appeared at all, which was right until `render.safe_frame` shipped. What must hold now
# is that it reaches the reader through the frame and never as markup in OUR document — so the
# fixture's HTML appears escaped inside a `srcdoc` attribute, and its tags never appear live.
ok("the sender's HTML is inside a frame's srcdoc", "srcdoc=" in html and "&lt;p&gt;" in html)
ok("...and never as live markup in our own document", "<p>framed me</p>" not in html)

# AN EMAIL-LENGTH MESSAGE TAKES THE COLUMN. At 82% and 390px a forty-line email wrapped into a
# ragged 44-character strip; the threshold is on the text, so a one-line email still reads as a
# chat bubble, which is the shape the owner asked to keep.
ok("a long message is marked to take the full width", 'class="msg in long"' in html)


# ── 3. and nothing else breaks ──────────────────────────────────────────────────────────────
print("\ntest_a_message_with_no_detail_renders_exactly_as_before")

# EVERY MESSAGE STORED BEFORE 2026-09-23 IS THIS CASE, and so is every Messenger and Instagram
# message ever. If this regresses, the screen breaks for all of them at once and for nobody in
# testing, because test fixtures are written with the new shape.
seed("t-dm", participant="Len Okafor", body="Are you open Saturday?")
dm = thread("t-dm")
ok("the thread renders", "Len Okafor" in dm)
ok("...the message is there", "Are you open Saturday?" in dm)
ok("...no address line is invented", "@" not in dm.split("Len Okafor")[1][:200],
   dm.split("Len Okafor")[1][:120])
# `class="det"` SPECIFICALLY. A bare `<details` also matches the row-actions menu, which is a
# different control that belongs on this page — the first cut of this assertion caught that one
# and reported a bug that was not there.
ok("...and no empty caret is drawn", 'class="det"' not in dm)
ok("...nor is a short message widened", 'class="msg in long"' not in dm)


# ── 4. the untrusted part ───────────────────────────────────────────────────────────────────
print("\ntest_a_header_is_a_strangers_string")

# HEADERS ARE WRITTEN BY WHOEVER SENT THE MAIL. Every value on this panel is attacker-controlled
# and reaches the page, so it is escaped the way every other body on this screen is.
nasty = dict(HEADERS, From='"<script>alert(1)</script>" <x@y.test>',
             Subject="</span><img src=x onerror=alert(1)>")
seed("t-evil", participant="Someone", body="hi",
     detail={"body_html": "", "body_text": "hi", "headers": nasty})
evil = thread("t-evil")
ok("a script tag in a header does not reach the page as markup",
   "<script>alert(1)</script>" not in evil)
# THE DANGEROUS FORM IS THE TAG, NOT THE WORD. `onerror=alert(1)` sitting in the page as TEXT
# is inert — it is an attribute only if a `<` opened a tag, and every one of those is escaped.
# The first cut of this asserted the bare word and failed on its own escaped output, which is a
# test reporting a hole that does not exist.
ok("...nor does an image tag it could hang off", "<img" not in evil)
ok("...and it is escaped rather than silently dropped", "&lt;script&gt;" in evil)


print("\n" + ("all good" if not _failed else f"{_failed} FAILED"))
sys.exit(1 if _failed else 0)
