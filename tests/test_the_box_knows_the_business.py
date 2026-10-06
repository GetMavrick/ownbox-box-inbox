"""What your box knows about your business: read every week, quoted, corrected by the owner (Morning Review V2 step 1).

docs/PLAN_MORNING_REVIEW_ADVISOR.md, Input A: "A short profile of the business, rebuilt once a week, never daily ...
Every line is a quoted sentence with its source ... The owner sees it and can correct it, on one screen: 'What your box
knows about your business' ... An industry line, chosen from a short fixed list by the same sentence-number method."
Owner, 2026-10-05, on the plan's decisions: "Agree with all of your recommendations" (1: Sonnet for the weekly
profile; 2: show the profile and let the owner correct it). The quoting itself is tests/test_business_full_scan.py.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * the site is read again before a week has passed, or never again after one (a new price never reaches the review);
  * a site the box gave up on last week is never tried again;
  * a line the owner struck stays on the screen or in the knowledge file, or a later read brings it back;
  * "Put it back" does not bring a struck line back at once (Not right is never a one-way slip of the thumb);
  * the industry the site shows is stored without a person's yes, overwrites one the owner chose, is not one of the
    list, rests on a sentence that doesn't exist, or is asked again after a no;
  * the screen lets someone other than the owner strike a line or answer the question;
  * the weekly read runs on anything but Sonnet.

No network, no model: fetch and think are handed in.

Run: python tests/test_the_box_knows_the_business.py
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["AIOS_HERMETIC_TEST"] = "1"
TMP = tempfile.mkdtemp()
os.environ["AIOS_DB_PATH"] = os.path.join(TMP, "knows.db")
os.environ["DASH_TOKEN"] = "pw"

from core import state  # noqa: E402

state.init_db()
from core import box_settings, brain, site_reader  # noqa: E402
from core import business_context as bc  # noqa: E402

brain.KNOWLEDGE_DIR = pathlib.Path(TMP) / "knowledge"          # never the repo's my/knowledge
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -- {str(detail)[:400]}" if not cond and detail else ""))
    if not cond:
        _failed += 1


SITE = "https://glow-medspa.example"
PAGES = {
    SITE: "<html><body><h1>Glow Med Spa</h1><p>We are a med spa offering Botox, fillers and Hydrafacials.</p>"
          "<p>We are open Tuesday to Saturday, from 9 to 6.</p></body></html>",
}


def fetch(url):
    return PAGES.get(url.rstrip("/"), "")


def num(text):
    return next(i for i, (s, _) in enumerate(site_reader.numbered(SITE, fetch=fetch)["sentences"], 1)
                if s.startswith(text))


def think_with(answer):
    return lambda **kw: json.dumps(answer)


def scan(answer):
    return bc.full_scan(think=think_with(answer), fetch=fetch)


def ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


bc.put("website", SITE, by="owner")

print("test_read_every_week")
runs = []
real_scan = bc.full_scan
bc.full_scan = lambda site=None, **kw: runs.append(site) or {"ok": True, "lines": 3}
box_settings.put(bc.NS, "_full_scan", {"site": SITE, "tries": 1, "status": "done", "at": ago(3)}, set_by="t")
ok("read three days ago: not read again", bc.full_scan_if_needed() == {"status": "done"} and runs == [], runs)
box_settings.put(bc.NS, "_full_scan", {"site": SITE, "tries": 1, "status": "done", "at": ago(8)}, set_by="t")
ok("read eight days ago: read again", bc.full_scan_if_needed()["status"] == "done" and runs == [SITE], runs)
ok("...and that read starts the week's count again",
   (box_settings.get(bc.NS, "_full_scan") or {}).get("tries") == 1, box_settings.get(bc.NS, "_full_scan"))
runs.clear()
box_settings.put(bc.NS, "_full_scan", {"site": SITE, "tries": 3, "status": "none", "at": ago(1)}, set_by="t")
ok("three failed tries this week: the box stops for the week", bc.full_scan_if_needed() == {"status": "gave_up"}
   and runs == [])
box_settings.put(bc.NS, "_full_scan", {"site": SITE, "tries": 3, "status": "none", "at": ago(8)}, set_by="t")
ok("...and tries again the next week", bc.full_scan_if_needed()["status"] == "done" and runs == [SITE], runs)
bc.full_scan = real_scan

print("\ntest_the_owner_strikes_what_is_wrong")
offer, hours = num("We are a med spa"), num("We are open")
out = scan({"sells": [offer], "hours": [hours]})
ok("the read quotes two lines", out["ok"] and len(bc.get()["profile"]) == 2, out)
line = "We are open Tuesday to Saturday, from 9 to 6."
ok("the owner strikes one", bc.strike(line, by="owner") is True)
ok("...it leaves the profile", [p["line"] for p in bc.get()["profile"]] == [
   "We are a med spa offering Botox, fillers and Hydrafacials."], bc.get()["profile"])
body = (brain.KNOWLEDGE_DIR / bc.PROFILE_FILE).read_text()
ok("...and the knowledge file the box's AI reads", line not in body and "We are a med spa" in body, body[:300])
scan({"sells": [offer], "hours": [hours]})
ok("next week's read of the same sentence does not bring it back",
   line not in [p["line"] for p in bc.get()["profile"]], bc.get()["profile"])
ok("striking a line that isn't there changes nothing", bc.strike("Not a line.", by="owner") is False)
ok("Put it back returns it now", bc.unstrike(line, by="owner") is True
   and line in [p["line"] for p in bc.get()["profile"]] and line in (brain.KNOWLEDGE_DIR / bc.PROFILE_FILE).read_text())
scan({"sells": [offer], "hours": [hours]})
ok("...and the next read keeps it", line in [p["line"] for p in bc.get()["profile"]])
bc.strike(line, by="owner")

print("\ntest_the_industry_is_a_question")
scan({"sells": [offer], "industry": {"pick": "med spa", "because": offer}})
h = bc.industry_hint()
ok("the read says which industry, quoting the sentence that shows it",
   h.get("industry") == "med spa" and h.get("line").startswith("We are a med spa") and h.get("source") == SITE, h)
ok("...and stores nothing until a person says yes", bc.get()["industry"] == "")
box_settings.put(bc.NS, bc.INDUSTRY_HINT, {}, set_by="t")
for bad, why in (({"pick": "spa", "because": offer}, "a word not on the list"),
                 ({"pick": "med spa", "because": 99}, "a sentence that doesn't exist"),
                 ({"pick": "other", "because": offer}, "'other'"),
                 ("med spa", "an answer that isn't the shape")):
    scan({"sells": [offer], "industry": bad})
    ok(f"never asked on {why}", bc.industry_hint() == {}, bc.industry_hint())
scan({"sells": [offer], "industry": {"pick": "med spa", "because": offer}})
ok("no: the industry stays empty", bc.answer_industry(False, by="owner") == "" and bc.get()["industry"] == "")
scan({"sells": [offer], "industry": {"pick": "med spa", "because": offer}})
ok("...and the same pick is never asked again", bc.industry_hint() == {})
box_settings.put(bc.NS, bc.INDUSTRY_NO, [], set_by="t")
scan({"sells": [offer], "industry": {"pick": "med spa", "because": offer}})
ok("yes: the industry is filled", bc.answer_industry(True, by="owner") == "med spa" and bc.get()["industry"] == "med spa")
bc.put("industry", "dental", by="owner")
scan({"sells": [offer], "industry": {"pick": "med spa", "because": offer}})
ok("an industry the owner chose is never questioned or overwritten",
   bc.industry_hint() == {} and bc.get()["industry"] == "dental")

print("\ntest_the_screen")
from core import dash  # noqa: E402
from core.dash import business as screen  # noqa: E402
from core.dispatch import app  # noqa: E402

bc.put("industry", "", by="owner")
scan({"sells": [offer], "hours": [hours], "industry": {"pick": "med spa", "because": offer}})
box_settings.put(bc.NS, bc.STRUCK, [], set_by="t")
scan({"sells": [offer], "hours": [hours], "industry": {"pick": "med spa", "because": offer}})
o = app.test_client()
o.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
m = app.test_client()
m.set_cookie(dash.COOKIE, dash.new_session(state.add_user("sam@glow-medspa.example", name="Sam", role="member")["id"]))
page = o.get(screen.DOOR).get_data(as_text=True)
ok("the owner sees 'What your box knows about your business', read every week",
   "What your box knows about your business" in page and "every week" in page)
ok("...each line with its own Not right", page.count('value="strike"') == 2)
ok("...and the industry as a question, with the sentence", "Is this a med spa business?" in page
   and "We are a med spa offering Botox" in page)
mp = m.get(screen.DOOR).get_data(as_text=True)
ok("a member reads it, with no Not right and no question", "What your box knows about your business" in mp
   and "Not right" not in mp and "Is this a med spa business?" not in mp)
r = m.post(screen.DOOR, data={"action": "strike", "line": line})
ok("a member can't strike a line", r.status_code == 403 and line in [p["line"] for p in bc.get()["profile"]])
r = m.post(screen.DOOR, data={"action": "industry_yes"})
ok("...or answer the question", r.status_code == 403 and bc.get()["industry"] == "")
r = o.post(screen.DOOR, data={"action": "strike", "line": line})
ok("the owner's Not right takes the line off", r.status_code == 303 and line not in [p["line"] for p in
   bc.get()["profile"]] and line not in o.get(screen.DOOR).get_data(as_text=True))
after = o.get(r.headers["Location"]).get_data(as_text=True)
ok("...and offers Put it back, which works", "Put it back" in after
   and o.post(screen.DOOR, data={"action": "unstrike", "line": line}).status_code == 303
   and line in [p["line"] for p in bc.get()["profile"]])
o.post(screen.DOOR, data={"action": "strike", "line": line})
r = o.post(screen.DOOR, data={"action": "industry_yes"})
ok("the owner's yes fills the industry", r.status_code == 303 and bc.get()["industry"] == "med spa")

print("\ntest_the_weekly_read_uses_sonnet")
from core.config import get_config  # noqa: E402
ok("the weekly read is Sonnet (owner, 2026-10-05, decision 1)",
   get_config()["models"].get("business.full_scan") == "sonnet", get_config()["models"].get("business.full_scan"))

print("\nALL BOX-KNOWS-THE-BUSINESS CHECKS PASS" if not _failed else f"\n{_failed} BOX-KNOWS-THE-BUSINESS CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
