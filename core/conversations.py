"""Conversations: how a machine of the owner's own works with the box's inbox (`m.claim`, `m.send_dm`, SDK 1).

docs/PLAN_LEAD_MAGNET_MACHINE.md step 2, approved by the owner 2026-10-01: an automation's messages go through
the inbox's own send path, and it CLAIMS the conversations it runs so the inbox doesn't draft them (decision
2) and hands back what it can't answer (decision 3).

CORE NAMES NO MACHINE. The inbox is an add-on machine, and core may not import it (tests/test_core_boundary.py).
So this is a seam with one PROVIDER: the machine that holds the box's conversations registers itself here at
import (`provide`), and the SDK asks this module, never the inbox. A box with no inbox has no provider, and
every call says so in a sentence a builder can act on.

WHAT A PROVIDER ANSWERS (all keyword arguments; `machine` is the caller's key, `title` its display name):
  claim(machine=, title=, conversation=, days=, trigger=)
                                                  -> bool    hold it; the inbox stops drafting it. Never back
                                                             after a person took it over, unless the owner hands
                                                             it back or `trigger` is a comment the box itself read,
                                                             from that person, newer than the takeover
  release(machine=, conversation=, note=)         -> bool    hand it back, with what the person should know
  holder(conversation=)                           -> dict | None
  messages(conversation=, since=, limit=)         -> list[{"id", "direction", "sent_by", "body", "at"}]
  send(machine=, conversation=, text=, key=, buttons=, quick_replies=)
                                                  -> {"status": "sent" | "duplicate" | "refused" | "unknown",
                                                      "message_id", "reason", "code"}: `code` is why, for a
                                                      program (see sdk.Machine.send_dm)
  comments(post=, since=, posts=, per_post=)      -> list[{"id", "post", "account", "space", "text",
                                                           "author": {"id", "username", "name"}, "at"}]
  reply_to_comment(machine=, comment=, text=, key=, quick_replies=)
                                                  -> the same answer as send: the private reply that opens a
                                                     conversation with someone who commented
  conversation_for(comment=)                      -> the commenter's DM conversation id, or None (not yet)
  follows_you(conversation=)                      -> True | False | None (the platform didn't say)
  opted_out(conversation=)                        -> bool    this person said STOP on this box; never contact
                                                             them, by any channel (OSDev1's review of #1810)
The three reads (comments, conversation_for, follows_you) NEVER RAISE: a Space or a read that fails is skipped
or answers None, logged with its reason, so one bad key never breaks every Space (OSDev1's review of #1789).
"""
from __future__ import annotations

from core.logging import get_logger

log = get_logger(__name__)

_PROVIDER = None
_NEEDED = ("claim", "release", "holder", "messages", "send", "opted_out", "comments", "reply_to_comment",
           "conversation_for", "follows_you")


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
