"""SDK construction + call policy (docs/ZERNIO_DEEP_INTEGRATION.md §1).

The ONE place a raw `Zernio(...)` is built and the ONE place an SDK exception is
classified. Reads get the SDK's retries; MUTATIONS are built `max_retries=1` — the
SDK auto-retries POSTs on timeout (default 3), which would double-DM / double-post
beneath the exactly-once ledger (F-4/A-1). AIOS owns reconciliation; the transport
must never re-issue a mutation.
"""
from core.config import settings

from .errors import ZernioError, is_indeterminate


def raw_client(api_key: str | None, *, max_retries: int | None = None):
    # FAIL-CLOSED at the ONE construction point: a falsy key must REFUSE, never fall
    # through to the SDK's ZERNIO_API_KEY / LATE_API_KEY env fallbacks — that silent
    # fallback is exactly how a mis-keyed Space (unset zernio_key_env on a fresh box)
    # would operate as ANOTHER tenant: list their inbox, discover their accounts,
    # post to their pages. Reads and mutations alike refuse here.
    if not api_key:
        raise ZernioError("zernio client refused: no per-Space key resolved "
                          "(fail-closed — never the SDK env-var fallback)")
    # Lazy import — only a process that actually touches Zernio loads the SDK.
    from zernio import Zernio
    kwargs = {"api_key": api_key, "timeout": settings.zernio_timeout}
    if max_retries is not None:
        kwargs["max_retries"] = max_retries
    return Zernio(**kwargs)


def call(op: str, fn, *, mutating: bool = False):
    """Run one SDK call under the error contract. Reads are never indeterminate
    (nothing landed); a MUTATING call inherits the timeout-might-have-landed hazard."""
    try:
        return fn()
    except ZernioError:
        raise
    except Exception as e:  # noqa: BLE001 — single classification point
        raise ZernioError(f"zernio {op} failed: {str(e)[:200]}",
                          indeterminate=mutating and is_indeterminate(e)) from e
