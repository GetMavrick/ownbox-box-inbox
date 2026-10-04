"""AEO → Airtable: a business connects its own table as it is. Nothing is renamed (#1793 §1.3).

OWNER, 2026-10-01, after the box refused his table over "Seed" versus "Seed Idea": "Clients are gonna be
having lots of issues and there's going to be a ton of technical support tickets!!!" Then: "Go, build the
field matching." OSDev1's 10-01 assignment, unchanged in the plan:
  1. declare the roles and replace TEMPLATE_FIELDS;
  2. save each piece the moment it is valid, then show the matches with a dropdown each;
  3. a role with no match gets its plain label and a one-tap Add this field for me (schema.bases:write);
  4. every read and write goes through fields.field("aeo_machine", role).

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  · his renamed table (Seed Idea, Status, URL) or his original one (Seed) needs a rename to connect;
  · a key Airtable accepted has to be pasted again because the table address was wrong;
  · a dropdown can save a field that can't hold the role, or one field for two roles;
  · reading the fields again throws away a choice the owner made;
  · "Add this field for me" adds anything but the one field asked for, or a token without
    schema.bases:write gets anything but that scope named;
  · a member can change the fields;
  · the owner's AI can't see which field holds what (aeo.sources).

NO NETWORK: `sources.net` is a scripted stand-in that records every request.

Run: python tests/test_aeo_field_matching.py
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "aeo_fields.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state                                                   # noqa: E402

state.init_db()

from core import box_secrets, dash, net                                  # noqa: E402
from core.airtable import fields as af                                   # noqa: E402
from core.connector import tools as registry                             # noqa: E402
from core.dispatch import app as web                                     # noqa: E402
from marketing.aeo_machine import sources                                # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


class Net:
    PostRefused = net.PostRefused

    def __init__(self, gets=None, posts=None):
        self.gets, self.posts, self.seen = gets or {}, posts or {}, []

    def _answer(self, table, url):
        return next((reply for frag, reply in table.items() if frag in url), (0, ""))

    def get_public(self, url, *, headers=None, **_kw):
        self.seen.append(("GET", url, None))
        return self._answer(self.gets, url)

    def post_public(self, url, *, headers=None, json=None, **_kw):
        self.seen.append(("POST", url, json))
        return self._answer(self.posts, url)


def F(*pairs):
    return [{"name": n, "type": t} for n, t in pairs]


BASE, TABLE, VIEW = "app" + "A" * 14, "tbl" + "B" * 14, "viw" + "C" * 14
URL = f"https://airtable.com/{BASE}/{TABLE}/{VIEW}"
KEY = "pat" + "Z" * 60
# THE OWNER'S TABLE, RENAMED (names and types only, never an id: tests ship to boxes).
RENAMED = F(("ID", "autoNumber"), ("Title", "singleLineText"), ("Seed Idea", "multilineText"),
            ("Body", "multilineText"), ("Status", "singleSelect"), ("URL", "url"), ("X URL", "url"))
# HIS ORIGINAL ONE, the one the box refused on 10-01.
ORIGINAL = F(("Title", "singleLineText"), ("Seed", "multilineText"), ("Status", "singleSelect"),
             ("URL", "url"))
# A BUSINESS WITH NO STATUS AND NO ADDRESS FIELD YET.
BARE = F(("Question", "multilineText"), ("Notes", "multilineText"))


def schema(fields):
    return {"/meta/whoami": (200, '{"id":"u"}'),
            f"/meta/bases/{BASE}/tables": (200, json.dumps(
                {"tables": [{"id": TABLE, "fields": fields, "views": [{"id": VIEW}]}]})),
            f"/v0/{BASE}/{TABLE}?": (200, '{"records":[]}')}


def use(fake):
    sources.net = fake
    return fake


def client(uid):
    c = web.test_client()
    c.set_cookie(dash.COOKIE, dash.new_session(uid))
    return c


OWNER = state.owner_user()["id"]
MEMBER = state.add_user("sam@northwind-consulting.com", name="Sam")["id"]
owner, member = client(OWNER), client(MEMBER)

print("test_his_tables_connect_with_no_rename")
for name, fields, question in (("renamed", RENAMED, "Seed Idea"), ("original", ORIGINAL, "Seed")):
    use(Net(gets=schema(fields)))
    r = owner.post("/aeo/sources/airtable", data={"table_url": URL, "api_key": KEY})
    ok(f"his {name} table connects", r.status_code == 303, r.status_code)
    ok(f"...and its fields are matched with no rename ({question})",
       sources.field_name("question") == question and sources.field_name("status") == "Status"
       and sources.field_name("url") == "URL", af.load_map("aeo_machine"))
ok("the map lives where the plan says: fields.field('aeo_machine', role)",
   af.field("aeo_machine", "question") == "Seed")
ok("the optional title is matched to a title field when one exists", sources.field_name("title") == "Title")
page = owner.get("/aeo/sources/airtable").get_data(as_text=True)
ok("the screen shows each match in its own dropdown",
   '<select id="f-question" name="question">' in page and '<option value="Seed" selected>' in page)
ok("...and no longer asks for exact names", "exactly these names" not in page)

print("\ntest_a_valid_key_is_kept_when_the_table_is_wrong")
use(Net(gets={**schema(ORIGINAL), f"/meta/bases/{BASE}/tables": (200, '{"tables": []}')}))
NEWKEY = "pat" + "Y" * 60
r = owner.post("/aeo/sources/airtable", data={"table_url": URL, "api_key": NEWKEY})
ok("a wrong table address is refused", r.status_code == 400 and "no table at that address" in r.get_data(as_text=True))
ok("...but the key Airtable accepted is saved, so it isn't pasted again",
   box_secrets.get(sources.AIRTABLE_KEY) == NEWKEY)
ok("...and never shown back", NEWKEY not in r.get_data(as_text=True))
use(Net(gets={**schema(ORIGINAL), "/meta/whoami": (401, "")}))
owner.post("/aeo/sources/airtable", data={"table_url": URL, "api_key": "pat" + "X" * 60})
ok("a key Airtable refuses is never saved", box_secrets.get(sources.AIRTABLE_KEY) == NEWKEY)

print("\ntest_the_dropdowns")
use(Net(gets=schema(RENAMED + F(("Question", "multilineText")))))
owner.post("/aeo/sources/airtable", data={"table_url": URL, "api_key": ""})
r = owner.post("/aeo/sources/airtable/fields", data={"do": "save", "question": "Body", "status": "Status",
                                                     "url": "URL", "title": ""})
ok("the owner picks his own field for a role, one the matcher would not have chosen",
   r.status_code == 303 and sources.field_name("question") == "Body")
ok("...and leaves the optional one unused", sources.field_name("title") is None)
r = owner.post("/aeo/sources/airtable/fields", data={"do": "save", "question": "ID", "status": "Status", "url": "URL"})
ok("a field that can't hold the role is refused", r.status_code == 400 and sources.field_name("question") == "Body")
r = owner.post("/aeo/sources/airtable/fields", data={"do": "save", "question": "Body", "status": "Status",
                                                     "url": "URL", "title": "Body"})
ok("one field for two roles is refused", r.status_code == 400
   and "only one" in r.get_data(as_text=True), r.status_code)
use(Net(gets=schema(RENAMED + F(("Question", "multilineText")))))
owner.post("/aeo/sources/airtable/fields", data={"do": "refresh"})
ok("reading the fields again keeps his own choice", sources.field_name("question") == "Body")
use(Net(gets=schema([f for f in RENAMED if f["name"] != "Body"])))
owner.post("/aeo/sources/airtable/fields", data={"do": "refresh"})
ok("...unless that field is gone, when the best match takes its place", sources.field_name("question") == "Seed Idea")

print("\ntest_add_this_field_for_me")
use(Net(gets=schema(BARE)))
owner.post("/aeo/sources/airtable", data={"table_url": URL, "api_key": ""})
fs = sources.fields_state()
ok("a table with no status and no address field lists both in plain words",
   [label for _n, label in fs["missing"]] == ["where each article stands", "the published article's address"], fs)
page = owner.get("/aeo/sources/airtable").get_data(as_text=True)
ok("...each with its own Add this field for me", page.count("Add this field for me") == 2
   and "schema.bases:write" in page)
f = use(Net(posts={f"/meta/bases/{BASE}/tables/{TABLE}/fields": (403, '{"error":"INVALID_PERMISSIONS"}')}))
r = owner.post("/aeo/sources/airtable/fields", data={"do": "add", "role": "url"})
ok("a token without schema.bases:write gets that scope named", r.status_code == 400
   and "Your token can't add fields. In Airtable, edit the token and add the schema.bases:write scope"
   in r.get_data(as_text=True).replace("&#x27;", "'").replace("&#39;", "'"))
f = use(Net(posts={f"/meta/bases/{BASE}/tables/{TABLE}/fields": (200, '{"id":"fld1","name":"URL","type":"url"}')}))
r = owner.post("/aeo/sources/airtable/fields", data={"do": "add", "role": "url"})
posts = [s for s in f.seen if s[0] == "POST"]
ok("one press adds exactly the one field, the sample's way", r.status_code == 303 and len(posts) == 1
   and posts[0][2] == {"name": "URL", "type": "url"}, posts)
ok("...and the AEO Machine uses it at once", sources.field_name("url") == "URL")
f = use(Net(posts={f"/meta/bases/{BASE}/tables/{TABLE}/fields": (200, '{"id":"fld2","name":"Status","type":"singleSelect"}')}))
owner.post("/aeo/sources/airtable/fields", data={"do": "add", "role": "status"})
added = [s for s in f.seen if s[0] == "POST"][0][2]
ok("a Status field comes with the two options the machine reads",
   added["type"] == "singleSelect" and [c["name"] for c in added["options"]["choices"]] == ["Create Article", "Posted"])
ok("now nothing is missing", sources.fields_state()["missing"] == [])
f = use(Net(posts={}))
r = owner.post("/aeo/sources/airtable/fields", data={"do": "add", "role": "nonsense"})
ok("a role that isn't one is refused and sends nothing", r.status_code == 400 and f.seen == [])

print("\ntest_owner_only")
before = af.load_map("aeo_machine")
f = use(Net(gets=schema(RENAMED), posts={"/fields": (200, "{}")}))
for data in ({"do": "save", "question": "Notes"}, {"do": "add", "role": "title"}, {"do": "refresh"}):
    r = member.post("/aeo/sources/airtable/fields", data=data)
    ok(f"a member can't {data['do']}", r.status_code == 403)
ok("...and nothing was sent or saved", f.seen == [] and af.load_map("aeo_machine") == before)

print("\ntest_the_owners_ai_sees_it")
seat = {"id": "seat_owner", "role": "read"}
body, code = registry.call("aeo.sources", {}, seat)
air = body.get("result", body)["airtable"]
ok("aeo.sources says which field holds what, in words",
   air.get("fields", {}).get("the question each article answers") == "Question"
   and air.get("fields_missing") == [], air)
ok("...and never the key", KEY not in json.dumps(body) and NEWKEY not in json.dumps(body))

print("\n— and this file cannot silently fall out of CI —")
if (ROOT / ".github").is_dir():
    ok("test_aeo_field_matching is in the workflow's suite list",
       "test_aeo_field_matching" in (ROOT / ".github/workflows/tests.yml").read_text())

print("\nALL OK" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
