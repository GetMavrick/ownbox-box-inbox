"""The box keeps the buyer's time (plan #1857 H9).

Every sold box shipped `cost.timezone: UTC` and no page set it, so the owner's Morning Review read "As of 1:57 AM",
while the claim form had caught his browser's zone and kept it unused.

Held here:
  1. a sold box with no claim and no choice keeps UTC;
  2. the zone the buyer's browser gave at claim becomes the box's, with no step from them;
  3. Settings, General, Time Zone shows it selected, US zones first, and changes it; a zone not in the list is
     refused; a member is refused;
  4. everything follows the one zone: the review's clock, "As of" in words, the cost cycle and the digest;
  5. an old box with only `cost.timezone` in config keeps it.
Run: python tests/test_the_box_keeps_the_buyers_time.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "tz.db")
os.environ["DISPATCH_BEARER_TOKEN"], os.environ["DASH_TOKEN"] = "bearer", "pw"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import state  # noqa: E402

state.init_db()
import core.config as _cfgmod  # noqa: E402
from core import box_settings, cost_digest, cost_guard, dash, report  # noqa: E402
from core.connector import words  # noqa: E402
from core.dispatch import app  # noqa: E402

_failed = 0


def ok(label: str, cond: bool, detail="") -> None:
    global _failed
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  — {str(detail)[:500]}"))
    if not cond:
        _failed += 1


_real = _cfgmod.get_config
CONFIG_TZ = {"tz": "UTC"}                               # what a sold box ships


def _cfg():
    c = _real()
    return {**c, "cost": {**(c.get("cost") or {}), "timezone": CONFIG_TZ["tz"]}}


_cfgmod.get_config = report.get_config = _cfg
for _m in (cost_guard, cost_digest):
    if hasattr(_m, "get_config"):
        _m.get_config = _cfg


def everything() -> dict:
    return {"review": report.now_local().tzinfo.key, "words": words._tz().key,
            "cost cycle": cost_guard._cycle_start().tzinfo.key, "digest": cost_digest._tz().key}


print("\n1. a sold box, nobody has said —")
ok("UTC", report.tz_name() == "UTC", report.tz_name())

print("\n2. the buyer claimed it from a browser in Los Angeles —")
with state.connect() as c:
    c.execute("INSERT INTO box_claim (id, claimed_at, order_id, user_id, email, pw_hash, ip, user_agent, timezone) "
              "VALUES (1, ?, 'ord_1', ?, 'maria@glowmedspa.com', 'x', '', '', 'America/Los_Angeles')",
              (state._now(), state.owner_user()["id"]))
ok("THE CLAIMED ZONE IS THE BOX'S, with no step from them", report.tz_name() == "America/Los_Angeles",
   report.tz_name())
ok("...and EVERYTHING FOLLOWS IT: the review's clock, 'As of' in words, the cost cycle, the digest",
   set(everything().values()) == {"America/Los_Angeles"}, everything())

print("\n3. Settings, General, Time Zone —")
owner = app.test_client()
owner.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
page = owner.get("/settings/timezone").get_data(as_text=True)
ok("it shows the box's zone selected", '<option value="America/Los_Angeles" selected>' in page, page[:400])
ok("...US zones first, then every other", page.index('label="United States"') < page.index("America/New_York")
   < page.index('label="Everywhere else"') < page.index("Europe/London"))
ok("...one Save, and it's in General on the menu", page.count("<button") == 1
   and re.search(r'<span class="glabel">General</span>.*?Time Zone', page, re.S) is not None)
owner.post("/settings/timezone", data={"tz": "America/New_York"})
ok("CHANGING IT CHANGES THE BOX", report.tz_name() == "America/New_York"
   and box_settings.get(report.TZ_NS, report.TZ_KEY) == "America/New_York", report.tz_name())
ok("...and everything follows the change", set(everything().values()) == {"America/New_York"}, everything())
from datetime import datetime, timezone as _utc  # noqa: E402

from core import notify  # noqa: E402

_at = datetime(2026, 10, 3, 12, 30, tzinfo=_utc.utc)              # 8:30 AM in New York, 1:30 PM in London
ok("THE 8 AM AND 5 PM SLOTS FOLLOW THE PAGE, not a second resolver (OSDev1's review of #1863)",
   notify._local(_at).tzinfo.key == "America/New_York" and notify.due_slot(_at) == "morning",
   (notify._local(_at), notify.due_slot(_at)))
r = owner.post("/settings/timezone", data={"tz": "Mars/Olympus_Mons"}).get_data(as_text=True)
ok("a zone not in the list is refused, and nothing changes", "That was not saved" in r
   and report.tz_name() == "America/New_York")
member = app.test_client()
member.set_cookie(dash.COOKIE, dash.new_session(state.add_user("tomas@glowmedspa.com", name="Tomas",
                                                               role="member")["id"]))
ok("a member is refused", member.post("/settings/timezone", data={"tz": "Europe/London"}).status_code == 403
   and report.tz_name() == "America/New_York")

print("\n4. an old box with only config —")
box_settings.put(report.TZ_NS, report.TZ_KEY, "")
with state.connect() as c:
    c.execute("DELETE FROM box_claim")
CONFIG_TZ["tz"] = "America/Chicago"
ok("its config zone still works", report.tz_name() == "America/Chicago", report.tz_name())

print()
print("FAILED" if _failed else "ALL PASSED", _failed)
sys.exit(1 if _failed else 0)
