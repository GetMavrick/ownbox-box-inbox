"""Config shapes for the multi-subaccount reality (plan §6, amended 2026-09-01).

Three LinkedIn accounts on one Waalaxy team means three subaccounts, three API
keys, three disjoint sets of lists and campaigns. An Account is the unit every
push names explicitly; a Route maps a GTM bucket to a list NAME (resolved to
_id at runtime so the owner can reorganize Waalaxy without a code change - a
missing name fails closed rather than importing into the wrong list).
"""
from dataclasses import dataclass, field

from core.config import get_config

from .errors import WaalaxyError


@dataclass(frozen=True)
class Route:
    bucket: str                 # GTM icp_segment: A|B|C|D
    list_name: str              # Waalaxy prospect list, matched by exact name
    campaign_name: str | None   # None = import only, never enroll (phase 3)
    list_id: str | None = None  # when the destination names the id, skip the name lookup:
                                # resolving by name is a second chance to hit the wrong list


@dataclass(frozen=True)
class Account:
    name: str                   # e.g. "brian", "sdr2" - the ledger's account key
    key_env: str                # env var holding THIS subaccount's API key
    daily_cap: int              # hard ceiling on imports/day, enforced from the ledger
    routes: tuple = field(default_factory=tuple)


def waalaxy_config() -> dict:
    return (get_config() or {}).get("waalaxy") or {}


def enabled() -> bool:
    return bool(waalaxy_config().get("enabled"))


def origin() -> str:
    return str(waalaxy_config().get("origin") or "aios")


def accounts() -> list:
    out = []
    for a in waalaxy_config().get("accounts") or []:
        routes = tuple(Route(bucket=str(r.get("bucket") or "").upper(),
                             list_name=r.get("list") or "",
                             campaign_name=r.get("campaign"),
                             list_id=r.get("list_id"))
                       for r in a.get("routes") or [])
        out.append(Account(name=a.get("name") or "", key_env=a.get("key_env") or "",
                           daily_cap=int(a.get("daily_cap") or 0), routes=routes))
    return out


def account(name: str) -> Account:
    for a in accounts():
        if a.name == name:
            if not a.key_env or not a.daily_cap:
                raise WaalaxyError(f"waalaxy account '{name}' misconfigured: "
                                   f"key_env and daily_cap are both required")
            return a
    raise WaalaxyError(f"waalaxy account '{name}' not in config (fail-closed)")


def route_for(acct: Account, bucket: str) -> Route:
    for r in acct.routes:
        if r.bucket == bucket.upper():
            if not r.list_name:
                raise WaalaxyError(f"waalaxy route {acct.name}/{bucket}: empty "
                                   f"list name (fail-closed)")
            return r
    raise WaalaxyError(f"waalaxy: no route for bucket {bucket} on account "
                       f"'{acct.name}' (fail-closed - nothing imports without "
                       f"an explicit destination)")
