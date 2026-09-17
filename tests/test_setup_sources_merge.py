"""Registering one set-up step must not delete the others.

THE LANDMINE, MEASURED 2026-09-17 (OSDev4, scope #1307). `core/onboarding.py` exists so a machine
registers its own set-up step instead of core carrying it, and `app._setup_source` PREFERRED it:

    live = [dict(e) for e in onboarding.steps()]
    if live:
        return live

So the first step anybody registered through the seam — about anything at all — made every step
still living in `box_secrets` vanish from the screen. The set-up page went from
['email', 'zernio', 'anthropic'] to ['relay'] on one unrelated registration. Not a crash: a page
that renders, and is wrong, on the screen a buyer uses to connect his box. Nothing in production
registers through the seam yet, which is the only reason this had not fired.

MERGING IS ALSO WHAT MAKES THE MIGRATION POSSIBLE, which is why the fix is not just defensive.
`core/onboarding`'s own comment says the old section gets deleted "once every step is registered" —
under the old behaviour that meant all three had to move in ONE commit or the screen broke in
between. Merged, a step moves on its own.

Run: python tests/test_setup_sources_merge.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "merge.db")
os.environ["DASH_TOKEN"] = "pw"
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"

from core import state  # noqa: E402

state.init_db()

from core import onboarding  # noqa: E402
from marketing.customer_voice import app  # noqa: E402

FAILS = []


def ok(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        FAILS.append(label)


def _register(key, order, title, status="connected"):
    onboarding.register_step(
        key, order=order, machine="core", title=title, why="why a buyer would do this",
        fields=({"name": "k", "label": "Key", "type": "password"},),
        steps=("do the thing",), state=lambda: {"status": status},
        save=lambda values, user_id=None: None)


print("\ntest_the_old_steps_survive_a_new_seam_step")

base = [e["key"] for e in app._setup_source()]
ok("the box starts with the three core credentials", base == ["email", "zernio", "anthropic"], str(base))

_register("relay", 40, "Relay")
after = [e["key"] for e in app._setup_source()]
ok("A NEW SEAM STEP ADDS ITSELF AND DELETES NOTHING — the whole bug, in one assertion",
   after == ["email", "zernio", "anthropic", "relay"], str(after))
ok("...and the buyer's own order is untouched", after[:3] == base, str(after))


print("\ntest_a_migrated_step_replaces_its_predecessor_in_place")

_register("email", 10, "Migrated mailbox")
merged = app._setup_source()
keys = [e["key"] for e in merged]
ok("migrating a key does not duplicate it", keys.count("email") == 1, str(keys))
ok("...and the SEAM's version is the one that renders",
   [e["title"] for e in merged if e["key"] == "email"] == ["Migrated mailbox"], str(keys))
ok("...IN PLACE, not jumped to the end by its order — the owner set this screen's order",
   keys == ["email", "zernio", "anthropic", "relay"], str(keys))


print("\ntest_the_page_outranks_the_seam")

_broken = onboarding.steps
onboarding.steps = lambda: (_ for _ in ()).throw(RuntimeError("a machine registered nonsense"))
survived = [e["key"] for e in app._setup_source()]
ok("A SEAM THAT RAISES NEVER TAKES THE SET-UP PAGE DOWN — the old contract renders alone",
   survived == ["email", "zernio", "anthropic"], str(survived))
onboarding.steps = _broken

print("\ntest_every_entry_still_carries_what_the_screen_binds_to")

for e in app._setup_source():
    missing = [f for f in ("key", "title", "why", "fields", "status") if f not in e]
    ok(f"{e['key']}: renders with no missing field", not missing, str(missing))

print("\n— and this file cannot silently fall out of CI —")
import pathlib  # noqa: E402

_wf = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml"
if _wf.is_file():
    ok("test_setup_sources_merge is in the workflow's suite list",
       "test_setup_sources_merge" in _wf.read_text())
else:
    print("  --   no workflow file here (a box, not the repo) — nothing to check")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
