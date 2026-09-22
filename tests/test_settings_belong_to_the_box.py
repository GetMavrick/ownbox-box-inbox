"""Settings belong to the box, and sit in the box's own drawer.

Owner, 2026-09-22, on where these live: the system drawer — *"That's where they belong"* — and on
which of them are the box's: *"The LLM and the phone."* Put two shapes to him and he chose the
second: **two destinations**, core's Settings for what the box shares and each machine's own
Settings for what only that machine has. Not one merged registry that core would have to
understand every machine's settings shape to draw — which `tests/test_core_boundary.py` refuses
anyway, and which would have made this wait on a registry nobody has built.

WHY THIS SUITE AND NOT A GLANCE AT THE DIFF. The interesting half is not that the rows render, it
is WHERE THEY POINT. Core must not learn one machine's URL to send a person to the screen that
sets their AI key — `shell.setup_href()` is the seam, and a hardcoded `/inbox/setup` would pass
every eyeball and then break the first box that ships a different machine. So the strong test
below builds a box with a machine that is not the inbox, at a set-up URL that is not `/inbox`,
and reads the links back.

The first draft of the code this covers was ALREADY caught once: `test_the_inbox_joins_the_rail`
refused a comment in `core/dash/home.py` that named a machine in prose. The menu names nobody.

Run: python tests/test_settings_belong_to_the_box.py
"""
import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB = pathlib.Path(tempfile.mkdtemp(prefix="box-settings-")) / "box.db"
os.environ["AIOS_DB_PATH"] = str(_DB)                      # BEFORE any core import
os.environ.setdefault("DASH_TOKEN", "test-dash-pw")

from core import state                                      # noqa: E402

state.init_db()

from core import box_secrets, shell                         # noqa: E402
from core.dash import box_settings, home                    # noqa: E402

# THE DOORS CORE ACTUALLY SERVES, read off the module that serves them rather than
# copied into this file — a list typed here would agree with itself forever.
bs_doors = set(box_settings.registered_doors())

_failed = 0


def ok(what: str, cond: bool, got: str = "") -> None:
    global _failed
    if cond:
        print(f"  ok   {what}")
    else:
        _failed += 1
        print(f"  FAIL {what}" + (f"  — {got}" if got else ""))


def _box(setup_href: str = "/inbox/setup") -> None:
    """A box shape this suite owns outright: core's two sections plus ONE made-up machine.

    THE MACHINE IS DELIBERATELY NOT THE INBOX. A fixture that registers the real one proves only
    that the code works on the box we happen to sell today; the question is whether core learned
    that box's URLs, and the only way to ask is to hand it different ones.
    """
    shell._SECTIONS.clear()
    shell.register_section("dashboard", order=0, machine="core", title="Dashboard",
                           href="/dashboard", home=True)
    shell.register_section("settings", order=90, machine="core", title="Settings",
                           href="/settings")
    shell.register_section("widgets", order=10, machine="widget_works", title="Widgets",
                           href="/widgets/",
                           items=[{"key": shell.SETUP_KEY, "label": "Set up",
                                   "href": setup_href}])


def _rows(owner: bool = True) -> str:
    return home._box_rows(owner=owner)


def test_settings_is_a_core_section_and_it_is_last():
    print("test_settings_is_a_core_section_and_it_is_last")
    _box()
    secs = shell.sections()
    got = [(s.key, s.machine) for s in secs]
    ok("core registers Settings itself", ("settings", "core") in got, str(got))
    # LAST, NOT SECOND. The rail's middle belongs to the machines, however many a box carries;
    # Settings is where a person looks when they want to change a thing, and that is the bottom.
    ok("Settings is the last row in the rail", secs[-1].key == "settings", str([s.key for s in secs]))
    ok("core's home is still first", secs[0].key == "dashboard", str([s.key for s in secs]))


def test_it_carries_the_box_level_steps_and_leaves_the_machines_theirs():
    print("test_it_carries_the_box_level_steps_and_leaves_the_machines_theirs")
    _box()
    html = _rows()
    keys = {str(e.get("key")): e for e in box_secrets.setup_state()}
    # THE OWNER'S TWO, BY NAME. He ruled these, so they are asserted by his words and not by
    # whatever `BOX_SETTINGS` happens to say — a test that reads the constant it is checking
    # passes no matter what somebody puts in it.
    for key in ("anthropic", "phone"):
        title = str(keys.get(key, {}).get("title") or "")
        ok(f"the box's Settings carries {key!r}", bool(title) and title in html, title)
    # AND NOT THE MACHINE'S. Mailbox and channels are the inbox's business; a box running some
    # other machine has neither, which is exactly why they cannot live on this screen.
    for key in ("email", "zernio"):
        title = str(keys.get(key, {}).get("title") or "")
        ok(f"it leaves {key!r} to the machine that owns it",
           bool(title) and title not in html, title)


def test_core_does_not_know_where_set_up_lives():
    print("test_core_does_not_know_where_set_up_lives")
    # THE ONE THAT MATTERS. If this screen carried `/inbox/setup` in its source it would still
    # render correctly on the box we sell and send every other box to a 404.
    _box(setup_href="/widgets/first-run")
    html = _rows()
    hrefs = re.findall(r'href="([^"]+)"', html)
    ok("every row points somewhere", bool(hrefs), str(hrefs))
    # THE LINKS CORE SYNTHESISES — the ones this screen builds — follow the registry. That is the
    # invariant this file owns and it is asserted strictly.
    #
    # NO BOX STEP SYNTHESISES ONE TODAY, since 2026-09-22: all three now declare a core door of
    # their own, so `built` is legitimately empty on a real box. THAT IS NOT A REASON TO DROP THE
    # ASSERTION — the fallback is live code that the next box-level step will land on, and an
    # invariant nobody exercises is one a refactor deletes unnoticed. So it is exercised directly,
    # with a step that declares no door at all.
    built = [h for h in hrefs if "#" in h]
    ok("every link core builds follows the machine that registered set-up",
       all(h.startswith("/widgets/first-run#") for h in built), str(built))

    _real = box_secrets.setup_state
    try:
        box_secrets.setup_state = lambda: [
            {"key": "anthropic", "title": "Your AI account", "status": "not_connected",
             "detail": ""}]                      # no `link`, no `action_href` — the fallback path
        fallback = re.findall(r'href="([^"]+)"', _rows())
    finally:
        box_secrets.setup_state = _real
    ok("a step with no door of its own falls back to the registry, not to a written URL",
       fallback == ["/widgets/first-run#anthropic"], str(fallback))
    ok("no row hardcodes the inbox's set-up screen",
       not any("/inbox/setup" in h for h in hrefs), str(hrefs))

    # THE EXCEPTION THIS TEST NAMED HAS ENDED THE WAY IT SAID IT WOULD (OSDev4, 2026-09-22).
    #
    # OSDev5 wrote it as a known, named exception rather than a silent tolerance: `_AGENT_STEP`
    # declared `/inbox/agent` — a machine path, in CORE step data — so on a box carrying some
    # other machine that row pointed at a 404. His words were "the moment `/inbox/agent` moves
    # behind the registry this line fails and gets deleted — which is the correct way for a known
    # exception to end." It moved. The owner ruled the three box-level steps into core's own
    # drawer and their doors moved with them, so this is now the strong form of the assertion:
    # every link a step declares is a path CORE serves, on any box, whatever machine it carries.
    declared = [h for h in hrefs if "#" not in h]
    ok("every declared link is a path core serves on any box",
       declared and all(h in bs_doors for h in declared), str(declared))
    ok("...and not one of them names a machine",
       not any(h.startswith("/inbox/") for h in declared), str(declared))


def test_a_member_is_never_offered_a_door_they_are_refused_at():
    print("test_a_member_is_never_offered_a_door_they_are_refused_at")
    # THE WALK SUITE FOUND THIS ONE, not this file, and it was a REAL dead end on a member's box:
    # /settings drew "Set up" against the agent step, whose route /inbox/agent is owner-only, so a
    # member pressed it and met a 403. The owner's standing rule is no dead screens.
    #
    # THE SCREEN STILL ADMITS MEMBERS, deliberately — a phone belongs to a person, not to the
    # box's owner, and hiding the whole page to dodge one row would be the worse trade.
    _box(setup_href="/widgets/first-run")     # a machine that is NOT the inbox, as everywhere here
    import re as _re
    member = _rows(owner=False)
    ok("a member still sees what is set", "Your AI account" in member and "Your phone" in member)
    ok("...and is never pointed at a machine's own door",
       "/inbox/" not in member, str(_re.findall(r'href="([^"]+)"', member)))
    # THE SPLIT IS BY WHO KNOWS THE GATE, and core knows its own. A phone belongs to a person
    # rather than to whoever bought the box, so a member is offered it — properly, for the first
    # time. The two that bill or read the whole box declare `owner_only` and are not drawn.
    ok("...but the phone, which is theirs and whose gate core knows, IS offered",
       "/settings/phone" in member, str(_re.findall(r'href="([^"]+)"', member)))
    ok("...and the two the box would refuse them at are not drawn",
       "/settings/ai" not in member and "/settings/agent" not in member,
       str(_re.findall(r'href="([^"]+)"', member)))
    owner_view = _rows(owner=True)
    ok("the owner is still offered all of it",
       all(h in owner_view for h in ("/settings/ai", "/settings/phone", "/settings/agent")),
       str(_re.findall(r'href="([^"]+)"', owner_view)))
    src = (pathlib.Path(__file__).resolve().parents[1] / "core/dash/home.py").read_text()
    ok("and the source does not carry that URL either", "/inbox/setup" not in src)


def test_a_box_with_no_set_up_screen_still_renders():
    print("test_a_box_with_no_set_up_screen_still_renders")
    # A LEAD BOX HAS NO SET-UP SCREEN AT ALL — `shell.setup_href()` says so itself and returns "".
    # The rows must then say what is set and simply offer nowhere to press, rather than drawing a
    # link to "" that lands the person back on the page they are standing on.
    shell._SECTIONS.clear()
    shell.register_section("dashboard", order=0, machine="core", title="Dashboard",
                           href="/dashboard", home=True)
    shell.register_section("settings", order=90, machine="core", title="Settings",
                           href="/settings")
    ok("this box really has nowhere to send anyone", shell.setup_href() == "")
    html = _rows()
    ok("the screen still names what is set", "Your AI account" in html, html[:120])
    ok("and draws no empty link", 'href=""' not in html, html[:200])


def test_an_unreadable_reader_is_not_a_500():
    print("test_an_unreadable_reader_is_not_a_500")
    # SETTINGS IS NOT ALLOWED TO BE THE SCREEN THAT BREAKS. Every other step reader on this box is
    # wrapped; this one is the page itself, and a paying customer clicking Settings must never meet
    # a stack trace because one credential reader raised.
    _box()
    real = box_secrets.setup_state

    def _boom():
        raise RuntimeError("reader is down")

    box_secrets.setup_state = _boom
    try:
        html = _rows()
    finally:
        box_secrets.setup_state = real
    ok("it says so in words instead of raising", "could not read" in html.lower(), html[:160])
    # AND THE GUARD IS NOT VACUOUS: the same call with the real reader says something else.
    ok("the healthy page does not say that", "could not read" not in _rows().lower())


def test_the_whole_page_renders():
    print("test_the_whole_page_renders")
    _box()
    html = home.chrome("/settings", title="Settings", lede="x",
                       body='<div class="card">' + _rows() + '</div>')
    ok("it is a page", html.lstrip().startswith("<!doctype html>"), html[:40])
    ok("Settings lights its own row in the rail", 'href="/settings" aria-current' in html
       or ('aria-current' in html and '/settings' in html), "")


for _fn in (test_settings_is_a_core_section_and_it_is_last,
            test_it_carries_the_box_level_steps_and_leaves_the_machines_theirs,
            test_core_does_not_know_where_set_up_lives,
            test_a_member_is_never_offered_a_door_they_are_refused_at,
            test_a_box_with_no_set_up_screen_still_renders,
            test_an_unreadable_reader_is_not_a_500,
            test_the_whole_page_renders):
    _fn()

print(("FAILED — %d failure(s)" % _failed) if _failed else "PASS — 0 failure(s)")
sys.exit(1 if _failed else 0)
