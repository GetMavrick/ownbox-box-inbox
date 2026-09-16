"""SQLite state for AIOS.

Tables: spend_ledger, jobs (with a UNIQUE idempotency_key), heartbeats, alert_state.
WAL + busy_timeout so the dispatch app, the worker, and the watchdog can all write
without 'database is locked'. Keep write transactions short — the context manager
commits and closes immediately. Back this file up with Litestream (see spec §5/§9).
"""
import hashlib
import json
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from core.config import settings
from core.logging import get_logger

log = get_logger(__name__)

# Sentinel distinguishing "checkpoint absent" from "checkpoint stored None" — a step
# whose REAL result is null (ScrapeCreators returns null for a no-speech reel) must
# not look absent, or the handler re-runs (re-spends) it on every re-entry.
MISSING = object()

# ── TABLES THAT HOLD A CREDENTIAL, declared once so nothing has to remember them ──────────
#
# WHY THIS IS A CONSTANT AND NOT A COMMENT. `scripts/export_data.py` is customer-facing — the
# buyer's "give me my data" — and it was a DENY-LIST: a table nobody named was exported in full.
# The day `box_secrets` arrived, the buyer's own Anthropic key went into the export as plaintext,
# and `box_claim`'s password hash with it. Measured by seeding one and running the export.
#
# Naming them there would have fixed that day only. Declaring them HERE, beside the schema that
# creates them, means the next person to add a secret-bearing table adds it to a list that sits
# in front of them — and the exporter also redacts credential-NAMED columns in every other table
# as a second rule, for the table nobody classified.
SECRET_TABLES = frozenset({"box_secrets", "box_claim", "user_invites"})

SCHEMA = """
CREATE TABLE IF NOT EXISTS spend_ledger (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  ts                  TEXT    NOT NULL,
  job_id              TEXT,
  task                TEXT,
  model               TEXT,
  input_tokens        INTEGER DEFAULT 0,
  output_tokens       INTEGER DEFAULT 0,
  cache_write_tokens  INTEGER DEFAULT 0,
  cache_read_tokens   INTEGER DEFAULT 0,
  cost_usd            REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_spend_ts ON spend_ledger(ts);

CREATE TABLE IF NOT EXISTS jobs (
  id               TEXT PRIMARY KEY,
  idempotency_key  TEXT UNIQUE,          -- strict idempotency for money ops
  agent_name       TEXT,
  intent           TEXT,
  raw_text         TEXT,
  slack_channel_id TEXT,
  status           TEXT NOT NULL DEFAULT 'queued',  -- queued|running|done|failed
  result           TEXT,
  error            TEXT,
  attempts         INTEGER NOT NULL DEFAULT 0,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);

-- THE MORNING REVIEW (core/report.py, docs/PLAN_MORNING_REVIEW.md §2.2). One row per machine
-- per local day. The worker rewrites today's row every fifteen minutes while final = 0 and
-- closes it once the day is over; a closed row cannot match the UPSERT's WHERE, so history is
-- immutable by construction. Never pruned: kilobytes a day on a box that stores video.
CREATE TABLE IF NOT EXISTS daily_reports (
  day         TEXT NOT NULL,          -- 'YYYY-MM-DD' in cost.timezone
  machine     TEXT NOT NULL,          -- customer_voice | content_machine | lead_machine | meters
  report_json TEXT NOT NULL,
  written_at  TEXT NOT NULL,
  final       INTEGER NOT NULL DEFAULT 0,   -- 0 = today, still moving; 1 = the day is closed
  PRIMARY KEY (day, machine)
);

-- THE TENANT OVERLAY'S OFF-BOX COPY (core/box_config_backup.py). `my/settings.yaml` is gitignored
-- by design and Litestream replicates this database only, so the overlay lived on one disk. Its
-- exact TEXT is kept here and leaves the box on the replication the restore drill already proves.
-- One row per DISTINCT content per path: an unchanged file advances verified_at, and the newest
-- verified_at is the current overlay. Never pruned; it grows only when the file changes. In
-- SCHEMA, not MIGRATIONS: a new table needs no version number.
CREATE TABLE IF NOT EXISTS box_config_backup (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  path         TEXT NOT NULL,               -- relative to the install root, e.g. my/settings.yaml
  sha256       TEXT NOT NULL,               -- of the file's bytes
  body         TEXT NOT NULL,               -- the file's exact text, comments and all
  bytes        INTEGER NOT NULL,
  valid        INTEGER NOT NULL DEFAULT 1,  -- 1 = parses as a YAML mapping; 0 = stored anyway
  source_mtime TEXT,                        -- the file's mtime at capture, UTC ISO-8601
  captured_at  TEXT NOT NULL,               -- first time this exact content was seen
  verified_at  TEXT NOT NULL,               -- last time the file on disk still matched it
  UNIQUE (path, sha256)
);
CREATE INDEX IF NOT EXISTS idx_box_config_backup_newest ON box_config_backup(path, verified_at);

CREATE TABLE IF NOT EXISTS heartbeats (
  component TEXT PRIMARY KEY,
  ts        TEXT NOT NULL,
  status    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_state (
  key            TEXT PRIMARY KEY,
  state          TEXT NOT NULL,   -- 'OK' | 'FAIL'
  since_ts       TEXT,
  last_alert_ts  TEXT
);

-- Resumability: a handler records the output of each costly/side-effecting step
-- here, keyed by (job_id, step). On a requeue (budget pause or retryable error) the
-- handler re-enters from the top, loads completed steps, and skips them — so a paused
-- reel never re-spends ScrapeCreators/HeyGen/Anthropic. See docs/HANDLER_CONTRACT.md.
CREATE TABLE IF NOT EXISTS job_checkpoints (
  job_id  TEXT NOT NULL,
  step    TEXT NOT NULL,
  value   TEXT,                 -- JSON output of the completed step
  ts      TEXT NOT NULL,
  PRIMARY KEY (job_id, step)
);

-- Vendor meter: non-Anthropic spend is measured in each vendor's native UNITS
-- (HeyGen renders, ScrapeCreators credits, ...) because their billing is credit/
-- subscription-based, not per-dollar. cost_guard.check_vendor() reads this against
-- the per-vendor monthly cap in config — "no surprise bill" covers EVERY vendor,
-- not just Claude.
--
-- idem_key makes the meter write itself EXACTLY-ONCE (INSERT OR IGNORE on the
-- unique index): a submit that times out after the vendor charged can't under-count
-- when reconcile later records it, and a double-reconcile can't over-count. It is
-- the vendor-ledger analog of jobs.idempotency_key. NULL idem_key rows (unmetered
-- adjustments) are always allowed — SQLite permits multiple NULLs in a unique index.
CREATE TABLE IF NOT EXISTS vendor_ledger (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  ts        TEXT    NOT NULL,
  vendor    TEXT    NOT NULL,
  units     REAL    NOT NULL DEFAULT 1,
  job_id    TEXT,
  idem_key  TEXT,
  note      TEXT
);
CREATE INDEX IF NOT EXISTS idx_vendor_ts ON vendor_ledger(vendor, ts);
CREATE UNIQUE INDEX IF NOT EXISTS idx_vendor_idem ON vendor_ledger(idem_key);

-- Dashboard sessions (random ids, server-side expiry). `user_id` arrives in migration 47;
-- a row with user_id NULL is the OWNER, because that is what every session on every live box
-- is on the day this ships and the owner's standing rule is that he is never locked out.
CREATE TABLE IF NOT EXISTS sessions (
  id          TEXT PRIMARY KEY,
  created_at  TEXT NOT NULL,
  expires_at  TEXT NOT NULL
);

-- ── THE PEOPLE WHO MAY SIGN IN (docs/SPEC_UNIFIED_INBOX_SCREEN_AND_AUTH.md §3.1) ──────────
-- SCHEMA, not MIGRATIONS: a brand-new table needs no version number (the rule above).
--
-- WHY THIS TABLE IS A SECURITY FIX AND NOT A FEATURE. Before it, the box had exactly one human
-- credential and four call sites answered "is this the owner?" with "does this request carry any
-- valid session?" — core/dash viewer_is_owner and privileged, machine_app.unlocked, and the
-- voice app's _admitted. Minting a receptionist a session with the mechanism that existed would
-- have made that receptionist the owner of the whole box: every lead's real name, email and
-- phone on the Lead Machine, plus the Email / Call / Add-to-campaign actions. Not of the inbox —
-- of the box. So the user has to exist before a second person can sign in, and `role` is what
-- those four call sites read.
--
-- ROLE IS DATA, NEVER A CONSTANT IN A BRANCH. The seat count and the roles a box allows are
-- tiering (docs/PRICING_AND_PACKAGING_OWNBOX.md §6), and tiering changes per box and over time.
-- A tier check compiled into an `if` is one that cannot be sold differently tomorrow — and, far
-- worse, a tier check inside a SECURITY path is a paywall that fails open the day the config is
-- missing. The limit is enforced where a user is CREATED (`add_user`), never where one is
-- ADMITTED.
--
-- active INTEGER, not a DELETE. Standing owner rule: "Don't delete anything ever." Revoking an
-- employee flips the flag, and the sends they already made keep resolving to a real name.
CREATE TABLE IF NOT EXISTS users (
  id           TEXT PRIMARY KEY,                 -- random, NEVER the email
  email        TEXT NOT NULL,                    -- lowercased at write
  name         TEXT,
  role         TEXT NOT NULL DEFAULT 'member',   -- 'owner' | 'member'
  active       INTEGER NOT NULL DEFAULT 1,       -- revoke = 0, never DELETE
  created_at   TEXT NOT NULL,
  last_seen_at TEXT,
  UNIQUE (email)
);

-- ── THE BOX IS CLAIMED ONCE, BY THE PERSON WHO BOUGHT IT (docs/SPEC_OWNBOX_DELIVERY.md) ──
-- SCHEMA, not MIGRATIONS: a brand-new table needs no version number (the rule above).
--
-- THE PROBLEM THIS SOLVES: a delivered box had no way in. Provisioning builds a droplet, points
-- a subdomain at it and emails the buyer — and then the buyer meets a password box holding a
-- password only we know. Handing him one in the email would mean Ownbox mints a box secret and
-- keeps a copy, which the delivery design refuses outright. So the box hands ITSELF over, once,
-- to whoever can prove they hold the order it was built from.
--
-- CHECK (id = 1) IS THE WHOLE "ONCE" GUARANTEE, and it is here rather than in a caller because
-- that is the difference between a rule and a hope. "Refuse every later claim for good" written
-- as an `if` in a route is two concurrent POSTs away from two owners; written as a primary key
-- constraint, the second INSERT fails inside SQLite and there is no window at all. Same shape as
-- `claim_opener` and the send ledger's UNIQUE idem_key — this codebase has paid for the lesson
-- that a read followed by a write is not a claim.
--
-- pw_hash IS A HASH AND THE PASSWORD IS NEVER STORED. scrypt from hashlib — stdlib, no new
-- dependency on a 1-vCPU box, and not hand-rolled (owner, 2026-09-12: "Never hand roll
-- anything"). The parameters travel IN the string, so raising them later cannot strand a box
-- whose owner set his password under the old ones.
--
-- order_id IS RECORDED, NOT COMPARED LATER. It is the audit answer to "which order became this
-- box", and it is already on disk in /opt/aios/provision.json; keeping it here means the answer
-- survives a droplet rebuild that loses the file. It is a Stripe Checkout Session id, not a
-- card, not a secret worth stealing — and the moment this row exists it opens nothing.
CREATE TABLE IF NOT EXISTS box_claim (
  id          INTEGER PRIMARY KEY CHECK (id = 1),  -- exactly one row on this box, ever
  claimed_at  TEXT NOT NULL,
  order_id    TEXT NOT NULL,   -- the Stripe Checkout Session id the box was built from
  user_id     TEXT NOT NULL,   -- the owner row this claim created
  email       TEXT NOT NULL,   -- what he signs in as
  pw_hash     TEXT NOT NULL,   -- scrypt$n$r$p$salt$hash — never the password
  ip          TEXT,            -- who claimed it, for the audit line
  user_agent  TEXT
);

-- ── AN INVITATION TO SIGN IN, ISSUED BY THE BOX'S OWNER (docs/DESIGN_PER_PERSON_LOGIN.md) ──
-- SCHEMA, not MIGRATIONS: a brand-new table needs no version number (the rule above).
--
-- A SECOND PERSON NEEDS A CREDENTIAL, AND NOBODY CAN BE TRUSTED TO TYPE ONE IN FOR THEM. The owner
-- invites; the link is shown to the owner ONCE; the invited person sets their own password on it.
-- No self-registration exists anywhere, for the same reason the claim link is single-use.
--
-- token_hash IS A HASH. The token in the link is 32 random bytes and is never stored: a copy of this
-- table (a backup, a restore, an export that forgot the rule) cannot be turned back into a link.
-- ONCE, AND FOR SEVEN DAYS: `used_at` is set in the same statement that checks it is still NULL, so
-- two submits cannot both set a password, and an invite nobody used goes dead on its own.
CREATE TABLE IF NOT EXISTS user_invites (
  id          TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL,
  token_hash  TEXT NOT NULL UNIQUE,  -- sha256 of the link's token, never the token
  created_by  TEXT,                  -- the owner's user id, for the audit trail
  created_at  TEXT NOT NULL,
  expires_at  TEXT NOT NULL,
  used_at     TEXT                   -- set once: redeemed, or retired by a newer invite
);

-- ── THE BOX'S OWN SECRETS, set from a screen rather than from .env ───────────────────
-- SCHEMA, not MIGRATIONS: a brand-new table needs no version number (the rule above).
--
-- WHY THIS TABLE HAD TO EXIST. A delivered box drafts nothing: `brain.backend` ships as `api`
-- (bring your own key), install.sh mints none and the provisioner passes none, so
-- `drafter/draft.py` returns {"skipped": "unconfigured"} and the box files messages in silence.
-- The buyer's only way to fix that is to give the box a key — and the ONE PLACE THEY CAN TYPE
-- IT IS A SCREEN. They have no shell, no .env and no reason to want either.
--
-- AND A SCREEN CANNOT WRITE THE ENVIRONMENT. `core/config.Settings` reads os.environ at import,
-- so a value set after boot is invisible to the running process; worse, the DRAFTER RUNS IN THE
-- WORKER, a different process from the web app entirely. Nothing the web process puts in its own
-- environment ever reaches it. The database is the only medium the two already share, which is
-- why this is a table and not a file write plus a restart.
--
-- WHOSE SECRET THIS IS, because it decides whether the row is alarming. It is the CUSTOMER'S own
-- vendor key, on a box the customer has root on, sitting beside the password to that same box
-- (`box_claim.pw_hash`). Ownbox holds no copy and has no way in. It is not a fleet credential and
-- it must never become one — OSDev1's standing rule: no static Ownbox key on a box the buyer
-- has root on.
--
-- STORED AS THE VALUE, NOT A HASH, and that is forced rather than chosen: unlike a password, the
-- box has to PRESENT this to Anthropic on every call, so a one-way hash cannot work. What
-- follows from that: it is never rendered back to any screen (the Settings row shows only that a
-- key is present), never returned by any route, and `core/logging` already scrubs key-ish values
-- from the journal.
CREATE TABLE IF NOT EXISTS box_secrets (
  name        TEXT PRIMARY KEY,   -- 'anthropic_api_key'
  value       TEXT NOT NULL,
  set_at      TEXT NOT NULL,
  set_by      TEXT                -- the users.id who typed it, for the audit answer
);

-- ── CONNECTOR SEATS (docs/PLAN_AIOS_CONNECTOR.md section 6 step 2) ────────────────────────
-- SCHEMA, not MIGRATIONS: a brand-new table needs no version number (the rule above).
--
-- WHY SEATS EXIST AT ALL, and it is not tidiness. DISPATCH_BEARER_TOKEN is not an API key. It
-- is ALSO the dashboard password (core/config.py:59 falls back to it for DASH_TOKEN) and, on a
-- box that never set UNSUB_SIGNING_KEY, it signs every opt-out link in mail already sent
-- (core/compliance.py:218-221). Handing that one string to an agent hands over all three, and
-- there is no way to revoke just that agent: rotating it changes the dashboard password and
-- invalidates unsubscribe links a recipient may click tomorrow, which is a CAN-SPAM problem
-- rather than an inconvenience. The narrow-token pattern already exists one door over --
-- /deploy has its own key for exactly this reason. Seats are that, generalised from one extra
-- token to a table.
CREATE TABLE IF NOT EXISTS seats (
  id           TEXT PRIMARY KEY,   -- 'seat_<random>'; the PUBLIC handle, safe to log and to audit
  label        TEXT NOT NULL,      -- 'Mavrick (prod)', 'Dana - ops'; what a human recognises
  role         TEXT NOT NULL,      -- read | act | service
  secret_hash  TEXT NOT NULL,      -- sha256 of the secret. The secret itself is NEVER stored
  created_at   TEXT NOT NULL,
  last_seen_at TEXT,
  -- SOFT DELETE, deliberately. A revoked seat's rows in seat_actions must keep resolving to a
  -- label, or the audit trail loses the answer to the only question it gets asked: who did this.
  revoked_at   TEXT
);

-- EVERY seat-originated call, allowed or refused. `outcome='denied'` rows are the point: a
-- credential being tried and refused is the signal that matters, and a table that only records
-- successes cannot show it.
--
-- THERE IS DELIBERATELY NO FOREIGN KEY ON seat_id, AND THAT IS NOT AN OVERSIGHT. SQLite enforces
-- foreign keys only when `PRAGMA foreign_keys=ON`, and core/state.py's connect() does not set it
-- (measured 2026-09-12: every connection reports foreign_keys = 0). A declared REFERENCES clause
-- here would therefore reject nothing while reading, to the next person, like a guarantee — which
-- is strictly worse than no constraint at all. Turning the pragma on globally would start
-- enforcing every other relationship in this schema at once on a live box, which is a decision
-- far bigger than this table.
--
-- So the integrity rule lives where it can actually run: seats.record() never invents an actor
-- (it marks a seat-less row `!orphan` and logs at ERROR), and tests/test_connector_seats.py
-- asserts that no row references a seat that does not exist.
CREATE TABLE IF NOT EXISTS seat_actions (
  id        TEXT PRIMARY KEY,
  seat_id   TEXT NOT NULL,
  at        TEXT NOT NULL,
  tool      TEXT NOT NULL,
  args_json TEXT,                  -- validated args AS ACCEPTED; never the raw request body
  outcome   TEXT NOT NULL,         -- ok | denied | error | unknown (no such tool here)
  job_id    TEXT
);
-- NOT OPTIONAL. This table is projected at ~500k rows a year and is read precisely when
-- something has gone wrong, which is the worst moment to discover it needs a full scan.
CREATE INDEX IF NOT EXISTS ix_seat_actions_seat_at ON seat_actions(seat_id, at);

-- ONE ROW. The box's own stable identity, so a caller holding seats on three boxes can tell them
-- apart and so an audit exported from one box cannot be mistaken for another's.
--
-- GENERATED ON FIRST READ, never derived from anything the box already has. A hostname changes,
-- a domain moves, the database is restored onto new hardware -- and an identity derived from any
-- of those silently becomes a different box, or worse, two boxes agree they are the same one.
-- Writing it down once is the only version that survives a restore, which is exactly the case
-- this has to survive: docs/PLAN_AIOS_CONNECTOR.md section 11.6 sells "a failed box is replaced
-- from the export in under an hour", and the replacement must still be the same box to a caller.
-- prospects_outbox IS KERNEL, NOT THE LEAD MACHINE'S — the split found this (2026-09-13): it is the
-- AIOS→engine prospect-lake seam (core/vendors/prospect_lake drains it), and the Unified Inbox
-- machine writes to it too. A table two machines and the core share lives here.
CREATE TABLE IF NOT EXISTS prospects_outbox (
        id              TEXT PRIMARY KEY,
        idempotency_key TEXT UNIQUE NOT NULL,
        payload         TEXT NOT NULL,
        local_table     TEXT,
        local_id        TEXT,
        created_at      TEXT NOT NULL,
        attempts        INTEGER NOT NULL DEFAULT 0,
        error           TEXT,
        sent_at         TEXT,
        prospect_id     TEXT
    );

-- gtm_suppression IS KERNEL, NOT THE LEAD MACHINE'S — the split found this too (2026-09-13). An
-- opt-out applies to every channel: core/compliance.py writes it and every send path — the
-- Lead Machine's mail AND the Unified Inbox's replies — checks it. A Unified Inbox image without
-- it would fail its own compliance check at send time. Keeps its historical name.
CREATE TABLE IF NOT EXISTS gtm_suppression (
        email       TEXT PRIMARY KEY,
        reason      TEXT NOT NULL,           -- unsubscribe|bounce|manual|complaint
        added_at    TEXT NOT NULL,
        note        TEXT
    );

-- PER-MACHINE MIGRATION LEVEL (the schema split, docs/SPEC_GOLDEN_DROPLET_IMPACT.md §5).
-- `PRAGMA user_version` is the KERNEL's level and walks every step number once, as it always
-- has. A machine's tagged steps (see _MIGRATION_OWNER) run only when that machine is
-- registered, and this row records how far they have been applied, so a machine that
-- registers AFTER init_db — every script that imports it late — replays exactly the steps it
-- missed and nothing twice. Bootstrapped by step 46 for boxes that predate the split.
CREATE TABLE IF NOT EXISTS schema_state (
  machine    TEXT PRIMARY KEY,
  version    INTEGER NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS connector_box (
  only_row   INTEGER PRIMARY KEY CHECK (only_row = 1),   -- the constraint IS the documentation
  box_id     TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect():
    conn = sqlite3.connect(settings.db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000;")   # set FIRST so later statements WAIT, not crash
    # The journal_mode=WAL switch needs a brief EXCLUSIVE lock that the busy_timeout handler does
    # not retry, so a concurrent fresh-clone boot (worker + dispatch + watchdog racing init_db on
    # a delete-mode DB, or a Litestream restore into delete mode) can raise 'database is locked'
    # here and crash the entrypoint. Retry it bounded; once ANY connection flips the file to WAL
    # it stays WAL for good, so this self-heals within the first fraction of a second of boot.
    for _attempt in range(50):
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            break
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() or _attempt == 49:
                raise
            time.sleep(0.1)
    conn.execute("PRAGMA synchronous=NORMAL;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ── machine schemas: a machine declares its tables the way it declares its tools ─────────────
# core/state.py holds the KERNEL tables only (above). Each machine package carries its own base
# DDL in <package>/schema.py and calls register_schema() at import; init_db() creates the kernel
# plus whatever is registered, so a golden image creates exactly the tables of the machines it
# loads — a Unified Inbox box no longer carries the Lead Machine's schema
# (docs/SPEC_GOLDEN_DROPLET_IMPACT.md §5: 56 tables shipped to every image, 28 dead on one).
#
# ORDER DOES NOT MATTER, deliberately. dispatch and the worker call init_db() BEFORE they load
# modules, and two dozen scripts import a machine after init_db() has run. So a machine that
# registers after the database is ready gets its tables created and its tagged migrations
# replayed right then, tracked per machine in schema_state — rather than requiring every
# entrypoint to be reordered and every script to remember.
#
# ADDITIVE, NEVER DESTRUCTIVE. A box that already carries every machine's tables keeps them; nothing
# here drops or renames. Step 46 records such a box as fully migrated so no step replays on it.
_SCHEMAS: dict[str, str] = {}          # machine -> base DDL, in registration order
_DB_READY = False                      # set by init_db(); registrations after it apply immediately
_SCHEMA_LOCK = threading.Lock()


def register_schema(machine: str, ddl: str) -> None:
    """Declare a machine's tables. Idempotent: re-registering the same machine is a no-op."""
    with _SCHEMA_LOCK:
        if machine in _SCHEMAS:
            return
        _SCHEMAS[machine] = ddl
        ready = _DB_READY
    if ready:
        _apply_machine(machine)
        log.info("state.schema_registered", machine=machine, when="after_init")
    else:
        log.info("state.schema_registered", machine=machine, when="before_init")


def registered_machines() -> tuple[str, ...]:
    return tuple(_SCHEMAS)


# WHICH MIGRATION STEP BELONGS TO WHICH MACHINE. Measured 2026-09-13: every step touches the tables
# of at most ONE machine, or only kernel tables (1, 3, 15), so a step is either the kernel's or one
# machine's. A tagged step runs only where its machine is registered; a kernel step runs everywhere;
# user_version walks every number regardless, exactly as before. NEVER edit a step body to make it
# tolerant — tag it here instead; the governing rule below still stands.
_MIGRATION_OWNER: dict[int, str] = {
    **{n: "content" for n in (2, 4, 5, 6, 8, 11, 12, 13, 14, 20, 21, 22, 23, 24, 25, 26, 28,
                              30, 33, 34, 35, 36, 37, 43)},
    **{n: "lead" for n in (7, 9, 10, 16, 17, 18, 19, 27, 29, 31, 32, 38, 39, 40, 41, 42, 44, 45)},
    # 48 touches inbox_conversations and inbox_send_ledger — tables only a Customer Voice box
    # carries, so the kernel pass must skip it and the machine's own replay must run it.
    48: "customer_voice",
}
# A table each machine is known by, for the one-time bootstrap of boxes that predate the split.
_MACHINE_MARKER = {"customer_voice": "voice_rails", "content": "reel_scripts", "lead": "gtm_leads"}


def _table_exists(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (name,)).fetchone() is not None


def _apply_machine(machine: str) -> None:
    """Create a machine's base tables and replay the tagged steps it has not yet had.

    Own connection and an explicit transaction, like _run_migrations: executescript() commits
    implicitly, and a half-replayed machine is worse than an unreplayed one.
    """
    ddl = _SCHEMAS[machine]
    for attempt in range(6):
        try:
            with connect() as c:
                c.executescript(ddl)
            break
        except sqlite3.OperationalError as e:
            if attempt == 5 or not any(m in str(e) for m in _SCHEMA_RACE):
                raise
            time.sleep(0.05 * (attempt + 1))
    conn = sqlite3.connect(settings.db_path, timeout=30, isolation_level=None)
    try:
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("BEGIN IMMEDIATE;")
        kernel_level = conn.execute("PRAGMA user_version").fetchone()[0]
        _replay_machine(conn, machine, kernel_level)
        conn.execute("COMMIT;")
    except Exception:
        conn.execute("ROLLBACK;")
        raise
    finally:
        conn.close()


def _replay_machine(conn, machine: str, upto: int) -> None:
    """Run the tagged steps `machine` has not yet had — from its schema_state level up to `upto`,
    in version order — and record it at `upto`. The ONE place a machine's steps replay, for both
    fronts: init_db() for machines registered before it, register_schema() for those after.

    The level is read here, inside the caller's transaction, never carried in from before the
    kernel loop: step 46 may have just written it (a box that predates the split), and a level
    read too early replays every ALTER a live box already has."""
    row = conn.execute("SELECT version FROM schema_state WHERE machine=?", (machine,)).fetchone()
    have = int(row[0]) if row else 0
    for version in range(have + 1, upto + 1):
        if _MIGRATION_OWNER.get(version) == machine:
            step = MIGRATIONS.get(version)
            if step:
                step(conn)
    conn.execute("INSERT INTO schema_state (machine, version, updated_at) VALUES (?,?,?) "
                 "ON CONFLICT(machine) DO UPDATE SET version=excluded.version, "
                 "updated_at=excluded.updated_at", (machine, upto, _now()))


# ── schema migrations (PRAGMA user_version) ───────────────────────────────────
# GOVERNING RULE:
#   • New TABLES may be added to SCHEMA as CREATE TABLE IF NOT EXISTS (safe to
#     re-run; new tables reach every box on the next deploy).
#   • Any change to an EXISTING, SHIPPED table — ADD COLUMN, backfill, index on a
#     new column — MUST be a migration here. CREATE IF NOT EXISTS silently SKIPS
#     tables that already exist, so an ALTER expressed only in SCHEMA never reaches
#     a box that already has the table. Do not edit a shipped table's DDL in SCHEMA;
#     fresh boxes get base SCHEMA and then replay the same migrations as old boxes.
#   • NEVER EDIT AN APPLIED MIGRATION, not even to add to it. The runner only walks
#     versions ABOVE the box's current one, so a change to a step already applied is a
#     permanent no-op in production while a fresh clone replays the new body and looks
#     correct — CI, clones and tests all pass and only the live box is wrong. Adding a
#     table to `_migration_28` (#459) cost days of silently unrecorded post metrics.
#     A new table goes in SCHEMA; a change to a shipped table gets its OWN new step.
# To evolve: bump SCHEMA_VERSION, append a callable. Steps run in order, once each,
# inside ONE transaction (user_version is transactional in the DB header, so a failed
# migration rolls back whole — no half-applied schema). BEGIN IMMEDIATE serializes
# concurrent migrators: worker, dispatch, and watchdog can all boot and call init_db;
# exactly one runs the steps, the rest wait on the lock then see the bumped version.

SCHEMA_VERSION = 49


def _migration_1(c) -> None:
    # Retry backoff: a requeued RetryableError job is invisible to claim_next until
    # not_before. Without it, a single-worker box re-claims the job immediately and
    # burns all max_attempts in seconds during an outage — exactly what retry-to-cap
    # was meant to survive. (jobs has shipped, hence a migration, not a SCHEMA edit.)
    c.execute("ALTER TABLE jobs ADD COLUMN not_before TEXT")


def _migration_2(c) -> None:
    # Capability review links: every reel gets an unguessable token, so the link
    # posted to Slack opens the public review page with NO login (the previous
    # system's review_token contract, web-spec §7). The token is the entire access
    # grant — distinct from the row id on purpose, so the primary key never doubles
    # as a secret. Backfill existing rows; links must work for old content too.
    c.execute("ALTER TABLE reel_scripts ADD COLUMN review_token TEXT")
    for row in c.execute("SELECT id FROM reel_scripts").fetchall():
        c.execute("UPDATE reel_scripts SET review_token = ? WHERE id = ?",
                  (uuid.uuid4().hex, row[0]))
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_reel_scripts_token "
              "ON reel_scripts(review_token)")


def _migration_3(c) -> None:
    # Slack THREADING: the owner's request message anchors a thread; the ack
    # ("Script received") and the ready link must land in that thread, not as new
    # channel messages — the previous system's exact UX. Set at enqueue by the
    # driving agent (Mavrick passes the triggering message's ts).
    c.execute("ALTER TABLE jobs ADD COLUMN slack_thread_ts TEXT")


def _migration_4(c) -> None:
    # Where a script CAME FROM, remembered on the row: a draft submitted from
    # Slack and approved on the DASHBOARD must still deliver its ready-link back
    # to the original Slack thread — the approve button's produce job inherits
    # these. Without them, dashboard-approved reels report to nobody (found in
    # self-review before any user hit it).
    c.execute("ALTER TABLE reel_scripts ADD COLUMN slack_channel_id TEXT")
    c.execute("ALTER TABLE reel_scripts ADD COLUMN slack_thread_ts TEXT")


def _migration_5(c) -> None:
    # Per-reel caption style: the review page's Caption Style picker selects a
    # named preset from config `reel.caption_styles` (HyperFrames is a local
    # renderer — caption looks are OUR yaml, not a vendor account). NULL = use
    # `reel.default_caption_style`.
    c.execute("ALTER TABLE reel_scripts ADD COLUMN caption_style TEXT")


def _migration_6(c) -> None:
    # Watched creators move from yaml to DATA (spec §2.3-4): the owner curates
    # the list from Slack ("watch @handle"), and each creator carries a
    # WATERMARK (the newest reel ref seen) so a sweep can stop at known
    # territory instead of re-walking the window. Rows are soft-deactivated,
    # never deleted (owner mandate).
    c.execute("""CREATE TABLE IF NOT EXISTS watch_creators (
        handle      TEXT PRIMARY KEY,
        added_at    TEXT NOT NULL,
        active      INTEGER NOT NULL DEFAULT 1,
        last_ref    TEXT
    )""")


def _migration_7(c) -> None:
    # GTM Engine foundations — COMPLIANCE FIRST (spec §8: the legal layer is
    # designed in before any outreach feature exists to misuse it).
    # gtm_suppression is append-only by contract: an address that opted out
    # stays out forever (CAN-SPAM); rows are never deleted, only annotated.
    c.execute("""CREATE TABLE IF NOT EXISTS gtm_suppression (
        email       TEXT PRIMARY KEY,
        reason      TEXT NOT NULL,           -- unsubscribe|bounce|manual|complaint
        added_at    TEXT NOT NULL,
        note        TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS gtm_leads (
        id          TEXT PRIMARY KEY,
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL,
        status      TEXT NOT NULL,           -- discovered|scored|verified|drafted|approved|pushed|suppressed|failed
        source_ref  TEXT UNIQUE,             -- discovery dedup (Apollo person/org id)
        email       TEXT,
        name        TEXT,
        company     TEXT,
        domain      TEXT,
        fit_score   INTEGER,
        fit_reason  TEXT,
        copy_text   TEXT,
        campaign_id TEXT,
        error       TEXT
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_gtm_leads_status "
              "ON gtm_leads(status, created_at)")


def _migration_8(c) -> None:
    # Timestamp watermarks for creator sweeps. LIVE-VERIFIED 2026-06-11: the
    # vendor lists PINNED reels first (taken_at years old, ahead of new posts),
    # so "items[0] is newest" — the premise of the ref-based watermark — is
    # false and stalls intake for any creator with pins. Position-independent
    # fix: remember the newest taken_at processed; eligibility is a timestamp
    # comparison, immune to ordering. last_ref stays (nothing is deleted) but
    # the sweep no longer relies on it.
    c.execute("ALTER TABLE watch_creators ADD COLUMN last_taken_at INTEGER")


def _migration_9(c) -> None:
    # GTM lead pipeline columns: a lead's job title (sharpens ICP scoring),
    # free-text intake notes (manual entry context — "met at conference"), and
    # the drafted email subject (body lives in copy_text). Additive; nothing
    # dropped.
    for col in ("title", "notes", "subject"):
        c.execute(f"ALTER TABLE gtm_leads ADD COLUMN {col} TEXT")


def _migration_10(c) -> None:
    # Owner-settable GTM runtime controls (daily send cap, active from-address),
    # set from Slack and persisted. Key-value. The daily cap is the deliverability
    # guardrail since Resend has no warmup/inbox-rotation layer (V3 §IV.4) — a
    # conservative per-day ceiling enforced at approve→send.
    c.execute("CREATE TABLE IF NOT EXISTS gtm_settings ("
              "key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")


def _migration_11(c) -> None:
    # Airtable two-way sync link: the id of the VIDEOS record this script mirrors.
    # It is the WHOLE loop-prevention mechanism — a script AIOS pushed carries the
    # record id, so the inbound poll recognizes it as ours and never re-imports it
    # (and an Airtable-born script carries it too, so the outbound sweep never
    # re-pushes it). NULL = not yet synced. The UNIQUE index (NULLs exempt in
    # SQLite) guarantees one script per record — no duplicate adoption of a row.
    c.execute("ALTER TABLE reel_scripts ADD COLUMN airtable_record_id TEXT")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_reel_scripts_airtable "
              "ON reel_scripts(airtable_record_id)")


def _migration_12(c) -> None:
    # Social auto-post (Zernio). posted_at is the EXACTLY-ONCE guard: a public post
    # is irreversible, so an atomic claim on posted_at (NULL → stamped) makes a
    # double-trigger a no-op — the law-of-the-land twin of claim_for_produce.
    # social_post_id holds the Zernio post id once created (audit + dedupe).
    c.execute("ALTER TABLE reel_scripts ADD COLUMN posted_at TEXT")
    c.execute("ALTER TABLE reel_scripts ADD COLUMN social_post_id TEXT")


def _migration_13(c) -> None:
    # Multi-client SPACES (docs/SPACES.md). Every reel belongs to exactly one Space
    # (one client = one Airtable base + Slack channel + Zernio profile + brand). The
    # stamp is set at creation and immutable; all downstream sync/produce/post/notify
    # read bindings ONLY from the reel's Space — a reel can never touch another
    # client's account. NULL/'default' = the single implicit Space (flat config), so
    # an existing single-tenant box is unaffected.
    c.execute("ALTER TABLE reel_scripts ADD COLUMN space TEXT NOT NULL DEFAULT 'default'")
    c.execute("CREATE INDEX IF NOT EXISTS idx_reel_scripts_space ON reel_scripts(space)")


def _migration_14(c) -> None:
    # One-off data rename: the owner renamed the @BuildwithBMAC account to @default,
    # so its Space slug follows. reel_scripts.space is the immutable per-reel binding key
    # (set at creation), so existing rows are renamed in lockstep with the config block
    # that now reads `name: default` — config and data can never disagree because both
    # land in the SAME deploy. Idempotent and a no-op on any box that never held the old
    # slug (e.g. a fresh clone): the WHERE simply matches nothing.
    c.execute("UPDATE reel_scripts SET space='default' WHERE space='build-with-bmac'")


def _migration_15(c) -> None:
    # Actor model: who triggered a job (the Slack user). Lets the agent gate spend/post
    # staging to the owner — a non-owner in a shared channel can ask, but can't make
    # Mavrick spend or publish. NULL on existing/automated jobs (treated as no actor).
    c.execute("ALTER TABLE jobs ADD COLUMN slack_user_id TEXT")


def _migration_16(c) -> None:
    # Outbound mirror of GTM leads into the Airtable Contacts table (the GoHighLevel-modeled
    # CRM view). Each lead remembers the Contact it created (airtable_record_id) and when it
    # last synced (airtable_synced_at), so the poll-only sweep pushes a lead once on create
    # and again only when it actually changes — never in a loop, never a duplicate.
    c.execute("ALTER TABLE gtm_leads ADD COLUMN airtable_record_id TEXT")
    c.execute("ALTER TABLE gtm_leads ADD COLUMN airtable_synced_at TEXT")


def _migration_17(c) -> None:
    # Two-touch outbound SEQUENCE + ICP segmentation. A lead now carries which ICP it
    # belongs to (A = in-Slack coworker, B = speed-to-lead) and the full state of its
    # scheduled follow-up: when touch 2 is DUE (stamped at touch-1 send = now +
    # GTM_FOLLOWUP_DELAY_DAYS), the drafted touch-2 subject/body (kept DISTINCT from
    # touch 1's subject/copy_text — nothing is ever overwritten), when touch 2 actually
    # SENT, its vendor ref, and an owner CANCEL flag. touch2_sent_at doubles as the
    # exactly-once CLAIM (NULL→now, atomic) so a re-run or a second worker can never
    # double-send the same prospect. The follow-up sweep reads exactly these columns.
    for col in ("icp_segment", "touch1_sent_at", "touch2_due_at", "touch2_subject",
                "touch2_copy_text", "touch2_sent_at", "touch2_campaign_id"):
        c.execute(f"ALTER TABLE gtm_leads ADD COLUMN {col} TEXT")
    c.execute("ALTER TABLE gtm_leads ADD COLUMN seq_cancelled INTEGER NOT NULL DEFAULT 0")
    # The sweep's hot query: due follow-ups by status + due-time.
    c.execute("CREATE INDEX IF NOT EXISTS idx_gtm_leads_followup "
              "ON gtm_leads(status, touch2_due_at)")


def _migration_18(c) -> None:
    # Reply ingestion → Sales hand-off. A prospect who replies to touch 1 or touch 2
    # is classified (Haiku: interested/unsubscribe/bounce/not_interested/auto_reply/neutral)
    # and handed off through data, not a direct call to the as-yet-unbuilt Sales handler.
    # sales_handoffs is Sales' durable inbox; `reply_msgid UNIQUE` is the exactly-once guard
    # (re-processing the same IMAP Message-Id is a no-op). The "replied" status on gtm_leads
    # has no DDL (free-text TEXT column exists); its validation lives in leads_store.STATUSES.
    c.execute("""CREATE TABLE IF NOT EXISTS sales_handoffs (
        id            TEXT PRIMARY KEY,
        created_at    TEXT NOT NULL,
        gtm_lead_id   TEXT NOT NULL,
        email         TEXT NOT NULL,
        company       TEXT,
        reply_excerpt TEXT,
        reply_msgid   TEXT UNIQUE,
        status        TEXT NOT NULL DEFAULT 'new',
        handled_at    TEXT
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sales_handoffs_status "
              "ON sales_handoffs(status, created_at)")


def _migration_19(c) -> None:
    # Prospect Lake step ② (docs/PROSPECTS_CONTRACT.md v3.2 §10): the local outbox
    # AIOS drains into POST $MAV_INGEST_URL. `idempotency_key UNIQUE` is the local
    # exactly-once guard AND the server's dedupe_key (one key, both ends). The
    # payload column stores the fully-rendered §3 JSON so the HMAC signs exactly
    # the bytes that were enqueued. gtm_leads gains prospect_id — the universal
    # key returned by the ingest endpoint, minted into ?pid= links on every
    # outbound GTM email.
    c.execute("""CREATE TABLE IF NOT EXISTS prospects_outbox (
        id              TEXT PRIMARY KEY,
        idempotency_key TEXT UNIQUE NOT NULL,
        payload         TEXT NOT NULL,
        local_table     TEXT,
        local_id        TEXT,
        created_at      TEXT NOT NULL,
        attempts        INTEGER NOT NULL DEFAULT 0,
        error           TEXT,
        sent_at         TEXT,
        prospect_id     TEXT
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_prospects_outbox_unsent "
              "ON prospects_outbox(sent_at, created_at)")
    c.execute("ALTER TABLE gtm_leads ADD COLUMN prospect_id TEXT")


def _migration_20(c) -> None:
    # Per-Space watched creators (bug: a creator watched in a client's #reels channel had its
    # scraped candidates announced to the PRIMARY channel, because watch_creators had no Space).
    # A creator now belongs to the Space it was watched in, so its candidates route to that
    # channel. Existing rows default to 'default' (the primary/global list — exactly where they
    # were already going, so no behavior change for them). handle stays the PK: a creator belongs
    # to one Space's list at a time (re-watching in another channel moves it there).
    c.execute("ALTER TABLE watch_creators ADD COLUMN space TEXT NOT NULL DEFAULT 'default'")


def _migration_21(c) -> None:
    # Personalize the lead-magnet welcome email ("Hey {{firstname}}"). Capture the viewer's first
    # name from the Zernio conversation participant during the sweep so the welcome greeting is
    # not generic. Nullable — an absent name simply falls back to "Hey there" (fail-open on a
    # cosmetic field only; nothing about the funnel gate depends on it).
    c.execute("ALTER TABLE leadmagnet_leads ADD COLUMN first_name TEXT")


def _migration_22(c) -> None:
    # A local mirror of HeyGen's avatar/voice library (owner mandate 2026-07-08: pick from a
    # large library in Airtable, not 3 hand-typed config entries). Populated by
    # avatar_library.sync(); AIOS's own copy of heygen_id<->airtable_record_id is what lets
    # producing a reel resolve a linked-record cell WITHOUT an extra Airtable call per render,
    # and what lets `push` write a NEW candidate's default avatar back as a real link.
    # UNIQUE(space, kind, heygen_id): one row per avatar/voice per Space (a Space's own Airtable
    # base gets its own library rows — no cross-Space leak of avatar catalogs, same invariant as
    # everything else Space-stamped).
    c.execute("""CREATE TABLE IF NOT EXISTS heygen_library (
        id                 TEXT PRIMARY KEY,
        space              TEXT NOT NULL,
        kind               TEXT NOT NULL,        -- 'avatar' | 'voice'
        heygen_id          TEXT NOT NULL,         -- avatar_id or voice_id — what submit() sends
        name               TEXT NOT NULL,
        gender             TEXT,
        language           TEXT,                  -- voices only
        preview_url        TEXT,                  -- avatar preview image / voice preview audio
        scope              TEXT,                  -- 'public' | 'private' (avatars only)
        airtable_record_id TEXT,                  -- this row's record in the Space's Avatars/Voices table
        synced_at          TEXT NOT NULL,
        UNIQUE (space, kind, heygen_id)
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_heygen_library_lookup "
              "ON heygen_library (space, kind, name)")


def _migration_23(c) -> None:
    # Closing "delivery DM": after an email is captured, the sweep sends ONE Instagram DM with the
    # guide link (the reference funnel's final step) — in addition to the welcome email. Stamp it so
    # it sends exactly once and self-heals (a failed send stays NULL → next sweep retries), the same
    # discipline as welcome_sent_at. Nullable: a lead captured before this column existed simply
    # gets the DM on the next sweep.
    c.execute("ALTER TABLE leadmagnet_leads ADD COLUMN delivery_dm_sent_at TEXT")


def _migration_24(c) -> None:
    # Exactly-once private-reply: the funnel's message 1 is now sent as a private reply to the
    # keyword comment (so it can carry a WORKING quick-reply button on a poll-only box). Meta allows
    # ONE private reply per comment, so we must send it exactly once — record each comment we've
    # DM'd here (INSERT-OR-IGNORE on the PK) so a re-poll of the same comment never double-DMs.
    c.execute("""CREATE TABLE IF NOT EXISTS leadmagnet_comment_dm (
        space       TEXT NOT NULL,
        comment_id  TEXT NOT NULL,
        message_id  TEXT,
        sent_at     TEXT NOT NULL,
        PRIMARY KEY (space, comment_id)
    )""")


def _migration_25(c) -> None:
    # Carousel Machine Phase 1 (docs/CAROUSEL_MACHINE_SPEC.md §3): a carousel is a second
    # CONTENT TYPE on the same reel_scripts pipeline — never a parallel table/machine.
    #   content_type  'reel' (default, all existing rows) | 'carousel'
    #   slides        the ---delimited slide blocks (owner-editable intermediate; §4 format)
    #   asset_urls    JSON list of staged image URLs, current render rev, slide order
    #   render_rev    bumps per re-render → fresh R2 keys (r{rev}/) so a re-render can never
    #                 serve stale CDN-cached slides to Zernio (spec §5.3)
    c.execute("ALTER TABLE reel_scripts ADD COLUMN content_type TEXT NOT NULL DEFAULT 'reel'")
    c.execute("ALTER TABLE reel_scripts ADD COLUMN slides TEXT")
    c.execute("ALTER TABLE reel_scripts ADD COLUMN asset_urls TEXT")
    c.execute("ALTER TABLE reel_scripts ADD COLUMN render_rev INTEGER NOT NULL DEFAULT 0")


def _migration_26(c) -> None:
    # Avatar V per-reel knobs (HeyGen v3 — docs/HEYGEN_MOTION.md):
    #   motion  natural-language gesture prompt. Airtable Motion → row.motion →
    #           the v3 motion_prompt. "Right hand points at camera, confident."
    #   engine  per-reel engine override (avatar_v/avatar_iv/avatar_iii). Airtable
    #           Engine → row.engine → the v3 engine, so a cinematic hero reel and a
    #           cheap volume reel coexist on one box (and an engine A/B is two rows).
    # Both empty → the config default (reel.heygen.*). Inert unless api_version: v3.
    c.execute("ALTER TABLE reel_scripts ADD COLUMN motion TEXT")
    c.execute("ALTER TABLE reel_scripts ADD COLUMN engine TEXT")


def _migration_27(c) -> None:
    # Places-native cold discovery (DEV1 handoff, renumbered from his 25: live main
    # already shipped 25=carousel content_type and 26=HeyGen v3 motion/engine).
    # A CONTACT (gtm_leads) now hangs off a BUSINESS (gtm_businesses, keyed by
    # Google's place_id) instead of carrying a free-text company name: one business
    # legitimately has several contacts, and `company` typed by a form-filler is not
    # the same string as the Google listing name we personalise from. gtm_leads is a
    # shipped table, so this ADD COLUMN has to be a migration — SCHEMA's CREATE IF
    # NOT EXISTS would silently skip existing boxes.
    c.execute("ALTER TABLE gtm_leads ADD COLUMN place_id TEXT")
    c.execute("CREATE INDEX IF NOT EXISTS idx_gtm_leads_place ON gtm_leads(place_id)")


def _migration_28(c) -> None:
    # The Approve cascade's exactly-once ledger (owner 2026-07-30). One row per
    # (space, WRITTEN row, channel) — the unit that gets published, and therefore the
    # unit that must be claimed.
    #
    # WHY THIS IS NOT THE AIRTABLE URL COLUMN. The obvious ledger is the row's own
    # `X URL` / `LinkedIn URL` field, and that is where the RECEIPT goes — but it
    # cannot be the AUTHORITY. Two live failure modes make it unsafe: an Airtable
    # write can fail AFTER a post has gone out (the next sweep would then re-post),
    # and the owner can clear the field by hand while tidying the row (which would
    # silently re-arm an irreversible public post). A local PRIMARY KEY claim can do
    # neither. Airtable reports; SQLite decides.
    #
    # status: claimed → posted, or claimed → (deleted) on a DETERMINATE failure so the
    # channel retries alone. An INDETERMINATE failure KEEPS the row forever: we may
    # already be public and there is no way to know, so we never retry it. Same posture
    # as reel_scripts.claim_for_post — losing a post is recoverable, double-posting is not.
    c.execute("""CREATE TABLE IF NOT EXISTS written_posts (
        id         TEXT PRIMARY KEY,       -- "{space}:{row_id}:{channel}"
        space      TEXT NOT NULL,
        row_id     TEXT NOT NULL,
        channel    TEXT NOT NULL,
        status     TEXT NOT NULL,          -- claimed | posted | indeterminate
        post_id    TEXT,
        claimed_at TEXT NOT NULL,
        posted_at  TEXT
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_written_posts_row "
              "ON written_posts(space, row_id)")

    # A POST'S NUMBERS AT A FIXED AGE, so that two posts can be compared at all.
    #
    # The owner, on the first day the machine could see its own numbers: "sometimes a post
    # can start off slow, but then catch on later. There must be some time period recorded
    # and equation performed." He is right, and the first version was wrong for exactly that
    # reason — it wrote one line per row and overwrote it every sweep, so the board held a
    # SNAPSHOT at whatever moment the last sweep ran. Row 19 at one hour old showed 5
    # impressions next to row 3 at three days showing 70, and the pair says nothing: the
    # difference is age, not quality, and averaging or ranking them would encode that mistake
    # into every seed the judge weights afterwards.
    #
    # One row per (space, row, channel, bucket), taken at the first sweep at or after that
    # age and NEVER updated — INSERT OR IGNORE is what freezes it. That makes "impressions at
    # 24h" a real, comparable quantity across every post the machine has ever made, and it
    # keeps the growth curve besides, which is what tells a slow burner from a dud.
    c.execute("""CREATE TABLE IF NOT EXISTS written_metrics (
        id          TEXT PRIMARY KEY,      -- "{space}:{row_id}:{channel}:{bucket}"
        space       TEXT NOT NULL,
        row_id      TEXT NOT NULL,
        channel     TEXT NOT NULL,
        bucket      TEXT NOT NULL,         -- 1h | 6h | 24h | 72h | 7d | 30d
        age_h       REAL NOT NULL,         -- the ACTUAL age when taken, not the nominal one
        impressions INTEGER NOT NULL,
        reach       INTEGER NOT NULL,
        -- THE ACTIONS ARE KEPT APART, and that is not tidiness. A share is the only action
        -- that puts the post in front of a graph we do not own, which is what virality IS;
        -- a like costs a thumb. Summing them into one `engagement` integer — as the first
        -- version did — destroys the distinction before anything can weigh it, and the
        -- distinction is the entire signal the owner asked for.
        likes       INTEGER NOT NULL DEFAULT 0,
        comments    INTEGER NOT NULL DEFAULT 0,
        shares      INTEGER NOT NULL DEFAULT 0,
        clicks      INTEGER NOT NULL DEFAULT 0,
        engagement  INTEGER NOT NULL,      -- the plain sum, kept for a human reading the row
        taken_at    TEXT NOT NULL
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_written_metrics_row "
              "ON written_metrics(space, row_id)")


def _add_column_if_missing(c, table: str, col: str, decl: str = "TEXT") -> None:
    """`ALTER TABLE ... ADD COLUMN`, but re-runnable.

    SQLite has no `ADD COLUMN IF NOT EXISTS`, so a migration that adds several columns is
    only atomic if nothing interrupts it. If the process dies (or a later statement raises)
    after some ALTERs but BEFORE user_version is bumped, the migration re-runs on the next
    boot, hits `duplicate column name`, and the box can never finish migrating — it is
    bricked in a way that only a hand-edited database recovers from. On a clone we do not
    operate, that is unacceptable. Asking the schema first makes the step idempotent.
    """
    cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
    if col not in cols:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def _migration_29(c) -> None:
    # BULK LIST INGEST (docs/GTM_BULK_IMPORT_SPEC.md). Industry directories arrive WITH
    # contact data; Google Places does not. These columns let a business carry a supplied
    # contact so enrich() can create a lead WITHOUT a website to harvest — a phone-only
    # directory row is otherwise a dead end. NULL for every Places row: behaviour there
    # is unchanged.
    for col in ("contact_email", "contact_name", "contact_title", "contact_phone"):
        _add_column_if_missing(c, "gtm_businesses", col)
    # Phone on the CONTACT. Two jobs: it is the SMS deliverable, and it is the lake
    # identity (marketing.prospects.phone_e164) — which is why it is stored E.164 and
    # nowhere else. Identity-merge in the lake is EXACT-MATCH, so "(843) 555-0134" and
    # "+18435550134" would resolve to two different people.
    _add_column_if_missing(c, "gtm_leads", "phone")
    c.execute("CREATE INDEX IF NOT EXISTS idx_gtm_leads_phone ON gtm_leads(phone)")


def _migration_30(c) -> None:
    # THE CAPTION-FREE MASTER (owner, 2026-08-08). Captions are burned into the pixels in
    # one libass pass, so a delivered cut is a one-way door: when the caption height turned
    # out to clash with Meta's ad chrome, there was no way back to a clean frame short of
    # re-rendering every ad through HeyGen — and `avatar_iv` is generative, so the re-render
    # would not have been the same performance. The master is the pre-burn mux, which the
    # pipeline already produced and then threw away with its work directory. Recording its
    # url next to the cut makes captions a re-runnable layer. NULL for every cut made before
    # this, which is exactly the six that prompted it.
    _add_column_if_missing(c, "reel_cuts", "master_url")


def _migration_32(c) -> None:
    """Separate the VENDOR id from the INTERNAL key.

    `place_id` is the primary key and 233 leads point at it, so it can never be blank — but
    its comment said "Google's canonical id" and that was never true: on 2026-08-08 the table
    held csv:606, overture:100, and exactly ONE real Google id, the oldest synthetic dating to
    08-04. A column named for a vendor while holding our own surrogate is how a synthetic id
    ends up in a paid Details URL.

    So the vendor id gets its own column, NULL until Google actually gives us one. That is the
    field the owner expects to be blank, and now it is blank by construction rather than by
    hope. `place_id` keeps its job: our dedupe key, prefixed by origin.
    """
    _add_column_if_missing(c, "gtm_businesses", "google_place_id")


def _migration_31(c) -> None:
    # CLEAN THE COLD POOL, WITHOUT DELETING ANYTHING (owner, 2026-08-08). The store grew to
    # 531 businesses behind a working list of 172 because bulk_import upserts and nothing in
    # the system ever retracts. Dirty data is worse than less data: every count is wrong and
    # every stale row is a call to a business already decided against.
    #
    # THE BAR: a prospect we work has an owner NAME, a company name and a phone. Email is
    # what separates drip-able from call-only, not a lead from junk — with no name there is
    # nobody to ask for.
    #
    # SUPPRESSED, NEVER DELETED — house policy, and the convention was already here:
    # `mailable()` selects `WHERE suppress_reason IS NULL`, which is how national chains are
    # excluded. So the marker removes a row from every working query while the record itself
    # survives, and `UPDATE ... SET suppress_reason = NULL WHERE suppress_reason =
    # 'below_bar'` puts them all back. A row deleted because today's bar said so is
    # unrecoverable when tomorrow's bar disagrees, and the bar has already moved twice.
    #
    # WHY A MIGRATION. This is a data fix that has to land on a box no one can SSH into from
    # a cloud session, and a migration is the one path that applies itself on deploy. It is
    # idempotent — the WHERE clause excludes rows already marked — so a redeploy is a no-op.
    # It only ever ADDS a marker: an existing suppress_reason (chain, closed, no_email) is a
    # different judgement and is left exactly as it is.
    cols = {r[1] for r in c.execute("PRAGMA table_info(gtm_businesses)")}
    if "contact_name" not in cols:
        return                      # pre-29 box: nothing to judge against yet
    if "linkedin_url" not in cols:  # the owner's personal profile needs a home in the store
        _add_column_if_missing(c, "gtm_businesses", "linkedin_url")
    below = ("COALESCE(contact_name,'') = '' OR COALESCE(phone,'') = '' "
             "OR COALESCE(name_places,'') = ''")
    live = c.execute("SELECT COUNT(*) FROM gtm_businesses "
                     "WHERE suppress_reason IS NULL").fetchone()[0]
    if not live:
        return
    doomed = c.execute(f"SELECT COUNT(*) FROM gtm_businesses "
                       f"WHERE suppress_reason IS NULL AND ({below})").fetchone()[0]
    # THE SAFETY THAT MATTERS, and it is not hypothetical: OSDev1 measured the live box at
    # 531 businesses with contact_name populated on ZERO of them, because an earlier import
    # dropped every name (split First/Last vs one contact_name). Against that box this bar
    # matches EVERYTHING, and suppressing the whole pool is not a clean-up — it is a
    # different bug wearing a clean-up's clothes. The remedy there is the re-import that
    # carries the names, not the marker. So: refuse, loudly, and leave the store untouched.
    if doomed >= live * 0.9:
        log.warning("migration31.skipped_would_empty_pool", live=live, doomed=doomed,
                    hint="contact_name looks unpopulated — re-import with names FIRST, "
                         "then redeploy; this migration is idempotent and will apply then")
        return
    c.execute(f"UPDATE gtm_businesses SET suppress_reason = 'below_bar' "
              f"WHERE suppress_reason IS NULL AND ({below})")


def _migration_33(c) -> None:
    """The follow-nudge cooldown clock (sweep._NUDGE_COOLDOWN_S, defined 2026-07 and never
    wired). A KNOWN non-follower at the follow wall must be re-asked at most once per cooldown
    — before this stamp existed the verified path held them in SILENCE, and the soft path
    advanced them without a follow at all (the hole the owner caught 2026-08-17). The stamp is
    per-lead state, so it must survive a worker restart; hence a column, not memory."""
    _add_column_if_missing(c, "leadmagnet_leads", "nudged_at")


def _migration_34(c) -> None:
    """The served-once ledger for lead-magnet requests: one row per (lead, keyword) ever served.

    A returning converter — someone who finished the funnel and later comments a DIFFERENT
    keyword — is re-armed for the second guide (leads_store.rearm). What makes that safe is
    THIS table, because Zernio's automation fire logs are a HISTORY, not an event stream: every
    sweep re-reads them, so a viewer who once commented two keywords appears in both logs
    forever. Without a served-once claim the two would re-arm each other in turn and DM a guide
    every cycle, permanently. The claim is INSERT-OR-IGNORE on UNIQUE(lead, keyword) — the same
    exactly-once discipline the rest of the machine uses — and it doubles as per-guide
    attribution, which the single `keyword` column on the lead row cannot hold.

    Backfilled from every existing lead, so the keyword a live lead ALREADY converted on can
    never re-arm them after this ships."""
    c.execute("""
      CREATE TABLE IF NOT EXISTS leadmagnet_lead_requests (
        lead_id      TEXT NOT NULL,
        keyword      TEXT NOT NULL,
        requested_at TEXT NOT NULL,
        UNIQUE (lead_id, keyword)
      )""")
    c.execute("INSERT OR IGNORE INTO leadmagnet_lead_requests (lead_id, keyword, requested_at)"
              " SELECT id, keyword, COALESCE(created_at, ?) FROM leadmagnet_leads"
              " WHERE keyword IS NOT NULL AND keyword != ''", (_now(),))


def _migration_35(c) -> None:
    """The cast pick for a two-voice reel: which voice answers the phone.

    A dialogue script names its speakers in the copy (MAVRICK:/DAVE:), but the copy cannot
    say WHICH voice plays the far end — that is a per-reel choice between recurring personas
    (a hostile no-heat caller, a skeptical spa lead) and it has to survive the round trip
    through Airtable so the row still knows what it was rendered with.

    Deliberately a NAME, not a provider id. The ids are Higgsfield voice UUIDs reachable only
    through the MCP; putting one in an Airtable cell means hand-copying a UUID per row, and a
    typo there fails after the plates are already paid for. Same split as the Look picker:
    the board holds a name, config holds the id."""
    _add_column_if_missing(c, "reel_scripts", "voice2")


def _migration_36(c) -> None:
    """Per-platform publish outcome — the thing the machine could never say.

    `zernio.posted` means Zernio ACCEPTED a post, not that any network published it. On
    2026-08-19 two rows went out and BOTH were `partial`: row 76 reached Facebook and YouTube
    while Instagram and TikTok never started, and row 83 reached three of five. Every signal we
    produced said success — the Slack line listed what was SUBMITTED, and `Date Posted` stamped
    on submit. The owner found one gap by opening LinkedIn; the rest were found by reading
    Zernio's post record by hand.

    One row per (script, platform), because that is the grain the truth actually has. The
    PRIMARY KEY is also the guarantee the future re-publish feature needs: a platform can hold
    exactly one state per script, so `published` is terminal and cannot be re-attempted into a
    double post no matter how many times a trigger fires.

    `post_report_at` on reel_scripts marks the report as delivered, so a settle pass that runs
    every couple of minutes reports each piece of content ONCE.
    """
    c.execute("""
      CREATE TABLE IF NOT EXISTS reel_post_targets (
        script_id    TEXT NOT NULL,
        platform     TEXT NOT NULL,
        state        TEXT NOT NULL DEFAULT 'submitted',   -- submitted|published|failed
        post_id      TEXT,
        detail       TEXT,
        submitted_at TEXT NOT NULL,
        settled_at   TEXT,
        PRIMARY KEY (script_id, platform)
      )""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_reel_post_targets_state "
              "ON reel_post_targets (state, submitted_at)")
    _add_column_if_missing(c, "reel_scripts", "post_report_at")


def _migration_37(c) -> None:
    """One reminder, then silence, for a piece of content still missing a network.

    The settle report fires ONCE. A gap noticed at 2am is forgotten by morning, and a gap
    repeated every two minutes is muted by lunchtime — so a row that is still incomplete after
    a few hours earns exactly one nudge, and this column is what makes "exactly one" true
    across restarts."""
    _add_column_if_missing(c, "reel_scripts", "post_reminder_at")


def _migration_38(c) -> None:
    """INVESTORS — a separate universe, never a segment (owner, 2026-08-25).

    The owner is repurposing the GTM engine for investor outreach while an outside firm
    takes over cold marketing volume. His constraint is absolute: investors must NEVER
    receive a marketing email. That is a data-architecture requirement, not a filter —
    a filter is one bug away from mailing a fund partner a "missed calls" pitch.

    So investors get their OWN tables, and the separation is structural:
      · no marketing pipeline (enrich / drip / queue_export / prospect lake / Airtable
        Prospects mirror) reads these tables — enforced by test_gtm_investor_wall's
        static scan, not by convention;
      · the marketing push gate (compliance.assert_pushable) additionally REFUSES any
        address that appears here, so even an investor row that somehow leaked into
        gtm_leads cannot be sent to — belt on top of the structural braces;
      · nothing in this schema can send. Outreach machinery arrives in a later PR,
        behind the owner's explicit greenlight (config `investors.outreach_enabled`,
        shipped false and read by nothing until that PR).

    investor_touches is the multi-channel history (email / linkedin_invite /
    linkedin_dm / call / meeting), one row per touch — because investor outreach is
    low-volume and personal, the record of WHO was touched WHEN on WHICH channel is
    the asset, and it must survive any change of channel tooling.
    """
    c.execute("""CREATE TABLE IF NOT EXISTS investors (
        investor_id  TEXT PRIMARY KEY,
        created_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL,
        first_name   TEXT, last_name TEXT, full_name TEXT,
        firm         TEXT, title TEXT,
        email        TEXT UNIQUE,
        phone        TEXT,
        linkedin_url TEXT, twitter_url TEXT, website TEXT,
        city         TEXT, region TEXT,
        stage        TEXT, focus TEXT, check_size TEXT,
        source       TEXT, notes TEXT,
        status       TEXT NOT NULL DEFAULT 'researching',
        suppress_reason TEXT
      )""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_investors_status ON investors (status)")
    c.execute("""CREATE TABLE IF NOT EXISTS investor_touches (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        investor_id TEXT NOT NULL REFERENCES investors(investor_id),
        channel     TEXT NOT NULL,
        direction   TEXT NOT NULL DEFAULT 'outbound',
        note        TEXT,
        created_at  TEXT NOT NULL
      )""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_investor_touches_inv "
              "ON investor_touches (investor_id, created_at)")


def _migration_39(c) -> None:
    """LIST FACTORY v5 (OUTREACH_STACK_V5 §6.2) — lead provenance, gates and batches.

    The factory's whole product is a defensible row: where a lead came from (every
    source, not just the first), what the gate decided and when, what enrichment
    actually spent, what the verifier concluded, and which agency batch shipped it.
    Bucket rides the existing icp_segment column (values A/B/C/D) and score rides
    fit_score — reusing them keeps every older query working.

    phone_confirmed is the measured star of the Aug 29-31 runs: 1 when the CSLB
    licensed number was found in the company website's own HTML — the strongest
    identity proof in the stack (it rejected 24 of 43 phone-search candidates,
    including a city government mistaken for a contractor).
    """
    for col, decl in [("credits_spent", "INTEGER NOT NULL DEFAULT 0"),
                      ("alt_source_refs", "TEXT"),            # JSON list of merged refs
                      ("gate_result", "TEXT"),                # pass|fail|unknown
                      ("gate_checked_at", "TEXT"),
                      ("email_outcome", "TEXT"),  # verified|constructed_valid|catch_all|not_found
                      ("email_format", "TEXT"),
                      ("batch_ref", "TEXT"),                  # MAV-{YYYYWW}-{bucket}
                      ("phone_confirmed", "INTEGER NOT NULL DEFAULT 0")]:
        _add_column_if_missing(c, "gtm_leads", col, decl)
    c.execute("CREATE INDEX IF NOT EXISTS ix_gtm_leads_batch ON gtm_leads (batch_ref)")


def _migration_40(c) -> None:
    """WAALAXY (docs/WAALAXY_INTEGRATION_PLAN.md) — LinkedIn channel plumbing.

    gtm_leads gains linkedin_url: the resolver already MATCHES on a LinkedIn slug but
    had nowhere to keep the URL, so coverage lived only in the Airtable mirror (25 of
    189) and in li: source refs — unqueryable for a channel whose import contract
    requires exactly this field. (waalaxy_pushes, a NEW table, lives in SCHEMA.)
    """
    _add_column_if_missing(c, "gtm_leads", "linkedin_url")


def _migration_41(c) -> None:
    """THE PLUG SEAM (docs/LEAD_MACHINE_PLUG_CONTRACT.md). Three columns a plug-in's
    contract row carries that gtm_leads had nowhere to keep:
      city, state  — the practice LOCATION (never the mailing address), so a mixed tray
                     can be filtered by geography without reaching into gtm_businesses;
      signal       — opaque JSON the machine owns and the core NEVER interprets. The
                     instant core code reads signal["clock_stage"], the core knows a
                     specific machine exists and the contract is broken.
    `fit` needs no column: it rides the existing fit_score."""
    for col in ("city", "state", "signal"):
        _add_column_if_missing(c, "gtm_leads", col, "TEXT")


def _migration_42(c) -> None:
    """WHICH PURCHASED RECIPE FOUND THIS BUSINESS. Page 2 of the machine GUI filters rows by
    recipe, and until now there was no path: config recipes deliberately SHARE the kernel
    `places:` prefix (two Places recipes emit the same provenance — correct, it is the same
    source), and gtm_discovery_runs records TILES, not rows.

    FIRST-SEEN, NEVER OVERWRITTEN. `campaign` is set on INSERT only. A business that two
    recipes both find belongs to the one that PAID to find it first; a re-run of a finished
    sweep must not re-attribute rows it did not pay for, or per-recipe cost per business
    becomes a number that moves on its own.

    BACKFILL ONLY WHERE IT IS TRUE. A tile does not record which businesses it returned, so
    existing rows cannot be attributed in general. The one honest case is a box whose whole
    history is a single campaign: then every Places-sourced row came from it. Anything else
    stays NULL and the GUI shows it as unattributed — a NULL a human can read beats a guess
    that looks like a measurement."""
    _add_column_if_missing(c, "gtm_businesses", "campaign", "TEXT")
    c.execute("CREATE INDEX IF NOT EXISTS idx_gtm_biz_campaign ON gtm_businesses(campaign)")
    rows = c.execute("SELECT DISTINCT campaign FROM gtm_discovery_runs").fetchall()
    if len(rows) == 1:
        c.execute("UPDATE gtm_businesses SET campaign = ? "
                  "WHERE campaign IS NULL AND source = 'google_places'", (rows[0][0],))


def _migration_43(c) -> None:
    """`signal` — the column the CORE agrees never to understand.

    A content plug-in knows things the Content Machine does not and should not: a cold-call
    pack tracks which objection a lead raised, a recipe pack tracks a cuisine. Those belong
    to the plug-in. Without somewhere to put them, every new machine either widens
    reel_scripts with a column nobody else uses, or the core learns that machine's shape —
    and a core that knows one machine's shape is a core that breaks when that machine
    changes. This is the opaque box: JSON in, JSON out, never parsed here.

    THE RULE IS ENFORCED, NOT ASKED FOR. The Lead Machine carries the identical column and
    an AST scan that fails the build the moment core code subscripts `signal[...]` — because
    the instant core reads signal["stage"], the core knows a specific machine exists and the
    contract is over. Content's own scan lands with the plug contract; this is the column it
    will guard, and it is clean today (measured across core/ and content_machine/: zero
    subscripts).

    Nullable, no default, no backfill: a row written before any plug-in existed has no
    machine-owned state, and NULL says exactly that. Writing '{}' would claim otherwise.
    """
    _add_column_if_missing(c, "reel_scripts", "signal")


def _migration_44(c) -> None:
    """THE ARCHIVIST'S THREE COLUMNS (docs/SPEC_ARCHIVIST.md 3.2). Owner, 2026-09-09: "there's
    gotta be some sort of archivist process where things reach a dead end", and the standing
    rule beside it: NOTHING IS DELETED, EVER. Archiving is a state, not a removal: the row,
    its address, its history and its provenance stay exactly where they are; it leaves the
    working list, the pushes and the sends.

    `archived_at`     when; NULL is live. Every working-list read filters on this.
    `archived_reason` why: catch_all | role_only | no_address | undeliverable |
                      company_mismatch. With the date, a row can come BACK when the evidence
                      changes; without it the archive is unrecoverable.
    `tiers_tried`     comma list of waterfall tiers that have actually run for this lead.
                      EXHAUSTED IS THE LOAD-BEARING WORD: a row nobody has tried is in
                      progress, not a dead end, and archiving it would hide work the machine
                      has not done. This column is what makes 'archived' mean something."""
    for col in ("archived_at", "archived_reason", "tiers_tried"):
        _add_column_if_missing(c, "gtm_leads", col, "TEXT")
    c.execute("CREATE INDEX IF NOT EXISTS idx_gtm_leads_archived ON gtm_leads(archived_at)")


def _migration_45(c) -> None:
    """WHEN THE ADDRESS ARRIVED. Owner, 2026-09-10: "every day we need at least 20", and the
    machine could not answer honestly how many it made today. `created_at` is when the COMPANY
    was found, often weeks earlier; `updated_at` moves on every write, so a drip send or an
    Airtable sync would count an old lead as today's work. Neither is the question.

    `email_at` is stamped once, by leads_store, at the moment `email` goes from empty to
    non-empty, and never again. It is the only column that can answer "how many people did
    this machine make reachable today", which is the number the owner runs the business on."""
    _add_column_if_missing(c, "gtm_leads", "email_at", "TEXT")
    c.execute("CREATE INDEX IF NOT EXISTS idx_gtm_leads_email_at ON gtm_leads(email_at)")


def _migration_47(c) -> None:
    """WHO IS BEHIND THIS SESSION. `sessions` predates there being more than one person, so
    every row on every live box today means "the owner" and nothing else.

    NULL IS THE OWNER, AND THAT IS THE WHOLE DESIGN OF THIS STEP. Backfilling a user id here
    would need a user to backfill TO, and minting one silently is how a box ends up with an
    account nobody created. Leaving it NULL costs nothing and buys the property that matters:
    the owner's existing 30-day cookie keeps working across this migration. Owner, 2026-09-07:
    "I don't ever wanna be locked out of these machines." A schema change that signs him out of
    his own box on deploy is the exact failure that rule exists to forbid.

    NULL IS NOT THE OWNER. An earlier cut of this step left existing sessions NULL and taught
    every gate to read NULL as "the owner" — which is a rule that FAILS OPEN: any future code
    path that forgets to set `user_id`, or any row inserted with it missing, becomes the owner of
    the whole box. OSDev1 flagged it; he is right. The fix is that the assignment happens ONCE,
    here, instead of being re-derived on every request forever.

    So this step creates the owner's row and BACKFILLS every session that predates it. After it
    runs there is no such thing as a session without a person: the owner's 30-day cookie keeps
    working (owner, 2026-09-07, "I don't ever wanna be locked out of these machines") because it
    now points at a real row, not because a NULL is being generously interpreted.

    No index: `sessions` is looked up by primary key and holds one row per sign-in.
    """
    _add_column_if_missing(c, "sessions", "user_id", "TEXT")
    # The row must exist before anything can point at it. INSERT OR IGNORE so a re-run after a
    # crash mid-migration is a no-op rather than a UNIQUE failure on `email`.
    c.execute("INSERT OR IGNORE INTO users (id, email, name, role, active, created_at) "
              "VALUES (?,?,?,?,1,?)",
              (OWNER_USER_ID, OWNER_USER_EMAIL, "Owner", "owner", _now()))
    c.execute("UPDATE sessions SET user_id = ? WHERE user_id IS NULL", (OWNER_USER_ID,))


def _migration_48(c) -> None:
    """THE TWO COLUMNS A HUMAN REPLY NEEDS (SPEC_UNIFIED_INBOX_SCREEN_AND_AUTH §3.3 and §6).

    `inbox_conversations.account_id` — WITHOUT THIS THE SCREEN CANNOT SEND AT ALL, and the spec
    did not notice. `account_id` is the account that owns the thread; the gateway requires it
    (`zernio.client(sp).inbox.send(zcid, account_id, text)`) and it is the tenant boundary under
    the Space. The poller reads it from the vendor payload (`inbox/poller.py:166`) and puts it in
    the JOB, which is why the automated opener can send — but it was never STORED, so a person
    opening that thread an hour later has no account to send from. It is a property of the
    conversation, it does not change, and it belongs on the row.

    `inbox_send_ledger.user_id` — WHO hit send. Not bookkeeping: the thread screen's whole claim
    is that it says who said every line, and `sent_by` only separates `ai` from `human`. The day
    a second human signs in, "human" stops being an answer and the screen starts lying quietly.
    NULL means the machine sent it, which is what every existing row is.

    ALTERs on tables a live box already carries, so a migration rather than a SCHEMA edit — the
    rule at the top of this file and the _migration_28 incident it cites. Tagged `customer_voice`
    in _MIGRATION_OWNER because a Lead or Content box has neither table.
    """
    _add_column_if_missing(c, "inbox_conversations", "account_id", "TEXT")
    _add_column_if_missing(c, "inbox_send_ledger", "user_id", "TEXT")



def _migration_46(c) -> None:
    """THE SCHEMA SPLIT'S ONE-TIME BOOTSTRAP. A box that predates the split already carries every
    machine's tables with every migration applied (user_version was 45 for all of them together).
    Record each such machine as fully migrated, so its tagged steps never replay on this box —
    an ALTER TABLE ADD COLUMN that already exists is an error, not a no-op. A machine whose marker
    table is absent gets no row and starts from zero if it is ever registered here, which is the
    fresh-image path. Nothing is dropped, nothing is created; this step only records.

    The level recorded is the user_version the box HAD when this run began, not a constant 45:
    a fresh database registers its machines before init_db(), so its base tables exist by the
    time this step runs, and recording them at 45 would skip every tagged step they still need.
    At 0 they replay from the start; on the live box (45) nothing replays; a box parked below 45
    replays only what it lacks."""
    level = min(45, _BOOT_USER_VERSION if _BOOT_USER_VERSION is not None else 45)
    for machine, marker in _MACHINE_MARKER.items():
        if _table_exists(c, marker):
            c.execute("INSERT INTO schema_state (machine, version, updated_at) VALUES (?,?,?) "
                      "ON CONFLICT(machine) DO UPDATE SET version=excluded.version, "
                      "updated_at=excluded.updated_at", (machine, level, _now()))

def _migration_49(c) -> None:
    """A PASSWORD PER PERSON (docs/DESIGN_PER_PERSON_LOGIN.md). The $499 card sells "up to 5 people,
    each with their own login", and until this every session on every box was the owner's: `users`
    held identities that nothing could authenticate as.

    ONE CREDENTIAL STORE. A box already claimed (#1172) holds its owner's password in
    `box_claim.pw_hash`; it moves onto the owner's row here, so sign-in reads one place. The claim
    row stays, whole, as the audit of who took the box.

    NULL MEANS NO PASSWORD YET, which is exactly an invited person who has not joined. The owner row
    on an unclaimed box stays NULL too: DASH_TOKEN is its way in, unchanged.
    """
    # RE-RUNNABLE, because a kernel step is replayed whenever a box's user_version is behind its tables
    # (tests/test_schema_integrity pins a box at 32 and restarts it): the column is added only if absent.
    if "pw_hash" not in {r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()}:
        c.execute("ALTER TABLE users ADD COLUMN pw_hash TEXT")
    if _table_exists(c, "box_claim"):
        row = c.execute("SELECT pw_hash, user_id FROM box_claim WHERE id = 1").fetchone()
        if row and row[0]:
            c.execute("UPDATE users SET pw_hash = ? WHERE id = ? AND pw_hash IS NULL", (row[0], row[1]))


MIGRATIONS = {
    46: _migration_46,   # the schema split's bootstrap (kernel step)
    1: _migration_1, 2: _migration_2, 3: _migration_3, 4: _migration_4,
              5: _migration_5, 6: _migration_6, 7: _migration_7, 8: _migration_8,
              9: _migration_9, 10: _migration_10, 11: _migration_11, 12: _migration_12,
              13: _migration_13, 14: _migration_14, 15: _migration_15, 16: _migration_16,
              17: _migration_17, 18: _migration_18, 19: _migration_19, 20: _migration_20,
              21: _migration_21, 22: _migration_22, 23: _migration_23, 24: _migration_24,
              25: _migration_25, 26: _migration_26, 27: _migration_27,
              28: _migration_28, 29: _migration_29, 30: _migration_30, 31: _migration_31, 32: _migration_32, 33: _migration_33,
              34: _migration_34, 35: _migration_35, 36: _migration_36,
              37: _migration_37, 38: _migration_38,
              39: _migration_39, 40: _migration_40, 41: _migration_41,
              42: _migration_42, 43: _migration_43, 44: _migration_44,
              45: _migration_45, 47: _migration_47, 48: _migration_48,
              49: _migration_49}


# init_db IS SAFE TO CALL FROM MANY THREADS AND PROCESSES AT ONCE. Main went red on 2026-09-06
# (a0ff1b5) on `OperationalError: database schema has changed`: eight threads ran it together,
# one migration's ALTER landed while another thread's statement was prepared, and sqlite gives
# up re-preparing after a few rounds. In-process callers are serialised on a lock; a SECOND
# PROCESS (worker and dispatch boot side by side on the box) cannot share the lock, so the
# schema race is retried a bounded number of times instead of surfacing as a crash at boot.
# tests/test_schema_integrity reads the first 400 chars of init_db's source: keep the body short.
_INIT_LOCK = threading.Lock()
_SCHEMA_RACE = ("database schema has changed", "database is locked")


def init_db() -> None:
    with _INIT_LOCK:
        for attempt in range(6):
            try:
                # 1) Kernel tables, then the base DDL of every machine registered so far
                #    (idempotent CREATE IF NOT EXISTS).
                with _SCHEMA_LOCK:
                    machines = tuple(_SCHEMAS)
                with connect() as c:
                    c.executescript(SCHEMA)
                    for m in machines:
                        c.executescript(_SCHEMAS[m])
                # 2) Versioned changes: kernel steps everywhere, a machine's steps only where it
                #    is registered (recorded per machine in schema_state).
                _run_migrations(machines)
                global _DB_READY
                _DB_READY = True
                return
            except sqlite3.OperationalError as e:
                if attempt == 5 or not any(m in str(e) for m in _SCHEMA_RACE):
                    raise
                time.sleep(0.05 * (attempt + 1))


_BOOT_USER_VERSION: int | None = None   # user_version at the start of the current _run_migrations


def _run_migrations(machines: tuple[str, ...] = ()) -> None:
    # Own connection, autocommit mode, explicit transaction — executescript() above
    # implicitly commits, so migrations must not share that flow.
    global _BOOT_USER_VERSION
    conn = sqlite3.connect(settings.db_path, timeout=30, isolation_level=None)
    try:
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("BEGIN IMMEDIATE;")
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        _BOOT_USER_VERSION = current
        # Two passes, deliberately. The kernel walks user_version and runs ONLY kernel steps;
        # then every registered machine replays its own tagged steps from the level schema_state
        # records for it. A machine's level and the kernel's are different numbers: install.sh
        # brings a fresh image's kernel to SCHEMA_VERSION with nothing registered, and the first
        # process to load a machine must still give it every tagged step from 1 — a single loop
        # keyed on the kernel's number found nothing to run and recorded the machine as done
        # with base tables only (CI, 2026-09-13: "gtm_leads has no column named email_at").
        for version in range(current + 1, SCHEMA_VERSION + 1):
            step = MIGRATIONS.get(version)
            if step and version not in _MIGRATION_OWNER:
                step(conn)
            conn.execute(f"PRAGMA user_version = {version};")  # PRAGMA takes no params
        for m in machines:
            _replay_machine(conn, m, SCHEMA_VERSION)
        conn.execute("COMMIT;")
    except Exception:
        conn.execute("ROLLBACK;")
        raise
    finally:
        conn.close()


# ── the people who may sign in (SPEC_UNIFIED_INBOX_SCREEN_AND_AUTH §3.1) ─────────────────
# THE BOX'S OWNER, as a real row rather than as the ABSENCE of one. A fixed id because
# migration 47 both creates it and backfills every existing session to it, and because every
# later boot must find the same row rather than mint a second.
OWNER_USER_ID = "usr_owner"
OWNER_USER_EMAIL = "owner@localhost"


class SeatsFull(Exception):
    """The box is at its configured seat limit. Raised where a user is CREATED, never where one
    is admitted — see `max_users`."""


class SharedPasswordRefused(Exception):
    """This box's dashboard password IS its API key, so it cannot admit a second person yet."""


def dash_password_is_the_box_key() -> bool:
    """Is the dashboard password the same secret that drives the whole box through /dispatch?

    `core/config.py:59` — `dash_token = os.environ.get("DASH_TOKEN", "") or dispatch_bearer_token`
    — and `scripts/install.sh:88-93` mints the bearer but NOT `DASH_TOKEN`. So on a box nobody
    has configured further, typing the dashboard password into a browser form is typing the key
    that runs the box, and on a box without `UNSUB_SIGNING_KEY` it is also the key that signs
    every opt-out link in mail already sent (`core/compliance.py`).

    That was FINE while the only person who logged in was the owner, which is exactly what
    `scripts/doctor.py:120` says. It stops being fine the moment a second person can sign in.

    COMPARES THE EFFECTIVE VALUES, not merely "is DASH_TOKEN set". Setting `DASH_TOKEN` to the
    same string as the bearer passes an is-it-set check and is precisely the situation the check
    exists to refuse — a separate name for one secret is not a separate secret.
    """
    from core.config import settings
    dash = str(getattr(settings, "dash_token", "") or "")
    bearer = str(getattr(settings, "dispatch_bearer_token", "") or "")
    return bool(dash) and dash == bearer


def max_users() -> int:
    """How many active people this box may carry. 0 or absent = unlimited.

    READ PER CALL, NEVER BOUND AT IMPORT. `marketing/customer_voice/rails.py:34` documents two of
    its own tests passing VACUOUSLY because a module-level `get_config` kept the config the
    process started with; the same mistake cost an hour in `core/dash` the same morning. A box
    whose owner edits this line and restarts a worker must not have one process disagreeing with
    another about how many seats it sold.

    THE $499 / PRO DIFFERENCE IS THIS ONE LINE (PRICING_AND_PACKAGING_OWNBOX §6.1). It lives
    under `dash` rather than the `inbox` that doc names, because `export_box.sh` ships `dash` to
    every box and `inbox` only to customer_voice — and an absent limit means UNLIMITED, so on a
    Lead box the seat cap would have vanished.

    OWNER, 2026-09-16: regular is THREE people, Pro is unlimited. The tracked config carries the
    base three; a Pro box gets `dash.max_users: 0` written into its untracked overlay at first
    boot (`scripts/connector_handoff.apply_seats`), from the tier in `provision.json`.

    "EDITABLE PER BOX, BY HAND" USED TO BE WRITTEN HERE AND IT WAS NEVER TRUE. The provisioner
    builds boxes unattended and nobody ever opened that file — so for as long as the tier failed
    to reach the droplet, every box booted on the base line and a $1,599 buyer got the $499 seat
    count. A knob only a human can turn is not a tier; it is a bug with a comment on it.
    """
    from core.config import get_config
    try:
        raw = (get_config().get("dash") or {}).get("max_users", 0)
        return max(0, int(raw or 0))
    except Exception:                      # noqa: BLE001 — a junk value must not decide a seat
        return 0


def count_active_users() -> int:
    with connect() as c:
        row = c.execute("SELECT COUNT(*) AS n FROM users WHERE active = 1").fetchone()
    return int(row["n"] if row else 0)


def add_user(email: str, *, name: str | None = None, role: str = "member") -> dict:
    """Create a person who may sign in. Lowercases the address; refuses past the seat limit.

    THE SEAT CHECK LIVES HERE AND NOWHERE ELSE. PRICING §6.5: no tier check inside a security
    path, ever. A limit consulted at ADMIT time is a paywall in the authentication path — and a
    paywall that fails open on a missing config is worse than no paywall, while one that fails
    closed locks a paying customer out of his own box at 2am. Capacity is decided when a seat is
    handed out; after that the person either exists or does not.

    Re-adding an existing address REACTIVATES rather than duplicating, because `email` is UNIQUE
    and because "re-hire the person we revoked" is a real thing that must not need a DBA.
    """
    email = str(email or "").strip().lower()
    if not email or "@" not in email:
        raise ValueError("a user needs an email address")
    role = str(role or "member").strip().lower()
    if role not in ("owner", "member"):
        raise ValueError(f"unknown role: {role!r}")
    # SPEC §2: "A box that admits employees MUST have a distinct DASH_TOKEN set, and the
    # bootstrap check has to refuse otherwise." Refused HERE because this is the only place a
    # second person can come into existence — the same reason the seat limit lives here and not
    # in an admit path. It is not a tier check: it costs nothing, it cannot be bought off, and
    # it gates capability rather than capacity.
    #
    # ONLY A NON-OWNER. The owner needs no row at all (a session with user_id NULL is him), so
    # refusing every add_user would block nothing real while risking a future owner row. What
    # must not happen is an EMPLOYEE on a box whose dashboard password is its API key.
    if role != "owner" and dash_password_is_the_box_key():
        raise SharedPasswordRefused(
            "this box's dashboard password is also its API key (DASH_TOKEN is unset, so "
            "core/config falls back to DISPATCH_BEARER_TOKEN). Set a distinct DASH_TOKEN in "
            ".env before adding anyone else — otherwise signing someone in hands them the "
            "credential that drives the whole box through /dispatch.")
    with connect() as c:
        row = c.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row is not None:
            # Reactivating consumes a seat; leaving an already-active user alone does not.
            if not row["active"]:
                cap = max_users()
                n = c.execute("SELECT COUNT(*) AS n FROM users WHERE active = 1").fetchone()["n"]
                if cap and int(n) >= cap:
                    raise SeatsFull(f"this box is configured for {cap} people")
                c.execute("UPDATE users SET active = 1 WHERE id = ?", (row["id"],))
            return _person(row) | {"active": 1}
        cap = max_users()
        n = c.execute("SELECT COUNT(*) AS n FROM users WHERE active = 1").fetchone()["n"]
        if cap and int(n) >= cap:
            raise SeatsFull(f"this box is configured for {cap} people")
        uid = "usr_" + secrets.token_hex(8)      # same shape as core/connector/seats.py:74
        c.execute("INSERT INTO users (id, email, name, role, active, created_at) "
                  "VALUES (?,?,?,?,1,?)", (uid, email, name, role, _now()))
    return {"id": uid, "email": email, "name": name, "role": role, "active": 1}


def owner_user() -> dict:
    """The box's owner row — created if a database somehow reaches this point without one.

    Migration 47 creates it on every box that boots normally, and `init_db` runs SCHEMA before
    the migrations, so this is belt and braces. It exists because `new_session` now refuses to
    mint a session that belongs to nobody, which means a MISSING ROW would turn `/dash/login`
    into a 500 and lock the owner out of his own box — the one outcome his standing rule
    (2026-09-07, "I don't ever wanna be locked out of these machines") forbids outright. Found
    the honest way: `tests/test_customer_voice_app.py` never calls `init_db`, and the login
    started failing with exactly that 500.

    IT GRANTS NOTHING, and that is what makes it safe rather than a back door. Every caller has
    already proven the box's own `DASH_TOKEN` before reaching here, so this resolves an identity
    that is already established; it does not create a way in. Idempotent by the fixed id, so two
    concurrent logins cannot mint two owners.
    """
    try:
        row = get_user(OWNER_USER_ID)
    except Exception:                        # noqa: BLE001 — a database with no users table yet
        row = None
    if row and row.get("active"):
        return row
    init_db()                                # brings SCHEMA and every migration up to date
    with connect() as c:
        c.execute("INSERT OR IGNORE INTO users (id, email, name, role, active, created_at) "
                  "VALUES (?,?,?,?,1,?)",
                  (OWNER_USER_ID, OWNER_USER_EMAIL, "Owner", "owner", _now()))
        # Repair rather than trust: a row that was somehow left inactive or re-roled would
        # otherwise fail `session_user` on the very next request, with the session already set.
        c.execute("UPDATE users SET active = 1, role = 'owner' WHERE id = ?", (OWNER_USER_ID,))
    return get_user(OWNER_USER_ID)


def _person(row) -> dict:
    """A user row as the rest of the box may see it: EVERYTHING BUT THE PASSWORD HASH.

    `users.pw_hash` arrived in migration 49 and every reader here is `SELECT *`, so without this a
    hash would ride out of `get_user` into a template, a JSON response or a log line the first time
    someone printed a user. Sign-in reads the hash through `password_hash_for` and nothing else does.
    """
    d = dict(row)
    d.pop("pw_hash", None)
    return d


def get_user(user_id: str) -> dict | None:
    if not user_id:
        return None
    with connect() as c:
        row = c.execute("SELECT * FROM users WHERE id = ?", (str(user_id),)).fetchone()
    return _person(row) if row else None


def user_by_email(email: str) -> dict | None:
    email = str(email or "").strip().lower()
    if not email:
        return None
    with connect() as c:
        row = c.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    return _person(row) if row else None


def set_user_active(user_id: str, active: bool) -> None:
    """Revoke (or restore) a person. NEVER a DELETE — standing owner rule, and the sends they
    already made must keep resolving to a real name forever.

    THE OWNER'S OWN ROW CANNOT BE SWITCHED OFF. Every session on the box points at it since
    migration 47, and `session_user` refuses an inactive user — so deactivating it would sign the
    owner out of his own box with no way back in, which is the one outcome his standing rule
    forbids outright.
    """
    if str(user_id) == OWNER_USER_ID and not active:
        raise ValueError("the box owner's own account cannot be deactivated")
    with connect() as c:
        c.execute("UPDATE users SET active = ? WHERE id = ?",
                  (1 if active else 0, str(user_id)))


# ── a password per person, and the only way to get one: an invite (DESIGN_PER_PERSON_LOGIN.md) ──
INVITE_DAYS = 7


def _token_hash(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def list_users() -> list[dict]:
    """Everyone who has ever been let in, owner first, with whether they have set a password —
    never the hash itself."""
    with connect() as c:
        rows = c.execute("SELECT *, (pw_hash IS NOT NULL) AS has_password FROM users "
                         "ORDER BY (role = 'owner') DESC, created_at").fetchall()
    return [_person(r) for r in rows]


def password_hash_for(email: str) -> tuple[dict, str] | None:
    """(person, hash) for an ACTIVE person with a password, or None. The one reader of pw_hash.

    Revoked people and invited-but-not-joined people both return None, so sign-in cannot tell them
    apart from an address nobody holds — and must not, or the form becomes a list of who works here.
    """
    email = str(email or "").strip().lower()
    if not email:
        return None
    with connect() as c:
        row = c.execute("SELECT * FROM users WHERE email = ? AND active = 1 AND pw_hash IS NOT NULL",
                        (email,)).fetchone()
    return (_person(row), str(row["pw_hash"])) if row else None


def set_password(user_id: str, pw_hash: str) -> None:
    """Give a person a (new) password and END EVERY SESSION THEY ALREADY HOLD.

    A password is changed because the old one may be known to someone else; a session that
    survives the change would keep that someone signed in. Sessions are ended by expiring them,
    not deleting rows (standing owner rule: nothing is deleted).
    """
    now = _now()
    with connect() as c:
        c.execute("UPDATE users SET pw_hash = ? WHERE id = ?", (str(pw_hash), str(user_id)))
        c.execute("UPDATE sessions SET expires_at = ? WHERE user_id = ? AND expires_at > ?",
                  (now, str(user_id), now))


def create_invite(user_id: str, *, created_by: str | None = None) -> str:
    """Mint a single-use sign-up link token for an ACTIVE person. Returns the token; stores its hash.

    A NEW INVITE RETIRES EVERY EARLIER UNUSED ONE for the same person. An owner re-invites because a
    link went to the wrong place or was lost, and the old link must stop working when he does.
    """
    who = get_user(user_id)
    if not who or not who.get("active"):
        raise ValueError("an invite needs an active person")
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    with connect() as c:
        c.execute("UPDATE user_invites SET used_at = ? WHERE user_id = ? AND used_at IS NULL",
                  (now.isoformat(), str(user_id)))
        c.execute("INSERT INTO user_invites (id, user_id, token_hash, created_by, created_at, "
                  "expires_at) VALUES (?,?,?,?,?,?)",
                  ("inv_" + secrets.token_hex(8), str(user_id), _token_hash(token), created_by,
                   now.isoformat(), (now + timedelta(days=INVITE_DAYS)).isoformat()))
    return token


_INVITE_OPEN = ("FROM user_invites i JOIN users u ON u.id = i.user_id "
                "WHERE i.token_hash = ? AND i.used_at IS NULL AND i.expires_at > ? AND u.active = 1")


def invite_user(token: str) -> dict | None:
    """The person a still-usable invite is for, or None. Never says WHY it is unusable."""
    if not str(token or "").strip():
        return None
    with connect() as c:
        row = c.execute("SELECT u.* " + _INVITE_OPEN, (_token_hash(token), _now())).fetchone()
    return _person(row) if row else None


def redeem_invite(token: str, pw_hash: str) -> dict | None:
    """Use an invite ONCE: set that person's password and end their other sessions. None if unusable.

    THE UPDATE IS THE CHECK. `used_at IS NULL` is re-tested in the statement that sets it, so of two
    concurrent submits exactly one changes a row; the other gets None and sets nothing.
    """
    if not str(token or "").strip():
        return None
    now = _now()
    with connect() as c:
        row = c.execute("SELECT i.id AS invite_id, u.id AS user_id " + _INVITE_OPEN,
                        (_token_hash(token), now)).fetchone()
        if not row:
            return None
        used = c.execute("UPDATE user_invites SET used_at = ? WHERE id = ? AND used_at IS NULL",
                         (now, row["invite_id"]))
        if used.rowcount != 1:
            return None
        c.execute("UPDATE users SET pw_hash = ? WHERE id = ?", (str(pw_hash), row["user_id"]))
        c.execute("UPDATE sessions SET expires_at = ? WHERE user_id = ? AND expires_at > ?",
                  (now, row["user_id"], now))
    return get_user(row["user_id"])


def touch_user(user_id: str) -> None:
    if not user_id:
        return
    with connect() as c:
        c.execute("UPDATE users SET last_seen_at = ? WHERE id = ?", (_now(), str(user_id)))


# ── spend ledger ──────────────────────────────────────────────────────────────
def record_spend(*, task, model, cost_usd, job_id=None, input_tokens=0,
                 output_tokens=0, cache_write_tokens=0, cache_read_tokens=0) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO spend_ledger (ts, job_id, task, model, input_tokens, "
            "output_tokens, cache_write_tokens, cache_read_tokens, cost_usd) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (_now(), job_id, task, model, input_tokens, output_tokens,
             cache_write_tokens, cache_read_tokens, cost_usd),
        )


def spend_since(iso_start: str) -> float:
    with connect() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM spend_ledger WHERE ts >= ?",
            (iso_start,),
        ).fetchone()
        return float(row["total"])


def spend_for_job(job_id: str) -> float:
    """Total think() spend attributed to one job (the dashboard's per-reel cost)."""
    with connect() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM spend_ledger "
            "WHERE job_id = ?", (job_id,)).fetchone()
        return float(row["total"])


# ── vendor meter (units, not USD — see vendor_ledger DDL) ────────────────────--
def record_vendor_usage(vendor: str, units: float = 1, *, job_id: str | None = None,
                        idem_key: str | None = None, note: str | None = None) -> bool:
    """Record vendor usage. With idem_key, the write is EXACTLY-ONCE — a repeat with
    the same key is a silent no-op (INSERT OR IGNORE on the unique index). Every
    charge/refund tied to a specific paid action must pass one (convention:
    "{job_id}:{step}" for charges, "{job_id}:{step}:refund" for refunds). Returns
    True if a row was written, False if the key had already been recorded."""
    with connect() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO vendor_ledger (ts, vendor, units, job_id, idem_key, note) "
            "VALUES (?,?,?,?,?,?)",
            (_now(), vendor, units, job_id, idem_key, note),
        )
        return cur.rowcount > 0


def vendor_units_for_job_by_vendor(job_id: str) -> dict:
    """Net vendor units attributed to one job (refunds are negative rows), keyed by
    vendor — the vendor half of a per-reel cost line."""
    with connect() as c:
        rows = c.execute(
            "SELECT vendor, COALESCE(SUM(units), 0) AS total FROM vendor_ledger "
            "WHERE job_id = ? GROUP BY vendor", (job_id,)).fetchall()
        return {r["vendor"]: float(r["total"]) for r in rows}


def vendor_usage_since(vendor: str, iso_start: str) -> float:
    with connect() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(units), 0) AS total FROM vendor_ledger "
            "WHERE vendor = ? AND ts >= ?",
            (vendor, iso_start),
        ).fetchone()
        return float(row["total"])


def vendor_units_for_job(vendor: str, job_id: str) -> float:
    """Net units this job has already been charged for a vendor. Reconcile uses it to
    avoid double-recording an adopted render; the refund path uses it to never refund
    a job that was never charged."""
    with connect() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(units), 0) AS total FROM vendor_ledger "
            "WHERE vendor = ? AND job_id = ?",
            (vendor, job_id),
        ).fetchone()
        return float(row["total"])


# ── jobs / queue substrate ──────────────────────────────────────────────────--
def create_job(*, idempotency_key, agent_name=None, intent=None, raw_text=None,
               slack_channel_id=None, slack_thread_ts=None, slack_user_id=None):
    """Insert a job. If idempotency_key already exists, return the existing row.

    Returns (job_dict, created: bool). This is the strict-idempotency guarantee:
    a retried dispatch never double-creates work. slack_user_id is the ACTOR (who
    triggered it) — used to gate owner-only actions (spend/post).
    """
    jid, now = str(uuid.uuid4()), _now()
    with connect() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO jobs (id, idempotency_key, agent_name, intent, "
            "raw_text, slack_channel_id, slack_thread_ts, slack_user_id, status, "
            "created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?, 'queued', ?, ?)",
            (jid, idempotency_key, agent_name, intent, raw_text, slack_channel_id,
             slack_thread_ts, slack_user_id, now, now),
        )
        if cur.rowcount == 0:  # idempotency_key already present
            row = c.execute("SELECT * FROM jobs WHERE idempotency_key = ?",
                            (idempotency_key,)).fetchone()
            return dict(row), False
        row = c.execute("SELECT * FROM jobs WHERE id = ?", (jid,)).fetchone()
        return dict(row), True


def get_job(job_id: str):
    with connect() as c:
        row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None


# Columns update_job is allowed to write. Defense in depth: the SET clause is built
# from dict keys, so even though every caller today passes code-controlled keys, an
# allow-list guarantees a future caller can never turn a forwarded user key into SQL
# injection. Values are always parameterized; this constrains the identifiers.
_UPDATABLE_JOB_COLUMNS = frozenset({
    "status", "result", "error", "intent", "attempts",
    "agent_name", "raw_text", "slack_channel_id", "updated_at"
})


def update_job(job_id: str, **fields) -> None:
    if not fields:
        return
    bad = set(fields) - _UPDATABLE_JOB_COLUMNS
    if bad:
        raise ValueError(f"update_job: disallowed column(s) {sorted(bad)}")
    fields["updated_at"] = _now()
    assignment = ", ".join(f"{k} = ?" for k in fields)
    with connect() as c:
        # A5 (audit): a job that reached 'done' has already had its (possibly PAID,
        # possibly irreversible) side effects — a later failure write (a notifier raise
        # landing in the worker's generic except, a reaper race) must never flip it to
        # 'failed' and tell the owner their succeeded render/send failed. Terminal-done
        # is terminal. Suppressions are logged, never silent.
        if fields.get("status") == "failed":
            cur = c.execute(
                f"UPDATE jobs SET {assignment} WHERE id = ? AND status != 'done'",
                (*fields.values(), job_id))
            if cur.rowcount == 0:
                log.warning("state.done_to_failed_suppressed", job_id=job_id)
            return
        c.execute(f"UPDATE jobs SET {assignment} WHERE id = ?", (*fields.values(), job_id))



# ── per-platform publish outcome (migration 36) ───────────────────────────────────────────

def record_post_targets(script_id: str, platforms, post_id: str | None) -> None:
    """Record which platforms a post was SUBMITTED to. Never downgrades a settled row.

    `published` is terminal: once a network has the video, nothing may move it back to
    `submitted`, or a later pass would re-attempt a platform that already has the content.
    """
    now = _now()
    with connect() as c:
        for p in platforms:
            c.execute(
                "INSERT INTO reel_post_targets (script_id, platform, state, post_id, "
                "submitted_at) VALUES (?, ?, 'submitted', ?, ?) "
                "ON CONFLICT(script_id, platform) DO UPDATE SET "
                "  post_id=excluded.post_id, submitted_at=excluded.submitted_at, "
                "  state=CASE WHEN reel_post_targets.state='published' THEN 'published' "
                "             ELSE 'submitted' END, "
                "  settled_at=CASE WHEN reel_post_targets.state='published' "
                "                  THEN reel_post_targets.settled_at ELSE NULL END",
                (script_id, str(p), post_id, now))


def settle_post_target(script_id: str, platform: str, state: str,
                       detail: str | None = None) -> None:
    """Mark one (script, platform) settled: `published`, `failed`, or `pending`.

    `pending` is a SETTLED READING OF AN UNSETTLED LEG — we asked and Zernio had not decided.
    It is deliberately neither of the other two: the retry button offers only `failed`, so no
    machine resends a leg that may still be in flight, while the owner's deliberate pick (which
    is blocked only by `claimed`) can still force it. Leaving it `claimed` would strand it
    forever; calling it `failed` would let an automatic retry double-post it in public.
    """
    with connect() as c:
        c.execute("UPDATE reel_post_targets SET state=?, detail=?, settled_at=? "
                  "WHERE script_id=? AND platform=?",
                  (state, (detail or "")[:200], _now(), script_id, platform))


def post_targets(script_id: str) -> list:
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM reel_post_targets WHERE script_id=? ORDER BY platform",
            (script_id,)).fetchall()]


def scripts_awaiting_settle(max_age_h: int = 24) -> list:
    """Rows with a Zernio post id, at least one UNSETTLED platform, and no report sent yet.

    Bounded by age so the settle pass never walks the whole history — a post that never
    settles within the window is reported on the grace path and stops being scanned.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_h)).isoformat()
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT s.* FROM reel_scripts s WHERE s.social_post_id IS NOT NULL "
            "  AND s.post_report_at IS NULL AND s.posted_at > ? "
            "  AND EXISTS (SELECT 1 FROM reel_post_targets t "
            "              WHERE t.script_id = s.id AND t.settled_at IS NULL)",
            (cutoff,)).fetchall()]


def mark_post_reported(script_id: str) -> bool:
    """Stamp the report as delivered. Returns True only for the pass that won the race —
    two workers settling the same row must not both post the report to Slack."""
    with connect() as c:
        cur = c.execute("UPDATE reel_scripts SET post_report_at=?, updated_at=? "
                        "WHERE id=? AND post_report_at IS NULL", (_now(), _now(), script_id))
        return cur.rowcount > 0


def published_platforms(script_id: str) -> set:
    """Platforms that PROVABLY have this video. The subtraction set for any re-publish."""
    with connect() as c:
        return {r["platform"] for r in c.execute(
            "SELECT platform FROM reel_post_targets WHERE script_id=? AND state='published'",
            (script_id,)).fetchall()}


def claim_post_platforms(script_id: str, platforms, *, deliberate: bool = False) -> list:
    """Atomically claim the platforms this re-publish may send to. Returns only what WE won.

    THE DOUBLE-POST GUARANTEE LIVES HERE, and it is the one thing in the re-publish feature
    that can cause irreversible harm. A row may fire any number of times; a NETWORK must
    receive the video once. So the claim is per (script, platform) and it refuses two states:

      * `published` — that network already has it. Nothing may re-open it, ever.
      * `claimed`   — another trigger is mid-flight with it right now.

    One statement per platform, and the WHERE clause is what decides — never a read-then-write,
    which is exactly the race a second Airtable sweep 120 seconds later would win.
    """
    # `deliberate` is the OWNER NAMING A PLATFORM BY HAND on a freshly re-armed trigger. It is
    # the one case where a platform we recorded as `published` may be claimed again, and it
    # exists because the record can be stale in a way only he can see: he deleted the post ON
    # the network. Refusing him there is not safety, it is the machine insisting it knows
    # better about a thing it cannot observe.
    #
    # It does NOT weaken the guarantee, because the guarantee is about ACCIDENTS: a trigger
    # that fires twice, a sweep that races, a stale picker steering a button. `claimed` is
    # STILL refused even here — an in-flight post is never sent twice, whoever asks — and the
    # Slack retry button never sets this flag at all.
    blocked = "('claimed')" if deliberate else "('published', 'claimed')"
    now, won = _now(), []
    with connect() as c:
        for p in platforms:
            cur = c.execute(
                "INSERT INTO reel_post_targets (script_id, platform, state, submitted_at) "
                "VALUES (?, ?, 'claimed', ?) "
                "ON CONFLICT(script_id, platform) DO UPDATE SET "
                "  state='claimed', submitted_at=excluded.submitted_at, settled_at=NULL "
                f"WHERE reel_post_targets.state NOT IN {blocked}",
                (script_id, str(p), now))
            if cur.rowcount:
                won.append(str(p))
    return won


def release_post_platforms(script_id: str, platforms) -> None:
    """Hand back a claim after a DETERMINATE failure — nothing was sent, so the platform must
    be attemptable again. Never called on an indeterminate failure: a post that MAY have landed
    keeps its claim, because the cost of being wrong is a duplicate public post."""
    with connect() as c:
        for p in platforms:
            c.execute("UPDATE reel_post_targets SET state='failed', settled_at=? "
                      "WHERE script_id=? AND platform=? AND state='claimed'",
                      (_now(), script_id, str(p)))

def claim_next_job():
    """Atomically claim the oldest CLAIMABLE queued job (-> running). Returns dict
    or None. Claimable = status 'queued' AND its backoff window (not_before) has
    passed — a freshly requeued retry stays invisible until then.

    A single UPDATE ... WHERE id=(SELECT ... LIMIT 1) ... RETURNING * does the claim
    in one statement, so two concurrent claimers can never receive the same job:
    the write lock serializes them, and the second claimer's subquery sees the first
    job already 'running' and picks the next one (or returns nothing). Requires
    SQLite >= 3.35 (RETURNING); Ubuntu 24 and Python 3.12 both ship newer.
    """
    now = _now()
    with connect() as c:
        row = c.execute(
            "UPDATE jobs SET status='running', attempts = attempts + 1, updated_at = ? "
            "WHERE id = (SELECT id FROM jobs WHERE status='queued' "
            "             AND (not_before IS NULL OR not_before <= ?) "
            "           ORDER BY created_at LIMIT 1) "
            "  AND status='queued' "
            "RETURNING *",
            (now, now),
        ).fetchone()
        return dict(row) if row else None


def requeue_job(job_id: str, *, refund_attempt: bool = True,
                not_before: str | None = None) -> None:
    """Put a job back to 'queued'. refund_attempt=True (budget pause) gives the
    attempt back; not_before makes the job invisible to claim_next until that time —
    the retry backoff that stops a single worker burning all attempts in seconds."""
    with connect() as c:
        if refund_attempt:
            c.execute(
                "UPDATE jobs SET status='queued', attempts = MAX(0, attempts - 1), "
                "not_before = ?, updated_at = ? WHERE id = ?",
                (not_before, _now(), job_id))
        else:
            c.execute(
                "UPDATE jobs SET status='queued', not_before = ?, updated_at = ? "
                "WHERE id = ?", (not_before, _now(), job_id))


def reap_orphan_jobs(stale_after_s: int = 1800, max_attempts: int | None = None
                     ) -> tuple[list[dict], list[dict]]:
    """Handle 'running' jobs whose updated_at is older than the threshold — the
    worker that claimed them died mid-job (kill -9, OOM, box reboot). Returns
    (requeued_jobs, failed_jobs) so the caller can fire module failure hooks for
    the failed ones (the DB rows are already final when this returns).

    Staleness is liveness, not duration: the worker's heartbeat thread touches the
    current job's updated_at every beat (touch_job), so a legitimately long render
    stays fresh for hours while a dead worker's job goes stale in exactly
    stale_after_s. The threshold therefore does NOT need to exceed the longest job.

    max_attempts closes the poison-job loop: a job that KILLS the worker never
    raises, so the worker-side retry cap can't fire — without this check here, the
    cycle claim → crash → restart → reap → requeue spins forever, taking the worker
    down every lap. Jobs at/over the cap are terminally failed instead of requeued.
    Pass None (the watchdog does) to only requeue — terminal-failing belongs to the
    worker process, the only one with module failure hooks loaded.

    The attempt is never refunded; resumability (checkpoints) means a re-run skips
    completed paid steps.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=stale_after_s)).isoformat()
    requeued, failed = [], []
    with connect() as c:
        stale = c.execute("SELECT * FROM jobs WHERE status='running' AND updated_at < ?",
                          (cutoff,)).fetchall()
        for row in stale:
            job = dict(row)
            if max_attempts is not None and job["attempts"] >= max_attempts:
                c.execute(
                    "UPDATE jobs SET status='failed', error = ?, updated_at = ? "
                    "WHERE id = ? AND status='running'",
                    (f"reaped: worker died mid-job; {job['attempts']} attempts "
                     "exhausted", _now(), job["id"]))
                failed.append(job)
            else:
                c.execute(
                    "UPDATE jobs SET status='queued', not_before = NULL, updated_at = ? "
                    "WHERE id = ? AND status='running'", (_now(), job["id"]))
                requeued.append(job)
    return requeued, failed


def touch_job(job_id: str) -> None:
    """Freshen a RUNNING job's updated_at — called from the worker's heartbeat
    thread while a job executes, so reap_orphan_jobs never mistakes a legitimately
    long job (a composite render holds process_one() for many minutes) for an
    orphan. Guarded on status so a touch can never resurrect a finished row."""
    with connect() as c:
        c.execute("UPDATE jobs SET updated_at = ? WHERE id = ? AND status='running'",
                  (_now(), job_id))


# ── checkpoints (resumability — see docs/HANDLER_CONTRACT.md) ────────────────--
def save_checkpoint(job_id: str, step: str, value) -> None:
    """Record the output of a completed step so a requeued handler can skip it.
    `value` must be JSON-serializable (it is the step's result, e.g. a dict)."""
    with connect() as c:
        c.execute(
            "INSERT INTO job_checkpoints (job_id, step, value, ts) VALUES (?,?,?,?) "
            "ON CONFLICT(job_id, step) DO UPDATE SET value=excluded.value, ts=excluded.ts",
            (job_id, step, json.dumps(value), _now()))


def load_checkpoint(job_id: str, step: str, default=None):
    """Return a completed step's stored output, or `default` when the step hasn't run.

    PRESENCE is decided by the row, never by truthiness of the value: a stored None/
    null is a legitimate completed result (e.g. "no transcript — reel had no speech")
    and must NOT look absent, or the step re-runs and re-spends on every re-entry.
    Callers that store possibly-null results pass default=state.MISSING and test
    `is MISSING` (Handler Contract Rule 1.4)."""
    with connect() as c:
        row = c.execute(
            "SELECT value FROM job_checkpoints WHERE job_id = ? AND step = ?",
            (job_id, step)).fetchone()
        if row is None:
            return default
        return json.loads(row["value"]) if row["value"] is not None else None


# ── heartbeats + alert state (for the watchdog) ─────────────────────────────--
def heartbeat(component: str, status: str) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO heartbeats (component, ts, status) VALUES (?,?,?) "
            "ON CONFLICT(component) DO UPDATE SET ts = excluded.ts, status = excluded.status",
            (component, _now(), status),
        )


def get_heartbeats() -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM heartbeats").fetchall()]


def get_alert(key: str):
    with connect() as c:
        row = c.execute("SELECT state FROM alert_state WHERE key = ?", (key,)).fetchone()
        return row["state"] if row else None


def get_alert_row(key: str):
    with connect() as c:
        row = c.execute("SELECT * FROM alert_state WHERE key = ?", (key,)).fetchone()
        return dict(row) if row else None


def clear_alert(key: str) -> None:
    """Forget a probe entirely — used when it is SILENCED, not when it recovers.

    Deleting the row rather than flipping it to OK matters: an OK row would emit a
    ':white_check_mark: recovered' DM it never earned, and a lingering FAIL row would keep
    the silenced condition on the standing-failure digest forever. Silence means gone from
    the operator's view, not quietly re-labelled.
    """
    with connect() as c:
        c.execute("DELETE FROM alert_state WHERE key = ?", (key,))


def failing_alerts() -> list[dict]:
    """Every probe currently in FAIL, oldest failure first — the standing-failure digest's
    input. Ordered by `since_ts` because how LONG something has been broken is the ranking
    that matters once more than one thing is down."""
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT key, state, since_ts, last_alert_ts FROM alert_state "
            "WHERE state = 'FAIL' ORDER BY since_ts")]


def set_alert(key: str, state_value: str, *, bump_alert_ts: bool = False) -> None:
    """Persist alert state. `bump_alert_ts=True` records that we just notified the
    operator (used to space out the 'still down' reminders). `since_ts` is set when
    a key first enters FAIL and preserved while it stays FAIL."""
    now = _now()
    with connect() as c:
        existing = c.execute(
            "SELECT state, since_ts, last_alert_ts FROM alert_state WHERE key = ?",
            (key,)).fetchone()
        if existing and existing["state"] == state_value:
            since = existing["since_ts"] or now
        else:
            since = now  # state changed → reset the since timestamp
        last_alert = now if bump_alert_ts else (existing["last_alert_ts"] if existing else None)
        c.execute(
            "INSERT INTO alert_state (key, state, since_ts, last_alert_ts) VALUES (?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET state = excluded.state, "
            "since_ts = excluded.since_ts, last_alert_ts = excluded.last_alert_ts",
            (key, state_value, since, last_alert),
        )
