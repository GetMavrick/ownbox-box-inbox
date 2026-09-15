"""HTTP construction + call policy - the ONE place a Waalaxy request is built.

Fail-closed on the key: a subaccount whose env var is unset REFUSES, it never
falls through to some other account's key - with three LinkedIn accounts on one
team, a silent fallback is how account A's campaign messages account B's list.

Mutating retries are OFF by construction: plain requests, no Session retry
adapter, one attempt. A timeout on the single write endpoint is INDETERMINATE
(the prospects may have landed); the ledger reconciles, the transport never
re-issues. Reads may be retried by callers freely - they are side-effect free.
"""
import os

from core.logging import get_logger

from .errors import WaalaxyError, classify

log = get_logger(__name__)

# The REAL gateway (plan §2). The docs-site host does not resolve from the box;
# the zpka_ key prefix is a Zuplo key and this is the Zuplo gateway behind the
# docs. Override with WAALAXY_BASE_URL only in tests.
BASE_URL = "https://waalaxy-omicron-8ce699a.zuplo.app"
TIMEOUT = 30


def _base() -> str:
    return os.environ.get("WAALAXY_BASE_URL") or BASE_URL


def resolve_key(key_env: str) -> str:
    """The per-subaccount key, from the env var the account's config names."""
    key = (os.environ.get(key_env) or "").strip()
    if not key:
        raise WaalaxyError(
            f"waalaxy refused: {key_env} is not set (fail-closed - a push must "
            f"name a subaccount whose own key resolves, never borrow another's)")
    return key


def _request(method: str, path: str, key: str, payload: dict | None,
             *, mutating: bool):
    import requests
    try:
        r = requests.request(
            method, f"{_base()}{path}", json=payload, timeout=TIMEOUT,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"})
    except WaalaxyError:
        raise
    except Exception as e:  # noqa: BLE001 - single classification point
        raise WaalaxyError(f"waalaxy {method} {path} failed: {str(e)[:200]}",
                           indeterminate=classify(e, mutating=mutating)) from e
    if r.status_code == 401:
        raise WaalaxyError(f"waalaxy auth rejected on {path} (regenerate this "
                           f"subaccount's key in Waalaxy)", status=401)
    if r.status_code == 429:
        raise WaalaxyError("waalaxy rate limited (429) - back off, do not retry "
                           "a mutation blind", status=429,
                           indeterminate=mutating)
    if r.status_code >= 400:
        raise WaalaxyError(f"waalaxy {method} {path} -> {r.status_code}: "
                           f"{r.text[:200]}", status=r.status_code,
                           indeterminate=mutating and r.status_code in (502, 504))
    try:
        return r.json()
    except ValueError as e:
        raise WaalaxyError(f"waalaxy {path} returned non-JSON") from e


def get(path: str, key: str):
    return _request("GET", path, key, None, mutating=False)


def post(path: str, key: str, payload: dict):
    """The mutation path. ONE attempt, ever, by construction."""
    return _request("POST", path, key, payload, mutating=True)
