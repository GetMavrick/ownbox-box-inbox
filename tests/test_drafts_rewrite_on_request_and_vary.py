"""Written replies can be rewritten on one approval, and every draft sees how recent replies ended (2026-10-08).

Owner, 2026-10-08: "It's still writing that human approval thing every single time." Two causes, on every box:
  * each draft is its own model call and sees no other draft, so "vary the closing line" could not be kept by any one
    call; and the sent replies shown as examples said "match their wording", so every draft copied their closing line;
  * drafts a person's own AI wrote through the connector (KEEP) are never rewritten by the box, so a settings change
    never reached them.
OSDev1 ASSIGNED, the same night: "one owner-approved 'rewrite these with the current rules' (a tool ...) that rewrites
the selected or all ready drafts, KEEP included, once, under the sweep's spend cap."

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * a draft's prompt stops showing how recent replies ended, shows this conversation's own, or the examples go back
    to "match their wording";
  * a waiting draft stops saying who wrote it (the box, or a person's own AI);
  * asking to rewrite changes anything before the owner approves;
  * an approved rewrite skips a KEEP draft, rewrites one twice, outruns a new message, or spends past the bound;
  * a requested draft that is no longer waiting stays on the list.

No network, no model.

Run: python tests/test_drafts_rewrite_on_request_and_vary.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "rewrite.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"

from core import approvals, brain, cost_guard, spaces, state  # noqa: E402

state.init_db()
from core.dispatch import app  # noqa: E402,F401
from marketing.customer_voice.drafter import draft, store  # noqa: E402
from marketing.customer_voice.inbox import store as inbox_store  # noqa: E402
from marketing.customer_voice.inbox import tools as inbox_tools  # noqa: E402

FAILS: list[str] = []
SPACE = inbox_tools._space()
CALLS: list[dict] = []


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:400]}"))
    if not cond:
        FAILS.append(label)


def _think(task, prompt, *, system=None, job_id=None, **kw):
    CALLS.append({"job_id": str(job_id), "prompt": prompt})
    return "Glad to help. Happy to walk you through it on a quick call."


brain.think = _think
cost_guard.check_vendor = lambda *a, **k: None
spaces.all_spaces = lambda: [{"name": SPACE}]
draft.per_sweep = lambda: 2
n = [0]


def inbound(zcid: str, body: str) -> str:
    n[0] += 1
    zmid = f"{zcid}-in-{n[0]}"
    inbox_store.upsert_conversation(space=SPACE, zcid=zcid, participant=zcid.title(), account_id="a1",
                                    platform="email", last_inbound_at="2026-10-07T09:00:00Z")
    inbox_store.record_message(space=SPACE, zcid=zcid, zmid=zmid, direction="in", sent_by=f"{zcid}@client.com",
                               body=body)
    return zmid


def _rules(draft_id: str):
    with state.connect() as c:
        r = c.execute("SELECT rules FROM inbox_draft_rules WHERE space = ? AND draft_id = ?", (SPACE, draft_id)).fetchone()
    return r["rules"] if r else None


def sweep() -> dict:
    CALLS.clear()
    return draft.sweep(SPACE)


print("test_every_draft_sees_how_recent_replies_ended")
a = inbound("ana", "Can you quote a roof?")
store.put(space=SPACE, zcid="ana", in_reply_to=a, body="Sure, Tuesday works. I build AI machines with human approval "
          "for consequential actions.", rules=store.KEEP)
b = inbound("ben", "Do you do gutters?")
store.put(space=SPACE, zcid="ben", in_reply_to=b, body="We do. Thanks!", rules=draft.rules("email"))
ends = store.recent_endings(SPACE, exclude_zcid="ben")
ok("the last sentence of each recent reply, a short sign-off passed over for the line before it",
   "I build AI machines with human approval for consequential actions." in ends and "We do." not in ends, ends)
ok("...never this conversation's own", all("We do" not in e for e in store.recent_endings(SPACE, exclude_zcid="ben")))
z = inbound("zed", "Still open?")
store.put(space=SPACE, zcid="zed", in_reply_to=z, body="Yes. A discarded closing line nobody ever read.",
          rules=draft.rules("email"))
store.dismiss(SPACE, store.for_inbound(SPACE, z)["id"])
ok("...nor a draft that never went out", all("discarded closing line" not in e for e in store.recent_endings(SPACE)),
   store.recent_endings(SPACE))
p = draft._prompt(space=SPACE, zcid="cara", inbound="Are you open Saturday?", history=[])
ok("a new draft's prompt shows those endings, and asks for something different",
   "ended like this" in p and "human approval for consequential actions" in p and "say something different" in p, p)
with state.connect() as c:                     # one reply the business really sent, kept as an example
    c.execute("INSERT INTO inbox_draft_lessons (id, space, draft_id, zernio_conversation_id, asked, draft_body, "
              "sent_body, edited, created_at) VALUES ('l1', ?, 'd-old', 'old', 'Open today?', 'Yes.', "
              "'Yes, 9 to 5. P.S. every action gets a human approval first.', 1, '2026-10-06T10:00:00')", (SPACE,))
p = draft._prompt(space=SPACE, zcid="cara", inbound="Are you open Saturday?", history=[])
ok("examples teach how the business writes, never their sentences or their closing line",
   "--- examples ---" in p and "Never copy a sentence from them" in p and "their wording" not in p, p[:700])
ok("...and a sent reply's ending is among the endings to steer away from",
   "every action gets a human approval first." in p.split("--- end of examples ---")[1], p)

print("\ntest_a_waiting_draft_says_who_wrote_it")
got = {d["who"]: d["written_by"] for d in inbox_tools.waiting(limit=10)["drafts_ready"]}
ok("one the box wrote, and one a person's own AI wrote", got.get("Ben") == "your Ownbox"
   and got.get("Ana") == "your own AI, through the connector", got)

print("\ntest_rewriting_on_request_waits_for_the_owner")
before = {d["id"]: d["body"] for d in store.waiting(SPACE)}
asked = inbox_tools.propose_rewrite_drafts(drafts="all")
ok("asking is a proposal, and the card says the owner's own AI's replies are rewritten too",
   asked.get("asked") is True and "written by your own AI" in str(approvals.get(asked["approval"])["detail"]), asked)
ok("...and nothing changed by asking", {d["id"]: d["body"] for d in store.waiting(SPACE)} == before
   and store.requested_rewrites(SPACE) == [])
ok("asking for nothing asks nothing", inbox_tools.propose_rewrite_drafts(drafts="nope-1").get("asked") is False)

print("\ntest_an_approved_rewrite_reaches_every_draft_once")
r = approvals.decide(asked["approval"], True, by="usr_owner")
ok("approved: both are on the list", r.get("status") == "done" and len(store.requested_rewrites(SPACE)) == 2, r)
inbound("dana", "Hi, are you around this week?")
out = sweep()
ok("a new message still comes first, and the bound holds", out.get("drafted") == 1 and out.get("rewritten") == 1
   and len(CALLS) == 2, (out, [c["job_id"] for c in CALLS]))
out = sweep()
ok("the next sweep finishes the list", out.get("rewritten") == 1 and store.requested_rewrites(SPACE) == [], out)
ana = store.for_inbound(SPACE, a)
ok("the draft the person's own AI wrote is rewritten too, once, and is the box's now",
   "quick call" in ana["body"] and store.KEEP != _rules(ana["id"]) == draft.rules("email"), ana)
out = sweep()
ok("...and nothing is rewritten twice", out.get("rewritten") == 0 and not CALLS, out)

print("\ntest_a_requested_draft_no_longer_waiting_leaves_the_list")
e = inbound("eli", "Price for a new door?")
store.put(space=SPACE, zcid="eli", in_reply_to=e, body="About $900.", rules=draft.rules("email"))
did = store.for_inbound(SPACE, e)["id"]
store.request_rewrites(SPACE, [did])
store.dismiss(SPACE, did)
sweep()
ok("discarded before its turn: taken off the list, and no call paid for it",
   store.requested_rewrites(SPACE) == [] and not any(did in c["job_id"] for c in CALLS), CALLS)


print("\nALL REWRITE-ON-REQUEST CHECKS PASS" if not FAILS else f"\n{len(FAILS)} REWRITE-ON-REQUEST CHECK(S) FAILED")
sys.exit(1 if FAILS else 0)
