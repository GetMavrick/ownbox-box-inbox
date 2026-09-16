"""Plug-in points for set-up steps and health checks (core/onboarding.py, core/watchdog.register_probe).

Owner, 2026-09-16: "Give machines a plug-in point for set-up steps and one for health checks" and
"We need to protect the base machine and keep it clean for modular upgrades."

What has to hold, and why each one is here:

  * A machine registers a step or a probe at import; core orders, merges and runs them without
    knowing the machine by name.
  * THE TWO-PROCESS TRAP. A box runs a worker, a web process and a separate watchdog, and a
    registration is a module global. A probe registered by a machine module must run in a watchdog
    process that never imported that machine, and a step must render in a web process that never
    did — or the registry is empty there and says nothing, which is indistinguishable from a
    machine with nothing to report. Proven below in FRESH subprocesses, not in this one, because
    this process has imported everything and would pass for the wrong reason.
  * One broken machine costs that machine its step or check — never the screen, never the pass.
  * A machine cannot shadow another machine's step, or a probe core owns.
  * Nothing but a status and a sentence reaches the screen, and only declared fields reach save().

Run: python tests/test_plugin_points.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "plug.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state  # noqa: E402

state.init_db()

from core import onboarding, watchdog, worker  # noqa: E402
from core.box_secrets import SecretRejected  # noqa: E402

FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


def raises(fn, exc=ValueError) -> str:
    try:
        fn()
    except exc as e:
        return str(e) or type(e).__name__
    return ""


# In THIS process the registry must not try to import the repo's real machines: the in-process
# half is about the contract, and the subprocess half below is about loading.
worker._REGISTRATIONS_IMPORTED = True

FIELD = {"name": "key", "label": "Key", "type": "password"}
stored: dict = {}


def base_step(**over):
    kw = dict(order=20, machine="alpha", title="Alpha account", why="So alpha works.",
              fields=[FIELD], steps=["Get a key.", "Paste it."],
              state=lambda: {"status": "connected" if stored.get("key") else "not_connected",
                             "who": "a@b.co", "detail": ""},
              save=lambda values, user_id=None: stored.update(values))
    kw.update(over)
    return kw


print("\n— a machine registers a set-up step, core renders it —")
onboarding._STEPS.clear()
onboarding.register_step("alpha", **base_step())
onboarding.register_step("beta", **base_step(order=10, machine="beta", title="Beta first"))
got = onboarding.steps()
ok("steps come back in the ORDER the machines declared, not registration order",
   [s["key"] for s in got] == ["beta", "alpha"], str([s["key"] for s in got]))
ok("each carries the copy a buyer reads", got[1]["title"] == "Alpha account" and got[1]["steps"] ==
   ["Get a key.", "Paste it."])
ok("...and a live status from the machine", got[1]["status"] == "not_connected", got[1]["status"])

print("\n— save() hands a machine ONLY what its step declared —")
onboarding.save("alpha", {"key": "k-123", "smuggled": "DROP TABLE", "user_id": "evil"}, user_id="u1")
ok("the declared field arrives", stored.get("key") == "k-123", str(stored))
ok("A FIELD THE STEP NEVER DECLARED DOES NOT REACH THE MACHINE", "smuggled" not in stored, str(stored))
ok("...and the next render reads connected, with no restart", onboarding.steps()[1]["status"] == "connected")

print("\n— refusals speak to the buyer —")


def refuse(values, user_id=None):
    raise onboarding.StepRejected("That key does not look right. Copy it again.")


def refuse_old(values, user_id=None):
    raise SecretRejected("Google refused the app password.")


onboarding.register_step("gamma", **base_step(order=30, machine="gamma", save=refuse))
ok("a StepRejected sentence reaches the caller verbatim",
   "Copy it again" in raises(lambda: onboarding.save("gamma", {"key": "x"}), onboarding.StepRejected))
onboarding.register_step("delta", **base_step(order=40, machine="delta", save=refuse_old))
ok("the older box_secrets.SecretRejected is translated, so a step can move without rewording",
   "Google refused" in raises(lambda: onboarding.save("delta", {"key": "x"}), onboarding.StepRejected))
ok("an unknown step is a sentence, not a stack trace",
   "does not exist" in raises(lambda: onboarding.save("nope", {}), onboarding.StepRejected))

print("\n— one broken machine costs that machine's row, never the screen —")


def boom():
    raise RuntimeError("database is locked")


onboarding.register_step("eps", **base_step(order=50, machine="eps", state=boom))
onboarding.register_step("zeta", **base_step(order=60, machine="zeta",
                                             state=lambda: {"status": "banana"}))
rows = {s["key"]: s for s in onboarding.steps()}
ok("a state() that RAISES renders as unavailable, and the other steps still render",
   rows["eps"]["status"] == "unavailable" and rows["alpha"]["status"] == "connected", str(rows["eps"]))
ok("...with a sentence, not the exception text", "database is locked" not in rows["eps"]["detail"],
   rows["eps"]["detail"])
ok("a status outside the closed set is unavailable, never handed to the screen raw",
   rows["zeta"]["status"] == "unavailable", rows["zeta"]["status"])
ok("every status on the page is from the closed set",
   all(s["status"] in onboarding.STATUSES for s in rows.values()))

print("\n— nothing but a status and a sentence leaves a machine's state() —")
onboarding.register_step("leaky", **base_step(order=70, machine="leaky",
                         state=lambda: {"status": "connected", "detail": "", "password": "hunter2",
                                        "api_key": "sk-live-SECRET"}))
# The whole page, not one filtered row: a leak that also renamed or dropped the row must still fail
# HERE, on this assertion, rather than crash somewhere incidental.
page = json.dumps(onboarding.steps())
ok("A CREDENTIAL A CARELESS state() RETURNS NEVER REACHES THE SCREEN",
   "hunter2" not in page and "sk-live-SECRET" not in page,
   page[page.find("leaky") - 40:page.find("leaky") + 240] if "leaky" in page else page[:200])
ok("...and a state() key can never overwrite the step's own metadata",
   any(r["key"] == "leaky" and r["machine"] == "leaky" for r in json.loads(page)))

print("\n— the link out waits for the step it depends on —")
onboarding.register_step("linked", **base_step(
    order=80, machine="linked", state=lambda: {"status": "not_connected"},
    link={"label": "Connect in Vendor", "url": "https://vendor.example/accounts", "new_tab": True,
          "disabled_because": "Paste your key first."}))
row = [s for s in onboarding.steps() if s["key"] == "linked"][0]
ok("disabled while not connected, with the machine's own reason",
   row["link"]["enabled"] is False and row["link"]["disabled_because"] == "Paste your key first.",
   str(row["link"]))

print("\n— registration refuses mistakes at import, not on a customer's screen —")
ok("another machine cannot take a step key that is held",
   "already registered" in raises(lambda: onboarding.register_step("alpha", **base_step(machine="intruder"))))
ok("the same machine re-registering (a re-import) replaces, never stacks",
   raises(lambda: onboarding.register_step("alpha", **base_step())) == "" and
   [s["key"] for s in onboarding.steps()].count("alpha") == 1)
ok("a step asking for nothing is refused",
   "asks for nothing" in raises(lambda: onboarding.register_step("empty", **base_step(machine="e", fields=[]))))
ok("a field with no type is refused",
   "every field" in raises(lambda: onboarding.register_step(
       "badf", **base_step(machine="b", fields=[{"name": "k", "label": "K"}]))))
ok("a link that is not https is refused",
   "https" in raises(lambda: onboarding.register_step(
       "badl", **base_step(machine="b", link={"label": "x", "url": "http://plain.example"}))))

print("\n— health checks: a machine registers one, the pass runs it —")
watchdog._REGISTERED_PROBES.clear()
watchdog.register_probe("alpha_sync", lambda: (False, "last sync 3h ago"),
                        consequence="Alpha is not syncing — new items are not arriving", machine="alpha")
res = watchdog._registered_probe_results()
ok("a registered probe runs and its result is reported", res.get("alpha_sync") == (False, "last sync 3h ago"),
   str(res))
ok("its consequence is what the digest will say", watchdog._consequence("alpha_sync").startswith("Alpha is not"))


def probe_boom():
    raise KeyError("heartbeats")


watchdog.register_probe("alpha_crash", probe_boom, consequence="x", machine="alpha")
res = watchdog._registered_probe_results()
ok("A PROBE THAT RAISES IS A FAILURE WITH ITS MACHINE NAMED — never a quiet skip",
   res["alpha_crash"][0] is False and "alpha" in res["alpha_crash"][1], str(res["alpha_crash"]))
ok("...and the other machine's probe still ran", "alpha_sync" in res)
ok("a machine cannot register a key the watchdog owns",
   "belongs to the watchdog" in raises(lambda: watchdog.register_probe(
       "leadmagnet_throughput", lambda: (True, ""), consequence="x", machine="alpha")))
ok("nor one another machine holds",
   "already registered" in raises(lambda: watchdog.register_probe(
       "alpha_sync", lambda: (True, ""), consequence="x", machine="intruder")))

print("\n— the pass itself: registered probes join it and cannot overwrite a built-in —")
import ast  # noqa: E402
src = (ROOT / "core" / "watchdog.py").read_text()
_node = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "run_once")
body = ast.get_source_segment(src, _node) or ""
merge = body.find("_registered_probe_results()")
ok("run_once merges registered probes", merge != -1)
ok("...AFTER every built-in key is assembled, including the late ones (slack_socket)",
   merge > body.find('probes["slack_socket"]') > 0, str((merge, body.find('probes["slack_socket"]'))))
ok("...and skips a colliding key rather than overwriting it",
   "if _key in probes:" in body and "continue" in body[merge:merge + 400])

# ── THE TWO-PROCESS TRAP, in fresh interpreters that imported no machine ─────────────────────
print("\n— a FRESH process that never imported the machine still sees its step and its probe —")
pkg = Path(tempfile.mkdtemp())
(pkg / "fixture_machine_pp").mkdir()
(pkg / "fixture_machine_pp" / "__init__.py").write_text(textwrap.dedent('''
    from core import onboarding, watchdog
    onboarding.register_step("fixture", order=5, machine="fixture", title="Fixture account",
        why="Proves loading.", fields=[{"name": "key", "label": "Key", "type": "password"}],
        steps=["Paste."], state=lambda: {"status": "connected"}, save=lambda v, user_id=None: None)
    watchdog.register_probe("fixture_health", lambda: (True, "fixture ok"),
        consequence="Fixture is down", machine="fixture")
'''))
(pkg / "fixture_broken_pp").mkdir()
(pkg / "fixture_broken_pp" / "__init__.py").write_text("raise ImportError('this machine is broken')\n")

child = textwrap.dedent(f'''
    import os, sys, json
    sys.path.insert(0, {str(ROOT)!r}); sys.path.insert(0, {str(pkg)!r})
    os.environ.update(AIOS_HERMETIC_TEST="1", AIOS_DB_PATH={os.path.join(_T, "child.db")!r},
                      DISPATCH_BEARER_TOKEN="b", DASH_TOKEN="p")
    from core import state; state.init_db()
    import core.config as cfg
    cfg.get_config = lambda: {{"modules": ["fixture_broken_pp", "fixture_machine_pp"]}}
    from core import packs; packs.discover = lambda: []
    assert "fixture_machine_pp" not in sys.modules
    from core import {{mod}}
    print(json.dumps({{expr}}))
''')
for label, mod, expr in (
    ("the WEB side: onboarding.steps() in a process that imported no machine",
     "onboarding", "[s['key'] for s in onboarding.steps()]"),
    ("the WATCHDOG side: its pass in a process that imported no machine",
     "watchdog", "watchdog._registered_probe_results()"),
):
    r = subprocess.run([sys.executable, "-c", child.replace("{mod}", mod).replace("{expr}", expr)],
                       capture_output=True, text=True, timeout=120)
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith(("[", "{"))]
    out = json.loads(lines[-1]) if lines else None
    if mod == "onboarding":
        ok(label + " — sees the step", out == ["fixture"], (r.stdout + r.stderr)[-400:])
    else:
        ok(label + " — runs the probe", bool(out) and out.get("fixture_health") == [True, "fixture ok"],
           (r.stdout + r.stderr)[-400:])
    ok("...and the BROKEN machine beside it cost only itself, logged by name",
       r.returncode == 0 and "fixture_broken_pp" in (r.stdout + r.stderr), (r.stdout + r.stderr)[-300:])

print("\n— and the proof can fail: without the loader, a fresh process sees nothing —")
r = subprocess.run([sys.executable, "-c", child.replace("{mod}", "onboarding").replace(
    "{expr}", "[s['key'] for s in onboarding.steps()]").replace(
    "from core import onboarding", "from core import onboarding, worker; worker._REGISTRATIONS_IMPORTED = True")],
    capture_output=True, text=True, timeout=120)
lines = [ln for ln in r.stdout.splitlines() if ln.startswith("[")]
ok("with loading switched off the registry is EMPTY — the trap is real, and the loader is what closes it",
   bool(lines) and json.loads(lines[-1]) == [], (r.stdout + r.stderr)[-300:])

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
