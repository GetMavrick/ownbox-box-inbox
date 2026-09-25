"""A machine signs in to its own Claude or ChatGPT account without touching the box's.

docs/SCOPE_ONE_PLACE_PER_SETTING.md §4.3, PR 4 of the build plan: `claude_login` and
`codex_login` take `machine`. Owner, 2026-09-24: "each machine should have the choice to use the
base machine or another account."

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  · starting a machine's sign-in reaps the box's one in flight, or the other way round;
  · a machine's token lands in the box's secrets, or the box's in the machine's row;
  · a machine's ChatGPT sign-in runs in the box's CODEX_HOME (it would sign the box out);
  · the box's ChatGPT status or consent moves when a machine signs in or out;
  · a machine signed in to ChatGPT does not actually draft through its own directory.

Both CLIs are stood in for by shell scripts that print what the real ones print (see
test_you_sign_in_to_claude_from_the_box / _chatgpt_ for the transcripts) and report the account
they were run with. The detached helpers are real: they run as separate processes, as on a box.

Run: python tests/test_a_machine_signs_in_to_its_own_account.py
"""
from __future__ import annotations

import os
import pathlib
import stat
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(T, "signin.db")
os.environ["AIOS_CODEX_HOME"] = os.path.join(T, "codex")
for _k in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY"):
    os.environ.pop(_k, None)

BIN = os.path.join(T, "bin")
os.makedirs(BIN)
_CLAUDE = r'''#!/bin/sh
# setup-token: the link, the prompt, then the token named by FAKE_TOKEN once a code arrives.
printf 'Opening browser to sign in...\n\n'
printf 'https://claude.com/cai/oauth/authorize?code=true&client_id=abc&response_type=code&code_challenge=xyz&state=ST4TE\n\n'
printf 'Paste code here if prompted > '
read line
printf '\n\nYour OAuth token (valid for 1 year):\n\n%s\n\nStore this token securely.\n' "$FAKE_TOKEN"
sleep 0.2
'''
_CODEX = r'''#!/bin/sh
case "$1 $2" in
  "login --device-auth")
    printf '1. Open this link in your browser and sign in to your account\n   https://auth.openai.com/codex/device\n\n'
    printf '2. Enter this one-time code (expires in 15 minutes)\n   WXYZ-1234\n\n'
    sleep 0.3; mkdir -p "$CODEX_HOME"; echo '{"fake":1}' > "$CODEX_HOME/auth.json"; echo "Successfully logged in"; exit 0;;
  "login status")
    if [ -f "$CODEX_HOME/auth.json" ]; then echo "Logged in using ChatGPT"; else echo "Not logged in"; fi; exit 0;;
  "exec "*)
    cat > /dev/null
    if [ ! -s "$CODEX_HOME/auth.json" ]; then echo "401 Unauthorized: Missing bearer" >&2; exit 1; fi
    printf 'drafted in %s' "$CODEX_HOME"; exit 0;;
  "logout ")
    rm -f "$CODEX_HOME/auth.json"; exit 0;;
  *) echo "codex-cli 0.155.1";;
esac
'''
for name, body in (("claude", _CLAUDE), ("codex", _CODEX)):
    p = os.path.join(BIN, name)
    with open(p, "w") as f:
        f.write(body)
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
os.environ["PATH"] = BIN + os.pathsep + os.environ.get("PATH", "")

from core import state                                                   # noqa: E402

state.init_db()

from core import box_secrets as bs, brain, claude_login, codex_login     # noqa: E402
from core import machine_accounts as ma                                  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def wait(pred, secs=20.0):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.2)
    return pred()


BOX_TOKEN = "sk-ant-oat01-" + "B" * 60
OWN_TOKEN = "sk-ant-oat01-" + "M" * 60

try:
    print("test_two_claude_sign_ins_run_side_by_side")
    os.environ["FAKE_TOKEN"] = OWN_TOKEN
    url_m = claude_login.start(machine="inbox")
    os.environ["FAKE_TOKEN"] = BOX_TOKEN
    url_b = claude_login.start()
    ok("both started and returned a sign-in link", url_m.startswith("https://claude.com/")
       and url_b.startswith("https://claude.com/"), f"{url_m[:40]} {url_b[:40]}")
    ok("starting the box's did not end the machine's",
       claude_login.in_progress(machine="inbox") and claude_login.in_progress())
    ok("...and each hands back its own link on a reload",
       claude_login.pending_url(machine="inbox") == url_m and claude_login.pending_url() == url_b)

    print("\ntest_each_token_lands_in_its_own_place")
    claude_login.finish("code-for-the-machine", user_id="usr_owner", machine="inbox")
    ok("the machine's token is the machine's", ma.account("inbox") and
       ma.account("inbox")["value"] == OWN_TOKEN and ma.account("inbox")["kind"] == "claude_oauth")
    ok("...and not the box's", bs.get(bs.CLAUDE_OAUTH) == "")
    ok("the box's login is still waiting", claude_login.in_progress())
    claude_login.finish("code-for-the-box", user_id="usr_owner")
    ok("the box's token is the box's", bs.get(bs.CLAUDE_OAUTH) == BOX_TOKEN)
    ok("...and the machine's is unchanged", ma.account("inbox")["value"] == OWN_TOKEN)
    ok("both logins are over", not claude_login.in_progress() and not claude_login.in_progress(machine="inbox"))

    print("\ntest_cancel_ends_only_the_one_named")
    os.environ["FAKE_TOKEN"] = OWN_TOKEN
    claude_login.start(machine="lead")
    claude_login.start()
    claude_login.cancel(machine="lead")
    ok("cancelling the machine's leaves the box's running",
       not claude_login.in_progress(machine="lead") and claude_login.in_progress())
    claude_login.cancel()

    print("\ntest_a_machine_signs_in_to_its_own_chatgpt")
    box_status_before = bs.get(bs.CODEX_STATUS)
    got = codex_login.start(machine="inbox", user_id="usr_owner", consented=True)
    ok("it shows the link and the code", got.get("url", "").startswith("https://auth.openai.com/")
       and got.get("code") == "WXYZ-1234", str(got))
    ok("...and finishes when the CLI does", wait(lambda: codex_login.status(machine="inbox") == "done"),
       codex_login.status(machine="inbox"))
    home = ma.codex_home("inbox")
    ok("the machine's sign-in is in its own directory",
       os.path.isfile(os.path.join(home, "auth.json")), home)
    ok("...not in the box's", not os.path.isfile(os.path.join(bs.codex_home(), "auth.json")))
    ok("the machine's row says ChatGPT", ma.state_of("inbox")["kind"] == "codex")
    ok("the box's ChatGPT status did not move", bs.get(bs.CODEX_STATUS) == box_status_before,
       bs.get(bs.CODEX_STATUS))

    print("\ntest_the_machine_drafts_through_its_own_directory")
    ok("think(machine=...) runs the CLI in the machine's home",
       brain.think("inbox_draft", "hello", machine="inbox") == f"drafted in {home}")

    print("\ntest_signing_the_machine_out_leaves_the_box_alone")
    codex_login.disconnect(machine="inbox", user_id="usr_owner")
    ok("the machine is back on the Base Machine's account", ma.choice("inbox") == "box")
    ok("...its CLI file is gone", not os.path.isfile(os.path.join(home, "auth.json")))
    ok("...and the box's ChatGPT status is exactly as it was", bs.get(bs.CODEX_STATUS) == box_status_before)
finally:
    for m in (None, "inbox", "lead"):
        claude_login.cancel(m)
        codex_login.cancel(m)

print("\n— and this file cannot silently fall out of CI —")
if (ROOT / ".github").is_dir():                  # a buyer's box has no repository
    ok("test_a_machine_signs_in_to_its_own_account is in the workflow's suite list",
       "test_a_machine_signs_in_to_its_own_account" in (ROOT / ".github/workflows/tests.yml").read_text())

print("\nALL OK" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
