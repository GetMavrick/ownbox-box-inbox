"""One AEO site per box, and migration 58 (#1793 F5 items 1.2 and 1.4; OSDev1, 2026-10-04).

THE GAP: the AEO Machine writes for the website on AEO Settings, and reads searches from the box's one Search Console
property, and nothing compared the two. A box writing for ownbox.io with Search Console on brian-macdonald.com read
brian-macdonald.com's searches as its own, offered articles from them and filed them as its customers' questions.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * a website or property is read as the wrong host (www., a path, a port, sc-domain:, upper case);
  * a domain property stops covering its subdomains, or a URL-prefix property covers another host;
  * Google is asked for another site's searches, or the last good answer (the other site's) is shown;
  * the sentence stops naming both sites and both fixes, on aeo.status, aeo.sources, aeo.searches, Performance or
    Data Sources; a member is offered the owner's door;
  * the common questions take another site's searches;
  * with no website saved or no property chosen, the box claims a mismatch;
  * migration 58 is missing a column, or one Airtable row can become two plan rows, or a new row has no site.

NO NETWORK: the Search Console module's `net` is a stand-in that records requests.

Run: python tests/test_aeo_one_site.py
"""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "aeo_one_site.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state                                                   # noqa: E402

state.init_db()

from core import box_secrets, box_settings, dash, net                    # noqa: E402
from core.dispatch import app as web                                     # noqa: E402
from core.vendors import google_search_console as gsc                    # noqa: E402
from marketing.aeo_machine import plan, questions, searches, site        # noqa: E402
from marketing.aeo_machine import tools as aeo_tools                     # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -- {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


class Net:
    """Google's searchAnalytics endpoint, scripted. Records every request."""
    PostRefused = net.PostRefused

    def __init__(self, rows=None):
        self.rows, self.seen = rows or [], []

    def post_public(self, url, *, json=None, headers=None, **_kw):
        self.seen.append(url)
        return 200, __import__("json").dumps({"rows": self.rows})

    def get_public(self, url, **_kw):
        self.seen.append(url)
        return 0, ""


def use(fake):
    gsc.net = fake
    searches.forget()
    return fake


def signed_in(prop):
    box_secrets.put(gsc.REFRESH, "1//refresh" + "R" * 30, user_id="t")
    box_secrets.put(gsc.PROPERTY, prop, user_id="t")
    gsc._cached.update(token="ya29." + "T" * 40, until=time.time() + 3600)


def writes_for(url):
    box_settings.put("seo", "site_url", url)


ROWS = [{"keys": ["how much is botox"], "clicks": 30, "impressions": 900, "position": 2.0},
        {"keys": ["what does a med spa do"], "clicks": 0, "impressions": 2400, "position": 6.0}]
WRONG = ("Search Console is connected to brian-macdonald.com, but this machine writes for ownbox.io. "
         "Connect the ownbox.io property, or change the website on AEO Settings.")

print("test_one_bare_host")
for given, want in (("https://www.Ownbox.io/articles/x", "ownbox.io"), ("ownbox.io", "ownbox.io"),
                    ("sc-domain:ownbox.io", "ownbox.io"), ("https://ownbox.io:8443/", "ownbox.io"),
                    ("http://blog.ownbox.io", "blog.ownbox.io"), ("WWW.OWNBOX.IO.", "ownbox.io"),
                    ("", ""), (None, ""), ("localhost", ""), ("https://", "")):
    ok(f"{given!r} is {want!r}", site.bare(given) == want, site.bare(given))

print("\ntest_what_a_property_covers")
ok("a domain property covers the domain", site.covers("sc-domain:ownbox.io", "ownbox.io"))
ok("...and every subdomain", site.covers("sc-domain:ownbox.io", "blog.ownbox.io"))
ok("...but never a domain that only ends the same way", not site.covers("sc-domain:ownbox.io", "myownbox.io"))
ok("a URL-prefix property covers its host, www. or not", site.covers("https://www.ownbox.io/", "ownbox.io")
   and site.covers("https://ownbox.io/", "www.ownbox.io"))
ok("...but not a subdomain, which Google reports apart", not site.covers("https://ownbox.io/", "blog.ownbox.io"))
ok("another website is never covered", not site.covers("sc-domain:brian-macdonald.com", "ownbox.io"))

print("\ntest_unknown_is_not_wrong")
ok("no website saved: no sentence", site.site() == "" and site.mismatch("sc-domain:brian-macdonald.com") == "")
writes_for("https://www.ownbox.io/")
ok("no property chosen: no sentence", site.mismatch("") == "" and site.mismatch(None) == "")
ok("the search-engine host stands in when the website is empty",
   (box_settings.put("seo", "site_url", "") or box_settings.put("seo", "host", "ownbox.io") or True)
   and site.site() == "ownbox.io")
box_settings.put("seo", "host", "")
writes_for("https://www.ownbox.io/")

print("\ntest_another_sites_searches_are_never_read")
signed_in("sc-domain:ownbox.io")
f = use(Net(rows=ROWS))
good = searches.searches()
ok("the property for this site is read", good.get("ok") is True and len(f.seen) == 1, good)
signed_in("sc-domain:brian-macdonald.com")
f = use(Net(rows=ROWS))
got = searches.searches(fresh=True)
ok("a property for another website: Google is not asked", f.seen == [], f.seen)
ok("...and the answer is the sentence, naming both sites and both fixes",
   got.get("ok") is False and got.get("wrong_site") is True and got.get("why") == WRONG, got)
signed_in("sc-domain:ownbox.io")
use(Net(rows=ROWS))
searches.searches()                                     # a good answer, kept as the last good one
writes_for("https://brian-macdonald.com")               # now the machine writes for the other site
got = searches.searches(fresh=True)
ok("a last good answer for another site is never shown as this one's",
   got.get("ok") is False and not got.get("stale") and "writes for brian-macdonald.com" in got.get("why", ""), got)
writes_for("https://www.ownbox.io/")

print("\ntest_every_surface_says_it")
signed_in("sc-domain:brian-macdonald.com")
use(Net(rows=ROWS))
st = aeo_tools.status()
ok("aeo.status carries the sentence", st.get("search_console") == WRONG, st.get("search_console"))
ok("...and says it in words, with the page that fixes it",
   WRONG in aeo_tools._render_status(st) and "/aeo/sources" in aeo_tools._render_status(st))
src = aeo_tools.sources()["google_search_console"]
ok("aeo.sources names the site the machine writes for, and the problem",
   src.get("writes_for") == "ownbox.io" and src.get("problem") == WRONG, src)
ok("...in words, under the Search Console line", WRONG in aeo_tools._render_sources(aeo_tools.sources()))
sr = aeo_tools.searches()
ok("aeo.searches says what to fix, never that Google failed",
   sr.__class__.__name__ == "NotConfigured" and WRONG in sr.reason and "Google Search Console" in sr.reason,
   getattr(sr, "reason", sr))
qs, why = questions.from_searches()
ok("the common questions take nothing from another site", qs == {} and why == WRONG, (qs, why))


def client(user_id):
    c = web.test_client()
    c.set_cookie(dash.COOKIE, dash.new_session(user_id))
    return c


OWNER = state.owner_user()["id"]
MEMBER = state.add_user("sam@glow-medspa.example", name="Sam")["id"]
owner, member = client(OWNER), client(MEMBER)
page = owner.get("/aeo/performance").get_data(as_text=True)
ok("Performance says Search Console is reading another website, with the sentence and the owner's door",
   "Search Console is reading another website" in page and "brian-macdonald.com" in page
   and "Choose your site" in page, page[-2000:])
page = member.get("/aeo/performance").get_data(as_text=True)
ok("...and a member is told who can change it, with no door", "The owner of this box can change it." in page
   and "/settings/aeo/google" not in page.split("</nav>", 1)[-1])
page = owner.get("/aeo/sources").get_data(as_text=True)
ok("Data Sources says it on the Search Console card", "but this machine writes for ownbox.io" in page)
signed_in("sc-domain:ownbox.io")
use(Net(rows=ROWS))
st = aeo_tools.status()
ok("with the right property, nothing is said anywhere", st.get("search_console") == ""
   and aeo_tools.sources()["google_search_console"]["problem"] is None
   and "writes for" not in owner.get("/aeo/sources").get_data(as_text=True))

print("\ntest_migration_58")
with state.connect() as c:
    cols = {r[1] for r in c.execute("PRAGMA table_info(seo_plan)")}
    idx = c.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name='seo_plan_airtable_id'").fetchone()
ok("seo_plan has site, airtable_id and airtable_pushed", {"site", "airtable_id", "airtable_pushed"} <= cols, cols)
ok("...and the record id is unique where it is set", idx is not None and "UNIQUE" in idx[0].upper(), idx)
ok("the box is at 58 or later, and 58 belongs to the AEO Machine",
   state.SCHEMA_VERSION >= 58 and state._MIGRATION_OWNER.get(58) == "aeo_machine")
a = plan.add("Botox aftercare", "What should I avoid after Botox?")
b = plan.add("Filler or Botox")
ok("a new row carries the site it was planned for", plan.get(a)["site"] == "ownbox.io", plan.get(a))
with state.connect() as c:
    c.execute("UPDATE seo_plan SET airtable_id = ? WHERE id = ?", ("rec123", a))
    try:
        c.execute("UPDATE seo_plan SET airtable_id = ? WHERE id = ?", ("rec123", b))
        twice = True
    except sqlite3.IntegrityError:
        twice = False
ok("one Airtable row can never become two plan rows", twice is False)
with state.connect() as c:
    c.execute("UPDATE seo_plan SET airtable_id = '' WHERE id IN (?, ?)", (a, b))
ok("...while rows with no record id are never compared", plan.get(a)["airtable_id"] == plan.get(b)["airtable_id"] == "")
box_settings.put("seo", "site_url", "")
c3 = plan.add("Membership pricing")
ok("with no website saved, a row carries none, never a guess", plan.get(c3)["site"] is None, plan.get(c3))

print("\nALL ONE-SITE CHECKS PASS" if not _failed else f"\n{_failed} ONE-SITE CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
