"""The box's Managed page (/dash/managed) — what a buyer is told before their card is charged.

Managed renews itself (owner, 2026-09-15). A subscription that renews itself owes its buyer two things:
to be told before the charge, and to be able to stop it without asking us. This suite holds the box to
both, and to the rule that makes them safe to ship — NO credential of ours on a machine its customer
has root on.

Run: python tests/test_managed_page.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "managed.db")
os.environ["AIOS_PROVISION_JSON"] = os.path.join(_T, "provision.json")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state  # noqa: E402

state.init_db()

from core import claim  # noqa: E402
from core.dispatch import app as flask_app  # noqa: E402

FAILS: list[str] = []
ORDER = "cs_live_Q7rTt2mK9xLp4vNb8wZa3cYd6eFg1hJk"


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


def provision(**extra):
    facts = {"buyer": "Acme", "order": ORDER, "host": "acme.ownbox.app", "box_type": "customer_voice"}
    facts.update(extra)
    with open(os.environ["AIOS_PROVISION_JSON"], "w", encoding="utf-8") as fh:
        json.dump(facts, fh)


def as_owner():
    c = flask_app.test_client()
    c.post("/dash/login", data={"token": "pw"})   # DASH_TOKEN break-glass: the field is `token`
    return c


def body(client=None):
    return (client or as_owner()).get("/dash/managed").get_data(as_text=True)


print("\n— the date the provisioner gave this box —")
provision(managed_until="2026-12-19T12:00:00+00:00")
ok("the box reads the day its free three months end", claim.provisioned_managed_until() == "2026-12-19T12:00:00+00:00")
provision()
ok("a box with no Managed reads nothing, and does not raise", claim.provisioned_managed_until() == "")
os.unlink(os.environ["AIOS_PROVISION_JSON"])
ok("a box nobody bought — no provision.json at all — reads nothing either", claim.provisioned_managed_until() == "")

print("\n— a managed box, told plainly —")
provision(managed_until="2026-12-19T12:00:00+00:00")
html = body()
ok("it names the day, as a person writes a day", "19 December 2026" in html, html[-600:])
ok("IT SAYS THE CHARGE IS COMING: a renewal nobody mentioned is the complaint we would deserve",
   "it renews after that" in html, html[-600:])
ok("...conditionally, because a buyer who already cancelled must not be told a charge is coming",
   "Unless you cancel" in html, html[-600:])
ok("...and that cancelling does not cost them the box they bought",
   "keeps running" in html and "stays yours" in html, html[-600:])

print("\n— how they stop it —")
from core.config import settings  # noqa: E402

settings.managed_portal_url = "https://billing.stripe.com/p/login/test_abc123"
html = body()
ok("with a portal configured, the cancel link is Stripe's own page",
   "https://billing.stripe.com/p/login/test_abc123" in html and "cancel" in html.lower(), html[-500:])
ok("...and the page names Stripe as the one that knows the CURRENT state, not this box",
   "current state" in html and "already cancelled, Stripe will say so" in html, html[-500:])
ok("NO CREDENTIAL OF OURS IS ON THE PAGE — the customer has root on this machine",
   not any(t in html for t in ("sk_live", "sk_test", "rk_live", "Bearer ")), html[-400:])
settings.managed_portal_url = ""
html = body()
ok("with none configured it gives a real instruction, not a dead button",
   "reply to your welcome email" in html and "billing.stripe.com" not in html, html[-500:])
ok("...and never renders an empty link", 'href=""' not in html)

print("\n— a box without Managed says so and stops —")
provision()
html = body()
ok("no service, no date, no cancel button", "does not have the Managed service" in html
   and "it renews after that" not in html, html[-400:])

print("\n— who may see it —")
anon = flask_app.test_client().get("/dash/managed")
ok("a stranger is sent to the login, never shown a billing date", anon.status_code == 302
   and "/dash/login" in anon.headers.get("Location", ""), str(anon.status_code))
provision(managed_until="2026-12-19T12:00:00+00:00")
ok("the owner sees it", as_owner().get("/dash/managed").status_code == 200)

print("\n— the page a person has to be able to FIND —")
# Not a test of the page: a note in the suite so the next person meets it. The route exists and the
# copy is right; it is linked from nothing, exactly like /dash/people, and a cancel door nobody can
# find is not click-to-cancel. The inbox chrome is OSDev4/OSDev5's lane, so the link is theirs to place.
src = (ROOT / "core/dash/__init__.py").read_text()
ok("the route is registered under a name a person could guess", '"/dash/managed"' in src)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
