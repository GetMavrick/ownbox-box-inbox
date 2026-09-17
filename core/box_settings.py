"""What a person changed on this box — read here, with the shipped config underneath.

THE THIRD THING THAT WAS MISSING. A box already had two answers for "what is this value": the
tracked `config/aios.config.yaml`, which is what a box SHIPS with, and the untracked overlay, which
is what an OPERATOR pins. Neither is what a BUYER edits from a screen, and until this there was
nowhere for that to go (docs/PLAN_BOX_SETTINGS_AND_CONNECTIONS.md §5).

WHY NOT THE FILE. Measured 2026-09-17, and any one of the three settles it:

  1. `core.config.get_config` is `@functools.lru_cache(maxsize=1)` — read once per process. A
     screen that writes the file changes NOTHING until the box restarts. The buyer sees his change
     saved and it does not happen.
  2. The overlay's `_deep_merge` recurses into dicts and replaces everything else, so writing one
     key of a list drops the rest: `send_days: [mon]` wipes the other six days.
  3. The file has no author, and a box must be able to say who changed a setting — the same answer
     `box_secrets.set_by` gives for a credential.

THE READ ORDER IS THE WHOLE CONTRACT, and it runs from the most specific answer to the most
general: this person's answer, then this box's, then what the box shipped with, then the caller's
default. A setting nobody has touched reads exactly as it did before this module existed, which is
what makes adopting it safe one key at a time.

IT STORES JSON, NOT `str(value)`. A settings screen posts booleans, numbers and lists, and a store
that flattened them would hand back the string "False" — which is true. Round-tripping through JSON
is the difference between a toggle that works and a toggle that is always on.
"""
from __future__ import annotations

import json

from core import state
from core.logging import get_logger

log = get_logger(__name__)

_MISSING = object()

# THE BOX'S OWN ANSWER, AS A STORABLE VALUE. SQLite treats NULLs as distinct inside a primary key,
# so a NULL scope made `ON CONFLICT` a no-op and every write appended a row the reader never saw.
# Callers still say `user_id=None`; it is translated here and nowhere else.
_BOX = ""


def _scope(user_id: str | None) -> str:
    return str(user_id) if user_id else _BOX


def get(machine: str, key: str, *, user_id: str | None = None, default=None):
    """The value in force for this person, on this box. Never raises.

    NEVER RAISES BECAUSE OF WHERE THIS IS CALLED FROM — a worker deciding whether to send, a screen
    rendering a row. A settings store that throws takes down the thing it was meant to configure,
    so an unreadable row falls through to the layer beneath it exactly as an absent one does.
    """
    try:
        if user_id:
            mine = _read(machine, key, user_id)
            if mine is not _MISSING:
                return mine
        ours = _read(machine, key, None)
        if ours is not _MISSING:
            return ours
    except Exception as e:                       # noqa: BLE001 — see the docstring
        log.warning("settings.unreadable", machine=machine, key=key, error=str(e)[:120])
    shipped = _from_config(machine, key)
    return shipped if shipped is not _MISSING else default


def _read(machine: str, key: str, user_id: str | None):
    with state.connect() as c:
        row = c.execute("SELECT value FROM box_settings WHERE machine = ? AND key = ? "
                        "AND user_id = ?", (machine, key, _scope(user_id))).fetchone()
    if row is None:
        return _MISSING
    try:
        return json.loads(row["value"])
    except ValueError:
        # A row written before this module, or by hand. The raw text is a better answer than a
        # crash, and better than pretending the setting is unset.
        return row["value"]


def _from_config(machine: str, key: str):
    """What the box SHIPPED with — `<machine>.<key>` in the config, dots walked.

    The config is the default layer, never the store: nothing here writes to it, which is what
    keeps `git merge --ff-only` working on a clone that has changed its settings.
    """
    try:
        from core.config import get_config
        node = get_config().get(machine)
        for part in str(key).split("."):
            if not isinstance(node, dict) or part not in node:
                return _MISSING
            node = node[part]
        return node
    except Exception:                            # noqa: BLE001
        return _MISSING


def put(machine: str, key: str, value, *, user_id: str | None = None,
        set_by: str | None = None) -> None:
    """Write one setting. `user_id=None` is the box's answer; a user id is that person's.

    `set_by` IS WHO TYPED IT AND `user_id` IS WHO IT IS ABOUT, and they are genuinely different:
    an owner setting a box-wide value writes `user_id=None, set_by=<him>`. Collapsing them would
    lose the audit answer on exactly the rows where it matters most.
    """
    payload = json.dumps(value)
    with state.connect() as c:
        c.execute(
            "INSERT INTO box_settings (machine, key, user_id, value, set_at, set_by) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(machine, key, user_id) DO UPDATE SET value = excluded.value, "
            "set_at = excluded.set_at, set_by = excluded.set_by",
            (machine, key, _scope(user_id), payload, state._now(), set_by))
    # THE VALUE IS NEVER LOGGED. A setting is not a secret, but "notify me at this address" and
    # "my timezone" are a person's, and a journal nobody tails is where they would sit forever.
    log.info("settings.set", machine=machine, key=key, scope=("user" if user_id else "box"),
             by=set_by)


def clear(machine: str, key: str, *, user_id: str | None = None) -> bool:
    """Forget an answer so the layer beneath it applies again. -> whether a row went.

    THIS IS HOW "RESET TO DEFAULT" WORKS, and it is why the read order matters: clearing a person's
    row falls back to the box's, and clearing the box's falls back to what it shipped with. Writing
    the shipped value back instead would look identical today and silently pin it forever the day
    the default changes.
    """
    with state.connect() as c:
        cur = c.execute("DELETE FROM box_settings WHERE machine = ? AND key = ? AND user_id = ?",
                        (machine, key, _scope(user_id)))
        gone = cur.rowcount > 0
    if gone:
        log.info("settings.cleared", machine=machine, key=key,
                 scope=("user" if user_id else "box"))
    return gone


def describe(machine: str, key: str, *, user_id: str | None = None) -> dict:
    """Where the value in force came from, and who put it there — for a screen and for an audit.

    A settings row that shows a value and not its ORIGIN is how somebody changes a box-wide setting
    and cannot work out why nothing happened: his own per-person row was overriding it.
    """
    out = {"value": get(machine, key, user_id=user_id), "source": "default", "set_by": None,
           "set_at": None}
    try:
        with state.connect() as c:
            rows = c.execute(
                "SELECT user_id, set_by, set_at FROM box_settings WHERE machine = ? AND key = ?",
                (machine, key)).fetchall()
        by_scope = {r["user_id"]: r for r in rows}
        hit = by_scope.get(user_id) if user_id else None
        if hit is None:
            hit = by_scope.get(_BOX)
            if hit is not None:
                out["source"] = "box"
        else:
            out["source"] = "person"
        if hit is not None:
            out["set_by"], out["set_at"] = hit["set_by"], hit["set_at"]
        elif _from_config(machine, key) is not _MISSING:
            out["source"] = "shipped"
    except Exception as e:                       # noqa: BLE001
        log.warning("settings.describe_failed", machine=machine, key=key, error=str(e)[:120])
    return out
