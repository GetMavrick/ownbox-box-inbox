"""The claim hands the buyer onward to a CONFIGURED place — and only to a safe one.

WHY A SETTING. OSDev1 left this out of #1468 on purpose: which set-up step a new owner meets first
waits on the owner's word, now that the Unified Inbox replaced Instagram as the headline. So the
destination after the claim is `dash.after_claim`, one config line, and this suite holds the two
promises that make it safe to be a setting at all:

  1. it is honoured — a real claim, posted through the route, lands where the config says;
  2. it cannot be turned into a hole — a path this box does not serve, or one that leaves the
     site, falls back to `landing()` instead of sending a brand-new owner to a 404 or to a
     stranger's page with their fresh session cookie.

Run: python tests/test_the_claim_hands_off.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "handoff.db")
os.environ["AIOS_PROVISION_JSON"] = os.path.join(_T, "provision.json")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state                                                # noqa: E402

state.init_db()

from core import config                                               # noqa: E402
from core.dispatch import app as flask_app                            # noqa: E402

ORDER = "cs_live_Q7rTt2mK9xLp4vNb8wZa3cYd6eFg1hJk"
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def fresh_box():
    with open(os.environ["AIOS_PROVISION_JSON"], "w", encoding="utf-8") as fh:
        json.dump({"buyer": "Ownbox Test Co", "order": ORDER, "host": "testco.ownbox.app",
                   "box_type": "customer_voice"}, fh)
    with state.connect() as c:
        c.execute("DELETE FROM box_claim")


_real = config.get_config()


def with_after_claim(value):
    """Point `dash.after_claim` at `value` (None removes it) for the next claim."""
    cfg = json.loads(json.dumps(_real))
    dash = cfg.setdefault("dash", {})
    if value is None:
        dash.pop("after_claim", None)
    else:
        dash["after_claim"] = value
    config.get_config = lambda: cfg


def claim_lands():
    """Claim through the ROUTE, as a buyer does, and read where the redirect points."""
    fresh_box()
    r = flask_app.test_client().post("/claim", data={
        "c": ORDER, "email": "buyer@testco.com", "password": "a-long-enough-password", "tz": ""})
    return r.status_code, r.headers.get("Location", "")


# ── 1. the shipped value ──────────────────────────────────────────────────────────────────────
print("\ntest_the_box_ships_a_destination")
ok("the tracked config names where a new owner lands",
   (_real.get("dash") or {}).get("after_claim") == "/dashboard",
   repr((_real.get("dash") or {}).get("after_claim")))
with_after_claim("/dashboard")
code, where = claim_lands()
ok("a claim redirects", code in (302, 303), str(code))
ok("...to the dashboard, whose first card carries on into set-up", where.endswith("/dashboard"), where)


# ── 2. the setting is honoured ───────────────────────────────────────────────────────────────
print("\ntest_changing_one_line_changes_where_they_land")
with_after_claim("/settings/ai")
code, where = claim_lands()
ok("pointed at the AI account screen, the claim lands there", where.endswith("/settings/ai"), where)


# ── 3. and cannot be turned into a hole ──────────────────────────────────────────────────────
print("\ntest_a_bad_value_costs_the_buyer_nothing")
with_after_claim(None)
_, default = claim_lands()
for bad, why in (("/no/such/page", "a path this box does not serve would be a 404"),
                 ("//evil.example", "a protocol-relative address leaves the site"),
                 ("https://evil.example/", "an absolute address leaves the site"),
                 ("/\t/evil.example", "a control character is the known smuggle")):
    with_after_claim(bad)
    _, where = claim_lands()
    ok(f"{bad!r} falls back to the ordinary landing — {why}", where == default, where)
ok("...and that fallback is itself a page on this box", default.startswith("/") or "://" not in default,
   default)


config.get_config = lambda: _real
print("\n" + ("all good" if not _failed else f"{_failed} FAILED"))
sys.exit(1 if _failed else 0)
