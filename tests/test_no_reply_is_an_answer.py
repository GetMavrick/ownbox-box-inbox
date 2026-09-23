"""Deciding a message needs no reply is an answer, and it is recorded like one.

MEASURED ON THE OWNER'S BOX, 2026-09-22, in the first two drafts after the robot filter went on.
The targeting was right — both went to real senders, neither to a robot — and both said:

    "I think this message may have been sent to us by mistake."

One was a reply on HIS OWN DigitalOcean support ticket; the other a residents' notice. Neither
was a mistake. The prompt only knew one shape — a stranger enquiring of a business — so anything
that was not that read as a wrong number.

So the model is now told the three shapes his inbox actually contains, and the third one is
allowed to produce NOTHING: an announcement, a receipt, a newsletter, an automated report.

AND THE DECISION IS RECORDED, WHICH IS THE HALF THAT MATTERS HERE. Returning None would leave
the row with no draft, `needs_a_draft` would hand it back every two minutes, and the box would
pay for the same refusal forever — this morning's seven-hour head-block (#1436) wearing a third
face. The row is stored and dismissed in one step: the decision is on the record, the Drafts tab
never shows it, and the sweep never sees it again.

Run: python tests/test_no_reply_is_an_answer.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "noreply.db")
os.environ.pop("ZERNIO_API_KEY", None)

from core import cost_guard, state                                # noqa: E402
from core import spaces as _spaces                                # noqa: E402

state.init_db()
_spaces.space_by_name = lambda name, allow_default_alias=True: {"name": name, "key": "k"}

from marketing.customer_voice.drafter import draft, store as ds   # noqa: E402
from marketing.customer_voice.inbox import store as inbox         # noqa: E402

SPACE = "acme"
cost_guard.check_vendor = lambda v, u=1, now=None: None
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def arrived(zcid, mid, body, who="Sam"):
    inbox.upsert_conversation(space=SPACE, zcid=zcid, participant=who, account_id="acct-1")
    inbox.record_message(space=SPACE, zcid=zcid, zmid=mid, direction="in", sent_by=who, body=body)


def says(answer):
    draft.brain.think = lambda **kw: answer


# ── 1. the prompt tells the model it may decline ────────────────────────────────────────────
print("test_the_model_is_told_the_three_shapes_and_how_to_decline")
S = draft.SYSTEM
ok("it names the case where the business itself started the thread", "BUSINESS ITSELF STARTED" in S)
ok("...and forbids the sentence the owner actually read",
   "wrong address" in S and "sent this by mistake" in S, S[-300:])
ok("it names the case where nobody needs an answer", "NOBODY NEEDS AN ANSWER" in S)
ok("...and gives an exact token for it", draft.NO_REPLY in S)
ok("...and says choosing it is a real answer, not a failure", "costs the business nothing" in S)


# ── 2. a decision not to answer ─────────────────────────────────────────────────────────────
print("test_no_reply_writes_no_draft_and_is_never_asked_again")
arrived("c-notice", "m-notice", "Your monthly statement is ready.", "Statements")
says(draft.NO_REPLY)
out = draft.draft_one(space=SPACE, zcid="c-notice", in_reply_to="m-notice",
                      inbound="Your monthly statement is ready.")
ok("no draft text comes back", out is None, repr(out))
ok("...nothing is offered on the Drafts tab",
   "c-notice" not in [r["zcid"] for r in ds.waiting(SPACE, limit=50)])
ok("...and the sweep never sees that row again — the head-block does not return",
   "c-notice" not in [r["zcid"] for r in ds.needs_a_draft(SPACE, limit=50)],
   str([r["zcid"] for r in ds.needs_a_draft(SPACE, limit=50)]))
ok("...but the decision IS on the record, not thrown away",
   ds.for_inbound(SPACE, "m-notice") is not None)

print("test_the_sentinel_is_matched_exactly_not_loosely")
arrived("c-talks", "m-talks", "Should we reply?", "Dana")
says("We could send NO_REPLY_NEEDED to them, but let's answer properly instead.")
out = draft.draft_one(space=SPACE, zcid="c-talks", in_reply_to="m-talks", inbound="Should we reply?")
ok("a reply that merely MENTIONS the token is still a reply", bool(out), repr(out))
ok("...and it is offered", "c-talks" in [r["zcid"] for r in ds.waiting(SPACE, limit=50)])

print("test_a_real_answer_is_untouched")
arrived("c-ask", "m-ask", "Do you open on Sundays?", "Priya")
says("Yes — Sundays, nine to four.")
out = draft.draft_one(space=SPACE, zcid="c-ask", in_reply_to="m-ask", inbound="Do you open on Sundays?")
ok("the draft is stored as written", out == "Yes — Sundays, nine to four.", repr(out))
ok("...and waits on the tab", "c-ask" in [r["zcid"] for r in ds.waiting(SPACE, limit=50)])

print("test_a_flood_of_notices_cannot_starve_one_real_question")
# THE REGRESSION IN ITS REAL SHAPE. Twenty newer notices, all declined, over one older question.
# Without recording the decision each would return forever and the question would never be reached.
for i in range(20):
    arrived(f"c-n{i}", f"m-n{i}", "Your receipt.", f"Receipts {i}")
says(draft.NO_REPLY)
for i in range(20):
    draft.draft_one(space=SPACE, zcid=f"c-n{i}", in_reply_to=f"m-n{i}", inbound="Your receipt.")
left = [r["zcid"] for r in ds.needs_a_draft(SPACE, limit=50)]
ok("every declined notice is out of the queue", not [z for z in left if z.startswith("c-n")], str(left))

print("\nFAILED" if _failed else "\nALL PASS")
sys.exit(1 if _failed else 0)
