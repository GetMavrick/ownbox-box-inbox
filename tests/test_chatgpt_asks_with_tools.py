"""A box that thinks on ChatGPT runs the box's AI as an agent with the box's tools (#1857 H3, core/brain.py
`_run_agent_codex`), held here with a stand-in `codex` that records what it was given. The real CLI, end to end, is
tests/test_chatgpt_thinks_end_to_end.py (CI runs it with the binary the box installs).

Measured on codex-cli 0.155.1 (2026-10-03) and held here:
  * the command: `exec --json`, read-only, every acting feature off, the box's MCP by URL with its bearer read from
    AIOS_SEAT, and the seat itself NEVER on the command line;
  * the sign-in: handed in by environment, written as the CLI's auth.json, the variable gone before the CLI starts;
  * a sign-in the CLI refreshed during the run is written back to the box (refresh tokens rotate), unless the box's
    copy changed meanwhile;
  * the answer, the turns and the tokens are read from the events; a 401, a closed window, a busy network and a run
    with no answer each end as the exception the runner already handles;
  * in the sandbox: systemd-run, the sign-in and the seat only in the root-only environment file;
  * core.ask on a ChatGPT box goes to the agent, not to one think() over stored numbers.

Run: python tests/test_chatgpt_asks_with_tools.py
"""
from __future__ import annotations

import base64
import json
import os
import pathlib
import stat
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
T = pathlib.Path(tempfile.mkdtemp())
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"

from core import state  # noqa: E402

state.init_db()
from core import box_secrets, brain  # noqa: E402
from core.coworkers import sandbox  # noqa: E402
from core.exceptions import BudgetExceeded, RetryableError  # noqa: E402

FAILS: list[str] = []


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:400]}"))
    if not cond:
        FAILS.append(label)


# ── the box: signed in to ChatGPT, its CLI file in its own CODEX_HOME ─────────────────────────────────────────────
HOME = T / "codex-home"
HOME.mkdir(mode=0o700)
AUTH = json.dumps({"auth_mode": "chatgpt", "tokens": {"refresh_token": "rt_1", "access_token": "at_1"}}).encode()
(HOME / "auth.json").write_bytes(AUTH)
box_secrets.codex_connected = lambda: True
box_secrets.codex_home = lambda: str(HOME)
NOTED = []
box_secrets.note_codex_status = lambda status, detail="", **k: NOTED.append(status)

# ── the stand-in `codex`: records argv, env, stdin and its CODEX_HOME, then does what CONTROL says ────────────────
CONTROL, SEEN = T / "control.json", T / "seen.json"
BIN = T / "bin"
BIN.mkdir()
(BIN / "codex").write_text(f'''#!{sys.executable}
import json, os, pathlib, sys
c = json.loads(pathlib.Path({str(CONTROL)!r}).read_text())
home = pathlib.Path(os.environ.get("CODEX_HOME", "/nonexistent"))
seen = {{"argv": sys.argv[1:], "env": dict(os.environ), "stdin": sys.stdin.read(),
        "auth": (home / "auth.json").read_text() if (home / "auth.json").exists() else None}}
pathlib.Path({str(SEEN)!r}).write_text(json.dumps(seen))
if c.get("refresh"):
    (home / "auth.json").write_text(c["refresh"])
if c.get("box_changed"):
    pathlib.Path(c["box_changed"]).write_text("changed by someone else")
for ev in c.get("events", []):
    print(json.dumps(ev))
sys.stderr.write(c.get("stderr", ""))
sys.exit(c.get("rc", 0))
''')
(BIN / "codex").chmod((BIN / "codex").stat().st_mode | stat.S_IEXEC)
os.environ["PATH"] = f"{BIN}{os.pathsep}{os.environ.get('PATH', '')}"


def answer(text="Three people are waiting.", calls=2, extra=()):
    ev = [{"type": "thread.started", "thread_id": "t1"},
          {"type": "item.completed", "item": {"id": "i0", "type": "error", "message": "Model metadata not found."}},
          {"type": "turn.started"}]
    for i in range(calls):
        ev.append({"type": "item.completed", "item": {"id": f"c{i}", "type": "mcp_tool_call", "server": "aios",
                                                       "tool": "core.health", "status": "completed"}})
    ev += list(extra)
    ev += [{"type": "item.completed", "item": {"id": "m", "type": "agent_message", "text": text}},
           {"type": "turn.completed", "usage": {"input_tokens": 1200, "cached_input_tokens": 300,
                                                "output_tokens": 80, "reasoning_output_tokens": 0}}]
    return ev


def control(**c):
    CONTROL.write_text(json.dumps(c))


WS = T / "workspace"
WS.mkdir()
MCP = {"url": "http://127.0.0.1:8000/mcp", "credential": "seat-SECRET-123"}
brain._backend = lambda: "codex"
brain.can_think = lambda: (True, "codex")


def run(**kw):
    args = dict(run_id="ask_test1", coworker="ask", workspace=str(WS), system="You are the box.",
                mcp=MCP, web=(), max_turns=12, max_minutes=3, task="ask", sandboxed=False)
    args.update(kw)
    return brain.run_agent("What needs me today?", **args)


print("\nThe run, on ChatGPT\n")
control(events=answer())
got = run()
seen = json.loads(SEEN.read_text())
argv = seen["argv"]
ok("it answers with the CLI's final message", got["text"] == "Three people are waiting." and got["backend"] == "codex",
   got)
ok("...counting each box tool call as a turn", got["turns"] == 2, got)
ok("...and an `error` item (a warning) does not fail it", got["text"])
ok("it runs `codex exec --json`, read-only, prompt on stdin", argv[:2] == ["exec", "--json"]
   and argv[argv.index("-s") + 1] == "read-only" and argv[-1] == "-", argv)
off = {argv[i + 1] for i, a in enumerate(argv) if a == "-c"}
ok("every acting feature is off: no shell, no web, no sub-agent tools, no images, no plugins",
   {"features.shell_tool=false", "features.unified_exec=false", "features.multi_agent=false",
    "features.image_generation=false", "features.plugins=false", 'web_search="disabled"'} <= off, sorted(off))
ok("the box's MCP by URL, its bearer read from AIOS_SEAT", 'mcp_servers.aios.url="http://127.0.0.1:8000/mcp"' in off
   and 'mcp_servers.aios.bearer_token_env_var="AIOS_SEAT"' in off, sorted(off))
ok("THE SEAT IS NEVER ON THE COMMAND LINE", not any("seat-SECRET-123" in a for a in argv), argv)
ok("...it is in the CLI's environment, where the CLI reads it", seen["env"].get("AIOS_SEAT") == "seat-SECRET-123")
ok("the sign-in reached the CLI as its own auth.json", seen["auth"] == AUTH.decode(), seen["auth"])
ok("...and the variable that carried it is gone before the CLI starts", "AIOS_CODEX_AUTH" not in seen["env"]
   and "AIOS_CODEX_DIR" not in seen["env"], sorted(seen["env"]))
ok("...in the run's own CODEX_HOME, never the box's", seen["env"]["CODEX_HOME"] != str(HOME))
ok("no API key reaches it: the subscription the buyer signed in with, and only that",
   not {"OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY"} & set(seen["env"]))
ok("the standing instructions go first on stdin, then the question",
   seen["stdin"].startswith("You are the box.") and seen["stdin"].endswith("What needs me today?"), seen["stdin"])
with state.connect() as c:
    row = c.execute("SELECT * FROM spend_ledger WHERE job_id = 'ask_test1'").fetchone()
ok("its tokens are on the spend ledger at $0 (a subscription has no per-run price)", row is not None
   and row["cost_usd"] == 0.0 and row["input_tokens"] == 1200 and row["output_tokens"] == 80
   and row["model"] == "codex:default", dict(row) if row else None)
ok("the box's own sign-in file is untouched", (HOME / "auth.json").read_bytes() == AUTH)

print("\nA sign-in refreshed during the run\n")
NEW = json.dumps({"auth_mode": "chatgpt", "tokens": {"refresh_token": "rt_2", "access_token": "at_2"}})
control(events=answer(), refresh=NEW)
run(run_id="ask_test2")
ok("is written back to the box (refresh tokens rotate; the old one is spent)",
   (HOME / "auth.json").read_text() == NEW, (HOME / "auth.json").read_text())
ok("...root-only, as the CLI keeps it", stat.S_IMODE((HOME / "auth.json").stat().st_mode) == 0o600)
control(events=answer(), refresh=json.dumps({"tokens": {"refresh_token": "rt_3"}}),
        box_changed=str(HOME / "auth.json"))
run(run_id="ask_test3")
ok("...but never over a copy something else refreshed meanwhile", (HOME / "auth.json").read_text()
   == "changed by someone else", (HOME / "auth.json").read_text())
(HOME / "auth.json").write_text(NEW)
control(events=answer(), rc=0, stderr="")
out, after = brain._codex_split_auth("progress line\n@@aios-codex-auth@@ZmFrZQ==\n")
ok("the sign-in is cut out of stderr before stderr is read for anything", out == "progress line"
   and after == "ZmFrZQ==", (out, after))

print("\nEvery ending says what happened\n")


def ending(**c):
    control(**c)
    try:
        run(run_id=f"ask_e{len(NOTED)}{os.urandom(2).hex()}")
        return None
    except Exception as e:                                  # noqa: BLE001
        return e


e = ending(events=[{"type": "turn.failed", "error": {"message": "unexpected status 401 Unauthorized"}}], rc=1)
ok("a 401: ChatGPT isn't signed in, in words, and the box's status says so",
   isinstance(e, RuntimeError) and str(e) == brain.CODEX_SIGNED_OUT and NOTED[-1:] == ["needs_reauth"], e)
e = ending(events=[{"type": "turn.failed", "error": {"message": "You've hit your usage limit."}}], rc=1)
ok("a closed subscription window is BudgetExceeded", isinstance(e, BudgetExceeded), e)
e = ending(events=[{"type": "turn.failed", "error": {"message": "stream disconnected: connection reset"}}], rc=1)
ok("a dropped connection is RetryableError (the runner's one retry)", isinstance(e, RetryableError), e)
e = ending(events=[{"type": "turn.started"}], rc=0)
ok("a run with no answer is a failure, never an empty answer", isinstance(e, RuntimeError)
   and not isinstance(e, (BudgetExceeded, RetryableError)), e)
e = ending(events=answer(extra=[{"type": "error", "message": "Reconnecting... 1/5"}]))
ok("a stream notice the CLI retried past does not fail an answered run", e is None, e)
e = ending(events=[{"type": "turn.failed", "error": {"message": "401 Unauthorized"}}], rc=1,
           stderr="Bearer seat-SECRET-123 refused")
ok("...and no ending carries the seat", e is not None and "seat-SECRET-123" not in str(e), e)

print("\nIn the sandbox\n")
CMDS = []
_cli, _rd = brain._run_agent_cli, sandbox.run_dir
sandbox.cli_path = lambda link=sandbox.CLI_LINK: "/usr/local/bin/codex" if link == brain.CODEX_LINK else "/x/claude"
RUNS = T / "runs"
sandbox.run_dir = lambda run_id, base=None: _rd(run_id, base=str(RUNS))
ENVS = []


def stub(cmd, **kw):
    CMDS.append(cmd)
    i = next(n for n, a in enumerate(cmd) if a.startswith("EnvironmentFile="))
    ENVS.append(pathlib.Path(cmd[i].split("=", 1)[1]).read_text())
    return 0, "\n".join(json.dumps(ev) for ev in answer()), "@@aios-codex-auth@@\n"


brain._run_agent_cli = stub
got = run(run_id="ask_sbx1", sandboxed=True)
cmd = CMDS[-1]
ok("it runs as a systemd-run unit, the same one the Claude path uses", cmd[0] == "systemd-run"
   and any(a == "User=aios-shift" for a in cmd) and "--wait" in cmd and got["text"], cmd[:8])
ok("...the pinned CLI, through the sign-in script, in the unit's work folder", "/usr/local/bin/codex" in cmd
   and cmd[cmd.index("--") + 1] == "/bin/sh" and cmd[cmd.index("-C") + 1] == sandbox.MOUNT, cmd[-40:])
ok("...with the minutes as its limit", any(a.startswith("RuntimeMaxSec=") for a in cmd))
ok("NEITHER THE SEAT NOR THE SIGN-IN IS ON THE COMMAND LINE", not any("seat-SECRET-123" in a or
   base64.b64encode(NEW.encode()).decode()[:20] in a for a in cmd), cmd)
ok("...both are in the root-only environment file", "AIOS_SEAT=seat-SECRET-123" in ENVS[-1]
   and f"AIOS_CODEX_AUTH={base64.b64encode(NEW.encode()).decode()}" in ENVS[-1], ENVS[-1][:200])
ok("...which is gone when the run ends", not list(RUNS.glob("*")), list(RUNS.glob("*")))
brain._run_agent_cli = _cli

print("\nAsk your box, on a ChatGPT box\n")
from core import ask  # noqa: E402
from core.connector import seats  # noqa: E402

AGENT = []
_ra = brain.run_agent
brain.run_agent = lambda *a, **k: AGENT.append(k) or {"text": json.dumps({"answer": "Three are waiting.",
                                                                         "means": "", "ask_next": [],
                                                                         "can_start": []})}
os.environ.pop("AIOS_HERMETIC_TEST")
_, cred = seats.mint("ChatGPT", "act")
a = ask.ask("What needs me today?", seats.verify(cred))
os.environ["AIOS_HERMETIC_TEST"] = "1"
brain.run_agent = _ra
ok("it is answered by the agent with the box's tools, not one think over stored numbers",
   a.get("answered_by") == "box_ai" and AGENT and AGENT[-1]["mcp"]["url"] == ask.MCP_URL, a)

print("\nThe CLI and its code-mode host reach every box\n")
import hashlib  # noqa: E402
import platform  # noqa: E402
import subprocess  # noqa: E402

upd = (ROOT / "scripts" / "box_update.sh").read_text()
ok("every box update runs the installer, and its failure never fails the update",
   'bash scripts/install_codex.sh || echo "   codex install failed; box unaffected"' in upd)
if platform.machine() == "x86_64":
    I = T / "install"
    (I / "bin").mkdir(parents=True)
    (I / "bin" / "codex").write_text("#!/bin/sh\necho 'codex-cli 0.155.1'\n")
    (I / "bin" / "codex").chmod(0o755)
    (I / "host").write_bytes(b"the pinned host")
    (I / "host").chmod(0o755)
    (I / "fakebin").mkdir()
    (I / "fakebin" / "curl").write_text(f"#!/bin/sh\necho \"$@\" >> {I}/curl.log\nexit 22\n")
    (I / "fakebin" / "curl").chmod(0o755)
    env = {**os.environ, "PATH": f"{I / 'fakebin'}{os.pathsep}{os.environ['PATH']}",
           "CODEX_BIN": str(I / "bin" / "codex"), "CODEX_HOST_BIN": str(I / "host"),
           "CODEX_HOME_DIR": str(I / "home"),
           "CODEX_HOST_BIN_SHA256": hashlib.sha256(b"the pinned host").hexdigest()}
    r = subprocess.run(["bash", str(ROOT / "scripts" / "install_codex.sh")], env=env, capture_output=True, text=True)
    ok("a box that has the pinned CLI and host downloads nothing", r.returncode == 0
       and not (I / "curl.log").exists() and "code-mode host already installed" in r.stdout, (r.stdout, r.stderr))
    (I / "host").unlink()
    r = subprocess.run(["bash", str(ROOT / "scripts" / "install_codex.sh")], env=env, capture_output=True, text=True)
    said = (I / "curl.log").read_text() if (I / "curl.log").exists() else ""
    ok("a box without the host fetches the pinned release asset", "codex-code-mode-host-x86_64-unknown-linux-musl"
       in said and "rust-v0.155.1" in said, said)
    ok("...and a failed fetch fails the installer, never installs anything", r.returncode != 0
       and not (I / "host").exists())

print("\nALL CHATGPT AGENT CHECKS PASS" if not FAILS else f"\n{len(FAILS)} CHATGPT AGENT CHECK(S) FAILED")
sys.exit(1 if FAILS else 0)
