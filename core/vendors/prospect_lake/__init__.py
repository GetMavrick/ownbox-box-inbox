"""Prospect Lake — AIOS's write path into `marketing.prospects` (the People lake).

Contract: docs/PROSPECTS_CONTRACT.md (v3.2, §6 + §10 — the AIOS build card).
AIOS never holds DB credentials; the lake is written through ONE versioned HTTP
surface (`POST $MAV_INGEST_URL`) with an HMAC-signed body. Intake writes a local
OUTBOX row first (same transaction as the intake job — AIOS stays fully
functional if the endpoint is down); a periodic sync worker drains it with
exactly-once semantics (the outbox idempotency key IS the server's dedupe_key;
a replayed key returns the same prospect_id and is treated as success).

Inert until MAV_INGEST_URL + AIOS_INGEST_SECRET are set (house convention).
"""
from core.worker import register_periodic

from . import sync

# Drain every 2 min — prospects aren't seconds-critical, and a failed POST just
# waits one sweep. Cheap no-op when the outbox is empty or the env is unset.
register_periodic(sync.run_sync, interval_s=120, name="prospects_sync")
