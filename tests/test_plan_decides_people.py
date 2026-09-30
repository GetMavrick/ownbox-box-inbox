"""A Base box stays Base: the plan decides how many people can sign in, and reaching it offers Pro.

docs/PLAN_TIER_INTEGRITY.md step 1 (owner-approved 2026-09-30). Its done-when, on the path a box takes:
  · the owner tries to add a fourth person and is offered the upgrade;
  · a `dash.max_users: 50` line in `my/settings.yaml` leaves the limit at 3;
  · a Pro box adds a fourth and a fifth;
  · a box already over its limit keeps every person it has;
  · the box's AI is told the plan is Ownbox's (the rule in the box's CLAUDE.md).
Plus the rules around it: the config cannot even lower the plan's number, a box nobody sold keeps its
configured number, and a box that can't upgrade is never shown a button that could not work.

Run: python tests/test_plan_decides_people.py
"""
import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
T = pathlib.Path(tempfile.mkdtemp())
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "the-api-key-not-a-password"
os.environ["DASH_TOKEN"] = "the-owners-break-glass-token"
os.environ["DEPLOY_TOKEN"] = "deploy-narrow"
PROVISION = T / "provision.json"
os.environ["AIOS_PROVISION_JSON"] = str(PROVISION)

from core import claim, config, state, tiers  # noqa: E402

claim.PROVISION_JSON = str(PROVISION)
state.init_db()
from core import dash  # noqa: E402
from core.config import settings  # noqa: E402
from core.dispatch import app as flask_app  # noqa: E402

_failed = 0
H = {"Authorization": "Bearer deploy-narrow"}
REAL = config.get_config
_seq = [0]


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def box(tier="ownbox", *, people=0, max_users=None):
    """A fresh box: built by Ownbox as `tier` (None = nobody sold it), `people` active members,
    and `max_users` as if written in my/settings.yaml (None = the shipped line)."""
    with state.connect() as c:
        c.execute("DELETE FROM box_settings WHERE machine = 'core' AND key = 'plan'")
        c.execute("DELETE FROM users WHERE role != 'owner'")
        for i in range(people):
            c.execute("INSERT INTO users (id, email, role, active, created_at) VALUES (?,?,?,1,?)",
                      (f"usr_{i}", f"p{i}@co.com", "member", "2026-09-30T00:00:00Z"))
    if tier is None:
        PROVISION.unlink(missing_ok=True)
    else:
        PROVISION.write_text(json.dumps({"tier": tier, "order": "cs_test_1"}))
    config.get_config = REAL if max_users is None else (
        lambda: {**REAL(), "dash": {**(REAL().get("dash") or {}), "max_users": max_users}})


def members() -> int:
    with state.connect() as c:
        return c.execute("SELECT COUNT(*) AS n FROM users WHERE active = 1 AND role != 'owner'").fetchone()["n"]


def add(email) -> bool:
    try:
        state.add_user(email)
        return True
    except state.SeatsFull:
        return False


def push(tier):
    _seq[0] += 1
    r = flask_app.test_client().post("/deploy/plan", json={"seq": 100 + _seq[0], "tier": tier}, headers=H)
    assert r.status_code == 200, r.get_json()


def owner_client():
    dash._fails.clear()
    c = flask_app.test_client()
    r = c.post("/dash/login", data={"token": settings.dash_token})
    assert r.status_code in (302, 303), r.status_code
    return c


owner_rows = state.count_active_users() - 0          # the owner's own row, if migrations made one
print("the plan decides, and the config has no say")
box("ownbox")
ok("a Base box Ownbox built carries three people", state.max_users() == 3, str(state.max_users()))
box("ownbox", max_users=50)
ok("A `max_users: 50` LINE IN my/settings.yaml LEAVES BASE AT THREE", state.max_users() == 3,
   str(state.max_users()))
box("ownbox", max_users=0)
ok("...and so does an 'unlimited' line", state.max_users() == 3, str(state.max_users()))
box("ownbox", max_users=2)
ok("...nor one that lowers it: the tracked config ships 3 to every box, so 'lower only' would cap "
   "a bigger plan at a number nobody chose", state.max_users() == 3, str(state.max_users()))
box("pro")
ok("a Pro box is unlimited, though the tracked config ships 3 to every box", state.max_users() == 0,
   str(state.max_users()))
box("ownbox")
push("pro")
ok("a Base box Ownbox upgrades becomes unlimited at once", state.max_users() == 0)
box("ownbox", max_users=50)
push("ownbox")
ok("a plan from Ownbox counts as built by Ownbox, and holds Base at three", state.max_users() == 3)

print("a box nobody sold keeps its configured number")
box(None)
ok("the shipped line: three", state.max_users() == 3, str(state.max_users()))
box(None, max_users=50)
ok("the owner's own box, set to 50, stays at 50: no release caps the team by surprise",
   state.max_users() == 50, str(state.max_users()))

print("adding people")
box("ownbox", people=3 - owner_rows)
ok("at the limit, the next person is refused", not add("fourth@co.com"))
box("pro", people=3 - owner_rows)
ok("a Pro box adds a fourth...", add("fourth@co.com"))
ok("...and a fifth", add("fifth@co.com") and members() == 5 - owner_rows, str(members()))
box("ownbox", people=6)
ok("A BOX ALREADY OVER ITS LIMIT KEEPS EVERY PERSON IT HAS", members() == 6, str(members()))
ok("...and only the next one waits for the upgrade", not add("seventh@co.com") and members() == 6)
with state.connect() as c:
    c.execute("UPDATE users SET active = 0 WHERE id = 'usr_5'")
ok("...a person removed there can't be restored past the limit", not add("p5@co.com"))

print("the People screen")
box("ownbox", people=3 - owner_rows)
oc = owner_client()
page = oc.get("/dash/people").get_data(as_text=True)
ok("at the limit the owner is told what the plan covers", "Base Machine covers 3 people." in page, page[-1500:])
ok("...and offered Upgrade to Pro, pointing at the one upgrade sheet",
   'href="/dashboard/upgrade"' in page and ">Upgrade to Pro<" in page)
ok("...instead of an invite form that could only fail", 'action="/dash/people/invite"' not in page)
r = oc.post("/dash/people/invite", data={"email": "fourth@co.com"})
body = r.get_data(as_text=True)
ok("an invite at the limit is refused, and the refusal offers the upgrade first",
   r.status_code == 409 and "Upgrade to Pro for unlimited people, or remove someone first." in body, body[-1500:])
ok("...with one Upgrade button on the screen, not two", body.count(">Upgrade to Pro<") == 1,
   str(body.count(">Upgrade to Pro<")))
box("ownbox", people=1)
page = oc.get("/dash/people").get_data(as_text=True)
ok("below the limit, the invite form is there and no upgrade is pushed",
   'action="/dash/people/invite"' in page and "/dashboard/upgrade" not in page)
box(None, people=3 - owner_rows)
page = oc.get("/dash/people").get_data(as_text=True)
ok("a box nobody sold, at its limit, is never offered an upgrade it could not buy, and keeps its form",
   "/dashboard/upgrade" not in page and 'action="/dash/people/invite"' in page, page[-1500:])
member_view = flask_app.test_client()
ok("a member never reaches the People screen (only the owner can pay)",
   member_view.get("/dash/people").status_code in (302, 303))

print("the box's AI is told")
# ON A BOX THE FOUNDATION'S CLAUDE.md IS THE ROOT ONE (scripts/export_box.sh copies it there), and this
# suite ships in the box (tests/test_recipe_ships.py runs it there), so read whichever this tree has.
_rule = ROOT / "docs" / "box" / "foundation" / "CLAUDE.md"
rule = (_rule if _rule.exists() else ROOT / "CLAUDE.md").read_text()
ok("the box's CLAUDE.md says the plan is Ownbox's, and not a setting",
   "## The plan is Ownbox's" in rule and "It is not a setting." in rule)
ok("...tells it never to edit a setting, file or database row to raise it",
   "do not edit any setting, file or database row" in rule)
ok("...and to explain the upgrade instead", "/dashboard/upgrade" in rule and "System Settings → People" in rule)
ok("the settings table no longer says every key can be overridden", "(not the plan: see below)" in rule)

config.get_config = REAL
print("\n" + ("ALL OK" if not _failed else f"{_failed} FAILED"))
sys.exit(1 if _failed else 0)
