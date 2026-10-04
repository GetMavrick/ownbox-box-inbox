"""The common questions: what customers ask, counted from the website's surveys and searches, never who asked.

OWNER, 2026-10-04 (to WebDev1, relayed by OSDev1; "yes, go" in OSDev6's session): feed the website's survey answers
into the AEO Machine and keep a table of common customer questions (#1793 Phase 2, the 4th evidence kind).

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  · anything is read while the labs switch is off;
  · a rating, a one-word choice, or anything with an email address, a long number or a link counts as a question;
  · the same question in two wordings is counted as two, or a refresh counts the same answers twice;
  · a question nobody asks any more keeps its old count;
  · who asked reaches the table (only the question travels);
  · an answered question is offered as open, or an open one isn't offered with one tap;
  · "What should we write next" doesn't read the questions.

NO NETWORK: PostHog and Search Console are stubs.

Run: python tests/test_aeo_common_questions.py
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
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "aeo_questions.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"

from core import state                                                   # noqa: E402

state.init_db()

from core import box_secrets, box_settings, labs                         # noqa: E402
from core.connector import prompts, tools as registry                    # noqa: E402
from marketing.aeo_machine import plan, posthog, questions, searches, tools  # noqa: E402,F401

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


READ = {"id": "seat_owner", "role": "read"}
asked_posthog, asked_search = [], []
SURVEY = []                                  # each event's properties, as PostHog returns them (a JSON string)
SEARCH = {"ok": True, "top": [], "opportunities": []}


def fake_one(host, project, key, hogql):
    asked_posthog.append(hogql)
    return [[json.dumps(p)] for p in SURVEY]


posthog._one = fake_one
searches.searches = lambda **_k: asked_search.append(1) or SEARCH
box_settings.put("seo", "posthog_host", "https://us.posthog.com")
box_settings.put("seo", "posthog_project", "4242")
box_settings.put("seo", "host", "northwind.example")
box_secrets.put(posthog.KEY, "phx_" + "S" * 40, user_id="t")

print("test_off_reads_nothing")
ok("the labs name is one core knows", questions.LABS in labs.KNOWN)
ok("off, a refresh reads nothing", questions.refresh() == {"off": True} and not asked_posthog and not asked_search)
body, code = registry.call("aeo.questions", {}, READ)
ok("off, the tool says so in a typed state", code == 200 and body.get("not_configured"), body)
labs.switch(questions.LABS, True, by="test")

print("\ntest_what_counts_as_a_question")
for text, want in (("Do you take walk-ins?", True), ("how much does botox cost", True),
                   ("Is it safe while pregnant", True), ("5", False), ("Pricing", False), ("Why?", False),
                   ("Can you email me at sam@example.com?", False), ("Is my order 12345678 shipped?", False),
                   ("What is https://example.com?", False), ("Great service, thanks.", False)):
    ok(f"{text!r} {'is' if want else 'is not'} a question", questions.is_question(text) is want)
ok("two wordings are one question", questions.key("Do you take walk-ins?") == questions.key("do you take walk ins"))

print("\ntest_surveys_and_searches_fill_the_table")
SURVEY[:] = [
    {"$survey_id": "s1", "$survey_response": "Do you take walk-ins?", "$survey_response_1": "5"},
    {"$survey_id": "s1", "$survey_response": "do you take walk ins", "distinct_id": "person-1"},
    {"$survey_id": "s2", "$survey_response_q9": "How long does a facial take?"},
    {"$survey_id": "s3", "$survey_response": "Pricing", "$survey_response_2": ["a", "b"]},
    {"$survey_id": "s3", "$survey_response": "Can you text me at 9495551234567?"},
]
SEARCH.update(top=[{"query": "how much does a facial cost", "clicks": 3, "impressions": 900, "position": 6.0},
                   {"query": "northwind spa", "clicks": 40, "impressions": 2000, "position": 1.0}],
              opportunities=[{"query": "how much does a facial cost", "clicks": 0, "impressions": 900, "position": 6.0},
                             {"query": "do you take walk ins", "clicks": 0, "impressions": 120, "position": 9.0}])
got = questions.refresh(now="2026-10-04T09:00:00+00:00")
ok("refresh reports what each source gave", got == {"questions": 3, "from_surveys": 2, "from_searches": 2}, got)
hogql = asked_posthog[-1]
ok("it asks PostHog for survey answers on this website only, the last 90 days",
   "event = 'survey sent'" in hogql and "northwind.example" in hogql and "90 DAY" in hogql, hogql)
ok("...and selects the answers, never the person", hogql.startswith("SELECT properties ") and "distinct_id" not in hogql
   and "person" not in hogql.lower())
rows = {q["question"]: q for q in questions.table()}
walk = rows.get("Do you take walk-ins?")
ok("two wordings in two answers count as one question asked twice, also seen in search",
   walk and walk["times_asked"] == 2 and walk["seen_in_search"] == 120
   and walk["sources"] == ["website survey", "Google search"], walk)
ok("a question in either survey key format is read", "How long does a facial take?" in rows)
ok("a search question in both lists counts once", rows["How much does a facial cost?"]["seen_in_search"] == 900)
ok("ratings, choices, lists and anything private never count", not any(
   k in rows for k in ("5?", "Pricing?", "Can you text me at 9495551234567?")) and len(rows) == 3, sorted(rows))
ok("a branded search that isn't a question isn't one", "Northwind spa?" not in rows)
with state.connect() as c:
    cols = [r["name"] for r in c.execute("PRAGMA table_info(aeo_questions)")]
ok("the table has no column that could hold who asked",
   not any(w in " ".join(cols) for w in ("person", "distinct", "email", "user", "name")), cols)
ok("most asked first, then most searched", [q["question"] for q in questions.table()][:2]
   == ["Do you take walk-ins?", "How long does a facial take?"])

print("\ntest_a_refresh_replaces_counts")
questions.refresh()
ok("twice is not double", {q["question"]: q["times_asked"] for q in questions.table()}["Do you take walk-ins?"] == 2)
SURVEY[:] = [{"$survey_response": "How long does a facial take?"}]
SEARCH.update(top=[], opportunities=[])
questions.refresh()
names = [q["question"] for q in questions.table()]
ok("a question nobody asks any more drops out", names == ["How long does a facial take?"], names)

print("\ntest_answered_or_open")
SURVEY[:] = [{"$survey_response": "Do you take walk-ins?"}, {"$survey_response": "How long does a facial take?"},
             {"$survey_response": "How long does a facial take?"}]
live = plan.add("Walk-ins", "Do you take walk-ins?")
plan.mark(live, "published", slug="walk-ins", url="https://northwind.example/articles/walk-ins", title="Walk-ins")
questions.refresh()
rows = {q["question"]: q for q in questions.table()}
ok("a question a live article answers says so, with its address",
   rows["Do you take walk-ins?"]["status"] == "answered"
   and rows["Do you take walk-ins?"]["article"]["url"].endswith("/walk-ins"), rows["Do you take walk-ins?"])
ok("the open ones are the ideas", [q["question"] for q in questions.ideas()] == ["How long does a facial take?"])
body, code = registry.call("aeo.questions", {}, READ)
res = body.get("result", body)
ok("the owner's AI reads the table", code == 200 and len(res["questions"]) == 2
   and res["open"] == ["How long does a facial take?"], res)
words = registry.registry()["aeo.questions"]["render"](res)       # what the MCP layer sends as in_words
ok("...in words, offering one tap to write the open one", "How long does a facial take?" in words
   and "Approvals" in words and "asked 2 times" in words, words[-600:])
ok("...and never names a person", "person-1" not in json.dumps(body))
from core import conversations                                            # noqa: E402
saved, conversations._PROVIDER = conversations._PROVIDER, None
note = registry.call("aeo.questions", {}, READ)[0]["result"]["note"]
ok("a box with no Unified Inbox is never told its inbox was counted (OSDev1's review)", "inbox" not in note, note)
conversations._PROVIDER = object()
note = registry.call("aeo.questions", {}, READ)[0]["result"]["note"]
ok("...and a box with one is", "the inbox" in note, note)
conversations._PROVIDER = saved

print("\ntest_what_to_write_next_reads_them")
p = prompts.get("what_to_write_next") if hasattr(prompts, "get") else None
src = (ROOT / "marketing/aeo_machine/tools.py").read_text()
ok("the ready-made ask uses the questions first", '(f"{MACHINE}.questions", "the questions customers ask most' in src
   and "the questions my \"\n        \"customers ask" in src)
from core.worker import PERIODIC                                          # noqa: E402
ok("the table is rebuilt daily", any(getattr(x, "name", None) == "aeo_questions" or
                                     (isinstance(x, dict) and x.get("name") == "aeo_questions")
                                     or "aeo_questions" in str(x) for x in (PERIODIC.values() if isinstance(PERIODIC, dict) else PERIODIC)))

print("\n— and this file cannot silently fall out of CI —")
if (ROOT / ".github").is_dir():
    ok("test_aeo_common_questions is in the workflow's suite list",
       "test_aeo_common_questions" in (ROOT / ".github/workflows/tests.yml").read_text())

print("\nALL OK" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
