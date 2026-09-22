"""Nothing ships built and wired to nothing.

THE SHAPE THIS EXISTS FOR, three times in two weeks:

  · the connector was finished and no screen reached it (#1409)
  · POST /deploy was finished and nobody held a bearer to call it (#1414)
  · `ownboxEnableNotifications` was DEFINED, called by NOTHING, so subscribe() never ran, zero
    endpoints were ever stored, and `notify._ring` looped over zero rows and reported success —
    push had never worked on any box we had sold, and no test caught it (#1418, 2026-09-22)

Every one of them passed CI. Python's own import catches this for a periodic or a route — a name
that is not there raises — but the browser half of this product is JavaScript inside Python
string literals, which nothing type-checks, imports or runs. That is where the gap was, and this
measures it in BOTH directions:

  · a function a page DEFINES for the browser must be reachable from it — otherwise it is push
  · a `window.ownbox*` a page CALLS must be defined somewhere — otherwise it is a dead control

WHY THE CALL HALF IS SCOPED TO `ownbox`. `window` is ALSO an ordinary Python object in this repo
— `marketing/customer_voice/inbox` has a send window with `window.allowed_send()` and
`window.explain()` — and a page may legitimately call browser built-ins like
`window.matchMedia()`. Widening this to every `window.X()` reports all of those as dead controls,
which is how a guard earns the reputation that gets it deleted. `ownbox` is the product's own
prefix for what it defines, so it is exactly the set we are responsible for.

WHAT THIS CANNOT PROVE: that the wiring is CORRECT — the right button calling the right function
on the right screen. A rendered-page test answers that (test_the_box_asks_before_it_notifies, the
walk-every-screen suite). This answers the cheaper question nobody was asking at all: is it
connected to anything.

Run: python tests/test_nothing_ships_wired_to_nothing.py
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


# `window.NAME =` and `function NAME(` are unambiguously JavaScript: neither is valid Python, so
# anything matching lives inside a string literal a page serves.
_ASSIGNED = re.compile(r"window\.([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)")
_DECLARED = re.compile(r"\bfunction\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_CALLED = re.compile(r"window\.(ownbox[A-Za-z0-9_]*)\s*\(")


def scan(files: dict[str, str]) -> tuple[list[str], list[str]]:
    """(orphans, ghosts) for a set of {path: source}. One implementation, so the self-check below
    exercises the code that guards the product and not a second copy of it."""
    defined: dict[str, set[str]] = {}
    called: dict[str, set[str]] = {}
    for rel, text in files.items():
        for m in _ASSIGNED.finditer(text):
            defined.setdefault(m.group(1), set()).add(rel)
        for m in _DECLARED.finditer(text):
            defined.setdefault(m.group(1), set()).add(rel)
        for m in _CALLED.finditer(text):
            called.setdefault(m.group(1), set()).add(rel)
    # A MENTION IS ANY OCCURRENCE THAT IS NOT THE DEFINITION — a call, an onclick="name()", an
    # `if (!window.name)` guard, a reference handed to addEventListener. Counted across the whole
    # product: the page that defines a helper and the page that uses it are often different files.
    used: set[str] = set()
    for name in defined:
        for text in files.values():
            stripped = _ASSIGNED.sub("", _DECLARED.sub("", text))
            if re.search(rf"\b{re.escape(name)}\b", stripped):
                used.add(name)
                break
    return sorted(set(defined) - used), sorted(n for n in called if n not in defined)


# ── the scan catches the bug it was written for, or nothing below means anything ─────────────
print("— the scan catches the bug it was written for —")
PUSH_BEFORE = {"core/push.py": '''
_JS = """
  window.ownboxEnableNotifications = function () {
    return Notification.requestPermission();
  };
"""
'''}
o, g = scan(PUSH_BEFORE)
ok("push as it shipped — defined, called by nothing — is reported",
   o == ["ownboxEnableNotifications"], f"orphans={o}")

PUSH_AFTER = dict(PUSH_BEFORE, **{"marketing/customer_voice/app.py": '''
_JS = """
  if (!window.ownboxEnableNotifications) { return; }
  window.ownboxEnableNotifications();
"""
'''})
o, g = scan(PUSH_AFTER)
ok("...and once a page calls it, it is not", o == [], f"orphans={o}")

o, g = scan({"a.py": 'X = """ window.ownboxRingIt(); """'})
ok("a control calling a function nobody defines is reported", g == ["ownboxRingIt"], f"ghosts={g}")

o, g = scan({"a.py": 'w = window.allowed_send(x)\nq = window.matchMedia("(display-mode: standalone)")'})
ok("...while the repo's own Python `window` object and browser built-ins are not",
   g == [] and o == [], f"orphans={o} ghosts={g}")


# ── the product ─────────────────────────────────────────────────────────────────────────────
SOURCES = {p.relative_to(ROOT).as_posix(): p.read_text(errors="replace")
           for d in ("core", "marketing") for p in sorted((ROOT / d).rglob("*.py"))}
orphans, ghosts = scan(SOURCES)

print("\n— the scan sees the product, or every assertion below is vacuous —")
# NOT "DID IT FIND PUSH". This suite SHIPS, and is run inside every box a buyer owns — a Lead box
# carries no customer_voice tree and no core/push.py, so an assertion naming that file fails on a
# box that is perfectly healthy. (Measured: test_recipe_ships installs a pack into a fresh Lead
# box and runs all 143 shipped suites there; this one failed for exactly that reason.) The honest
# vacuity guard is that the scan read this box's own trees and found browser code in them at all.
ok("it read this box's shipped trees", len(SOURCES) >= 10, str(len(SOURCES)))
_defs = set()
for _t in SOURCES.values():
    _defs |= {m.group(1) for m in _ASSIGNED.finditer(_t)} | {m.group(1) for m in _DECLARED.finditer(_t)}
ok("...and this box defines browser functions the scan can see", bool(_defs), str(sorted(_defs)[:8]))

# A BOX ONLY INSTALLS THE MACHINES IT WAS SOLD, and core ships to all of them. `core/push.py`
# defines the notification helper; the only thing that calls it is the inbox. So on a LEAD box the
# helper is genuinely unreachable — measured by exporting one and running this there — and that is
# the shape of that box, not a defect in it. Named here with its reason, the way test_core_boundary
# names what it froze, so a NEW orphan still fails while this one does not cry wolf on every box.
#
# NOT ASSERTED STALE, deliberately. On a Customer Voice box this name is reachable and the list is
# empty, which is correct; a staleness check would then fail on the box where everything is right.
EXPECTED_ORPHANS = {
    "ownboxEnableNotifications":
        "core/push.py ships to every box; only the inbox calls it, so a box without the inbox "
        "carries it unreachable. Reachable wherever the inbox IS installed.",
}

print("\n— nothing is defined for a browser that no browser can reach —")
_new = [n for n in orphans if n not in EXPECTED_ORPHANS]
for n in orphans:
    if n in EXPECTED_ORPHANS:
        print(f"  --   {n} unreachable on this box — {EXPECTED_ORPHANS[n]}")
ok("no NEW orphans", not _new,
   f"{_new} — defined and mentioned nowhere else, which is exactly how push shipped")

print("\n— and no control calls an ownbox function that does not exist —")
ok("no dead controls", not ghosts, str(ghosts))

print("\n— this file cannot silently fall out of CI —")
# GUARDED, BECAUSE A BOX HAS NO REPOSITORY. Every suite here ships to a customer and is run from
# inside their box by `box_boots`; `.github/` is not in that tree, so an unguarded read crashes
# the suite on the machine it is meant to protect. Caught by the guard that exists for exactly
# this (tests/test_suite_integrity.py) the first time this file reached CI.
if not (ROOT / ".github").is_dir():
    print("  --   not the repo — a buyer's box has no CI manifest to be named in")
else:
    ok("test_nothing_ships_wired_to_nothing is in the workflow's suite list",
       "test_nothing_ships_wired_to_nothing" in (ROOT / ".github/workflows/tests.yml").read_text())

print("\nFAILED" if FAILS else "\nALL PASS")
sys.exit(1 if FAILS else 0)
