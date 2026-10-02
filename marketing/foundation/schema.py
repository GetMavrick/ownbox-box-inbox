"""The marketing foundation's own tables: a site's day, as numbers (docs/PLAN_ANALYTICS_FOUNDATION_PHASE1.md §2).

The outreach source's tables (`out_*`) follow the same rules: keyed by Instantly's campaign id, replaced per
(campaign, day) and per (campaign, week), read through the seam.

One row per site and day, and the day's named things (conversions, pages, UTM sources, referring sites) in one
items table keyed by kind. Every row carries its site, so two sites on one box never mix. A re-sync of a day
replaces that day's rows (marketing/foundation/store.py), so the store is idempotent per (site, day).

NAMES ARE FOR KEEPS: `web_*` stays the storage name whatever the code is later called (a one-shot rename
migration splits data on box_update's auto-rollback).
"""
DDL = """
CREATE TABLE IF NOT EXISTS web_site_days (
    site       TEXT NOT NULL,
    day        TEXT NOT NULL,                 -- YYYY-MM-DD, the buyer's calendar day
    pageviews  INTEGER NOT NULL DEFAULT 0,
    visitors   INTEGER NOT NULL DEFAULT 0,    -- distinct people
    sessions   INTEGER NOT NULL DEFAULT 0,    -- distinct visits
    converted  INTEGER NOT NULL DEFAULT 0,    -- sessions that converted, summed over the conversions
    synced_at  TEXT NOT NULL,
    PRIMARY KEY (site, day)
);
CREATE TABLE IF NOT EXISTS web_site_day_items (
    site   TEXT NOT NULL,
    day    TEXT NOT NULL,
    kind   TEXT NOT NULL,                     -- conversion | page | utm | referrer
    name   TEXT NOT NULL,                     -- the conversion's name, the path, the utm_source, the domain
    n      INTEGER NOT NULL DEFAULT 0,        -- sessions (conversion, page, utm) or visits (referrer)
    detail TEXT,                              -- conversion: the event count; referrer: JSON [[path, views], ...]
    PRIMARY KEY (site, day, kind, name)
);
CREATE INDEX IF NOT EXISTS idx_web_site_day_items_day ON web_site_day_items (site, day, kind);
CREATE TABLE IF NOT EXISTS web_site_day_search (
    site        TEXT NOT NULL,
    day         TEXT NOT NULL,
    clicks      INTEGER NOT NULL DEFAULT 0,
    impressions INTEGER NOT NULL DEFAULT 0,
    position    REAL,
    synced_at   TEXT NOT NULL,
    PRIMARY KEY (site, day)
);
-- outbound_mail.py: one email to one person who asked for it. The consent is on the row, so "why did this address
-- get mail?" always has an answer. idem_key is the caller's exactly-once key; transport_key is the one handed to the
-- email service, new for each attempt after a definite failure. 'unknown' is never sent again.
CREATE TABLE IF NOT EXISTS outbound_mail (
    idem_key      TEXT PRIMARY KEY,
    machine       TEXT NOT NULL,
    to_addr       TEXT NOT NULL,
    subject       TEXT NOT NULL,
    body_text     TEXT NOT NULL,
    body_html     TEXT NOT NULL DEFAULT '',
    sender_name   TEXT NOT NULL DEFAULT '',
    consent       TEXT NOT NULL,              -- JSON {"conversation", "message"}: where the person asked
    status        TEXT NOT NULL,              -- queued | sending | sent | failed | unknown
    attempt       INTEGER NOT NULL DEFAULT 1, -- bumped by a new send() after a definite failure
    transport_key TEXT NOT NULL,              -- outbound:<idem_key>:<attempt>
    tries         INTEGER NOT NULL DEFAULT 0, -- transient retries within this attempt
    next_at       TEXT,                       -- when a queued email may go
    last_try_at   TEXT,                       -- counted against the hourly cap
    message_id    TEXT,
    error         TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outbound_mail_due ON outbound_mail (status, next_at);
CREATE INDEX IF NOT EXISTS idx_outbound_mail_tried ON outbound_mail (last_try_at);
-- outreach_store.py: the outreach source (Instantly), read only. Every row is keyed by Instantly's campaign id, never
-- its name, so a renamed campaign keeps its history (OSDev1's review); the name is a label, refreshed each pull.
CREATE TABLE IF NOT EXISTS out_campaigns (
    campaign_id TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    status      INTEGER,                      -- Instantly's: 0 draft, 1 active, 2 paused, 3 completed, ...
    pulled_through TEXT,                      -- the last day its steps were pulled for; NULL until its backfill
    synced_at   TEXT NOT NULL
);
-- one campaign's step on one of the buyer's days. The day's unique counts are that day's.
CREATE TABLE IF NOT EXISTS out_step_days (
    campaign_id TEXT NOT NULL,
    day         TEXT NOT NULL,                -- YYYY-MM-DD, the buyer's calendar day
    step        TEXT NOT NULL,                -- as Instantly numbers it; '' when it can't tell
    sent        INTEGER NOT NULL DEFAULT 0,
    opened      INTEGER NOT NULL DEFAULT 0,   -- unique, as are clicked and replied
    clicked     INTEGER NOT NULL DEFAULT 0,
    replied     INTEGER NOT NULL DEFAULT 0,
    booked      INTEGER NOT NULL DEFAULT 0,
    synced_at   TEXT NOT NULL,
    PRIMARY KEY (campaign_id, day, step)
);
-- a week asked for AS A WEEK: unique counts never add up over days, so a week is never summed from out_step_days.
-- step '' is the whole campaign (/campaigns/analytics); any other step is that step's (/campaigns/analytics/steps).
CREATE TABLE IF NOT EXISTS out_weeks (
    campaign_id   TEXT NOT NULL,
    start_day     TEXT NOT NULL,
    end_day       TEXT NOT NULL,              -- inclusive: seven of the buyer's days
    step          TEXT NOT NULL,
    sent          INTEGER NOT NULL DEFAULT 0,
    contacted     INTEGER NOT NULL DEFAULT 0,
    opened        INTEGER NOT NULL DEFAULT 0,
    clicked       INTEGER NOT NULL DEFAULT 0,
    replied       INTEGER NOT NULL DEFAULT 0,
    bounced       INTEGER NOT NULL DEFAULT 0,
    unsubscribed  INTEGER NOT NULL DEFAULT 0,
    opportunities INTEGER NOT NULL DEFAULT 0,
    booked        INTEGER NOT NULL DEFAULT 0,
    synced_at     TEXT NOT NULL,
    PRIMARY KEY (campaign_id, start_day, end_day, step)
);
CREATE INDEX IF NOT EXISTS idx_out_weeks_end ON out_weeks (end_day, step);
"""
