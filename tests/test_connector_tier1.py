"""Tier 1 — the Morning Review's three questions, and the proof that the registry is a registry.

Step 4 of docs/PLAN_AIOS_CONNECTOR.md section 6. Almost no new logic on purpose: the value is
proving credential -> role -> validation -> audit row -> JSON end to end on functions that cannot
themselves be wrong.

TWO THINGS THIS FILE EXISTS TO CATCH, and they are the two that would quietly ruin the product:

  1. THE MONEY RAIL REACHING A READ SEAT. `meters` is the box's spend. It is withheld from `read`
     and it must be withheld LOUDLY — named in `withheld`, never returned as an empty section.
     An empty meters section reads as "nothing was spent", which is a different and much worse
     sentence than "you may not see this". Section 4: a withheld value is never returned in a
     field that looks like the real one.

  2. A STALE REPORT READING LIKE A QUIET DAY. A six-hour-old snapshot rendered identically to a
     fresh one is how a person acts on yesterday's numbers believing they are today's.
     Stale-and-labelled is survivable; stale-and-confident is not.

And one structural claim, asserted rather than asserted-in-prose: adding these three tools
required NO edit to the registry, the manifest builder, or the transport. If it had, the registry
was a closed tuple wearing a registry's clothes (section 11.5).

Run: python tests/test_connector_tier1.py
"""
import json
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = tempfile.mkdtemp() + "/tier1.db"
os.environ["DISPATCH_BEARER_TOKEN"] = "the-wide-token"

from core import state  # noqa: E402

state.init_db()

from core import report  # noqa: E402
from core.connector import seats, tools  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def seed(day: str, machine: str, headline_value, written_at: str, final: int = 0):
    rep = {"machine": machine, "title": machine.replace("_", " ").title(),
           "headline": {"value": headline_value, "label": "things"}}
    with state.connect() as c:
        c.execute("INSERT OR REPLACE INTO daily_reports (day, machine, report_json, written_at, "
                  "final) VALUES (?,?,?,?,?)", (day, machine, json.dumps(rep), written_at, final))


NOW = datetime.now(timezone.utc)
FRESH = NOW.isoformat()
OLD = (NOW - timedelta(seconds=report.STALE_AFTER_S + 600)).isoformat()


# ── an empty box says so, rather than answering zero ──────────────────────────────────────
from core import report_tools  # noqa: E402  (imported AFTER the db exists, like the web process)
from core import dispatch  # noqa: E402

read_id, read_cred = seats.mint("reader", "read")
act_id, act_cred = seats.mint("operator", "act")
client = dispatch.app.test_client()
RH = {"Authorization": f"Bearer {read_cred}"}
AH = {"Authorization": f"Bearer {act_cred}"}


def call(tool, args=None, headers=RH):
    body = {"tool": tool}
    if args is not None:
        body["args"] = args
    return client.post("/api/v1/call", json=body, headers=headers)


for t in ("aios.morning_review.report_day", "aios.morning_review.report_days", "aios.morning_review.report_trend"):
    b = call(t).get_json()
    ok(f"{t} on a box with no report ever says not_configured",
       b.get("not_configured") is True, str(b))
    ok(f"{t} does NOT answer with a zero or an empty list", "result" not in b, str(b))


# ── now there is data ─────────────────────────────────────────────────────────────────────
TODAY = "2026-09-12"
YESTERDAY = "2026-09-11"
seed(TODAY, "lead_machine", 7, FRESH)
seed(TODAY, "content_machine", 3, FRESH)
seed(TODAY, report.METERS, "$11", FRESH)
seed(YESTERDAY, "lead_machine", 4, FRESH, final=1)
seed(YESTERDAY, report.METERS, "$9", FRESH, final=1)

b = call("aios.morning_review.report_days").get_json()["result"]
ok("report_days lists the stored days, newest first", b["days"] == [TODAY, YESTERDAY], str(b))
ok("and counts them", b["count"] == 2)


# ── the money rail is withheld from a read seat, and NAMED ────────────────────────────────
b = call("aios.morning_review.report_day", {"day": TODAY}).get_json()["result"]
machines = [s["machine"] for s in b["segments"]]
ok("a read seat sees the machine segments", "lead_machine" in machines, str(machines))
ok("a read seat does NOT see the meters segment", report.METERS not in machines, str(machines))
ok("and the withholding is NAMED, not silent", b["withheld"] == ["meters:role_read"], str(b["withheld"]))
ok("meters is absent, never present-and-empty (that reads as 'nothing was spent')",
   not any(s.get("machine") == report.METERS for s in b["segments"]))
ok("the spend figure appears nowhere in the read seat's response",
   "$11" not in json.dumps(b), json.dumps(b)[:200])

b = call("aios.morning_review.report_day", {"day": TODAY}, headers=AH).get_json()["result"]
machines = [s["machine"] for s in b["segments"]]
ok("an ACT seat does see meters", report.METERS in machines, str(machines))
ok("and nothing is marked withheld for it", b["withheld"] == [], str(b["withheld"]))


# ── freshness is on every answer ──────────────────────────────────────────────────────────
ok("a fresh day is not stale", call("aios.morning_review.report_day", {"day": TODAY}).get_json()["result"]["stale"] is False)
ok("and carries written_at", call("aios.morning_review.report_day", {"day": TODAY}).get_json()["result"]["written_at"])

seed(TODAY, "lead_machine", 7, OLD)
seed(TODAY, "content_machine", 3, OLD)
seed(TODAY, report.METERS, "$11", OLD)
b = call("aios.morning_review.report_day", {"day": TODAY}).get_json()["result"]
ok("a report older than the module's own threshold is marked STALE", b["stale"] is True, str(b["stale"]))

b = call("aios.morning_review.report_day", {"day": YESTERDAY}).get_json()["result"]
ok("a CLOSED day is never stale — it is finished, not late", b["stale"] is False, str(b))
ok("and is marked final", b["final"] is True)

b = call("aios.morning_review.report_day", {"day": "2020-01-01"}).get_json()["result"]
ok("a day with no stored report says so in words", b["note"] and "no Morning Review" in b["note"],
   str(b))
ok("rather than returning an unexplained empty list", b["segments"] == [])

b = call("aios.morning_review.report_day").get_json()["result"]
ok("omitting the day gives the most recent stored one", b["day"] == TODAY, str(b["day"]))


# ── the trend ─────────────────────────────────────────────────────────────────────────────
b = call("aios.morning_review.report_trend", {"day": TODAY, "back": 5}).get_json()["result"]
ok("the trend returns a series per machine", "lead_machine" in b["series"], str(b["series"]))
ok("days with no report are ABSENT, not zero — a gap is not a zero",
   len(b["series"]["lead_machine"]) == 2, str(b["series"]["lead_machine"]))
ok("the money headline has no series (it is a currency string, and a chart of strings is nothing)",
   report.METERS not in b["series"], str(list(b["series"])))
ok("and the response says the gap rule out loud", "absent rather than zero" in b["note"])

b = call("aios.morning_review.report_trend", {"back": 100000}).get_json()["result"]
ok("an absurd window is clamped rather than scanning the table", b["days_back"] == 365,
   str(b["days_back"]))
ok("a zero window is clamped up, not divided by",
   call("aios.morning_review.report_trend", {"back": 0}).get_json()["result"]["days_back"] == 1)


# ── the seat is identity, never an argument ───────────────────────────────────────────────
r = call("aios.morning_review.report_day", {"seat": {"id": "x", "role": "service"}})
ok("a caller cannot pass `seat` as an argument to claim a role", r.status_code == 400,
   str(r.status_code))
ok("and the refusal names it", "seat" in r.get_json()["message"])
try:
    tools.register("z", fn=lambda seat: 1, description="d", machine="m",
                   capability="read:reports", args={"seat": {"type": "string"}})
    ok("registering a tool with a `seat` ARGUMENT is refused", False, "it was accepted")
except ValueError:
    ok("registering a tool with a `seat` argument is refused", True)


# ── THE STRUCTURAL CLAIM: the registry needed no edit ─────────────────────────────────────
# Section 11.5's whole proposition. If the connector's own files had to learn about the Morning
# Review to serve it, then "a machine registers the questions it can answer" was never true and
# every future machine — including a buyer's own — would need a core edit.
# Checked by IMPORT and by TOOL NAME, not by an English substring: the connector's prose uses
# the word "reported" about its own box type, and a test that trips on a comment is a test that
# will be silenced rather than believed.
import ast as _ast  # noqa: E402


def _imports(path):
    out = []
    for node in _ast.walk(_ast.parse(path.read_text())):
        if isinstance(node, _ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, _ast.ImportFrom):
            out.append(("." * node.level) + (node.module or ""))
    return out


_TIER1 = ("aios.morning_review.report_day", "aios.morning_review.report_days", "aios.morning_review.report_trend")
for f in ("tools.py", "manifest.py", "http.py"):
    path = ROOT / "core" / "connector" / f
    imps = _imports(path)
    ok(f"core/connector/{f} does not import the Morning Review",
       not any(i in ("core.report", "core.report_tools") or i.endswith(".report")
               for i in imps), str(imps))
    src = path.read_text()
    ok(f"core/connector/{f} names none of the Tier 1 tools",
       not any(n in src for n in _TIER1), f)

ok("yet all three Tier 1 tools are served", {"aios.morning_review.report_day", "aios.morning_review.report_days", "aios.morning_review.report_trend"}
   <= set(tools.registry()))
man = client.get("/api/v1/manifest", headers=RH).get_json()
ok("and they are in the manifest with their schemas",
   all(t["machine"] == "morning_review" for t in man["tools"] if t["name"].startswith("report_")))
ok("report_day advertises its day argument",
   next(t for t in man["tools"] if t["name"] == "aios.morning_review.report_day")["args"]["day"]["type"] == "string")


# ── a core-level registration failure must not kill the web process ───────────────────────
# OSDev1's review of #1101: `_load_packs` catches a pack's import failure and records it, so a
# broken pack cannot take the box down — but the core-level tool import sat OUTSIDE that
# protection. A duplicate tool name raises from `register`, and at core level that would stop the
# box answering /dispatch because a reporting tool collided. Asserted on the source, because
# actually breaking the import would break this test process too.
_disp = (ROOT / "core" / "dispatch.py").read_text()
# Asserted over the LIST of core tool modules rather than one import line. The list grew — box
# health and spend joined the Morning Review — and a check pinned to one module name would have
# gone on passing while a second core registration sat outside the protection it is testing for.
_names = _disp.index("core.report_tools")
_try = _disp.find("try:", _names)
_note = _disp.find("note_absent(", _names)
ok("every core tool module is named in one place",
   all(m in _disp[_names:_names + 200] for m in ("core.report_tools", "core.box_tools")),
   _disp[_names:_names + 200])
ok("the core tool import sits inside a try, like a pack's does",
   _try != -1 and (_try - _names) < 200, f"nearest try is {_try - _names} chars on")
ok("and its failure is RECORDED, not swallowed — absence stays legible",
   _note != -1 and (_note - _names) < 700, f"note_absent at offset {_note - _names}")


# ── audited ───────────────────────────────────────────────────────────────────────────────
with state.connect() as c:
    rows = [dict(x) for x in c.execute(
        "SELECT tool, outcome FROM seat_actions WHERE seat_id = ?", (read_id,))]
tools_seen = {r["tool"] for r in rows}
ok("every Tier 1 call is on the ledger",
   {"aios.morning_review.report_day", "aios.morning_review.report_days", "aios.morning_review.report_trend"} <= tools_seen, str(tools_seen))
ok("including the refused one", any(r["outcome"] == "denied" for r in rows))


print(("FAILED " + str(_failed)) if _failed else "all ok")
sys.exit(1 if _failed else 0)
