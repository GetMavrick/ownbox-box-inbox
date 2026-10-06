"""The AEO Machine's proposals: a person's own AI asks, and nothing changes until that person taps Approve.

OWNER, 2026-10-02: full command of the machine from Claude (#1793 §1.1, "Agree. go"). OSDev1's seat
ruling the same morning: an own-AI seat may "read, draft, propose; never send, publish or charge ...
every proposal is a tap."

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  · a proposal changes the plan or a setting before a person approves it;
  · a read-only seat gains a way to ask (the tools are `act` and up, as the Inbox's draft_reply is);
  · Decline, an expired proposal or a decision without a person runs anything;
  · two taps on Approve run it twice, or a retried proposal makes two rows;
  · the owner approves words other than the ones that run (shown arguments != run arguments);
  · a proposal the screens would refuse is accepted (duplicate topic, a weekly number out of range, a
    website that isn't one), or one that was fine a week ago runs although it no longer is;
  · a connection or a key can be changed by a proposal.

Run: python tests/test_aeo_connector_proposals.py
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "aeo_proposals.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"

from core import state                                            # noqa: E402

state.init_db()

from core import approvals, box_settings                          # noqa: E402
from core.connector import tools as registry                      # noqa: E402
from marketing.aeo_machine import plan, proposals, settings, tools  # noqa: E402,F401

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


ACT = {"id": "seat_own", "role": "act", "label": "the owner's Claude"}
READ = {"id": "seat_ro", "role": "read", "label": "a read-only assistant"}
NAMES = ("aeo.propose_topic", "aeo.propose_retry", "aeo.propose_setting")


def ask(name, args, seat=ACT):
    body, code = registry.call(name, args, seat)
    return body.get("result", body), code


def mine():
    return [a for a in approvals.waiting() if a["machine"] == "aeo"]


def approve(aid):
    return approvals.decide(aid, True, by="owner")


print("test_who_may_ask")
reg = registry.registry()
ok("the three proposals register", all(n in reg for n in NAMES))
ok("each is write:proposals, act and up", all(reg[n]["capability"] == "write:proposals"
                                              and reg[n]["min_role"] == "act" for n in NAMES))
act_sees = {s["name"] for s in registry.visible_to(ACT)}
read_sees = {s["name"] for s in registry.visible_to(READ)}
ok("an own-AI (act) seat sees them", set(NAMES) <= act_sees, sorted(set(NAMES) - act_sees))
ok("a read-only seat does not", not (set(NAMES) & read_sees))
body, code = registry.call("aeo.propose_topic", {"question": "x?"}, READ)
ok("...and is refused if it tries", code == 403, (code, body))
ok("every title is plain words", all(registry.plain_title(reg[n]["title"]) for n in NAMES))

print("\ntest_a_topic_waits_for_approve")
res, code = ask("aeo.propose_topic", {"question": "How much does a roof repair cost?", "topic": "Roof repair cost"})
ok("asked, not added", code == 200 and res["asked"] and not plan.rows(), (code, res))
w = mine()
ok("one proposal waits, with who asked", len(w) == 1 and w[0]["proposed_by"] == "the owner's Claude", w)
ok("the owner reads exactly what will run", w[0]["detail"]["arguments"] ==
   {"topic": "Roof repair cost", "question": "How much does a roof repair cost?", "write now": "no"},
   w[0]["detail"])
again, _ = ask("aeo.propose_topic", {"question": "How much does a roof repair cost?", "topic": "Roof repair cost"})
ok("asking twice is one proposal", again["repeat"] and again["approval"] == res["approval"] and len(mine()) == 1)
ok("a decision without a person runs nothing", not approvals.decide(res["approval"], True, by="")["ok"]
   and not plan.rows())
out = approve(res["approval"])
rows = plan.rows()
ok("Approve adds it, once", out["ok"] and len(rows) == 1 and rows[0]["topic"] == "Roof repair cost", (out, rows))
ok("a second tap runs nothing", not approve(res["approval"])["ok"] and len(plan.rows()) == 1)
dup, _ = ask("aeo.propose_topic", {"question": "Roof repair cost"})
ok("a topic already waiting is refused at once, nothing asked", dup.get("asked") is False
   and "already waiting" in dup["error"] and not mine(), dup)

now, _ = ask("aeo.propose_topic", {"question": "Do you offer free estimates?", "now": True})
ok("write now is shown", mine()[0]["detail"]["arguments"]["write now"] == "yes")
approve(now["approval"])
row = [r for r in plan.rows() if r["topic"] == "Do you offer free estimates?"][0]
ok("approved with now, it is next up", row["requested_at"] is not None, row)

late, _ = ask("aeo.propose_topic", {"question": "Gutter cleaning"})
plan.add("Gutter cleaning")
out = approve(late["approval"])
ok("checked again when it runs: a topic added meanwhile is not added twice", not out["ok"]
   and "already waiting" in out["text"]
   and sum(r["topic"] == "Gutter cleaning" for r in plan.rows()) == 1, out)

decl, _ = ask("aeo.propose_topic", {"question": "Siding repair"})
n = len(plan.rows())
ok("Decline changes nothing", approvals.decide(decl["approval"], False, by="owner")["ok"] and len(plan.rows()) == n)

print("\ntest_retry_waits_for_approve")
held = plan.add("Acme vs us")
plan.mark(held, "refused", refusal='competitor: "Acme"')
res, _ = ask("aeo.propose_retry", {"id": held})
ok("asked; the article is still held back", res["asked"] and plan.get(held)["status"] == "refused", res)
ok("the owner sees why it stopped", mine()[0]["detail"]["arguments"]["why it stopped"] == 'competitor: "Acme"')
approve(res["approval"])
ok("Approve sends it back, next up", plan.get(held)["status"] == "planned" and plan.get(held)["requested_at"])
live = plan.add("Live one")
plan.mark(live, "published", url="https://x.example/articles/live")
bad, _ = ask("aeo.propose_retry", {"id": live})
ok("a live article can't be retried", bad.get("asked") is False and "held back" in bad["error"], bad)
ok("an unknown id is a plain error", "error" in ask("aeo.propose_retry", {"id": 9999})[0])

print("\ntest_settings_wait_for_approve")
res, _ = ask("aeo.propose_setting", {"name": "weekly_cap", "value": "6"})
ok("asked; still 4 until approved", res["asked"] and settings.get()["weekly_cap"] == 4, res)
ok("the owner sees the current value beside the new one",
   mine()[0]["detail"]["arguments"] == {"setting": "Articles a week", "change": "set to", "value": 6, "now": 4},
   mine()[0]["detail"])
approve(res["approval"])
ok("Approve sets it", settings.get()["weekly_cap"] == 6)
ok("out of range is refused at once", "0 to 21" in ask("aeo.propose_setting", {"name": "weekly_cap", "value": "99"})[0]
   .get("error", ""))

res, _ = ask("aeo.propose_setting", {"name": "site_url", "value": "roofs.example"})
approve(res["approval"])
ok("the website is set with its host, as the screen does",
   settings.get()["site_url"] == "https://roofs.example" and settings.get()["host"] == "roofs.example")
ok("a website that isn't one is refused", "error" in ask("aeo.propose_setting",
                                                           {"name": "site_url", "value": "http://x"})[0])

res, _ = ask("aeo.propose_setting", {"name": "never_words", "value": "cheap"})
ok("a list defaults to add", res["asked"] and mine()[0]["detail"]["arguments"]["change"] == "add", res)
approve(res["approval"])
ok("Approve adds the one entry", settings.lists()["never_words"] == ("cheap",))
ok("adding it again is refused", "already" in ask("aeo.propose_setting",
                                                  {"name": "never_words", "value": "Cheap"})[0].get("error", ""))
late, _ = ask("aeo.propose_setting", {"name": "competitors", "value": "Acme Roofing"})
box_settings.put("seo", "competitors", ["Acme Roofing"])
out = approve(late["approval"])
ok("checked again when it runs: an entry added meanwhile is not added twice",
   not out["ok"] and settings.lists()["competitors"] == ("Acme Roofing",), (out, settings.lists()["competitors"]))
res, _ = ask("aeo.propose_setting", {"name": "never_words", "value": "cheap", "change": "remove"})
approve(res["approval"])
ok("Approve removes it", settings.lists()["never_words"] == ())
ok("removing one that isn't there is refused", "not on" in ask(
    "aeo.propose_setting", {"name": "competitors", "value": "Acme", "change": "remove"})[0].get("error", ""))
ok("two entries at once are refused", "exactly one" in ask(
    "aeo.propose_setting", {"name": "facts", "value": "a\nb"})[0].get("error", ""))
for name in ("airtable_base", "project_id", "posthog_project", "indexnow_key", "SANITY_API_TOKEN_OWNBOX"):
    r = ask("aeo.propose_setting", {"name": name, "value": "x"})[0]
    ok(f"{name} can't be suggested", r.get("asked") is False and "can't be suggested" in r["error"], r)
ok("nothing refused was queued", not mine(), mine())

print("\ntest_status_counts_what_waits")
ask("aeo.propose_setting", {"name": "facts", "value": "Licensed and insured since 2009."})
st = registry.call("aeo.status", {}, READ)[0]["result"]
ok("status counts suggestions waiting for the owner's OK", st["suggestions_waiting_for_ok"] == 1, st)

print("\n— and this file cannot silently fall out of CI —")
if (ROOT / ".github").is_dir():
    ok("test_aeo_connector_proposals is in the workflow's suite list",
       "test_aeo_connector_proposals" in (ROOT / ".github/workflows/tests.yml").read_text())

print("\nALL OK" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
