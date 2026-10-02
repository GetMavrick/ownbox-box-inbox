"""Department foundations: shared code a machine needs that is not the Base Machine
(owner, 2026-10-01: three layers, Base, department foundations, machines; keep the Base lean).

A machine says `needs: [foundation:marketing]`. That is a second kind of `needs:` entry beside plan features
(`coworkers`): validated here, NEVER a plan feature, so a tier can never gate a foundation. The box imports the
foundation's package (`<department>.foundation`) before the machine, in both processes, by putting it ahead of
the machine's modules in the one list both processes load from (core/machines.py). The exporter ships the
package with any box that carries a machine needing it (scripts/export_box.sh).

CORE NAMES NO DEPARTMENT: the slug is the directory, and the only check is that the package is here.
"""
from __future__ import annotations

import importlib.util
import re

PREFIX = "foundation:"
_SLUG = re.compile(r"^[a-z][a-z0-9_]{1,30}$")


def is_foundation(name) -> bool:
    return isinstance(name, str) and name.startswith(PREFIX)


def split(wanted) -> tuple[list[str], list[str]]:
    """(plan features, foundation entries) from a manifest's `needs:` list."""
    wanted = [w for w in (wanted or []) if isinstance(w, str)]
    return [w for w in wanted if not is_foundation(w)], [w for w in wanted if is_foundation(w)]


def module_of(name: str) -> str:
    """`foundation:marketing` -> `marketing.foundation`. "" for a malformed name."""
    slug = name[len(PREFIX):] if is_foundation(name) else ""
    return f"{slug}.foundation" if _SLUG.match(slug) else ""


def refused(name: str) -> str:
    """Why this entry cannot be honoured on this box, or "". Said once, at import, where somebody is watching."""
    mod = module_of(name)
    if not mod:
        return f"needs: '{name}' is not a foundation name; write foundation:<department>, like foundation:marketing"
    try:
        found = importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        found = False
    if not found:
        return (f"needs: this machine needs the {mod.split('.')[0]} foundation, which this box does not carry. "
                f"Take the update first: git pull && bash scripts/install.sh")
    return ""


def is_module(path) -> bool:
    """Is this dotted path a department foundation's package (`<department>.foundation`)?"""
    parts = str(path or "").split(".")
    return len(parts) == 2 and parts[1] == "foundation" and bool(_SLUG.match(parts[0]))


def modules_for(wanted) -> list[str]:
    """The foundation modules to import before a machine, in the order its manifest names them, each once."""
    out: list[str] = []
    for name in split(wanted)[1]:
        mod = module_of(name)
        if mod and mod not in out:
            out.append(mod)
    return out
