"""A connection declares its settings the way it already declares its set-up.

docs/PLAN_BOX_SETTINGS_AND_CONNECTIONS.md §3 and §6, step 3. `core/onboarding` already held the
credential half of a connection — copy, fields, `state()`, `save()`, validated at import. This adds
the other half, so **set-up and Settings are the same registry in two moods** rather than a second
screen that has to be kept in step with the first.

WHAT CORE STILL DOES NOT KNOW. A machine declares WHAT its settings are; core stores and serves
them. No machine implements storage, so no machine can implement it wrong — and `test_core_boundary`
keeps core free of machine-specific code exactly as before, because nothing here names a machine.

THE ROLE GATE IS ON THE WRITE, NOT THE SCREEN, and that is the property worth a test of its own: a
screen that hides a control is a courtesy, and a form can be posted without ever rendering the page
that would have hidden it.

Run: python tests/test_settings_on_a_registration.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "regset.db")
os.environ["DASH_TOKEN"] = "pw"
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"

from core import state  # noqa: E402

state.init_db()

from core import onboarding as ob  # noqa: E402

FAILS = []
# A word this product has never contained, so nothing can have an arm for it.
M = "gutter_pigeons"


def ok(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        FAILS.append(label)


def _register(settings=(), key="loft"):
    ob._STEPS.pop(key, None)
    ob.register_step(key, order=50, machine=M, title="Connect your gutter pigeons",
                     why="So the birds know which roof is yours.",
                     fields=({"name": "whistle", "label": "Whistle code", "type": "password"},),
                     steps=("Open the loft hatch.",),
                     state=lambda: {"status": "connected"},
                     save=lambda values, *, user_id=None: None,
                     settings=settings)


THREE = (
    {"name": "on", "label": "Notify me", "type": "toggle", "default": True,
     "scope": "person", "who": "member"},
    {"name": "hour", "label": "Hour", "type": "number", "default": 8},
    {"name": "when", "label": "Times", "type": "choice",
     "choices": ("both", "morning", "evening"), "default": "both"},
)


# ── 1. declared, and defaulted from the declaration ──────────────────────────────────────
print("\ntest_a_setting_works_the_day_it_is_declared")

_register(THREE)
got = ob.read_settings("loft", user_id="u-dana")
ok("every declared setting has its value", got == {"on": True, "hour": 8, "when": "both"}, str(got))
ok("THE DECLARED DEFAULT IS THE FLOOR, not None — a toggle nobody touched is not 'off'",
   got["on"] is True)

_register(())
ok("a step that declares none has none", ob.read_settings("loft") == {})
ok("...and settings_for says so without the caller reaching into the registry",
   ob.settings_for("loft") == [])
ok("an unknown step reads empty rather than raising", ob.read_settings("nosuch") == {})


# ── 2. the role gate is on the write ─────────────────────────────────────────────────────
print("\ntest_the_gate_is_where_the_write_is_not_where_the_screen_is")

_register(THREE)
ob.write_settings("loft", {"on": "false"}, user_id="u-dana", role="member")
ok("a member may change a member setting", ob.read_settings("loft", user_id="u-dana")["on"] is False)

try:
    ob.write_settings("loft", {"hour": "9"}, user_id="u-dana", role="member")
    said = ""
except ob.StepRejected as e:
    said = str(e)
ok("A MEMBER POSTING AN OWNER SETTING IS REFUSED — the form never rendered for him",
   "owner" in said.lower(), said)
ok("...with the setting's own label, so he can tell which control it was", "Hour" in said, said)
ok("...and nothing was written", ob.read_settings("loft")["hour"] == 8)

ob.write_settings("loft", {"hour": "9"}, user_id="u-owner", role="owner")
ok("an owner may", ob.read_settings("loft")["hour"] == 9)

# THE DEFAULT ROLE IS THE WEAK ONE. `write_settings(role=...)` defaults to "member", so a caller
# that forgets to pass a role gets the restrictive answer rather than the permissive one — which is
# the direction a gate has to fail.
try:
    ob.write_settings("loft", {"hour": "1"}, user_id="u-x")
    refused = False
except ob.StepRejected:
    refused = True
ok("a caller that forgets to pass a role is treated as a member, not an owner", refused)
ok("...and nothing was written", ob.read_settings("loft")["hour"] == 9)


# ── 3. only declared names, and only as themselves ───────────────────────────────────────
print("\ntest_a_crafted_form_cannot_write_into_a_machines_namespace")

ob.write_settings("loft", {"sneaky": "x"}, user_id="u-owner", role="owner")
ok("an undeclared name is DROPPED, never stored", "sneaky" not in ob.read_settings("loft"))
from core import box_settings as BS  # noqa: E402

ok("...and it is not hiding in the store under the machine's key",
   BS.get(M, "loft.sneaky", default=None) is None)

print("\ntest_a_form_posts_strings_and_the_store_keeps_types")

for raw, want in (("false", False), ("true", True), ("on", True), ("", False), ("0", False)):
    ob.write_settings("loft", {"on": raw}, user_id="u-dana", role="member")
    got = ob.read_settings("loft", user_id="u-dana")["on"]
    ok(f"a toggle posted as {raw!r} reads as {want}", got is want, repr(got))

ob.write_settings("loft", {"hour": "17"}, user_id="u-owner", role="owner")
ok("a number posted as a string reads as an int", ob.read_settings("loft")["hour"] == 17)
try:
    ob.write_settings("loft", {"hour": "lunchtime"}, user_id="u-owner", role="owner")
    said = ""
except ob.StepRejected as e:
    said = str(e)
ok("...and a number that is not one is refused in words", "number" in said.lower(), said)

try:
    ob.write_settings("loft", {"when": "never"}, user_id="u-owner", role="owner")
    said = ""
except ob.StepRejected as e:
    said = str(e)
ok("a choice outside the offered set is refused", bool(said), said)
ok("...AND THE POSTED VALUE IS NOT ECHOED BACK — it renders on a page", "never" not in said, said)


# ── 4. whose answer it is ────────────────────────────────────────────────────────────────
print("\ntest_a_person_setting_is_theirs_and_a_box_setting_is_everyones")

_register(THREE)
ob.write_settings("loft", {"on": "false"}, user_id="u-dana", role="member")
ok("one person's answer does not change another's",
   ob.read_settings("loft", user_id="u-sam")["on"] is True)
ob.write_settings("loft", {"hour": "6"}, user_id="u-owner", role="owner")
ok("a BOX setting changes for everybody", ob.read_settings("loft", user_id="u-sam")["hour"] == 6)
ok("...including for somebody who never signed in", ob.read_settings("loft")["hour"] == 6)

d = BS.describe(M, "loft.hour")
ok("and the box can say who changed it", d["set_by"] == "u-owner" and d["source"] == "box", str(d))


# ── 5. refused at import, where a mistake is a log line ──────────────────────────────────
print("\ntest_a_bad_declaration_never_reaches_a_buyers_screen")

for bad, why in (({"name": "x", "label": "X", "type": "banana"}, "an unknown type"),
                 ({"name": "x", "label": "X", "type": "choice"}, "a choice with no choices"),
                 ({"name": "x", "label": "X", "type": "toggle", "who": "wizard"}, "an unknown who"),
                 ({"name": "x", "label": "X", "type": "toggle", "scope": "galaxy"}, "an unknown scope"),
                 ({"name": "x", "type": "toggle"}, "no label"),
                 ({"label": "X", "type": "toggle"}, "no name")):
    try:
        _register((bad,), key="d2")
        ok(f"{why} is refused at import", False, "it registered")
    except ValueError:
        ok(f"{why} is refused at import", True)

try:
    _register(({"name": "x", "label": "A", "type": "toggle"},
               {"name": "x", "label": "B", "type": "toggle"}), key="d3")
    ok("the same setting declared twice is refused", False, "it registered")
except ValueError:
    ok("the same setting declared twice is refused", True)


# ── 5b. how long an answer may be ────────────────────────────────────────────────────────
print("\ntest_the_registry_caps_what_it_stores")

# OSDev1's gate on this step, 2026-09-17: "put() trusts its caller, so the registry must refuse
# undeclared keys and cap value size." The first half is section 3. This is the second.
#
# THREE OF THE FOUR TYPES NEED NO CAP AND THAT IS THE POINT — the bound is their shape, not a
# number somebody remembered to write down. Pinned so that a later type added without a bound is
# a failing test rather than a quiet row nothing can render.
_register(THREE)
ob.write_settings("loft", {"on": "x" * 5000}, user_id="u-dana", role="owner")
ok("a toggle cannot be long — a bool has no length",
   ob.read_settings("loft", user_id="u-dana")["on"] is False)
try:
    ob.write_settings("loft", {"hour": "9" * 5000}, user_id="u-dana", role="owner")
    ok("a number cannot be long", False, "it stored")
except ob.StepRejected:
    ok("a number cannot be long — it is an int or it is refused", True)
try:
    ob.write_settings("loft", {"when": "x" * 5000}, user_id="u-dana", role="owner")
    ok("a choice cannot be long", False, "it stored")
except ob.StepRejected:
    ok("a choice cannot be long — it is one of the declared list or it is refused", True)

# `text` IS THE ONE THAT NEEDS A NUMBER, because it is whatever arrived in the form body.
_register(({"name": "note", "label": "Signature", "type": "text", "default": ""},), key="cap")
ok("a text setting is capped by default without the machine saying anything",
   ob.settings_for("cap")[0]["max_len"] == ob._TEXT_MAX)
ob.write_settings("cap", {"note": "y" * ob._TEXT_MAX}, user_id="u-dana", role="owner")
ok("exactly the cap is allowed — the boundary is not off by one",
   ob.read_settings("cap", user_id="u-dana")["note"] == "y" * ob._TEXT_MAX)
try:
    ob.write_settings("cap", {"note": "y" * (ob._TEXT_MAX + 1)}, user_id="u-dana", role="owner")
    ok("one character over is refused", False, "it stored")
except ob.StepRejected as e:
    ok("one character over is refused", True)
    ok("...and the sentence NAMES the limit — 'too long' with no number is a form he cannot pass",
       str(ob._TEXT_MAX) in str(e), str(e))
    ok("...and it still never echoes what he typed", "yyy" not in str(e), str(e))
ok("THE REFUSAL LEFT THE OLD VALUE ALONE — a rejected save is not a save of nothing",
   ob.read_settings("cap", user_id="u-dana")["note"] == "y" * ob._TEXT_MAX)

_register(({"name": "note", "label": "Signature", "type": "text", "max_len": 12},), key="cap2")
ob.write_settings("cap2", {"note": "z" * 12}, user_id="u-dana", role="owner")
ok("a machine may name its own limit", ob.read_settings("cap2")["note"] == "z" * 12)
try:
    ob.write_settings("cap2", {"note": "z" * 13}, user_id="u-dana", role="owner")
    ok("...and the machine's limit is the one enforced, not the default", False, "it stored")
except ob.StepRejected:
    ok("...and the machine's limit is the one enforced, not the default", True)

for bad, why in ((ob._TEXT_CEILING + 1, "a limit past the ceiling"),
                 (0, "a limit of zero — a field nothing fits in"),
                 (-5, "a negative limit"),
                 ("200", "a limit that is a string"),
                 (True, "a limit that is a bool, which int() would have swallowed")):
    try:
        _register(({"name": "n", "label": "N", "type": "text", "max_len": bad},), key="d4")
        ok(f"{why} is refused at import", False, "it registered")
    except ValueError:
        ok(f"{why} is refused at import", True)

# A FORM IS ONE SAVE, NOT FIVE. A rejection part-way through would store the fields before the bad
# one and refuse the rest, so the buyer reads "that did not work" over a box that half changed.
_register(({"name": "a", "label": "A", "type": "text"},
           {"name": "b", "label": "B", "type": "text", "max_len": 5}), key="cap3")
ob.write_settings("cap3", {"a": "kept", "b": "ok"}, user_id="u-dana", role="owner")
try:
    ob.write_settings("cap3", {"a": "changed", "b": "far too long"}, user_id="u-dana", role="owner")
    ok("a form with one bad field is refused", False, "it stored")
except ob.StepRejected:
    ok("a form with one bad field is refused", True)
ok("NOTHING IN THAT FORM WAS WRITTEN — not even the field that was fine",
   ob.read_settings("cap3") == {"a": "kept", "b": "ok"}, str(ob.read_settings("cap3")))

# The same for the role gate, which is the other way a form can be refused mid-way.
_register(({"name": "mine", "label": "Mine", "type": "text", "who": "member"},
           {"name": "theirs", "label": "Theirs", "type": "text", "who": "owner"}), key="cap4")
try:
    ob.write_settings("cap4", {"mine": "yes", "theirs": "no"}, user_id="u-dana", role="member")
    ok("a member posting a form that touches an owner setting is refused", False, "it stored")
except ob.StepRejected:
    ok("a member posting a form that touches an owner setting is refused", True)
ok("...and the member's OWN field in that same form did not slip through",
   ob.read_settings("cap4") == {"mine": None, "theirs": None},   # no default declared
   str(ob.read_settings("cap4")))


# A DECLARATION THE CODE DOES NOT HONOUR IS A LIE THE NEXT READER BELIEVES, so a cap on a type
# that is already bounded is refused rather than ignored.
try:
    _register(({"name": "n", "label": "N", "type": "toggle", "max_len": 50},), key="d5")
    ok("max_len on a type that cannot use it is refused, not ignored", False, "it registered")
except ValueError:
    ok("max_len on a type that cannot use it is refused, not ignored", True)


# ── 6. it is still the same registry the set-up screen renders ───────────────────────────
print("\ntest_set_up_and_settings_are_one_registry")

_register(THREE)
entry = [e for e in ob.steps() if e["key"] == "loft"][0]
ok("the step carries its settings to whatever renders it", len(entry["settings"]) == 3)
ok("...and its credential fields are untouched beside them",
   [f["name"] for f in entry["fields"]] == ["whistle"])
entry["settings"].append({"name": "injected"})
ok("A SCREEN CANNOT EDIT THE REGISTRY IT READS — the list is copied, like the fields",
   len(ob.settings_for("loft")) == 3)

import inspect  # noqa: E402

src = inspect.getsource(ob)
for name in ("inbox", "customer_voice", "zernio", "resend", "anthropic", "gmail"):
    ok(f"core still names no machine: {name!r}",
       f'"{name}"' not in src and f"'{name}'" not in src)

print("\n— and this file cannot silently fall out of CI —")
import pathlib  # noqa: E402

_wf = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml"
if _wf.is_file():
    ok("test_settings_on_a_registration is in the workflow's suite list",
       "test_settings_on_a_registration" in _wf.read_text())
else:
    print("  --   no workflow file here (a box, not the repo) — nothing to check")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
