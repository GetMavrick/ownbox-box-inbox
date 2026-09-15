"""Cost guard (Layer 2): the in-system spend governor.

Caps month-to-date think() spend at the configured ceiling and refuses further
reasoning when hit (BudgetExceeded -> automation pauses). The window is pinned to
the SAME billing cycle day + timezone as the Anthropic console cap (Layer 1) so the
two layers can't drift out of phase.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from core import state
from core.config import as_bool, get_config
from core.exceptions import BudgetExceeded


def _cost_cfg() -> dict:
    return get_config()["cost"]


def _cycle_start(now: datetime | None = None) -> datetime:
    """Start of the current billing cycle, in the configured timezone."""
    cfg = _cost_cfg()
    tz = ZoneInfo(cfg.get("timezone", "UTC"))
    now = (now or datetime.now(tz)).astimezone(tz)
    day = int(cfg.get("billing_cycle_day", 1))
    # Clamp to a safe day-of-month (handles 29/30/31 on short months).
    day = min(day, 28)
    if now.day >= day:
        return now.replace(day=day, hour=0, minute=0, second=0, microsecond=0)
    first_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_month_last = first_of_month - timedelta(days=1)
    return prev_month_last.replace(day=day, hour=0, minute=0, second=0, microsecond=0)


def month_to_date_spend(now: datetime | None = None) -> float:
    start_utc = _cycle_start(now).astimezone(ZoneInfo("UTC")).isoformat()
    return state.spend_since(start_utc)


def ceiling() -> float:
    return float(_cost_cfg()["monthly_ceiling_usd"])


def check(estimated_usd: float = 0.0) -> float:
    """Raise BudgetExceeded if MTD spend (+ estimate) has reached the ceiling.

    Returns remaining headroom in USD otherwise. Called by brain.think() before
    every reasoning call.
    """
    spent = month_to_date_spend()
    cap = ceiling()
    if spent + estimated_usd >= cap:
        raise BudgetExceeded(f"AIOS budget reached: ${spent:.2f} of ${cap:.2f} this cycle")
    return cap - spent


# ── vendor meter — "no surprise bill" for every vendor, not just Claude ──────--
def vendor_cap(vendor: str) -> float | None:
    """The vendor's monthly unit cap from config (`vendors:`), or None = unmetered."""
    cfg = get_config().get("vendors", {}).get(vendor, {})
    cap = cfg.get("monthly_unit_cap")
    return float(cap) if cap is not None else None


def metered_vendors() -> list[str]:
    """Vendors with a monthly_unit_cap configured."""
    vendors = get_config().get("vendors", {}) or {}
    return [v for v, cfg in vendors.items()
            if (cfg or {}).get("monthly_unit_cap") is not None]


def vendor_alerts_enabled(vendor: str) -> bool:
    """Whether this vendor's METER may page the operator (`vendors.<v>.alerts`, default on).

    ENFORCEMENT IS UNAFFECTED — this only silences notifications. The cap still refuses
    spend exactly as before, the usage still appears in the spend line, and the vendor
    stays fully wired in. The single purpose is to stop a standing quota alert that the
    operator has already seen and judged, which is otherwise unsilenceable and turns into
    a daily message that trains him to ignore the channel it arrives on.

    Added for Apollo (owner, 2026-07-31): the autopilot spent the monthly allowance in ten
    days and stopped calling on Jul 21, so the alert was reporting a condition that was no
    longer happening — accurate about the ledger, wrong about the world.
    """
    cfg = get_config().get("vendors", {}).get(vendor, {}) or {}
    return as_bool(cfg.get("alerts"), default=True)


def vendor_usage(vendor: str, now=None) -> float:
    """Net units used this billing cycle (same window as the Claude guard)."""
    start_utc = _cycle_start(now).astimezone(ZoneInfo("UTC")).isoformat()
    return state.vendor_usage_since(vendor, start_utc)


def vendor_usd_rate(vendor: str) -> float:
    """est_usd_per_unit from config — the USD *overlay*. Enforcement stays in native
    units (the cap); this exists so spend can be REPORTED as one cross-vendor dollar
    number ("no surprise bill" is ultimately a dollar promise). 0 = not priced yet.
    Never sum native units across vendors — aggregate only through this overlay."""
    cfg = get_config().get("vendors", {}).get(vendor, {}) or {}
    return float(cfg.get("est_usd_per_unit", 0.0))


def usd_for_job(job_id: str) -> float:
    """One job's total estimated cost in dollars — Claude spend (exact, from the
    ledger; $0 on the subscription backend) plus vendor units priced through the
    USD overlay. This is the `(cost: $1.85)` line on the owner's ready message."""
    total = state.spend_for_job(job_id)
    for vendor, units in state.vendor_units_for_job_by_vendor(job_id).items():
        total += units * vendor_usd_rate(vendor)
    return total


# Paid vendors that MUST be metered. A missing cap for one of these is a config
# ERROR, not a free pass — fail CLOSED (refuse to spend) rather than silently allow
# unbounded spend. This matters most for HeyGen: the no-login review page can trigger
# a paid render (dashboard.public_approve), so an accidentally-uncapped HeyGen would be
# an unbounded anonymous-spend hole. A genuinely free/unmetered vendor is simply absent
# from this set and keeps returning None (allowed).
#
# W1.5 (audit A-N3): EVERY paid vendor, not just HeyGen. The old single-entry tuple meant
# a clone that dropped one `monthly_unit_cap:` line from the config got silent UNBOUNDED
# spend on exactly the vendors that bill per call — the opposite of the "no surprise
# bill" promise. All six ship with caps in config/aios.config.yaml, so this widening
# changes nothing on a correctly-configured box; it only converts a silent config gap
# into a loud BudgetExceeded.
_MUST_BE_METERED = ("heygen", "resend", "apollo", "hunter", "tomba", "scrapecreators",
                    "google_places")


def check_vendor(vendor: str, units: float = 1, now=None) -> float | None:
    """Refuse (BudgetExceeded) if this vendor's cycle usage + units would cross its cap.

    Units are the vendor's native currency (HeyGen renders, ScrapeCreators credits).
    Same billing-cycle window as the Claude guard, same pause semantics: the worker
    requeues the job (attempt refunded) to wait for the window — and because costly
    steps are checkpointed, the retry never re-spends what already succeeded.
    Returns remaining headroom, or None when the vendor is unmetered (no cap set) —
    EXCEPT a must-be-metered vendor with no cap fails closed (config error).
    """
    cap = vendor_cap(vendor)
    if cap is None:
        if vendor in _MUST_BE_METERED:
            raise BudgetExceeded(
                f"{vendor} has no monthly_unit_cap configured — refusing to spend. A "
                "paid vendor must be metered (set it under `vendors:` in the config).")
        return None
    start_utc = _cycle_start(now).astimezone(ZoneInfo("UTC")).isoformat()
    used = state.vendor_usage_since(vendor, start_utc)
    # `>` (not `>=` like the USD guard): units are DISCRETE — spending exactly to
    # the cap is allowed (the 100th render on a 100-render plan is paid for). The
    # USD guard refuses at the line because its estimates are fractional/fuzzy.
    # Intentional asymmetry; don't "fix" one to match the other.
    if used + units > cap:
        raise BudgetExceeded(
            f"{vendor} cap reached: {used:g} of {cap:g} units this cycle")
    return cap - used
