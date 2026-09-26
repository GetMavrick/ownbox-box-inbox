"""What a box bought, and the features it switches on (docs/SCOPE_TIERS.md §4, piece 1).

The done-when, each shown on the path a box takes:
  · a box built as Pro answers allows("coworkers") true;
  · a Base box told {"seq": 2, "tier": "pro"} switches on without a restart, and its seat limit rises;
  · an older seq is refused, and every answer carries the plan the box holds;
  · with Ownbox unreachable the last answer stands (nothing here calls out, so nothing switches off);
  · an unknown tier or feature is refused and never stored;
plus: the door is the deploy token's alone, the table has display names, the seat limit only ever
widens, and two messages at once cannot let an older seq win.

Run: python tests/test_tiers.py
"""
import json
import os
import pathlib
import sys
import tempfile
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
T = pathlib.Path(tempfile.mkdtemp())
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DEPLOY_TOKEN"] = "deploy-narrow"
PROVISION = T / "provision.json"
os.environ["AIOS_PROVISION_JSON"] = str(PROVISION)

from core import claim, state, tiers  # noqa: E402

claim.PROVISION_JSON = str(PROVISION)
state.init_db()

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def fresh(tier=None):
    with state.connect() as c:
        c.execute("DELETE FROM box_settings WHERE machine = 'core' AND key = 'plan'")
    if tier is None:
        PROVISION.unlink(missing_ok=True)
    else:
        PROVISION.write_text(json.dumps({"tier": tier}))


print("the table")
ok("every tier has a display name, and Base's id is not its name",
   tiers.TIERS["ownbox"]["name"] == "Base Machine" and tiers.TIERS["pro"]["name"] == "Base Machine Pro")
ok("seats carry today's values: Base is the box's configured limit (3 as shipped), Pro unlimited",
   tiers.TIERS["ownbox"]["people"] is None and tiers.TIERS["pro"]["people"] == 0
   and tiers.current()["people"] == 3)
ok("coworkers are Pro's feature, and the known features are what the table names",
   "coworkers" in tiers.TIERS["pro"]["features"]
   and tiers.FEATURES == {"coworkers", "machine:aeo", "machine:inbox"})

print("where the answer comes from")
fresh("pro")
c = tiers.current()
ok("a box built as Pro answers allows('coworkers') true, from provision.json",
   tiers.allows("coworkers") and c["tier"] == "pro" and c["source"] == "provision.json", str(c))
fresh("ownbox")
ok("a box built as Base does not", not tiers.allows("coworkers")
   and tiers.current()["name"] == "Base Machine")
fresh(None)
c = tiers.current()
ok("a box with no recorded tier is Base, by default, and says so",
   c["tier"] == "ownbox" and c["source"] == "default" and not tiers.allows("coworkers"), str(c))
PROVISION.write_text(json.dumps({"tier": "platinum"}))
ok("an unknown tier in provision.json is Base, not a guess", tiers.current()["source"] == "default")
PROVISION.write_text("{not json")
ok("an unreadable provision.json is Base, and nothing raises", tiers.current()["tier"] == "ownbox")

print("Ownbox tells the box: POST /deploy/plan")
from core.dispatch import app  # noqa: E402

cl = app.test_client()
H = {"Authorization": "Bearer deploy-narrow"}
fresh("ownbox")
ok("the door is shut without the deploy token",
   cl.post("/deploy/plan", json={"seq": 1, "tier": "pro"}).status_code == 401)
ok("...and shut to the wide dashboard token",
   cl.post("/deploy/plan", json={"seq": 1, "tier": "pro"},
           headers={"Authorization": "Bearer bearer"}).status_code == 401)
ok("nothing was stored by either", tiers.current()["source"] == "provision.json")
seats_before = state.max_users()
r = cl.post("/deploy/plan", json={"seq": 2, "tier": "pro"}, headers=H)
body = r.get_json()
ok("a Base box told {seq 2, tier pro} switches on, with no restart",
   r.status_code == 200 and tiers.allows("coworkers") and tiers.current()["source"] == "ownbox",
   str(body))
ok("the reply is the plan the box now holds", body["plan"]["tier"] == "pro"
   and body["plan"]["seq"] == 2 and body["plan"]["name"] == "Base Machine Pro", str(body))
ok("and its seat limit rises: three to unlimited",
   seats_before == 3 and state.max_users() == 0, f"{seats_before} -> {state.max_users()}")
r = cl.post("/deploy/plan", json={"seq": 1, "tier": "ownbox"}, headers=H)
ok("an older seq is refused, and the reply still carries the plan held",
   r.status_code == 409 and r.get_json()["error"] == "stale_seq"
   and r.get_json()["plan"]["tier"] == "pro" and tiers.allows("coworkers"), str(r.get_json()))
r = cl.post("/deploy/plan", json={"seq": 2, "tier": "ownbox"}, headers=H)
ok("the same seq again is refused too (a retry after a lost reply learns the plan from the 409)",
   r.status_code == 409 and tiers.current()["tier"] == "pro")
for bad, label in (({"seq": 3, "tier": "platinum"}, "an unknown tier"),
                   ({"seq": 3, "tier": "ownbox", "add": ["teleport"]}, "an unknown feature"),
                   ({"seq": 3, "tier": "ownbox", "add": "coworkers"}, "add that is not a list"),
                   ({"tier": "ownbox"}, "no seq"), ({"seq": 0, "tier": "ownbox"}, "seq 0"),
                   ({"seq": "three", "tier": "ownbox"}, "a seq that is not a number")):
    r = cl.post("/deploy/plan", json=bad, headers=H)
    ok(f"{label} is refused and never stored", r.status_code == 400
       and r.get_json()["error"] == "bad_plan" and tiers.current()["seq"] == 2, str(r.get_json()))
ok("a body that is not JSON is refused",
   cl.post("/deploy/plan", data="junk", headers=H).status_code == 400 and tiers.current()["seq"] == 2)

print("a plan is a tier plus add-ons")
r = cl.post("/deploy/plan", json={"seq": 3, "tier": "ownbox", "add": ["coworkers"]}, headers=H)
c = tiers.current()
ok("a Base box with Coworkers added has the feature and Base's seats",
   r.status_code == 200 and tiers.allows("coworkers") and c["name"] == "Base Machine" and c["people"] == 3
   and c["add"] == ["coworkers"], str(c))
cl.post("/deploy/plan", json={"seq": 4, "tier": "ownbox"}, headers=H)
ok("the next plan without the add-on is exactly Base", not tiers.allows("coworkers"))

print("the last answer stands")
PROVISION.write_text(json.dumps({"tier": "pro"}))
ok("what Ownbox last said wins over provision.json, and nothing here calls out to Ownbox",
   tiers.current()["source"] == "ownbox" and tiers.current()["tier"] == "ownbox")
src = (ROOT / "core" / "tiers.py").read_text()
ok("core/tiers.py makes no network call, so an unreachable Ownbox can switch nothing off",
   not any(w in src for w in ("requests", "urllib", "core.net", "http")))

print("the seat limit only ever widens")
from core import config  # noqa: E402

real = config.get_config
fresh("ownbox")
config.get_config = lambda: {**real(), "dash": {"max_users": 10}}
ok("an owner who set more seats by hand keeps them on Base", state.max_users() == 10)
config.get_config = lambda: {**real(), "dash": {"max_users": 0}}
ok("an unlimited overlay stays unlimited", state.max_users() == 0)
config.get_config = real
config.get_config = lambda: {**real(), "dash": {"max_users": 2}}
ok("a box configured for fewer on Base keeps its configured number", state.max_users() == 2)
cl.post("/deploy/plan", json={"seq": 9, "tier": "pro"}, headers=H)
ok("...and Pro makes it unlimited whatever the config says", state.max_users() == 0)
fresh("ownbox")
config.get_config = real
ok("and the shipped Base config gives three", state.max_users() == 3)
real_people = tiers.TIERS["ownbox"]["people"]
tiers.TIERS["ownbox"]["people"] = 5                  # a tier with a finite number, as one may be
ok("a tier with a finite number raises a smaller configured limit", state.max_users() == 5)
config.get_config = lambda: {**real(), "dash": {"max_users": 10}}
ok("...and never lowers a larger one", state.max_users() == 10)
config.get_config = real
tiers.TIERS["ownbox"]["people"] = real_people
real_current = tiers.current
tiers.current = lambda: (_ for _ in ()).throw(RuntimeError("database is locked"))
ok("if the plan cannot be read, the config stands", state.max_users() == 3)
tiers.current = real_current

print("two messages at once")
fresh("ownbox")
codes = {}


def send(seq):
    codes[seq] = app.test_client().post("/deploy/plan", json={"seq": seq, "tier": "pro" if seq % 2
                                                              else "ownbox"}, headers=H).status_code


for _ in range(5):
    fresh("ownbox")
    codes.clear()
    ts = [threading.Thread(target=send, args=(s,)) for s in (5, 6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    if tiers.current()["seq"] != 6:
        break
ok("the higher seq always ends up held, whichever arrives first",
   tiers.current()["seq"] == 6 and tiers.current()["tier"] == "ownbox", f"{codes} {tiers.current()}")

print()
if _failed:
    print(f"{_failed} FAILED")
    sys.exit(1)
print("ALL OK")
