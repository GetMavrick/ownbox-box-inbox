"""This box's MCP as the one gateway to the apps the owner connected.

`refresh()` makes the tool registry match the saved connections: each app's tools that are ON (read-only, by the
app's own word) are registered as `app_<slug>.<tool>` under the `read:apps` capability, titled in plain words
("Search pages in Notion"). core/connector/mcp.py calls it before every tools/list and tools/call. When nothing
changed it is one settings read, so a connection made on Data sources is live on the next request, no restart.

A CALL goes to the app in a fresh session with the token from box_secrets, and the app's answer comes back as
data: its text, capped, and whether the app said it failed (`app_error`), so a coworker reads the app's own words
and can correct itself. Images and files the app returns are named, not carried, in phase 1.

Every call goes through tools.call(), so the seat's capability is checked and the call is on the run's receipt
exactly as for the box's own tools. Nothing here can widen what a seat may do.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading

from core.connections import client as _client
from core.connections import store
from core.connector import tools
from core.logging import get_logger

log = get_logger(__name__)

CAPABILITY = "read:apps"
# A TOOL THAT CHANGES THINGS IS OFFERED ONLY AS A PROPOSAL (core/approvals.py): calling it asks the owner, and a
# person's yes runs it, once. `write:proposals` is the capability for exactly that (core/connector/tools.py), and
# `act` is the least role, as for the inbox's draft_reply: a read-only key never gains a way to ask.
ASK = "write:proposals"
ASKS = " (asks you first)"
KIND = "app_action"
PREFIX = "app_"
TEXT_MAX = 50_000
_TITLE_BAD = re.compile(r"[^A-Za-z0-9 ,'’()-]+")

from core import approvals as _approvals  # noqa: E402 — the kind is registered wherever this module is

_lock = threading.Lock()
_seen = ""                    # a fingerprint of the connections the registry matches
_mine: set = set()            # the machines this module registered
_skipped: dict = {}           # {slug: {tool id: why}}: the app tools left off the list, for its Data Sources row

# ONE APP TOOL MUST NEVER BREAK THE OWNER'S WHOLE LIST (the connector strike, 2026-10-02). An app's tools go into
# this box's tools/list beside its own, and a client that can't take one tool refuses the whole reload: the owner
# saw "Couldn't reload tools from the server". So every app tool is checked before it is listed, and one that
# fails is left off, logged, and named on its row on Data sources with why.
NAME_MAX = 64                 # the strictest length a client allows a tool's name


def unlistable(slug: str, t: dict) -> str:
    """Why this app tool can't be offered to an AI, in words for the owner, or "" when it can."""
    if len(f"{PREFIX}{slug}.{t.get('id') or ''}") > NAME_MAX:
        return "its name is too long for an AI to use"
    schema = t.get("input_schema") or {}
    if not isinstance(schema, dict):
        return "the app describes its inputs in a shape an AI can't read"
    if schema.get("type") not in (None, "object"):
        return "its inputs aren't a set of named fields, which is what an AI fills in"
    if "properties" in schema and not isinstance(schema["properties"], dict):
        return "the app describes its inputs in a shape an AI can't read"
    req = schema.get("required")
    if req is not None and not (isinstance(req, list) and all(isinstance(x, str) for x in req)):
        return "the app describes its inputs in a shape an AI can't read"
    return ""


def skipped(slug: str) -> dict:
    """{tool id: why} for the app tools left off the list the last time it was built."""
    return dict(_skipped.get(slug) or {})


def _fingerprint(items: dict) -> str:
    return hashlib.sha256(json.dumps(items, sort_keys=True, default=str).encode()).hexdigest()


def _words(s: str) -> str:
    return " ".join(_TITLE_BAD.sub(" ", s.replace("_", " ").replace(".", " ").replace("/", " ")).split())


def title_for(tool: dict, app: str) -> str:
    """"Search pages in Notion": the app's own title (or its tool name, in words) and the app's name."""
    base = _words(tool.get("title") or "") or _words(tool["name"])
    app = _words(app)
    text = base if app.lower() in base.lower() else f"{base} in {app}"
    if len(text) > 60:
        text = (base[:60 - len(app) - 4].rstrip() + f" in {app}") if app.lower() not in base.lower() else base[:60]
    text = text[:1].upper() + text[1:]
    if not text[:1].isalpha():
        text = f"Use {text}"[:60]
    return text.rstrip()


def ask_title(tool: dict, app: str) -> str:
    """"Create a page in Notion (asks you first)": the plain title, cut to leave room for the promise."""
    base, room = title_for(tool, app), 60 - len(ASKS)
    if len(base) > room:
        base = base[:room].rsplit(" ", 1)[0].rstrip(" ,-(") or base[:room]
    return base + ASKS


def _unique_title(text: str, taken: set) -> str:
    if text not in taken:
        return text
    for n in range(2, 100):
        candidate = f"{text[:54]} ({n})"
        if candidate not in taken:
            return candidate
    return text


def refresh() -> bool:
    """Make the registry match the saved connections. True when it changed anything. Never raises."""
    global _seen
    try:
        items = store.load()["items"]
    except Exception:                                   # noqa: BLE001 — an unreadable store serves none
        log.exception("connections.load_failed")
        items = {}
    fp = _fingerprint(items)
    with _lock:
        if fp == _seen:
            return False
        # BUILD THE NEW SET, THEN SWAP IT IN (OSDev4, review of #1751): unregistering first left a moment in
        # which a call on another thread found no tool at all.
        replacing = frozenset(set(_mine) | {PREFIX + slug for slug in items})
        specs, taken = [], tools.titles(excluding=replacing)
        _skipped.clear()
        for slug, rec in sorted(items.items()):
            if isinstance(rec, dict):
                specs += _specs(slug, rec, replacing, taken)
        tools.swap(replacing, specs)
        _mine.clear()
        _mine.update(spec["machine"] for spec in specs)
        _seen = fp
    return True


def _specs(slug: str, rec: dict, replacing: frozenset, taken: set) -> list:
    machine, on, out = PREFIX + slug, set(rec.get("enabled") or []), []
    for t in rec.get("tools") or []:
        if t.get("id") not in on or t.get("read_only") is not True:
            continue
        if _left_off(slug, t):
            continue
        try:
            spec = tools.make_spec(t["id"], fn=_forwarder(slug, t["name"]), machine=machine,
                                   capability=CAPABILITY, min_role="read",
                                   title=_unique_title(title_for(t, rec.get("name") or slug), taken),
                                   description=_description(t, rec), input_schema=t.get("input_schema") or {},
                                   replacing=replacing)
        except Exception as e:                          # noqa: BLE001 — one odd tool never hides the rest
            log.warning("connections.tool_skipped", app=slug, tool=str(t.get("id"))[:60],
                        error=f"{type(e).__name__}: {str(e)[:160]}")
            continue
        taken.add(spec["title"])
        out.append(spec)
    asks = set(rec.get("ask_first") or [])
    for t in rec.get("tools") or []:
        if t.get("id") not in asks or t.get("read_only") is True:
            continue
        if _left_off(slug, t):
            continue
        try:
            spec = tools.make_spec(t["id"], fn=_proposer(slug, t, rec), machine=machine, capability=ASK,
                                   min_role="act", wants_seat=True,
                                   title=_unique_title(ask_title(t, rec.get("name") or slug), taken),
                                   description=_ask_description(t, rec),
                                   input_schema=t.get("input_schema") or {}, replacing=replacing)
        except Exception as e:                          # noqa: BLE001 — one odd tool never hides the rest
            log.warning("connections.tool_skipped", app=slug, tool=str(t.get("id"))[:60],
                        error=f"{type(e).__name__}: {str(e)[:160]}")
            continue
        taken.add(spec["title"])
        out.append(spec)
    return out


def _left_off(slug: str, t: dict) -> bool:
    why = unlistable(slug, t)
    if why:
        _skipped.setdefault(slug, {})[str(t.get("id"))] = why
        log.warning("connections.tool_left_off", app=slug, tool=str(t.get("id"))[:60], why=why)
    return bool(why)


def _ask_description(t: dict, rec: dict) -> str:
    app = rec.get("name") or "a connected app"
    said = (t.get("description") or "").strip()
    whose = (f"From {app}, an app connected to this box. This changes something in {app}, so calling it doesn't "
             "do it: it asks the box's owner, who approves or declines. Ask once; the answer is the owner's.")
    return f"{said}\n\n{whose}" if said else whose


def _proposer(slug: str, tool: dict, rec: dict):
    app, title = rec.get("name") or slug, title_for(tool, rec.get("name") or slug)

    def fn(seat=None, **arguments):
        from core import approvals
        a = approvals.propose(KIND, machine=PREFIX + slug, title=title,
                              detail={"app": app, "slug": slug, "tool": tool["name"], "arguments": arguments},
                              seat_id=str((seat or {}).get("label") or (seat or {}).get("id") or ""))
        return {"app": app, "app_error": False, "asked": True, "approval": a["id"],
                "text": (f"Asked: \"{title}\" is waiting for the owner, who approves or declines. Nothing in "
                         f"{app} has changed yet. Don't ask again for the same thing.")}
    fn.__name__ = f"ask_{slug}"
    return fn


def _run_action(detail: dict) -> dict:
    """An approved proposal, carried out: the one place an app's changing tool is ever called."""
    res = call(str(detail.get("slug") or ""), str(detail.get("tool") or ""), dict(detail.get("arguments") or {}))
    if isinstance(res, tools.NotConfigured):
        return {"ok": False, "text": "The app was disconnected on Data Sources, so nothing was done."}
    return {"ok": res.get("app_error") is not True, "text": str(res.get("text") or "")}


def _description(t: dict, rec: dict) -> str:
    said = (t.get("description") or "").strip()
    whose = f"From {rec.get('name') or 'a connected app'}, an app connected to this box. Read only."
    return f"{said}\n\n{whose}" if said else whose


def _forwarder(slug: str, tool_name: str):
    def fn(**arguments):
        return call(slug, tool_name, arguments)
    fn.__name__ = f"app_{slug}"
    return fn


def call(slug: str, tool_name: str, arguments: dict) -> dict:
    """Ask the app, and hand back its answer as data. Never raises for the app's failure: it is the answer."""
    rec, conn = store.get(slug), store.secret(slug)
    if not rec or not conn:
        return tools.NotConfigured("this app was disconnected on Data sources")
    from core.connections import oauth
    app = rec.get("name") or slug

    def ask(token: str) -> dict:
        c = _client.Client(conn["url"], token)
        c.open()
        return c.call_tool(tool_name, arguments)

    try:
        try:
            result = ask(oauth.token_for(slug, conn))
        except (_client.TokenRefused, _client.SignInNeeded):
            if not conn.get("refresh"):
                raise _client.ConnectionFailed(f"{app} refused the box's token. Connect it again on Data Sources "
                                               "with a current one.") from None
            # A SIGNED-IN TOKEN THE APP NO LONGER TAKES: refresh once and ask again.
            try:
                result = ask(oauth.token_for(slug, conn, force=True))
            except (_client.TokenRefused, _client.SignInNeeded, oauth.SignInFailed):
                raise _client.ConnectionFailed(f"{app} signed the box out. Disconnect it on Data Sources, then "
                                               "connect it again.") from None
    except _client.ConnectionFailed as e:
        log.warning("connections.call_failed", app=slug, tool=tool_name[:60], error=str(e)[:200])
        return {"app": app, "app_error": True, "text": str(e)}
    return as_data(app, result)


def as_data(app: str, result: dict) -> dict:
    """An app's MCP result as plain data: its text (capped), whether it failed, and what was left out."""
    parts, left = [], []
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "text":
            parts.append(str(item.get("text") or ""))
        elif kind == "resource" and isinstance(item.get("resource"), dict) and "text" in item["resource"]:
            parts.append(str(item["resource"].get("text") or ""))
        elif kind == "resource_link":
            parts.append(f"[{item.get('name') or 'link'}: {item.get('uri') or ''}]")
        else:
            left.append(str(kind or "item"))
    text = "\n\n".join(p for p in parts if p)
    if not text and isinstance(result.get("structuredContent"), dict):
        text = json.dumps(result["structuredContent"], default=str)
    if len(text) > TEXT_MAX:
        text = text[:TEXT_MAX] + f"\n\n[cut at {TEXT_MAX:,} characters]"
    out = {"app": app, "app_error": result.get("isError") is True, "text": text}
    if left:
        out["not_shown"] = f"{len(left)} {'item' if len(left) == 1 else 'items'} the box can't show yet " \
                           f"({', '.join(sorted(set(left)))})"
    return out


_approvals.register_kind(KIND, run=_run_action)
