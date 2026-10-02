"""Ask your box (#1839 Phase 1, core/ask.py): the box answers like an AI Business Machine.

Owner, 2026-10-02, after asking his AI for the Morning Review over the connector: "This is not an AI business
machine. This is a dumb box." Measured here, against the box's real report store, seats, approvals and /mcp, with
only the AI's own words stood in for:

  ASK YOUR BOX (core/ask.py)
  * the box's AI answers on a seat holding exactly the asker's capabilities, revoked after
  * the four parts come back in words; what it proposed is read from Approvals, never from its own claim
  * a slow answer returns an ask id inside the wait, and core.ask_result collects it, for that connection only
  * one question at a time; a run seat can't ask; a failure is a sentence; a ChatGPT box answers from the facts
  * no AI signed in: plain words and the D5 line

Run: python tests/test_ask_your_box.py
"""
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
T = pathlib.Path(tempfile.mkdtemp(prefix="askyourbox_"))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "x"
os.environ["DASHBOARD_BASE_URL"] = "https://box.example"

from core import state  # noqa: E402

state.init_db()
from core import approvals, brain, dispatch, report  # noqa: E402
from core import ask as ask_mod  # noqa: E402
from core import brief  # noqa: E402
from core.config import settings  # noqa: E402
from core.connector import seats, tools  # noqa: E402

settings.dashboard_base_url = "https://box.example"
_SHIPPED_WS = ask_mod.WORKSPACE
ask_mod.WORKSPACE = str(T / "ask_workspace")

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


client = dispatch.app.test_client()


def mcp(cred, method, params=None, rpc_id=1):
    body = {"jsonrpc": "2.0", "id": rpc_id, "method": method, **({"params": params} if params is not None else {})}
    r = client.post("/mcp", data=json.dumps(body), headers={"Authorization": f"Bearer {cred}",
                                                            "Content-Type": "application/json",
                                                            "MCP-Protocol-Version": "2025-06-18"})
    return r.status_code, r.get_json(silent=True)


owner_id, owner_cred = seats.mint("Claude", "act")
owner = seats.verify(owner_cred)

# ── the store, as the reporters fill it ─────────────────────────────────────────────────────────────────────
inbox = {"title": "Unified Inbox", "headline": {"label": "waiting on you", "value": 120, "delta": 31},
         "needs_you": [{"text": "120 conversations are waiting on your reply", "href": "/inbox/inbox"},
                       {"text": "22 replies are written and ready to send", "href": "/inbox/waiting"}],
         "happened": [{"text": "messages came in", "value": 43},
                      {"text": "people wrote for the first time", "value": 38}]}
site = {"title": "Website", "headline": {"label": "", "value": 0},
        "happened": [{"text": "brian-site.example: 179 visits from people this week (+231%)"}]}
meters = {"title": "Meters", "headline": {"label": "of $90 this cycle", "value": "$12"},
          "happened": [{"text": "Resend 9 of 3000", "value": "0%"}]}
report.REPORTERS.clear()
report.register_reporter("customer_voice", "Unified Inbox", lambda d: inbox)
report.register_reporter("website", "Website", lambda d: site)
report.register_reporter(report.METERS, "Meters", lambda d: meters)
out = report.refresh()
report.refresh()

print("\ncore.ask over the connector\n")
status, body = mcp(owner_cred, "tools/list", {})
names = {t["name"] for t in body["result"]["tools"]}
ok("an owner's connection lists core.ask and core.ask_result", {"core.ask", "core.ask_result"} <= names, sorted(names))

print("\nAsk your box: no AI signed in (D5)\n")
got = ask_mod.ask("What needs me today?", owner)
ok("it answers in plain words from today's brief", got["status"] == "answered" and got["answered_by"] == "plain"
   and "120 conversations are waiting on your reply" in got["text"], got)
ok("...and says how to get insights", brief.NO_AI in got["text"], got["text"])

print("\nAsk your box: the box's AI answers\n")
runs = []


def fake_agent(prompt, **kw):
    seat = seats.verify(kw["mcp"]["credential"])
    runs.append({"prompt": prompt, "seat": seat, **kw})
    # The run proposes through the box's own tool, on its own seat, as a real run would over /mcp.
    st, body = mcp(kw["mcp"]["credential"], "tools/call", {"name": "core.propose_stop", "arguments": {}})
    runs[-1]["proposed"] = body
    return {"text": "I looked at your inbox. {not json} " + json.dumps({
        "answer": "120 people are waiting on you; 22 replies are ready.",
        "means": "Your replies are the bottleneck today.",
        "ask_next": ["Who has waited longest?"],
        "can_start": [{"tool": "mcp__aios__core_propose_stop", "why": "Pause while you catch up."},
                      {"tool": "core.made_up", "why": "no"}]}),
        "turns": 4, "minutes": 0.2, "cost_usd": 0.0, "api_usd": 0.01, "backend": "claude_code",
        "model": "sonnet", "denied": []}


got = ask_mod.ask("What needs me today?", owner, run_agent=fake_agent, wait_s=10)
run = runs[-1]
ok("the box's AI answered, in the four parts", got["status"] == "answered" and got["answered_by"] == "box_ai"
   and got["answer"].startswith("120 people") and got["means"] and got["ask_next"], got)
ok("...on the box's own MCP, in the sandbox (sandboxed is the default), with a turn and time cap",
   run["mcp"]["url"] == "http://127.0.0.1:8000/mcp" and "sandboxed" not in run and run["max_minutes"] == 3
   and run["max_turns"] == ask_mod.MAX_TURNS and run["task"] == "ask", {k: run[k] for k in ("mcp", "max_minutes")})
ok("...on a seat holding exactly what the asker's holds, never more",
   set(run["seat"]["capabilities"]) == {c for c in tools.held(owner) if tools.ai_may_hold(c)},
   run["seat"]["capabilities"])
ok("...revoked when the answer is in", seats.verify(run["mcp"]["credential"]) is None)
ok("...and hidden from the owner's list of connections", not any(s["label"].startswith("Ask ask_")
                                                                for s in seats.all_seats()))
ok("what it proposed is read from Approvals, by that seat", [p["title"] for p in got["proposed"]]
   == ["Stop everything"], got["proposed"])
ok("the text says what is waiting on Approvals, then ask next, then I can start",
   "Waiting for your OK on Approvals: Stop everything" in got["text"] and "Ask me next: Who has waited longest?"
   in got["text"] and "I can start: Ask before stopping your box" in got["text"], got["text"])
ok("'I can start' never offers a tool the box doesn't have", "made_up" not in json.dumps(got), got["can_start"])
ok("the answer itself is words, never the closing JSON", "{" not in got["answer"], got["answer"])

reader_id, reader_cred = seats.mint("Old read seat", "read")
reader = seats.verify(reader_cred)
got = ask_mod.ask("What needs me today?", reader, run_agent=fake_agent, wait_s=10)
ok("asked by a read-only connection, its AI can't propose, and nothing is offered to start",
   "write:proposals" not in runs[-1]["seat"]["capabilities"] and not got["can_start"]
   and not got["proposed"], (runs[-1]["seat"]["capabilities"], got["can_start"], got["proposed"]))
with state.connect() as c:
    c.execute("DELETE FROM approvals")

print("\nA slow answer, collected later\n")
gate = threading.Event()


def slow_agent(prompt, **kw):
    gate.wait(10)
    return {"text": json.dumps({"answer": "Here it is, later.", "means": "", "ask_next": [], "can_start": []}),
            "turns": 2, "minutes": 1, "cost_usd": 0, "api_usd": 0, "backend": "claude_code", "model": "sonnet",
            "denied": []}


t0 = time.monotonic()
got = ask_mod.ask("Which searches bring people to my site?", owner, run_agent=slow_agent, wait_s=0.2)
ok("it answers inside the wait, saying it is still working, with an ask id", got["status"] == "working"
   and got["ask_id"].startswith("ask_") and "core.ask_result" in got["text"] and time.monotonic() - t0 < 5, got)
busy = ask_mod.ask("And another?", owner, run_agent=fake_agent, wait_s=1)
ok("one question at a time: a second is told so, in words", busy["status"] == "busy" and "another question"
   in busy["text"], busy)
early = ask_mod.ask_result(got["ask_id"], owner)
ok("collected early, it is still working", early["status"] == "working", early)
gate.set()
for _ in range(50):
    if ask_mod._load(got["ask_id"]).get("status") != "working":
        break
    time.sleep(0.05)
later = ask_mod.ask_result(got["ask_id"], owner)
ok("collected after, the answer is there", later["status"] == "answered" and later["text"] == "Here it is, later.",
   later)
other = ask_mod.ask_result(got["ask_id"], reader)
ok("another connection can't collect it, or learn it exists", other["status"] == "not_found", other)
ok("a made-up id is not found, in words", ask_mod.ask_result("ask_000000000000", owner)["status"] == "not_found")
status, body = mcp(owner_cred, "tools/call", {"name": "core.ask_result", "arguments": {"ask_id": got["ask_id"]}})
ok("core.ask_result over the connector returns the answer's words",
   body["result"]["structuredContent"]["text"] == "Here it is, later.", body)

print("\nEvery other ending is a sentence\n")
got = ask_mod.ask("What needs me today?", owner,
                  run_agent=lambda p, **kw: (_ for _ in ()).throw(brain.AgentLimit("stopped: 3-minute limit")),
                  wait_s=10)
ok("an AI that hit its limit says so in words", got["status"] == "failed" and "time limit" in got["text"], got)
ok("...and its seat is revoked all the same", not any(s["label"].startswith("Ask ") for s in
                                                       seats.all_seats(include_revoked=False, include_runs=True)))
got = ask_mod.ask("What needs me today?", owner, run_agent=lambda p, **kw: {"text": "Just words, no JSON."},
                  wait_s=10)
ok("an AI that forgot the JSON still answers, with its own words", got["answer"] == "Just words, no JSON.", got)
run_seat_id, run_cred = seats.mint("coworker x r1", "act", capabilities=["read:reports"])
got = ask_mod.ask("What needs me today?", seats.verify(run_cred), run_agent=fake_agent)
ok("a run seat (a coworker, or an ask's own AI) can't ask the box", got["status"] == "refused", got)
ok("too short a question is refused in words", ask_mod.ask("?", owner)["status"] == "refused")
got = ask_mod.ask(None, owner)
ok("no question at all is 'what's going on today?': today's brief, instantly", got["answered_by"] == "brief"
   and got["text"].startswith("Your business today"), got)
status, body = mcp(owner_cred, "tools/call", {"name": "core.ask_result", "arguments": {}})
ok("core.ask_result with no id is this connection's latest question",
   body["result"]["structuredContent"]["status"] in ("answered", "failed"), body)
ok("too long a question is refused in words", ask_mod.ask("x" * 1001, owner)["status"] == "refused")

print("\nA ChatGPT box answers from today's facts\n")
seen = []


def fake_think(task, prompt, **kw):
    seen.append(json.loads(prompt))
    return json.dumps({"answer": "120 people are waiting on you.", "means": "", "ask_next": [],
                       "can_start": [{"tool": "core.propose_stop", "why": "Catch up on 120 first."}]})


got = ask_mod.ask("What needs me today?", owner, think=fake_think)
ok("it answers from the stored facts, no tools", got["answered_by"] == "box_ai_facts"
   and seen[-1]["question"] == "What needs me today?" and "facts" in seen[-1], got)
ok("...the spend never among them", "$12" not in json.dumps(seen[-1]))
ok("...and still offers what it can start", [c["tool"] for c in got["can_start"]] == ["core.propose_stop"], got)

print("\nThe pieces are where the box loads them\n")
src = (ROOT / "core" / "dispatch.py").read_text()
ok("dispatch imports core.ask with the other core tools (the web process registers them)",
   '"core.ask"' in src)
ok("an ask's workspace is never read as a coworker (the runner skips '_' folders)",
   _SHIPPED_WS.endswith("/my/coworkers/_ask/workspace"), _SHIPPED_WS)

print("\nALL ASK YOUR BOX CHECKS PASS" if not _failed else f"\n{_failed} ASK YOUR BOX CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
