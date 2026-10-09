"""What a sandboxed machine asks for, and what the owner allowed it (decision D7: deny by default).

A machine declares its permissions in machine.yaml:

    permissions:
      think: {per_month: 300}           # AI through m.think, at most 300 calls a month (or `think: true`)
      fetch: [api.hubapi.com]           # the hosts the box may reach for it, by exact name, https only
      keys:                             # keys the owner types on its Keys page; the machine never reads one
        hubspot:
          host: api.hubapi.com          # the ONLY host this key is ever sent to (D8)
          header: Authorization
          prefix: "Bearer "
          label: HubSpot private app token

`parse()` turns that into the one shape the box stores and enforces, or refuses it with a sentence. The owner
approves it per version and can grant less than was asked (`narrow()`); a new version that asks for more shows
the difference (`diff()`) and needs a fresh yes (step 4's review card). Grants live in the box's database
(machine_grants), which no sandboxed machine can open.

Step 2 carries think, fetch and keys. people, inbox and send arrive next, each a named permission of its own.
"""
from __future__ import annotations

import ipaddress
import json
import re

from core import state

PERMISSIONS = ("think", "fetch", "keys")
THINK_PER_MONTH = 300
THINK_PER_MONTH_MAX = 5000
# AI IS ALSO BUDGETED BY SIZE (OSDev4's review of #2066): characters out and back a month, the answer's room counted
# before the call. 2,000,000 characters is about 500,000 tokens, a small part of the box's monthly AI ceiling, so one
# machine can never spend what the inbox needs. The owner can allow more, up to the most below.
THINK_CHARS_PER_MONTH = 2_000_000
THINK_CHARS_PER_MONTH_MAX = 50_000_000
CHARS_PER_TOKEN = 4
MAX_HOSTS = 20
MAX_KEYS = 10

_HOST = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_PRIVATE_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".test", ".invalid", ".example",
                     ".onion", ".arpa")
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_HEADER = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,40}$")
# Headers the box sets itself, or that would change where or how a request goes.
FORBIDDEN_HEADERS = {"host", "content-length", "transfer-encoding", "connection", "upgrade", "te", "trailer",
                     "proxy-authorization", "proxy-connection", "keep-alive", "expect", "cookie"}


class GrantError(ValueError):
    """A permission the box cannot accept. The message is a sentence for the machine's author or the owner."""


def _host(h) -> str:
    h = str(h or "").strip().lower().rstrip(".")
    try:
        ipaddress.ip_address(h.strip("[]"))
        is_address = True
    except ValueError:
        is_address = False
    if is_address:
        raise GrantError(f"fetch host {h!r} is an address: name the host, and the box checks where it points")
    if not _HOST.match(h) or h.endswith(_PRIVATE_SUFFIXES) or h in ("localhost",):
        raise GrantError(f"fetch host {h!r} is not a public host name")
    return h


def parse(manifest: dict) -> dict:
    """machine.yaml's `permissions:` -> {"think": {"per_month": n} | None, "fetch": [hosts], "keys": {...}}."""
    raw = (manifest or {}).get("permissions") or {}
    if not isinstance(raw, dict):
        raise GrantError("permissions must be a mapping, like `think: true`")
    unknown = sorted(set(raw) - set(PERMISSIONS))
    if unknown:
        raise GrantError(f"{', '.join(unknown)} is not a permission the box offers yet "
                         f"(it offers {', '.join(PERMISSIONS)})")
    out = {"think": None, "fetch": [], "keys": {}}

    t = raw.get("think")
    if t is True:
        out["think"] = {"per_month": THINK_PER_MONTH, "chars_per_month": THINK_CHARS_PER_MONTH}
    elif isinstance(t, dict):
        n = t.get("per_month", THINK_PER_MONTH)
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= THINK_PER_MONTH_MAX:
            raise GrantError(f"think.per_month must be a whole number from 1 to {THINK_PER_MONTH_MAX}")
        c = t.get("chars_per_month", THINK_CHARS_PER_MONTH)
        if isinstance(c, bool) or not isinstance(c, int) or not 1 <= c <= THINK_CHARS_PER_MONTH_MAX:
            raise GrantError(f"think.chars_per_month must be a whole number from 1 to {THINK_CHARS_PER_MONTH_MAX:,}")
        out["think"] = {"per_month": n, "chars_per_month": c}
    elif t not in (None, False):
        raise GrantError("think is `true` or `{per_month: <n>}`")

    hosts = raw.get("fetch") or []
    if not isinstance(hosts, list):
        raise GrantError("fetch is a list of host names, like [api.hubapi.com]")
    out["fetch"] = sorted({_host(h) for h in hosts})
    if len(out["fetch"]) > MAX_HOSTS:
        raise GrantError(f"a machine may reach at most {MAX_HOSTS} hosts")

    keys = raw.get("keys") or {}
    if not isinstance(keys, dict):
        raise GrantError("keys is a mapping of key names, each with its host")
    if len(keys) > MAX_KEYS:
        raise GrantError(f"a machine may hold at most {MAX_KEYS} keys")
    for name, spec in keys.items():
        if not isinstance(name, str) or not _KEY.match(name):
            raise GrantError(f"key name {name!r} must be lowercase letters, digits and _")
        if not isinstance(spec, dict):
            raise GrantError(f"key {name} needs host, header and label")
        host = _host(spec.get("host"))
        if host not in out["fetch"]:
            raise GrantError(f"key {name} goes to {host}, which is not in fetch: a key only travels to a host "
                             f"the machine may reach")
        header = str(spec.get("header") or "Authorization")
        if not _HEADER.match(header) or header.lower() in FORBIDDEN_HEADERS:
            raise GrantError(f"key {name} cannot be sent as the {header!r} header")
        prefix = str(spec.get("prefix") or "")
        if len(prefix) > 20 or any(c in prefix for c in "\r\n\0"):
            raise GrantError(f"key {name}'s prefix must be short and on one line")
        label = str(spec.get("label") or "").strip()
        if not label or len(label) > 60:
            raise GrantError(f"key {name} needs a label of at most 60 characters: the words the owner reads")
        out["keys"][name] = {"host": host, "header": header, "prefix": prefix, "label": label,
                             "help": str(spec.get("help") or "")[:200]}
    return out


def narrow(asked: dict, allowed: dict) -> dict:
    """What the owner granted: never more than the machine asked for, whatever `allowed` says."""
    out = {"think": None, "fetch": [], "keys": {}}
    if asked.get("think") and allowed.get("think"):
        out["think"] = {"per_month": min(asked["think"]["per_month"], allowed["think"]["per_month"]),
                        "chars_per_month": min(_chars(asked["think"]), _chars(allowed["think"]))}
    out["fetch"] = sorted(set(asked.get("fetch") or []) & set(allowed.get("fetch") or []))
    out["keys"] = {k: v for k, v in (asked.get("keys") or {}).items()
                   if k in (allowed.get("keys") or {}) and v["host"] in out["fetch"]}
    return out


def diff(old: dict | None, new: dict) -> dict:
    """The permission difference a new version brings: {"added": [sentences], "removed": [sentences]}.

    An added line is shown in red on the review card and needs the owner's fresh yes (D7)."""
    old = old or {"think": None, "fetch": [], "keys": {}}
    added, removed = [], []
    if new.get("think") and not old.get("think"):
        added.append(f"use AI, up to {new['think']['per_month']} times a month")
    elif old.get("think") and not new.get("think"):
        removed.append("use AI")
    else:
        if new.get("think") and new["think"]["per_month"] > old["think"]["per_month"]:
            added.append(f"use AI more: up to {new['think']['per_month']} times a month "
                         f"(was {old['think']['per_month']})")
        if new.get("think") and _chars(new["think"]) > _chars(old["think"]):
            added.append(f"use AI more: up to {_chars(new['think']):,} characters a month "
                         f"(was {_chars(old['think']):,})")
    for h in sorted(set(new.get("fetch") or []) - set(old.get("fetch") or [])):
        added.append(f"send and receive data with {h}")
    for h in sorted(set(old.get("fetch") or []) - set(new.get("fetch") or [])):
        removed.append(f"reach {h}")
    for k, v in sorted((new.get("keys") or {}).items()):
        was = (old.get("keys") or {}).get(k)
        if was is None:
            added.append(f"use your {v['label']} with {v['host']}")
        elif was["host"] != v["host"]:
            added.append(f"send your {v['label']} to {v['host']} instead of {was['host']}")
    for k in sorted(set(old.get("keys") or {}) - set(new.get("keys") or {})):
        removed.append(f"use your {old['keys'][k]['label']}")
    return {"added": added, "removed": removed}


def _chars(think: dict) -> int:
    """A think grant's monthly characters; a grant stored before there was one gets the default."""
    return int((think or {}).get("chars_per_month") or THINK_CHARS_PER_MONTH)


def grant(slug: str, version: str, grants: dict, *, by: str) -> None:
    """Store what the owner approved for this version. Replaces the last grant whole."""
    with state.connect() as c:
        c.execute("INSERT INTO machine_grants (machine, version, grants, granted_by, granted_at) VALUES (?,?,?,?,?) "
                  "ON CONFLICT(machine) DO UPDATE SET version = excluded.version, grants = excluded.grants, "
                  "granted_by = excluded.granted_by, granted_at = excluded.granted_at",
                  (slug, str(version), json.dumps(grants, sort_keys=True), str(by), state._now()))


def current(slug: str) -> tuple[str, dict]:
    """(version, grants) the owner approved, or ("", nothing) for a machine with no grant: it may do nothing."""
    with state.connect() as c:
        row = c.execute("SELECT version, grants FROM machine_grants WHERE machine = ?", (slug,)).fetchone()
    if row is None:
        return "", {"think": None, "fetch": [], "keys": {}}
    return row["version"], json.loads(row["grants"])


def revoke(slug: str) -> bool:
    with state.connect() as c:
        return c.execute("DELETE FROM machine_grants WHERE machine = ?", (slug,)).rowcount > 0
