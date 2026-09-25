"""Today opens with a greeting, the app ships its own typefaces, and the orb sits in the bar.

THREE OWNER CALLS, 2026-09-18, in one screen's worth of work:
  1. *"Yes today header."*  — a greeting and one plain sentence, after he put a competitor's
     phone screen beside ours and ours opened with the word "Today" in an h1.
  2. *"A all the way."*     — Archivo headings over a Public Sans body, of the three pairings
     rendered on the real screen.
  3. *"Ship the orb in place and make it look really cool like glass, but don't make it do
     anything yet."* — his call, made after I argued that a control which does nothing is the
     dead control this app keeps deleting. The objection was mine and it lost; what this suite
     guards is that it is not a CONTROL at all, so nobody is promised anything.

WHAT THIS SUITE IS REALLY FOR. Not that the words appear — that a future tidy-up cannot quietly
undo the three things that make them true: the screen must keep computing nothing, the fonts
must keep coming from this box, and the orb must keep being decoration.

THE BUG THIS WORK UNCOVERED IS IN HERE TOO, and it is the most valuable line in the file. The
report reads a Space and so does the screen, and they were not the same one: `_space_name()`
answers "the first Space" and `app._space()` answers "the Space of this request". On a sold box
there is one and they agree; on a multi-Space box `/inbox/` was rendering one tenant's inbox
figures directly above another tenant's conversation list. Found by rendering the greeting and
noticing it had no sentence.
"""
import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB = pathlib.Path(tempfile.mkdtemp(prefix="today-hello-")) / "box.db"
# BEFORE ANY core IMPORT — core.config.Settings reads os.environ at class-body time.
os.environ["AIOS_DB_PATH"] = str(_DB)
os.environ.setdefault("DASH_TOKEN", "test-dash-pw")

from datetime import datetime, timedelta, timezone   # noqa: E402

from core import state                               # noqa: E402

state.init_db()

from marketing.customer_voice import app as _app     # noqa: E402
from marketing.customer_voice import report as _rep  # noqa: E402
from marketing.customer_voice.inbox import store     # noqa: E402

_SRC = pathlib.Path(_app.__file__).read_text()
_ROOT = pathlib.Path(_app.__file__).resolve().parent
_failed = 0
NOW = datetime.now(timezone.utc)
SPACE = "default"


def ok(what: str, cond: bool, got: str = "") -> None:
    global _failed
    if cond:
        print(f"  ok   {what}")
    else:
        _failed += 1
        print(f"  FAIL {what}" + (f"  — {got}" if got else ""))


def _listening(on: bool = True) -> None:
    """Say out loud whether this box has a channel that could deliver a message.

    THIS SUITE PASSED LOCALLY AND FAILED IN CI FOR WANT OF THIS. `r_today` leads with the SET-UP,
    and renders no greeting at all, on a box nothing can reach — a deliberate contract, guarded by
    test_today_first_screen. Seeding conversations does not make a box listening, so every greeting
    assertion here was silently running on whatever credential happened to be sitting in the
    developer's own box_secrets. On a clean runner there is none, Today correctly rendered the
    set-up, and four assertions failed for a reason that had nothing to do with greetings.

    A test must never read the machine it runs on for a fact the product branches on.
    """
    from core import box_secrets, spaces
    # BOTH HALVES, because `_nothing_arrives_yet()` takes a mailbox credential OR a Zernio key —
    # stubbing one leaves the other reading this machine, which is the bug this helper exists for.
    box_secrets.email_credential = (lambda: {"user": "a@b.c", "password": "x"}) if on \
        else (lambda: {})
    spaces.all_spaces = (lambda: [{"name": SPACE, "zernio_key": "z"}]) if on \
        else (lambda: [{"name": SPACE}])


def _c():
    from core.config import settings
    from core.dispatch import app
    c = app.test_client()
    c.post("/dash/login", data={"token": settings.dash_token})
    return c


def _talk(zcid: str, who: str, mins: int = 5, direction: str = "in") -> None:
    store.upsert_conversation(space=SPACE, zcid=zcid, platform="instagram", participant=who,
                              last_inbound_at=(NOW - timedelta(minutes=mins)).isoformat())
    store.record_message(space=SPACE, zcid=zcid, zmid=f"{zcid}-0", direction=direction,
                         sent_by="them" if direction == "in" else "us", body="a message")


def _wipe() -> None:
    with state.connect() as c:
        c.execute("DELETE FROM inbox_messages")
        c.execute("DELETE FROM inbox_conversations")


def _hello_of(body: str) -> str:
    if 'class="hello"' not in body:
        return ""
    return body.split('class="hello"', 1)[1].split("</div>", 1)[0]


# ── the greeting replaced a heading that said nothing ─────────────────────────────────────────
print("test_today_opens_with_a_greeting")
_listening(True)
_wipe()
_talk("g1", "Dana Whitfield", mins=3)
_c_ = _c()
_b = _c_.get("/inbox/").get_data(as_text=True)
ok("Today answers", "<h1>" in _b)
ok("it opens with the greeting block", 'class="hello"' in _b)
# THE DATE IS IN THE BAR AND "Today" IS THE TAB HE PRESSED. The largest type on the app's first
# screen was spent on the one fact already stated twice.
# READ THE MARKUP, NOT THE PAGE. This app inlines its whole stylesheet, so every CSS COMMENT
# ships as page bytes — and the comment above `.hello` explains that it replaced an
# `<h1>Today</h1>`, which is the exact string this line looks for. It matched its own
# explanation. THIRD TIME this trap has cost a test tonight; the fix is always the same, which
# is to read the region you actually mean.
_markup = _b.split("</style>", 1)[-1]
ok("...and no longer with an h1 that says Today", "<h1>Today</h1>" not in _markup)
ok("the greeting is a time of day",
   any(w in _b for w in ("Good morning.", "Good afternoon.", "Good evening.")), _b[:0])
ok("...and the sentence carries the numbers", 'class="line"' in _b)


# ── the sentence is the report's, not the screen's ────────────────────────────────────────────
print("\ntest_the_screen_still_computes_nothing")
# THE RULE THIS PAGE LIVES BY: one producer, so the 8am message and the screen can never
# disagree. That is why `unread` was added to `report()` rather than counted here.
_today_src = _SRC.split("def r_today(", 1)[-1].split("\ndef ", 1)[0]
_hello_src = _SRC.split("def _hello(", 1)[-1].split("\ndef ", 1)[0]
for _name, _src in (("r_today", _today_src), ("_hello", _hello_src)):
    ok(f"{_name} calls no store reader of its own",
       not re.search(r"(unread_conversations|awaiting_reply|day_counts)\s*\(", _src),
       f"{_name} is counting for itself")
ok("the report is what produces unread", "unread_conversations(" in
   pathlib.Path(_rep.__file__).read_text())
ok("...and exposes it as a figure", '"inbox_unread"' in pathlib.Path(_rep.__file__).read_text())


# ── the Space bug this work found ─────────────────────────────────────────────────────────────
print("\ntest_the_report_answers_for_the_space_the_screen_is_showing")
# THE FAILURE: `_space_name()` is "the first Space" and `_space()` is "this request's Space".
# They agree on a sold box and not on a multi-Space one, so Today rendered one tenant's figures
# above another tenant's list. Caught because the greeting came back with no sentence at all.
import inspect                                      # noqa: E402
ok("report() takes a space", "space" in inspect.signature(_rep.report).parameters)
ok("...and Today passes its own", "_report(day, space=_space())" in _SRC)
# DELIBERATELY DIFFERENT COUNTS. The first version of this seeded one conversation in each
# Space, so both reported "1 waiting" and the assertion passed for a reason that had nothing to
# do with scoping — it would have gone green with the argument ignored entirely.
_other = "a-different-space"
for _i in range(3):
    store.upsert_conversation(space=_other, zcid=f"other{_i}", platform="instagram",
                              participant=f"Somebody Else {_i}",
                              last_inbound_at=(NOW - timedelta(minutes=2)).isoformat())
    store.record_message(space=_other, zcid=f"other{_i}", zmid=f"other{_i}-0", direction="in",
                         sent_by="them", body="not this tenant")
from core.report import today as _rtoday            # noqa: E402
_mine = (_rep.report(_rtoday(), space=SPACE).get("figures") or {})
_theirs = (_rep.report(_rtoday(), space=_other).get("figures") or {})
ok("this Space has one waiting", (_mine.get("inbox_waiting") or {}).get("value") == 1,
   str(_mine.get("inbox_waiting")))
ok("...and the other has three, from the same function",
   (_theirs.get("inbox_waiting") or {}).get("value") == 3, str(_theirs.get("inbox_waiting")))
ok("...and asking for a Space with nothing in it says nothing about the inbox",
   "inbox_waiting" not in (_rep.report(_rtoday(), space="empty-space").get("figures") or {}))


# ── every sentence it can say is true ─────────────────────────────────────────────────────────
print("\ntest_all_four_sentences")
def _line(figs: dict) -> str:
    return re.sub(r"<[^>]+>", "", _app._hello(figs))

_both = _line({"inbox_waiting": {"value": 9}, "inbox_unread": {"value": 4}})
ok("unread and waiting", "4 unread" in _both and "9 people" in _both, _both)
_u = _line({"inbox_waiting": {"value": 0}, "inbox_unread": {"value": 4}})
ok("unread only, and it says nobody is waiting",
   "4 unread" in _u and "Nobody is waiting" in _u, _u)
_w = _line({"inbox_waiting": {"value": 1}})
ok("waiting only, singular", "Nothing unread" in _w and "1 person" in _w, _w)
_none = _line({"inbox_waiting": {"value": 0}})
ok("neither — and it says the good thing out loud", "caught up" in _none, _none)
# NO INBOX SEGMENT, NO SENTENCE. Inventing "you have 0 of everything" for a box that has never
# had a conversation is the row of zeroes this screen exists not to show.
ok("a box with no inbox segment gets the greeting alone",
   'class="line"' not in _app._hello({}), _line({}))


# ── a dash is not a figure ────────────────────────────────────────────────────────────────────
print("\ntest_a_dash_is_never_rendered_as_a_number")
# Under the old `<h1>Today</h1>` a shrug was harmless. Under a greeting that has just said
# something true it is the largest thing on the screen, which is what made this mine to fix.
_tiles = re.findall(r'<div class="fig"><div class="v">([^<]*)</div>', _b)
ok("no figure tile is a dash", not any(t.strip() == "—" for t in _tiles), str(_tiles))
ok("...and the headline block is absent rather than shrugging",
   not re.search(r'<div class="head"><div class="v">\s*—\s*</div>', _b))
ok("the tiles that remain all say something", all(t.strip() for t in _tiles), str(_tiles))


# ── the box serves its own typefaces ──────────────────────────────────────────────────────────
print("\ntest_the_box_serves_its_own_typefaces")
for _n, _fam in (("head", "Archivo"), ("body", "Public Sans")):
    _r = _c_.get(f"/inbox/font/{_n}.woff2")
    ok(f"/inbox/font/{_n}.woff2 is served by this box", _r.status_code == 200, str(_r.status_code))
    ok(f"...as a real woff2", _r.get_data()[:4] == b"wOF2", str(_r.get_data()[:4]))
    ok(f"...and as font/woff2", _r.headers.get("Content-Type") == "font/woff2",
       str(_r.headers.get("Content-Type")))
    # KEPT, NOT DECLARED: the inbox wears the box's Inter now (D1, docs/SCOPE_DESIGN_LANGUAGE.md),
    # and these files stay served so a page cached before that release still draws.
# THE WHOLE POINT: a single-tenant box installed to a home screen must not need a third party
# to draw its own text, and must not report every open to one.
ok("no page asks Google for a font",
   "fonts.googleapis.com" not in _b and "fonts.gstatic.com" not in _b)
ok("...and neither does the stylesheet",
   "googleapis" not in _app.CSS and "gstatic" not in _app.CSS)
# THE TYPE IS THE BOX'S (owner, 2026-09-24, D1): Inter and Geist Mono, declared once in core's
# box.css and served from /ui/font/, which the inbox links through look.head_tags().
_box_css = (_ROOT.parents[1] / "core" / "dash" / "static" / "box.css").read_text()
ok("the inbox page links the box's stylesheet", "/ui/box.css?v=" in _b)
ok("...and the box's text face is preloaded, so the first paint is already Inter",
   'href="/ui/font/sans.woff2"' in _b)
ok("the inbox's type is the box's sans, never its own family",
   "font:16px/1.5 var(--sans)" in _app.CSS and "Archivo" not in re.sub(r"(?s)/\*.*?\*/", "", _app.CSS)
   and "Public Sans" not in re.sub(r"(?s)/\*.*?\*/", "", _app.CSS))
ok("text is never invisible while a face loads", _box_css.count("font-display: swap") >= 2)
ok("the fallback stack survives the first family",
   '"Inter", -apple-system' in _box_css)
# A NAME IS A KEY, NEVER A PATH.
ok("an unknown face is a 404", _c_.get("/inbox/font/nope.woff2").status_code == 404)
ok("a traversal is a miss, not a file",
   _c_.get("/inbox/font/..%2f..%2fapp.woff2").status_code == 404)
# OFL 1.1 REQUIRES THE LICENCE TO TRAVEL WITH THE FILES, and these ship in every clone.
for _f in ("archivo-latin-var.woff2", "public-sans-latin-var.woff2",
           "OFL-Archivo.txt", "OFL-PublicSans.txt", "README.md"):
    ok(f"fonts/{_f} is in the repository", (_ROOT / "fonts" / _f).is_file())
ok("both licences name the SIL Open Font License",
   all("SIL Open Font License" in (_ROOT / "fonts" / n).read_text()
       for n in ("OFL-Archivo.txt", "OFL-PublicSans.txt")))


# ── the orb is in place and is not a control ───────────────────────────────────────────────────
print("\ntest_the_orb_is_decoration_not_a_control")
_bar = _b.split('<nav class="tabs"', 1)[-1]
ok("the orb is in the bar", 'class="orb"' in _bar)
# COUNT THE TABS, NOT THE WORD. `class="tab` is a prefix of `class="tabs-in"`, so the naive
# count read 3 on the left and 2 on the right for a bar that is in fact symmetrical.
_left, _right = _bar.split('class="orb"', 1)
_n = lambda part: len(re.findall(r'<a class="tab[ "]', part))
ok("...dead centre of an even number of tabs", _n(_left) == _n(_right) == 2,
   f"{_n(_left)} left, {_n(_right)} right")
# HIS CALL: in place, glass, doing nothing. So it is not a disabled button (which reads as
# broken) and not a link somewhere unrelated (which is worse) — it is decoration.
ok("it is not a link", '<a class="orb' not in _bar)
ok("it is not a button", "<button" not in _bar)
ok("it is hidden from a screen reader", 'class="orb" aria-hidden="true"' in _bar)
ok("it is out of the focus order", "tabindex" not in _bar)
ok("...and a tap falls through it", "pointer-events:none" in _app.CSS)
# GLASS IS FOUR THINGS STACKED. Take one away and it is a coloured bead.
for _tok in ("--orb-glass", "--orb-sheen", "--orb-lift", "--orb-floor"):
    ok(f"{_tok} is declared in both themes", _app.CSS.count(_tok + ":") == 2,
       str(_app.CSS.count(_tok + ":")))
ok("the bead is a circle in a slot, not the slot itself",
   ".tabs-in .orb .bead{" in _app.CSS and "width:54px;height:54px" in _app.CSS)
ok("...with a specular of its own", ".bead::before" in _app.CSS)
ok("...and a shadow on the floor", ".bead::after" in _app.CSS)
# A FLAT ORB ON A DESKTOP WOULD BE A MYSTERY: the bar it belongs to is gone above 820px.
ok("it is absent where the bar is",
   re.search(r"@media \(min-width:821px\)\{[^@]*\.tabs-in \.orb\{display:none\}", _app.CSS,
             re.S) is not None)


# ── THE SEAM THAT BROKE EIGHT ASSERTIONS SILENTLY ─────────────────────────────────────────────
print("\ntest_a_box_nothing_can_reach_still_opens_on_the_set_up")
# THE GREETING DOES NOT OUTRANK THE SET-UP. On day one a buyer needs to connect a channel, not to
# be greeted, and test_today_first_screen already guards that order. This asserts the new header
# did not quietly climb above it — the two screens cannot drift while both are checked.
_listening(False)
_bare = _c().get("/inbox/").get_data(as_text=True)
ok("a bare box leads with the set-up", "Your box is running" in _bare)
ok("...and is given no greeting to read instead", 'class="hello"' not in _bare)
_listening(True)
_after = _c().get("/inbox/").get_data(as_text=True)
ok("...while a listening box gets the greeting back", 'class="hello"' in _after)


print("\ntest_the_screen_and_the_report_agree_on_the_signature")
# r_today calls report(day, space=…) INSIDE A GUARD that turns any exception into "the report
# could not be read". That guard is right — a buyer must never see a stack trace — but it also
# swallows a plain signature mismatch, and the screen then renders the set-up with no figures and
# no greeting while every suite that does not assert on a figure stays green. That is exactly what
# happened: a stub written as `lambda day:` degraded test_today_first_screen's eight assertions in
# one go. So assert the signature here, where a mismatch is a failure and not a degradation.
import inspect as _inspect
_sig = _inspect.signature(_rep.report)
ok("report() takes the Space the screen is showing", "space" in _sig.parameters, str(_sig))
ok("...defaulted, so the 8am message and every other caller are unchanged",
   "space" in _sig.parameters and _sig.parameters["space"].default is None,
   str(_sig.parameters.get("space")))
ok("...and the screen actually passes it",
   "space=_space()" in _inspect.getsource(_app.r_today))


# ── WHAT A BUYER MEETS, MEASURED ON AN EXPORTED BOX ───────────────────────────────────────────
print("\ntest_nothing_a_buyer_reads_is_hidden_or_said_twice")
# BOTH OF THESE WERE FOUND BY EXPORTING A BOX WITH NOTHING CONNECTED AND READING THE SCREENS,
# on the morning we started bringing customers on. Neither was visible in a diff.

# 1. THE ORB IS OPAQUE AND RISES ABOVE THE BAR. The bar is frosted, so content passing under it
# stays legible by design; the bead simply hides what it covers. The page's bottom padding cleared
# the BAR and not the BEAD, so the last line of Today came to rest underneath it — measured as
# "Every figure here is read from your own rails." half-covered. Derive both numbers from the
# stylesheet rather than restating them, so changing the lift without the padding fails here.
_pad = re.search(r"padding-bottom:calc\((\d+)px \+ env\(safe-area-inset-bottom", _app.CSS)
_lift = re.search(r"\.tabs-in \.orb \.bead\{[^}]*?transform:translateY\(-(\d+)px\)", _app.CSS, re.S)
_barh = re.search(r"^\.tabs\{[^}]*?height:(\d+)px", _app.CSS, re.M | re.S)
ok("the page declares a bottom padding", _pad is not None)
ok("...the bead declares a lift", _lift is not None)
if _pad and _lift:
    _p, _l = int(_pad.group(1)), int(_lift.group(1))
    _bar = int(_barh.group(1)) if _barh else 59
    ok("...and the padding clears the bar AND the bead's overhang",
       _p >= _bar + _l, f"padding {_p}px vs bar {_bar}px + lift {_l}px")

# 2. TWO RAILS ASKING FOR THE SAME THING MUST NOT USE THE SAME WORDS. uptime and pagespeed both
# want a website address, and both said "tell Ownbox your website address and it will watch it for
# you" — byte-identical, stacked, under different labels, on a buyer's first screen.
_prompts = [v[1] for v in _rep.LABEL.values()]
_dupes = sorted({p for p in _prompts if _prompts.count(p) > 1})
ok("no two rails prompt with the same sentence", not _dupes, str(_dupes))
ok("...and every prompt says something", all(p.strip() for p in _prompts))


# ── EVERY SEGMENT ON THE APPEARANCE SWITCH HAS TO BE TRUE ─────────────────────────────────────
print("\ntest_the_appearance_switch_offers_only_states_it_has")
# It shipped three segments over two states. The third said "System", was the one SELECTED on an
# untouched box, and could not follow the system: `?to=system` merely deleted the cookie, and the
# prefers-color-scheme block had been removed (correctly) on 2026-09-16 to honour "white screens
# first and foremost". So it rendered white on a dark phone while claiming to follow it, and was
# byte-for-byte the same state as Light. Owner, 2026-09-18: drop it.
_sw = _c().get("/inbox/settings").get_data(as_text=True)
_segs = re.findall(r'<a class="([^"]*)" href="/inbox/theme\?to=([a-z]+)">([^<]+)</a>', _sw)
ok("the switch offers exactly the states this app has", len(_segs) == 2, str([x[2] for x in _segs]))
ok("...which are Light and Dark", [x[2] for x in _segs] == ["Light", "Dark"], str(_segs))
ok("...and nothing claims to follow the operating system",
   not any("system" in x[1].lower() or "System" in x[2] for x in _segs), str(_segs))
ok("an untouched box shows LIGHT selected, which is what it renders",
   [lbl for cls, to, lbl in _segs if "on" in cls] == ["Light"],
   str([(lbl, cls) for cls, to, lbl in _segs]))
# THE OLD URL STILL LANDS SOMEWHERE SAFE. A bookmark or a restored tab must not wedge the app.
_old = _c().get("/inbox/theme?to=system")
ok("a stale ?to=system clears the cookie rather than 500ing", _old.status_code == 303,
   str(_old.status_code))
ok("...and clears it, so the box lands on white",
   "Max-Age=0" in " ".join(v for k, v in _old.headers if k == "Set-Cookie"))


# ── the greeting reads the reader's clock ─────────────────────────────────────────────────────
print("\ntest_the_greeting_is_the_readers_time_of_day")
# OWNER, 2026-09-24: "that thing says good afternoon when it's morning here. It should be on the
# local time not UTC." A sold box runs on UTC; the reader's zone is `notify.buyer_timezone()`
# (the settings override, then the zone the browser gave at claim). Each case below picks a
# zone whose time of day DIFFERS from the box's own, so reading the box's clock fails it.
from zoneinfo import ZoneInfo as _Z                                        # noqa: E402
from core import notify as _notify, report as _report                      # noqa: E402
from marketing.customer_voice import app as _cv                            # noqa: E402

def _word(h):
    return "Good morning" if h < 12 else ("Good afternoon" if h < 17 else "Good evening")

_box_word = _word(datetime.now(_report.tz()).hour)
_real_bt = _notify.buyer_timezone
try:
    _seen = set()
    for _off in range(-12, 15):
        _zone = f"Etc/GMT{'+' if _off <= 0 else '-'}{abs(_off)}" if _off else "UTC"
        _w = _word(datetime.now(_Z(_zone)).hour)
        if _w == _box_word or _w in _seen:
            continue
        _seen.add(_w)
        _notify.buyer_timezone = (lambda z=_zone: z)
        _got = _cv._hello({})
        ok(f"a reader in {_zone} is told '{_w}', not the box's '{_box_word}'",
           f"<h1>{_w}.</h1>" in _got, _got[:80])
    ok("...checked against at least one other time of day", len(_seen) >= 1)
    # THE TIMES IN A THREAD READ THE SAME CLOCK: 17:00 UTC is 19:00 two hours east.
    _notify.buyer_timezone = lambda: "Etc/GMT-2"
    ok("a message's time is printed on the reader's clock",
       _cv._when("2026-09-24T17:00:00+00:00") == "Thu 24 Sep, 19:00",
       _cv._when("2026-09-24T17:00:00+00:00"))
    # A ZONE THE BOX CANNOT READ FALLS BACK TO THE BOX'S CLOCK, never a 500.
    _notify.buyer_timezone = lambda: "Not/AZone"
    ok("an unreadable zone still greets, on the box's clock",
       f"<h1>{_box_word}.</h1>" in _cv._hello({}))
finally:
    _notify.buyer_timezone = _real_bt


# ── CI runs this file ─────────────────────────────────────────────────────────────────────────
print("\ntest_ci_actually_runs_this_file")
# GUARDED: this suite ships with the machine, and a buyer's box is not a repository.
_here = pathlib.Path(__file__).resolve().parents[1]
if not (_here / ".github").is_dir():
    print("  --   not the repo — a buyer's box has no CI manifest to be named in")
else:
    ok("registered in the suite list",
       "test_today_says_good_morning" in (_here / ".github/workflows/tests.yml").read_text())


print(f"\n{'FAILED — ' + str(_failed) + ' failure(s)' if _failed else 'all checks passed'}")
sys.exit(1 if _failed else 0)
