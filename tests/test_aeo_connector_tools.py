"""The AEO Machine is readable through the connector: every screen's answer, to the owner's own AI.

OWNER, 2026-10-02, looking at his connector's eight tools, none of them this machine's: "If it
doesn't expose enough for a user to actually be in full command of that machine, then we need to
upgrade the MCP immediately." Step one of #1793, which he approved ("Agree. go"), is these reads.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  · a tool is missing, or hidden from the owner's `read` seat (read:aeo not granted is the way that
    happened to read:inbox in #1205);
  · a credential (the Sanity token, the Airtable or PostHog key, the Google sign-in) appears in any
    answer;
  · an unconnected PostHog or Search Console answers with zeros instead of the typed not-configured
    state;
  · status says ready while something is missing, or hides that the AI isn't signed in;
  · an article that stopped is shown without its reason and what to do;
  · any tool here writes, sends, publishes or spends (they are all `read:` and touch no network);
  · a title is not plain words a person reads on their AI's permission screen.

Run: python tests/test_aeo_connector_tools.py
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
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "aeo_tools.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"

from core import state                                                   # noqa: E402

state.init_db()

from core import box_secrets, box_settings, box_tools, net                # noqa: E402
from core.connector import tools as registry                              # noqa: E402
from core.vendors import google_search_console as gsc                     # noqa: E402
from marketing.aeo_machine import plan, posthog, searches, sources, tools  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


READ = {"id": "seat_owner", "role": "read", "label": "the owner's own assistant"}
NAMES = ("aeo.status", "aeo.articles", "aeo.article", "aeo.performance", "aeo.searches",
         "aeo.settings", "aeo.sources")


def call(name, args=None, seat=READ):
    return registry.call(name, args or {}, seat)


# Every network door shut: a tool that reached for a vendor would raise here.
class NoNet:
    PostRefused = net.PostRefused

    def __getattr__(self, name):
        raise AssertionError(f"a read tool touched the network ({name})")


posthog.net = NoNet()
gsc.net = NoNet()
box_tools.health = lambda: {"brain": {"state": "no_ai_key", "ok": None,
                                      "note": "no AI key is set, so nothing on this box can draft yet"}}

print("test_registered_and_visible")
reg = registry.registry()
ok("all seven tools register", all(n in reg for n in NAMES), sorted(n for n in reg if n.startswith("aeo.")))
seen = {s["name"] for s in registry.visible_to(READ)}
ok("the owner's read seat sees every one of them (read:aeo is granted)", set(NAMES) <= seen,
   sorted(set(NAMES) - seen))
ok("so do act and service seats", all(set(NAMES) <= {s["name"] for s in registry.visible_to(
    {"id": f"s_{r}", "role": r})} for r in ("act", "service")))
ok("every one is a read, so none can write, send, publish or spend",
   all(reg[n]["capability"] == "read:aeo" and reg[n]["min_role"] == "read" for n in NAMES))
ok("every title is plain words a person reads", all(registry.plain_title(reg[n]["title"]) for n in NAMES),
   [reg[n]["title"] for n in NAMES])

print("\ntest_status_on_a_fresh_box")
out, code = call("aeo.status")
ok("answers", code == 200, (code, out))
res = out.get("result", out)
ok("not ready, and the AI account is named first among what's missing",
   res["ready"] is False and res["missing"][0].startswith("a signed-in AI account")
   and "your website's address" in res["missing"] and "a Sanity connection" in res["missing"], res)
ok("...with the AI state as the box reports it", res["ai"]["state"] == "no_ai_key")
ok("the weekly number and what is left of it", res["articles_a_week"] == 4 and res["left_this_week"] == 4)

print("\ntest_articles_and_why_they_stopped")
a = plan.add("Scope", "What should a statement of work include?")
plan.mark(a, "published", slug="sow", url="https://northwind.example/articles/sow",
          title="What a statement of work should include")
b = plan.add("Rivals", "Is Acme better than us?")
plan.mark(b, "refused", refusal='competitor: "Acme" (a rival is never named)')
c = plan.add("Tools", "Which tools do we use?")
plan.mark(c, "failed", refusal="no AI account is signed in")
plan.add("Retainers", "How do retainers work?")
res = call("aeo.articles")[0]["result"]
by = {x["id"]: x for x in res["articles"]}
ok("every topic is listed", res["total"] == 4 and len(by) == 4, res)
ok("a published one has its title and live address",
   by[a]["state"] == "live" and by[a]["url"].endswith("/articles/sow") and by[a]["title"])
ok("nothing unpublished carries an address, so an assistant never calls a draft live",
   all(x["url"] is None for x in by.values() if x["status"] != "published"), by)
ok("a held one says why and what to do", by[b]["state"] == "held back" and "Acme" in by[b]["why"]
   and "Reword" in by[b]["what_to_do"])
ok("a failed one says why and what to do", by[c]["state"] == "did not publish"
   and "AI account" in by[c]["why"] and "Try again" in by[c]["what_to_do"])
ok("a filter by status works", [x["id"] for x in call("aeo.articles", {"status": "failed"})[0]["result"]
                                ["articles"]] == [c])
ok("a wrong status is a plain error, not every row",
   "error" in call("aeo.articles", {"status": "everything"})[0]["result"])
one = call("aeo.article", {"id": b})[0]["result"]
ok("one article in full", one["id"] == b and one["question"] == "Is Acme better than us?")
ok("an unknown id is a plain error", "error" in call("aeo.article", {"id": 999})[0]["result"])
st = call("aeo.status")[0]["result"]
ok("status counts the two that wait on the owner", st["waiting_on_you"] == 2, st)

print("\ntest_not_connected_is_a_typed_state_never_zeros")
for name in ("aeo.performance", "aeo.searches"):
    body, code = call(name)
    ok(f"{name}: not configured, with how to connect it", code == 200 and body.get("not_configured")
       and "connect" in body.get("reason", "").lower(), body)

print("\ntest_connected_numbers")
box_settings.put("seo", "posthog_host", "https://us.posthog.com")
box_settings.put("seo", "posthog_project", "4242")
box_secrets.put(posthog.KEY, "phx_" + "S" * 40, user_id="t")
posthog.performance = lambda **_k: {
    "ok": True, "empty": False, "site": "northwind.example", "visitors": 40, "visitors_change": -20,
    "views": 120, "views_change": 20, "article_views": 60, "article_views_change": None,
    "ai_visits": 9, "ai_visits_change": 200, "top_articles": [("/articles/sow", 31)],
    "top_sources": [("chatgpt.com", 9)]}
perf = call("aeo.performance")[0]["result"]
ok("performance: the week's numbers and changes", perf["visitors"] == 40 and perf["ai_visits"] == 9
   and perf["article_views_change"] is None, perf)
ok("...the most read articles and the sites that sent visitors",
   perf["top_articles"] == [{"path": "/articles/sow", "views": 31}]
   and perf["top_sources"] == [{"site": "chatgpt.com", "views": 9}])
searches.searches = lambda **_k: {"ok": True, "start": "2026-09-01", "end": "2026-09-28",
                                  "top": [{"query": "sow template", "clicks": 3, "impressions": 90,
                                           "position": 2.0}], "opportunities": []}
sr = call("aeo.searches")[0]["result"]
ok("searches: top searches with their dates", sr["from"] == "2026-09-01" and sr["top_searches"][0]
   ["query"] == "sow template", sr)

print("\ntest_no_credential_ever_leaves")
SECRETS = {"sanity": "sk" + "Q" * 60, "airtable": "pat" + "Z" * 60, "posthog": "phx_" + "S" * 40,
           "google": "1//" + "G" * 40}
box_settings.put("seo", "project_id", "abc123xy")
box_secrets.put(sources.SANITY_TOKEN, SECRETS["sanity"], user_id="t")
box_settings.put("seo", "airtable_base", "app" + "A" * 14)
box_settings.put("seo", "airtable_table", "tbl" + "B" * 14)
box_secrets.put(sources.AIRTABLE_KEY, SECRETS["airtable"], user_id="t")
box_secrets.put(gsc.REFRESH, SECRETS["google"], user_id="t")
box_secrets.put(gsc.PROPERTY, "sc-domain:northwind.example", user_id="t")
srcs = call("aeo.sources")[0]["result"]
ok("sources: each connected or not, with its table and site",
   srcs["sanity"]["connected"] and srcs["airtable"]["connected"] and srcs["posthog"]["connected"]
   and srcs["google_search_console"]["site"] == "sc-domain:northwind.example", srcs)
everything = json.dumps([call(n, {"id": a} if n == "aeo.article" else {})[0] for n in NAMES])
ok("no credential appears in any answer", not any(v in everything for v in SECRETS.values()),
   [k for k, v in SECRETS.items() if v in everything])

print("\ntest_settings")
box_settings.put("seo", "site_url", "https://northwind.example")
box_settings.put("seo", "never_words", ["cheap"])
box_settings.put("seo", "facts", ["Founded in 2019."])
cfg = call("aeo.settings")[0]["result"]
ok("settings: the website, the weekly number and the buyer's own lists",
   cfg["site_url"] == "https://northwind.example" and cfg["never_words"] == ["cheap"]
   and cfg["facts"] == ["Founded in 2019."] and cfg["weekly_cap"] == 4, cfg)

print("\ntest_every_call_is_audited")
with state.connect() as c2:
    n = c2.execute("SELECT COUNT(*) FROM seat_actions WHERE seat_id = 'seat_owner'").fetchone()[0]
ok("every call wrote its audit row", n >= 15, n)

print("\n— and this file cannot silently fall out of CI —")
if (ROOT / ".github").is_dir():                  # a buyer's box has no repository
    ok("test_aeo_connector_tools is in the workflow's suite list",
       "test_aeo_connector_tools" in (ROOT / ".github/workflows/tests.yml").read_text())

print("\nALL OK" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
