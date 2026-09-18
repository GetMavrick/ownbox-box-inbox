"""A buyer with a Claude subscription and no API key can set up their box.

WHY THIS EXISTS. Owner, 2026-09-18, signing up as customer #1 on go-to-market day:

    "Make sure to enable Oauth. I am going to sign up as Customer number one and that must be
     available when I set up my box. It has to because I don't have an API key."

And the ruling underneath it, which is his and not ours to re-litigate:

    "that should be the choice of the client. They are taking ownership of these boxes and
     they're using them most often for an individual so they have to review their own terms and
     be responsible for themselves. We are not the police."

WHAT WAS BROKEN. `export_box.sh` writes `backend: api` into every box it builds, and `_backend()`
read that config and nothing else. So the set-up screen could only ask for an API key, and a buyer
whose only credential is a Claude subscription — the owner among them — had no way in at all. The
box was not refusing them; it could not see them.

THE SHAPE OF THE FIX: one field takes either credential, routed by prefix, and CONNECTING a
subscription token is what selects the subscription backend. No radio button, no second form, and
no config edit a buyer would never find.

WHAT THIS SUITE HOLDS:
  · a subscription token is stored as one, and an API key still takes its own verified path
  · connecting a token switches the backend — the whole point, and it is asserted, not assumed
  · the owner's box, configured `claude_code`, is untouched by any of it
  · a box with NEITHER credential says so in a sentence instead of failing silently
  · the token reaches the CLI, which is the only way it can ever work on a sold box
  · the terms are LINKED, because the owner ruled the choice is the buyer's to make informed

Run: python tests/test_a_buyer_can_connect_their_claude_subscription.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "sub.db")
# THIS SUITE MUST NOT READ THE MACHINE IT RUNS ON for a fact the product branches on — OSDev5's
# rule, 2026-09-18, which cost four assertions that day and a red PR the same morning. A dev box
# carries these; a customer's box and CI do not, and the answers differ.
for _v in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
    os.environ.pop(_v, None)

from core import state  # noqa: E402

state.init_db()

from core import box_secrets as bs, brain  # noqa: E402

FAILS: list[str] = []

# Shapes only — neither is a real credential and neither is ever sent anywhere.
API_KEY = "sk-ant-api03-" + "a" * 60
SUB_TOKEN = "sk-ant-oat01-" + "b" * 60


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        FAILS.append(label)


def clean() -> None:
    bs.clear_claude_oauth()
    bs.clear_anthropic()


# ── 1. one field, two credentials, routed by what was pasted ─────────────────────────────
print("\ntest_the_one_field_knows_which_credential_it_was_given")

clean()
ok("a subscription token is recognised as one", bs.looks_like_subscription(SUB_TOKEN))
ok("...and an API key is not", not bs.looks_like_subscription(API_KEY))

# THE ORDER OF THE PREFIX TEST IS THE WHOLE TRICK, and getting it backwards is silent: both
# credentials start `sk-ant-`, so a check on the SHORTER prefix first files every subscription
# token as an API key and sends it to an endpoint that can only refuse it.
ok("...even though BOTH start sk-ant-, which is why the longer prefix is asked about first",
   API_KEY.startswith("sk-ant-") and SUB_TOKEN.startswith("sk-ant-"))

clean()
ok("pasting a subscription token stores it as a subscription",
   bs.put_ai_credential(SUB_TOKEN, consented=True) == "subscription")
ok("...and it is readable back through the resolver the brain uses",
   bs.claude_oauth_token() == SUB_TOKEN)

# MOVING TO A SUBSCRIPTION MEANS MOVING. An API key left behind as a silent fallback is how a
# box keeps billing an account its owner believed they had stopped using.
ok("...and the API key it replaces is gone, not left as a silent fallback",
   bs.anthropic_key() == "", repr(bs.anthropic_key()))

# SAVED IS NOT CONNECTED. Nothing has thought with this token yet, and a screen that says
# "connected" about something untested is the claim this repo keeps deleting.
ok("...and its state reads `saved`, never `connected`, because nothing has tested it",
   bs.claude_oauth_state()["status"] == "saved", str(bs.claude_oauth_state()))


# ── 2. connecting it is what chooses the backend ─────────────────────────────────────────
print("\ntest_connecting_a_subscription_is_what_switches_the_backend")

# The shipped config of every sold box says `api` — that is what export_box.sh writes. These
# assertions are therefore about a box exactly as a customer receives it.
cfg_backend = (brain.get_config().get("brain") or {}).get("backend", "api")

clean()
ok("with nothing connected the box is on the API backend, as every box is today",
   brain._backend() == "api" or cfg_backend == "claude_code", brain._backend())

ready, why = brain.can_think()
ok("...and a box with NEITHER credential says so in a sentence rather than failing silently",
   ready is False or cfg_backend == "claude_code", f"{ready} {why}")

bs.put_ai_credential(SUB_TOKEN, consented=True)
ok("connecting a subscription token SWITCHES the backend — the point of the whole change",
   brain._backend() == "claude_code", brain._backend())

clean()
bs.put(bs.ANTHROPIC, API_KEY)          # `put`, to store a key without a live vendor probe
ok("...while an API key still puts the box on the API backend",
   brain._backend() == "api" or cfg_backend == "claude_code", brain._backend())


# ── 3. the owner's existing box is not re-decided underneath him ─────────────────────────
print("\ntest_the_owner_box_keeps_behaving_exactly_as_it_does_today")

# His box carries `backend: claude_code` in config and its token in /opt/aios/.env. Config still
# wins outright, so nothing about this change reaches a box that was already working.
clean()
_real = brain.get_config


def _as_owner_box():
    cfg = dict(_real())
    cfg["brain"] = {"backend": "claude_code"}
    return cfg


brain.get_config = _as_owner_box                       # type: ignore[assignment]
try:
    ok("config claude_code still wins outright, with nothing in the secrets table",
       brain._backend() == "claude_code", brain._backend())
    os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = SUB_TOKEN
    ok("...and the environment is still read first, exactly as the API key is",
       bs.claude_oauth_token() == SUB_TOKEN, bs.claude_oauth_token()[:12])
finally:
    os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    brain.get_config = _real                           # type: ignore[assignment]


# ── 4. the token actually reaches the CLI ────────────────────────────────────────────────
print("\ntest_the_token_reaches_the_cli_which_is_the_only_way_it_can_work")

# ON A SOLD BOX THE TOKEN IS IN THE DATABASE, NOT THE ENVIRONMENT. If `_run_claude` passed the
# inherited environment through unchanged, the CLI would run unauthenticated and stop on a login
# prompt nobody can see. Everything above would still pass and the box would draft nothing.
clean()
bs.put_ai_credential(SUB_TOKEN, consented=True)
seen: dict = {}
_real_run = brain.subprocess.run


def _fake_run(cmd, **kw):
    seen.update(kw.get("env") or {})

    class R:
        returncode, stdout, stderr = 0, '{"result": "hi", "is_error": false}', ""
    return R()


brain.subprocess.run = _fake_run                       # type: ignore[assignment]
try:
    brain._run_claude(["claude", "--version"], timeout=5)
finally:
    brain.subprocess.run = _real_run                   # type: ignore[assignment]

ok("the stored token is handed to the CLI in its environment",
   seen.get("CLAUDE_CODE_OAUTH_TOKEN") == SUB_TOKEN, str(seen.get("CLAUDE_CODE_OAUTH_TOKEN"))[:20])

# A COPY, NOT os.environ ITSELF. Mutating the real environment to pass one argument leaks a live
# credential into every unrelated subprocess the box ever spawns, and into any crash dump of them.
ok("...without leaking it into this process's own environment",
   os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") is None,
   str(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")))


# ── 5. the choice is the buyer's, made in front of the terms ─────────────────────────────
print("\ntest_the_buyer_is_shown_the_terms_rather_than_told_the_answer")

step = next(s for s in bs.SETUP_STEPS if s["key"] == "anthropic")
ok("the set-up step links a vendor's own terms",
   all(u.startswith("https://") for _, u in step.get("terms_links") or ()),
   str(step.get("terms_links")))
ok("...and both vendors' terms are offered, not just the one we happen to use",
   len(step.get("terms_links") or ()) == 2, str(step.get("terms_links")))
ok("...and names where to ask for help instead",
   "help@ownbox.io" in (step.get("terms_note") or ""))
ok("...and the field asks for either credential, not only a key",
   "subscription" in step["fields"][0]["label"].lower(), step["fields"][0]["label"])

# THE BOX STATES A CONDITION; IT DOES NOT REFUSE ANYBODY. The owner ruled this explicitly —
# "We are not the police" — so a suite that let a refusal creep back in would be reversing him.
clean()
ok("a subscription token is ACCEPTED, not refused with a lecture about terms",
   bs.put_ai_credential(SUB_TOKEN) == "subscription")


# ── 6. NOTHING IS REFUSED — the box is the buyer's ───────────────────────────────────────
print("\ntest_the_box_does_not_police_what_its_owner_does_with_their_own_account")

# Owner, 2026-09-18, after an earlier cut of this made the tick a CONDITION: "We are not policing
# people's usage of their own property. They buy these boxes from us and they want to do what they
# want with the box and they can review their own fucking terms."
#
# THIS IS THE ASSERTION THAT KEEPS THAT TRUE. A future reader with good intentions and a nervous
# afternoon will be tempted to put the gate back; this turns that into a red suite rather than a
# surprise on a customer's box.
clean()
stored = bs.put_ai_credential(SUB_TOKEN)              # no tick, deliberately
ok("an UNTICKED subscription token is stored — the box refuses nobody",
   stored == "subscription" and bs.claude_oauth_token() == SUB_TOKEN, str(stored))
ok("...and the box thinks on it, so nothing is withheld either",
   brain._backend() == "claude_code", brain._backend())

# The tick still means something when it is given — it costs the buyer nothing and answers the
# question later if anyone asks. It is never required and never checked before a store.
clean()
bs.put_ai_credential(SUB_TOKEN, consented=True, user_id="owner")
ok("ticking it IS recorded, with when and who",
   "owner" in bs.oauth_consent_record(), bs.oauth_consent_record())
ok("...and the record never carries the token itself",
   SUB_TOKEN not in bs.oauth_consent_record())

clean()
bs.put_ai_credential(SUB_TOKEN)
ok("...while leaving it unticked records nothing, rather than a false consent",
   bs.oauth_consent_record() == "", bs.oauth_consent_record())

bs.put_ai_credential(SUB_TOKEN, consented=True, user_id="owner")
bs.clear_claude_oauth()
ok("clearing the subscription clears its consent record too",
   bs.oauth_consent_record() == "", bs.oauth_consent_record())


# ── 7. both vendors' terms, one click away ───────────────────────────────────────────────
print("\ntest_the_page_carries_both_vendors_terms_so_a_buyer_can_read_their_own")

step7 = next(s for s in bs.SETUP_STEPS if s["key"] == "anthropic")
from marketing.customer_voice import app as _cv_app  # noqa: E402

html = _cv_app._setup_consent(step7)
ok("Anthropic's terms are linked", bs.ANTHROPIC_TERMS_URL in html, html[:100])
ok("...and OpenAI's are too, because a buyer's subscription may be either",
   bs.OPENAI_TERMS_URL in html, html[:100])
ok("...both opening away from the box rather than navigating out of set-up",
   html.count('rel="noopener noreferrer"') == 2, str(html.count('rel="noopener noreferrer"')))
ok("...and the tick is there to take, not to demand",
   f'name="{step7["consent_field"]}"' in html and "required" not in html)

# THE COPY IS INFORMATION, NOT A WARNING ABOUT THEIR OWN CONDUCT.
ok("the text says whose box and whose account this is",
   "your box" in (step7.get("terms_warning") or "").lower(), str(step7.get("terms_warning"))[:70])
ok("...and says plainly that nothing is withheld either way",
   "nothing here is withheld" in (step7.get("terms_note") or "").lower(),
   str(step7.get("terms_note"))[:70])

# A STEP THAT DECLARES NO CONSENT DRAWS NOTHING — this stays a contract, not a branch on a name.
ok("a step with no consent_field renders nothing at all",
   _cv_app._setup_consent({"key": "email"}) == "")


# ── 8. THE SCREEN AGREES WITH THE BOX ────────────────────────────────────────────────────
print("\ntest_the_screen_says_connected_once_the_box_is_actually_connected")

# MEASURED, NOT IMAGINED. On 2026-09-18 a real subscription token was posted through the real
# form: it stored, `_backend()` switched to claude_code, `can_think()` said ready — and the set-up
# screen still read "Not connected yet" while the dashboard kept counting three things to connect.
# `anthropic_state()` asked only about the API key, so the step that takes TWO credentials could
# only ever see one of them.
#
# IT IS THE WORST SHAPE A BUG CAN HAVE HERE: the product works and tells its owner it does not. He
# pastes what he was asked for, nothing changes on screen, and the only sane conclusion is that it
# failed. The owner was hours from filming exactly that for investors.
clean()
ok("with nothing connected the AI step reads not_connected",
   bs.anthropic_state()["status"] == "not_connected", str(bs.anthropic_state()))

bs.put_ai_credential(SUB_TOKEN, consented=True)
ok("a SUBSCRIPTION alone makes the AI step read connected — not just the API key",
   bs.anthropic_state()["status"] == "connected", str(bs.anthropic_state()))

# AND THE COUNT THE DASHBOARD SHOWS IS DRIVEN BY THE SAME READER, so this is the assertion that
# keeps "1 of 3 connected" true rather than "3 things to connect" over a working box.
states = {e["key"]: e["status"] for e in bs.setup_state()}
ok("...so the set-up list counts it as done, which is what the dashboard card reads",
   states.get("anthropic") == "connected", str(states))

# THE BRAIN AND THE SCREEN MUST NOT DISAGREE IN EITHER DIRECTION. A screen that says connected
# over a box that cannot think is the same defect wearing the other mask.
#
# But a CI runner is not a box. It never runs the bootstrap, so it has no `claude` CLI, and a bare
# `is True` here fails for a reason that has nothing to do with this fix. Every SOLD box has the CLI
# (#1381 installs it at first boot; measured 2.1.277 on a clone of snapshot 246025588), so where the
# CLI is present the brain must agree outright. Where it is absent the assertion still has teeth: the
# brain must blame the missing CLI and never the credential the buyer just pasted — telling those two
# apart is exactly what #1384 is for, and getting it backwards sends a buyer hunting for a new key.
_ready, _why = brain.can_think()
if shutil.which("claude"):
    ok("...and the box really can think, so the screen is not merely optimistic",
       _ready is True, _why)
else:
    ok("...and with no CLI installed the brain blames the CLI, never the credential",
       _ready is False and "CLI" in _why and "key" not in _why.lower(), _why)

clean()
ok("clearing it puts the step back to not_connected, rather than stranding a green row",
   bs.anthropic_state()["status"] == "not_connected", str(bs.anthropic_state()))


print("\n— and this file cannot silently fall out of CI —")
import pathlib  # noqa: E402

_wf = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml"
if _wf.is_file():
    ok("test_a_buyer_can_connect_their_claude_subscription is in the workflow's suite list",
       "test_a_buyer_can_connect_their_claude_subscription" in _wf.read_text())
else:
    print("  --   no workflow file here (a box, not the repo) — nothing to check")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:")
    for f in FAILS:
        print("   -", f)
    sys.exit(1)
print("ALL OK")
