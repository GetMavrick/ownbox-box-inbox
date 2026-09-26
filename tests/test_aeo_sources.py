"""AEO > Data sources: the Sanity and Airtable connections, each proved before it is saved.

OWNER, 2026-09-25, in OSDev6's session: "Business owners are going to be required to use sanity and
set that up. And then they're gonna be required to use Airtable." OSDev1's spec the same afternoon:
on save, prove the connection for real, and map each failure to one plain sentence.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  · a failed proof saves anything, or a credential is shown back on any page;
  · Sanity's proof writes a real document (every write it makes must be a dry run);
  · Airtable's proof writes anything at all;
  · a read-only Sanity token is accepted, or refused without saying "Editor";
  · an Airtable table without the template's fields is accepted, or the missing field is not named;
  · the token lands anywhere but the name the publisher reads;
  · a member, or a GET, changes anything;
  · a screen uses one of the nouns reserved for the receptionist machine (CLAUDE.md).

NO NETWORK. `sources.net` is replaced with a scripted stand-in that records every request.

Run: python tests/test_aeo_sources.py
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "aeo_sources.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state                                                   # noqa: E402

state.init_db()

from core import box_secrets, box_settings, dash, net, shell             # noqa: E402
from core.dispatch import app as web                                     # noqa: E402
from marketing.aeo_machine import app as aeo_app, settings, sources      # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


class Net:
    """Answers by URL fragment. Records every request so a test can say what was and was not sent."""
    PostRefused = net.PostRefused

    def __init__(self, gets=None, posts=None, post_raises=False):
        self.gets, self.posts, self.post_raises = gets or {}, posts or {}, post_raises
        self.seen = []

    def _answer(self, table, url):
        for frag, reply in table.items():
            if frag in url:
                return reply
        return 0, ""

    def get_public(self, url, *, headers=None, max_bytes=net.DEFAULT_MAX_BYTES, **_kw):
        self.seen.append(("GET", url, dict(headers or {}), None, max_bytes))
        return self._answer(self.gets, url)

    def post_public(self, url, *, headers=None, json=None, **_kw):
        self.seen.append(("POST", url, dict(headers or {}), json, None))
        if self.post_raises:
            raise net.PostRefused("no route")
        return self._answer(self.posts, url)


def use(fake):
    sources.net = fake
    return fake


SAN_OK = {"/users/me": (200, '{"id":"p1"}')}
MUTATE_OK = {"/data/mutate/": (200, '{"results":[]}')}
FIELDS = [{"name": n} for n in sources.TEMPLATE_FIELDS]
BASE, TABLE, VIEW = "app" + "A" * 14, "tbl" + "B" * 14, "viw" + "C" * 14
TABLES = {"tables": [{"id": TABLE, "fields": FIELDS + [{"name": "Notes"}], "views": [{"id": VIEW}]}]}
AIR_OK = {"/meta/whoami": (200, '{"id":"usr1"}'),
          f"/meta/bases/{BASE}/tables": (200, json.dumps(TABLES)),
          f"/v0/{BASE}/{TABLE}?": (200, '{"records":[]}')}
URL = f"https://airtable.com/{BASE}/{TABLE}/{VIEW}?blocks=hide"
SAN_TOKEN = "sk" + "Q" * 60
AIR_KEY = "pat" + "Z" * 60

print("test_the_sanity_proof")
f = use(Net(gets=SAN_OK, posts=MUTATE_OK))
ok("a token that can write passes", sources.check_sanity("abc123xy", "production", SAN_TOKEN) is None)
ok("...asking the project's own API, with the token as a bearer",
   f.seen[0][1].startswith("https://abc123xy.api.sanity.io/v") and f.seen[0][1].endswith("/users/me")
   and f.seen[0][2].get("Authorization") == f"Bearer {SAN_TOKEN}", f.seen[0][:2])
post = [s for s in f.seen if s[0] == "POST"]
ok("the write proof is a DRY RUN, so nothing is stored in the owner's Sanity",
   len(post) == 1 and "dryRun=true" in post[0][1] and "/data/mutate/production" in post[0][1], post)
ok("...of the one thing the publisher creates, an article",
   post[0][3]["mutations"][0]["create"]["_type"] == "article")
for gets, posts, want, why in (
        ({"/users/me": (401, "")}, MUTATE_OK, sources.SANITY_BAD_TOKEN, "a bad token"),
        ({"/users/me": (404, "")}, MUTATE_OK, sources.SANITY_NO_PROJECT, "a project that does not exist"),
        ({}, MUTATE_OK, sources.SANITY_UNREACHABLE, "no answer at all"),
        (SAN_OK, {"/data/mutate/": (403, '{"error":"permission create required"}')},
         sources.SANITY_READ_ONLY, "a read-only token"),
        (SAN_OK, {"/data/mutate/": (401, "")}, sources.SANITY_BAD_TOKEN, "a token refused on the write")):
    use(Net(gets=gets, posts=posts))
    ok(f"{why} gets its own sentence", sources.check_sanity("abc123xy", "production", SAN_TOKEN) == want)
ok("the read-only sentence says which kind of token to make", "Editor" in sources.SANITY_READ_ONLY)
use(Net(gets=SAN_OK, posts={"/data/mutate/": (404, '{"error":"Dataset not found"}')}))
got = sources.check_sanity("abc123xy", "staging", SAN_TOKEN)
ok("a missing dataset is named", got and "no dataset called staging" in got, got)
use(Net(gets=SAN_OK, post_raises=True))
ok("a write that cannot be sent is 'did not answer', not a crash",
   sources.check_sanity("abc123xy", "production", SAN_TOKEN) == sources.SANITY_UNREACHABLE)
use(Net(gets={"/users/me": (500, "")}))
got = sources.check_sanity("abc123xy", "production", SAN_TOKEN)
ok("anything else says what Sanity answered", got and "(500)" in got, got)

print("\ntest_the_airtable_address")
ok("an address with a view gives base, table and view", sources.parse_table_url(URL) == (BASE, TABLE, VIEW))
ok("...and without one, no view", sources.parse_table_url(f"airtable.com/{BASE}/{TABLE}") == (BASE, TABLE, ""))
for bad, why in ((f"https://airtable.com/{BASE}", "a base with no table"),
                 (f"https://evil.example/{BASE}/{TABLE}", "another website"),
                 ("http://[", "a malformed address"), ("", "nothing")):
    try:
        sources.parse_table_url(bad)
        refused = False
    except sources.BadTableUrl:
        refused = True
    ok(f"refused: {why}", refused)
ok("an address is rebuilt from what was saved", sources.table_url(BASE, TABLE, VIEW)
   == f"https://airtable.com/{BASE}/{TABLE}/{VIEW}")

print("\ntest_the_airtable_proof")
f = use(Net(gets=AIR_OK))
ok("a key that can read a table with the template's fields passes",
   sources.check_airtable(AIR_KEY, BASE, TABLE, VIEW) is None)
ok("...and the proof never writes to the owner's table", all(s[0] == "GET" for s in f.seen), f.seen)
ok("...reading one record, in the view the machine follows",
   any("maxRecords=1" in s[1] and f"view={VIEW}" in s[1] for s in f.seen))


def air(**over):
    gets = dict(AIR_OK)
    gets.update(over.pop("gets", {}))
    use(Net(gets=gets))
    return sources.check_airtable(AIR_KEY, BASE, TABLE, over.get("view", VIEW))


ok("a bad key gets its own sentence", air(gets={"/meta/whoami": (401, "")}) == sources.AIRTABLE_BAD_KEY)
got = air(gets={"/meta/whoami": (200, '{"id":"u","scopes":["data.records:read","schema.bases:read"]}')})
ok("where Airtable lists the scopes, a missing one is named in Airtable's words",
   got and "data.records:write" in got, got)
ok("a key without the base", air(gets={f"/meta/bases/{BASE}/tables": (403, "")}) == sources.AIRTABLE_NO_BASE)
got = air(gets={f"/meta/bases/{BASE}/tables": (200, json.dumps({"tables": []}))})
ok("a table that is not in the base", got and "no table at that address" in got, got)
got = air(view="viw" + "D" * 14)
ok("a view that is not in the table", got and "no view at that address" in got, got)
thin = {"tables": [{"id": TABLE, "fields": [{"name": "Question"}], "views": []}]}
got = air(gets={f"/meta/bases/{BASE}/tables": (200, json.dumps(thin))}, view="")
ok("a table missing the template's fields names each one",
   got and "Status" in got and "URL" in got and "Question" not in got.split("missing", 1)[1], got)
ok("a key that can see the schema but not the rows",
   air(gets={f"/v0/{BASE}/{TABLE}?": (403, "")}) == sources.AIRTABLE_NO_READ)
ok("no answer at all", air(gets={"/meta/whoami": (0, "")}) == sources.AIRTABLE_UNREACHABLE)
f = use(Net(gets=AIR_OK))
sources.check_airtable(AIR_KEY, BASE, TABLE, VIEW)
schema_read = [s for s in f.seen if "/meta/bases/" in s[1]]
ok("a big base's schema is read whole, not cut at the 400 KB default (OSDev1, #1572)",
   schema_read and schema_read[0][4] >= 5_000_000, schema_read and schema_read[0][4])
got = air(gets={f"/meta/bases/{BASE}/tables": (200, '{"tables": [{"id": "tbl')})
ok("a schema that cannot be read says so, never 'no table'", got == sources.AIRTABLE_UNREADABLE, got)

print("\ntest_the_screens")


def client(user_id):
    c = web.test_client()
    c.set_cookie(dash.COOKIE, dash.new_session(user_id))
    return c


def main_of(html: str) -> str:
    return html.split("</nav>", 1)[-1]


OWNER = state.owner_user()["id"]
MEMBER = state.add_user("sam@northwind-consulting.com", name="Sam")["id"]
owner, member = client(OWNER), client(MEMBER)

ok("Data sources is a menu inside AEO", shell.crumb("/aeo/sources/sanity") == ("Data sources", "Sanity")
   and shell.rail("/aeo/sources/airtable").back == "/aeo", str(shell.crumb("/aeo/sources/sanity")))
ok("...and a row in AEO's own menu", any(i.href == "/aeo/sources" for i in shell.rail("/aeo/topics").items))
page = owner.get("/aeo/sources").get_data(as_text=True)
ok("the overview says both are needed, and neither is connected", "needs both" in page
   and page.count("Not connected.") == 3)          # and PostHog, recommended

print("\ntest_connecting_sanity")
f = use(Net(gets=SAN_OK, posts={"/data/mutate/": (403, "")}))
r = owner.post("/aeo/sources/sanity", data={"project_id": "AbC123xy", "dataset": "", "token": SAN_TOKEN})
html = r.get_data(as_text=True)
ok("a read-only token is refused with the sentence that says Editor",
   r.status_code == 400 and "Editor access" in html, r.status_code)
ok("...nothing is saved", settings.get()["project_id"] == "" and not box_secrets.is_set(aeo_app.TOKEN))
ok("...what was typed stays in the form, except the token", 'value="abc123xy"' in html.lower()
   and SAN_TOKEN not in html)
f = use(Net(gets=SAN_OK, posts=MUTATE_OK))
r = owner.post("/aeo/sources/sanity", data={"project_id": "AbC123xy", "dataset": "", "token": SAN_TOKEN})
ok("a token that can write is saved", r.status_code == 303 and settings.get()["project_id"] == "abc123xy"
   and settings.get()["dataset"] == "production")
ok("...under the one name the publisher reads",
   box_secrets.get(aeo_app.TOKEN) == SAN_TOKEN and sources.SANITY_TOKEN == "SANITY_API_TOKEN_OWNBOX")
page = owner.get("/aeo/sources/sanity?said=connected").get_data(as_text=True)
ok("the token is never shown back", SAN_TOKEN not in page and "Leave blank to keep it" in page)
ok("the screen says it is connected", "Checked and saved" in page)
f = use(Net(gets=SAN_OK, posts=MUTATE_OK))
owner.post("/aeo/sources/sanity", data={"project_id": "abc123xy", "dataset": "staging", "token": ""})
ok("a blank token keeps the saved one, and is still proved",
   settings.get()["dataset"] == "staging" and box_secrets.get(aeo_app.TOKEN) == SAN_TOKEN
   and f.seen and f.seen[0][2].get("Authorization") == f"Bearer {SAN_TOKEN}")
f = use(Net(gets=SAN_OK, posts=MUTATE_OK))
r = owner.post("/aeo/sources/sanity", data={"project_id": "not a project!", "token": SAN_TOKEN})
ok("a malformed project ID is refused before anything is sent", r.status_code == 400 and f.seen == [])
r = owner.post("/aeo/sources/sanity", data={"project_id": "abc123xy", "token": "sk has spaces in it now"})
ok("a token with spaces is refused before anything is sent", r.status_code == 400 and f.seen == []
   and box_secrets.get(aeo_app.TOKEN) == SAN_TOKEN)
# A REAL SANITY TOKEN is "sk" and about 180 more characters, longer than any project ID (32) or
# dataset name (64) may be, so it can never pass either field's shape and be shown back.
PASTED = "sk" + "Wx9" * 60
for field in ("project_id", "dataset"):
    r = owner.post("/aeo/sources/sanity",
                   data={"project_id": "abc123xy", "dataset": "production", field: PASTED, "token": ""})
    ok(f"a token pasted into {field} is never shown back on the refusal (OSDev1, #1572)",
       r.status_code == 400 and PASTED.lower() not in r.get_data(as_text=True).lower())
ok("...and the dataset limit that guarantees it is where the check relies on it",
   sources.DATASET_RE.match("a" * 64) and not sources.DATASET_RE.match("a" * 65))

print("\ntest_connecting_airtable")
f = use(Net(gets=AIR_OK))
r = owner.post("/aeo/sources/airtable", data={"table_url": "https://example.com/nope", "api_key": AIR_KEY})
ok("an address that is not a table is refused before anything is sent",
   r.status_code == 400 and "copy the address" in r.get_data(as_text=True).lower() and f.seen == [])
PASTED_KEY = "pat" + "W" * 50
r = owner.post("/aeo/sources/airtable", data={"table_url": PASTED_KEY, "api_key": ""})
ok("a key pasted into Table address is never shown back on the refusal (OSDev1, #1572)",
   r.status_code == 400 and PASTED_KEY not in r.get_data(as_text=True))
thin = {"tables": [{"id": TABLE, "fields": [{"name": "Question"}], "views": [{"id": VIEW}]}]}
use(Net(gets={**AIR_OK, f"/meta/bases/{BASE}/tables": (200, json.dumps(thin))}))
r = owner.post("/aeo/sources/airtable", data={"table_url": URL, "api_key": AIR_KEY})
html = r.get_data(as_text=True)
ok("a table without the template's fields is refused, naming them",
   r.status_code == 400 and "Status" in html and not box_secrets.is_set(sources.AIRTABLE_KEY))
ok("...and the key is not shown back", AIR_KEY not in html)
use(Net(gets=AIR_OK))
r = owner.post("/aeo/sources/airtable", data={"table_url": URL, "api_key": AIR_KEY})
s = settings.get()
ok("a table that passes is saved", r.status_code == 303 and (s["airtable_base"], s["airtable_table"],
   s["airtable_view"]) == (BASE, TABLE, VIEW), r.status_code)
ok("...with its key in box_secrets, apart from the box's other Airtable key",
   box_secrets.get(sources.AIRTABLE_KEY) == AIR_KEY and sources.AIRTABLE_KEY != "AIRTABLE_API_KEY")
page = owner.get("/aeo/sources/airtable").get_data(as_text=True)
ok("the key is never shown back", AIR_KEY not in page and "Leave blank to keep it" in page)
ok("the screen lists the fields the table needs", all(fl in page for fl in sources.TEMPLATE_FIELDS))
use(Net(gets=AIR_OK))
owner.post("/aeo/sources/airtable", data={"table_url": f"https://airtable.com/{BASE}/{TABLE}", "api_key": ""})
ok("saving without a view clears the old one", settings.get()["airtable_view"] == "")
with state.connect() as c:
    keys = {r["key"] for r in c.execute("SELECT key FROM box_settings WHERE machine = 'aeo'").fetchall()}
ok("every key written is one settings.py defines", keys <= set(settings.DEFAULTS),
   sorted(keys - set(settings.DEFAULTS)))
page = owner.get("/aeo/sources").get_data(as_text=True)
ok("the overview shows both connected", "needs both" not in page
   and "Connected to project abc123xy, dataset staging." in page)

print("\ntest_owner_only_to_change")
before, secrets = settings.get(), (box_secrets.get(aeo_app.TOKEN), box_secrets.get(sources.AIRTABLE_KEY))
f = use(Net(gets={**SAN_OK, **AIR_OK}, posts=MUTATE_OK))
r1 = member.post("/aeo/sources/sanity", data={"project_id": "evil1234", "token": "sk" + "E" * 40})
r2 = member.post("/aeo/sources/airtable", data={"table_url": URL, "api_key": "pat" + "E" * 40})
ok("a member's saves are refused, and nothing is sent or written",
   r1.status_code == 403 and r2.status_code == 403 and f.seen == [] and settings.get() == before
   and (box_secrets.get(aeo_app.TOKEN), box_secrets.get(sources.AIRTABLE_KEY)) == secrets)
rows = {i.label: i for i in shell.rail("/aeo/sources").items}
ok("Data sources lists Google Search Console, for the owner only",
   rows.get("Google Search Console") is not None and rows["Google Search Console"].owner_only
   and rows["Google Search Console"].href == "/settings/aeo/google")
page = main_of(member.get("/aeo/sources").get_data(as_text=True))
ok("a member's overview offers no door they are refused at",
   'href="/settings/aeo/google"' not in page and "Connect " not in page and "See Sanity" in page)
for path in ("/aeo/sources/sanity", "/aeo/sources/airtable"):
    page = main_of(member.get(path).get_data(as_text=True))
    ok(f"a member reads {path} without a form or a credential",
       "<form" not in page and "Only the owner" in page and SAN_TOKEN not in page and AIR_KEY not in page)
owner.get("/aeo/sources/sanity?project_id=zzzz9999&token=" + "sk" + "G" * 40)
ok("a GET changes nothing", settings.get()["project_id"] == "abc123xy")

print("\ntest_the_words_a_buyer_reads")
_RESERVED = re.compile(r"\b(phones?|rings?|calls?|dial|lines?|voice)\b", re.I)
for path in ("/aeo/sources", "/aeo/sources/sanity", "/aeo/sources/airtable"):
    for who in (owner, member):
        text = re.sub(r"<[^>]+>", " ", main_of(who.get(path).get_data(as_text=True)))
        ok(f"{path} uses none of the reserved nouns", not _RESERVED.search(text),
           str(_RESERVED.findall(text)))
for sentence in (v for k, v in vars(sources).items() if k.isupper() and isinstance(v, str)):
    ok(f"sources' sentence has no reserved noun: {sentence[:40]}", not _RESERVED.search(sentence))

print("\n— and this file cannot silently fall out of CI —")
if (ROOT / ".github").is_dir():                  # a buyer's box has no repository
    ok("test_aeo_sources is in the workflow's suite list",
       "test_aeo_sources" in (ROOT / ".github/workflows/tests.yml").read_text())

print("\nALL OK" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
