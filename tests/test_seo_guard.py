"""The pre-publish guard refuses, and refuses for the right reason — and refuses NOTHING of its
own accord.

WHY THIS SUITE IS THE POINT AND NOT A FORMALITY. `OWNER 2026-09-25:` the machine will "write and
publish on its own". `docs/OWNBOX_ARTICLES.md`: "Nobody reads an article before it is live, so the
machine's pre-publish check is the only reviewer", and OSDev1 ruled it ships with a PLANTED-FAILURE
TEST FOR EACH rule. So every rule below is proven by a string that must be refused — a guard nobody
has watched refuse something is a guard nobody knows works.

AND THE MIRROR OF THAT, added after OSDev1 rejected the first draft (#1552, 2026-09-25): every
planted failure is proven to PASS on a box that supplied no lists. His case, verbatim: "a plumber,
a call centre or a phone-repair shop could never publish an article about its own business".
"""
import os, sys, tempfile, pathlib
os.environ.setdefault("AIOS_HERMETIC_TEST", "1")
os.environ.setdefault("AIOS_DB_PATH", tempfile.mkdtemp() + "/t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marketing.seo_machine import guard

ROOT = pathlib.Path(__file__).resolve().parent.parent
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  — {detail}"))
    if not cond:
        _failed += 1


def rules(text, **kw):
    return {r.rule for r in guard.check(text, **kw)}


# Ownbox's own settings, as they will be ENTERED ON THE SETTINGS SCREEN rather than shipped in
# code. They live in this test because this test is Ownbox's box; the module knows none of them.
OURS = dict(
    never_words=("phone", "ring", "call", "dial", "line", "voice"),
    never_phrases=("voice search", "plumbing", "Customer Voice"),
)

print("-- planted failure, one per rule --")
ok("the receptionist's words are refused when the box lists them",
   rules("Never miss a call again", **OURS) == {"never_word"})
for word in ("phone", "ring", "call", "dial", "line", "voice"):
    ok(f"   ...{word}", "never_word" in rules(f"Our {word} is open", **OURS))
ok("'voice search' — the SEO industry's own term, the likeliest violation of all",
   "never_phrase" in rules("Optimise for voice search", **OURS))
ok("a phrase on the box's list", "never_phrase" in rules("This is the plumbing behind it.", **OURS))
ok("a product name that collides with the reserved words",
   "never_phrase" in rules("Customer Voice watches your reviews.", **OURS))
ok("a competitor the box named", "competitor" in rules("Better than Acme CRM.", competitors=("Acme CRM",)))

print("\n-- competitors are whole words, which is OSDev6's 'Front' finding (#1545 §5) --")
RIVALS = ("Front", "HubSpot", "AI Emaily")
ok("'upfront' is no longer refused by a box that listed 'Front'",
   guard.check("The upfront cost", competitors=RIVALS) == [])
ok("...nor is 'on several fronts' — a company's plural is just an English word",
   guard.check("on several fronts", competitors=RIVALS) == [])
ok("the company itself is still caught",
   "competitor" in rules("Front is a shared inbox", competitors=RIVALS))
ok("...including possessively", "competitor" in rules("Front's pricing", competitors=RIVALS))
ok("a multi-word name still matches", "competitor" in rules("beats AI Emaily", competitors=RIVALS))
ok("and matching stays case-insensitive", "competitor" in rules("hubspot", competitors=RIVALS))

# FIXED 2026-09-25 (OSDev1): a one-word name spelled like an ordinary word keeps the capital the
# buyer typed, so "Front" can go on the list without refusing the site's own "front end" copy.
ok("'building the front end' passes a box listing 'Front'",
   "competitor" not in rules("building the front end", competitors=RIVALS))
ok("...and so does 'in front of you'", "competitor" not in rules("in front of you", competitors=RIVALS))
ok("...while 'Front' the company, capitalised, is still refused",
   "competitor" in rules("Try Front today", competitors=RIVALS))
ok("a number that is not on the box's fact list",
   "unsourced_number" in rules("It costs $500.", allowed_numbers=("499",)))

print("\n-- nothing of ours leaks into a stranger's box (OSDev1, #1552) --")
ok("a box that supplied no lists publishes the sentence OUR box forbids outright",
   guard.check("Call our plumbing line for a quote") == [])
ok("...and a phone-repair shop can write about phones",
   guard.check("We fix phones, and we answer the phone.") == [])
ok("...and an SEO agency can write the words 'voice search'",
   guard.check("A guide to voice search") == [])
# Read the AST, not the prose. The docstring EXPLAINS the rule using the very words it no longer
# enforces, so a grep over source text can only ever fail here. What leaks to a buyer is a
# module-level list of words, so that is what this looks for.
import ast
mod = ast.parse((ROOT / "marketing/seo_machine/guard.py").read_text())
word_lists = [t.id for n in mod.body if isinstance(n, ast.Assign)
              for t in n.targets if isinstance(t, ast.Name)
              if isinstance(n.value, (ast.Tuple, ast.List, ast.Set))
              and any(isinstance(e, ast.Constant) and isinstance(e.value, str)
                      for e in n.value.elts)]
ok("the module defines NO list of words at all — there is nothing of ours to leak",
   word_lists == [], word_lists)

print("\n-- false positives, which would make it useless --")
ok("iPhone survives a box that forbids 'phone' — or install steps become unpublishable",
   guard.check("Open the iPhone app", **OURS) == [])
ok("...and 'phones' does NOT survive it, which is the hole a bare word list leaves",
   "never_word" in rules("We sell phones", **OURS))
ok("matching is case-insensitive", "never_word" in rules("VOICE", **OURS))
ok("a sourced price passes", guard.check("It costs $499.", allowed_numbers=("$499",)) == [])
ok("$499, 499 and 1,599 normalise to one fact each",
   guard.check("$499 and 499 and 1,599", allowed_numbers=("499", "1599")) == [])
ok("a percentage passes when the box listed that percentage", guard.check("40%", allowed_numbers=("40%",)) == [])
ok("...but an allowed 40 does NOT vouch for 40% (a price is not a rate)",
   "unsourced_number" in rules("40% faster", allowed_numbers=("40",)))

print("\n-- hardened 2026-09-25: every hole the adversarial review proved --")
W = dict(never_words=("agency", "class", "person", "phone"), never_phrases=("voice search",))
for label, text in (("a no-break space", "voice search"), ("a double space", "voice  search"),
                    ("a hyphen", "voice-search"), ("a line break", "voice\nsearch"),
                    ("run together", "voicesearch"), ("a soft hyphen", "voice sea­rch"),
                    ("a zero-width space", "voice​search"), ("capitals", "VOICE SEARCH"),
                    ("plural", "voice searches")):
    ok(f"'voice search' written with {label} is refused", "never_phrase" in rules(f"Try {text} now", **W))
for word, text in (("agency", "Two agencies agreed"), ("class", "All classes"),
                   ("person", "Most people"), ("phone", "Your phone's screen")):
    ok(f"'{word}' catches '{text}'", "never_word" in rules(text, **W))
ok("a full-width word is the same word", "never_word" in rules("ｐｈｏｎｅ", **W))
C = ("Domino's", ".NET", "Yahoo!", "C++", "Acme Inc.", "CallRail")
for text in ("Domino’s delivers", "built on .NET", "Yahoo! Mail", "written in C++",
             "Acme Inc. says", "Acme Inc says", "Yahoo mail", "Call Rail", "call-rail", "CALLRAIL"):
    ok(f"competitor caught in '{text}'", "competitor" in rules(text, competitors=C))
for text in ("net revenue", "grade C work", "the dominoes fell"):
    ok(f"...but not in '{text}'", "competitor" not in rules(text, competitors=C))
N = dict(allowed_numbers=("$499", "5", "10"))
for text in ("$499k a year", "$5M raised", "10x faster", "499% growth", "4,99 euros"):
    ok(f"'{text}' is refused though 499, 5 and 10 are allowed", "unsourced_number" in rules(text, **N))
for text in ("twelve thousand customers", "3 million users", "thousands of teams", "twenty-five seats"):
    ok(f"a spelled-out count is refused: '{text}'", "unsourced_number" in rules(text, allowed_numbers=("3",)))
ok("...unless the box listed it", guard.check("twelve thousand customers",
                                              allowed_numbers=("twelve thousand",)) == [])
ok("one to ten stay ordinary prose", guard.check("one of the two reasons", allowed_numbers=()) == [])
ok("499.00 and a stored 499.0 are the same fact as 499",
   guard.check("$499.00 or 499", allowed_numbers=("499.0",)) == [])
ok("1,599 still reads as 1599", guard.check("$1,599", allowed_numbers=("1599",)) == [])
ok("a unit the box listed passes", guard.check("$5M raised", allowed_numbers=("$5M",)) == [])

print("\n-- the refusal is usable --")
r = guard.check("Call us on 5 lines", **OURS)
ok("every reason comes back at once, not the first one",
   {x.rule for x in r} == {"never_word", "unsourced_number"} and len(r) >= 3, [x.rule for x in r])
ok("each refusal names the exact text that tripped it", all(x.found for x in r))
ok("the reason is the box's, not a lecture of ours",
   all("never-use list" in x.why for x in r if x.rule == "never_word"),
   [x.why for x in r])
try:
    guard.assert_publishable("Call now", **OURS)
    ok("assert_publishable raises rather than returning", False)
except guard.GuardRefused as e:
    ok("assert_publishable raises rather than returning", True)
    ok("...carrying the refusals, not just a string", len(e.refusals) >= 1)
ok("empty lists on empty text is not a crash",
   guard.check("") == [] and guard.check(None) == [])

print(f"\n{_failed} FAILED" if _failed else "\nall ok")
sys.exit(1 if _failed else 0)
