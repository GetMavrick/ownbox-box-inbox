"""The AEO machine's one table: the plan of articles this box will write, and what became of each.

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
"""
