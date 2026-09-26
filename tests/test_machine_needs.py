"""A machine may say `needs: [coworkers]` (docs/SCOPE_TIERS.md §2.3, row 4).

The done-when: a machine that needs Coworkers shows "needs Pro" on Base and installs on Pro. Shown
on each path a machine takes onto a box:
  · add_machine.py refuses the pack on Base before a file is copied, and takes it on Pro;
  · a pack copied onto a Base box by hand is left out of packs.discover(), so nothing loads it,
    and held_back() says why; on Pro it is found like any other;
  · an owner's own machine in my/machines/ shows "needs Base Machine Pro" and registers nothing
    on Base, and starts on Pro;
  · `needs` names features, never tiers: an unknown name is refused, naming it, and a manifest
    that uses `needs` must require foundation 1.2, so a box too old to read it refuses the lot;
  · the plan is read without the config, because the config's own loading runs discover():
    a Base box never told by Ownbox loads its config without recursing.

Run: python tests/test_machine_needs.py
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
T = pathlib.Path(tempfile.mkdtemp())
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")
PROVISION = T / "provision.json"
os.environ["AIOS_PROVISION_JSON"] = str(PROVISION)
os.environ["AIOS_MY_MACHINES"] = str(T / "my_machines")

from core import claim, custom_machines, packs, state, tiers  # noqa: E402

claim.PROVISION_JSON = str(PROVISION)
state.init_db()

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


_SEQ = [0]


def plan(tier=None):
    """None: a Base box nobody has told anything. Otherwise Ownbox tells it `tier`."""
    with state.connect() as c:
        c.execute("DELETE FROM box_settings WHERE machine = 'core' AND key = 'plan'")
    PROVISION.unlink(missing_ok=True)
    if tier is not None:
        _SEQ[0] += 1
        tiers.set_plan({"seq": _SEQ[0], "tier": tier})


PACK = """manifest: 1
slug: {slug}
name: Fixture {slug}
host: lead_machine
contract: 1
requires_foundation: "{rf}"
kind: places-campaign
prefix: places
industry: fixture
{needs}config:
  enabled: false
"""


def pack(root, slug, needs="", rf="1.2"):
    d = pathlib.Path(root) / slug.replace("-", "_")
    d.mkdir(parents=True)
    (d / "machine.yaml").write_text(PACK.format(slug=slug, rf=rf, needs=f"needs: {needs}\n" if needs else ""))
    return d


def manifest(**kw):
    m = {"manifest": 1, "slug": "x-pack", "name": "X", "host": "lead_machine", "contract": 1,
         "requires_foundation": "1.2", "kind": "places-campaign", "prefix": "places",
         "industry": "fixture", "config": {"enabled": False}}
    m.update(kw)
    return m


def refused(m):
    try:
        packs.validate(m)
        return ""
    except packs.PackError as e:
        return str(e)


print("the field: features, never tiers")
ok("the foundation that reads needs: is 1.2", packs.foundation_version() == packs.NEEDS_SINCE == "1.2",
   packs.foundation_version())
ok("absent is fine, and a known feature is fine", not refused(manifest()) and not refused(manifest(needs=["coworkers"])),
   refused(manifest(needs=["coworkers"])))
why = refused(manifest(needs=["pro"]))
ok("a tier is refused, naming it, and saying to name the feature", "'pro'" in why and "tier" in why, why)
why = refused(manifest(needs=["coworkers", "teleport"]))
ok("an unknown feature is refused, naming it", "'teleport'" in why, why)
ok("a string, an empty list or a non-string entry is refused",
   all(refused(manifest(needs=v)) for v in ("coworkers", [], [1])))
why = refused(manifest(needs=["coworkers"], requires_foundation="1.1"))
ok("needs: with requires_foundation below 1.2 is refused, so an older box refuses the machine outright",
   "1.2" in why and "requires_foundation" in why, why)
ok("without needs:, 1.1 is still fine", not refused(manifest(requires_foundation="1.1")))

print("the plan, read without the config")
plan(None)
ok("a Base box never told lacks coworkers, and says which tier includes it",
   tiers.needs(["coworkers"]) == (False, "needs Base Machine Pro"), tiers.needs(["coworkers"]))
plan("pro")
ok("told Pro, the need is met", tiers.needs(["coworkers"]) == (True, ""))
plan(None)
PROVISION.write_text(json.dumps({"tier": "pro"}))
ok("built as Pro (provision.json), the need is met", tiers.needs(["coworkers"])[0])
plan(None)
from core import box_settings, config  # noqa: E402
_real_get, _real_cfg = box_settings.get, config.get_config


def _boom(*a, **k):
    raise AssertionError("the plan must not be read through the config")


box_settings.get = config.get_config = _boom
try:
    base = tiers.features()
    plan("pro")
    pro = tiers.features()
finally:
    box_settings.get, config.get_config = _real_get, _real_cfg
ok("features() reads neither the config nor box_settings.get (whose fallback IS the config)",
   base == frozenset() and "coworkers" in pro, f"{base} {pro}")

print("a pack copied onto the box by hand")
R = T / "plugins"
pack(R, "plain-pack")
pack(R, "coworker-pack", needs="[coworkers]")
plan(None)
found = [m["slug"] for m in packs.discover([R])]
ok("on Base, discover() leaves the pack that needs coworkers out, so nothing loads it",
   found == ["plain-pack"], found)
ok("held_back() says which, and why", packs.held_back([R]) == [("coworker-pack", "needs Base Machine Pro")],
   packs.held_back([R]))
ok("and it is not invalid: waiting is not broken", packs.invalid([R]) == [], packs.invalid([R]))
ok("the installer still sees it (include_held): a waiting pack owns its slug and prefix",
   [m["slug"] for m in packs.discover([R], include_held=True)] == ["coworker-pack", "plain-pack"])
plan("pro")
found = [m["slug"] for m in packs.discover([R])]
ok("on Pro it is found like any other", found == ["coworker-pack", "plain-pack"] and packs.held_back([R]) == [],
   found)

print("the config loads on a Base box nobody told, and leaves the held pack's defaults out")
plan(None)
_roots = packs.PACK_ROOTS
packs.PACK_ROOTS = (R,)
try:
    config.get_config.cache_clear()
    cfg = config.get_config()
    machines = cfg.get("machines") or {}
    ok("no recursion, the plain pack's defaults merged, the held pack's not",
       "plain-pack" in machines and "coworker-pack" not in machines, sorted(machines))
    plan("pro")
    config.get_config.cache_clear()
    ok("on Pro, both merge", "coworker-pack" in (config.get_config().get("machines") or {}))
finally:
    packs.PACK_ROOTS = _roots
    config.get_config.cache_clear()

print("the owner's own machine, my/machines/")
M = pathlib.Path(os.environ["AIOS_MY_MACHINES"])
d = M / "helper"
d.mkdir(parents=True)
(d / "machine.yaml").write_text('name: helper\nversion: 0.1.0\nrequires_foundation: "1.2"\nneeds: [coworkers]\n')
MARK = T / "helper-imported"
(d / "__init__.py").write_text(f"open({str(MARK)!r}, 'w').write('x')\n")
plan(None)
custom_machines._STATUS.clear()
custom_machines.load("test")
row = {r["slug"]: r for r in custom_machines.status()}.get("helper") or {}
ok("on Base it shows 'needs Base Machine Pro'", row.get("ok") is False and row.get("reason") == "needs Base Machine Pro", row)
ok("and registers nothing: its code never ran", not MARK.exists())
(d / "machine.yaml").write_text('name: helper\nversion: 0.1.0\nrequires_foundation: "1.2"\nneeds: [pro]\n')
custom_machines._STATUS.clear()
custom_machines.load("test")
row = {r["slug"]: r for r in custom_machines.status()}.get("helper") or {}
ok("a tier named in its needs: is refused the same way a pack's is", "'pro'" in (row.get("reason") or ""), row)
(d / "machine.yaml").write_text('name: helper\nversion: 0.1.0\nrequires_foundation: "1.2"\nneeds: [coworkers]\n')
plan("pro")
custom_machines._STATUS.clear()
custom_machines.load("test")
row = {r["slug"]: r for r in custom_machines.status()}.get("helper") or {}
ok("on Pro it starts", row.get("ok") is True and MARK.exists(), row)

print("add_machine.py: refused before a file is copied, installed on Pro")
# A SCRATCH BOX, never this checkout: a real install writes the pack, licence.json and
# my/settings.yaml into the tree it runs in (a mutation run of this suite once did exactly that).
# The foundation, the config and the script are copied; the Lead Machine is its marker file only.
import shutil  # noqa: E402
BOX = T / "box"
_skip = shutil.ignore_patterns("__pycache__")
shutil.copytree(ROOT / "core", BOX / "core", ignore=_skip)
shutil.copytree(ROOT / "config", BOX / "config", ignore=_skip)
(BOX / "scripts").mkdir()
shutil.copy2(ROOT / "scripts" / "add_machine.py", BOX / "scripts" / "add_machine.py")
(BOX / "marketing" / "lead_machine" / "plugins").mkdir(parents=True)
(BOX / "my").mkdir()                                   # every sold box ships my/ (its README says so)
for f in ("marketing/__init__.py", "marketing/lead_machine/__init__.py", "marketing/lead_machine/handler.py"):
    (BOX / f).write_text("")
src = pack(T / "incoming", "coworker-recipe", needs="[coworkers]")
dest = BOX / "marketing" / "lead_machine" / "plugins" / "coworker_recipe"


def add(*args):
    env = dict(os.environ, PYTHONPATH=str(BOX))
    return subprocess.run([sys.executable, str(BOX / "scripts" / "add_machine.py"), str(src), *args],
                          cwd=BOX, env=env, capture_output=True, text=True, timeout=120)


plan(None)
r = add()
ok("on Base it is refused with 'needs Base Machine Pro', naming the feature",
   r.returncode == 1 and "needs Base Machine Pro" in r.stdout and "coworkers" in r.stdout, (r.stdout + r.stderr)[-300:])
ok("and nothing was copied, recorded or armed",
   not dest.exists() and not (BOX / "licence.json").exists() and not (BOX / "my" / "settings.yaml").exists())
plan("pro")
r = add()
ok("on Pro it installs", r.returncode == 0 and (dest / "machine.yaml").exists(), (r.stdout + r.stderr)[-300:])
plan(None)
other = T / "incoming2"
d2 = pack(other, "same-prefix", rf="1.2")
(d2 / "machine.yaml").write_text((d2 / "machine.yaml").read_text().replace("prefix: places", "prefix: fixturepx"))
(dest / "machine.yaml").write_text((dest / "machine.yaml").read_text().replace("prefix: places", "prefix: fixturepx"))
r2 = subprocess.run([sys.executable, str(BOX / "scripts" / "add_machine.py"), str(d2)], cwd=BOX,
                    env=dict(os.environ, PYTHONPATH=str(BOX)), capture_output=True, text=True, timeout=120)
ok("back on Base, the installed-but-waiting pack still owns its prefix: a newcomer claiming it is refused",
   r2.returncode == 1 and "already claimed by the installed pack coworker-recipe" in r2.stdout, (r2.stdout + r2.stderr)[-300:])
ok("(and this checkout was never written to)",
   not (ROOT / "marketing" / "lead_machine" / "plugins" / "coworker_recipe").exists())

print()
print("ALL OK" if not _failed else f"{_failed} FAILED")
sys.exit(1 if _failed else 0)
