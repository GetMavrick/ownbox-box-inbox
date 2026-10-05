"""The business's full scan: its own website, quoted, into the business context (#1957 C3; OSDev1, 2026-10-04).

Owner, 2026-10-04: "These people need to have a valuable machine on day one. Not a dumb box." The light scan (C1)
suggests a website; once one is known, the full scan reads it and keeps what it says, so the Morning Review, the AEO
writer and the Inbox's drafter know the business from the first morning.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * a profile line that is not a sentence from the business's own site, word for word, with its page;
  * a model answer that isn't a sentence number becoming text;
  * the AEO Machine keeping a website reader of its own instead of core's;
  * the profile not reaching a call that isn't isolated, or the owner's plans reaching it at all;
  * the scan spending a try when the box can't think yet, trying forever, or never reading a changed website.

NO NETWORK, NO MODEL: fetch and think are handed in.

Run: python tests/test_business_full_scan.py
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
TMP = tempfile.mkdtemp()
os.environ["AIOS_DB_PATH"] = os.path.join(TMP, "full_scan.db")

from core import state  # noqa: E402

state.init_db()
from core import box_settings, brain, site_reader  # noqa: E402
from core import business_context as bc  # noqa: E402

brain.KNOWLEDGE_DIR = pathlib.Path(TMP) / "knowledge"          # never the repo's my/knowledge
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -- {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


SITE = "https://glow-medspa.example"
PAGES = {
    SITE: """<html><body><nav><a href="/pricing">Pricing</a><a href="/about">About</a>
<a href="https://elsewhere.example/x">Partner</a></nav>
<h1>Glow Med Spa</h1><p>We offer Botox, fillers and Hydrafacials in South Austin.</p>
<p>Book your glow today and feel amazing!</p></body></html>""",
    SITE + "/pricing": "<html><body><p>Botox is $12 a unit, with a 20 unit minimum.</p>"
                       "<p>A Hydrafacial costs $189 and takes about an hour.</p></body></html>",
    SITE + "/about": "<html><body><p>Our nurse injectors have more than ten years of experience.</p>"
                     "<p>We are open Tuesday to Saturday, from 9 to 6.</p>"
                     "<p>Most of our clients are busy professionals in South Austin.</p></body></html>",
}
fetched = []


def fetch(url):
    fetched.append(url)
    return PAGES.get(url.rstrip("/"), "")


def numbered():
    return site_reader.numbered(SITE, fetch=fetch)["sentences"]


def num(text):
    return next(i for i, (s, _) in enumerate(numbered(), 1) if s.startswith(text))


calls = []


def think_with(answer):
    def think(**kw):
        calls.append(kw)
        return answer if isinstance(answer, str) else json.dumps(answer)
    return think


print("test_one_reader")
from marketing.aeo_machine import facts_draft  # noqa: E402
ok("the AEO facts draft reads with core's reader, not one of its own",
   facts_draft.read is site_reader.read and facts_draft.sentences is site_reader.sentences
   and facts_draft._pages_to_read is site_reader.pages_to_read)
src = (ROOT / "marketing/aeo_machine/facts_draft.py").read_text()
ok("...and keeps no HTML parser of its own", "HTMLParser" not in src and "class _Text" not in src)
got = site_reader.numbered(SITE, fetch=fetch)
ok("the home page and the same site's pages are read, another site never",
   got["pages"][0] == SITE and not any("elsewhere" in u for u in fetched) and got["status"] == "", (got, fetched))
ok("a website that doesn't answer says so", site_reader.numbered(SITE, fetch=lambda u: "")["status"] == "no_answer")

print("\ntest_the_full_scan_quotes_the_site")
bc.put("website", SITE, by="owner")
bc.put("coming", [{"what": "Spring skin package", "month": "2027-03"}], by="owner")
answer = {"sells": [num("We offer Botox")], "prices": [num("Botox is $12"), num("A Hydrafacial costs")],
          "customers": [num("Most of our clients")], "area": [], "hours": [num("We are open")],
          "different": [num("Our nurse injectors"), "We are the best med spa in Texas.", 999, "2"]}
out = bc.full_scan(think=think_with(answer), fetch=fetch)
prof = bc.get()["profile"]
every = {s for s, _ in numbered()}
ok("the scan says it worked", out["ok"] is True and out["lines"] == 7, out)  # "2" is a number: sentence 2
ok("every profile line is a sentence from the site, word for word, with its page",
   prof and all(p["line"] in every and p["source"].startswith(SITE) for p in prof), prof)
ok("prices quote the pricing page", [p["source"] for p in prof if p["field"] == "prices"] == [SITE + "/pricing"] * 2)
ok("a sentence the model wrote, or a number out of range, is dropped, never used",
   not any("best med spa" in p["line"] for p in prof) and len([p for p in prof if p["field"] == "different"]) == 2,
   [p for p in prof if p["field"] == "different"])
ok("a field the site doesn't state stays empty", not any(p["field"] == "area" for p in prof))
ok("one reasoning call, isolated, answering with numbers only",
   len(calls) == 1 and calls[0]["isolated"] is True and calls[0]["task"] == "business.full_scan"
   and "Never write a sentence of your own" in calls[0]["system"], calls[-1:])
ok("at most six lines per field", len(bc._picks(json.dumps({"sells": list(range(1, 9))}), numbered())) == 6)
ok("an answer that isn't JSON gives no lines", bc._picks("I think it sells Botox.", numbered()) == [])

print("\ntest_the_profile_reaches_the_calls_that_need_it")
f = brain.KNOWLEDGE_DIR / bc.PROFILE_FILE
body = f.read_text() if f.exists() else ""
ok("written to my/knowledge/business-profile.md, saying where it came from",
   "Written by your box from your own website" in body and "## What it costs" in body
   and f"Botox is $12 a unit, with a 20 unit minimum. ({SITE}/pricing)" in body, body[:400])
ok("the owner's plans are never in it (the Inbox's drafter writes to customers)", "Spring skin" not in body)
ctx = brain._with_knowledge(None, False) or ""
ok("a call that isn't isolated carries it (the Morning Review, the AEO writer, the Inbox's drafter)",
   "Botox is $12 a unit" in ctx)
ok("...an isolated one doesn't", brain._with_knowledge(None, True) is None)
mail = (ROOT / "core/brain.py").read_text()
ok("the voice file keeps its own first line", 'made_from: str = "your own sent mail"' in mail)

print("\ntest_your_business_shows_what_the_site_says")
from core import dash  # noqa: E402
from core.dash import business as screen  # noqa: E402
from core.dispatch import app  # noqa: E402
o = app.test_client()
o.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
m = app.test_client()
m.set_cookie(dash.COOKIE, dash.new_session(state.add_user("sam@glow-medspa.example", name="Sam", role="member")["id"]))
page = o.get(screen.DOOR).get_data(as_text=True)
ok("the owner sees what the website says, each line quoted with its page",
   "What your website says" in page and "Botox is $12 a unit, with a 20 unit minimum." in page
   and f'href="{SITE}/pricing"' in page and ">pricing</a>" in page, page[:300])
ok("...grouped under what it is about", page.index("What it costs") < page.index("Botox is $12 a unit"))
ok("a member sees it too, and can't change it", "What your website says" in m.get(screen.DOOR).get_data(as_text=True))
bc.put("profile", [{"line": "<script>alert(1)</script> We open at 9.", "source": SITE + "/", "field": "hours"},
                   {"line": "Click here.", "source": "javascript:alert(1)", "field": "sells"}], by="test")
page = o.get(screen.DOOR).get_data(as_text=True)
ok("a line from the web is shown as text, never run, and only a web page is ever linked",
   "<script>alert(1)" not in page and "&lt;script&gt;" in page and "javascript:" not in page
   and ">home page</a>" in page)
bc.put("profile", [], by="test")
ok("with nothing read yet there is no card", "What your website says" not in o.get(screen.DOOR).get_data(as_text=True))

print("\ntest_a_site_that_says_nothing")
ok("a site of images says so in words, and is not retried",
   bc.full_scan(think=think_with({}), fetch=lambda u: "<html><body><img src=a.png></body></html>")
   == {"ok": False, "final": True, "lines": 0,
       "why": "Your website has no sentences the box could read. It may be built only with images or scripts."})
quiet = bc.full_scan(think=think_with({"sells": []}), fetch=fetch)
ok("a site with nothing to quote is final, and the old profile is not kept as if new",
   quiet["final"] is True and bc.get()["profile"] == [], quiet)
ok("a site that doesn't answer is tried again later",
   "tries again later" in bc.full_scan(think=think_with({}), fetch=lambda u: "")["why"])

print("\ntest_the_scan_runs_once_per_website")
box_settings.put(bc.NS, "_full_scan", {}, set_by="test")
ok("with no AI yet the scan waits and spends no try",
   bc.full_scan_if_needed()["status"] == "waiting" and not box_settings.get(bc.NS, "_full_scan"))
real = bc.full_scan
bc.full_scan = lambda site=None, **kw: {"ok": False, "why": "no answer", "lines": 0}
runs = [bc.full_scan_if_needed()["status"] for _ in range(5)]
ok("a site that never answers is tried three times, then left alone",
   runs == ["none", "none", "none", "gave_up", "gave_up"], runs)
bc.full_scan = lambda site=None, **kw: {"ok": True, "why": "", "lines": 4}
bc.put("website", "glow-austin.example", by="owner")
ok("a changed website is read again", bc.full_scan_if_needed()["status"] == "done"
   and box_settings.get(bc.NS, "_full_scan")["site"] == "https://glow-austin.example")
bc.full_scan = lambda site=None, **kw: (_ for _ in ()).throw(AssertionError("read twice"))
ok("...and once read, it is not read again", bc.full_scan_if_needed()["status"] == "done")
ok("the screen can tell when it was read", bc.full_scan_state().get("lines") == 4)
bc.full_scan = lambda site=None, **kw: {"ok": True, "why": "", "lines": 9}
bc.put("website", "glow-downtown.example", by="owner")
ok("a website changed after a finished scan is read again too",
   bc.full_scan_if_needed()["status"] == "done" and bc.full_scan_state().get("lines") == 9, bc.full_scan_state())
bc.full_scan = real
box_settings.put(bc.NS, "website", "", set_by="test")
box_settings.put("seo", "site_url", "", set_by="test")
ok("no website, nothing to read", bc.full_scan_if_needed()["status"] == "no_website")

print("\ntest_the_suite_runs_in_ci")
if (ROOT / ".github").is_dir():                         # a box has no repository
    wf = (ROOT / ".github/workflows/tests.yml").read_text()
    ok("test_business_full_scan is in the workflow's suite list", "test_business_full_scan \\" in wf)

print("\nALL FULL-SCAN CHECKS PASS" if not _failed else f"\n{_failed} FULL-SCAN CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
