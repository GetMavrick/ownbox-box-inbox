"""Conversations: how a machine of the owner's own works with the box's inbox (`m.claim`, `m.send_dm`, SDK 1).

docs/PLAN_LEAD_MAGNET_MACHINE.md step 2, approved by the owner 2026-10-01: an automation's messages go through
the inbox's own send path, and it CLAIMS the conversations it runs so the inbox doesn't draft them (decision
2) and hands back what it can't answer (decision 3).

CORE NAMES NO MACHINE. The inbox is an add-on machine, and core may not import it (tests/test_core_boundary.py).
So this is a seam with one PROVIDER: the machine that holds the box's conversations registers itself here at
import (`provide`), and the SDK asks this module, never the inbox. A box with no inbox has no provider, and
every call says so in a sentence a builder can act on.

WHAT A PROVIDER ANSWERS (all keyword arguments; `machine` is the caller's key, `title` its display name):
  claim(machine=, title=, conversation=, days=)   -> bool    hold it; the inbox stops drafting it
  release(machine=, conversation=, note=)         -> bool    hand it back, with what the person should know
  holder(conversation=)                           -> dict | None
  messages(conversation=, since=, limit=)         -> list[{"id", "direction", "sent_by", "body", "at"}]
  send(machine=, conversation=, text=, key=)      -> {"status": "sent" | "duplicate" | "refused" | "unknown",
                                                      "message_id", "reason"}
"""
from __future__ import annotations

from core.logging import get_logger

log = get_logger(__name__)

_PROVIDER = None
_NEEDED = ("claim", "release", "holder", "messages", "send")


class NoProvider(RuntimeError):
    """This box has no inbox to hold conversations."""


def provide(provider) -> None:
    """Register the box's conversation provider. The last one registered wins (a re-import)."""
    missing = [n for n in _NEEDED if not callable(getattr(provider, n, None))]
    if missing:
        raise ValueError(f"a conversation provider must offer {', '.join(missing)}")
    global _PROVIDER
    _PROVIDER = provider
    log.info("conversations.provided", provider=getattr(provider, "__name__", type(provider).__name__))


def provider():
    """The provider, or NoProvider naming what to do about it."""
    if _PROVIDER is None:
        raise NoProvider("this box has no inbox to hold conversations: install the Unified Inbox first, and "
                      "say so in your machine.yaml under needs:")
    return _PROVIDER
