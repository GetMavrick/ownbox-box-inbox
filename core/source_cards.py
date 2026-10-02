"""Built-in cards on Settings → Data Sources, from code that is not core.

A department foundation (core/foundations.py) can own a source the box reads on its own schedule. CORE NAMES NO
DEPARTMENT, not even here, so the foundation registers its card when it is imported, and the Data Sources page
draws what is registered. A box without that foundation has no card, rather than an empty one.

No Flask here: a foundation is imported in the worker as well as the web process.
"""
from __future__ import annotations

from typing import Callable

# key -> {"title", "render", "handle", "summary", "idea", "category", "order", "owner"}
_CARDS: dict[str, dict] = {}


def register(key: str, *, title: str, render: Callable, handle: Callable, order: int = 50,
             summary: Callable | None = None, idea: str = "", category: str = "") -> None:
    """`render(note) -> html`: the card, with `note` ((ok, sentence) or None) saying what the last action did.
    `handle(do, form, by) -> (ok, sentence)`: one action, posted to Data Sources as card=<key>, do=<action>.
    `summary() -> {"what", "when", "status", "connected"}`: the card's one row in the Data Sources table (owner,
    2026-10-02: one table, a row per source, each opening its own page, so the page never grows a card per
    source). Words only; the row links to the card's own page, /settings/sources/<key>.
    `idea`, `category`: until it is connected the card is not a row but an idea, with this sentence of what it does
    for the business (owner, 2026-10-02: "name of the app and then a sentence on what you can do with it and what
    business use case and outcome"); suggested, never assumed.

    REFUSED FOR A KEY ANOTHER MODULE ALREADY HOLDS, as core/onboarding.register_step is, so one department's card
    can never silently replace another's. The same module registering again (a reload) replaces its own."""
    owner = str(getattr(render, "__module__", "") or "")
    held = _CARDS.get(str(key))
    if held is not None and held["owner"] != owner:
        raise ValueError(f"Data Sources card {key!r} is already registered by {held['owner']!r}")
    _CARDS[str(key)] = {"title": str(title), "render": render, "handle": handle, "order": int(order),
                        "owner": owner, "summary": summary, "idea": str(idea or ""),
                        "category": str(category or "")}


def summary(key: str) -> dict:
    """A card's row, never raising: {"what", "when", "status", "connected"}, empty strings when it says nothing."""
    spec = _CARDS.get(str(key or ""))
    got: dict = {}
    if spec and spec.get("summary"):
        try:
            got = dict(spec["summary"]() or {})
        except Exception:                                # noqa: BLE001 — a row is never worth a broken page
            # NOT KNOWN IS NOT "NOT CONNECTED": a source whose summary can't be read stays a row, saying so, rather
            # than dropping into the ideas as if it had never been set up.
            return {"what": "", "when": "", "status": "Could not be read just now", "connected": None}
    return {"what": str(got.get("what") or ""), "when": str(got.get("when") or ""),
            "status": str(got.get("status") or ""), "connected": bool(got.get("connected"))}


def get(key: str) -> dict | None:
    return _CARDS.get(str(key or ""))


def cards() -> list[tuple[str, dict]]:
    return sorted(_CARDS.items(), key=lambda kv: (kv[1]["order"], kv[0]))
