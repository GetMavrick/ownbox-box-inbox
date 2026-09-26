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
import subprocess
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
    "pro":    {"name": "Base Machine Pro", "features": frozenset({"coworkers", "machine:aeo", "machine:inbox"}),
               "people": 0},
}
# ADD-ON MACHINES ARE FEATURES TOO (SCOPE_TIERS §2.7). Base includes neither and gets the one it was
# bought with through `add`; Pro includes both. The feature is `machine:<slug>`, and which of the
# box's modules make up that machine is DATA, config `machine_features:`, so core never names a
# machine's package. A module no feature claims is not an add-on and always loads.
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
    """The row Ownbox last wrote, read straight from the database, NEVER through the config.
    `box_settings.get` falls back to the config when the row is absent, and the config merges the
    packs, which ask `features()` whether their `needs:` are met (core/packs.py): reading the plan
    through it would recurse on every box Ownbox has not told yet. Only Ownbox writes a plan."""
    try:
        from core import state
        with state.connect() as c:
            row = c.execute("SELECT value FROM box_settings WHERE machine = ? AND key = ? AND user_id = ''",
                            (NS, KEY)).fetchone()
        v = json.loads(row["value"]) if row else None
    except Exception:                   # noqa: BLE001 — unreadable: fall through, never raise
        return None
    return v if isinstance(v, dict) and v.get("tier") in TIERS else None


def _plan() -> tuple:
    """(tier, add, source, seq, since) — current() without the seat count, which reads the config."""
    held = _stored()
    if held:
        return (held["tier"], [a for a in held.get("add") or [] if a in FEATURES], "ownbox",
                int(held.get("seq") or 0), held.get("since") or "")
    p = _provisioned()
    tier, source = (p, "provision.json") if p in TIERS else (DEFAULT, "default")
    return tier, [], source, 0, ""


def features() -> frozenset:
    """The features this box's plan includes. Never raises, and never reads the config, so the
    config's own loading may call it (core/packs.py, `needs:`)."""
    try:
        tier, add, *_ = _plan()
        return TIERS[tier]["features"] | frozenset(add)
    except Exception:                   # noqa: BLE001
        return TIERS[DEFAULT]["features"]


def current() -> dict:
    """{"tier", "name", "add", "features", "people", "seq", "source", "since"}. Never raises."""
    tier, add, source, seq, since = _plan()
    t = TIERS[tier]
    return {"tier": tier, "name": t["name"], "add": sorted(add),
            "features": sorted(t["features"] | set(add)), "people": _people(tier),
            "seq": seq, "source": source, "since": since}


def needs(wanted) -> tuple[bool, str]:
    """(ok, why) for a machine that says `needs: [...]` (core/packs.py). `why` names the first tier
    that includes every feature wanted, as a person reads it: "needs Base Machine Pro"."""
    missing = [f for f in wanted if f not in features()]
    if not missing:
        return True, ""
    tier = next((t["name"] for t in TIERS.values() if set(wanted) <= t["features"]), "")
    return False, (f"needs {tier}" if tier else "needs " + ", ".join(missing) + " added to this box's plan")


def allows(feature: str) -> bool:
    """Is `feature` in this box's plan? Paid for, not ready: readiness is the feature's own check."""
    return feature in current()["features"]


def _machine_map() -> dict:
    """{feature: [module prefix, ...]} from config `machine_features:`, keeping only features the
    table knows. Read per call; a config that cannot be read claims nothing."""
    try:
        from core.config import get_config
        raw = get_config().get("machine_features") or {}
    except Exception:                   # noqa: BLE001 — unreadable config: no module is an add-on
        return {}
    if not isinstance(raw, dict):
        return {}
    return {f: [str(p) for p in (v or []) if isinstance(p, str) and p]
            for f, v in raw.items() if f in FEATURES and f.startswith("machine:") and isinstance(v, list)}


def machine_of(path: str) -> str:
    """The `machine:<slug>` feature that `path` (a dotted module path) belongs to, or "".

    A module belongs to a machine when it is one of the machine's prefixes or sits under one, on
    whole components: `a.b` claims `a.b` and `a.b.c`, never `a.bc`. The longest prefix wins.
    """
    path, best, won = str(path or ""), "", ""
    for feature, prefixes in _machine_map().items():
        for p in prefixes:
            if (path == p or path.startswith(p + ".")) and len(p) > len(best):
                best, won = p, feature
    return won


def module_on(path: str) -> bool:
    """Does this box load the module at `path`? Never raises.

    True for anything that is not part of an add-on machine. For a machine's module: true until
    Ownbox has told this box its plan (SCOPE_TIERS §2.7: never switch something off because Ownbox
    hasn't spoken yet), and then exactly when the plan includes that machine's feature.

    Only LOADING is decided here. A machine that is off keeps every row and file it ever wrote, so
    buying it later turns it back on with everything intact.
    """
    try:
        feature = machine_of(path)
        if not feature:
            return True
        c = current()
        return c["source"] != "ownbox" or feature in c["features"]
    except Exception:                   # noqa: BLE001 — a question we cannot answer switches nothing off
        return True


def modules_on(paths) -> list:
    """`paths` without the modules of an add-on machine this box doesn't have, order kept."""
    return [p for p in (paths or []) if module_on(p)]


def machines_held(plan: dict | None = None) -> list:
    """The `machine:*` features a plan switches on, sorted, for comparing one plan with the next.
    A box never told its plan holds every machine (§2.7), so its answer is all of them."""
    c = plan if plan is not None else current()
    if c.get("source") != "ownbox":
        return sorted(f for f in FEATURES if f.startswith("machine:"))
    return sorted(f for f in c.get("features") or [] if f.startswith("machine:"))


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
    """Store what Ownbox says this box's plan is. Returns current() after, plus `restarting`: True
    when the plan changed which add-on machines this box runs and a restart was asked for (§2.7).
    Raises PlanRefused.

    Nothing is stored unless everything checks. The seq comparison and the write happen under one
    write lock, so of two messages arriving together the higher seq wins, whatever the order.
    """
    seq, tier, add = _validate(body if isinstance(body, dict) else {})
    from core import state
    before = machines_held()
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
    after = current()
    after["restarting"] = machines_held(after) != before and restart_services()
    return after


# THE RESTART THAT MAKES A CHANGE IN MACHINES TAKE EFFECT (§2.7). A machine is imported at start or
# not at all, so switching one on or off needs the web process and the worker started again, once.
# A transient unit does it, a few seconds later, so the reply to Ownbox is sent before this process
# goes. It waits for the update lock, so it never restarts services in the middle of an update, and
# a second change while one is pending is covered by the one already waiting (the unit name is fixed).
RESTART_UNIT = "aios-plan-restart"
RESTART_SERVICES = ("aios-dispatch", "aios-worker")


def restart_services(*, run=subprocess.run) -> bool:
    """Ask systemd to restart the box's long-running services once. True if it was asked.

    Never under a hermetic test run, whatever `run` is: a suite run on a live box must not restart it.
    A test that means to reach systemd passes its own `run`."""
    import os

    from core.coworkers import locks
    if os.environ.get("AIOS_HERMETIC_TEST") and run is subprocess.run:
        log.info("tiers.restart_skipped_in_test")
        return False
    try:
        r = run(["systemd-run", f"--unit={RESTART_UNIT}", "--collect", "--quiet", "--on-active=5",
                 "flock", "-w", "3600", locks.DEPLOY_LOCK, "systemctl", "try-restart", *RESTART_SERVICES],
                capture_output=True, stdin=subprocess.DEVNULL, timeout=15)
    except Exception as e:              # noqa: BLE001 — not a systemd box (a test, a laptop): say so
        log.warning("tiers.restart_unavailable", error=f"{type(e).__name__}: {str(e)[:200]}")
        return False
    err = (r.stderr or b"")[:300].decode(errors="replace") if isinstance(r.stderr, bytes) else str(r.stderr or "")
    if r.returncode != 0 and "already" in err:
        log.info("tiers.restart_already_pending", unit=RESTART_UNIT)
        return True                     # one is waiting, and it starts after this change: covered
    if r.returncode != 0:
        log.warning("tiers.restart_refused", code=r.returncode,
                    detail=err[:200])
        return False
    log.info("tiers.restart_scheduled", unit=RESTART_UNIT, services=list(RESTART_SERVICES))
    return True
