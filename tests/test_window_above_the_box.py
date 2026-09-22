"""The send window, in the buyer's words, above the box he is about to type in.

OSDev4 shipped `window.explain()` in #1290 and asked this screen to bind it. His instruction was
specific and this suite holds the screen to it: bind `state`, NOT `reason`. `reason` is dev-facing
on purpose — "nobody has written the send rules for this platform" is an accurate sentence about
OUR work and a baffling one to a plumber looking at his own inbox.

THE NEGATIVE MATTERS MORE THAN THE SENTENCE. His PR guards hardest on "nothing here may ever stop
a send", and a screen is the easiest place to break that by accident: hide the box and the send is
stopped just as dead as if the code refused it, with no error and nothing to override. So the
assertion with teeth here is that a conversation whose window shut days ago STILL RENDERS A
WORKING REPLY BOX, with a warning above it.

AND SILENCE WHEN THE ANSWER IS YES. "You can reply now" over a reply box is the screen narrating
itself. The sentence earns its space only when the answer is something other than yes.

Run: python tests/test_window_above_the_box.py
"""
import os
import pathlib
import re
import sys
import tempfile
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB = pathlib.Path(tempfile.mkdtemp(prefix="win-box-")) / "box.db"
# BEFORE ANY core IMPORT — core.config.Settings reads os.environ at class-body time.
os.environ["AIOS_DB_PATH"] = str(_DB)
os.environ.setdefault("DASH_TOKEN", "test-dash-pw")

from core import state                                    # noqa: E402

state.init_db()

from marketing.customer_voice.inbox import store           # noqa: E402

_failed = 0
SPACE = "default"
NOW = dt.datetime.now(dt.timezone.utc)


def ok(what: str, cond: bool, got: str = "") -> None:
    global _failed
    if cond:
        print(f"  ok   {what}")
    else:
        _failed += 1
        print(f"  FAIL {what}" + (f"  — {got}" if got else ""))


def _c():
    from core.config import settings
    from core.dispatch import app
    c = app.test_client()
    c.post("/dash/login", data={"token": settings.dash_token})
    return app, c


def _text(html_: str) -> str:
    import html as _h
    stripped = re.sub(r"(?s)<(script|style|svg).*?</\1>", " ", html_)
    return " ".join(_h.unescape(re.sub(r"<[^>]+>", " ", stripped)).split())


def _wipe() -> None:
    with state.connect() as c:
        c.execute("DELETE FROM inbox_messages")
        c.execute("DELETE FROM inbox_conversations")


def _thread(zcid, *, platform="instagram", hours=2, who="Dana Whitfield", account="a1"):
    store.upsert_conversation(space=SPACE, zcid=zcid, platform=platform, participant=who,
                              last_inbound_at=(NOW - dt.timedelta(hours=hours)).isoformat(),
                              account_id=account)
    store.record_message(space=SPACE, zcid=zcid, zmid=None, direction="in",
                         sent_by="contact", body="Can you come out?")


def _page(zcid):
    app, c = _c()
    return c.get(f"/inbox/inbox/{zcid}").get_data(as_text=True)


def test_a_shut_window_still_renders_a_working_reply_box():
    """THE ONE THAT MATTERS. #1290: "nothing here may ever stop a send." A screen is the easiest
    place to break that by accident — a hidden box stops the send as dead as a refusal would, with
    no error and no override, and it would not even be true: reply.py never consults the window to
    decide, only to translate a refusal the vendor already made."""
    _wipe()
    _thread("shut", platform="instagram", hours=300)
    html_ = _page("shut")
    ok("the reply box is present", 'class="compose"' in html_ and 'id="reply"' in html_)
    ok("...with a Send button on it", "Send" in _text(html_))
    words = _text(html_)
    ok("...under a warning that says what will probably happen",
       "more than 24 hours since they wrote to you" in words, words[:260])
    ok("...which hands the decision to the channel, not to us",
       "Instagram decides, not us" in words, words[:260])


def test_an_open_window_says_nothing_at_all():
    """"You can reply now" over a reply box is the screen narrating itself."""
    _wipe()
    _thread("open", platform="instagram", hours=2)
    html_ = _page("open")
    words = _text(html_)
    ok("the box is there", 'class="compose"' in html_)
    ok("...and the screen does not congratulate itself", "You can reply now" not in words,
       words[:220])
    ok("...no window card is drawn at all", 'class="card win' not in html_)


def test_the_tagged_window_tells_him_the_restriction_it_cannot_enforce():
    """Code cannot read intent, so the person is TOLD rather than policed — and told plainly
    enough to act on."""
    _wipe()
    _thread("tagged", platform="messenger", hours=30)
    words = _text(_page("tagged"))
    ok("it says he can still reply", "You can reply" in words, words[:240])
    ok("...and what the reply must be", "answer what they asked" in words, words[:240])
    ok("...and what it must not be", "No offers, discounts or promotion" in words, words[:240])


def test_the_note_is_styled_on_state_never_on_the_english():
    """OSDev4 added `state` to explain() precisely so a screen can style a row "without matching
    on English or reading `_RULES`". A screen that branched on the sentence would break the first
    time he improved the copy — and copy is the half most likely to change."""
    _wipe()
    _thread("styled", platform="instagram", hours=300)
    html_ = _page("styled")
    ok("the state reaches the markup as a class", 'class="card win closed"' in html_,
       html_[html_.find("card win"):][:80] if "card win" in html_ else "no win card")
    src = pathlib.Path("marketing/customer_voice/app.py")
    from marketing.customer_voice import app as voice        # the exporter must see this
    body = pathlib.Path(voice.__file__).read_text()
    fn = body[body.find("def _window_note("):]
    fn = fn[:fn.find("\ndef ")]
    ok("the renderer reads `state`", '"state"' in fn or "get(\"state\")" in fn)
    ok("...and never branches on `reason`", '"reason"' not in fn, fn[:200])


def test_email_gets_its_reply_box_and_is_never_told_a_window_shut():
    """Email used to draw no compose box: the box had no SMTP path, so it said "send it from your
    own mail app" instead of offering a dead button. Owner, 2026-09-22 — it sends email now.

    THE PAIR THAT MATTERS IS THE TWO AGES. Email has no clock, so a two-hour-old thread and a
    year-old one must read identically. Getting that wrong is how a screen tells a business owner
    he may not answer his own customer, which is the thing this whole file exists to prevent.
    """
    for hours in (2, 24 * 400):
        _wipe()
        _thread("mail", platform="email", hours=hours)
        html_ = _page("mail")
        words = _text(html_)
        ok(f"email draws a reply box at {hours}h — the box sends it", 'class="compose"' in html_)
        ok(f"...and no longer sends them to their own mail app ({hours}h)",
           "mail app" not in words, words[:200])
        ok(f"...and is never told a window shut, because email has none ({hours}h)",
           "window" not in words.lower(), words[:200])


def test_explain_falling_over_costs_the_note_and_never_the_box():
    """`explain()` is documented never to raise. This screen is the one place where being wrong
    about that would take away a working reply box on a live conversation."""
    _wipe()
    _thread("boom", platform="instagram", hours=300)
    from marketing.customer_voice.inbox import window as _w
    real = _w.explain

    def explodes(*a, **k):
        raise RuntimeError("window is on fire")

    _w.explain = explodes
    try:
        html_ = _page("boom")
    finally:
        _w.explain = real
    ok("the reply box survives", 'class="compose"' in html_)
    ok("...and no window card is drawn", 'class="card win' not in html_)
    ok("...and the failure never reaches the page", "on fire" not in html_)


def test_ci_actually_runs_this_file():
    """A GUARD MUST KNOW WHERE IT IS STANDING. `.github/` is ours and never ships, and this suite
    DOES ship to a Customer Voice box — a bare read crashes a paying buyer's own run."""
    here = pathlib.Path(__file__).resolve().parents[1]
    if not (here / ".github").is_dir():
        print("  --   not the repo — a buyer's box has no CI manifest to be named in")
        return
    wf = (here / ".github/workflows/tests.yml").read_text()
    ok("test_window_above_the_box is in the workflow's suite list",
       "test_window_above_the_box" in wf)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    print("all ok" if not _failed else f"{_failed} FAILED")
    sys.exit(1 if _failed else 0)
