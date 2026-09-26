"""Machine packs — discover and validate `machine.yaml` manifests.

The manifest is the contract (docs/MACHINE_PACK_SPEC.md). This module is the ONLY reader of
its shape: the worker, the prefix registry, the installer, the exporter and the tests all
come through here, so a change to the format is made in one place and refused in one place.

A pack that cannot be validated does not exist to the system — it is not loaded, not
registered, not listed. Refusal names the field, because a silently-dropped manifest is how
a machine "ships" and does nothing.
"""
from __future__ import annotations

import os
import re
import pathlib
from typing import Iterator

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
FOUNDATION_VERSION_FILE = ROOT / "core" / "FOUNDATION_VERSION"

# Where a pack may live. The first is the layout (department → machine → plugins/); the second
# is where the first plug-in shipped before the layout existed, recognised until PR 7 moves it.
PACK_ROOTS = (
    ROOT / "marketing" / "lead_machine" / "plugins",
    ROOT / "marketing" / "content_machine" / "plugins",
)                                                    # marketing/machines/ retired 2026-09-05 (plan PR 7)
HOSTS = {"lead_machine", "content_machine"}
KINDS = {"places-campaign", "register-pull"}      # config recipes the host knows how to run
#   places-campaign  buys rows from Google Places, per request, under its own max_usd
#   register-pull    reads a PUBLISHED register for nothing; the reader ships in a pack,
#                    never here (owner, 2026-09-05: keep the core lightweight and clean)
AUTOPILOT_MAX_USD = 5                             # owner, 2026-09-05: "Five dollars per recipe." One number, three doors:
                                                  # the stamp (make_machine), the merge (allowlist), the box (arrivals)
_SLUG = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
_PREFIX = re.compile(r"^[a-z][a-z0-9_]{1,15}$")
_VERSION = re.compile(r"^\d+(\.\d+)*$")
# `needs:` arrived in this foundation. A box older than it ignores the field and would install the
# machine anyway, half of it, so a manifest that says `needs:` must also require this version: the
# older box then refuses it on `requires_foundation`, which it does understand. SCOPE_TIERS §2.3.
NEEDS_SINCE = "1.2"


class PackError(ValueError):
    """A manifest that does not meet the contract. The message names the field."""


def foundation_version() -> str:
    try:
        return FOUNDATION_VERSION_FILE.read_text().strip()
    except OSError:
        return "0"


def _vtuple(v: str) -> tuple:
    return tuple(int(x) for x in str(v).split("."))


def load(path: str | os.PathLike) -> dict:
    """Read one machine.yaml (or a pack directory holding one). Validated on the way in."""
    p = pathlib.Path(path)
    mf = p / "machine.yaml" if p.is_dir() else p
    if not mf.exists():
        raise PackError(f"{p}: no machine.yaml")
    try:
        data = yaml.safe_load(mf.read_text()) or {}
    except yaml.YAMLError as e:
        raise PackError(f"{mf}: not valid YAML — {e}") from e
    if not isinstance(data, dict):
        raise PackError(f"{mf}: top level must be a mapping")
    validate(data, mf.parent)
    data["_path"] = str(mf.parent)
    return data


def validate(m: dict, pack_dir: pathlib.Path | None = None) -> None:
    """Refuse anything that would let a pack ship broken, armed, or in the wrong place."""
    def need(field, typ=None):
        if field not in m:
            raise PackError(f"missing required field: {field}")
        if typ is not None and not isinstance(m[field], typ):
            raise PackError(f"{field}: expected {typ.__name__}, got {type(m[field]).__name__}")
        return m[field]

    if need("manifest", int) != 1:
        raise PackError(f"manifest: only format 1 exists, got {m['manifest']}")
    slug = need("slug", str)
    if not _SLUG.match(slug):
        raise PackError(f"slug: '{slug}' must match {_SLUG.pattern} (lowercase, hyphens)")
    need("name", str)
    host = need("host", str)
    if host not in HOSTS:
        raise PackError(f"host: '{host}' is not a core machine ({sorted(HOSTS)})")
    contract = need("contract", int)
    if contract != 1:
        raise PackError(f"contract: {contract} — this foundation speaks plug contract 1")
    rf = str(need("requires_foundation"))
    if not _VERSION.match(rf):
        raise PackError(f"requires_foundation: '{rf}' is not a version")
    why = needs_refused(m)
    if why:
        raise PackError(why)
    has_kind, has_module = "kind" in m, "module" in m
    if has_kind == has_module:
        raise PackError("exactly one of `kind` (config recipe) or `module` (code recipe) is required")
    if has_kind and m["kind"] not in KINDS:
        raise PackError(f"kind: '{m['kind']}' — this host runs {sorted(KINDS)}")
    if has_module and not re.match(r"^marketing\.[a-z_]+\.plugins\.[a-z_][a-z0-9_]*$|^marketing\.machines\.[a-z0-9_-]+$", str(m["module"])):
        raise PackError(f"module: '{m['module']}' must be a package under the host's plugins/")
    prefix = need("prefix", str)
    if not _PREFIX.match(prefix):
        raise PackError(f"prefix: '{prefix}' must match {_PREFIX.pattern}")
    need("industry", str)
    for lst in ("env", "vendors", "suites"):
        if lst in m and not isinstance(m[lst], list):
            raise PackError(f"{lst}: expected a list")
    cfg = need("config", dict)
    if cfg.get("enabled", None) is not False:
        raise PackError("config.enabled: must be present and false — a pack ships dark, always")
    if pack_dir is not None:
        expected = slug.replace("-", "_")
        if pack_dir.name not in (expected, slug):          # slug↔dir; the hyphen form is legacy
            raise PackError(f"slug '{slug}' does not match directory '{pack_dir.name}' (expected '{expected}')")
        for s in m.get("suites", []):
            if not (pack_dir / s).exists():
                raise PackError(f"suites: '{s}' does not exist in the pack")


def unattended_reasons(m: dict) -> list[str]:
    """Why a manifest may NOT be merged or armed with nobody in the loop (call validate() first).
    A bot may not raise a ceiling: the day's vendor spend is bounded only if every unattended
    machine sits under AUTOPILOT_MAX_USD; a person edits max_usd, a person arms it.

    THE BOUND FOLLOWS THE COST. A places-campaign buys every row, so the bound that matters is
    dollars. A register-pull buys nothing — it reads a file someone already published — so
    demanding a dollar ceiling of it would send every FREE machine to meet a person, which is
    exactly backwards while Places is capped (owner, 2026-09-06). What is unbounded there is not
    spend but ARRIVALS: an unfiltered national register is a hundred thousand strangers landing
    overnight. So a register-pull is judged on `max_push`, its row bound, and the runner still
    refuses an unfiltered pull at the door."""
    cfg = m.get("config") or {}
    reasons = []
    if cfg.get("enabled", None) is not False:
        reasons.append("config.enabled: must be false — a pack ships dark, always")

    def _positive(key):
        try:
            return float(cfg.get(key))
        except (TypeError, ValueError):
            return None

    # ...AND THE COST IS NOT THE KIND'S NAME. Judging this by `kind == "register-pull"` refused
    # the first free CODE machine (a `module:` pack reading SEC filings and public job boards,
    # 2026-09-06) — it buys nothing and was still asked for a dollar ceiling, which is the same
    # backwards outcome one name later. What decides is whether the machine SPENDS: a manifest
    # declaring no vendors has no dollars to bound, so it is bound on rows like a register pull.
    # A manifest that lies about its vendors is not caught here — the meter catches that at the
    # call — but it is a lie in a reviewed file, not a gap in the gate.
    _buys = [v for v in (m.get("vendors") or []) if str(v).strip()]
    if m.get("kind") == "register-pull" or not _buys:
        rows = _positive("max_push")
        if rows is None or rows <= 0:
            reasons.append("config.max_push: must be a positive number — a machine that buys nothing "
                           "is bounded on ROWS, and nobody asked for the whole file")
        spend = _positive("max_usd")            # a register costs nothing; if one is set it still binds
        if spend is not None and spend > AUTOPILOT_MAX_USD:
            reasons.append(f"config.max_usd: {cfg.get('max_usd')} is over the unattended ceiling "
                           f"${AUTOPILOT_MAX_USD:g} — a person raises a ceiling, not a bot")
        return reasons

    v = _positive("max_usd")
    if v is None or v <= 0:
        reasons.append("config.max_usd: must be a positive number — a campaign without a ceiling does not run")
    elif v > AUTOPILOT_MAX_USD:
        reasons.append(f"config.max_usd: {cfg.get('max_usd')} is over the unattended ceiling ${AUTOPILOT_MAX_USD:g} — a person raises a ceiling, not a bot")
    return reasons


def requires_ok(m: dict) -> tuple[bool, str]:
    """Does this foundation satisfy the pack's `requires_foundation`? (ok, message)."""
    have, want = foundation_version(), str(m.get("requires_foundation", "0"))
    if _vtuple(have) >= _vtuple(want):
        return True, f"foundation {have} ≥ {want}"
    return False, (f"this pack needs foundation {want}; this box is {have}. "
                   f"Take the update first: git pull && bash scripts/install.sh")


def needs_refused(m: dict) -> str:
    """Why a manifest's `needs:` is malformed, or "" (absent is fine). Shared by packs and the
    owner's own machines (core/custom_machines.py), so one field means one thing on a box.

    FEATURES, NEVER TIERS. `needs: [coworkers]`, never `needs: [pro]`: which tier includes a
    feature is core/tiers.py's one table, and a machine that named a tier would break the day a
    feature moved between tiers. An unknown name is refused, naming it."""
    if "needs" not in m:
        return ""
    wanted = m["needs"]
    if not isinstance(wanted, list) or not wanted or not all(isinstance(f, str) for f in wanted):
        return "needs: expected a list of feature names, e.g. needs: [coworkers]"
    from core import tiers
    unknown = [f for f in wanted if f not in tiers.FEATURES]
    if unknown:
        hint = " (that is a tier; name the feature it includes)" if unknown[0] in tiers.TIERS else ""
        return f"needs: '{unknown[0]}' is not a feature this foundation knows{hint}; known: {sorted(tiers.FEATURES)}"
    rf = str(m.get("requires_foundation", "0"))
    if not _VERSION.match(rf) or _vtuple(rf) < _vtuple(NEEDS_SINCE):
        return (f"needs: requires_foundation must be {NEEDS_SINCE} or later, "
                f"so a box too old to read needs: refuses this machine instead of installing half of it")
    return ""


def needs_ok(m: dict) -> tuple[bool, str]:
    """Does this box's plan include everything the manifest `needs`? (ok, message). Call
    needs_refused() first. Reads the plan without the config (tiers.features()), because
    discover() below runs while the config is loading."""
    wanted = m.get("needs") or []
    if not wanted:
        return True, ""
    from core import tiers
    return tiers.needs(wanted)


def _walk(roots) -> Iterator[dict]:
    seen = set()
    for r in (roots or PACK_ROOTS):
        r = pathlib.Path(r)
        if not r.is_dir():
            continue
        for d in sorted(r.iterdir()):
            if not d.is_dir() or d.name.startswith(("_", ".")) or not (d / "machine.yaml").exists():
                continue
            m = load(d)                                   # raises PackError, naming the field
            if m["slug"] in seen:
                raise PackError(f"two packs claim slug '{m['slug']}' ({d})")
            seen.add(m["slug"])
            yield m


def held_back(roots: Iterator[pathlib.Path] | None = None) -> list[tuple[str, str]]:
    """(slug, why) for every valid pack this box's plan does not include — for the log and the
    doctor. discover() leaves these out; they are waiting, not broken."""
    return [(m["slug"], why) for m in _walk(roots) for ok, why in [needs_ok(m)] if not ok]


def discover(roots: Iterator[pathlib.Path] | None = None, *, include_held: bool = False) -> list[dict]:
    """Every valid pack under the roots, sorted by slug. Invalid ones are reported, not loaded.

    A pack whose `needs:` this box's plan does not include is left out too, so nothing loads it:
    not the worker, not the web process, not the coworker runner, not its config defaults. That
    is checked here, at every start, so a pack copied onto the box by hand past add_machine.py's
    refusal still registers nothing (SCOPE_TIERS §2.3). held_back() says which, and why.
    `include_held` is for the installer only: a waiting pack still owns its slug and prefix."""
    return [m for m in _walk(roots) if include_held or needs_ok(m)[0]]


def invalid(roots=None) -> list[tuple[str, str]]:
    """(path, reason) for every machine.yaml that fails validation — for the doctor."""
    bad = []
    for r in (roots or PACK_ROOTS):
        r = pathlib.Path(r)
        if not r.is_dir():
            continue
        for d in sorted(r.iterdir()):
            if not d.is_dir() or d.name.startswith(("_", ".")):     # same rule as discover(): a _ folder (the template) is inert
                continue
            if d.is_dir() and (d / "machine.yaml").exists():
                try:
                    load(d)
                except PackError as e:
                    bad.append((str(d), str(e)))
    return bad
