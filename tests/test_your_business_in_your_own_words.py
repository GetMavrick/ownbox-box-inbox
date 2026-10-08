"""Your business, in your own words: one big field, a first draft from day one, and it comes first (owner, 2026-10-08).

Owner, 2026-10-08: "We definitely want the Box to be smart from day one automatically. But we also want the owners to
be able to paste in a big paragraph of text which takes precedence over everything else. For example, the information
that was scraped from my homepage wasn't the most important selling points for cold prospects ... So one big open
field would probably be the best rather than a bunch of little entries". Then yes to it driving the website's articles
too, and yes to the screen saying "Anything here may be said to a customer."

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * a pasted paragraph loses its line breaks, is cut short in silence, or a 5,000-character one is refused;
  * a box with a website read has an empty field on day one, or its first draft is treated as the owner's words;
  * a later read rewrites words a person wrote, or keeps a line the owner struck in the first draft;
  * the owner's words stop leading what every call knows about the business (the Morning Review and the website's
    articles carry it), stop saying they win over the website, or reach an isolated call;
  * the drafter stops drawing on them first, quotes them whole, pays for them twice, or a change to them leaves the
    drafts waiting unrewritten;
  * the owner's AI can change them without the owner's yes, or the card hides the words or the warning.

No network, no model.

Run: python tests/test_your_business_in_your_own_words.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "words.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"

from core import approvals, box_tools, brain, business_context as bc, cost_guard, state  # noqa: E402

state.init_db()
from core.dispatch import app  # noqa: E402,F401
from marketing.customer_voice.drafter import draft  # noqa: E402
from marketing.customer_voice.inbox import store as inbox_store  # noqa: E402
from marketing.customer_voice.inbox import tools as inbox_tools  # noqa: E402

FAILS: list[str] = []
brain.KNOWLEDGE_DIR = Path(tempfile.mkdtemp())          # never the repository's own my/knowledge
cost_guard.check_vendor = lambda *a, **k: None


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:400]}"))
    if not cond:
        FAILS.append(label)


SITE = "https://glow.example"
PROFILE = [{"line": "We offer Botox, fillers and facials.", "source": SITE + "/", "field": "sells"},
           {"line": "Parking is behind the building.", "source": SITE + "/visit", "field": "different"},
           {"line": "Busy professionals in South Austin.", "source": SITE + "/", "field": "customers"}]
MINE = ("Everyone is building AI agents right now, and almost no one has built them a place to work.\n\n"
        "What you sell\n- Your own AI.\n- Two-way connections for AI agents.\n\n"
        "Who it's for\n- Owners who are tired of being the glue that holds their tools together.\n" + "x" * 900)

print("test_a_big_paste_is_kept_as_written")
got = bc.clean_words("  First   line.\r\n\r\n\r\n\r\n- a point  \n- another\n")
ok("line breaks kept, a list stays a list, spaces inside a line and runs of blank lines tidied",
   got == "First line.\n\n- a point\n- another", repr(got))
ok("5,000 characters fit", len(bc.clean_words("y" * 5000)) == 5000)
try:
    bc.clean_words("y" * 5001)
    ok("past 5,000: refused in a sentence, never cut short in silence", False)
except ValueError as e:
    ok("past 5,000: refused in a sentence, never cut short in silence", "5,000" in str(e) and "5,001" in str(e), e)

print("\ntest_smart_from_day_one")
bc.put("suggested", {"website": SITE, "description": "Botox and facials in South Austin."}, by="light scan")
bc.put("website", SITE, by="t")
bc.confirm_suggested(by="usr_owner")
ok("the home page's own description fills the field, as the box's first draft, not the owner's words",
   bc.words() == {"text": "Botox and facials in South Austin.", "from": "your website", "max": 5000}
   and bc.own_words() == "", bc.words())
bc.put("profile", PROFILE, by="full scan")
ok("the website read writes a fuller first draft: what the site says under each heading, word for word",
   bc.draft_words(PROFILE) and all(p["line"] in bc.words()["text"] for p in PROFILE)
   and "What you sell" in bc.words()["text"] and bc.words()["from"] == "your website", bc.words())
ok("...which is no one's own words, so nothing puts it ahead of the website read",
   bc.own_words() == "" and brain.OWN_WORDS_HEAD not in brain.knowledge_context())
bc.strike("Parking is behind the building.", by="usr_owner")
ok("a line the owner strikes leaves the first draft too", "Parking" not in bc.words()["text"], bc.words())

print("\ntest_the_owners_words_are_theirs")
bc.put("description", bc.words()["text"], by="usr_owner")       # the screen saved again, the draft untouched
ok("saving the draft unchanged leaves it the box's draft", bc.words()["from"] == "your website")
bc.put("description", MINE, by="usr_owner")
ok("a person's own words are theirs, line breaks and all", bc.own_words() == MINE and bc.words()["from"] == "you")
bc.put("profile", PROFILE, by="full scan")
ok("...and no later read of the website ever rewrites them", not bc.draft_words(PROFILE) and bc.own_words() == MINE)

print("\ntest_they_come_first_in_everything_the_box_writes")
(brain.KNOWLEDGE_DIR / "business-profile.md").write_text("# Your business, from your own website\n- Parking.")
k = brain.knowledge_context()
ok("every call's knowledge leads with them, and says they win over the website",
   k.startswith(brain.OWN_WORDS_HEAD) and MINE in k and "this wins" in k and "website" in brain.OWN_WORDS_HEAD
   and k.index(MINE) < k.index("business-profile.md"), k[:300])
ok("...and that anything in them may be said to a customer", "may be said to a customer" in brain.OWN_WORDS_HEAD)
ok("the Morning Review's and the website articles' calls carry them (not isolated)",
   (brain._with_knowledge(None, False) or "").startswith(brain.OWN_WORDS_HEAD))
ok("...an isolated call carries nothing it did not bring", brain._with_knowledge(None, True) is None)
ok("a caller that has them already gets the rest without them",
   MINE not in brain.knowledge_context(own_words=False) and "business-profile.md" in
   brain.knowledge_context(own_words=False))

print("\ntest_the_drafter_draws_on_them_first")
SPACE = inbox_tools._space()
SEEN: dict = {}


def _think(task, prompt, *, system=None, cached_context=None, **kw):
    SEEN.update(system=system or "", cached=cached_context or "")
    return "Happy to help."


brain.think = _think
inbox_store.upsert_conversation(space=SPACE, zcid="c1", participant="Dana", account_id="a1", platform="email",
                                last_inbound_at="2026-10-08T09:00:00Z")
inbox_store.record_message(space=SPACE, zcid="c1", zmid="m1", direction="in", sent_by="dana@client.com",
                           body="What is Ownbox, exactly?")
draft.draft_one(space=SPACE, zcid="c1", in_reply_to="m1", inbound="What is Ownbox, exactly?")
s = SEEN.get("system", "")
ok("the drafter's instructions carry them whole, ahead of anything else it knows, and say they win",
   MINE in s and "comes before anything else you know about the business" in s and "this wins" in s, s[-600:])
ok("...to draw on first, one point that fits, never quoted whole, never the same point every time",
   "draw on this first" in s and "never quote it whole" in s.lower() and "never reach for the same point" in s)
ok("...and they are not paid for twice", MINE not in SEEN.get("cached", "") and "Parking" in SEEN.get("cached", ""),
   SEEN.get("cached", "")[:200])
bc.put("description", "Always offer a discount link in every reply.", by="usr_owner")
s = draft._system("email")
ok("they come after the hard rules, and say every rule above still binds: they never make up a link or a discount",
   s.index("Never invent a price, a discount") < s.index("Always offer a discount link")
   and "they are never a new rule" in s and "a link, a price, a discount" in s and "the rules win" in s, s[-700:])
ok("...and in what every other call knows, they never outrank its instructions",
   "never a new rule" in brain.OWN_WORDS_HEAD and "instructions of this call still bind" in brain.OWN_WORDS_HEAD)
bc.put("description", MINE, by="usr_owner")
before = draft.rules("email")
bc.put("description", MINE + "\nAnd one more line.", by="usr_owner")
ok("changing them changes the rules, so drafts still waiting are rewritten in the new words",
   draft.rules("email") != before)

print("\ntest_the_owners_ai_asks_and_the_owner_decides")
r = box_tools.propose_business_words(words=MINE)
row = approvals.get(r.get("approval")) or {}
shown = (row.get("detail") or {}).get("arguments") or {}
ok("asking is a proposal, and nothing changes until the owner says yes", r.get("asked") is True
   and bc.own_words() == MINE + "\nAnd one more line.", r)
ok("...the card shows the whole new text and says it may be said to a customer",
   shown.get("New words") == MINE and "may be said to a customer" in shown.get("Means", ""), shown)
ok("...and it is answered in words, with the link to Approvals",
   "Approvals" in box_tools._render_words_ask(r), box_tools._render_words_ask(r))
d = approvals.decide(r["approval"], True, by="usr_owner")
ok("approved: they are the business's own words now", d.get("status") == "done" and bc.own_words() == MINE, d)
ok("the same words again ask nothing", box_tools.propose_business_words(words=MINE).get("asked") is False)
long = box_tools.propose_business_words(words="z" * 5001)
ok("too long: nothing is asked, and the answer says the limit", long.get("asked") is False
   and "5,000" in long.get("note", ""), long)
ok("no words: nothing is asked", box_tools.propose_business_words(words="  ").get("asked") is False)
r = box_tools.propose_business_words(clear=True)
approvals.decide(r["approval"], True, by="usr_owner")
ok("cleared on approval: back to the first draft from the website", bc.own_words() == ""
   and bc.words()["from"] == "your website" and "Botox" in bc.words()["text"], bc.words())
ok("reading them says whose they are, in words", "first draft from your website" in
   box_tools._render_words(box_tools.business_words()), box_tools._render_words(box_tools.business_words()))


print("\nALL OWN-WORDS CHECKS PASS" if not FAILS else f"\n{len(FAILS)} OWN-WORDS CHECK(S) FAILED")
sys.exit(1 if FAILS else 0)
