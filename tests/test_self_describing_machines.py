"""Self-describing add-on machines (docs/SCOPE_MACHINE_MARKETPLACE.md §3.3, owner-approved 2026-09-29).

The done-when, each shown on the path a box takes:
  · a test machine added as ONE FOLDER is discovered, with no edit to the Tiers table,
    config/aios.config.yaml, .github/workflows/tests.yml or the menu: its feature, its worker and
    web modules, its screen, its menu row, its config defaults and its own suite all appear;
  · the AEO Machine and the Unified Inbox moved over: the effective config and tiers are what they
    were (same modules, same web modules, same machine_features, same Pro, same FEATURES);
plus: the plan still gates a discovered machine, `needs:` holds it back, a bad manifest is refused by
name without hiding the others, and discovery never reads the config it feeds.

Run: python tests/test_self_describing_machines.py
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
T = pathlib.Path(tempfile.mkdtemp())
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")

from core import machines  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


REAL_ROOTS = os.pathsep.join(str(ROOT / r) for r in machines.DEFAULT_ROOTS)
LAB = T / "zzlab"                                   # a machine root of our own, importable as `zzlab`


def lab_machine(folder="hello", **over):
    d = LAB / folder
    (d / "tests").mkdir(parents=True, exist_ok=True)
    (d / "__init__.py").write_text(
        "from core import worker\n"
        "def _tick():\n    return None\n"
        f"worker.register_periodic(_tick, interval_s=3600, name='{folder}_tick')\n")
    (d / "web.py").write_text(textwrap.dedent(f"""\
        from flask import Blueprint
        blueprint = Blueprint("{folder}_lab", __name__)
        @blueprint.get("/{folder}-lab")
        def page():
            return "{folder} answers"
        """))
    (d / "tests" / f"test_{folder}.py").write_text(f"print('{folder} suite ran')\n")
    m = {"addon": 1, "slug": folder, "name": folder.title() + " Machine", "modules": [f"zzlab.{folder}"],
         "worker": [f"zzlab.{folder}"], "web": [f"zzlab.{folder}.web"], "tiers": [],
         "menu": {"key": f"{folder}_lab", "title": folder.title(), "href": f"/{folder}-lab", "order": 90},
         "product": {"name": folder.title() + " Machine"},
         "config": {f"{folder}_lab": {"greeting": "from the machine", "size": 3}},
         "suites": [f"tests/test_{folder}.py"]}
    m.update(over)
    m = {k: v for k, v in m.items() if v is not None}
    import yaml
    (d / "machine.yaml").write_text(yaml.safe_dump(m, sort_keys=False))
    return d


PROBE = r"""
import json, os, sys
sys.path.insert(0, os.environ["REPO"]); sys.path.insert(0, os.environ["LABPARENT"])
from core import state, machines, tiers
state.init_db()
if os.environ.get("PLAN"):
    tiers.set_plan(json.loads(os.environ["PLAN"]))
from core.config import get_config
c = get_config()
out = {"slugs": [m["slug"] for m in machines.discover()], "invalid": machines.invalid(),
       "features": sorted(tiers.FEATURES), "pro": sorted(tiers.TIERS["pro"]["features"]),
       "base": sorted(tiers.TIERS["ownbox"]["features"]), "modules": c.get("modules"),
       "web": c.get("web_modules"), "mf": c.get("machine_features"),
       "lab": c.get("hello_lab"), "products": machines.products()}
if os.environ.get("BOOT"):
    from core import worker, shell
    worker.load_modules()
    from core import dispatch
    rules = {r.rule for r in dispatch.app.url_map.iter_rules()}
    out["periodic"] = sorted(p["name"] for p in worker.PERIODIC if str(p.get("name", "")).endswith("_tick"))
    out["route"] = "/hello-lab" in rules
    out["menu"] = "hello_lab" in getattr(shell, "_SECTIONS", {})
    if out["route"]:
        out["body"] = dispatch.app.test_client().get("/hello-lab").get_data(as_text=True)
print("RESULT " + json.dumps(out, default=list))
"""


def probe(extra_root=True, **env):
    e = {k: v for k, v in os.environ.items() if not k.startswith("AIOS_")}
    e.update({"AIOS_HERMETIC_TEST": "1", "AIOS_DB_PATH": str(T / f"p{len(os.listdir(T))}.db"),
              "DISPATCH_BEARER_TOKEN": "bearer", "REPO": str(ROOT), "LABPARENT": str(T),
              "AIOS_MACHINE_ROOTS": REAL_ROOTS + (os.pathsep + str(LAB) if extra_root else "")})
    e.update(env)
    r = subprocess.run([sys.executable, "-c", PROBE], cwd=ROOT, env=e, capture_output=True, text=True,
                       timeout=300)
    line = next((ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")), None)
    if line is None:
        raise AssertionError(r.stdout[-1500:] + r.stderr[-2500:])
    return json.loads(line[len("RESULT "):])


print("the two machines moved over, and nothing a box reads changed")
base = probe(extra_root=False)
ok("exactly the AEO Machine and the Unified Inbox are found", base["slugs"] == ["aeo", "inbox"], base["slugs"])
ok("no manifest is refused", base["invalid"] == [], base["invalid"])
ok("Pro holds coworkers and both machines; Base holds none",
   base["pro"] == ["coworkers", "machine:aeo", "machine:inbox"] and base["base"] == [], (base["pro"], base["base"]))
ok("FEATURES is what it was", base["features"] == ["coworkers", "machine:aeo", "machine:inbox"], base["features"])
ok("machine_features maps each machine to the modules that were in the config",
   base["mf"] == {"machine:aeo": ["marketing.aeo_machine", "marketing.seo_machine"],
                  "machine:inbox": ["marketing.customer_voice"]}, base["mf"])
ok("their worker modules still load",
   {"marketing.aeo_machine", "marketing.customer_voice.inbox", "marketing.customer_voice"} <= set(base["modules"]))
ok("their screens still mount", {"marketing.aeo_machine.app", "marketing.customer_voice.app"} <= set(base["web"]))
ok("and each module is listed once", len(base["modules"]) == len(set(base["modules"]))
   and len(base["web"]) == len(set(base["web"])))
ok("each declares its product name for the provisioner",
   base["products"] == {"machine:aeo": "AEO Machine", "machine:inbox": "Unified Inbox"}, base["products"])
cfg_text = (ROOT / "config" / "aios.config.yaml").read_text()
import re  # noqa: E402
ok("the tracked config no longer lists them", not re.search(r"^machine_features:", cfg_text, re.M)
   and "- marketing.aeo_machine" not in cfg_text and "- marketing.customer_voice" not in cfg_text)

print("a new machine is ONE folder")
lab_machine()
boot = probe(BOOT="1")
ok("it is found", boot["slugs"] == ["aeo", "inbox", "hello"], boot["slugs"])
ok("its feature is one a plan may name", "machine:hello" in boot["features"])
ok("tiers: [] keeps it out of Pro (Base and Pro get it through add:)", "machine:hello" not in boot["pro"])
ok("its worker module joins modules:", "zzlab.hello" in boot["modules"])
ok("its web module joins web_modules:", "zzlab.hello.web" in boot["web"])
ok("its modules become its machine_features: entry", boot["mf"].get("machine:hello") == ["zzlab.hello"])
ok("its config defaults are in the config", boot["lab"] == {"greeting": "from the machine", "size": 3}, boot["lab"])
ok("the worker imports it", boot["periodic"] == ["hello_tick"], boot["periodic"])
ok("its screen answers", boot["route"] and boot.get("body") == "hello answers", boot.get("body"))
ok("its menu row is registered from the manifest", boot["menu"])
suites = subprocess.run([sys.executable, "scripts/machine_suites.py", "--list"], cwd=ROOT, capture_output=True,
                        text=True, env=dict(os.environ, AIOS_MACHINE_ROOTS=REAL_ROOTS + os.pathsep + str(LAB)))
ok("CI's machine-suites step lists its suite", "hello\t" in suites.stdout and "test_hello.py" in suites.stdout,
   suites.stdout + suites.stderr)
ran = subprocess.run([sys.executable, "scripts/machine_suites.py"], cwd=ROOT, capture_output=True, text=True,
                     env=dict(os.environ, AIOS_MACHINE_ROOTS=REAL_ROOTS + os.pathsep + str(LAB)))
ok("...and runs it", ran.returncode == 0 and "hello suite ran" in ran.stdout, ran.stdout[-400:])
for rel in ("core/tiers.py", "config/aios.config.yaml", ".github/workflows/tests.yml", "core/shell.py"):
    if rel.startswith(".github/") and not (ROOT / ".github").is_dir():
        continue                                        # a box has no repository, so no workflow to read
    ok(f"with no edit to {rel}", "hello" not in (ROOT / rel).read_text().lower().replace("othello", ""))

print("the plan still decides whether it loads")
told = probe(BOOT="1", PLAN=json.dumps({"seq": 1, "tier": "ownbox"}))
ok("a Base box told its plan, without the machine: no worker, no screen, no menu row",
   told["periodic"] == [] and not told["route"] and not told["menu"], told)
bought = probe(BOOT="1", PLAN=json.dumps({"seq": 1, "tier": "ownbox", "add": ["machine:hello"]}))
ok("...and with it in add:, all three", bought["periodic"] == ["hello_tick"] and bought["route"] and bought["menu"])

print("config defaults sit beneath the tracked file and the overlay")
overlay = T / "settings.yaml"
overlay.write_text("hello_lab:\n  greeting: from the owner\n")
layered = probe(AIOS_CONFIGLOCAL_CONFIG_PATH=str(overlay))
ok("the owner's overlay wins over the machine's default, and the rest of it stays",
   layered["lab"] == {"greeting": "from the owner", "size": 3}, layered["lab"])

print("needs: holds a machine back until the plan includes it")
lab_machine(needs=["coworkers"])
held = probe(BOOT="1", PLAN=json.dumps({"seq": 1, "tier": "ownbox", "add": ["machine:hello"]}))
ok("bought on Base, but it needs coworkers: it does not load", held["periodic"] == [] and not held["route"], held)
pro = probe(BOOT="1", PLAN=json.dumps({"seq": 1, "tier": "pro", "add": ["machine:hello"]}))
ok("on Pro it loads", pro["periodic"] == ["hello_tick"] and pro["route"])
lab_machine()                                          # back to no needs

print("a bad manifest is refused by name, and never hides the others")
cases = {
    "unknown field": ({"colour": "red"}, "unknown field 'colour'"),
    "a slug that isn't one": ({"slug": "Hello World"}, "slug"),
    "a worker module outside the machine": ({"worker": ["marketing.aeo_machine"]}, "not under this machine's modules"),
    "a section that belongs to the box": ({"config": {"brain": {"backend": "api"}}}, "belongs to the box"),
    "a suite that isn't there": ({"suites": ["tests/test_nope.py"]}, "does not exist"),
    "no modules": ({"modules": []}, "at least one module"),
    "another format": ({"addon": 2}, "only format 1"),
}
for label, (over, want) in cases.items():
    lab_machine("broken", **{"modules": ["zzlab.broken"], "worker": [], "web": [], "menu": None,
                             "config": {}, "suites": [], **over})
    r = probe()
    why = " ".join(w for _, w in r["invalid"])
    ok(f"{label}: refused, naming it, and the others still found",
       want in why and r["slugs"] == ["aeo", "inbox", "hello"], (why, r["slugs"]))
import shutil  # noqa: E402
shutil.rmtree(LAB / "broken")
lab_machine("thief", modules=["zzlab.hello"], worker=[], web=[], menu=None, config={}, suites=[])
r = probe()
ok("a machine claiming another's modules is refused, and the first keeps them",
   "overlaps another machine's modules" in " ".join(w for _, w in r["invalid"]) and "thief" not in r["slugs"])
shutil.rmtree(LAB / "thief")
lab_machine("twin", slug="hello", modules=["zzlab.twin"], worker=[], web=[], menu=None, config={}, suites=[])
r = probe()
ok("a second folder claiming the same slug is refused", "already claimed" in " ".join(w for _, w in r["invalid"]))
shutil.rmtree(LAB / "twin")

print("discovery never reads the config it feeds")
from core import config  # noqa: E402
real = config.get_config


def _boom(*a, **k):
    raise AssertionError("machines read the config")


config.get_config = _boom
try:
    machines.refresh()
    found = [m["slug"] for m in machines.discover()]
finally:
    config.get_config = real
ok("discover() runs with get_config booby-trapped", found == ["aeo", "inbox"], found)
print()
print("ALL OK" if not _failed else f"{_failed} FAILED")
sys.exit(1 if _failed else 0)
