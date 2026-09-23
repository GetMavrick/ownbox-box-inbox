"""A sender's own HTML reaches the reader, and reaches nothing else.

WHY THIS IS THE CAREFUL ONE. Every other change this week moved data around. This one puts
markup written by a stranger — anybody who can email the business — onto a screen that is also
logged in to the box. Get it wrong and the payload is not a defaced page, it is the buyer's
session, read by whoever felt like writing to them.

THE BOUNDARY IS THE SANDBOX, NOT A SANITISER, and that distinction is what this suite pins. A
sanitiser is a denylist of everything anybody has thought of so far; it is useful and it is not
a boundary. `sandbox` with neither `allow-scripts` nor `allow-same-origin` is one, enforced by
the browser: script does not run — not "is stripped", does not run, including whatever a
denylist had never heard of — and the document is an opaque origin that cannot read our cookies,
our DOM, or reach `window.parent`.

AND THE CSP IS THE HALF THAT STOPS THE QUIET ATTACK. `default-src 'none'` with `img-src data:`
means a REMOTE image cannot load, and a remote image in a customer's email is a read receipt
telling the sender the moment the buyer opened it. Blocked by the browser, with no list of
tracker domains for anybody to maintain.

WHAT THIS SUITE CANNOT DO is run a browser. So it asserts the two things that are checkable
without one and that no reviewer can eyeball reliably: that the attributes carrying the
guarantee are present and correctly spelled, and that a sender's bytes cannot escape the
`srcdoc` attribute they are quoted into. The first is what a browser then enforces; the second
is the only way out of the frame that does not need a browser bug.

Run: python tests/test_a_senders_html_cannot_touch_this_page.py
"""
from __future__ import annotations

import html as html_mod
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "frame.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state                                                   # noqa: E402

state.init_db()

from core import dash, spaces                                            # noqa: E402
from marketing.customer_voice import app as cv                           # noqa: E402,F401
from marketing.customer_voice.inbox import render, store                 # noqa: E402
from core.dispatch import app                                            # noqa: E402

state.init_db()

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


SPACE = spaces.DEFAULT
NICE = ('<html><body><h2>Lead</h2><ul><li>companies found: <b>38</b></li></ul>'
        '<a href="https://aios.nlvl.co/app/review">the full review</a></body></html>')


# ── 1. the attributes that carry the guarantee ──────────────────────────────────────────────
print("\ntest_the_frame_is_spelled_the_way_the_guarantee_needs")

frame = render.safe_frame(NICE, label="Morning Review")
ok("a message with markup is framed", frame.startswith("<iframe"), frame[:60])

m = re.search(r'sandbox="([^"]*)"', frame)
sandbox = (m.group(1) if m else "MISSING").split()
ok("the frame is sandboxed at all", m is not None, frame[:200])
# EACH OF THESE IS A SEPARATE HOLE, so each is asserted separately rather than as one string
# compare — a future edit that adds one token should fail on that token by name.
ok("NO allow-scripts — sender JavaScript does not run", "allow-scripts" not in sandbox,
   str(sandbox))
ok("NO allow-same-origin — the frame cannot read our session or our DOM",
   "allow-same-origin" not in sandbox, str(sandbox))
ok("NO allow-forms — a fake sign-in inside a message posts nowhere",
   "allow-forms" not in sandbox, str(sandbox))
ok("NO allow-top-navigation — a message cannot steer the buyer's tab away",
   not any(s.startswith("allow-top-navigation") for s in sandbox), str(sandbox))
ok("NO allow-modals — no alert loop a reader cannot escape",
   "allow-modals" not in sandbox, str(sandbox))
# THE ONE GRANT, AND IT IS DELIBERATE. Without it every link in every email is dead, which is
# not a safe inbox, it is a broken one.
ok("allow-popups IS granted, so a real link still opens", "allow-popups" in sandbox, str(sandbox))

csp = re.search(r'Content-Security-Policy&quot; content=&quot;([^&]*(?:&(?!quot;)[^&]*)*)',
                frame)
csp_text = html_mod.unescape(csp.group(1)) if csp else ""
ok("the framed document carries a CSP", bool(csp_text), frame[:300])
ok("...that fetches NOTHING by default", "default-src 'none'" in csp_text, csp_text)
# THE READ-RECEIPT LINE. `img-src data:` and nothing else means a tracking pixel cannot load.
ok("...and allows images only as data:, so a tracking pixel cannot fire",
   "img-src data:" in csp_text and "img-src *" not in csp_text, csp_text)
ok("...with no http or https source anywhere in it",
   "http://" not in csp_text and "https://" not in csp_text, csp_text)
ok("...and a form inside a message can post nowhere", "form-action 'none'" in csp_text, csp_text)
ok("the frame sends no referrer", 'referrerpolicy="no-referrer"' in frame)


# ── 2. the one escape that does not need a browser bug ──────────────────────────────────────
print("\ntest_a_sender_cannot_break_out_of_the_srcdoc_attribute")

# THE WHOLE TRUST BOUNDARY AT THIS LAYER IS ONE `quote=True`. `srcdoc` is an ATTRIBUTE: a
# sender's un-escaped `"` closes it and everything after is markup in OUR document, outside the
# sandbox, with our origin. Every payload below is a real attempt at that.
for name, payload in (
        ("a bare quote", '<p>hi" onload="alert(1)</p>'),
        ("a closed attribute and a new tag", '<p>x</p>"><script>alert(1)</script>'),
        ("an ampersand entity that could re-form one", '<p>&quot;&gt;&lt;script&gt;x</p>'),
        ("a single quote", "<p>it's</p>"),
        ("a raw close of the iframe", '<p>a</p></iframe><script>alert(1)</script>')):
    got = render.safe_frame(payload)
    body = got[got.index('srcdoc="') + 8:]
    body = body[:body.index('"')]                    # everything up to the FIRST closing quote
    # THE PROPERTY IS "THE ATTRIBUTE ENDS WHERE WE ENDED IT", not "these words are absent".
    # The first cut looked for the substring `onload=` and failed on `onload=&quot;` — escaped
    # text sitting inertly inside the attribute, which is the system working. What actually
    # matters is that the sender's own `"` never terminated the attribute: so the slice up to
    # the FIRST quote must still contain the end of the document we built, and the tag must
    # close the way we closed it.
    ok(f"{name}: the attribute is not closed early",
       body.endswith("&lt;/html&gt;") and got.endswith('"></iframe>'), body[-60:])
    # AND NO LIVE TAG SURVIVES ANYWHERE IN THE FRAME WE EMIT. Our own iframe is the only tag in
    # it; a `<script` here would mean markup escaped the attribute entirely.
    ok(f"{name}: ...and no live tag of theirs is in our markup",
       "<script" not in got and "<p>" not in got, got[:120])
    # AND IT IS ESCAPED, NOT DROPPED. A sender's quote must survive into their document — the
    # fix for an injection is never to silently eat the reader's content.
    ok(f"{name}: ...and the content survives escaped",
       "&quot;" in got or "&#x27;" in got or "&amp;" in got, got[:120])

# NOTHING TO FRAME IS NOT A FRAME. An empty or text-only part must not produce an empty iframe:
# a bordered 560px box with nothing in it reads as a broken message.
for nothing in ("", "   ", "\n", "plain words with no tags"):
    ok(f"{nothing!r} produces no frame", render.safe_frame(nothing) == "")
ok("...and `has_markup` agrees", not render.has_markup("just text"))
ok("real markup is recognised", render.has_markup("<p>x</p>"))


# ── 2b. the height, which is a guess that must never come out short ─────────────────────────
print("\ntest_the_height_guess_is_generous_on_the_shapes_that_were_measured")

# WHY THIS EXISTS. The frame does not scroll — a scrollable region inside a scrolling page is a
# scroll trap on a phone, and every buyer screen here is mobile first. So the height is estimated
# from the content, and the two failure modes are not equal: over is white space at the bottom of
# a card, under is a paragraph of somebody's email below a fold that does not move.
#
# THE NUMBERS BELOW WERE MEASURED, in Chromium at 390px with this frame's own stylesheet, on
# 2026-09-23. They are frozen here so that a later tuning pass has to beat the measurements
# rather than its own arithmetic — the first cut of those constants was arithmetic and came out
# 14% short on a four-section newsletter.
MEASURED = (
    ("one-line reply", "<p>Hi — are you open Saturday?</p>", 75),
    ("four-section newsletter",
     "<h1>This week</h1>" + "".join(
         f"<h2>Section {i}</h2><p>{'word ' * 60}</p><ul><li>one</li><li>two</li></ul>"
         for i in range(4)), 1268),
    ("twenty-row table",
     "<table>" + "".join(f"<tr><td>row {i}</td><td>value {i}</td></tr>" for i in range(20))
     + "</table>", 550),
)
for label, sample, real_px in MEASURED:
    est = render._estimate_px(sample)
    ok(f"{label}: the guess is not short of the measured {real_px}px", est >= real_px,
       f"estimated {est}px")
    # AND NOT ABSURD. A frame ten times taller than its content is white space somebody has to
    # scroll past, which is the other way to make this feature annoying.
    ok(f"{label}: ...and not more than 2x it", est <= max(real_px * 2, 200), f"{est}px")

ok("an empty document never asks for a frame", render.safe_frame("") == "")
ok("a huge message is capped rather than asking for a mile of page",
   render._estimate_px("<p>x</p>" * 20000) <= render._FRAME_MAX_PX)
ok("...and more content always means more height, never less",
   render._estimate_px("<p>a</p>" * 50) > render._estimate_px("<p>a</p>"))
# THE HEIGHT IS AN INLINE STYLE, NOT A CSS RULE, because it is per-message. A stylesheet cannot
# know how tall one stranger's email is.
ok("the frame carries its own height", "style=\"height:" in render.safe_frame("<p>x</p>"))


# ── 3. on the page ──────────────────────────────────────────────────────────────────────────
print("\ntest_the_thread_frames_it_and_keeps_the_text_underneath")

store.upsert_conversation(space=SPACE, zcid="f1", platform="email",
                          participant="Morning Review", last_inbound_at=state._now(),
                          account_id="brian@nlvl.co")
store.record_message(space=SPACE, zcid="f1", zmid="<f1@x>", direction="in",
                     sent_by="review@example.test", body="the plain fallback",
                     detail={"body_html": NICE, "body_text": "the plain fallback",
                             "headers": {"From": "Morning Review <review@example.test>"}})
c = app.test_client()
c.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
page = c.get("/inbox/inbox/f1").get_data(as_text=True)

ok("the thread renders a frame", "<iframe" in page and 'sandbox="' in page)
ok("...carrying the sender's markup, escaped into the attribute", "&lt;h2&gt;Lead&lt;/h2&gt;" in page)
# THE TAGS NEVER GO LIVE IN OUR DOCUMENT. If this fails, the frame is decoration and the page is
# rendering a stranger's HTML directly.
ok("...and never as live markup in our own page", "<h2>Lead</h2>" not in page)
# THE TEXT STAYS. A frame cannot be searched, swept with one selection, or copied the way a
# person copies an address out of a message.
ok("the plain text is still reachable under it", "the plain fallback" in page)
ok("...folded, not shouting", 'class="orig"' in page)

# A MESSAGE WITH NO MARKUP IS UNCHANGED — every Messenger message, and all mail stored before
# the HTML was kept. This is the case that breaks for everybody at once if it regresses.
store.upsert_conversation(space=SPACE, zcid="f2", platform="messenger",
                          participant="Len Okafor", last_inbound_at=state._now())
store.record_message(space=SPACE, zcid="f2", zmid="<f2@x>", direction="in",
                     sent_by="len", body="Are you open Saturday?")
dm = c.get("/inbox/inbox/f2").get_data(as_text=True)
ok("a message with no HTML gets no frame", "<iframe" not in dm)
ok("...and no empty Plain text fold", 'class="orig"' not in dm)
ok("...and still says what it says", "Are you open Saturday?" in dm)


print("\n" + ("all good" if not _failed else f"{_failed} FAILED"))
sys.exit(1 if _failed else 0)
