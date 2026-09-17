"""The box's navigation — two levels, one rail, and core never naming a machine.

THE OWNER RULED THE SHAPE (2026-09-17, in OSDev1's session, quoted on the wall): *"They should go
to /dashboard and then they should see some helpful information and can click settings to set
things up. That should be a two level navigation so when people click settings, the choices in the
left navigation change to that sub menu and then there's a back arrow at the top."* He then asked
for the same move at the top of the inbox — a back arrow home, with the inbox's own choices where
the main nav was.

SO THE RAIL IS NOT TWO RAILS. Level 2 REPLACES level 1 in place; it does not sit beside it and it
does not push it off-screen. That is the whole reason the pattern survives a phone, which is where
this box is mostly read: one column of choices at a time, and one way back.

WHY THIS IS A REGISTRY AND NOT A LIST. `core/` ships whole into every box, so a rail that spelled
out the sections would put one product's menu into every other product — the exact thing
`test_core_boundary` exists to stop, and the reason `register_step`, `register_reporter` and
`register_schema` are shaped the way they are. A machine declares its own section at import; core
orders, resolves and renders. A box that ships no machine has a rail with only what core itself
registered, which is correct rather than empty.

IT TAKES THE PATH AND RETURNS THE RAIL — a function of one argument, with no request, no globals
and no caller-supplied "which level am I" flag. That flag is the bug this signature refuses: a
screen that passed the wrong one would draw a back arrow to nowhere, or hide the main nav on the
main nav's own page, and nothing would catch it because the caller would simply be lying politely.
The path already knows. Ask it.

WHAT CORE DOES NOT DO HERE. It does not know what a connection is, it does not read a setting, and
it renders no field. `rail()` answers "where am I and what is next to me"; everything the buyer
actually edits belongs to the machine that owns it, behind its own registration.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from core.logging import get_logger

log = get_logger(__name__)

_KEY = re.compile(r"^[a-z][a-z0-9_]{1,39}$")

# HOW AN ITEM IS DRAWN, as a closed set for the same reason `onboarding.STATUSES` is one: an
# unknown tone reaching a template renders as an unstyled row in front of a paying customer.
#
# `danger` IS NOT DECORATION. The owner's reference puts Danger Zone in red and alone at the
# bottom, and red here means "this ends something" — it is the one tone a rail may use to slow a
# person down. `away` marks a choice that LEAVES the box (billing on someone else's domain); it
# earns its own tone because a link that navigates away without saying so is how a person loses
# work they had half-typed.
TONES = frozenset({"", "danger", "away"})


@dataclass(frozen=True)
class Item:
    """One choice inside a section — the second level."""
    key: str
    label: str
    href: str
    tone: str = ""


@dataclass(frozen=True)
class Section:
    """One choice in the main rail — the first level. `items` makes it a second level too."""
    key: str
    order: int
    machine: str
    title: str
    href: str
    items: tuple[Item, ...] = ()
    home: bool = False


@dataclass(frozen=True)
class Rail:
    """What to draw, already decided. A template using this needs no conditionals of its own.

    `level` is 1 or 2. At level 2 `back` is always a real path, never "" — a back arrow that goes
    nowhere is the dead control this codebase keeps deleting by name, and it would appear on the
    one screen whose whole job is to be escapable.
    """
    level: int
    title: str
    back: str
    items: tuple[Item, ...]
    here: str


_SECTIONS: dict[str, Section] = {}


def register_section(key: str, *, order: int, machine: str, title: str, href: str,
                     items: Iterable = (), home: bool = False) -> None:
    """Declare one rail section. Called at import, like every other seam in this box.

    CHECKED HERE, AT IMPORT, where a mistake is a failed boot line — not on the screen, where it
    would be a customer's broken menu. `register_step` says the same thing about itself and for
    the same reason; this is deliberately its twin, so a machine author who has met one has met
    both.

    IDEMPOTENT FOR THE SAME MACHINE so a re-import (tests, a reload) cannot stack two, and REFUSED
    for a key another machine already holds so one machine cannot silently take over another's
    place in the rail.
    """
    if not _KEY.match(key or ""):
        raise ValueError(f"rail section {key!r} must be a short lowercase slug")
    if not machine or not isinstance(machine, str):
        raise ValueError(f"rail section {key!r} must name the machine that owns it")
    held = _SECTIONS.get(key)
    if held is not None and held.machine != machine:
        raise ValueError(f"rail section {key!r} is already registered by {held.machine!r}")
    if not isinstance(order, int):
        raise ValueError(f"rail section {key!r} needs an integer order")
    if not (title or "").strip():
        raise ValueError(f"rail section {key!r} needs a title a buyer can read")
    if not (href or "").startswith("/"):
        raise ValueError(f"rail section {key!r} needs an absolute path, got {href!r}")

    built = []
    seen = set()
    for raw in items or ():
        it = dict(raw)
        ikey = str(it.get("key") or "")
        if not _KEY.match(ikey):
            raise ValueError(f"rail section {key!r} has an item with a bad key {ikey!r}")
        if ikey in seen:
            # TWO ITEMS, ONE KEY is not a cosmetic duplicate: the rail marks the current choice by
            # key, so the second would light up whenever the first was open.
            raise ValueError(f"rail section {key!r} repeats item key {ikey!r}")
        seen.add(ikey)
        if not (it.get("label") or "").strip():
            raise ValueError(f"rail item {ikey!r} needs a label")
        if not str(it.get("href") or "").startswith("/"):
            raise ValueError(f"rail item {ikey!r} needs an absolute path")
        tone = str(it.get("tone") or "")
        if tone not in TONES:
            raise ValueError(f"rail item {ikey!r} has tone {tone!r}; expected one of {sorted(TONES)}")
        built.append(Item(key=ikey, label=str(it["label"]).strip(),
                          href=str(it["href"]), tone=tone))

    if home:
        other = next((s for s in _SECTIONS.values() if s.home and s.key != key), None)
        if other is not None:
            # THE BACK ARROW RESOLVES TO THE HOME SECTION, so two of them is not a preference
            # question — it is an ambiguous destination for every second-level screen in the box.
            raise ValueError(f"rail section {key!r} claims home, but {other.key!r} already does")

    _SECTIONS[key] = Section(key=key, order=order, machine=machine, title=title.strip(),
                             href=href, items=tuple(built), home=bool(home))
    log.info("shell.section_registered", key=key, machine=machine, items=len(built))


def sections() -> tuple[Section, ...]:
    """Every registered section, in rail order. Ties break on key so the rail cannot reshuffle
    itself between two renders of the same box — a menu whose items move is a menu nobody learns."""
    return tuple(sorted(_SECTIONS.values(), key=lambda s: (s.order, s.key)))


def home_href() -> str:
    """Where the back arrow goes. The registered home, else the first section, else the login —
    which is the only page every box is guaranteed to serve (`core.dash` says so about its own
    landing, and for the same reason: this must terminate, not loop)."""
    for s in sections():
        if s.home:
            return s.href
    got = sections()
    return got[0].href if got else "/dash/login"


def _within(path: str, href: str) -> bool:
    """Is `path` this section's own page or something underneath it?

    ANCHORED ON A SEPARATOR, never a bare prefix. `"/inbox".startswith("/in")` is true and means
    nothing; without the boundary a section at `/in` would claim `/inbox` and light up the wrong
    rail entry on every page of it. The same class of mistake as matching a URL without its
    quote — a prefix that is not a path prefix.
    """
    if not href:
        return False
    if path == href:
        return True
    stem = href.rstrip("/")
    return path.startswith(stem + "/") if stem else path.startswith("/")


def current(path: str) -> Section | None:
    """The section this path belongs to, or None when it belongs to none.

    THE LONGEST MATCH WINS, so a section nested under another (`/settings/keys` beneath
    `/settings`) is answered by the nearer of the two rather than by whichever registered first.
    """
    best = None
    for s in sections():
        if _within(path or "", s.href) and (best is None or len(s.href) > len(best.href)):
            best = s
    return best


def rail(path: str) -> Rail:
    """What the left rail shows for this path — the whole two-level decision, in one place.

    LEVEL 2 IS EARNED BY HAVING SOMEWHERE TO GO. A section with no items stays at level 1 even
    while you are standing on it: swapping the main nav for an empty column and a back arrow takes
    every choice away and offers nothing in exchange, which is what "the choices change to that sub
    menu" cannot mean when there is no sub menu.

    THE HOME SECTION NEVER GOES TO LEVEL 2 either, even if it registers items. Back-to-home from
    home is a control that cannot succeed, and it would be the first thing a buyer taps.
    """
    path = path or "/"
    here = current(path)
    if here is not None and here.items and not here.home:
        return Rail(level=2, title=here.title, back=home_href(), items=here.items, here=path)
    # LEVEL 1 — the sections themselves, rendered through the same `Item` the second level uses so
    # a template has one row to draw and not two.
    top = tuple(Item(key=s.key, label=s.title, href=s.href) for s in sections())
    return Rail(level=1, title="", back="", items=top, here=path)


def crumb(path: str) -> tuple[str, ...]:
    """The breadcrumb, as plain text: ("Settings", "Profile") — or () when there is nothing to say.

    ONLY AT LEVEL 2. "Dashboard" alone over the dashboard is the screen reading its own title back
    to the person standing on it, and the owner's reference shows the trail only once there is a
    trail: `Settings / Profile`.
    """
    got = rail(path)
    if got.level != 2:
        return ()
    for it in got.items:
        if it.href == path or _within(path, it.href):
            return (got.title, it.label)
    return (got.title,)


def is_current(item: Item, path: str) -> bool:
    """Is this the choice the person is looking at? Used to mark one row, and only one."""
    return _within(path or "", item.href)


def _reset_for_tests() -> None:
    """Empty the registry. Tests only — a suite that registers sections must not leak them into
    the next one, and importing a machine twice is otherwise indistinguishable from a real clash."""
    _SECTIONS.clear()
