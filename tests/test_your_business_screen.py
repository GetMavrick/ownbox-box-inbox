"""System Settings → Your Business (#1957 C2): the owner tells the box what the business does, who its customers are
and how it plans to grow, on one mobile-first screen; every machine reads it from core/business_context.py.

Owner, 2026-10-04: "These people need to have a valuable machine on day one. Not a dumb box." and "we also should put
certain settings at the top of the base machine settings that are crucial to gather that business context."

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * the screen is not the first group in System Settings, or a member can change it;
  * an answer is stored anywhere but through business_context.put, or a bad one is stored instead of refused in words;
  * the light scan's suggestion fills a field without a yes, or its page text reaches the screen unescaped;
  * a field is under 16px or a choice under 48px on a mobile;
  * the home card shows after the owner answered or dismissed it, or to someone who isn't the owner.

Run: python tests/test_your_business_screen.py
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "your_business.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state  # noqa: E402

state.init_db()
from core import box_settings, dash  # noqa: E402
from core import business_context as bc  # noqa: E402
from core.dispatch import app  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -- {str(detail)[:400]}" if not cond and detail else ""))
    if not cond:
        _failed += 1


o = app.test_client()
o.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
m = app.test_client()
m.set_cookie(dash.COOKIE, dash.new_session(state.add_user("sam@example-medspa.com", name="Sam", role="member")["id"]))

print("test_where_it_lives")
settings = o.get("/settings").get_data(as_text=True)
ok("Your Business is in System Settings, first after the Overview",
   "/settings/business" in settings and settings.index("/settings/business") < settings.index("/settings/ai"),
   settings[:300])
home = o.get("/dashboard").get_data(as_text=True)
ok("the home screen asks the owner to tell the box about the business", "Tell your box about your business" in home)

print("\ntest_the_suggestion_is_a_question")
EVIL = '<script>alert(1)</script>'
bc.put("suggested", {"website": "https://glow-medspa.example", "name": "Glow Med Spa " + EVIL,
                     "area": "12 Main St, Austin, TX", "hours": "Mon 9-6"}, by="light scan")
page = o.get("/settings/business").get_data(as_text=True)
ok("the screen asks 'Is this your website?' with what the page said",
   "Is this your website?" in page and "glow-medspa.example" in page and "12 Main St, Austin, TX" in page, page[:600])
ok("...escaped: page text from the web never runs", EVIL not in page and "&lt;script&gt;" in page)
ok("...and the home card asks it too", "glow-medspa.example</b> your website?" in o.get("/dashboard")
   .get_data(as_text=True))
ok("nothing is filled before a yes", bc.get()["name"] == "" and bc.get()["website"] == "")
r = o.post("/settings/business", data={"action": "confirm"})
g = bc.get()
ok("a yes fills the empty fields", r.status_code == 303 and g["website"] == "https://glow-medspa.example"
   and g["area"] == "12 Main St, Austin, TX", g)

print("\ntest_the_owner_answers")
FULL = {"action": "save", "website": "glow-medspa.example", "name": "Glow Med Spa", "industry": "med spa",
        "area": "South Austin", "sells": "Botox - $12 a unit\nHydrafacial: $189\nGift cards",
        "customers": "Busy professionals", "customer_kinds": ["professionals", "locals"],
        "plan_what": ["Spring skin package", "", ""], "plan_month": ["2027-03", "", ""],
        "push": ["Botox"], "goals": ["more bookings", "better reviews"], "workflows": ["customer messages", "reviews"],
        "stage": "growing", "team_size": "2-5"}   # the whole form, as a browser posts it
r = o.post("/settings/business", data=FULL)
g = bc.get()
ok("every answer is stored through the one home", r.status_code == 303 and g["industry"] == "med spa"
   and g["sells"] == [{"name": "Botox", "price": "$12 a unit"}, {"name": "Hydrafacial", "price": "$189"},
                      {"name": "Gift cards", "price": ""}]
   and g["coming"] == [{"what": "Spring skin package", "month": "2027-03"}] and g["push"] == ["Botox"]
   and g["goals"] == ["more bookings", "better reviews"] and g["stage"] == "growing" and g["team_size"] == "2-5"
   and g["customer_kinds"] == ["professionals", "locals"], g)
page = o.get("/settings/business?saved=1").get_data(as_text=True)
ok("the screen shows what was saved", "Saved" in page and 'value="Glow Med Spa"' in page
   and "Botox - $12 a unit" in page and 'value="2027-03"' in page, page[-800:])
ok("...with every chosen chip checked", 'value="more bookings" checked' in page and 'value="growing" checked' in page)
r = o.post("/settings/business", data={**FULL, "website": "not a site", "goals": list(bc.GOALS[:4])})
body = r.get_data(as_text=True)
ok("bad answers are refused in words, and what was fine is kept", r.status_code == 400
   and "look like a website" in body and "Pick up to 3 goals" in body and bc.get()["name"] == "Glow Med Spa"
   and bc.get()["website"] == "https://glow-medspa.example" and bc.get()["goals"] == ["more bookings", "better reviews"],
   body[:600])
ok("...and what they typed is still in the form, nothing to type again (OSDev1)", 'value="not a site"' in body
   and 'value="launch something new"' not in body.split("bz-err")[0] and 'value="Glow Med Spa"' in body, body[-1500:])
ok("a plan with no month is refused in words", "Give each plan a month" in o.post("/settings/business", data={
   **FULL, "plan_what": ["Gift cards", "", ""], "plan_month": ["", "", ""]}).get_data(as_text=True)
   and bc.get()["coming"] == [{"what": "Spring skin package", "month": "2027-03"}])

print("\ntest_no_it_isnt_is_remembered")
bc.put("suggested", {"website": "https://other-spa.example", "name": "Other"}, by="light scan")
r = o.post("/settings/business", data={"action": "reject"})
ok("'No, it isn't' clears the suggestion and the scan never offers that site again (OSDev1)",
   r.status_code == 303 and bc.get()["suggested"] == {}
   and "other-spa.example" in (box_settings.get(bc.NS, "_rejected", default=[]) or []))

print("\ntest_it_promises_only_what_is_true")
page = o.get("/settings/business?saved=1").get_data(as_text=True)
ok("no promise that every machine reads it yet (C3 and C5 make that true)", "Every machine" not in page
   and "Saved." in page, page[:400])
ok("...only the one the Welcome review makes true (C4)", "Your next Morning Review starts from it." in page)

print("\ntest_mobile_first")
page = o.get("/settings/business").get_data(as_text=True)
ok("every choice is a 48px row on a mobile, wrapping to pills only on a wide screen",
   ".bz-chips label{display:flex" in page and "min-height:48px" in page and "@media (min-width:720px)" in page)
ok("fields use the box's 16px rule", "max(16px" in page)

print("\ntest_who_may_change_it")
page = m.get("/settings/business").get_data(as_text=True)
ok("a member reads it", "Glow Med Spa" in page and "Only the owner can change these" in page
   and 'name="action"' not in page, page[-600:])
ok("...and can't change it", m.post("/settings/business", data={"action": "save", "name": "Hacked"}).status_code == 403
   and bc.get()["name"] == "Glow Med Spa")
ok("the home card is the owner's only", "Tell your box about your business" not in m.get("/dashboard")
   .get_data(as_text=True))

print("\ntest_the_home_card_stops")
ok("answered: the card is gone", "Tell your box about your business" not in o.get("/dashboard").get_data(as_text=True))
for f in ("goals", "stage", "sells", "push"):
    box_settings.put(bc.NS, f, [] if f != "stage" else "", set_by="test")
ok("unanswered again: it is back", "Tell your box about your business" in o.get("/dashboard").get_data(as_text=True))
r = o.post("/settings/business", data={"action": "dismiss"})
ok("Not now dismisses it, for good", r.status_code == 303
   and "Tell your box about your business" not in o.get("/dashboard").get_data(as_text=True))

print("\ntest_the_screen_touches_no_store_but_the_one_home")
src = (ROOT / "core/dash/business.py").read_text()
ok("core/dash/business.py writes the business only through business_context.put",
   'box_settings.put(bc.NS' not in src and 'box_settings.put("business"' not in src and "bc.put(" in src)

print("\nALL YOUR-BUSINESS CHECKS PASS" if not _failed else f"\n{_failed} YOUR-BUSINESS CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
