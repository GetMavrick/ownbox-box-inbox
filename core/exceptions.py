"""AIOS exception types."""
import re


class AIOSError(Exception):
    """Base class for all AIOS errors."""


# A vendor 4xx/429 whose body carries any of these is a HARD quota — a billing-period
# or credit exhaustion that takes DAYS to resolve — not a transient per-second rate limit
# (which clears in moments). Hunter's monthly cap reads "...per billing period"; Apollo
# credit-out reads "out of credits" / "plan limit". Also HERE: an account-state block
# (Hunter's "restricted_account" / "account was restricted", a suspension) — it is 429-shaped
# but won't clear on its own either, so it must fail honestly + fail over, NOT pause-and-retry
# (that re-froze the whole worker). Kept conservative so a plain "too many requests" stays soft.
_HARD_QUOTA_RE = re.compile(
    r"billing period|per month|monthly (?:limit|quota)|quota (?:exceeded|exhausted|reached)"
    r"|out of (?:credits|searches|quota)|no credits|plan limit|upgrade your plan"
    r"|restricted|suspended|account (?:disabled|blocked)", re.I)


def is_hard_quota(text: str) -> bool:
    """True when a vendor error body signals a HARD, non-self-clearing block — a billing/period/
    credit quota OR an account-state restriction (days to resolve, needs owner action) — as
    opposed to a transient rate limit. Drives BudgetExceeded(hard=...): a hard block fails the
    job honestly + fails over to the next key, never pauses the whole worker."""
    return bool(_HARD_QUOTA_RE.search(text or ""))


class BudgetExceeded(AIOSError):
    """A spend/quota ceiling was hit. NOT a crash.

    Two shapes, distinguished by `hard`:
      • SOFT (default) — a self-refreshing window: the cost guard's month-to-date
        ceiling, or the Claude subscription's ~5-hour cap. The worker PAUSES the job
        (no attempt consumed) and retries; it clears on its own. Intended autonomy-safety.
      • HARD (`hard=True`) — a vendor billing-period / credit exhaustion (e.g. Hunter's
        monthly search cap) that will NOT clear for days. The worker FAILS it honestly
        with a vendor-named, actionable notice instead of pausing — because pausing +
        retrying every few minutes for weeks only re-burns paid upstream calls (each
        `discover` re-runs the Apollo sweep) and freezes the whole worker, all while the
        owner is told a misleading "I'll finish shortly." `vendor` names the culprit.
    """

    def __init__(self, message: str, *, hard: bool = False, vendor: str | None = None):
        super().__init__(message)
        self.hard = hard
        self.vendor = vendor


class RetryableError(AIOSError):
    """A transient failure that should be retried later, not failed permanently.

    Raised by the layer that KNOWS an error is transient — brain.think() for an
    exhausted-retry Anthropic outage (429/5xx/overloaded/connection/timeout), and
    later each vendor client for its own transient failures. The worker stays dumb:
    it requeues a RetryableError up to an attempt cap, and only then fails. This is
    what keeps a multi-minute API outage from silently burning the whole queue to
    'failed'. Contrast BudgetExceeded (pause indefinitely, no attempt consumed) and
    any other Exception (a real bug — fail immediately, terminal).
    """


class RateCapped(AIOSError):
    """An hourly send cap is full — DEFER the job until capacity exists, without
    consuming a retry attempt. Deliberately NOT a RetryableError subclass: the retry
    path carries an attempt budget (~15 min of backoff) while a cap window is up to
    an hour, so classifying over-cap as retryable terminally failed jobs that merely
    arrived in a burst. The worker requeues with not_before and keeps the loop
    moving (unlike BudgetExceeded it does NOT pause — other modules' jobs flow on).

    not_before: ISO-8601 time when capacity is expected to exist again.
    """

    def __init__(self, message: str, *, not_before: str):
        super().__init__(message)
        self.not_before = not_before


class DispatchAuthError(AIOSError):
    """Bearer token missing or invalid on a /dispatch request."""


class ParseError(AIOSError):
    """A module could not parse a request into a runnable action."""


class VendorError(AIOSError):
    """A TERMINAL vendor failure — bad request, auth, a render that HeyGen itself
    marked failed. Not retryable: the worker fails the job immediately. Transient
    vendor trouble (5xx/429/timeouts/connection) must raise RetryableError instead —
    the distinction is the whole error contract (docs/HANDLER_CONTRACT.md Rule 2)."""

    def __init__(self, vendor: str, status: int | str, message: str):
        super().__init__(f"{vendor} error {status}: {message}")
        self.vendor = vendor
        self.status = status
