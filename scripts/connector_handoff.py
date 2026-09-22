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
import re
import stat
import sys

# THE REPO ROOT ON THE PATH BEFORE core IS IMPORTED. bootstrap.sh runs this as
# `python3 scripts/connector_handoff.py`, so the interpreter puts scripts/ on sys.path and not the
# root above it; without this line the import below raises ModuleNotFoundError and first boot
# reports the handoff as failed on every box.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

PROVISION_JSON = os.environ.get("AIOS_PROVISION_JSON", "/opt/aios/provision.json")
_DEPLOY_OK = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
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


DEPLOY_VAR = "DEPLOY_TOKEN"


def deploy_token_from(path: str = PROVISION_JSON) -> str:
    """The narrow /deploy key this box was built with, or "" — never raises on a box without one."""
    try:
        with open(path, encoding="utf-8") as fh:
            facts = json.load(fh)
    except (OSError, ValueError):
        return ""
    tok = str((facts or {}).get("deploy_token") or "")
    return tok if _DEPLOY_OK.match(tok) else ""


def apply_deploy_token(token: str, *, env_path: str = ENV_PATH) -> str:
    """Put DEPLOY_TOKEN in `.env`, and NEVER over one the box already has.

    WHY THIS EXISTS. `/deploy` lets a box install the newest verified release on demand — the same
    thing its timer does at 08:00 UTC, but now. It has existed since #900 and has been UNUSABLE on
    every box ever sold: with no DEPLOY_TOKEN, `core/dispatch._deploy_authorized` falls back to
    DISPATCH_BEARER_TOKEN, which is also the dashboard password and the key that signs every
    unsubscribe link. Nobody responsible hands that to a deploy caller, so the door had no key.
    The owner hit it on 2026-09-22 with a demo hours away and a box five hours from its timer.

    NEVER OVERWRITES. Same rule as the connector key above: a box that already has one keeps it,
    so re-running bootstrap cannot silently invalidate a token somebody is holding.
    """
    if not token:
        return "no deploy token in provision.json (this box updates on its timer only)"
    lines = _read_env(env_path)
    if _value_of(lines, DEPLOY_VAR):
        return "kept the box's own deploy token"
    lines.append(f"{DEPLOY_VAR}={token}")
    fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines).rstrip("\n") + "\n")
    mode = stat.S_IMODE(os.stat(env_path).st_mode)
    if mode & 0o077:
        os.chmod(env_path, mode & 0o700)
    return "written"


# ── seats ───────────────────────────────────────────────────────────────────────────────────────
# WHERE A SOLD BOX MAY KEEP A PER-BOX SETTING. `config/aios.config.yaml` is tracked, so a box that
# edited its own copy would conflict with its own next `git pull`. `core.config.get_config` already
# deep-merges an UNTRACKED overlay over the tracked file for exactly this, and that is the only
# place a provisioned value belongs.
SETTINGS_PATH = os.environ.get("AIOS_SETTINGS_PATH", "/opt/aios/my/settings.yaml")


def tier_from(path: str = PROVISION_JSON) -> str:
    """What this buyer paid for, or "" when the box was not sold (the owner's own machine)."""
    try:
        with open(path, encoding="utf-8") as fh:
            return str(json.load(fh).get("tier") or "").strip().lower()
    except (OSError, ValueError):
        return ""


def apply_seats(tier: str, *, settings_path: str = SETTINGS_PATH) -> str:
    """Give a Pro box the unlimited seats it paid for. Returns what happened, for the boot log.

    OWNER, 2026-09-16: regular is three people, Pro is unlimited. Until now the tier never left the
    orders table, so a $1,599 buyer booted on the same seat line as a $499 one.

    ONLY EVER WIDENS, NEVER NARROWS. The base tier lives in the tracked config and this writes
    nothing at all for it — so a box whose provision.json is missing, unreadable or says `ownbox`
    keeps exactly what it shipped with. A bug here can fail to give somebody seats they bought,
    which is a support ticket; the other direction would lock a paying customer out of their own
    box, which is not.

    WRITTEN AS YAML BY HAND, NOT WITH A LIBRARY. This runs under the system python3 during
    bootstrap, before the venv exists, so pyyaml is not importable yet — the same constraint that
    keeps `core.config` out of this file. Two lines of YAML do not need a parser.
    """
    if tier != "pro":
        return "base tier, seats unchanged"
    try:
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        existing = ""
        if os.path.exists(settings_path):
            with open(settings_path, encoding="utf-8") as fh:
                existing = fh.read()
        if re.search(r"^\s*max_users\s*:", existing, re.M):
            return "seats already set, left alone"    # a human chose; never overwrite them
        block = ("\n" if existing and not existing.endswith("\n") else "")
        # 0 MEANS UNLIMITED (core.state.max_users), which is why Pro is a number and not a
        # deletion: the overlay can only ADD to the tracked file, never remove a key from it.
        block += "dash:\n  max_users: 0   # Ownbox Pro — unlimited people (provisioned)\n"
        with open(settings_path, "a", encoding="utf-8") as fh:
            fh.write(block)
        return "Pro: unlimited seats"
    except OSError as e:
        # NEVER FATAL. A box that boots with the base seat count is a box somebody can be given
        # more seats on in a minute; a box that refuses to finish first boot is a refund.
        return f"could NOT set Pro seats ({e}) — box has the base tier"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Copy this box's provisioned connector into its .env")
    ap.add_argument("--provision", default=PROVISION_JSON)
    ap.add_argument("--env", default=ENV_PATH)
    ap.add_argument("--force", action="store_true", help="replace a key this box already has (a rotation)")
    ap.add_argument("--settings", default=SETTINGS_PATH)
    a = ap.parse_args(argv)
    # SEATS FIRST, AND INDEPENDENT OF THE CONNECTOR. A box with no connector block still has a
    # tier, and returning early below on `conn is None` would have silently skipped it.
    print(f"   seats: {apply_seats(tier_from(a.provision), settings_path=a.settings)}")
    # DEPLOY TOKEN NEXT, AND ALSO INDEPENDENT OF THE CONNECTOR — for the same reason seats are.
    # A box with no connector block still needs to be reachable for a same-day fix, and the early
    # return below on `conn is None` would have skipped it for exactly the boxes most likely to
    # be plain: the ones sold to somebody who has not connected Instagram.
    print(f"   deploy token: {apply_deploy_token(deploy_token_from(a.provision), env_path=a.env)}")
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
