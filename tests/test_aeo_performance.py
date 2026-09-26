"""AEO > Performance, and the PostHog connection it reads from.

OWNER, 2026-09-25, in OSDev6's session: "post hog is going to be the next data source we connect ...
we should suggest post hog" and "Customers are going to demand current performance numbers
immediately."

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  · a failed PostHog check saves anything, or the key is shown back on any page;
  · a key that works but finds no page views is refused (it is connected; the snippet is missing);
  · the key lands anywhere but box_secrets, under the name the Performance screen reads;
  · anything is ever written to PostHog (every request is a read-only query);
  · the numbers are not limited to this website's host when AEO Settings names it;
  · a PostHog that is down breaks the Performance page instead of costing it its numbers;
  · PostHog is asked again on every page open (the numbers are cached);
  · a member, or a GET, changes anything;
  · a screen uses one of the nouns reserved for the receptionist machine (CLAUDE.md).

NO NETWORK. `posthog.net` is replaced with a scripted stand-in that records every request.

Run: python tests/test_aeo_performance.py
"""
from __future__ import annotations

import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "aeo_performance.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state                                                   # noqa: E402

state.init_db()

from core import box_secrets, box_settings, dash, net, shell             # noqa: E402
from core.dispatch import app as web                                     # noqa: E402
from marketing.aeo_machine import app as aeo_app, posthog, settings      # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


class Net:
    """PostHog's query API, scripted. Answers each query by a fragment of its HogQL."""
    PostRefused = net.PostRefused

    def __init__(self, status=200, answers=None, raises=False):
        self.status, self.answers, self.raises = status, answers or {}, raises
        self.seen = []

    def get_public(self, url, **_kw):
        self.seen.append(("GET", url, None, None))
        return 0, ""

    def post_public(self, url, *, headers=None, json=None, **_kw):
        self.seen.append(("POST", url, dict(headers or {}), json))
        if self.raises:
            raise net.PostRefused("no route")
        q = (json or {}).get("query", {}).get("query", "")
        for frag, rows in self.answers.items():
            if frag in q:
                return self.status, __import__("json").dumps({"results": rows})
        return self.status, '{"results": [[0]]}'


def use(fake):
    posthog.net = fake
    posthog.forget()
    return fake


KEY = "phx_" + "K" * 40
WEEK = {"countIf(timestamp >= now()": [[120, 100, 40, 50, 60, 0, 9, 3]],
        "LIKE '/articles/%'\n            GROUP BY path": [["/articles/what-goes-in-a-sow", 31]],
        "GROUP BY d": [["chatgpt.com", 9], ["google.com", 7]]}
CHECK = {"INTERVAL 30 DAY": [[742]]}

print("test_the_address")
ok("a self-hosted PostHog on its own port keeps the port",
   posthog.api_host("https://ph.northwind.example:8443") == "https://ph.northwind.example:8443")
ok("US and EU are PostHog Cloud's API hosts", posthog.api_host("us") == "https://us.posthog.com"
   and posthog.api_host("EU") == "https://eu.posthog.com")
ok("a self-hosted address is kept, as https and host only",
   posthog.api_host("https://ph.northwind.example/") == "https://ph.northwind.example")
for bad in ("http://ph.northwind.example", "https://us.i.posthog.com", "ph", "https://x.example/path",
            "javascript:alert(1)", "", "https://ph.example:99999"):
    try:
        posthog.api_host(bad)
        refused = False
    except posthog.BadHost:
        refused = True
    ok(f"refused as an API address: {bad!r}", refused)

print("\ntest_the_check")
f = use(Net(answers=CHECK))
ok("a working key: no problem, and the 30-day page views",
   posthog.check("https://us.posthog.com", "4242", KEY) == (None, 742))
ok("...asked of the project's query API, with the key as a bearer, as a HogQL query",
   f.seen[0][1] == "https://us.posthog.com/api/projects/4242/query/"
   and f.seen[0][2].get("Authorization") == f"Bearer {KEY}"
   and f.seen[0][3]["query"]["kind"] == "HogQLQuery", f.seen[0][:2])
ok("...and it is a SELECT: nothing is ever written to PostHog",
   all(s[3]["query"]["query"].lstrip().upper().startswith("SELECT") for s in f.seen))
for status, want in ((401, posthog.BAD_KEY), (403, posthog.NO_ACCESS), (404, posthog.NO_PROJECT)):
    use(Net(status=status))
    ok(f"{status} gets its own sentence", posthog.check("https://us.posthog.com", "1", KEY)[0] == want)
use(Net(raises=True))
ok("no answer at all says so", posthog.check("https://us.posthog.com", "1", KEY)[0] == posthog.UNREACHABLE)
use(Net(status=500))
ok("any other error is still one sentence, with its number",
   "(500)" in posthog.check("https://us.posthog.com", "1", KEY)[0])
use(Net(answers={"INTERVAL 30 DAY": "nope"}))
ok("an answer that cannot be read says so",
   posthog.check("https://us.posthog.com", "1", KEY)[0] == posthog.UNREADABLE)
ok("the missing-scope sentence names the scope, in PostHog's own words", posthog.SCOPE in posthog.NO_ACCESS)

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

ok("PostHog is a row in the Data sources menu",
   any(i.href == "/aeo/sources/posthog" for i in shell.rail("/aeo/sources").items))
ok("Performance is a row in AEO's own menu",
   any(i.href == "/aeo/performance" for i in shell.rail("/aeo/topics").items))
page = main_of(owner.get("/aeo/sources").get_data(as_text=True))
ok("the overview offers PostHog, as Recommended", "Connect PostHog" in page and "Recommended" in page)
ok("...and an unconnected PostHog is not drawn as a problem", not re.search(
    r'<div class="card" style="border-color:var\(--danger\)"><h2>PostHog', page))

page = main_of(owner.get("/aeo/performance").get_data(as_text=True))
ok("before PostHog: the Performance screen says how to connect it, in one tap",
   "Connect PostHog" in page and 'href="/aeo/sources/posthog"' in page)
f = use(Net())
member.get("/aeo/performance")
ok("...and nobody's page open asks PostHog anything", f.seen == [])

print("\ntest_connecting_posthog")
f = use(Net(status=403))
r = owner.post("/aeo/sources/posthog", data={"region": "eu", "project": "4242", "api_key": KEY})
html = r.get_data(as_text=True)
ok("a key that cannot read the project is refused, with the scope named",
   r.status_code == 400 and posthog.SCOPE in html, r.status_code)
ok("...nothing is saved", settings.get()["posthog_project"] == "" and not box_secrets.is_set(posthog.KEY))
ok("...the key is never shown back", KEY not in html)
ok("...and the region and project stay chosen", 'value="eu" checked' in html and 'value="4242"' in html)
r = owner.post("/aeo/sources/posthog", data={"region": "own", "own_host": "https://us.i.posthog.com",
                                             "project": "4242", "api_key": KEY})
html = r.get_data(as_text=True)
ok("the event-sending address is refused before PostHog is asked",
   r.status_code == 400 and "sending events" in html)
ok("...and the address that failed is not put back in the form", 'value="https://us.i.posthog.com"'
   not in html and "Choose US or EU" in html)
f = use(Net(answers=CHECK))
r = owner.post("/aeo/sources/posthog", data={"region": "us", "project": "42x", "api_key": KEY})
ok("a project ID that is not a number is refused, and PostHog is not asked",
   r.status_code == 400 and f.seen == [])

f = use(Net(answers={"INTERVAL 30 DAY": [[0]]}))
r = owner.post("/aeo/sources/posthog", data={"region": "us", "project": "4242", "api_key": KEY})
ok("a key that works on a project with no page views is CONNECTED",
   r.status_code == 303 and box_secrets.get(posthog.KEY) == KEY
   and settings.get()["posthog_host"] == "https://us.posthog.com"
   and settings.get()["posthog_project"] == "4242", r.status_code)
page = owner.get(r.headers["Location"]).get_data(as_text=True)
ok("...with the warning that the snippet may be missing", "snippet" in page and "no page" in page)

f = use(Net(answers=CHECK))
r = owner.post("/aeo/sources/posthog", data={"region": "us", "project": "4243", "api_key": ""})
ok("a blank key keeps the saved one, and it is the one that is checked",
   r.status_code == 303 and f.seen and f.seen[0][2]["Authorization"] == f"Bearer {KEY}"
   and settings.get()["posthog_project"] == "4243")
for data, why in (({"region": "own", "own_host": "https://ph.attacker.example"}, "a new own address"),
                  ({"region": "eu"}, "another region")):
    f = use(Net(answers=CHECK))
    r = owner.post("/aeo/sources/posthog", data={**data, "project": "4243", "api_key": ""})
    ok(f"{why} with the key left blank: refused, and the saved key is sent NOWHERE (OSDev1, HIGH)",
       r.status_code == 400 and f.seen == [] and "Paste the key again" in r.get_data(as_text=True)
       and settings.get()["posthog_host"] == "https://us.posthog.com", (r.status_code, f.seen[:1]))
page = owner.get("/aeo/sources/posthog").get_data(as_text=True)
ok("the saved key is never on the page", KEY not in page and "Saved. Leave blank" in page)

r1 = member.post("/aeo/sources/posthog", data={"region": "us", "project": "1", "api_key": "phx_" + "E" * 40})
ok("a member's save is refused, and nothing is changed",
   r1.status_code in (403, 400) and settings.get()["posthog_project"] == "4243"
   and box_secrets.get(posthog.KEY) == KEY, r1.status_code)
page = main_of(member.get("/aeo/sources/posthog").get_data(as_text=True))
ok("a member reads the PostHog page without a form or the key",
   "<form" not in page and "Only the owner" in page and KEY not in page)
owner.get("/aeo/sources/posthog?project=9&region=us")
ok("a GET changes nothing", settings.get()["posthog_project"] == "4243")

print("\ntest_the_numbers")
box_settings.put("seo", "host", "northwind.example")
f = use(Net(answers=WEEK))
p = posthog.performance()
ok("the week's numbers, and the change on the week before",
   p["ok"] and p["views"] == 120 and p["views_change"] == 20 and p["visitors"] == 40
   and p["visitors_change"] == -20 and p["ai_visits"] == 9 and p["ai_visits_change"] == 200, p)
ok("a number with nothing the week before is new, not infinite", p["article_views_change"] is None)
ok("every query is limited to this website's host, with and without www.",
   f.seen and all("properties.$host IN ('northwind.example', 'www.northwind.example')"
                  in s[3]["query"]["query"] for s in f.seen))
ok("...and the site itself is not counted as where visitors came from",
   any("d NOT IN ('northwind.example', 'www.northwind.example')" in s[3]["query"]["query"]
       for s in f.seen))
ok("...and every one is a SELECT", all(
    s[3]["query"]["query"].lstrip().upper().startswith("SELECT") for s in f.seen))
ok("the AI answer engines are counted by where the visit came from",
   "chatgpt.com" in f.seen[0][3]["query"]["query"] and "perplexity.ai" in f.seen[0][3]["query"]["query"])
ok("...and ordinary search is not an answer engine (OSDev1's review)",
   "duckduckgo" not in f.seen[0][3]["query"]["query"] and "brave" not in f.seen[0][3]["query"]["query"])
ok("page speed is gone (owner, 2026-09-25: 'I'm not interested in any Page speed stuff')",
   "vitals" not in p and not any("web_vitals" in s[3]["query"]["query"] for s in f.seen))
n = len(f.seen)
posthog.performance()
ok("a second open inside the cache window asks PostHog nothing", len(f.seen) == n)
posthog.performance(fresh=True)
ok("...and fresh=True asks again", len(f.seen) > n)

posthog.forget()
page = main_of(owner.get("/aeo/performance").get_data(as_text=True))
ok("no page speed anywhere on the screen", "speed" not in page.lower() and "vital" not in page.lower())
ok("the screen shows the four tiles", all(k in page for k in (
    "Visitors", "Page views", "Article views", "From AI answers")) and page.count('class="pf-t"') == 4)
ok("...each with its change", "&uarr; 20% vs last week" in page and "&darr; 20%" in page)
ok("...and no change is made up where there is no week before", page.count("vs last week") == 3
   and "New this week" not in page)
ok("...the most read article, and where visitors came from",
   ">what-goes-in-a-sow<" in page and "chatgpt.com" in page)
ok("the tiles are two across on a mobile device and four on a wide screen (mobile first)",
   ".pf-g{display:grid;grid-template-columns:1fr 1fr" in aeo_app._PH_CSS
   and "@media (min-width:760px){.pf-g{grid-template-columns:repeat(4,1fr)}}" in aeo_app._PH_CSS)
page_m = main_of(member.get("/aeo/performance").get_data(as_text=True))
ok("a member sees the same numbers", "From AI answers" in page_m and "<form" not in page_m)


box_settings.put("seo", "host", "www.northwind.example")
f = use(Net(answers=WEEK))
posthog.performance()
ok("a host saved with www. still matches both", all(
    "IN ('northwind.example', 'www.northwind.example')" in s[3]["query"]["query"] for s in f.seen))
box_settings.put("seo", "host", "northwind.example")

use(Net(answers={**WEEK, "countIf(timestamp >= now()": [[0, 0, 0, 0, 0, 0, 0, 0]]}))
page = main_of(owner.get("/aeo/performance").get_data(as_text=True))
ok("nothing recorded in either week: one card saying so, never four tiles of zero (OSDev1)",
   "No visits recorded yet" in page and "snippet" in page and 'class="pf-t"' not in page)

for bad, why in (({"countIf(timestamp >= now()": [[1, 2, 3]]}, "a totals row of 3 columns"),
                 ({next(k for k in WEEK if "GROUP BY path" in k): [["/articles/x"]]},
                  "an article row of 1 column"),
                 ({"GROUP BY d": [None]}, "a null row"),
                 ({"countIf(timestamp >= now()": [None]}, "a null totals row"),
                 ({"countIf(timestamp >= now()": []}, "no totals row at all")):
    use(Net(answers={**WEEK, **bad}))
    r = owner.get("/aeo/performance")
    ok(f"{why}: the page says it could not be read, and never 500s (OSDev1)",
       r.status_code == 200 and aeo_app._esc(posthog.UNREADABLE) in r.get_data(as_text=True),
       r.status_code)

f = use(Net(status=500))
posthog.performance()
n = len(f.seen)
posthog.performance()
ok("a failure is kept for a minute: PostHog down is not asked again on every open (OSDev1)",
   n == 1 and len(f.seen) == n, (n, len(f.seen)))
for k, (t, v) in list(posthog._cache.items()):
    posthog._cache[k] = (t - posthog.FAIL_CACHE_S - 1, v)
posthog.performance()
ok("...and asked again once the minute is up", len(f.seen) == n + 1)

use(Net(status=401))
page_r = owner.get("/aeo/performance")
page = main_of(page_r.get_data(as_text=True))
ok("PostHog refusing the key costs the page its numbers, never the page",
   page_r.status_code == 200 and posthog.BAD_KEY in page and 'class="pf-t"' not in page, page_r.status_code)
use(Net(raises=True))
ok("PostHog down: performance() says why and never raises",
   posthog.performance() == {"ok": False, "why": posthog.UNREACHABLE})
use(Net(answers={"countIf(timestamp >= now()": "nope"}))
ok("an answer that cannot be read: says so", posthog.performance().get("why") == posthog.UNREADABLE)
ok("no credential is ever in what performance() returns", KEY not in repr(posthog.performance()))

print("\ntest_the_words_a_buyer_reads")
_RESERVED = re.compile(r"\b(phones?|rings?|calls?|dial|lines?|voice)\b", re.I)
use(Net(answers=WEEK))
for path in ("/aeo/sources", "/aeo/sources/posthog", "/aeo/performance"):
    for who in (owner, member):
        text = re.sub(r"<[^>]+>", " ", main_of(who.get(path).get_data(as_text=True)))
        ok(f"{path} uses none of the reserved nouns", not _RESERVED.search(text),
           str(_RESERVED.findall(text)))
for sentence in (v for k, v in vars(posthog).items() if k.isupper() and isinstance(v, str)):
    ok(f"posthog's sentence has no reserved noun: {sentence[:40]}", not _RESERVED.search(sentence))

print("\n— and this file cannot silently fall out of CI —")
if (ROOT / ".github").is_dir():                  # a buyer's box has no repository
    ok("test_aeo_performance is in the workflow's suite list",
       "test_aeo_performance" in (ROOT / ".github/workflows/tests.yml").read_text())

print("\nALL OK" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
