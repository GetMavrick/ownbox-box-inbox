"""The mobile app page promises email only on a box that can send it.

Walk #7 (OSDev4, docs/JOURNEY_WALK_2026-09-23.md): `/settings/mobile` said *"Email keeps arriving
either way"* on a box that could not send email at all — a sold box ships with none until its owner
adds a way (`/settings/email`, #1473). This suite renders the page both ways and holds:
  1. with no email set up, it says so, and shows the owner (only) where to add it;
  2. with email set up, it says the app is the faster of two ways;
  3. neither version says "keeps arriving either way".
It also holds the page's order from the 2026-09-24 walk: anything wrong first, the state before the
steps.

Run: python tests/test_the_mobile_page_promises_only_what_is_true.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "mobile.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
os.environ.pop("ANTHROPIC_API_KEY", None)

from core import state                                                # noqa: E402

state.init_db()

from core import box_mail, dash, push                                 # noqa: E402
from core.dispatch import app                                         # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def page(user_id, sending):
    real = box_mail.is_configured
    box_mail.is_configured = lambda: sending
    try:
        c = app.test_client()
        c.set_cookie(dash.COOKIE, dash.new_session(user_id))
        r = c.get("/settings/mobile")
        return r.status_code, r.get_data(as_text=True)
    finally:
        box_mail.is_configured = real


owner = state.owner_user()["id"]
member = state.add_user("sam@example.com", name="Sam")["id"]

print("\ntest_no_email_means_no_promise_of_email")
code, html = page(owner, False)
ok("the page answers", code == 200, str(code))
ok("it does not promise email that cannot come", "keeps arriving either way" not in html
   and "keeps working either way" not in html)
ok("...it says there is no email yet, so the app is how the box reaches you",
   "no email set up yet" in html)
ok("...and shows the owner where to add it", 'href="/settings/email"' in html)
_, mhtml = page(member, False)
ok("a member is told the same, without a door only the owner can open",
   "no email set up yet" in mhtml and 'href="/settings/email"' not in mhtml)

print("\ntest_email_set_up_is_said_as_the_second_way")
_, html2 = page(owner, True)
ok("with email set up, it says the reports also arrive by email", "also arrive by email" in html2)
ok("...and still never the old promise", "keeps arriving either way" not in html2)

print("\ntest_the_page_reads_in_the_order_a_person_needs_it")
stands, steps = html.find("Where this box stands"), html.find("On an iPhone")
ok("where the box stands comes before the install steps", -1 < stands < steps, f"{stands} {steps}")
real = push.available
push.available = lambda: (False, "the part of this box that sends notifications did not start")
try:
    _, broken = page(owner, False)
finally:
    push.available = real
first = broken.find("One thing first")
ok("a box that cannot notify says so before anything else on the page",
   -1 < first < broken.find("Where this box stands"), str(first))

print("\n" + ("all good" if not _failed else f"{_failed} FAILED"))
sys.exit(1 if _failed else 0)
