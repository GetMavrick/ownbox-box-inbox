"""The box learns what the business is, from the business's own sent mail.

THE GAP, measured on the owner's box 2026-09-22. Every draft read like this:

    "I'd be happy to help! Could you tell me what service you're interested in?"

Not a weak model — a blind one. `draft.py` calls `brain.think(..., isolated=True)` and
`_with_knowledge` returns early on `isolated`, so the drafter had never received one word about
the business it answers for; and `my/knowledge/` on a fresh box holds a README and nothing else.
A product sold as "it answers your customers" could not answer a single real question.

The answer was already in the mailbox: a trading business has years of its own replies in its
Sent folder — every price it has quoted, every "we're open till six". So the box reads them once
and writes down what it learns. Nothing for the buyer to type.

WHAT THIS SUITE IS REALLY FOR. Two of these assertions are about the feature; the rest are about
the two ways it could hurt somebody:

  · a customer's details end up in `my/knowledge/`, which rides on EVERY call this box makes,
    for every machine — so one person's address would be repeated to every future customer
  · the buyer's own hand-written knowledge is overwritten by a machine

Run: python tests/test_the_box_learns_your_business.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "learn2.db")

import pathlib                                                     # noqa: E402

from core import brain, state                                      # noqa: E402

state.init_db()
KDIR = pathlib.Path(tempfile.mkdtemp())
brain.KNOWLEDGE_DIR = KDIR

from marketing.customer_voice.drafter import learn_business as lb   # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


REAL = ["Hi — we're open Monday to Friday 8 till 6, Saturdays till 1. A standard service is "
        "£95 including parts." for _ in range(12)]


def says(answer):
    brain.think = lambda **kw: answer


# ── 1. it learns, and the drafter actually receives it ──────────────────────────────────────
print("test_what_it_learns_reaches_the_drafter")
says("## What we do\nPlumbing.\n\n## Hours\nMon-Fri 8-6, Sat till 1.")
out = lb.summarise(REAL)
ok("it learned something", out["status"] == "ok", str(out))
ok("...and read the whole sample", out["read"] == len(REAL), str(out))
brain.write_knowledge(lb.OUT_NAME, out["text"])
ok("...the file is written where the brain looks", (KDIR / lb.OUT_NAME).is_file())
ok("...it says it was written BY the box, so nobody mistakes it for their own",
   "Written by your box" in (KDIR / lb.OUT_NAME).read_text())
ok("...and knowledge_context() now carries the hours into every call",
   "Mon-Fri 8-6" in brain.knowledge_context(), brain.knowledge_context()[:120])

print("test_the_drafter_asks_for_that_knowledge")
src = pathlib.Path(__file__).resolve().parents[1] / "marketing/customer_voice/drafter/draft.py"
t = src.read_text()
ok("draft.py passes the business's facts to the model",
   "knowledge_context()" in t, "the drafter still knows nothing about the business")
ok("...and stays isolated, so this repo's own context never reaches a customer",
   "isolated=True" in t)


# ── 2. THE HARM: a customer's details must never enter that folder ──────────────────────────
print("test_a_customers_details_are_refused_even_if_the_model_returns_them")
for leak, what in (("Call Dana on 0161 496 0000 about her boiler.", "a phone number"),
                   ("Dana's address is dana@acme.co and she wants a refund.", "an email address")):
    says(leak)
    out = lb.summarise(REAL)
    ok(f"refused: {what}", out["status"] == "refused_personal", str(out))
    ok("...and nothing is returned to be written", "text" not in out, str(out.keys()))

print("test_the_prompt_says_so_too_belt_and_braces")
ok("the model is told never to include a customer's details", "NEVER include any customer" in lb.SYSTEM)
ok("...and told these words ride on every reply the box drafts", "every reply the box ever drafts" in lb.SYSTEM)


# ── 3. THE OTHER HARM: never overwrite what the buyer wrote ─────────────────────────────────
print("test_the_buyers_own_knowledge_is_never_touched")
mine = KDIR / "my-own-notes.md"
mine.write_text("# Ours\nWe never quote over the phone.\n")
says("## What we do\nPlumbing, second pass.")
out = lb.summarise(REAL)
brain.write_knowledge(lb.OUT_NAME, out["text"])
ok("the buyer's file is byte-for-byte untouched",
   mine.read_text() == "# Ours\nWe never quote over the phone.\n")
ok("...and it is still loaded alongside", "never quote over the phone" in brain.knowledge_context())
ok("learning again replaces only the box's own file",
   "second pass" in (KDIR / lb.OUT_NAME).read_text())


# ── 4. the quiet cases ──────────────────────────────────────────────────────────────────────
print("test_a_thin_mailbox_teaches_nothing_and_says_so")
ok("too little to learn from is a status, not a guess", lb.summarise(["hi"])["status"] == "too_little")
says(lb.NOTHING)
ok("the model may say it learned nothing", lb.summarise(REAL)["status"] == "nothing_learned")


def boom(**kw):
    raise RuntimeError("model down")


brain.think = boom
ok("a model that will not answer is not a crash", lb.summarise(REAL)["status"] == "think_failed")

print("\nFAILED" if _failed else "\nALL PASS")
sys.exit(1 if _failed else 0)
