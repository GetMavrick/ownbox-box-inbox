"""Each person can hide a section of their Morning Review, on the page and in their email (#1953 Phase 1, step 1.6).

Owner, 2026-10-04: "Please ensure that the morning review has the capability for per user controls, where they can
change what they want to see." The first of those controls: any section can be hidden by the person looking, stored
for them alone; their email leaves it out too (OSDev1's recommendation), and "See everything" stays at the foot of both.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * a section has no Hide, or the Hide is smaller than a thumb (48px);
  * a hidden section still shows, or comes back on its own, or hides for someone else;
  * there is no way back: no "See everything" for one look, no "Show again" for good;
  * the email to that person still carries what they hid, or loses its "See everything";
  * Slack, which everyone shares, follows one person's choice;
  * someone not signed in, a section that doesn't exist, or a link off the review can change anything.

No network, no model.

Run: python tests/test_the_review_hides_what_you_hide.py
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
from datetime import date, datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "review_hide.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state  # noqa: E402

state.init_db()
from core import box_settings, dash, report, review_brief, review_email  # noqa: E402
from core.dash import review as page  # noqa: E402
from core.dispatch import app  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -- {str(detail)[:500]}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def row(day, machine, title, **parts):
    with state.connect() as c:
        c.execute("INSERT OR REPLACE INTO daily_reports (day, machine, report_json, written_at, final) "
                  "VALUES (?,?,?,?,1)", (day.isoformat(), machine,
                                         json.dumps({"machine": machine, "title": title, **parts}), state._now()))


T = date.today()
ABOUT = T - timedelta(days=1)
NOW = datetime.combine(T, datetime.min.time()).replace(hour=8)
row(ABOUT, "gtm", "Lead Machine", happened=[{"text": "emails sent", "value": 23}])
row(T, "gtm", "Lead Machine", needs_you=[{"text": "3 replies to read", "href": "/gtm"}])
review_brief.ensure(ABOUT, NOW)
OWNER = str(state.owner_user()["id"])
MEMBER = str(state.add_user("sam@example-spa.com", name="Sam", role="member")["id"])
BACK = f"/app/review/{ABOUT.isoformat()}"
o = app.test_client()
o.set_cookie(dash.COOKIE, dash.new_session(OWNER))


def html_for(viewer, show_all=False):
    return page.render(ABOUT.isoformat(), NOW, viewer=viewer, show_all=show_all)[0]


print("test_every_section_can_be_hidden")
h = html_for(OWNER)
ok("both sections are there, each with its own Hide", "Worth your time today" in h and "Already moving" in h
   and h.count('action="/app/review/hide"') == 2 and 'name="section" value="moving"' in h, h[-2500:])
ok("the Hide is a 48px target", ".mr-hide button{min-height:48px" in h)
ok("someone not signed in (a token) sees no controls", 'action="/app/review/hide"' not in html_for(None))

print("\ntest_hiding_is_theirs_and_it_stays")
r = o.post("/app/review/hide", data={"section": "moving", "back": BACK})
ok("Hide goes back to the page they were on", r.status_code == 303 and r.headers["Location"].endswith(BACK),
   (r.status_code, r.headers.get("Location")))
ok("stored for them alone", review_brief.hidden_for(OWNER) == {"moving"} and review_brief.hidden_for(MEMBER) == set())
h = html_for(OWNER)
ok("the section is gone from their page", "Already moving" not in h and "Worth your time today" in h)
ok("...and the foot says so, with See everything",
   "You hid 1 section, and 1 had something today" in h and f'href="{BACK}?all=1">See everything' in h, h[-800:])
ok("someone else still sees it", "Already moving" in html_for(MEMBER))
every = html_for(OWNER, show_all=True)
ok("See everything shows it for one look, with Show again", "Already moving" in every and "Show again" in every
   and "You hid" not in every and "Already moving" not in html_for(OWNER))

print("\ntest_their_email_follows")
e = review_email.build(ABOUT, NOW, user_id=OWNER)
text, mail = review_email.text(e), review_email.html(e)
ok("the email to them leaves it out", "ALREADY MOVING" not in text and "Already moving" not in mail
   and "WORTH YOUR TIME" in text, text)
ok("...and says how to see everything", "You hid 1 section of your Morning Review. See everything: " in text
   and text.rstrip().endswith("?all=1") and "?all=1" in mail and "See everything</a>" in mail, text[-300:])
plain = review_email.text(review_email.build(ABOUT, NOW))
ok("an email for nobody in particular is whole", "ALREADY MOVING" in plain and "See everything" not in plain)
sent = []
report._email("o@example.com", ABOUT, NOW, T, send_email=lambda *a, **k: sent.append(a), user_id=OWNER)
ok("the morning send applies the owner's own choice", sent and "ALREADY MOVING" not in sent[0][2], sent[:1])
ok("Slack, which everyone shares, is unchanged", "Lead Machine" in report.render(ABOUT, NOW))

print("\ntest_show_again")
r = o.post("/app/review/hide", data={"section": "moving", "show": "1", "back": BACK})
ok("Show again brings it back for good", r.status_code == 303 and review_brief.hidden_for(OWNER) == set()
   and "Already moving" in html_for(OWNER))

print("\ntest_nobody_else_can_change_it")
ok("a section that doesn't exist is refused", o.post("/app/review/hide", data={"section": "nope"}).status_code == 400
   and review_brief.hidden_for(OWNER) == set())
r = o.post("/app/review/hide", data={"section": "ideas", "back": "https://elsewhere.example/x"})
ok("a link off the review is never followed", r.status_code == 303 and r.headers["Location"].endswith("/app/review"),
   r.headers.get("Location"))
review_brief.set_hidden(OWNER, "ideas", False)
anon = app.test_client()
r = anon.post("/app/review/hide", data={"section": "worth"})
ok("someone not signed in changes nothing", r.status_code in (302, 303, 401, 403, 404)
   and review_brief.hidden_for(OWNER) == set(), r.status_code)
ok("every section the email has can be hidden, and nothing else",
   set(review_brief.section_keys()) == {k for k, _ in review_email.SECTIONS})

print("\nALL REVIEW-HIDE CHECKS PASS" if not _failed else f"\n{_failed} REVIEW-HIDE CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
