"""brain.run_agent(): a coworker's shift goes through the brain, locked down (SCOPE_SHIFTS §4.4).

What is pinned here, through the one subprocess seam (`brain._run_agent_cli`), with no CLI run
and no spend:

  · the lock-down: only Read and Write (plus the web tools that were granted), --restricted, one
    MCP server, dontAsk, no settings, the turn limit, no session kept;
  · no box key in the process: the CLI's environment is built, never copied, so a vendor key in
    the box's environment never reaches it, and only the AI account's own credential does;
  · no secret where the AI can read it: the MCP file names the seat as ${AIOS_SEAT}; the
    credentials go in the sandbox's root-only EnvironmentFile (or, in tests, a built env); the
    prompt goes in on stdin and the job in a file, never on the command line;
  · the sandbox (OSDev1's core/coworkers/sandbox.py): systemd-run from sandbox.argv, the run's
    files in its read-only run directory at their in-unit paths, and a unit past its minutes
    STOPPED, not abandoned;
  · the money: the $90 guard refuses an API-key run BEFORE it starts, the run's own dollar limit
    is passed on, and spend is recorded under the run id, even for a run stopped at a limit;
  · every ending named, judged on is_error (a 401 arrives as subtype success) with
    api_error_status deciding the retry: a limit is AgentLimit, a subscription window
    BudgetExceeded, a busy API RetryableError, a refused account or anything else RuntimeError.

The flags themselves were measured on the pinned CLI (2.1.278) on 2026-09-26; see core/brain.py.

Run: python tests/test_brain_run_agent.py
"""
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = tempfile.mkdtemp() + "/agent.db"

from core import state  # noqa: E402

state.init_db()

from core import brain, cost_guard  # noqa: E402
from core.config import get_config  # noqa: E402
from core.exceptions import BudgetExceeded, RetryableError  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


# A `claude` on PATH, so can_think() is honest about the box; it is never executed.
bindir = pathlib.Path(tempfile.mkdtemp())
fake = bindir / "claude"
fake.write_text("#!/bin/sh\nexit 99\n")
fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
os.environ["PATH"] = f"{bindir}:{os.environ.get('PATH', '')}"
os.environ["ZERNIO_API_KEY"] = "zr-vendor-secret"           # a box key the AI must never hold
os.environ["DISPATCH_BEARER_TOKEN"] = "the-wide-token"
cfg = get_config().setdefault("brain", {})
WS = pathlib.Path(tempfile.mkdtemp())

SEEN = {}


def reply(**over):
    d = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 7,
         "total_cost_usd": 0.42, "result": "Drafted 3 replies.",
         "usage": {"input_tokens": 1200, "output_tokens": 300},
         "permission_denials": [{"tool_name": "Write", "tool_input": {}}]}
    d.update(over)
    return 0, json.dumps(d), ""


def fake_cli(result=None, exc=None):
    def run(cmd, *, stdin, env, cwd, timeout):
        SEEN.clear()
        SEEN.update(cmd=cmd, stdin=stdin, env=env, cwd=cwd, timeout=timeout)
        # What exists WHILE the CLI runs. In the sandbox the CLI names in-unit paths, so the host
        # copies are read from the run directory the test pointed the sandbox at.
        cli = cmd[cmd.index("--") + 1:] if cmd[0] == "systemd-run" else cmd
        def host(path):
            return path.replace(sandbox.RUN_DIR, RUNS_NOW[0]) if cmd[0] == "systemd-run" else path
        SEEN["mcp"] = json.loads(pathlib.Path(host(cli[cli.index("--mcp-config") + 1])).read_text())
        SEEN["system"] = pathlib.Path(host(cli[cli.index("--system-prompt-file") + 1])).read_text()
        envfile = [c.split("=", 1)[1] for c in cmd if c.startswith("EnvironmentFile=")]
        if envfile:
            f = pathlib.Path(envfile[0])
            SEEN["envfile"] = f.read_text()
            SEEN["envfile_mode"] = stat.S_IMODE(f.stat().st_mode)
            SEEN["envfile_path"] = str(f)
        SEEN["workspace_after"] = sorted(x.name for x in WS.iterdir())
        if exc:
            raise exc
        return result or reply()
    brain._run_agent_cli = run


def arg(flag, c=None):
    c = c if c is not None else SEEN["cmd"]
    return c[c.index(flag) + 1] if flag in c else None


def spend_rows(run_id):
    with state.connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT task, model, cost_usd, input_tokens FROM spend_ledger WHERE job_id = ?",
            (run_id,))]


from core.coworkers import sandbox  # noqa: E402

RUNS_NOW = [""]
_real_run_dir = sandbox.run_dir
RUNS_BASE = tempfile.mkdtemp()


def _test_run_dir(run_id, base=None):
    RUNS_NOW[0] = _real_run_dir(run_id, base=RUNS_BASE)
    return RUNS_NOW[0]


sandbox.run_dir = _test_run_dir
CLI_REAL = "/usr/local/lib/claude-code/2.1.278/claude"
sandbox.cli_path = lambda link=sandbox.CLI_LINK: CLI_REAL
STOPPED = []
brain._stop_agent_unit = lambda cmd: STOPPED.append(cmd)

MCP = {"url": "http://127.0.0.1:8000/mcp", "credential": "seat_abc.s3cret"}
KW = dict(coworker="front-desk", workspace=WS)

print("in the sandbox, as every box runs it")
cfg["backend"] = "claude_code"
os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = "sk-ant-oat-test"
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-api-should-not-leak"
fake_cli()
r = brain.run_agent("Today: triage the inbox.", run_id="run_s1", system="You are the front desk.",
                    mcp=MCP, max_turns=40, max_minutes=30, **KW)
c = SEEN["cmd"]
cli = c[c.index("--") + 1:]
ok("it starts through systemd-run, as sandbox.argv builds it",
   c[0] == "systemd-run" and "--unit=aios-coworker-front-desk-run-s1" in c
   and "User=aios-shift" in c and f"BindPaths={WS}:{sandbox.MOUNT}" in c)
ok("the run directory is bound read-only into the unit",
   f"BindReadOnlyPaths={RUNS_NOW[0]}:{sandbox.RUN_DIR}" in c)
ok("the CLI is the one sandbox.cli_path() allows", cli[0] == CLI_REAL)
ok("only Read and Write are built in (no Bash, no code runners)", arg("--tools", cli) == "Read,Write")
ok("allowed: those, plus the box's MCP server",
   arg("--allowedTools", cli) == "Read,Write,mcp__aios")
for flag in ("--restricted", "--strict-mcp-config", "--no-session-persistence", "-p"):
    ok(f"{flag} is set", flag in cli)
ok("nothing is asked, anything unlisted is refused", arg("--permission-mode", cli) == "dontAsk")
ok("no settings are loaded from the box's code", arg("--setting-sources", cli) == "")
ok("the turn limit is passed on", arg("--max-turns", cli) == "40")
ok("the model is the coworker tier", arg("--model", cli) == "sonnet")
ok("a subscription run has no dollar flag", "--max-budget-usd" not in cli)
ok("the CLI is given the files at their IN-UNIT paths",
   arg("--mcp-config", cli) == f"{sandbox.RUN_DIR}/mcp.json"
   and arg("--system-prompt-file", cli) == f"{sandbox.RUN_DIR}/system.md")
ok("the prompt goes in on stdin, which --pipe passes through",
   SEEN["stdin"] == "Today: triage the inbox." and not any("triage" in x for x in c))
ok("the job goes in a file", SEEN["system"] == "You are the front desk."
   and not any("front desk" in x for x in c))
ok("the unit gets no environment and no directory from the caller",
   SEEN["env"] is None and SEEN["cwd"] is None)
ok("the timeout is the minute limit plus start-up", SEEN["timeout"] == 30 * 60 + 30)
ok("the credentials go in the root-only EnvironmentFile",
   SEEN["envfile_mode"] == 0o600 and "CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat-test" in SEEN["envfile"]
   and "AIOS_SEAT=seat_abc.s3cret" in SEEN["envfile"])
ok("no vendor key, no dispatch token, no API key goes in it",
   not any(x in SEEN["envfile"] for x in ("zr-vendor-secret", "the-wide-token",
                                          "should-not-leak")))
ok("the MCP file names the seat, it does not hold it",
   SEEN["mcp"] == {"mcpServers": {"aios": {"type": "http", "url": MCP["url"],
                                           "headers": {"Authorization": "Bearer ${AIOS_SEAT}"}}}})
ok("no secret is on the command line", not any("s3cret" in x or "sk-ant" in x for x in c))
ok("nothing is written into the workspace", SEEN["workspace_after"] == [])
ok("the run directory is gone afterwards", not pathlib.Path(RUNS_NOW[0]).exists())
ok("it returns turns, minutes, cost and the final text",
   (r["text"], r["turns"], r["backend"]) == ("Drafted 3 replies.", 7, "claude_code")
   and isinstance(r["minutes"], float))
ok("a subscription run spends $0, and the API-equivalent is kept",
   r["cost_usd"] == 0.0 and r["api_usd"] == 0.42)
ok("refused tool uses are reported", r["denied"] == ["Write"])
rows = spend_rows("run_s1")
ok("one spend row, under the run id, at $0, with its tokens",
   rows == [{"task": "coworker", "model": "cc:sonnet", "cost_usd": 0.0, "input_tokens": 1200}],
   str(rows))

fake_cli(exc=subprocess.TimeoutExpired(cmd="systemd-run", timeout=1830))
STOPPED.clear()
try:
    brain.run_agent("Go.", run_id="run_t", max_minutes=30, **KW)
    ok("a run past its minutes is stopped", False)
except brain.AgentLimit as e:
    ok("a run past its minutes raises AgentLimit, saying so", "30-minute limit" in str(e))
ok("and its UNIT IS STOPPED, not left drafting", STOPPED == [["systemctl", "stop",
                                                              "aios-coworker-front-desk-run-t"]])
ok("and its run directory is removed", not pathlib.Path(RUNS_NOW[0]).exists())

sandbox.cli_path = lambda link=sandbox.CLI_LINK: (_ for _ in ()).throw(
    sandbox.SandboxError("the AI CLI is at /root/.local/bin/claude, inside a home folder"))
called = []
brain._run_agent_cli = lambda *a, **k: called.append(1) or reply()
try:
    brain.run_agent("Go.", run_id="run_sb2", **KW)
    ok("a CLI the sandbox cannot run is refused before anything starts", False)
except sandbox.SandboxError as e:
    ok("a CLI the sandbox cannot run is refused before anything starts, with the reason",
       called == [] and "home folder" in str(e))
sandbox.cli_path = lambda link=sandbox.CLI_LINK: CLI_REAL

print("an API-key box: the guard first, the run's own ceiling, the real cost recorded")
cfg["backend"] = "api"
os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN")
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-api-test"
fake_cli()
r = brain.run_agent("Go.", run_id="run_a1", mcp=None, max_usd=2.5, **KW)
cli = SEEN["cmd"][SEEN["cmd"].index("--") + 1:]
ok("the run's own dollar limit is passed on", arg("--max-budget-usd", cli) == "2.50")
ok("the key is in the env file, and no subscription token",
   "ANTHROPIC_API_KEY=sk-ant-api-test" in SEEN["envfile"]
   and "CLAUDE_CODE_OAUTH_TOKEN" not in SEEN["envfile"])
ok("with no MCP, only the built-ins, an empty MCP config, and no seat",
   arg("--allowedTools", cli) == "Read,Write" and SEEN["mcp"] == {"mcpServers": {}}
   and "AIOS_SEAT" not in SEEN["envfile"])
ok("the cost is what was spent", r["cost_usd"] == 0.42)
rows = spend_rows("run_a1")
ok("and it is in the ledger under the run id", len(rows) == 1 and rows[0]["cost_usd"] == 0.42
   and rows[0]["model"] == brain._model_for("coworker"), str(rows))

called = []
brain._run_agent_cli = lambda *a, **k: called.append(1) or reply()
real_mtd = cost_guard.month_to_date_spend
cost_guard.month_to_date_spend = lambda now=None: cost_guard.ceiling() - 1.0
try:
    brain.run_agent("Go.", run_id="run_a2", max_usd=2.5, **KW)
    ok("a run that could cross the $90 line is refused", False)
except BudgetExceeded:
    ok("a run that could cross the $90 line is refused BEFORE it starts", called == [])
finally:
    cost_guard.month_to_date_spend = real_mtd

# A TIMEOUT IS NEVER ASSUMED FREE: on an API-key box the run is recorded at its own ceiling.
fake_cli(exc=subprocess.TimeoutExpired(cmd="systemd-run", timeout=1830))
try:
    brain.run_agent("Go.", run_id="run_at", max_usd=2.5, **KW)
except brain.AgentLimit:
    pass
rows = spend_rows("run_at")
ok("an API run stopped at its minutes is recorded at its dollar ceiling, marked unreported",
   len(rows) == 1 and rows[0]["cost_usd"] == 2.5 and rows[0]["model"].endswith(":unreported"),
   str(rows))

import time as _time  # noqa: E402
_real_mono = _time.monotonic
for label, run_id, elapsed, want in (("a CLI that dies at start records nothing", "run_ad0", 5, 0),
                                     ("one that ran minutes, then died without a result, is "
                                      "recorded at its ceiling", "run_ad1", 300, 1)):
    clock = [1000.0]
    _time.monotonic = lambda: clock[0]
    def died(*a, _e=elapsed, **k):
        clock[0] += _e
        return 1, "", "segfault"
    brain._run_agent_cli = died
    try:
        brain.run_agent("Go.", run_id=run_id, max_usd=2.5, **KW)
    except RuntimeError:
        pass
    finally:
        _time.monotonic = _real_mono
    rows = spend_rows(run_id)
    ok(label, len(rows) == want and all(r["cost_usd"] == 2.5 for r in rows), str(rows))

print("the web, only when granted")
for i, (web, want) in enumerate(((("search",), "Read,Write,WebSearch"),
                                 (("read",), "Read,Write,WebFetch"),
                                 (("read", "search"), "Read,Write,WebSearch,WebFetch"))):
    fake_cli()
    brain.run_agent("Go.", run_id=f"run_w{i}", web=web, **KW)
    cli = SEEN["cmd"][SEEN["cmd"].index("--") + 1:]
    ok(f"web {web} gives {want}", arg("--tools", cli) == want and arg("--allowedTools", cli) == want)
for bad in (("post",), ("bash",)):
    try:
        brain.run_agent("Go.", run_id="run_wx", web=bad, **KW)
        ok(f"web {bad} is refused", False)
    except ValueError:
        ok(f"web {bad} is refused", True)

print("every ending is named")
cases = (
    ("its turn limit", reply(subtype="error_max_turns", is_error=True), brain.AgentLimit,
     "40-turn limit"),
    ("its dollar limit", reply(subtype="error_max_budget_usd", is_error=True), brain.AgentLimit,
     "$1.00 limit"),
    ("a refused sign-in, labelled success (OSDev1, measured)",
     reply(is_error=True, api_error_status=401, result="Invalid API key · Please run /login"),
     RuntimeError, "needs attention in Set up"),
    ("an overloaded API (529)", reply(is_error=True, api_error_status=529, result="Overloaded"),
     RetryableError, "529"),
    ("rate limited (429)", reply(is_error=True, api_error_status=429, result="rate_limit_error"),
     RetryableError, "429"),
    ("a bad request (400) is not retried", reply(is_error=True, api_error_status=400,
                                                 result="invalid_request_error"),
     RuntimeError, "(400)"),
    ("a subscription window", (1, "", "Claude usage limit reached, resets 5pm"), BudgetExceeded, ""),
    ("the network, before any JSON", (1, "", "connection reset by peer"), RetryableError, ""),
    ("garbage", (1, "not json", "segfault"), RuntimeError, "rc=1"),
    ("an unknown error ending", reply(subtype="error_during_execution", is_error=True,
                                      result="boom"), RuntimeError, "error_during_execution"),
)
for i, (label, result, exc, text) in enumerate(cases):
    fake_cli(result=result)
    try:
        brain.run_agent("Go.", run_id=f"run_e{i}", **KW)
        ok(f"{label}: raises {exc.__name__}", False)
    except exc as e:
        ok(f"{label}: raises {exc.__name__}", text in str(e) and
           (exc is not RuntimeError or not isinstance(e, brain.AgentLimit)), str(e))
rows = spend_rows("run_e0")
ok("a run stopped at its turn limit still records what it spent", len(rows) == 1
   and rows[0]["cost_usd"] == 0.42, str(rows))

print("outside the sandbox: tests and development only")
fake_cli()
brain.run_agent("Go direct.", run_id="run_d1", mcp=MCP, sandboxed=False, **KW)
c = SEEN["cmd"]
ok("no systemd-run, the CLI from PATH", c[0] == str(fake) and "systemd-run" not in c)
ok("a BUILT environment: the key and the seat, no vendor key or dispatch token",
   SEEN["env"].get("ANTHROPIC_API_KEY") == "sk-ant-api-test"
   and SEEN["env"].get("AIOS_SEAT") == "seat_abc.s3cret"
   and not {"ZERNIO_API_KEY", "DISPATCH_BEARER_TOKEN"} & set(SEEN["env"]))
ok("a private HOME, removed afterwards", "aios-run-" in SEEN["env"]["HOME"]
   and not pathlib.Path(SEEN["env"]["HOME"]).exists())
ok("run in the workspace, the prompt on stdin", SEEN["cwd"] == str(WS)
   and SEEN["stdin"] == "Go direct.")

print("refusals before anything runs")
for label, kw in (("a relative workspace", {"workspace": "workspace"}),
                  ("a workspace that does not exist", {"workspace": "/nonexistent/ws"})):
    try:
        brain.run_agent("Go.", run_id="r", coworker="front-desk", **kw)
        ok(f"{label} is refused", False)
    except ValueError:
        ok(f"{label} is refused", True)
real_backend = brain._backend
real_can = brain.can_think
brain._backend = lambda: "codex"
brain.can_think = lambda: (True, "codex")
try:
    brain.run_agent("Go.", run_id="r", **KW)
    ok("a ChatGPT box is told plainly", False)
except RuntimeError as e:
    ok("a ChatGPT box is told plainly, not moved onto another account", "ChatGPT" in str(e), str(e))
finally:
    brain._backend, brain.can_think = real_backend, real_can
os.environ.pop("ANTHROPIC_API_KEY")
try:
    brain.run_agent("Go.", run_id="r", **KW)
    ok("a box that cannot think says why", False)
except RuntimeError as e:
    ok("a box that cannot think says why", "cannot run" in str(e) and "key" in str(e), str(e))

print()
if _failed:
    print(f"{_failed} FAILED")
    sys.exit(1)
print("ALL OK")
