"""The AEO rename changes code and addresses, never where a live box keeps its data.

Owner, 2026-09-25: "Yes, please code rename." The package, routes and labels say aeo. The table,
settings namespace, secret names and AI-account key keep the names every live box already holds
(seo_plan, box_settings machine 'seo', AIRTABLE_API_KEY_SEO, POSTHOG_API_KEY_SEO, account 'seo').

WHY NOT COPY TO NEW NAMES. #1595's first version copied the data once into aeo_* names. box_update
rolls a release back when it fails its health check, and the migrated database stays; the old code
then writes the old names, the copy never runs again, and the two halves drift. The review
reproduced it: a row published while rolled back came back "planned" and was published twice. One
set of names, shared by the release before and the release after, cannot drift.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "rename.db")

from core import box_secrets, box_settings, state  # noqa: E402

state.init_db()

from marketing.aeo_machine import plan, posthog, settings, sources  # noqa: E402

FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


def tables() -> set[str]:
    with state.connect() as c:
        return {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


print("-- the names live boxes already hold --")
ok("the plan table is seo_plan, as every live box has it", "seo_plan" in tables(), str(sorted(tables())))
ok("...and no second copy under a new name exists to drift", "aeo_plan" not in tables())
ok("settings live under machine 'seo'", settings.MACHINE == "seo", settings.MACHINE)
ok("the Airtable key keeps its name", sources.AIRTABLE_KEY == "AIRTABLE_API_KEY_SEO", sources.AIRTABLE_KEY)
ok("the PostHog key keeps its name", posthog.KEY == "POSTHOG_API_KEY_SEO", posthog.KEY)
ok("no data-copy migration ships with the rename", 57 not in state.MIGRATIONS and state.SCHEMA_VERSION == 56,
   f"version {state.SCHEMA_VERSION}")

print("\n-- what the release BEFORE wrote, the release AFTER reads (rolled forward) --")
with state.connect() as c:
    c.execute("INSERT INTO seo_plan (topic, question, status, slug, url, created_at, updated_at) "
              "VALUES ('Old topic', 'What is it?', 'published', 'what-is-it', 'https://example.com/articles/what-is-it', "
              "'2026-09-25T10:00:00+00:00', '2026-09-25T10:00:00+00:00')")
box_settings.put("seo", "site_url", "https://example.com")
box_secrets.put("AIRTABLE_API_KEY_SEO", "patOLDRELEASEVALUE.1234567890")
got = [r for r in plan.rows() if r["topic"] == "Old topic"]
ok("a row the old code published reads as published, with its address", bool(got)
   and got[0]["status"] == "published" and got[0]["slug"] == "what-is-it", str(got))
ok("a setting the old code saved is the new code's setting", settings.get()["site_url"] == "https://example.com")
ok("a key the old code saved is the new code's key", box_secrets.get(sources.AIRTABLE_KEY) == "patOLDRELEASEVALUE.1234567890")

print("\n-- what the release AFTER writes, the release BEFORE reads (rolled back) --")
new_id = plan.add("New topic", "How does it work?")
box_settings.put(settings.MACHINE, "weekly_cap", 6)
with state.connect() as c:
    row = c.execute("SELECT topic, status FROM seo_plan WHERE id = ?", (new_id,)).fetchone()
ok("a topic added by the new code is in the table the old code reads", row is not None
   and row["topic"] == "New topic" and row["status"] == "planned", str(dict(row) if row else None))
ok("a setting saved by the new code is what the old code reads", str(box_settings.get("seo", "weekly_cap")) == "6")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
