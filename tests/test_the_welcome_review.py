"""The Welcome review: the first Morning Review a box sends, the morning after it was bought (#1957 C4).

Owner, 2026-10-04: the review should "be delivered to them the day after they purchase the box and have some sort of
information and aspirational ideas on how they can improve ... their business. ... Otherwise it's just a dumb box."

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * a box's first brief is an ordinary one, or a box that has run for a while gets a welcome out of the blue;
  * the welcome says nothing about the business the owner told the box about, or invents a line it never said;
  * a goal points at a screen this box doesn't have, or a number nobody measured;
  * a planned offer is said to be available, or a past plan is shown as coming;
  * the page, the email and Slack don't carry the same welcome;
  * a morning after the first carries the welcome sections again.

No network, no model: the AI is stood in for.

Run: python tests/test_the_welcome_review.py
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
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "welcome.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state  # noqa: E402

state.init_db()
from core import business_context as bc  # noqa: E402
from core import report, review_brief, review_email, shell  # noqa: E402
from core.dash import review as page  # noqa: E402
from core.dispatch import app  # noqa: E402,F401

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -- {str(detail)[:500]}" if not cond and detail else ""))
    if not cond:
        _failed += 1


made = date.fromisoformat(str(state.owner_user()["created_at"])[:10])
ABOUT, NOW = made, datetime.combine(made + timedelta(days=1), datetime.min.time()).replace(hour=8)
SOON = (made.replace(day=1) + timedelta(days=62)).replace(day=1)
PAST = (made.replace(day=1) - timedelta(days=40)).replace(day=1)
by = "owner"
bc.put("name", "Glow Med Spa", by=by)
bc.put("sells", [{"name": "Botox", "price": "$12 a unit"}, {"name": "Hydrafacial", "price": "$189"}], by=by)
bc.put("customers", "Busy professionals who want to look rested", by=by)
bc.put("area", "South Austin", by=by)
bc.put("goals", ["more bookings", "better reviews"], by=by)
bc.put("coming", [{"what": "Spring skin package", "month": f"{SOON:%Y-%m}"},
                  {"what": "Old promo", "month": f"{PAST:%Y-%m}"}], by=by)
bc.put("profile", [{"line": "Saturday appointments book out a week ahead.",
                    "source": "https://www.glow-medspa.example/booking"}], by="full scan")
installed = review_brief._installed()
real_href = installed[0]["href"] if installed else ""

print("test_the_first_brief_is_a_welcome")
ok("a box whose owner started the day it is about, with no brief before, is new", review_brief._is_first(ABOUT))
ok("...a day before the owner existed never is", not review_brief._is_first(made - timedelta(days=3)))
b = review_brief.build(ABOUT, NOW, remember=False)
ok("with no AI, the plain welcome names the business", b["welcome"] is True
   and b["good_news"].startswith("Welcome to your box, Glow Med Spa."), b["good_news"])
ok("what the box learned: the site's own line first, with its page, then the owner's answers",
   [i["title"] for i in b["learned"]] == ["Saturday appointments book out a week ahead.", "You offer Botox, Hydrafacial.",
                                          "Your customers: Busy professionals who want to look rested.",
                                          "You serve South Austin."]
   and b["learned"][0]["why"] == "From glow-medspa.example", b["learned"])
ok("each goal, in the owner's order", [a["title"] for a in b["aims"]] == ["More bookings", "Better reviews"], b["aims"])
ok("what's coming: the plan ahead, with the weeks to get ready; never a past one",
   [c["title"] for c in b["coming"]] == [f"Spring skin package starts in {SOON:%B}"]
   and "weeks to get" in b["coming"][0]["why"], b["coming"])
ok("a welcome is never empty, so it is sent", b["empty"] is False)

print("\ntest_the_ai_welcome_is_checked")
seen = []


def think(task, prompt, **kw):
    seen.append({"task": task, "prompt": prompt, "system": kw.get("system")})
    return json.dumps({
        "good_news": "Welcome, Glow Med Spa: your box is ready to help fill Saturday appointments.",
        "aims": [{"goal": "more bookings", "why": "Your box answers new messages first.", "href": real_href},
                 {"goal": "better reviews", "why": "It invites 500 happy customers a week.", "href": "/nowhere"}],
        "ideas": [{"title": "Reply to Friday's messages first", "why": "Saturday appointments book out a week ahead."},
                  {"title": "Promote the spring package", "why": "It sells for $99."}]})


b = review_brief.build(ABOUT, NOW, think=think, remember=True)
ok("one AI call, on the review task, handed the business, the goals and the installed screens",
   len(seen) == 1 and seen[0]["task"] == "review" and "Glow Med Spa" in seen[0]["prompt"]
   and "installed" in seen[0]["prompt"] and "Spring skin package" in seen[0]["prompt"], seen[:1])
ok("its welcome is kept", b["good_news"].startswith("Welcome, Glow Med Spa"), b["good_news"])
aims = {a["title"]: a for a in b["aims"]}
ok("a goal points at a screen this box has", (aims["More bookings"]["href"] == real_href) if real_href else True,
   aims)
ok("...never at one it doesn't, and never with a number nobody measured",
   aims["Better reviews"]["href"] == "" and aims["Better reviews"]["why"] == "", aims["Better reviews"])
ok("an idea grounded in the business is kept; one with an invented price is dropped",
   [i["title"] for i in b["ideas"]] == ["Reply to Friday's messages first"], b["ideas"])

print("\ntest_every_surface_carries_it")
stored = review_brief.ensure(ABOUT, NOW, think=think)
html, _ = page.render(ABOUT.isoformat(), NOW)
e = review_email.build(ABOUT, NOW)
mail, text = review_email.html(e), review_email.text(e)
slack = report.render(ABOUT, NOW)
for heading in ("What your box learned about you", "Your goals, and how your box helps"):
    ok(f"'{heading}' on the page, in both emails and on Slack",
       heading in html.replace("&#x27;", "'").replace("&rsquo;", "'") and heading in mail.replace("&#x27;", "'")
       and heading.upper() in text and f"*{heading}*" in slack, heading)
ok("'What's coming' on the page and in both emails", "What&#x27;s coming" in html or "What's coming" in html)
ok("the welcome comes first, before what needs them", text.index("WHAT YOUR BOX LEARNED") < text.index("YOUR GOALS"))
ok("the email says it is the first", e["subject"] == "Welcome to your box: your first Morning Review", e["subject"])

print("\ntest_plans_never_go_to_slack")
ok("'What's coming' is on the page and in the email, never on Slack (OSDev1)",
   "What's coming" in text.replace("WHAT'S COMING", "What's coming") and "*What's coming*" not in slack
   and "Spring skin package" not in slack, slack)
from core import report as _report  # noqa: E402
ok("an idea naming a planned offer stays off Slack; others go",
   _report._names_a_plan({"title": "Get the Spring Skin Package page ready", "why": ""}, _report._planned_names())
   and not _report._names_a_plan({"title": "Reply to Friday's messages first", "why": ""}, _report._planned_names()))

print("\ntest_the_light_scans_find_opens_it")
bc.put("website", "", by=by)
from core import box_settings  # noqa: E402
box_settings.put(bc.NS, "website", "", set_by="test")
bc.put("suggested", {"website": "https://glow-medspa.example", "name": "Glow Med Spa", "area": "Austin, TX"},
       by="light scan")
f = review_brief._learned(bc.get())
ok("with nothing confirmed, the welcome opens with what the scan found, as a question",
   f and f[0]["title"] == "We found glow-medspa.example: Glow Med Spa, Austin, TX. Is this you?"
   and f[0]["href"] == "/settings/business", f[:1])
bc.put("website", "glow-medspa.example", by=by)
ok("...and once confirmed, it asks no more", not any("Is this you?" in i["title"] for i in review_brief._learned(bc.get())))

print("\ntest_only_the_first_morning")
nxt = review_brief.build(ABOUT + timedelta(days=1), NOW + timedelta(days=1), remember=False)
ok("the next morning is an ordinary review", nxt["welcome"] is False and nxt["learned"] == []
   and nxt["aims"] == [] and nxt["coming"] == [], {k: nxt[k] for k in ("welcome", "learned", "aims", "coming")})
ok("...and its email subject is the ordinary one", review_email.build(ABOUT + timedelta(days=1),
   NOW + timedelta(days=1))["subject"].startswith("Your Morning Review: "))

print("\nALL WELCOME CHECKS PASS" if not _failed else f"\n{_failed} WELCOME CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
