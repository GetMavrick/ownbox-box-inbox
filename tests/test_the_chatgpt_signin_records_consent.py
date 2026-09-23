"""The ChatGPT sign-in records the tick, exactly as the Claude one does — or records nothing.

WHAT WAS WRONG. #1423 landed the ChatGPT device-code sign-in with no consent row and no tick on
the screen. Signing in there runs a box on a consumer subscription in precisely the way signing in
with Claude does, so the gate legal counsel asked for (owner, 2026-09-18, relaying his counsel)
applied to both and shipped on one. A box could be drafting on somebody's ChatGPT subscription
with nothing on record that they had ever been shown the terms.

THE RULE IS RECORD, NEVER REQUIRE, and this suite pins both halves. The owner ruled on 2026-09-18
that a box does not refuse its owner's own credential — "we are not the police" — so an unticked
sign-in must complete normally and simply write nothing down. A test that only checked the happy
path would let somebody "fix" this into a gate, which is the thing he overruled.

WHY THE TICK TRAVELS IN A FILE. The form that carries it and the helper that finishes the login
are different processes minutes apart; the request is long gone when the CLI returns. So `start`
writes the answer into the session directory and the helper reads it on success — and ONLY on
success, because a tick recorded at button-press would evidence consent for a sign-in that never
happened.

Run: python tests/test_the_chatgpt_signin_records_consent.py
"""
from __future__ import annotations

import ast
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "consent.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
# NEVER READ THE MACHINE YOU RUN ON for a fact the product branches on — our boxes carry these
# and CI carries none, and a suite that passes on only one of the two is worse than no suite.
os.environ.pop("ANTHROPIC_API_KEY", None)
# The CLI's credential lives under this; the suite never runs the CLI, but `codex_status()` looks
# for the file, so it is pointed somewhere empty rather than at a real box's directory.
os.environ["AIOS_CODEX_HOME"] = os.path.join(_T, "codex")

from core import state                                                   # noqa: E402

state.init_db()

from core import box_secrets, codex_login, dash                          # noqa: E402
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


SRC = pathlib.Path(__file__).resolve().parents[1]
FIELD = next(e for e in box_secrets.SETUP_STEPS if e["key"] == "anthropic")["consent_field"]


# ── 1. the row exists at all, and is the twin of Claude's ───────────────────────────────────
print("\ntest_the_box_has_somewhere_to_record_the_chatgpt_tick")

ok("there is a consent row for the ChatGPT sign-in", bool(box_secrets.CODEX_CONSENT))
ok("...distinct from Claude's, because they are different decisions",
   box_secrets.CODEX_CONSENT != box_secrets.CLAUDE_OAUTH_CONSENT)
ok("...and it is readable by whoever has to answer for it later",
   callable(getattr(box_secrets, "codex_consent_record", None)))

box_secrets.note_codex_consent(user_id="usr_owner")
rec = box_secrets.codex_consent_record()
ok("recording it writes WHEN and WHO", bool(rec) and "usr_owner" in rec, rec)
# THE ROW NEVER HOLDS A CREDENTIAL. It is evidence of a decision, not a secret, and a consent
# record that carried a token would turn an audit answer into a leak.
ok("...and nothing that looks like a credential", "sk-" not in rec, rec)

# ITS LIFETIME IS THE SIGN-IN'S. A tick left behind would be inherited by whoever signs in next.
box_secrets.clear_codex(user_id="usr_owner")
ok("disconnecting forgets the tick with the sign-in it was given for",
   box_secrets.codex_consent_record() == "", box_secrets.codex_consent_record())


# ── 2. it is carried by the login, and written only when the login works ────────────────────
print("\ntest_the_tick_travels_with_the_signin_and_lands_only_on_success")

sig = codex_login.start.__kwdefaults__ or {}
ok("codex_login.start takes the tick rather than assuming it", "consented" in sig, str(sig))
ok("...and defaults to NOT consented, so silence is never read as a yes",
   sig.get("consented") is False, repr(sig.get("consented")))

src = (SRC / "core/codex_login.py").read_text()
tree = ast.parse(src)
# AST, NOT GREP — a comment or a docstring mentioning the argument must not satisfy this.
literal_true = [
    n for n in ast.walk(tree)
    if isinstance(n, ast.Call)
    for kw in n.keywords
    if kw.arg == "consented" and isinstance(kw.value, ast.Constant) and kw.value.value is True]
ok("...and no call inside it passes consent as a literal True", not literal_true)

# THE WRITE IS ON THE SUCCESS PATH, NOT THE START PATH. `note_codex_consent` must be reachable
# only after the CLI has been asked whether it worked — the same shape `claude_login` uses.
writes = [n for n in ast.walk(tree)
          if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
          and n.func.attr == "note_codex_consent"]
ok("the consent is written exactly once, somewhere in this module", len(writes) == 1,
   str(len(writes)))
_serve = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_serve"), None)
ok("...inside the helper that waits for the CLI, not inside start()",
   _serve is not None and any(w in ast.walk(_serve) for w in writes))
_start = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "start"), None)
ok("...and start() records nothing, because the sign-in has not happened yet",
   _start is not None and not any(w in ast.walk(_start) for w in writes))


# ── 3. the screen offers it, with the terms beside it, and never demands it ──────────────────
print("\ntest_the_screen_shows_the_tick_and_the_terms")

html = owner_client().get("/settings/chatgpt").get_data(as_text=True)
ok("the ChatGPT screen renders for the owner", "ChatGPT" in html)
ok("...and carries the tick", f'name="{FIELD}"' in html, FIELD)

import re                                                                # noqa: E402

forms = re.findall(r"(?s)<form\b.*?</form>", html)
start_form = [f for f in forms if 'value="start"' in f]
ok("...on the form that starts the sign-in, which is what it is about",
   len(start_form) == 1 and f'name="{FIELD}"' in start_form[0])

# NEVER `required`. The owner's ruling is that the box refuses nobody; a browser-side gate would
# reimpose exactly the condition he struck out, and it would do it invisibly.
tick = re.search(rf'<input[^>]*name="{re.escape(FIELD)}"[^>]*>', html)
ok("...and it is not a `required` field", bool(tick) and "required" not in tick.group(0),
   tick.group(0) if tick else "no tick rendered")

# BOTH VENDORS' TERMS, ONE CLICK AWAY. Owner, 2026-09-18: the information is what we owe somebody
# standing here. A tick with nothing to read beside it is the decoration this gate must not be.
for label, url in box_secrets.TERMS_LINKS:
    ok(f"...with {label} linked beside it", url in html, url)


# ── 4. and an unticked sign-in is not refused ────────────────────────────────────────────────
print("\ntest_nothing_is_refused_for_want_of_a_tick")

# THE HALF THAT IS EASY TO "FIX" INTO A BUG. Owner, 2026-09-18, on a box somebody owns outright:
# we do not police what a person does with their own property. So the screen must offer the tick
# and the store must survive its absence.
box_secrets.clear_codex(user_id="usr_owner")
box_secrets.note_codex_status("connected", user_id="usr_owner")
ok("a sign-in with the box unticked is still recorded as a sign-in",
   box_secrets.get(box_secrets.CODEX_STATUS) == "connected")
ok("...and records no consent that was never given",
   box_secrets.codex_consent_record() == "", box_secrets.codex_consent_record())
box_secrets.note_codex_consent(user_id="usr_owner")
ok("...while a tick that IS given is written down",
   bool(box_secrets.codex_consent_record()))


# ── 5. the comment that told this session the wrong thing ───────────────────────────────────
print("\ntest_the_file_does_not_claim_a_gate_it_does_not_have")

# THIS IS WHY THE SUITE HAS A FIFTH SECTION. The comment on CLAUDE_OAUTH_CONSENT said
# `put_claude_oauth` "refuses without it" — true of the first cut, overruled by the owner the
# same day, and still sitting in the file months later. It was read back as fact and repeated to
# him as fact. A sentence describing a rule has to live next to the code that enforces it.
secrets_src = (SRC / "core/box_secrets.py").read_text()
head = secrets_src[:secrets_src.index("def get(")]
ok("no constant claims the store refuses a token without the tick",
   "refuses without it" not in head, "the stale claim is back in the header")

# AND THE BEHAVIOUR IT WAS WRONG ABOUT, ASSERTED DIRECTLY so the comment can never drift alone.
box_secrets.clear_claude_oauth(user_id="usr_owner")
box_secrets.put_claude_oauth("sk-ant-oat" + "a" * 40, user_id="usr_owner")
ok("a Claude token given without the tick is stored anyway",
   box_secrets.get(box_secrets.CLAUDE_OAUTH).startswith("sk-ant-oat"))
ok("...and records no consent either", box_secrets.oauth_consent_record() == "",
   box_secrets.oauth_consent_record())


print("\n" + ("all good" if not _failed else f"{_failed} FAILED"))
sys.exit(1 if _failed else 0)
