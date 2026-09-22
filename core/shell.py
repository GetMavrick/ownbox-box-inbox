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


# WHERE A RAIL ROW MAY POINT. Two shapes and no third.
#
# A PATH ON THIS BOX is the normal answer and stays the default. The one exception the owner
# asked for (2026-09-22) is a row that leaves the box entirely — "Add a Machine" goes to the
# shop on ownbox.io, which is a website and not a screen this box serves. Before this, both
# validators below refused anything not starting with "/", so that row could not be registered
# at all; the rule was right and simply had no room for the case.
#
# HTTPS ONLY, AND THAT IS THE WHOLE ALLOW-LIST. A rail href becomes an `href` attribute on a
# page a paying customer is signed in to, so the shapes deliberately refused are the ones that
# would matter: `javascript:` (script execution from a menu), `http://` (a downgrade on a box
# that exists to be private), and `//host` (protocol-relative, which reads as a path and is not
# one). Anything else — `mailto:`, `data:`, a bare word — is refused by falling through.
def _href_problem(href: str) -> str:
    """"" if this href may be drawn in the rail, else why not, in words for the raising line."""
    h = str(href or "")
    if h.startswith("//"):
        # PROTOCOL-RELATIVE READS AS A PATH AND IS NOT ONE. `//evil.example` starts with "/" and
        # would have passed the old check, which is the one hole worth naming here.
        return f"is protocol-relative, which is not a path on this box: {h!r}"
    if h.startswith("/"):
        return ""
    if h.startswith("https://") and len(h) > len("https://"):
        return ""
    return f"must be a path on this box or an https:// address, got {h!r}"


def is_off_box(href: str) -> bool:
    """Whether following this row leaves the box — DERIVED, never declared.

    The `away` tone and its out-arrow exist already, and a row could simply be asked to carry
    both. It is not asked to: a link whose destination says it leaves and whose tone says it does
    not is a row that lies, and the two would drift the first time somebody edited one of them.
    The href is the fact; everything else is read off it.
    """
    return str(href or "").startswith("https://")


@dataclass(frozen=True)
class Item:
    """One choice inside a section — the second level."""
    key: str
    label: str
    href: str
    tone: str = ""
    icon: str = ""


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
    icon: str = ""


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
    # WHERE BACK GOES, IN WORDS. `title` is the section you are STANDING IN; this is the one you
    # ARRIVE AT, and they are not the same sentence. Rendered as "‹ Inbox" the arrow named the
    # room the person was already in while carrying them somewhere else — a control that describes
    # its origin. Owner, 2026-09-17, on the inbox: *"they can go back into the dashboard so there
    # should be a back arrow with a dashboard label."* Defaulted rather than per-section, because
    # the destination is the same for every section and a label that names it cannot be wrong.
    back_label: str = ""


_SECTIONS: dict[str, Section] = {}


def register_section(key: str, *, order: int, machine: str, title: str, href: str,
                     items: Iterable = (), home: bool = False, icon: str = "") -> None:
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
    _bad = _href_problem(href)
    if _bad:
        raise ValueError(f"rail section {key!r} href {_bad}")

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
        _ibad = _href_problem(it.get("href"))
        if _ibad:
            raise ValueError(f"rail item {ikey!r} href {_ibad}")
        tone = str(it.get("tone") or "")
        if tone not in TONES:
            raise ValueError(f"rail item {ikey!r} has tone {tone!r}; expected one of {sorted(TONES)}")
        # AN ICON IS SVG PATH DATA, the same shape the inbox's tab bar already stores — a `d`
        # string, never a URL and never a file. Core holds no image and fetches nothing; the
        # machine that owns the section owns how it looks, exactly as it owns its title.
        built.append(Item(key=ikey, label=str(it["label"]).strip(),
                          href=str(it["href"]), tone=tone, icon=str(it.get("icon") or "")))

    if home:
        other = next((s for s in _SECTIONS.values() if s.home and s.key != key), None)
        if other is not None:
            # THE BACK ARROW RESOLVES TO THE HOME SECTION, so two of them is not a preference
            # question — it is an ambiguous destination for every second-level screen in the box.
            raise ValueError(f"rail section {key!r} claims home, but {other.key!r} already does")

    _SECTIONS[key] = Section(key=key, order=order, machine=machine, title=title.strip(),
                             href=href, items=tuple(built), home=bool(home), icon=str(icon or ""))
    log.info("shell.section_registered", key=key, machine=machine, items=len(built))


def sections() -> tuple[Section, ...]:
    """Every registered section, in rail order. Ties break on key so the rail cannot reshuffle
    itself between two renders of the same box — a menu whose items move is a menu nobody learns."""
    return tuple(sorted(_SECTIONS.values(), key=lambda s: (s.order, s.key)))


# THE KEY A MACHINE USES TO SAY "MY SET-UP SCREEN IS HERE". One string, named once, because the
# machine that registers it and the core screens that look for it must not drift.
SETUP_KEY = "setup"


def setup_href() -> str:
    """Where a buyer with nothing connected should be sent, or "" when this box has nowhere.

    CORE KNOWS WHETHER SET-UP IS FINISHED AND NOT WHERE IT LIVES. `box_secrets.setup_state()` is
    core's and answers the first half on every box type. The screen itself belongs to whichever
    machine serves it — `/inbox/setup` on the box we sell today, something else on the next one —
    and core must not learn a machine's URL to find it (`tests/test_core_boundary.py` refuses
    exactly that, and rightly: core growing an arm per product is what the ratchet exists to stop).

    SO THE REGISTRY ANSWERS IT. A machine registers an item keyed `setup` in its own section and
    core asks the rail, the same way it already asks for the home href. A box whose machines
    register none gets "" and the screens that call this draw nothing — which is honest, and the
    correct behaviour on a Lead box that has no set-up screen at all.

    FIRST IN RAIL ORDER WINS, deliberately: with two machines carrying set-up screens the buyer
    should meet the one whose section the rail puts first, not whichever module imported first.
    """
    for sec in sections():
        for item in sec.items:
            if item.key == SETUP_KEY and item.href:
                return item.href
    return ""


def home_href() -> str:
    """Where the back arrow goes. The registered home, else the first section, else the login —
    which is the only page every box is guaranteed to serve (`core.dash` says so about its own
    landing, and for the same reason: this must terminate, not loop)."""
    for s in sections():
        if s.home:
            return s.href
    got = sections()
    return got[0].href if got else "/dash/login"


def home_title() -> str:
    """The word the back arrow uses for `home_href()`. Same fallback chain, same order — one
    resolution, so the label can never name a different place than the link goes to."""
    for s in sections():
        if s.home:
            return s.title
    got = sections()
    return got[0].title if got else "Home"


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
    if not stem:
        # ROOT IS NOT A CONTAINER HERE, and this is OSDev1's review catch on #1317. Every path is
        # "under" `/`, so treating it as one marks a section at root as current on every page of
        # the box — a rail where two rows light up at once, which tells the person the screen does
        # not know where they are either. Root matches root, and nothing else.
        return False
    return path.startswith(stem + "/")


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
        return Rail(level=2, title=here.title, back=home_href(), items=here.items, here=path,
                    back_label=home_title())
    # LEVEL 1 — the sections themselves, rendered through the same `Item` the second level uses so
    # a template has one row to draw and not two.
    top = tuple(Item(key=s.key, label=s.title, href=s.href, icon=s.icon) for s in sections())
    return Rail(level=1, title="", back="", items=top, here=path)


def current_item(path: str) -> Item | None:
    """Which ROW of the second level this path is standing on, or None.

    THE LONGEST MATCH WINS, and this function exists because that rule was written down twice and
    only one copy was right. `crumb()` had it (OSDev1's review catch on #1317); `is_current()` did
    not, and was a bare `_within` — so a section whose own index shares a prefix with its items lit
    TWO rows at once.

    NOT HYPOTHETICAL, AND NOT FOUND BY READING. It appeared the moment the inbox became the first
    machine to register a section: its index is `/inbox/` and Messages is `/inbox/inbox`, so on the
    Messages page `Today` matched as well and both rows drew `aria-current="page"`. Rendered, that
    is a menu telling the person it does not know where they are — the exact failure `_within`'s
    own docstring is about, one level further in.

    Every section with an index page has this shape, so the next one would have hit it too. One
    function now answers it, and `crumb()` and `is_current()` are both callers.

    IT ANSWERS AT BOTH LEVELS, over whatever rows the rail is currently showing. The first version
    returned None unless `level == 2` — copied from `crumb()`, where that guard is right because a
    one-word trail is not a trail. Here it meant the level-1 rail marked NOTHING, so the Dashboard
    row lost its highlight while you were standing on the dashboard. Caught by rendering the rail
    and reading the rows back, not by reading this diff, which looked correct.
    """
    got = rail(path)
    best = None
    for it in got.items:
        if _within(path, it.href) and (best is None or len(it.href) > len(best.href)):
            best = it
    return best


def crumb(path: str) -> tuple[str, ...]:
    """The breadcrumb, as plain text: ("Settings", "Profile") — or () when there is nothing to say.

    ONLY AT LEVEL 2. "Dashboard" alone over the dashboard is the screen reading its own title back
    to the person standing on it, and the owner's reference shows the trail only once there is a
    trail: `Settings / Profile`.
    """
    got = rail(path)
    if got.level != 2:
        return ()
    best = current_item(path)
    return (got.title, best.label) if best is not None else (got.title,)


def is_current(item: Item, path: str) -> bool:
    """Is this the choice the person is looking at? Used to mark one row, AND ONLY ONE.

    ASKS THE WHOLE RAIL, not just this item, because "am I the current row" is a question about
    SIBLINGS: `/inbox/inbox` is under `/inbox/` too, and an item cannot see that on its own. The
    signature is unchanged so every caller keeps working and none of them keeps the old answer.
    """
    got = current_item(path)
    return got is not None and got.key == item.key


def _reset_for_tests() -> None:
    """Empty the registry. Tests only — a suite that registers sections must not leak them into
    the next one, and importing a machine twice is otherwise indistinguishable from a real clash."""
    _SECTIONS.clear()
