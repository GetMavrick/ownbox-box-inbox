"""The inbox carries the box's menu — a hamburger on a phone, a rail on a desktop.

OWNER, 2026-09-18, looking at the rendered inbox: *"Why is there no hamburger menu."*

THE ANSWER WAS NOT A DESIGN ARGUMENT. `core.dash.home` has shipped a drawer since the rail
landed — an offscreen checkbox, a hamburger label, a scrim label, and `rail_html()` resolving the
sections from the registry — and every page drawn by `chrome()` wears it. This app draws its own
page, so it wore none of it: measured at 1280x820 on main, `/inbox/inbox` had no rail, no menu
button and exactly one navigation, a phone tab bar pinned to the foot of a desktop window, with a
224px filter column holding two chips above 600px of empty grey. The screen a buyer opens most was
the screen with the least navigation in it.

WHY THE FIX IS AN IMPORTED STRING AND NOT A CALL TO `chrome()`. This app's own `_shell` carries
the PWA manifest, the apple-touch icon, the `apple-mobile-web-app-*` pair, a theme-color that has
to follow the owner's light/dark choice, and the `data-theme` stamp that makes white the default.
`chrome()` carries none of it, and an installed phone app loses its identity the moment you swap
one for the other. So the drawer arrives as `RAIL_CSS` + `rail_html()`, and the PWA head stays
exactly where it was — which is the half of this change most likely to be broken later by someone
tidying up, and therefore the half this suite guards hardest.

AND WHY THERE IS STILL A TAB BAR. Below core's breakpoint the drawer holds the BOX (its sections)
and the bottom bar holds this SECTION (its three screens) — two scopes, the split Gmail uses, and
the bar is furniture the owner approved and uses daily. At and above the breakpoint the rail is
permanently on screen and the bar stands down, because two visible menus is two answers to "where
am I".
"""
import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB = pathlib.Path(tempfile.mkdtemp(prefix="box-menu-")) / "box.db"
# BEFORE ANY core IMPORT — core.config.Settings reads os.environ at class-body time.
os.environ["AIOS_DB_PATH"] = str(_DB)
os.environ.setdefault("DASH_TOKEN", "test-dash-pw")

from core import state                               # noqa: E402

state.init_db()

from core.dash import home as _home                  # noqa: E402
from marketing.customer_voice import app as _app     # noqa: E402

_SRC = pathlib.Path(_app.__file__).read_text()
_failed = 0

# THE CONTRACT THIS WHOLE SUITE RESTS ON, CHECKED FIRST AND OUT LOUD. Everything below reads
# `RAIL_CSS` off core, so without it the run tracebacks on an AttributeError halfway down and
# every later check silently never happens — which is how a suite quietly stops guarding the
# thing it was written for. Probed against main, where the constant does not exist: that is
# exactly what it did. Named instead, so the failure says which contract went missing.
if not hasattr(_home, "RAIL_CSS") or not hasattr(_home, "rail_html"):
    print("  FAIL core.dash.home no longer exports RAIL_CSS + rail_html — the inbox's menu "
          "is imported from there, so this suite cannot check anything without them")
    sys.exit(1)


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


def _page(path: str = "/inbox/inbox") -> str:
    return _c().get(path).get_data(as_text=True)


# ── the menu is on the page, and it is wired ──────────────────────────────────────────────────
print("test_the_menu_is_on_the_page_and_opens")
_b = _page()
ok("the offscreen checkbox is there", 'class="navtoggle" type="checkbox" id="navtoggle"' in _b)
ok("...and a hamburger label points at it",
   'class="ham" for="navtoggle"' in _b)
# TAPPING OUTSIDE HAS TO CLOSE IT. A drawer you can only shut with the button that opened it is a
# drawer people close by reloading the page.
ok("...and the scrim closes it, from the same checkbox",
   'class="scrim" for="navtoggle"' in _b)
ok("the button says what it is, for a screen reader", 'aria-label="Menu"' in _b)
# THE ORDER IS LOAD-BEARING, NOT COSMETIC. The whole drawer is `.navtoggle:checked~.lay .rail`
# and `~` only reaches LATER siblings, so a tidy-up that moves the checkbox below the layout
# silently stops it opening — with no error and no failing render.
_i_t, _i_s, _i_l = _b.find('class="navtoggle"'), _b.find('class="scrim"'), _b.find('class="lay"')
ok("the checkbox precedes the scrim and the layout it opens",
   -1 < _i_t < _i_s < _i_l, f"{_i_t} {_i_s} {_i_l}")


# ── it is CORE'S menu, not a second one ───────────────────────────────────────────────────────
print("\ntest_it_is_cores_menu_and_there_is_one_copy_of_it")
ok("the stylesheet is imported, not pasted", "CSS = CSS + _RAIL_CSS" in _SRC)
ok("...and core's copy is the one that arrives", _home.RAIL_CSS in _app.CSS)
# A SECOND HAND-WRITTEN RAIL IS THE FAILURE THIS GUARDS. Two menus that look almost the same is
# the drift `rail_html` was written to prevent, and it starts with one convenient local rule.
#
# SCOPING IS ALLOWED, REDEFINING IS NOT, and the distinction is the whole seam. This app says
# `.bar-in .ham{display:none}` and `.lay .main{padding:0}` — WHERE core's furniture sits in this
# page, which is this app's business and cannot live in a shared file. What it must never do is
# open a bare `.rail{` or `.ham{` block and start restating what the thing looks like. So this
# reads the selectors of the rules this app writes ITSELF, with core's string removed, and checks
# that none of them IS one of core's names.
_own = _app.CSS.replace(_home.RAIL_CSS, "")
_own_selectors = {s.strip() for chunk in _own.split("}") if "{" in chunk
                  for s in chunk.rsplit("{", 1)[0].split(",")}
for _sel in (".rail", ".ham", ".scrim", ".navtoggle", ".nav a", ".who", ".back", ".railfoot"):
    ok(f"this app redefines no {_sel} of its own",
       _sel not in _own_selectors,
       f"{_sel} is declared outside core's stylesheet")
ok("...while core's stylesheet is where they all come from",
   all(f"{s}{{" in _home.RAIL_CSS for s in (".rail", ".ham", ".scrim", ".navtoggle")))
ok("the hamburger glyph is core's, not a third drawing of three lines",
   "_HAM" in _SRC and _SRC.count("M4 7h16") == 0)
ok("the rows come from the renderer, not from a list in this file",
   "rail_html" in _SRC)


# ── the rail says where you are, and how to leave ─────────────────────────────────────────────
print("\ntest_the_rail_answers_where_am_i")
_rail = _b.split('<nav class="rail"', 1)[-1].split("</nav>", 1)[0]
ok("the rail is on the page", 'id="railnav"' in _b)
ok("...and it lists this machine's screens", all(w in _rail for w in ("Messages", "Today")))
ok("...and marks the one you are standing on",
   _rail.count('aria-current="page"') == 1, _rail.count('aria-current="page"'))
ok("...and the way out names where it goes", "Base Machine" in _rail)
# REGISTRY, NOT A LITERAL. This is the claim that makes the menu worth sharing: a machine that
# registers a section appears here on the day it ships, with no edit to the inbox.
from core import shell                               # noqa: E402
shell.register_section("a_later_machine", order=90, machine="later", title="Later",
                       href="/inbox/inbox", items=[])
ok("a section registered by another machine reaches this rail with no edit here",
   "Later" in _page().split('<nav class="rail"', 1)[-1].split("</nav>", 1)[0])


# ── two navigations, two names ────────────────────────────────────────────────────────────────
print("\ntest_the_two_menus_do_not_share_one_name")
# THEY BOTH SAID "Sections" THE FIRST TIME. A screen reader then offers "Sections navigation"
# twice with nothing to tell them apart, on the one screen that has two menus.
_names = re.findall(r'<nav [^>]*aria-label="([^"]+)"', _b)
ok("every navigation on the page is named", len(_names) >= 2, str(_names))
ok("...and no two share a name", len(set(_names)) == len(_names), str(_names))


# ── one navigation at a time ──────────────────────────────────────────────────────────────────
print("\ntest_one_navigation_at_a_time")
# The drawer and the bar swap at ONE number, so no width can show two menus or none. Core's
# drawer query is `max-width:820px`; this app's is the width immediately above it.
ok("the menu button is shown only where the rail is hidden",
   "@media (max-width:820px){ .bar-in .ham{display:flex} }" in _app.CSS)
ok("...and it is hidden by default", ".bar-in .ham{display:none" in _app.CSS)
ok("the tab bar stands down where the rail stands up",
   re.search(r"@media \(min-width:821px\)\{[^@]*nav\.tabs\{display:none\}", _app.CSS,
             re.S) is not None)
ok("core's drawer still breaks at the width this one is paired to",
   "@media (max-width:820px)" in _home.RAIL_CSS)
# AND THE BAR ITSELF NEVER HIDES — it carries the brand and the date at every width. Hiding the
# bar instead of the button is the mistake that first put `.topbar` in the shared stylesheet.
ok("this app's own bar is not hidden by the shared stylesheet", ".topbar" not in _home.RAIL_CSS)


# ── the shared rail is themeable, which is what makes it shareable ────────────────────────────
print("\ntest_the_shared_rail_carries_no_colour_of_its_own")
# IT SHIPPED WITH FIVE LITERALS, and `#333940` on a nav row was the one that mattered: the
# dashboard is light-only so it was invisible there, and this app is theme-stamped, where dark
# grey text on a dark rail is exactly the unreadable-app bug `test_inbox_design` refuses.
ok("no raw hex in the shared rail",
   not re.findall(r"#[0-9a-fA-F]{3,8}", _home.RAIL_CSS),
   str(re.findall(r"#[0-9a-fA-F]{3,8}", _home.RAIL_CSS)))
ok("...and no raw rgba either",
   not re.findall(r"rgba?\(", _home.RAIL_CSS))
# AND THIS APP ANSWERS EVERY NAME IT ASKS FOR, in BOTH themes, or the rail renders with
# unresolved variables and no test would see it.
_asked = set(re.findall(r"var\(--([a-z0-9-]+)\)", _home.RAIL_CSS))
_light = re.search(r":root\{(.*?)\n\}", _app.CSS, re.S)
_dark = re.search(r':root\[data-theme="dark"\]\{(.*?)\n\}', _app.CSS, re.S)
ok("both palette blocks were found to check", bool(_light) and bool(_dark))
# LIGHT IS THE BOX'S LOOK (docs/SCOPE_DESIGN_LANGUAGE.md step 6): the inbox links box.css, which
# answers --ink, --line and --scrim under the same names, so light's answer is box.css with this
# app's block on top. Dark must still answer every name itself: box.css is light-only.
_box_css = pathlib.Path(_home.__file__).resolve().parent / "static" / "box.css"
_box_root = re.search(r":root\s*\{([^}]*)\}", _box_css.read_text()) if _box_css.is_file() else None
_box_names = set(re.findall(r"--([a-z0-9-]+)\s*:", _box_root.group(1))) if _box_root else set()
for _name, _blk in (("light", _light), ("dark", _dark)):
    _have = set(re.findall(r"--([a-z0-9-]+)\s*:", _blk.group(1)))
    if _name == "light":
        _have |= _box_names
    ok(f"...and the {_name} palette answers every name the rail asks for",
       _asked <= _have, str(sorted(_asked - _have)))


# ── the PWA head survives, which is why this was not a call to chrome() ───────────────────────
print("\ntest_the_installed_app_keeps_its_identity")
for _what, _needle in (
        ("the manifest", '<link rel="manifest" href="/inbox/manifest.webmanifest">'),
        ("the home-screen icon", '<link rel="apple-touch-icon"'),
        ("apple's own capable flag", 'name="apple-mobile-web-app-capable"'),
        ("the status-bar style", 'name="apple-mobile-web-app-status-bar-style"'),
        ("a theme-color for the status bar", 'name="theme-color"'),
        ("the service worker registration", "<script>"),
):
    ok(f"{_what} is still in the head", _needle in _b)
# WHITE IS THE DEFAULT AND THE OS IS NOT CONSULTED — owner, 2026-09-16, white screens first.
# An unstamped page declares the LIGHT status-bar colour and no dark variant alongside it;
# declaring both would put a black band above a white app on an installed home-screen icon.
# (The first version of this line asserted the light colour was ABSENT, which is exactly
# backwards — it is the value that must be there. A check that passes for the wrong reason is
# worse than no check, and this one would have gone green the day the stamp broke.)
ok("the light status bar is the unstamped default",
   f'content="{_app._THEME_BG["light"]}"' in _b)
# READ OFF THE META TAGS, NOT OFF THE PAGE TEXT. This app inlines its whole stylesheet, so
# every CSS COMMENT is shipped page bytes — and one of those comments discusses
# `prefers-color-scheme` by name, which is what the first version of this line matched. It would
# have failed forever on prose while a real dark meta tag slipped past. Same trap cost this app
# a test fix on 2026-09-17; it is worth failing for once and writing down.
_tc = re.findall(r'<meta name="theme-color"[^>]*>', _b)
ok("exactly one status-bar colour is declared", len(_tc) == 1, str(_tc))
ok("...and it is offered unconditionally, not per OS preference",
   "media" not in _tc[0], str(_tc))
ok("...and the stamp is this app's own decision, in its own shell", "data-theme" in _SRC)
_dark_page = _c()
_dark_page.get("/inbox/settings?theme=dark")
ok("...and his choice still reaches <html>",
   'data-theme="dark"' in _dark_page.get("/inbox/inbox").get_data(as_text=True))


# ── a failure costs the menu, never the page ──────────────────────────────────────────────────
print("\ntest_a_rail_that_cannot_render_costs_the_menu_not_the_page")
# THE RAIL IS IN `_shell`, so it runs on every screen this app draws — including on a box
# mid-migration and a Space with no sections registered yet. An inbox that 500s because its
# navigation could not resolve is a worse product than one with no navigation.
_real = _home.rail_html
try:
    def _boom(*a, **k):
        raise RuntimeError("registry is on fire")
    _home.rail_html = _boom
    _r = _c().get("/inbox/inbox")
    ok("the page still answers 200", _r.status_code == 200, str(_r.status_code))
    _body = _r.get_data(as_text=True)
    ok("...and simply shows no rail", 'id="railnav"' not in _body)
    ok("...while the conversations are still there", "conv" in _body)
    ok("...and the tab bar still is too", 'class="tabs"' in _body)
finally:
    _home.rail_html = _real


print(f"\n{'FAILED — ' + str(_failed) + ' failure(s)' if _failed else 'all checks passed'}")
sys.exit(1 if _failed else 0)
