"""Today — the first screen a customer ever sees, and the one row he most needs to tap.

TWO DEFECTS, BOTH FOUND BY RENDERING THE PAGE AND READING IT.

1. A BOX NOTHING CAN REACH GREETED ITS OWNER WITH A WEBSITE UPTIME REPORT. Rendered on a bare box
   (2026-09-16): a dash for the headline, "— of checks answered today", "0 checks on your site",
   and two rails asking for a web address. Not one word about messages, on a box sold as a unified
   inbox, on the screen the app opens on. The report was not wrong — it was not what this person
   came for, and it was the first thing he read after paying.

2. THE ONE INSTRUCTION ON THAT SCREEN WAS NOT A LINK. `report()` writes "3 conversations are
   waiting on your reply" into `needs_you` WITH `href: /inbox/inbox`, and `_rows()` rendered `text`
   and `value` only — so the row a person most needs to act on was the row he could not tap.

WHAT THIS FILE HOLDS. That a first run leads with the set-up and promises no poll that will never
find anything; that nothing the report produced is DELETED by the re-order, only moved below it;
that an href becomes a real link resolved against the live url_map, and that an href this box does
not serve degrades to the plain row it was rather than to a 404 in somebody's first five minutes.

Run: python tests/test_today_first_screen.py
"""
import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB = pathlib.Path(tempfile.mkdtemp(prefix="today-first-")) / "box.db"
# BEFORE ANY core IMPORT — core.config.Settings reads os.environ at class-body time.
os.environ["AIOS_DB_PATH"] = str(_DB)
os.environ.setdefault("DASH_TOKEN", "test-dash-pw")

_failed = 0


def ok(what: str, cond: bool, got: str = "") -> None:
    global _failed
    if cond:
        print(f"  ok   {what}")
    else:
        _failed += 1
        print(f"  FAIL {what}" + (f"  — {got}" if got else ""))


def _signed_in():
    from core.config import settings
    from core.dispatch import app
    c = app.test_client()
    c.post("/dash/login", data={"token": settings.dash_token})
    return app, c


def _text(html_: str) -> str:
    """What a person READS. Markup hides a duplicate and shows a sentence that is not there."""
    import html as _h
    stripped = re.sub(r"(?s)<(script|style|svg).*?</\1>", " ", html_)
    return " ".join(_h.unescape(re.sub(r"<[^>]+>", " ", stripped)).split())


# A REAL BUYER BOX ON DAY ONE: no channel connected, and no rail named either. Both halves matter —
# the rails half is what made the old screen a website report, and stubbing only the channels would
# have tested this on the developer's own box, which has rails.
_NO_RAILS = {"headline": {"value": 0, "label": "rails set up"},
             "needs_you": [], "figures": {}, "happened": [],
             "watch": [{"text": "Nothing set up yet — say which of these this business has",
                        "state": "connect"}]}


def _with(*, listening: bool, report, path: str = "/inbox/"):
    """Render Today on a box we have fully described, and hand back (app, html)."""
    from core import box_secrets, spaces
    import marketing.customer_voice.report as rep
    keep = (box_secrets.email_credential, spaces.all_spaces, rep.report)
    try:
        box_secrets.email_credential = (lambda: {"user": "a@b.c", "password": "x"}) if listening \
            else (lambda: {})
        spaces.all_spaces = lambda: [{"name": "default"}]
        rep.report = report if callable(report) else (lambda day, r=report: dict(r, title="Today"))
        app, c = _signed_in()
        return app, c.get(path).get_data(as_text=True)
    finally:
        box_secrets.email_credential, spaces.all_spaces, rep.report = keep


def test_a_box_nothing_can_reach_opens_on_the_set_up_not_a_report():
    """The screen he lands on after paying says what this box is for and what to do about it."""
    app, body = _with(listening=False, report=_NO_RAILS)
    words = _text(body)
    ok("it leads with the box being up, not with a count of rails",
       "Your box is running." in words)
    ok("...and says plainly that nothing is connected",
       "Nothing is connected to it yet" in words)
    # NOT A SCORE. The old screen's headline was the rails count — which rendered as an em-dash,
    # because `0 or "—"` is an em-dash, so his first screen led with a shrug and a caption.
    ok("...and never opens on a rails score", "rails set up" not in words, words[:160])

    # THE PROMISE THE EMPTY INBOX WAS JUST FIXED FOR MAKING. "The rails you own are being polled"
    # is reassurance for a box that IS listening; on one with nothing connected it promises a poll
    # that cannot find anything.
    ok("...and promises no poll that will never find anything",
       "are being polled" not in words, words[:200])

    # "Every figure here is read from your own rails", printed under a screen with no figure, is
    # the same small untruth in caption form.
    ok("...and claims no figures under a screen carrying none",
       "Every figure here" not in words)


def test_the_one_thing_he_can_do_is_offered_and_cannot_404():
    app, body = _with(listening=False, report=_NO_RAILS)
    ok("there is exactly one button", body.count('class="btn"') == 1, str(body.count('class="btn"')))
    routes = {str(r) for r in app.url_map.iter_rules()}
    targets = re.findall(r'class="btn" href="([^"?#]+)', body)
    ok("...pointing only at a route THIS box serves",
       targets and all(t in routes for t in targets), f"{targets} vs the url_map")
    ok("...and it says what he gets, not just what to press",
       "What lands here once you do" in _text(body))


def test_the_re_order_deletes_nothing_the_report_produced():
    """A buyer with a website but no channel still has a website. The set-up goes ABOVE the report,
    it does not replace it — the regression that would make this fix worse than the defect."""
    r = dict(_NO_RAILS, happened=[{"text": "checks on your site", "value": 96}],
             figures={"up": {"value": "100%", "label": "good checks today"}})
    app, body = _with(listening=False, report=r)
    words = _text(body)
    # `.find`, not `.index`: a missing substring RAISES, and a raise here ends the whole file —
    # it did, on the probe, and four later tests silently never ran.
    i, j = words.find("Your box is running"), words.find("96")
    ok("the set-up is still first", 0 <= i < j, f"at {i} vs {j}")
    ok("...and his real figure is still on the page", "100%" in words and "96" in words)
    ok("...and the footer comes back once there are figures to explain",
       "Every figure here" in words)


def test_a_listening_box_gets_its_report_untouched():
    r = {"headline": {"value": 3, "label": "waiting on you"},
         "needs_you": [], "figures": {"w": {"value": 3, "label": "waiting on you"}},
         "happened": [{"text": "messages came in", "value": 11}], "watch": []}
    app, body = _with(listening=True, report=r)
    words = _text(body)
    ok("a box that IS listening still opens on its report", "waiting on you" in words)
    ok("...and is never told to connect something it already has",
       "Nothing is connected to it yet" not in words)


def test_the_row_he_must_act_on_is_a_link():
    """`needs_you` carries an href and it was being dropped. This is the whole point of the row."""
    r = dict(_NO_RAILS, needs_you=[{"text": "3 conversations are waiting on your reply",
                                    "href": "/inbox/inbox"}])
    app, body = _with(listening=True, report=r)
    ok("the waiting row is an anchor, not a div",
       '<a class="row go" href="/inbox/inbox">' in body,
       re.findall(r'<(?:a|div) class="row[^"]*"[^>]*>', body)[:3])
    ok("...and it still reads exactly as the report wrote it",
       "3 conversations are waiting on your reply" in _text(body))


def test_an_href_this_box_does_not_serve_is_not_a_link():
    """THE REPORT IS A DIFFERENT MODULE and has no idea which pages this box serves — the same
    per-box fact `core.dash.landing()` exists for. An unserved path degrades to the plain row it
    was before, never to a 404 handed to somebody in their first five minutes."""
    r = dict(_NO_RAILS, needs_you=[{"text": "something happened", "href": "/inbox/not-a-page"}])
    app, body = _with(listening=True, report=r)
    ok("no anchor is drawn for a route this box lacks", "/inbox/not-a-page" not in body)
    ok("...and the row itself is still shown", "something happened" in _text(body))
    ok("...as a plain row", '<div class="row">' in body)


def test_a_row_that_goes_somewhere_looks_like_one_and_not_like_a_web_link():
    """Kinso's rule and this app's: the WHOLE row is the target, with a visible affordance. A bare
    `text-decoration:none` would leave it indistinguishable from the rows that go nowhere."""
    from marketing.customer_voice.app import CSS
    css = re.sub(r"(?s)/\*.*?\*/", " ", CSS)
    ok("the row link drops the browser's underline and link colour",
       "a.row{text-decoration:none;color:inherit}" in css.replace("\n", ""))
    ok("...and carries a chevron, so the affordance is seen and not discovered by tapping",
       "a.row::after" in css)
    ok("...and answers a finger", "a.row:active" in css)


def test_an_unreadable_report_on_a_bare_box_is_still_the_set_up():
    """The guarded read says "could not be read". True about the read — and as somebody's FIRST
    screen it reports a fault in a report he has no data for yet."""
    def boom(day):
        raise RuntimeError("no such table: daily_reports")

    app, body = _with(listening=False, report=boom)
    words = _text(body)
    ok("a bare box leads with the set-up even when the report cannot be read",
       "Your box is running." in words)
    # THE FAULT IS NOT DELETED, IT IS DEMOTED. My first version replaced the sentence outright
    # and CI refused it — rightly: a screen that swallows a real fault because the buyer has
    # nothing connected yet is how a broken box looks fine to everybody. It is below the set-up
    # now, and it is not the headline.
    i, j = words.find("Your box is running"), words.find("could not be read")
    ok("...and still says the report could not be read", j > 0, words[:200])
    ok("...below the set-up, not as his first sentence", 0 <= i < j, f"at {i} vs {j}")

    # AND THE GUARD IS STILL A GUARD on a box that is listening: there the sentence is the right
    # one, and it must never become a 500 or a stack trace.
    from core.config import settings                                           # noqa: F401
    app2, b2 = _with(listening=True, report=boom)
    ok("a listening box still gets the honest sentence", "could not be read" in _text(b2))
    ok("...and never a stack trace", "Traceback" not in b2)


def test_ci_actually_runs_this_file():
    wf = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml"
    me = pathlib.Path(__file__).stem
    if not wf.is_file():
        # A SOLD BOX HAS NO CI AND THIS SUITE SHIPS INTO ONE. The read used to raise
        # FileNotFoundError and take the whole file down with it, so a buyer running their own
        # suites watched this one crash.
        #
        # Reported, not asserted, and deliberately so. The hazard this guards is a HAND-MAINTAINED
        # list in tests.yml drifting away from a filename. In a box there is no list, so there is
        # nothing that could have drifted — the box runs whatever is in tests/. Writing an ok()
        # here would mean inventing a condition that is true by construction, which is the shape
        # of a check that proves nothing. The line says why it did not run instead.
        print(f"  --   no workflow here — this box is not the repo, so {me} has no list to be "
              "missing from")
        return
    ok(f"{me} is in the workflow's suite list", me in wf.read_text(),
       "CI would skip this file and still print green")


if __name__ == "__main__":
    for fn in (test_a_box_nothing_can_reach_opens_on_the_set_up_not_a_report,
               test_the_one_thing_he_can_do_is_offered_and_cannot_404,
               test_the_re_order_deletes_nothing_the_report_produced,
               test_a_listening_box_gets_its_report_untouched,
               test_the_row_he_must_act_on_is_a_link,
               test_an_href_this_box_does_not_serve_is_not_a_link,
               test_a_row_that_goes_somewhere_looks_like_one_and_not_like_a_web_link,
               test_an_unreadable_report_on_a_bare_box_is_still_the_set_up,
               test_ci_actually_runs_this_file):
        print(fn.__name__)
        fn()
    print(f"{_failed} FAILED" if _failed else "all ok")
    sys.exit(1 if _failed else 0)
