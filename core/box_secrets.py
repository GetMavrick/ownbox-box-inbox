"""Secrets the BUYER sets from a screen, because they have no other way to set them.

THE ONE THAT MATTERS TODAY is the AI key. A delivered box ships `brain.backend: api` — bring
your own — with no key from install.sh and none from the provisioner, so `drafter/draft.py`
returns {"skipped": "unconfigured"} and the box files messages and drafts nothing. The buyer has
no shell and no `.env`; a screen is the only surface they have.

WHY A TABLE AND NOT THE ENVIRONMENT. `core/config.Settings` reads `os.environ` at import, so
anything set after boot is invisible to the process that set it — and the drafter does not run in
the web process at all. It runs in the WORKER, a separate unit. Nothing the web app puts in its
own environment can reach it. The database is the only medium the two already share.

THE ENVIRONMENT STILL WINS. A box whose `.env` carries ANTHROPIC_API_KEY behaves exactly as it
does today and never consults this table: the operator's own box, every box in CI, and any
deployment where someone has already made a deliberate choice. This only ever fills a hole.
"""
from __future__ import annotations

from core import state
from core.logging import get_logger

log = get_logger(__name__)

ANTHROPIC = "anthropic_api_key"


def get(name: str) -> str:
    """The stored value, or "". Never raises — a box too old to have the table has no secrets."""
    try:
        with state.connect() as c:
            row = c.execute("SELECT value FROM box_secrets WHERE name = ?", (name,)).fetchone()
    except Exception:                       # noqa: BLE001 — an unreadable table is "not set"
        return ""
    return str(row["value"]) if row else ""


def is_set(name: str) -> bool:
    return bool(get(name))


class SecretRejected(ValueError):
    """The value was not stored. The message is shown to the buyer, so it says what to fix."""


# WHAT AN ANTHROPIC KEY LOOKS LIKE. Deliberately loose: a shape check, not a validation of the
# key itself — only Anthropic can say whether a key works, and a box that refused a valid key
# because our regex was a month out of date would be worse than one that accepted a typo.
# What this catches is the mistake people actually make: pasting the wrong thing entirely —
# an email, a URL, a password, half a key with the front chewed off by a copy.
_ANTHROPIC_SHAPE = ("sk-ant-", 40)


def validate(name: str, value: str) -> str:
    """The cleaned value, or raise `SecretRejected` saying what to fix.

    VALIDATED ON SAVE, NOT ON USE, and the difference is where the person is standing. A key
    rejected here is rejected while they are looking at the field they pasted it into. The same
    key rejected on use fails hours later, inside a worker, on a draft nobody is watching — and
    the buyer's experience is that drafting silently does not work, which is the exact failure
    §2.6 exists to end.
    """
    value = str(value or "").strip()
    if not value:
        raise SecretRejected("Paste the key from your AI account, then turn drafts on.")
    if any(ch.isspace() for ch in value):
        # A pasted key with a newline or a stray space fails at the vendor with an opaque
        # error; caught here it is one sentence at the moment they can fix it.
        raise SecretRejected("That has a space or a line break in it — paste the key on its own.")
    if name == ANTHROPIC:
        prefix, least = _ANTHROPIC_SHAPE
        if not value.startswith(prefix) or len(value) < least:
            raise SecretRejected(f"That does not look like an Anthropic key — they start "
                                 f"with {prefix} and are longer than that.")
    return value


def put(name: str, value: str, *, user_id: str | None = None) -> None:
    """Set or replace a secret. The VALUE IS NEVER LOGGED — only that it changed, and by whom."""
    value = validate(name, value)
    with state.connect() as c:
        c.execute("INSERT INTO box_secrets (name, value, set_at, set_by) VALUES (?,?,?,?) "
                  "ON CONFLICT(name) DO UPDATE SET value = excluded.value, "
                  "set_at = excluded.set_at, set_by = excluded.set_by",
                  (name, value, state._now(), user_id))
    log.info("box_secret.set", name=name, user=user_id)


def clear(name: str, *, user_id: str | None = None) -> bool:
    """Remove it. Returns whether there was one. "Turn off" in the Settings row lands here."""
    with state.connect() as c:
        cur = c.execute("DELETE FROM box_secrets WHERE name = ?", (name,))
    gone = cur.rowcount > 0
    if gone:
        log.info("box_secret.cleared", name=name, user=user_id)
    return gone


def anthropic_key() -> str:
    """The key `core.brain` should use, environment first.

    ENVIRONMENT FIRST IS THE WHOLE COMPATIBILITY STORY. Every box running today has its key in
    `.env` and keeps behaving identically; this is consulted only where that is empty, which is
    exactly the delivered-box hole and nowhere else.

    `os.environ` IS READ LIVE, ahead of `settings`, and that is a correction rather than a
    flourish. `core.config.Settings` captures the environment at IMPORT, so a value set after
    that is invisible through it — and `brain.can_think()` has always read os.environ directly,
    which is why `tests/test_brain_readiness` sets the variable at runtime and expects it to
    count. Routing this through `settings` alone quietly broke that, and the suite said so.
    Reading both keeps every existing caller's behaviour and covers a `settings` that was
    deliberately overridden in-process.
    """
    import os

    from core.config import settings
    return ((os.environ.get("ANTHROPIC_API_KEY") or "").strip()
            or (getattr(settings, "anthropic_api_key", "") or "").strip()
            or get(ANTHROPIC))
