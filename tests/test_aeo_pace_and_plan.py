"""The weekly number spread across the week, and the order the owner sets (owner, 2026-10-08: "yes, build 1–3").

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  · a box with room publishes its whole week in minutes again, then nothing for six days;
  · a new box's first article waits, when nothing has gone out before it;
  · an asked-for topic gets around the pace or the week's room, or a rewrite is held back by them;
  · a rewrite moves the pace (it keeps its first date), or an unpublished article stops counting for it;
  · the screen, the connector or the job disagree about which topic is next (one order, plan.QUEUE);
  · Move up or Remove touches a live, unpublished, Airtable or being-written article;
  · one plan approval changes anything before Approve, adds a topic twice, adds a question a live article
    already answers, or runs half of itself when part of it no longer holds;
  · the card the owner approves is not what runs (the rows are read back);
  · "write now" promises minutes when the next article is hours away.

Run: python tests/test_aeo_pace_and_plan.py
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "aeo_pace.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"

from core import state                                                    # noqa: E402

state.init_db()

from core import approvals, box_settings                                  # noqa: E402
from core.connector import tools as registry                              # noqa: E402
from marketing.aeo_machine import job, plan, proposals, tools             # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


ACT = {"id": "seat_own", "role": "act", "label": "the owner's Claude"}
NOW = datetime.now(timezone.utc)


def ago(**kw) -> str:
    return (NOW - timedelta(**kw)).isoformat()


def reset(*rows, cap: int = 4):
    """Each row: (topic, status='planned', published_at=None, url=None)."""
    with state.connect() as c:
        c.execute("DELETE FROM seo_plan")
    box_settings.put("seo", "weekly_cap", cap)
    box_settings.put("seo", "facts", ["The Base Machine is bought once."])
    ids = []
    for r in rows:
        rid = plan.add(r[0], "")
        st = r[1] if len(r) > 1 else "planned"
        url = r[3] if len(r) > 3 else None
        with state.connect() as c:
            c.execute("UPDATE seo_plan SET status = ?, published_at = ?, url = ?, slug = ? WHERE id = ?",
                      (st, r[2] if len(r) > 2 else None, url, (url or "").rsplit("/", 1)[-1] or None, rid))
        ids.append(rid)
    return ids


def ids_in_order() -> list[int]:
    return [r["id"] for r in plan.queue()]


# THE JOB'S TWO OUTSIDE CALLS ARE STAND-INS: the publisher says it is set up, and a write publishes at once.
job.publisher.is_configured = lambda: (True, "")
WRITES: list[int] = []


def _fake_write(row):
    WRITES.append(row["id"])
    plan.mark(row["id"], "published", slug=f"a-{row['id']}", url=row.get("url") or f"https://example.com/a-{row['id']}")
    return {"id": row["id"], "status": "published"}


job._write_and_publish = _fake_write

print("test_pace_words")
ok("4 a week is one about every 42 hours", job.pace_words(4) == "one about every 42 hours", job.pace_words(4))
ok("7 a week is one a day", job.pace_words(7) == "one a day")
ok("1 a week is one a week", job.pace_words(1) == "one a week")
ok("2 a week is one about every 84 hours", job.pace_words(2) == "one about every 84 hours")
ok("0 is no pace at all", job.pace_words(0) == "" and job.spacing(0) is None)
ok("the spacing is a week over the number", job.spacing(4) == timedelta(hours=42))

print("test_a_new_box_publishes_its_first_at_once")
a, b = reset(("first",), ("second",))
ok("nothing out before: the next one is due now", job.next_at(now=NOW) == NOW)
WRITES.clear()
r = job.periodic()
ok("the first tick publishes the first topic", WRITES == [a] and plan.get(a)["status"] == "published", r)

print("test_the_week_is_spread_out")
r = job.periodic()
ok("the next tick waits for the pace instead of publishing the second at once",
   r.get("skipped") == "paced" and WRITES == [a] and plan.get(b)["status"] == "planned", r)
due = job.next_at()
pub = datetime.fromisoformat(plan.get(a)["published_at"])
ok("...and says when: 42 hours after the last one", due and abs((due - pub) - timedelta(hours=42)) < timedelta(seconds=2),
   (due, pub))
ok("...in the job's own answer too", r.get("next_at") == due.isoformat(), r)

print("test_the_rolling_week_still_holds")
# The burst a box could make before this change: one 6 days ago, then four 4 days ago, a few hours apart.
reset(("w1", "published", ago(days=6)), ("w2", "published", ago(days=4, hours=14)),
      ("w3", "published", ago(days=4, hours=13)), ("w4", "published", ago(days=4, hours=8)),
      ("w5", "published", ago(days=4)), ("next",))
due = job.next_at(now=NOW)
want = NOW - timedelta(days=4, hours=14) + timedelta(days=7)
ok("five out with four a week: the next waits until the second oldest is a week old",
   abs(due - want) < timedelta(seconds=2), (due, want))
WRITES.clear()
r = job.periodic()
ok("...and the tick says it is the week's room, not the pace", r.get("skipped") == "weekly_cap" and not WRITES, r)
reset(("w1", "published", ago(days=2)), ("next",), cap=0)
ok("at 0 a week nothing is due, ever", job.next_at() is None)
ok("...and the tick says paused by the cap", job.periodic().get("skipped") == "weekly_cap")

print("test_an_ask_waits_and_a_rewrite_does_not")
a, b, c = reset(("live", "published", ago(hours=1), "https://example.com/live"), ("queued",), ("asked",))
plan.request_now(c)
WRITES.clear()
ok("an asked-for topic waits for the pace like any other", job.periodic().get("skipped") == "paced" and not WRITES)
ok("...but it is first in line", plan.next_up(1)[0]["id"] == c)
before = job.next_at()
plan.rewrite(a)
r = job.periodic()
ok("a rewrite the owner asked for goes now: it replaces an article, it doesn't add one", WRITES == [a], (r, WRITES))
ok("...and keeps its first date, so the pace doesn't move", job.next_at() == before, (job.next_at(), before))
a, b = reset(("off", "refused", ago(hours=2), "https://example.com/off"), ("queued",))
with state.connect() as cx:
    cx.execute("UPDATE seo_plan SET refusal = ? WHERE id = ?", (plan.UNPUBLISHED + " on Oct 8, 2026.", a))
ok("an article unpublished since still counts for the pace: it went out", job.next_at() > NOW)

print("test_one_order_for_the_job_the_screen_and_the_connector")
a, b, c, d = reset(("a",), ("b",), ("c",), ("d",))
ok("with no order set, oldest first", ids_in_order() == [a, b, c, d])
ok("move up swaps a topic with the one before it", plan.move_up(c) and ids_in_order() == [a, c, b, d])
ok("the first can't move up", not plan.move_up(a) and ids_in_order() == [a, c, b, d])
plan.request_now(d)
ok("an ask goes first", ids_in_order()[0] == d)
ok("moving another folds the ask into the order, keeping it where it was",
   plan.move_up(b) and ids_in_order() == [d, a, b, c] and plan.get(d)["requested_at"] is None)
ok("place puts these first and the rest keep their order", plan.place([c, a]) == [c, a, d, b]
   and ids_in_order() == [c, a, d, b])
try:
    plan.place([c, 999])
    ok("place refuses a number that isn't a planned topic", False)
except plan.Rejected:
    ok("place refuses a number that isn't a planned topic, changing nothing", ids_in_order() == [c, a, d, b])
WRITES.clear()
job.periodic()
ok("the job takes the first in that order", WRITES == [c], WRITES)
listed = tools.articles()["articles"]
ok("the connector lists them in that order, next first",
   [x["id"] for x in listed if x["status"] == "planned"] == [a, d, b]
   and listed[0]["state"] == "next up" and listed[1]["state"] == "planned, 2nd in line", listed[:2])

print("test_remove_only_what_never_became_an_article")
a, b, c, d, e = reset(("planned",), ("live", "published", ago(days=9), "https://example.com/live"),
                      ("off", "refused", ago(days=9), "https://example.com/off"), ("held", "refused"),
                      ("from airtable",))
with state.connect() as cx:
    cx.execute("UPDATE seo_plan SET airtable_id = 'rec1' WHERE id = ?", (e,))
ok("a planned topic is removed", plan.remove(a) and plan.get(a) is None)
ok("a held-back topic with no address is removed", plan.remove(d) and plan.get(d) is None)
ok("a live article never is", plan.remove(b) is None and plan.get(b))
ok("an unpublished one never is", plan.remove(c) is None and plan.get(c))
ok("one from the owner's Airtable never is", plan.remove(e) is None and plan.get(e))

print("test_one_approval_sets_the_plan")
a, b, c, d, e = reset(("self hosted vs saas",), ("ai for gym owners",), ("ai for med spas",),
                      ("How can I get more leads from ChatGPT?", "published", ago(days=9),
                       "https://example.com/leads"),
                      ("writing now", "writing"))
order = "\n".join([f"{c}", "How can people and AI agents work together?", "- AI FOR GYM OWNERS",
                   f"Article {d}: How can I get more leads from ChatGPT?", "What is an MCP server?"])
before = (ids_in_order(), len(plan.rows()))
body, code = registry.call("aeo.propose_plan", {"order": order, "remove": f"{a}"}, ACT)
res = body.get("result", body)
ok("it asks once", code == 200 and res.get("asked"), (code, body))
ok("...and nothing changes before Approve", (ids_in_order(), len(plan.rows())) == before)
card = next(x for x in approvals.waiting() if x["id"] == res["approval"])
rows = (card.get("detail") or {}).get("arguments") or {}
ok("the card is the plan, one row per article in the order it will be written", list(rows) == [
    "01", "02", "03", "04", "05", "Take off 01", "Then"], list(rows))
ok("...the rewrite first, at its own address", rows["01"].startswith(f"Rewrite article {d} at its own address"))
ok("...a planned topic by its number", rows["02"] == f"Article {c}: ai for med spas", rows["02"])
ok("...a new question as new", rows["03"] == "New: How can people and AI agents work together?", rows["03"])
ok("...a question already planned is that topic, never a second copy", rows["04"] == f"Article {b}: ai for gym owners",
   rows["04"])
ok("...what is taken off, by name", rows["Take off 01"] == f"Article {a}: self hosted vs saas")
ok("...and the title says what it does", card["title"] == "Set your AEO plan: 5 in this order (2 new), 1 taken off",
   card["title"])
done = approvals.decide(res["approval"], True, by="owner")
text = str(done.get("text") or (done.get("result") or {}).get("text") or "")
q = plan.queue()
ok("approved, it runs: the rewrite first, then the plan's order", [r["id"] for r in q][:2] == [d, c]
   and [r["topic"] for r in q][2:5] == ["How can people and AI agents work together?", "ai for gym owners",
                                       "What is an MCP server?"], [r["topic"] for r in q])
ok("...the topic taken off is gone", plan.get(a) is None)
ok("...the topic being written is untouched", plan.get(e)["status"] == "writing")
ok("...and it says when the next goes out", "Your AEO plan is set" in text and "goes out" in text, text)

print("test_a_plan_that_no_longer_holds_changes_nothing")
a, b = reset(("one",), ("two",))
res, _ = registry.call("aeo.propose_plan", {"order": f"{b}\nA brand new question?"}, ACT)
res = res.get("result", res)
with state.connect() as cx:                                    # written and published while the card waited
    cx.execute("UPDATE seo_plan SET status = 'published', url = 'https://example.com/two' WHERE id = ?", (b,))
before = (ids_in_order(), len(plan.rows()))
done = approvals.decide(res["approval"], True, by="owner")
ok("the whole plan stops, with why", "nothing was changed" in str(done.get("text") or (done.get("result") or {}).get("text")),
   done)
ok("...and nothing was added or moved", (ids_in_order(), len(plan.rows())) == before)

print("test_what_a_plan_refuses")


def refused(args) -> str:
    body, _ = registry.call("aeo.propose_plan", args, ACT)
    r = body.get("result", body)
    return "" if r.get("asked") else str(r.get("error") or r)


a, b, c, d = reset(("planned",), ("How do I get leads from ChatGPT?", "published", ago(days=9),
                                  "https://example.com/leads"), ("being written", "writing"), ("held", "failed"))
ok("a question a live article answers: use its number instead",
   f"Put {b} on that line" in refused({"order": "how do I get leads from chatgpt"}))
ok("a number listed twice", "listed twice" in refused({"order": f"{a}\n{a}"}))
ok("a number that isn't an article", "no article 999" in refused({"order": "999"}))
ok("one being written", "being written" in refused({"order": f"{c}"}))
ok("one that stopped: try it again first", "propose_retry" in refused({"order": f"{d}"}))
ok("taking off a live article: unpublish it instead", "propose_unpublish" in refused({"remove": f"{b}"}))
ok("too many lines at once", f"at most {proposals.PLAN_MAX}" in refused(
    {"order": "\n".join(f"Question number {n}?" for n in range(proposals.PLAN_MAX + 1))}))
ok("nothing at all", "Give the order" in refused({}))

print("test_the_words")
reset(("w1", "published", ago(hours=1)), ("next",))
st = tools.status()
said = tools._render_status({**st, "ready": True})
ok("status says the pace and when the next goes out, not '0 left this week'",
   "4 a week means one about every 42 hours" in said and "Next new article: " in said
   and "left this week" not in said, said)
ok("...with the time as a field too", st["next_article_at"] and st["pace"] == "one about every 42 hours")
reg = registry.registry()
ok("propose_topic no longer promises minutes", "within minutes" not in reg["aeo.propose_topic"]["description"])
ok("propose_plan is act and up, write:proposals", reg["aeo.propose_plan"]["min_role"] == "act"
   and reg["aeo.propose_plan"]["capability"] == "write:proposals")
a, b = reset(("off", "refused", ago(days=3), "https://example.com/off"), ("next",))
with state.connect() as cx:
    cx.execute("UPDATE seo_plan SET refusal = ? WHERE id = ?", (plan.UNPUBLISHED + " on Oct 8, 2026.", a))
said = tools._render_articles(tools.articles())
ok("an unpublished article waits on nobody and is never offered 'try again'",
   "Waiting on you" not in said and "Try again" not in said, said)
ok("planned topics are listed in the order they will be written", "To be written, in this order:" in said, said)
ok("a time hours away is said as a time", job.when_words(NOW + timedelta(hours=30), now=NOW).startswith(
    ("tomorrow at", "today at")) or " at " in job.when_words(NOW + timedelta(hours=30), now=NOW))
ok("a time already due is 'within a few minutes'", job.when_words(NOW, now=NOW) == "within a few minutes")

print("\nALL OK" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
