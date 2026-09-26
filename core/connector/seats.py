"""Seat identity: mint, verify, revoke, and the audit row for every call.

THE CREDENTIAL IS `<seat_id>.<secret>` — a public handle and a secret, sent as one bearer string.
The handle is what makes this a lookup by primary key instead of a scan over every row comparing
hashes: one indexed read, then one constant-time compare. It is also what makes the audit trail
readable, because `seat_abc123` can be logged, printed in an incident and pasted into a message
without leaking anything.

THE SECRET IS NEVER STORED. `mint()` returns it exactly once and only its SHA-256 goes to the
database. There is no recovery path and that is the feature: a stolen database yields no working
credential, and "I lost it" is answered by minting a new seat and revoking the old one, which is
a thing the owner can see happen rather than a secret being read back out of a table.

WHY NOT JUST USE THE BEARER TOKEN. `DISPATCH_BEARER_TOKEN` is also the dashboard password
(core/config.py:59) and, absent `UNSUB_SIGNING_KEY`, the key that signs every opt-out link in
mail already sent (core/compliance.py:218-221). One string, three jobs, no per-holder revocation:
rotating it to cut off one agent changes the dashboard password and invalidates unsubscribe links
a recipient may click tomorrow. That is a CAN-SPAM problem, not an inconvenience.

THE SAFETY RULE THIS MODULE ENFORCES ONE HALF OF: a seat may create work for a human to approve.
It may never BE the approval. Roles here are deliberately coarse — three of them — because the
consuming agent already carries per-tool policy, and permissions configured twice in two systems
with no single view is how a customer ends up believing something is off when it is on.
"""
import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime, timezone

from core import state
from core.logging import get_logger

log = get_logger(__name__)

# read   — read functions only; an employee who should see, not touch
# act    — reads plus typed actions that QUEUE work for approval; a trusted operator
# service— as act, higher ceiling; the agent
#
# NO ROLE SPENDS MONEY, PUBLISHES, OR SENDS. That is not enforced by this tuple — it is enforced
# by never building such a function behind a seat — but the tuple is where somebody adding a
# fourth role will look, so the rule is written here where they will read it.
ROLES = ("read", "act", "service")

_SECRET_BYTES = 32          # 256 bits from secrets.token_urlsafe; not a guessable string
# `unknown` = no such tool on this box. NOT a flavour of `denied`: probing for tools a box does
# not have is reconnaissance, being refused one it does have is policy working, and an incident
# has to tell them apart. THIS tuple is the one that decides — record() coerces anything outside
# it to "error", so adding an outcome in the registry alone silently files it as a failure.
_OUTCOMES = ("ok", "denied", "error", "unknown")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def mint(label: str, role: str, capabilities=None) -> tuple[str, str]:
    """Create a seat. Returns (seat_id, credential). The credential is shown ONCE, never again.

    The caller is responsible for printing it and for not logging it. Nothing here writes the
    secret anywhere: the row gets its hash, and the plaintext exists only in the return value.

    `capabilities` makes a RUN SEAT: one coworker's shift, holding exactly that list
    (docs/SCOPE_SHIFTS.md §4.1). Every entry must be one an AI may hold, `tools.ai_may_hold()`,
    and anything else is refused here, by name, rather than quietly dropped: a coworker file asking
    for `act:send_email` is a mistake its author needs to hear about, not a grant that vanishes.
    """
    from core.connector import tools

    label = (label or "").strip()
    if not label:
        raise ValueError("a seat needs a label — an unlabelled seat is unauditable")
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}, got {role!r}")
    caps = None
    if capabilities is not None:
        if isinstance(capabilities, str) or not all(isinstance(x, str) for x in capabilities):
            raise ValueError("capabilities must be a list of names like 'read:inbox'")
        caps = sorted(set(capabilities))
        refused = [x for x in caps if not tools.ai_may_hold(x)]
        if refused:
            raise ValueError(f"a run seat may not hold {', '.join(refused)}: an AI holds read: "
                             f"capabilities and write:proposals, never act:")

    seat_id = "seat_" + secrets.token_hex(8)
    secret = secrets.token_urlsafe(_SECRET_BYTES)
    # ONE TRANSACTION. A run seat whose list failed to land would verify with its ROLE's
    # capabilities, which is wider than it was granted. Both rows or neither.
    with state.connect() as c:
        c.execute(
            "INSERT INTO seats (id, label, role, secret_hash, created_at) VALUES (?,?,?,?,?)",
            (seat_id, label, role, _hash(secret), _now()),
        )
        if caps is not None:
            c.execute("INSERT INTO seat_capabilities (seat_id, capabilities) VALUES (?,?)",
                      (seat_id, json.dumps(caps)))
    # The label and role are safe to log; the credential is not, and is not passed here.
    log.info("connector.seat_minted", seat_id=seat_id, label=label, role=role, capabilities=caps)
    return seat_id, f"{seat_id}.{secret}"


def _capabilities(raw):
    """A stored list, or () if the row is unreadable. Never None for a run seat.

    An unreadable list must FAIL CLOSED to nothing. Falling back to None would hand the seat its
    role's capabilities, which is the one outcome worse than the seat not working.
    """
    try:
        v = json.loads(raw)
    except (TypeError, ValueError):
        return ()
    return tuple(x for x in v if isinstance(x, str)) if isinstance(v, list) else ()


def verify(credential: str) -> dict | None:
    """Resolve a credential to its seat row, or None. Fail-closed on every malformed input.

    Returns None — never raises — because this runs on the request path and an exception here
    would be a 500 where a 401 belongs. A caller cannot tell WHY it failed, deliberately: an
    error that distinguishes 'no such seat' from 'wrong secret' is an oracle for enumerating
    seat ids.
    """
    if not credential or not isinstance(credential, str):
        return None
    seat_id, sep, secret = credential.partition(".")
    if not sep or not seat_id.startswith("seat_") or not secret:
        return None
    try:
        with state.connect() as c:
            row = c.execute(
                "SELECT s.id, s.label, s.role, s.secret_hash, s.revoked_at, "
                "k.seat_id AS run_seat, k.capabilities FROM seats s "
                "LEFT JOIN seat_capabilities k ON k.seat_id = s.id WHERE s.id = ?",
                (seat_id,),
            ).fetchone()
    except Exception as e:                      # noqa: BLE001 — a DB error must not 500 the door
        log.error("connector.seat_lookup_failed", error=f"{type(e).__name__}: {e}")
        return None
    if row is None:
        return None
    # CONSTANT-TIME, and it still runs for a revoked seat. Returning early on `revoked_at` would
    # make a revoked id measurably faster to reject than a live one with a wrong secret.
    good = hmac.compare_digest(row["secret_hash"], _hash(secret))
    if not good or row["revoked_at"]:
        return None
    seat = {"id": row["id"], "label": row["label"], "role": row["role"]}
    if row["run_seat"] is not None:
        seat["capabilities"] = _capabilities(row["capabilities"])
    return seat


def touch(seat_id: str) -> None:
    """Record that a seat was seen. Best-effort and deliberately unguarded by the caller.

    One row per call is the write budget on this box — SQLite is WAL with one writer and a
    five-second busy timeout, so an extra write per request is a real cost. This is the cheap
    one; the audit row is the one that earns its lock.
    """
    try:
        with state.connect() as c:
            c.execute("UPDATE seats SET last_seen_at = ? WHERE id = ?", (_now(), seat_id))
    except Exception as e:                      # noqa: BLE001
        log.warning("connector.seat_touch_failed", seat_id=seat_id, error=type(e).__name__)


def revoke(seat_id: str) -> bool:
    """Soft-delete a seat. Returns False if there was no live seat by that id.

    SOFT, because seat_actions rows must keep resolving to a label. An audit trail whose actor
    has been deleted answers nothing, and 'who did this' is the only question it ever gets.
    Re-revoking an already-revoked seat is a no-op that returns False rather than moving the
    timestamp — the first revocation is the one that matters.
    """
    with state.connect() as c:
        cur = c.execute(
            "UPDATE seats SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (_now(), seat_id),
        )
        changed = cur.rowcount > 0
    log.info("connector.seat_revoked", seat_id=seat_id, changed=changed)
    return changed


def all_seats(include_revoked: bool = True, include_runs: bool = False) -> list[dict]:
    """Every seat, newest first. Never returns a secret or a hash — there is nothing to show.

    RUN SEATS ARE LEFT OUT unless asked for. One is minted per shift and revoked at its end, two
    a weekday for one coworker, and the screen that lists what the owner connected would be
    buried under them within a month. A run's record is its receipt, not this list.
    """
    where = [] if include_revoked else ["revoked_at IS NULL"]
    if not include_runs:
        where.append("id NOT IN (SELECT seat_id FROM seat_capabilities)")
    sql = ("SELECT id, label, role, created_at, last_seen_at, revoked_at FROM seats"
           + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY created_at DESC")
    with state.connect() as c:
        return [dict(r) for r in c.execute(sql).fetchall()]


# NOT a seat id, and it cannot collide with one: real ids are "seat_<hex>". A row carrying this
# means a caller reached the ledger without a verified seat, which is a DEFECT, and the row says
# so instead of naming an actor that never existed.
ORPHAN = "!orphan"


def record(seat_id: str, tool: str, *, outcome: str, args: dict | None = None,
           job_id: str | None = None) -> None:
    """Write one audit row. `denied` rows are the point — a ledger of successes shows nothing.

    `args` is what the validator ACCEPTED, never the raw request body: the body can carry
    anything a caller chose to send, including a credential someone pasted into the wrong field,
    and this table is read by people during incidents.

    NEVER INVENTS AN ACTOR. The first version wrote `seat_id or "unknown"`, which pairs badly with
    the fact that `seat_actions.seat_id` has NO FOREIGN KEY: a missing seat produced a row that
    looked like a real one, referencing a seat that does not exist, and nothing anywhere rejected
    it. Raised by OSDev1, 2026-09-12, as the second half of the fail-open transport bug.

    WHY THE ROW IS STILL WRITTEN. Dropping it would be worse: an audit trail that goes quiet
    exactly when something is wrong is failing at the one job it has. So the row lands, marked
    ORPHAN — which can never be mistaken for a seat — and logged at ERROR so it is loud rather
    than silent. Silent was the actual complaint.
    """
    if not seat_id:
        log.error("connector.audit_orphan_no_seat", tool=tool, outcome=outcome)
        seat_id = ORPHAN
    if outcome not in _OUTCOMES:
        outcome = "error"
    try:
        with state.connect() as c:
            c.execute(
                "INSERT INTO seat_actions (id, seat_id, at, tool, args_json, outcome, job_id) "
                "VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), seat_id, _now(), tool,
                 json.dumps(args, sort_keys=True) if args else None, outcome, job_id),
            )
    except Exception as e:                      # noqa: BLE001 — never fail a request on the log
        log.error("connector.audit_write_failed", seat_id=seat_id, tool=tool,
                  error=f"{type(e).__name__}: {e}")
