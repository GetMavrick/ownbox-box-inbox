"""Self-describing add-on machines (docs/SCOPE_MACHINE_MARKETPLACE.md §3.3, owner-approved 2026-09-29).

WHY. Every new add-on machine used to edit the same six shared places: the Tiers table, the config's
`modules:`, `web_modules:` and `machine_features:`, the CI suite list and the menu. Two machines built
at once conflicted in all of them, every day. Now a machine describes itself in ONE file inside its
own folder, `<root>/<package>/machine.yaml`, and the box discovers it the way it already discovers
packs (core/packs.py). Adding a machine is adding a folder.

WHAT A MANIFEST SAYS (format `addon: 1`, every other field refused by name if unknown):

    addon: 1                      # this file's format
    slug: aeo                     # the machine's feature is machine:<slug>
    name: AEO Machine             # what a person reads
    modules: [marketing.aeo_machine]   # the module prefixes that ARE this machine: gated by the plan
    worker: [marketing.aeo_machine]    # imported by the worker (joins config `modules:`)
    web: [marketing.aeo_machine.app]   # mounted by the web process (joins config `web_modules:`)
    tiers: [pro]                  # tiers that include it; Base gets it through a plan's `add:`
    needs: []                     # FEATURES it needs, never tiers (SCOPE_TIERS §2.3)
    menu: {key:, title:, href:, order:}   # optional; registered for it when it has no code of its own
    product: {name: AEO Machine}  # optional; declared for the provisioner (Stripe is OSDev1's)
    config: {section: {...}}      # optional; top-level config defaults, BENEATH the tracked config
    suites: [tests/test_x.py]     # optional; its own suites, relative to its folder, run by CI

WHERE THE ANSWERS GO. `core/config.py` merges each machine into `get_config()` (its config defaults
beneath the tracked file, its modules and `machine_features:` entry after the overlay), so every
reader of `modules:`, `web_modules:` and `machine_features:` keeps working unchanged. `core/tiers.py`
builds Pro's features and FEATURES from `discover()`. `core/dispatch.py` registers a manifest's menu.

NEVER READS THE CONFIG. `get_config()` calls this, so a config read here would recurse. Only the
manifests on disk are read. A broken manifest is skipped and reported by `invalid()`, never raised:
one bad folder must not take down the box's config for every other machine.

CORE LEARNS A DIRECTORY, NEVER A MACHINE'S NAME (tests/test_core_boundary.py): slugs, modules and
titles here are data read from the machines' own folders.
"""
from __future__ import annotations

import functools
import os
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
FORMAT = 1
# The top-level folders whose packages may be add-on machines. An env override exists for tests only.
DEFAULT_ROOTS = ("marketing", "operations")

_SLUG = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_MODULE = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)+$")
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_FIELDS = {"addon", "slug", "name", "modules", "worker", "web", "tiers", "needs", "menu", "product",
           "config", "suites"}
_MENU_FIELDS = {"key", "title", "href", "order", "icon"}
# Sections a machine may never set through `config:`: they are the lists and maps this module
# feeds into the config itself, and the box's own identity.
_RESERVED_SECTIONS = {"modules", "web_modules", "machine_features", "machines", "operator", "brain",
                      "cost", "dash"}


class MachineError(ValueError):
    """A manifest that does not meet the format. The message names the field."""


def roots() -> list[pathlib.Path]:
    env = os.environ.get("AIOS_MACHINE_ROOTS")
    if env:
        return [pathlib.Path(p) for p in env.split(os.pathsep) if p]
    return [ROOT / r for r in DEFAULT_ROOTS]


def _strs(m: dict, field: str, pattern=None) -> list:
    v = m.get(field, [])
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise MachineError(f"{field}: expected a list of names")
    if pattern is not None:
        bad = [x for x in v if not pattern.match(x)]
        if bad:
            raise MachineError(f"{field}: '{bad[0]}' is not a valid name")
    return list(v)


def validate(m, folder: pathlib.Path | None = None) -> dict:
    """The manifest, normalised, or MachineError naming the first problem."""
    if not isinstance(m, dict):
        raise MachineError("machine.yaml must be a mapping")
    unknown = sorted(set(m) - _FIELDS)
    if unknown:
        raise MachineError(f"unknown field '{unknown[0]}'")
    if m.get("addon") != FORMAT:
        raise MachineError(f"addon: only format {FORMAT} exists, got {m.get('addon')!r}")
    slug = m.get("slug")
    if not isinstance(slug, str) or not _SLUG.match(slug):
        raise MachineError(f"slug: {slug!r} must be 2-32 lowercase letters, digits or underscores")
    name = m.get("name")
    if not isinstance(name, str) or not name.strip():
        raise MachineError("name: a name a person reads is required")
    modules = _strs(m, "modules", _MODULE)
    if not modules:
        raise MachineError("modules: name at least one module prefix that is this machine")
    worker, web = _strs(m, "worker", _MODULE), _strs(m, "web", _MODULE)
    for field, paths in (("worker", worker), ("web", web)):
        for p in paths:
            if not any(p == pre or p.startswith(pre + ".") for pre in modules):
                raise MachineError(f"{field}: '{p}' is not under this machine's modules")
    tiers = _strs(m, "tiers", _KEY)
    needs = _strs(m, "needs")
    menu = m.get("menu")
    if menu is not None:
        if not isinstance(menu, dict):
            raise MachineError("menu: expected a mapping (key, title, href, order)")
        extra = sorted(set(menu) - _MENU_FIELDS)
        if extra:
            raise MachineError(f"menu: unknown field '{extra[0]}'")
        if not all(isinstance(menu.get(k), str) and menu.get(k).strip() for k in ("key", "title", "href")):
            raise MachineError("menu: key, title and href are required")
        if not isinstance(menu.get("order", 100), int):
            raise MachineError("menu: order must be a whole number")
    product = m.get("product")
    if product is not None and not (isinstance(product, dict) and isinstance(product.get("name"), str)
                                    and set(product) <= {"name"}):
        raise MachineError("product: expected {name: <what the product is called>}")
    config = m.get("config") or {}
    if not isinstance(config, dict) or not all(isinstance(v, dict) for v in config.values()):
        raise MachineError("config: expected top-level sections, each a mapping")
    reserved = sorted(set(config) & _RESERVED_SECTIONS)
    if reserved:
        raise MachineError(f"config: '{reserved[0]}' belongs to the box, not a machine")
    suites = _strs(m, "suites")
    if folder is not None:
        missing = [s for s in suites if not (folder / s).is_file()]
        if missing:
            raise MachineError(f"suites: '{missing[0]}' does not exist in the machine's folder")
    return {"slug": slug, "feature": f"machine:{slug}", "name": name.strip(), "modules": modules,
            "worker": worker, "web": web, "tiers": tiers, "needs": needs, "menu": menu,
            "product": product, "config": config, "suites": suites,
            "folder": str(folder) if folder is not None else ""}


def _scan() -> tuple[list[dict], list[tuple[str, str]]]:
    import yaml
    found, bad, slugs, owned = [], [], {}, {}
    for root in roots():
        if not root.is_dir():
            continue
        for d in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))):
            f = d / "machine.yaml"
            if not f.is_file():
                continue
            try:
                m = validate(yaml.safe_load(f.read_text(encoding="utf-8")), d)
                if m["slug"] in slugs:
                    raise MachineError(f"slug '{m['slug']}' is already claimed by {slugs[m['slug']]}")
                clash = next((p for p in m["modules"] for q in owned
                              if p == q or p.startswith(q + ".") or q.startswith(p + ".")), None)
                if clash:
                    raise MachineError(f"modules: '{clash}' overlaps another machine's modules")
            except Exception as e:                  # noqa: BLE001 — one bad folder must not hide the rest
                bad.append((str(f), str(e)[:300]))
                continue
            slugs[m["slug"]] = str(d)
            owned.update({p: m["slug"] for p in m["modules"]})
            found.append(m)
    return found, bad


@functools.lru_cache(maxsize=1)
def _cached() -> tuple:
    found, bad = _scan()
    return tuple(found), tuple(bad)


def discover() -> list[dict]:
    """Every valid add-on machine on this box, in folder order. Never raises."""
    try:
        return [dict(m) for m in _cached()[0]]
    except Exception:                               # noqa: BLE001 — no yaml, unreadable disk: none
        return []


def invalid() -> list[tuple[str, str]]:
    """(manifest path, reason) for every machine.yaml that was refused — for the doctor and the log."""
    try:
        return list(_cached()[1])
    except Exception:                               # noqa: BLE001
        return []


def refresh() -> None:
    """Forget what was found (tests, and a box that just installed a machine)."""
    _cached.cache_clear()


def features() -> frozenset:
    """machine:<slug> for every machine found."""
    return frozenset(m["feature"] for m in discover())


def in_tier(tier: str) -> frozenset:
    """The features of the machines whose manifest says `tier` includes them."""
    return frozenset(m["feature"] for m in discover() if tier in m["tiers"])


def needs_of(feature: str) -> list:
    return next((list(m["needs"]) for m in discover() if m["feature"] == feature), [])


def products() -> dict:
    """{feature: product name}, declared for the provisioner (which maps Stripe products to features)."""
    return {m["feature"]: m["product"]["name"] for m in discover() if m.get("product")}


def into_config(cfg: dict, *, stage: str) -> dict:
    """Merge the machines into a config being loaded (core/config.py). Never raises.

    stage="defaults": each machine's `config:` sections go BENEATH the tracked config, so the tracked
    file and the owner's overlay both win over a machine's defaults.
    stage="wiring": after the overlay, each machine's worker and web modules are appended to
    `modules:` / `web_modules:` (skipping any already listed) and its module prefixes become its
    `machine_features:` entry. Whether a machine then LOADS is the plan's decision (tiers.module_on),
    never the list's, so an overlay can't switch a paid machine on or leave a gate unguarded.
    """
    try:
        machines = discover()
        if not machines:
            return cfg
        if stage == "defaults":
            from core.config import _deep_merge
            base: dict = {}
            for m in machines:
                base = _deep_merge(base, m["config"])
            return _deep_merge(base, cfg)
        out = dict(cfg)
        for key, field in (("modules", "worker"), ("web_modules", "web")):
            have = [p for p in (out.get(key) or []) if isinstance(p, str)]
            out[key] = have + [p for m in machines for p in m[field] if p not in have]
        mf = dict(out.get("machine_features") or {}) if isinstance(out.get("machine_features"), dict) else {}
        for m in machines:
            mf.setdefault(m["feature"], list(m["modules"]))
        out["machine_features"] = mf
        return out
    except Exception:                               # noqa: BLE001 — config must load even if machines can't
        return cfg
