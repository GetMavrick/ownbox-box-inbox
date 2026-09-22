"""You sign in to ChatGPT from the box: a link, a one-time code, and the box drafts on it.

Owner, 2026-09-21: "We need the Claude login. And then we're gonna need the ChatGPT login right
behind it." The second of the four logins the product sells.

WHAT IS FAKED AND WHY. A `codex` shell script stands in for the CLI, printing EXACTLY what
codex-cli 0.155.1 printed on the test box on 2026-09-22 (the device-code text this module parses),
and answering `login status`, `exec` and `logout` the way the real one does — including the
measured not-signed-in failure ("401 Unauthorized: Missing bearer"). Every failure path a buyer
meets alone is here: the admin-disabled refusal, an expired code, an absent CLI, a member at the
door.

Run: python tests/test_you_sign_in_to_chatgpt_from_the_box.py
"""
import os
import stat
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(T, "gpt.db")
os.environ["AIOS_CODEX_HOME"] = os.path.join(T, "codex-home")
os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ["OPENAI_API_KEY"] = "sk-must-not-reach-the-cli"     # proves the env is scrubbed

# ── the fake CLI ────────────────────────────────────────────────────────────────────────────
BIN = os.path.join(T, "bin"); os.makedirs(BIN)
FAKE = os.path.join(BIN, "codex")
with open(FAKE, "w") as f:
    f.write(r'''#!/bin/sh
case "$1 $2" in
  "login --device-auth")
    printf 'Welcome to Codex [v0.155.1]\n\nFollow these steps to sign in with ChatGPT using device code authorization:\n\n'
    printf '1. Open this link in your browser and sign in to your account\n   https://auth.openai.com/codex/device\n\n'
    printf '2. Enter this one-time code (expires in 15 minutes)\n   WXYZ-1234\n\n'
    printf 'Continue only if you started this login in Codex. If a website or another person gave you this code, cancel.\n'
    case "$FAKE_CODEX" in
      ok)    sleep 0.4; mkdir -p "$CODEX_HOME"; echo '{"fake":1}' > "$CODEX_HOME/auth.json"; echo "Successfully logged in"; exit 0;;
      admin) sleep 0.2; echo "Please contact your workspace admin to enable device code authentication" >&2; exit 1;;
      hang)  sleep 30; exit 1;;
      *)     exit 1;;
    esac;;
  "login status")
    if [ -f "$CODEX_HOME/auth.json" ]; then echo "Logged in using ChatGPT"; else echo "Not logged in"; fi; exit 0;;
  "exec "*)
    printf '%s\n' "$@" > "$CODEX_HOME/last_argv"; env > "$CODEX_HOME/last_env"; cat > "$CODEX_HOME/last_stdin"
    if [ ! -s "$CODEX_HOME/auth.json" ]; then echo "ERROR: unexpected status 401 Unauthorized: Missing bearer or basic authentication in header" >&2; exit 1; fi
    echo "progress that must not be mistaken for the answer" >&2
    printf 'Yes — we open at nine.'; exit 0;;
  "logout ")
    rm -f "$CODEX_HOME/auth.json"; exit 0;;
  *) echo "codex-cli 0.155.1";;
esac
''')
os.chmod(FAKE, os.stat(FAKE).st_mode | stat.S_IEXEC)
os.environ["PATH"] = BIN + os.pathsep + os.environ.get("PATH", "")

from core import brain, cost_guard, state                       # noqa: E402
from core import box_secrets as bs                              # noqa: E402
from core import codex_login, dash, watchdog                    # noqa: E402
from core.dispatch import app                                   # noqa: E402

state.init_db()
cost_guard.check_vendor = lambda v, u=1, now=None: None
# A SOLD BOX IS NOT PINNED TO A BACKEND. export_box.sh writes `backend: api` into every box it
# builds; the repo's own config pins the owner's box to claude_code, which would make every
# assertion below about a sold box false. Patched on the module attribute brain reads at call
# time — not at import, where it would bind (reference_config_bound_at_import).
import core.config as _cfgmod
_real_cfg = _cfgmod.get_config
def _unpinned():
    c = _real_cfg()
    return {**c, "brain": {**(c.get("brain") or {}), "backend": "api"}}
_cfgmod.get_config = _unpinned          # watchdog imports it inside the function
brain.get_config = _unpinned            # brain bound it at import
os.environ["AIOS_CODEX_APPROVE_TIMEOUT_S"] = "600"
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


def member_client():
    u = state.add_user("m@example.com", name="Member", role="member")
    c = app.test_client()
    c.set_cookie(dash.COOKIE, dash.new_session(u["id"]))
    return c


def wait_status(want, timeout=8.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if codex_login.status() == want or (want == "done" and not codex_login.status()
                                            and bs.codex_connected()):
            return True
        time.sleep(0.15)
    return False


print("test_the_setup_screen_offers_a_chatgpt_door_beside_the_claude_one")
step = next(e for e in bs.SETUP_STEPS if e["key"] == "anthropic")
ok("the AI step declares the ChatGPT door", step.get("alt_action_href") == "/settings/chatgpt")
ok("...labelled as a sign-in", "sign in" in (step.get("alt_action_label") or "").lower())
html = owner_client().get("/settings/ai").get_data(as_text=True)
ok("...and the owner's AI settings screen links to it", "/settings/chatgpt" in html)
ok("...while the Claude door is still there", "Use your Claude subscription" in html)
mhtml = member_client().get("/settings/ai").get_data(as_text=True)
ok("...and a member is not shown a door they would be refused at", "/settings/chatgpt" not in mhtml)
ok("ChatGPT is a live choice in the model list",
   next(m for m in bs.DRAFTING_MODELS if m["id"] == "openai")["available"] is True)

print("test_the_connect_screen_asks_for_nothing_secret")
r = owner_client().get("/settings/chatgpt")
body = r.get_data(as_text=True)
ok("it renders for the owner", r.status_code == 200, str(r.status_code))
ok("...offers Connect before any code exists", 'name="do" value="start"' in body)
ok("...has no input field at all — the code goes to ChatGPT, not to us", "<input name=" not in body)
ok("...says the box never sees a password", "never sees your password" in body)
ok("...and says where the switch is if ChatGPT refuses", "Security" in body)
mr = member_client().get("/settings/chatgpt")
ok("a member is refused — not 200, and no Connect button",
   mr.status_code != 200 and 'value="start"' not in mr.get_data(as_text=True), str(mr.status_code))
ok("anonymous is bounced", app.test_client().get("/settings/chatgpt").status_code in (302, 303, 401))

print("test_connect_shows_the_link_and_the_code_then_finishes_on_its_own")
os.environ["FAKE_CODEX"] = "ok"
r = owner_client().post("/settings/chatgpt", data={"do": "start"})
body = r.get_data(as_text=True)
ok("the page shows the link", "https://auth.openai.com/codex/device" in body)
ok("...and the code, large", "WXYZ-1234" in body)
ok("...and refreshes itself", 'http-equiv="refresh"' in body)
ok("the helper reached awaiting_approval or done",
   codex_login.status() in ("awaiting_approval", "done") or bs.codex_connected(), codex_login.status())
ok("...and finished when the CLI did", wait_status("done"), codex_login.status())
ok("the box now counts as connected", bs.codex_connected(), str(bs.codex_status()))
st = bs.anthropic_state()
ok("...and the AI-account reader says so", st["status"] == "connected" and st.get("provider") == "chatgpt", str(st))
r = owner_client().get("/settings/chatgpt")
ok("a reload after done goes back to set up", r.status_code in (302, 303) and "/settings" in r.headers.get("Location", ""),
   str(r.status_code))
ok("the CLI's file is where the box said it would be",
   os.path.isfile(os.path.join(os.environ["AIOS_CODEX_HOME"], "auth.json")))

print("test_the_brain_drafts_on_chatgpt_with_no_tools_and_no_key_in_the_environment")
ok("the backend is codex once signed in (no Claude token, no API key)", brain._backend() == "codex", brain._backend())
okk, why = brain.can_think()
ok("can_think says ready", okk and why == "codex", f"{okk} {why}")
out = brain.think(task="inbox_draft", prompt="Customer: are you open at nine?", system="Be brief.")
ok("the final message came back, and only it", out == "Yes — we open at nine.", repr(out))
argv = open(os.path.join(os.environ["AIOS_CODEX_HOME"], "last_argv")).read()
ok("read-only sandbox", "read-only" in argv, argv)
ok("no MCP tools", "mcp_servers={}" in argv, argv)
ok("ephemeral, no user config, no rules", all(f in argv for f in ("--ephemeral", "--ignore-user-config", "--ignore-rules")), argv)
ok("prompt on stdin", argv.strip().endswith("-"), argv)
ok("no model invented by default", "\n-m\n" not in argv, argv)
stdin = open(os.path.join(os.environ["AIOS_CODEX_HOME"], "last_stdin")).read()
ok("system text goes first, then the prompt", stdin.index("Be brief.") < stdin.index("Customer:"), stdin[:80])
envd = open(os.path.join(os.environ["AIOS_CODEX_HOME"], "last_env")).read()
ok("OPENAI_API_KEY never reaches the CLI", "sk-must-not-reach-the-cli" not in envd)
ok("CODEX_HOME points at the box's own directory", f"CODEX_HOME={os.environ['AIOS_CODEX_HOME']}" in envd)

print("test_a_revoked_sign_in_is_named_not_retried_forever")
os.remove(os.path.join(os.environ["AIOS_CODEX_HOME"], "auth.json"))
bs.note_codex_status("connected")                      # the box still THINKS it is signed in...
open(os.path.join(os.environ["AIOS_CODEX_HOME"], "auth.json"), "w").close()   # ...file present but empty
try:
    brain.think(task="inbox_draft", prompt="x")
    ok("a 401 raises", False, "no raise")
except brain.RetryableError as e:
    ok("a 401 raises", False, f"retryable, would loop: {e}")
except Exception as e:                                          # noqa: BLE001
    ok("a 401 raises a terminal error, not a retry", "not signed in" in str(e), str(e)[:120])
ok("...and the status row says needs_reauth", bs.codex_status() == "needs_reauth", bs.codex_status())
ok("...so the AI-account reader tells the owner", bs.anthropic_state()["status"] == "needs_reauth", str(bs.anthropic_state()))

print("test_disconnect_signs_the_box_out")
owner_client().post("/settings/chatgpt", data={"do": "disconnect"})
ok("not connected any more", not bs.codex_connected() and bs.codex_status() == "")
ok("the file is gone", not os.path.exists(os.path.join(os.environ["AIOS_CODEX_HOME"], "auth.json")))
ok("the backend falls back", brain._backend() == "api", brain._backend())

print("test_the_admin_switch_refusal_says_where_the_switch_is")
os.environ["FAKE_CODEX"] = "admin"
r = owner_client().post("/settings/chatgpt", data={"do": "start"})
wait_status("error", 6.0)
r = owner_client().get("/settings/chatgpt")
body = r.get_data(as_text=True)
ok("the buyer is told about Settings → Security", "Security" in body and "device code" in body, body[-400:])
ok("...and offered Connect again", 'value="start"' in body)

print("test_an_expired_code_is_a_sentence")
os.environ["FAKE_CODEX"] = "hang"
os.environ["AIOS_CODEX_APPROVE_TIMEOUT_S"] = "1"      # reaches the helper process
r = owner_client().post("/settings/chatgpt", data={"do": "start"})
ok("the code showed", "WXYZ-1234" in r.get_data(as_text=True))
ok("...then expired into a sentence", wait_status("error", 8.0) and "expired" in codex_login.pending().get("error", ""),
   str(codex_login.pending()))
codex_login.cancel()
os.environ["AIOS_CODEX_APPROVE_TIMEOUT_S"] = "600"

print("test_the_watchdog_probes_chatgpt_without_spending")
os.environ["FAKE_CODEX"] = "ok"
owner_client().post("/settings/chatgpt", data={"do": "start"})
wait_status("done")
key, good, detail = watchdog.probe_backend()
ok("the probe is the codex one", key == "codex", key)
ok("...and it is green", good, detail)

print("test_without_the_cli_the_screen_says_so")
os.environ["PATH"] = os.environ["PATH"].replace(BIN + os.pathsep, "", 1)
r = owner_client().post("/settings/chatgpt", data={"do": "start"})
ok("an absent CLI is a sentence with a way out", "not installed" in r.get_data(as_text=True))

print("\nFAILED" if _failed else "\nALL PASS")
sys.exit(1 if _failed else 0)
