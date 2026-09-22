"""The box learns how this business replies from what its people actually send.

OWNER, 2026-09-22, on drafting: "that's one of the killer features for the unified inbox. The
big time-saver. Even Gmail doesn't have that." The way that stays true after launch is that the
drafts get better with use — and the only teacher that costs nobody anything is the send button.
Every draft sent unedited says "that was right"; every draft edited before sending is a diff,
the style guide nobody had to write.

WHAT THIS MEASURES. (1) At send time, if the box had drafted a reply to the message being
answered, the pair (draft, sent) is kept, with `edited` telling them apart — and only then: a
reply with no draft behind it, or a draft against an OLDER message, teaches nothing. (2) The
drafter shows the model what was SENT, not what it drafted, and never another space's replies.
(3) Bounded: a box that has sent a thousand replies shows a handful, clipped. (4) The send is
never hostage to the lesson: a raising `learn` leaves the reply's success untouched.

Run: python tests/test_the_box_learns_how_you_reply.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "learn.db")
os.environ.pop("ZERNIO_API_KEY", None)

from core.vendors import zernio                                      # noqa: E402


class FakeInbox:
    def __init__(self):
        self.sent = []

    def send(self, cid, account_id, text, *, tag=None):
        self.sent.append((cid, text))
        return {"message_id": f"out-{len(self.sent)}"}


class FakeScoped:
    def __init__(self, inbox):
        self.inbox = inbox


INBOX = FakeInbox()
zernio.client = lambda space: FakeScoped(INBOX)

from core import brain, cost_guard, state                            # noqa: E402
from core import spaces as _spaces                                   # noqa: E402

state.init_db()
_spaces.space_by_name = lambda name, allow_default_alias=True: {"name": name, "key": "k"}

from marketing.customer_voice.drafter import draft, store as drafts  # noqa: E402
from marketing.customer_voice.inbox import reply, store as inbox     # noqa: E402

U = state.add_user("dana@example.com", name="Dana")["id"]
SPACE = "acme"
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def conv(zcid, *, space=SPACE, asked="Do you open on Sundays?", mid=None):
    inbox.upsert_conversation(space=space, zcid=zcid, participant="Sam", account_id="acct-1")
    mid = mid or f"m-{zcid}"
    inbox.record_message(space=space, zcid=zcid, zmid=mid, direction="in", sent_by="Sam", body=asked)
    return mid


def send(zcid, text, *, space=SPACE, n=[0]):
    n[0] += 1
    return reply.send_reply(space=space, zcid=zcid, text=text, user_id=U, nonce=f"n{n[0]}")


cost_guard.check_vendor = lambda v, u=1, now=None: None

# ── 1. what gets learned, and when ──────────────────────────────────────────────────────────
print("test_an_edited_send_is_a_lesson_and_an_unedited_one_is_a_confirmation")
m = conv("c-edit")
drafts.put(space=SPACE, zcid="c-edit", in_reply_to=m, body="Yes, we are open Sundays 10 to 4.")
send("c-edit", "Yep! Sundays 10–4, come on by.")
L = drafts.lessons(SPACE, limit=10)
ok("one lesson", len(L) == 1, str(len(L)))
ok("...marked edited", L and L[0]["edited"] == 1, str(L))
ok("...carrying what was SENT", L and L[0]["sent_body"] == "Yep! Sundays 10–4, come on by.")
ok("...and what was asked", L and L[0]["asked"] == "Do you open on Sundays?", str(L and L[0]["asked"]))

m = conv("c-same", asked="Do you take cards?")
drafts.put(space=SPACE, zcid="c-same", in_reply_to=m, body="We do — all major cards.")
send("c-same", "  We do — all major cards.  ")            # the Drafts tab: sent as written
L = {r["asked"]: r for r in drafts.lessons(SPACE, limit=10)}
ok("an unedited send is kept as a confirmation, not an edit",
   L["Do you take cards?"]["edited"] == 0, str(L.get("Do you take cards?")))

print("test_a_reply_with_no_draft_behind_it_teaches_nothing")
conv("c-nodraft", asked="Are you hiring?")
before = len(drafts.lessons(SPACE, limit=100))
send("c-nodraft", "Not right now, but keep an eye on our page.")
ok("no lesson without a draft", len(drafts.lessons(SPACE, limit=100)) == before)

print("test_a_draft_against_an_older_message_is_not_paired")
# THE PERSON WAS NOT ANSWERING THAT. The box drafted for message 1; the customer wrote again;
# the person answered message 2 by hand. Pairing the reply with the draft for message 1 would
# teach the model that "what colours?" is answered with an opening-hours reply.
m1 = conv("c-stale", asked="What time do you close?")
drafts.put(space=SPACE, zcid="c-stale", in_reply_to=m1, body="We close at six.")
conv("c-stale", asked="And what colours do you have?", mid="m-c-stale-2")
before = len(drafts.lessons(SPACE, limit=100))
send("c-stale", "Blue, green and black at the moment.")
ok("no lesson from a stale draft", len(drafts.lessons(SPACE, limit=100)) == before)

print("test_a_dismissed_draft_followed_by_a_hand_written_reply_is_the_best_lesson")
m = conv("c-dismissed", asked="Can I bring my dog?")
drafts.put(space=SPACE, zcid="c-dismissed", in_reply_to=m, body="Unfortunately we cannot accommodate pets.")
drafts.dismiss(SPACE, drafts.for_inbound(SPACE, m)["id"])
send("c-dismissed", "Of course — dogs are very welcome, we keep a water bowl by the door.")
L = {r["asked"]: r for r in drafts.lessons(SPACE, limit=100)}
ok("the pair is kept even though the draft was dismissed",
   "Can I bring my dog?" in L and L["Can I bring my dog?"]["edited"] == 1)

print("test_one_lesson_per_draft")
send("c-edit", "Also — parking is free on Sundays.")   # a second send on the same thread
ok("a second send on the same draft does not weigh it twice",
   sum(1 for r in drafts.lessons(SPACE, limit=100) if r["asked"] == "Do you open on Sundays?") == 1)

# ── 2. what the drafter shows the model ─────────────────────────────────────────────────────
print("test_the_drafter_shows_what_was_sent_never_what_it_drafted")
SEEN = {}


def capture(**kw):
    SEEN.update(kw)
    return "Sure."


brain.think = capture
m = conv("c-new", asked="Do you open on bank holidays?")
draft.draft_one(space=SPACE, zcid="c-new", in_reply_to=m, inbound="Do you open on bank holidays?")
P = SEEN.get("prompt", "")
ok("the model is shown replies this business sent", "come on by" in P, P[:300])
ok("...and the question each answered", "Do you open on Sundays?" in P)
ok("...NOT the box's own earlier draft", "open Sundays 10 to 4" not in P)
ok("...quoted as examples, before the transcript",
   0 <= P.find("--- examples ---") < P.find("--- transcript ---"), str(P.find("--- examples ---")))
ok("edited lessons come first", P.find("come on by") < P.find("all major cards"), P[:400])

print("test_another_space_is_never_an_example")
m = conv("c-other", space="other", asked="Do you deliver?")
drafts.put(space="other", zcid="c-other", in_reply_to=m, body="We deliver locally.")
send("c-other", "We deliver anywhere in the county, same day before noon.", space="other")
SEEN.clear()
m = conv("c-new2", asked="Any weekend slots?")
draft.draft_one(space=SPACE, zcid="c-new2", in_reply_to=m, inbound="Any weekend slots?")
ok("acme's drafter never sees other's replies", "same day before noon" not in SEEN.get("prompt", ""))

print("test_the_examples_are_bounded")
for i in range(40):
    z = f"c-bulk-{i}"
    m = conv(z, asked=f"Question number {i} about {'x' * 500}?")
    drafts.put(space=SPACE, zcid=z, in_reply_to=m, body=f"Draft {i}")
    send(z, f"Reply number {i} " + "y" * 900)
SEEN.clear()
m = conv("c-new3", asked="One more?")
draft.draft_one(space=SPACE, zcid="c-new3", in_reply_to=m, inbound="One more?")
P = SEEN.get("prompt", "")
ok("at most a handful of examples", P.count("Business replied:") <= draft._MAX_EXAMPLES,
   str(P.count("Business replied:")))
ok("...each side clipped", "y" * 400 not in P and "x" * 400 not in P)
ok("...so the whole prompt stays small", len(P) < 6000, str(len(P)))

print("test_no_lessons_means_no_examples_block")
SEEN.clear()
m = conv("c-fresh", space="fresh", asked="Hello?")
draft.draft_one(space="fresh", zcid="c-fresh", in_reply_to=m, inbound="Hello?")
ok("a new box's prompt has no empty examples section", "--- examples ---" not in SEEN.get("prompt", ""))

# ── 3. the send is never hostage to the lesson ──────────────────────────────────────────────
print("test_a_failing_lesson_never_fails_the_send")
real_learn = drafts.learn


def boom(*a, **k):
    raise RuntimeError("disk gone")


drafts.learn = boom
m = conv("c-hostage", asked="Still there?")
drafts.put(space=SPACE, zcid="c-hostage", in_reply_to=m, body="Yes.")
try:
    out = send("c-hostage", "Yes, still here.")
    ok("the reply went", out.get("status") == "ok", str(out))
    ok("...and was recorded", any(t == "Yes, still here." for _, t in INBOX.sent))
except Exception as e:                                                # noqa: BLE001
    ok("the reply went", False, f"raised {type(e).__name__}")
drafts.learn = real_learn

print("\nFAILED" if _failed else "\nALL PASS")
sys.exit(1 if _failed else 0)
