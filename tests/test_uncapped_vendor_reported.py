"""A vendor the owner chose not to cap is still REPORTED, and still never refused.

OSDev1's finding on #1597 (2026-09-26): deleting monthly_unit_cap to uncap MyEmailVerifier dropped
it out of metered_vendors(), and every spend report enumerates that list, so the ledger kept
recording while nothing showed it. The owner's premise was "I just need to purchase more when we
get low", which needs the usage seen. `metered: true` keeps an uncapped vendor in every report;
`soft_goal` is shown beside usage and never enforced. `monthly_unit_cap: 0` is NOT the way to say
"uncapped": it refuses every call.
"""
import os, sys, tempfile
os.environ.setdefault("AIOS_HERMETIC_TEST", "1")
os.environ.setdefault("AIOS_DB_PATH", tempfile.mkdtemp() + "/t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime
from zoneinfo import ZoneInfo
from core import cost_guard, cost_digest, box_tools
from core.exceptions import BudgetExceeded

_failed = 0
def ok(label, cond, detail=""):
    global _failed
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  — {detail}"))
    if not cond: _failed += 1

VENDORS = {"email_verify": {"metered": True},
           "google_places": {"monthly_unit_cap": 3100, "soft_goal": 310},
           "goal_vendor": {"monthly_unit_cap": 3100, "soft_goal": 310},
           "zero_cap": {"monthly_unit_cap": 0},
           "unlisted": {"est_usd_per_unit": 0.01}}
_REAL = cost_guard.get_config()
cost_guard.get_config = lambda: {**_REAL, "vendors": VENDORS}
USAGE = {"email_verify": 761.0, "google_places": 400.0, "goal_vendor": 400.0, "zero_cap": 0.0}
cost_guard.vendor_usage = lambda v, now=None: USAGE.get(v, 0.0)
cost_guard.state.vendor_usage_since = lambda v, start: USAGE.get(v, 0.0)

m = cost_guard.metered_vendors()
ok("an uncapped vendor marked metered: true is in every report's list", "email_verify" in m, m)
ok("a vendor with neither a cap nor metered: true stays out", "unlisted" not in m, m)
ok("metered: true enforces nothing: check_vendor returns None (no refusal)",
   cost_guard.check_vendor("email_verify", 10_000) is None)
try:
    cost_guard.check_vendor("zero_cap", 1); refused = False
except BudgetExceeded:
    refused = True
ok("monthly_unit_cap: 0 refuses every call — never use it to mean 'uncapped'", refused)
ok("the label says 'no cap' for an uncapped vendor", cost_guard.vendor_limit_label("email_verify") == "no cap")
ok("the label carries the soft goal beside the cap",
   cost_guard.vendor_limit_label("goal_vendor") == "3100, goal 310", cost_guard.vendor_limit_label("goal_vendor"))

now = datetime(2026, 9, 26, 9, tzinfo=ZoneInfo("UTC"))
rows = {r["vendor"]: r for r in cost_digest.meters(now)}
ok("the digest never claims an uncapped vendor hits its cap", rows["email_verify"]["hits_cap_this_cycle"] is False, rows["email_verify"])
text = cost_digest.render(now)
ok("the digest shows the uncapped vendor's usage and says no cap", "email_verify" in text and "761 used, no cap" in text, text)
ok("the digest shows a soft goal, and says so when usage is past it", "soft goal 310 (PAST IT" in text, text)
tools = {v["vendor"]: v for v in box_tools.spend()["vendors"]}
ok("box_tools.spend reports the uncapped vendor with units_cap None", tools["email_verify"]["units_cap"] is None, tools)
ok("box_tools.spend carries the soft goal beside the cap", tools["goal_vendor"]["units_soft_goal"] == 310, tools)
ok("google_places: past its 310 goal (400 used) it is still allowed; 3100 is only a runaway stop",
   cost_guard.vendor_limit_label("google_places") == "3100, goal 310"
   and tools["google_places"]["units_soft_goal"] == 310
   and cost_guard.check_vendor("google_places", 1) == 2700, cost_guard.vendor_limit_label("google_places"))
gp = _REAL["vendors"]["google_places"]
ok("the shipped config keeps Places at a 3100 runaway stop with the 310 soft goal",
   gp.get("monthly_unit_cap") == 3100 and gp.get("soft_goal") == 310, gp)

print("\nall ok" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
