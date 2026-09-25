"""A machine can think on its own AI account, and falls back to the Base Machine's without stopping.

Owner, 2026-09-24: "The base machine should have an LLM account. And each machine should have the
choice to use the base machine or another account." And on what happens when that account needs
signing in again: "I don't want any lost functionality for any period of time" (§7 question 1).
Plan: docs/SCOPE_ONE_PLACE_PER_SETTING.md §4.2, PR 3.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  · `think()` with no machine, or a machine with no account of its own, stops thinking on the
    box's account exactly as it did (the byte-for-byte promise);
  · a machine's own account is not the one a call actually uses, on any of the three backends;
  · a refused own account stops the call instead of falling back, or falls back without saying so;
  · a transient failure switches accounts (it must retry on the machine's own);
  · a machine's ChatGPT failure is written onto the BOX's status;
  · switching back to the Base Machine's account keeps the machine's credential.

No network: the Anthropic SDK and both CLIs are stood in for, and each reports which account it
was handed, so every assertion is about which credential a real call would have carried.

Run: python tests/test_a_machine_can_think_on_its_own_account.py
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "own.db")
os.environ["AIOS_CODEX_HOME"] = os.path.join(tempfile.mkdtemp(), "codex")
for _k in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
    os.environ.pop(_k, None)

from core import state                                                   # noqa: E402

state.init_db()

from core import box_secrets, brain, machine_accounts as ma              # noqa: E402
from core.config import get_config                                       # noqa: E402
from core.exceptions import RetryableError                               # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


BOX_KEY = "sk-ant-api03-" + "b" * 60
OWN_KEY = "sk-ant-api03-" + "o" * 60
OWN_TOKEN = "sk-ant-oat01-" + "t" * 60

# ── stand-ins that say which account they were handed ────────────────────────────────────────
FAIL: dict[str, Exception] = {}          # api key -> the error its next call raises


class _Status(Exception):
    def __init__(self, msg, status):
        super().__init__(msg)
        self.status_code = status


class _Anthropic:
    def __init__(self, api_key, **_):
        self.key = api_key
        self.messages = types.SimpleNamespace(create=self._create)

    def _create(self, **_):
        if self.key in FAIL:
            raise FAIL[self.key]
        usage = types.SimpleNamespace(input_tokens=1, output_tokens=1,
                                      cache_creation_input_tokens=0, cache_read_input_tokens=0)
        return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=f"api:{self.key[-4:]}")],
                                     usage=usage, stop_reason="end_turn")


sys.modules["anthropic"] = types.SimpleNamespace(Anthropic=_Anthropic)
CLI: dict[str, object] = {}              # what the fake CLI returns next: (rc, stdout, stderr)


def _run(cmd, **kw):
    env = kw.get("env") or {}
    CLI["env"] = env
    if "exec" in cmd:                    # codex
        rc, out, err = CLI.pop("codex", (0, f"codex:{env.get('CODEX_HOME')}", ""))
    else:                                # claude
        rc, out, err = CLI.pop("claude", (0, json.dumps({"result": f"cc:{env.get('CLAUDE_CODE_OAUTH_TOKEN', '')[-4:]}",
                                                         "usage": {}}), ""))
    return subprocess.CompletedProcess(cmd, rc, out, err)


brain.subprocess.run = _run
brain.shutil.which = lambda name: f"/usr/bin/{name}"
_cfg = get_config().setdefault("brain", {})
_old_backend = _cfg.get("backend")
_cfg["backend"] = "api"
box_secrets.put(box_secrets.ANTHROPIC, BOX_KEY)


def think(machine=None):
    return brain.think("inbox_draft", "hello", max_tokens=16, machine=machine)


try:
    print("test_no_machine_is_the_box_exactly_as_before")
    ok("think() with no machine uses the box's key", think() == f"api:{BOX_KEY[-4:]}", think())
    ok("...and a machine with no account of its own uses it too",
       think("inbox") == f"api:{BOX_KEY[-4:]}")
    ok("...recording no fallback, because nothing fell back",
       ma.state_of("inbox") == {"choice": "box", "kind": "", "status": "", "detail": "",
                                "fell_back_at": ""})
    ok("the call context is empty again afterwards", brain._CALL.get() is None)

    print("\ntest_a_machine_thinks_on_its_own_api_key")
    ma.put("inbox", "anthropic_key", OWN_KEY, user_id="usr_owner")
    ok("its call carries its own key", think("inbox") == f"api:{OWN_KEY[-4:]}", think("inbox"))
    ok("...while another machine and the box keep the box's key",
       think("lead") == f"api:{BOX_KEY[-4:]}" and think() == f"api:{BOX_KEY[-4:]}")
    ok("the page sees the choice and the kind, never the key",
       ma.state_of("inbox")["choice"] == "own" and OWN_KEY not in json.dumps(ma.state_of("inbox")))

    print("\ntest_a_refused_own_account_falls_back_and_says_so")
    FAIL[OWN_KEY] = _Status("authentication_error: invalid x-api-key", 401)
    ok("the call still returns, written on the box's key", think("inbox") == f"api:{BOX_KEY[-4:]}")
    st = ma.state_of("inbox")
    ok("...the machine's account is marked as needing sign-in", st["status"] == "needs_reauth", str(st))
    ok("...and the fallback is recorded for the page", bool(st["fell_back_at"]))
    ok("...the choice is still the machine's own", st["choice"] == "own")
    ok("...and the box's own status is untouched",
       box_secrets.get(box_secrets.ANTHROPIC_STATUS) in ("", "connected"))
    FAIL.pop(OWN_KEY)
    ok("while it needs sign-in, calls go straight to the box's account",
       think("inbox") == f"api:{BOX_KEY[-4:]}")
    ma.put("inbox", "anthropic_key", OWN_KEY)
    ok("signing in again brings the machine back to its own", think("inbox") == f"api:{OWN_KEY[-4:]}")

    print("\ntest_a_transient_failure_does_not_switch_accounts")
    FAIL[OWN_KEY] = _Status("overloaded", 529)
    raised = None
    try:
        think("inbox")
    except RetryableError as e:
        raised = e
    ok("it raises RetryableError so the job retries", raised is not None)
    ok("...on the machine's own account, whose status stays connected",
       ma.state_of("inbox")["status"] == "connected")
    FAIL.pop(OWN_KEY)

    print("\ntest_out_of_credit_falls_back_and_says_so")
    FAIL[OWN_KEY] = _Status("Your credit balance is too low to access the Anthropic API", 400)
    ok("the call returns on the box's key", think("inbox") == f"api:{BOX_KEY[-4:]}")
    ok("...and the machine's account is marked as needing payment",
       ma.state_of("inbox")["status"] == "payment_required")
    FAIL.pop(OWN_KEY)

    print("\ntest_a_machine_on_its_own_claude_subscription")
    ma.put("inbox", "claude_oauth", OWN_TOKEN)
    ok("the Claude CLI is handed the machine's token", think("inbox") == f"cc:{OWN_TOKEN[-4:]}",
       str(CLI.get("env", {}).get("CLAUDE_CODE_OAUTH_TOKEN", ""))[-6:])
    ok("...even though the box itself thinks on its API key", think() == f"api:{BOX_KEY[-4:]}")
    CLI["claude"] = (1, "", "Invalid API key · Please run /login")
    ok("an expired sign-in falls back to the box", think("inbox") == f"api:{BOX_KEY[-4:]}")
    ok("...and says the machine needs signing in", ma.state_of("inbox")["status"] == "needs_reauth")

    print("\ntest_a_machine_on_its_own_chatgpt_sign_in")
    ma.put("inbox", "codex")
    home = ma.codex_home("inbox")
    ok("the ChatGPT CLI runs from the machine's own directory",
       think("inbox") == f"codex:{home}" and home != box_secrets.codex_home(), think("inbox"))
    box_before = box_secrets.get(box_secrets.CODEX_STATUS)
    CLI["codex"] = (1, "", "401 Unauthorized: Missing bearer")
    ok("a refused ChatGPT sign-in falls back to the box", think("inbox") == f"api:{BOX_KEY[-4:]}")
    ok("...the verdict goes on the machine", ma.state_of("inbox")["status"] == "needs_reauth")
    ok("...and NOT on the box's ChatGPT status",
       box_secrets.get(box_secrets.CODEX_STATUS) == box_before, box_secrets.get(box_secrets.CODEX_STATUS))

    print("\ntest_switching_back_forgets_the_machines_account")
    ma.put("inbox", "anthropic_key", OWN_KEY)
    ok("forget says it had one", ma.forget("inbox") is True)
    ok("...the choice is the box's again", ma.choice("inbox") == "box")
    ok("...and the call uses the box's key", think("inbox") == f"api:{BOX_KEY[-4:]}")
    with state.connect() as c:
        left = c.execute("SELECT COUNT(*) n FROM machine_accounts WHERE value = ?", (OWN_KEY,)).fetchone()["n"]
    ok("...with the machine's key gone from the box", left == 0)

    print("\ntest_the_store_refuses_what_it_should")
    for bad, why in ((("Inbox", "anthropic_key", OWN_KEY), "a key that is not a machine name"),
                     (("inbox", "gemini", OWN_KEY), "a kind the box cannot think on"),
                     (("inbox", "anthropic_key", "  "), "an empty key")):
        try:
            ma.put(*bad)
            refused = False
        except ValueError:
            refused = True
        ok(f"refused: {why}", refused)
finally:
    if _old_backend is None:
        _cfg.pop("backend", None)
    else:
        _cfg["backend"] = _old_backend

print("\n— and this file cannot silently fall out of CI —")
if (ROOT / ".github").is_dir():                  # a buyer's box has no repository
    ok("test_a_machine_can_think_on_its_own_account is in the workflow's suite list",
       "test_a_machine_can_think_on_its_own_account" in (ROOT / ".github/workflows/tests.yml").read_text())

print("\nALL OK" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
