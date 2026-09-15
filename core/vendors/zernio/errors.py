"""The one Zernio error contract (docs/ZERNIO_DEEP_INTEGRATION.md §1).

Every Zernio call in the environment raises this — so the determinate vs.
INDETERMINATE distinction (a timeout MAY have landed → reconcile, never blind-
retry) is classified in exactly one place, for every resource group.
"""


class ZernioError(RuntimeError):
    """A Zernio failure. `indeterminate=True` means the request MAY have landed
    (timeout/connection drop after it left) — an irreversible mutation (a public
    DM, a social post, a paid ad) must be reconciled by id, never blind-retried."""

    def __init__(self, message: str, *, indeterminate: bool = False):
        super().__init__(message)
        self.indeterminate = indeterminate


def is_indeterminate(exc: Exception) -> bool:
    """A failure that MAY have reached Zernio is indeterminate; one that provably
    did not is determinate. Classified by exception TYPE first (name-based — httpx
    is never imported here), keywords as the last-resort fallback.

    - ConnectError / LateConnectionError → DETERMINATE: the SDK (pinned 1.4.189)
      raises LateConnectionError only from `except httpx.ConnectError`
      (late/client/base.py:183/312) — TCP/DNS failed, nothing left the box, retry
      freely. Without this branch the 'Connection failed:' text tripped the
      'connection' keyword and permanently parked posts/openers on any Zernio blip.
    - ReadError / RemoteProtocolError / WriteError → INDETERMINATE: these propagate
      RAW (the SDK's retry loop catches only TimeoutException + ConnectError); the
      request LEFT and the response was lost — it may have landed. httpx.ReadError
      often str()s to '' so no keyword scan can ever catch it.
    - 502/504 → INDETERMINATE: the proxy/LB may have forwarded the mutation before
      failing; '[504] HTTP 504' carries no keyword.
    - httpx.ConnectTimeout (→ LateTimeoutError) is also pre-send but stays on the
      keyword path → indeterminate: conservative in the SAFE direction, do not
      widen without source-level proof (see test drift-guard).
    """
    name = type(exc).__name__
    if name in ("ConnectError", "LateConnectionError"):
        return False
    if name in ("ReadError", "RemoteProtocolError", "WriteError"):
        return True
    if getattr(exc, "status_code", None) in (502, 504):
        return True
    blob = f"{name} {exc}".lower()
    return any(k in blob for k in ("timeout", "timed out", "connection", "read error"))


def is_not_found(exc: Exception) -> bool:
    """True when the resource PROVABLY does not exist (HTTP 404).

    This is the opposite kind of answer from `is_indeterminate`: a 404 is DEFINITE.
    The distinction matters because "unsure" and "provably gone" call for opposite
    recoveries — unsure means leave it alone, gone means rebuild it. Conflating them
    is what stranded `leadmagnet:SCALE`: its automation had been deleted platform-side,
    the account-reconcile guard caught the 404 in a bare `except`, logged "unsure, take
    the safe path", and then took no path at all — so every re-arm went on to UPDATE an
    id that no longer existed, and the keyword could never heal itself.

    `transport.call` re-raises `from e`, so the SDK's own class survives on `__cause__`
    and is checked first; the `[404]` marker the SDK writes into the message is the
    fallback for anything that arrives already flattened.
    """
    for e in (exc, getattr(exc, "__cause__", None)):
        if e is None:
            continue
        if type(e).__name__ in ("LateNotFoundError", "NotFoundError"):
            return True
        if getattr(e, "status_code", None) == 404:
            return True
        if "[404]" in str(e):
            return True
    return False
