"""brain.think() — the single reasoning gateway for all of AIOS.

Every LLM call in the system goes through here (Handler Contract Rule 3), which is
exactly what makes the backend pluggable. Two backends, chosen by config `brain.backend`:

  api          Anthropic Messages API, API key, Commercial Terms. The REQUIRED
               backend for resold clones — each client brings their own key. Cost
               guard checked before every call; actual spend recorded after.
  claude_code  The OWNER's Claude subscription via the official Claude Code CLI in
               headless print mode (`claude -p --output-format json`), authed by a
               long-lived `claude setup-token` token. $0 marginal cost; governed by
               the subscription's rolling usage windows — hitting one raises
               BudgetExceeded, which the worker already treats as "pause and wait",
               the exact right behavior for a window that resets on its own.
               OWNER-BOX ONLY: a consumer subscription must never power resold
               clones (consumer terms are per-person; clones stay `api`/BYOK).

Model tier is chosen per task from config; large reused context (brand-brief,
frame.md) is sent as a cached system block on the api backend (caching is moot on
subscription). Spend rows are written either way — with cost 0 on claude_code —
so per-job attribution and the watchdog's task counts keep working.
"""
from core.config import ROOT
import contextvars
import json
import os
import re
import shutil
import pathlib
import subprocess
import tempfile
import threading
import time

from core import cost_guard, state
from core.config import get_config, settings
from core.exceptions import BudgetExceeded, RetryableError
from core.logging import get_logger

log = get_logger(__name__)

_client = None
_client_key = ""          # the key `_client` was built with; a change rebuilds it

# THE ACCOUNT THIS ONE CALL THINKS ON, when a machine chose its own (core/machine_accounts.py).
# None, the default, is the Base Machine's account, read from box_secrets exactly as before. A
# context variable rather than a parameter threaded through every backend: `think()` sets it for
# the length of one call and resets it, so the four readers below (the backend choice, the API
# client, the Claude CLI's token, the ChatGPT CLI's home) each ask one question in one place.
_CALL: contextvars.ContextVar = contextvars.ContextVar("brain_call_account", default=None)
_KIND_BACKEND = {"claude_oauth": "claude_code", "anthropic_key": "api", "codex": "codex"}

# Anthropic exception class names that mean "transient — retry later", matched by
# name so the spine never imports the SDK at module load. Covers overloaded (529),
# rate limit (429), 5xx, and connection/timeout. Bad-request/auth are absent on
# purpose — those are terminal and must propagate, not requeue.
_TRANSIENT_EXC_NAMES = frozenset({
    "APIConnectionError", "APITimeoutError", "InternalServerError",
    "RateLimitError", "OverloadedError", "ServiceUnavailableError",
})


def _is_transient(e: Exception) -> bool:
    if type(e).__name__ in _TRANSIENT_EXC_NAMES:
        return True
    # APIStatusError carries an HTTP status; 408/409/429/5xx are transient.
    status = getattr(e, "status_code", None)
    return isinstance(status, int) and (status == 429 or status >= 500)


def _client_():
    global _client, _client_key
    # THE KEY CAN NOW ARRIVE AFTER BOOT. A delivered box has none in its environment — the buyer
    # types it into Settings, and it lands in `box_secrets` because the web process cannot write
    # the worker's environment (different unit entirely). So the client is keyed BY THE KEY: if
    # the buyer changes or removes it, the next call rebuilds instead of holding a stale client
    # for the life of the worker. `.env` still wins, so every box running today is untouched.
    from core import box_secrets
    own = _CALL.get()
    key = own["value"] if own and own.get("kind") == "anthropic_key" else box_secrets.anthropic_key()
    if _client is not None and key != _client_key:
        _client = None
    if _client is None:
        from anthropic import Anthropic  # imported lazily so the spine needs no SDK
        # Autonomous system: transient 429/529 (overloaded) from Anthropic are
        # routine and must not fail a whole job. The SDK does exponential backoff
        # for us when max_retries is set. A per-call timeout bounds a hung request
        # (a tiny router call should never block the worker for minutes).
        _client_key = key
        _client = Anthropic(
            api_key=key,
            max_retries=4,      # ~exponential backoff on 429/500/503/529 + network errors
            timeout=120.0,      # seconds; generous for reasoning, finite for safety
        )
    return _client


def verify_key(value: str) -> tuple[bool, str]:
    """Ask Anthropic whether this key works. Returns (ok, a sentence for the buyer).

    WHY THIS LIVES HERE AND NOT IN `box_secrets`. This module owns the only Anthropic import in
    the spine, and it owns `_is_transient` — the classifier that decides whether a failure is the
    key's fault or the minute's. Putting the probe anywhere else would mean a second SDK import
    and a second, drifting copy of that judgement.

    IT ASKS `models.list`, NOT `messages.create`, AND THAT IS THE WHOLE DESIGN. Listing models
    authenticates without generating a single token: it is not inference, so it does not belong
    in `think()` (invariant 2 stays intact — nothing here reasons), and it is not billed, so
    there is nothing for `cost_guard` to meter (invariant 4 has no work to do). A one-token
    `messages.create` would have proved the same thing and cost money at the exact moment a
    buyer is first trying to set their box up.

    UNREACHABLE IS NOT REFUSED — the rule OSDev5 wrote into the mailbox path, and the difference
    matters more here than anywhere. Told "your key is wrong", a person goes to the console and
    makes a NEW key they did not need, and now has two. So a 401 or 403 is the only thing this
    calls a bad key; a rate limit, a 5xx, a dropped connection or a box whose SDK is not
    installed all come back as "try again", which is true and costs them nothing.
    """
    value = str(value or "").strip()
    if not value:
        return False, "Paste the key from your AI account, then turn drafts on."
    try:
        from anthropic import Anthropic  # lazily, exactly as `_client_` does
    except Exception:                            # noqa: BLE001 — a box missing its own SDK is
        # A BOX FAULT, NOT A BAD KEY, so it must not send anybody to the console. Every delivered
        # box installs `anthropic>=0.40` (pyproject.toml:7), so this is the sandbox and the
        # half-installed box — and in both the honest answer is that we could not ask.
        log.warning("verify_key.no_sdk")
        return False, ("Ownbox could not check that key just now. Nothing is saved — "
                       "try again in a minute.")
    # A THROWAWAY CLIENT, NEVER `_client_()`. That one is keyed by the STORED key and this key is
    # not stored yet — calling it here would probe whatever is already on the box and report a
    # verdict about the wrong credential. `max_retries=0` because a person is standing at the
    # screen: four backoffs on a dead network is a minute of a blank button.
    try:
        probe = Anthropic(api_key=value, max_retries=0, timeout=15.0)
        probe.models.list(limit=1)
    except Exception as e:                       # noqa: BLE001 — every failure becomes a sentence
        status = getattr(e, "status_code", None)
        if status in (401, 403) or type(e).__name__ in (
                "AuthenticationError", "PermissionDeniedError"):
            log.info("verify_key.refused", status=status)
            # TWO DIFFERENT FIXES, SO TWO DIFFERENT SENTENCES. 401 means the key is not valid;
            # 403 is most often a console that has run out of credit, and a person told to
            # re-paste a perfectly good key over that learns nothing.
            if status == 403 or type(e).__name__ == "PermissionDeniedError":
                return False, ("Your AI account refused that key — usually it means the account "
                               "needs credit. Add some there, then paste it again.")
            return False, ("Your AI account did not recognise that key. Copy it again from the "
                           "console — a key is only shown once, when you make it.")
        log.warning("verify_key.unreachable", error=f"{type(e).__name__}"[:60])
        return False, ("Ownbox could not reach your AI account just now. Nothing is saved — "
                       "try again in a minute.")
    return True, "connected"


def _model_for(task: str) -> str:
    cfg = get_config()
    tier = cfg["models"].get(task, cfg["models"]["default"])
    return cfg["model_ids"][tier]


def _cost_usd(model: str, usage) -> float:
    rates = get_config()["rates"].get(model, {})

    def part(attr: str, rate_key: str) -> float:
        tokens = getattr(usage, attr, 0) or 0
        return tokens / 1_000_000 * rates.get(rate_key, 0.0)

    return (part("input_tokens", "input")
            + part("output_tokens", "output")
            + part("cache_creation_input_tokens", "cache_write")
            + part("cache_read_input_tokens", "cache_read"))


def _estimate_usd(model: str, prompt: str, cached_context: str | None,
                  system: str | None, max_tokens: int) -> float:
    """Worst-case cost of THIS call, so the guard refuses *before* a call that could
    cross the ceiling (rather than only after). Assumes max_tokens of output and a
    rough chars/4 input-token estimate. Bounded and intentionally conservative."""
    rates = get_config()["rates"].get(model, {})
    input_chars = len(prompt) + len(cached_context or "") + len(system or "")
    est_input_tokens = input_chars / 4
    return (est_input_tokens / 1_000_000 * rates.get("input", 0.0)
            + max_tokens / 1_000_000 * rates.get("output", 0.0))


def _backend() -> str:
    """Which backend this box thinks on, answered from what the BUYER connected.

    IT USED TO BE CONFIG ALONE, and on a sold box that made the choice unreachable. `export_box.sh`
    writes `backend: api` into every box it builds, so a buyer who has a Claude subscription and no
    API account at all — which the owner says is most of the people we sell to, and is the owner
    himself as customer #1 — had a set-up screen that could only ask for a thing they do not have.
    Owner, 2026-09-18: "I am going to sign up as Customer number one and that must be available
    when I set up my box... I don't have an API key."

    ORDER, AND EVERY LINE OF IT IS DELIBERATE:
      1. config `claude_code` still wins outright — the owner's existing box keeps behaving
         exactly as it does today, token in .env, nothing re-decided underneath it.
      2. otherwise a subscription token the buyer connected selects `claude_code`. This is the new
         path and it is the whole point: connecting one IS choosing it.
      3. otherwise `api`, which is what every box does now and what every box with an API key
         keeps doing.

    A box that has connected NEITHER lands on `api` and `can_think()` says so in a sentence —
    the same honest "not set up yet" it has always given, never a silent failure.
    """
    # A MACHINE'S OWN ACCOUNT, WHEN THIS CALL HAS ONE, DECIDES: the machine chose it on purpose,
    # so it outranks the box's config as well as the box's connections.
    own = _CALL.get()
    if own:
        return _KIND_BACKEND[own["kind"]]
    configured = (get_config().get("brain") or {}).get("backend", "api")
    if configured in ("claude_code", "codex"):
        return configured
    from core import box_secrets
    if box_secrets.claude_oauth_token():
        return "claude_code"
    # SIGN IN WITH CHATGPT: connecting one IS choosing it, exactly as with Claude. A Claude token
    # still wins when both exist, because the owner ruled Claude the default (2026-09-22).
    if box_secrets.codex_connected():
        return "codex"
    return "api"


def _cli_model(model_id: str) -> str:
    """Map a config model id to a Claude Code --model value. The CLI accepts both
    aliases and full ids; aliases survive model-id bumps, so prefer them."""
    if "haiku" in model_id:
        return "haiku"
    if "sonnet" in model_id:
        return "sonnet"
    if "opus" in model_id:
        return "opus"
    return model_id


# Subscription-limit smells in CLI output → BudgetExceeded (pause, window resets);
# network smells → RetryableError. Anything else is terminal (a real bug/config).
_CLI_LIMIT_RE = re.compile(r"rate.?limit|usage limit|limit (?:reached|will reset)"
                           r"|out of (?:usage|credits)|quota", re.I)
_CLI_TRANSIENT_RE = re.compile(r"connection|network|timed?.?out|overloaded"
                               r"|unavailable|5\d\d", re.I)


# ONE CLI AT A TIME PER PROCESS. THE BOX HAS ONE CORE (2026-08-12).
#
# The worker runs TWO threads that both reason: the job loop on the main thread
# (`while True: process_one()`) and `_periodic_loop` on a daemon thread. Each `think()` on this
# backend spawns the `claude` CLI, which is a Node process doing real work. On the owner's $12
# 1-vCPU Droplet, two of those overlapping do not take turns — they interleave, and BOTH run
# several times slower than either would alone.
#
# That is the shape of the defect this was written for. Seven `claude_code timed out` failures
# in three hours, on a call that should take thirty seconds; sometimes fine, sometimes past
# SEVEN MINUTES. Prompt size was the obvious suspect and it is wrong — measured, the rows that
# timed out carried ~2,700-token prompts while the four LARGEST prompts on the board (~4,250
# tokens) all succeeded. The variable was never the prompt. It was whatever else the box
# happened to be reasoning about at the same instant.
#
# A lock does not make the box faster; it stops it making itself slower. Two overlapping calls
# that each time out become one that returns in thirty seconds and another that starts after
# it. Waiting is strictly better than thrashing when there is one core to thrash.
#
# CLAUDE_CODE ONLY, deliberately. The `api` backend is network-bound — it holds no local CPU
# while it waits, so serialising it would cost a clone throughput for nothing.
#
# The wait is logged when it is real, because contention that nobody can see is exactly how
# this went unexplained for a night.
_CLI_AUTH_RE = re.compile(r"\b401\b|unauthori[sz]ed|not logged in|missing bearer", re.I)
_CLI_LOCK = threading.Lock()
_CLI_WAIT_LOG_S = 5.0
# How long a caller queues before it gives the tick back instead. Long enough to wait out a
# normal call (a derived draft is bounded at 240s and most finish in well under a minute), short
# enough that the periodic thread cannot be held past its own 120s cadence for long. See the
# note in `_run_claude`: this bound is the difference between a lock and a stall.
_CLI_WAIT_MAX_S = float(os.environ.get("BRAIN_CLI_WAIT_MAX_S", "45"))


def _run_claude(cmd: list[str], *, timeout: float) -> tuple[int, str, str]:
    """Subprocess seam (single point for tests). Returns (rc, stdout, stderr).

    Serialised process-wide: see `_CLI_LOCK`. The timeout still bounds the RUN, not the wait —
    a call that queued behind another gets its full allowance once it starts, because a caller
    that waited its turn has not used any of its own budget.
    """
    waited = time.monotonic()
    # THE WAIT IS BOUNDED, AND THE FIRST CUT OF THIS WAS NOT — a defect I shipped and then
    # watched (2026-08-12). An unbounded `with _CLI_LOCK` turns a busy job loop into a total
    # stall of the periodic thread: gate 1 stops reaching rows at all, produces no failures
    # because it never runs, and looks exactly like the wedge this lock was added to cure. A
    # lock that can starve a thread indefinitely is a worse bug than the contention it fixes.
    #
    # So a caller waits its turn for a while and then GIVES THE TICK BACK. RetryableError is
    # the honest outcome: nothing was attempted, the row keeps its trigger, and the next sweep
    # 120 seconds later tries again — which is precisely how a busy box should degrade, rather
    # than by holding a thread nobody can see it holding.
    if not _CLI_LOCK.acquire(timeout=_CLI_WAIT_MAX_S):
        log.warning("brain.cli_busy", waited_s=_CLI_WAIT_MAX_S,
                    hint="another thread has held the CLI this whole time; giving the tick "
                         "back rather than blocking the periodic loop behind it")
        raise RetryableError("claude_code busy — another call held the CLI for "
                             f"{_CLI_WAIT_MAX_S:.0f}s")
    try:
        queued = time.monotonic() - waited
        if queued >= _CLI_WAIT_LOG_S:
            log.info("brain.cli_queued", waited_s=round(queued, 1),
                     hint="another thread was reasoning; one core, so they take turns")
        # stdin=DEVNULL IS LOAD-BEARING, AND ITS ABSENCE COST DAYS.
        # `capture_output=True` redirects stdout and stderr and says nothing about stdin, so the
        # CLI inherited this process's stdin and blocked reading a descriptor that would never
        # produce anything. It says so itself, on stderr:
        #   "Warning: no stdin data received in 3s, proceeding without it. If piping from a slow
        #    command, redirect stdin explicitly: < /dev/null to skip, or wait longer."
        # — and the `except subprocess.TimeoutExpired` below threw that message away with the
        # rest of `e.stderr`, so nobody ever saw it.
        #
        # Measured 2026-08-12 on row rec9w8ZYALrSfG2Se, which had failed 8 times with 0 successes:
        #   without DEVNULL -> timed out at 300s, 0 bytes of stdout
        #   with    DEVNULL -> rc=0 in 255s, 8,050 bytes, valid JSON
        # Same row, same prompt, same box, one argument different.
        #
        # It explains every symptom the outage had and none of the theories did: 0.2% CPU while
        # "hung" (blocked on a read, not computing), indifferent to prompt size, unchanged on an
        # idle box, and invisible from outside the process.
        # THE TOKEN REACHES THE CLI THROUGH THE ENVIRONMENT, WHICH IS THE ONLY DOOR IT HAS.
        # On the owner's box it is already in /opt/aios/.env and inherited, and this changes
        # nothing for it. On a SOLD box the buyer pastes it into Set up, so it lives in the
        # secrets table and would otherwise never reach the subprocess at all — the CLI would
        # run unauthenticated and fail with a login prompt nobody can see.
        #
        # A COPY, NOT os.environ ITSELF. Mutating the process environment to pass one argument
        # leaks a live credential into every unrelated subprocess this box ever spawns — ffmpeg,
        # git, the installer — and into anything that dumps its own environment on a crash.
        env = dict(os.environ)
        try:
            from core import box_secrets
            own = _CALL.get()
            tok = (own["value"] if own and own.get("kind") == "claude_oauth"
                   else box_secrets.claude_oauth_token())
            if tok:
                env["CLAUDE_CODE_OAUTH_TOKEN"] = tok
        except Exception as e:                       # noqa: BLE001
            # Bookkeeping must never be what stops a box thinking: if the table cannot be read,
            # fall through on whatever the environment already carries and let the CLI say so.
            log.warning("brain.oauth_token_unreadable", error=f"{type(e).__name__}: {e}")
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, env=env)
    finally:
        _CLI_LOCK.release()
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _think_codex(task: str, prompt: str, *, system, cached_context, job_id,
                 timeout: float | None) -> str:
    """One reasoning call through the buyer's ChatGPT subscription (Codex CLI, `codex exec`).

    MEASURED ON codex-cli 0.155.1 (2026-09-22): the final message goes to stdout and nothing
    else does; progress goes to stderr; an unsigned box fails rc=1 with "401 Unauthorized:
    Missing bearer". The prompt is read from stdin with `-`.

    NO TOOLS, NO PROJECT, NO SHELL. `-s read-only` is the sandbox; `mcp_servers={}` empties the
    tool list; `-C <empty dir>` gives it nothing to read; `--ignore-user-config --ignore-rules
    --ephemeral` keep the box's own files and any config out of it. Prompts here carry text from
    strangers (transcripts); a prompt injection must find nothing to grab, exactly as `--tools ""`
    guarantees on the Claude path.

    NO SYSTEM FLAG on this CLI, so the system text goes FIRST on stdin, above the prompt. The
    drafter already frames the untrusted part of its prompt as a quoted transcript, which is what
    keeps that ordering honest.

    NO MODEL BY DEFAULT. The CLI picks the account's default; `brain.codex_model` in config sets
    one explicitly. Inventing an OpenAI model name here would be a guess at a vendor's catalogue.
    """
    bin_ = shutil.which("codex")
    if not bin_:
        raise RuntimeError("this box drafts on ChatGPT but the `codex` CLI is not installed — "
                           "run scripts/install_codex.sh")
    from core import box_secrets
    own = _CALL.get()
    home = pathlib.Path(own["home"] if own and own.get("kind") == "codex"
                        else box_secrets.codex_home())
    workdir = home / "empty"
    try:
        workdir.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        pass
    sys_text = "\n\n".join(p for p in (cached_context, system) if p)
    full = (sys_text + "\n\n" if sys_text else "") + prompt
    cmd = [bin_, "exec", "--skip-git-repo-check", "-C", str(workdir), "--ephemeral",
           "--ignore-user-config", "--ignore-rules", "-s", "read-only", "--color", "never",
           "-c", "mcp_servers={}"]
    chosen = str((get_config().get("brain") or {}).get("codex_model") or "").strip()
    if chosen:
        cmd += ["-m", chosen]
    cmd.append("-")
    env = dict(os.environ)
    env["CODEX_HOME"] = str(home)
    env["NO_COLOR"] = "1"
    env.pop("OPENAI_API_KEY", None)      # the subscription the person signed in with, and only that
    env.pop("CODEX_API_KEY", None)
    if not _CLI_LOCK.acquire(timeout=_CLI_WAIT_MAX_S):
        raise RetryableError(f"codex busy for {_CLI_WAIT_MAX_S:.0f}s ({task})")
    try:
        proc = subprocess.run(cmd, input=full, capture_output=True, text=True,
                              timeout=(timeout or 120.0) + 30.0, env=env)
    except subprocess.TimeoutExpired as e:
        tail = e.stderr or b""
        if isinstance(tail, bytes):
            tail = tail.decode("utf-8", "replace")
        raise RetryableError(f"codex timed out ({task})"
                             + (f" — said: {tail.strip()[-300:]}" if tail else "")) from e
    finally:
        _CLI_LOCK.release()
    out, err = proc.stdout or "", proc.stderr or ""
    if proc.returncode != 0 or not out.strip():
        blob = (err or out)[-600:]
        if _CLI_AUTH_RE.search(blob):
            # THE VERDICT BELONGS TO THE ACCOUNT THAT FAILED. On a machine's own sign-in this
            # is recorded by `think()` against that machine; writing it to the box's status here
            # would tell the owner the Base Machine is signed out when it is not.
            if not (own and own.get("kind") == "codex"):
                try:
                    box_secrets.note_codex_status("needs_reauth", blob[:200])
                except Exception as e:                   # noqa: BLE001
                    log.warning("brain.codex_status_unwritable", error=type(e).__name__)
            raise RuntimeError(f"codex not signed in (rc={proc.returncode}): {blob[:200]}")
        if _CLI_LIMIT_RE.search(blob):
            raise BudgetExceeded(f"chatgpt subscription limit: {blob[:200]}")
        if _CLI_TRANSIENT_RE.search(blob):
            raise RetryableError(f"codex transient (rc={proc.returncode}): {blob[:200]}")
        raise RuntimeError(f"codex failed (rc={proc.returncode}): {blob[:300]}")
    text = out.strip()
    try:
        state.record_spend(job_id=job_id, task=task, model=f"codex:{chosen or 'default'}",
                           cost_usd=0.0, input_tokens=0, output_tokens=0,
                           cache_write_tokens=0, cache_read_tokens=0)
    except Exception as e:                               # noqa: BLE001
        log.error("think.SPEND_UNRECORDED", task=task, model="codex", cost_usd=0.0,
                  error=str(e)[:200])
    return text


def _think_claude_code(task: str, model: str, prompt: str, *, system, cached_context,
                       max_tokens: int, job_id, timeout: float | None,
                       isolated: bool = False) -> str:
    """One reasoning call through the owner's subscription (Claude Code headless).

    Safety: `--tools \"\"` — the model gets NO tools. Prompts include UNTRUSTED
    scraped text (transcripts); a prompt injection must have nothing to grab.
    `--system-prompt` OVERRIDES Claude Code's default coding system prompt (we are
    not coding here, and the override drops ~10k tokens of irrelevant context).
    max_tokens is advisory on this backend (no CLI flag) — callers already bound
    output by prompt design; stop_reason still reports truncation when it happens.
    """
    bin_ = shutil.which("claude")
    if not bin_:
        raise RuntimeError("brain.backend=claude_code but the `claude` CLI is not "
                           "installed — run scripts/install_claude_code.sh")
    sys_text = "\n\n".join(p for p in (cached_context, system) if p)
    cmd = [bin_, "-p", prompt, "--model", _cli_model(model),
           "--output-format", "json", "--tools", ""]
    if sys_text:
        cmd += ["--system-prompt", sys_text]
    if isolated:
        # Mavrick identity lock (Track B): load NO setting sources, so /opt/aios/CLAUDE.md
        # and .claude/ (the codebase + dev-workflow context) cannot bleed into a reply to
        # the owner — that inheritance is what leaked the coding-assistant / DEVSTATE
        # framing. The explicit --system-prompt above IS the identity; this guarantees no
        # project/coding context rides along with it.
        cmd += ["--setting-sources", ""]

    try:
        # CLI startup adds ~3s; give headroom over the api-backend timeout.
        rc, out, err = _run_claude(cmd, timeout=(timeout or 120.0) + 30.0)
    except subprocess.TimeoutExpired as e:
        # KEEP WHAT THE PROCESS SAID. `TimeoutExpired` carries the partial `stdout`/`stderr`,
        # and discarding them is how the stdin warning above went unread through 29 failures in
        # one night. A timeout with no explanation is the hardest failure to diagnose; one that
        # quotes the process is usually self-solving.
        tail = e.stderr or e.stdout or b""
        if isinstance(tail, bytes):
            tail = tail.decode("utf-8", "replace")
        tail = tail.strip()[-300:]
        raise RetryableError(
            f"claude_code timed out ({task})" + (f" — said: {tail}" if tail else "")) from e

    if rc != 0 or not out.strip():
        blob = (err or out)[-400:]
        if _CLI_LIMIT_RE.search(blob):
            # The subscription window is exhausted — pause; it resets on its own.
            raise BudgetExceeded(f"claude subscription limit: {blob[:200]}")
        if _CLI_TRANSIENT_RE.search(blob):
            raise RetryableError(f"claude_code transient (rc={rc}): {blob[:200]}")
        raise RuntimeError(f"claude_code failed (rc={rc}): {blob[:300]}")

    try:
        data = json.loads(out)
    except ValueError as e:
        raise RetryableError(f"claude_code non-JSON output: {out[:200]}") from e

    text = str(data.get("result") or "")
    if data.get("is_error"):
        if _CLI_LIMIT_RE.search(text):
            raise BudgetExceeded(f"claude subscription limit: {text[:200]}")
        raise RuntimeError(f"claude_code error result: {text[:300]}")

    usage = data.get("usage") or {}
    try:
        # cost_usd=0.0 ON PURPOSE: subscription marginal cost is zero, and recording
        # the CLI's API-equivalent number would burn the $90 guard on money never
        # spent. Tokens are recorded so per-task/job visibility survives.
        state.record_spend(
            job_id=job_id, task=task, model=f"cc:{_cli_model(model)}", cost_usd=0.0,
            input_tokens=usage.get("input_tokens", 0) or 0,
            output_tokens=usage.get("output_tokens", 0) or 0,
            cache_write_tokens=usage.get("cache_creation_input_tokens", 0) or 0,
            cache_read_tokens=usage.get("cache_read_input_tokens", 0) or 0,
        )
    except Exception as e:
        log.error("think.SPEND_UNRECORDED", task=task, model=model, cost_usd=0.0,
                  error=str(e)[:200])
    if data.get("stop_reason") == "max_tokens":
        log.warning("think.truncated", task=task, model=model, max_tokens=max_tokens)
    log.info("think.done", task=task, model=f"cc:{_cli_model(model)}", cost_usd=0.0)
    return text


KNOWLEDGE_DIR = ROOT / "my" / "knowledge"
KNOWLEDGE_CAP = 40_000          # chars; it rides on every call — cached, but not free


def knowledge_context() -> str:
    """Every .md in my/knowledge/, name order, as one block — the FACTS about the business
    the brain should carry into every machine's calls (the brand brief is the VOICE). Capped
    so a buyer who drops a 2 MB PDF export in here gets a warning, not a bill."""
    if not KNOWLEDGE_DIR.is_dir():
        return ""
    parts, total = [], 0
    for f in sorted(KNOWLEDGE_DIR.glob("*.md")):
        if f.name.upper() == "README.MD":
            continue
        t = f.read_text(errors="replace")
        if total + len(t) > KNOWLEDGE_CAP:
            log.warning("brain.knowledge_capped", file=f.name, cap=KNOWLEDGE_CAP)
            t = t[: max(0, KNOWLEDGE_CAP - total)]
        parts.append(f"# {f.name}\n{t}"); total += len(t)
        if total >= KNOWLEDGE_CAP:
            break
    return "\n\n".join(parts)


def write_knowledge(name: str, text: str) -> bool:
    """Write one generated file into my/knowledge/. `core` owns that folder, so `core` writes it.

    NAMED, AND ONLY ITS OWN FILE. Everything else in there is the buyer's — what he typed about
    his own business — and a machine that rewrote those would be destroying the thing it was
    given. A generated file says so in its first line, so nobody mistakes it for their own.
    """
    try:
        KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
        (KNOWLEDGE_DIR / name).write_text(
            "<!-- Written by your box from your own sent mail. Edit freely: this file is\n"
            "     rewritten when the box learns again, so put anything you want kept in a\n"
            "     file of your own beside it. -->\n\n" + str(text).strip() + "\n")
        return True
    except OSError as e:
        log.warning("brain.knowledge_unwritable", file=name, error=type(e).__name__)
        return False


def _with_knowledge(cached_context: str | None, isolated: bool) -> str | None:
    if isolated:
        return cached_context                       # isolated calls load NO setting sources, by contract
    k = knowledge_context()
    if not k:
        return cached_context
    return k if not cached_context else k + "\n\n" + cached_context


def can_think() -> tuple[bool, str]:
    """Can this box think at all, on the backend it is configured for? (ready, why).

    A script that gated on the literal ANTHROPIC_API_KEY refused the owner's demo box, whose
    brain is backend=claude_code with an OAuth token and no API key (2026-09-05). Ask the brain,
    not an env var: the api backend needs ANTHROPIC_API_KEY; claude_code needs the `claude` CLI on
    PATH and CLAUDE_CODE_OAUTH_TOKEN in the environment. Nothing here spends.
    """
    import shutil
    be = _backend()
    if be == "codex":
        if not shutil.which("codex"):
            return False, "this box drafts on ChatGPT but the `codex` CLI is not on PATH"
        from core import box_secrets
        if not box_secrets.codex_connected():
            return False, ("this box thinks on a ChatGPT subscription but nobody is signed in — "
                           "use Sign in to ChatGPT in Set up")
        return True, "codex"
    if be == "claude_code":
        if not shutil.which("claude"):
            return False, "brain.backend=claude_code but the `claude` CLI is not on PATH"
        # ONE RESOLUTION RULE, SHARED WITH THE THING BEING ASKED ABOUT. Reading os.environ here
        # while `_run_claude` reads `box_secrets.claude_oauth_token()` is how the two would come to
        # disagree — the exact defect `probe_backend` was fixed for on 2026-09-16, one file over.
        from core import box_secrets
        if not box_secrets.claude_oauth_token():
            return False, ("this box thinks on a Claude subscription but no token is connected — "
                           "add one in Set up, or set CLAUDE_CODE_OAUTH_TOKEN in the environment")
        return True, "claude_code"
    # THE BUYER'S KEY COUNTS, and asking the environment alone is what made this answer wrong on
    # a delivered box: a customer who has typed their key into Settings has a box that can think,
    # and a probe that said otherwise would report a working box as broken.
    from core import box_secrets
    if not box_secrets.anthropic_key():
        return False, ("brain.backend=api needs an Anthropic key — set ANTHROPIC_API_KEY in the "
                       "environment, or add one in Settings")
    return True, "api"


def think(task: str, prompt: str, *, system: str | None = None,
          cached_context: str | None = None, max_tokens: int = 1024,
          job_id: str | None = None, timeout: float | None = None,
          isolated: bool = False, machine: str | None = None) -> str:
    """Run one reasoning call and return the text, on the calling machine's account if it has one.

    machine  the calling machine's key (core names none; the caller passes its own). When that
             machine chose its own AI account and it is usable, this call thinks on it. Otherwise,
             and always when `machine` is None, it thinks on the Base Machine's account exactly as
             before (docs/SCOPE_ONE_PLACE_PER_SETTING.md §4.2).

    DRAFTING NEVER STOPS FOR A MACHINE'S OWN ACCOUNT (owner, 2026-09-24, §7 question 1: "I don't
    want any lost functionality for any period of time"). If that account is refused, out of
    credit or at its limit, this same call is made again on the Base Machine's account, and the
    fallback is recorded against the machine so its AI page can say so. A transient failure is
    not a reason to switch: it raises RetryableError as it always has, and the retry comes back
    to the machine's own account.

    The $90 guard is box-wide (§7 question 2): an api call on any account is checked against it.
    """
    kw = dict(system=system, cached_context=cached_context, max_tokens=max_tokens,
              job_id=job_id, timeout=timeout, isolated=isolated)
    own = _machine_account(machine)
    if own is None:
        return _think_now(task, prompt, **kw)
    token = _CALL.set(own)
    try:
        return _think_now(task, prompt, **kw)
    except Exception as e:
        verdict = _own_account_verdict(e)
        if verdict is None:
            raise
        _own_account_failed(machine, verdict, e)
    finally:
        _CALL.reset(token)
    return _think_now(task, prompt, **kw)


def _machine_account(machine: str | None) -> dict | None:
    """The machine's own account if it chose one and it can be used; None means the box's.

    A machine that chose its own account but cannot use it right now (signed out, out of credit)
    thinks on the box's, and that is recorded, so the page tells the owner rather than drafting
    silently stopping or silently changing account.
    """
    if not machine:
        return None
    try:
        from core import machine_accounts
        own = machine_accounts.account(machine)
        if own is None and machine_accounts.choice(machine) == "own":
            machine_accounts.note_fallback(machine)
            log.warning("brain.machine_account_unusable", machine=machine,
                        status=machine_accounts.state_of(machine).get("status"))
        return own
    except Exception as e:                       # noqa: BLE001 — the box's account is always there
        log.warning("brain.machine_account_unreadable", machine=str(machine)[:40],
                    error=f"{type(e).__name__}: {e}"[:160])
        return None


_CREDIT_RE = re.compile(r"credit balance|billing|payment required|\b402\b", re.I)
# WIDER THAN `_CLI_AUTH_RE`, and only ever asked about a machine's own account: an expired Claude
# sign-in answers "Invalid API key · Please run /login", which the box's pattern never needed.
_OWN_AUTH_RE = re.compile(r"invalid (?:api key|x-api-key|bearer)|please run /login"
                          r"|oauth token|authentication[_ ]error|not signed in", re.I)


def _own_account_verdict(e: Exception) -> str | None:
    """Why a machine's own account just failed, as a status, or None when it did not fail ON ITS
    OWN ACCOUNT'S ACCOUNT (a transient error, or anything not about who is paying)."""
    if isinstance(e, RetryableError):
        return None
    if isinstance(e, BudgetExceeded):
        # A subscription window closing, or the box-wide $90 guard. Either way, not a verdict on
        # the account: fall back for this call and leave its status alone.
        return "limit"
    status = getattr(e, "status_code", None)
    name = type(e).__name__
    text = str(e)
    if status in (401, 403) or name in ("AuthenticationError", "PermissionDeniedError") \
            or _CLI_AUTH_RE.search(text) or _OWN_AUTH_RE.search(text):
        return "needs_reauth"
    if status == 402 or _CREDIT_RE.search(text):
        return "payment_required"
    return None


def _own_account_failed(machine: str, verdict: str, e: Exception) -> None:
    """Record what the machine's own account said, and that this call fell back to the box's."""
    try:
        from core import machine_accounts
        if verdict in ("needs_reauth", "payment_required"):
            machine_accounts.note_status(machine, verdict, str(e)[:200])
        machine_accounts.note_fallback(machine)
    except Exception as err:                     # noqa: BLE001 — bookkeeping never stops a draft
        log.warning("brain.machine_account_unrecorded", machine=machine,
                    error=f"{type(err).__name__}: {err}"[:160])
    log.warning("brain.machine_account_fell_back", machine=machine, verdict=verdict,
                error=f"{type(e).__name__}: {e}"[:200])


def _think_now(task: str, prompt: str, *, system: str | None = None,
               cached_context: str | None = None, max_tokens: int = 1024,
               job_id: str | None = None, timeout: float | None = None,
               isolated: bool = False) -> str:
    """One reasoning call on the account `_CALL` names, or the box's. Everything `think()` did
    before per-machine accounts is here unchanged.

    timeout  per-call override (seconds). A tiny router call (max_tokens=8) should
             not inherit the client's 120s default — pass e.g. timeout=20.
    isolated when True on the claude_code backend, the call loads NO setting sources
             (no project CLAUDE.md / .claude context) — used by the agent so the
             codebase/dev framing can't bleed into a reply. No-op on the api backend
             (the Messages API has no project context to inherit).

    task            selects the model tier (see config `models`).
    cached_context  large reused text (e.g. brand-brief.md) sent as a cached
                    system block so repeat calls pay only the delta.
    Raises BudgetExceeded (via cost_guard) if this call could cross the ceiling.
    """
    cached_context = _with_knowledge(cached_context, isolated)
    model = _model_for(task)
    if _backend() == "codex":
        return _think_codex(task, prompt, system=system, cached_context=cached_context,
                            job_id=job_id, timeout=timeout)
    if _backend() == "claude_code":
        # Subscription path: no USD pre-check (marginal cost is zero) — the
        # subscription's own usage window is the guard, surfacing as BudgetExceeded.
        return _think_claude_code(task, model, prompt, system=system,
                                  cached_context=cached_context,
                                  max_tokens=max_tokens, job_id=job_id,
                                  timeout=timeout, isolated=isolated)

    estimate = _estimate_usd(model, prompt, cached_context, system, max_tokens)
    cost_guard.check(estimate)  # refuse BEFORE spending if this call could cross the line

    system_blocks = []
    if cached_context:
        # NOTE: prompt caching is GA; confirm cache_control shape against your
        # installed anthropic SDK version.
        system_blocks.append({"type": "text", "text": cached_context,
                              "cache_control": {"type": "ephemeral"}})
    if system:
        system_blocks.append({"type": "text", "text": system})

    kwargs = {"model": model, "max_tokens": max_tokens,
              "messages": [{"role": "user", "content": prompt}]}
    if system_blocks:
        kwargs["system"] = system_blocks
    if timeout is not None:
        kwargs["timeout"] = timeout

    try:
        resp = _client_().messages.create(**kwargs)
    except Exception as e:
        # The SDK already retried transient errors (max_retries on the client). If we
        # still land here with a transient class, the layer that KNOWS it's transient
        # (this one) re-raises RetryableError so the worker requeues instead of failing
        # the job permanently. A real client error (bad request, auth) is NOT retryable
        # and propagates as-is → terminal. Classified without importing the SDK at
        # module load (spine stays SDK-free).
        if _is_transient(e):
            log.warning("think.transient", task=task, model=model, error=str(e)[:200])
            raise RetryableError(f"anthropic transient ({type(e).__name__}): {e}") from e
        raise

    usage = resp.usage
    cost = _cost_usd(model, usage)

    try:
        state.record_spend(
            job_id=job_id, task=task, model=model, cost_usd=cost,
            input_tokens=getattr(usage, "input_tokens", 0),
            output_tokens=getattr(usage, "output_tokens", 0),
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0),
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0),
        )
    except Exception as e:
        # The call already happened and was PAID. A ledger hiccup must not fail the
        # job (the result in hand is real), but an unrecorded paid call undercounts
        # the budget guard — so this is deliberately the loudest log line we emit.
        log.error("think.SPEND_UNRECORDED", task=task, model=model,
                  cost_usd=round(cost, 6), error=str(e)[:200])

    if getattr(resp, "stop_reason", None) == "max_tokens":
        # Silent truncation poisons downstream parsing (a cut-off JSON scores 0/0;
        # a cut-off rewrite loses its CTA). Surface it where the caller can see.
        log.warning("think.truncated", task=task, model=model, max_tokens=max_tokens)

    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    log.info("think.done", task=task, model=model, cost_usd=round(cost, 5))
    return text


# ── run_agent(): a coworker's shift (docs/SCOPE_SHIFTS.md §4) ──────────────────────────────────────
#
# THE SECOND GATEWAY, BESIDE think() AND FOR THE SAME REASON (non-negotiable #2). think() is one
# question and one answer with no tools. A coworker's shift is a loop: the AI reads through the
# box's MCP, drafts proposals, and leaves notes in its workspace. That loop is the `claude` CLI,
# and every run of it comes through here so the backend, the $90 guard and the spend ledger stay
# authoritative for it exactly as they are for think().
#
# WHAT THE AI GETS, AND WHY EACH FLAG IS THERE. Measured on the pinned CLI (2.1.278) on
# 2026-09-26; a live run told to execute `id` and print its environment reported that it had no
# shell, and its write outside the allowed list came back in `permission_denials`.
#
#   --tools Read,Write[,WebSearch][,WebFetch]   the ONLY built-ins. No Bash, no code runners.
#   --restricted            belt and braces on the same point: drops command/code tools unless
#                           --tools names them, ignores user/project/local settings, confines
#                           file tools to the working directory (the coworker's workspace).
#   --strict-mcp-config + --mcp-config   exactly one MCP server, the box's, on this run's seat;
#                           no server configured anywhere else on the box can ride along.
#   --permission-mode dontAsk + --allowedTools  anything not listed is refused, never prompted.
#                           Nobody is there to answer a prompt; a prompt would hang the shift.
#   --setting-sources ""    no CLAUDE.md or .claude/ context from the box's own code.
#   --max-turns             the coworker's turn limit. Hidden from --help, and parsed (measured).
#   --no-session-persistence   nothing about the run is kept in the CLI's own store.
#   --max-budget-usd        API-key boxes only: the run's own spending ceiling, in dollars.
#
# WHERE IT RUNS. On a box, always in OSDev1's sandbox (core/coworkers/sandbox.py, #1613): its
# own user, a locked systemd unit, the workspace bind-mounted, /opt/aios hidden. `sandboxed=False`
# exists for tests and development only; on a box a coworker never runs outside it. Both paths
# take the same flags and reach the same verdicts, so what the tests prove is what the box runs.
#
# WHAT CROSSES INTO THE UNIT, AND HOW (measured by OSDev1 on a box, #1613). A unit starts with
# systemd's environment, not the caller's, and has its own /tmp, so neither `env=` nor a tempfile
# reaches it. Instead:
#   · the credentials (the AI account's, and the run seat as AIOS_SEAT) go in a root-only
#     EnvironmentFile that systemd reads before dropping to the coworker's user
#     (sandbox.write_env). Directly, the same values go in a BUILT environment, never a copy of
#     os.environ: the box's environment carries every vendor key it has.
#   · the MCP config and the job go in the run's private directory, bound read-only into the unit
#     (sandbox.run_dir / write_private), and the CLI is given the IN-UNIT paths. The MCP config
#     names the seat as ${AIOS_SEAT}, which the CLI fills in from its environment (measured on
#     2.1.278), so even a file the coworker can read holds no credential.
#   · the prompt goes in on stdin: `systemd-run --pipe` passes it through.
#   · a run past its minutes is STOPPED (sandbox.stop_argv): killing the waiting client leaves the
#     unit running, and a coworker drafting after its receipt says FAILED is worse than one that
#     stopped.
#
# NOT UNDER _CLI_LOCK. A shift lasts minutes, and holding the lock that long would starve every
# draft the box makes meanwhile. Shifts queue behind each other in the runner instead, one at a
# time, which is the same one-core argument made where it belongs.

class AgentLimit(RuntimeError):
    """The run hit one of its own limits (minutes, turns or dollars) and was stopped. FAILED, with
    the limit named, and not retried: the same run would hit the same limit."""


_AGENT_ENV_KEEP = ("PATH", "LANG", "LC_ALL", "TZ", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
                   "https_proxy", "http_proxy", "no_proxy", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE")
AGENT_WEB = {"search": "WebSearch", "read": "WebFetch"}
# api_error_status values worth the one retry (§3): rate limited, overloaded, the API's own 5xx.
_AGENT_RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}
# A SUBSCRIPTION WINDOW, NARROWER THAN think()'s _CLI_LIMIT_RE on purpose: that one also matches
# "rate limit", and an API's 429 is a busy minute worth the one retry, not a window that closes the
# shift. Only the words a closed window actually uses count here.
_AGENT_WINDOW_RE = re.compile(r"usage limit|limit (?:reached|will reset)|out of (?:usage|credits)"
                              r"|quota", re.I)


def _run_agent_cli(cmd: list[str], *, stdin: str, env: dict | None, cwd: str | None,
                   timeout: float) -> tuple[int, str, str]:
    """Subprocess seam (single point for tests). Returns (rc, stdout, stderr). No lock: see above."""
    proc = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout,
                          env=env, cwd=cwd)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _stop_agent_unit(cmd: list[str]) -> None:
    """Seam: stop a sandboxed run's unit. Best effort, and loud when it fails."""
    try:
        subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
    except Exception as e:                           # noqa: BLE001 — RuntimeMaxSec is the backstop
        log.error("brain.agent_unit_not_stopped", cmd=" ".join(cmd), error=f"{type(e).__name__}: {e}")


def run_agent(prompt: str, *, run_id: str, coworker: str, workspace, system: str = "",
              mcp: dict | None = None, web=(), max_turns: int = 40, max_minutes: int = 30,
              max_usd: float = 1.0, task: str = "coworker", sandboxed: bool = True) -> dict:
    """Run one coworker shift's AI loop and return what it did. Raises on anything else.

    prompt      today's instructions for the shift (the runner builds them); goes in on stdin
    run_id      the run's id; its spend lands in the ledger under it, as job_id
    coworker    the coworker's slug, which names the unit
    workspace   the coworker's workspace on the box, an existing absolute directory
    system      the coworker's standing instructions (its job.md)
    mcp         {"url": ..., "credential": ...}: the box's MCP as the CLI reaches it, and this
                run's seat. None: no MCP. In the sandbox the url is http://127.0.0.1:8000/mcp.
    web         any of "search", "read": the web capabilities the coworker was granted
    max_*       the coworker's limits. max_usd applies on an API-key box only
    sandboxed   False only in tests and development. On a box, always True.

    Returns {"text", "turns", "minutes", "cost_usd", "api_usd", "backend", "model", "denied"}.
    `cost_usd` is what was spent: the CLI's figure on an API-key box, 0.0 on a subscription (its
    marginal cost is zero, as think() records it). `api_usd` is the CLI's figure either way.

    Raises BudgetExceeded (the $90 guard, or a subscription window), RetryableError (the API was
    busy or unreachable: the runner's one retry), AgentLimit (a limit was hit), RuntimeError
    (anything else, named), and SandboxError when the sandbox cannot start it safely.
    """
    ws = pathlib.Path(workspace)
    if not ws.is_absolute() or not ws.is_dir():
        raise ValueError(f"a run's workspace is an existing absolute directory, got {workspace!r}")
    unknown = sorted(set(web) - set(AGENT_WEB))
    if unknown:
        raise ValueError(f"web may be {sorted(AGENT_WEB)}, got {unknown}")
    ready, why = can_think()
    if not ready:
        raise RuntimeError(f"a coworker cannot run: {why}")
    be = _backend()
    if be not in ("claude_code", "api"):
        # A plain refusal, not a quiet fallback onto somebody else's account.
        raise RuntimeError("coworkers run on a Claude account today, and this box thinks on "
                           "ChatGPT. Connect a Claude sign-in or an Anthropic key in Set up")
    if sandboxed:
        from core.coworkers import sandbox as _sandbox
        bin_ = _sandbox.cli_path()                 # refuses a CLI under /root, by name
        unit = _sandbox.unit_name(coworker, run_id)
    else:
        bin_ = shutil.which("claude")
        if not bin_:
            raise RuntimeError("a coworker runs on the `claude` CLI, and it is not installed — "
                               "run scripts/install_claude_code.sh")
    from core import box_secrets
    if be == "api":
        # Refuse BEFORE spending if the run's own ceiling could cross the box's line.
        cost_guard.check(max_usd)

    model = _model_for(task)
    builtins = ["Read", "Write"] + [AGENT_WEB[w] for w in ("search", "read") if w in set(web)]
    allowed = builtins + (["mcp__aios"] if mcp else [])

    secrets_ = {"DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
    if be == "api":
        secrets_["ANTHROPIC_API_KEY"] = box_secrets.anthropic_key()
    else:
        secrets_["CLAUDE_CODE_OAUTH_TOKEN"] = box_secrets.claude_oauth_token()
    if mcp:
        secrets_["AIOS_SEAT"] = mcp["credential"]
    servers = {}
    if mcp:
        servers["aios"] = {"type": "http", "url": mcp["url"],
                           "headers": {"Authorization": "Bearer ${AIOS_SEAT}"}}
    mcp_json = json.dumps({"mcpServers": servers})

    private = None
    try:
        if sandboxed:
            private = _sandbox.run_dir(run_id)
            mcp_path = _sandbox.write_private(private, "mcp.json", mcp_json)
            sys_path = _sandbox.write_private(private, "system.md", system or "")
            env_file = _sandbox.write_env(private, secrets_)
        else:
            private = tempfile.mkdtemp(prefix="aios-run-")
            os.chmod(private, 0o700)
            mcp_path = os.path.join(private, "mcp.json")
            sys_path = os.path.join(private, "system.md")
            pathlib.Path(mcp_path).write_text(mcp_json)
            pathlib.Path(sys_path).write_text(system or "")
            os.mkdir(os.path.join(private, "home"))

        cli = [bin_, "-p", "--output-format", "json", "--model", _cli_model(model),
               "--tools", ",".join(builtins), "--allowedTools", ",".join(allowed),
               "--restricted", "--strict-mcp-config", "--mcp-config", mcp_path,
               "--permission-mode", "dontAsk", "--setting-sources", "",
               "--max-turns", str(int(max_turns)), "--no-session-persistence",
               "--system-prompt-file", sys_path]
        if be == "api":
            cli += ["--max-budget-usd", f"{max_usd:.2f}"]

        if sandboxed:
            cmd = _sandbox.argv(unit=unit, command=cli, workspace_dir=str(ws),
                                minutes=int(max_minutes), env_file=env_file, ro_dir=private)
            env, cwd = None, None
        else:
            passthrough = {k: os.environ[k] for k in _AGENT_ENV_KEEP if k in os.environ}
            cmd = cli
            env = {**passthrough, "HOME": os.path.join(private, "home"), **secrets_}
            cwd = str(ws)

        started = time.monotonic()
        log.info("brain.agent_start", run_id=run_id, coworker=coworker, backend=be,
                 model=_cli_model(model), tools=builtins, mcp=bool(mcp), max_turns=max_turns,
                 max_minutes=max_minutes, sandboxed=sandboxed)
        try:
            rc, out, err = _run_agent_cli(cmd, stdin=prompt, env=env, cwd=cwd,
                                          timeout=max_minutes * 60 + 30)
        except subprocess.TimeoutExpired as e:
            if sandboxed:
                _stop_agent_unit(_sandbox.stop_argv(unit))
            _record_unreported_spend(run_id=run_id, be=be, model=model, task=task,
                                     max_usd=max_usd, why="timeout")
            raise AgentLimit(f"stopped: it ran past its {max_minutes:g}-minute limit") from e
        minutes = (time.monotonic() - started) / 60
    finally:
        if private:
            shutil.rmtree(private, ignore_errors=True)

    return _agent_verdict(rc, out, err, run_id=run_id, be=be, model=model, task=task,
                          minutes=minutes, max_turns=max_turns, max_usd=max_usd)


_AGENT_SPENT_AFTER_MIN = 1.0


def _record_unreported_spend(*, run_id, be, model, task, max_usd, why) -> None:
    """A run that ended without reporting its cost is recorded at its ceiling.

    A TIMEOUT IS INDETERMINATE, NEVER ASSUMED FREE (non-negotiable #4). On an API-key box the run
    may have spent anything up to --max-budget-usd, so that is what the $90 guard sees: an
    overcount that can only make the guard stricter, never an undercount that hides money. A
    subscription box spends no dollars, so nothing is written for it.
    """
    if be != "api":
        return
    log.error("brain.agent_spend_unreported", run_id=run_id, why=why, recorded_usd=max_usd)
    try:
        state.record_spend(job_id=run_id, task=task, model=f"{model}:unreported",
                           cost_usd=float(max_usd), input_tokens=0, output_tokens=0,
                           cache_write_tokens=0, cache_read_tokens=0)
    except Exception as e:                           # noqa: BLE001 — never mask the real ending
        log.error("brain.agent_SPEND_UNRECORDED", run_id=run_id, cost_usd=max_usd,
                  error=str(e)[:200])


def _agent_verdict(rc: int, out: str, err: str, *, run_id, be, model, task, minutes,
                   max_turns, max_usd) -> dict:
    """What the CLI's JSON says happened, as a result or a named exception."""
    try:
        data = json.loads(out) if out.strip() else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        # A CLI that dies at start spent nothing; one that ran a minute or more before dying
        # may have spent up to its ceiling, and never said how much.
        if minutes >= _AGENT_SPENT_AFTER_MIN:
            _record_unreported_spend(run_id=run_id, be=be, model=model, task=task,
                                     max_usd=max_usd, why=f"no result (rc={rc})")
        blob = (err or out)[-400:]
        if _AGENT_WINDOW_RE.search(blob):
            raise BudgetExceeded(f"claude subscription limit: {blob[:200]}")
        if _CLI_TRANSIENT_RE.search(blob):
            raise RetryableError(f"agent run transient (rc={rc}): {blob[:200]}")
        raise RuntimeError(f"agent run failed (rc={rc}): {blob[:300]}")

    # THE MONEY IS RECORDED BEFORE ANY VERDICT: a run stopped at its limit still spent.
    api_usd = float(data.get("total_cost_usd") or 0.0)
    spent = api_usd if be == "api" else 0.0
    usage = data.get("usage") or {}
    label = model if be == "api" else f"cc:{_cli_model(model)}"
    try:
        state.record_spend(
            job_id=run_id, task=task, model=label, cost_usd=spent,
            input_tokens=usage.get("input_tokens", 0) or 0,
            output_tokens=usage.get("output_tokens", 0) or 0,
            cache_write_tokens=usage.get("cache_creation_input_tokens", 0) or 0,
            cache_read_tokens=usage.get("cache_read_input_tokens", 0) or 0)
    except Exception as e:                           # noqa: BLE001 — the run happened and was paid
        log.error("brain.agent_SPEND_UNRECORDED", run_id=run_id, cost_usd=round(spent, 6),
                  error=str(e)[:200])

    turns = int(data.get("num_turns") or 0)
    subtype = str(data.get("subtype") or "")
    text = str(data.get("result") or "")
    if subtype == "error_max_turns":
        raise AgentLimit(f"stopped: it reached its {max_turns}-turn limit")
    if "budget" in subtype:
        raise AgentLimit(f"stopped: it reached its ${max_usd:.2f} limit for one run")
    # JUDGED ON is_error, NOT subtype: a 401 comes back subtype "success" (OSDev1, measured on a
    # box, #1613). api_error_status says what the API answered, and it alone decides the retry.
    if data.get("is_error") or subtype != "success":
        status = data.get("api_error_status")
        blob = text or err[-300:] or subtype
        if status in (401, 403) or _CLI_AUTH_RE.search(blob):
            raise RuntimeError(f"the AI account refused the run ({status or 'auth'}): the Claude "
                               f"sign-in or key needs attention in Set up")
        if _AGENT_WINDOW_RE.search(blob):
            raise BudgetExceeded(f"claude subscription limit: {blob[:200]}")
        if status in _AGENT_RETRY_STATUS or (status is None and _CLI_TRANSIENT_RE.search(blob)):
            raise RetryableError(f"agent run: the AI was busy or unreachable "
                                 f"({status or 'network'}): {blob[:200]}")
        raise RuntimeError(f"agent run ended with {subtype or 'an error'}"
                           f"{f' ({status})' if status else ''}: {blob[:300]}")

    denied = [str(d.get("tool_name")) for d in (data.get("permission_denials") or [])
              if isinstance(d, dict)]
    log.info("brain.agent_done", run_id=run_id, turns=turns, minutes=round(minutes, 2),
             cost_usd=round(spent, 5), api_usd=round(api_usd, 5), denied=len(denied))
    return {"text": text, "turns": turns, "minutes": round(minutes, 3), "cost_usd": spent,
            "api_usd": api_usd, "backend": be, "model": label, "denied": denied}
