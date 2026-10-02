"""Data Sources: what is connected, then ideas for what to connect (owner, 2026-10-02, approved from a preview).

His words: "We want a clean Page for customers to arrive at which gives them lots of ideas for things to connect. I
would say Google search console would be another top choice, notion would be one, Post hog would be another top
choice", "a table format where it's like name of the app and then a sentence on what you can do with it and what
business use case and outcome", and before that "We need to suggest things, but not assume everybody is going to want
to connect post hog and instantly and things like that."

Held here:
  1. a new box shows no connected table, and the ideas in the approved order, his three picks first;
  2. each MCP idea is the vendor's own hosted server, an https address on the vendor's own domain;
  3. Connect fills the form from the list, never from what is typed in the address bar;
  4. a connected app or source is a row, and leaves the ideas;
  5. a department's own source not yet set up is a built-in idea, not a "not connected" row;
  6. Instantly is in neither, and no idea uses a word the box keeps for the receptionist.
"""
from __future__ import annotations

import html
import os
import re
import sys
import tempfile
from urllib.parse import urlsplit

_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "ideas.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import state  # noqa: E402

state.init_db()
from core import dash, source_cards  # noqa: E402
from core.connections import ideas, store  # noqa: E402
from core.dispatch import app  # noqa: E402
from core.vendors import google_search_console as gsc  # noqa: E402

_failed = 0


def ok(label: str, cond: bool, detail: str = "") -> None:
    global _failed
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  — {str(detail)[:600]}"))
    if not cond:
        _failed += 1


owner = app.test_client()
owner.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))


def page(q: str = "") -> str:
    return owner.get("/settings/sources" + q).get_data(as_text=True)


def idea_names(p: str) -> list[str]:
    t = p[p.find('<table class="ideas">'):p.find("</table>", p.find('<table class="ideas">'))]
    return [html.unescape(n) for n in re.findall(r'<td class="app"><b>([^<]*)', t)]


def text(h: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<style>.*?</style>", " ", h, flags=re.S)))


print("\ntest_a_new_box_arrives_at_ideas")
# A BASE BOX: no department's own source registered (a box with the marketing tools adds "Website analytics" as a
# built-in idea after Search Console; test_a_departments_source_is_an_idea_until_set_up holds that).
_departments = dict(source_cards._CARDS)
source_cards._CARDS.clear()
p = page()
ok("NOTHING CONNECTED IS NO CONNECTED TABLE, never a list of things not connected", "<h2>Connected</h2>" not in p
   and "Not connected" not in p)
names = idea_names(p)
ok("ideas, his three picks first: Search Console, Notion, PostHog",
   names[:3] == ["Google Search Console", "Notion", "PostHog"], names)
ok("...then the rest of the approved list, in its order",
   names[3:] == ["Stripe", "Calendly", "monday.com", "Airtable", "Klaviyo", "Pipedrive", "ClickUp", "PayPal", "Wix",
                 "Fireflies"], names)
ok("HUBSPOT IS NOT AN IDEA: its server needs the box registered by hand (OSDev1's review, 2026-10-02)",
   "HubSpot" not in names)
ok("each with a sentence of what it does for the business", all(len(i[4]) > 40 for i in ideas.MCP_IDEAS)
   and "so you know who to chase" in p)
ok("Search Console is the box's own, set up on its own screen", re.search(
    r'Google Search Console<span class=tag>Built in</span>.*?href="/settings/aeo/google">Set up<', p, re.S) is not None)
ok("then the form to connect any other app", p.index("Ideas for what to connect") < p.index("Connect any app"))
bad = re.compile(r"\b(phone|phones|ring|call|calls|dial|line|lines|voice|engine)\b", re.I)
ok("no word the box keeps for the receptionist", not bad.search(text(p)), bad.findall(text(p)))
ok("INSTANTLY IS NOT AN IDEA", "Instantly" not in p and "instantly" not in p.lower().split("</nav>", 1)[-1])

print("\ntest_every_idea_is_the_vendors_own_server")
for slug, name, cat, url, what, doc in ideas.MCP_IDEAS:
    host, dhost = urlsplit(url).hostname or "", urlsplit(doc).hostname or ""
    ok(f"{name}: https, on its vendor's own domain, checked at its vendor's own page",
       url.startswith("https://") and host.split(".")[-2] == dhost.split(".")[-2] and doc.startswith("https://"),
       (url, doc))

print("\ntest_connect_fills_the_form")
p = page("?idea=notion")
ok("Connect on an idea fills in its name and its vendor's MCP address",
   'value="Notion"' in p and 'value="https://mcp.notion.com/mcp"' in p)
p = page("?idea=%22%3E%3Cscript%3Ealert(1)%3C/script%3E")
ok("...and nothing typed in the address bar reaches the form", "<script>alert" not in p
   and 'id="c-url"' in p and re.search(r'id="c-url"[^>]*value=""', p) is not None)

print("\ntest_connected_is_a_row_and_leaves_the_ideas")
store._save({"items": {"notion": {"name": "Notion", "host": "mcp.notion.com", "url": "https://mcp.notion.com/mcp",
                                  "tools": [], "enabled": [], "added_at": "2026-10-02T19:00:00+00:00"}}}, "t")
p = page()
ok("A CONNECTED APP IS A ROW in the Connected table", "<h2>Connected</h2>" in p and 'id="app-notion"' in p)
ok("...and leaves the ideas", "Notion" not in idea_names(p), idea_names(p))
real = gsc.status
gsc.status = lambda: {"connected": True, "account": "owner@acme.example", "property": "sc-domain:acme.example"}
p = page()
ok("Search Console, once connected, is a row naming its site, and leaves the ideas",
   'href="/settings/aeo/google"><span class="src-name">Google Search Console' in p and "acme.example" in p
   and "Google Search Console" not in idea_names(p))
gsc.status = real

print("\ntest_a_departments_source_is_an_idea_until_set_up")
source_cards._CARDS.update(_departments)
# A Lead box ships without marketing/foundation (scripts/export_box.sh), and test_recipe_ships runs this suite there.
if os.path.isdir(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "marketing", "foundation")):
    ok("a box with the marketing tools suggests its Website analytics too, built in, right after Search Console",
       idea_names(page())[:2] == ["Google Search Console", "Website analytics"], idea_names(page())[:3])
else:
    ok("a box without the marketing tools suggests no Website analytics", "Website analytics" not in idea_names(page()),
       idea_names(page())[:3])
source_cards._CARDS.clear()
SAID = {"connected": False}
source_cards.register("tidy_ideas", title="Bakery orders", order=5, category="From your till",
                      idea="Yesterday's orders by shop, in your Morning Review.",
                      render=lambda note: '<div class="card"><h2>Bakery orders</h2></div>',
                      handle=lambda do, form, by: (True, ""),
                      summary=lambda: {"what": "3 shops", "status": "Connected" if SAID["connected"] else "",
                                       "connected": SAID["connected"]})
p = page()
ok("NOT SET UP, A DEPARTMENT'S SOURCE IS A BUILT-IN IDEA, after Search Console and before the apps",
   idea_names(p)[:2] == ["Google Search Console", "Bakery orders"]
   and 'href="/settings/sources/tidy_ideas">Set up<' in p and 'class="src-link" href="/settings/sources/tidy_ideas"'
   not in p, idea_names(p))
SAID["connected"] = True
p = page()
ok("...SET UP, it is a row and leaves the ideas", 'class="src-link" href="/settings/sources/tidy_ideas"' in p
   and "Bakery orders" not in idea_names(p))
source_cards._CARDS["tidy_ideas"]["summary"] = lambda: 1 / 0
p = page()
ok("A SOURCE WHOSE SUMMARY CAN'T BE READ STAYS A ROW, saying so, never dropped into the ideas",
   'class="src-link" href="/settings/sources/tidy_ideas"' in p and "Could not be read just now" in p
   and "Bakery orders" not in idea_names(p))

print()
print("FAILED" if _failed else "ALL PASSED", _failed)
sys.exit(1 if _failed else 0)
