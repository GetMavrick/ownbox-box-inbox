"""The inbox takes its own place in the box's menu — and the rail stops lighting two rows.

Owner, 2026-09-17: *"Each machine will plug in different menu options"*, *"the Customer machine
plug-ins will add dashboard areas"*, and on the inbox itself: *"When people click inbox and then
messages, it launches into the exact inbox that you designed in that mock up… they can go back into
the dashboard so there should be a back arrow with a dashboard label."*

This is the first machine ever to call `shell.register_section`, so it is where the plug-in model
becomes behaviour instead of a design. Registering it exposed TWO defects in core that nothing had
reached yet, both found by RENDERING THE RAIL AND READING THE ROWS BACK — neither was visible in a
diff, and one of them I introduced myself while fixing the other:

  1. TWO ROWS LIT AT ONCE. `crumb()` picked the longest matching item (OSDev1's review catch on
     #1317); `is_current()` was still a bare prefix test. The inbox's index is `/inbox/` and
     Messages is `/inbox/inbox`, so on the Messages page `Today` matched as well and both rows drew
     `aria-current="page"` — a menu telling the person it does not know where they are. Every
     section with an index page has this shape.
  2. NOTHING LIT AT ALL, at level 1. The first fix copied `crumb()`'s `level != 2` guard into the
     shared helper, so the Dashboard row lost its highlight while you stood on the dashboard. The
     guard belongs to the breadcrumb (a one-word trail is not a trail), not to "which row am I on".

Run: python tests/test_the_inbox_joins_the_rail.py
"""
import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB = pathlib.Path(tempfile.mkdtemp(prefix="inbox-rail-")) / "box.db"
os.environ["AIOS_DB_PATH"] = str(_DB)                      # BEFORE any core import
os.environ.setdefault("DASH_TOKEN", "test-dash-pw")

from core import state                                      # noqa: E402

state.init_db()

from core import shell                                      # noqa: E402
from core.dash import home                                  # noqa: E402
from marketing.customer_voice import app as cv              # noqa: E402,F401

# THE MACHINE'S OWN REGISTRATION, SNAPSHOTTED AT IMPORT — before any fixture has run.
#
# `_restore()` below OWNS the registry and replaces what the import put there, which is the right
# thing for every test that builds a box shape. It also means that by the time a later test asks
# what the machine actually registered, the answer is gone: the icon test read the fixture's rows,
# found no icons, and failed on its own scaffolding rather than on the code. Caught by running it.
#
# `register_section` is idempotent for the same machine, so re-importing cannot bring it back
# either. The only moment this is readable is now.
_AS_REGISTERED = shell._SECTIONS.get("inbox")

_failed = 0


def ok(what: str, cond: bool, got: str = "") -> None:
    global _failed
    if cond:
        print(f"  ok   {what}")
    else:
        _failed += 1
        print(f"  FAIL {what}" + (f"  — {got}" if got else ""))


def _restore() -> None:
    """The box this suite is about: core's Dashboard plus the inbox this machine registered.

    THE FIXTURE OWNS THE REGISTRY rather than appending to it. `shell._SECTIONS` is module-global,
    and a suite that inherits whatever the previous test left behind is asserting a box it never
    built — the same leak that made `tests/test_the_dashboard_reads.py` pass only in alphabetical
    order. Re-importing the machine cannot restore it either, because `register_section` is
    idempotent by design, so the registration is repeated here on purpose.
    """
    shell._reset_for_tests()
    shell.register_section("dashboard", order=0, machine="core", title="Dashboard",
                           href="/dashboard", home=True)
    shell.register_section(
        "inbox", order=10, machine="customer_voice", title="Inbox", href="/inbox/",
        items=[{"key": "messages", "label": "Messages", "href": "/inbox/inbox"},
               {"key": "today", "label": "Today", "href": "/inbox/"},
               {"key": "settings", "label": "Settings", "href": "/inbox/settings"}])


_ROW = re.compile(r'<a href="([^"]+)"[^>]*?(aria-current="page")?>.*?<span class="lbl">([^<]*)</span>')


def _rendered(path: str):
    """The rail as a person would read it: (labels, the ones marked current). RENDERED, not asked.

    Every defect in this file's docstring was invisible to `shell.rail()` and obvious in the HTML,
    because the bug was never in what the rail returned — it was in what `rail_html` did with it.
    """
    _restore()
    html_ = home.rail_html(path, who="Ownbox")
    rows = _ROW.findall(html_)
    return [lbl for _h, _c, lbl in rows], [lbl for _h, cur, lbl in rows if cur]


# ── the machine plugs in ────────────────────────────────────────────────────────────────────────

def test_the_box_grows_an_inbox_row_because_it_carries_this_machine():
    print("test_the_box_grows_an_inbox_row_because_it_carries_this_machine")
    _restore()
    got = {s.key: s for s in shell.sections()}
    ok("the inbox has a section", "inbox" in got, str(sorted(got)))
    ok("...owned by the machine, not by core", got["inbox"].machine == "customer_voice",
       got["inbox"].machine)
    ok("...and core still owns the dashboard", got["dashboard"].machine == "core")
    ok("core's own home comes first in the rail",
       [s.key for s in shell.sections()][0] == "dashboard",
       str([s.key for s in shell.sections()]))


def test_core_names_no_machine_to_make_that_happen():
    print("test_core_names_no_machine_to_make_that_happen")
    # THE WHOLE POINT OF A PLUG-IN. If core had to learn the word `customer_voice` for the row to
    # appear, every future machine would need core edited — which is the arrangement the owner
    # ruled out ("the lead machine shouldn't own core features", 2026-09-17) from the other side.
    root = pathlib.Path(__file__).resolve().parents[1]
    for rel in ("core/shell.py", "core/dash/home.py"):
        src = (root / rel).read_text()
        # `/inbox/` appears in core/dash/__init__.py's landing list, which is a separate question
        # and OSDev1's; these two files are the menu, and the menu names nobody.
        ok(f"{rel} names no machine",
           not any(m in src for m in ("customer_voice", "lead_machine", "content_machine")), rel)


def test_two_taps_to_messages_which_is_what_he_asked_for():
    print("test_two_taps_to_messages_which_is_what_he_asked_for")
    # *"When people click inbox and then messages"* — so the section's own href is NOT the inbox.
    _restore()
    inbox = next(s for s in shell.sections() if s.key == "inbox")
    ok("clicking Inbox does not land straight in Messages", inbox.href != "/inbox/inbox",
       inbox.href)
    ok("...and Messages is a row beneath it",
       "/inbox/inbox" in [i.href for i in inbox.items], str([i.href for i in inbox.items]))
    # AND NO SHIPPED URL MOVED. The app still serves exactly the paths it served before; the rail
    # is a second way to reach them, not a renaming of them.
    ok("every row is a path the app already served",
       all(i.href in ("/inbox/", "/inbox/inbox", "/inbox/settings") for i in inbox.items),
       str([i.href for i in inbox.items]))


# ── the two defects, at every path that can reach them ──────────────────────────────────────────

def test_exactly_one_row_is_ever_lit():
    print("test_exactly_one_row_is_ever_lit")
    # THE ONE THAT REACHED THE SCREEN. Asserted at EVERY path, not at the one that broke, because
    # the defect was a rule ("longest match") applied in one place and not its twin.
    for path in ("/dashboard", "/inbox/", "/inbox/inbox", "/inbox/inbox/zc_abc123",
                 "/inbox/settings"):
        _rows, lit = _rendered(path)
        ok(f"{path} lights exactly one row", len(lit) == 1, f"lit {lit}")


def test_the_row_that_is_lit_is_the_page_you_are_on():
    print("test_the_row_that_is_lit_is_the_page_you_are_on")
    # ONE ROW IS NOT ENOUGH — it has to be the RIGHT one. A longest-match bug that picked the
    # shortest would also light exactly one, and it would be the wrong one every time.
    for path, want in (("/dashboard", "Dashboard"),
                       ("/inbox/", "Today"),
                       ("/inbox/inbox", "Messages"),
                       ("/inbox/inbox/zc_abc123", "Messages"),
                       ("/inbox/settings", "Settings")):
        _rows, lit = _rendered(path)
        ok(f"{path} lights {want}", lit == [want], f"lit {lit}")


def test_the_breadcrumb_and_the_lit_row_never_disagree():
    print("test_the_breadcrumb_and_the_lit_row_never_disagree")
    # THEY ARE ONE QUESTION AND THEY USED TO HAVE TWO ANSWERS. The crumb said Messages while the
    # rail lit Today, on the same screen, at the same moment. Tying them together here means the
    # next person who changes one of them cannot leave the other behind.
    for path in ("/inbox/", "/inbox/inbox", "/inbox/inbox/zc_abc123", "/inbox/settings"):
        _restore()
        parts = shell.crumb(path)
        _rows, lit = _rendered(path)
        ok(f"{path}: crumb {parts[-1]!r} is the row that is lit",
           bool(parts) and lit == [parts[-1]], f"crumb {parts} vs lit {lit}")


def test_a_level_one_rail_still_marks_where_you_are():
    print("test_a_level_one_rail_still_marks_where_you_are")
    # THE REGRESSION I WROTE WHILE FIXING THE OTHER ONE, kept as its own test because a fix that
    # trades one silent wrong answer for another is not a fix. `crumb()`'s `level != 2` guard is
    # about the TRAIL, not about which row you are on.
    _rows, lit = _rendered("/dashboard")
    ok("standing on the dashboard, the Dashboard row is marked", lit == ["Dashboard"], f"lit {lit}")
    ok("...and the level-1 rail shows the machine's section too", "Inbox" in _rows, str(_rows))


# ── the back arrow ──────────────────────────────────────────────────────────────────────────────

def test_the_back_arrow_says_where_it_goes():
    print("test_the_back_arrow_says_where_it_goes")
    # Owner, 2026-09-17: *"a back arrow with a dashboard label."* It read "Inbox" — the room being
    # left — while carrying the person to /dashboard.
    _restore()
    for path in ("/inbox/", "/inbox/inbox", "/inbox/settings"):
        got = shell.rail(path)
        ok(f"{path}: back goes to the box's home", got.back == "/dashboard", got.back)
        ok(f"{path}: ...and says so", got.back_label == "Dashboard", got.back_label)
    # THE ELEMENT CHANGED THREE TIMES; THE ASSERTION DID NOT. It was `<a class="back">` above the
    # list, then a Dashboard row inside it, and now — owner, 2026-09-23, of the reference he sent:
    # *"the first thing at the top you have a back arrow/chevron that functions as the back
    # button. And then settings is in bold showing that we are on the settings. I think we need to
    # do away with the back arrow and the dashboard that we have."* So the way out is a chevron
    # beside the section's own title. What this test is for still has its teeth: the control's
    # HREF and the words that name it must be the pair `shell` resolved, because a menu whose
    # words and destination come apart is the defect the 2026-09-17 ruling was raised about.
    html_ = home.rail_html("/inbox/inbox", who="Ownbox")
    arrow = re.search(r'<a class="home" href="([^"]+)" aria-label="Back to ([^"]*)">', html_)
    ok("the rendered way home carries both", bool(arrow) and arrow.group(1) == "/dashboard"
       and arrow.group(2) == "Dashboard",
       arrow.groups() if arrow else "no back control rendered")
    ok("...and there is exactly one of it", html_.count('class="home"') == 1,
       str(html_.count('class="home"')))
    ok("...drawn as the back chevron, not a row with the dashboard's icon",
       home._BACK_ARROW in html_ and home._HOME_ICON not in html_)
    # WHERE YOU ARE, IN BOLD, beside the way out — the other half of the reference.
    title = re.search(r'<div class="subhead"><a class="home"[^>]*>.*?</a><b>([^<]*)</b></div>',
                      html_, re.S)
    ok("...beside the section's own title, in bold",
       bool(title) and title.group(1) == shell.rail("/inbox/inbox").title,
       title.group(1) if title else "no section title beside the back chevron")
    ok("...and the old Dashboard row is gone from the list",
       '<span class="lbl">Dashboard</span>' not in html_)


def test_a_row_with_a_sub_menu_says_so():
    print("test_a_row_with_a_sub_menu_says_so")
    # Owner, 2026-09-23: *"Notice the forward chevron to show when a menu item has sub menu
    # items."* Read off the rendered level-1 rail, row by row, not off the registry.
    _restore()
    html_ = home.rail_html("/dashboard", who="Ownbox")
    rows = re.findall(r'<a href="([^"]+)"[^>]*>(.*?)</a>', html_, re.S)
    by = {h: body for h, body in rows}
    for sec in shell.sections():
        body = by.get(sec.href)
        if body is None:
            continue
        has = bool(sec.items) and not sec.home
        ok(f"{sec.title}: {'carries' if has else 'does not carry'} the forward chevron",
           ('class="fwd"' in body) == has, body[-200:])


def test_the_label_cannot_come_apart_from_the_link():
    print("test_the_label_cannot_come_apart_from_the_link")
    # TWO RESOLUTIONS OF THE SAME QUESTION IS HOW A MENU STARTS LYING. `home_href()` and
    # `home_title()` walk the same chain in the same order, so renaming the home section renames
    # the arrow, and moving it moves the arrow — asserted by doing exactly that.
    shell._reset_for_tests()
    shell.register_section("overview", order=0, machine="core", title="Overview",
                           href="/overview", home=True)
    shell.register_section("inbox", order=10, machine="customer_voice", title="Inbox",
                           href="/inbox/",
                           items=[{"key": "messages", "label": "Messages", "href": "/inbox/inbox"}])
    got = shell.rail("/inbox/inbox")
    ok("the arrow follows the home section's new path", got.back == "/overview", got.back)
    ok("...and its new word", got.back_label == "Overview", got.back_label)
    _restore()


def test_the_icons_are_the_tab_bar_s_own():
    print("test_the_icons_are_the_tab_bar_s_own")
    # ONE GLYPH PER DESTINATION. Two menus drawing the same page with two different marks is drift
    # that nobody notices until a buyer does; a copied `d` string is how it starts.
    tabs = {href: d for href, _label, d in cv._TABS}
    live = _AS_REGISTERED                 # the machine's own, snapshotted before any fixture ran
    ok("the machine registered its section at import", live is not None)
    if live is None:
        return
    ok("...and it is the machine that owns it", live.machine == "customer_voice", str(live.machine))
    by_href = {i.href: i.icon for i in live.items}
    for href in ("/inbox/", "/inbox/inbox", "/inbox/settings"):
        ok(f"{href} draws the same mark in both menus", by_href.get(href) == tabs.get(href), href)
    ok("and the section itself wears the tray", live.icon == tabs["/inbox/inbox"])
    _restore()
    ok("the fixture rebuilt the box", "inbox" in {s.key for s in shell.sections()})


def test_the_suite_is_named_in_ci():
    print("test_the_suite_is_named_in_ci")
    here = pathlib.Path(__file__).resolve().parents[1]
    if not (here / ".github").is_dir():
        print("  --   not the repo — a buyer's box has no CI manifest to be named in")
        return
    wf = (here / ".github/workflows/tests.yml").read_text()
    ok("this suite runs in CI", "test_the_inbox_joins_the_rail \\" in wf)


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print()
            fn()
    print("\n" + ("FAILED" if _failed else "PASS") + f" — {_failed} failure(s)")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
