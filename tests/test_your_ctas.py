"""Your CTAs: up to three lines, written by the owner, that end every email reply (owner, 2026-10-09).

Owner, 2026-10-09, after a 4,000-character "Your business" sent every draft's closing line somewhere different: "Your
business should just stay there and could just be a brain dump to be used for general business context in other
purposes ... it should be another field called CTAs ... these will be placed at the end of email drafts to drive
traffic and Leeds. If you don't want a CTA leave this box empty." How they are used: "give three examples and then
instruct the box to slightly [change] them each time according to what it appears would work best in each given
situation". And: "if it's a customer service focused approach then they wouldn't want the CTA at the end of the email."

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * the store keeps more than three CTAs, or one too long to end an email, or cuts what someone wrote instead of
    saying why it refused;
  * a box with no CTAs gets anything added (its instructions are exactly what they were, on every style);
  * CTAs reach a DM, or an email set to Customer service;
  * with CTAs, the light sentence (and its "say something different") still competes for the closing line, or the
    recent endings are still shown to push the CTA away;
  * the CTAs reach the drafter as anything but the closing line: one of them, its point and link kept, a few words
    changed to fit, never two, never in case 3, never on a customer's open problem;
  * changing the CTAs does not mark the email drafts still waiting for a rewrite (or marks the DMs');
  * a cold pitch turned around with the CTA gets a second link stuck under it;
  * the owner's AI can change them without the owner's yes on Approvals.

No network, no model.

Run: python tests/test_your_ctas.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "ctas.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"

from core import approvals, box_settings, brain, business_context as bc, cost_guard, state  # noqa: E402

state.init_db()
from core.dispatch import app  # noqa: E402,F401
from core import box_tools  # noqa: E402
from marketing.customer_voice import app as inbox_app  # noqa: E402
from marketing.customer_voice.drafter import draft, store as drafts  # noqa: E402
from marketing.customer_voice.inbox import pitch_back, store  # noqa: E402

FAILS: list[str] = []
SPACE = inbox_app._space()
MINE = ("Everyone's building AI agents right now, so I built them a place to work: contained, sharing the same "
        "business context, and doing real work with the team. It's at ownbox.io. Would love to hear what's on your "
        "mind.\n"
        "What I'm working on these days: a shared place where AI agents and human teams do real work together "
        "(ownbox.io). Curious what's on your mind.\n"
        "I build AI business systems where people and agents work side by side from the same business context: "
        "ownbox.io. What are you working on right now?")


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:500]}"))
    if not cond:
        FAILS.append(label)


def refused(fn) -> str:
    try:
        fn()
    except ValueError as e:
        return str(e)
    return ""


def style(email: str = "", dms: str = "") -> None:
    box_settings.put("inbox", "reply_style.email", email, set_by="t")
    box_settings.put("inbox", "reply_style.dms", dms, set_by="t")


print("test_the_field_holds_up_to_three_short_lines")
ok("a new box has none", bc.ctas() == [] and bc.get()["ctas"] == "", bc.get().get("ctas"))
got = bc.set_ctas("  First   line, with a link: example.com  \r\n\r\n\nSecond line?\n", by="owner")
ok("each line is one CTA: spaces collapsed, empty lines dropped, the order kept",
   got == "First line, with a link: example.com\nSecond line?"
   and bc.ctas() == ["First line, with a link: example.com", "Second line?"], repr(got))
why = refused(lambda: bc.set_ctas("one\ntwo\nthree\nfour", by="owner"))
ok("a fourth is refused in a sentence that says what to do, and nothing is cut",
   "4 CTAs" in why and "up to 3, one per line" in why and len(bc.ctas()) == 2, why)
why = refused(lambda: bc.set_ctas("fine\n" + "x" * (bc.CTA_CHARS + 1), by="owner"))
ok("one too long to end an email is refused by number, and nothing is cut",
   "CTA 2" in why and str(bc.CTA_CHARS) in why and len(bc.ctas()) == 2, why)
ok("every change says who made it", "says who made it" in refused(lambda: bc.put("ctas", "a", by="")))
ok("exactly three is fine", len(bc.ctas()) == 2 and bc.set_ctas("a\nb\nc", by="owner") == "a\nb\nc")
bc.set_ctas("", by="owner")
ok("empty clears them", bc.ctas() == [])

print("\ntest_no_ctas_changes_nothing")
plain = {}
for s in ("", "sales", "subtle", "service"):
    style(s, s)
    plain[s] = (draft._system("email"), draft._system("instagram"))
    ok(f"no CTAs, style {s or 'unset'}: no CTA instructions on email or DMs",
       all("THE OWNER'S CTAs" not in x for x in plain[s]))
style()
ok("no CTAs, no style: the instructions are exactly the base ones", plain[""][0] == draft.SYSTEM)

print("\ntest_ctas_close_email_in_the_selling_styles")
bc.set_ctas(MINE, by="owner")
for s in ("sales", "subtle", ""):
    style(s, "sales")
    e = draft._system("email")
    ok(f"{s or 'unset'}: every one of the owner's CTAs reaches the email drafter, word for word",
       all(c in e for c in bc.ctas()), e[-900:])
    ok(f"{s or 'unset'}: ...as the closing line: one of them, the one that fits, its point and link kept, a few "
       "words changed", "end every reply you write" in e and "with ONE of them" in e and "fits this conversation best"
       in e and "its link as written" in e and "Change only a few words" in e and "never two" in e, e[-1400:])
    ok(f"{s or 'unset'}: ...never in case 3, never on a customer's open problem, never twice in a conversation",
       "never case 3" in e and "customer with an open problem" in e and "already ended with one" in e)
    ok(f"{s or 'unset'}: ...they replace the light sentence, which no longer competes for the line",
       "THE LIGHT SENTENCE" not in e and "never a stock line" not in e and "replace the light sentence" in e)
    ok(f"{s or 'unset'}: ...and they come last, after everything that could reopen the closing line",
       e.rstrip().endswith("--- end of the owner's CTAs ---"), e[-200:])
style("subtle", "subtle")
bc.put("description", "A long brain dump about everything the business does, its machines and its plans.", by="owner")
e = draft._system("email")
ok("with the owner's own words too, the CTAs still come after them",
   e.index("OWNER'S OWN WORDS") < e.index("THE OWNER'S CTAs"), e[-300:])
bc.put("description", "", by="owner")
style("service", "sales")
e = draft._system("email")
ok("email set to Customer service: no CTA at all (owner: 'they wouldn't want the CTA')",
   "THE OWNER'S CTAs" not in e and not any(c in e for c in bc.ctas()) and e == plain["service"][0])
style("sales", "sales")
for dm in ("instagram", "messenger"):
    d = draft._system(dm)
    ok(f"a {dm} DM: no CTA (email drafts only), and its light sentence stays",
       "THE OWNER'S CTAs" not in d and not any(c in d for c in bc.ctas()) and "THE LIGHT SENTENCE" in d)
ok("a draft whose channel is unknown is not given them", "THE OWNER'S CTAs" not in draft._system(None))

print("\ntest_changing_them_rewrites_the_waiting_email_drafts_only")
style("sales", "sales")
before_e, before_dm = draft.rules("email"), draft.rules("instagram")
bc.set_ctas(MINE.split("\n")[0], by="owner")
ok("a new set of CTAs changes the email rules, so email drafts waiting are rewritten with them",
   draft.rules("email") != before_e)
ok("...and leaves the DMs' rules alone, so no DM is paid for again", draft.rules("instagram") == before_dm)
bc.set_ctas("", by="owner")
style("sales", "sales")
ok("cleared, email is back to exactly what it was", draft._system("email") == plain["sales"][0])

print("\ntest_the_prompt_stops_pushing_the_ending_elsewhere")
real_endings, real_lessons = drafts.recent_endings, drafts.lessons
drafts.recent_endings = lambda *a, **k: ["Take a look at www.ownbox.io/machines."]
drafts.lessons = lambda *a, **k: [{"asked": "How does it work?", "sent_body": "It runs on your own server. Cheers."}]
p = draft._prompt(space=SPACE, zcid="z", inbound="Hi", history=[], platform="email")
ok("no CTAs: recent endings are shown, to vary the light sentence", "ended like this" in p and
   "least of all their closing line" in p, p[:600])
bc.set_ctas(MINE, by="owner")
p = draft._prompt(space=SPACE, zcid="z", inbound="Hi", history=[], platform="email")
ok("with CTAs on email: no 'say something different' list, and sent replies' endings are not a thing to avoid",
   "ended like this" not in p and "least of all their closing line" not in p and "owner's CTAs" in p, p[:600])
p = draft._prompt(space=SPACE, zcid="z", inbound="Hi", history=[], platform="instagram")
ok("...a DM still varies its light sentence against recent endings", "ended like this" in p)
drafts.recent_endings, drafts.lessons = real_endings, real_lessons

print("\ntest_a_pitch_turned_around_by_the_cta_gets_no_second_link")
calls, answer = [], {"text": ""}
cost_guard.check_vendor = lambda *a, **k: None
brain.think = lambda **kw: calls.append(kw) or answer["text"]
pitch_back.put(True, "www.ownbox.io/machines", by="owner")
style("sales", "sales")


def pitch(n: int, text: str) -> str:
    zcid = f"conv-{n}"
    store.upsert_conversation(space=SPACE, zcid=zcid, platform="email", participant=f"Vendor {n}",
                              last_inbound_at="2026-10-09T09:00:00Z", account_id="acc-1")
    store.record_message(space=SPACE, zcid=zcid, zmid=f"m-{n}", direction="in", sent_by="contact", body=text)
    return zcid


z = pitch(1, "We fund IT businesses, 50k-2mm. Want me to explain further?")
answer["text"] = ("PITCH_BACK: Thanks, Leah. Everyone's building AI agents right now, so I built them a place to work. "
                  "It's at ownbox.io. Would love to hear what's on your mind.")
got = draft.draft_one(space=SPACE, zcid=z, in_reply_to="m-1", inbound="We fund IT businesses.", platform="email")
ok("the CTAs reach the one model call for an email", calls and "THE OWNER'S CTAs" in calls[-1]["system"])
ok("a pitch turned around with the CTA's own link gets no 'Take a look' under it",
   got and "Take a look" not in got and got.endswith("on your mind."), got)
z = pitch(2, "We build websites. Interested?")
answer["text"] = "PITCH_BACK: Thanks for reaching out, happy to connect."
got = draft.draft_one(space=SPACE, zcid=z, in_reply_to="m-2", inbound="We build websites.", platform="email")
ok("...one with no link at all still gets the cold-pitch link, as before",
   got and got.endswith("Take a look: www.ownbox.io/machines"), got)
bc.set_ctas("", by="owner")
z = pitch(3, "SEO call?")
answer["text"] = "PITCH_BACK: Thanks! Ownbox is at ownbox.io."
got = draft.draft_one(space=SPACE, zcid=z, in_reply_to="m-3", inbound="SEO call?", platform="email")
ok("...and with no CTAs, the cold-pitch link is still added exactly as before",
   got and got.endswith("Take a look: www.ownbox.io/machines"), got)
pitch_back.put(False, "www.ownbox.io/machines", by="owner")
style()

print("\ntest_their_ai_reads_them_and_asks_before_changing_them")
r = box_tools.business_ctas()
ok("none: the answer says how they work and that empty is fine",
   r["ctas"] == [] and "No CTAs yet" in box_tools._render_ctas(r) and "/settings/business" in box_tools._render_ctas(r))
a = box_tools.propose_business_ctas(ctas=MINE, seat={"label": "Claude"})
ok("asking puts a card on Approvals and changes nothing", a.get("asked") and bc.ctas() == [], a)
row = approvals.get(a["approval"])
shown = (row.get("detail") or {}).get("arguments") or {}
ok("the card shows the CTAs whole and what they will do",
   shown.get("New CTAs") == bc.clean_ctas(MINE) and "Customer service" in shown.get("Means", ""), shown)
approvals.decide(a["approval"], True, by="owner")
ok("the owner's yes stores them", bc.ctas() == bc.clean_ctas(MINE).split("\n"), bc.ctas())
ok("...and the read answer lists them, numbered", "1. Everyone's building" in box_tools._render_ctas(
    box_tools.business_ctas()))
ok("the same CTAs again is not a card", box_tools.propose_business_ctas(ctas=MINE).get("asked") is False)
four = box_tools.propose_business_ctas(ctas="a\nb\nc\nd")
ok("four is refused before any card, in the store's own sentence", four.get("asked") is False and
   "up to 3" in four.get("note", ""), four)
c = box_tools.propose_business_ctas(clear=True)
approvals.decide(c["approval"], True, by="owner")
ok("clearing is a card too, and the yes clears them", c.get("asked") and bc.ctas() == [], c)
ok("nothing to clear is not a card", box_tools.propose_business_ctas(clear=True).get("asked") is False)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL OK")
