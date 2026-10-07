"""The closing line about the business varies, and draws on the owner's own words (owner, 2026-10-07).

Owner, 2026-10-07, about a draft ending "I build AI machines that automate business workflows with human approval for
consequential actions": "It's always putting with human approval on everyone and that's not really the key selling
point ... it's better than sending this every single time" and "Switch it up a little bit. It's overly using that
human approval thing every single time."

The sales and subtle styles end most replies with one light sentence about what the business does. On every box it
came out as one stock line, because nothing told the model to vary it, and the business was described from whatever
its knowledge held (mail the box learned from included), never from the description its owner wrote on the "Your
business" screen.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * the light sentence loses its rule to vary (fresh words, the one true thing that fits this person, never a stock
    line), or its rule to stay out of a conversation that has already heard it;
  * customer service gains a light sentence;
  * the owner's description on "Your business" stops reaching the drafter, or reaches it as words to quote whole;
  * a box with no description gets anything added (its instructions are exactly what they were);
  * rewriting the description does not mark the drafts still waiting for a rewrite.

No network, no model.

Run: python tests/test_the_light_sentence_varies.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "light.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"

from core import box_settings, business_context, state  # noqa: E402

state.init_db()
from core.dispatch import app  # noqa: E402,F401
from marketing.customer_voice.drafter import draft  # noqa: E402

FAILS: list[str] = []


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:400]}"))
    if not cond:
        FAILS.append(label)


print("test_the_light_sentence_is_never_a_stock_line")
for style in ("sales", "subtle"):
    box_settings.put("inbox", "reply_style.email", style, set_by="t")
    s = draft._system("email")
    ok(f"{style}: it varies, in fresh words, the one true thing that fits this person",
       "never a stock line" in s and "fresh words" in s and "fits this person best" in s, s[-700:])
    ok(f"{style}: ...and stays out of a conversation that has already heard it",
       "already said what the business does, leave the sentence out" in s)
box_settings.put("inbox", "reply_style.email", "service", set_by="t")
ok("customer service still has no light sentence at all", "THE LIGHT SENTENCE" not in draft._system("email"))
box_settings.put("inbox", "reply_style.email", "", set_by="t")

print("\ntest_the_owners_own_words_lead")
plain = draft._system("email")
ok("no description: nothing is added, so a box's instructions are exactly what they were",
   "OWNER'S OWN WORDS" not in plain and plain == draft.SYSTEM, plain[-300:])
before = draft.rules("email")
business_context.put("description", "We give a business's AI agents one place to work beside its team, on the same "
                     "business context, so real work gets done.", by="owner")
s = draft._system("email")
ok("the description on 'Your business' reaches the drafter, as words to draw on, never to quote whole",
   "on the same business context, so real work gets done." in s and "never quote it whole" in s
   and "draw on this first" in s, s[-400:])
ok("...on DMs too", "on the same business context" in draft._system("instagram"))
ok("rewriting it changes the rules, so the drafts still waiting are rewritten in the new words",
   draft.rules("email") != before)
business_context.put("description", "", by="owner")
ok("cleared, the instructions go back to exactly what they were", draft._system("email") == plain)

print("\nALL LIGHT-SENTENCE CHECKS PASS" if not FAILS else f"\n{len(FAILS)} LIGHT-SENTENCE CHECK(S) FAILED")
sys.exit(1 if FAILS else 0)
