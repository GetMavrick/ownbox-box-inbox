"""Built-in cards on Settings → Data Sources, from code that is not core.

A department foundation (core/foundations.py) can own a source the box reads on its own schedule. CORE NAMES NO
DEPARTMENT, not even here, so the foundation registers its card when it is imported, and the Data Sources page
draws what is registered. A box without that foundation has no card, rather than an empty one.

No Flask here: a foundation is imported in the worker as well as the web process.
"""
from __future__ import annotations

from typing import Callable

# key -> {"title", "render", "handle", "order", "owner"}
_CARDS: dict[str, dict] = {}


def register(key: str, *, title: str, render: Callable, handle: Callable, order: int = 50) -> None:
    """`render(note) -> html`: the card, with `note` ((ok, sentence) or None) saying what the last action did.
    `handle(do, form, by) -> (ok, sentence)`: one action, posted to Data Sources as card=<key>, do=<action>.

    REFUSED FOR A KEY ANOTHER MODULE ALREADY HOLDS, as core/onboarding.register_step is, so one department's card
    can never silently replace another's. The same module registering again (a reload) replaces its own."""
    owner = str(getattr(render, "__module__", "") or "")
    held = _CARDS.get(str(key))
    if held is not None and held["owner"] != owner:
        raise ValueError(f"Data Sources card {key!r} is already registered by {held['owner']!r}")
    _CARDS[str(key)] = {"title": str(title), "render": render, "handle": handle, "order": int(order),
                        "owner": owner}


def get(key: str) -> dict | None:
    return _CARDS.get(str(key or ""))


def cards() -> list[tuple[str, dict]]:
    return sorted(_CARDS.items(), key=lambda kv: (kv[1]["order"], kv[0]))
