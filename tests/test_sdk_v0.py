"""SDK v0: the facade a company builds its own machine on (docs/SCOPE_MACHINE_MARKETPLACE.md §9).

Owner-approved 2026-09-29; OSDev1's order (Step B). Done when:
  * `from core import sdk` exports the promised seams as `sdk: 1`
  * the starter machine (`ownbox new`, pointed at my/machines) builds and runs against the facade
    alone: if this suite breaks, it is our bug
  * `ownbox check` flags AI outside the brain, unpromised core imports, and a missing needs: or
    requires_foundation, each with a one-sentence fix
  * the box's guides teach only the facade
  * the break notice says whether a promised seam broke
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
T = pathlib.Path(tempfile.mkdtemp(prefix="sdk_v0_"))
MINE = T / "machines"
MINE.mkdir()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")
os.environ["AIOS_MY_MACHINES"] = str(MINE)
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

FAILS: list[str] = []


def ok(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(label)


from core import state  # noqa: E402

state.init_db()
from core import sdk, sdk_check  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
import ownbox  # noqa: E402

print("— the facade —")
ok("it is versioned on its own: sdk 1", sdk.VERSION == 1)
handle = sdk.Machine.__dict__
missing = [s for s in sdk.SEAMS if not (hasattr(sdk, s) or s in handle)]
ok("every promised seam exists by name", not missing, str(missing))
ok("the manifest fields it promises", set(sdk.MANIFEST_FIELDS) == {"name", "version", "requires_foundation",
                                                                   "needs", "sdk"})
ok("it does not re-export the unpromised seams",
   not any(hasattr(sdk, n) for n in ("register_periodic", "register_schema", "worker", "state")))
try:
    sdk.machine("Bad Name")
    ok("a bad machine name is refused at import, naming the rule", False)
except ValueError as e:
    ok("a bad machine name is refused at import, naming the rule", "lowercase" in str(e))

print("\n— the starter, built and run against the facade alone —")
folder = ownbox.new("job-tracker")
ok("`ownbox new` writes one folder in my/machines", folder == MINE / "job-tracker"
   and {p.name for p in folder.iterdir()} == {"machine.yaml", "__init__.py", "test_job_tracker.py"})
try:
    ownbox.new("job-tracker")
    ok("...and refuses to write over it", False)
except ValueError as e:
    ok("...and refuses to write over it", "already exists" in str(e))
found = sdk_check.check(folder)
ok("`ownbox check` finds nothing: it is built only on what the box promises", found == [], str(found))
imports = re.findall(r"^\s*(?:from|import)\s+(core[\w.]*)", (folder / "__init__.py").read_text(), re.M)
ok("its only import from the box is core.sdk", imports == ["core"] and
   "from core import sdk" in (folder / "__init__.py").read_text(), str(imports))
own = subprocess.run([sys.executable, str(folder / "test_job_tracker.py")], capture_output=True, text=True)
ok("its own test passes", own.returncode == 0 and "ALL OK" in own.stdout, own.stdout + own.stderr)

from core import brain, custom_machines, report, shell  # noqa: E402
from core.connector import tools  # noqa: E402

asked = []
real_think = brain.think
brain.think = lambda task, prompt, **kw: asked.append((task, kw.get("machine"))) or "fine"
from core import dash  # noqa: E402
from core.dispatch import app  # noqa: E402

res = {r["slug"]: r for r in custom_machines.status()}
ok("the box loads it", res.get("job-tracker", {}).get("ok") is True, str(res.get("job-tracker")))
ok("its menu row is registered", any(s.key == "job_tracker" and s.href == "/job-tracker" for s in shell.sections()))
anon = app.test_client().get("/job-tracker")
ok("its screen is behind the box's sign-in (a stranger is sent to sign in)", anon.status_code in (302, 303)
   and "login" in anon.headers.get("Location", ""), f"{anon.status_code} {anon.headers.get('Location')}")
owner = app.test_client()
owner.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
page = owner.get("/job-tracker")
ok("the owner sees it on the box's own page", page.status_code == 200 and "Job Tracker" in page.get_data(as_text=True))
posted = owner.post("/job-tracker", data={"note": "call the Hendersons back"}, headers={"Origin": "http://localhost"})
ok("a note saves", posted.status_code == 303 and "call the Hendersons back" in owner.get("/job-tracker")
   .get_data(as_text=True), str(posted.status_code))
today = datetime.now(timezone.utc).date()
rep = report.REPORTERS.get("job_tracker")
out = rep["fn"](today) if rep else {}
ok("its Morning Review line counts today's notes", out.get("headline", {}).get("value") == 1, str(out))
ok("...and says nothing on a quiet day", rep and rep["fn"](today.replace(year=today.year - 1)) == {})
tool = tools._REGISTRY.get("aios.job_tracker.latest_notes")
ok("the owner's AI can ask it a question", tool is not None and tool["fn"]()["notes"] == ["call the Hendersons back"])
mod = sys.modules.get("my_machines.job_tracker")
ok("m.think goes through the box's brain, on the machine's own account",
   mod is not None and mod.m.think("summarise", "x") == "fine" and asked == [("summarise", "job_tracker")])
brain.think = real_think
d = mod.m.data_dir() if mod else None
ok("its own data folder is inside its own folder", d is not None and d.parent == folder and d.is_dir())

print("\n— the manifest —")
bad = MINE / "too-new"
bad.mkdir()
(bad / "__init__.py").write_text("")
(bad / "machine.yaml").write_text('name: too-new\nversion: 0.1.0\nrequires_foundation: "1.2"\nneeds: []\nsdk: 2\n')
man, why = custom_machines._manifest(bad)
ok("a machine built for a newer sdk is refused by name", man is None and "sdk 2" in why and "sdk 1" in why, why)
(bad / "machine.yaml").write_text('name: too-new\nversion: 0.1.0\nrequires_foundation: "1.2"\nneeds: []\nsdk: 1\n')
ok("`needs: []` (runs on every plan) is accepted", custom_machines._manifest(bad)[0] is not None,
   custom_machines._manifest(bad)[1])

print("\n— ownbox check names what is wrong, and its fix —")
lab = MINE / "lab"
lab.mkdir()
(lab / "machine.yaml").write_text("name: other\nversion: 0.1.0\n")
(lab / "__init__.py").write_text(
    "import anthropic\n"
    "from openai import OpenAI\n"
    "from core import state, sdk\n"
    "from core.worker import register_periodic\n"
    "import core.brain\n"
    "import importlib\n"
    "importlib.import_module('core.box_settings')\n"
    "URL = 'https://api.openai.com/v1/chat'\n"
    "import subprocess\n"
    "subprocess.run(['claude', '-p', 'hi'])\n"
    "m = sdk.machine('someone-else')\n"
    "import requests, smtplib\n"
    "from core.sdk import machine\n"
    "import core.sdk\n")
rows = sdk_check.check(lab)
said = [r["problem"] for r in rows]


def has(frag):
    return any(frag in p for p in said)


ok("an AI library imported directly", has("imports anthropic, an AI library") and has("from openai, an AI library"))
ok("an AI company's address called directly", has("calls api.openai.com directly"))
ok("the claude command run directly", has("runs the claude command itself"))
ok("an unpromised core import, in every form", has("core.state") and has("core.worker") and has("core.brain")
   and has("core.box_settings"), str(said))
ok("the handle naming another machine", has("names another machine"))
ok("a missing requires_foundation, needs: and sdk:", has("requires_foundation is missing") and
   has("needs: is missing") and has("sdk: is missing"))
ok("a name that is not the folder's", has("not the folder's name"))
ok("NOT its own code acting on the world (requests, smtplib): that is the owner's to decide",
   not has("requests") and not has("smtplib"))
ok("NOT the promised forms (from core.sdk import …, import core.sdk)",
   sum("core.sdk" in p for p in said) == 0)
ok("every finding names its fix in one sentence", rows and all(
   r["fix"].endswith(".") and r["fix"].count(". ") <= 1 and len(r["fix"]) < 200 for r in rows),
   str([r["fix"] for r in rows if not r["fix"].endswith(".")]))
cli = subprocess.run([sys.executable, "scripts/ownbox.py", "check", "lab"], cwd=ROOT, capture_output=True,
                     text=True, env=dict(os.environ))
ok("the command exits 1 and prints file:line, the problem and 'Fix:'", cli.returncode == 1
   and "__init__.py:1: it imports anthropic" in cli.stdout and "Fix: " in cli.stdout, cli.stdout[-400:])
clean = subprocess.run([sys.executable, "scripts/ownbox.py", "check", "job-tracker"], cwd=ROOT,
                       capture_output=True, text=True, env=dict(os.environ))
ok("...and 0 on the starter", clean.returncode == 0 and "job-tracker" in clean.stdout, clean.stdout)

print("\n— the break notice says whether a promise broke —")
from core import machine_breaks  # noqa: E402

machine_breaks.observe([{"slug": "job-tracker", "ok": True}, {"slug": "lab", "ok": True}],
                       release="release/2026.09.29.1", notify=lambda *a: None)
machine_breaks.observe([{"slug": "job-tracker", "ok": False, "reason": "ImportError: x"},
                        {"slug": "lab", "ok": False, "reason": "ImportError: y"}],
                       release="release/2026.09.30.1", notify=lambda *a: None)
rec = machine_breaks.stopped()
ok("a machine built only on sdk: the break is ours", rec.get("job-tracker", {}).get("promised") is True, str(rec))
ok("one that reached past it: told what it used, with the fix", rec.get("lab", {}).get("promised") is False
   and "Fix:" in rec.get("lab", {}).get("unpromised", ""), str(rec.get("lab")))
from core.dash import home  # noqa: E402

real_status = custom_machines.status
custom_machines.status = lambda: [{"slug": "job-tracker", "ok": False, "reason": "ImportError: x"},
                                  {"slug": "lab", "ok": False, "reason": "ImportError: y"}]
rows_html = home._custom_machine_rows()
custom_machines.status = real_status
ok("the Add a Machine page says whose fix it is", "ours to fix: tell us" in rows_html
   and "never promised" in rows_html, rows_html[:400])

print("\n— the guides teach only the facade —")
# In the repository the guides live in docs/box/foundation/; on a box, where the exporter put them.
_FOUNDATION = ROOT / "docs" / "box" / "foundation"


def _guide(repo_name: str, box_path: str) -> str:
    return ((_FOUNDATION / repo_name) if _FOUNDATION.is_dir() else (ROOT / box_path)).read_text()


guide = _guide("BUILD_A_MACHINE.md", "BUILD_A_MACHINE.md")
mine = _guide("my-CLAUDE.md", "my/CLAUDE.md")
boxmd = _guide("CLAUDE.md", "CLAUDE.md")
code = "\n".join(re.findall(r"```python\n(.*?)```", guide, re.S))
ok("BUILD_A_MACHINE.md's code imports nothing from the box but sdk",
   "from core import sdk" in code and not re.search(r"from core(\.\w+)* import (?!sdk)|import core\.(?!sdk)", code))
ok("...and no longer teaches the unpromised seams", not re.search(
   r"core\.(shell|worker|state|report|brain)\.", guide), str(re.findall(r"core\.\w+\.\w+", guide)))
ok("...and teaches ownbox new and ownbox check", "ownbox.py new" in guide and "ownbox.py check" in guide)
ok("my/CLAUDE.md tells the box's AI: the starter, sdk only, m.think, check",
   all(s in mine for s in ("ownbox.py new", "from core import sdk", "m.think", "ownbox.py check")))
ok("the box's CLAUDE.md points at the facade and the check",
   "from core import sdk" in boxmd and "ownbox.py check" in boxmd)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    sys.exit(1)
print("ALL OK")
