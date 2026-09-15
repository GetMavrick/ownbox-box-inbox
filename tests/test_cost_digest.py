"""The cost digest says the true meter, once a day, and cries once when a vendor crosses 80%."""
import os, sys, tempfile, pathlib
os.environ.setdefault("AIOS_HERMETIC_TEST", "1")
os.environ.setdefault("AIOS_DB_PATH", tempfile.mkdtemp() + "/t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime
from zoneinfo import ZoneInfo
from core import cost_digest as cd, cost_guard

_failed = 0
def ok(label, cond, detail=""):
    global _failed
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  — {detail}"))
    if not cond: _failed += 1

TMP = pathlib.Path(tempfile.mkdtemp()); cd._state_path = lambda: TMP / "cost_digest.json"
# THE MORNING REVIEW ABSORBED THE DAILY DIGEST (core/report.py). The standalone DM only goes out
# when cost.digest.standalone says so; the crossing alert is untouched. This file proves the old
# path still works when asked for, and that it is quiet when not.
cd._cfg = lambda: {"enabled": True, "hour_local": 8, "standalone": True}
USAGE = {"google_places": 201.0, "heygen": 12.0, "hunter": 0.0}
CAPS = {"google_places": 200.0, "heygen": 100.0, "hunter": 50.0}
cost_guard.metered_vendors = lambda: list(CAPS)
cost_guard.vendor_cap = lambda v: CAPS[v]
cost_guard.vendor_usage = lambda v, now=None: USAGE[v]
cost_guard.month_to_date_spend = lambda now=None: 12.34
cost_guard.ceiling = lambda: 90.0
from core.config import settings
settings.operator_slack_user_id = "U_OWNER"
tz = ZoneInfo("UTC"); at = lambda h, d=6: datetime(2026, 9, d, h, 5, tzinfo=tz)

sent = []
send = lambda text: (sent.append(text) or True)

text = cd.render(at(9))
ok("the digest names every metered vendor with used / cap and a percentage", all(v in text for v in CAPS) and "201 / 200" in text and "100%" in text, text)
ok("a vendor AT CAP says calls are refused", "AT CAP" in text and "refused" in text, text)
ok("a vendor in use projects days to the cap at this pace", "days to cap" in text, text)
ok("a vendor with no use says so, not a division by zero", "no use yet" in text, text)
ok("the USD line reads the same guard the brain is charged against", "$12.34 of $90.00" in text, text)

r = cd.run(at(7), send)
ok("before hour_local: no digest, but the AT-CAP crossing alert goes out immediately", r["digest"] is False and r["alerts"] == ["google_places"] and len(sent) == 1 and "100%" in sent[0], str(r))
r = cd.run(at(9), send)
ok("at hour_local: the digest goes out, and the crossing does NOT repeat", r["digest"] is True and r["alerts"] == [] and len(sent) == 2, str(r))
r = cd.run(at(15), send)
ok("later the same day: nothing goes out twice", r["digest"] is False and r["alerts"] == [] and len(sent) == 2, str(r))
r = cd.run(at(9, d=7), send)
ok("next day: one digest again, still no repeat of the crossing", r["digest"] is True and r["alerts"] == [] and len(sent) == 3, str(r))
USAGE["heygen"] = 85.0
r = cd.run(at(10, d=7), send)
ok("a vendor crossing 80% mid-day is announced at once, once", r["alerts"] == ["heygen"] and len(sent) == 4 and "heygen is at 85%" in sent[-1], str(r))
cd._cfg = lambda: {"enabled": True, "hour_local": 8, "standalone": False}
USAGE["hunter"] = 45.0
r = cd.run(at(9, d=8), send)
ok("standalone false (the default): NO daily digest — the review carries the meters — but the 80% crossing still fires at once",
   r["digest"] is False and r["alerts"] == ["hunter"] and len(sent) == 5 and "hunter is at 90%" in sent[-1], str(r))
settings.operator_slack_user_id = ""
ok("no operator on this box → nothing is sent, and it says so", cd.run(at(9, d=9), send)["status"] == "no_operator" and len(sent) == 5)
print(f"{_failed} FAILED" if _failed else "all ok"); sys.exit(1 if _failed else 0)
