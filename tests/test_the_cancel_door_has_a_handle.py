"""The Managed cancel door is reachable from the screen a buyer actually lands on.

THE DOOR WAS BUILT AND NOTHING POINTED AT IT. #1229 (50de3880) shipped `/dash/managed` with
Ownbox's Stripe customer portal as the DEFAULT value of `MANAGED_PORTAL_URL`, so the page works on
every sold box, renders "Manage or cancel Managed", and returns 200. Measured on an exported
customer_voice box on 2026-09-17: `/dashboard`, `/dash/people`, `/inbox/settings` and
`/inbox/inbox` contained ZERO occurrences of `/dash/managed` or of the word Managed. The only way
in was to already know the URL.

WHY THAT IS NOT A NICETY. The owner ruled (2026-09-15, OSDev2's session, on the wall) that Managed
is its own Stripe product with a ninety-day free trial that DOES auto-renew at $99/mo, and that it
needs "self-serve cancel in the box, one click to the Stripe portal, as easy as ticking the box
(FTC click-to-cancel)". A page nobody can find is not one click, and the checkout copy promises
that cancelling before day 90 costs nothing — a promise the box could not keep from any screen it
shows on its own.

WHAT THIS SUITE HOLDS:
  · a box that never bought Managed says nothing about it — no card, no link, no invitation to go
    looking for a charge that does not exist
  · a box that did gets the card, with the date in words, and a link to the real page
  · a member does not see it, and — the half that matters — the ROUTE refuses them too
  · the card never claims that cancelling stops the box

Run: python tests/test_the_cancel_door_has_a_handle.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "managed.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state                                                   # noqa: E402

state.init_db()

from core import claim as _claim                                         # noqa: E402
from core import dash as _dash                                           # noqa: E402
from core.dispatch import app                                            # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def owner():
    c = app.test_client()
    c.set_cookie(_dash.COOKIE, _dash.new_session(state.owner_user()["id"]))
    return c


def set_managed(value):
    """Write what the PROVISIONER writes. `managed_until` reaches a box in provision.json, from
    the cart's line items — never from the database — so this is the only honest way to seed it."""
    path = _claim.PROVISION_JSON
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if value is None:
        if os.path.exists(path):
            os.remove(path)
        return
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"managed_until": value}, fh)


# ── 1. most boxes never bought it, and hear nothing about it ─────────────────────────
print("test_a_box_without_managed_says_nothing_about_it")
set_managed(None)
home = owner().get("/dashboard").get_data(as_text=True)
ok("the dashboard renders", len(home) > 500, f"{len(home)} bytes")
ok("...and does not mention Managed at all", "Managed" not in home)
ok("...and offers no link to a page about it", "/dash/managed" not in home)


# ── 2. the box that DID buy it gets the handle ───────────────────────────────────────
print("\ntest_a_box_that_bought_managed_can_reach_the_cancel_door_from_its_home")
set_managed("2026-12-16")
ok("the provisioner's value is what the box reads",
   _claim.provisioned_managed_until() == "2026-12-16")
home = owner().get("/dashboard").get_data(as_text=True)
ok("the dashboard now links to the cancel door", 'href="/dash/managed"' in home,
   "a buyer would still have to guess the URL")
# THE DATE, IN WORDS, WITHOUT A SECOND CLICK. What a person wants from a dashboard is whether
# anything is about to happen to their card.
ok("...and names the day the free months end, in words rather than an ISO string",
   "16 December 2026" in home and "2026-12-16" not in home, home[home.find("Managed"):][:200])
ok("...and says plainly that it renews unless they cancel", "it renews" in home)
# THE ONE SENTENCE THAT STOPS THIS READING AS A THREAT. Cancelling a $99/mo service must not look
# like it takes the box with it — the box is theirs either way, and that is the whole pitch.
ok("...and that cancelling does not take the box with it",
   "does not touch the box" in home)


# ── 3. it leads to the REAL thing, not another signpost ──────────────────────────────
print("\ntest_the_link_lands_on_the_page_that_actually_cancels")
page = owner().get("/dash/managed")
ok("the page it points at exists", page.status_code == 200, str(page.status_code))
body = page.get_data(as_text=True)
ok("...and carries the button, not another link to somewhere else",
   "Manage or cancel Managed" in body, body[-300:])
# A CANCEL CONTROL MUST GO TO STRIPE. The box holds no billing credential — it runs on a droplet
# the customer has root on — so the only honest door is the portal Stripe already runs.
ok("...which goes to Stripe's own portal", "billing.stripe.com" in body)


# ── 4. a member does not see it, and the route refuses them anyway ───────────────────
print("\ntest_billing_is_the_owners")
sam = state.add_user("sam@acme.test", name="Sam", role="member")
member = app.test_client()
member.set_cookie(_dash.COOKIE, _dash.new_session(sam["id"]))
with app.test_request_context("/", headers={"Cookie": f"{_dash.COOKIE}={_dash.new_session(sam['id'])}"}):
    from flask import request as _rq
    who = _dash.session_user(_rq) or {}
# WITHOUT THIS THE REFUSALS BELOW PROVE NOTHING. A member who was never signed in is refused as a
# STRANGER, which is a different gate and would pass whatever the role rules said.
ok("the member really is signed in — otherwise the refusals below are vacuous",
   who.get("role") == "member", str(who))
r = member.get("/dash/managed")
ok("the ROUTE refuses a member, which is the half that matters", r.status_code == 403,
   str(r.status_code))
ok("...and tells them why rather than showing a blank refusal",
   "Only the box owner" in r.get_data(as_text=True))
mhome = member.get("/dashboard")
if mhome.status_code == 200:
    ok("...and the card is not drawn for them either",
       "/dash/managed" not in mhome.get_data(as_text=True))
else:
    # A member bounced from /dashboard cannot see the card by construction; say which case ran
    # rather than reporting a green that came from a redirect.
    ok(f"...and /dashboard does not serve them at all ({mhome.status_code})", True)


# ── 5. a date we cannot parse is shown, never swallowed ──────────────────────────────
print("\ntest_an_unreadable_date_is_still_shown")
# A CARD THAT VANISHES ON A MALFORMED VALUE is the worst outcome: the buyer loses the cancel link
# over a formatting problem they cannot see and we would never hear about.
set_managed("whenever")
home = owner().get("/dashboard").get_data(as_text=True)
ok("the card survives a date it cannot format", 'href="/dash/managed"' in home)
ok("...and prints what it was given", "whenever" in home)
set_managed(None)

print("\nall ok" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
