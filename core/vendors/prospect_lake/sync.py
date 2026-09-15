"""prospects_sync — drains the outbox into `POST $MAV_INGEST_URL` (contract §3/§10).

One event per POST (v1). The signature covers the EXACT stored payload bytes:
  X-Mav-Signature:   HMAC-SHA256(raw body, AIOS_INGEST_SECRET), hex
  X-Mav-Timestamp:   unix seconds (server rejects |now-ts| > 300s — replay window)
  X-Contract-Version: 1

Outcome contract (§10):
  200                  → store returned prospect_id (locally + writeback), done.
                         A replayed key returns the SAME prospect_id — success.
  5xx / network        → attempts += 1, retried next sweep (backoff = sweep cadence).
  4xx                  → PARKED loudly (payload/contract violation — a retry can't
                         make it valid; a human fixes, then clears attempts).
Inert until MAV_INGEST_URL + AIOS_INGEST_SECRET are set. No Supabase credentials
exist on this box — the endpoint is the only write path (v3.2 §0 tiebreak).
"""
import hashlib
import hmac
import json
import time

import requests

from core.config import settings
from core.logging import get_logger

from . import outbox

log = get_logger(__name__)

_TIMEOUT = 20
_MAX_ATTEMPTS = 8


def is_configured() -> bool:
    return bool(getattr(settings, "mav_ingest_url", "")
                and getattr(settings, "aios_ingest_secret", ""))


def _headers(body: str) -> dict:
    secret = settings.aios_ingest_secret.encode()
    return {
        "Content-Type": "application/json",
        "X-Mav-Signature": hmac.new(secret, body.encode(), hashlib.sha256).hexdigest(),
        "X-Mav-Timestamp": str(int(time.time())),
        "X-Contract-Version": "1",
    }


def _deliver(row: dict) -> tuple[bool, str]:
    """POST one outbox row. Returns (done, note). done=True marks the row sent."""
    body = row["payload"]                      # sign the STORED bytes, never re-render
    try:
        resp = requests.post(settings.mav_ingest_url, data=body.encode(),
                             headers=_headers(body), timeout=_TIMEOUT)
    except requests.exceptions.RequestException as e:
        outbox.mark_attempt(row["id"], f"network: {type(e).__name__}: {e}")
        return False, "network"
    if resp.status_code == 200:
        try:
            pid = (resp.json() or {}).get("prospect_id")
        except ValueError:
            pid = None
        if not pid:
            # A 200 without a prospect_id is a contract violation — park loudly
            # rather than mark sent with nothing to mint ?pid= links from.
            outbox.mark_attempt(row["id"], f"200 without prospect_id: {resp.text[:120]}",
                                park=True, max_attempts=_MAX_ATTEMPTS)
            return False, "shape"
        # ORDER MATTERS: write the prospect_id back to the local row FIRST, THEN
        # mark the outbox row sent. Sent-first diverges on a crash — the row is
        # never revisited yet gtm_leads.prospect_id stays NULL, so every later
        # email for that lead mints CTA links with no ?pid= and nothing replays the
        # writeback. Writeback-first CONVERGES: a crash before mark_sent leaves the
        # row unsent → next sweep re-POSTs → the server's dedupe_key returns the
        # SAME prospect_id → the idempotent writeback + mark_sent complete.
        if row.get("local_table") and row.get("local_id"):
            try:
                outbox.writeback_prospect_id(row["local_table"], row["local_id"], pid)
            except Exception as e:  # noqa: BLE001 — e.g. SQLite busy past the timeout
                # Do NOT mark_sent: keep the row for a retry (loud, backed-off, and
                # parkable) rather than silently losing the stitch forever.
                outbox.mark_attempt(row["id"], f"writeback: {type(e).__name__}: {e}")
                return False, "writeback"
        outbox.mark_sent(row["id"], pid)
        return True, "ok"
    if resp.status_code in (401, 403, 429):
        # Auth failure (wrong / rotated / absent secret) or rate limit — NOT a per-row rejection.
        # Record but do NOT count toward the park cap, so a bad-secret window can never PERMANENTLY
        # drop prospect events; every row drains the instant the correct secret lands.
        outbox.mark_attempt(row["id"], f"HTTP {resp.status_code}: {resp.text[:160]}", count=False)
        log.warning("prospect_lake.auth_retry", key=row["idempotency_key"],
                    status=resp.status_code, note="secret/rate — retrying, not parked")
        return False, "auth"
    if 400 <= resp.status_code < 500:
        # A genuine per-row rejection (bad shape, dup, validation) — park; it won't fix itself.
        outbox.mark_attempt(row["id"], f"HTTP {resp.status_code}: {resp.text[:200]}",
                            park=True, max_attempts=_MAX_ATTEMPTS)
        log.warning("prospect_lake.parked_4xx", key=row["idempotency_key"],
                    status=resp.status_code, body=resp.text[:160])
        return False, "parked"
    outbox.mark_attempt(row["id"], f"HTTP {resp.status_code}: {resp.text[:200]}")
    return False, "5xx"


def run_sync() -> dict:
    """Worker periodic: drain the unsent outbox, oldest first. Stops the batch on
    the first network/5xx failure (the endpoint is down — the rest can wait a
    sweep rather than burn 25 failing POSTs)."""
    if not is_configured():
        return {"skipped": "unconfigured"}
    rows = outbox.unsent(limit=25, max_attempts=_MAX_ATTEMPTS)
    if not rows:
        return {"sent": 0}
    sent = parked = 0
    for row in rows:
        done, note = _deliver(row)
        if done:
            sent += 1
        elif note == "parked" or note == "shape":
            parked += 1
        else:                      # network / 5xx — endpoint unhealthy, stop the batch
            log.info("prospect_lake.sync_backoff", after=sent, note=note)
            break
    if sent or parked:
        log.info("prospect_lake.synced", sent=sent, parked=parked, batch=len(rows))
    return {"sent": sent, "parked": parked, "batch": len(rows)}
