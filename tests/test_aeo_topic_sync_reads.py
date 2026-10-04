"""The AEO Machine reads its topics from the buyer's Airtable: the read side (#1793 Phase 3,
docs/PLAN_AEO_AIRTABLE_TOPIC_SYNC.md). Owner, 2026-10-02: only rows set to Create Article become articles.

Airtable here is a recording stand-in at the core client's HTTP layer (requests.request inside
core/airtable/client.py), so the client's own pacing, retries and quota guard run for real and every request is
checked at the wire. No network, ever.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  · labs off asks Airtable anything; labs on reads rows other than the "write this" option, or reads them through
    anything but the core client with the AEO Machine's own key, or writes anything at all;
  · the list request is not one filtered call (filterByFormula on the matched option, the saved view, the two fields);
  · a second read re-reads the schema; an option name with a quote can break the formula;
  · the owner's table (Seed Idea, his HQ Status options, URL) or one that says Seed needs a rename;
  · R2: a row with no question becomes a blank topic; a renamed question field is not matched again after one
    schema read; a field gone does not pause with "pick it below"; a 422 on the Status filter is not the same;
  · R1: a table the content machine's article gate watches (by name or by id) is read;
  · R5: a refusal (no write option, quota, permission) does not pause with the sentence that names its fix, or the
    quota refusal is retried in a loop;
  · R4: the content machine's own calls change.
Run: python tests/test_aeo_topic_sync_reads.py
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
from urllib.parse import unquote

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "topic_sync.db")
os.environ["DISPATCH_BEARER_TOKEN"], os.environ["DASH_TOKEN"] = "bearer", "pw"

from core import state  # noqa: E402

state.init_db()
from core import box_secrets, box_settings, labs, spaces  # noqa: E402
from core.airtable import client  # noqa: E402
from core.config import settings as core_settings  # noqa: E402
from marketing.aeo_machine import settings as aeo_settings, sources, topic_sync  # noqa: E402

FAILS: list[str] = []


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:900]}"))
    if not cond:
        FAILS.append(label)


BASE, TABLE, VIEW, KEY = "appz62YGuLOjSjtCT", "tbllPO6iA1w3ltpRW", "viwhawCMPvDBy1tFp", "patAEOMACHINEkey.0123"
HQ_OPTIONS = ["Draft", "Create Article", "Create Carousel", "Create Social", "Ready to Post", "Posted", "Archived"]


def schema(question="Seed Idea", status="Status", options=None, extra=()):
    fields = [{"name": "Name", "type": "singleLineText"}]
    if question:
        fields.append({"name": question, "type": "multilineText"})
    if status:
        fields.append({"name": status, "type": "singleSelect",
                       "options": {"choices": [{"name": o} for o in (options or HQ_OPTIONS)]}})
    fields += [{"name": "URL", "type": "url"}, *extra]
    return {"tables": [{"id": TABLE, "name": "AEO Machine", "fields": fields}]}


AT = {"schema": schema(), "rows": [], "refuse": None, "status_field": "Status"}
CALLS: list = []


class _Resp:
    def __init__(self, code, body):
        self.status_code, self.ok = code, code < 400
        self.text = json.dumps(body)
        self.content, self.headers = self.text.encode(), {}

    def json(self):
        return json.loads(self.text)


def _airtable(method, url, timeout=None, headers=None, params=None, json=None, **kw):
    CALLS.append({"method": method.upper(), "url": url, "params": dict(params or {}), "auth": (headers or {}).get(
        "Authorization"), "json": json})
    if AT["refuse"]:
        return _Resp(*AT["refuse"])
    if url == f"{client._META_API}/{BASE}/tables":
        return _Resp(200, AT["schema"])
    if url == f"{client._API}/{BASE}/{TABLE}" and method.lower() == "get":
        f = (params or {}).get("filterByFormula", "")
        if "{" + AT["status_field"] + "}" not in f:
            return _Resp(422, {"error": {"type": "INVALID_FILTER_BY_FORMULA"}})
        want = f.split('="', 1)[1][:-1].replace('\\"', '"')
        rows = [r for r in AT["rows"] if r["fields"].get(AT["status_field"]) == want]
        return _Resp(200, {"records": rows})
    raise AssertionError(f"unexpected Airtable call {method} {url}")


client.requests.request = _airtable
box_secrets.put(sources.AIRTABLE_KEY, KEY)
for k, v in (("airtable_base", BASE), ("airtable_table", TABLE), ("airtable_view", VIEW)):
    box_settings.put(aeo_settings.MACHINE, k, v)
spaces.get_config = lambda: {}                                  # no content machine binding on this box


def rec(rid, status, question=None, field="Seed Idea"):
    f = {AT["status_field"]: status}
    if question is not None:
        f[field] = question
    return {"id": rid, "createdTime": "2026-10-04T00:00:00.000Z", "fields": f}


AT["rows"] = [rec("recBotox", "Create Article", "How much does Botox cost in Austin?"),
              rec("recWalk", "Create Article", "  Do you take   walk-ins? "),
              rec("recEmpty", "Create Article"),                                     # no question in the JSON
              rec("recDraft", "Draft", "Is microneedling painful?"),
              rec("recLive", "Posted", "What is a HydraFacial?")]
with state.connect() as c:
    plan_before = c.execute("SELECT COUNT(*) FROM seo_plan").fetchone()[0]

print("\nlabs off: nothing is asked")
got = topic_sync.read()
ok("off by default, and not one call", got.get("off") is True and not CALLS, (got, CALLS))

print("\nlabs on: one filtered read, through the core client, with the AEO Machine's own key")
box_settings.put("labs", topic_sync.LABS, True)
got = topic_sync.read()
ok("only the rows set to Create Article, each with its record id and question",
   got["topics"] == [{"airtable_id": "recBotox", "question": "How much does Botox cost in Austin?"},
                     {"airtable_id": "recWalk", "question": "Do you take walk-ins?"}] and not got["paused"], got)
ok("...a row with no question in its JSON is skipped, never a blank topic (R2)",
   "recEmpty" not in json.dumps(got))
ok("...Draft and Posted rows are never even returned: the filter is Airtable's", all(
   c["params"].get("filterByFormula") == '{Status}="Create Article"' for c in CALLS if c["url"].endswith(TABLE)))
lst = [c for c in CALLS if c["url"].endswith(TABLE)]
ok("THE WIRE: one schema read, then one list call of 100 a page, on the saved view, asking for two fields",
   [c["url"] for c in CALLS] == [f"{client._META_API}/{BASE}/tables", f"{client._API}/{BASE}/{TABLE}"]
   and lst[0]["params"] == {"pageSize": 100, "fields[]": ["Seed Idea", "Status"], "view": VIEW,
                            "filterByFormula": '{Status}="Create Article"'}, CALLS)
ok("...every call with the AEO Machine's key, never the content machine's (R4)",
   all(c["auth"] == f"Bearer {KEY}" for c in CALLS), [c["auth"] for c in CALLS])
ok("...and every call a read: nothing is written to Airtable", all(c["method"] == "GET" for c in CALLS))
with state.connect() as c:
    ok("...nor to the plan", c.execute("SELECT COUNT(*) FROM seo_plan").fetchone()[0] == plan_before)
CALLS.clear()
topic_sync.read()
ok("a second read is ONE call: the schema is kept from the first", len(CALLS) == 1
   and CALLS[0]["url"].endswith(TABLE), [c["url"] for c in CALLS])

print("\nmatching: no rename asked of anyone")
box_settings.put("airtable", f"fields:{sources.FIELD_MACHINE}", {})
box_settings.put("airtable", topic_sync._TABLE, {})
AT["schema"] = schema(question="Seed")
AT["rows"] = [rec("recSeed", "Create Article", "Do fillers hurt?", field="Seed")]
got = topic_sync.read()
ok("a table that says Seed is read the same", got["topics"] == [{"airtable_id": "recSeed",
                                                                  "question": "Do fillers hurt?"}], got)
AT["schema"] = schema(options=["To Do", "Write it", "Done"])
box_settings.put("airtable", topic_sync._TABLE, {})
box_settings.put("airtable", topic_sync._STATUS_MAP, {})
got = topic_sync.read()
ok("R5: no option that means 'write this' pauses, naming the fix",
   got["paused"] == topic_sync.NO_WRITE_OPTION and not got["topics"], got)
AT["schema"] = schema(options=['Write "this" one', "Posted"])
box_settings.put("airtable", topic_sync._TABLE, {})
box_settings.put("airtable", topic_sync._STATUS_MAP, {"to_write": 'Write "this" one'})
AT["rows"] = [rec("recQ", 'Write "this" one', "Is Botox safe?")]
CALLS.clear()
got = topic_sync.read()
ok("the buyer's own pick of option is used, and a quote in it cannot break the formula",
   got["topics"] == [{"airtable_id": "recQ", "question": "Is Botox safe?"}]
   and CALLS[-1]["params"]["filterByFormula"] == '{Status}="Write \\"this\\" one"', (got, CALLS[-1:]))

print("\nR2: a renamed field, a field gone, a renamed Status")
AT["schema"] = schema()
box_settings.put("airtable", topic_sync._TABLE, {})
box_settings.put("airtable", topic_sync._STATUS_MAP, {})
AT["rows"] = [rec("recBotox", "Create Article", "How much does Botox cost?")]
topic_sync.read()
AT["schema"] = schema(question="Article Question")
AT["rows"] = [rec("recBotox", "Create Article", "How much does Botox cost?", field="Article Question")]
CALLS.clear()
got = topic_sync.read()
ok("a question field renamed in Airtable: absent from every row, so the schema is read ONCE more and matched again",
   got["topics"] == [{"airtable_id": "recBotox", "question": "How much does Botox cost?"}]
   and [c["url"].split("/")[-1] for c in CALLS] == [TABLE, "tables", TABLE], (got, [c["url"] for c in CALLS]))
AT["rows"] = [rec("recBlank", "Create Article")]
CALLS.clear()
got = topic_sync.read()
ok("...while a field that IS in the schema, empty on every row, just skips them (no pause)",
   got == {"topics": [], "paused": ""} and len(CALLS) == 3, (got, len(CALLS)))
AT["schema"] = schema(question=None)
AT["rows"] = [rec("recGone", "Create Article", "Anything", field="Gone")]
got = topic_sync.read()
ok("a question field gone altogether pauses with 'pick it below'", got["paused"] == topic_sync.QUESTION_GONE, got)
AT["schema"], AT["status_field"] = schema(status="Stage"), "Stage"
AT["rows"] = [rec("recStage", "Create Article", "When can I book?")]
CALLS.clear()
got = topic_sync.read()
ok("the Status field renamed: Airtable's 422 on the filter reads the schema once more, then the new name",
   got["topics"] == [{"airtable_id": "recStage", "question": "When can I book?"}]
   and CALLS[-1]["params"]["filterByFormula"] == '{Stage}="Create Article"', (got, [c["params"] for c in CALLS]))
AT["schema"] = schema(status=None)
box_settings.put("airtable", topic_sync._TABLE, {})
got = topic_sync.read()
ok("...and a Status field gone pauses, naming the fix", got["paused"] in (topic_sync.STATUS_GONE,
                                                                          topic_sync.NO_STATUS_FIELD), got)
AT["schema"], AT["status_field"] = schema(), "Status"
box_settings.put("airtable", topic_sync._TABLE, {})
AT["rows"] = [rec("recBotox", "Create Article", "How much does Botox cost?")]
ok("(a good read, so the kept schema and map are current)", topic_sync.read()["topics"])
AT["schema"], AT["status_field"] = schema(status="State"), "State"                 # renamed in Airtable, since
AT["rows"] = [rec("recPipe", "Create Article", "Do you offer payment plans?")]
CALLS.clear()
got = topic_sync.read()
ok("FROM A CURRENT KEPT SCHEMA, a Status field renamed since: the filter's 422 reads the schema once, then reads again",
   got["topics"] == [{"airtable_id": "recPipe", "question": "Do you offer payment plans?"}]
   and [c["url"].split("/")[-1] for c in CALLS] == [TABLE, "tables", TABLE]
   and CALLS[-1]["params"]["filterByFormula"] == '{State}="Create Article"', (got, [c["url"] for c in CALLS]))
AT["schema"], AT["status_field"] = schema(), "Status"
box_settings.put("airtable", topic_sync._TABLE, {})

print("\nR1: one row, one machine")
for watched in ("AEO Machine", TABLE):
    spaces.get_config = lambda w=watched: {"spaces": [{"name": "hq", "airtable_base": BASE, "written_table": w}]}
    CALLS.clear()
    got = topic_sync.read()
    ok(f"a table the content machine's article gate watches ({watched}) is never read",
       got["paused"] == topic_sync.FEEDS_CONTENT and not [c for c in CALLS if c["url"].endswith(TABLE)], (got, CALLS))
spaces.get_config = lambda: {"spaces": [{"name": "hq", "airtable_base": "appOTHERBASE00000", "written_table": TABLE}]}
ok("...the same table id in another base is not that table", not topic_sync.read()["paused"])
spaces.get_config = lambda: {}

print("\nR5: Airtable's refusals stop the read and name the fix")
AT["refuse"] = (429, {"errors": [{"error": "PUBLIC_API_BILLING_LIMIT_EXCEEDED"}]})
box_settings.put("airtable", topic_sync._TABLE, {})
CALLS.clear()
got = topic_sync.read()
ok("the month's quota spent: paused, 'wait until then, or upgrade', and ONE call, never a retry loop",
   got["paused"] == topic_sync.QUOTA and len(CALLS) == 1, (got, len(CALLS)))
AT["refuse"] = (403, {"error": {"type": "INVALID_PERMISSIONS_OR_MODEL_NOT_FOUND"}})
got = topic_sync.read()
ok("a token that can no longer read the table: paused, 'give it this base again'", got["paused"] == topic_sync.NO_READ,
   got)
AT["refuse"] = None
ok("...and the pause is kept for the Airtable screen, with when it was read",
   (box_settings.get(aeo_settings.MACHINE, "topic_sync") or {}).get("paused") == topic_sync.NO_READ)
got = topic_sync.read()
ok("...cleared on the next good read", not got["paused"]
   and not (box_settings.get(aeo_settings.MACHINE, "topic_sync") or {}).get("paused"))

print("\nR4: the content machine's calls are unchanged")
ok("without key= the client sends the content machine's key", client._headers()["Authorization"]
   == f"Bearer {core_settings.airtable_api_key}")
ok("with key= it sends that one", client._headers("patX")["Authorization"] == "Bearer patX")
ok("labs knows the name", topic_sync.LABS in labs.KNOWN)

print()
print(f"{len(FAILS)} FAILED" if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
