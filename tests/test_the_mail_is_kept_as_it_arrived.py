"""What arrives is kept: both body parts, untruncated, and the headers that say who sent it.

WHAT WAS BEING THROWN AWAY, found by rendering the owner's own morning review side by side with
Gmail's copy of it (2026-09-22/23). `email_channel._body_text()` preferred `text/plain` and
discarded the `text/html` part, so on the overwhelmingly common multipart/alternative message a
buyer read the fallback the sender's mailer generated — tables exploded one value per line,
`<https://…>` link syntax, stacks of blank lines. Gmail shows the HTML part. We read it, found
it, and dropped it. Owner, 2026-09-23: *"that if you are smart is the most urgent thing to do."*

AND A SECOND ONE IN THE SAME PATH. `store.record_message` caps `body` at 2000 characters. That
is correct for a list-view preview and it had been silently cutting long mail since the machine
shipped, because the preview was the only copy.

WHY THIS SUITE IS URGENT RATHER THAN IMPORTANT. Neither loss is recoverable from the database: a
part not kept at ingest can only be got back by re-reading somebody's mailbox. Every day this
went unfixed was another day of mail stored without it.

WHAT IS DELIBERATELY NOT HERE. Nothing renders `body_html`. A sender's markup needs a sanitiser,
a sandboxed frame, a restrictive CSP and remote images blocked until asked for — a remote image
in a customer's email is a read receipt that tells the sender when the buyer opened it. That is
its own change. This one exists so that when it lands, the mail is already here.

Run: python tests/test_the_mail_is_kept_as_it_arrived.py
"""
from __future__ import annotations

import email.message
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "mail.db")
os.environ.setdefault("DASH_TOKEN", "pw")

from core import state                                                   # noqa: E402

state.init_db()

from marketing.customer_voice import app as cv                           # noqa: E402,F401
from marketing.customer_voice.inbox import email_channel as ec           # noqa: E402
from marketing.customer_voice.inbox import store                         # noqa: E402

state.init_db()                                                          # the machine's tables

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


TEXT_PART = "Morning review\n\nLEAD\n  companies found: 38\n"
HTML_PART = ('<html><body><h2>Lead</h2><ul><li>companies found: <b>38</b></li></ul>'
             '<a href="https://aios.nlvl.co/app/review/2026-09-19">the full review</a>'
             '</body></html>')


def alternative() -> email.message.Message:
    """The shape almost every real email has, and the one the old code got wrong."""
    m = email.message.EmailMessage()
    m["From"] = "Morning Review <review@example.test>"
    m["To"] = "buyer@example.test"
    m["Subject"] = "Morning review, Sun 20 Sep: 4 things need you"
    m["Date"] = "Sun, 20 Sep 2026 08:01:00 +0000"
    m["Message-ID"] = "<m1@example.test>"
    m["Return-Path"] = "<bounce@mail.example.test>"
    m["DKIM-Signature"] = "v=1; a=rsa-sha256; d=example.test; s=sel;"
    m["Authentication-Results"] = "mx.google.com; spf=pass; dkim=pass; dmarc=pass"
    m.set_content(TEXT_PART)
    m.add_alternative(HTML_PART, subtype="html")
    return m


# ── 1. both parts survive, neither invented ─────────────────────────────────────────────────
print("\ntest_both_parts_are_kept_and_neither_is_synthesised")

text, html_part = ec._body_parts(alternative())
ok("the text part is the sender's own text", text.startswith("Morning review"), text[:60])
ok("THE HTML PART IS KEPT AT ALL — this is the whole bug", "<h2>Lead</h2>" in html_part,
   html_part[:80])
ok("...verbatim, not stripped or rewritten", 'href="https://aios.nlvl.co' in html_part)

# A TAG-STRIPPED STRING IS NOT A TEXT PART. Storing a guess beside the real thing is how the
# real thing stops being consulted, so an HTML-only message yields ("", html) — never a
# flattened imitation of a text part the sender never wrote.
only_html = email.message.EmailMessage()
only_html["Message-ID"] = "<m2@example.test>"
only_html.set_content(HTML_PART, subtype="html")
t2, h2 = ec._body_parts(only_html)
ok("an HTML-only message gives up no fake text part", t2 == "", repr(t2[:60]))
ok("...and its HTML is kept", "<h2>Lead</h2>" in h2)

plain = email.message.EmailMessage()
plain["Message-ID"] = "<m3@example.test>"
plain.set_content("just text")
t3, h3 = ec._body_parts(plain)
ok("a text-only message gives up no fake HTML", h3 == "" and t3 == "just text", repr((t3, h3)))

# AN ATTACHMENT IS NOT THE BODY — the rule the old code had and this must not lose.
withatt = alternative()
withatt.add_attachment(b"%PDF-1.4 fake", maintype="application", subtype="pdf",
                       filename="invoice.pdf")
t4, h4 = ec._body_parts(withatt)
ok("an attachment is never mistaken for the body",
   t4.startswith("Morning review") and "%PDF" not in h4 and "%PDF" not in t4)


# ── 2. the headers that answer "who really sent this" ───────────────────────────────────────
print("\ntest_the_headers_the_details_panel_needs_are_kept")

h = ec._kept_headers(alternative())
for name in ("From", "To", "Subject", "Date", "Return-Path", "DKIM-Signature",
             "Authentication-Results"):
    ok(f"{name} is kept", name in h, str(sorted(h)))
# THE ADDRESS, NOT JUST THE DISPLAY NAME. The screen shows "Morning Review" and nothing else
# today, which is precisely what a spoof relies on.
ok("From carries the ADDRESS, which the screen does not show today",
   "review@example.test" in h.get("From", ""), h.get("From", ""))
ok("...and the display name with it", "Morning Review" in h.get("From", ""))

# NO TLS CLAIM. Gmail's "standard encryption (TLS)" describes the hop into Google's own servers,
# written by Google. We did not make that hop. The authentication results answer the question a
# buyer actually has, and that one we can stand behind.
ok("no Received chain is hoarded", "Received" not in h, str(sorted(h)))

# REPEATED HEADERS ARE JOINED, NOT DROPPED — one Authentication-Results per hop, one
# DKIM-Signature per signer, and keeping the first would report one signer while another passed
# unmentioned.
two = alternative()
two["DKIM-Signature"] = "v=1; a=rsa-sha256; d=forwarder.test; s=b;"
ok("a second signature is kept beside the first",
   ec._kept_headers(two)["DKIM-Signature"].count("d=") == 2,
   ec._kept_headers(two)["DKIM-Signature"])

# AND NOTHING OUTSIDE THE ALLOWLIST RIDES ALONG. A message carries dozens of headers and some
# are nobody's business to keep; the set is declared, not "whatever arrived".
nosy = alternative()
nosy["X-Originating-IP"] = "203.0.113.9"
ok("a header nobody asked for is not stored", "X-Originating-IP" not in ec._kept_headers(nosy))


# ── 3. it reaches the database, untruncated, and it backfills ───────────────────────────────
print("\ntest_the_store_keeps_it_and_a_re_poll_backfills_it")

SPACE, ZCID = "default", "thread-1"
long_text = "x" * 5000
store.record_message(space=SPACE, zcid=ZCID, zmid="<m1@example.test>", direction="in",
                     sent_by="review@example.test", body=long_text,
                     detail={"body_html": HTML_PART, "body_text": long_text,
                             "headers": ec._kept_headers(alternative())})

rows = store.messages_for(SPACE, ZCID)
ok("the message is mirrored", len(rows) == 1, str(len(rows)))
# THE PREVIEW IS STILL A PREVIEW. The 2000-char cap is right for the list view and is left alone.
ok("...and its preview is still capped, because that column draws the list",
   len(str(rows[0]["body"])) == 2000, str(len(str(rows[0]["body"]))))

d = store.message_detail(rows[0]["id"])
ok("the detail row exists", bool(d), str(d)[:80])
ok("THE FULL TEXT IS KEPT — the 2000-char cut was the second loss in this path",
   len(d["body_text"]) == 5000, str(len(d["body_text"])))
ok("the HTML is in the database", "<h2>Lead</h2>" in d["body_html"])
ok("the headers came back as a dict", isinstance(d["headers"], dict) and "From" in d["headers"],
   str(d["headers"])[:80])

# THE BACKFILL. A message stored before any of this existed has a row and no detail; re-reading
# the mailbox must write the detail for it rather than skipping it because the message is known.
store.record_message(space=SPACE, zcid=ZCID, zmid="<old@example.test>", direction="in",
                     sent_by="someone@example.test", body="stored before the fix")
old_row = [r for r in store.messages_for(SPACE, ZCID)
           if r["zernio_message_id"] == "<old@example.test>"][0]
ok("a message stored the old way has no detail", store.message_detail(old_row["id"]) == {})
store.record_message(space=SPACE, zcid=ZCID, zmid="<old@example.test>", direction="in",
                     sent_by="someone@example.test", body="stored before the fix",
                     detail={"body_html": "<p>recovered</p>", "body_text": "recovered",
                             "headers": {"From": "someone@example.test"}})
back = store.message_detail(old_row["id"])
ok("...and a re-poll recovers it instead of skipping it",
   "recovered" in back.get("body_html", ""), str(back)[:80])
ok("...without duplicating the message row",
   len([r for r in store.messages_for(SPACE, ZCID)
        if r["zernio_message_id"] == "<old@example.test>"]) == 1)

# A CHANNEL WITH NOTHING TO GIVE IS NORMAL, NOT BROKEN. Messenger and Instagram have no parts
# and no headers, and every reader must cope with an empty answer.
store.record_message(space=SPACE, zcid=ZCID, zmid="<dm1@example.test>", direction="in",
                     sent_by="a-dm", body="hi")
dm = [r for r in store.messages_for(SPACE, ZCID) if r["zernio_message_id"] == "<dm1@example.test>"][0]
ok("a message with no detail reads as {} rather than raising",
   store.message_detail(dm["id"]) == {})
ok("...and an id nobody has heard of does too", store.message_detail("not-a-message") == {})


# ── 4. nothing renders it ───────────────────────────────────────────────────────────────────
print("\ntest_nothing_in_this_change_renders_a_senders_markup")

# THE LINE THIS SECTION GUARDS, AND HOW IT MOVED. Until the frame shipped this asserted that
# NOTHING read `body_html` at all — the right guard while the mail was being kept and nothing
# could yet show it safely. `render.safe_frame` is the thing it was holding the line for, so the
# assertion becomes the stronger one: a sender's markup reaches a page through the sandbox or it
# does not reach one at all. `tests/test_a_senders_html_cannot_touch_this_page.py` proves the
# sandbox's own properties; this is the cheap structural check that nothing routes around it.
SRC = pathlib.Path(__file__).resolve().parents[1] / "marketing/customer_voice"
for rel in ("inbox/store.py", "inbox/email_channel.py"):
    src = (SRC / rel).read_text()
    ok(f"{rel} never puts body_html on a page", "srcdoc" not in src and "|safe" not in src, rel)
app_src = (SRC / "app.py").read_text()
# THE APP MAY NOT BUILD THE FRAME ITSELF. One module owns the sandbox and its CSP; a second
# place assembling an iframe around a stranger's HTML is how one of them ends up without the
# `sandbox` attribute, six months from now, in a hurry.
# THE ATTRIBUTE FORM, NOT THE WORD. The first cut of this matched the bare word and caught a
# CSS comment in app.py explaining why the frame paints its own background — a guard failing on
# prose about itself, which teaches the next person to delete it rather than read it.
ok("the app does not assemble a frame of its own", 'srcdoc="' not in app_src,
   "app.py is building an iframe instead of calling render.safe_frame")
ok("...it goes through the one module that owns the sandbox", "safe_frame" in app_src)


print("\n" + ("all good" if not _failed else f"{_failed} FAILED"))
sys.exit(1 if _failed else 0)
