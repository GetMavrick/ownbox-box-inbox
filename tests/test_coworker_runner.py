"""The clockwork end to end: tick, queue, runner, steps, seat, AI, receipt, report.

docs/SCOPE_SHIFTS.md §3 and §4, with a fake clock, a fake AI and fake step tools, over a real
database, so each promise is shown on the path a box takes:

  · a shift starts on time and reports DONE; every run ends with exactly one report;
  · due shifts run one at a time, earliest latest first; a slot runs at most once;
  · a shift that cannot start by its latest is MISSED, and says why (another shift, an update,
    or the box being off), never run late;
  · a run whose unit died is closed FAILED, never left "running";
  · a failure before any work is retried once; after work began it is FAILED;
  · a step that fails stops the run and names its machine and tool; the AI never runs after a
    failed before-step, and after-steps never run after a failed AI;
  · the AI's seat holds exactly the coworker's grant (web aside) and is revoked after;
  · Run now is a dry run, and every step is told so;
  · a Base box runs nothing and says why.

Run: python tests/test_coworker_runner.py
"""
import contextlib
import json
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = tempfile.mkdtemp() + "/runner.db"
COWORKERS = pathlib.Path(tempfile.mkdtemp())
os.environ["AIOS_MY_COWORKERS"] = str(COWORKERS)
PROVISION = pathlib.Path(tempfile.mkdtemp()) / "provision.json"
os.environ["AIOS_PROVISION_JSON"] = str(PROVISION)

from core import state  # noqa: E402

state.init_db()

from core import box_mail, brain, claim, push  # noqa: E402
from core.connector import seats, tools  # noqa: E402
from core.coworkers import contract, hire, locks, runner, runs  # noqa: E402
from core.coworkers import contract, locks, runner, runs  # noqa: E402
from core.exceptions import RetryableError  # noqa: E402

claim.PROVISION_JSON = str(PROVISION)
PROVISION.write_text(json.dumps({"tier": "pro"}))            # a box built as Pro: coworkers in plan
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


PT = ZoneInfo("America/Los_Angeles")
runner.box_tz = lambda: PT
WS_ROOT = pathlib.Path(tempfile.mkdtemp())
runner.ROOT = WS_ROOT                                        # workspaces land in a temp dir


def at(h, m, d=28):
    return datetime(2026, 9, d, h, m, tzinfo=PT)             # Monday 28 September 2026


def coworker(slug, *, start, latest, may=("read:inbox", "write:proposals", "web:search"),
             before=(), after=(), enabled=True, minutes=30, job="Read the inbox. Draft replies."):
    f = COWORKERS / slug
    f.mkdir(exist_ok=True)
    (f / "coworker.yaml").write_text(json.dumps({
        "coworker": 1, "title": slug.replace("-", " ").title(), "job": "job.md", "from": "my",
        "may": list(may), "shifts": [{"days": "Mon-Fri", "start": start, "latest": latest}],
        "steps": {"before": list(before), "after": list(after)},
        "limits": {"minutes": minutes, "turns": 40}, "enabled": enabled}))
    (f / "job.md").write_text(job)
    cw, why = contract.load(f)
    assert cw is not None, why
    if contract.risks(cw):
        hire.acknowledge(cw, by="owner-1")      # the owner's OK (§4.3); tested on its own below


# ── the box's seams, faked ────────────────────────────────────────────────────────────────────────
NEXT, LAUNCHED, ALIVE, SLEPT = [], [], set(), []
locks.write_next = lambda epoch, next_file=None: NEXT.append(epoch)
UPDATE = [False]
locks.update_running = lambda deploy_lock=None: UPDATE[0]
locks._busy = lambda path: False
HELD = [True]


@contextlib.contextmanager
def fake_shift(shift_lock=None, deploy_lock=None):
    yield HELD[0]


locks.shift = fake_shift
runner._load_machines = lambda: None
REAL_REPORT = runner.report
STOPS = []
runner._stop_agent = lambda cmd: STOPS.append(cmd)
brain.can_think = lambda: (True, "claude_code")


def launch(slot, unit, minutes):
    LAUNCHED.append((slot, unit, minutes))
    ALIVE.add(unit)


def unit_active(unit):
    return unit in ALIVE


def tick(now):
    return runner.tick(now, launch=launch, unit_active=unit_active)


MAIL, PUSHES = [], []
box_mail.is_configured = lambda: True
box_mail.to_box_people = lambda user_id=None: [{"id": user_id, "email": "owner@example.com"}]
MAIL_FAILS = [0]


def fake_send(to, subject, text, html, *, idem_key, sender_name, note):
    if MAIL_FAILS[0]:
        MAIL_FAILS[0] -= 1
        raise RuntimeError("smtp down")
    MAIL.append({"to": to, "subject": subject, "text": text, "idem_key": idem_key, "note": note})
    return "msg_1"


box_mail.send = fake_send
push.subscriptions_for = lambda user_id: [{"endpoint": "https://push.example/1"}]
push.send = lambda sub, **kw: PUSHES.append(kw) or (True, "ok")

# Step tools, registered the way a partner would (contract.STEP_ARGS).
STEP_CALLS = []
STEP_BEHAVIOUR = {}


def make_step(name):
    def fn(**ctx):
        STEP_CALLS.append((name, ctx))
        b = STEP_BEHAVIOUR.get(name)
        if b == "raise":
            raise RuntimeError("vendor 500")
        if b == "bad":
            return {"summary": "", "outputs": []}
        verb = "Would have saved" if ctx["dry_run"] else "Saved"
        return {"summary": f"{verb} 3 rows.", "outputs": [{"type": "row", "machine": "acme",
                                                           "id": f"r_{name}"}]}
    return fn


STEP_SPEC = {a: {"type": t, "required": True} for a, t in contract.STEP_ARGS.items()}
tools._reset_for_tests()
for n in ("fetch", "save"):
    tools.register(n, fn=make_step(f"acme.{n}"), description="d", machine="acme",
                   capability="act:save_rows", min_role="act", args=STEP_SPEC)
tools.register("list_threads", fn=lambda: ["t1"], description="d", machine="inbox_demo",
               capability="read:inbox")

AGENT_CALLS = []
AGENT_BEHAVIOUR = []           # queue of results: dict, or an exception to raise


def fake_agent(prompt, **kw):
    AGENT_CALLS.append({"prompt": prompt, **kw})
    seat = seats.verify(kw["mcp"]["credential"])
    AGENT_CALLS[-1]["seat"] = seat
    b = AGENT_BEHAVIOUR.pop(0) if AGENT_BEHAVIOUR else "work"
    if b == "work":
        tools.call("aios.inbox_demo.list_threads", None, seat)
        return {"text": "Drafted 3 replies.\nAll quiet otherwise.", "turns": 9, "minutes": 2.0,
                "cost_usd": 0.0, "api_usd": 0.31, "backend": "claude_code", "model": "cc:sonnet",
                "denied": []}
    if b == "work_then_busy":
        tools.call("aios.inbox_demo.list_threads", None, seat)
        raise RetryableError("agent run: the AI was busy or unreachable (529)")
    raise b


def reset():
    for x in (NEXT, LAUNCHED, SLEPT, MAIL, PUSHES, STEP_CALLS, AGENT_CALLS, AGENT_BEHAVIOUR,
              STOPS):
        x.clear()
    ALIVE.clear()
    STEP_BEHAVIOUR.clear()
    UPDATE[0] = False
    HELD[0] = True
    with state.connect() as c:
        c.execute("DELETE FROM coworker_runs")
        c.execute("DELETE FROM heartbeats")
    for f in COWORKERS.iterdir():
        for g in f.iterdir():
            g.unlink()
        f.rmdir()


def run(slot):
    return runner.run(slot, run_agent=fake_agent, sleep=SLEPT.append)


def status(slot):
    r = runs.get(slot)
    return r and r["status"]


def receipt(slot):
    return json.loads(runs.get(slot)["receipt"])


SLOT = "front-desk@2026-09-28T07:30"

print("a shift starts on time and reports DONE")
reset()
coworker("front-desk", start="07:30", latest="07:45", before=["acme.fetch"], after=["acme.save"])
d = tick(at(7, 29))
ok("before its start nothing runs, and the next start is written for box updates",
   LAUNCHED == [] and NEXT[-1] == int(at(7, 30).timestamp()), str(d))
d = tick(at(7, 30))
ok("at 07:30 it is queued and started in its own unit",
   d["started"] == SLOT and LAUNCHED[0][0] == SLOT and LAUNCHED[0][1].startswith("aios-shift-run-")
   and LAUNCHED[0][2] == 30 and status(SLOT) == "starting", str(d))
rc = run(SLOT)
ok("the run ends DONE, with the AI's own words as its reason",
   rc and rc["outcome"] == "DONE" and rc["reason"].startswith("Drafted 3 replies."), str(rc))
ok("the receipt checks as a v1 receipt", contract.check_receipt(receipt(SLOT)) == [])
names = [n for n, _ in STEP_CALLS]
ok("before-step, then the AI, then after-step", names == ["acme.fetch", "acme.save"]
   and len(AGENT_CALLS) == 1)
ctx = STEP_CALLS[0][1]
ok("each step gets exactly the run context, not a dry run",
   set(ctx) == set(contract.STEP_ARGS) and ctx["dry_run"] is False and ctx["slot"] == SLOT
   and ctx["coworker"] == "front-desk" and pathlib.Path(ctx["workspace"]).is_dir())
a = AGENT_CALLS[0]
ok("the AI gets the job as its standing instructions and the box's MCP on a run seat",
   a["system"] == "Read the inbox. Draft replies." and a["mcp"]["url"] == runner.MCP_URL
   and a["coworker"] == "front-desk" and a["max_minutes"] == 30 and a["max_turns"] == 40)
ok("the seat holds exactly the grant, web aside; the web goes to the CLI instead",
   a["seat"]["capabilities"] == ("read:inbox", "write:proposals") and a["web"] == ["search"])
ok("the seat is revoked when the run ends", seats.verify(a["mcp"]["credential"]) is None)
r = receipt(SLOT)
ok("the receipt counts the tools the AI used, by machine.tool",
   r["tools"] == [{"tool": "inbox_demo.list_threads", "calls": 1, "failed": 0}], str(r["tools"]))
ok("and carries what the steps made", [o["id"] for o in r["outputs"]] == ["r_acme.fetch",
                                                                          "r_acme.save"])
ok("both steps are in the receipt, ok", [(s["phase"], s["outcome"]) for s in r["steps"]]
   == [("before", "ok"), ("after", "ok")])
ok("a subscription run records no dollar cost", r["usage"]["cost_usd"] is None
   and r["usage"]["turns"] == 9)
ok("exactly one email, keyed by the run id, to the owner",
   len(MAIL) == 1 and MAIL[0]["idem_key"] == f"coworker-run:{r['run_id']}"
   and MAIL[0]["to"] == "owner@example.com" and MAIL[0]["subject"] == "Front Desk: shift done")
ok("and one app notification, a fixed sentence that opens Shifts",
   len(PUSHES) == 1 and PUSHES[0]["body"] == runner.PUSH_BODY
   and PUSHES[0]["navigate"] == "/shifts/")
d = tick(at(7, 31))
ok("the next tick sends nothing twice and starts nothing", len(MAIL) == 1 and len(PUSHES) == 1
   and d["started"] is None and d["reported"] == 0)
ok("and a slot that ran is never queued again", d["queued"] == [] and len(LAUNCHED) == 1)

print("one at a time, earliest latest first, and MISSED with the reason")
reset()
coworker("long", start="09:00", latest="09:40")
coworker("short", start="09:00", latest="09:10")
d = tick(at(9, 0))
ok("two due at once: the one that would miss soonest starts first",
   d["started"] == "short@2026-09-28T09:00" and sorted(d["queued"]) ==
   ["long@2026-09-28T09:00", "short@2026-09-28T09:00"], str(d))
d = tick(at(9, 1))
ok("the other waits, and its row says for whom",
   d["started"] is None and runs.get("long@2026-09-28T09:00")["note"] ==
   "It waited for short's shift to finish.", str(runs.get("long@2026-09-28T09:00")))
run("short@2026-09-28T09:00")
d = tick(at(9, 2))
ok("when it is done, the next one starts", d["started"] == "long@2026-09-28T09:00")
reset()
coworker("first", start="09:00", latest="09:30")
coworker("second", start="09:00", latest="09:10")
tick(at(9, 0))                                  # second starts (earliest latest)
tick(at(9, 5))                                  # first waits behind it
ALIVE.clear()
ALIVE.add(runs.get("second@2026-09-28T09:00")["unit"])
d = tick(at(9, 11))
ok("a queued shift whose window closes while it waits is MISSED, not run late",
   status("first@2026-09-28T09:00") == "queued")
d = tick(at(9, 31))
r = receipt("first@2026-09-28T09:00")
ok("MISSED, with who it waited for, in the owner's words",
   r["outcome"] == "MISSED" and "between 09:00 and 09:30" in r["reason"]
   and "waited for second's shift to finish" in r["reason"] and r["started_at"] is None
   and r["attempts"] == 0, r["reason"])
ok("and reported like any other run", any(m["subject"] == "First: shift missed" for m in MAIL))

print("the box was off, or updating")
reset()
coworker("front-desk", start="07:30", latest="07:45")
tick(at(6, 0))
d = tick(at(18, 0))
r = receipt(SLOT)
ok("a shift slept through is MISSED once, saying when the box was away",
   d["missed"] == [SLOT] and "the box was off, or not running shifts, from 06:00 to 18:00"
   in r["reason"], r["reason"])
reset()
coworker("front-desk", start="07:30", latest="07:45")
UPDATE[0] = True
d = tick(at(7, 30))
ok("during a box update nothing starts, and the row says so",
   d["started"] is None and runs.get(SLOT)["note"] == "It waited for a box update to finish.")
d = tick(at(7, 46))
ok("still updating at its latest: MISSED, with the update as the reason",
   "waited for a box update to finish" in receipt(SLOT)["reason"])
reset()
coworker("front-desk", start="07:30", latest="07:45")
tick(at(7, 30))
HELD[0] = False                                 # an update started before the unit came up
ok("a runner that finds an update in progress hands the slot back",
   run(SLOT) is None and status(SLOT) == "queued"
   and runs.get(SLOT)["note"] == "It waited for a box update to finish.")
HELD[0] = True
d = tick(at(7, 31))
ok("and the next tick starts it again, inside its window", d["started"] == SLOT)

print("a run that died is closed, never left running")
reset()
coworker("front-desk", start="07:30", latest="07:45")
tick(at(7, 30))
ALIVE.clear()                                   # the unit is gone, and nothing reported
with state.connect() as c:
    c.execute("UPDATE coworker_runs SET claimed_at = ?", ((at(7, 30) - timedelta(minutes=5))
                                                          .astimezone(timezone.utc).isoformat(),))
d = tick(at(7, 35))
r = receipt(SLOT)
ok("closed FAILED, saying it stopped without reporting",
   d["closed"] == [SLOT] and r["outcome"] == "FAILED" and "stopped without reporting" in r["reason"]
   and contract.check_receipt(r) == [], str(r))
ok("and reported", MAIL and MAIL[-1]["subject"] == "Front Desk: shift failed")
reset()
coworker("front-desk", start="07:30", latest="07:45")
tick(at(7, 30))
ALIVE.clear()
d = tick(at(7, 31))
ok("a unit still coming up is not a crash", d["closed"] == [] and status(SLOT) == "starting")
reset()
coworker("slow", start="07:30", latest="08:30")
coworker("quick", start="07:30", latest="07:45")
tick(at(7, 30))                                 # quick starts; slow waits
run("quick@2026-09-28T07:30")
d = tick(at(7, 50))                             # slow, queued 20 minutes ago, starts now
ALIVE.clear()
d = tick(at(7, 51))
ok("a shift that waited 20 minutes in the queue is not a crash the minute after it starts",
   d["closed"] == [] and status("slow@2026-09-28T07:30") == "starting", str(d))

print("one retry, only before any work")
reset()
coworker("front-desk", start="07:30", latest="07:45")
tick(at(7, 30))
AGENT_BEHAVIOUR.append(RetryableError("agent run: the AI was busy or unreachable (529)"))
now_real = runner.datetime


class _Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return at(7, 31).astimezone(tz) if tz else at(7, 31)


runner.datetime = _Clock
rc = run(SLOT)
ok("a failure before any work is retried once, a minute later, and the run is DONE",
   rc["outcome"] == "DONE" and rc["attempts"] == 2 and SLEPT == [runner.RETRY_AFTER_S]
   and len(AGENT_CALLS) == 2, str(rc))
reset()
coworker("front-desk", start="07:30", latest="07:45")
tick(at(7, 30))
AGENT_BEHAVIOUR.append("work_then_busy")
rc = run(SLOT)
ok("after work began it is FAILED, never retried (the work could happen twice)",
   rc["outcome"] == "FAILED" and rc["attempts"] == 1 and SLEPT == [] and len(AGENT_CALLS) == 1
   and "busy or could not be reached" in rc["reason"], str(rc))
runner.datetime = now_real

print("steps: named when they fail, and nothing runs after")
reset()
coworker("front-desk", start="07:30", latest="07:45", before=["acme.fetch"], after=["acme.save"])
STEP_BEHAVIOUR["acme.fetch"] = "raise"
tick(at(7, 30))
rc = run(SLOT)
ok("a before-step that raises: FAILED, naming its machine and tool, and the AI never runs",
   rc["outcome"] == "FAILED" and rc["failed"] == {"machine": "acme", "tool": "acme.fetch"}
   and AGENT_CALLS == [] and "The AI did not run" in rc["reason"], str(rc))
ok("and no seat was ever minted for it", not any(s["label"].startswith("coworker front-desk")
                                                  and not s["revoked_at"]
                                                  for s in seats.all_seats(include_runs=True)))
reset()
coworker("front-desk", start="07:30", latest="07:45", before=["acme.fetch"], after=["acme.save"])
STEP_BEHAVIOUR["acme.save"] = "bad"
tick(at(7, 30))
rc = run(SLOT)
ok("an after-step that returns something a step may not: FAILED, named",
   rc["outcome"] == "FAILED" and rc["failed"]["tool"] == "acme.save"
   and "returned something a step may not" in rc["reason"], str(rc))
reset()
coworker("front-desk", start="07:30", latest="07:45", before=["acme.fetch"], after=["acme.save"])
tick(at(7, 30))
AGENT_BEHAVIOUR.append(brain.AgentLimit("stopped: it ran past its 30-minute limit"))
rc = run(SLOT)
ok("an AI that hit its limit: FAILED, and the after-step never runs",
   rc["outcome"] == "FAILED" and "30-minute limit" in rc["reason"]
   and [n for n, _ in STEP_CALLS] == ["acme.fetch"], str(rc))
reset()
coworker("front-desk", start="07:30", latest="07:45", before=["gone.fetch"])
tick(at(7, 30))
rc = run(SLOT)
ok("a step whose machine is not on the box: FAILED before anything runs, naming it",
   rc["outcome"] == "FAILED" and rc["failed"] == {"machine": "gone", "tool": "gone.fetch"}
   and "gone machine" in rc["reason"] and AGENT_CALLS == [] and STEP_CALLS == [], str(rc))

print("reading the box's data and the web together waits for the owner's OK (§4.3)")
reset()
coworker("front-desk", start="07:30", latest="07:45")
from core import box_settings  # noqa: E402
box_settings.clear("coworkers", "risk_ok:my:front-desk")
tick(at(7, 30))
rc = run(SLOT)
ok("without the OK it does not run, and says where to give it",
   rc["outcome"] == "FAILED" and "needs your OK before it runs" in rc["reason"]
   and "scripts/coworker_ok.py front-desk" in rc["reason"]
   and AGENT_CALLS == [], str(rc))
reset()
coworker("front-desk", start="07:30", latest="07:45")          # OK given for this grant
f = COWORKERS / "front-desk" / "coworker.yaml"                # then the file is widened
d = json.loads(f.read_text())
d["may"] = ["read:inbox", "read:spend", "write:proposals", "web:search"]
f.write_text(json.dumps(d))
tick(at(7, 30))
rc = run(SLOT)
ok("an OK given for one grant does not cover a wider one", rc["outcome"] == "FAILED"
   and "needs your OK" in rc["reason"] and AGENT_CALLS == [], str(rc))
reset()
coworker("front-desk", start="07:30", latest="07:45", may=("read:inbox", "web:search"))
box_settings.put("coworkers", "risk_ok:my:front-desk", ["read:inbox", "web:search",
                                                     "write:proposals"])
tick(at(7, 30))
rc = run(SLOT)
ok("and an OK for a different grant is not this one's", rc["outcome"] == "FAILED"
   and "needs your OK" in rc["reason"], str(rc))
reset()
coworker("front-desk", start="07:30", latest="07:45", may=("read:inbox", "write:proposals"))
box_settings.clear("coworkers", "risk_ok:my:front-desk")
tick(at(7, 30))
ok("a coworker that reads but never reaches the web needs no OK", run(SLOT)["outcome"] == "DONE")

print("Run now is a dry run")
reset()
coworker("front-desk", start="07:30", latest="07:45", before=["acme.fetch"], after=["acme.save"],
         enabled=False)
slot = runner.start_now("front-desk", now=at(12, 0))
d = tick(at(12, 1))
ok("it queues like a shift and starts on the next tick, even for a coworker switched off",
   slot.endswith("+now") and d["started"] == slot)
rc = run(slot)
ok("every step is told it is a dry run, and says what it would have done",
   all(c["dry_run"] is True for _, c in STEP_CALLS)
   and rc["steps"][0]["reason"].startswith("Would have saved"), str(rc["steps"]))
ok("the AI is told it is a trial", "trial run" in AGENT_CALLS[0]["prompt"])
ok("and the report says so", "trial run started by hand" in MAIL[-1]["text"])

print("the report goes out once, and a failed email is retried")
reset()
coworker("front-desk", start="07:30", latest="07:45")
tick(at(7, 30))
MAIL_FAILS[0] = 1
run(SLOT)
ok("the email failed, the notification went", MAIL == [] and len(PUSHES) == 1
   and runs.get(SLOT)["emailed_at"] is None)
d = tick(at(7, 31))
ok("the next tick sends the email, once, and not the notification again",
   len(MAIL) == 1 and len(PUSHES) == 1 and d["reported"] == 1)

print("launch failures, at most once, and the gate")
reset()
coworker("front-desk", start="07:30", latest="07:45")


def bad_launch(slot, unit, minutes):
    raise OSError("systemd-run: not found")


d = runner.tick(at(7, 30), launch=bad_launch, unit_active=unit_active)
ok("a run the box could not start is FAILED, saying so", status(SLOT) == "FAILED"
   and "could not start the run (OSError)" in receipt(SLOT)["reason"])
reset()
coworker("front-desk", start="07:30", latest="07:45")
tick(at(7, 30))
tick(at(7, 30))
ok("two ticks in the same minute start it once", len(LAUNCHED) == 1)
ok("a slot the tick did not claim is refused by the runner", run("nobody@2026-09-28T07:30") is None)
reset()
coworker("front-desk", start="07:30", latest="07:45")
from core import tiers  # noqa: E402
tiers.set_plan({"seq": 1, "tier": "ownbox"})                 # Ownbox says: Base
d = tick(at(7, 30))
ok("a box whose plan has no coworkers runs nothing, says why, and holds back no update",
   d.get("off") == runner.NOT_IN_PLAN and LAUNCHED == [] and NEXT == [None]
   and runs.get(SLOT) is None, str(d))
try:
    runner.start_now("front-desk", now=at(7, 31))
    ok("...and Run now is refused there too", False)
except ValueError as e:
    ok("...and Run now is refused there too, with the same sentence", str(e) == runner.NOT_IN_PLAN)
tiers.set_plan({"seq": 2, "tier": "ownbox", "add": ["coworkers"]})   # an add-on is enough
d = tick(at(7, 30))
ok("the gate is the feature, not the tier: Base with Coworkers added runs it",
   d["started"] == SLOT, str(d))
reset()
coworker("front-desk", start="07:30", latest="07:45")
tiers.set_plan({"seq": 3, "tier": "pro"})
d = tick(at(7, 30))
ok("a Pro box runs it", d["started"] == SLOT)
src = (ROOT / "core" / "coworkers" / "runner.py").read_text()
ok("the runner reads no tier of its own: the one gate is tiers.allows('coworkers')",
   'tiers.allows("coworkers")' in src and "provision.json" not in src.split("def allowed")[1][:600]
   and "def tier(" not in src)
ok("the tick writes the heartbeat /health reads",
   any(h["component"] == "shift_tick" and h["status"] == "ok" for h in state.get_heartbeats()))
h = runner.health(now=at(7, 31))
ok("/health says the tick is ok, and how long ago, naming no coworker",
   h == {"tick": "ok", "age_s": 60}, str(h))
ok("three minutes without a tick is stale", runner.health(now=at(7, 34))["tick"] == "stale")
with state.connect() as c:
    c.execute("DELETE FROM heartbeats")
ok("a box where no tick has run adds nothing to /health", runner.health() is None)
from core import dispatch  # noqa: E402
body = dispatch.app.test_client().get("/health").get_json()
ok("/health still answers, without a shifts field on such a box",
   body.get("ok") is True and "shifts" not in body, str(body)[:200])
state.heartbeat("shift_tick", "ok")
body = dispatch.app.test_client().get("/health").get_json()
ok("and with it once the tick runs", body.get("shifts", {}).get("tick") == "ok", str(body)[:200])

print("review fixes: nothing one row does stops the clock")
from core.exceptions import VendorError  # noqa: E402
reset()
coworker("front-desk", start="07:30", latest="07:45")
for bad, label in (("Front Desk", "a name that is not a coworker's"), ("ghost", "a coworker that does not exist")):
    try:
        runner.start_now(bad, now=at(7, 0))
        ok(f"Run now refuses {label}", False)
    except ValueError as e:
        ok(f"Run now refuses {label}, saying so", "no coworker called" in str(e), str(e))
runs.queue("Front Desk@2026-09-28T07:00+now", coworker="Front Desk", start=at(7, 0).isoformat(),
           latest=at(7, 10).isoformat(), dry_run=True, now=at(7, 0).isoformat())
UPDATE[0] = True
tick(at(7, 5))                                  # it waits: an update is running
UPDATE[0] = False
d = tick(at(7, 16))
ok("a queued row naming no valid coworker is closed, never retried forever",
   status("Front Desk@2026-09-28T07:00+now") in ("MISSED", "FAILED") and "error" not in d, str(d))
d = tick(at(7, 30))
ok("and every shift after it still runs", d["started"] == SLOT and "error" not in d, str(d))

reset()
coworker("front-desk", start="07:30", latest="07:40")
tick(at(7, 20))
real_discover = runner.discover
runner.discover = lambda: (_ for _ in ()).throw(RuntimeError("database is locked"))
for m in (25, 30, 35, 40, 45):
    tick(at(7, m))
during = runner.health(now=at(7, 45))["tick"]
runner.discover = real_discover
d = tick(at(7, 50))
ok("a window that closed during failed ticks is reported MISSED, not lost",
   d["missed"] == [SLOT] and status(SLOT) == "MISSED" and len(MAIL) == 1, str(d))
ok("and /health said error while it was failing, then ok",
   during == "error" and runner.health(now=at(7, 50))["tick"] == "ok", during)

reset()
coworker("alpha", start="07:00", latest="08:00")
UPDATE[0] = True
tick(at(7, 0))                                  # alpha is queued, waiting for the update
UPDATE[0] = False
for g in (COWORKERS / "alpha").iterdir():
    g.unlink()
(COWORKERS / "alpha").rmdir()
coworker("bravo", start="07:30", latest="08:00")
d = tick(at(7, 30))
ok("a queued shift whose file is gone never blocks the one behind it",
   d["started"] == "bravo@2026-09-28T07:30", str(d))
ok("and it is closed MISSED at once, saying why, never run on a schedule it no longer has",
   status("alpha@2026-09-28T07:00") == "MISSED"
   and "can no longer be used" in receipt("alpha@2026-09-28T07:00")["reason"])

print("review fixes: the retry, the runner's time, and a runner that died")
reset()
coworker("front-desk", start="07:30", latest="07:45")
tick(at(7, 30))
import time as _time  # noqa: E402
_real_mono = _time.monotonic
CLOCK = [5000.0]
_time.monotonic = lambda: CLOCK[0]


def slow_busy(prompt, **kw):
    AGENT_CALLS.append(kw)
    CLOCK[0] += 20 * 60                         # twenty minutes of web reading, no seat call
    raise RetryableError("agent run: the AI was busy or unreachable (529)")


runner.datetime = _Clock
try:
    rc = runner.run(SLOT, run_agent=slow_busy, sleep=SLEPT.append)
finally:
    runner.datetime = now_real
ok("a long attempt that failed is FAILED, never retried: it may have worked without the seat",
   rc["outcome"] == "FAILED" and rc["attempts"] == 1 and len(AGENT_CALLS) == 1 and SLEPT == [],
   str(rc))
reset()
coworker("front-desk", start="07:30", latest="07:45")
tick(at(7, 30))
BEHAV = ["busy", "work"]


def quick_busy(prompt, **kw):
    b = BEHAV.pop(0)
    CLOCK[0] += 90
    if b == "busy":
        AGENT_CALLS.append(kw)
        raise RetryableError("agent run: the AI was busy or unreachable (529)")
    return fake_agent(prompt, **kw)


runner.datetime = _Clock
try:
    rc = runner.run(SLOT, run_agent=quick_busy, sleep=SLEPT.append)
finally:
    runner.datetime = now_real
    _time.monotonic = _real_mono
ok("a quick failure is retried, with what is left of its minutes, not a fresh set",
   rc["outcome"] == "DONE" and rc["attempts"] == 2 and AGENT_CALLS[0]["max_minutes"] == 30
   and AGENT_CALLS[1]["max_minutes"] == 29, str([c.get("max_minutes") for c in AGENT_CALLS]))
ok("the runner's unit outlives a retried run: AI minutes, steps, the pause and the retry window",
   runner._runner_seconds(30) >= (30 + runner.STEPS_ALLOWANCE_MIN) * 60 + runner.RETRY_AFTER_S
   + runner.RETRY_WITHIN_S)

reset()
coworker("front-desk", start="07:30", latest="07:45")
tick(at(7, 30))
row = runs.get(SLOT)
seat_id, _ = seats.mint(f"coworker front-desk {row['run_id']}", "act",
                        capabilities=["read:inbox"])
ALIVE.clear()
with state.connect() as c:
    c.execute("UPDATE coworker_runs SET claimed_at = ?", ((at(7, 30) - timedelta(minutes=5))
                                                          .astimezone(timezone.utc).isoformat(),))
tick(at(7, 35))
live = [x for x in seats.all_seats(include_revoked=False, include_runs=True) if x["id"] == seat_id]
ok("a runner that died has its seat revoked", status(SLOT) == "FAILED" and live == [], str(live))
ok("and its AI stopped", STOPS == [["systemctl", "stop", "aios-coworker-front-desk-"
                                    + row["run_id"].replace("_", "-")]], str(STOPS))

print("review fixes: reports")
reset()
coworker("front-desk", start="07:30", latest="07:45")
tick(at(7, 30))
AGENT_BEHAVIOUR.append("work")
runner.report = (lambda real: (lambda row: 0))(runner.report)   # hold the runner's own report
rc = run(SLOT)
runner.report = REAL_REPORT
stale = runs.get(SLOT)
runner.report(stale)
runner.report(stale)                            # the tick and the runner, with the same snapshot
ok("two reporters at once send one app notification", len(PUSHES) == 1, str(len(PUSHES)))
ok("and one email, by the run's key", len({m["idem_key"] for m in MAIL}) == 1)

reset()
coworker("front-desk", start="07:30", latest="07:45")
tick(at(7, 30))
REFUSE = [True]


def refusing_send(to, subject, text, html, *, idem_key, sender_name, note):
    if REFUSE[0]:
        raise VendorError("smtp", "refused", "550 mailbox unavailable")
    MAIL.append({"idem_key": idem_key})
    return "msg_1"


box_mail.send = refusing_send
AGENT_BEHAVIOUR.append("work")
run(SLOT)
REFUSE[0] = False                                # a retry under the same key would "succeed"
tick(at(7, 50))
box_mail.send = fake_send
e = runs.get(SLOT)["emailed_at"]
ok("an email the server refused is recorded as not sent, never as sent on a later retry",
   e.startswith("not sent") and MAIL == [], str(e))

print("OSDev1's review of #1614")
# MAJOR 2: the retry must START before latest, not just be asked for before it.
reset()
coworker("front-desk", start="07:30", latest="07:35")
tick(at(7, 30))
AGENT_BEHAVIOUR.append(RetryableError("agent run: the AI was busy or unreachable (529)"))


class _Late(datetime):
    @classmethod
    def now(cls, tz=None):
        t = at(7, 34) + timedelta(seconds=40)
        return t.astimezone(tz) if tz else t


runner.datetime = _Late
try:
    rc = run(SLOT)
finally:
    runner.datetime = now_real
ok("a retry that would start after latest is not made: FAILED, saying why",
   rc["outcome"] == "FAILED" and SLEPT == [] and len(AGENT_CALLS) == 1
   and "window closes before a retry" in rc["reason"], str(rc))

# MAJOR 3: a waiting shift does not run after it was switched off or moved.
reset()
coworker("front-desk", start="07:30", latest="07:45")
UPDATE[0] = True
tick(at(7, 30))                                  # queued, waiting for an update
UPDATE[0] = False
coworker("front-desk", start="07:30", latest="07:45", enabled=False)
d = tick(at(7, 31))
ok("switched off while it waited: it is not started, and is MISSED saying why",
   d["started"] is None and LAUNCHED == [] and status(SLOT) == "MISSED"
   and "switched off" in receipt(SLOT)["reason"], str(d))
reset()
coworker("front-desk", start="07:30", latest="07:45")
UPDATE[0] = True
tick(at(7, 30))
UPDATE[0] = False
coworker("front-desk", start="09:00", latest="09:15")
d = tick(at(7, 31))
ok("moved to 09:00 while it waited: the 07:30 does not run", d["started"] is None
   and status(SLOT) == "MISSED", str(d))
d = tick(at(9, 0))
ok("and the 09:00 runs, once", d["started"] == "front-desk@2026-09-28T09:00" and len(LAUNCHED) == 1)
reset()
coworker("front-desk", start="07:30", latest="07:45", enabled=False)
slot = runner.start_now("front-desk", now=at(7, 30))
d = tick(at(7, 30))
ok("Run now still works on a switched-off coworker: it is the owner asking by hand",
   d["started"] == slot, str(d))

# MAJOR 4: the heartbeat is written before the reports, and the reports have a budget.
reset()
coworker("front-desk", start="07:30", latest="07:45")
tick(at(7, 30))
AGENT_BEHAVIOUR.append("work")
runner.report = lambda row: 0                     # hold every report back
run(SLOT)
beats = []
SLOW = [0]


def slow_report(row):
    beats.append({h["component"]: h["ts"] for h in state.get_heartbeats()}.get("shift_tick"))
    CLOCK2[0] += 15
    return 1


import time as _t2  # noqa: E402
CLOCK2 = [100.0]
_mono = _t2.monotonic
_t2.monotonic = lambda: CLOCK2[0]
runs.queue("other@2026-09-28T07:30", coworker="other", start=at(7, 30).isoformat(),
           latest=at(7, 45).isoformat(), now=at(7, 30).isoformat())
for extra in ("a@2026-09-28T07:30", "b@2026-09-28T07:30", "c@2026-09-28T07:30"):
    runs.queue(extra, coworker="front-desk", start=at(7, 30).isoformat(),
               latest=at(7, 31).isoformat(), now=at(7, 30).isoformat())
runner.report = slow_report
try:
    d = tick(at(7, 50))
finally:
    runner.report = REAL_REPORT
    _t2.monotonic = _mono
ok("the heartbeat is written before any report is sent",
   beats and beats[0] == at(7, 50).isoformat(timespec="seconds"), str(beats))
ok("reports stop at the budget and wait for the next tick",
   d.get("reports_deferred") and 1 <= len(beats) < 4, f"{len(beats)} {d}")
ok("stopping a dead run's AI never outlasts the tick",
   runner.STOP_TIMEOUT_S <= 20 and "timeout=STOP_TIMEOUT_S" in src)

# MINOR 5: a job file that cannot be read leaves no seat behind.
reset()
coworker("front-desk", start="07:30", latest="07:45")
tick(at(7, 30))
from core.coworkers import contract as _contract  # noqa: E402
real_load = _contract.load
loaded = real_load(COWORKERS / "front-desk")                 # valid when it was checked...
(COWORKERS / "front-desk" / "job.md").write_bytes(b"\xff\xfe not text")   # ...unreadable at run
_contract.load = lambda folder: loaded
before = {x["id"] for x in seats.all_seats(include_revoked=True, include_runs=True)}
try:
    rc = run(SLOT)
finally:
    _contract.load = real_load
after = {x["id"] for x in seats.all_seats(include_revoked=True, include_runs=True)}
ok("a job file that cannot be read: FAILED, and no seat was ever minted",
   rc["outcome"] == "FAILED" and "job file cannot be read" in rc["reason"] and after == before,
   str(rc))
ok("the seat is minted only after the job is read",
   src.index("job = (folder / cw.job).read_text") < src.index("seats.mint("))

# MINOR 6: the next-window time is never in the past while a shift waits.
reset()
coworker("front-desk", start="07:30", latest="07:45")
UPDATE[0] = True
tick(at(7, 32))                                  # due since 07:30, waiting
UPDATE[0] = False
ok("while a shift is due, the time updates wait for is at least a minute ahead",
   NEXT and NEXT[-1] >= int(at(7, 32).timestamp()) + 60, f"{NEXT[-1:]} vs {int(at(7, 32).timestamp())}")

# MINOR 7: a name that is not a slug is never queued, and the slot uses the checked slug.
reset()
coworker("front-desk", start="07:30", latest="07:45")
for bad in ("../x", "front-desk/../front-desk", "FRONT-DESK"):
    try:
        runner.start_now(bad, now=at(7, 0))
        ok(f"Run now refuses {bad!r}", False)
    except ValueError:
        ok(f"Run now refuses {bad!r}", runs.by_status("queued") == [])
slot = runner.start_now("front-desk", now=at(7, 0))
ok("the queued Run now row carries the coworker's own slug",
   runs.get(slot)["coworker"] == "front-desk" and slot.startswith("front-desk@"))

print()
if _failed:
    print(f"{_failed} FAILED")
    sys.exit(1)
print("ALL OK")
