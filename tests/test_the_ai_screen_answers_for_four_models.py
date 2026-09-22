"""`/settings/ai` answers for all four models, and takes a key for none it cannot draft with.

WHAT THIS IS ABOUT. Owner, 2026-09-22, looking at his own box: *"he removed this drop-down, which
allowed you to choose which LLM you were going to connect. There were four choices in it, Claude,
ChatGPT, Gemini and GROK. We need to put that back."* And then: *"the page is gonna have to be
completely different according to which drop-down is chosen… you should do the research and start
writing a copy for each selection."*

IT WAS NEVER REMOVED, WHICH IS THE INTERESTING PART. `_AI_STEP["choose"]` has carried all four
models the whole time and the inbox's wizard drew it. When the step moved into core (#1418) it
landed on a screen with no renderer for a picker — so a declared, tested, four-entry control
silently stopped being on anybody's screen, with nothing going red. That is the failure this
suite exists to make loud: the contract declaring a thing is not the same as a buyer seeing it.

THE ONE RULE THAT MUST SURVIVE THE FEATURE. A screen that accepts a credential `core/brain.py`
cannot draft with stores something that never writes a reply — the "fake feature" the owner named
on 2026-09-21. Selecting a model the box cannot use is therefore allowed to change the whole page
and is NOT allowed to offer anywhere to type. Both halves are asserted below, because the first
without the second is how that bug comes back.

AND THE RULE IS ASSERTED AGAINST THE TABLE, NEVER AGAINST A NAME. Until 2026-09-22 this suite
said "available ⇒ the page has a key field", which was a true sentence about Claude wearing the
costume of a general one. ChatGPT went live the same day as a SIGN-IN with no key at all and took
it red. What is asserted now is the shape the product actually promises: every live model offers
a control that connects that model, on the screen that owns it, and no model offers a field for a
credential it does not have.

AND THE TICK MOVED. *"I have reviewed tick box needs to be moved up to the use your claude
subscription. It's in the wrong box."* It sat in the "Or paste a key" card — the one card on the
screen where what you are connecting is not a subscription — and the sign-in path, which IS the
subscription, recorded `consented=True` with nobody ever having been shown a box to tick.

Run: python tests/test_the_ai_screen_answers_for_four_models.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "aiscreen.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
# NEVER READ THE MACHINE YOU RUN ON for a fact the product branches on. Our dev boxes carry these
# and CI carries none; a suite that passes on only one of the two is worse than no suite.
for _k in [k for k in list(os.environ) if k.startswith("ZERNIO_API_KEY")] + ["ANTHROPIC_API_KEY"]:
    os.environ.pop(_k, None)

from core import state                                                   # noqa: E402

state.init_db()

from core import box_secrets, dash                                       # noqa: E402
from core.dispatch import app                                            # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def owner_client():
    c = app.test_client()
    c.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
    return c


def body(path: str) -> str:
    return owner_client().get(path).get_data(as_text=True)


def forms(html: str) -> list:
    """Every <form> on the page, as its own string. The question "which card is the tick in" is a
    question about which FORM submits it — a checkbox in another form is not on the journey."""
    return re.findall(r"(?s)<form\b.*?</form>", html)


MODELS = box_secrets.DRAFTING_MODELS
IDS = [m["id"] for m in MODELS]


# ── 1. the picker is back, with all four in it ──────────────────────────────────────────────
print("\ntest_the_picker_offers_the_four_the_contract_declares")

html = body("/settings/ai")
ok("the screen answers the owner", "Your AI account" in html)
ok("the contract still declares four models", len(MODELS) == 4, str(IDS))
ok("...and they are Claude, ChatGPT, Gemini and Grok",
   set(IDS) == {"claude", "openai", "gemini", "grok"}, str(sorted(IDS)))
ok("the screen draws a <select> for them", "<select" in html and 'name="model"' in html)
for m in MODELS:
    ok(f"{m['id']}: is an option on the screen",
       f'value="{m["id"]}"' in html, m["id"])
    ok(f"{m['id']}: ...and is SELECTABLE, because choosing it only shows you a page",
       f'value="{m["id"]}" disabled' not in html and f'disabled value="{m["id"]}"' not in html)

# THE CAPTION MUST NOT ARGUE WITH THE CONTROL UNDER IT. It read "Only the ones your box can
# actually use are selectable", which was true of the machine's greyed-out copy and became false
# the moment core's picker let you choose one to read about. Found by rendering, 2026-09-22.
_note = ((box_secrets.SETUP_STEPS and next(
    (e for e in box_secrets.SETUP_STEPS if e["key"] == "anthropic"), {})).get("choose") or {}
    ).get("note", "")
ok("the picker's caption does not claim the others are unselectable",
   "selectable" not in _note.lower(), _note)


# ── 2. choosing one changes the page, and each has its own words ────────────────────────────
print("\ntest_each_model_gets_its_own_page_and_its_own_copy")

pages = {m["id"]: body(f"/settings/ai?model={m['id']}") for m in MODELS}
for m in MODELS:
    p = pages[m["id"]]
    ok(f"{m['id']}: the page names it", m["name"] in p, m["name"])
    ok(f"{m['id']}: ...and is SELECTED in the picker afterwards",
       f'value="{m["id"]}" selected' in p or f'selected>{m["name"]}' in p)

# The four pages are genuinely different documents, not one page with a logo swapped. Comparing
# them to each other is the only way to assert that without freezing the copy itself.
_bodies = {k: v for k, v in pages.items()}
ok("no two models render the same page",
   len({v for v in _bodies.values()}) == 4, str(len({v for v in _bodies.values()})))

for m in MODELS:
    if m.get("available"):
        continue
    p = pages[m["id"]]
    ok(f"{m['id']}: says plainly the box cannot draft on it yet",
       "cannot draft on" in p, m["id"])
    # THE RESEARCH IS THE PRODUCT HERE. Two of these three surprise somebody holding a $20
    # subscription, and the sentence that says so is the reason the panel is worth rendering.
    ok(f"{m['id']}: tells a buyer their consumer subscription is not the thing",
       (m.get("subscription") is False) and ("not include API" in p or "does not cover" in p))
    ok(f"{m['id']}: names where the key would come from",
       bool(m.get("key_from")) and m["key_from"][0] in p, str(m.get("key_from")))


# ── 3. and offers nowhere to type a credential it cannot use ────────────────────────────────
print("\ntest_a_model_the_box_cannot_use_is_offered_no_field_at_all")

for m in MODELS:
    p = pages[m["id"]]
    has_key_field = 'name="key"' in p
    if m.get("available"):
        # A LIVE MODEL MUST OFFER A WAY IN — BUT NOT NECESSARILY A FIELD. This test used to read
        # "available ⇒ there is a key field", which was true while Claude was the only live one
        # and became wrong the hour ChatGPT went live: that one is a sign-in with no key anywhere
        # in it. The invariant that actually holds is that every live model gives the buyer a
        # control that connects THAT model, and every model the box cannot use gives them none.
        ok(f"{m['id']}: the live one offers a way to connect it",
           has_key_field or any('value="start"' in f for f in forms(p)))
        # AND IT POSTS TO THE SCREEN THAT OWNS IT. A Connect button on the ChatGPT page that
        # posted to Claude's route is the exact defect this whole branch exists to remove.
        href = m.get("connect_href") or "/settings/ai"
        ok(f"{m['id']}: ...on its own screen, not another model's",
           any(f'action="{href}"' in f and 'value="start"' in f for f in forms(p)), href)
        # NO FIELD FOR A CREDENTIAL THIS MODEL DOES NOT HAVE. ChatGPT declares no `key_label`,
        # so a key field on its page would be somewhere to put something unusable.
        if not m.get("key_label"):
            ok(f"{m['id']}: ...and no key field, because it has no key", not has_key_field)
    else:
        # A KEY THAT SITS THERE AND NEVER WRITES A REPLY IS WORSE THAN NO KEY. This is the whole
        # reason the other three were greyed out in the first place, kept across a redesign that
        # made them selectable.
        ok(f"{m['id']}: offers no field to paste a key into", not has_key_field)
        ok(f"{m['id']}: ...and no form that would save one",
           not any('value="key"' in f for f in forms(p)))

# AN UNTRUSTED STRING IS MATCHED AGAINST THE CONTRACT, NEVER INTERPOLATED.
junk = body("/settings/ai?model=<script>alert(1)</script>")
ok("a made-up model falls back to the live one rather than rendering itself",
   "<script>alert(1)</script>" not in junk and 'name="key"' in junk)


# ── 4. the tick is in the card the owner put it in ──────────────────────────────────────────
print("\ntest_the_consent_tick_travels_with_the_subscription_it_is_about")

field = next(e for e in box_secrets.SETUP_STEPS if e["key"] == "anthropic")["consent_field"]
claude = pages["claude"]
ok("the tick is on the screen at all", f'name="{field}"' in claude, field)

start_form = [f for f in forms(claude) if 'value="start"' in f]
key_form = [f for f in forms(claude) if 'value="key"' in f]
ok("the subscription card has a form that starts the sign-in", len(start_form) == 1)
ok("the paste-a-key card has one too", len(key_form) == 1)
ok("the tick is submitted BY the sign-in, which is what it is about",
   bool(start_form) and f'name="{field}"' in start_form[0])
ok("...and not by the key form, where a subscription is not what you are connecting",
   bool(key_form) and f'name="{field}"' not in key_form[0])

# WHAT THE TICK IS FOR IS A RECORD, AND A RECORD NOBODY GAVE IS THE BUG. The sign-in path stored
# `consented=True` unconditionally while the only box to tick sat in the other card.
import inspect                                                           # noqa: E402

from core import claude_login                                            # noqa: E402

ok("claude_login.start takes the tick rather than assuming it",
   "consented" in inspect.signature(claude_login.start).parameters)
# READ THE CODE, NOT THE PROSE. The first cut of this grepped the source for `consented=True`
# and went red on the docstring EXPLAINING that the hard-coded one was removed — a guard that
# cannot tell an assertion from a comment about it is a guard that punishes writing the comment.
import ast                                                               # noqa: E402

_tree = ast.parse(inspect.getsource(claude_login))
_hard = [n for n in ast.walk(_tree) if isinstance(n, ast.Call)
         for kw in n.keywords
         if kw.arg == "consented" and isinstance(kw.value, ast.Constant) and kw.value.value is True]
ok("...and no call in it passes consent as a literal True", not _hard, f"{len(_hard)} call(s)")

# NEVER REQUIRED, ON THE OWNER'S 2026-09-18 RULING: a box refuses nobody over our tick.
ok("the tick is not a `required` field", 'required' not in (start_form[0] if start_form else ""))
box_secrets.put_claude_oauth("sk-ant-oat01-" + "z" * 40, consented=False)
ok("a token given without the tick is stored anyway",
   bool(box_secrets.get(box_secrets.CLAUDE_OAUTH)))
ok("...and records no consent that was never given",
   not box_secrets.oauth_consent_record())
box_secrets.put_claude_oauth("sk-ant-oat01-" + "y" * 40, consented=True)
ok("...while a tick that IS given is written down", bool(box_secrets.oauth_consent_record()))


print()
if _failed:
    print(f"{_failed} FAILED")
    sys.exit(1)
print("all good")
