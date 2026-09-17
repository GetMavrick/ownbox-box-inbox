"""The rail the owner described: two levels, replaced in place, one way back.

Owner, 2026-09-17 (in OSDev1's session, quoted on the wall): *"They should go to /dashboard and
then they should see some helpful information and can click settings to set things up. That should
be a two level navigation so when people click settings, the choices in the left navigation change
to that sub menu and then there's a back arrow at the top."* He then asked for the same move at the
top of the inbox — back to the dashboard, with the inbox's own choices where the main nav was.

WHAT THIS SUITE IS REALLY GUARDING is that the rail is a function of the PATH. The tempting
signature takes a `level=` or an `active=` from the caller, and then every screen in the box is
free to be wrong about where it is — a back arrow on the dashboard, a highlighted row on a page
you are not on, and nothing to catch it, because the caller is not lying, it is just mistaken. So
the assertions below never tell `rail()` what it is looking at.

AND THAT CORE NAMES NO MACHINE. `core/` ships whole into every box; a rail that spelled out its
own sections would put one product's menu into every other product. Sections are registered, the
way tables, jobs, reports and set-up steps already are.

Run: python tests/test_the_two_level_rail.py
"""
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("AIOS_DB_PATH", "/tmp/rail-not-used.db")

from core import shell                                      # noqa: E402

_failed = 0


def ok(what: str, cond: bool, got: str = "") -> None:
    global _failed
    if cond:
        print(f"  ok   {what}")
    else:
        _failed += 1
        print(f"  FAIL {what}" + (f"  — {got}" if got else ""))


def raises(what: str, fn, *a, **kw) -> None:
    """A registration that SHOULD be refused. Anything other than ValueError is a failure too —
    a TypeError here means the guard never ran and the bad section would have reached a screen."""
    try:
        fn(*a, **kw)
    except ValueError:
        ok(what, True)
    except Exception as e:                                   # noqa: BLE001
        ok(what, False, f"raised {type(e).__name__}, expected ValueError")
    else:
        ok(what, False, "nothing raised")


SETTINGS_ITEMS = [
    {"key": "profile", "label": "Profile", "href": "/settings/profile"},
    {"key": "usage", "label": "Usage", "href": "/settings/usage", "tone": "away"},
    {"key": "notifications", "label": "Notifications", "href": "/settings/notifications"},
    {"key": "danger", "label": "Danger Zone", "href": "/settings/danger", "tone": "danger"},
]


def _box(*, inbox_items=True) -> None:
    """A box the way one is actually assembled: core registers home, machines register the rest."""
    shell._reset_for_tests()
    shell.register_section("dashboard", order=0, machine="core",
                           title="Dashboard", href="/dashboard", home=True)
    shell.register_section("inbox", order=10, machine="a_machine", title="Inbox", href="/inbox",
                           items=([{"key": "all", "label": "All", "href": "/inbox"},
                                   {"key": "unanswered", "label": "Unanswered",
                                    "href": "/inbox/unanswered"}] if inbox_items else []))
    shell.register_section("settings", order=20, machine="core",
                           title="Settings", href="/settings", items=SETTINGS_ITEMS)


# ── the owner's sentence, assertion by assertion ────────────────────────────────────────────────

def test_the_dashboard_shows_the_main_choices_and_no_way_back():
    """"They should go to /dashboard" — level 1, and a back arrow here would be the first thing a
    buyer taps and the first control that could not succeed."""
    _box()
    r = shell.rail("/dashboard")
    ok("the dashboard is level 1", r.level == 1)
    ok("...showing every section", [i.label for i in r.items] == ["Dashboard", "Inbox", "Settings"],
       str([i.label for i in r.items]))
    ok("...with no back arrow at all", r.back == "", r.back)
    ok("...and no breadcrumb reading its own name back", shell.crumb("/dashboard") == ())


def test_clicking_settings_replaces_the_choices_and_adds_the_back_arrow():
    """"the choices in the left navigation change to that sub menu and then there's a back arrow
    at the top" — the whole ruling, in one assertion block."""
    _box()
    r = shell.rail("/settings/profile")
    ok("settings is level 2", r.level == 2)
    ok("...the rail is now the sub menu",
       [i.label for i in r.items] == ["Profile", "Usage", "Notifications", "Danger Zone"],
       str([i.label for i in r.items]))
    ok("...the main sections are GONE, not pushed aside",
       "Inbox" not in [i.label for i in r.items])
    ok("...it is titled with where you are", r.title == "Settings", r.title)
    ok("...and the back arrow goes home", r.back == "/dashboard", r.back)


def test_the_inbox_gets_the_same_move_home():
    """His second ask: the same back arrow at the top of our inbox. Nothing about the rail is
    settings-specific — a section with choices of its own gets the second level, whoever owns it."""
    _box()
    r = shell.rail("/inbox/unanswered")
    ok("the inbox is level 2 too", r.level == 2)
    ok("...with its own choices", [i.label for i in r.items] == ["All", "Unanswered"])
    ok("...and a back arrow to the dashboard", r.back == "/dashboard", r.back)
    ok("...owned by the machine, not by core", shell.current("/inbox").machine == "a_machine")


def test_the_breadcrumb_is_settings_slash_profile():
    """The owner's reference carries "Settings / Profile" over the page."""
    _box()
    ok("two parts, section then choice", shell.crumb("/settings/profile") == ("Settings", "Profile"),
       str(shell.crumb("/settings/profile")))
    ok("the danger row names itself too",
       shell.crumb("/settings/danger") == ("Settings", "Danger Zone"))
    ok("a level-2 page matching no item still says where it is",
       shell.crumb("/settings") == ("Settings",), str(shell.crumb("/settings")))


# ── the decision is the path's, never the caller's ──────────────────────────────────────────────

def test_a_section_with_nothing_underneath_stays_at_level_one():
    """Swapping the main nav for an empty column and a back arrow takes every choice away and
    offers nothing back. "The choices change to that sub menu" cannot mean that."""
    _box(inbox_items=False)
    r = shell.rail("/inbox")
    ok("still level 1", r.level == 1, str(r))
    ok("...so the buyer keeps every other section", len(r.items) == 3)
    ok("...and no back arrow appears", r.back == "")


def test_home_never_becomes_level_two_even_carrying_items():
    """Back-to-home from home is a control that cannot succeed."""
    shell._reset_for_tests()
    shell.register_section("dashboard", order=0, machine="core", title="Dashboard",
                           href="/dashboard", home=True,
                           items=[{"key": "today", "label": "Today", "href": "/dashboard/today"}])
    ok("home is level 1 on itself", shell.rail("/dashboard").level == 1)
    ok("...and on its own children", shell.rail("/dashboard/today").level == 1)


def test_a_back_arrow_at_level_two_is_never_empty():
    """THE ONE THAT WOULD SHIP A DEAD CONTROL. Every level-2 rail in every shape must carry a real
    destination — the screen whose whole job is to be escapable is the worst place for a no-op."""
    _box()
    for path in ("/settings", "/settings/profile", "/settings/danger", "/settings/usage",
                 "/inbox", "/inbox/unanswered", "/inbox/anything/deeper"):
        r = shell.rail(path)
        if r.level == 2:
            ok(f"{path}: back is a real path", r.back.startswith("/"), r.back)


def test_a_prefix_that_is_not_a_path_prefix_claims_nothing():
    """`"/inbox".startswith("/in")` is true and means nothing. Without a separator boundary a
    section at /in lights up the rail on every page of /inbox — the same class of mistake as
    matching a URL without its quote."""
    shell._reset_for_tests()
    shell.register_section("home", order=0, machine="core", title="Home", href="/", home=True)
    shell.register_section("in", order=1, machine="m", title="In", href="/in",
                           items=[{"key": "one", "label": "One", "href": "/in/one"}])
    ok("/inbox does not belong to /in", (shell.current("/inbox") or shell.current("/")).key != "in",
       str(shell.current("/inbox")))
    ok("/in/one does", shell.current("/in/one").key == "in")
    ok("/in itself does", shell.current("/in").key == "in")


def test_the_nearer_section_wins():
    """A section nested under another is answered by the nearer of the two, not by whichever
    happened to register first."""
    shell._reset_for_tests()
    shell.register_section("settings", order=0, machine="core", title="Settings", href="/settings",
                           items=[{"key": "alpha", "label": "Alpha", "href": "/settings/alpha"}])
    shell.register_section("keys", order=1, machine="m", title="Keys", href="/settings/keys",
                           items=[{"key": "beta", "label": "Beta", "href": "/settings/keys/beta"}])
    ok("the deeper section owns its own page", shell.current("/settings/keys").key == "keys")
    ok("...and its children", shell.current("/settings/keys/beta").key == "keys")
    ok("the shallower one keeps the rest", shell.current("/settings/alpha").key == "settings")


def test_exactly_one_row_is_marked():
    """A rail marks where you are. Two marks is worse than none — it tells the person the screen
    does not know either."""
    _box()
    r = shell.rail("/settings/profile")
    marked = [i.key for i in r.items if shell.is_current(i, "/settings/profile")]
    ok("one row marked, and the right one", marked == ["profile"], str(marked))
    r1 = shell.rail("/dashboard")
    marked1 = [i.key for i in r1.items if shell.is_current(i, "/dashboard")]
    ok("level 1 marks the section you are in", marked1 == ["dashboard"], str(marked1))


def test_an_unknown_path_still_renders_a_rail():
    """A 404, a page nobody registered, a route from a machine this box does not carry. The rail
    must still draw — a menu that vanishes is how a person gets stranded."""
    _box()
    r = shell.rail("/nowhere")
    ok("level 1, not a crash", r.level == 1)
    ok("...with every section still reachable", len(r.items) == 3)
    ok("...and no breadcrumb invented for it", shell.crumb("/nowhere") == ())


# ── the registry refuses what would break a screen ──────────────────────────────────────────────

def test_it_refuses_at_import_not_on_the_screen():
    shell._reset_for_tests()
    raises("a key that is not a slug", shell.register_section, "Not A Key", order=1,
           machine="m", title="T", href="/t")
    raises("a one-letter key, like register_step refuses", shell.register_section, "k",
           order=1, machine="m", title="T", href="/t")
    raises("no machine named", shell.register_section, "thing", order=1, machine="",
           title="T", href="/t")
    raises("no title a buyer can read", shell.register_section, "thing", order=1, machine="m",
           title="  ", href="/t")
    raises("a relative path", shell.register_section, "thing", order=1, machine="m",
           title="T", href="t")
    raises("a non-integer order", shell.register_section, "thing", order="1", machine="m",
           title="T", href="/t")
    raises("an item with no label", shell.register_section, "thing", order=1, machine="m",
           title="T", href="/t", items=[{"key": "alpha", "label": "", "href": "/t/alpha"}])
    raises("an item with a relative path", shell.register_section, "thing", order=1, machine="m",
           title="T", href="/t", items=[{"key": "alpha", "label": "A", "href": "t/alpha"}])
    raises("a tone nothing can draw", shell.register_section, "thing", order=1, machine="m",
           title="T", href="/t",
           items=[{"key": "alpha", "label": "A", "href": "/t/alpha", "tone": "sparkly"}])


def test_two_items_sharing_a_key_are_refused():
    """The rail marks the current choice by key, so the second would light up whenever the first
    was open — a duplicate that looks cosmetic and is not."""
    shell._reset_for_tests()
    raises("one key, twice", shell.register_section, "thing", order=1, machine="m", title="T",
           href="/t", items=[{"key": "alpha", "label": "A", "href": "/t/alpha"},
                             {"key": "alpha", "label": "Also A", "href": "/t/beta"}])


def test_one_machine_cannot_take_another_s_place():
    shell._reset_for_tests()
    shell.register_section("inbox", order=1, machine="mine", title="Inbox", href="/inbox")
    raises("a second machine claiming the key", shell.register_section, "inbox", order=1,
           machine="theirs", title="Theirs", href="/theirs")
    ok("the first registration is untouched", shell.current("/inbox").machine == "mine")


def test_re_importing_the_same_machine_does_not_stack():
    shell._reset_for_tests()
    shell.register_section("inbox", order=1, machine="mine", title="Inbox", href="/inbox")
    shell.register_section("inbox", order=1, machine="mine", title="Inbox", href="/inbox")
    ok("still one section", len(shell.sections()) == 1, str(shell.sections()))


def test_two_homes_are_refused():
    """The back arrow resolves to the home section, so two of them is an ambiguous destination for
    every second-level screen in the box, not a preference."""
    shell._reset_for_tests()
    shell.register_section("alpha", order=1, machine="m", title="Alpha", href="/alpha", home=True)
    raises("a second home", shell.register_section, "beta", order=2, machine="m", title="Beta",
           href="/beta", home=True)


def test_the_rail_does_not_reshuffle_between_renders():
    """A menu whose items move is a menu nobody learns."""
    shell._reset_for_tests()
    for k in ("zebra", "apple", "mango"):
        shell.register_section(k, order=5, machine="m", title=k.title(), href=f"/{k}")
    once = [s.key for s in shell.sections()]
    twice = [s.key for s in shell.sections()]
    ok("ties break on key, stably", once == twice == ["apple", "mango", "zebra"], str(once))


def test_home_href_terminates_on_a_box_with_nothing():
    """`core.dash.landing` says this about itself: it must terminate, not loop. A box that
    registered no section at all still has to answer where 'back' goes."""
    shell._reset_for_tests()
    ok("falls through to the one page every box serves", shell.home_href() == "/dash/login",
       shell.home_href())
    shell.register_section("only", order=3, machine="m", title="Only", href="/only")
    ok("...then to the first section when none claims home", shell.home_href() == "/only")


# ── core stays core ─────────────────────────────────────────────────────────────────────────────

def test_the_base_box_carries_the_choices_and_a_machine_only_ADDS():
    """THE OWNER'S QUESTION, 2026-09-17: *"make sure that we are building the core box to have the
    basic dashboard and settings choices. And then the Customer machine plug-ins will add dashboard
    areas."* This is that contract, asserted in both directions, so it cannot drift into something
    else while nobody is looking.

    THE BASE IS CORE'S AND IT IS NEVER EMPTY. A box that carries no machine at all still has its
    own home. That is what makes `register_section` an ADDITION rather than a construction kit:
    nobody has to supply the floor.

    A MACHINE ADDS ITS OWN ROW AND CHANGES NOTHING ELSE. Not core's row, not another machine's, not
    the order of what was already there. Two boxes carrying different machines therefore differ by
    exactly the machines they carry — which is the whole promise of selling the same binary four
    ways.
    """
    shell._reset_for_tests()
    shell.register_section("dashboard", order=0, machine="core", title="Dashboard",
                           href="/dashboard", home=True)
    base = [(s.key, s.machine) for s in shell.sections()]
    ok("a box with no machine still has core's own home",
       base == [("dashboard", "core")], str(base))
    ok("...and it is the home the back arrow resolves to", shell.home_href() == "/dashboard")

    shell.register_section("inbox", order=10, machine="customer_voice", title="Inbox",
                           href="/inbox")
    with_one = [(s.key, s.machine) for s in shell.sections()]
    ok("a machine adds exactly one row",
       with_one == base + [("inbox", "customer_voice")], str(with_one))

    shell.register_section("outreach", order=20, machine="lead_machine", title="Outreach",
                           href="/outreach")
    with_two = [(s.key, s.machine) for s in shell.sections()]
    ok("a second machine adds its own and disturbs neither",
       with_two[:2] == with_one and with_two[2] == ("outreach", "lead_machine"), str(with_two))
    ok("...core's row has not moved", with_two[0] == ("dashboard", "core"))

    # THE OTHER DIRECTION: a box that does not carry a machine does not carry its row. Same
    # registry, one import fewer — which is exactly what an exported box is.
    shell._reset_for_tests()
    shell.register_section("dashboard", order=0, machine="core", title="Dashboard",
                           href="/dashboard", home=True)
    shell.register_section("outreach", order=20, machine="lead_machine", title="Outreach",
                           href="/outreach")
    ok("the box without the inbox has no inbox row",
       [s.key for s in shell.sections()] == ["dashboard", "outreach"],
       str([s.key for s in shell.sections()]))
    ok("...and core's row is identical in both boxes",
       shell.sections()[0].machine == "core" and shell.sections()[0].href == "/dashboard")


def test_a_machine_cannot_take_the_base_box_s_own_row():
    """The floor is core's. A machine claiming `dashboard` would replace the one thing every box is
    guaranteed to have, and `home_href()` resolves the back arrow through it."""
    shell._reset_for_tests()
    shell.register_section("dashboard", order=0, machine="core", title="Dashboard",
                           href="/dashboard", home=True)
    raises("a machine claiming core's key", shell.register_section, "dashboard", order=0,
           machine="customer_voice", title="My Dashboard", href="/inbox/home")
    ok("core still owns it", shell.current("/dashboard").machine == "core")


def test_core_names_no_machine():
    """`core/` ships whole into every box. A rail that spelled out its sections would put one
    product's menu into every other product — what test_core_boundary exists to stop."""
    src = pathlib.Path(__file__).resolve().parents[1] / "core" / "shell.py"
    text = src.read_text().lower()
    for word in ("customer_voice", "lead_machine", "content_machine", "instagram",
                 "messenger", "zernio", "resend", "gmail"):
        ok(f"core/shell.py never says {word!r}", word not in text)


def test_the_suite_is_named_in_ci():
    here = pathlib.Path(__file__).resolve().parents[1]
    if not (here / ".github").is_dir():
        print("  --   not the repo — a buyer's box has no CI manifest to be named in")
        return
    wf = (here / ".github/workflows/tests.yml").read_text()
    ok("this suite runs in CI", "test_the_two_level_rail \\" in wf)


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(f"\n{name}")
            fn()
    print("\n" + ("FAILED" if _failed else "PASS") + f" — {_failed} failure(s)")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
