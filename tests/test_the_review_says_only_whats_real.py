"""The Morning Review's own content says only what is real (OSDev1's assignment, 2026-10-02, scope #1839).

The owner asked his AI for the Morning Review over the connector and said: "This is not an AI business machine. This
is a dumb box." What it had been handed:
  · Meters, eleven lines from every vendor in the shipped config: "Instantly 0 of 0", "MyEmailVerifier (lead checks)
    0 of 0", "Tomba 0 of 1000", ... and the one real line, "Resend 9 of 3000", valued "0%";
  · a Website headline of label "" and value 0, above "brian-macdonald.com: 179 visits from people this week".

Held here:
  1. a meter is a line only when it was used this cycle (owner, 2026-09-29: "If a line doesn't have data, it should
     not be displayed"), and the at-cap and near-cap checks still speak;
  2. use under 1% is "<1%", a meter short of its cap never reads "100%", an uncapped meter says "no cap", and the
     page now draws the line it used to hide;
  3. the Website headline (tests/test_website_foundation.py, which ships only with the foundation);
  4. a stored headline nobody gave (old website rows, a machine that could not report) is no number: it neither
     makes tomorrow's change nor draws a point on the chart, while a quiet machine's real zero still does.
Run: python tests/test_the_review_says_only_whats_real.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date

_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "review.db")
os.environ["DISPATCH_BEARER_TOKEN"], os.environ["DASH_TOKEN"] = "bearer", "pw"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import state  # noqa: E402

state.init_db()
from core import cost_digest, cost_guard, report  # noqa: E402
from core.dash import review  # noqa: E402

_failed = 0


def ok(label: str, cond: bool, detail="") -> None:
    global _failed
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  — {str(detail)[:600]}"))
    if not cond:
        _failed += 1


DAY = date(2026, 10, 2)


def meter(vendor, used, cap):
    return {"vendor": vendor, "used": float(used), "cap": float(cap), "pct": (100.0 * used / cap) if cap else 0.0}


# THE OWNER'S METERS ON 2026-10-02, as his AI read them.
HIS = [meter("apollo", 0, 75), meter("email_verify", 0, 0), meter("google_places", 0, 3100), meter("heygen", 0, 100),
       meter("hunter", 0, 50), meter("instantly", 0, 0), meter("mev_leads", 0, 0), meter("prospeo", 0, 100),
       meter("resend", 9, 3000), meter("scrapecreators", 0, 1500), meter("tomba", 0, 1000)]
_real = (cost_digest.meters, cost_guard.month_to_date_spend, cost_guard.ceiling)
cost_guard.month_to_date_spend, cost_guard.ceiling = (lambda at=None: 0.0), (lambda: 90.0)


def meters_with(rows):
    cost_digest.meters = lambda at: rows
    return report._meters_report(DAY)


print("\ntest_only_a_meter_in_use_is_a_line")
m = meters_with(HIS)
ok("HIS ELEVEN LINES BECOME THE ONE THAT WAS USED: Resend 9 of 3,000, <1%",
   m["happened"] == [{"text": "Resend 9 of 3,000", "value": "<1%"}], m["happened"])
ok("...no 0-of-0 line, no Instantly, no Tomba, nothing at 0%",
   not any(w in str(m) for w in ("0 of 0", "Instantly", "Tomba", "'0%'")), m)
ok("the page draws it now (it hid '9 of 3000' because '0%' reads as nothing)",
   "Resend 9 of 3,000" in review._meters(m) and "&lt;1%" in review._meters(m), review._meters(m))
ok("nothing used: no lines, and the page shows no Spend section",
   meters_with([meter("tomba", 0, 1000), meter("instantly", 0, 0)])["happened"] == []
   and review._meters(meters_with([meter("tomba", 0, 1000)])) == "")
m = meters_with([meter("email_verify", 120, 0)])
ok("an uncapped meter in use says how many, and 'no cap', never '120 of 0' or '0%'",
   m["happened"] == [{"text": "MyEmailVerifier (inbox checks) 120 used", "value": "no cap"}], m["happened"])
m = meters_with([meter("google_places", 3100, 3100), meter("hunter", 45, 50), meter("apollo", 0, 75)])
ok("AT CAP AND NEAR CAP STILL SPEAK, each on its own line",
   [w["state"] for w in m["watch"]] == ["fail", "warn"] and "Google Places is AT CAP" in m["watch"][0]["text"]
   and "Hunter is at 90%" in m["watch"][1]["text"], m["watch"])
ok("...and Apollo, unused, is not a line", [h["text"] for h in m["happened"]] == ["Google Places 3,100 of 3,100",
                                                                                  "Hunter 45 of 50"], m["happened"])
cost_digest.meters, cost_guard.month_to_date_spend, cost_guard.ceiling = _real

print("\ntest_a_percent_never_rounds_use_away")
for (used, cap), want in {(9, 3000): "<1%", (0, 3000): "0%", (30, 3000): "1%", (45, 50): "90%",
                          (2999, 3000): "99%", (3000, 3000): "100%", (3100, 3000): "103%", (5, 0): "no cap"}.items():
    ok(f"{used} of {cap} reads {want}", report.meter_pct(used, cap) == want, report.meter_pct(used, cap))

print("\ntest_a_headline_nobody_gave_is_not_a_zero")
Y, D = date(2026, 10, 1), DAY
SAY: dict = {}


def boom(day):
    raise RuntimeError("sync down")


report.REPORTERS.clear()
report.REPORTERS.update({m: {"title": m, "fn": (lambda d, m=m: SAY[(m, d)]() if callable(SAY[(m, d)]) else SAY[(m, d)])}
                         for m in ("website", "aeo_machine", "lead_machine", "said_none")})
# YESTERDAY, AS STORED BEFORE THIS FIX: the website said its lines with no headline; the AEO Machine had a quiet day;
# the Lead Machine could not report.
SAY.update({("website", Y): {"title": "Website", "happened": [{"text": "brian-macdonald.com: 179 visits from people"}]},
            ("aeo_machine", Y): {}, ("lead_machine", Y): boom,
            ("said_none", Y): {"headline": {"value": 179, "label": "visits from people this week"}}})
report.snapshot(Y)
# TODAY: each gives a headline without its own change, so the box fills it from yesterday's row.
SAY.update({("website", D): {"headline": {"value": 185, "label": "visits from people this week"},
                             "happened": [{"text": "x"}]},
            ("aeo_machine", D): {"headline": {"value": 1, "label": "article published"}},
            ("lead_machine", D): {"headline": {"value": 4, "label": "emails sent"}},
            # a reporter that says "no change" itself (the website, when a site has no week ending the day before)
            ("said_none", D): {"headline": {"value": 185, "label": "visits from people this week", "delta": None}}})
report.snapshot(D)
heads = {r["machine"]: r["headline"] for r in report.read(D)}
ok("THE FIRST REAL WEBSITE HEADLINE IS NOT '+185 vs the day before'", heads["website"]["delta"] is None, heads)
ok("...nor is a machine's first number after it could not report", heads["lead_machine"]["delta"] is None, heads)
ok("A REPORTER THAT SAYS 'NO CHANGE' ITSELF IS NOT FILLED IN FROM YESTERDAY'S ROW (+6 would be a new site's week)",
   heads["said_none"]["delta"] is None, heads)
ok("A QUIET DAY'S ZERO IS STILL A ZERO: the AEO Machine's article reads +1", heads["aeo_machine"]["delta"] == 1, heads)
h = report.history(D)
ok("the chart draws no point for yesterday's website or the failed Lead Machine",
   [p["day"] for p in h.get("website", [])] == ["2026-10-02"]
   and [p["day"] for p in h.get("lead_machine", [])] == ["2026-10-02"], h)
ok("...and keeps the AEO Machine's quiet day at zero", h.get("aeo_machine") == [{"day": "2026-10-01", "value": 0},
                                                                                {"day": "2026-10-02", "value": 1}], h)

print()
print("FAILED" if _failed else "ALL PASSED", _failed)
sys.exit(1 if _failed else 0)
