"""Drafts still waiting are rewritten when the drafter changes (owner, 2026-10-04: an "absolute necessity").

OSDev1, 2026-10-04 (daily check): a waiting reply on a live box was drafted from the recruiter's side; it was
written before #1899 shipped and stayed wrong on the screen. Now every draft the box writes records a fingerprint
of the instructions that wrote it (draft.rules(platform): the instructions, the reply style, the cold-pitch
setting), and the sweep rewrites, in place, the waiting drafts whose fingerprint no longer matches. Measured here,
with only the model stood in for:
  * a fresh draft records its rules; a second sweep pays for nothing
  * the owner changes the reply style: the waiting draft is rewritten on the same row, once
  * a draft from before the box kept rules is rewritten once
  * never a dismissed draft, never one already answered, never one the owner's own AI wrote
  * a rewrite that chooses NO_REPLY_NEEDED dismisses the draft
  * new messages are drafted before stale drafts are rewritten, under the same spend bound

Run: python tests/test_waiting_drafts_follow_the_drafter.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "follow.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"

from core import box_settings, brain, cost_guard, spaces, state  # noqa: E402

state.init_db()
from core.dispatch import app  # noqa: E402,F401
from marketing.customer_voice.drafter import draft, store  # noqa: E402
from marketing.customer_voice.inbox import store as inbox_store  # noqa: E402
from marketing.customer_voice.inbox import tools as inbox_tools  # noqa: E402

FAILS: list[str] = []
SPACE = inbox_tools._space()
REPLY = ["We are open 10 til 4 on Sunday."]
CALLS: list[dict] = []


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:400]}"))
    if not cond:
        FAILS.append(label)


def _think(task, prompt, *, system=None, job_id=None, **kw):
    CALLS.append({"task": task, "job_id": job_id, "system": system})
    return REPLY[0]


brain.think = _think
cost_guard.check_vendor = lambda *a, **k: None
spaces.all_spaces = lambda: [{"name": SPACE}]
n = [0]


def inbound(zcid: str, body: str, platform: str = "instagram") -> None:
    n[0] += 1
    inbox_store.upsert_conversation(space=SPACE, zcid=zcid, participant="Dana", account_id="a1", platform=platform,
                                    last_inbound_at="2026-10-04T09:00:00Z")
    inbox_store.record_message(space=SPACE, zcid=zcid, zmid=f"{zcid}-in-{n[0]}", direction="in", sent_by="contact",
                               body=body)


def rules_of(draft_id: str):
    with state.connect() as c:
        r = c.execute("SELECT rules FROM inbox_draft_rules WHERE space = ? AND draft_id = ?",
                      (SPACE, draft_id)).fetchone()
    return r["rules"] if r else None


def sweep() -> dict:
    CALLS.clear()
    return draft.sweep(SPACE)


print("\nA fresh draft records its rules\n")
inbound("c1", "are you open sunday?")
out = sweep()
d1 = store.latest_for(SPACE, "c1") or {}
ok("drafted once, nothing rewritten", out.get("drafted") == 1 and out.get("rewritten") == 0, out)
ok("...with the rules that wrote it", rules_of(d1.get("id")) == draft.rules("instagram") and len(rules_of(d1["id"])) == 12,
   rules_of(d1.get("id")))
out = sweep()
ok("a second sweep pays for nothing", out == {"status": "ok", "drafted": 0, "considered": 0, "rechecked": 0,
                                             "rewritten": 0}
   and not CALLS, out)

print("\nThe owner changes the reply style\n")
box_settings.put("inbox", "reply_style.dms", "sales", set_by="owner")
ok("the rules changed for DMs, not for email", draft.rules("instagram") != rules_of(d1["id"])
   and draft.rules("email") != draft.rules("instagram"))
REPLY[0] = "We are open 10 til 4 on Sunday. Shall I book you in?"
out = sweep()
d1b = store.latest_for(SPACE, "c1") or {}
ok("the waiting draft is rewritten, in place", out.get("rewritten") == 1 and d1b.get("id") == d1.get("id")
   and d1b.get("body") == REPLY[0], (out, d1b))
ok("...one model call, in the new style, on its own job id", len(CALLS) == 1 and "STRONG SALES" in CALLS[0]["system"]
   and CALLS[0]["job_id"].startswith(f"redraft:{SPACE}:{d1['id']}:"), CALLS)
ok("...and its rules are now the box's", rules_of(d1["id"]) == draft.rules("instagram"))
ok("...so the next sweep pays for nothing", sweep().get("rewritten") == 0 and not CALLS)
ok("the screen still shows one draft for the conversation",
   [d["id"] for d in store.waiting(SPACE) if d["zcid"] == "c1"] == [d1["id"]])

print("\nA draft from before the box kept rules\n")
with state.connect() as c:
    c.execute("DELETE FROM inbox_draft_rules WHERE space = ? AND draft_id = ?", (SPACE, d1["id"]))
out = sweep()
ok("is rewritten once", out.get("rewritten") == 1 and rules_of(d1["id"]) == draft.rules("instagram"), out)
ok("...and only once", sweep().get("rewritten") == 0)

print("\nNever a dismissed draft, never one answered, never the AI's own\n")
inbox_store.record_message(space=SPACE, zcid="c1", zmid="c1-out", direction="out", sent_by="human", body="Yes we are.")
inbound("c2", "do you do gutters?")
inbound("c3", "price for a roof wash?")
inbound("c8", "which areas do you cover?")
ok("three new messages, three drafts", sweep().get("drafted") == 3)
d2, d3, d8 = store.latest_for(SPACE, "c2"), store.latest_for(SPACE, "c3"), store.latest_for(SPACE, "c8")
store.dismiss(SPACE, d2["id"])
inbox_store.record_message(space=SPACE, zcid="c3", zmid="c3-out", direction="out", sent_by="human", body="Yes, $150.")
inbound("c4", "any Saturday slots?")
mine = inbox_tools.draft_reply(id="c4", body="Saturday 9 works, Dana.")
ok("the owner's AI wrote c4's draft, marked kept", mine.get("written") is True
   and rules_of((store.latest_for(SPACE, "c4") or {}).get("id")) == store.KEEP, mine)
box_settings.put("inbox", "reply_style.dms", "subtle", set_by="owner")
with state.connect() as c:
    c.execute("DELETE FROM inbox_draft_rules WHERE space = ? AND draft_id IN (?, ?)", (SPACE, d2["id"], d3["id"]))
REPLY[0] = "We cover the whole county."
out = sweep()
ok("only c8 is rewritten: not c1 (answered), c2 (dismissed), c3 (answered) or c4 (the AI's)",
   out.get("rewritten") == 1 and (store.latest_for(SPACE, "c8") or {}).get("body") == REPLY[0]
   and (store.latest_for(SPACE, "c4") or {}).get("body") == "Saturday 9 works, Dana."
   and (store.latest_for(SPACE, "c3") or {}).get("body") == "We are open 10 til 4 on Sunday. Shall I book you in?"
   and (store.latest_for(SPACE, "c1") or {}).get("body") == "We are open 10 til 4 on Sunday. Shall I book you in?"
   and store.latest_for(SPACE, "c2") is None, out)
inbox_store.record_message(space=SPACE, zcid="c8", zmid="c8-out", direction="out", sent_by="human", body="Whole county.")

print("\nA rewrite that chooses no reply dismisses the draft\n")
inbound("c5", "Thanks, see you Tuesday!")
sweep()
d5 = store.latest_for(SPACE, "c5")
ok("drafted first", bool(d5))
box_settings.put("inbox", "reply_style.dms", "service", set_by="owner")
REPLY[0] = "NO_REPLY_NEEDED"
out = sweep()
ok("the rewrite dismissed it, and marked its rules so it is never asked again", out.get("rewritten") == 1
   and store.latest_for(SPACE, "c5") is None and rules_of(d5["id"]) == draft.rules("instagram"), out)
REPLY[0] = "Happy to help."
ok("...and the next sweep pays for nothing", sweep().get("rewritten") == 0 and not CALLS)

print("\nNew messages first, under the one spend bound\n")
inbound("c7", "do you take cards?")
ok("drafted under today's rules", sweep().get("drafted") == 1)
box_settings.put("inbox", "reply_style.dms", "sales", set_by="owner")      # c7 is stale now
inbound("c6", "what are your hours?")
_per = draft.per_sweep
draft.per_sweep = lambda: 1
out = sweep()
ok("with room for one call, the new message is drafted and the stale draft waits",
   out.get("drafted") == 1 and out.get("rewritten") == 0 and len(CALLS) == 1, out)
out = sweep()
ok("...and the next sweep rewrites it", out.get("drafted") == 0 and out.get("rewritten") == 1
   and CALLS[0]["job_id"].startswith("redraft:"), out)
draft.per_sweep = _per
draft.enabled = lambda: False
ok("drafting off: nothing is rewritten either", sweep() == {"status": "off", "drafted": 0})

print("\nALL FOLLOW-THE-DRAFTER CHECKS PASS" if not FAILS else f"\n{len(FAILS)} FOLLOW-THE-DRAFTER CHECK(S) FAILED")
sys.exit(1 if FAILS else 0)
