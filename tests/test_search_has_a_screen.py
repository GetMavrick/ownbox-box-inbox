"""Search has a screen of its own, and it is a door rather than a second room.

WHY IT EXISTS. `search_conversations` has been finished for weeks and the field that drives it
lives on the LIST — so "search" was a control you could only reach by first loading fifty
conversations you did not want, and on a phone the field then sits at the top of the screen,
which is the one place a thumb cannot go. It had no address.

WHAT THIS SUITE IS REALLY GUARDING. Not search: the store suite and
`test_inbox_search_screen.py` already own that, and `/inbox/inbox?q=` owns every result and all
five of the ways this box can legitimately have nothing to show. What can break HERE is the
seam — a second search implementation growing on this screen, the form drifting off the one
route that knows how to answer it, and the two menus disagreeing about where you are standing.

THE RAIL BUG IS IN HERE ON PURPOSE. `shell.is_current` falls back to a section's own href for a
path no registered item claims, so the first version of this screen lit *Today* in the desktop
rail while the bottom bar lit Search. A menu that tells you that you are somewhere you are not
is worse than a menu with a gap in it, and nothing else in the battery would have caught it —
it was found by rendering the four screens and reading back which row came marked.
"""
import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB = pathlib.Path(tempfile.mkdtemp(prefix="search-screen-")) / "box.db"
# BEFORE ANY core IMPORT — core.config.Settings reads os.environ at class-body time.
os.environ["AIOS_DB_PATH"] = str(_DB)
os.environ.setdefault("DASH_TOKEN", "test-dash-pw")

from datetime import datetime, timedelta, timezone   # noqa: E402

from core import state                               # noqa: E402

state.init_db()

from marketing.customer_voice import app as _app     # noqa: E402
from marketing.customer_voice.inbox import store     # noqa: E402

_SRC = pathlib.Path(_app.__file__).read_text()
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


def _c():
    from core.config import settings
    from core.dispatch import app
    c = app.test_client()
    c.post("/dash/login", data={"token": settings.dash_token})
    return c


def _talk(zcid: str, who: str, platform: str, text: str) -> None:
    store.upsert_conversation(space=SPACE, zcid=zcid, platform=platform, participant=who,
                              last_inbound_at=(NOW - timedelta(minutes=5)).isoformat())
    store.record_message(space=SPACE, zcid=zcid, zmid=None, direction="in",
                         sent_by="them", body=text)


def _wipe() -> None:
    with state.connect() as c:
        c.execute("DELETE FROM inbox_messages")
        c.execute("DELETE FROM inbox_conversations")


# ── it is a real address, and it is shut to a stranger ────────────────────────────────────────
print("test_the_screen_exists_and_is_shut_to_a_stranger")
from core.dispatch import app as _flask                # noqa: E402
_rules = {str(r.rule) for r in _flask.url_map.iter_rules()}
ok("/inbox/search is a route this box serves", "/inbox/search" in _rules)
_anon = _flask.test_client()
_r = _anon.get("/inbox/search")
ok("...and it is not open to the world", _r.status_code in (301, 302, 303, 401, 403),
   str(_r.status_code))

_c_ = _c()
_r = _c_.get("/inbox/search")
ok("the owner gets a page", _r.status_code == 200, str(_r.status_code))
_b = _r.get_data(as_text=True)


# ── a door, not a second room ─────────────────────────────────────────────────────────────────
print("\ntest_it_is_a_door_and_not_a_second_search")
_form = re.search(r'<form class="find" method="get" action="([^"]+)"', _b)
ok("the field is on it", _form is not None)
ok("...and it submits to the ONE route that knows how to answer",
   _form and _form.group(1) == "/inbox/inbox", _form.group(1) if _form else "no form")
# THE FAILURE THIS GUARDS: a helpful second implementation growing here, so that "no matches"
# means two different things depending on which screen you were standing on when you typed.
ok("it renders no conversation rows of its own",
   '<a class="conv' not in _b, "conversation rows are being drawn on the door")
# A CALL, NOT THE WORD. The first version of this line read the route's whole text for
# "search_conversations" and found it in the route's own DOCSTRING, which explains what search
# reads — so the check failed on prose while a real second reader would have slipped past a
# differently worded comment. It looks for the call.
_route = _SRC.split("def r_search(", 1)[-1]
ok("...and calls no reader itself",
   "search_conversations(" not in _route and "list_conversations(" not in _route,
   "the door is querying the store")
ok("it says what search actually reads, which no screen ever has",
   "not just their names" in _b)


# ── both menus agree about where you are standing ─────────────────────────────────────────────
print("\ntest_both_menus_agree_where_you_are")
def _marks(body: str) -> tuple:
    bar = body.split('<nav class="tabs"', 1)[-1]
    rail = (body.split('<nav class="rail"', 1)[-1].split("</nav>", 1)[0]
            if '<nav class="rail"' in body else "")
    return (bar.count('aria-current'), re.findall(r'<a href="([^"]*)"[^>]*aria-current', rail))

for _path in ("/inbox/search", "/inbox/inbox", "/inbox/", "/inbox/settings"):
    _body = _c_.get(_path).get_data(as_text=True)
    _barn, _rail = _marks(_body)
    ok(f"{_path} lights exactly one tab", _barn == 1, str(_barn))
    # THE REGRESSION. This read ['/inbox/'] for /inbox/search before Search was registered as an
    # item of the section — the rail said Today while the bar said Search.
    ok(f"...and the rail marks {_path} itself, not the section's index",
       _rail == [_path], f"rail marked {_rail}")

ok("Search is in the bar", ">Search<" in _b)
ok("...from one glyph, not a second magnifier",
   _SRC.count('"/inbox/search", "Search"') == 1)


# ── the filter row obeys the same rule it obeys everywhere ────────────────────────────────────
print("\ntest_the_channel_row_is_a_choice_or_it_is_absent")
_wipe()
_talk("s1", "Dana Whitfield", "instagram", "kitchen sink backing up")
ok("one channel draws no chip row — a filter with one option cannot change anything",
   'class="chips"' not in _c_.get("/inbox/search").get_data(as_text=True))
_talk("s2", "Marcus Bell", "messenger", "price to replace a hot water system")
ok("...two channels draw one",
   'class="chips"' in _c_.get("/inbox/search").get_data(as_text=True))


# ── typing in it lands on the list, with the query bound ──────────────────────────────────────
print("\ntest_typing_in_it_reaches_the_answer")
_hit = _c_.get("/inbox/inbox?q=hot+water").get_data(as_text=True)
ok("the query reaches the list and matches a message BODY", "Marcus Bell" in _hit)
ok("...and does not drag in the one it should not", "Dana Whitfield" not in _hit)
# A WILDCARD IS A CHARACTER, NOT A COMMAND — asserted from this side too, because a future
# caller could defeat it here by pre-formatting the query before it reaches the store.
_pct = _c_.get("/inbox/inbox?q=%25").get_data(as_text=True)
ok("a bare % asks for a percent sign, not for every conversation in the box",
   "Marcus Bell" not in _pct and "Dana Whitfield" not in _pct)


# ── the owner's word for the second pill ──────────────────────────────────────────────────────
print("\ntest_the_second_pill_says_what_he_calls_it")
# OWNER, 2026-09-18, ASKED DIRECTLY: "Leads". The filter did not change — still `from_ad`, still
# the poller's ad id — so this is one word a buyer reads, and it is asserted where it renders.
ok("the app ships his word", 'pill("Leads"' in _SRC)
# THE CALL, NOT THE WORD — again. The route's docstring deliberately RECORDS the case for
# "From an ad" and that it lost, because a decision with its reasoning kept beside it is worth
# more than a bare string. Banning the phrase from the file would have deleted that history to
# satisfy a test, which is the tail wagging the dog.
ok("...and no longer ships mine", 'pill("From an ad"' not in _SRC)
_ad = _c_.get("/inbox/inbox?from_ad=1").get_data(as_text=True)
ok("...and a buyer never reads it", ">From an ad<" not in _ad)


# ── CI runs this file ─────────────────────────────────────────────────────────────────────────
print("\ntest_ci_actually_runs_this_file")
# GUARDED, BECAUSE THIS SUITE SHIPS. It imports the inbox, so it travels to every Customer Voice
# box — and a box is not a repository: there is no `.github/` on it, so a bare read raises
# FileNotFoundError in a paying buyer's own run. `test_suite_integrity` caught exactly this on
# the first pass, which is the whole reason that guard exists. Narrow on purpose: no `.github`
# at all means this is not the repo.
_here = pathlib.Path(__file__).resolve().parents[1]
if not (_here / ".github").is_dir():
    print("  --   not the repo — a buyer's box has no CI manifest to be named in")
else:
    _wf = (_here / ".github/workflows/tests.yml").read_text()
    ok("registered in the suite list", "test_search_has_a_screen" in _wf)


print(f"\n{'FAILED — ' + str(_failed) + ' failure(s)' if _failed else 'all checks passed'}")
sys.exit(1 if _failed else 0)
