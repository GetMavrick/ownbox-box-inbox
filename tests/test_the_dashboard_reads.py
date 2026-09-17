"""`/dashboard` — the box's home, and every number on it belonging to the machine that counted it.

Owner, 2026-09-17: *"They should go to /dashboard and then they should see some helpful information
and can click settings to set things up."* White first (owner, 2026-09-16: "we want white screens
first and foremost"), which is why this page does not reuse `core.dash.page()` — that chrome is the
dark operator console, and handing it to a plumber is handing him the admin panel.

FIVE DEFECTS FOUND BEFORE THIS SUITE EXISTED, three by rendering the page and two by OSDev1's
review of #1317. Each has a test here, because every one of them was invisible in the diff and
obvious on the screen:

  1. `_esc(0)` returned "" — `str(s or "")`. A machine reporting "0 emails sent" drew the words
     with no number. On a page whose whole job is to be believed, a figure that silently vanishes
     is worse than a wrong one: nobody can see it go.
  2. The headline was read as `text`; `report._normalize` writes `label`. Every headline on the
     page rendered as a bare number with no words beside it.
  3. The "this machine said nothing" guard tested `value is not None` — and `_normalize` fills
     `value` with 0 for every machine ever registered, so the guard could never fire and two idle
     machines each drew an empty card.
  4. The big number at the top read `view()["needs"]` as people. It counts needs_you ITEMS. The
     page said "2 people are waiting on a reply" eight lines above "3 people are waiting on a
     reply" — an invented number, in the largest type, contradicted on its own screen.
  5. `crumb()` took the first matching item rather than the longest, so an item at /settings
     swallowed the crumb for its sibling at /settings/keys.

Run: python tests/test_the_dashboard_reads.py
"""
import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB = pathlib.Path(tempfile.mkdtemp(prefix="dash-home-")) / "box.db"
# BEFORE ANY core IMPORT — core.config.Settings reads os.environ at class-body time.
os.environ["AIOS_DB_PATH"] = str(_DB)
os.environ.setdefault("DASH_TOKEN", "test-dash-pw")

from core import state                                      # noqa: E402

state.init_db()

from core import report, shell                              # noqa: E402
from core.dash import home                                  # noqa: E402

_failed = 0


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


def _text(html_: str) -> str:
    import html as _h
    stripped = re.sub(r"(?s)<(script|style|svg).*?</\1>", " ", html_)
    return " ".join(_h.unescape(re.sub(r"<[^>]+>", " ", stripped)).split())


def _wipe() -> None:
    with state.connect() as c:
        c.execute("DELETE FROM daily_reports")


def _reports(**by_machine) -> None:
    """Register reporters and write a day, the way the worker does."""
    _wipe()
    for machine, rep in by_machine.items():
        report.register_reporter(machine, rep.get("title") or machine, lambda day, r=rep: r)
    report.snapshot()


def _page() -> str:
    _restore()
    return _c().get("/dashboard").get_data(as_text=True)


def _restore() -> None:
    """Put the box's own section back.

    TWO TESTS BELOW RESET THE SHELL REGISTRY to build a shape of their own, and the registry is
    module-global. Without this the page tests passed only because of the ALPHABETICAL order the
    runner happens to use — one of the resetting tests re-registered `dashboard` on its way past,
    so the rail assertions downstream were reading a fixture they never asked for. Renaming a test
    would have broken them for a reason no message would explain. So every page render restores
    what it needs instead of inheriting it.
    """
    shell._reset_for_tests()
    shell.register_section("dashboard", order=0, machine="core", title="Dashboard",
                           href="/dashboard", home=True)


# ── the five ────────────────────────────────────────────────────────────────────────────────────

def test_a_zero_is_a_number_and_survives_to_the_screen():
    """`str(s or "")` is how a dashboard loses every zero it was asked to show."""
    ok("_esc(0) keeps the zero", home._esc(0) == "0", repr(home._esc(0)))
    ok("_esc(False) keeps it too", home._esc(False) == "False", repr(home._esc(False)))
    ok("...and None is still the only absence", home._esc(None) == "")
    _reports(lead_machine={"title": "Lead",
                           "headline": {"value": 0, "label": "emails sent"}})
    words = _text(_page())
    ok("a reported zero reaches the page", "0 emails sent" in words, words[-200:])


def test_the_headline_says_words_not_just_a_number():
    """`report._normalize` writes {"value", "label", "delta"} — never `text`."""
    _reports(customer_voice={"title": "Unified Inbox",
                             "headline": {"value": 72, "label": "conversations mirrored"}})
    words = _text(_page())
    ok("the number arrives", "72" in words, words[-200:])
    ok("...with the machine's own words beside it", "conversations mirrored" in words,
       words[-200:])


def test_a_machine_with_nothing_to_say_draws_nothing():
    """Owner, via core/dash/review.py: "that section is just blank for now". A card carrying a
    heading, a subtitle and no rows reads as a broken screen, not an idle machine."""
    _reports(customer_voice={"title": "Unified Inbox",
                             "headline": {"value": 5, "label": "conversations mirrored"}},
             content_machine={"title": "Content"})          # no headline at all
    html_ = _page()
    words = _text(html_)
    ok("the machine that spoke is on the page", "conversations mirrored" in words)
    ok("...and the silent one is absent, not empty", "Content" not in words, words[-240:])


def test_the_top_card_derives_no_number_of_its_own():
    """THE ONE THAT REACHED THE SCREEN. `view()["needs"]` counts needs_you ITEMS, not people."""
    _reports(customer_voice={
        "title": "Unified Inbox",
        "headline": {"value": 72, "label": "conversations mirrored"},
        "needs_you": [{"value": 3, "text": "people are waiting on a reply"},
                      {"value": 2, "text": "leads came from an ad"}]})
    words = _text(_page())
    ok("the machine's own sentence is shown", "3 people are waiting on a reply" in words)
    ok("...and the item COUNT is never dressed up as people",
       "2 people are waiting on a reply" not in words, words[:320])
    ok("...the card says what it is", "Waiting on you" in words)


def test_the_breadcrumb_names_the_page_you_are_on():
    """OSDev1's review catch: first match, not longest, so /settings swallowed /settings/keys."""
    shell._reset_for_tests()
    shell.register_section("dashboard", order=0, machine="core", title="Dashboard",
                           href="/dashboard", home=True)
    shell.register_section("settings", order=1, machine="core", title="Settings", href="/settings",
                           items=[{"key": "profile", "label": "Profile", "href": "/settings"},
                                  {"key": "keys", "label": "Keys", "href": "/settings/keys"}])
    ok("the deeper page names itself",
       shell.crumb("/settings/keys") == ("Settings", "Keys"), str(shell.crumb("/settings/keys")))
    ok("...and the shallower one still names itself",
       shell.crumb("/settings") == ("Settings", "Profile"), str(shell.crumb("/settings")))


def test_a_section_at_root_is_not_current_everywhere():
    """OSDev1's other catch. Every path is "under" /, so root-as-container lights two rows at once
    and tells the person the screen does not know where they are either."""
    shell._reset_for_tests()
    shell.register_section("root", order=0, machine="m", title="Home", href="/", home=True)
    shell.register_section("things", order=1, machine="m", title="Things", href="/things")
    r = shell.rail("/things")
    marked = [i.key for i in r.items if shell.is_current(i, "/things")]
    ok("exactly one row is marked, and it is not root", marked == ["things"], str(marked))
    r2 = shell.rail("/")
    ok("root is still current on root itself",
       [i.key for i in r2.items if shell.is_current(i, "/")] == ["root"])


# ── the page itself ─────────────────────────────────────────────────────────────────────────────

def test_a_brand_new_box_says_so_instead_of_showing_zeros():
    """`view()` distinguishes "no report yet" from "reported zero" precisely so a page cannot
    invent a number for a day nothing was written for."""
    _wipe()
    words = _text(_page())
    ok("it says there is nothing yet", "Nothing to report yet" in words, words[-200:])
    ok("...and does not draw a waiting card over an empty day",
       "Waiting on you" not in words, words[-200:])


def test_nothing_waiting_reads_as_peace_not_as_a_problem():
    """A bold 0 under "waiting on you" reads as a problem for a second before it reads as calm."""
    _reports(customer_voice={"title": "Unified Inbox",
                             "headline": {"value": 40, "label": "conversations mirrored"}})
    words = _text(_page())
    ok("it says so in words", "Nothing is waiting on you" in words, words[:300])
    ok("...with no zero standing in for the sentence", "0 people" not in words)


def test_a_reporter_link_is_resolved_against_what_this_box_serves():
    """An href arrives from a REPORTER — a module with no idea which pages this box serves, and
    the kind of producer that outlives the screen it was written for."""
    _reports(customer_voice={
        "title": "Unified Inbox",
        "headline": {"value": 9, "label": "conversations mirrored"},
        "needs_you": [{"value": 1, "text": "someone is waiting", "href": "/not/served/here"}]})
    html_ = _page()
    ok("the sentence still shows", "someone is waiting" in _text(html_))
    ok("...but no link to a page this box does not serve",
       'href="/not/served/here"' not in html_, html_[:200])


def test_a_machine_that_failed_does_not_look_like_one_with_no_news():
    """report §1.11, and the page has to keep it."""
    _wipe()
    report.register_reporter("content_machine", "Content", lambda day: 1 / 0)
    report.snapshot()
    words = _text(_page())
    ok("the failure is visible as a failure", "could not report" in words, words[-240:])


def test_the_rail_is_there_and_marks_where_you_are():
    _reports(customer_voice={"title": "Unified Inbox",
                             "headline": {"value": 1, "label": "conversation mirrored"}})
    html_ = _page()
    ok("the rail renders", 'class="rail"' in html_)
    ok("...and marks the dashboard", 'aria-current="page"' in html_)
    ok("...with no back arrow on home", 'class="back"' not in html_)


def test_it_is_white_first():
    """Owner, 2026-09-16 and again 2026-09-17: white screens first and foremost."""
    html_ = _page()
    # THE RULE, NOT THE HEX. Pinned to "#f7f8f9" first, and the very next pass at the palette
    # turned this red while the page was still white — a failure that taught nobody anything. The
    # owner's instruction is that the page is light unless someone asks otherwise, not that it is
    # one particular grey.
    root = re.search(r":root\{([^}]*)\}", html_)
    ok(":root declares both the ground and the ink",
       bool(root) and "--bg:" in root.group(1) and "--ink:" in root.group(1),
       root.group(1)[:80] if root else "no :root")
    bg = re.search(r"--bg:#([0-9a-fA-F]{6})", html_)
    ok("...and the ground is a light one", bool(bg) and int(bg.group(1)[:2], 16) > 0xE0,
       bg.group(0) if bg else "no --bg")
    ok("the body paints it rather than inheriting the host's",
       "background:var(--bg)" in html_)
    ok("nothing flips a reader to dark who never asked",
       "prefers-color-scheme" not in html_)


def test_the_page_computes_nothing():
    """`core/dash/review.py` says this of itself and the reason is the same here: the web process
    does not load the worker's modules, so a live read renders blanks for every machine the worker
    reported on. A department import here is the bug that produces an empty dashboard."""
    src = (pathlib.Path(__file__).resolve().parents[1] / "core" / "dash" / "home.py").read_text()
    for banned in ("marketing.", "from marketing", "import marketing"):
        ok(f"home.py does not import a department ({banned!r})", banned not in src)


# ── the phone, which is where this box is mostly read ───────────────────────────────────────────

def _settings_box() -> None:
    """The owner's own Settings list, verbatim from his reference."""
    shell._reset_for_tests()
    shell.register_section("dashboard", order=0, machine="core", title="Dashboard",
                           href="/dashboard", home=True)
    shell.register_section("settings", order=20, machine="core", title="Settings",
                           href="/settings",
                           items=[{"key": "profile", "label": "Profile", "href": "/settings/profile"},
                                  {"key": "billing", "label": "Billing",
                                   "href": "/settings/billing", "tone": "away"},
                                  {"key": "security", "label": "Security", "href": "/settings/security"},
                                  {"key": "danger_zone", "label": "Danger Zone",
                                   "href": "/settings/danger", "tone": "danger"}])


def test_an_item_page_carries_exactly_one_way_back():
    """THE DRAWER DELETED A PROBLEM RATHER THAN MANAGING IT.

    An earlier pass rendered TWO back links — one home, one to the section's own index — because
    the phone stacked the menu above the page and the owner's ten-item Settings list buried the
    form under 538px of navigation (measured, Chromium 375x780: page title at 585px, first field
    card at 673px). Two labels, two destinations, a breakpoint choosing between them.

    A drawer is not in the page's flow until it is asked for, so it costs the page no height and
    the phone carries the same menu the desktop carries. Back then means one thing on both: leave
    this section — which is what the owner's reference shows. Two controls reading the same and
    going to different places was the price of the old answer; this keeps it from growing back.
    """
    _settings_box()
    html_ = home.chrome("/settings/profile", title="Profile", lede="Your name.", body="")
    backs = re.findall(r'<a class="back" href="([^"]+)"', html_)
    ok("exactly one back control", len(backs) == 1, str(backs))
    ok("...and it leaves the section", backs == ["/dashboard"], str(backs))


def test_the_phone_gets_a_drawer_not_a_stack():
    """The owner's reference, and the only shape that keeps a ten-item menu off the page."""
    _settings_box()
    html_ = home.chrome("/settings/profile", title="Profile", lede="x", body="")
    ok("a hamburger exists", 'class="ham"' in html_)
    ok("...and a scrim closes it by tapping outside",
       'class="scrim"' in html_ and html_.count('for="navtoggle"') == 2)
    ok("...driven by a control a keyboard can reach, not a hidden input",
       ".navtoggle{position:absolute" in html_ and 'type="checkbox" id="navtoggle"' in html_)
    ok("the drawer is off-canvas until asked for", "transform:translateX(-101%)" in html_)
    ok("...and opening it is what brings it back",
       ".navtoggle:checked~.lay .rail{transform:none" in html_)
    ok("...with motion respected", "prefers-reduced-motion" in html_)
    ok("...and no JavaScript anywhere", "<script" not in html_)


def test_a_link_that_leaves_the_box_says_so_in_a_drawn_mark():
    """A CSS escape rendered as a missing-character box followed by a stray "97" beside the word
    Billing, in a headless render. Every other mark in this rail is an SVG, and an icon that
    depends on the reader's installed fonts is not an icon."""
    _settings_box()
    html_ = home.chrome("/settings", title="Settings", lede="x", body="")
    ok("no font-dependent escape in the stylesheet", "2197" not in html_)
    ok("the away row carries a drawn arrow", 'class="out"' in html_)


def test_the_page_shape_is_decided_by_the_path():
    """`crumb()` already answers it: two parts is an item inside a section, one part is the
    section's own index, none is level 1. The stylesheet needs it and nothing else does."""
    _settings_box()
    ok("an item page is marked as one",
       'class="lay lvl2-item"' in home.chrome("/settings/profile", title="P", lede="x", body=""))
    ok("the section index is not",
       'class="lay lvl2-index"' in home.chrome("/settings", title="S", lede="x", body=""))
    ok("the dashboard is level 1",
       'class="lay lvl1"' in home.chrome("/dashboard", title="D", lede="x", body=""))


def test_every_rail_row_keeps_one_optical_column():
    """A section with no icon renders a fixed-width blank, not nothing. A list where some rows
    indent and others do not reads as a mistake before it reads as a list."""
    shell._reset_for_tests()
    shell.register_section("dashboard", order=0, machine="core", title="Dashboard",
                           href="/dashboard", home=True, icon="M3 10.5 12 3l9 7.5")
    shell.register_section("plain", order=1, machine="m", title="Plain", href="/plain")
    html_ = home.chrome("/dashboard", title="D", lede="x", body="")
    ok("the row with an icon draws one", '<svg class="ic"' in html_)
    ok("...and the row without still reserves the column",
       '<span class="ic" style="width:19px"' in html_, html_[:200])


def test_a_stranger_never_sees_the_numbers():
    """OSDev1 HELD THIS PR FOR EXACTLY THIS, and he was right — the route shipped with no gate.

    It is cross-machine: every installed machine's totals, plus the list of people waiting on a
    reply. That is the same shape as `/app/review`, and `/app/review` was MEASURED returning 200
    with the meters, the monthly ceiling and every machine's totals to a stranger on a demo-zone
    host, no session and no token (2026-09-09). Five labels were public on that box.

    The bounce matters as much as the refusal: the owner's own box IS a public label and the
    session cookie is host-only, so a 404 here would lock him out of his own screen.
    """
    _restore()
    from core.dispatch import app
    anon = app.test_client()
    r = anon.get("/dashboard")
    ok("a stranger is refused", r.status_code in (302, 303, 404), str(r.status_code))
    ok("...and bounced to a sign-in, not a dead end",
       r.status_code == 404 or "/dash/login" in (r.headers.get("Location") or ""),
       str(r.headers.get("Location")))
    body = r.get_data(as_text=True)
    ok("...having been shown no figure at all",
       "Waiting on you" not in body and "conversations mirrored" not in body, body[:160])
    ok("while a signed-in reader gets the page", _c().get("/dashboard").status_code == 200)


def test_the_gate_is_reused_and_not_copied():
    """`review._admit`'s own docstring is the reason: "mirroring a gate is not the same as
    inheriting its reasoning, and the reasoning is what did not transfer". A second copy of that
    logic is a second thing to forget to update the next time the demo zone changes."""
    src = (pathlib.Path(__file__).resolve().parents[1] / "core" / "dash" / "home.py").read_text()
    ok("it calls the review's admit", "_admit()" in src)
    ok("...rather than re-deriving the host rules",
       "demo_zone" not in src and "label_sources" not in src)


def test_the_front_door_follows_the_dashboard_without_a_second_place_to_remember():
    """Owner, 2026-09-17, on the front door: */ must redirect to landing()… it follows /dashboard
    automatically the moment that lands.* That sentence is only TRUE if `/dashboard` is in
    `_LANDINGS`, and this asserts the half that makes it true.

    THE ROUTE ITSELF IS OSDev1'S, IN #1323, AND THIS BRANCH DELIBERATELY DOES NOT CARRY ONE.
    Three of us fixed the same 404 inside fifteen minutes; his is better than the version I had
    written, because a stranger is sent straight to the login and shown nothing of the box, where
    mine resolved through `landing()` first and so told an unauthenticated caller which pages this
    box serves — which is which machines it has. His PR body already anticipates this half:
    "When `/dashboard` joins `_LANDINGS`, `/` follows it for free."

    Two suites owning one behaviour is the same drift R4 refuses in the settings rail, so the
    assertions about the route live in his `tests/test_bare_domain_lands.py` and only the tuple
    is asserted here.
    """
    from core import dash as _dash
    ok("/dashboard is one of the landings", "/dashboard" in _dash._LANDINGS,
       str(_dash._LANDINGS))
    # NAMED BY POSITION, NOT BY THE PATH BELOW IT. Written first as "ahead of /voice/inbox", and
    # that entry is GONE — OSDev1's cut renamed it to /inbox/inbox while this branch was open, so
    # `.index()` would have raised ValueError the moment he replayed this onto main. It was then
    # rewritten as `[:2] == ("/dash/home", "/dashboard")`, which names a neighbour just as surely,
    # one place further along, and went red the day the owner ruled that neighbour out of the head
    # (2026-09-17, in the test below). The rule was only ever about ONE entry — core's own home,
    # first — so assert that, and the rest of the tuple can be reordered as often as the routes are.
    ok("...and it heads the list, so every box opens on a page core itself serves",
       _dash._LANDINGS[0] == "/dashboard", str(_dash._LANDINGS))


def test_no_machine_page_stands_ahead_of_cores_own_home():
    """Owner, to me, 2026-09-17: *"The lead machine shouldn't own core features."*

    MEASURED BEFORE ACTING ON IT, and he is right about this tuple in the strongest way:
    `/dash/home` is `marketing/lead_machine/dash.py` — it reads `gtm_leads`, `gtm_businesses` and
    the lead funnel. Heading `_LANDINGS`, it meant "where does this box open" — core's question —
    was answered by a machine, on every box that happened to carry that machine. `/dashboard`
    heads it now, and core ships whole, so every box serves it.

    THIS MOVES THE OWNER'S OWN LANDING, and saying so plainly is the point rather than a footnote.
    On 2026-09-09 he ruled which screen he wanted to see first; the only reason a machine's route
    carried that ruling is that core had no home of its own when he made it. It has one now. He
    has not been asked whether he wants the lead page back on top — the PR flags it, and it is one
    line in either direction.

    IT ASKS THE TUPLE, NOT THIS BOX, and that correction is worth keeping. An earlier version
    asserted `"/dash/home" in app.url_map` — requiring, on every box, the very route whose absence
    it was written to describe. MEASURED, not reasoned: exported a `customer_voice` box with
    `scripts/export_box.sh` and ran this suite inside it, where it FAILED on its own label.

    CI would never have caught that. `test_recipe_ships` builds a LEAD box, which carries the lead
    machine and therefore serves `/dash/home`, so the suite passes there and fails only in the box
    we actually sell.
    """
    from core import dash as _dash
    ok("core's own home heads the list", _dash._LANDINGS[0] == "/dashboard",
       str(_dash._LANDINGS))

    # NOT MERELY "IT SAYS /dashboard". A path is a string; what makes it the right answer is WHO
    # SERVES IT, and `/dash/home` read as core-ish for weeks while belonging to a machine. So the
    # head is checked against core's own module — if someone moves `/dashboard` out to a machine,
    # this goes red even though the tuple still reads correctly.
    src = (pathlib.Path(__file__).resolve().parents[1] / "core" / "dash" / "home.py").read_text()
    ok("...and it is core/dash/home.py that registers that route",
       f'"{_dash._LANDINGS[0]}"' in src,
       "the head of _LANDINGS is not a route core itself serves")

    def lands_on(served):
        """`landing()`'s own rule — the first entry this box serves — without needing a box."""
        return next((p for p in _dash._LANDINGS if p in served), None)

    every = set(_dash._LANDINGS)                          # carries every machine we ship
    inbox_box = every - {"/dash/home"}                    # a buyer's: no lead machine
    lead_box = every - {"/inbox/inbox", "/inbox/"}        # the other buyer's: no inbox
    ok("an everything-box opens on the dashboard", lands_on(every) == "/dashboard",
       str(lands_on(every)))
    ok("...so does a box with no lead machine", lands_on(inbox_box) == "/dashboard",
       str(lands_on(inbox_box)))
    ok("...and so does a lead-only box, which used to open on its machine's own funnel",
       lands_on(lead_box) == "/dashboard", str(lands_on(lead_box)))
    ok("...none of which depends on which machines THIS box happens to carry",
       lands_on(set()) is None)


def test_the_suite_is_named_in_ci():
    here = pathlib.Path(__file__).resolve().parents[1]
    if not (here / ".github").is_dir():
        print("  --   not the repo — a buyer's box has no CI manifest to be named in")
        return
    wf = (here / ".github/workflows/tests.yml").read_text()
    ok("this suite runs in CI", "test_the_dashboard_reads \\" in wf)


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(f"\n{name}")
            fn()
    print("\n" + ("FAILED" if _failed else "PASS") + f" — {_failed} failure(s)")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
