"""The AEO Machine reads its topics from the owner's Airtable: the READ side (#1793 Phase 3,
docs/PLAN_AEO_AIRTABLE_TOPIC_SYNC.md). Owner, 2026-10-02: "yes, build the Airtable topic sync", and "only rows set to
Create Article become articles. Rows without a URL that aren't marked stay untouched."

`read()` asks the buyer's table, once, for the rows whose Status is the option that means "write this" (Create Article
in the owner's table), and returns each one's record id and question. It writes NOTHING: not the plan, not the
buyer's table. Turning rows into planned topics (by record id, so a second read never doubles one) and writing Posted
and the URL back need migration 58's `airtable_id` and `airtable_pushed` columns, which come with OSDev6's F5; that half
lands after it, on top of this.

WHAT THIS HALF HOLDS TO (the plan's review, R1 to R5):
  · R1 ONE ROW, ONE MACHINE: a table the content machine's article gate watches is refused, with the sentence that
    names the fix. Read from config, never from the content machine's code.
  · R2 MISSING IS UNKNOWN: Airtable leaves an empty field out of a record. A row with no question is skipped, never
    read as blank; a mapped field absent from every row of a non-empty read means it was renamed, so the schema is
    read once more and matched again, and if nothing matches the sync pauses with "pick it below". A 422 on the
    Status filter (the Status field renamed or gone) is the same.
  · R3 POLLING ONLY, ONE CALL A PAGE: the list is filtered server-side, so an idle read is one call.
  · R4 THE CORE CLIENT, WITH THE MACHINE'S OWN KEY: core/airtable/client.py's pacing and quota guard see every call.
  · R5 EVERY PAUSE NAMES ITS FIX, in the buyer's words, never a column name of ours.
BEHIND THE LABS SWITCH `aeo_topic_sync` (core/labs.py): off, nothing reads anything.
No model is called, and nothing here publishes.
"""
from __future__ import annotations

from core import box_settings, labs
from core.airtable import fields as af
from core.logging import get_logger

from . import settings, sources

log = get_logger(__name__)

LABS = "aeo_topic_sync"
# THE MACHINE'S STATES, matched to the buyer's own Status options (fields.match_options), best first. Owner, 10-02:
# "Create Article" is the one that means write this. The others are for the write-back, after F5.
STATES = {
    "to_write": ("Create Article", "To Write", "Ready to Write", "Approved"),
    "published": ("Posted", "Published", "Live"),
    "attention": ("Needs Review", "Blocked", "Failed", "Needs Attention"),
}
_STATUS_MAP = f"statuses:{sources.FIELD_MACHINE}"      # kept beside the field map, in the "airtable" namespace

# THE FIXES, IN THE BUYER'S WORDS (R5).
NO_QUESTION_FIELD = ("Pick the field that holds the question each article answers (below), or add a text field "
                     "for it in Airtable.")
NO_STATUS_FIELD = "Pick the field that says where each article stands (below), or add a Status field in Airtable."
NO_WRITE_OPTION = ("Pick which Status option means 'write this article' (below). The AEO Machine only takes rows "
                   "set to it.")
QUESTION_GONE = "Your table no longer has the field we read the question from. Pick it below."
STATUS_GONE = "Your table no longer has the Status field we read. Pick it below."
FEEDS_CONTENT = ("This table already feeds your content machine: rows set to Create Article here are written there. "
                 "Connect a different table, or a copy of this one, so no row is published twice.")
NO_READ = ("Your Airtable token can no longer read this table. Edit the token in Airtable and give it this base "
           "again.")
QUOTA = ("Airtable has paused this base's API until your Airtable plan's month resets. Wait until then, or upgrade "
         "your Airtable plan.")


class Paused(Exception):
    """The sync stops here, and `said` is the sentence that names the fix."""

    def __init__(self, said: str):
        super().__init__(said)
        self.said = said


def _client():
    from core.airtable import client
    return client


def binding() -> dict | None:
    """{key, base, table, view} as connected on AEO → Data sources → Airtable, or None."""
    from core import box_secrets
    s = settings.get()
    base, table = str(s.get("airtable_base") or ""), str(s.get("airtable_table") or "")
    key = box_secrets.get(sources.AIRTABLE_KEY) or ""
    if not (base and table and key):
        return None
    return {"key": key, "base": base, "table": table, "view": str(s.get("airtable_view") or "")}


_TABLE = f"table:{sources.FIELD_MACHINE}"              # the connected table's id, name and fields, as last read


def _table(b: dict, *, fresh: bool) -> dict:
    """The connected table: {id, name, fields}. From the copy kept at the last schema read, unless `fresh` or the
    table was reconnected since; then from one schema read (R3: schema reads only at connect and after a rename)."""
    kept = box_settings.get(af.NS, _TABLE, default={}) or {}
    here = f"{b['base']}/{b['table']}"
    if not fresh and isinstance(kept, dict) and kept.get("for") == here and kept.get("fields"):
        return kept
    got = _call(lambda: _client().get_base_schema(b["base"], key=b["key"]))
    t = next((x for x in got.get("tables") or [] if b["table"] in (x.get("id"), x.get("name"))), None)
    if not t:
        raise Paused(NO_READ)
    out = {"for": here, "id": str(t.get("id") or ""), "name": str(t.get("name") or ""),
           "fields": [{"name": str(f.get("name")), "type": str(f.get("type") or ""),
                       "options": [c.get("name") for c in ((f.get("options") or {}).get("choices") or [])
                                   if c.get("name")]}
                      for f in t.get("fields") or [] if f.get("name")]}
    box_settings.put(af.NS, _TABLE, out, set_by=sources.FIELD_MACHINE)
    sources.remember_fields(out["fields"])                # a renamed field is matched again; choices kept
    return out


def _call(fn):
    """One Airtable call, its refusals turned into the sentence that names the fix (R5)."""
    from core.airtable.client import AirtableError
    from core.exceptions import RetryableError
    try:
        return fn()
    except RetryableError as e:
        if "BILLING" in str(e).upper():
            raise Paused(QUOTA) from None
        raise
    except AirtableError as e:
        said = str(e)
        if said.startswith(("airtable 401", "airtable 403", "airtable 404")):
            raise Paused(NO_READ) from None
        raise


def one_row_one_machine(b: dict, table: dict) -> str | None:
    """R1: the sentence that refuses, when the content machine's article gate watches this table on this box (any
    Space's airtable_base with its written_table, or the airtable_table it falls back to). Config only."""
    from core import spaces
    mine = {str(table.get("id") or ""), str(table.get("name") or "").lower()} - {""}
    for sp in spaces.all_spaces():
        watched = str(sp.get("written_table") or sp.get("airtable_table") or "")
        if sp.get("airtable_base") == b["base"] and watched and (watched in mine or watched.lower() in mine):
            return FEEDS_CONTENT
    return None


def status_map(table: dict) -> dict:
    """{state: the buyer's option or None}, matched from the Status field's real options and saved. The buyer's
    own pick, once made on the Airtable screen, is kept while it is still an option."""
    status = sources.field_name("status")
    field = next((f for f in table.get("fields") or [] if f.get("name") == status), None)
    if not field:
        raise Paused(STATUS_GONE if status else NO_STATUS_FIELD)
    options = list(field.get("options") or [])
    fresh = af.match_options(STATES, options)
    kept = box_settings.get(af.NS, _STATUS_MAP, default={}) or {}
    out = {k: (kept.get(k) if kept.get(k) in options else fresh.get(k)) for k in STATES}
    if out != kept:
        box_settings.put(af.NS, _STATUS_MAP, out, set_by=sources.FIELD_MACHINE)
    return out


def _formula(field: str, option: str) -> str:
    """{Status}="Create Article", quoted so no option name can break out of the string."""
    esc = str(option).replace("\\", "\\\\").replace('"', '\\"')
    return '{' + field.replace("}", "") + '}="' + esc + '"'


def read() -> dict:
    """The rows set to "write this", as {"topics": [{"airtable_id", "question"}], "paused": sentence or ""}. Writes
    nothing. {"off": True} while the labs switch is off, and then nothing is asked."""
    if not labs.on(LABS):
        return {"off": True, "topics": [], "paused": ""}
    b = binding()
    if not b:
        return {"topics": [], "paused": ""}
    try:
        out = _read(b, rematched=False)
    except Paused as p:
        _note(paused=p.said)
        log.info("aeo.topic_sync_paused", why=p.said[:80])
        return {"topics": [], "paused": p.said}
    _note(paused="")
    return {"topics": out, "paused": ""}


def _read(b: dict, *, rematched: bool) -> list:
    from core.airtable.client import AirtableError
    table = _table(b, fresh=rematched)
    refuse = one_row_one_machine(b, table)
    if refuse:
        raise Paused(refuse)
    question, status = sources.field_name("question"), sources.field_name("status")
    try:
        if not question:
            raise Paused(QUESTION_GONE if rematched else NO_QUESTION_FIELD)
        if not status:
            raise Paused(STATUS_GONE if rematched else NO_STATUS_FIELD)
        write = status_map(table).get("to_write")
        if not write:
            raise Paused(NO_WRITE_OPTION)
    except Paused:
        # BEFORE PAUSING ON THE KEPT SCHEMA, ASK AIRTABLE ONCE: the buyer may have added the field or the option back
        # since. A paused sync costs one schema read a sweep, and resumes by itself once the table is right.
        if rematched:
            raise
        return _read(b, rematched=True)
    try:
        rows = _call(lambda: _client().list_records(
            b["table"], base=b["base"], key=b["key"], view=b["view"] or None,
            fields=[question, status], filter_formula=_formula(status, write)))
    except AirtableError as e:
        if not str(e).startswith("airtable 422"):
            raise
        if rematched:                          # read again and still refused: the Status field is gone
            raise Paused(STATUS_GONE) from None
        return _read(b, rematched=True)        # the Status field was probably renamed: read the schema once more
    # R2: A MAPPED FIELD ABSENT FROM EVERY ROW of a non-empty read was probably renamed: read the schema once more.
    # If the fresh schema still has it, those rows simply have no question yet, and are skipped below.
    if rows and not rematched and not any(question in (r.get("fields") or {}) for r in rows):
        return _read(b, rematched=True)
    topics = []
    for r in rows:
        text = " ".join(str((r.get("fields") or {}).get(question) or "").split())
        if r.get("id") and text:                     # R2: no question is unknown, never blank: skipped
            topics.append({"airtable_id": str(r["id"]), "question": text})
    return topics


def _note(*, paused: str) -> None:
    """When the table was last read, and why the sync is paused, if it is (for the Airtable screen, after F5)."""
    from datetime import datetime, timezone
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    box_settings.put(settings.MACHINE, "topic_sync", {"read_at": at, "paused": paused}, set_by=sources.FIELD_MACHINE)
