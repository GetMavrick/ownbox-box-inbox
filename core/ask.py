"""Ask your box: the buyer's own AI passes a question on, and the box's own AI answers it with the box's own tools.

docs/SCOPE_SMART_BOX_ANSWERS.md (#1839), Phase 1. Owner, 2026-10-02: *"They need to be able to ask things and the
box needs to be smart enough to provide answers and suggest additional data points that can be gathered or tasks
that can be started."* Ruled D1 the same evening: *"Yes, let's make sure that the box is going to use inference and
give the information and then offer additional reports data points and tasks."*

HOW IT ANSWERS. As a one-off coworker run (core/coworkers/runner.py is the worked example, and every safety
property comes from there): the box's AI runs in the shift sandbox (core/coworkers/sandbox.py: its own user, no
keys, no database, no shell), holding a seat minted for this one question and revoked when it ends, and it reaches
the box only through the box's own MCP tools. Its seat holds WHAT THE ASKER'S SEAT HOLDS AND NOTHING MORE: asking
can never widen what a connection may see or do. Anything it starts is a proposal on Approvals, waiting for a tap.

EVERY ANSWER HAS FOUR PARTS (scope §2): the answer in plain words with the box's own numbers, what it means, what
to ask next, and what the box can start. The run ends with them as JSON; anything it proposed is read back from
Approvals, never taken on the model's word.

THE WAIT (measured 2026-10-02 from each app's own documentation, posted on the wall): Claude gives a tool 240
seconds, ChatGPT about 60, Claude Code 60 to the first byte, and a progress message extends none of them. So an
ask waits WAIT_S, inside every one of those. A question that needs longer keeps working, and the answer comes back
with an ask id the buyer's AI collects with `core.ask_result`. Today's brief (core/brief.py) is written ahead and
never waits at all.

THREE WAYS IT ANSWERS, by what the box thinks on:
  * a Claude sign-in, an Anthropic key or ChatGPT: the agent above, the box's tools on the run's own seat
    (ChatGPT through `codex exec`, core/brain.py `_run_agent_codex`, #1857 H3);
  * any other thinking backend: one think() call over today's stored numbers (core/brief.facts), no tools;
  * no AI signed in (D5, ruled 2026-10-02): today's brief in plain words, and "sign in your AI on Settings for
    insights".

ONE QUESTION AT A TIME. Each agent run is a sandbox unit with up to 1 GB; two at once on a base box's 2 GB is how
the web process gets killed. A second question while one is working is told so, in words.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import threading
import time
from datetime import datetime, timezone

from core.logging import get_logger

log = get_logger(__name__)

MACHINE = "core"
NS = "core"
PREFIX = "ask:"                            # box_settings key per ask: "ask:<id>"
AI_TASK = "ask"                            # config models: -> haiku, the smallest that answers well
WAIT_S = 45                                # inside ChatGPT's ~60 s and Claude Code's 60 s to first byte
MAX_MINUTES = 3                            # the sandbox's own kill; an answer is not a shift
MAX_TURNS = 14
MAX_USD = 0.50                             # an API-key box: one question never spends more than this
KEEP_S = 24 * 3600                         # an answer can be collected for a day, then it is gone
QUESTION_MIN, QUESTION_MAX = 3, 1000
MCP_URL = "http://127.0.0.1:8000/mcp"      # the box's MCP, as the sandboxed CLI reaches it (runner.MCP_URL)
SLUG = "ask"                               # names the sandbox unit: aios-coworker-ask-<id>
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# "_ask": runner.discover() skips folders starting with "_", so this is never read as a coworker.
WORKSPACE = os.path.join(_ROOT, "my", "coworkers", "_ask", "workspace")
_ID = re.compile(r"^ask_[0-9a-f]{12}$")
_SELF = ("core.ask", "core.ask_result", "core_ask", "core_ask_result")

_SYSTEM = """You are the AI inside a small business's own business machine (the box). The owner asked a question \
through their own AI app, and you answer it for them, using the box's tools (named mcp__aios__...).

How to answer:
- Read before you answer: call the box's tools to get the real numbers. Start with core_brief for today's picture \
when it helps, then the tools that answer the question.
- Every number, name and date in your answer must come from a tool result. Never invent one, and never guess a \
cause you cannot see. If the box can't see something, say so plainly and say which data source would let it \
(connected on Settings, Data Sources).
- Plain, warm words for a busy owner. No field names, no raw data, no JSON or code in the answer itself. Use the \
full links the tools give you when pointing the owner somewhere.
- Only draft or propose something (tools with draft or propose in the name) when the question asks you to act. \
Every proposal waits for the owner's tap on Approvals; nothing sends, publishes or spends without it.
- Never call core_ask or core_ask_result: you are the one answering.
- Never use these words: phone, ring, call, dial, line, voice, engine. Say mobile app.

Finish with ONE JSON object and nothing after it:
{"answer": "the answer, two to six sentences, with the numbers", "means": "one or two sentences on what it means \
for the business, or empty", "ask_next": ["up to 3 questions the owner could ask next that the box's tools can \
answer"], "can_start": [{"tool": "the exact name of a box tool with propose in it", "why": "one sentence"}]}"""

_THINK_SYSTEM = """You answer a small business owner's question about their own business, using ONLY the facts \
given (today's and yesterday's stored numbers from each part of the business). Never invent a number, name, cause \
or event; if the facts don't answer it, say what the box can't see. Plain, warm words; no field names. Never use \
the words phone, ring, call, dial, line, voice or engine. Reply with JSON only: {"answer": "…", "means": "…", \
"ask_next": ["…"], "can_start": [{"tool": "a name copied exactly from startable", "why": "…"}]}"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(t: datetime | None = None) -> str:
    return (t or _now()).isoformat(timespec="seconds")


# ── the store: one box_settings row per ask, shared by both web processes ──────────────────────────────────
def _key(ask_id: str) -> str:
    return PREFIX + ask_id


def _save(ask_id: str, row: dict) -> None:
    from core import box_settings
    box_settings.put(NS, _key(ask_id), row, set_by="ask")


def _load(ask_id: str) -> dict | None:
    from core import box_settings
    got = box_settings.get(NS, _key(ask_id))
    return got if isinstance(got, dict) else None


def _all() -> list[tuple[str, dict, str]]:
    """(ask id, row, set_at) for every stored ask."""
    from core import state
    out = []
    with state.connect() as c:
        for key, value, set_at in c.execute(
                "SELECT key, value, set_at FROM box_settings WHERE machine = ? AND key LIKE ? AND user_id = ''",
                (NS, PREFIX + "%")).fetchall():
            try:
                row = json.loads(value)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append((key[len(PREFIX):], row, set_at))
    return out


def _age_s(iso: str) -> float:
    try:
        return (_now() - datetime.fromisoformat(str(iso))).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def _prune() -> None:
    from core import state
    old = [i for i, row, _ in _all() if _age_s(row.get("started_at")) > KEEP_S]
    if old:
        with state.connect() as c:
            c.executemany("DELETE FROM box_settings WHERE machine = ? AND key = ? AND user_id = ''",
                          [(NS, _key(i)) for i in old])


def _busy() -> bool:
    """Another question is being answered right now (its run can't outlast MAX_MINUTES plus the sandbox's grace)."""
    return any(row.get("status") == "working" and _age_s(row.get("started_at")) < MAX_MINUTES * 60 + 150
               for _, row, _ in _all())


# ── what an answer looks like ───────────────────────────────────────────────────────────────────────────────
def _clean(s, n: int) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()[:n]


def _parse(text: str) -> dict:
    """The run's closing JSON, or the run's own words as the answer when it gave none."""
    t = str(text or "")
    got = None
    end = t.rfind("}")
    start = t.rfind("{", 0, end + 1)
    # The LAST object that parses: the answer's own text may hold braces before it.
    while start != -1 and end != -1:
        try:
            got = json.loads(t[start:end + 1])
            break
        except ValueError:
            start = t.rfind("{", 0, start)
    if not isinstance(got, dict) or not got.get("answer"):
        plain = t[:start].strip() if isinstance(got, dict) else t.strip()
        return {"answer": _clean(plain or "The box finished without an answer.", 2000), "means": "",
                "ask_next": [], "can_start": []}
    return {"answer": _clean(got.get("answer"), 2000), "means": _clean(got.get("means"), 600),
            "ask_next": [q for q in (_clean(a, 120) for a in (got.get("ask_next") or []) if isinstance(a, str))
                         if q][:3],
            "can_start": [c for c in got.get("can_start") or [] if isinstance(c, dict)][:3]}


def _starts(wanted: list, held_caps) -> list[dict]:
    """The proposals it offered, kept only when the box really has that tool AND the asker could use it."""
    from core.connector import tools
    reg = tools.registry()
    by_flat = {n.replace(".", "_"): n for n in reg}
    out = []
    for c in wanted:
        raw = _clean(c.get("tool"), 120)
        raw = raw.split("__")[-1] if raw.startswith("mcp__") else raw
        name = raw if raw in reg else by_flat.get(raw)
        spec = reg.get(name or "")
        if not spec or spec.get("capability") != "write:proposals" or name.startswith("app_"):
            continue
        if spec["capability"] not in held_caps or name in {x["tool"] for x in out}:
            continue
        out.append({"tool": name, "title": str(spec.get("title") or name), "why": _clean(c.get("why"), 200)})
    return out


def _proposed(labels: tuple, since: str) -> list[dict]:
    """What the run put on Approvals, read from Approvals itself, never from what the model says it did."""
    from core import state
    want = [x for x in labels if x]
    if not want:
        return []
    with state.connect() as c:
        rows = c.execute(
            "SELECT id, title FROM approvals WHERE created_at >= ? AND proposed_by IN (%s) ORDER BY created_at"
            % ",".join("?" * len(want)), (since, *want)).fetchall()
    return [{"id": r[0], "title": r[1]} for r in rows]


def _text(row: dict) -> str:
    """The answer in words, ready for an AI app to show as is."""
    st = row.get("status")
    if st == "working":
        return (f"I'm still working on that. Ask me for the answer in a minute: it's ask {row['ask_id']} "
                f"(core.ask_result).")
    if st == "failed":
        said = f"I couldn't answer that this time: {row.get('why') or 'the box AI did not finish'}."
        return "\n\n".join(x for x in (said, row.get("fallback") or "") if x)
    out = [row.get("answer") or ""]
    if row.get("means"):
        out.append(f"What it means: {row['means']}")
    if row.get("proposed"):
        out.append("Waiting for your OK on Approvals: " + " · ".join(p["title"] for p in row["proposed"]))
    if row.get("ask_next"):
        out.append("Ask me next: " + " · ".join(row["ask_next"]))
    if row.get("can_start"):
        out.append("I can start: " + " · ".join(c["title"] for c in row["can_start"])
                   + " (each waits for your OK on Approvals)")
    if row.get("insights"):
        from core import brief
        link = brief._link("/settings/ai")
        out.append(row["insights"] + (f": {link}" if link else "."))
    return "\n\n".join(x for x in out if x)


def _public(row: dict) -> dict:
    keep = ("ask_id", "status", "question", "answer", "means", "ask_next", "can_start", "proposed", "answered_by",
            "took_s", "insights", "why", "unavailable")
    out = {k: row[k] for k in keep if k in row}
    out["text"] = _text(row)
    return out


# ── the three ways it answers ───────────────────────────────────────────────────────────────────────────────
def _workspace() -> str:
    os.makedirs(WORKSPACE, exist_ok=True)
    try:
        from core.coworkers import sandbox
        shutil.chown(WORKSPACE, user=sandbox.USER, group=sandbox.USER)
    except (LookupError, PermissionError, OSError):
        pass                                             # a laptop without the user: tests
    return WORKSPACE


def _agent(ask_id: str, question: str, caps: list, *, run_agent=None) -> dict:
    """The box's AI, on its own seat, in the sandbox. -> the finished row's fields. Never raises."""
    from core import brain
    from core.connector import seats
    run_agent = run_agent or brain.run_agent
    since = _iso()
    label = f"Ask {ask_id}"
    seat_id, cred = seats.mint(label, "act", capabilities=caps)
    t0 = time.monotonic()
    try:
        result = run_agent(question, run_id=ask_id, coworker=SLUG, workspace=_workspace(), system=_SYSTEM,
                           mcp={"url": MCP_URL, "credential": cred}, web=(), max_turns=MAX_TURNS,
                           max_minutes=MAX_MINUTES, max_usd=MAX_USD, task=AI_TASK)
        got = _parse(result.get("text") or "")
        return {"status": "answered", "answered_by": "box_ai", **got,
                "can_start": _starts(got["can_start"], set(caps)),
                "proposed": _proposed((label, seat_id), since), "took_s": round(time.monotonic() - t0, 1)}
    except Exception as e:                                # noqa: BLE001 — every ending has a sentence
        log.warning("ask.failed", ask_id=ask_id, error=f"{type(e).__name__}: {str(e)[:200]}")
        why = ("it reached its time limit" if isinstance(e, brain.AgentLimit) else
               "the AI account's limit was reached" if type(e).__name__ == "BudgetExceeded" else
               "the AI was busy or could not be reached" if type(e).__name__ == "RetryableError" else
               f"the box AI stopped ({type(e).__name__})")
        return {"status": "failed", "why": why, "proposed": _proposed((label, seat_id), since),
                "took_s": round(time.monotonic() - t0, 1), **_fallback()}
    finally:
        seats.revoke(seat_id)


def _think(question: str, caps: set, *, think=None) -> dict:
    """A backend the agent cannot run on: one answer over today's stored numbers, no tools."""
    from core import brain, brief
    f = brief.facts()
    ask = {"question": question, "facts": {"today": {"needs_you": f["needs_you"], "machines": f["machines"]},
                                           "yesterday": f["yesterday"]},
           "startable": [s["tool"] for s in brief.startable()]}
    raw = (think or brain.think)(AI_TASK, json.dumps(ask, ensure_ascii=False), system=_THINK_SYSTEM,
                                 max_tokens=900, timeout=40)
    got = _parse(raw)
    return {"status": "answered", "answered_by": "box_ai_facts", **got,
            "can_start": _starts(got["can_start"], caps), "proposed": []}


def _fallback() -> dict:
    """THE BOX'S OWN AI IS A VENDOR TOO (#1857 H5). When it does not answer (a timeout, a busy service, a lapsed
    sign-in, its own limit), the question is still answered with today's brief, which is written ahead and needs no
    AI, under one sentence that says so. Never an empty answer. No app is named: pages name no AI vendor."""
    try:
        from core import brief
        from core.connector import words
        u = words.unavailable("The box's own AI")
        today = brief.get()["text"].split("\n\n" + brief.NO_AI)[0]
        return {"unavailable": u, "fallback": f"{u['words']}\n\n{today}"}
    except Exception as e:                                # noqa: BLE001 — the fallback never adds a failure
        log.warning("ask.fallback_failed", error=f"{type(e).__name__}: {str(e)[:120]}")
        return {}


def _no_ai(why: str) -> dict:
    """D5: plain words, and how to get insights."""
    from core import brief
    b = brief.get()
    return {"status": "answered", "answered_by": "plain", "why": why,
            "answer": "I can't think about questions until an AI is signed in on this box. Here is today in plain "
                      "words.\n\n" + b["text"].split("\n\n" + brief.NO_AI)[0],
            "means": "", "ask_next": [], "can_start": [], "proposed": [], "insights": brief.NO_AI}


# ── the tools ───────────────────────────────────────────────────────────────────────────────────────────────
def ask(question: str | None = None, seat=None, *, run_agent=None, think=None, wait_s: float | None = None) -> dict:
    """Ask the box a question. No question is the one every owner asks first, "what's going on today?", and gets
    today's brief, instantly. `run_agent`, `think` and `wait_s` are a test's stand-ins."""
    from core import brain
    from core.connector import tools
    seat = seat or {}
    if seat.get("capabilities") is not None:
        # A RUN SEAT IS THE BOX ASKING ITSELF: a coworker or an ask's own AI. Refused, so one question can never
        # start another, and a shift can't spend the buyer's AI twice over.
        return {"status": "refused", "text": "The box's own AI can't ask the box; it answers directly."}
    q = _clean(question, QUESTION_MAX + 1)
    if not q:
        from core import brief
        b = brief.tool(seat)
        return {"status": "answered", "answered_by": "brief", "question": "", "text": b["text"],
                "ask_next": b.get("ask_next") or [], "can_start": b.get("can_start") or []}
    if len(q) < QUESTION_MIN:
        return {"status": "refused", "text": "Ask the box a question in words, for example: what needs me today?"}
    if len(q) > QUESTION_MAX:
        return {"status": "refused", "text": f"That question is too long; keep it under {QUESTION_MAX} characters."}

    if think is None and run_agent is None:
        ready, why = brain.can_think()
        if not ready or os.environ.get("AIOS_HERMETIC_TEST"):
            return _public({"question": q, **_no_ai(why or "a test never spends")})
    caps = sorted(c for c in tools.held(seat) if tools.ai_may_hold(c))
    if think is not None or (run_agent is None and brain._backend() not in ("claude_code", "api", "codex")):
        try:
            return _public({"question": q, **_think(q, set(caps), think=think)})
        except Exception as e:                            # noqa: BLE001
            log.warning("ask.think_failed", error=f"{type(e).__name__}: {str(e)[:160]}")
            return _public({"question": q, "status": "failed", "why": f"the box AI stopped ({type(e).__name__})",
                            **_fallback()})

    _prune()
    if _busy():
        return {"status": "busy", "text": "I'm answering another question right now. Ask me again in a minute."}
    ask_id = "ask_" + secrets.token_hex(6)
    row = {"ask_id": ask_id, "status": "working", "question": q, "started_at": _iso(),
           "seat": str(seat.get("id") or "")}
    _save(ask_id, row)
    log.info("ask.started", ask_id=ask_id, seat=row["seat"], capabilities=len(caps))

    def work():
        done = _agent(ask_id, q, caps, run_agent=run_agent)
        _save(ask_id, {**row, **done, "finished_at": _iso()})
        log.info("ask.finished", ask_id=ask_id, status=done["status"], took_s=done.get("took_s"))

    # THE QUESTION OUTLIVES THE WAIT. The run goes on in its own thread (a gthread worker heartbeats from its own
    # loop, so a long one is never killed as a stuck request), and the answer lands in the store for core.ask_result.
    th = threading.Thread(target=work, name=f"ask-{ask_id}", daemon=True)
    th.start()
    th.join(WAIT_S if wait_s is None else wait_s)
    return _public(_load(ask_id) or row)


def ask_result(ask_id: str | None = None, seat=None) -> dict:
    """Collect an answer that took longer than one wait. Only the connection that asked can collect it. No id is
    this connection's latest question."""
    seat = seat or {}
    aid = _clean(ask_id, 40)
    if not aid:
        mine = sorted(((row.get("started_at") or "", i) for i, row, _ in _all()
                       if row.get("seat") == str(seat.get("id") or "") and seat.get("id")), reverse=True)
        if not mine:
            return {"status": "not_found", "text": "This connection hasn't asked the box anything in the last day."}
        aid = mine[0][1]
    row = _load(aid) if _ID.match(aid) else None
    # NOT YOURS IS NOT FOUND: another connection's question (and so its answer) is never confirmed to exist.
    if not row or row.get("seat") != str(seat.get("id") or ""):
        return {"status": "not_found", "text": f"There's no question {aid or '(none)'} from this connection. "
                                               "Answers are kept for a day."}
    if row.get("status") == "working" and _age_s(row.get("started_at")) > MAX_MINUTES * 60 + 150:
        row = {**row, "status": "failed", "why": "the box AI did not finish in time"}
    return _public(row)


from core.connector import tools  # noqa: E402

tools.register(
    "ask", title="Ask your box a question",
    fn=ask, wants_seat=True, machine=MACHINE, min_role="read", capability="read:reports",
    render=lambda r: r.get("text") or "",           # already the answer in words (#1841)
    description="Ask the box anything about the business, in plain words. The box's own AI reads its own data "
                "and answers with real numbers, what they mean, what to ask next and what it can start. Takes "
                "up to 45 seconds. Show the 'text' to the owner as it is. If it says it is still working, use "
                "core.ask_result with the ask id a minute later.",
    args={"question": {"type": "string", "required": False,
                       "description": "The owner's question, in their words. Leave it out for today's brief."}})
tools.register(
    "ask_result", title="Collect your box's answer",
    fn=ask_result, wants_seat=True, machine=MACHINE, min_role="read", capability="read:reports",
    render=lambda r: r.get("text") or "",           # already the answer in words (#1841)
    description="The answer to a question core.ask was still working on. Pass its ask id. Show the 'text' to "
                "the owner as it is.",
    args={"ask_id": {"type": "string", "required": False,
                     "description": "The id core.ask gave, ask_…. Leave it out for this connection's latest."}})
