"""The SDK's two people calls: `m.person` and `m.touch` (OSDev1's ruling A1 on docs/PLAN_OWNBOX_RUNS_ON_OWNBOX.md;
docs/SCOPE_ONE_PERSON_RECORD.md).

  · a machine of your own meets a person by email and gets one person id; another machine meeting the same address
    gets the same id, so both see one person;
  · what happened is recorded once, under the machine's own `my_` name, with its ref;
  · a bad id, a bad kind of event or a missing person records nothing and never raises;
  · on a box with one Space the Space is implied; with several, a call that names none records nothing, and a call
    that names one files the person there.

Run: python tests/test_a_machine_knows_the_people_it_meets.py
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
T = pathlib.Path(tempfile.mkdtemp())
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")
os.environ["DISPATCH_BEARER_TOKEN"], os.environ["DASH_TOKEN"] = "bearer", "pw"

from core import people, sdk, spaces, state  # noqa: E402

state.init_db()
FAILS: list[str] = []


def ok(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


ONE = [{"name": "default"}]
spaces.all_spaces = lambda: ONE
guide = sdk.machine("lead-magnet")
crm = sdk.machine("med-spa-crm")

print("\none box, one Space —")
pid = guide.person("email", " Ava@Example.com ", name="Ava Reyes")
ok("a machine meets a person by email and gets a person id", isinstance(pid, str) and pid, repr(pid))
ok("another machine meeting the same address gets the same person", crm.person("email", "ava@example.com") == pid)
ok("...and the person record shows it", any(i.get("value") == "ava@example.com"
                                            for i in (people.person(pid) or {}).get("ids", [])),
   str(people.person(pid)))
ok("what happened is recorded", guide.touch(pid, "guide_sent", ref="reel-42") is True)
ok("...once, however many times it's told", guide.touch(pid, "guide_sent", ref="reel-42") is False)
ev = people.timeline(pid)
ok("...under the machine's own my_ name, with its ref", any(e.get("machine") == "my_lead_magnet"
                                                             and e.get("kind") == "guide_sent"
                                                             and e.get("ref") == "reel-42" for e in ev), str(ev))
ok("a prospect id is a way to know someone too", isinstance(guide.person("prospect", "3f2a9c1e-0b7d-4c55-9e1a-2b6f8d0c4e11"), str))

print("\nnothing is recorded for a bad call, and nothing raises —")
ok("an id that isn't one", guide.person("email", "not an email") is None)
ok("a kind of id the box doesn't know", guide.person("fax", "555-0100") is None)
ok("an event name with a step number in it (the step goes in ref)", guide.touch(pid, "touch1") is False)
ok("no person", guide.touch(None, "guide_sent") is False)

print("\na box with several Spaces —")
spaces.all_spaces = lambda: [{"name": "glowspa"}, {"name": "ironworks-gym"}]
ok("a call that names no Space records nothing: never filed under the wrong business",
   guide.person("email", "liam@example.com") is None)
ok("...but a touch needs no Space: the person already belongs to one", guide.touch(pid, "booked") is True)
gym_pid = guide.person("email", "liam@example.com", space="ironworks-gym")
ok("a call that names one files the person there", isinstance(gym_pid, str)
   and people.find("ironworks-gym", "email", "liam@example.com") == gym_pid
   and people.find("glowspa", "email", "liam@example.com") is None)
ok("a Space the box doesn't run records nothing", guide.person("email", "noa@example.com", space="nowhere") is None)
guide.touch(gym_pid, "class_booked", ref="spin-0600")
with state.connect() as _c:
    _stamped = [r["space"] for r in _c.execute("SELECT space FROM person_events WHERE kind = 'class_booked'")]
    _booked = [r["space"] for r in _c.execute("SELECT space FROM person_events WHERE kind = 'booked'")]
ok("an event is filed with the person's own Space, never another business's",
   _stamped == ["ironworks-gym"] and _booked == ["default"], f"class_booked {_stamped}, booked {_booked}")

print("\nnever raises —")
import sqlite3 as _sq  # noqa: E402

real_identify, real_touch = people.identify, people.touch
people.identify = lambda *a, **k: (_ for _ in ()).throw(_sq.OperationalError("database is locked"))
people.touch = lambda *a, **k: (_ for _ in ()).throw(_sq.OperationalError("database is locked"))
ok("a locked database: m.person returns None", guide.person("email", "x@example.com", space="glowspa") is None)
ok("...and m.touch returns False", guide.touch(gym_pid, "booked") is False)
people.identify, people.touch = real_identify, real_touch
ok("a number as the event kind", guide.touch(gym_pid, 5) is False)
ok("a list as the person", guide.touch(["p1"], "booked") is False)
real_all = spaces.all_spaces
spaces.all_spaces = lambda: (_ for _ in ()).throw(RuntimeError("config unreadable"))
ok("a config that can't be read", guide.person("email", "y@example.com") is None)
spaces.all_spaces = real_all

print("\nthe promise —")
ok("both calls are in the SDK's promised seams", {"person", "touch"} <= set(sdk.SEAMS))

print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    sys.exit(1)
print("all passed")
