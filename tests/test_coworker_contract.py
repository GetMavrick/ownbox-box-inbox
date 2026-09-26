"""The coworker contract, version 1 (docs/SCOPE_SHIFTS.md §11: the first PR is the contract alone).

Three things are public from here on, and each is pinned below so a change to one is a failing
test rather than a surprise on a partner's box:

  · the FILE, `coworker.yaml` at `coworker: 1`: what it accepts, and the plain reason for every
    file it refuses;
  · the RECEIPT every run ends with: DONE, FAILED or MISSED, never silence;
  · the RUN SEAT: a seat holding exactly the capabilities a coworker was granted, never a role's
    worth and never an `act:` one, checked through the same `tools.call` every seat goes through.

Run: python tests/test_coworker_contract.py
"""
import json
import os
import pathlib
import sys
import tempfile
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = tempfile.mkdtemp() + "/coworkers.db"

from core import state  # noqa: E402

state.init_db()

import yaml  # noqa: E402

from core import report, report_tools  # noqa: E402
from core.connector import seats, tools  # noqa: E402
from core.coworkers import contract as C  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


# The scope's own example (§2), as a partner would write it.
EXAMPLE = """\
coworker: 1
title: Front desk
job: job.md
from: my
may:
  - read:inbox
  - write:proposals
  - web:search
shifts:
  - {days: Mon-Fri, start: "07:30", latest: "07:45"}
  - {days: Mon-Fri, start: "17:00", latest: "17:15"}
steps:
  before: []
  after: []
limits: {minutes: 30, turns: 40}
enabled: true
"""


def base(**over):
    d = yaml.safe_load(EXAMPLE)
    d.update(over)
    return d


def refused(data, slug="front-desk"):
    cw, why = C.parse(data, slug)
    return cw is None, why


def says(why, text):
    return any(text in w for w in why)


print("the file: the scope's example is a coworker")
cw, why = C.parse(yaml.safe_load(EXAMPLE), "front-desk")
ok("the §2 example parses with no reasons", cw is not None and why == [], str(why))
ok("its capabilities are kept, sorted", cw.may == ("read:inbox", "web:search", "write:proposals"),
   str(cw.may))
ok("Mon-Fri is Monday to Friday", cw.shifts[0].days == frozenset({0, 1, 2, 3, 4}))
ok("both shifts are kept, in order",
   [(s.start, s.latest) for s in cw.shifts] == [("07:30", "07:45"), ("17:00", "17:15")])
ok("a Saturday is not a working day", not cw.shifts[0].works_on(date(2026, 9, 26)))
ok("a Monday is", cw.shifts[0].works_on(date(2026, 9, 28)))
ok("limits and enabled are read", (cw.minutes, cw.turns, cw.enabled) == (30, 40, True))
gone, why = refused(yaml.safe_load(EXAMPLE.replace("enabled: true", "on: true")))
ok("on: (which YAML reads as the key True) is refused, and the reason names enabled:",
   gone and says(why, "use enabled: true") and not says(why, "True"), str(why))

print("the version is checked first, and alone")
for v, want in ((None, "must start with the contract version"), (2, "this box reads coworker: 1"),
                ("1", "must start with the contract version"),
                (True, "must start with the contract version")):
    d = base(coworker=v, title="")               # a second fault the version must hide
    gone, why = refused(d)
    ok(f"coworker: {v!r} is refused by its version", gone and says(why, want) and len(why) == 1,
       str(why))
d = base()
del d["coworker"]
ok("a file with no version is refused", refused(d)[0])

print("strict keys, every reason at once")
gone, why = refused(base(shift=[]))
ok("a key v1 does not define is refused, by name (a typo is not ignored)",
   gone and says(why, "does not define: shift"), str(why))
gone, why = refused(base(title="", job="job.txt"))
ok("two faults give two reasons", gone and says(why, "title") and says(why, "job must be"), str(why))
d = base()
del d["may"]
ok("may is required (a coworker with no grant list says so)", says(refused(d)[1], "missing may"))
gone, why = refused(base(), slug="Front Desk")
ok("a folder name that is not a slug is refused", gone and says(why, "folder name"), str(why))
ok("from must be my or a machine", says(refused(base(**{"from": "Acme Co"}))[1], "from must be"))
ok("a machine may ship it", C.parse(base(**{"from": "acme-social"}), "poster")[0] is not None)
ok("enabled must be a boolean", says(refused(base(enabled="yes"))[1], "enabled must be"))
d = base()
del d["enabled"], d["limits"], d["steps"]
cw, _ = C.parse(d, "front-desk")
ok("left out: enabled is false, limits are 30 minutes and 40 turns, no steps",
   cw is not None and (cw.enabled, cw.minutes, cw.turns, cw.before, cw.after) == (False, 30, 40, (), ()))
ok("a title longer than 60 is refused", says(refused(base(title="x" * 61))[1], "at most 60"))

print("may: the AI reads and drafts, and never acts")
gone, why = refused(base(may=["read:inbox", "act:send_email"]))
ok("act: is refused, and the reason says to use a step", gone and says(why, "put the tool"), str(why))
gone, why = refused(base(may=["write:replies"]))
ok("a write other than proposals is refused", gone and says(why, "only write is write:proposals"),
   str(why))
for bad in ("stuff", "web:post", "read:", 7):
    ok(f"{bad!r} is not a capability", refused(base(may=[bad]))[0])
cw, _ = C.parse(base(may=["read:reports", "read:reports"]), "front-desk")
ok("a repeated capability is kept once", cw.may == ("read:reports",))
ok("an empty list is a coworker that can only think", C.parse(base(may=[]), "x-y")[0].may == ())

print("shifts: windows, in the box's own words")
d = yaml.safe_load(EXAMPLE.replace('start: "17:00"', "start: 17:00"))
gone, why = refused(d)
ok("an unquoted 17:00 (YAML reads it as 1020) is refused with the fix",
   gone and says(why, 'in quotes, like "17:00"'), str(why))
cw, _ = C.parse(base(shifts=[{"days": "Mon", "start": "7:30", "latest": "7:45"}]), "a-b")
ok('"7:30" is 07:30', cw.shifts[0].start == "07:30" and cw.shifts[0].latest == "07:45")
for s, want in (({"days": "Mon", "start": "09:00", "latest": "09:04"}, "at least 5 minutes"),
                ({"days": "Mon", "start": "09:00", "latest": "08:00"}, "at least 5 minutes"),
                ({"days": "Mon", "start": "23:58", "latest": "00:10"}, "same day"),
                ({"days": "Mon", "start": "24:00", "latest": "24:10"}, "24-hour time"),
                ({"days": "Funday", "start": "09:00", "latest": "09:15"}, "is not a day"),
                ({"days": ["Mon"], "start": "09:00", "latest": "09:15"}, "must be days"),
                ({"days": "Mon", "start": "09:00"}, "latest must be"),
                ({"days": "Mon", "start": "09:00", "latest": "09:15", "tz": "UTC"},
                 "does not define: tz")):
    gone, why = refused(base(shifts=[s]))
    ok(f"refused: {s} ({want})", gone and says(why, want), str(why))
for text, want in (("Daily", set(range(7))), ("Mon,Wed,Fri", {0, 2, 4}), ("sat-sun", {5, 6}),
                   ("Fri-Mon", {4, 5, 6, 0}), ("Mon - Wed", {0, 1, 2})):
    cw, why = C.parse(base(shifts=[{"days": text, "start": "09:00", "latest": "09:15"}]), "a-b")
    ok(f"days {text!r}", cw is not None and cw.shifts[0].days == frozenset(want), str(why))
ok("no shifts is refused", says(refused(base(shifts=[]))[1], "at least one shift"))
many = [{"days": "Mon", "start": f"{h:02d}:00", "latest": f"{h:02d}:10"} for h in range(24)]
ok("24 shifts is the most", C.parse(base(shifts=many), "a-b")[0] is not None)
ok("25 is refused", refused(base(shifts=many + many[:1]))[0])
gone, why = refused(base(shifts=[{"days": "Mon-Fri", "start": "09:00", "latest": "09:30"},
                                 {"days": "Fri", "start": "09:15", "latest": "09:45"}]))
ok("two windows that overlap on a shared day are refused (one shift at a time)",
   gone and says(why, "overlap"), str(why))
ok("the same windows on different days are fine",
   C.parse(base(shifts=[{"days": "Mon", "start": "09:00", "latest": "09:30"},
                        {"days": "Tue", "start": "09:15", "latest": "09:45"}]), "a-b")[0] is not None)

print("steps and limits")
cw, _ = C.parse(base(steps={"before": ["acme.fetch_list"], "after": ["acme.save_picks"]}), "a-b")
ok("steps are machine.tool names, in order",
   cw.before == ("acme.fetch_list",) and cw.after == ("acme.save_picks",))
ok("tool_name maps a step to the registry's name", C.tool_name("acme.save_picks")
   == "aios.acme.save_picks")
for s in ({"before": ["save_picks"]}, {"during": []}, {"after": "acme.x_y"}, ["acme.a_b"]):
    ok(f"steps {s!r} is refused", refused(base(steps=s))[0])
for lim in ({"minutes": 0}, {"minutes": 121}, {"turns": 201}, {"turns": True}, {"hours": 1}):
    ok(f"limits {lim} is refused", refused(base(limits=lim))[0])
ok("limits 120 minutes, 200 turns is the most",
   C.parse(base(limits={"minutes": 120, "turns": 200}), "a-b")[0] is not None)

print("load(): the folder, and its job")
tmp = pathlib.Path(tempfile.mkdtemp())


def folder(name, text=EXAMPLE, job="Read the inbox. Draft replies. Send nothing."):
    f = tmp / name
    f.mkdir()
    (f / "coworker.yaml").write_text(text)
    if job is not None:
        (f / "job.md").write_text(job)
    return f


cw, why = C.load(folder("front-desk"))
ok("a folder with its job loads", cw is not None and cw.slug == "front-desk", str(why))
ok("no coworker.yaml", C.load(tmp)[1] == ["no coworker.yaml in the folder"])
ok("no job file", says(C.load(folder("no-job", job=None))[1], "not in the folder"))
ok("an empty job file", says(C.load(folder("empty-job", job="  \n"))[1], "is empty"))
ok("a job file over 32,000 bytes", says(C.load(folder("big-job", job="x" * 32_001))[1], "at most"))
ok("unreadable YAML is a reason, not a crash",
   says(C.load(folder("bad-yaml", text="coworker: [1\n"))[1], "could not be read"))
f = folder("linked-job", job=None)
(f / "job.md").symlink_to(tmp / "front-desk" / "job.md")
ok("a job that is a link out of the folder is refused", says(C.load(f)[1], "not in the folder"))
ok("a job path is a file name, never a path",
   says(refused(base(job="../other/job.md"))[1], "job must be"))

print("risks, slots, and what the box is missing")
cw, _ = C.parse(yaml.safe_load(EXAMPLE), "front-desk")
ok("reading the box's data and reaching the web is asked about",
   C.risks(cw) == ("private_and_web",))
ok("the web alone is not", C.risks(C.parse(base(may=["web:read"]), "a-b")[0]) == ())
ok("reading alone is not", C.risks(C.parse(base(may=["read:inbox"]), "a-b")[0]) == ())
ok("a slot is coworker, day and start",
   C.slot_key("front-desk", date(2026, 9, 28), "07:30") == "front-desk@2026-09-28T07:30")
STEP_SPEC = {"args": {a: {"type": t, "required": True} for a, t in C.STEP_ARGS.items()}}
cw, _ = C.parse(base(steps={"before": ["acme.fetch_list"], "after": ["gone.save"]}), "a-b")
why = C.unresolved(cw, {"aios.acme.fetch_list": STEP_SPEC})
ok("a step whose machine is missing is named, with the machine",
   len(why) == 1 and "gone.save" in why[0] and "gone machine" in why[0], str(why))

print("the step call: what a step is given, and what it gives back")
ok("a tool taking exactly the run context can be a step", C.step_signature(STEP_SPEC) == "")
args = STEP_SPEC["args"]
for label, spec, want in (
        ("a tool missing workspace", {"args": {k: v for k, v in args.items() if k != "workspace"}},
         "does not take workspace"),
        ("a tool with an argument the box never passes",
         {"args": {**args, "limit": {"type": "integer"}}}, "never passes"),
        ("a tool whose dry_run is text", {"args": {**args, "dry_run": {"type": "string",
                                                                       "required": True}}},
         "dry_run must be a required boolean"),
        ("a tool whose run_id is optional", {"args": {**args, "run_id": {"type": "string"}}},
         "run_id must be a required string"),
        ("a tool that asks for the caller's seat", {**STEP_SPEC, "wants_seat": True}, "seat"),
        ("a tool with no arguments at all", {}, "does not take run_id")):
    why = C.step_signature(spec)
    ok(f"refused as a step: {label}", want in why, why)
cw, _ = C.parse(base(steps={"after": ["acme.save_picks"]}), "a-b")
why = C.unresolved(cw, {"aios.acme.save_picks": {"args": {}}})
ok("the preflight names a step tool that is not written to be a step",
   len(why) == 1 and "acme.save_picks cannot be a step" in why[0], str(why))

# The real registry agrees: a tool registered the ordinary way, with the five, is a step.
tools._reset_for_tests()
tools.register("save_picks", fn=lambda **kw: {"summary": "Saved 3 picks", "outputs": []},
               description="d", machine="acme", capability="act:save_rows", min_role="act",
               args={a: {"type": t, "required": True} for a, t in C.STEP_ARGS.items()})
ok("a step registered through tools.register passes the preflight",
   C.unresolved(cw, tools.registry()) == [], str(C.unresolved(cw, tools.registry())))
ctx = C.step_context(run_id="run_1", cw=cw, slot="a-b@2026-09-28T07:30",
                     workspace="/var/lib/aios/coworkers/a-b/workspace", dry_run=True)
ok("the run context is exactly the five, with the coworker's slug",
   set(ctx) == set(C.STEP_ARGS) and ctx["coworker"] == "a-b" and ctx["dry_run"] is True)
ok("and the registry accepts it as the tool's arguments",
   tools.validate(tools.registry()["aios.acme.save_picks"], ctx) == ctx)
for label, kw in (("a relative workspace", {"workspace": "workspace"}),
                  ("an empty run id", {"run_id": " "}),
                  ("a dry_run that is text", {"dry_run": "yes"})):
    try:
        C.step_context(**{"run_id": "run_1", "cw": cw, "slot": "s", "workspace": "/w",
                          "dry_run": False, **kw})
        ok(f"the run context refuses {label}", False)
    except ValueError:
        ok(f"the run context refuses {label}", True)
ok("a step returns a summary and typed outputs",
   C.check_step_result({"summary": "Would have sent 3 emails (dry run)",
                        "outputs": [{"type": "email", "machine": "acme", "id": "m_1"}]}) == [])
for label, r in (("nothing", None), ("an empty summary", {"summary": "", "outputs": []}),
                 ("no outputs list", {"summary": "ok"}),
                 ("an untyped output", {"summary": "ok", "outputs": [{"id": "1"}]}),
                 ("an extra key", {"summary": "ok", "outputs": [], "sent": 3})):
    ok(f"a step result is refused: {label}", C.check_step_result(r) != [])

print("the receipt: DONE, FAILED or MISSED, never silence")
cw, _ = C.parse(yaml.safe_load(EXAMPLE), "front-desk")
W = {"start": "2026-09-28T07:30:00-07:00", "latest": "2026-09-28T07:45:00-07:00"}
done = C.receipt(run_id="run_1", cw=cw, slot="front-desk@2026-09-28T07:30", outcome="DONE",
                 reason="Drafted 3 replies", window=W, started_at="2026-09-28T07:30:04-07:00",
                 ended_at="2026-09-28T07:41:10-07:00", minutes=11.1, turns=18, cost_usd=None,
                 tools_used=[{"tool": "customer_voice.list_threads", "calls": 2, "failed": 0}],
                 outputs=[{"type": "proposal", "machine": "customer_voice", "id": "d_9",
                           "title": "Reply to Dana"}])
ok("a DONE receipt builds and checks clean", C.check_receipt(done) == [])
ok("it is version 1 and names its coworker and source",
   (done["receipt"], done["coworker"], done["from"]) == (1, "front-desk", "my"))
ok("it survives JSON (it is stored and mailed as JSON)",
   C.check_receipt(json.loads(json.dumps(done))) == [])
missed = C.receipt(run_id="run_2", cw=cw, slot="front-desk@2026-09-28T17:00", outcome="MISSED",
                   reason="The box was updating until 17:20", window=W,
                   started_at="2026-09-28T17:00:00-07:00", ended_at="2026-09-28T17:16:00-07:00")
ok("a MISSED run never started: started_at null, attempts 0",
   missed["started_at"] is None and missed["attempts"] == 0 and C.check_receipt(missed) == [])
failed = C.receipt(run_id="run_3", cw=cw, slot="front-desk@2026-09-29T07:30", outcome="FAILED",
                   reason="acme-social's schedule_post returned an error", window=W,
                   started_at="2026-09-29T07:30:02-07:00", ended_at="2026-09-29T07:31:00-07:00",
                   attempts=1, failed={"machine": "acme-social", "tool": "acme-social.schedule_post"},
                   steps=[{"phase": "after", "tool": "acme-social.schedule_post",
                           "outcome": "failed", "reason": "HTTP 500"}])
ok("a FAILED run names the machine and tool", C.check_receipt(failed) == []
   and failed["failed"]["machine"] == "acme-social")
for label, change in (
        ("an outcome outside the three", {"outcome": "OK"}),
        ("an empty reason (a run never ends in silence)", {"reason": " "}),
        ("a time with no UTC offset", {"ended_at": "2026-09-28T07:41:10"}),
        ("failed named on a DONE run", {"failed": {"machine": "acme", "tool": None}}),
        ("a third attempt", {"attempts": 3}),
        ("an unknown key", {"note": "x"}),
        ("an output with no type", {"outputs": [{"machine": "acme", "id": "1"}]}),
        ("a tool count that is negative", {"tools": [{"tool": "acme.x_y", "calls": -1,
                                                      "failed": 0}]}),
        ("a cost that is text", {"usage": {"minutes": 1, "turns": 1, "cost_usd": "0.10"}})):
    ok(f"refused: {label}", C.check_receipt({**done, **change}) != [])
try:
    C.receipt(run_id="", cw=cw, slot="s", outcome="DONE", reason="x", window=W,
              started_at=W["start"], ended_at=W["latest"])
    ok("the builder refuses a receipt that would not check", False)
except ValueError as e:
    ok("the builder refuses a receipt that would not check, naming why", "run_id" in str(e))

print("the run seat: exactly its grant")
tools._reset_for_tests()
tools.register("inbox_list", fn=lambda: ["t1"], description="d", machine="t",
               capability="read:inbox")
tools.register("spend", fn=lambda: 11, description="d", machine="t", capability="read:spend")
tools.register("draft", fn=lambda: "ok", description="d", machine="t",
               capability="write:proposals", min_role="act")
tools.register("send", fn=lambda: "sent", description="d", machine="t",
               capability="act:send_email", min_role="act")
ok("an act: tool registers (steps call it)", "aios.t.send" in tools.registry())

sid, cred = seats.mint("Front desk 2026-09-28 07:30", "act",
                       capabilities=["write:proposals", "read:inbox"])
seat = seats.verify(cred)
ok("a run seat verifies with its own list",
   seat is not None and seat["capabilities"] == ("read:inbox", "write:proposals"), str(seat))
ok("it sees only its grant, not its role's",
   [s["name"] for s in tools.visible_to(seat)] == ["aios.t.draft", "aios.t.inbox_list"])
ok("it calls what it was granted", tools.call("aios.t.inbox_list", None, seat)[1] == 200)
ok("it may draft", tools.call("aios.t.draft", None, seat)[1] == 200)
ok("its role holds read:spend, and it still cannot read spend",
   tools.call("aios.t.spend", None, seat)[1] == 403)
ok("it cannot act", tools.call("aios.t.send", None, seat)[1] == 403)
for role in ("read", "act", "service"):
    s = {"id": "seat_x", "role": role}
    ok(f"no {role} seat can see or call an act: tool",
       "aios.t.send" not in [t["name"] for t in tools.visible_to(s)]
       and tools.call("aios.t.send", None, s)[1] == 403)
ok("an act: tool says it reaches the world",
   tools.annotations_for("act:send_email")["openWorldHint"] is True
   and tools.annotations_for("read:inbox")["openWorldHint"] is False)
ok("a seat row carrying act: is filtered at the call too (defence in depth)",
   "act:send_email" not in tools.held({"role": "act", "capabilities": ("act:send_email",)}))

for bad in (["act:send_email"], ["read:inbox", "write:replies"], ["web:search"], "read:inbox"):
    before = len(seats.all_seats(include_runs=True))
    try:
        seats.mint("bad", "act", capabilities=bad)
        ok(f"minting {bad!r} is refused", False)
    except ValueError:
        ok(f"minting {bad!r} is refused, and no seat is written",
           len(seats.all_seats(include_runs=True)) == before)

_, cred0 = seats.mint("Thinks only", "act", capabilities=[])
s0 = seats.verify(cred0)
ok("an empty grant holds nothing (not its role's)", s0["capabilities"] == ()
   and tools.visible_to(s0) == [])
_, credx = seats.mint("Corrupted", "act", capabilities=["read:inbox"])
with state.connect() as c:
    c.execute("UPDATE seat_capabilities SET capabilities = 'not json' WHERE seat_id = ?",
              (credx.split(".")[0],))
sx = seats.verify(credx)
ok("an unreadable grant fails CLOSED to nothing, never to the role",
   sx["capabilities"] == () and tools.call("aios.t.inbox_list", None, sx)[1] == 403)
_, credp = seats.mint("Owner's assistant", "act")
ok("a person's seat is unchanged: its role's capabilities, no list",
   "capabilities" not in seats.verify(credp)
   and tools.call("aios.t.spend", None, seats.verify(credp))[1] == 200)

ids = [s["id"] for s in seats.all_seats()]
ok("the connected-assistants list leaves run seats out", sid not in ids and credp.split(".")[0] in ids)
ok("and shows them when asked", sid in [s["id"] for s in seats.all_seats(include_runs=True)])
seats.revoke(sid)
ok("a revoked run seat stops working", seats.verify(cred) is None)

print("the Morning Review withholds spend by capability, not by role")
report.days = lambda: ["2026-09-28"]
report.read = lambda d: [{"machine": "sales", "final": 1, "at": "2026-09-28T12:00:00+00:00"},
                         {"machine": report.METERS, "final": 1, "at": "2026-09-28T12:00:00+00:00"}]
_, credr = seats.mint("Reporter", "act", capabilities=["read:reports"])
b = report_tools.report_day(seat=seats.verify(credr))
ok("a run seat granted the report but not spend does not see the meters",
   [s["machine"] for s in b["segments"]] == ["sales"], str(b["segments"]))
ok("and is told which grant it lacks", b["withheld"] == ["meters:not_granted_read_spend"],
   str(b["withheld"]))
_, credm = seats.mint("Money", "act", capabilities=["read:reports", "read:spend"])
b = report_tools.report_day(seat=seats.verify(credm))
ok("granted spend, it sees them", report.METERS in [s["machine"] for s in b["segments"]])
b = report_tools.report_day(seat={"id": "seat_r", "role": "read"})
ok("a read seat is told what it was always told", b["withheld"] == ["meters:role_read"])
b = report_tools.report_day(seat={"id": "seat_a", "role": "act"})
ok("an act seat still sees them", b["withheld"] == [])

print()
if _failed:
    print(f"{_failed} FAILED")
    sys.exit(1)
print("ALL OK")
