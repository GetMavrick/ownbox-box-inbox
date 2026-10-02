"""Today's brief (#1839 Phase 1, core/brief.py): the box answers like an AI Business Machine.

Owner, 2026-10-02, after asking his AI for the Morning Review over the connector: "This is not an AI business
machine. This is a dumb box." Measured here, against the box's real report store, seats, approvals and /mcp, with
only the AI's own words stood in for:

  TODAY'S BRIEF (core/brief.py)
  * written ahead, from the stored rows: core.brief answers in words, needs-you first, with full links
  * the box's AI ranks it: top three, why, next step, ask next, I can start
  * every number it writes is the box's: an invented one is dropped, a made-up link is dropped, a tool the box
    doesn't have is never offered, and an answer with nothing usable falls back to the plain brief
  * the AI is asked only when the numbers moved, and at most 4 times a day; the spend never appears in it
  * no AI signed in: plain words, and "Sign in your AI on Settings for insights" (D5)

Run: python tests/test_todays_brief.py
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
T = pathlib.Path(tempfile.mkdtemp(prefix="todaysbrief_"))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "x"
os.environ["DASHBOARD_BASE_URL"] = "https://box.example"

from core import state  # noqa: E402

state.init_db()
from core import approvals, brain, dispatch, report  # noqa: E402
from core import brief  # noqa: E402
from core.config import settings  # noqa: E402
from core.connector import seats, tools  # noqa: E402

settings.dashboard_base_url = "https://box.example"

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

print("\nToday's brief, with nothing reported yet\n")
b = brief.get()
ok("an empty box says so in words, not zeros", "Nothing has been reported yet" in b["text"], b["text"])
ok("...and, with no AI signed in, says how to get insights (D5)", brief.NO_AI in b["text"]
   and "https://box.example/settings/ai" in b["text"], b["text"])

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
ok("the 15-minute snapshot writes the brief too", out.get("brief") in ("plain", "ai", "same"), out)

print("\nThe plain brief (no AI)\n")
b = brief.get()
ok("needs-you comes first, each with a full link to the box, never a bare path",
   "120 conversations are waiting on your reply (https://box.example/inbox/inbox)" in b["text"], b["text"])
ok("one line per machine, headline then what happened",
   "Unified Inbox: 120 waiting on you (+31 since yesterday); 43 messages came in; 38 people wrote for the first "
   "time." in b["text"], b["text"])
ok("an empty headline (no label) is not read out as a zero", "0 " not in b["text"].split("Website:")[1][:4], b["text"])
ok("the spend is never in it, whoever reads it", "$12" not in b["text"] and "Meters" not in b["text"], b["text"])
ok("no raw data: no braces, no field names", "{" not in b["text"] and "needs_you" not in b["text"]
   and "headline" not in b["text"], b["text"])
ok("D5: no AI signed in means the line saying how to get insights", brief.NO_AI in b["text"])

print("\nThe thought-through brief (the box's AI)\n")
asked = []


def good_ai(task, prompt, **kw):
    asked.append((task, json.loads(prompt)))
    return "Here you go:\n" + json.dumps({
        "summary": "38 people wrote to you for the first time, and 22 replies are ready.",
        "top": [
            {"title": "22 replies are ready to send", "why": "38 people wrote for the first time; 22 replies wait.",
             "do": "Read them and approve the good ones.", "href": "https://box.example/inbox/waiting"},
            {"title": "Your site is pulling", "why": "179 visits from people this week, up 231%.",
             "do": "Write the next article.", "href": "https://evil.example/x"},
            {"title": "500 new leads arrived", "why": "An invented number.", "do": "Celebrate.", "href": ""},
        ],
        "ask_next": ["Who are the 38 new people?", "Which 7 pages did best?"],
        "can_start": [{"tool": "core.propose_stop", "why": "Pause while you catch up on 120 messages."},
                      {"tool": "core.send_everything", "why": "Not a real tool."}]})


ok("the brief's AI task is configured on the cheap model", brain._model_for("brief") == brain._model_for("review"))
r = brief.refresh(think=good_ai)
b = brief.get()
ok("the box's AI writes it when the numbers are new", r["status"] == "ai" and b["from"] == "ai", (r, b.get("from")))
ok("the AI is given the stored rows and the tools it may offer, nothing else",
   asked and asked[-1][0] == "brief" and set(asked[-1][1]) == {"facts", "startable"}
   and "core.propose_stop" in asked[-1][1]["startable"], asked and asked[-1])
ok("...and never the spend", "$12" not in json.dumps(asked[-1][1]), asked[-1][1])
ok("its top items are numbered, with why, what to do and the box's own link",
   "1. 22 replies are ready to send. 38 people wrote for the first time; 22 replies wait. Next: Read them and "
   "approve the good ones. (https://box.example/inbox/waiting)" in b["text"], b["text"])
ok("a link the box never gave is dropped, never shown", "evil.example" not in b["text"]
   and b["top"][1]["href"] == "", b["top"])
ok("an item with an invented number is dropped", "500" not in b["text"] and len(b["top"]) == 2, b["top"])
ok("'Ask me next' offers only questions whose numbers are the box's", "Ask me next: Who are the 38 new people?"
   in b["text"] and "7 pages" not in b["text"], b["text"])
ok("'I can start' offers a real proposal tool, by its title, and says it waits for an OK",
   "I can start: Ask before stopping your box (each waits for your OK on Approvals)" in b["text"], b["text"])
ok("...and never a tool the box doesn't have", "send_everything" not in json.dumps(b), b["can_start"])
ok("with an AI, no 'sign in' line", brief.NO_AI not in b["text"])

print("\nAsked only when the numbers moved, and at most 4 times a day (OSDev1's rule, 2026-10-02)\n")
n = len(asked)
r = brief.refresh(think=good_ai)
ok("the same numbers: no new question to the AI", r["status"] == "same" and len(asked) == n, (r, len(asked)))
inbox["happened"][0]["value"] = 44
report.snapshot()
r = brief.refresh(think=good_ai)
ok("new numbers within 6 hours: the AI's brief stays, no question", r["status"] == "same" and len(asked) == n,
   (r, len(asked)))
stored = brief._stored()
stored["built_at"] = (datetime.now(timezone.utc) - timedelta(seconds=brief.BRIEF_EVERY_S + 5)).isoformat()
brief._store(stored)
r = brief.refresh(think=good_ai)
ok("new numbers after 6 hours: the AI rewrites it", r["status"] == "ai" and len(asked) == n + 1, (r, len(asked)))
stored = brief._stored()
stored["built_at"] = (datetime.now(timezone.utc) - timedelta(seconds=brief.BRIEF_EVERY_S + 5)).isoformat()
brief._store(stored)
inbox["happened"][0]["value"] = 45
report.snapshot()
r = brief.refresh(think=lambda *a, **k: "not json at all")
ok("an AI answer that can't be used falls back to the plain brief, on time", r["status"] == "plain"
   and brief.get()["from"] == "plain", r)
r = brief.refresh(think=lambda *a, **k: (_ for _ in ()).throw(TimeoutError("slow")))
ok("an AI that fails is a plain brief too, never a crash", r["status"] in ("plain", "same"), r)
asked = []
r = brief.refresh(think=lambda *a, **k: asked.append(1) or "{}")
ok("a failed ask is not retried on the next snapshot: the 6-hour clock runs from the last ask, not the last AI brief",
   not asked, (r, len(asked)))
stored = brief._stored()
stored["ai_tried_at"] = (datetime.now(timezone.utc) - timedelta(seconds=brief.BRIEF_EVERY_S + 5)).isoformat()
brief._store(stored)
r = brief.refresh(think=good_ai)
stored = brief._stored()
stored["built_at"] = (datetime.now(timezone.utc) - timedelta(seconds=brief.BRIEF_EVERY_S + 5)).isoformat()
brief._store(stored)
ok("a quiet afternoon: an AI brief hours old whose numbers still hold is still the one served",
   r["status"] == "ai" and brief.get()["built_at"] == stored["built_at"] and brief.get()["from"] == "ai", r)
inbox["happened"][1]["value"] = 39
report.snapshot()
g = brief.get()
ok("...and once the numbers move, today's ranking stays, said to be from earlier, with the numbers as they are now",
   g["from"] == "ai" and "What your box's AI ranked at" in g["text"] and "Needs you now:" in g["text"]
   and "39 people" in g["text"], g["text"])
stored["built_at"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
brief._store(stored)
ok("...but a ranking from another day is never shown as today's: it is read plain",
   brief.get()["from"] == "plain" and "39 people" in brief.get()["text"], brief.get()["text"])

# THE CADENCE, PINNED: a whole day of 15-minute snapshots, the numbers different every time.
_facts, calls, n = brief.facts, [], [0]


def moving_facts(now=None):
    n[0] += 1
    return {"as_of": "", "needs_you": [{"text": f"{n[0]} conversations are waiting", "href": ""}],
            "machines": [], "yesterday": []}


def counting_ai(task, prompt, **kw):
    calls.append(1)
    k = json.loads(prompt)["facts"]["today"]["needs_you"][0]["text"].split()[0]
    return json.dumps({"top": [{"title": f"{k} conversations wait", "why": "", "do": "", "href": ""}]})


brief.facts = moving_facts
start = datetime(2026, 10, 5, 7, 0, tzinfo=timezone.utc)
brief._store({**brief._stored(), "built_at": (start - timedelta(days=1)).isoformat()})
for i in range(96):
    brief.refresh(start + timedelta(minutes=15 * i), think=counting_ai)
brief.facts = _facts
ok("a day of 96 snapshots, every one with new numbers, asks the box's AI at most 4 times", 1 <= len(calls) <= 4,
   len(calls))

print("\ncore.brief over the connector\n")
inbox["happened"][1]["value"] = 38           # back to the numbers good_ai writes about
report.snapshot()
brief._store({**brief._stored(), "built_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()})
r = brief.refresh(think=good_ai)
status, body = mcp(owner_cred, "tools/list", {})
names = {t["name"] for t in body["result"]["tools"]}
ok("an owner's connection lists core.brief", "core.brief" in names, sorted(names))
status, body = mcp(owner_cred, "tools/call", {"name": "core.brief", "arguments": {}})
res = body["result"]["structuredContent"]
ok("core.brief answers with the AI brief's words, ready to show", res["from"] == "ai"
   and res["text"].startswith("Your business today")
   and "Ask me next" in res["text"], res.get("text"))
ok("...and its parts for an AI that wants them, the spend never among them",
   {"text", "top", "ask_next", "can_start", "needs_you"} <= set(res) and "$12" not in json.dumps(res), sorted(res))

print("\nThe pieces are where the box loads them\n")
src = (ROOT / "core" / "dispatch.py").read_text()
ok("dispatch imports core.brief with the other core tools (the web process registers it)", '"core.brief"' in src)

print("\nALL TODAY'S BRIEF CHECKS PASS" if not _failed else f"\n{_failed} TODAY'S BRIEF CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
