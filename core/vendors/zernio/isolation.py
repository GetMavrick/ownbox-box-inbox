"""Per-tenant isolation (docs/ZERNIO_DEEP_INTEGRATION.md §1, ZERNIO_SDK_INTEGRATION §5).

Under the target ONE-key + Profile-per-Space model a shared key can reach EVERY
Zernio Profile, so isolation is enforced in code, not by construction:

  - the poll and account discovery are scoped by `profile_id` (in the resources);
  - and, as a backstop, a mutation must prove its target account BELONGS to the
    Space's profile before it fires — a public DM/post to the wrong tenant is
    unrecoverable. The membership set is cached briefly (a mutation is rare per
    conversation; this is not a per-call cost).

Transitional two-key mode (no profile_id) → the per-Space key already isolates and
these are no-ops.
"""
import time

from . import model, transport
from .errors import ZernioError

_TTL_S = 300
# INVARIANT: cached on the (profile_id, api_key) PAIR, not profile alone — under a
# key rotation or a shared-profile misconfig, a membership set fetched under one
# key must never authorize mutations made under another.
_cache: dict = {}          # (profile_id, api_key) -> (set[str], expires_at)


def accounts_for_profile(profile_id: str, api_key: str | None) -> set:
    now = time.time()
    ck = (profile_id, api_key)
    hit = _cache.get(ck)
    if hit and hit[1] > now:
        return hit[0]
    resp = transport.raw_client(api_key).accounts.list(profile_id=profile_id)   # may raise
    ids = {str(model.obj_id(a)) for a in model.items(resp, "accounts", "data", "items")
           if model.obj_id(a)}
    _cache[ck] = (ids, now + _TTL_S)
    return ids


def assert_member(account_id: str, *, profile_id: str | None, api_key: str | None) -> None:
    """Raise ZernioError (determinate) unless `account_id` is provably in the
    Space's Profile. FAIL-CLOSED: a lookup that can't resolve also raises — never
    send a public message we can't prove belongs to this tenant."""
    if not profile_id:
        return
    try:
        accts = accounts_for_profile(profile_id, api_key)
    except Exception as e:  # noqa: BLE001
        raise ZernioError(f"could not verify account/profile membership: {str(e)[:120]}",
                          indeterminate=False) from e
    if str(account_id) not in accts:
        raise ZernioError(f"isolation guard: account {account_id} is not in Space profile "
                          f"{profile_id} — refusing to act", indeterminate=False)
