"""core.vendors.zernio — the ONE Zernio integration gateway (docs/ZERNIO_DEEP_INTEGRATION.md).

Every Zernio touch in the environment goes through here — the Zernio analogue of
core.brain.think() for reasoning. A department asks for a client scoped to a Space
and calls typed resources; the gateway owns key+profile scoping, the SDK data-model
contract, the determinate/indeterminate error contract, the mutating-retries-off
policy, profile-membership isolation, and pin+signature verification.

    from core.vendors import zernio
    z = zernio.client(space)                 # scoped to the Space's key + profile
    z.inbox.send(conv_id, account_id, text)  # or z.inbox.list(), z.accounts.discover(),
                                             # z.posts.create(...)

Deterministic by construction (no brain.think — §11-6); poll-only (no webhooks —
§11-4). Inert until a Space carries a Zernio key.
"""
from core.config import settings

from .client import ScopedClient
from .errors import ZernioError
from .verify import PINNED_SDK, verify_sdk

__all__ = ["client", "is_configured", "verify_sdk", "ZernioError", "ScopedClient",
           "PINNED_SDK"]


def client(space: dict | None) -> ScopedClient:
    """A Zernio client scoped to a Space's binding {zernio_key, zernio_profile_id}."""
    sp = space or {}
    return ScopedClient(sp.get("zernio_key"), sp.get("zernio_profile_id"))


def is_configured(key: str | None = None) -> bool:
    """The integration is dormant without a key (a per-Space key, or the global one
    for a single-tenant box)."""
    return bool(key or settings.zernio_api_key)
