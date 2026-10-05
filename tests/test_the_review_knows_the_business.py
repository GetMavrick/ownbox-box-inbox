"""The Morning Review's ideas know the business, and never restate the to-do list (#1953 Phase 1, steps 1.1 and 1.3).

Owner, 2026-10-04: the review should be "smart and is suggesting great advice for companies based on studying their
website and their context in their business environment." Step 1.1 hands the review's AI what the owner told the box on
Your Business and what the full scan quoted from their site; step 1.3 stops an idea from saying again what "Worth your
time" already says ("Reply to the 4 people waiting" under "4 people waiting").

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * the AI is not told the business, its goals or its plans, or is told a plan whose month has passed;
  * the call becomes isolated, so my/knowledge/ (the sent-mail summary, the full scan's profile) stops riding in;
  * a price the owner typed cannot be quoted back, or a number nobody gave is kept;
  * an idea that restates a "Worth your time" line is shown, or one that adds something new is dropped;
  * the prompt stops telling the AI either rule.

No network, no model: the AI is stood in for.

Run: python tests/test_the_review_knows_the_business.py
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
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "review_business.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state  # noqa: E402

state.init_db()
from core import box_settings  # noqa: E402
from core import business_context as bc  # noqa: E402
from core import review_brief  # noqa: E402

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


# BEFORE THE OWNER EXISTED, so this is an ordinary review and never the Welcome edition.
made = date.fromisoformat(str(state.owner_user()["created_at"])[:10])
ABOUT = made - timedelta(days=5)
NOW = datetime.combine(ABOUT + timedelta(days=1), datetime.min.time()).replace(hour=8)
T = ABOUT + timedelta(days=1)
NEXT = (T.replace(day=1) + timedelta(days=40)).replace(day=1)
GONE = (T.replace(day=1) - timedelta(days=40)).replace(day=1)
row(ABOUT, "inbox", "Inbox", happened=[{"text": "12 messages came in", "value": 12}])
row(T, "inbox", "Inbox", needs_you=[{"text": "4 people waiting on a reply", "href": "/inbox"}])

by = "owner"
bc.put("name", "Glow Med Spa", by=by)
bc.put("industry", "med spa", by=by)
bc.put("sells", [{"name": "Hydrafacial", "price": "$189"}], by=by)
bc.put("customers", "Busy professionals", by=by)
bc.put("goals", ["more repeat customers", "better reviews"], by=by)
bc.put("coming", [{"what": "Spring skin package", "month": f"{NEXT:%Y-%m}"},
                  {"what": "Summer promo", "month": f"{GONE:%Y-%m}"}], by=by)
bc.put("profile", [{"line": "Saturday appointments book out a week ahead.",
                    "source": "https://glow-medspa.example/booking"}], by="full scan")

seen = []


def think(task, prompt, **kw):
    seen.append({"task": task, "prompt": json.loads(prompt), "kw": kw})
    return json.dumps({"good_news": "12 messages came in yesterday.", "ideas": [
        {"title": "Reply to the 4 people waiting", "why": "They wrote yesterday."},
        {"title": "Answer the 4 people waiting on a reply", "why": "Quick wins."},
        {"title": "Offer a $189 Hydrafacial rebook to regulars", "why": "Repeat customers are your first goal."},
        {"title": "Ask Saturday regulars for a review", "why": "Better reviews is a goal, and 12 wrote in."},
        {"title": "Run a $49 flash sale", "why": "Nobody said $49."}]})


print("test_the_ai_is_told_who_the_business_is")
b = review_brief.build(ABOUT, NOW, think=think)
ok("an ordinary morning, one AI call on the review task", b["welcome"] is False and len(seen) == 1
   and seen[0]["task"] == "review", seen[:1])
told = seen[0]["prompt"].get("business") or {}
ok("it is told what the business is, who it serves and what the site says",
   told.get("about", {}).get("name") == "Glow Med Spa" and told["about"].get("industry") == "med spa"
   and told["about"].get("customers") == "Busy professionals"
   and told["about"].get("profile") == ["Saturday appointments book out a week ahead."], told)
ok("...its goals, in the owner's order", told.get("goals") == ["more repeat customers", "better reviews"], told)
ok("...and its plans still ahead, never one whose month has passed",
   told.get("planned_not_yet_available") == [{"what": "Spring skin package", "month": f"{NEXT:%Y-%m}"}], told)
ok("the call is not isolated, so my/knowledge/ (sent mail, the site's profile) rides in as before",
   not seen[0]["kw"].get("isolated"), seen[0]["kw"])
sysp = seen[0]["kw"].get("system") or ""
ok("the prompt says to fit the business and never restate the to-do list",
   "every idea must fit that business" in sysp and "never repeat or rephrase" in sysp, sysp[-500:])

print("\ntest_it_never_restates_the_to_do_list")
titles = [i["title"] for i in b["ideas"]]
ok("'Worth your time' already says 4 people are waiting", any("4 people waiting" in w["title"] for w in b["worth"]),
   b["worth"])
ok("an idea that says it again is dropped, however it is worded",
   not any("people waiting" in t for t in titles), titles)
ok("ideas that add something new are kept, the owner's own price included",
   "Offer a $189 Hydrafacial rebook to regulars" in titles and "Ask Saturday regulars for a review" in titles, titles)
ok("a number nobody gave is still dropped", not any("$49" in t for t in titles), titles)
ok("the matching is about meaning-words, not exact text",
   review_brief._restates({"title": "Replies for the people waiting"}, [{"title": "4 people waiting on a reply"}])
   and not review_brief._restates({"title": "Reply faster next week"}, [{"title": "4 people waiting on a reply"}]))

print("\ntest_a_box_that_knows_nothing_asks_as_before")
for f in ("name", "industry", "sells", "customers", "goals", "coming", "profile"):
    box_settings.put(bc.NS, f, [] if f in ("sells", "goals", "coming", "profile") else "", set_by="test")
seen.clear()
review_brief.build(ABOUT, NOW, think=think)
ok("no business block when nothing is known, so the call is exactly the old one",
   "business" not in seen[0]["prompt"], seen[0]["prompt"])

print("\nALL REVIEW-KNOWS-THE-BUSINESS CHECKS PASS" if not _failed else f"\n{_failed} CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
