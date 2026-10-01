"""What the owner connected, and which of each app's tools are on. One home: box_settings "connections".

    {"items": {slug: {"name", "host", "tools": [{"id", "name", "title", "description", "input_schema",
                                                 "read_only"}],
                      "enabled": [tool id, ...], "added_at", "added_by", "checked_at"}}}

THE ADDRESS AND THE TOKEN ARE NOT HERE. They live together in box_secrets as `app_conn_<slug>`, because some apps
put their secret in the address itself. Settings keep the host, for the screen.

ONLY READ-ONLY TOOLS TURN ON IN PHASE 1. The app says which of its tools only read (MCP's readOnlyHint); those
start on. Everything else stays off and can't be turned on yet: actions come in the next step, each behind a
person's yes. The hint is the app's own word about its own tools; the box adds no guess of its own.

Every change is checked against the app first: connecting lists its tools for real, so a wrong address or token
fails on the screen where it was typed, never later inside a shift.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from core import box_secrets, box_settings
from core.connections import client as _client
from core.logging import get_logger

log = get_logger(__name__)

NS, KEY = "connections", "apps"
SECRET = "app_conn_"
APPS_MAX = 20
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 '’-]{1,29}$")
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_.\-/]{1,128}$")
SCHEMA_MAX = 8_000             # one tool's argument schema; a bigger one is kept as "any object"
DESCRIPTION_MAX = 1_000
APP_MAX = 300_000              # one app's tools, as saved: the gateway reads them on every request
# A TOOL THAT CHANGES THINGS IS NEVER "ON" (docs/SCOPE_CONNECTIONS_MCP_FIRST.md, phase 1): a coworker may only ASK
# for it, and a person approves each one on Waiting for you (core/approvals.py).
ACTIONS_LATER = "That one changes things, so a coworker can only ask for it, and you approve each time."


class Refused(ValueError):
    """Shown to the owner, so it says what to do."""


class NeedsSignIn(Refused):
    """The app signs people in on its own page: the screen starts that sign-in (core/connections/oauth.py)."""

    def __init__(self, message: str, challenge: str = ""):
        super().__init__(message)
        self.challenge = challenge


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load() -> dict:
    got = box_settings.get(NS, KEY, default={}) or {}
    items = got.get("items") if isinstance(got, dict) else None
    return {"items": items if isinstance(items, dict) else {}}


def _save(data: dict, by: str) -> None:
    box_settings.put(NS, KEY, {"items": data["items"]}, set_by=by or "owner")


def get(slug: str) -> dict | None:
    rec = load()["items"].get(slug)
    return rec if isinstance(rec, dict) else None


def save_secret(slug: str, conn: dict, *, user_id: str | None = None) -> None:
    """The connection's address and credentials, as one compact row. A value with a space in it is not
    something any app sends; refused rather than stored half-right."""
    clean = {k: v for k, v in conn.items() if v not in (None, "")}
    if any(isinstance(v, str) and any(ch.isspace() for ch in v) for v in clean.values()):
        raise Refused("The app sent sign-in details the box can't store. Try again, or use a token.")
    box_secrets.put(SECRET + slug, json.dumps(clean, separators=(",", ":")), user_id=user_id)


def secret(slug: str) -> dict:
    """{"url", "token"} for this connection, plus the refresh details when it was signed in. {} when there is
    none. For the gateway; never shown."""
    try:
        got = json.loads(box_secrets.get(SECRET + slug) or "{}")
    except ValueError:
        return {}
    return got if isinstance(got, dict) and got.get("url") else {}


def slug_for(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:24].strip("_")
    return s if s[:1].isalpha() else "app_" + s


def _tool_id(name: str, taken: set) -> str:
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")[:48] or "tool"
    tid, n = base, 2
    while tid in taken:
        tid, n = f"{base}_{n}", n + 1
    taken.add(tid)
    return tid


def _clean_tools(raw: list) -> list[dict]:
    """The app's tools, as the box keeps them. A tool with a name we can't carry is left out."""
    out, taken = [], set()
    for t in raw:
        name = t.get("name")
        if not isinstance(name, str) or not _TOOL_NAME.match(name):
            continue
        notes = t.get("annotations") if isinstance(t.get("annotations"), dict) else {}
        schema = t.get("inputSchema") if isinstance(t.get("inputSchema"), dict) else {}
        if len(json.dumps(schema, default=str)) > SCHEMA_MAX:
            schema = {"type": "object"}
        title = t.get("title") or notes.get("title") or ""
        out.append({"id": _tool_id(name, taken), "name": name,
                    "title": str(title)[:80],
                    "description": str(t.get("description") or "")[:DESCRIPTION_MAX],
                    "input_schema": schema,
                    "read_only": notes.get("readOnlyHint") is True,
                    # SAID TO CHANGE THINGS, as against saying nothing. MCP reads an unmarked tool as one that
                    # may change things, and so does the box; the screen just says which it is.
                    "changes": notes.get("readOnlyHint") is False or notes.get("destructiveHint") is True})
    return out


def _fetch_tools(url: str, token: str) -> tuple[dict, list[dict]]:
    c = _client.Client(url, token)
    server = c.open()
    return server, _clean_tools(c.list_tools())


def _fits(found: list, url: str) -> None:
    if len(json.dumps(found, default=str)) > APP_MAX:
        raise Refused(f"{_client.host_of(url)} offers more tools than one connection can carry. If the app "
                      "lets you choose which tools its MCP server offers, choose fewer and connect again.")


def check_new(name: str) -> tuple[str, str]:
    """(the name, its slug) for a new connection, or Refused saying what to change."""
    name = " ".join(str(name or "").split())
    if not _NAME.match(name):
        raise Refused("Name the app in plain words, like Notion or Google Drive.")
    items = load()["items"]
    slug = slug_for(name)
    if slug in items:
        raise Refused(f"{items[slug].get('name') or name} is already connected. Remove it first, "
                      "or give this one another name.")
    if len(items) >= APPS_MAX:
        raise Refused(f"A box connects up to {APPS_MAX} apps. Remove one you don't use first.")
    return name, slug


def add(name: str, url: str, token: str = "", *, by: str, conn: dict | None = None) -> dict:
    """Connect an app. Lists its tools for real first; the read-only ones start on. Returns the saved record.
    `conn` is a signed-in connection's details (core/connections/oauth.py); otherwise `token` is pasted."""
    name, slug = check_new(name)
    if conn is None:
        token = str(token or "").strip()
        if any(ch.isspace() for ch in token):
            raise Refused("The token has a space or a line break in it. Paste it on its own.")
        conn = {"url": url, "token": token}
    try:
        url = conn["url"] = _client.check_address(conn.get("url"))
        server, found = _fetch_tools(url, conn.get("token") or "")
    except _client.SignInNeeded as e:
        raise NeedsSignIn(str(e), e.challenge) from None
    except _client.ConnectionFailed as e:
        raise Refused(str(e)) from None
    if not found:
        raise Refused(f"{_client.host_of(url)} answered, but has no tools to offer.")
    _fits(found, url)
    data = load()
    save_secret(slug, conn, user_id=by or None)
    now = _now()
    rec = {"name": name, "host": _client.host_of(url),
           "server": str(server.get("name") or "")[:80],
           "tools": found, "enabled": [t["id"] for t in found if t["read_only"]],
           "added_at": now, "added_by": by or "owner", "checked_at": now}
    data["items"][slug] = rec
    try:
        _save(data, by)
    except Exception:
        box_secrets.clear(SECRET + slug, user_id=by or None)
        raise
    log.info("connections.added", app=slug, host=rec["host"], tools=len(found), enabled=len(rec["enabled"]))
    return dict(rec, slug=slug)


def remove(slug: str, *, by: str) -> bool:
    """Disconnect an app: its tools leave the box on the next request, and its token is forgotten."""
    data = load()
    if slug not in data["items"]:
        return False
    data["items"].pop(slug)
    _save(data, by)
    box_secrets.clear(SECRET + slug, user_id=by or None)
    log.info("connections.removed", app=slug, by=by)
    return True


def set_enabled(slug: str, tool_ids, *, by: str, asks=None) -> dict:
    """Choose which of an app's read-only tools are on (`tool_ids`) and, when `asks` is given, which of the tools
    that change things a coworker may ASK for. Anything else is refused, never silently dropped."""
    data = load()
    rec = data["items"].get(slug)
    if not isinstance(rec, dict):
        raise Refused("That app isn't connected.")
    tools = {t["id"]: t for t in rec.get("tools") or []}
    want = [str(t) for t in (tool_ids or [])]
    ask = [str(t) for t in (asks or [])]
    unknown = [t for t in want + ask if t not in tools]
    if unknown:
        raise Refused(f"{rec['name']} has no tool called {unknown[0]}.")
    if any(not tools[t]["read_only"] for t in want):
        raise Refused(ACTIONS_LATER)
    if any(tools[t]["read_only"] for t in ask):
        raise Refused("That one only reads, so it's simply on or off.")
    rec["enabled"] = sorted(set(want), key=list(tools).index)
    if asks is not None:
        rec["ask_first"] = sorted(set(ask), key=list(tools).index)
    _save(data, by)
    log.info("connections.enabled", app=slug, enabled=len(rec["enabled"]), asks=len(rec.get("ask_first") or []),
             by=by)
    return dict(rec, slug=slug)


def check(slug: str, *, by: str) -> dict:
    """Ask the app for its tools again. A tool the app no longer offers, or no longer calls read-only, goes off;
    a new read-only tool stays off until the owner turns it on."""
    data = load()
    rec = data["items"].get(slug)
    conn = secret(slug)
    if not isinstance(rec, dict) or not conn:
        raise Refused("That app isn't connected.")
    from core.connections import oauth
    try:
        server, found = _fetch_tools(conn["url"], oauth.token_for(slug, conn))
    except _client.SignInNeeded:
        raise Refused(f"{rec['name']} wants you to sign in again. Disconnect it, then connect it again.") from None
    except _client.ConnectionFailed as e:
        raise Refused(str(e)) from None
    _fits(found, conn["url"])
    was = {t["name"]: t["id"] for t in rec.get("tools") or [] if t["id"] in set(rec.get("enabled") or [])}
    asked = {t["name"] for t in rec.get("tools") or [] if t["id"] in set(rec.get("ask_first") or [])}
    rec["tools"] = found
    rec["enabled"] = [t["id"] for t in found if t["read_only"] and t["name"] in was]
    rec["ask_first"] = [t["id"] for t in found if not t["read_only"] and t["name"] in asked]
    rec["checked_at"] = _now()
    _save(data, by)
    return dict(rec, slug=slug)
