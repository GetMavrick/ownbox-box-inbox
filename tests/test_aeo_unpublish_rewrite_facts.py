"""AEO: nothing publishes without facts, and a person can unpublish or rewrite an article with one tap.

OWNER, 2026-10-04. Two articles went live on his box with an EMPTY fact list, and one told readers his
own Unified Inbox's capabilities were "not established". His answers, relayed by OSDev1 and confirmed
in OSDev6's session ("yes, go"): unpublish it, never delete it; rewrite both from facts; and, from the
day before, "nothing publishes until facts are set".

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  · the job writes or publishes while the fact list is empty, or status says "ready" over an empty list;
  · unpublishing deletes the article, or leaves a moment where it is neither live nor kept (the draft copy
    and the removal must be ONE request, one Sanity transaction);
  · anything is unpublished or rewritten before a person approves it, or a read-only seat can ask;
  · a rewrite lands at a new address, resets the date it first went live, or waits behind the week's
    number (it replaces an article, it doesn't add one);
  · a rewrite is offered with no facts to write from;
  · an unpublished article is shown as "held back", as live, or as waiting on the owner.

NO NETWORK, NO TOKENS: Sanity is a scripted stand-in and the writer is a stub.

Run: python tests/test_aeo_unpublish_rewrite_facts.py
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
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "aeo_unpub.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state                                                   # noqa: E402

state.init_db()

from core import approvals, box_secrets, box_settings, dash, net         # noqa: E402
from core.connector import tools as registry                             # noqa: E402
from core.dispatch import app as web                                     # noqa: E402
from marketing.aeo_machine import job, plan, publisher, sources, tools   # noqa: E402,F401
from marketing.aeo_machine import app as aeo_app                         # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


class Sanity:
    """Answers like Sanity's HTTP API, by URL fragment. Records every request."""
    PostRefused = net.PostRefused

    def __init__(self, live=None):
        self.live, self.seen = dict(live or {}), []          # slug -> full document

    def get_public(self, url, *, headers=None, **_kw):
        self.seen.append(("GET", url, None))
        from urllib.parse import parse_qs, urlsplit
        q = parse_qs(urlsplit(url).query)
        if "$slug" in q:
            doc = self.live.get(json.loads(q["$slug"][0]))
            return 200, json.dumps({"result": {"_id": doc["_id"]} if doc else None})
        if "$id" in q:
            want = json.loads(q["$id"][0])
            doc = next((d for d in self.live.values() if d["_id"] == want), None)
            return 200, json.dumps({"result": doc})
        return 404, ""

    def post_public(self, url, *, headers=None, json=None, **_kw):
        self.seen.append(("POST", url, json))
        for m in (json or {}).get("mutations", []):
            if "delete" in m:
                self.live = {s: d for s, d in self.live.items() if d["_id"] != m["delete"]["id"]}
        return 200, '{"results":[]}'


def setup_box(facts=True):
    box_settings.put("seo", "project_id", "abc123xy")
    box_settings.put("seo", "site_url", "https://northwind.example")
    box_secrets.put(sources.SANITY_TOKEN, "sk" + "Q" * 60, user_id="t")
    if facts:
        box_settings.put("seo", "facts", ["Northwind has helped 40 businesses since 2019."])
    else:
        box_settings.clear("seo", "facts")


def published(topic, slug, when="2026-09-20T10:00:00+00:00"):
    rid = plan.add(topic, f"{topic}?")
    with state.connect() as c:
        c.execute("UPDATE seo_plan SET status='published', slug=?, url=?, title=?, published_at=? WHERE id=?",
                  (slug, f"https://northwind.example/articles/{slug}", topic.title(), when, rid))
    return rid


ACT = {"id": "seat_own", "role": "act", "label": "the owner's Claude"}
READ = {"id": "seat_ro", "role": "read"}


def ask(name, args, seat=ACT):
    body, code = registry.call(name, args, seat)
    return body.get("result", body), code


def mine():
    return [a for a in approvals.waiting() if a["machine"] == "aeo"]


print("test_nothing_publishes_without_facts")
setup_box(facts=False)
planned = plan.add("Pricing", "How much does it cost?")
wrote = []
job.writer.write = lambda *a, **k: wrote.append(a) or {"title": "x", "slug": "x"}
out = job.periodic()
ok("the job skips and says why", out.get("skipped") == "no_facts" and "AEO Settings" in out.get("why", ""), out)
ok("...nothing was written, the row is still planned", not wrote and plan.get(planned)["status"] == "planned")
ok("the screens name it", aeo_app.FACTS_MISSING in aeo_app.missing())
st = ask("aeo.status", {}, READ)[0]
ok("status is not ready, and names the facts with the page that fixes them",
   st["ready"] is False and any(m.startswith("facts the writer may state") and "/aeo/settings" in m
                                for m in st["missing"]), st["missing"])
setup_box(facts=True)
ok("with facts, the machine no longer names them", aeo_app.FACTS_MISSING not in aeo_app.missing())
plan.mark(planned, "refused", refusal="parked for this test")

print("\ntest_unpublish_keeps_a_draft_in_one_transaction")
DOC = {"_id": "art1", "_type": "article", "_rev": "r1", "_createdAt": "x", "_updatedAt": "y",
       "title": "Best inbox apps", "slug": {"current": "best-inbox-apps"}, "body": [{"_type": "block"}]}
s = Sanity(live={"best-inbox-apps": DOC})
publisher.net = s
got = publisher.unpublish("best-inbox-apps")
posts = [x for x in s.seen if x[0] == "POST"]
ok("it reports the article unpublished", got == {"doc_id": "art1", "unpublished": True}, got)
ok("ONE request carries both: the draft copy and the removal", len(posts) == 1
   and [list(m)[0] for m in posts[0][2]["mutations"]] == ["createOrReplace", "delete"], posts)
draft = posts[0][2]["mutations"][0]["createOrReplace"]
ok("the draft is the whole article, under drafts.<id>", draft["_id"] == "drafts.art1"
   and draft["title"] == "Best inbox apps" and draft["body"] == [{"_type": "block"}]
   and "_rev" not in draft, draft)
ok("only the published copy is removed, never the draft",
   posts[0][2]["mutations"][1] == {"delete": {"id": "art1"}})
s2 = Sanity()
publisher.net = s2
ok("nothing live at that address is an answer, not an error, and sends no write",
   publisher.unpublish("gone") == {"doc_id": None, "unpublished": False}
   and not [x for x in s2.seen if x[0] == "POST"])

print("\ntest_unpublish_waits_for_a_tap")
art = published("best inbox apps", "best-inbox-apps")
publisher.net = Sanity(live={"best-inbox-apps": DOC})
ok("a read-only seat can't ask", "aeo.propose_unpublish" not in {t["name"] for t in registry.visible_to(READ)})
res, code = ask("aeo.propose_unpublish", {"id": art})
ok("the owner's AI asks; the article is still live", code == 200 and res["asked"]
   and plan.get(art)["status"] == "published" and publisher.net.live, (code, res))
ok("the owner reads what will happen", mine()[0]["detail"]["arguments"]["what happens"]
   == "Off your website. Kept as a draft in Sanity.")
out = approvals.decide(res["approval"], True, by="owner")
row = plan.get(art)
ok("Approve unpublishes it, once", out["ok"] and not publisher.net.live and plan.is_unpublished(row), out)
ok("...and the row keeps its address, for a rewrite", row["slug"] == "best-inbox-apps" and row["url"])
a = ask("aeo.article", {"id": art}, READ)[0]
ok("the AI reads it as unpublished, not held back, and not live",
   a["state"] == "unpublished" and a["url"] is None and "rewrite" in a["what_to_do"], a)
ok("it isn't counted as waiting on the owner", ask("aeo.status", {}, READ)[0]["waiting_on_you"] == 1)
ok("a second ask is refused: it isn't live any more", "error" in ask("aeo.propose_unpublish", {"id": art})[0])
c = web.test_client()
c.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
page = c.get("/aeo/topics").get_data(as_text=True)
ok("the Articles screen says Unpublished, and offers the rewrite", "<b>Unpublished.</b>" in page
   and "Rewrite from your facts" in page)

print("\ntest_rewrite_from_facts")
live = published("leads from chatgpt", "leads-from-chatgpt", when="2026-10-04T02:46:17+00:00")
from datetime import datetime, timezone                  # noqa: E402
published("another one this week", "another-one", when=datetime.now(timezone.utc).isoformat())
box_settings.put("seo", "weekly_cap", 1)                 # the week is already full
res, _ = ask("aeo.propose_rewrite", {"id": live})
ok("the owner's AI asks; nothing changes yet", res["asked"] and plan.get(live)["status"] == "published")
approvals.decide(res["approval"], True, by="owner")
row = plan.get(live)
ok("Approve puts it next up, at its own address", row["status"] == "planned" and row["requested_at"]
   and row["slug"] == "leads-from-chatgpt" and plan.is_rewrite(row), row)


class Pub:
    def __init__(self):
        self.published = []

    def is_configured(self):
        return True, ""

    def address_refusals(self, slug, *, lists=None):
        return []

    def find_by_slug(self, slug):
        return {"_id": "doc"}

    def publish(self, *, lists=None, **fields):
        self.published.append(fields)
        return {"doc_id": "doc", "slug": fields["slug"], "created": False,
                "url": f"https://northwind.example/articles/{fields['slug']}"}

    def ping_indexnow(self, urls):
        return True


ok("the week is full while the rewrite waits", job.room() == 0, job.room())
facts_seen = []
job.writer.write = lambda q, **k: facts_seen.append(k.get("facts")) or {"title": "Leads from ChatGPT", "slug": "drifted"}
job.publisher = Pub()
out = job.periodic()
row = plan.get(live)
ok("a rewrite doesn't wait behind a full week", out.get("status") == "published", out)
ok("...it was written from today's facts", facts_seen and facts_seen[-1] == ("Northwind has helped 40 businesses since 2019.",))
ok("...and published at the same address", job.publisher.published[-1]["slug"] == "leads-from-chatgpt"
   and row["url"].endswith("/leads-from-chatgpt"))
ok("...keeping the date it first went live", row["published_at"] == "2026-10-04T02:46:17+00:00", row["published_at"])
res, _ = ask("aeo.propose_rewrite", {"id": art})
approvals.decide(res["approval"], True, by="owner")
ok("an unpublished article can be rewritten too", plan.is_rewrite(plan.get(art)))
box_settings.put("seo", "weekly_cap", 4)
fresh = plan.add("Brand new", "A new one?")
ok("a planned article can't be 'rewritten'", "error" in ask("aeo.propose_rewrite", {"id": fresh})[0])
box_settings.clear("seo", "facts")
ok("with no facts, a rewrite isn't offered", "facts" in ask("aeo.propose_rewrite", {"id": live})[0].get("error", ""))

print("\n— and this file cannot silently fall out of CI —")
if (ROOT / ".github").is_dir():
    ok("test_aeo_unpublish_rewrite_facts is in the workflow's suite list",
       "test_aeo_unpublish_rewrite_facts" in (ROOT / ".github/workflows/tests.yml").read_text())

print("\nALL OK" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
