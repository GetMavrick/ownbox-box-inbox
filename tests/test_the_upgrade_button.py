"""Upgrade your box to Pro: the dashboard's button, its sheet, and the progress line.

docs/SCOPE_UPGRADE_TO_PRO.md §4 piece 4's done-when: *an owner on a mobile device at 390px reads what
Pro adds and how long the box is offline, and reaches the test-mode checkout.*

  · the owner of a Base box sees one ink pill, "Upgrade your box to Pro now", with the downtime;
  · the sheet lists what Pro adds from the Tiers table, the price, and the restart, and its button
    opens the upgrade link carrying this box's order id and the owner's email;
  · nobody but the owner sees the button or the sheet; a Pro box sees neither;
  · once paid, the dashboard shows the stage the provisioner last reported instead of the button;
  · no link yet, or no order on this box: said plainly, and no button to press.

Run: python tests/test_the_upgrade_button.py
"""
import json
import os
import pathlib
import re
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = _T + "/upgrade.db"
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
os.environ["AIOS_MY_MACHINES"] = os.path.join(_T, "none")
PROVISION = pathlib.Path(_T) / "provision.json"
os.environ["AIOS_PROVISION_JSON"] = str(PROVISION)
os.environ.pop("ANTHROPIC_API_KEY", None)

from core import state  # noqa: E402

state.init_db()

import core  # noqa: E402
from core import claim, dash  # noqa: E402
from core.dash import upgrade  # noqa: E402
from core.dispatch import app  # noqa: E402

claim.PROVISION_JSON = str(PROVISION)
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


# THE PLAN AND THE STAGE COME FROM core.tiers AND core.upgrade. Tests set them through a fixture
# (SCOPE_TIERS §3): the real TIERS table is read as it is, and current() and status() are replaced.
# A checkout without either module still runs, with a stand-in holding the same shapes.
try:
    from core import tiers  # noqa: E402
except ImportError:
    tiers = types.ModuleType("core.tiers")
    sys.modules["core.tiers"] = core.tiers = tiers
    tiers.TIERS = {"ownbox": {"name": "Base", "features": frozenset(), "people": None},
                   "pro": {"name": "Pro", "features": frozenset({"coworkers"}), "people": 0}}
try:
    from core import upgrade as box_upgrade  # noqa: E402
except ImportError:
    box_upgrade = types.ModuleType("core.upgrade")
    sys.modules["core.upgrade"] = core.upgrade = box_upgrade
PLAN, STATUS = {}, {}
tiers.current = lambda: dict(PLAN)
box_upgrade.status = lambda: dict(STATUS)


def plan(tier, people):
    PLAN.clear()
    t = tiers.TIERS[tier]
    PLAN.update({"tier": tier, "name": t["name"], "add": [], "features": sorted(t["features"]),
                 "people": people, "source": "ownbox"})


def stage(name, *, ago=timedelta(0)):
    STATUS.clear()
    if name:
        STATUS.update({"stage": name, "detail": "",
                       "at": (datetime.now(timezone.utc) - ago).isoformat()})


def main(page: str) -> str:
    return page.split("</nav>", 1)[-1]


def ink_pills(page: str) -> list:
    m = main(page)
    out = []
    for b in re.finditer(r"<button\b([^>]*)>(.*?)</button>", m, re.S):
        cls = set((re.search(r'class="([^"]*)"', b.group(1)) or [None, ""])[1].split())
        if not cls & {"ghost", "danger"}:
            out.append(re.sub(r"\s+", " ", b.group(2)).strip())
    out += [re.sub(r"<[^>]+>", "", a) for a in re.findall(r'<a class="btn"[^>]*>(.*?)</a>', m)]
    return out


_RESERVED = re.compile(r"\b(phones?|rings?|calls?|dial|lines?|voice)\b", re.I)


def reserved(page: str) -> list:
    text = re.sub(r"<style>.*?</style>", "", main(page), flags=re.S)
    return sorted(set(m.lower() for m in _RESERVED.findall(text)))


OWNER_EMAIL = "owner@studio.example"
with state.connect() as c:
    c.execute("UPDATE users SET email = ? WHERE id = ?", (OWNER_EMAIL, state.owner_user()["id"]))
PROVISION.write_text(json.dumps({"tier": "ownbox", "order": "cs_test_a1B2c3"}))

owner = app.test_client()
owner.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
member = app.test_client()
member.set_cookie(dash.COOKIE, dash.new_session(
    state.add_user("coach@studio.example", role="member")["id"]))

print("\ntest_a_base_box_shows_the_owner_the_button")
plan("ownbox", 3)
stage(None)
page = owner.get("/dashboard").get_data(as_text=True)
ok("the dashboard's one ink pill is Upgrade your box to Pro now",
   ink_pills(page) == ["Upgrade your box to Pro now"], str(ink_pills(page)))
ok("...with the downtime under it", "Your box will be offline for about 3 minutes." in page)
ok("...and it opens the sheet", 'href="/dashboard/upgrade">Upgrade your box to Pro now' in page)
ok("none of the reserved nouns", reserved(page) == [], str(reserved(page)))
page = member.get("/dashboard").get_data(as_text=True)
ok("a member sees no button", "Upgrade your box to Pro" not in page)
r = member.get("/dashboard/upgrade")
ok("...and is refused at the sheet, told it is the owner's", r.status_code == 403
   and "Only the owner" in r.get_data(as_text=True))

print("\ntest_the_sheet_says_what_pro_adds_and_what_happens")
page = owner.get("/dashboard/upgrade").get_data(as_text=True)
ok("what Pro adds comes from the Tiers table: coworkers and unlimited people",
   "Coworkers" in page and "Unlimited people" in page)
ok("the price, paid once", "$1,100</b>, paid once" in page)
ok("each thing Pro adds ends with one full stop, never two", ".." not in re.sub(r"<[^>]+>", "", main(page)))
ok("the restart, in plain words, with the same downtime",
   "Your box restarts once, for about 3 minutes, right after you pay." in page)
ok("no link yet: it says so, and there is nothing to press",
   "The payment page isn't open yet." in page and ink_pills(page) == [], str(ink_pills(page)))
ok("none of the reserved nouns", reserved(page) == [], str(reserved(page)))

print("\ntest_it_reaches_the_checkout_with_this_box_named")
upgrade.UPGRADE_LINK = "https://buy.stripe.com/test_upgrade123"
page = owner.get("/dashboard/upgrade").get_data(as_text=True)
ok("one ink pill, and it is Go to payment", ink_pills(page) == ["Go to payment"], str(ink_pills(page)))
href = re.search(r'<a class="btn" href="([^"]+)"', main(page)).group(1).replace("&amp;", "&")
q = parse_qs(urlparse(href).query)
ok("it is the upgrade link", href.startswith("https://buy.stripe.com/test_upgrade123?"), href)
ok("...carrying this box's order id", q.get("client_reference_id") == ["cs_test_a1B2c3"], str(q))
ok("...and the owner's email", q.get("prefilled_email") == [OWNER_EMAIL], str(q))
ok("...in a new tab", 'target="_blank"' in page)
ok("it says where progress will show", "your dashboard shows the upgrade's progress" in page)
upgrade.UPGRADE_LINK = "https://buy.stripe.com/test_upgrade123?locale=en"
href = re.search(r'<a class="btn" href="([^"]+)"',
                 main(owner.get("/dashboard/upgrade").get_data(as_text=True))).group(1)
ok("a link that already has a query keeps it", "?locale=en&amp;client_reference_id=" in href, href)

print("\ntest_a_box_with_no_order_is_told_before_paying")
PROVISION.write_text(json.dumps({"tier": "ownbox"}))
page = owner.get("/dashboard/upgrade").get_data(as_text=True)
ok("it says it can't be upgraded from here, and offers no payment",
   "no order on record" in page and ink_pills(page) == [], str(ink_pills(page)))
PROVISION.write_text(json.dumps({"tier": "ownbox", "order": "cs_test_a1B2c3"}))

print("\ntest_one_number_for_the_downtime")
upgrade.OFFLINE_MINUTES = 4
ok("the card and the sheet both follow it",
   "offline for about 4 minutes" in owner.get("/dashboard").get_data(as_text=True)
   and "for about 4 minutes, right after you pay" in owner.get("/dashboard/upgrade").get_data(as_text=True))
upgrade.OFFLINE_MINUTES = 3

print("\ntest_the_progress_line")
stage("paid")
page = owner.get("/dashboard").get_data(as_text=True)
ok("paid: Payment received, and the button is gone",
   "Payment received" in page and "restarts in about a minute" in page
   and "Upgrade your box to Pro now" not in page)
ok("...and the sheet says the same, with nothing to pay",
   "Payment received" in owner.get("/dashboard/upgrade").get_data(as_text=True)
   and ink_pills(owner.get("/dashboard/upgrade").get_data(as_text=True)) == [])
stage("restarting")
ok("restarting: it says the box is restarting to finish",
   "restarting to finish the upgrade" in owner.get("/dashboard").get_data(as_text=True))
stage("held")
page = owner.get("/dashboard").get_data(as_text=True)
ok("held: taking longer, and Ownbox has been told",
   "taking longer than it should" in page and "Ownbox has been told." in page)
stage("failed")
ok("failed: the same words, never a technical one",
   "taking longer than it should" in owner.get("/dashboard").get_data(as_text=True))
ok("a member sees no progress either", "taking longer" not in member.get("/dashboard").get_data(as_text=True))
plan("pro", 0)
stage("done")
page = owner.get("/dashboard").get_data(as_text=True)
ok("done: You're on Pro, including Coworkers", "You're on Pro" in page
   and "Pro, including Coworkers" in page)
stage("done", ago=timedelta(days=2))
page = owner.get("/dashboard").get_data(as_text=True)
ok("...for a day, then it goes", "You're on Pro" not in page)
ok("a Pro box shows no button", "Upgrade your box to Pro" not in page)
page = owner.get("/dashboard/upgrade").get_data(as_text=True)
ok("...and its sheet says it is on Pro already", "Pro, including Coworkers already" in page
   and ink_pills(page) == [])

print("\ntest_an_add_on_counts")
plan("ownbox", 3)
PLAN.update({"add": ["coworkers"], "features": ["coworkers"]})
stage(None)
page = owner.get("/dashboard/upgrade").get_data(as_text=True)
ok("a Base box that already has coworkers is offered only what it lacks",
   "Unlimited people" in page and "<b>Coworkers</b>" not in page)

print("\n" + ("all good" if not _failed else f"{_failed} FAILED"))
sys.exit(1 if _failed else 0)
