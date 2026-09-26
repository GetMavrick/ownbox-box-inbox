"""The drafter: one reasoning call, one repair round, and a slug it does not get to choose.

NO NETWORK AND NO TOKENS. `brain.think` is swapped for a scripted stand-in, so every assertion is
about what we ASKED for and what we did with the reply. The one thing a test must never do here is
spend money to prove the drafter works.
"""
import os, sys, tempfile, json
os.environ.setdefault("AIOS_HERMETIC_TEST", "1")
os.environ.setdefault("AIOS_DB_PATH", tempfile.mkdtemp() + "/t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marketing.aeo_machine import guard, writer

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  — {detail}"))
    if not cond:
        _failed += 1


GOOD = {"title": "What an AI coworker does",
        "short_answer": "It does the work you hand it.",
        "meta_description": "A short description.",
        "body_markdown": "## Heading\n\nIt does the work.\n\n1. Open it\n2. Hand it a task",
        "faqs": [{"question": "Is it fast?", "answer": "Yes."}]}


class Brain:
    """Returns each scripted reply in turn, recording what it was asked."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def think(self, **kw):
        self.calls.append(kw)
        return self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]


def run(*replies, **kw):
    b = Brain(*[r if isinstance(r, str) else json.dumps(r) for r in replies])
    writer.brain = b
    return writer.write(kw.pop("question", "What does an AI coworker do?"), **kw), b


print("-- the happy path --")
fields, b = run(GOOD)
ok("one clean draft costs exactly one reasoning call", len(b.calls) == 1, len(b.calls))
ok("the body is converted to Portable Text, not left as Markdown",
   isinstance(fields["body_blocks"], list) and fields["body_blocks"][0]["_type"] == "block")
ok("the Markdown is kept too, so an article can be re-edited later",
   fields["source_markdown"].startswith("## Heading"))
ok("FAQs survive", fields["faqs"][0]["question"] == "Is it fast?")
ok("it reasons on the AEO machine's own AI account", b.calls[0].get("machine") == "seo")
ok("the call is tagged so the spend ledger can attribute it",
   b.calls[0].get("task") == "aeo.article")

print("\n-- the slug is ours, not the model's --")
ok("derived from the title", fields["slug"] == "what-an-ai-coworker-does")
f2, _ = run({**GOOD, "slug": "something-the-model-made-up"})
ok("a slug the model volunteers is IGNORED — it is the article's identity and every link "
   "depends on it", f2["slug"] == "what-an-ai-coworker-does", f2["slug"])
f3, _ = run({**GOOD, "title": "🎉"})
ok("a title that reduces to nothing does not become /articles/guide", f3["slug"].startswith("article-"),
   f3["slug"])

print("\n-- the rules reach the model before the guard has to enforce them --")
_, b = run(GOOD, facts=("The box is $499 once.",),
           lists={"never_words": ("call",), "competitors": ("Acme",),
                  "allowed_numbers": ("499",)})
p = b.calls[0]["prompt"]
ok("the facts it may state are in the prompt", "$499 once" in p)
ok("the forbidden words are in the prompt", "call" in p)
ok("the competitors are in the prompt", "Acme" in p)
ok("the allowed numbers are in the prompt", "499" in p)
ok("a box with no numbers entered is told NO numbers, not given an empty list",
   "NO numbers" in run(GOOD)[1].calls[0]["prompt"])
sysp = b.calls[0]["system"]
ok("the 40-60 word standalone answer is in the system prompt", "40-60 words" in sysp)
ok("...and the rule that keeps digits out of prose, which the guard would otherwise refuse",
   "list markup rather than prose" in sysp)

print("\n-- the repair round, which is the difference between publishing and not --")
bad = {**GOOD, "short_answer": "Never miss a call."}
fields, b = run(bad, GOOD, lists={"never_words": ("call",)})
ok("a refused draft is sent back once, not thrown away", len(b.calls) == 2, len(b.calls))
ok("the repair names the exact text that tripped it", '"call"' in b.calls[1]["prompt"])
ok("...and asks for the whole article again, not a patch",
   "whole article" in b.calls[1]["prompt"])
ok("the repaired draft is what comes back", fields["short_answer"] == GOOD["short_answer"])

try:
    run(bad, bad, lists={"never_words": ("call",)})
    ok("a draft that is still refused after the repair RAISES — it must not publish", False)
except guard.GuardRefused as e:
    ok("a draft that is still refused after the repair RAISES — it must not publish", True)
    ok("...carrying every reason, for the row a person will read", len(e.refusals) >= 1)

b3 = Brain(json.dumps(bad), json.dumps(bad))
writer.brain = b3
try:
    writer.write("q", lists={"never_words": ("call",)})
except guard.GuardRefused:
    pass
ok("it stops at two calls — a third is money spent on a model that has already failed twice",
   len(b3.calls) == 2, len(b3.calls))

print("\n-- the model's formatting slips are not lost articles --")
ok("a fenced reply parses", run("```json\n" + json.dumps(GOOD) + "\n```")[0]["title"] == GOOD["title"])
ok("a reply with a sentence of preamble parses",
   run("Here you go:\n" + json.dumps(GOOD))[0]["title"] == GOOD["title"])
for junk, why in [("no json at all", "a reply with no object raises"),
                  ('{"title": ""}', "an empty title raises — the contract requires it")]:
    b = Brain(junk)
    writer.brain = b
    try:
        writer.write("q")
        ok(why, False, "it returned")
    except ValueError:
        ok(why + ", after its repair round and no further", len(b.calls) == 2, len(b.calls))

# REVIEW ITEM 8 (OSDev1, #1557). A clean JSON reply whose body carries a code block used to be cut
# down to the code block by the fence search, and raised before the repair round could run.
WITH_CODE = {**GOOD, "body_markdown": "## Setup\n\nRun this:\n\n```bash\necho ready\n```\n\nDone."}
got, b = run(WITH_CODE)
ok("a plain JSON reply whose body holds a code block parses as the article",
   got["title"] == GOOD["title"] and len(b.calls) == 1, len(b.calls))
ok("...and the code block survives into the body", any(x.get("_type") == "code" for x in got["body_blocks"]))
got, b = run("```json\n" + json.dumps(WITH_CODE) + "\n```")
ok("...wrapped in a fence of its own, too", got["title"] == GOOD["title"])
got, b = run("I could not do that.", GOOD)
ok("an unreadable reply spends the repair round instead of raising", got["title"] == GOOD["title"]
   and len(b.calls) == 2, len(b.calls))
ok("...and the second ask says why", "could not be used" in b.calls[1]["prompt"])

print("\n-- no lists means the box's lists, never none (review item 10) --")
class BoxSettings:
    def lists(self):
        return {"never_words": ("call",), "never_phrases": (), "competitors": (), "allowed_numbers": ()}
    def facts(self):
        return ("The box is yours.",)
_real_settings = writer.settings
writer.settings = BoxSettings()
bad = {**GOOD, "short_answer": "Never miss a call."}
b = Brain(json.dumps(bad), json.dumps(GOOD))
writer.brain = b
writer.write("q")
ok("a writer called with no lists checks the draft against the box's own", len(b.calls) == 2, len(b.calls))
ok("...and is given the box's facts", "The box is yours." in b.calls[0]["prompt"])
writer.settings = _real_settings

print("\n-- the writer reads the article the way the publisher does, markup included --")
in_code = {**GOOD, "body_markdown": "Run:\n\n```\nmissed_call()\n```"}
in_code["body_markdown"] = "Run:\n\n```\ncall()\n```"
got, b = run(in_code, GOOD, lists={"never_words": ("call",)})
ok("a banned word in a code block is caught by the writer, not first at the door", len(b.calls) == 2,
   len(b.calls))

print("\n-- the machine reasons in exactly one place --")
import ast, glob
callers = []
for path in sorted(glob.glob("marketing/aeo_machine/*.py")):
    tree = ast.parse(open(path).read())
    if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
           and n.func.attr == "think" for n in ast.walk(tree)):
        callers.append(path.split("/")[-1])
ok("only writer.py calls think() — everything else is deterministic (CLAUDE.md §3)",
   callers == ["writer.py"], callers)

print(f"\n{_failed} FAILED" if _failed else "\nall ok")
sys.exit(1 if _failed else 0)
