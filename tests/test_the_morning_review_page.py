"""The Morning Review in the app, as the owner's light page (docs/SCOPE_MORNING_REVIEW_V2.md).

Owner, 2026-10-01, on the mock-up: "Light and optimistic. That's what we want. We want to motivate and inspire
people." And, of the report it replaces: "such a harsh looking report with those huge buttons." The page draws
core/review_brief.py's contract exactly, so this holds the page to:

  1. the day's quote on a quiet band, the good-news line, then Worth your time, Already moving and Ideas,
     numbered, with a title that links where there is somewhere to go and no button anywhere in it;
  2. no heading over an empty list, and one calm sentence when there is nothing at all;
  3. the menu's page is THIS MORNING's brief, about yesterday, the one the email sent; a day's own address is
     that day's;
  4. the old per-machine numbers one tap down, and only when there are numbers.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import timedelta

_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "page.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import state  # noqa: E402

state.init_db()
from core import dash, report  # noqa: E402
from core.dispatch import app  # noqa: E402

_failed = 0


def ok(label: str, cond: bool, detail: str = "") -> None:
    global _failed
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  — {detail}"))
    if not cond:
        _failed += 1


def store_brief(day, **over) -> dict:
    b = {"about": day.isoformat(), "date_label": "Thursday · October 1, 2026",
         "quote": "A new month, a clean slate, a bright start.",
         "good_news": "The Lead Machine found seven new companies while you slept.",
         "worth": [{"title": "21 people would love a reply", "why": "Starting with the longest wait helps most.",
                    "href": "/inbox/inbox", "machine": "Unified Inbox"},
                   {"title": "Two posts may need a quick look", "why": "Instagram didn't confirm them.",
                    "href": "", "machine": "Content"}],
         "moving": [{"title": "7 companies found", "why": "", "href": "", "machine": "Lead Machine"}],
         "ideas": [{"title": "Let the Inbox draft your replies", "why": "One switch.", "href": "", "machine": ""}],
         "ideas_from": "ai", "empty": False, "link": "", "built_at": state._now()}
    b.update(over)
    with state.connect() as c:
        c.execute("INSERT OR REPLACE INTO daily_reports (day, machine, report_json, written_at, final) "
                  "VALUES (?, 'brief', ?, ?, 1)", (day.isoformat(), json.dumps(b), state._now()))
    return b


def wipe() -> None:
    with state.connect() as c:
        c.execute("DELETE FROM daily_reports")


def brief_part(page: str) -> str:
    """The light page only: from its band to the fold, so the old numbers below it are not read as it."""
    start = page.find('<div class="mr">')
    ends = [i for i in (page.find('class="mr-days"'), page.find('class="mr-more"')) if i > start]
    return page[start:min(ends) if ends else len(page)]


owner = app.test_client()
owner.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
TODAY = report.today()
Y = TODAY - timedelta(days=1)

print("\ntest_the_page_is_the_owners_light_page")
wipe()
store_brief(Y)
r = owner.get("/app/review")
page = r.get_data(as_text=True)
part = brief_part(page)
ok("the menu's Morning Review opens", r.status_code == 200, str(r.status_code))
ok("the day's quote leads, on its band", '<section class="mr-band"><p class="mr-quote">&ldquo;A new month, a clean slate, '
   'a bright start.&rdquo;</p>' in part, part[:300])
ok("...with the morning it is read under the title", "Thursday · October 1, 2026" in page)
ok("the good news follows the quote", part.index("mr-quote") < part.index("found seven new companies"))
ok("then Worth your time today, Already moving and Advice for today, in that order",
   0 < part.index("Worth your time today") < part.index("Already moving") < part.index("Advice for today"))
ok("each list is numbered from 01", part.count('<span class="mr-n">01</span>') == 3
   and '<span class="mr-n">02</span>' in part)
ok("a title links where there is somewhere to go", '<a class="mr-t" href="/inbox/inbox">21 people would love a reply</a>'
   in part)
ok("...and is plain words where there is not", '<span class="mr-t">Two posts may need a quick look</span>' in part)
ok("what a machine did reads machine first", '<span class="mr-t">Lead Machine</span><p class="mr-w">7 companies found</p>'
   in part)
ok("the AI's ideas say where they came from", "The advice comes from its AI" in part)
# THE ONLY BUTTON IS EACH SECTION'S QUIET "Hide" (#1953 step 1.6): a grey word with no fill and no border, because hiding
# changes a stored choice and so is a form. Anything else that looks like a button is the "huge buttons" he retired.
_quiet = re.sub(r'<form class="mr-hide".*?</form>', "", part, flags=re.S)
ok("NO BUTTON anywhere in it (owner: \"those huge buttons\"), only each section's quiet Hide",
   "<button" not in _quiet and "ui-btn" not in part and "border:0;background:none" in page)

print("\ntest_nothing_empty_is_drawn")
wipe()
store_brief(Y, moving=[], ideas=[], ideas_from="")
part = brief_part(owner.get("/app/review").get_data(as_text=True))
ok("a list with nothing in it has no heading", "Already moving" not in part and "Advice for today" not in part)
ok("...and no AI credit without AI ideas", "The advice comes from its AI" not in part)
wipe()
store_brief(Y, worth=[], moving=[], ideas=[], ideas_from="", good_news="", empty=True)
row = ("INSERT INTO daily_reports (day, machine, report_json, written_at, final) VALUES (?, 'lead_machine', ?, ?, 1)")
with state.connect() as c:                       # a box that has reported before, so this is a quiet morning
    c.execute(row, (Y.isoformat(), json.dumps({"machine": "lead_machine", "title": "Lead"}), state._now()))
part = brief_part(owner.get("/app/review").get_data(as_text=True))
ok("an empty morning is one calm sentence under the quote", '<p class="mr-quiet">' in part
   and "mr-sec" not in part and "mr-quote" in part, part[-400:])

print("\ntest_which_morning")
wipe()
store_brief(Y, quote="Yesterday's own quote.")
store_brief(TODAY, quote="A brief about a day still going on.")
page = owner.get("/app/review").get_data(as_text=True)
ok("the menu's page is THIS MORNING's brief, about yesterday: the one the email sent",
   "Yesterday&#x27;s own quote." in page or "Yesterday's own quote." in page)
ok("...never a brief about a day still going on", "still going on" not in page)
page = owner.get(f"/app/review/{Y.isoformat()}").get_data(as_text=True)
ok("a day's own address is that day's brief", "own quote." in page)
page = owner.get(f"/app/review/{TODAY.isoformat()}").get_data(as_text=True)
ok("today's own address is the menu's page", "own quote." in page and "still going on" not in page)

print("\ntest_the_numbers_are_one_tap_down")
wipe()
store_brief(Y)
page = owner.get("/app/review").get_data(as_text=True)
ok("with no numbers today there is no fold to open: an empty fold is a dead area", 'class="mr-more"' not in page)
with state.connect() as c:
    c.execute(row, (TODAY.isoformat(), json.dumps({"machine": "lead_machine", "title": "Lead",
                                                   "happened": [{"text": "companies found", "value": 3}]}),
                    state._now()))
page = owner.get("/app/review").get_data(as_text=True)
ok("with numbers, they are under the light page, closed", '<details class="mr-more"><summary>Today so far, in full'
   in page and page.index('<div class="mr">') < page.index('class="mr-more"'))
ok("a past day with nothing behind it is still a 404 that says so",
   owner.get("/app/review/2001-01-01").status_code == 404)

print("\ntest_a_past_day_without_a_review_says_so")
# OSDev1's review of #1782: /app/review/2026-09-21 showed TODAY's quote and today's "Worth your time" under
# that day's date, because a preview of a past day is built from today's decisions. Such a day says plainly
# that no review was written, and shows its numbers.
wipe()
OLD = TODAY - timedelta(days=9)
store_brief(Y, quote="This morning's quote, not that day's.")
with state.connect() as c:
    c.execute(row, (OLD.isoformat(), json.dumps({"machine": "lead_machine", "title": "Lead",
                                                 "happened": [{"text": "companies found", "value": 3}]}),
                    state._now()))
r = owner.get(f"/app/review/{OLD.isoformat()}")
page = r.get_data(as_text=True)
ok("it opens", r.status_code == 200, str(r.status_code))
ok("it says plainly that no review was written for that day", "No Morning Review was written for" in page)
ok("...and shows nothing of today's: not its quote, not its list",
   '<p class="mr-quote">' not in page and "This morning" not in page and "Worth your time today" not in page)
ok("...and that day's numbers are there to read", "companies found" in page)

print("\ntest_what_it_is_handed_is_escaped")
# A brief's words come from the reporters, from what people wrote to the box, and from an AI. None of it is
# markup. Escaping works today; this is what fails if it is ever removed (OSDev1's review of #1782).
wipe()
EVIL = '<script>alert(1)</script><img src=x onerror=alert(2)>'
store_brief(Y, quote="Quote " + EVIL, good_news="Good " + EVIL,
            worth=[{"title": "Title " + EVIL, "why": "Why " + EVIL, "href": "/inbox/inbox", "machine": "M " + EVIL},
                   {"title": "Off-site", "why": "", "href": "//evil.example/x", "machine": ""},
                   {"title": "Back-slash", "why": "", "href": "/\\evil.example", "machine": ""},
                   {"title": "Scheme", "why": "", "href": "javascript:alert(3)", "machine": ""}],
            moving=[{"title": "Did " + EVIL, "why": "", "href": "", "machine": "Machine " + EVIL}],
            ideas=[{"title": "Idea " + EVIL, "why": "From the AI " + EVIL, "href": "", "machine": ""}])
page = owner.get("/app/review").get_data(as_text=True)
part = brief_part(page)
ok("no markup from a brief reaches the page as markup", "<script>alert" not in page and "<img src=x" not in page,
   part[part.find("Quote"):][:200])
ok("...it is there as words", part.count("&lt;script&gt;alert(1)&lt;/script&gt;") >= 7, str(part.count("&lt;script&gt;")))
ok("a link never leaves the box: not //host, not /\\host, not a scheme",
   'href="//evil' not in page and 'href="/\\evil' not in page and 'href="javascript' not in page
   and '<span class="mr-t">Off-site</span>' in part and '<span class="mr-t">Scheme</span>' in part)
ok("...while a path on the box still links", '<a class="mr-t" href="/inbox/inbox">' in part)

print("\ntest_it_wears_the_box")
m = re.search(r"<style>\s*\.mr\{.*?</style>", page, re.S)
css = m.group(0) if m else ""
ok("its colours are the box's tokens, so dark mode is as light as light mode",
   css and not re.search(r"#[0-9a-fA-F]{3,8}\b", css), re.findall(r"#[0-9a-fA-F]{3,8}\b", css)[:3])
ok("its text sizes follow the reader's (calc on --px)",
   css and all("var(--px" in m for m in re.findall(r"font-size:[^;}]+", css)),
   [m for m in re.findall(r"font-size:[^;}]+", css) if "var(--px" not in m][:3])

print("\nall good" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
