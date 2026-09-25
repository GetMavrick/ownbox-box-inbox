"""AEO > Performance, the Search Console half: Top searches and Opportunities.

OWNER, 2026-09-25, in OSDev6's session: "I'm not interested in any Page speed stuff… I like the
referring websites and maybe something about keywords an opportunities would be better." OSDev1
approved the plan at 20:07: searchAnalytics() in core's Search Console module, the chosen property
URL-encoded (sc-domain: too), a window ending 3 days ago, cached like PostHog, a Connect link and
never zeros when Google is not connected, and one wire-level test.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  · the request is not the one Google's API takes (URL, property encoding, bearer, body);
  · the window ends today, so Google's late days read as a drop;
  · an opportunity outside position 4 to 20 is listed, or they are not most-seen first;
  · Google is asked again on every page open, or a failure is asked again at once;
  · Google not connected shows zeros instead of the next step, or a member is offered a door
    they are refused at;
  · a search is not escaped, or a screen uses a noun reserved for the receptionist machine.

NO NETWORK. The Search Console module's `net` is replaced with a stand-in that records requests.

Run: python tests/test_seo_searches.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import re
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "seo_searches.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state                                                   # noqa: E402

state.init_db()

from core import box_secrets, dash, net                                  # noqa: E402
from core.dispatch import app as web                                     # noqa: E402
from core.vendors import google_search_console as gsc                    # noqa: E402
from marketing.seo_machine import app as seo_app, searches               # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


class Net:
    """Google's searchAnalytics endpoint, scripted. Records every request."""
    PostRefused = net.PostRefused

    def __init__(self, status=200, rows=None, raises=False):
        self.status, self.rows, self.raises = status, rows or [], raises
        self.seen = []

    def post_public(self, url, *, json=None, headers=None, **_kw):
        self.seen.append((url, dict(headers or {}), json))
        if self.raises:
            raise net.PostRefused("no route")
        return self.status, __import__("json").dumps({"rows": self.rows})

    def get_public(self, url, **_kw):
        self.seen.append((url, {}, None))
        return 0, ""


def use(fake):
    gsc.net = fake
    searches.forget()
    return fake


TOKEN = "ya29." + "T" * 40


def signed_in(prop="sc-domain:northwind.example"):
    box_secrets.put(gsc.REFRESH, "1//refresh" + "R" * 30, user_id="t")
    if prop:
        box_secrets.put(gsc.PROPERTY, prop, user_id="t")
    else:
        box_secrets.clear(gsc.PROPERTY, user_id="t")
    gsc._cached.update(token=TOKEN, until=time.time() + 3600)


def row(q, clicks, impressions, position):
    return {"keys": [q], "clicks": clicks, "impressions": impressions,
            "ctr": clicks / max(impressions, 1), "position": position}


ROWS = [row("statement of work template", 40, 900, 2.1),
        row("what goes in a statement of work", 12, 2400, 6.4),
        row("sow vs contract", 3, 1300, 11.0),
        row("fixed fee consulting", 0, 5000, 3.9),         # just above the band: not an opportunity
        row("how to price a retainer", 0, 3000, 20.0),      # the band's far edge: in
        row("retainer agreement", 0, 9000, 20.1),          # just past it: out
        row("<b>bold</b> & co", 1, 50, 30.0),
        {"keys": [], "clicks": 5}, "not a row"]

print("test_the_window")
start, end = searches.window(dt.date(2026, 9, 25))
ok("28 days, ending 3 days ago (Google reports late)", (start, end) == ("2026-08-26", "2026-09-22"),
   (start, end))

print("\ntest_the_request_on_the_wire")
signed_in("sc-domain:northwind.example")
f = use(Net(rows=ROWS))
got = gsc.search_analytics("2026-08-26", "2026-09-22", ("query",), 250)
url, headers, body = f.seen[0]
ok("a domain property goes in whole and URL-encoded",
   url == "https://www.googleapis.com/webmasters/v3/sites/sc-domain%3Anorthwind.example"
          "/searchAnalytics/query", url)
ok("...with the box's Google token as a bearer", headers.get("Authorization") == f"Bearer {TOKEN}")
ok("...and the body Google's API takes",
   body == {"startDate": "2026-08-26", "endDate": "2026-09-22", "dimensions": ["query"],
            "rowLimit": 250}, body)
ok("only the rows that are objects come back", len(got) == len(ROWS) - 1, len(got))
signed_in("https://northwind.example/")
f = use(Net(rows=ROWS))
gsc.search_analytics("2026-08-26", "2026-09-22")
ok("a URL-prefix property is encoded whole too, slashes and all",
   f.seen[0][0] == "https://www.googleapis.com/webmasters/v3/sites/https%3A%2F%2Fnorthwind.example%2F"
                   "/searchAnalytics/query", f.seen[0][0])
for fake, key in ((Net(status=403), "google_said_no"), (Net(raises=True), "google_down")):
    use(fake)
    try:
        gsc.search_analytics("2026-08-26", "2026-09-22")
        refused = None
    except gsc.Refused as e:
        refused = e.key
    ok(f"Google refusing is Refused({key!r}), never an empty list", refused == key, refused)
signed_in(None)
f = use(Net(rows=ROWS))
try:
    gsc.search_analytics("2026-08-26", "2026-09-22")
    refused = None
except gsc.Refused as e:
    refused = e.key
ok("no site chosen: Refused('no_property'), and Google is not asked",
   refused == "no_property" and f.seen == [], refused)

print("\ntest_top_searches_and_opportunities")
signed_in()
f = use(Net(rows=ROWS))
s = searches.searches(today=dt.date(2026, 9, 25))
ok("the page's window is the lagged one", f.seen[0][2]["startDate"] == "2026-08-26"
   and f.seen[0][2]["endDate"] == "2026-09-22", f.seen[0][2])
ok("top searches: only ones clicked, most clicks first",
   [r["query"] for r in s["top"]] == ["statement of work template", "what goes in a statement of work",
                                      "sow vs contract", "<b>bold</b> & co"], s["top"])
ok("opportunities: position 4 to 20, both ends in, most seen first",
   [r["query"] for r in s["opportunities"]] == ["how to price a retainer",
                                                "what goes in a statement of work", "sow vs contract"],
   s["opportunities"])
n = len(f.seen)
searches.searches(today=dt.date(2026, 9, 25))
ok("a second open inside the cache window asks Google nothing", len(f.seen) == n)

f = use(Net(status=500))
searches.searches()
searches.searches()
ok("a failure is kept for a minute: Google down is asked once", len(f.seen) == 1, len(f.seen))
ok("...and says so in a sentence", searches.searches()["why"] == searches.UNREACHABLE)
for k, (t, v) in list(searches._cache.items()):
    searches._cache[k] = (t - searches.FAIL_CACHE_S - 1, v)
searches.searches()
ok("...and is asked again once the minute is up", len(f.seen) == 2, len(f.seen))

print("\ntest_the_screen")


def client(user_id):
    c = web.test_client()
    c.set_cookie(dash.COOKIE, dash.new_session(user_id))
    return c


def main_of(html: str) -> str:
    return html.split("</nav>", 1)[-1]


OWNER = state.owner_user()["id"]
MEMBER = state.add_user("sam@northwind-consulting.com", name="Sam")["id"]
owner, member = client(OWNER), client(MEMBER)

use(Net(rows=ROWS))
page = main_of(owner.get("/seo/performance").get_data(as_text=True))
ok("Performance shows Top searches and Opportunities", "Top searches" in page and "Opportunities" in page)
ok("...each search with its number", "40 clicks" in page and "position 6" in page)
ok("...a search is escaped, never markup", "&lt;b&gt;bold&lt;/b&gt; &amp; co" in page
   and "<b>bold</b> & co" not in page)
ok("...and the dates it covers, and that Google is late", "Aug 26 to Sep 22" in page
   or "to Sep 22" in page, re.findall(r"Searches from Google Search Console[^<]*", page))
ok("no page speed anywhere", "speed" not in page.lower())

box_secrets.clear(gsc.REFRESH, user_id="t")
f = use(Net(rows=ROWS))
page = main_of(owner.get("/seo/performance").get_data(as_text=True))
ok("Google not connected: the owner is offered Connect, never a row of zeros",
   "Connect Google Search Console" in page and f'href="{seo_app.GOOGLE}"' in page
   and "0 clicks" not in page and f.seen == [])
page = main_of(member.get("/seo/performance").get_data(as_text=True))
ok("...a member is told who can, with no door they are refused at",
   f'href="{seo_app.GOOGLE}"' not in page and "The owner of this box can connect it" in page)
signed_in(None)
page = main_of(owner.get("/seo/performance").get_data(as_text=True))
ok("connected with no site chosen: 'Choose your site'", "Choose your site" in page)

page = main_of(owner.get("/seo/sources").get_data(as_text=True))
ok("Data sources lists Google Analytics as Coming soon, with no door",
   "Google Analytics" in page and "Coming soon." in page
   and not re.search(r"Google Analytics[^<]*</h2>(?:(?!</div>).)*<a ", page, re.S))

print("\ntest_the_words_a_buyer_reads")
_RESERVED = re.compile(r"\b(phones?|rings?|calls?|dial|lines?|voice)\b", re.I)
signed_in()
use(Net(rows=ROWS))
for who in (owner, member):
    for path in ("/seo/performance", "/seo/sources"):
        raw = who.get(path).get_data(as_text=True)
        text = re.sub(r"<[^>]+>", " ", re.sub(r"<(style|script)\b.*?</\1>", " ", main_of(raw), flags=re.S))
        ok(f"{path} uses none of the reserved nouns", not _RESERVED.search(text),
           str(_RESERVED.findall(text)))
for v in (searches.SIGNED_OUT, searches.UNREACHABLE):
    ok(f"searches' sentence has no reserved noun: {v[:40]}", not _RESERVED.search(v))

print("\n— and this file cannot silently fall out of CI —")
if (ROOT / ".github").is_dir():                  # a buyer's box has no repository
    ok("test_seo_searches is in the workflow's suite list",
       "test_seo_searches" in (ROOT / ".github/workflows/tests.yml").read_text())

print("\nALL OK" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
