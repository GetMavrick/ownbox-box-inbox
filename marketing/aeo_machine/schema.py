"""The AEO machine's tables: the plan of articles this box will write, and the questions its customers ask.

DECLARED BY THE MACHINE, NOT BY CORE (docs/SPEC_GOLDEN_DROPLET_IMPACT.md §5). `__init__.py` calls
`state.register_schema("aeo_machine", DDL)` at import, so a box that does not carry this machine
never gets the table, and a box that does gets it whether `init_db()` ran first or not.

A BRAND-NEW TABLE NEEDS NO MIGRATION NUMBER, and two parallel branches must never both claim one.
Once this ships, a change to a column is a tagged migration in core/state.py, never an edit here.
"""

DDL = """

-- ONE ROW PER ARTICLE THE BOX MEANS TO WRITE. The buyer adds a topic on the Topics screen; the
-- writing job takes rows whose status is 'planned' and walks each to 'published', 'refused' (the
-- pre-publish guard said no, with its reason in `refusal`) or 'failed' (something broke, reason in
-- `refusal` too, so the screen has one column to read for "why not").
--
-- `requested_at` IS "WRITE AND PUBLISH NOW". A row with it set goes ahead of the weekly queue, and
-- the screen says so. It is a timestamp rather than a flag so the job can take the oldest first.
CREATE TABLE IF NOT EXISTS seo_plan (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  topic        TEXT NOT NULL,             -- what the article is about, in the buyer's words
  question     TEXT,                      -- the question a searcher asks that it answers
  status       TEXT NOT NULL DEFAULT 'planned'
               CHECK (status IN ('planned', 'writing', 'published', 'refused', 'failed')),
  slug         TEXT,                      -- set when written; the article's address on the site
  url          TEXT,                      -- the live article, once published
  refusal      TEXT,                      -- why it was refused or failed, in words a person can act on
  published_at TEXT,
  requested_at TEXT,                      -- "Write and publish now" was pressed; NULL = weekly queue
  created_at   TEXT NOT NULL,
  created_by   TEXT,                      -- the user id that added it
  updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS seo_plan_status ON seo_plan (status, requested_at, id);

-- THE COMMON QUESTIONS (owner, 2026-10-04: "keep a table of common customer questions"). One row per
-- question, de-duplicated by `key` (lowercase words, no punctuation), rebuilt daily by questions.py from
-- what this box can see: answers to the website's own surveys, questions people search when the site shows
-- up, and (once the Unified Inbox provides them) questions customers wrote in. ONLY THE QUESTION IS KEPT,
-- never who asked: no id, address or name reaches this table (the website's /privacy promise for surveys).
-- `plan_id` is the article that answers it, when one is planned or live.
CREATE TABLE IF NOT EXISTS aeo_questions (
  key          TEXT PRIMARY KEY,           -- the question, normalised, so "Do you?" and "do you" are one
  question     TEXT NOT NULL,              -- the clearest wording seen, as people wrote it
  asked        INTEGER NOT NULL DEFAULT 0, -- times people asked it themselves (surveys, inbox)
  survey       INTEGER NOT NULL DEFAULT 0,
  inbox        INTEGER NOT NULL DEFAULT 0,
  searched     INTEGER NOT NULL DEFAULT 0, -- times the site showed up for it in Google search (impressions)
  plan_id      INTEGER,                    -- seo_plan.id of the article answering it, if any
  first_seen   TEXT NOT NULL,
  last_seen    TEXT NOT NULL
);
"""
