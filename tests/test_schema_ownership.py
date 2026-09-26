"""E3 — schema ownership is structural (docs/AIOS_MASTER_PLAN.md §4 E3, MACHINE_PACK_SPEC.md rule 9).

The schema split (A3, #1124) moved every machine's tables out of core/state.py. Nothing in A3
stops the NEXT machine putting a table back, or a migration quietly reaching across a machine
boundary, or a pack creating a table whose name a machine already owns. The owner's rule —
"do not weigh down the core application for one specific use case" — is a sentence until a
test goes red the moment someone breaks it. This is that test. Four assertions, each measured
against the tree, none against a hand-kept list:

  (a) every kernel table is named by core/ itself — a table only a machine reads is a
      machine's table in the wrong place;
  (b) a migration step touches its owner's tables and the kernel's, never another machine's;
      an untagged step touches the kernel only;
  (e) a pack's tables collide with nothing above them — no kernel or machine name, no other pack;
  (f) a pack imports its host's door and a short, measured list from core/ — nothing else.

(c) core/ naming a machine table in two files only, and (d) the four table sets being disjoint,
already live in tests/test_schema_integrity.py and tests/test_schema_split.py; not repeated here.

Ships to every image. It loads machines from their schema.py files and packs from their
directories, never by import — the exporter drops a suite whose static imports name a machine
the image lacks — so on a one-machine box it measures that machine; on CI it measures all.
"""
from __future__ import annotations

import ast
import inspect
import os
import pathlib
import re
import sys
import tempfile

os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "ownership.db")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import state  # noqa: E402

FAILS: list[str] = []
_CREATE = re.compile(r"CREATE TABLE IF NOT EXISTS (\w+)")


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


# ── the map, measured ──────────────────────────────────────────────────────────
KERNEL = set(_CREATE.findall(state.SCHEMA))
MACHINES: dict[str, set[str]] = {}          # registry key -> its tables (only machines this tree ships)
for pkg, key in (("customer_voice", "customer_voice"), ("lead_machine", "lead"), ("content_machine", "content"),
                 ("aeo_machine", "aeo_machine")):
    f = ROOT / "marketing" / pkg / "schema.py"
    if f.exists():
        MACHINES[key] = set(_CREATE.findall(f.read_text()))
OWNER = {t: "kernel" for t in KERNEL}
for key, tables in MACHINES.items():
    OWNER.update({t: key for t in tables})
KNOWN = set(OWNER)
PACKS: dict[pathlib.Path, str] = {}        # pack dir -> host package name
for d in sorted(ROOT.glob("marketing/*/plugins/*/")):
    if d.name.startswith("_") or not (d / "machine.yaml").exists():
        continue
    PACKS[d] = d.parents[1].name            # marketing/<host>/plugins/<pack>
print(f"\n— measured: kernel {len(KERNEL)} tables; machines {', '.join(f'{k}={len(v)}' for k, v in MACHINES.items()) or 'none'}; packs {len(PACKS)} —")


def _names_in(text: str, universe: set[str]) -> set[str]:
    if not universe:
        return set()
    pat = re.compile(r"\b(" + "|".join(sorted(map(re.escape, universe), key=len, reverse=True)) + r")\b")
    return set(pat.findall(text))


# ── (a) every kernel table is the kernel's to read ─────────────────────────────
print("\n— (a) a kernel table is named by core/ itself —")


def _state_py_without_definitions() -> str:
    """core/state.py is both the kernel's definition (the SCHEMA literal, the migration steps) and
    the accessor layer for several kernel tables (record_spend → spend_ledger, save_checkpoint →
    job_checkpoints, _replay_machine → schema_state). Naming a table in a definition proves
    nothing; naming it in an accessor is exactly what (a) asks. So the definitions are cut out and
    the rest of the file counts as kernel code."""
    src = inspect.getsource(state)
    m = re.search(r'SCHEMA\s*=\s*"""', src)
    if m:
        src = src[:m.start()] + src[src.index('"""', m.end()) + 3:]
    for fn in state.MIGRATIONS.values():
        src = src.replace(inspect.getsource(fn), "")
    return src


core_files = [p for p in (ROOT / "core").rglob("*.py") if p.name != "state.py"]
core_files += [p for p in (ROOT / "app.py",) if p.exists()]
core_text = "\n".join(p.read_text() for p in core_files) + "\n" + _state_py_without_definitions()
named_by_core = _names_in(core_text, KERNEL)
for t in sorted(KERNEL):
    ok(f"kernel table {t} is read or written by kernel code", t in named_by_core,
       "nothing under core/ names it outside SCHEMA and MIGRATIONS — it belongs in a machine's schema.py")

# ── (b) a migration step stays inside its owner's tables ───────────────────────
print("\n— (b) a migration step names its owner's tables and the kernel's, never another machine's —")
for version in sorted(state.MIGRATIONS):
    src = inspect.getsource(state.MIGRATIONS[version])
    owner = state._MIGRATION_OWNER.get(version)
    named = {OWNER[t] for t in _names_in(src, KNOWN)}
    allowed = {"kernel", owner} if owner else {"kernel"}
    # A step may CREATE a table (that is how one reaches a box mid-life) but every such table must
    # also be in its owner's DDL. That half can only be judged where the owner's DDL is on the
    # image: a Lead box carries no content_machine/schema.py, so a content step's CREATE is
    # unknowable there, not wrong. The full tree on CI judges every owner.
    created = (set(_CREATE.findall(src)) - KNOWN) if (owner is None or owner in MACHINES) else set()
    ok(f"step {version} ({owner or 'kernel'}) touches only {sorted(allowed)}", named <= allowed and not created,
       f"names {sorted(named)}; creates unknown {sorted(created)}")
ok("every tagged owner is a machine key the registry knows",
   set(state._MIGRATION_OWNER.values()) <= {"customer_voice", "lead", "content", "aeo_machine"},
   str(sorted(set(state._MIGRATION_OWNER.values()))))
if len(MACHINES) < 3:
    print("  note: this image ships fewer than three machines, so (b) sees only the tables it carries; CI runs the full tree")

# ── (e) a pack's tables collide with nothing above them ────────────────────────
print("\n— (e) a pack's tables are its own — no kernel, machine or other-pack name —")
pack_tables: dict[str, set[str]] = {}
for d, host in PACKS.items():
    tables: set[str] = set()
    for p in d.glob("*.py"):
        tables |= set(_CREATE.findall(p.read_text()))
    pack_tables[d.name] = tables
    clash = tables & KNOWN
    ok(f"pack {d.name} ({len(tables)} tables) names no kernel or machine table", not clash, str(sorted(clash)))
_seen: dict[str, str] = {}
for name, tables in pack_tables.items():
    for t in sorted(tables):
        ok(f"pack table {t} is claimed by one pack only", t not in _seen, f"also in {_seen.get(t)}")
        _seen.setdefault(t, name)
if not PACKS:
    ok("no packs ship on this image, so (e) has nothing to measure here (CI measures the tree)", True)

# ── (f) a pack imports its host's door and a measured list from core/ ──────────
print("\n— (f) a pack imports marketing.<host>.plug and a short list from core/, nothing else —")
# Measured 2026-09-13 over the four shipped packs. Widen it here, in one place, with a reason.
CORE_ALLOWED = {"core.logging", "core.config", "core.worker", "core.state", "core.packs", "core.net"}


def _imports(path: pathlib.Path) -> set[str]:
    """Fully qualified names a file imports: `import a.b` → a.b; `from a.b import c` → a.b.c.
    The bare `a.b` of a from-import is NOT counted — `from core import state` reaches core.state,
    not the whole of core, and the allowlist is judged on what is actually bound."""
    out: set[str] = set()
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError as e:                       # a pack that does not parse is its own failure
        return {f"<syntax error: {e}>"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out |= {f"{node.module}.{a.name}" for a in node.names}
    return out


for d, host in PACKS.items():
    own = f"marketing.{host}.plugins.{d.name}"
    door = f"marketing.{host}.plug"
    bad: set[str] = set()
    for p in d.rglob("*.py"):
        for mod in _imports(p):
            if mod.startswith("marketing"):
                if not (mod == door or mod.startswith(door + ".") or mod == own or mod.startswith(own + ".")):
                    bad.add(mod)
            elif mod.startswith("core"):
                base = ".".join(mod.split(".")[:2])
                if base not in CORE_ALLOWED and mod not in CORE_ALLOWED:
                    bad.add(mod)
            elif mod.startswith("<"):
                bad.add(mod)
    ok(f"pack {d.name} imports only its door ({door}), itself, and the core/ allowlist", not bad, str(sorted(bad)))

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
