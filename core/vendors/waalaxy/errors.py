"""The one Waalaxy error contract (mirrors core/vendors/zernio/errors.py).

Every Waalaxy call raises this, so determinate vs INDETERMINATE is classified in
exactly one place. The distinction is the whole safety story of the import path:
a timeout on POST /prospects/addProspectFromIntegration MAY have landed, and a
blind retry double-enrolls a real person into a connection sequence.
"""


class WaalaxyError(RuntimeError):
    """A Waalaxy failure. indeterminate=True means the request MAY have reached
    the API (timeout or dropped response after send) - a mutation must then be
    reconciled against the waalaxy_pushes ledger, never blind-retried."""

    def __init__(self, message: str, *, indeterminate: bool = False,
                 status: int | None = None):
        super().__init__(message)
        self.indeterminate = indeterminate
        self.status = status


def classify(exc: Exception, *, mutating: bool) -> bool:
    """True when a MUTATING failure may have landed. Reads are never indeterminate.

    requests wraps everything, so this stays deliberately conservative in the safe
    direction: on a mutation, any timeout or connection-shaped failure is treated
    as maybe-landed. The only cheap exception is a pure pre-connect DNS/refused
    error, which provably never left the box.
    """
    if not mutating:
        return False
    name = type(exc).__name__
    if name in ("ConnectTimeout",):
        return False                       # handshake never completed; nothing sent
    cause = str(getattr(exc, "args", "")) + str(exc)
    if name == "ConnectionError" and ("Name or service not known" in cause
                                      or "Connection refused" in cause):
        return False
    return True
