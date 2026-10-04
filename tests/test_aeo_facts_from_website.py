"""AEO: the box drafts a business's facts from its own website, word for word, and the owner approves them.

OWNER, 2026-10-04: "Where would a business owner put these facts in? I don't know where those would go… we're not
building for me." OSDev1's 08:27/08:28 assignment, "Yes" in OSDev6's session.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  · a drafted fact is anything but a sentence from the business's own pages, word for word (the model answers with
    sentence numbers; anything else is dropped, never used as text);
  · a script, a style block, or another website's page is read as the business's;
  · anything is saved before the owner approves, or a fact already on the list is offered again;
  · drafting runs inside a web request, or twice at once;
  · a member, or a read-only seat, can start it;
  · the Settings screen stops explaining each field with an example, or loses the Draft button;
  · an empty fact list is reported without offering the draft as the fix.

NO NETWORK, NO TOKENS: pages and the model are stand-ins.

Run: python tests/test_aeo_facts_from_website.py
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
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "aeo_facts.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state                                                   # noqa: E402

state.init_db()

from core import approvals, box_settings, dash                           # noqa: E402
from core.connector import tools as registry                             # noqa: E402
from core.dispatch import app as web                                     # noqa: E402
from marketing.aeo_machine import facts_draft, proposals, settings, tools  # noqa: E402,F401

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


SITE = "https://radiance.example"
HOME = """<html><head><title>Radiance</title><style>.x{color:red}</style>
<script>var secret = "Botox is free for everyone today forever";</script></head><body>
<nav><a href="/pricing">Pricing</a> <a href="/about/">About us</a> <a href="https://other.example/x">Partner</a>
<a href="/blog/2024/a-post">Blog</a> <a href="#top">Top</a></nav>
<h1>Radiance Med Spa</h1>
<p>We are open Tuesday to Saturday, from 9 to 6. Book online any time.</p>
<p>Love this place!!!</p>
<script>window.offer = "Every visitor gets a free treatment worth a lot today";</script>
<style>p.promo::after { content: "Our prices are the lowest anywhere in town"; }</style>
</body></html>"""
PRICING = "<html><body><ul><li>Botox is $12 a unit, with a 20 unit minimum.</li>" \
          "<li>A HydraFacial is $1,599 for a package of ten visits.</li></ul></body></html>"
ABOUT = "<html><body><p>Dr. Lee has been injecting since 2012 and trains other nurses.</p></body></html>"
PAGES = {SITE: HOME, f"{SITE}/pricing": PRICING, f"{SITE}/about": ABOUT}
fetched = []


def fetch(url):
    fetched.append(url)
    return PAGES.get(url, "")


print("test_reading_a_page")
text, links = facts_draft.read(HOME)
ok("scripts and styles are never read as the business's words", "Botox is free" not in text and "color" not in text
   and "free treatment" not in text and "lowest anywhere" not in text, text)
ok("...the page's links are", "/pricing" in links and "/about/" in links)
ok("only sentences of 5 to 45 words count, and a menu is not one", facts_draft.sentences(text) ==
   ["We are open Tuesday to Saturday, from 9 to 6."], facts_draft.sentences(text))
pages = facts_draft._pages_to_read(SITE, links)
ok("it reads the home page, then this site's likeliest pages, never another site's",
   pages[0] == SITE and f"{SITE}/pricing" in pages and f"{SITE}/about" in pages
   and not any("other.example" in p for p in pages) and len(pages) <= facts_draft.MAX_PAGES, pages)
ok("pricing comes before a blog post", pages.index(f"{SITE}/pricing") < pages.index(f"{SITE}/blog/2024/a-post"))
ok("numbers are taken as the guard compares them", facts_draft.numbers_in(
   ["Botox is $12 a unit.", "A HydraFacial is $1,599 for ten."]) == ["12", "1599"])

print("\ntest_nothing_is_invented")
calls = []


def think(**kw):
    calls.append(kw)
    listing = kw["prompt"]
    n_open = next(line.split(".")[0] for line in listing.splitlines() if "open Tuesday" in line)
    n_botox = next(line.split(".")[0] for line in listing.splitlines() if "Botox is $12" in line)
    return json.dumps({"picks": [int(n_botox), 999, "Botox is free", int(n_botox), int(n_open)]})


got = facts_draft.draft(SITE, think=think, fetch=fetch)
texts = [f["text"] for f in got["facts"]]
ok("one reasoning call, named, on the machine's AI account key", len(calls) == 1 and calls[0]["task"] ==
   "aeo.facts_draft" and calls[0]["machine"] == "seo", calls[0].keys() if calls else calls)
ok("the facts are the picked sentences, word for word; a bad number, text and a repeat are dropped",
   texts == ["Botox is $12 a unit, with a 20 unit minimum.", "We are open Tuesday to Saturday, from 9 to 6."], texts)
ok("each fact says which page it came from", got["facts"][0]["page"] == f"{SITE}/pricing"
   and got["facts"][1]["page"] == SITE)
ok("...and its numbers are drafted with it", got["numbers"] == ["12", "20", "9", "6"], got["numbers"])
ok("every drafted fact appears on a page, exactly", all(any(t in facts_draft.read(h)[0] for h in PAGES.values())
                                                       for t in texts))
for answer, why in (('{"picks": ["We cure everything."]}', "a sentence of the model's own"),
                    ("I think sentences 1 and 2.", "an answer that isn't JSON")):
    got = facts_draft.draft(SITE, think=lambda **kw: answer, fetch=fetch)
    ok(f"{why} gives no facts, and says so", got["facts"] == [] and got.get("why"), got)
ok("a website that doesn't answer is said in words", "didn't answer" in facts_draft.draft(
   SITE, think=think, fetch=lambda u: "")["why"])
ok("no website address is said in words", "website address" in facts_draft.draft("", think=think, fetch=fetch)["why"])

print("\ntest_drafting_waits_for_the_owner")
c = web.test_client()
OWNER = state.owner_user()["id"]
c.set_cookie(dash.COOKIE, dash.new_session(OWNER))
r = c.post("/aeo/settings/draft")
ok("with no website on AEO Settings, the button says what to do first", r.status_code == 400
   and "website" in r.get_data(as_text=True).lower())
box_settings.put("seo", "site_url", SITE)
box_settings.put("seo", "facts", ["We are open Tuesday to Saturday, from 9 to 6."])     # already on his list
r = c.post("/aeo/settings/draft")
ok("the button queues the draft for the worker and says so", r.status_code == 303 and "said=drafting" in r.location)
with state.connect() as db:
    jobs = [dict(x) for x in db.execute("SELECT * FROM jobs WHERE intent = ?", (proposals.DRAFT_INTENT,))]
ok("...one job, never run inside the request", len(jobs) == 1 and jobs[0]["status"] == "queued" and not calls[1:])
ok("a second press while it runs is refused, in words", c.post("/aeo/settings/draft").status_code == 400)
facts_draft.draft = lambda site: {"facts": [{"text": "Botox is $12 a unit, with a 20 unit minimum.",
                                             "page": f"{SITE}/pricing"},
                                            {"text": "We are open Tuesday to Saturday, from 9 to 6.", "page": SITE}],
                                  "numbers": ["12", "20", "9", "6"], "pages": [SITE, f"{SITE}/pricing"]}
out = proposals.do_draft(jobs[0])
waiting = [a for a in approvals.waiting() if a["detail"].get("do") == "add_facts"]
ok("the worker puts what it found on Approvals as one proposal", out["proposed"] == 1 and len(waiting) == 1, out)
args = waiting[0]["detail"]["arguments"]
ok("the owner reads each fact, the numbers and the pages read; a fact already on the list isn't offered again",
   args == {"fact 1": "Botox is $12 a unit, with a 20 unit minimum.", "numbers it may use": "12, 20, 9, 6",
            "read from": f"{SITE}, {SITE}/pricing"}, args)
ok("nothing is saved before the tap", settings.facts() == ("We are open Tuesday to Saturday, from 9 to 6.",))
with state.connect() as db:                               # the worker marks its job done when do_draft returns
    db.execute("UPDATE jobs SET status = 'done' WHERE intent = ?", (proposals.DRAFT_INTENT,))
page = c.get("/aeo/settings").get_data(as_text=True)
ok("Settings says the draft is waiting, with the way to it", "waiting for your OK" in page and 'href="/approvals"' in page)
res = approvals.decide(waiting[0]["id"], True, by="owner")
ok("Approve adds the facts and numbers", res["ok"] and "Botox is $12 a unit, with a 20 unit minimum." in settings.facts()
   and set(settings.lists()["allowed_numbers"]) == {"12", "20", "9", "6"}, (res, settings.lists()))
with state.connect() as db:
    db.execute("UPDATE jobs SET status = 'done' WHERE intent = ?", (proposals.DRAFT_INTENT,))
proposals.start_draft(by="o")
with state.connect() as db:
    job2 = dict(db.execute("SELECT * FROM jobs WHERE intent = ? AND status = 'queued'", (proposals.DRAFT_INTENT,)).fetchone())
out = proposals.do_draft(job2)
ok("drafting again with nothing new proposes nothing and says why", out["proposed"] == 0
   and "already on your list" in proposals.last_draft().get("said", ""), proposals.last_draft())

print("\ntest_who_may_start_it")
READ, ACT = {"id": "r", "role": "read"}, {"id": "a", "role": "act", "label": "the owner's Claude"}
ok("a read-only seat can't", "aeo.propose_facts" not in {t["name"] for t in registry.visible_to(READ)})
with state.connect() as db:
    db.execute("UPDATE jobs SET status = 'done' WHERE intent = ?", (proposals.DRAFT_INTENT,))
body, code = registry.call("aeo.propose_facts", {}, ACT)
ok("the owner's own AI can, and is told what happens next", code == 200 and body["result"]["asked"]
   and "Approvals" in body["result"]["text"], body)
MEMBER = state.add_user("sam@radiance.example", name="Sam")["id"]
m = web.test_client()
m.set_cookie(dash.COOKIE, dash.new_session(MEMBER))
ok("a member can't press it", m.post("/aeo/settings/draft").status_code == 403)
ok("...and isn't shown it", "Draft these from my website" not in m.get("/aeo/settings").get_data(as_text=True))

print("\ntest_the_settings_screen_explains_itself")
page = c.get("/aeo/settings").get_data(as_text=True)
ok("the Draft button is there for the owner", "Draft these from my website" in page and 'action="/aeo/settings/draft"' in page)
ok("each list says what it's for with a med spa example", "For a med spa" in page and "Botox is $12 a unit" in page
   and "cure, guaranteed" in page and "the spa down the street" in page)
box_settings.clear("seo", "facts")
st = registry.call("aeo.status", {}, READ)[0]["result"]
ok("with no facts, status offers the draft as the fix", any("aeo.propose_facts" in m and "/aeo/settings" in m
                                                            for m in st["missing"]), st["missing"])
page = c.get("/aeo/topics").get_data(as_text=True)
ok("...and so does the setup card", "Draft the facts from your website" in page)

print("\n— and this file cannot silently fall out of CI —")
if (ROOT / ".github").is_dir():
    ok("test_aeo_facts_from_website is in the workflow's suite list",
       "test_aeo_facts_from_website" in (ROOT / ".github/workflows/tests.yml").read_text())

print("\nALL OK" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
