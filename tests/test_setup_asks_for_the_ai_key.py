"""A buyer who finishes set-up has a box that can actually draft.

WHAT WAS WRONG, verified by OSDev1 on 2026-09-17 and confirmed here. `SETUP_STEPS` was
(email, zernio). `r_setup` prints "You are set up. Everything below is connected." when every step
reads connected. The AI key was not a step — so a buyer connected Gmail and his social accounts,
read that sentence, and owned a box that could not write one reply. Drafting is what the $499 card
sells: "the box writes, a human sends".

IT IS CORE'S CREDENTIAL, WHICH IS WHY IT LIVES IN CORE. `core/onboarding.py` exists to move steps
OUT of `box_secrets` so that adding a MACHINE stops meaning editing core. The AI key belongs to no
machine — `core.brain` is the one gateway all of them reason through — so there is nothing to
register it and core asks for it directly.

THE BUG THE THIRD STEP WOULD HAVE CAUSED. `setup_state` picked a status with
`email_state() if key == "email" else zernio_state()`: correct for exactly two steps, and silently
wrong for three. The AI key would have rendered ZERNIO's status, so a buyer with a connected social
account would have read "Connected" under a key he had never pasted — the worst possible version of
this bug, since it is the one that tells him the thing he is missing is already there.

Run: python tests/test_setup_asks_for_the_ai_key.py
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "setup.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
os.environ.pop("ANTHROPIC_API_KEY", None)

from core import state  # noqa: E402

state.init_db()

from core import box_secrets as bs  # noqa: E402
from core.config import settings  # noqa: E402

settings.anthropic_api_key = ""

FAILS = []
GOOD_KEY = "sk-ant-" + "a" * 40


def ok(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        FAILS.append(label)


def quiet(fn, *a, **k):
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        return fn(*a, **k)


# ── 1. it is asked for at all ─────────────────────────────────────────────────────────────
print("\ntest_the_screen_asks_for_the_key_the_box_cannot_draft_without")

keys = [e["key"] for e in bs.setup_state()]
ok("the AI key is a set-up step", "anthropic" in keys, str(keys))
# AFTER THE CHANNELS, NOT NECESSARILY LAST. It was asserted as last until "Your phone" joined the
# list (owner, 2026-09-20: "Yes in onboarding, all machines will need this"). The reason for the
# rule is unchanged — the AI key is what the box DOES with the channels, so it cannot sit above
# them — and pinning it to the final index tested the length of the list rather than the ordering
# that matters.
ok("...and it comes after the channels, because it is what the box DOES with them",
   keys.index("anthropic") > max(keys.index("email"), keys.index("zernio")), str(keys))

step = next(e for e in bs.setup_state() if e["key"] == "anthropic")
ok("it says plainly that the key is the buyer's own, on the buyer's own bill",
   "your own key" in step["why"] and "own bill" in step["why"], step["why"])
ok("...and that nothing is sent automatically, which is the whole product promise",
   "Nothing is sent automatically" in step["why"])
ok("...and what a box WITHOUT one still does, so the step reads as a choice not a wall",
   "still reads everything" in step["note"], step["note"])
ok("the field is a password field — a key is never echoed back onto a screen",
   step["fields"][0]["type"] == "password")


# ── 2. THE BUG A THIRD STEP WOULD HAVE CAUSED ────────────────────────────────────────────
print("\ntest_the_third_step_does_not_inherit_zernios_status")

import core.vendors.zernio.verify as zverify  # noqa: E402

zverify.verify_key = lambda key: (True, "")                     # type: ignore[assignment]
quiet(bs.put_zernio, "BUYERS_ZERNIO_KEY")

got = {e["key"]: e["status"] for e in bs.setup_state()}
ok("zernio reads connected, because it is", got["zernio"] == "connected", str(got))
ok("AND THE AI KEY DOES NOT — the old binary would have handed it Zernio's answer",
   got["anthropic"] == "not_connected", str(got))
ok("...nor does the mailbox, which is the other half of the same mistake",
   got["email"] == "not_connected", str(got))
ok("...nor the phone, which no credential on this screen can turn on",
   got["phone"] == "not_connected", str(got))

ok("an unknown step key resolves to nothing, never to the last branch's answer",
   bs._STATE_READERS.get("a-step-nobody-wrote") is None)


# ── 3. "You are set up" cannot be reached without it ─────────────────────────────────────
print("\ntest_you_are_set_up_now_means_it")


def done_count():
    return sum(1 for e in bs.setup_state() if e["status"] == "connected")


total = len(bs.setup_state())
ok("with the channels connected the box is NOT finished any more",
   done_count() < total, f"{done_count()}/{total}")

quiet(bs.put, bs.ANTHROPIC, GOOD_KEY)
ok("the key is stored and reads connected",
   {e["key"]: e["status"] for e in bs.setup_state()}["anthropic"] == "connected")
# `total` counts every rendered step, including the optional phone one that no credential on
# this screen can finish — so "everything but the mailbox" is total minus the mailbox minus
# the optional steps, not total minus one.
_optional = sum(1 for e in bs.setup_state() if e.get("optional"))
ok("...and now only the mailbox is outstanding", done_count() == total - 1 - _optional,
   f"{done_count()}/{total} with {_optional} optional")


# ── 4. the shape check, and what it deliberately does not do ─────────────────────────────
print("\ntest_the_key_is_shape_checked_and_never_sent_to_the_vendor")

for bad, why in ((" ", "blank"), ("hunter2", "the wrong thing entirely"),
                 ("sk-ant-short", "half a key"),
                 (f"sk-ant-{'a' * 20} {'a' * 20}", "a space INSIDE the key"),
                 (f"sk-ant-{'a' * 20}\n{'a' * 20}", "a line break inside the key")):
    try:
        quiet(bs.put, bs.ANTHROPIC, bad)
        said = ""
    except bs.SecretRejected as e:
        said = str(e)
    ok(f"{why} is refused with a sentence the buyer can act on", bool(said), f"{bad!r} was accepted")

# A TRAILING SPACE IS STRIPPED, NOT REFUSED, and that is deliberate rather than an oversight:
# `validate` strips before it checks, exactly as the app-password path does, because a stray space
# on the end of a paste is the buyer's clipboard and not his mistake. Only whitespace INSIDE the
# key is a real problem, and that is what the loop above pins.
quiet(bs.put, bs.ANTHROPIC, GOOD_KEY + "  ")
ok("a trailing space is STRIPPED rather than refused — that is the clipboard, not a mistake",
   bs.anthropic_key() == GOOD_KEY, repr(bs.anthropic_key()))

import contextlib  # noqa: E402
import inspect  # noqa: E402
import types  # noqa: E402

# THIS ASSERTION USED TO READ "NO ROUND TRIP TO ANTHROPIC, DELIBERATELY", and the argument under
# it was that only Anthropic can say whether a key works, so refusing a valid one on a stale regex
# would be the worse bug. The first half of that is the case FOR asking them; what it was really
# defending was guessing. So the property splits in two, and both are now pinned.
#
# `validate` STAYS FREE AND OFFLINE. It is the rule every writer of a key goes through, it runs on
# the way in, and it must cost nothing: it catches the wrong THING entirely — a password, a Zernio
# key, half a paste — without spending a round trip on it.
ok("the shape check itself still makes no network call, so the cheap rule stays cheap",
   "anthropic" not in inspect.getsource(bs.validate).lower().replace("anthropic_shape", "")
   or "requests" not in inspect.getsource(bs.validate))
# AND THE ROUND TRIP EXISTS, ONE DOOR OUT. Measured on an exported box on 2026-09-17: without it a
# well-formed dud was stored, `can_think()` said (True, 'api'), and the settings row stated "Drafts
# On. Ownbox writes a reply for every message that arrives" on a box that never would.
ok("...and the front door DOES ask the vendor before it stores anything",
   "verify_key" in inspect.getsource(bs.put_anthropic))


# ── 5. a key in the environment IS a working box ─────────────────────────────────────────
print("\ntest_a_key_in_the_environment_counts_because_brain_uses_it")

quiet(bs.clear, bs.ANTHROPIC)
ok("with nothing anywhere, the row is honest", bs.anthropic_state()["status"] == "not_connected")
os.environ["ANTHROPIC_API_KEY"] = GOOD_KEY
ok("A BOX WHOSE KEY IS IN .env CAN DRAFT, so the screen must not send its owner shopping",
   bs.anthropic_state()["status"] == "connected", str(bs.anthropic_state()))
ok("...and `brain` reads the same value, so the row and the behaviour cannot disagree",
   bs.anthropic_key() == GOOD_KEY)
os.environ.pop("ANTHROPIC_API_KEY", None)

ok("the row never carries the key itself", GOOD_KEY not in str(bs.setup_state()))


# ── 6. the screen can save it, or the step is decoration ─────────────────────────────────
print("\ntest_the_step_can_actually_be_saved")

from marketing.customer_voice import app as _app  # noqa: E402


@contextlib.contextmanager
def vendor_says_yes():
    """A stand-in `anthropic` for the length of a block, so this suite makes no network call.

    `anthropic>=0.40` is a real dependency (pyproject.toml:7). On a machine that has it installed,
    saving a made-up key here would make a REAL call and get a REAL 401 — red for the wrong
    reason, and impossible in a sandbox with no egress. `sys.modules` beats the installed package,
    so the same stub serves a machine with the SDK and one without."""
    mod = types.ModuleType("anthropic")

    class _A:
        def __init__(self, **kw):
            self.models = type("M", (), {"list": lambda _s, **_k: object()})()
    mod.Anthropic = _A
    was = sys.modules.get("anthropic")
    sys.modules["anthropic"] = mod
    try:
        yield
    finally:
        sys.modules["anthropic"] = was if was is not None else sys.modules.pop("anthropic", None)


with vendor_says_yes():
    quiet(_app._setup_save, "anthropic", {"key": GOOD_KEY}, user_id=None)
ok("the save path the screen posts to stores the key", bs.anthropic_key() == GOOD_KEY)
try:
    with vendor_says_yes():
        quiet(_app._setup_save, "anthropic", {"key": "nope"}, user_id=None)
    refused = False
except bs.SecretRejected:
    refused = True
# THE SHAPE CHECK, NOT THE VENDOR — the stub above says yes to everything, so a refusal here can
# only have come from `validate`. That is the point: the cheap rule still runs first and still
# catches the wrong thing entirely without spending anything.
ok("...and refuses a bad one through the same door, before any round trip", refused)

# ASSERTED ON THE COPY AS IT IS RENDERED, not on the source text. My first version of this check
# searched the whole function for "Two things only you can do" and failed — on MY OWN COMMENT,
# which quotes the old sentence to explain why it went. A test that greps for prose finds the
# prose explaining the fix.
src = inspect.getsource(_app.r_setup)
ok("THE HEADING COUNTS THE STEPS rather than saying 'Two', which my third step made false",
   'quiet">Two things' not in src and "len(steps)" in src)
ok("...and with three steps it reads 'Three things', in words rather than a digit",
   'Three things' in src and '{_n} only you can do' in src)

print("\n— the model list cannot promise what the box cannot do —")
# OWNER, 2026-09-21, asked for the other three shown GREYED OUT, and greyed out is a promise
# about honesty: `core/brain.py` speaks to Anthropic and nothing else, so a SELECTABLE ChatGPT
# would take a buyer's sk-... , store it, and never draft a reply. That is the "fake feature" he
# called out on his own box the same day.
#
# THIS TEST IS THE LINK BETWEEN THE TWO. A provider may only be `available: True` if brain.py
# actually has a client for it — so the day somebody enables one without doing the backend work,
# this goes red instead of a customer finding out.
import pathlib as _pl  # noqa: E402

_brain_path = _pl.Path(__file__).resolve().parents[1] / "core/brain.py"
_brain = _brain_path.read_text().lower() if _brain_path.is_file() else ""
# The evidence is the thing that DRAFTS, not a string that could be in the file for any reason:
# "openai" appears in brain.py merely because OPENAI_API_KEY is scrubbed from a subprocess, so the
# ChatGPT path is evidenced by `codex` — the CLI it actually reasons through.
_EVIDENCE = {"claude": "anthropic", "openai": "codex", "gemini": "genai", "grok": "xai"}
from core import box_secrets as _bsx  # noqa: E402
for _m in _bsx.DRAFTING_MODELS:
    if _m.get("available"):
        ok(f"{_m['id']} is offered AND brain.py can reach it",
           (not _brain) or _EVIDENCE.get(_m["id"], _m["id"]) in _brain,
           f"no {_EVIDENCE.get(_m['id'])} client in core/brain.py — this option would store a key "
           f"and never draft")
    else:
        ok(f"{_m['id']} is offered but greyed out, and says so",
           "coming soon" in str(_m.get("note", "")).lower(), str(_m.get("note")))
# AT LEAST ONE, NOT EXACTLY ONE. This counted to 1 while Claude was the only login built; that
# was a snapshot of a Monday, not a rule, and it went red the day ChatGPT shipped (2026-09-22)
# for a change that was correct. What must never be true is a box with NOTHING live — a screen
# offering four greyed-out options and no way to draft — and the loop above is what keeps every
# live one honest.
ok("at least one model is live, or a buyer has no way to draft at all",
   any(m.get("available") for m in _bsx.DRAFTING_MODELS),
   str([m["id"] for m in _bsx.DRAFTING_MODELS if m.get("available")]))
ok("all four launch assistants can connect over MCP",
   {c["id"] for c in _bsx.AGENT_CLIENTS} == {"claude", "chatgpt", "gemini", "grok"},
   str(sorted(c["id"] for c in _bsx.AGENT_CLIENTS)))

print("\n— no set-up step sends a buyer to a URL nobody has opened —")
# THE OWNER HIT A 404 MID-ONBOARDING, 2026-09-21, on https://zernio.com/accounts: a path nobody
# had ever fetched, on the step whose entire job is to send him somewhere. app.py already carried
# the note ("the site root, not a guessed deep link") and this file's steps ignored it.
#
# SO THE RULE IS MECHANICAL, BECAUSE JUDGEMENT ALREADY FAILED ONCE: an outbound link in a set-up
# step is a site ROOT, unless its exact URL is on the list below — and a URL gets on that list by
# a person loading it, not by looking plausible. A vendor's paths are theirs to change without
# telling us, and one extra click costs a buyer far less than a 404 does while we are asking them
# for an API key.
from urllib.parse import urlsplit  # noqa: E402

from core import box_secrets as _bs  # noqa: E402

# url -> who opened it, and when. Nothing else may carry a path.
VERIFIED_DEEP_LINKS = {
    "https://zernio.com/dashboard/connections": "owner, 2026-09-21, read off his own dashboard",
}

for _step in tuple(_bs.SETUP_STEPS) + (_bs._AI_STEP, _bs._PHONE_STEP):
    _url = str((_step.get("link") or {}).get("url") or "")
    if not _url.startswith("http"):
        continue
    _path = urlsplit(_url).path
    ok(f"{_step['key']}: {_url}",
       _path in ("", "/") or _url in VERIFIED_DEEP_LINKS,
       f"path {_path!r} is neither a root nor on the verified list — has anyone opened it?")

print("\n— and this file cannot silently fall out of CI —")
import pathlib  # noqa: E402

_wf = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml"
if _wf.is_file():
    ok("test_setup_asks_for_the_ai_key is in the workflow's suite list",
       "test_setup_asks_for_the_ai_key" in _wf.read_text())
else:
    print("  --   no workflow file here (a box, not the repo) — nothing to check")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
