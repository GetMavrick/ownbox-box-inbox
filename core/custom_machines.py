"""The owner's own machines — `my/machines/<name>/` — loaded without ever forking the box.

WHY THIS EXISTS. Owner, 2026-09-23: *"We want to encourage people to be able to build their own
machines."* Until now the only way to do that was to edit tracked files, and one edited tracked
file stops every update (`core/release/verify.py`, `dirty_tree`). A buyer who took us at our word
silently lost security updates. `my/` is the zone an update never touches — `publish_box.sh`
leaves it out of every release — so a machine that lives there can never collide with one of ours,
and the box keeps updating underneath it. Scope and decisions: docs/SCOPE_ADD_MACHINE_PAGE.md (R2).

WHAT A MACHINE IS HERE. One self-contained folder: a `machine.yaml` manifest and a Python package
(`__init__.py`). Importing the package is the whole contract — it registers through the same seams
our machines use (`shell.register_section`, `worker.register` / `register_periodic`,
`state.register_schema`, `report.register_reporter`, `connector.tools.register`), and a package that
exposes a Flask `blueprint` has it mounted by the web process. Nothing outside the folder is read,
so a folder can later be zipped, shared or sold without redesign (the marketplace is about five
years out — owner, 2026-09-23 — and this is the only thing done for it now).

A BROKEN MACHINE NEVER BREAKS THE BOX (R8). This runs inside the web process and the worker at
boot. A machine that raised here would take `/dispatch` down with it, and — worse — a box that fails
its post-update health check rolls the update back, so one bad customer machine would pin the box
on its old release forever. So every machine loads inside its own try/except: a failure is logged,
recorded in `status()` for the Add a Machine page to show, and skipped. Nothing here ever raises.

CORE LEARNS A DIRECTORY, NEVER A MACHINE'S NAME. `tests/test_core_boundary.py` still holds.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import re
import sys

from core.logging import get_logger

log = get_logger(__name__)

ROOT = pathlib.Path(__file__).resolve().parents[1]

# `^[a-z][a-z0-9-]{1,40}$` — the same slug rule packs use, so one name means one thing on a box.
_SLUG = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
_REQUIRED = ("name", "version", "requires_foundation")

# slug -> {"ok": bool, "reason": str, "version": str, "path": str}. Read by the page; written here.
_STATUS: dict[str, dict] = {}
_BLUEPRINTS: dict[str, object] = {}


def machines_dir() -> pathlib.Path:
    """Where the owner's machines live. An env override exists for tests only."""
    return pathlib.Path(os.environ.get("AIOS_MY_MACHINES") or (ROOT / "my" / "machines"))


def _manifest(folder: pathlib.Path) -> tuple[dict | None, str]:
    """(manifest, "") or (None, why it cannot load). Never raises."""
    f = folder / "machine.yaml"
    if not f.is_file():
        return None, "no machine.yaml in the folder"
    try:
        import yaml
        m = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    except Exception as e:                          # noqa: BLE001 — a bad file is a reason, not a crash
        return None, f"machine.yaml could not be read: {type(e).__name__}"
    if not isinstance(m, dict):
        return None, "machine.yaml must be a mapping"
    missing = [k for k in _REQUIRED if not str(m.get(k) or "").strip()]
    if missing:
        return None, "machine.yaml is missing " + ", ".join(missing)
    name = str(m["name"]).strip()
    if not _SLUG.match(name):
        return None, f"name '{name}' must be lowercase letters, digits and hyphens"
    if name != folder.name:
        return None, f"name '{name}' must match its folder '{folder.name}'"
    if not (folder / "__init__.py").is_file():
        return None, "no __init__.py — a machine is a Python package"
    try:
        from core import packs
        ok, why = packs.requires_ok(m)
    except Exception as e:                          # noqa: BLE001
        ok, why = False, f"foundation version could not be checked: {type(e).__name__}"
    if not ok:
        return None, why
    # `needs: [coworkers]` — checked at every start, so a folder copied onto a Base box shows
    # "needs Base Machine Pro" on the Add a Machine page and registers nothing (SCOPE_TIERS §2.3).
    try:
        why = packs.needs_refused(m)
        ok, held = packs.needs_ok(m) if not why else (False, why)
    except Exception as e:                          # noqa: BLE001
        ok, held = False, f"the plan could not be checked: {type(e).__name__}"
    if not ok:
        return None, held
    return m, ""


def discover() -> list[dict]:
    """Every folder under `my/machines/`, valid or not, sorted by name. Never raises."""
    base = machines_dir()
    out = []
    try:
        folders = sorted(p for p in base.iterdir() if p.is_dir()
                         and not p.name.startswith(("_", ".")))
    except OSError:
        return out                                  # no my/machines/ at all: nothing to load
    for d in folders:
        m, why = _manifest(d)
        out.append({"slug": d.name, "path": str(d), "manifest": m, "reason": why})
    return out


def _module_name(slug: str) -> str:
    return "my_machines." + slug.replace("-", "_")


def load(process: str) -> list[dict]:
    """Import every valid machine, each in isolation. Returns this call's results. Never raises.

    `process` only labels the log line ("dispatch", "worker", "registrations"): registrations are
    module-level globals and a box runs several processes, so each process imports for itself —
    the same reason `dispatch._load_packs` exists. Importing twice is a no-op (sys.modules).
    """
    results = []
    for d in discover():
        slug = d["slug"]
        if d["manifest"] is None:
            _STATUS[slug] = {"ok": False, "reason": d["reason"], "version": "", "path": d["path"]}
            log.warning("custom_machine.invalid", slug=slug, process=process, reason=d["reason"])
            results.append({"slug": slug, **_STATUS[slug]})
            continue
        name = _module_name(slug)
        version = str(d["manifest"].get("version"))
        try:
            mod = sys.modules.get(name)
            if mod is None:
                folder = pathlib.Path(d["path"])
                spec = importlib.util.spec_from_file_location(
                    name, folder / "__init__.py", submodule_search_locations=[str(folder)])
                mod = importlib.util.module_from_spec(spec)
                sys.modules[name] = mod
                try:
                    spec.loader.exec_module(mod)
                except BaseException:
                    sys.modules.pop(name, None)     # a half-imported module must not look loaded
                    raise
            bp = getattr(mod, "blueprint", None)
            if bp is not None:
                _BLUEPRINTS[slug] = bp
            _STATUS[slug] = {"ok": True, "reason": "", "version": version, "path": d["path"]}
            log.info("custom_machine.loaded", slug=slug, process=process, version=version)
        except BaseException as e:                  # noqa: BLE001 — see the module docstring (R8)
            if isinstance(e, KeyboardInterrupt):
                raise
            why = f"{type(e).__name__}: {str(e)[:200]}"
            _STATUS[slug] = {"ok": False, "reason": why, "version": version, "path": d["path"]}
            log.error("custom_machine.import_failed", slug=slug, process=process, error=why)
        results.append({"slug": slug, **_STATUS[slug]})
    return results


def blueprints() -> dict[str, object]:
    """slug -> Flask blueprint, for every loaded machine that offers one."""
    return dict(_BLUEPRINTS)


def status() -> list[dict]:
    """What the Add a Machine page shows: every machine seen, and why any did not load."""
    return [{"slug": s, **v} for s, v in sorted(_STATUS.items())]
