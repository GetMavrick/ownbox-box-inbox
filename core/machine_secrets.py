"""A machine's own secrets (`m.secret`, SDK 1): an API token it needs, kept where the box keeps every key.

OSDev1, 2026-10-02 (the Lead Magnet port, which needs a Sanity token; OSDev5's website machine reuses it, "no
second version"): "m.secret(name), generic, one home per secret on the machine's settings page".

HOW IT WORKS. A machine declares each secret once, with the words the owner reads (`declare`). The box gives every
machine that declared one a Keys page, `/settings/machines/<slug>/keys`, owner only: one field per secret, which
never shows a saved value back, only whether one is saved. The machine reads the value with `get`. Values live in
`box_secrets`, the same table as the box's own keys, under `machine:<machine key>:<name>`, so one machine can never
read another's and none can read the box's own.

NOTHING HERE LOGS A VALUE; `box_secrets.put` logs only that a name changed, and by whom.
"""
from __future__ import annotations

import re

from core import box_secrets

_NAME = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_DECLARED: dict[str, dict[str, dict]] = {}       # {machine key: {name: {"label", "help"}}}
MAX_LEN = 4096


def _row(machine: str, name: str) -> str:
    return f"machine:{machine}:{name}"


def check_name(name: str) -> str:
    name = str(name or "").strip()
    if not _NAME.match(name):
        raise ValueError("a secret's name is lowercase letters, digits and _, starting with a letter "
                         "(e.g. 'sanity_token')")
    return name


def declare(machine: str, name: str, *, label: str, help: str = "") -> None:
    """Say this machine needs a secret. Its Keys page shows a field for it, with `label` and `help`."""
    name = check_name(name)
    label = str(label or "").strip()
    if not label:
        raise ValueError("a secret needs a label the owner can read, e.g. 'Sanity write token'")
    _DECLARED.setdefault(machine, {})[name] = {"label": label[:80], "help": str(help or "").strip()[:300]}


def declared(machine: str) -> dict[str, dict]:
    return dict(_DECLARED.get(machine) or {})


def get(machine: str, name: str) -> str:
    """The saved value, or "". Never raises."""
    try:
        return box_secrets.get(_row(machine, check_name(name)))
    except ValueError:
        return ""


def is_set(machine: str, name: str) -> bool:
    return bool(get(machine, name))


def put(machine: str, name: str, value: str, *, user_id: str | None = None) -> None:
    """Save it. Raises `box_secrets.SecretRejected` with a sentence for the owner when it can't be a key."""
    name = check_name(name)
    if name not in declared(machine):
        raise box_secrets.SecretRejected("That key isn't one this machine asks for.")
    value = str(value or "").strip()
    if not value:
        raise box_secrets.SecretRejected("Paste the key, then save.")
    if any(ch.isspace() for ch in value):
        raise box_secrets.SecretRejected("That has a space or a line break in it — paste the key on its own.")
    if len(value) > MAX_LEN:
        raise box_secrets.SecretRejected("That is far longer than a key. Paste just the key.")
    box_secrets.put(_row(machine, name), value, user_id=user_id)


def clear(machine: str, name: str, *, user_id: str | None = None) -> bool:
    try:
        return box_secrets.clear(_row(machine, check_name(name)), user_id=user_id)
    except ValueError:
        return False
