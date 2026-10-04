"""core.ask carries the tasks its own tools offered, and a second question waits its turn (OSDev1, 2026-10-04).

THE DAILY CHECK (release 2026.10.04.1, H2): smart answers 4 of 5, the med spa set 2 of 5, and every miss was mark 4,
"I can start". The box's AI read a tool that offered a task (aeo.searches: "Write an article on your top search") and
answered with none. And a second question asked while one was running was refused: "I'm answering another question
right now."

WHAT IS MEASURED, through the box's real /mcp, seats, registry and store, with only the AI's own reply stood in for:
  * the AI names no task, but a tool it read offered one: the answer offers it
  * the AI names a task itself: that one is kept, not replaced
  * an asker who may not propose is offered nothing, whatever the tools offered
  * nothing read, nothing offered: no task is invented
  * the kept offers are forgotten when the run ends
  * a second question is queued and answered after the first; two runs never overlap; a full queue says busy
  * a question asked earlier goes first; a run that died long ago does not hold the box

Run: python tests/test_ask_carries_offers.py
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
T = pathlib.Path(tempfile.mkdtemp(prefix="askoffers_"))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "x"
os.environ["DASHBOARD_BASE_URL"] = "https://box.example"

from core import state  # noqa: E402

state.init_db()
from core import box_settings, dispatch  # noqa: E402
from core import ask as ask_mod  # noqa: E402
from core.config import settings  # noqa: E402
from core.connector import seats, tools, words  # noqa: E402

settings.dashboard_base_url = "https://box.example"
ask_mod.WORKSPACE = str(T / "ask_workspace")
ask_mod.TURN_POLL_S = 0.05

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -- {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


client = dispatch.app.test_client()


def mcp(cred, method, params=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, **({"params": params} if params is not None else {})}
    r = client.post("/mcp", data=json.dumps(body), headers={"Authorization": f"Bearer {cred}",
                                                            "Content-Type": "application/json",
                                                            "MCP-Protocol-Version": "2025-06-18"})
    return r.status_code, r.get_json(silent=True)


# A READ TOOL WHOSE ANSWER OFFERS A TASK, the way aeo.searches offers "Write an article on your top search".
tools.register(
    "offers_a_task", title="A tool that offers a task", fn=lambda: {"seen": 3}, machine="core", min_role="read",
    capability="read:reports", description="Stands in for a tool whose answer offers a task.", args={},
    render=lambda r: words.answer(f"{r['seen']} people are waiting on you.",
                                  words.can_start(("core.propose_stop", words.approve_line("Pause while you catch up")))))

owner = seats.verify(seats.mint("Claude", "act")[1])
reader = seats.verify(seats.mint("Reader", "read")[1])
calls = {"now": 0, "most": 0}
lock = threading.Lock()


def agent(read=True, said=(), gate=None):
    """The box's AI, stood in for: it reads the offering tool over /mcp on its own seat (or not), then replies
    naming the tasks in `said`."""
    def run(prompt, **kw):
        with lock:
            calls["now"] += 1
            calls["most"] = max(calls["most"], calls["now"])
        try:
            run.seat = seats.verify(kw["mcp"]["credential"])
            if read:
                mcp(kw["mcp"]["credential"], "tools/call", {"name": "core.offers_a_task", "arguments": {}})
            if gate is not None:
                gate.wait(10)
            return {"text": json.dumps({"answer": f"Answered: {prompt[:30]}", "means": "", "ask_next": [],
                                        "can_start": [{"tool": t, "why": "mine"} for t in said]}),
                    "turns": 2, "minutes": 0.1, "cost_usd": 0.0, "api_usd": 0.0, "backend": "claude_code",
                    "model": "sonnet", "denied": []}
        finally:
            with lock:
                calls["now"] -= 1
    return run


def offers_rows():
    with state.connect() as c:
        return c.execute("SELECT COUNT(*) FROM box_settings WHERE machine = ? AND key LIKE ?",
                         (ask_mod.NS, ask_mod.OFFERS + "%")).fetchone()[0]


print("\nThe answer carries the task its own tools offered (H2 mark 4)\n")
a = agent()
got = ask_mod.ask("Which searches bring people to my site?", owner, run_agent=a, wait_s=10)
ok("the AI named no task, but the tool it read offered one: the answer offers it",
   [c["tool"] for c in got["can_start"]] == ["core.propose_stop"] and "I can start:" in got["text"], got)
ok("...kept on the question's own seat, which the tool's words knew it by", str(a.seat["label"]).startswith("Ask ask_"),
   a.seat)
ok("...and forgotten when the run ended", offers_rows() == 0, offers_rows())

got = ask_mod.ask("What needs me today?", owner, run_agent=agent(said=["core.propose_stop"]), wait_s=10)
ok("the AI named a task itself: that one is kept, with its own reason, not replaced",
   [(c["tool"], c["why"]) for c in got["can_start"]] == [("core.propose_stop", "mine")], got["can_start"])

got = ask_mod.ask("What needs me today?", reader, run_agent=agent(), wait_s=10)
ok("an asker who may not propose is offered nothing, whatever the tools offered", got["can_start"] == []
   and offers_rows() == 0, got["can_start"])

got = ask_mod.ask("How many people visited this week?", owner, run_agent=agent(read=False), wait_s=10)
ok("nothing read that offered a task: no task is invented", got["can_start"] == [], got["can_start"])

got = ask_mod.ask("Which searches bring people?", owner, run_agent=agent(said=["core.made_up"]), wait_s=10)
ok("the AI named a tool that doesn't exist: dropped, and the tools' own offer stands in",
   [c["tool"] for c in got["can_start"]] == ["core.propose_stop"], got["can_start"])

print("\nA second question waits its turn, and two runs never overlap\n")
gate = threading.Event()
calls["most"] = 0
first = ask_mod.ask("First question?", owner, run_agent=agent(gate=gate), wait_s=0.3)
second = ask_mod.ask("Second question?", owner, run_agent=agent(), wait_s=0.3)
ok("the first is working, the second is queued with its own ask id and says it is next",
   first["status"] == "working" and second["status"] == "queued" and "next in line" in second["text"], (first, second))
ok("...and ask_result on the queued one says the same, in words",
   ask_mod.ask_result(second["ask_id"], owner)["status"] == "queued")
gate.set()
for _ in range(200):
    if all((ask_mod._load(x["ask_id"]) or {}).get("status") == "answered" for x in (first, second)):
        break
    time.sleep(0.05)
r1, r2 = ask_mod._load(first["ask_id"]), ask_mod._load(second["ask_id"])
ok("both are answered, the second after the first", r1["status"] == r2["status"] == "answered"
   and r1["finished_at"] <= r2["run_at"], (r1.get("finished_at"), r2.get("run_at")))
ok("two runs never ran at once", calls["most"] == 1, calls)

saved = ask_mod.QUEUE_MAX
ask_mod.QUEUE_MAX = 1
gate2 = threading.Event()
a1 = ask_mod.ask("Holding the box?", owner, run_agent=agent(gate=gate2), wait_s=0.3)
a2 = ask_mod.ask("Waiting?", owner, run_agent=agent(), wait_s=0.3)
a3 = ask_mod.ask("One too many?", owner, run_agent=agent(), wait_s=0.3)
ok("a full queue says busy, in words, and starts nothing", a2["status"] == "queued" and a3["status"] == "busy"
   and "few minutes" in a3["text"] and "ask_id" not in a3, a3)
gate2.set()
for _ in range(200):
    if (ask_mod._load(a2["ask_id"]) or {}).get("status") == "answered":
        break
    time.sleep(0.05)
ask_mod.QUEUE_MAX = saved

print("\nWhose turn it is\n")


def put(ask_id, status, started, **more):
    box_settings.put(ask_mod.NS, ask_mod._key(ask_id), {"ask_id": ask_id, "status": status, "started_at": started,
                                                        "question": "q", "seat": "s", **more}, set_by="test")


now = datetime.now(timezone.utc)
iso = lambda t: t.isoformat(timespec="seconds")  # noqa: E731
put("ask_aaaaaaaaaaa1", "queued", iso(now - timedelta(seconds=20)))
put("ask_aaaaaaaaaaa2", "queued", iso(now - timedelta(seconds=10)))
ok("a question asked earlier goes first: the later one can't take the box", ask_mod._claim("ask_aaaaaaaaaaa2") is False)
ok("...and the earlier one can", ask_mod._claim("ask_aaaaaaaaaaa1") is True
   and ask_mod._load("ask_aaaaaaaaaaa1")["status"] == "working")
ok("while it runs, nobody else takes the box", ask_mod._claim("ask_aaaaaaaaaaa2") is False)
put("ask_aaaaaaaaaaa1", "working", iso(now - timedelta(hours=1)), run_at=iso(now - timedelta(hours=1)))
ok("a run that died an hour ago doesn't hold the box", ask_mod._claim("ask_aaaaaaaaaaa2") is True)
ok("a question that is not queued is never started twice", ask_mod._claim("ask_aaaaaaaaaaa2") is False)

# OSDev4's review of #1901: a waiting question whose thread died (a deploy restarted the web process) must not hold
# every later question back.
put("ask_aaaaaaaaaaa2", "answered", iso(now))
put("ask_bbbbbbbbbbb1", "queued", iso(now - timedelta(minutes=2)))            # orphaned: nobody is looking any more
put("ask_bbbbbbbbbbb2", "queued", iso(now))
ok("a waiting question whose thread died (no look for its turn in 30 s) doesn't hold the next one back",
   ask_mod._claim("ask_bbbbbbbbbbb2") is True and ask_mod._waiting() == 0, ask_mod._load("ask_bbbbbbbbbbb1"))
put("ask_bbbbbbbbbbb2", "answered", iso(now))
put("ask_ccccccccccc1", "queued", iso(now - timedelta(minutes=2)), polled_at=iso(now))   # still looking
put("ask_ccccccccccc2", "queued", iso(now))
ok("...while one that is still looking keeps its place in line", ask_mod._claim("ask_ccccccccccc2") is False
   and ask_mod._load("ask_ccccccccccc2").get("polled_at"), ask_mod._load("ask_ccccccccccc2"))

print("\nALL ASK-CARRIES-OFFERS CHECKS PASS" if not _failed else f"\n{_failed} ASK-CARRIES-OFFERS CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
