"""The connector — one machine-readable surface on the box, behind per-seat identity.

Step 1 shut the /api/ door (core/dispatch.py `_auth_gate`). Step 2 was the lock: a table of seats,
a credential that is checked rather than shared, and a ledger of everything a seat did. Step 3 is
what is behind the door — a registry of named, typed functions, a manifest built from that same
registry, and the HTTP transport over it.

    seats.py     identity and audit
    tools.py     the registry: one definition, every transport reads it
    manifest.py  what THIS box can answer, derived rather than declared
    http.py      the HTTP transport (the MCP adapter is a second one, over the same functions)

Nothing in this package reasons. No brain.think, no vendor call, no spend — identity, registry and
dispatch are exactly the deterministic work CLAUDE.md section 11-6 says must never route through a
model.
"""
from . import manifest, seats, tools  # noqa: F401

__all__ = ["seats", "tools", "manifest"]
