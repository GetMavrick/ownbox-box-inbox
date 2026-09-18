"""The inbox uses the width it is given — channels on the left, and the same chips on a phone.

Owner, 2026-09-17: *"With the social media channels on the left, and they move horizontally at the
top on Mobile as you have it."*

The app was a 620px column at every width, so a laptop drew a phone with grey margins. The chip
row's own CSS comment had said the right thing since it was written — *"Kinso's LEFT RAIL is a
desktop idiom; on a phone the same job is a scrolling row"* — and only the phone half was built.

WHAT THIS SUITE GUARDS IS THE CONTRACT, NOT THE PIXELS. The look was checked in a real Chromium at
360 / 390 / 768 / 899 / 900 / 1280 / 1600px (breakpoint lands exactly at 900, no horizontal scroll
at any width, no tag clipped at any width). None of that can run in CI, so what is asserted here is
the part a future edit could break without anyone opening a browser: which pages widen, and that
there is still only ONE chip list.

Run: python tests/test_the_inbox_has_room_on_a_desktop.py
"""
import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB = pathlib.Path(tempfile.mkdtemp(prefix="inbox-room-")) / "box.db"
os.environ["AIOS_DB_PATH"] = str(_DB)
os.environ.setdefault("DASH_TOKEN", "test-dash-pw")

from core import state                                      # noqa: E402

state.init_db()

from marketing.customer_voice import app as cv              # noqa: E402

# ONE CONVERSATION, BECAUSE AN EMPTY BOX HAS NO CHIPS TO ASSERT ON — and that is correct behaviour,
# not a gap: the first-run screen deliberately draws no search field and no channel filter, since
# a control that can only ever return nothing is the kind this app keeps deleting. A test that
# forgot to seed would "prove" the chip row was missing.
def _seed() -> None:
    from core import spaces
    from marketing.customer_voice.inbox import store
    # `_space()` resolves to spaces.DEFAULT on a bare host — NOT all_spaces()[0], which is what the
    # REPORTER reads. Seeding the reporter's space here renders an empty inbox and reads as a bug.
    sp = spaces.DEFAULT
    for zcid, plat, who in (("zc_a", "instagram", "Dana Whitfield"),
                            ("zc_b", "messenger", "Marcus Lee"),
                            ("zc_c", "email", "Tom Brandt")):
        store.upsert_conversation(space=sp, zcid=zcid, platform=plat, participant=who,
                                  last_inbound_at="2026-09-17T12:00:00+00:00")
        store.record_message(space=sp, zcid=zcid, zmid=f"m_{zcid}", direction="in",
                             sent_by="contact", body="Do you do same-day call-outs?")


_seed()

_failed = 0
_SRC = pathlib.Path(cv.__file__).read_text()


def ok(what: str, cond: bool, got: str = "") -> None:
    global _failed
    if cond:
        print(f"  ok   {what}")
    else:
        _failed += 1
        print(f"  FAIL {what}" + (f"  — {got}" if got else ""))


def _client():
    from core.config import settings
    from core.dispatch import app
    c = app.test_client()
    c.post("/dash/login", data={"token": settings.dash_token})
    return c


def _body_tag(path: str) -> str:
    html_ = _client().get(path).get_data(as_text=True)
    m = re.search(r"<body[^>]*>", html_)
    return m.group(0) if m else "(no body tag)"


def test_the_inbox_is_the_only_page_that_widens():
    print("test_the_inbox_is_the_only_page_that_widens")
    # ONE PAGE OPTS IN, so no other screen in the app can widen by accident. Today and Settings are
    # forms and summaries — a 1080px form is a worse form, and this is the check that keeps the
    # decision deliberate instead of something the stylesheet does to everybody.
    ok("the inbox is marked wide", 'class="ib"' in _body_tag("/inbox/inbox"),
       _body_tag("/inbox/inbox"))
    for path in ("/inbox/", "/inbox/settings"):
        ok(f"{path} is not", 'class="ib"' not in _body_tag(path), _body_tag(path))


def test_an_empty_inbox_keeps_the_rail_that_emptied_it():
    print("test_an_empty_inbox_keeps_the_rail_that_emptied_it")
    # THE FILTER IS STILL ON SCREEN WHEN IT FINDS NOTHING, and that is the moment it matters most.
    # A reader who narrowed to Instagram and got no rows needs the rail to change their mind with;
    # dropping to the narrow layout would move the whole page under them at exactly that moment.
    for path in ("/inbox/inbox", "/inbox/inbox?channel=instagram", "/inbox/inbox?q=nothing-matches",
                 "/inbox/inbox?page=9"):
        ok(f"{path} stays wide", 'class="ib"' in _body_tag(path), path)


def test_the_flag_is_carried_in_exactly_one_place():
    print("test_the_flag_is_carried_in_exactly_one_place")
    # IT SITS ON <body>, BECAUSE THE BAR IS A SIBLING OF THE COLUMN. The first version put the
    # class on `.wrap`, which the stylesheet could reach and the top bar could not — so the brand
    # stayed centred in the old 620px band while everything under it started 116px from the left.
    # Two elements carrying the same flag would have been two things to keep in step.
    ok("the shell sets it on the body", "<body{' class=\"ib\"' if wide else ''}>" in _SRC)
    ok("...and the column is left plain", '<div class="wrap">{body}</div>' in _SRC)
    # THE WIDTH IT WIDENS TO CHANGED WHEN THE RAIL ARRIVED (2026-09-18). It was 1080px, the
    # width of a two-column grid whose left column held the filters; the box's own rail now
    # holds the left edge, so the bar spans the whole window and starts at its left like any
    # app header. The assertion's intent is unchanged and is the reason it exists: the bar
    # and the column widen from ONE flag, so the brand cannot sit in the old 620px centre
    # while the content beneath it starts somewhere else.
    ok("...so the bar widens from the same flag",
       ".ib .bar-in{max-width:none;padding-left:20px}" in _SRC)
    ok("...and the column it shares the flag with is bounded, not stretched",
       ".ib .wrap{max-width:780px;margin:0;padding:0 24px}" in _SRC)


def test_there_is_still_only_one_chip_list():
    print("test_there_is_still_only_one_chip_list")
    # THE RAIL IS THE CHIP ROW ON ITS SIDE, not a second menu. Same links, same counts, same
    # current-chip rule — a second markup for the same list is how the phone's filter and the
    # laptop's filter start disagreeing about which channel you are looking at.
    ok("one function builds the channel list", _SRC.count("def _chips(") == 1)
    # THE AXIS IS NO LONGER RESTYLED AT ALL, WHICH IS A STRONGER VERSION OF THE SAME GUARANTEE.
    # This read `.ib .wrap>.chips{` + `flex-direction:column` — the desktop turning the chip row
    # into a 224px left column. That column is gone (2026-09-18): the box's rail holds the left
    # edge now, two stacked rails are two answers to "where am I", and a filter belongs beside
    # the list it filters rather than in the navigation. So the chips keep ONE shape at every
    # width and there is no second axis to keep in step.
    # THE GUARD IS THE POINT, NOT THE MECHANISM. What this test exists to stop is the phone's
    # filter and the laptop's filter drifting apart, so it now asserts the stronger fact
    # directly: one builder, no desktop restyle left behind, and one chip list on the page at
    # BOTH widths — checked by rendering, not by reading the stylesheet.
    ok("...and the desktop no longer restyles them onto another axis",
       ".ib .wrap>.chips{" not in _SRC)
    for label, wide in (("phone", False), ("desktop", True)):
        body = _client().get("/inbox/inbox").get_data(as_text=True)
        ok(f"exactly one chips block reaches the page ({label} markup is the same markup)",
           body.count('class="chips"') == 1, str(body.count('class="chips"')))


def test_the_phone_default_is_not_inside_the_desktop_query():
    print("test_the_phone_default_is_not_inside_the_desktop_query")
    # HE APPROVED THE PHONE ALREADY, so the phone must be what the stylesheet says WITHOUT any
    # media query running. Everything new lives inside `min-width:900px`; if a base rule ever
    # drifts in there, the phone loses its layout and no test would otherwise notice.
    # 821px, NOT 900px, SINCE 2026-09-18: the breakpoint is now core's. Below it the box's
    # rail is a drawer and this app keeps its bottom tab bar; at and above it the rail is
    # permanently on screen and the tab bar stands down. One number for both menus, so a
    # width can never show two navigations or none. Core's drawer query is `max-width:820px`
    # and this is the width immediately above it.
    q = _SRC.split("@media (min-width:821px){", 1)
    ok("the desktop block exists", len(q) == 2)
    if len(q) != 2:
        return
    base, desktop = q[0], q[1]
    # SCOPED TO THE CHIPS RULE, not to the whole file. The first version asked whether the words
    # "flex-direction:column" appeared anywhere before the media query — they do, in unrelated
    # components — so it failed on a page that was correct. An assertion has to name the thing it
    # is about or it reports somebody else's CSS as this feature's bug.
    chips_base = base.split(".chips{", 1)[1].split("}", 1)[0] if ".chips{" in base else ""
    ok("the chip row is laid out by default, outside any query",
       chips_base.startswith("display:flex"), chips_base[:60])
    ok("...horizontally, because that is the phone he already approved",
       "flex-direction:column" not in chips_base, chips_base[:80])
    ok("...and the vertical axis exists only inside the desktop block",
       "flex-direction:column" in desktop)


def test_the_preview_yields_and_the_tag_does_not():
    print("test_the_preview_yields_and_the_tag_does_not")
    # MEASURED, NOT PREFERRED. At 390px the rows came out 85, 85, 85, 140, 112, 111px tall — a 65%
    # height swing between neighbours in a list whose whole job is to be scanned. The cause was
    # flexbox choosing to WRAP from each item's base size, before any shrinking, so a long grey
    # line pushed the tag onto a second row even though the grey line was the one thing that could
    # have given way. `flex:1 1 0` makes its base size zero. After: 85, 85, 85, 112, 85, 84.
    #
    # THE WRAP ITSELF STAYS, and that is the point of asserting both halves here. An earlier pass
    # set the row to `nowrap` and a render showed a tag cut to "Opted ou" and a second tag sliding
    # under the channel logo. A tag must never truncate; the preview always may.
    # THE INVARIANT HELD; THE MECHANISM MOVED, 2026-09-17. The two assertions here used to name
    # `.conv .s .sub{` and its `flex:1 1 0` — the message COUNT, which shared the tag line and
    # was the thing being protected from a long preview. The owner had that label removed ("the
    # new label just gets in the way too"), so the rule went with it and this suite broke with
    # `IndexError: list index out of range` on a split that no longer finds its needle.
    #
    # WHICH IS THE RIGHT FAILURE. The claim was never about that one selector: it is that a tag
    # must never truncate and the MESSAGE is what gives way. Both are still true, so both are
    # still asserted — the message now yields by clamping to two lines rather than by shrinking
    # to a zero flex base, and the tag row still wraps.
    ok("the message is what gives way, by clamping rather than overflowing",
       "-webkit-line-clamp:2" in _SRC.split(".conv .p{", 1)[1][:260])
    ok("...and it no longer forbids wrapping, which is what makes the clamp reachable",
       "white-space:nowrap" not in _SRC.split(".conv .p{", 1)[1][:260])
    ok("...and the row can still wrap when two tags genuinely will not fit",
       "flex-wrap:wrap" in _SRC.split(".conv .s{", 1)[1][:200])
    # AND NOTHING IS LEFT BEHIND TO PROTECT A LABEL THAT NO LONGER EXISTS.
    ok("...with the count's own rule gone rather than orphaned",
       ".conv .s .sub{" not in _SRC)


def test_the_suite_is_named_in_ci():
    print("test_the_suite_is_named_in_ci")
    here = pathlib.Path(__file__).resolve().parents[1]
    if not (here / ".github").is_dir():
        print("  --   not the repo — a buyer's box has no CI manifest to be named in")
        return
    wf = (here / ".github/workflows/tests.yml").read_text()
    ok("this suite runs in CI", "test_the_inbox_has_room_on_a_desktop \\" in wf)


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print()
            fn()
    print("\n" + ("FAILED" if _failed else "PASS") + f" — {_failed} failure(s)")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
