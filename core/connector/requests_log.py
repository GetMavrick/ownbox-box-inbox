"""The connector's last few requests, for /health and core.health (OSDev1 ASSIGNED 2026-10-07, owner-approved).

The owner saw "Couldn't reload tools" on his own box while every replay of the connector came back clean. The likely
cause was an update restarting the web service (scripts/box_update.sh), but nothing on the box could say what his AI
had actually asked for, or what it got back. This is that record: the newest KEEP requests to /mcp, each one line of
when, which JSON-RPC method, which client (the product name its User-Agent gives), and how it went ("ok", "error
-32602", "http 401"). Never a parameter, an argument, a seat or a credential, so it can stand on the public /health.

One row per request, pruned on every write. Never raises: a request is never the worse for being logged.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from core import state
from core.logging import get_logger

log = get_logger(__name__)

KEEP = 50                    # rows kept; /health and core.health show the newest SHOWN
SHOWN = 10
_SAFE = re.compile(r"[^A-Za-z0-9._/\-]")
_SAFE_OUTCOME = re.compile(r"[^A-Za-z0-9 \-]")


def _clean(v, cap: int = 40, pattern=_SAFE) -> str:
    return pattern.sub("", str(v or ""))[:cap].strip()


def client_of(user_agent: str | None, client_info: dict | None = None) -> str:
    """The client's own name: an `initialize`'s clientInfo.name when it gave one, else its User-Agent's first product
    token ("claude-ai/1.0 (...)" -> "claude-ai"). "unknown" when it gives neither."""
    name = _clean((client_info or {}).get("name")) if isinstance(client_info, dict) else ""
    if not name:
        first = str(user_agent or "").strip().split(" ", 1)[0]
        name = _clean(first.split("/", 1)[0])
    return name or "unknown"


def record(*, method: str, client: str, outcome: str) -> None:
    try:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with state.connect() as c:
            cur = c.execute("INSERT INTO connector_requests (at, method, client, outcome) VALUES (?,?,?,?)",
                            (now, _clean(method) or "?", _clean(client) or "unknown", _clean(outcome, 30, _SAFE_OUTCOME) or "?"))
            c.execute("DELETE FROM connector_requests WHERE id <= ?", (int(cur.lastrowid or 0) - KEEP,))
    except Exception as e:                       # noqa: BLE001 — bookkeeping never costs a request
        log.warning("connector.request_unlogged", error=type(e).__name__)


def recent(n: int = SHOWN) -> list[dict]:
    """The newest `n` requests, newest first: [{"at", "method", "client", "outcome"}]. [] when unreadable."""
    try:
        with state.connect() as c:
            rows = c.execute("SELECT at, method, client, outcome FROM connector_requests ORDER BY id DESC LIMIT ?",
                             (int(n),)).fetchall()
        return [dict(r) for r in rows]
    except Exception:                            # noqa: BLE001
        return []
