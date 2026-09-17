"""The reply box asks the messages, not the clock column.

OSDev4's call, 2026-09-17: "a thread only renders because messages exist." `_compose` was hiding
the reply box whenever `inbox_conversations.last_inbound_at` was empty, on the reasoning that a
reply-only channel has nothing to reply to. That column is an INFERENCE a poller fills, and two
paths leave it NULL on a thread the customer demonstrably wrote on — `upsert_conversation`
COALESCEs only non-null values in, and `email_channel` passes None whenever the message it is
mirroring happens to be outbound.

WHAT RENDERING SHOWED, AND IT IS THE REASON THIS SUITE EXISTS. A thread printed three messages,
two of them from the customer in her own words, and then closed with "Nothing has come in on this
conversation yet." The page contradicted itself inside one scroll, and the reply box — the whole
product on a reply-only channel — was gone.

THE SENTENCE IS NOT DELETED, BECAUSE IT IS SOMETIMES TRUE. A thread carrying only outbound
messages genuinely has nothing to reply to, and the assertions below hold that case exactly as it
was. What changed is which question decides: the messages, which are the fact, instead of a column
that was standing in for them.

THE TRAP THIS SUITE GUARDS HARDEST is the fix that looks better than this one. A message's
`created_at` is `state._now()` at MIRROR time, not when the customer hit send, so dating the
window from the messages would stamp a backfilled three-week-old thread as minutes old and print
"you can reply now" over a window that shut a fortnight ago. That is a confident lie pushing
toward a send, which is worse than the silence it replaces. The note says it cannot tell the hour.

Run: python tests/test_the_box_asks_the_messages.py
"""
import os
import pathlib
import re
import sys
import tempfile
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB = pathlib.Path(tempfile.mkdtemp(prefix="asks-msgs-")) / "box.db"
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


def _conv(zcid, *, platform="instagram", clock=None, who="Dana Whitfield", account="a1",
          opted_out=False):
    store.upsert_conversation(space=SPACE, zcid=zcid, platform=platform, participant=who,
                              last_inbound_at=clock, account_id=account)
    if opted_out:
        with state.connect() as c:
            c.execute("UPDATE inbox_conversations SET opted_out = 1 "
                      " WHERE space = ? AND zernio_conversation_id = ?", (SPACE, zcid))


def _msg(zcid, direction, body, *, sent_by=None):
    store.record_message(space=SPACE, zcid=zcid, zmid=None, direction=direction,
                         sent_by=sent_by or ("contact" if direction == "in" else "human"),
                         body=body)


def _page(zcid):
    _, c = _c()
    return c.get(f"/inbox/inbox/{zcid}").get_data(as_text=True)


def _inbox():
    _, c = _c()
    return c.get("/inbox/inbox").get_data(as_text=True)


# ── the defect itself ───────────────────────────────────────────────────────────────────────────

def test_a_thread_printing_her_words_does_not_say_she_never_wrote():
    """THE ONE THAT MATTERS. Rendered on main before this change: her messages on the screen, and
    under them the sentence saying nothing had ever come in, with no box to answer her in."""
    _wipe()
    _conv("nullclock", clock=None)
    _msg("nullclock", "in", "Can you come out Thursday?")
    _msg("nullclock", "out", "Yes — 9am works.")
    _msg("nullclock", "in", "Perfect, see you then.")
    html_ = _page("nullclock")
    words = _text(html_)
    ok("her words are on the screen", "Can you come out Thursday?" in words)
    ok("...and the screen does not then deny she wrote them",
       "Nothing has come in on this conversation yet" not in words, words[-220:])
    ok("...the reply box is there", 'class="compose"' in html_ and 'id="reply"' in html_)
    ok("...with a Send button on it", "Send" in words)


def test_the_row_offers_reply_on_that_same_thread():
    """`_acts` documents that Reply appears on exactly the rows whose thread shows a box. It asked
    the same dead column, so it withheld Reply from the same conversations."""
    _wipe()
    _conv("nullclock", clock=None)
    _msg("nullclock", "in", "Can you come out Thursday?")
    html_ = _inbox()
    ok("the hover menu offers Reply", ">Reply<" in html_)
    ok("...pointing at the thread's own box", "#reply" in html_)


def test_the_row_does_not_tag_her_thread_no_inbound_yet():
    """A tag is a claim about the conversation. "No inbound yet" on a thread full of her messages
    is a false one, and it sat beside the row that would not offer Reply."""
    _wipe()
    _conv("nullclock", clock=None)
    _msg("nullclock", "in", "Can you come out Thursday?")
    words = _text(_inbox())
    ok("no false 'No inbound yet' tag", "No inbound yet" not in words, words[:260])
    ok("...it reads the window instead, which is the honest unknown",
       "Window closed" in words, words[:260])


# ── the sentence is kept, because it is sometimes true ──────────────────────────────────────────

def test_an_outbound_only_thread_still_says_there_is_nothing_to_reply_to():
    """NOT DELETED, NARROWED. Every channel here is reply-only, so a thread carrying only our own
    messages genuinely has nothing to reply to, and the box would be a control that cannot win."""
    _wipe()
    _conv("outonly", clock=None)
    _msg("outonly", "out", "Just following up on your quote.")
    html_ = _page("outonly")
    words = _text(html_)
    ok("the sentence still prints when it is true",
       "Nothing has come in on this conversation yet" in words, words[-220:])
    ok("...and there is no box", 'class="compose"' not in html_)
    ok("...and the row withholds Reply to match", ">Reply<" not in _inbox())
    ok("...and the row does tag it", "No inbound yet" in _text(_inbox()))


def test_a_thread_with_no_messages_at_all_never_reaches_the_box():
    """The route returns before `_compose` on an empty thread, which is why the branch above can
    be about outbound-only threads without also having to be about empty ones."""
    _wipe()
    _conv("silent", clock=None)
    html_ = _page("silent")
    ok("it says so plainly", "No messages have been mirrored" in _text(html_))
    ok("...and draws no box", 'class="compose"' not in html_)


# ── the note above the box ──────────────────────────────────────────────────────────────────────

def test_the_note_admits_it_cannot_tell_the_hour():
    """Moving the box back without this branch just moves the false sentence one line lower —
    `explain()` handed a NULL column answers "nothing from them has reached the box yet"."""
    _wipe()
    _conv("nullclock", clock=None)
    _msg("nullclock", "in", "Can you come out Thursday?")
    words = _text(_page("nullclock"))
    ok("it does not repeat the denial in the note",
       "Nothing from them has reached the box yet" not in words, words[:300])
    ok("...it says what is actually true", "cannot tell when they last wrote" in words, words[:300])
    ok("...and hands the decision to the channel", "the channel decides" in words, words[:300])


def test_the_note_never_claims_the_window_is_open():
    """THE TEMPTING FIX, REFUSED. `created_at` is `state._now()` at MIRROR time, so a backfilled
    three-week-old thread looks minutes old. Dating the window from it would print "you can reply
    now" over a window that shut a fortnight ago — a confident lie pushing toward a send."""
    _wipe()
    _conv("nullclock", clock=None)
    _msg("nullclock", "in", "Can you come out Thursday?")     # created_at == now, by mirror time
    words = _text(_page("nullclock"))
    ok("no 'you can reply now'", "You can reply now" not in words, words[:300])
    ok("no claim of hours remaining", "hours left" not in words and "remaining" not in words,
       words[:300])
    ok("...but the box is present anyway, because a note never blocks a send",
       'class="compose"' in _page("nullclock"))


def test_a_real_clock_still_drives_the_real_note():
    """The new branch is reached only when the column is empty. A thread that HAS a clock must
    still get OSDev4's `explain()` sentence, unchanged."""
    _wipe()
    _conv("shut", clock=(NOW - dt.timedelta(hours=300)).isoformat())
    _msg("shut", "in", "Are you still around?")
    words = _text(_page("shut"))
    ok("the real window sentence survives",
       "more than 24 hours since they wrote to you" in words, words[:300])
    ok("...and not the unknown-clock one", "cannot tell when they last wrote" not in words)
    ok("...and the box is still there", 'class="compose"' in _page("shut"))


def test_an_open_window_is_still_silent():
    _wipe()
    _conv("open", clock=(NOW - dt.timedelta(hours=2)).isoformat())
    _msg("open", "in", "Morning!")
    words = _text(_page("open"))
    ok("no note at all when the answer is simply yes",
       "cannot tell when they last wrote" not in words and "You can reply now" not in words)
    ok("...and the box is there", 'class="compose"' in _page("open"))


# ── the store column ────────────────────────────────────────────────────────────────────────────

def test_has_inbound_is_not_awaiting_reply():
    """Two different questions, and conflating them would withhold the box from every thread the
    owner has already answered — the most ordinary thing in an inbox to answer again."""
    _wipe()
    _conv("answered", clock=None)
    _msg("answered", "in", "Do you do gutters?")
    _msg("answered", "out", "We do — Tuesday work?")
    row = [r for r in store.list_conversations(space=SPACE)
           if r["zernio_conversation_id"] == "answered"][0]
    ok("the newest message is ours, so it is not waiting", not row["awaiting_reply"])
    ok("...but she has written, so there is something to reply to", bool(row["has_inbound"]))
    ok("...and the thread draws a box", 'class="compose"' in _page("answered"))


def test_both_readers_carry_it():
    """A row that has something to reply to does not stop having it because somebody typed a name
    into the search box — the same reason `awaiting_reply` is selected in both."""
    _wipe()
    _conv("found", clock=None, who="Marcus Bell")
    _msg("found", "in", "Quote please")
    listed = [r for r in store.list_conversations(space=SPACE)
              if r["zernio_conversation_id"] == "found"]
    searched = [r for r in store.search_conversations(space=SPACE, query="Marcus")
                if r["zernio_conversation_id"] == "found"]
    ok("the list carries has_inbound", listed and bool(listed[0]["has_inbound"]))
    ok("the search carries it too", searched and bool(searched[0]["has_inbound"]))


def test_it_is_false_where_it_should_be():
    _wipe()
    _conv("none", clock=None)
    _msg("none", "out", "Checking in.")
    row = [r for r in store.list_conversations(space=SPACE)
           if r["zernio_conversation_id"] == "none"][0]
    ok("outbound only is not inbound", not row["has_inbound"])


def test_it_does_not_leak_across_conversations():
    """The subselect is keyed on space AND conversation. A bare `direction = 'in'` would make one
    inbound message anywhere in the box unlock the reply form on every silent row in it."""
    _wipe()
    _conv("loud", clock=None)
    _msg("loud", "in", "Hello!")
    _conv("quiet", clock=None)
    _msg("quiet", "out", "Anyone there?")
    rows = {r["zernio_conversation_id"]: r for r in store.list_conversations(space=SPACE)}
    ok("the one with inbound has it", bool(rows["loud"]["has_inbound"]))
    ok("the one without does not", not rows["quiet"]["has_inbound"])


# ── the branches that must still win ────────────────────────────────────────────────────────────

def test_opted_out_still_beats_everything():
    """A box to type into when the words can never leave is the failure this file names by hand."""
    _wipe()
    _conv("stopped", clock=None, opted_out=True)
    _msg("stopped", "in", "STOP")
    html_ = _page("stopped")
    ok("no box for someone who said STOP", 'class="compose"' not in html_)
    ok("...and the banner says why", "opted out" in _text(html_))
    ok("...and the row offers no Reply", ">Reply<" not in _inbox())


def test_a_channel_with_no_send_rule_still_shows_no_box():
    """`window.decide`'s most important branch: adding a platform to the poller must never be
    enough, by itself, to authorise a send on it. A compose box IS that authorisation."""
    _wipe()
    _conv("norule", platform="gutter_pigeons", clock=None)
    _msg("norule", "in", "Coo.")
    html_ = _page("norule")
    ok("no box on a channel nobody wrote a rule for", 'class="compose"' not in html_)
    ok("...and it says so without blaming the buyer", "no reply rule" in _text(html_).lower())


def test_a_thread_missing_its_account_still_shows_no_box():
    _wipe()
    _conv("noacct", clock=None, account=None)
    _msg("noacct", "in", "Hi there")
    html_ = _page("noacct")
    ok("no box without an account to send from", 'class="compose"' not in html_)
    ok("...and the row agrees", ">Reply<" not in _inbox())


# ── the two surfaces agree ──────────────────────────────────────────────────────────────────────

def test_the_row_and_the_thread_agree_on_every_shape():
    """The promise `_acts` makes in its own docstring, held across the cases this change touches.
    A menu item that lands on a thread with nowhere to type is the same broken promise as a
    greyed-out one, just further away."""
    shapes = [
        ("in_nullclock",  dict(clock=None),                          ["in"],        True),
        ("in_realclock",  dict(clock=(NOW - dt.timedelta(hours=2)).isoformat()), ["in"], True),
        ("in_shut",       dict(clock=(NOW - dt.timedelta(hours=300)).isoformat()), ["in"], True),
        ("answered",      dict(clock=None),                          ["in", "out"], True),
        ("out_only",      dict(clock=None),                          ["out"],       False),
        ("opted",         dict(clock=None, opted_out=True),          ["in"],        False),
        ("norule",        dict(clock=None, platform="gutter_pigeons"), ["in"],      False),
        ("noacct",        dict(clock=None, account=None),            ["in"],        False),
    ]
    for zcid, kw, dirs, expect in shapes:
        _wipe()
        _conv(zcid, **kw)
        for d in dirs:
            _msg(zcid, d, "a message")
        box = 'class="compose"' in _page(zcid)
        reply = ">Reply<" in _inbox()
        ok(f"{zcid}: the box is {'there' if expect else 'absent'}", box is expect)
        ok(f"{zcid}: and the row's Reply item matches it", reply is box,
           f"box={box} reply={reply}")


# ── the screen ships to a buyer, not to us ──────────────────────────────────────────────────────

def test_the_suite_is_named_in_ci():
    """Every suite in this stack is registered, one per line, or it silently stops running."""
    here = pathlib.Path(__file__).resolve().parents[1]
    if not (here / ".github").is_dir():
        print("  --   not the repo — a buyer's box has no CI manifest to be named in")
        return
    wf = (here / ".github/workflows/tests.yml").read_text()
    ok("this suite runs in CI", "test_the_box_asks_the_messages \\" in wf)


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(f"\n{name}")
            fn()
    print("\n" + ("FAILED" if _failed else "PASS") + f" — {_failed} failure(s)")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
