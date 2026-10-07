"""Drafts are written by Sonnet, and when in doubt the inbox drafts (owner, 2026-10-06).

Owner, 2026-10-06: "I wanna make sure that sonnet is used to make sure we have good quality. And number two, please make
sure that all the right emails are getting drafts. I think I've seen some come through that should've had a draft, but
they didn't get drafted. Please slightly error on the side of drafting too many."

Measured on his box that day: 79 people waiting, 27 with a draft. `inbox_draft` was in no model map, so every draft
fell to `default` (Haiku); the instructions told the model a reply nobody needed was worse than none, and had no case
for a friend making plans, which is how a friend's Instagram thread ended with no draft.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * a draft is written by anything but Sonnet, on the API or on the owner's own Claude sign-in;
  * the instructions stop saying "when in doubt, draft", or lose the case for someone the owner knows;
  * a "no reply needed" made under older instructions is never asked again, or is asked twice under the same ones;
  * one a person dismissed, one older than 30 days, one already answered or one from a robot is asked again;
  * a re-check costs a new person their first draft (new messages come first);
  * a revived draft still hides its person from the Morning Review's "Who to answer first".

No network: the model is stood in for.

Run: python tests/test_drafts_lean_to_drafting.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "lean.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"

from core import brain, cost_guard, spaces, state  # noqa: E402

state.init_db()
from core.config import get_config  # noqa: E402
from core.dispatch import app  # noqa: E402,F401
from marketing.customer_voice.drafter import draft, store  # noqa: E402
from marketing.customer_voice.inbox import store as inbox_store  # noqa: E402
from marketing.customer_voice.inbox import tools as inbox_tools  # noqa: E402

FAILS: list[str] = []
SPACE = inbox_tools._space()
ANSWER = ["Sounds good, see you Wednesday."]
CALLS: list[dict] = []


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:400]}"))
    if not cond:
        FAILS.append(label)


print("test_drafts_are_written_by_sonnet")
cfg = get_config()
ok("the drafter's task is Sonnet in the model map, never the default",
   cfg["models"].get("inbox_draft") == "sonnet" and cfg["models"]["default"] != "sonnet", cfg["models"].get("inbox_draft"))
ok("...so the API call names Sonnet's id", brain._model_for("inbox_draft") == cfg["model_ids"]["sonnet"])
ok("...and the owner's own Claude sign-in is asked for Sonnet", brain._cli_model(brain._model_for("inbox_draft")) == "sonnet")
real_think = brain.think
seen_task = []
brain.think = lambda task, prompt, **kw: seen_task.append(task) or ANSWER[0]
cost_guard.check_vendor = lambda *a, **k: None
spaces.all_spaces = lambda: [{"name": SPACE}]
n = [0]


def inbound(zcid: str, body: str, *, platform: str = "instagram", sender: str = "contact", days_ago: float = 0) -> str:
    n[0] += 1
    zmid = f"{zcid}-in-{n[0]}"
    at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    inbox_store.upsert_conversation(space=SPACE, zcid=zcid, participant="Chris", account_id="a1", platform=platform,
                                    last_inbound_at=at)
    inbox_store.record_message(space=SPACE, zcid=zcid, zmid=zmid, direction="in", sent_by=sender, body=body)
    with state.connect() as c:
        c.execute("UPDATE inbox_messages SET created_at = ? WHERE space = ? AND zernio_message_id = ?",
                  (at, SPACE, zmid))
    return zmid


def no_reply(zcid: str, zmid: str, rules: str = "older-rules") -> str:
    """The box's own "no reply needed" mark on this message, as the drafter stored it before 10-06."""
    store.put(space=SPACE, zcid=zcid, in_reply_to=zmid, body=store.NO_REPLY_BODY, rules=rules)
    row = store.for_inbound(SPACE, zmid)
    store.dismiss(SPACE, row["id"])
    return row["id"]


def think(task, prompt, *, system=None, job_id=None, **kw):
    CALLS.append({"task": task, "job_id": job_id, "system": system})
    return ANSWER[0]


def sweep() -> dict:
    CALLS.clear()
    return draft.sweep(SPACE)


m = inbound("seed", "hi")
draft.draft_one(space=SPACE, zcid="seed", in_reply_to=m, inbound="hi", platform="instagram")
ok("a draft is asked for on the drafter's own task", seen_task == ["inbox_draft"], seen_task)
brain.think = think
inbox_store.record_message(space=SPACE, zcid="seed", zmid="seed-out", direction="out", sent_by="human", body="hey")

print("\ntest_the_instructions_lean_to_drafting")
S = draft._system("instagram")
ok("when in doubt, draft", "When in doubt, draft" in S and "Choose 3 only when you are sure no person is waiting" in S)
ok("...and never again 'a reply nobody needed is worse than no reply at all'", "worse than no reply at all" not in S)
ok("someone the owner knows is answered too, in the owner's own tone",
   "someone the owner knows (a friend, a colleague, a contact)" in S)

print("\ntest_an_old_no_reply_is_asked_again_once")
zm = inbound("chris", "Cool I can hit u up on my way back down from LA Tuesday and we can train or surf")
did = no_reply("chris", zm)
out = sweep()
row = store.for_inbound(SPACE, zm) or {}
ok("a 'no reply needed' from older instructions is asked again, and now has a draft",
   out.get("rechecked") == 1 and row.get("body") == ANSWER[0] and row.get("dismissed_at") is None, (out, row))
ok("...on the same row, waiting like any draft", row.get("id") == did
   and "chris" in {d["zcid"] for d in store.waiting(SPACE)})
ok("...on the drafter's own task", [c["task"] for c in CALLS] == ["inbox_draft"], CALLS)
ok("...and its person is no longer hidden from Who to answer first", "chris" not in store.judged_not_for_a_person(SPACE))
out = sweep()
ok("asked once: the next sweep pays for nothing", not CALLS and out.get("rechecked") == 0, (out, CALLS))

zm2 = inbound("notice", "Got it, thanks!", platform="email", sender="Jamie <jamie@friends.example>")
d2 = no_reply("notice", zm2)
ANSWER[0] = draft.NO_REPLY
out = sweep()
ok("no reply again: settled under these rules, the mark kept", (store.for_inbound(SPACE, zm2) or {}).get("body")
   == store.NO_REPLY_BODY and out.get("rechecked") == 1, out)
out = sweep()
ok("...and never asked again under them", not CALLS, CALLS)
ANSWER[0] = "Sounds good, see you Wednesday."

print("\ntest_only_what_should_be_asked_again")
zp = inbound("person-no", "Can you do Friday?")
store.put(space=SPACE, zcid="person-no", in_reply_to=zp, body="Friday works!", rules="older-rules")
store.dismiss(SPACE, store.for_inbound(SPACE, zp)["id"])
zo = inbound("old", "Want to grab lunch?", days_ago=45)
no_reply("old", zo)
za = inbound("answered", "See you then")
no_reply("answered", za)
inbox_store.record_message(space=SPACE, zcid="answered", zmid="answered-out", direction="out", sent_by="human",
                           body="Great")
with state.connect() as c:
    c.execute("UPDATE inbox_messages SET created_at = ? WHERE zernio_message_id = 'answered-out'",
              ((datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),))
zr = inbound("robot", "Your order has shipped", platform="email", sender="no-reply@shop.example")
rid = no_reply("robot", zr)
out = sweep()
ok("a draft a PERSON dismissed is never brought back", (store.for_inbound(SPACE, zp) or {}).get("dismissed_at"))
ok("one older than 30 days, or already answered, is left alone",
   (store.for_inbound(SPACE, zo) or {}).get("body") == store.NO_REPLY_BODY
   and (store.for_inbound(SPACE, za) or {}).get("body") == store.NO_REPLY_BODY)
ok("a robot is settled with no model call", not CALLS
   and store.stale_no_reply(SPACE, email_rules=draft.rules("email"), dms_rules=draft.rules("instagram")) == [],
   (CALLS, rid))

print("\ntest_a_new_person_comes_first")
zn = inbound("new", "Hey are you around this weekend?")
zq = inbound("queued", "Thanks man, let's catch up soon")
no_reply("queued", zq)
draft.per_sweep = lambda: 1
out = sweep()
ok("with room for one, the new message gets its first draft and the re-check waits",
   out.get("drafted") == 1 and out.get("rechecked") == 0 and store.for_inbound(SPACE, zn)
   and (store.for_inbound(SPACE, zq) or {}).get("body") == store.NO_REPLY_BODY, out)
out = sweep()
ok("...and the next sweep asks again", out.get("rechecked") == 1
   and (store.for_inbound(SPACE, zq) or {}).get("body") == ANSWER[0], out)

brain.think = real_think
print("\nALL LEAN-TO-DRAFTING CHECKS PASS" if not FAILS else f"\n{len(FAILS)} LEAN-TO-DRAFTING CHECK(S) FAILED")
sys.exit(1 if FAILS else 0)
