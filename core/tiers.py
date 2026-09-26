"""What a box bought, kept as one core fact, and the features that fact switches on.

docs/SCOPE_TIERS.md (#1617, owner-approved 2026-09-26). Piece 1: this module, `POST /deploy/plan`
(core/dash/box_settings.py) and the seat count read from here (core/state.max_users).

CODE ASKS ABOUT A FEATURE, NEVER ABOUT A TIER. `tiers.allows("coworkers")`, never `tier == "pro"`.
Which tier includes which feature is the one table below. A new tier, an add-on for Base, or moving
a feature between tiers is a change to that table, never to a runner, a screen or a partner's
machine. That is what keeps a pricing decision from becoming a rebuild.

WHERE THE ANSWER COMES FROM, in order: what Ownbox last told this box (`POST /deploy/plan`, kept in
box_settings), then the tier in provision.json from the build, then Base. Ownbox being unreachable
never switches anything off: the box keeps the last answer it was given.

`seq` ONLY GOES UP. A plan message whose `seq` is not higher than the one held is refused, so a
delayed older message can never undo a newer one. The comparison and the write are one transaction,
because the web process runs two workers and both can receive a message.

A SWITCH, NOT A LOCK (SCOPE_TIERS §2.4). The buyer has root on their own box. Nothing here is a
licence or anti-tamper; money is enforced on Ownbox's side, where updates end with the paid period.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from core.logging import get_logger

log = get_logger(__name__)

# THE ONE TABLE. `id` is what orders and boxes store and never changes (Base's id is "ownbox", the
# brand); `name` is what a person reads: "Base Machine" and "Base Machine Pro" (owner, 2026-09-26,
# matching the pricing page and the Stripe products). `people` is the seat limit, carrying today's values
# unchanged (owner, 2026-09-16: Base is three people, Pro is unlimited): 0 is unlimited, and None
# is "the box's configured limit", `dash.max_users`, which ships as 3 and which an owner may raise.
TIERS = {
    "ownbox": {"name": "Base Machine", "features": frozenset(), "people": None},
    "pro":    {"name": "Base Machine Pro", "features": frozenset({"coworkers"}), "people": 0},
}
DEFAULT = "ownbox"
# Every feature a plan may name. An add-on is one of these; anything else is refused.
FEATURES = frozenset().union(*(t["features"] for t in TIERS.values()))

NS, KEY = "core", "plan"                 # box_settings: {"seq", "tier", "add", "since"}


class PlanRefused(ValueError):
    """A plan message this box will not store. `stale` is True when only its seq was too old."""
    def __init__(self, message: str, *, stale: bool = False):
        super().__init__(message)
        self.stale = stale


def _provisioned() -> str:
    """The tier written at build, or "" (an unsold box, or one sold before 2026-09-16)."""
    try:
        from core import claim
        with open(claim.PROVISION_JSON, encoding="utf-8") as fh:
            return str(json.load(fh).get("tier") or "").strip().lower()
    except Exception:                   # noqa: BLE001 — absent or unreadable: no tier recorded
        return ""


def configured_people() -> int:
    """`dash.max_users` from the box's config, 0 or absent = unlimited. Read per call, never bound."""
    from core.config import get_config
    try:
        return max(0, int((get_config().get("dash") or {}).get("max_users", 0) or 0))
    except Exception:                   # noqa: BLE001 — a junk value must not decide a seat
        return 0


def _people(tier: str) -> int:
    """This tier's seat limit, 0 = unlimited. ONLY EVER WIDENS what the box is configured for: a
    tier can make seats unlimited, and never takes seats away that an owner set by hand."""
    configured = configured_people()
    want = TIERS[tier]["people"]
    if want is None or configured == 0:
        return configured
    return 0 if want == 0 else max(configured, want)


def _stored() -> dict | None:
    try:
        from core import box_settings
        v = box_settings.get(NS, KEY, default=None)
    except Exception:                   # noqa: BLE001 — unreadable: fall through, never raise
        return None
    return v if isinstance(v, dict) and v.get("tier") in TIERS else None


def current() -> dict:
    """{"tier", "name", "add", "features", "people", "seq", "source", "since"}. Never raises."""
    held = _stored()
    if held:
        tier, add, source = held["tier"], [a for a in held.get("add") or [] if a in FEATURES], "ownbox"
        seq, since = int(held.get("seq") or 0), held.get("since") or ""
    else:
        p = _provisioned()
        tier, source = (p, "provision.json") if p in TIERS else (DEFAULT, "default")
        add, seq, since = [], 0, ""
    t = TIERS[tier]
    return {"tier": tier, "name": t["name"], "add": sorted(add),
            "features": sorted(t["features"] | set(add)), "people": _people(tier),
            "seq": seq, "source": source, "since": since}


def allows(feature: str) -> bool:
    """Is `feature` in this box's plan? Paid for, not ready: readiness is the feature's own check."""
    return feature in current()["features"]


def _validate(body: dict) -> tuple:
    try:
        seq = int(body.get("seq"))
    except (TypeError, ValueError):
        raise PlanRefused("seq must be a whole number") from None
    if seq < 1:
        raise PlanRefused("seq must be 1 or more")
    tier = str(body.get("tier") or "")
    if tier not in TIERS:
        raise PlanRefused(f"unknown tier {tier[:20]!r}")
    add = body.get("add", [])
    if not isinstance(add, list) or not all(isinstance(a, str) for a in add):
        raise PlanRefused("add must be a list of feature names")
    unknown = sorted(set(add) - FEATURES)
    if unknown:
        raise PlanRefused(f"unknown feature {unknown[0][:30]!r}")
    return seq, tier, sorted(set(add))


def set_plan(body: dict) -> dict:
    """Store what Ownbox says this box's plan is. Returns current() after. Raises PlanRefused.

    Nothing is stored unless everything checks. The seq comparison and the write happen under one
    write lock, so of two messages arriving together the higher seq wins, whatever the order.
    """
    seq, tier, add = _validate(body if isinstance(body, dict) else {})
    from core import state
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with state.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT value FROM box_settings WHERE machine = ? AND key = ? AND user_id = ''",
                        (NS, KEY)).fetchone()
        held = 0
        if row:
            try:
                held = int((json.loads(row["value"]) or {}).get("seq") or 0)
            except (ValueError, TypeError, AttributeError):
                held = 0
        if seq <= held:
            raise PlanRefused(f"seq {seq} is not newer than the {held} this box holds", stale=True)
        value = json.dumps({"seq": seq, "tier": tier, "add": add, "since": now})
        c.execute("INSERT INTO box_settings (machine, key, user_id, value, set_at, set_by) "
                  "VALUES (?,?,'',?,?,?) ON CONFLICT(machine, key, user_id) DO UPDATE SET "
                  "value = excluded.value, set_at = excluded.set_at, set_by = excluded.set_by",
                  (NS, KEY, value, now, "ownbox"))
    log.info("tiers.plan_set", seq=seq, tier=tier, add=add)
    return current()
