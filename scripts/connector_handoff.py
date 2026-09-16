#!/usr/bin/env python3
"""First boot: turn this box's provisioned connector into its own credentials (SPEC_CONNECT_HANDOFF, box half).

The provisioner mints a Zernio profile for each box it builds and an API key scoped to that profile alone,
and hands both to the machine in `/opt/aios/provision.json` (0600 root:root). Nothing reads them until this
script runs: it copies the pair into the box `.env`, which is where every Space resolves its key from.

WHY A SCRIPT AND NOT A LINE OF SHELL. The key must never pass through a shell — not as an argument, not in
an environment a `ps` can read, not in an `echo` that lands in a log. It is read from a file and written to
a file by this process and nothing else. Nothing here is ever printed: the output says which NAMES were
written, never a value.

WHAT IT REFUSES TO DO. It does not overwrite a key this box already has. A buyer who pasted their own Zernio
key keeps it, and this says so plainly rather than silently swapping the account their posts go to. Use
--force for a deliberate rotation, which is a thing a person decides, not a boot.

ORDINARY OUTCOMES, none of them failures: no provision.json (a box nobody bought — the owner's own machine,
a hand-installed box), no `connector` block in it (built before the connector existed, or the owner's team
key was not set that day), or the values already in place. Each exits 0 and says which.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import stat
import sys

# THE REPO ROOT ON THE PATH BEFORE core IS IMPORTED. bootstrap.sh runs this as
# `python3 scripts/connector_handoff.py`, so the interpreter puts scripts/ on sys.path and not the
# root above it; without this line the import below raises ModuleNotFoundError and first boot
# reports the handoff as failed on every box.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

PROVISION_JSON = os.environ.get("AIOS_PROVISION_JSON", "/opt/aios/provision.json")
ENV_PATH = os.environ.get("AIOS_ENV_PATH", "/opt/aios/.env")

# The same shapes the provisioner validated before it wrote them; checked again here because a file is not
# an argument, and the half that trusts is the half that gets a shell metacharacter in a key one day.
# IMPORTED RATHER THAN RESTATED, so "the same shapes" is enforced instead of asserted in a comment —
# core/handoff_shape.py says why, and imports nothing but `re` — which this script needs, because
# bootstrap.sh runs it under the system python3 before the venv exists, and because importing
# core.config here would freeze every setting merely by importing this module.
from core.handoff_shape import KEY as _KEY, PROFILE_ID as _PROFILE_ID, \
    VENDOR as _VENDOR  # noqa: E402
KEY_VAR, PROFILE_VAR = "ZERNIO_API_KEY", "ZERNIO_PROFILE_ID"


class HandoffError(Exception):
    """The connector block is present but unusable. Loud, because a bad key written quietly is worse."""


def connector_from(path: str = PROVISION_JSON) -> dict | None:
    """This box's connector handoff, or None when there is simply nothing to hand over."""
    try:
        with open(path, encoding="utf-8") as fh:
            facts = json.load(fh)
    except (OSError, ValueError):            # absent, unreadable, or not JSON: nothing to do
        return None
    block = facts.get("connector")
    if not isinstance(block, dict):
        return None
    vendor = str(block.get("vendor") or "")
    profile_id, key = str(block.get("profile_id") or ""), str(block.get("key") or "")
    if vendor != _VENDOR:
        raise HandoffError(f"unknown connector vendor {vendor!r}: this box was built for one it does not have")
    if not _PROFILE_ID.match(profile_id):
        raise HandoffError("the connector profile id is not a Zernio profile id")
    if not _KEY.match(key):                  # never say what it was: this message can reach a log
        raise HandoffError("the connector key is not a usable key")
    return {"vendor": vendor, "profile_id": profile_id, "key": key}


def _read_env(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().splitlines()
    except OSError:
        return []


def _value_of(lines: list[str], name: str) -> str | None:
    for line in lines:
        if line.startswith(f"{name}="):
            return line[len(name) + 1:].strip()
    return None


def apply(conn: dict, *, env_path: str = ENV_PATH, force: bool = False) -> dict:
    """Put the pair in `.env`. Returns what happened, per variable, in words a boot log can carry."""
    lines = _read_env(env_path)
    wanted = {KEY_VAR: conn["key"], PROFILE_VAR: conn["profile_id"]}
    outcome, changed = {}, False
    for name, value in wanted.items():
        current = _value_of(lines, name)
        if current == value:
            outcome[name] = "already set"
            continue
        if current and not force:
            # THE BOX KEEPS THE KEY IT HAS. Replacing it would move where this box's posts go, silently.
            outcome[name] = "kept the box's own value (use --force to replace it)"
            continue
        replaced = False
        for i, line in enumerate(lines):
            if line.startswith(f"{name}="):
                lines[i], replaced = f"{name}={value}", True
                break
        if not replaced:
            lines.append(f"{name}={value}")
        outcome[name] = "replaced" if current else "written"
        changed = True
    if changed:
        fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines).rstrip("\n") + "\n")
        # TIGHTEN, NEVER LOOSEN. O_CREAT's mode applies only when the file is CREATED, so an .env that
        # already existed keeps whatever mode it had — and install.sh sets none explicitly, so a box
        # whose .env came out 0644 would now hold a key any local account could read. Measured on the
        # live box 2026-09-15: its .env is 0600, but by umask rather than by decision, which is not a
        # property to rely on. So: strip group and other from the file we just put a secret in. Never
        # the reverse — a box that has made it stricter than 0600 keeps its own choice.
        mode = stat.S_IMODE(os.stat(env_path).st_mode)
        if mode & 0o077:
            os.chmod(env_path, mode & 0o700)
            outcome["(file mode)"] = f"tightened from {mode:04o} to {mode & 0o700:04o}"
    return outcome


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Copy this box's provisioned connector into its .env")
    ap.add_argument("--provision", default=PROVISION_JSON)
    ap.add_argument("--env", default=ENV_PATH)
    ap.add_argument("--force", action="store_true", help="replace a key this box already has (a rotation)")
    a = ap.parse_args(argv)
    try:
        conn = connector_from(a.provision)
    except HandoffError as e:
        print(f"   connector handoff REFUSED: {e}", file=sys.stderr)
        return 1
    if conn is None:
        print("   no connector for this box; nothing to hand over")
        return 0
    for name, what in apply(conn, env_path=a.env, force=a.force).items():
        print(f"   {name}: {what}")          # the NAME and what happened to it, never the value
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
