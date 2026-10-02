"""Ready-made asks: the MCP `prompts` a person picks from their AI's menu for this box.

OWNER, 2026-10-02, ruling D3 of docs/SCOPE_SMART_BOX_ANSWERS.md: five ready-made asks, named *Morning brief*,
*What needs me today*, *Who should I follow up with*, *How are my websites doing* and *What should we write next*.
A buyer who connects their AI should not have to know what to ask a box on day one; the menu tells them, in their
own words, and each ask tells the AI which of this box's tools answer it and how to answer (plain words, what it
means, what to ask next, what the box can start).

A REGISTRY, THE SAME SHAPE AS THE TOOLS (core/connector/tools.py). A machine registers the asks it can answer
beside its tools, and adds its tools to an ask registered elsewhere with `use()`, so core names no machine and a
box offers exactly the asks its machines can answer. Nothing here is a list of products to keep in step.

AN ASK IS LISTED ONLY WHEN THIS CONNECTION CAN ANSWER IT: at least one tool it relies on is one the seat can use
(`tools.visible_to`). The same rule as tools/list, for the same reason: a menu item that ends in "I can't do that
here" teaches a buyer the box is broken.

THE BOX'S OWN AI COMES FIRST WHEN IT IS HERE. An ask may name tools to `prefer` (the box's own brief, or its own
AI answering the question with its reasons). They are looked up at request time, so the day those tools register,
every ask starts using them with no edit here.

NOTHING HERE REASONS. An ask is text assembled from the registry for this seat.
"""
from __future__ import annotations

import re

from core.connector import tools
from core.logging import get_logger

log = get_logger(__name__)

_NAME = re.compile(r"^[a-z][a-z0-9_]{2,39}$")

# HOW EVERY ANSWER READS, said once for every ask (the scope's §2: answers, says what it means, offers what to ask
# next, offers what it can start). The tools' own text is already written this way; this tells the AI to keep it.
HOW = ("Answer me in plain words, the way a sharp assistant briefs a busy business owner. Lead with what matters "
       "most and say why it matters, using the real numbers from this box, and give the link to the page each one "
       "comes from. Never show me raw fields, ids or JSON. End with \"Ask next:\" and two or three questions this "
       "box can answer, then \"I can start:\" with what the box can do for me to approve on Approvals, only where "
       "one of its tools can really do it. Nothing is sent, published or spent until I approve it.")

_ASKS: dict = {}
_USES: dict = {}          # name -> [(tool, why)], added by machines; kept apart so the order of imports never matters


def register(name: str, *, title: str, description: str, ask: str, uses=(), prefer=(), machine: str) -> None:
    """One ready-made ask. `uses` is [(tool, what it gives this ask)]; `prefer` names tools that answer the
    whole ask when the box has them. Refused at import, where somebody is watching, like a tool."""
    if not _NAME.match(str(name or "")):
        raise ValueError(f"prompt {name!r}: a name is a short lowercase slug, like 'morning_brief'")
    if not tools.plain_title(title):
        raise ValueError(f"prompt {name!r}: title must be plain words a person reads, got {title!r}")
    if name in _ASKS and _ASKS[name]["machine"] != machine:
        raise ValueError(f"prompt {name!r} is already registered by {_ASKS[name]['machine']!r}")
    if not str(ask or "").strip():
        raise ValueError(f"prompt {name!r} needs the ask itself")
    _ASKS[name] = {"name": name, "title": title, "description": description, "ask": str(ask).strip(),
                   "uses": [(str(t), str(w)) for t, w in uses], "prefer": [str(t) for t in prefer],
                   "machine": machine}
    log.info("connector.prompt_registered", prompt=name, machine=machine)


def use(name: str, tool: str, why: str) -> None:
    """A machine adds one of its tools to an ask registered elsewhere (the morning brief reads the inbox too)."""
    rows = _USES.setdefault(str(name), [])
    if (str(tool), str(why)) not in rows:
        rows.append((str(tool), str(why)))


def _visible(seat: dict) -> set:
    return {s["name"] for s in tools.visible_to(seat)}


def _parts(spec: dict, seat: dict) -> tuple[list, list]:
    """(the tools this seat can use for the ask, the preferred ones it can use), each [(tool, why)]."""
    mine = _visible(seat)
    uses = [(t, w) for t, w in spec["uses"] + _USES.get(spec["name"], []) if t in mine]
    prefer = []
    for t in spec["prefer"]:
        if t in mine:
            prefer.append((t, (tools.registry().get(t) or {}).get("title") or ""))
    return uses, prefer


def listed(seat: dict) -> list:
    """The asks this seat can answer, as MCP prompt entries, sorted by name (a client caches the list)."""
    out = []
    for name in sorted(_ASKS):
        spec = _ASKS[name]
        uses, prefer = _parts(spec, seat)
        # LISTED BY ITS DETAIL TOOLS, never by core.ask alone: the box's AI answers with the asker's own reach, so a
        # seat that can't read the inbox can't be offered "who should I follow up with" because it can reach core.ask.
        if uses:
            out.append({"name": name, "title": spec["title"], "description": spec["description"],
                        "arguments": []})
    return out


def message(name: str, seat: dict) -> dict | None:
    """The ask as one user message, for this seat, or None when the seat can't use it (or it isn't here)."""
    spec = _ASKS.get(str(name or ""))
    if spec is None:
        return None
    uses, prefer = _parts(spec, seat)
    if not uses:                                    # the same rule as listed()
        return None
    lines = [spec["ask"], ""]
    if prefer:
        first, title = prefer[0]
        lines.append(f"Start with the box's own answer: {first}" + (f" ({title})" if title else "")
                     + ". Pass my question to it as I asked it. It reads this box and answers with its reasons; "
                       "use the tools below only for detail it leaves out.")
        lines.append("")
    if uses:
        lines.append("Use these tools on this box:")
        lines += [f"- {t}: {w}" for t, w in uses]
        lines.append("")
    lines.append(HOW)
    return {"description": spec["description"],
            "messages": [{"role": "user", "content": {"type": "text", "text": "\n".join(lines).strip()}}]}


def names() -> list:
    return sorted(_ASKS)


def _reset_for_tests() -> None:
    _ASKS.clear()
    _USES.clear()
