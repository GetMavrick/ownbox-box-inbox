"""Machines ship coworkers; the owner hires them (docs/SCOPE_SHIFTS.md §5, §4.3).

  · a machine's coworkers/<name>/ is OFFERED, never run where it is;
  · only a machine that declares requires_foundation "1.2" may ship them, and the box says so;
  · the permission sheet says what it wants, what it can't do, what the box does for it, when it
    works, and the §4.3 risk when it reads data and reaches the web;
  · hiring copies exactly the coworker file and its job into my/coworkers, switched on, and the
    tick then runs it; a hire that needs the OK is refused without it;
  · nothing is half-copied, and a name already taken is refused.

Run: python tests/test_coworker_hire.py
"""
import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = tempfile.mkdtemp() + "/hire.db"
MACHINES = pathlib.Path(tempfile.mkdtemp())
MINE = pathlib.Path(tempfile.mkdtemp())
os.environ["AIOS_MY_MACHINES"] = str(MACHINES)
os.environ["AIOS_MY_COWORKERS"] = str(MINE)

from core import state  # noqa: E402

state.init_db()

from core import packs  # noqa: E402
from core.connector import tools  # noqa: E402
from core.coworkers import contract, hire, schedule  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def machine(slug, *, foundation="1.2", coworkers=None):
    m = MACHINES / slug
    m.mkdir()
    (m / "machine.yaml").write_text(f'name: {slug}\nversion: 1\nrequires_foundation: "{foundation}"\n')
    (m / "__init__.py").write_text("")
    for name, spec in (coworkers or {}).items():
        c = m / "coworkers" / name
        c.mkdir(parents=True)
        (c / "coworker.yaml").write_text(json.dumps({
            "coworker": 1, "title": spec.get("title", name.title()), "job": "job.md",
            "from": spec.get("from", slug), "may": spec["may"],
            "shifts": [{"days": "Mon-Fri", "start": "10:00", "latest": "10:30"}],
            "steps": {"before": [], "after": spec.get("after", [])}, "enabled": False}))
        (c / "job.md").write_text(f"Be the {name}.")
        (c / "extra.sh").write_text("rm -rf /")                    # never copied


ok("the box speaks foundation 1.2", packs.foundation_version() == hire.FOUNDATION)

machine("acme-social", coworkers={
    "poster": {"may": ["read:reports", "write:proposals"], "title": "Poster",
               "after": ["acme-social.schedule_post"]},
    "scout": {"may": ["read:inbox", "web:read"], "title": "Scout"},
    "stray": {"may": ["read:reports"], "from": "someone-else"}})
machine("old-machine", foundation="1.1", coworkers={"helper": {"may": ["read:reports"]}})
machine("no-coworkers")
tools._reset_for_tests()
tools.register("schedule_post", fn=lambda **k: None, description="schedule the posts you approved",
               machine="acme-social", capability="act:publish_posts", min_role="act",
               args={a: {"type": t, "required": True} for a, t in contract.STEP_ARGS.items()})

print("offers")
got = {(o["machine"], o["name"]): o for o in hire.offers()}
ok("a machine's coworkers are offered", ("acme-social", "poster") in got
   and got[("acme-social", "poster")]["coworker"] is not None)
ok("a machine with no coworkers offers none", not any(m == "no-coworkers" for m, _ in got))
o = got[("old-machine", "helper")]
ok("a machine on foundation 1.1 may not ship them, and is told the fix",
   o["coworker"] is None and 'requires_foundation "1.2"' in o["reason"], o["reason"])
o = got[("acme-social", "stray")]
ok("a coworker whose file names another machine is refused",
   o["coworker"] is None and "from: someone-else" in o["reason"], o["reason"])
ok("an offer is never run where it is: the tick sees only my/coworkers",
   hire._mine() == MINE and not list(MINE.iterdir()))

print("the permission sheet")
poster = got[("acme-social", "poster")]["coworker"]
sh = hire.sheet(poster, machine="acme-social")
ok("who, by machine and title", sh["who"] == "acme-social's Poster")
ok("what it wants, in the owner's words",
   sh["wants"] == ["read your morning report", "draft things for your approval"], str(sh["wants"]))
ok("what it can't do", sh["cannot"] == ["send, publish or pay for anything",
                                        "see your keys or passwords"])
ok("what the box does for it, from the step tool's own description",
   sh["box_does"] == ["After its shift: schedule the posts you approved"], str(sh["box_does"]))
ok("when it works", sh["works"] == ["Weekdays, starting between 10:00 and 10:30"], str(sh["works"]))
ok("no risk sentence when it never reaches the web", sh["risk"] is None)
scout = got[("acme-social", "scout")]["coworker"]
ok("the risk sentence when it reads data and reaches the web",
   hire.sheet(scout, machine="acme-social")["risk"] == hire.RISK)
ok("an unknown capability is described from its name, never blank",
   hire._want("read:client_notes") == "read your client notes")

def fp(machine, name):
    o = next((o for o in hire.offers() if o["machine"] == machine and o["name"] == name), None)
    return hire.sheet(o["coworker"], machine=machine)["fingerprint"] if o and o["coworker"] else "x"


def H(machine, name, **kw):
    """Hire as the screen does: with the fingerprint of the sheet the owner just saw."""
    return hire.hire(machine, name, fingerprint=fp(machine, name), by="owner-1", **kw)


print("hiring")
cw = H("acme-social", "poster")
ok("hired into my/coworkers, switched on, still from its machine",
   cw.slug == "poster" and cw.enabled and cw.source == "acme-social"
   and (MINE / "poster" / "coworker.yaml").is_file())
ok("exactly the coworker file and its job are copied, nothing else",
   sorted(p.name for p in (MINE / "poster").iterdir()) == ["coworker.yaml", "job.md"])
ok("the times survive the copy as the same times", cw.shifts[0].start == "10:00"
   and cw.shifts[0].latest == "10:30")
ok("the offer now shows as hired", next(o for o in hire.offers() if o["name"] == "poster")["hired"])
ok("no staging folder is left behind", not (MINE / ".hiring" / "poster").exists())
from datetime import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402
from core.coworkers import runner  # noqa: E402
cws, _ = runner.discover()
p = schedule.plan(cws, now=datetime(2026, 9, 28, 10, 0, tzinfo=ZoneInfo("UTC")), since=None, taken=())
ok("and the tick finds it due at its first shift", [s.key for s in p.due]
   == ["poster@2026-09-28T10:00"], str(p.due))
for machine_, name, want in (("acme-social", "poster", "already have a coworker called poster"),
                             ("acme-social", "stray", "can't be hired"),
                             ("acme-social", "nobody", "does not offer"),
                             ("old-machine", "helper", "requires_foundation")):
    try:
        H(machine_, name)
        ok(f"hiring {machine_}/{name} is refused", False)
    except ValueError as e:
        ok(f"hiring {machine_}/{name} is refused: {want}", want in str(e), str(e))

print("the OK for data and the web together")
try:
    H("acme-social", "scout")
    ok("a coworker that needs the OK can't be hired without it", False)
except ValueError as e:
    ok("a coworker that needs the OK can't be hired without it, and the sentence is shown",
       hire.RISK in str(e) and not (MINE / "scout").exists())
cw = H("acme-social", "scout", accept_risk=True)
ok("with the tick it is hired, and the OK is recorded for its exact grant",
   hire.acknowledged(cw))
wider = contract.Coworker(**{**cw.__dict__, "may": ("read:inbox", "read:spend", "web:read")})
ok("the same coworker with a wider grant is not covered", not hire.acknowledged(wider))
ok("the OK is kept by the box, never written into the file",
   "risk" not in (MINE / "scout" / "coworker.yaml").read_text())
ok("a coworker without the risk needs no OK", hire.acknowledged(poster))

print("OSDev1's review of #1615")
import json as _json  # noqa: E402
from core import box_settings, config  # noqa: E402

# MAJOR 1: what the sheet showed is what gets hired.
machine("acme-ops", coworkers={"clerk": {"may": ["read:reports"], "title": "Clerk"}})
seen = fp("acme-ops", "clerk")
f = MACHINES / "acme-ops" / "coworkers" / "clerk" / "coworker.yaml"
orig = f.read_text()
for change, label in ((lambda d: d.update(may=d["may"] + ["read:spend"]), "a wider grant"),
                      (lambda d: d["steps"].update(after=["acme-social.schedule_post"]), "a new act: step"),
                      (lambda d: d.update(shifts=[{"days": "Daily", "start": "03:00",
                                                    "latest": "03:30"}]), "new hours")):
    d = _json.loads(orig)
    change(d)
    f.write_text(_json.dumps(d))
    try:
        hire.hire("acme-ops", "clerk", fingerprint=seen, by="owner-1")
        ok(f"an offer changed after the sheet ({label}) is not hired", False)
    except ValueError as e:
        ok(f"an offer changed after the sheet ({label}) is not hired, and the owner is told",
           "has changed since you looked" in str(e) and not (MINE / "clerk").exists(), str(e))
f.write_text(orig)
ok("the unchanged offer hires with the sheet's fingerprint",
   hire.hire("acme-ops", "clerk", fingerprint=seen, by="owner-1").slug == "clerk")

# MINOR 2: "hired" means this machine's coworker, not any folder of that name.
machine("acme-two", coworkers={"helper2": {"may": ["read:reports"], "title": "Helper"}})
own = MINE / "helper2"
own.mkdir()
(own / "coworker.yaml").write_text(_json.dumps({
    "coworker": 1, "title": "Mine", "job": "job.md", "from": "my", "may": ["read:reports"],
    "shifts": [{"days": "Mon-Fri", "start": "09:00", "latest": "09:30"}], "enabled": True}))
(own / "job.md").write_text("Mine.")
ok("an owner's own folder of the same name is not this machine's offer hired",
   next(o for o in hire.offers() if o["name"] == "helper2")["hired"] is False)

# MINOR 3: the OK is read from the database only, never from a config file.
scout_cw = cw
with state.connect() as c:
    c.execute("DELETE FROM box_settings WHERE machine = 'coworkers'")
real_cfg = config.get_config
config.get_config = lambda: {**real_cfg(), "coworkers": {hire._key(scout_cw): sorted(scout_cw.may)}}
try:
    ok("an OK written in a config file does not count", not hire.acknowledged(scout_cw))
finally:
    config.get_config = real_cfg

# MINOR 4 and 5: keyed on where it came from, and who gave it is recorded.
hire.acknowledge(scout_cw, by="owner-1")
mine_scout = contract.Coworker(**{**scout_cw.__dict__, "source": "my"})
ok("the OK for a hired scout never covers the owner's own scout of the same name",
   hire.acknowledged(scout_cw) and not hire.acknowledged(mine_scout))
with state.connect() as c:
    row = c.execute("SELECT set_by FROM box_settings WHERE machine = 'coworkers' AND key = ?",
                    (hire._key(scout_cw),)).fetchone()
ok("who gave the OK is recorded", row and row["set_by"] == "owner-1", str(row and dict(row)))

# MINOR 6: the OK is saved before the coworker exists; a failed save hires nothing.
machine("acme-three", coworkers={"spy": {"may": ["read:inbox", "web:read"], "title": "Spy"}})
real_put = box_settings.put
box_settings.put = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("database is locked"))
try:
    H("acme-three", "spy", accept_risk=True)
    ok("a failed OK save hires nothing", False)
except RuntimeError:
    ok("a failed OK save hires nothing, so a retry works", not (MINE / "spy").exists()
       and not (MINE / ".hiring" / "spy").exists())
finally:
    box_settings.put = real_put
ok("...and the retry does", H("acme-three", "spy", accept_risk=True).slug == "spy")

# MINOR 7: the owner can give the OK for their own coworker from the server.
import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location("coworker_ok", ROOT / "scripts" / "coworker_ok.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
lab = MINE / "lab"
lab.mkdir()
(lab / "coworker.yaml").write_text(_json.dumps({
    "coworker": 1, "title": "Lab", "job": "job.md", "from": "my", "may": ["read:inbox", "web:search"],
    "shifts": [{"days": "Mon-Fri", "start": "11:00", "latest": "11:30"}], "enabled": True}))
(lab / "job.md").write_text("Research.")
lab_cw, _ = contract.load(lab)
import builtins  # noqa: E402
real_input = builtins.input
builtins.input = lambda prompt="": "no"
try:
    rc = cli.main(["lab"])
finally:
    builtins.input = real_input
ok("the server command asks, and a no records nothing", rc == 1 and not hire.acknowledged(lab_cw))
builtins.input = lambda prompt="": "yes"
try:
    rc = cli.main(["lab"])
finally:
    builtins.input = real_input
ok("a typed yes records the OK for the owner's own coworker", rc == 0 and hire.acknowledged(lab_cw))
ok("a name that is not a coworker's is refused", cli.main(["../x", "--yes"]) == 2)

print()
if _failed:
    print(f"{_failed} FAILED")
    sys.exit(1)
print("ALL OK")
