"""prospects_outbox — the local, durable half of the lake write path.

Every intake event (GTM lead created, reply classified, opt-out, later IG/
Messenger) becomes ONE outbox row, written in the CALLER's transaction when a
connection is passed (contract §10: "written in the same transaction as the
intake job"). The row carries the fully-rendered §3 payload JSON — rendered at
enqueue time so the sync worker signs and sends EXACTLY the stored bytes (the
HMAC covers the raw body; re-rendering at send time could drift).

Exactly-once, twice over:
  - locally: `idempotency_key UNIQUE` + INSERT OR IGNORE → a re-run of the same
    intake (job requeue, sweep re-read) can never enqueue the same event twice;
  - remotely: the same key travels as the server's dedupe_key — a replayed POST
    returns the same prospect_id with no duplicate touch (treated as success).
"""
import json
import uuid
from datetime import datetime, timezone

from core import state
from core.logging import get_logger
from . import touch_utm

log = get_logger(__name__)

# Tables the sync worker may write a resolved prospect_id back onto. Allowlist,
# not convention — the writeback is raw SQL by design (no updated_at bump, so
# mirrors never see their own writeback as a change).
WRITEBACK_TABLES = frozenset({"gtm_leads"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def enqueue(*, key: str, identities: list[dict], touch: dict,
            person: dict | None = None, enrichment: dict | None = None,
            stage_hint: str | None = None, pid: str | None = None,
            local_table: str | None = None, local_id: str | None = None,
            conn=None) -> bool:
    """Queue one §3 ingest event. Returns True if enqueued, False if the key was
    already queued (idempotent no-op). `key` must be DETERMINISTIC for the
    logical event (e.g. 'gtm-create:<lead_id>', 'gtm-replied:<msgid>').

    caller='aios' may only hint cold/engaged — advance_stage rejects more (§2);
    refuse it here too so a bug can't even leave the box.
    """
    if stage_hint not in (None, "cold", "engaged"):
        raise ValueError(f"stage_hint {stage_hint!r} is not AIOS's lane (§2 caller matrix)")
    identities = [i for i in (identities or []) if i.get("kind") and i.get("value")]
    if not identities and not pid:
        log.warning("prospect_lake.enqueue_no_identity", key=key)
        return False
    if local_table and local_table not in WRITEBACK_TABLES:
        raise ValueError(f"writeback table {local_table!r} not allowlisted")
    touch = dict(touch or {})
    touch.setdefault("occurred_at", _now_iso())
    if touch.get("utm"):
        touch_utm.assert_touch_utm(touch["utm"])  # fail-closed: a malformed utm can't leave the box
    idem = f"outbox:{key}" if not key.startswith("outbox:") else key
    payload = {
        "idempotency_key": idem,
        "pid": pid,
        "identities": identities,
        "person": person or {},
        "enrichment": enrichment or {},
        "touch": touch,
        "stage_hint": stage_hint,
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    sql = ("INSERT OR IGNORE INTO prospects_outbox "
           "(id, idempotency_key, payload, local_table, local_id, created_at, attempts) "
           "VALUES (?,?,?,?,?,?,0)")
    args = (str(uuid.uuid4()), idem, body, local_table, local_id, state._now())
    if conn is not None:
        new = conn.execute(sql, args).rowcount > 0
    else:
        with state.connect() as c:
            new = c.execute(sql, args).rowcount > 0
    if new:
        log.info("prospect_lake.enqueued", key=idem,
                 channel=touch.get("channel"), kind=touch.get("kind"))
    return new


def unsent(limit: int = 25, max_attempts: int = 8) -> list[dict]:
    """Oldest-first batch of rows still to deliver. Rows at/over max_attempts are
    PARKED (visible via `error`, never silently dropped) until a human looks."""
    with state.connect() as c:
        rows = c.execute(
            "SELECT * FROM prospects_outbox WHERE sent_at IS NULL AND attempts < ? "
            "ORDER BY created_at ASC LIMIT ?", (max_attempts, limit)).fetchall()
    return [dict(r) for r in rows]


def mark_sent(row_id: str, prospect_id: str | None) -> None:
    with state.connect() as c:
        c.execute("UPDATE prospects_outbox SET sent_at = ?, prospect_id = ?, error = NULL "
                  "WHERE id = ?", (state._now(), prospect_id, row_id))


def mark_attempt(row_id: str, error: str, *, park: bool = False,
                 count: bool = True, max_attempts: int = 8) -> None:
    """Record a failed delivery attempt.

    park=True  — terminal 4xx (payload/contract is wrong, retrying can't fix it): jump attempts
                 to the cap so the row waits for a human instead of hammering the endpoint.
    count=False — transient AUTH/rate failure (a wrong/rotated/absent secret, or 429): record the
                 error but DON'T increment, so the row keeps retrying every sweep and can NEVER
                 permanently park during a bad-secret window — it drains the instant the correct
                 secret lands. (The default count=True is ordinary 5xx/network backoff.)
    """
    with state.connect() as c:
        if park:
            c.execute("UPDATE prospects_outbox SET attempts = ?, error = ? WHERE id = ?",
                      (max_attempts, error[:300], row_id))
        elif count:
            c.execute("UPDATE prospects_outbox SET attempts = attempts + 1, error = ? WHERE id = ?",
                      (error[:300], row_id))
        else:
            c.execute("UPDATE prospects_outbox SET error = ? WHERE id = ?",
                      (error[:300], row_id))


def writeback_prospect_id(local_table: str, local_id: str, prospect_id: str) -> None:
    """Store the resolved prospect_id on the local record (§10: it's what ?pid=
    links are minted from). Raw SQL on an allowlisted table, no updated_at bump."""
    if local_table not in WRITEBACK_TABLES:
        raise ValueError(f"writeback table {local_table!r} not allowlisted")
    with state.connect() as c:
        c.execute(f"UPDATE {local_table} SET prospect_id = ? WHERE id = ?",  # noqa: S608 — allowlisted
                  (prospect_id, local_id))
