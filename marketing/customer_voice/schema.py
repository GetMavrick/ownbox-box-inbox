"""Base DDL for the Unified Inbox machine (package customer_voice) — the tables this machine owns, moved here verbatim from core/state.py.

A MACHINE DECLARES ITS TABLES THE WAY IT DECLARES ITS TOOLS. `core/state.py` holds the kernel only;
this package calls `state.register_schema("customer_voice", DDL)` at import, and a golden image creates
exactly the tables of the machines it loads (docs/SPEC_GOLDEN_DROPLET_IMPACT.md §5). Nothing is
dropped on a box that already has more tables than it uses — the split is additive.

THIS IS THE *BASE* SHAPE, deliberately. state.py's governing rule still holds: a change to a shipped
table is a migration, never an edit here — a fresh box gets this DDL and then replays this machine's
tagged migrations exactly as an old box did. Do not modernise a column in this file; add a step in
core/state.py and tag it with this machine in _MIGRATION_OWNER.
"""

DDL = """

-- CUSTOMER VOICE (marketing/customer_voice/, docs/PLAN_CUSTOMER_VOICE.md Stage 1). Four tables,
-- all in SCHEMA and none in MIGRATIONS: a brand-new table needs no version number, and two
-- parallel branches must never both claim one (the rule above, and the _migration_28 incident).
--
-- RAIL OWNERSHIP IS DECLARED, NEVER INFERRED. The box cannot know that a restaurant has an
-- unconnected Yelp listing while a B2B consultancy never will. Guess in the safe-looking
-- direction and every clone nags "connect your Yelp" forever on a rail the business does not
-- use. `owned` is the owner's own answer; NULL means he has not been asked yet.
CREATE TABLE IF NOT EXISTS voice_rails (
  rail         TEXT PRIMARY KEY,        -- uptime | pagespeed | reviews | comments | seo | competitors
  owned        INTEGER,                 -- 1 yes, 0 no, NULL not asked
  connected_at TEXT,                    -- when a credential first worked; NULL = not connected
  updated_at   TEXT NOT NULL
);

-- PER-SOURCE HEALTH, so a source that FAILED is distinguishable from one with nothing to say.
-- Without this "0 new reviews" and "our token expired since Tuesday" render identically, and an
-- owner who cannot tell them apart stops trusting both.
CREATE TABLE IF NOT EXISTS voice_sources (
  source      TEXT PRIMARY KEY,
  last_ok_at  TEXT,
  last_try_at TEXT,
  last_error  TEXT,                     -- NULL when the last attempt succeeded
  updated_at  TEXT NOT NULL
);

-- WHAT A SOURCE SAW. UNIQUE(source, external_id) is LOAD-BEARING, not hygiene: every source is
-- polled over overlapping windows, so without it the weekly page counts the same review three
-- times. `external_id` is the vendor's own id where there is one, and a deterministic key
-- (e.g. "uptime:2026-09-09T08:00") where there is not.
CREATE TABLE IF NOT EXISTS voice_observations (
  id          TEXT PRIMARY KEY,
  source      TEXT NOT NULL,
  external_id TEXT NOT NULL,
  observed_at TEXT NOT NULL,            -- when the THING happened, per the source
  recorded_at TEXT NOT NULL,            -- when this box learned of it
  kind        TEXT NOT NULL,            -- check | review | comment | ranking | …
  value       REAL,                     -- the number, when the observation is one
  payload     TEXT,                     -- JSON, no contact values (masked above the page)
  UNIQUE (source, external_id)
);
CREATE INDEX IF NOT EXISTS ix_voice_obs_source ON voice_observations (source, observed_at);

-- THE COMPETITOR ROSTER — bounded, and OWNER-NAMED. The box has no way to decide who a business
-- competes with: the three shops on the same street, the national chain, or neither. Guessing
-- makes the most motivating page in the product also the least trustworthy one, so the roster is
-- his list and nothing else populates it.
--
-- `place_id` is RESOLVED ONCE and then reused: a text search costs a metered Places call, and
-- re-searching a competitor every month to learn the id we already had is the same money spent
-- twice. `is_self` marks his own listing, because the whole point is the comparison.
CREATE TABLE IF NOT EXISTS voice_competitors (
  slug          TEXT PRIMARY KEY,       -- stable key derived from his roster entry
  label         TEXT NOT NULL,          -- what HE called it
  query         TEXT NOT NULL,          -- the Places text query this resolves through
  place_id      TEXT,                   -- resolved once; NULL until the first successful search
  is_self       INTEGER NOT NULL DEFAULT 0,
  rating        REAL,
  reviews_total INTEGER,
  checked_at    TEXT,
  last_error    TEXT,
  updated_at    TEXT NOT NULL
);

-- A NUMBER FOR A PERIOD IS A FACT THAT GETS CORRECTED. GA4 and Search Console backfill for ~48h,
-- so this OVERWRITES by primary key rather than accumulating: the latest reading of a given
-- (source, metric, day) is the true one, and saying so out loud is what keeps it trustworthy.
CREATE TABLE IF NOT EXISTS voice_metrics (
  source     TEXT NOT NULL,
  metric     TEXT NOT NULL,
  day        TEXT NOT NULL,
  value      REAL NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (source, metric, day)
);

-- Unified Inbox (docs/UNIFIED_INBOX_SPEC.md, Phase 1 — Messenger first). NEW tables,
-- so they live in SCHEMA (no migration; the state.py governing rule). `space` on every
-- row is the tenant-isolation boundary; zernio ids are the vendor-side keys.
CREATE TABLE IF NOT EXISTS inbox_conversations (
  id             TEXT PRIMARY KEY,
  space          TEXT NOT NULL,
  zernio_conversation_id TEXT NOT NULL,
  platform       TEXT NOT NULL DEFAULT 'messenger',
  ad_meta_id     TEXT,                 -- CTM attribution (metadata.referral)
  ad_title       TEXT,
  participant    TEXT,                 -- display name/handle when the payload carries one
  last_inbound_at TEXT,               -- drives the 24h window (window.py)
  opted_out      INTEGER NOT NULL DEFAULT 0,
  opener_sent_at TEXT,                -- the ONE-opener-per-conversation claim (atomic UPDATE)
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  UNIQUE (space, zernio_conversation_id)
);

CREATE TABLE IF NOT EXISTS inbox_messages (
  id          TEXT PRIMARY KEY,
  space       TEXT NOT NULL,
  zernio_conversation_id TEXT NOT NULL,
  zernio_message_id TEXT UNIQUE,      -- vendor id → mirror is naturally idempotent
  direction   TEXT NOT NULL,          -- 'in' | 'out'
  sent_by     TEXT,                   -- 'contact' | 'ai' | 'human'
  body        TEXT,
  created_at  TEXT NOT NULL
);

-- Exactly-once + audit for every send (spec §8): idem_key UNIQUE means a requeue can
-- never double-send; status records sending|ok|indeterminate|failed for reconcile.
-- A human-sent reply CLAIMS the key as 'sending' before the vendor call and resolves it
-- after (`inbox/store.py: claim_send`), so two concurrent POSTs make one vendor call.
CREATE TABLE IF NOT EXISTS inbox_send_ledger (
  id          TEXT PRIMARY KEY,
  space       TEXT NOT NULL,
  zernio_conversation_id TEXT NOT NULL,
  idem_key    TEXT UNIQUE NOT NULL,
  kind        TEXT NOT NULL,          -- 'opener' (Phase 1) | 'reply' (Phase 2)
  zernio_message_id TEXT,
  status      TEXT NOT NULL,          -- 'sending' | 'ok' | 'indeterminate' | 'failed'
  error       TEXT,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_inbox_ledger_space_ts ON inbox_send_ledger (space, created_at);

-- ── WHAT THE MACHINE WOULD SAY (docs ruling, OSDev1 2026-09-15 07:17) ─────────────────────
-- SCHEMA, not MIGRATIONS: a brand-new table needs no version number (the rule above).
--
-- A DRAFT IS NOT A MESSAGE. It is a suggestion sitting on a screen, and the only thing that
-- can turn it into a message is a person tapping send -- which goes through
-- `inbox/reply.py` exactly as a reply they typed themselves does. Nothing here sends, nothing
-- here is sendable, and no row in this table is visible to a customer.
--
-- KEYED ON THE MESSAGE IT ANSWERS, not on the conversation. A draft written for "are you open
-- Sunday?" is worthless once they have sent three more messages, and a draft that silently
-- answers the wrong question is worse than none. UNIQUE on the inbound id also makes drafting
-- exactly-once per inbound: a sweep that runs twice cannot spend twice.
CREATE TABLE IF NOT EXISTS inbox_drafts (
  id          TEXT PRIMARY KEY,
  space       TEXT NOT NULL,
  zernio_conversation_id TEXT NOT NULL,
  in_reply_to TEXT NOT NULL,          -- the inbound message id this answers
  body        TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  dismissed_at TEXT,                  -- he said no. Kept, never deleted -- it is the record
  UNIQUE (space, in_reply_to)
);
CREATE INDEX IF NOT EXISTS ix_inbox_drafts_conv
  ON inbox_drafts (space, zernio_conversation_id, created_at);

-- WHAT THE BUSINESS ACTUALLY SENT, next to what the box drafted for it. Written once, at the
-- moment a person presses send on a conversation the box had drafted for: the draft as it was,
-- the reply as it went out, and whether they differ. Every edit is the style guide nobody had to
-- write; every unedited send says "that was right". The drafter reads the newest of these back
-- as examples, so the box writes more like this business the more it is used — no fine-tuning,
-- no setup screen, and nothing leaves the box.
--
-- ONE LESSON PER DRAFT (UNIQUE on draft_id): a second send on the same conversation, or a retry,
-- teaches nothing new and must not weigh the examples twice. Kept, never deleted -- like drafts.
CREATE TABLE IF NOT EXISTS inbox_draft_lessons (
  id          TEXT PRIMARY KEY,
  space       TEXT NOT NULL,
  draft_id    TEXT NOT NULL,
  zernio_conversation_id TEXT NOT NULL,
  asked       TEXT,                   -- the customer's message the draft answered
  draft_body  TEXT NOT NULL,
  sent_body   TEXT NOT NULL,
  edited      INTEGER NOT NULL,       -- 1 when sent_body differs from draft_body
  created_at  TEXT NOT NULL,
  UNIQUE (space, draft_id)
);
CREATE INDEX IF NOT EXISTS ix_inbox_draft_lessons_space
  ON inbox_draft_lessons (space, edited, created_at);

-- Poll watermark: the newest vendor message id seen per conversation (+ the raw
-- activity marker so an unchanged conversation costs zero message fetches).
CREATE TABLE IF NOT EXISTS inbox_state (
  space       TEXT NOT NULL,
  zernio_conversation_id TEXT NOT NULL,
  last_seen_msg_id TEXT,
  last_activity    TEXT,
  updated_at  TEXT NOT NULL,
  PRIMARY KEY (space, zernio_conversation_id)
);
"""
