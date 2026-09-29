"""R4: when an update stops a machine the owner built, the owner is told (core/machine_breaks.py).

  · a machine that started on the last release and fails on this one was stopped BY THE UPDATE:
    it is recorded with the release and the reason, and announced once;
  · a machine that was already broken, or is new and wrong, or fails with no update in between, is
    the owner's own and is never blamed on an update;
  · a stopped machine that starts again is no longer listed;
  · the page names the update; the notification opens that page and names no machine;
  · the worker's one boot is where it runs, and nothing here can raise into it.

Run: python tests/test_machine_breaks.py
"""
import inspect
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
_T = pathlib.Path(tempfile.mkdtemp())
os.environ["AIOS_DB_PATH"] = str(_T / "breaks.db")
MINE = _T / "machines"
MINE.mkdir()
os.environ["AIOS_MY_MACHINES"] = str(MINE)

from core import state  # noqa: E402

state.init_db()

from core import custom_machines, machine_breaks  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def machine(slug, body="VALUE = 1\n"):
    d = MINE / slug
    d.mkdir(exist_ok=True)
    (d / "machine.yaml").write_text(f'name: {slug}\nversion: 1\nrequires_foundation: "1.0"\n')
    (d / "__init__.py").write_text(body)
    sys.modules.pop(custom_machines._module_name(slug), None)


def boot(release):
    """One worker boot on `release`: load the owner's machines, then observe, as the worker does."""
    for s in list(sys.modules):
        if s.startswith("my_machines."):
            sys.modules.pop(s)
    custom_machines._STATUS.clear()
    told = []
    newly = machine_breaks.observe(custom_machines.load("worker"), release=release,
                                   notify=lambda slugs, rel: told.append((list(slugs), rel)))
    return newly, told


BROKEN = "from core import a_name_the_new_release_removed\n"

print("the first boot, and boots with no update")
machine("price-list")
machine("quote-builder")
newly, told = boot("release/2026.09.26.1")
ok("the first boot announces nothing: there is nothing to compare with", newly == [] and told == [])
machine("quote-builder", BROKEN)
newly, told = boot("release/2026.09.26.1")
ok("a machine the owner broke, with no update in between, is not blamed on one",
   newly == [] and told == [] and machine_breaks.stopped() == {}, str(machine_breaks.stopped()))

print("an update that stops a machine that was working")
machine("quote-builder")
boot("release/2026.09.26.1")                                  # both start again
machine("price-list", BROKEN)                                 # what the new release does to it
newly, told = boot("release/2026.09.27.1")
rec = machine_breaks.stopped()
ok("it is recorded as stopped by that update, with the reason",
   newly == ["price-list"] and rec.get("price-list", {}).get("release") == "release/2026.09.27.1"
   and "a_name_the_new_release_removed" in rec["price-list"]["reason"], str(rec))
ok("and announced once, naming it only to the page", told == [(["price-list"], "release/2026.09.27.1")])
newly, told = boot("release/2026.09.27.1")
ok("a restart on the same release does not announce it again, and it stays listed",
   newly == [] and told == [] and "price-list" in machine_breaks.stopped())
machine("quote-builder", BROKEN)                              # already broken before the next update
boot("release/2026.09.27.1")
newly, told = boot("release/2026.09.28.1")
ok("a machine that was already broken before an update is not blamed on it",
   "quote-builder" not in newly and "quote-builder" not in machine_breaks.stopped(), str(newly))
machine("new-one", BROKEN)                                    # added and wrong, across an update
boot("release/2026.09.28.1")
newly, _ = boot("release/2026.09.29.1")
ok("a machine that never started is not blamed on an update", "new-one" not in newly)

print("fixed, removed, unknown")
machine("price-list")
boot("release/2026.09.29.1")
ok("a stopped machine that starts again is no longer listed",
   "price-list" not in machine_breaks.stopped())
machine("price-list", BROKEN)
boot("release/2026.09.30.1")
ok("(stopped again by the next update)", "price-list" in machine_breaks.stopped())
import shutil  # noqa: E402
shutil.rmtree(MINE / "price-list")
boot("release/2026.09.30.1")
ok("a stopped machine the owner removed is no longer listed", machine_breaks.stopped() == {})
machine("price-list")
boot("release/2026.09.30.1")
machine("price-list", BROKEN)
newly, told = boot("")
ok("with no release known, nothing is blamed on an update", newly == [] and told == [])

print("never raises into the worker")
from core import box_settings  # noqa: E402
real_put = box_settings.put
box_settings.put = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("database is locked"))
try:
    got = machine_breaks.observe([{"slug": "x", "ok": False}], release="release/2026.10.01.1")
    ok("a database that cannot be written returns nothing and raises nothing", got == [])
finally:
    box_settings.put = real_put
from core import worker  # noqa: E402
src = inspect.getsource(worker.load_modules)
ok("the worker's boot is where it runs, on exactly what the loader returned",
   "machine_breaks.observe(custom_machines.load(\"worker\"))" in src)

print("what the owner sees")
machine("price-list")
boot("release/2026.10.01.1")
machine("price-list", BROKEN)
boot("release/2026.10.02.1")
from core.dash import home  # noqa: E402
rows = home._custom_machine_rows()
ok("the page names the update that stopped it, and why",
   "Stopped by the update to 2026.10.02.1" in rows and "a_name_the_new_release_removed" in rows,
   rows[:300])
ok("the notification is one fixed sentence that names no machine, and opens that page",
   "price-list" not in machine_breaks.PUSH_BODY and machine_breaks.NAVIGATE == "/add-machine")
# THE INSTALLED APP IS THE INBOX MACHINE'S, and not every box ships it (a Lead box does not): this
# suite runs on exported boxes too, so it checks the app only where the app is.
sw_file = ROOT / "marketing" / "customer_voice" / "app.py"
if sw_file.is_file():
    # The allowlist was a chain of `to !== ...` when this suite was written. #1618 (Shifts)
    # restructured it into a positive `var door = ... to === ...` list, so the spelling moved
    # with it when the two were merged. Same assertion: the app permits /add-machine as a door.
    ok("and the installed app lets a notification open it",
       "to === '/add-machine'" in sw_file.read_text())
sent = []
from core import push  # noqa: E402
push.subscriptions_for = lambda user_id: [{"endpoint": "https://push.example/1"}]
push.send = lambda sub, **kw: sent.append(kw) or (True, "ok")
state.owner_user = lambda: {"id": "u_owner"}
machine_breaks._notify(["price-list"], "release/2026.10.02.1")
ok("the real notice goes to the owner's devices with that sentence",
   sent == [{"title": "Add a Machine", "body": machine_breaks.PUSH_BODY,
             "navigate": "/add-machine"}], str(sent))

print()
if _failed:
    print(f"{_failed} FAILED")
    sys.exit(1)
print("ALL OK")
