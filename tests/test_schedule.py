"""core.schedule is THE registry query for what this box does on its own. The doctor renders
it; the machine GUI's page 1 will read it. Proven hermetically: timers carry their gate and
state from config, a config recipe rides gtm_recipes with off/dry/live from the overlay,
a prefix collision surfaces as a warning, and the doctor's lines ARE render(snapshot())."""
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = pathlib.Path(tempfile.mkdtemp())
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(TMP / "t.db")
os.environ["AIOS_CONFIGLOCAL_CONFIG_PATH"] = str(TMP / "settings.yaml")

from core import packs, schedule, state          # noqa: E402
from core.config import get_config               # noqa: E402

state.init_db()
fails = 0


def ok(name, cond, detail=""):
    global fails
    print(("  ok   " if cond else "  FAIL ") + name + (f"  — {detail}" if detail and not cond else ""))
    fails += 0 if cond else 1


def overlay(text):
    (TMP / "settings.yaml").write_text(text); get_config.cache_clear()


MANIFEST = """manifest: 1
slug: {slug}
name: Test
host: lead_machine
contract: 1
requires_foundation: "1.0"
kind: places-campaign
prefix: {prefix}
industry: t
config:
  enabled: false
  cells: ["Napa, CA"]
  categories: ["spa"]
  max_usd: 1
suites: []
"""
REAL_ROOTS = packs.PACK_ROOTS
# Load every module ONCE with the real roots, so in-process PERIODIC matches what the doctor
# subprocess sees — including packs the worker imports by manifest (Practice Finder since
# plan PR 7). Patching the roots first left that pack out in-process only: green alone, red
# together (#740 + #741, caught by #742's CI, 2026-09-05).
from core import worker                          # noqa: E402
worker.load_modules()
PACKS = TMP / "plugins"; packs.PACK_ROOTS = [PACKS]
for slug, pfx in (("alpha-recipe", "places"),):
    d = PACKS / slug.replace("-", "_"); d.mkdir(parents=True); (d / "machine.yaml").write_text(MANIFEST.format(slug=slug, prefix=pfx))

# ── timers ────────────────────────────────────────────────────────────────────────────
overlay("gtm:\n  outbound_sending: false\n")
snap = schedule.snapshot(load=False)
rows = {r["name"]: r for r in snap["rows"]}
# GTM IS A LANE, NOT A CONSTANT. `gtm_recipes` is registered by marketing/lead_machine, so a sold
# customer_voice box has no such row and this block asserted a timer that box is right not to have
# — it failed inside every exported inbox box while passing in the monorepo (measured 2026-09-15).
# The gate/overlay arithmetic is still asserted wherever a gated timer actually exists, and the
# part that is true of every box — it registers timers, and each row carries the full schema — is
# asserted on all of them. What is NOT done is quietly skipping the file: a box with no timers at
# all is a broken box and still fails here.
ok("this box registers timers at all", bool(rows) and all(r.get("kind") for r in snap["rows"]),
   str(sorted(rows))[:120])
if "gtm_recipes" in rows:
    ok("timers present", rows["gtm_recipes"]["kind"] == "timer")
    ok("a gtm timer carries its gate and reads the overlay (off)",
       rows["gtm_recipes"]["gate"] == "gtm.outbound_sending" and rows["gtm_recipes"]["state"] == "off")
    ok("interval is the registered one", rows["gtm_recipes"]["interval_s"] == 21600)
else:
    print("  ok   no GTM lane on this box — its gated-timer cases do not apply")
    ok("every timer row still carries the full schema (kind, state, interval)",
       all(r["kind"] and r["state"] and isinstance(r["interval_s"], int)
           for r in snap["rows"] if r["kind"] == "timer"),
       str([r for r in snap["rows"] if not r.get("state")])[:120])
ok("an ungated timer says so", any(r["state"] == "ungated" and r["gate"] is None for r in snap["rows"]))
overlay("gtm:\n  outbound_sending: true\n")
if "gtm_recipes" in rows:
    ok("… and on when the overlay says on", schedule.snapshot(load=False)["rows"] and
       {r["name"]: r for r in schedule.snapshot(load=False)["rows"]}["gtm_recipes"]["state"] == "on")

# ── recipes ───────────────────────────────────────────────────────────────────────────
r = {x["name"]: x for x in schedule.snapshot(load=False)["rows"]}["alpha-recipe"]
ok("a config recipe is a row, off, ships dark", r["kind"] == "recipe" and r["state"] == "off" and r["note"] == "ships dark")
# A RECIPE PACK ONLY RUNS IF SOMETHING CARRIES IT. Where the carrier timer is registered the row
# reports its interval; where it is not, the row must say so rather than report a carrier that is
# not there. Measured 2026-09-15 inside an exported customer_voice box: a pack the customer had
# paid for and switched on rendered "via gtm_recipes  ON, LIVE" with interval_s 0 — a timer that
# does not exist on that box, for work nothing would ever run.
if "gtm_recipes" in rows:
    ok("it rides the carrier's interval", r["interval_s"] == 21600)
    ok("and it is marked runnable", r.get("runnable") is True, str(r))
else:
    ok("with no carrier on this box the recipe is marked NOT runnable",
       r.get("runnable") is False and r["interval_s"] == 0, str(r))
    ok("and the rendered line says so instead of naming a timer this box does not have",
       any("NOT RUNNABLE HERE" in l for l in schedule.render(schedule.snapshot(load=False))),
       str(schedule.render(schedule.snapshot(load=False)))[:160])
ok("its gate is machines.<slug>.enabled", r["gate"] == "machines.alpha-recipe.enabled")
overlay("machines:\n  alpha-recipe:\n    enabled: true\n")
ok("enabled + dry_run default → dry", {x["name"]: x for x in schedule.snapshot(load=False)["rows"]}["alpha-recipe"]["state"] == "dry")
overlay("machines:\n  alpha-recipe:\n    enabled: true\n    dry_run: false\n")
ok("enabled + dry_run false → live", {x["name"]: x for x in schedule.snapshot(load=False)["rows"]}["alpha-recipe"]["state"] == "live")

# ── a collision surfaces as a warning, not a crash ────────────────────────────────────
for slug in ("beta-recipe", "gamma-recipe"):
    d = PACKS / slug.replace("-", "_"); d.mkdir(); (d / "machine.yaml").write_text(MANIFEST.format(slug=slug, prefix="novel"))
snap = schedule.snapshot(load=False)
# THE COLLISION CHECK IS THE LEAD MACHINE'S. snapshot() asks marketing.lead_machine.plug for the
# registered prefixes and passes on ImportError, so a box without that lane raises no warning —
# correctly, since nothing on it registers a prefix or runs a recipe in the first place.
if "gtm_recipes" in rows:
    ok("two packs on one NEW prefix → a warning naming both", any("novel" in w and "beta-recipe" in w and "gamma-recipe" in w for w in snap["warnings"]), str(snap["warnings"]))
else:
    print("  ok   no recipe lane on this box — prefix collisions are not a thing it can have")
ok("… and the rows still render", any(r["name"] == "alpha-recipe" for r in snap["rows"]))

# ── the doctor's lines are render(snapshot()) ─────────────────────────────────────────
overlay(""); packs.PACK_ROOTS = REAL_ROOTS                  # the doctor subprocess sees the real tree
env = {k: v for k, v in os.environ.items()}
out = subprocess.run([sys.executable, str(ROOT / "scripts/doctor.py")], capture_output=True, text=True, env=env, cwd=ROOT).stdout
lines = [l for l in out.splitlines() if not l.startswith("{")]
i = next(i for i, l in enumerate(lines) if "WHAT RUNS" in l)
j = next(j for j, l in enumerate(lines) if "timers ship OFF" in l)
doctor_block = [l for l in lines[i + 2:j] if l.strip()]
expected = schedule.render(schedule.snapshot(load=False))
ok("the doctor prints exactly render(snapshot())", doctor_block == expected,
   f"doctor={doctor_block[:3]}… expected={expected[:3]}…")
# REAL_ROOTS is back in place here, so this asks about the packs the box actually ships. A sold
# box ships none until its owner buys a machine, and an empty recipe list is the correct state for
# it — asserting a recipe line unconditionally failed inside every exported inbox box.
_real = [r for r in schedule.snapshot(load=False)["rows"] if r["kind"] == "recipe"]
if _real:
    ok("render marks a recipe line", any(l.strip().startswith("recipe ") for l in expected))
else:
    print("  ok   no packs installed on this box — nothing for render to mark as a recipe")
print(f"{fails} FAILED" if fails else "all ok")
sys.exit(1 if fails else 0)
