"""ChatGPT as the box's AI, end to end, with the REAL codex CLI (owner, 2026-10-02: his box thinks on ChatGPT for his
investor demo; OSDev1's assignment).

WHAT IS REAL AND WHAT IS STOOD IN. The `codex` binary is the real one the box installs (scripts/install_codex.sh,
codex-cli 0.155.1). What it talks to is a local stand-in for OpenAI: the sign-in server (device code, then a token
exchange that hands back a FAKE token), the ChatGPT backend and the model's Responses stream. A two-line wrapper
named `codex` points the real CLI at the stand-in (`--experimental_issuer` for the sign-in, a stand-in model provider
for `exec`, and the token-refresh address), and changes nothing else. No account, no request to OpenAI. The Claude
sign-in is held the same way (tests/test_the_real_cli_draws_the_token.py: real CLI output, a stand-in token server).

Held here, through the box's own code:
  1. sign in from Settings: the page shows the link and the code as the REAL CLI prints them (in colour, whatever
     NO_COLOR says), the helper finishes, the box is on ChatGPT;
  2. everything that thinks runs on it: the gate's AI check (core/key_features.py), Today's brief (core/brief.py),
     Ask your box (core/ask.py), an inbox draft (drafter/draft.py), and the watchdog probe (core/watchdog.py);
  3. ChatGPT refuses the sign-in: the box stays a ChatGPT box and each of those says plainly that ChatGPT isn't
     signed in and where to sign in again, never "add an Anthropic key". So does a box whose CLI file is gone.

AIOS_REAL_CODEX names the binary. CI installs it with the box's own installer and names it, so a broken install is
a failure there. Unset (a developer's machine, a box), this prints a SKIP line naming it: never a `codex` that happens to
be on PATH.
Run: python tests/test_chatgpt_thinks_end_to_end.py
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REAL = os.environ.get("AIOS_REAL_CODEX", "")
if not REAL:
    print("SKIP: needs AIOS_REAL_CODEX, a codex 0.155.1 binary (CI installs one; a skip there is a failure)")
    sys.exit(0)
if not (os.path.isfile(REAL) and os.access(REAL, os.X_OK)):
    print(f"FAIL AIOS_REAL_CODEX names {REAL}, and no CLI is there (CI puts it on with scripts/install_codex.sh)")
    sys.exit(1)

T = pathlib.Path(tempfile.mkdtemp())
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "gpt.db")
os.environ["AIOS_CODEX_HOME"] = str(T / "codex-home")
os.environ["DISPATCH_BEARER_TOKEN"], os.environ["DASH_TOKEN"] = "bearer", "pw"
for _k in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY"):
    os.environ.pop(_k, None)

FAILS: list[str] = []


def ok(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:500]}"))
    if not cond:
        FAILS.append(label)


# ── the stand-in for OpenAI ─────────────────────────────────────────────────────────────────────────────────
def _b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


FAKE_JWT = ".".join([_b64({"alg": "none"}), _b64({
    "email": "owner@example.com", "exp": int(time.time()) + 86400,
    "https://api.openai.com/auth": {"chatgpt_plan_type": "plus", "chatgpt_account_id": "acct_test",
                                    "chatgpt_user_id": "user_test"}}), "not-a-signature"])
SAID = {"reply": "OK", "refuse": False, "polls": 0, "asked": 0, "tool": "", "offered": [], "tool_said": ""}


class StandIn(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code: int, obj) -> None:
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._json(404, {})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        p = self.path.split("?")[0]
        if p.endswith("/deviceauth/usercode"):
            return self._json(200, {"device_auth_id": "dev_test", "user_code": "WXYZ-1234", "interval": "1"})
        if p.endswith("/deviceauth/token"):
            SAID["polls"] += 1                           # the first poll is still waiting for the person
            return (self._json(403, {"error": "authorization_pending"}) if SAID["polls"] < 2 else
                    self._json(200, {"authorization_code": "code_test", "code_challenge": "c", "code_verifier": "v"}))
        if p.endswith("/oauth/token"):
            if SAID["refuse"]:
                return self._json(401, {"error": {"message": "refresh token revoked", "code": "invalid_grant"}})
            return self._json(200, {"id_token": FAKE_JWT, "access_token": FAKE_JWT, "refresh_token": "rt_test",
                                    "expires_in": 3600})
        if p.endswith("/codex/responses"):
            SAID["asked"] += 1
            if SAID["refuse"]:
                return self._json(401, {"error": {"message": "Your authentication token has been invalidated.",
                                                  "code": "token_invalidated"}})
            try:
                req = json.loads(raw or b"{}")
            except ValueError:
                req = {}
            # THE BOX'S TOOLS, AS THE REAL CLI OFFERS THEM (measured on 0.155.1): one namespace, mcp__aios. With
            # SAID["tool"] set, the stand-in model calls that tool first, then answers once its output comes back.
            SAID["all_tools"] = [(t.get("type"), t.get("name")) for t in req.get("tools") or []]
            box = [t for t in req.get("tools") or [] if t.get("type") == "namespace" and t.get("name") == "mcp__aios"]
            if box:
                SAID["offered"] = ["mcp__aios__" + str(x.get("name")) for x in box[0].get("tools") or []]
                SAID["others"] = sorted({t.get("name") or t.get("type") for t in req.get("tools") or []} - {"mcp__aios"})
            done = [x for x in req.get("input") or [] if x.get("type") in ("function_call_output",
                                                                        "custom_tool_call_output")]
            if done:
                SAID["tool_said"] = json.dumps(done[-1].get("output"))
                for part in done[-1].get("output") if isinstance(done[-1].get("output"), list) else []:
                    t = str(part.get("text") or "")
                    if t.startswith("[") and "mcp__aios__" in t:
                        SAID["offered"] = json.loads(t)
            # CODE MODE (measured on 0.155.1: the CLI's default model when the account names none). The tools are not
            # offered as functions: the model writes JavaScript for one `exec` tool, and the box's tools are methods on
            # `tools` (tools.mcp__aios__core_health). The script reports ALL_TOOLS too, so the test sees what it had.
            code = any(x.get("type") == "additional_tools" for x in req.get("input") or [])
            if code and SAID["tool"] and not done:
                js = (f"text(JSON.stringify(ALL_TOOLS.map(t => t.name))); "
                      f"text(JSON.stringify(await tools.mcp__aios__{SAID['tool']}({{}})));")
                item = {"type": "custom_tool_call", "id": "ctc_1", "call_id": "call_1", "name": "exec", "input": js}
            elif box and SAID["tool"] and not done:
                item = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "namespace": "mcp__aios",
                        "name": SAID["tool"], "arguments": "{}"}
            else:
                item = {"type": "message", "role": "assistant", "id": "msg_1",
                        "content": [{"type": "output_text", "text": SAID["reply"]}]}
            usage = {"input_tokens": 5, "input_tokens_details": None, "output_tokens": 4,
                     "output_tokens_details": None, "total_tokens": 9}
            ev = [("response.created", {"type": "response.created", "response": {"id": "resp_1"}}),
                  ("response.output_item.done", {"type": "response.output_item.done", "item": item}),
                  ("response.completed", {"type": "response.completed", "response": {"id": "resp_1", "usage": usage}})]
            b = "".join(f"event: {k}\ndata: {json.dumps(v)}\n\n" for k, v in ev).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return None
        return self._json(404, {})


srv = ThreadingHTTPServer(("127.0.0.1", 0), StandIn)
threading.Thread(target=srv.serve_forever, daemon=True).start()
U = f"http://127.0.0.1:{srv.server_address[1]}"

# ── the wrapper: the real CLI, pointed at the stand-in, nothing else changed ───────────────────────────────────
BIN = T / "bin"
BIN.mkdir()
# THE ONE LINE OF OUTPUT CHANGED: the CLI prints its issuer's link, here the stand-in's. The box shows only a link on
# auth.openai.com (core/codex_login.py `_URL`, a pin worth keeping), so the link is shown under OpenAI's address, the
# way the person would see it. Everything else the CLI prints, when it prints it, and its exit code pass through.
(BIN / "as_openai.py").write_text(f'''import subprocess, sys
p = subprocess.Popen(sys.argv[1:], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
for line in iter(p.stdout.readline, b""):
    sys.stdout.buffer.write(line.replace(b"{U}/codex/device", b"https://auth.openai.com/codex/device"))
    sys.stdout.buffer.flush()
sys.exit(p.wait())
''')
(BIN / "codex").write_text(f'''#!/bin/sh
export CODEX_REFRESH_TOKEN_URL_OVERRIDE="{U}/oauth/token"
if [ "$1" = "login" ] && [ "$2" = "--device-auth" ]; then
  exec "{sys.executable}" "{BIN / "as_openai.py"}" "{REAL}" "$@" --experimental_issuer "{U}"; fi
if [ "$1" = "exec" ]; then shift; exec "{REAL}" exec -c 'chatgpt_base_url="{U}/backend-api/"' \\
  -c 'model_provider="standin"' \\
  -c 'model_providers.standin={{name="standin", base_url="{U}/backend-api/codex", wire_api="responses", requires_openai_auth=true, supports_websockets=false}}' "$@"; fi
exec "{REAL}" "$@"
''')
(BIN / "codex").chmod((BIN / "codex").stat().st_mode | stat.S_IEXEC)
# NEVER THE MACHINE'S OWN CLAUDE: a `claude` that refuses, ahead of any real one on PATH.
(BIN / "claude").write_text("#!/bin/sh\necho 'this test never runs Claude' >&2\nexit 97\n")
(BIN / "claude").chmod((BIN / "claude").stat().st_mode | stat.S_IEXEC)
os.environ["PATH"] = f"{BIN}{os.pathsep}{os.environ.get('PATH', '')}"
_v = subprocess.run(["codex", "--version"], capture_output=True, text=True).stdout.strip()

from core import state  # noqa: E402

state.init_db()
from core import ai_health, ask, brain, brief, codex_login, dash, key_features, report, watchdog  # noqa: E402
from core import box_secrets as bs  # noqa: E402
from core.dispatch import app  # noqa: E402

# A SOLD BOX: `brain.backend: api`, so connecting ChatGPT is what chooses it (this repo's own config pins Claude).
import core.config as _cfgmod  # noqa: E402

_real_cfg = _cfgmod.get_config


def _as_sold():
    c = _real_cfg()
    return {**c, "brain": {**(c.get("brain") or {}), "backend": "api"}}


_cfgmod.get_config = brain.get_config = _as_sold


@contextlib.contextmanager
def spends():
    """The brief and Ask refuse to think in a test ("a test never spends"). Here the AI is a stand-in on this machine,
    so the box's own decision runs: lifted for the one call, then put back."""
    os.environ.pop("AIOS_HERMETIC_TEST", None)
    try:
        yield
    finally:
        os.environ["AIOS_HERMETIC_TEST"] = "1"


print(f"\nthe real CLI — {_v} ({REAL})")
ok("the box's `codex` is the real CLI the box installs, through the wrapper", _v == "codex-cli 0.155.1", _v)

print("\n1. sign in with ChatGPT from Settings —")
owner = app.test_client()
owner.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
ok("before: the box isn't on ChatGPT", brain._backend() != "codex", brain._backend())
body = owner.post("/settings/chatgpt", data={"do": "start", "subscription_consent": "on"}).get_data(as_text=True)
for _ in range(100):
    if "WXYZ-1234" in body or codex_login.status() == "done":
        break
    time.sleep(0.1)
    body = owner.get("/settings/chatgpt").get_data(as_text=True)
ok("the page shows the link the REAL CLI printed, in colour, whatever NO_COLOR says",
   "https://auth.openai.com/codex/device" in body and "did not return a sign-in link" not in body, body[-800:])
ok("...and the one-time code", "WXYZ-1234" in body and "Successfully logged in" not in body, body[-800:])
for _ in range(300):
    if codex_login.status() == "done" or bs.codex_connected():
        break
    time.sleep(0.1)
ok("ChatGPT approves and the helper finishes: the box is signed in", bs.codex_connected(), str(bs.codex_status()))
ok("...the real CLI agrees (`codex login status`)", codex_login.logged_in())
ok("...and the box now thinks on ChatGPT", brain._backend() == "codex" and brain.can_think() == (True, "codex"),
   f"{brain._backend()} {brain.can_think()}")

print("\n2. everything that thinks runs on ChatGPT —")
SAID["reply"] = "Yes."
n = SAID["asked"]
ok("THE GATE'S AI CHECK asks ChatGPT and passes", key_features.check_ai() is True, ai_health.last_test())
ok("...through the real `codex exec`, answered by the stand-in", SAID["asked"] == n + 1
   and (ai_health.last_test() or {}).get("answer") == "Yes.", ai_health.last_test())
ok("THE WATCHDOG PROBE: ChatGPT, signed in", watchdog.probe_backend() == ("codex", True, "signed in to ChatGPT"),
   watchdog.probe_backend())

DAY = report.today()
with state.connect() as c:
    c.execute("INSERT OR REPLACE INTO daily_reports (day, machine, report_json, written_at, final) VALUES (?,?,?,?,0)",
              (DAY.isoformat(), "customer_voice", json.dumps({
                  "machine": "customer_voice", "title": "Unified Inbox",
                  "headline": {"value": 3, "label": "waiting on you"},
                  "needs_you": [{"text": "3 conversations are waiting on your reply", "href": "/inbox/waiting"}],
                  "happened": [{"text": "messages came in", "value": 12}]}), state._now()))
f = brief.facts()
SAID["reply"] = json.dumps({"summary": "3 conversations are waiting on your reply.",
                            "top": [{"title": "Reply to the 3 conversations waiting",
                                     "why": "3 conversations are waiting on your reply",
                                     "do": "Open the inbox", "href": f["needs_you"][0]["href"]}],
                            "ask_next": [], "can_start": []})
n = SAID["asked"]
with spends():
    r = brief.refresh()
b = brief.get()
ok("TODAY'S BRIEF is written by ChatGPT", r.get("status") == "ai" and b.get("from") == "ai"
   and b["top"][0]["title"] == "Reply to the 3 conversations waiting" and SAID["asked"] == n + 1, (r, b.get("top")))

SAID["reply"] = json.dumps({"answer": "Three people are waiting on your reply.",
                            "means": "Answer them first.", "ask_next": [], "can_start": []})
n = SAID["asked"]
# THE BOX'S OWN MCP, SERVED ON LOOPBACK AS ON A BOX (127.0.0.1:8000 there), so the real CLI reaches it over HTTP
# with the ask's own seat. Unsandboxed: CI has no systemd to start a unit; the sandbox's argv is held by
# tests/test_chatgpt_asks_with_tools.py.
from werkzeug.serving import make_server  # noqa: E402

box_srv = make_server("127.0.0.1", 0, app, threaded=True)
threading.Thread(target=box_srv.serve_forever, daemon=True).start()
ask.MCP_URL = f"http://127.0.0.1:{box_srv.server_port}/mcp"
SAID["tool"] = "core_health"
_cli, CLI = brain._run_agent_cli, {}


def _heard(cmd, **kw):
    rc, out, err = _cli(cmd, **kw)
    CLI.update(rc=rc, out=out[-1500:])
    return rc, out, err


brain._run_agent_cli = _heard
with spends():
    from core.connector import seats as _seats  # noqa: E402
    _, _cred = _seats.mint("ChatGPT", "act")         # the buyer's own AI, asking through its connection
    a = ask.ask("What needs me today?", _seats.verify(_cred),
                run_agent=lambda *a_, **k: brain.run_agent(*a_, **{**k, "sandboxed": False}))
ok("ASK YOUR BOX is answered by ChatGPT, as the agent with the box's tools", a.get("answered_by") == "box_ai"
   and a.get("answer") == "Three people are waiting on your reply." and SAID["asked"] == n + 2, (a, SAID))
ok("...the real CLI offered the model the box's own tools, by name", "mcp__aios__core_health" in SAID["offered"]
   and "mcp__aios__core_ask" in SAID["offered"], (SAID["offered"], SAID.get("all_tools"), CLI))
ok("...and NOTHING ELSE THAT ACTS: no shell, no web, no images", not ({"exec_command", "shell", "web_search",
   "view_image", "image_generation"} & (set(SAID.get("others") or []) | set(SAID["offered"]))),
   (SAID.get("others"), SAID["offered"]))
ok("...the tool ran on the box, through its MCP, and its answer went back to the model",
   "box" in SAID["tool_said"].lower() and 'isError\\":true' not in SAID["tool_said"].replace(" ", ""), SAID["tool_said"][:300])
_asks = [s_ for s_ in _seats.all_seats(include_runs=True) if str(s_.get("label") or "").startswith("Ask ask_")]
ok("...on the ask's own seat, revoked when it finished", _asks and all(s_.get("revoked_at") for s_ in _asks), _asks)
SAID["tool"] = ""
brain._run_agent_cli = _cli
box_srv.shutdown()

from marketing.customer_voice.drafter import draft as D  # noqa: E402

SAID["reply"] = "Thanks for asking. We open at nine tomorrow."
n = SAID["asked"]
got = D.draft_one(space="default", zcid="c1", in_reply_to="m1", inbound="What time do you open tomorrow?",
                  history=[])
ok("AN INBOX DRAFT is written by ChatGPT", got == "Thanks for asking. We open at nine tomorrow."
   and SAID["asked"] == n + 1, got)

print("\n3. ChatGPT refuses the sign-in: every one says so, plainly —")
SAID["refuse"] = True
n = SAID["asked"]
t = ai_health.test(fresh=True)
ok("THE FIRST REFUSAL, as the real CLI words it, reads in plain words: ChatGPT isn't signed in, and where",
   t["ok"] is False and t["why"] == f"RuntimeError: {brain.CODEX_SIGNED_OUT}" and SAID["asked"] > n, (t, SAID))
ok("THE GATE'S AI CHECK fails", key_features.check_ai() is False, ai_health.state())
ok("THE BOX IS STILL A CHATGPT BOX: it never falls back to an Anthropic key it never had",
   brain._backend() == "codex", brain._backend())
ready, why = brain.can_think()
ok("can_think says it in words", ready is False and why == brain.CODEX_SIGNED_OUT, why)
ok("...never 'Anthropic' or a setting name", "Anthropic" not in why and "backend" not in why and "_" not in why, why)
ok("THE WATCHDOG PROBE fails, saying the same", watchdog.probe_backend() == ("codex", False, brain.CODEX_SIGNED_OUT),
   watchdog.probe_backend())
n = SAID["asked"]
with spends():
    a = ask.ask("What needs me today?")
ok("ASK YOUR BOX answers in plain words, and says why", a.get("answered_by") == "plain"
   and a.get("why") == brain.CODEX_SIGNED_OUT, a)
with spends():
    brief.refresh()
ok("TODAY'S BRIEF is the plain one, saying how to get insights", brief.get().get("insights") == brief.NO_AI,
   brief.get())
ok("AN INBOX DRAFT is not written, and ChatGPT isn't asked again by any of them",
   D.draft_one(space="default", zcid="c2", in_reply_to="m2", inbound="Are you open on Sunday?", history=[]) is None
   and SAID["asked"] == n, SAID)
ok("...and no Anthropic verdict was written for a ChatGPT failure", not bs.get("anthropic_status"))

print("\n4. the CLI's own file is gone (signed out outside the box) —")
SAID["refuse"] = False
bs.note_codex_status("connected")
(pathlib.Path(os.environ["AIOS_CODEX_HOME"]) / "auth.json").unlink()
ok("still a ChatGPT box, saying sign in again", brain._backend() == "codex"
   and brain.can_think() == (False, brain.CODEX_SIGNED_OUT), f"{brain._backend()} {brain.can_think()}")
ok("...and the watchdog says the same", watchdog.probe_backend() == ("codex", False, brain.CODEX_SIGNED_OUT))
state.set_alert("codex", "FAIL")
codex_login.disconnect(user_id=state.owner_user()["id"])
ok("DISCONNECT is the one way off ChatGPT", brain._backend() != "codex", brain._backend())
watchdog.resolve_deconfigured_backend_alerts(watchdog.probe_backend()[0])
ok("...and a ChatGPT alert left behind is cleared, not red forever", state.get_alert_row("codex")["state"] == "OK",
   dict(state.get_alert_row("codex") or {}))

srv.shutdown()
shutil.rmtree(T, ignore_errors=True)
print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    sys.exit(1)
print("all passed")
