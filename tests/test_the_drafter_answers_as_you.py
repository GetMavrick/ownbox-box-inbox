"""The drafter answers as the owner when he is the applicant, the buyer or the one who opened the ticket.

Owner, 2026-10-04: "Fix this: The inbox drafter fix, so it answers as you when you started the thread, e.g. your job
applications." On 10-02 a draft answered a job-application confirmation as the employer: every inbound line was
labelled "Customer", so the recruiter writing about HIS application read as a customer of his. Measured here, with
only the model's words stood in for:
  * nobody is called the customer: the transcript says "Them" and "You (the business)"
  * the prompt says who wrote first, and that them writing first does not make them a customer
  * case 2 makes the owner the applicant, the buyer, the ticket-opener, in the first person, and never selling
  * the Sales style is for customers only, never case 2
  * the rest stands: one model call, the transcript still quoted, the examples still the business's own replies

Run: python tests/test_the_drafter_answers_as_you.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "as_you.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"

from core import brain, cost_guard, state  # noqa: E402

state.init_db()
from core.dispatch import app  # noqa: E402,F401
from marketing.customer_voice import app as inbox_app  # noqa: E402
from marketing.customer_voice.drafter import draft, store as drafts  # noqa: E402
from marketing.customer_voice.inbox import store  # noqa: E402

FAILS: list[str] = []
SPACE = inbox_app._space()


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:400]}"))
    if not cond:
        FAILS.append(label)


calls = []
cost_guard.check_vendor = lambda *a, **k: None
brain.think = lambda **kw: calls.append(kw) or "Tuesday at ten works well for me, thank you."
n = [0]


def thread(*msgs) -> tuple[str, str, str]:
    """A conversation from (direction, text) pairs, oldest first. -> (zcid, last inbound id, its text)."""
    n[0] += 1
    zcid = f"conv-{n[0]}"
    store.upsert_conversation(space=SPACE, zcid=zcid, platform="email", participant="Recruiting at Acme",
                              last_inbound_at="2026-10-04T09:00:00Z", account_id="acc-1")
    last = ("", "")
    for i, (direction, text) in enumerate(msgs):
        mid = f"m-{n[0]}-{i}"
        store.record_message(space=SPACE, zcid=zcid, zmid=mid, direction=direction,
                             sent_by="contact" if direction == "in" else "owner", body=text)
        if direction == "in":
            last = (mid, text)
    return zcid, last[0], last[1]


def drafted(*msgs) -> dict:
    zcid, mid, text = thread(*msgs)
    calls.clear()
    draft.draft_one(space=SPACE, zcid=zcid, in_reply_to=mid, inbound=text,
                    history=drafts.history_for(SPACE, zcid)[:-1], platform="email")
    return calls[-1] if calls else {}


print("\nA reply to his own job application\n")
c = drafted(("in", "Thank you for applying to the Operations Lead role at Acme. We'd like to interview you. "
                   "Are you free Tuesday at 10am?"))
p, sysm = c.get("prompt", ""), c.get("system", "")
ok("nobody is called the customer", "Customer:" not in p and "Them: Thank you for applying" in p, p[-400:])
ok("the prompt says they wrote first, and that it does not make them a customer",
   "They wrote first" in p and "does not make them a customer" in p and "by applying" in p, p[:400])
ok("case 2 makes the owner the applicant, in the first person", "a job the owner applied for" in sysm
   and "Reply AS THE OWNER" in sysm and "the applicant answers the recruiter" in sysm, sysm)
ok("...whoever wrote first, with the signs named", "whoever wrote first" in sysm
   and "thank you for applying" in sysm and "your order" in sysm)
ok("...never as the business they deal with, never selling", "Never write as the business they are dealing with"
   in sysm and "never sell" in sysm)
ok("the reply is the owner's", p.rstrip().endswith("Write the business's next reply, as the business's owner."))
ok("still one model call", len(calls) == 1)

print("\nA thread he started\n")
c = drafted(("out", "Hi, I placed order 4471 last week and it hasn't arrived."),
            ("in", "Sorry about that! Can you confirm the delivery address?"))
p = c.get("prompt", "")
ok("the prompt says he wrote first", "You, the business, wrote first in this conversation." in p, p[:300])
ok("his own lines are his", "You (the business): Hi, I placed order 4471" in p, p)

print("\nA customer writing to his business\n")
c = drafted(("in", "Do you do Botox on Saturdays?"))
ok("still case 1, unchanged", "1. SOMEONE IS ASKING THE BUSINESS SOMETHING" in c.get("system", "")
   and "Them: Do you do Botox on Saturdays?" in c.get("prompt", ""))

print("\nThe Sales style never sells to a recruiter\n")
from core import box_settings  # noqa: E402

box_settings.put("inbox", "reply_style.email", "sales", set_by="owner")
c = drafted(("in", "Thanks for applying! When can you start?"))
sm = c.get("system", "")
ok("Strong sales pushes prospects only; a recruiter is anyone else", "- Prospect: answer it first, then always push"
   in sm and "ANYONE ELSE: case 2" in sm, sm[-500:])
ok("...and a recruiter gets only the one light sentence, written as the applicant (owner, 10-04: everyone, subtly)",
   "never pushy. In case 2 it never changes who you are writing as: the applicant stays the applicant" in sm
   and "- Anyone else: the light sentence, never a hard sell." in sm, sm[-500:])
ok("a thread with no messages stored says nothing about who started it",
   drafts.started_by(SPACE, "no-such-conversation") == "")

print("\nALL ANSWERS-AS-YOU CHECKS PASS" if not FAILS else f"\n{len(FAILS)} ANSWERS-AS-YOU CHECK(S) FAILED")
sys.exit(1 if FAILS else 0)
