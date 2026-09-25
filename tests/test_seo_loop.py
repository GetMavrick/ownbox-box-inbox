"""The SEO machine's real path: question -> writer -> job -> publisher -> guard, end to end.

WHY THIS FILE EXISTS (OSDev9's review, docs/REVIEW_SEO_MACHINE_OSDEV6_FIXES.md §5): every other
seo suite fakes the module on the far side of its seam. The job's suite fakes the writer, the
writer's suite checks with its own slug, the publisher's suite never sees the job. Each passed while
two high-severity bugs lived on the path an article actually takes. "A unit suite proves a module;
only a seam test proves the machine."

ONLY THREE THINGS ARE STUBBED, the three outside the box: the model (`brain.think`), the network
(`core.net`) and the Sanity token. Everything else is the real code, on a real `seo_plan` table.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED (the review's §7, one planted failure each):
  · F-A: a competitor in lowercase in a slug, a URL or a link is published;
  · F-B: a question holding a banned word costs a model call, on the first try or any retry;
  · F-B: the writer checks a different slug from the one the publisher will publish;
  · F-C: an FAQ with an extra key fails an article that was already paid for;
  · F-D: a created article has no publishedAt, or a patch moves it;
  · F-E: one banned word produces two refusals.

Run: python tests/test_seo_loop.py
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
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "seo_loop.db")

from core import box_settings, state                                     # noqa: E402

state.init_db()

from marketing.seo_machine import guard, job, plan, publisher, writer    # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


# ── the three stubs ───────────────────────────────────────────────────────────────────────────
CALLS = {"think": 0}
MUTATIONS: list = []
LIVE: dict = {}                     # slug -> doc id, what "Sanity" holds

publisher._token = lambda: "tok"                                   # 1 of 3: the token


class FakeNet:                                                     # 2 of 3: the network
    PostRefused = publisher.net.PostRefused

    def get_public(self, url, **_kw):
        for slug, doc_id in LIVE.items():
            if json.dumps(slug) in __import__("urllib.parse").parse.unquote(url):
                return 200, json.dumps({"result": {"_id": doc_id}})
        return 200, json.dumps({"result": None})

    def post_public(self, url, **kw):
        if "indexnow" in url:
            return 200, "{}"
        m = kw["json"]["mutations"][0]
        MUTATIONS.append(m)
        if "create" in m:
            doc_id = f"doc{len(MUTATIONS)}"
            LIVE[m["create"]["slug"]["current"]] = doc_id
            return 200, json.dumps({"results": [{"id": doc_id}]})
        return 200, json.dumps({"results": [{"id": m["patch"]["id"]}]})


publisher.net = FakeNet()


def model_says(*replies):                                          # 3 of 3: the model
    queue = [json.dumps(r) for r in replies]

    def think(**_kw):
        CALLS["think"] += 1
        return queue.pop(0) if len(queue) > 1 else queue[0]
    writer.brain = type("B", (), {"think": staticmethod(think)})


def configure(**lists):
    for k in ("never_words", "never_phrases", "competitors", "allowed_numbers", "facts"):
        box_settings.clear("seo", k)
    for k, v in dict(project_id="proj1234", dataset="production",
                     site_url="https://northwind.example", weekly_cap=50, **lists).items():
        box_settings.put("seo", k, v)


def reset():
    CALLS["think"] = 0
    MUTATIONS.clear()
    LIVE.clear()
    with state.connect() as c:
        c.execute("DELETE FROM seo_plan")


def run_one(topic, question):
    rid = plan.add(topic, question)
    job.periodic()
    return plan.get(rid)


CLEAN = {"title": "What a statement of work should include",
         "short_answer": "Scope, deliverables, timeline and acceptance criteria.",
         "meta_description": "What goes in a statement of work.",
         "body_markdown": "## The parts\n\nScope, deliverables, timeline and acceptance.",
         "faqs": [{"question": "Who signs it?", "answer": "Both parties."}]}

SEED = ("Kinso", "Unibox", "HeyRobyn", "Converlo", "Mailbird", "Missive", "AI Emaily",
        "Intercom", "Zendesk", "HubSpot", "Salesforce")

print("test_F_A_competitors_in_markup")
for name in SEED:
    low = name.lower()
    lists = {"competitors": [name]}
    by_slug = publisher.refusals(lists=lists, title="Plain title", slug=f"why-{low.replace(' ', '-')}")
    by_link = publisher.refusals(lists=lists, title="Plain title", slug="plain",
                                 source_markdown=f"See [pricing](https://www.{low.replace(' ', '')}.com/x).")
    ok(f"{name}: refused in a slug and in a link, whatever the case",
       any(r.rule == "competitor" for r in by_slug) and any(r.rule == "competitor" for r in by_link),
       (by_slug, by_link))
for name, slug, link in (("HubSpot", "is-hub-spot-worth-it", "https://call-rail.example/hub-spot"),
                         ("CallRail", "call-rail-vs-us", "https://call-rail.example/x")):
    got = (publisher.refusals(lists={"competitors": [name]}, title="Plain title", slug=slug)
           + publisher.refusals(lists={"competitors": [name]}, title="Plain title", slug="plain",
                                source_markdown=f"See [this]({link})."))
    ok(f"a CamelCase name split in an address is still refused: {name} (OSDev1, #1578)",
       sum(r.rule == "competitor" for r in got) >= 2, got)
reset()
configure(competitors=["HubSpot"])
model_says(CLEAN)
row = run_one("Tools", "Is Hub Spot worth it?")
ok("end to end: 'Is Hub Spot worth it?' never publishes, and costs nothing",
   row["status"] == "refused" and CALLS["think"] == 0 and not MUTATIONS, (row["status"], CALLS["think"]))

WORD = {"word_competitors": ["Front"]}          # a rival the buyer marked as an everyday word
prose = publisher.refusals(lists=WORD, title="Plain title", slug="plain",
                           source_markdown="Build the front end first.")
ok("a marked everyday word keeps its case in prose and Markdown: 'the front end' passes",
   prose == [], prose)
slugged = publisher.refusals(lists=WORD, title="Plain title", slug="front-end-guide")
ok("...but not in a web address, which carries no case",
   any(r.rule == "competitor" for r in slugged), slugged)

reset()
configure(competitors=["Salesforce"])
model_says(CLEAN)
row = run_one("Cost", "Is a one-time machine cheaper than Salesforce?")
ok("end to end: a competitor in the question never reaches a public address",
   row["status"] == "refused" and not MUTATIONS, row)

reset()
configure(competitors=["Zendesk"])
model_says(dict(CLEAN, body_markdown="## The parts\n\nSee [their pricing](https://www.zendesk.com/pricing)."))
row = run_one("Scope", "What should a statement of work include?")
ok("end to end: a drafted link to a competitor's site is never published",
   row["status"] == "refused" and not MUTATIONS, (row["status"], row.get("refusal")))

print("\ntest_F_B_the_address_is_checked_before_any_spend")
reset()
configure(never_words=["call"])
model_says(CLEAN)
row = run_one("Missed calls", "How do I stop missing calls?")
ok("a banned word in the question: refused with ZERO model calls",
   row["status"] == "refused" and CALLS["think"] == 0, (row["status"], CALLS["think"]))
ok("...and the owner is told it is in the web address, from the question",
   "web address" in (row["refusal"] or "") and "question" in row["refusal"], row["refusal"])
plan.request_now(row["id"])
job.periodic()
ok("pressing 'Try again now' still costs nothing", CALLS["think"] == 0
   and plan.get(row["id"])["status"] == "refused", CALLS["think"])

reset()
configure(competitors=["acme"])
model_says(CLEAN)
try:
    writer.write("How do I write a statement of work?", slug="acme-statement-of-work")
    refused = []
except guard.GuardRefused as e:
    refused = e.refusals
ok("the writer checks the SAME slug the publisher will publish",
   any(r.rule == "competitor" for r in refused), refused)
fields = writer.write("How do I write a statement of work?", slug="pinned-by-the-row")
ok("...and returns it, not the one its title would make", fields["slug"] == "pinned-by-the-row")

print("\ntest_F_C_an_extra_key_on_an_FAQ")
reset()
configure()
messy = dict(CLEAN, faqs=[{"id": 1, "question": "Who signs it?", "answer": "Both parties.",
                           "source": "model"},
                          {"question": "Is it binding?", "answer": ["Yes,", "once signed."]}])
model_says(messy)
row = run_one("SOW", "What should a statement of work include?")
created = [m["create"] for m in MUTATIONS if "create" in m]
ok("publishes, on the first paid draft", row["status"] == "published" and CALLS["think"] == 1,
   (row["status"], row.get("refusal"), CALLS["think"]))
ok("...with each FAQ exactly {question, answer}, both strings",
   created and created[0]["faqs"] == [{"question": "Who signs it?", "answer": "Both parties."},
                                      {"question": "Is it binding?", "answer": "Yes, once signed."}],
   created and created[0].get("faqs"))

print("\ntest_F_D_the_publish_date")
ok("a created article carries publishedAt", created and bool(created[0].get("publishedAt")),
   created and created[0])
first_date = created[0].get("publishedAt") if created else None
publisher.publish(lists={}, **dict(writer._fields(CLEAN), slug=row["slug"]))
patches = [m["patch"] for m in MUTATIONS if "patch" in m]
ok("a later patch of it leaves publishedAt alone",
   patches and "publishedAt" not in patches[-1]["set"], patches and patches[-1]["set"].keys())
ok("...so the date it first went live stays its date", first_date is not None)

reset()
configure()
dicty = dict(CLEAN, faqs=[{"question": "Who signs it?", "answer": {"text": "Both parties."}}])
model_says(dicty, CLEAN)
row = run_one("SOW2", "Who signs a statement of work?")
created2 = [m["create"] for m in MUTATIONS if "create" in m]
ok("an FAQ answer that is not text spends the repair round, never reaches the page",
   row["status"] == "published" and CALLS["think"] == 2
   and "{'text'" not in json.dumps(created2), (row["status"], CALLS["think"]))

print("\ntest_F_E_one_word_one_refusal")
found = publisher.refusals(lists={"never_words": ["call"]}, title="Plain title", slug="plain",
                           body_blocks=[{"_type": "block", "children": [{"text": "Give us a call."}]}],
                           source_markdown="Give us a call.")
ok("one banned word, one refusal", len(found) == 1, found)

print("\n— and this file cannot silently fall out of CI —")
if (ROOT / ".github").is_dir():                  # a buyer's box has no repository
    ok("test_seo_loop is in the workflow's suite list",
       "test_seo_loop" in (ROOT / ".github/workflows/tests.yml").read_text())

print("\nALL OK" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
