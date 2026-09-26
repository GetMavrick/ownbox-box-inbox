"""The article publisher writes what the contract says, patches rather than replaces, and cannot
reach the network with text the guard would refuse.

NO NETWORK. `core.net` is swapped for a recorder, so every assertion here is about the REQUEST we
would have sent. That is the part we can get wrong silently: a publish that 200s having quietly
erased four fields looks exactly like a publish that worked.
"""
import os, sys, tempfile, json
os.environ.setdefault("AIOS_HERMETIC_TEST", "1")
os.environ.setdefault("AIOS_DB_PATH", tempfile.mkdtemp() + "/t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marketing.aeo_machine import guard, publisher

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  — {detail}"))
    if not cond:
        _failed += 1


class FakeNet:
    """Stands in for core.net and RETURNS ITS SHAPE — (status, body) with a JSON string, not a
    dict. A fake that is kinder than the real thing tests nothing; this one got the real bug.

    `returns_ids=False` answers a create the way Sanity does WITHOUT `returnIds=true`: no id. The
    first version of this fake always returned one, so the missing-id path never ran (review
    item 11). `found_after_create` is the id a slug lookup finds once the create has landed.
    """

    def __init__(self, existing=None, returns_ids=True, found_after_create=None):
        self.existing, self.calls = existing, []
        self.returns_ids, self.found_after_create, self.created = returns_ids, found_after_create, False

    def get_public(self, url, **kw):
        self.calls.append(("GET", url, kw))
        found = self.existing or (self.found_after_create if self.created else None)
        result = {"_id": found, "_updatedAt": "x"} if found else None
        return 200, json.dumps({"result": result})

    def post_public(self, url, **kw):
        self.calls.append(("POST", url, kw))
        self.created = True
        honours = self.returns_ids and "returnIds=true" in url
        return 200, json.dumps({"results": [{"id": self.existing or "new-doc-id"}] if honours else []})

    def mutations(self):
        for method, _url, kw in self.calls:
            if method == "POST" and "mutations" in (kw.get("json") or {}):
                return kw["json"]["mutations"][0]
        return {}


# A box with its settings filled in — as the AEO settings SCREEN saves them. Not YAML: a buyer
# cannot edit YAML, and the exporter's KEEP list strips the section on a sold box, which is what
# blocked the first version of this publisher (OSDev1, #1554). test_aeo_settings.py checks that
# against the exporter itself.
CFG = {"project_id": "p1", "dataset": "production", "api_version": "2025-02-19",
       "site_url": "https://example.test", "host": "example.test", "indexnow_key": "abc123",
       "never_words": ["call", "voice"], "never_phrases": ["voice search"],
       "competitors": [], "allowed_numbers": [], "facts": [], "weekly_cap": 4}
ARTICLE = dict(title="What an AI coworker does", slug="what-an-ai-coworker-does",
               short_answer="It does the work.", body_blocks=[{"_type": "block"}],
               category="Basics", meta_description="A short description.")


from marketing.aeo_machine import settings as real_settings


def use(cfg=CFG, token="tok"):
    """Point the publisher at a box with these settings. One place, so a test cannot half-set.

    ONLY THE STORE IS FAKED. The first version replaced `settings.lists` too, so the real one,
    the one that turned a list saved as text into its characters, never ran here (review item 9).
    Now the publisher uses the real module and only `get()`, the read from the box, is stood in.
    """
    real_settings.get = lambda: dict(cfg)
    publisher.settings = real_settings
    publisher.box_secrets = type("S", (), {"get": staticmethod(lambda n: token)})


def run(fake, cfg=CFG, token="tok", **over):
    publisher.net = fake
    use(cfg, token)
    return publisher.publish(**{**ARTICLE, **over})


print("-- a box that was never set up --")
use({**real_settings.DEFAULTS}, token="")
ready, why = publisher.is_configured()
ok("an unconfigured box answers instead of crashing", ready is False and "project" in why, why)
use(CFG, token="")
ok("...and says which piece is missing, not just 'not configured'",
   "token" in publisher.is_configured()[1], publisher.is_configured()[1])
for bad in ("", "acme-heating.test", "/articles", "ftp://acme.test", "http://["):
    use({**CFG, "site_url": bad})
    ok(f"a box with no absolute website address is not set up ({bad!r})",
       publisher.is_configured() == (False, "no website address"), publisher.is_configured())
use(CFG)
ok("...and one with it is (OSDev1, #1561: the screen and the worker agree)",
   publisher.is_configured() == (True, ""), publisher.is_configured())
ok("the token has ONE name, the one the owner gave it",
   publisher.TOKEN_KEY == "SANITY_API_TOKEN_OWNBOX", publisher.TOKEN_KEY)

# THE BUG THAT BLOCKED THIS PR. The exporter's customer_voice KEEP list carries no `aeo`, so a
# sold box has no YAML section at all. Defaults must therefore live in code, or every sold box
# reports "not configured" forever — the only boxes that matter.
ok("a sold box, whose YAML section the exporter strips, still has a usable dataset",
   real_settings.DEFAULTS["dataset"] == "production"
   and real_settings.DEFAULTS["api_version"],
   "defaults must not depend on the config section")
ok("...and still starts with nothing of OURS in it",
   all(not real_settings.DEFAULTS[k] for k in
       ("project_id", "site_url", "host", "indexnow_key", "never_words", "competitors",
        "allowed_numbers", "facts")))

print("\n-- the guard is in the path, not beside it --")
f = FakeNet()
try:
    run(f, title="Never miss a call again")
    ok("text the guard refuses cannot be published", False, "it published")
except guard.GuardRefused as e:
    ok("text the guard refuses cannot be published", True)
    ok("...and it refused BEFORE any network call", f.calls == [], f.calls)
    ok("...naming the rule", any(r.rule == "never_word" for r in e.refusals))

f = FakeNet()
try:
    run(f, short_answer="Answer 40 questions an hour.")
    ok("an unsourced number in the SHORT ANSWER is caught too, not just the body", False)
except guard.GuardRefused:
    ok("an unsourced number in the SHORT ANSWER is caught too, not just the body", True)

f = FakeNet()
try:
    run(f, short_answer="It costs $499.", lists={"allowed_numbers": ("$499",)})
    ok("a number on the box's own fact list passes", True)
except guard.GuardRefused as e:
    ok("a number on the box's own fact list passes", False, [r.found for r in e.refusals])

# The rules come from the BOX, and the box is where they must come from: a caller that passes
# nothing gets the settings, not an empty guard. Silently permissive is the one failure mode the
# only reviewer cannot have.
f = FakeNet()
try:
    run(f, title="Never miss a call")
    ok("a caller that passes no lists still gets the BOX'S rules, not an empty guard", False)
except guard.GuardRefused:
    ok("a caller that passes no lists still gets the BOX'S rules, not an empty guard", True)

f = FakeNet()
try:
    run(f, cfg={**CFG, "never_words": [], "never_phrases": []}, title="Never miss a call")
    ok("...and a box that listed nothing publishes the same words freely (a plumber's box)", True)
except guard.GuardRefused as e:
    ok("...and a box that listed nothing publishes the same words freely (a plumber's box)",
       False, [r.found for r in e.refusals])

print("\n-- the guard reads the RENDERED words, not the markdown --")
# Blocks written out by hand, NOT built with the converter. This module must not depend on
# content machine even in its tests — that import is the thing the AEO pack exists to avoid.
def blocks(*texts, **kw):
    return [{"_type": "block", "children": [{"_type": "span", "text": t, "marks": []}], **kw}
            for t in texts]

# What the converter emits for "1. Open the app": the digit is list markup, never prose.
numbered = blocks("Open the app", "Press connect", listItem="number")
ok("a numbered list does NOT trip the number rule — the digits are list markup, not prose",
   guard.check(publisher._readable({"title": "T", "body": numbered})) == [],
   publisher._readable({"title": "T", "body": numbered}))

f = FakeNet()
try:
    run(f, body_blocks=blocks("Never miss a call."), source_markdown=None)
    ok("a forbidden word hidden in the BODY is caught — body is what the site renders", False)
except guard.GuardRefused:
    ok("a forbidden word hidden in the BODY is caught — body is what the site renders", True)

ok("a forbidden word in a TABLE cell is caught too",
   "never_word" in {r.rule for r in guard.check(
       publisher._readable({"body": [{"_type": "table", "rows": [
           {"_type": "tableRow", "cells": ["a", "voice"]}]}]}),
       never_words=("voice",))})
ok("...and in image alt text, which a screen reader speaks",
   "never_word" in {r.rule for r in guard.check(
       publisher._readable({"body": [{"_type": "imageUrl", "alt": "a voice"}]}),
       never_words=("voice",))})
ok("markdown syntax itself is not guarded — its prose is the body's prose in another form",
   guard.check(publisher._readable({"title": "T", "sourceMarkdown": "# 1999 heading"})) == [])

print("\n-- the guard reads the article whole (review items 1 and 2) --")
LISTS = {"never_words": ("call",), "never_phrases": ("voice search",), "competitors": ("Acme",),
         "allowed_numbers": ()}
def refused(**fields):
    return {r.rule for r in publisher.refusals(lists=LISTS, **{**ARTICLE, **fields})}
def styled(*spans, marks=(), defs=()):
    return [{"_type": "block", "markDefs": list(defs),
             "children": [{"_type": "span", "text": t, "marks": list(m)} for t, m in spans]}]
ok("a phrase split by bold is still the phrase",
   "never_phrase" in refused(body_blocks=styled(("Try voice ", ()), ("search", ("strong",)), (" now", ()))))
ok("a competitor split across a link is still named",
   "competitor" in refused(body_blocks=styled(("Unlike ", ()), ("Acme", ("l1",)), (" CRM", ()),
                                              defs=[{"_type": "link", "_key": "l1", "href": "/x"}])))
ok("a code block is read", "never_word" in refused(body_blocks=[{"_type": "code", "code": "call()"}]))
ok("image-row alt text is read", "competitor" in refused(
   body_blocks=[{"_type": "imageRow", "images": [{"url": "u", "alt": "Acme dashboard"}]}]))
ok("image-row captions are read", "never_phrase" in refused(
   body_blocks=[{"_type": "imageRow", "images": [{"url": "u", "alt": "x", "caption": "voice search"}]}]))
ok("an image caption is read", "never_word" in refused(
   body_blocks=[{"_type": "image", "alt": "x", "caption": "Missed call"}]))
ok("the author's name is read", "competitor" in refused(author={"name": "Acme team"}))
ok("the slug is read, word by word", "never_word" in refused(slug="never-miss-a-call"))
ok("a link target is read", "competitor" in refused(body_blocks=styled(
   ("see this", ("l1",)), defs=[{"_type": "link", "_key": "l1", "href": "https://x.test/Acme-vs-us"}])))
ok("sourceMarkdown, public in the dataset, is read", "never_phrase" in refused(
   source_markdown="Rank for voice search"))
ok("digits in a URL, a slug, code or Markdown list syntax are not unsourced claims",
   refused(slug="top-10-tips", source_markdown="1. Open it\n2. Go",
           body_blocks=[{"_type": "code", "code": "port = 8080"}]
           + styled(("docs", ("l1",)), defs=[{"_type": "link", "_key": "l1", "href": "https://x.test/v2/2024"}]))
   == set(), refused(slug="top-10-tips"))
ok("...while a number in the prose still is", "unsourced_number" in refused(short_answer="Save 40 hours."))
f = FakeNet()
try:
    run(f, lists=LISTS, body_blocks=[{"_type": "code", "code": "call()"}])
    ok("publish() refuses on the markup too, not only the prose", False, "it published")
except guard.GuardRefused:
    ok("publish() refuses on the markup too, not only the prose", f.calls == [])

print("\n-- a list saved as text is a list, not its letters (review item 9) --")
use({**CFG, "never_words": "call\n voice ,cheap\n\n", "never_phrases": " voice search \n",
     "competitors": "Acme, Inc.\nRival Heat", "allowed_numbers": "$1,599\n499"})
got = real_settings.lists()
ok("words saved one per line, or comma-separated, become clean entries",
   got["never_words"] == ("call", "voice", "cheap"), got["never_words"])
ok("phrases are stripped", got["never_phrases"] == ("voice search",), got["never_phrases"])
ok("a comma inside a competitor's name does not split it", got["competitors"] == ("Acme, Inc.", "Rival Heat"),
   got["competitors"])
ok("...nor inside a number", got["allowed_numbers"] == ("$1,599", "499"), got["allowed_numbers"])
use({**CFG, "never_words": [" call ", "", "call", 7]})
ok("a stored list is stripped, de-duplicated, and emptied of blanks",
   real_settings.lists()["never_words"] == ("call", "7"), real_settings.lists()["never_words"])
f = FakeNet()
use({**CFG, "never_words": "call"})
publisher.net = f
try:
    publisher.publish(**{**ARTICLE, "title": "A guide for coworkers"})
    ok("a one-word list saved as text refuses that word, not the letter a", True)
except guard.GuardRefused as e:
    ok("a one-word list saved as text refuses that word, not the letter a", False, [r.found for r in e.refusals])
use(CFG)

print("\n-- create --")
f = FakeNet()
out = run(f)
m = f.mutations()
ok("a slug that is new is CREATED", "create" in m and out["created"] is True)
ok("the create asks Sanity to return the id", any(me == "POST" and "returnIds=true" in u
                                                 for me, u, _ in f.calls))
ok("the type is the one the contract names", m["create"]["_type"] == "article")
ok("the slug is written in Sanity's slug shape",
   m["create"]["slug"] == {"_type": "slug", "current": ARTICLE["slug"]})
ok("the URL handed back is the live one", out["url"] == "https://example.test/articles/" + ARTICLE["slug"])
ok("the doc id comes from the response, not from hope", out["doc_id"] == "new-doc-id")
f = FakeNet(returns_ids=False, found_after_create="doc-from-lookup")
out = run(f)
ok("a create answered with no id looks the slug up instead of recording None (review item 11)",
   out["doc_id"] == "doc-from-lookup", out)
try:
    run(FakeNet(returns_ids=False))
    ok("...and if even the lookup finds nothing, it raises rather than report success", False)
except RuntimeError as e:
    ok("...and if even the lookup finds nothing, it raises rather than report success",
       "no id" in str(e), str(e))

print("\n-- patch, which is where the data loss lives --")
f = FakeNet(existing="doc-7")
out = run(f, title="A better title")
m = f.mutations()
ok("an existing slug is PATCHED, never created twice", "patch" in m and out["created"] is False)
ok("...IN PLACE, at the id we found", m["patch"]["id"] == "doc-7")
ok("...and NEVER with createOrReplace, which drops unsent fields",
   "createOrReplace" not in json.dumps(f.calls))
ok("the slug is not in the patch — the contract calls it stable and every link depends on it",
   "slug" not in m["patch"]["set"])
ok("the new title is", m["patch"]["set"]["title"] == "A better title")

print("\n-- absent must mean absent --")
f = FakeNet()
run(f, meta_description=None, category=None)
created = f.mutations()["create"]
ok("a field not supplied is OMITTED, not sent as null",
   "metaDescription" not in created and "category" not in created, list(created))
ok("...which on a patch is the difference between leaving a field and ERASING it",
   None not in created.values())

print("\n-- only the contract's fields, spelled its way, with the values it was given --")
f = FakeNet()
AUTHOR = {"name": "Jo Owner", "url": "https://example.test/about"}
HERO = "https://cdn.sanity.io/images/p1/production/abc-1200x630.png"
run(f, faqs=[{"question": "Q?", "answer": "A."}], published_at="2026-09-25T10:00:00Z",
    source_markdown="# H", author=AUTHOR, hero_image=HERO, content_type="B")
created = f.mutations()["create"]
expected = {"_type", "title", "slug", "shortAnswer", "body", "category", "contentType",
            "metaDescription", "heroImage", "author", "faqs", "publishedAt", "sourceMarkdown"}
ok("no field is invented beyond docs/OWNBOX_ARTICLES.md",
   set(created) <= expected, set(created) - expected)
# The first version checked only the NAMES, with an author string, a non-URL hero and a made-up
# content type, all of which it stored. Values are the contract too.
ok("each field carries the value given, under the contract's name",
   created["author"] == AUTHOR and created["heroImage"] == HERO and created["contentType"] == "B"
   and created["faqs"] == [{"question": "Q?", "answer": "A."}]
   and created["publishedAt"] == "2026-09-25T10:00:00Z" and created["sourceMarkdown"] == "# H"
   and created["shortAnswer"] == ARTICLE["short_answer"]
   and created["metaDescription"] == ARTICLE["meta_description"]
   and created["category"] == "Basics", created)

print("\n-- the contract's shapes are checked before anything is written (review item 12) --")
for label, bad in (("an author that is a bare string", {"author": "Ownbox"}),
                   ("an author with no name", {"author": {"url": "https://x.test"}}),
                   ("an author with an extra field", {"author": {"name": "A", "email": "a@b"}}),
                   ("an author link that is not a URL", {"author": {"name": "A", "url": "about"}}),
                   ("a content type the site does not know", {"content_type": "answer"}),
                   ("a hero image that is not a URL", {"hero_image": {"_type": "image"}}),
                   ("FAQs that are not question/answer pairs", {"faqs": ["Q?"]}),
                   ("an empty title", {"title": "  "})):
    f = FakeNet()
    try:
        run(f, **bad)
        ok(f"{label} is refused", False, "it published")
    except publisher.InvalidArticle:
        ok(f"{label} is refused, before any network call", f.calls == [], f.calls)

print("\n-- the API version, which 404s if you read the config straight --")
use({**CFG, "project_id": "p", "dataset": "d", "api_version": "2025-02-19"})
ok("a bare date in settings is sent v-prefixed, as the HTTP API demands",
   publisher._endpoint("mutate") == "https://p.api.sanity.io/v2025-02-19/data/mutate/d",
   publisher._endpoint("mutate"))
use({**CFG, "project_id": "p", "dataset": "d", "api_version": "v2025-02-19"})
ok("...and a setting that already has the v is not given two",
   publisher._endpoint("mutate").count("/v2025") == 1, publisher._endpoint("mutate"))

print("\n-- the query cannot be closed by its own input --")
f = FakeNet()
run(f, slug='x") || true || ("')
get_url = next(u for me, u, _ in f.calls if me == "GET")
ok("the slug travels as a PARAMETER, not spliced into the GROQ",
   "%24slug=" in get_url and '|| true ||' not in get_url.split("query=")[1].split("&")[0])

print("\n-- an unparseable answer is not a successful publish --")
class Broken(FakeNet):
    def post_public(self, url, **kw):
        return 401, "Unauthorized"
try:
    run(Broken())
    ok("a 401 raises instead of returning a doc_id of None", False, "it returned")
except RuntimeError as e:
    ok("a 401 raises instead of returning a doc_id of None", "401" in str(e), str(e))

print("\n-- IndexNow is a notification, never a claim --")
class Dead(FakeNet):
    def post_public(self, url, **kw):
        raise Exception("host unreachable")
publisher.net = Dead()
use(CFG)
ok("a failed ping is reported False, and does NOT raise into the caller",
   publisher.ping_indexnow(["https://example.test/articles/a"]) is False)
use({**CFG, "indexnow_key": ""})
publisher.net = FakeNet()
ok("a box with no IndexNow key skips quietly rather than posting nothing anywhere",
   publisher.ping_indexnow(["https://example.test/a"]) is False)
f = FakeNet()
publisher.net = f
use({**CFG, "site_url": "https://www.acme-heating.test/", "host": "stale.example"})
publisher.ping_indexnow(["https://www.acme-heating.test/articles/a"])
sent = next(kw["json"] for me, u, kw in f.calls if me == "POST")
ok("the IndexNow host is the site's own, from site_url, never a second setting",
   sent["host"] == "www.acme-heating.test", sent)
use({**CFG, "site_url": "acme-heating.test"})
f = FakeNet(); publisher.net = f
publisher.ping_indexnow(["https://acme-heating.test/articles/a"])
ok("...including a site_url saved without its https://",
   next(kw["json"] for me, u, kw in f.calls if me == "POST")["host"] == "acme-heating.test")

print("\n-- it has to work on a stranger's box --")
src = open("marketing/aeo_machine/publisher.py").read().lower()
# upush96l is the project THIS publisher writes to on our box; 5b464472 is the other one. The
# first version only looked for the other one, so the likely leak was the one it could not see.
OURS = ("ownbox.io", "ownbox.app", "upush96l", "5b464472", "brian", "mavrick")
ok("no brand, project id or domain of ours is hardcoded",
   not any(w in src for w in OURS), [w for w in OURS if w in src])
# Read the AST, not the prose: the module's own docstring says the words "brain.think()", so a
# grep over source text can only ever fail here. What we care about is whether it CALLS one.
import ast
tree = ast.parse(open("marketing/aeo_machine/publisher.py").read())
imports = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
    a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
calls = {n.func.attr for n in ast.walk(tree)
         if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
ok("and it reasons about nothing — a deterministic rail calls no LLM (CLAUDE.md §3)",
   not any("brain" in m for m in imports) and "think" not in calls, sorted(imports))

print(f"\n{_failed} FAILED" if _failed else "\nall ok")
sys.exit(1 if _failed else 0)
