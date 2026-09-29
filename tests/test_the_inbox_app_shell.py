"""The inbox app's shell: its own typefaces, the orb in the tab bar, the Appearance switch, and
the reader's clock.

THIS FILE WAS test_today_says_good_morning. The Today screen and its greeting went on 2026-09-29
(owner, IA decision D1 in docs/SCOPE_APP_IA.md: the day's summary is the Morning Review's, "right
now" is the Base Machine's, and the inbox opens on its messages). What that suite held about the
greeting went with it; what it held about everything else still ships, and is held here, on the
messages screen. Two owner calls from 2026-09-18 still stand:
  1. *"A all the way."*  — the typefaces stay served by this box, never by Google.
  2. *"Ship the orb in place and make it look really cool like glass, but don't make it do
     anything yet."* — so it is decoration, never a control.

THE BUG THE OLD SUITE FOUND IS STILL HELD: the report reads a Space and must read the one it is
asked for, not "the first Space", or one tenant's figures reach another's review.
"""
import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB = pathlib.Path(tempfile.mkdtemp(prefix="inbox-shell-")) / "box.db"
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


# ── the messages screen, as the rest of this file reads it ─────────────────────────────────────
_listening(True)
_wipe()
_talk("g1", "Dana Whitfield", mins=3)
_c_ = _c()
_b = _c_.get("/inbox/inbox").get_data(as_text=True)
ok("the messages screen answers", "<h1" in _b)


# ── the Space bug the old Today screen found ──────────────────────────────────────────────────
print("\ntest_the_report_answers_for_the_space_the_screen_is_showing")
# THE FAILURE: `_space_name()` is "the first Space" and `_space()` is "this request's Space".
# They agree on a sold box and not on a multi-Space one, so Today rendered one tenant's figures
# above another tenant's list. Caught because the greeting came back with no sentence at all.
import inspect                                      # noqa: E402
ok("report() takes a space", "space" in inspect.signature(_rep.report).parameters)
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
# THE TABS ARE THE MENU'S ROWS (owner, 2026-09-29, IA D1 and D5): Messages · Replies · Search ·
# Settings. No "Today" — the day's summary is the Morning Review's — and no "Inbox" beside a menu
# that calls the same screen Messages.
_labels = re.findall(r'<a class="tab[^"]*" href="[^"]+"[^>]*>.*?<span>([^<]+)</span></a>', _bar, re.S)
ok("the tabs are Messages, Replies, Search and Settings",
   _labels == ["Messages", "Replies", "Search", "Settings"], str(_labels))
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


print("\ntest_the_screen_and_the_report_agree_on_the_signature")
# THE OLD TODAY SCREEN called report(day, space=…) INSIDE A GUARD that turns any exception into "the report
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


# ── EVERY SEGMENT ON THE APPEARANCE SWITCH HAS TO BE TRUE ──────────────────────────────────
print("\ntest_the_appearance_switch_offers_only_states_it_has")
# It shipped three segments over two states. The third said "System", was the one SELECTED on an
# untouched box, and could not follow the system: `?to=system` merely deleted the cookie, and the
# prefers-color-scheme block had been removed (correctly) on 2026-09-16 to honour "white screens
# first and foremost". So it rendered white on a dark phone while claiming to follow it, and was
# byte-for-byte the same state as Light. Owner, 2026-09-18: drop it.
# THE THIRD SEGMENT IS BACK AND IS NOW TRUE (owner, 2026-09-27: "system default"; PR #1659).
# core/dash/theme.py stamps the page before paint, and for Automatic writes the script that reads
# the device. So the rule this section holds is unchanged — a segment may only offer a state the
# app can render — and it is now proved for Automatic by the script actually arriving.
from core.dash import theme as _core_theme  # noqa: E402
_sw = _c().get("/inbox/settings").get_data(as_text=True)
_form = re.search(r'<form class="ui-seg"[^>]*action="' + re.escape(_core_theme.ROUTE) + r'".*?</form>', _sw, re.S)
_segs = re.findall(r'value="([a-z]+)" aria-pressed="(true|false)">([^<]+)</button>',
                   _form.group(0)) if _form else []
ok("the switch offers exactly the states this app has", len(_segs) == 3, str([x[2] for x in _segs]))
ok("...which are Light, Dark and Automatic", [x[2] for x in _segs] == ["Light", "Dark", "Automatic"],
   str(_segs))
ok("an untouched box shows LIGHT selected, which is what it renders",
   [lbl for v, pressed, lbl in _segs if pressed == "true"] == ["Light"], str(_segs))
_auto_c = _c()
_auto_c.post(_core_theme.ROUTE, data={"theme": "system", "next": "/inbox/settings"})
_auto_head = _auto_c.get("/inbox/settings").get_data(as_text=True).split("</head>")[0]
ok("...and Automatic is TRUE: choosing it sends the script that follows the device, before paint",
   "matchMedia('(prefers-color-scheme: dark)')" in _auto_head, "no device script in <head>")
_auto_c.post(_core_theme.ROUTE, data={"theme": "light"})   # leave the person as they were found
# THE OLD URL STILL LANDS SOMEWHERE SAFE. A bookmark or a restored tab must not wedge the app.
_old = _c().get("/inbox/theme?to=system")
ok("a stale ?to=system clears the cookie rather than 500ing", _old.status_code == 303,
   str(_old.status_code))
ok("...and clears it, so the box lands on white",
   "Max-Age=0" in " ".join(v for k, v in _old.headers if k == "Set-Cookie"))


# ── the reader's clock ──────────────────────────────────────────────────────────────────────────
print("\ntest_times_are_on_the_readers_clock")
# OWNER, 2026-09-24: "It should be on the local time not UTC." A sold box runs on UTC; the reader's
# zone is `notify.buyer_timezone()` (the settings override, then the zone the browser gave at claim).
from core import notify as _notify                                         # noqa: E402
from marketing.customer_voice import app as _cv                            # noqa: E402
_real_bt = _notify.buyer_timezone
try:
    # THE TIMES IN A THREAD READ THE READER'S CLOCK: 17:00 UTC is 19:00 two hours east.
    _notify.buyer_timezone = lambda: "Etc/GMT-2"
    ok("a message's time is printed on the reader's clock",
       _cv._when("2026-09-24T17:00:00+00:00") == "Thu 24 Sep, 19:00",
       _cv._when("2026-09-24T17:00:00+00:00"))
    # A ZONE THE BOX CANNOT READ FALLS BACK TO THE BOX'S CLOCK, never a 500.
    _notify.buyer_timezone = lambda: "Not/AZone"
    ok("an unreadable zone still prints a time", bool(_cv._when("2026-09-24T17:00:00+00:00")))
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
       "test_the_inbox_app_shell" in (_here / ".github/workflows/tests.yml").read_text())


print(f"\n{'FAILED — ' + str(_failed) + ' failure(s)' if _failed else 'all checks passed'}")
sys.exit(1 if _failed else 0)
