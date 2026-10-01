"""Panels: a section one machine adds to another machine's screen (`m.panel(slot, ...)`, SDK 1).

docs/PLAN_LEAD_MAGNET_MACHINE.md §2, approved by the owner 2026-10-01 with OSDev1's review. A machine's
controls can belong on another machine's screen, where the owner already works. That screen must not be
edited for it, or it names a machine it doesn't own, and the machine can't move cleanly when it graduates.
So the screen that hosts a slot asks this registry what's in it, and a machine adds to it here. The
dependency points one way: the host knows a slot name, never a machine.

WHO OWNS WHAT:
  * this registry, keyed by slot name. Core knows no slot by name; a slot is a string a host screen chose.
  * the HOST machine declares the slot by rendering it (`render(slot)`) inside its own screen, so a panel
    is shown only to someone that screen already admitted: the host's access rules are the panel's.
  * a machine adds panels. Each is its own card; none can replace or reach into another.

ISOLATION (OSDev1's condition on the approval). A panel that raises renders a quiet "This section couldn't
load" card and is logged with its machine's name. It never breaks the screen it sits on.

ORDER. Stable, by title, so a box's screen doesn't reshuffle between restarts or imports.
"""
from __future__ import annotations

import html
import re

from core.logging import get_logger, scrub_secrets

log = get_logger(__name__)

_SLOT = re.compile(r"^[a-z][a-z0-9_-]{1,40}$")
_REGISTRY: dict[tuple[str, str, str], dict] = {}      # (slot, machine, title) -> panel


def register(slot: str, *, machine: str, title: str, render) -> None:
    """Add a panel titled `title` to `slot`. `render()` returns the panel's HTML, inside a card the box
    draws. Registering the same machine and title again replaces it (a re-import), never a second card."""
    if not _SLOT.match(str(slot or "")):
        raise ValueError(f"a panel's slot {slot!r} must be lowercase letters, digits, hyphens and underscores")
    title = str(title or "").strip()
    if not title or len(title) > 60:
        raise ValueError("a panel needs a title of 1 to 60 characters: it is the card's heading")
    if not callable(render):
        raise ValueError("a panel needs render=, a function that returns its HTML")
    _REGISTRY[(slot, machine, title)] = {"slot": slot, "machine": machine, "title": title, "render": render}


def panels(slot: str) -> list[dict]:
    """The panels in `slot`, in the order they are shown."""
    return sorted((p for p in _REGISTRY.values() if p["slot"] == slot),
                  key=lambda p: (p["title"].lower(), p["machine"]))


def render(slot: str) -> str:
    """Every panel in `slot`, each in its own card. '' when the slot is empty. Never raises."""
    cards = []
    for p in panels(slot):
        title = html.escape(p["title"])
        try:
            body = p["render"]()
            body = "" if body is None else str(body)
        except Exception as e:                             # noqa: BLE001 — one panel, never the screen
            log.error("panel.render_failed", slot=slot, machine=p["machine"], title=p["title"],
                      error=scrub_secrets(f"{type(e).__name__}: {str(e)[:200]}"))
            cards.append(f'<div class="card panel"><h2>{title}</h2>'
                         '<p class="quiet">This section couldn\'t load.</p></div>')
            continue
        cards.append(f'<div class="card panel"><h2>{title}</h2>{body}</div>')
    return "".join(cards)
