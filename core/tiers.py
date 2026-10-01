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
import os
import subprocess
from datetime import datetime, timezone

from core.logging import get_logger

log = get_logger(__name__)

# THE ONE TABLE. `id` is what orders and boxes store and never changes (Base's id is "ownbox", the
# brand); `name` is what a person reads: "Base Machine" and "Base Machine Pro" (owner, 2026-09-26,
# matching the pricing page and the Stripe products). `people` is the seat limit (owner, 2026-09-16:
# Base is three people, Pro is unlimited), 0 is unlimited. ON A BOX OWNBOX BUILT THE PLAN DECIDES IT,
# never the config (docs/PLAN_TIER_INTEGRITY.md step 1, owner 2026-09-30): `my/settings.yaml` may
# override any setting, so a limit read from the config was one line away from Pro. See `_people`.
# ADD-ON MACHINES ARE FEATURES TOO (SCOPE_TIERS §2.7), and they DESCRIBE THEMSELVES (core/machines.py,
# #1665 §3.3): each machine's own machine.yaml names its feature `machine:<slug>`, the tiers that
# include it and the modules that make it up, so this table names no machine and a new machine never
# edits it. Base includes none and gets the one it was bought with through `add`.
from core import machines as _machines

TIERS = {
    "ownbox": {"name": "Base Machine", "features": frozenset() | _machines.in_tier("ownbox"), "people": 3},
    "pro":    {"name": "Base Machine Pro", "features": frozenset({"coworkers"}) | _machines.in_tier("pro"),
               "people": 0},
}
DEFAULT = "ownbox"
# Every feature a plan may name: the tiers' own, plus every add-on machine found on this box.
# Anything else is refused.
FEATURES = frozenset().union(*(t["features"] for t in TIERS.values())) | _machines.features()

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


def built_by_ownbox() -> bool:
    """Did Ownbox build this box? True once it holds a plan Ownbox sent, or has a provision.json
    (every box the provisioner builds does, tier or not). False on the owner's own box, a dev
    checkout or a box installed by hand: nobody sold those, so their config still decides seats."""
    if _stored():
        return True
    try:
        from core import claim
        return os.path.exists(claim.PROVISION_JSON)
    except Exception:                   # noqa: BLE001 — cannot tell: treat it as built, the safe side
        return True


def configured_people() -> int:
    """`dash.max_users` from the box's config, 0 or absent = unlimited. Read per call, never bound."""
    from core.config import get_config
    try:
        return max(0, int((get_config().get("dash") or {}).get("max_users", 0) or 0))
    except Exception:                   # noqa: BLE001 — a junk value must not decide a seat
        return 0


def _people(tier: str) -> int:
    """This box's seat limit, 0 = unlimited.

    ON A BOX OWNBOX BUILT, THE PLAN'S NUMBER AND NOTHING ELSE (PLAN_TIER_INTEGRITY step 1). A
    `dash.max_users: 50` line in `my/settings.yaml` leaves Base at three. The config cannot even lower
    it: the tracked config ships `3` to every box, so a "lower only" rule would hold a Pro box, or a
    future five-person tier, at a number nobody chose.

    NOBODY LOSES ACCESS. This number is read only where a person is ADDED (core/state.add_user), so
    a box already carrying more people than its plan keeps every one of them, and only the next one
    waits for the upgrade (rule 1: never cut anyone off).

    A BOX NOBODY SOLD keeps its configured number, as before: the owner's own box and a dev checkout
    have no plan, and must not find their team capped by a release."""
    if not built_by_ownbox():
        return configured_people()
    return TIERS[tier]["people"]


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


# ── signed plans (docs/PLAN_TIER_INTEGRITY.md, step 3 contract, phase B) ─────────────────────────────
#
# ONCE THIS BOX IS ARMED (a release shipped `config/ownbox_plan_signers`) AND HOLDS A SIGNED PLAN, THE
# PLAN IS THE SIGNED TEXT AND NOTHING ELSE. The row keeps `signed: {text, sig}` beside the plain fields,
# and the plain fields are never read: editing them does nothing, and editing the text breaks the
# signature. Unarmed, or holding a plan from before signing, the row is read exactly as it always was.

def _from_signed(held: dict) -> dict | None:
    """The plan a stored `signed` value proves, as {tier, add, seq, since, until}; None if forged.

    FORGED (a bad signature, another box's plan) is treated as no plan at all: the box falls back to
    what it was built as and says so in its next check-in. UNVERIFIABLE (no ssh-keygen, a timeout) keeps
    the plan the text says, unproven, because nothing is known against it (rule 2: never downgrade a
    paying box because a check failed)."""
    from core import plan_signing
    result, plan, why = plan_signing.verify(held.get("signed"))
    if result == plan_signing.FORGED:
        log.warning("tiers.signed_plan_refused", why=why)
        return None
    if plan is None:                                     # unverifiable: read the text, unproven
        try:
            plan = json.loads(held["signed"]["text"])
        except (KeyError, TypeError, ValueError):
            return None
    if not isinstance(plan, dict) or plan.get("tier") not in TIERS:
        return None
    return {"tier": plan["tier"], "add": plan.get("add") or [], "seq": plan.get("seq") or 0,
            "since": held.get("since") or "", "until": plan.get("until")}


def _trial_over(until) -> bool:
    """Has a trial plan's `until` passed? The box's clock is only the offline fallback: at `until`
    Ownbox issues a Base plan at seq + 1, which the next check-in reply delivers (contract point 3)."""
    if not until:
        return False
    try:
        end = datetime.fromisoformat(str(until).replace("Z", "+00:00"))
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= end
    except ValueError:
        return False


def _held() -> dict | None:
    """The plan this box holds from Ownbox, or None: the signed text when there is one and the box is
    armed, else the row as it always was."""
    held = _stored()
    if not held:
        return None
    if held.get("signed") is not None:
        from core import plan_signing
        if plan_signing.armed():
            return _from_signed(held)
    return held


def verified() -> bool | None:
    """For the check-in's `plan.verified`: None on a box not yet armed (it says nothing new); True when
    it holds a plan whose signature checks out; False otherwise, which Ownbox reads as "re-send"."""
    from core import plan_signing
    if not plan_signing.armed():
        return None
    held = _stored()
    if not held or held.get("signed") is None:
        return False
    return plan_signing.verify(held["signed"])[0] == plan_signing.OK


def _plan() -> tuple:
    """(tier, add, source, seq, since) — current() without the seat count, which reads the config."""
    held = _held()
    if held:
        tier = DEFAULT if _trial_over(held.get("until")) else held["tier"]
        return (tier, [a for a in held.get("add") or [] if a in FEATURES], "ownbox",
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
        # A MACHINE THAT NEEDS A FEATURE this plan lacks doesn't load, even on a box Ownbox never
        # told (the same rule as a pack's `needs:`, core/packs.py). A machine with no needs is unchanged.
        wanted = _machines.needs_of(feature)
        if wanted and not needs(wanted)[0]:
            return False
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


def _check_signed(body: dict) -> dict | None:
    """On an armed box: {"plan", "signed"} for a body carrying a good `signed` plan, None for an unsigned
    body the box may still accept. Raises PlanRefused for a forged or unverifiable signed plan (nothing
    is stored, so the box keeps what it holds), and for an unsigned body once it holds a signed plan:
    from then on only Ownbox's signature changes it (contract phase B). Unarmed, `signed` is ignored and
    every body is read exactly as before (inert until a release ships the signers)."""
    from core import plan_signing
    if not plan_signing.armed():
        return None
    if body.get("signed") is None:
        held = _stored()
        if held and held.get("signed") is not None:
            raise PlanRefused("this box holds a signed plan; only a plan signed by Ownbox changes it")
        return None
    result, plan, why = plan_signing.verify(body.get("signed"))
    if result != plan_signing.OK:
        log.warning("tiers.signed_plan_refused", result=result, why=why)
        raise PlanRefused(f"signed plan refused: {why}")
    return {"plan": plan, "signed": {"text": body["signed"]["text"], "sig": body["signed"]["sig"]}}


def set_plan(body: dict) -> dict:
    """Store what Ownbox says this box's plan is. Returns current() after, plus `restarting`: True
    when the plan changed which add-on machines this box runs and a restart was asked for (§2.7).
    Raises PlanRefused.

    Nothing is stored unless everything checks. The seq comparison and the write happen under one
    write lock, so of two messages arriving together the higher seq wins, whatever the order.
    """
    body = body if isinstance(body, dict) else {}
    signed = _check_signed(body)
    seq, tier, add = _validate(signed["plan"] if signed else body)
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
                v = json.loads(row["value"]) or {}
                # A SIGNED ROW'S SEQ IS THE SIGNED TEXT'S: the plain field beside it is editable, and a
                # huge number there would make every later plan from Ownbox look stale.
                if v.get("signed") is not None:
                    v = json.loads(v["signed"]["text"])
                held = int(v.get("seq") or 0)
                held_plan = (str(v.get("tier") or ""), sorted(set(v.get("add") or [])))
            except (ValueError, TypeError, AttributeError, KeyError):
                held, held_plan = 0, None
            # THE CHECK-IN REPLY RE-CONFIRMS THE PLAN AT THE SAME SEQ (OSDev1, phase A): a signed plan at
            # the seq held, saying exactly what is held, only replaces the signature. That is how a box
            # holding a plan from before signing gets its first signature, and how a new key re-signs
            # every box before the old one retires (contract point 1). Anything that CHANGES the plan
            # still needs seq + 1, so an equal seq can never be used to swap a plan.
            if signed and seq == held and held_plan == (tier, add):
                row_v = json.loads(row["value"]) or {}
                c.execute("UPDATE box_settings SET value = ?, set_at = ?, set_by = ? "
                          "WHERE machine = ? AND key = ? AND user_id = ''",
                          (json.dumps({"seq": seq, "tier": tier, "add": add,
                                       "since": row_v.get("since") or now, "signed": signed["signed"]}),
                           now, "ownbox", NS, KEY))
                log.info("tiers.plan_confirmed", seq=seq, tier=tier)
                return current() | {"restarting": False}
        if seq <= held:
            raise PlanRefused(f"seq {seq} is not newer than the {held} this box holds", stale=True)
        value = json.dumps({"seq": seq, "tier": tier, "add": add, "since": now,
                            **({"signed": signed["signed"]} if signed else {})})
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
