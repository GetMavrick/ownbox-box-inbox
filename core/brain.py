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
import json
import os
import re
import shutil
import subprocess
import threading
import time

from core import cost_guard, state
from core.config import get_config, settings
from core.exceptions import BudgetExceeded, RetryableError
from core.logging import get_logger

log = get_logger(__name__)

_client = None

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
    global _client
    if _client is None:
        from anthropic import Anthropic  # imported lazily so the spine needs no SDK
        # Autonomous system: transient 429/529 (overloaded) from Anthropic are
        # routine and must not fail a whole job. The SDK does exponential backoff
        # for us when max_retries is set. A per-call timeout bounds a hung request
        # (a tiny router call should never block the worker for minutes).
        _client = Anthropic(
            api_key=settings.anthropic_api_key,
            max_retries=4,      # ~exponential backoff on 429/500/503/529 + network errors
            timeout=120.0,      # seconds; generous for reasoning, finite for safety
        )
    return _client


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
    return (get_config().get("brain") or {}).get("backend", "api")


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
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
    finally:
        _CLI_LOCK.release()
    return proc.returncode, proc.stdout or "", proc.stderr or ""


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
    if be == "claude_code":
        if not shutil.which("claude"):
            return False, "brain.backend=claude_code but the `claude` CLI is not on PATH"
        if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            return False, "brain.backend=claude_code needs CLAUDE_CODE_OAUTH_TOKEN in the environment"
        return True, "claude_code"
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False, "brain.backend=api needs ANTHROPIC_API_KEY in the environment"
    return True, "api"


def think(task: str, prompt: str, *, system: str | None = None,
          cached_context: str | None = None, max_tokens: int = 1024,
          job_id: str | None = None, timeout: float | None = None,
          isolated: bool = False) -> str:
    """Run one reasoning call and return the text.

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
