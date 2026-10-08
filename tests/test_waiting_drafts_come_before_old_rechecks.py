"""After a rules change, the drafts waiting on a person are rewritten before old "no reply" calls are re-asked, and no
row that keeps failing can hold a queue (owner, 2026-10-08).

Owner, 2026-10-08, the day of an investor demo, an hour after #2054 (a varied closing line) went live: "Please make
sure that 2054 is deployed. It's still writing that human approval thing every single time." It was deployed. The
drafts waiting on him were not being rewritten: a style change made every "no reply needed" call of the last 30 days
look stale too, and their re-checks came first and took every slot of the spend bound, sweep after sweep.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * a new message stops coming first;
  * with room for one call, an old "no reply" re-check is asked before a waiting draft is rewritten;
  * the order costs more calls than the spend bound allows;
  * a rewrite or a draft that fails every time holds its queue: three failures in a row must set it aside so the
    next one is reached, while a single failure is retried on the very next sweep (speed to a reply comes first);
  * a row set aside is never tried again.

No network, no model.

Run: python tests/test_waiting_drafts_come_before_old_rechecks.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "order.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"

from core import box_settings, brain, cost_guard, spaces, state  # noqa: E402

state.init_db()
from core.dispatch import app  # noqa: E402,F401
from marketing.customer_voice.drafter import draft, store  # noqa: E402
from marketing.customer_voice.inbox import store as inbox_store  # noqa: E402
from marketing.customer_voice.inbox import tools as inbox_tools  # noqa: E402

FAILS: list[str] = []
SPACE = inbox_tools._space()
CALLS: list[str] = []
SILENT: set = set()                               # job ids whose call the model will not answer


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:400]}"))
    if not cond:
        FAILS.append(label)


def _think(task, prompt, *, system=None, job_id=None, **kw):
    CALLS.append(str(job_id))
    if any(s in str(job_id) for s in SILENT):
        return ""
    return "Happy to help, here are the details."


brain.think = _think
cost_guard.check_vendor = lambda *a, **k: None
spaces.all_spaces = lambda: [{"name": SPACE}]
draft.per_sweep = lambda: 1
n = [0]


def inbound(zcid: str, body: str) -> str:
    n[0] += 1
    zmid = f"{zcid}-in-{n[0]}"
    inbox_store.upsert_conversation(space=SPACE, zcid=zcid, participant="Dana", account_id="a1", platform="email",
                                    last_inbound_at="2026-10-07T09:00:00Z")
    inbox_store.record_message(space=SPACE, zcid=zcid, zmid=zmid, direction="in", sent_by="dana@client.com",
                               body=body)
    return zmid


def sweep() -> dict:
    CALLS.clear()
    return draft.sweep(SPACE)


# Under yesterday's rules: one draft waiting on the owner, one "no reply needed" call.
box_settings.put("inbox", "reply_style.email", "service", set_by="t")
w1 = inbound("z-wait", "Can you quote a roof on Elm St?")
store.put(space=SPACE, zcid="z-wait", in_reply_to=w1, body="Sure. P.S. we do it with human approval.",
          rules=draft.rules("email"))
q1 = inbound("z-quiet", "Thanks, all good!")
store.put(space=SPACE, zcid="z-quiet", in_reply_to=q1, body=store.NO_REPLY_BODY, rules=draft.rules("email"))
store.dismiss(SPACE, (store.for_inbound(SPACE, q1) or {})["id"])
box_settings.put("inbox", "reply_style.email", "sales", set_by="t")         # today's rules differ

print("test_a_waiting_draft_comes_before_an_old_no_reply_call")
out = sweep()
ok("with room for one call, the draft waiting on a person is rewritten, and the re-check waits",
   out.get("rewritten") == 1 and out.get("rechecked") == 0 and len(CALLS) == 1, (out, CALLS))
out = sweep()
ok("...and the next sweep asks the old call again", out.get("rechecked") == 1 and len(CALLS) == 1, (out, CALLS))

print("\ntest_a_new_message_still_comes_first")
w2 = inbound("z-wait2", "Do you do gutters too?")
store.put(space=SPACE, zcid="z-wait2", in_reply_to=w2, body="Yes.", rules="yesterday")
inbound("z-new", "Hi, are you open Saturday?")
out = sweep()
ok("a new message takes the one call; nothing else is paid for", out.get("drafted") == 1
   and out.get("rewritten") == 0 and len(CALLS) == 1, (out, CALLS))

print("\ntest_a_rewrite_that_keeps_failing_is_set_aside")
w3 = inbound("z-wait3", "What areas do you cover?")
store.put(space=SPACE, zcid="z-wait3", in_reply_to=w3, body="Most of town.", rules="yesterday")
stuck = (store.for_inbound(SPACE, w2) or {})["id"]                        # the oldest stale draft: z-wait2
SILENT.add(f"redraft:{SPACE}:{stuck}")
out = sweep()
ok("one failure: tried again on the very next sweep", out.get("rewritten") == 0 and CALLS
   and stuck in CALLS[0], (out, CALLS))
out = sweep()
ok("...and again", out.get("rewritten") == 0 and CALLS and stuck in CALLS[0], (out, CALLS))
out = sweep()
ok("...a third failure in a row sets it aside", out.get("rewritten") == 0 and CALLS and stuck in CALLS[0], (out, CALLS))
out = sweep()
ok("so the next draft behind it is reached", out.get("rewritten") == 1 and CALLS and stuck not in CALLS[0],
   (out, CALLS))
SILENT.clear()
for _ in range(draft._REST_SWEEPS):
    sweep()
ok("...and after its rest it is tried again, and rewritten", (store.for_inbound(SPACE, w2) or {}).get("body")
   == "Happy to help, here are the details.", store.for_inbound(SPACE, w2))

print("\ntest_the_last_sweep_is_said_where_anyone_can_read_it")
w4 = inbound("z-wait4", "Is Monday free?")
store.put(space=SPACE, zcid="z-wait4", in_reply_to=w4, body="Yes.", rules="yesterday")
inbound("z-new2", "Do you take cards?")
sweep()
last = box_settings.get("inbox", "drafter.last_sweep", default={}) or {}
ok("the sweep records what it did and what is still to rewrite, with no log needed",
   last.get("drafted") == 1 and last.get("still_to_rewrite") == 1 and last.get("at"), last)
st = inbox_tools.status()
ok("...and inbox.status carries it, in words too",
   st.get("drafter_last_sweep", {}).get("still_to_rewrite") == 1
   and "1 written reply is being rewritten under your latest settings" in inbox_tools._render_status(st),
   inbox_tools._render_status(st))

print("\nALL ORDER CHECKS PASS" if not FAILS else f"\n{len(FAILS)} ORDER CHECK(S) FAILED")
sys.exit(1 if FAILS else 0)
