"""The Base Machine's MCP endpoint has something to say on day one.

ownbox.io sells a box with NO add-on machine on it and names the MCP server as one of four
reasons to buy it. This suite is the proof of that claim on the box that actually ships, and its
subject is the day-one state specifically: no machine installed, no report stored, no worker beat
yet. That is the state a buyer connects their agent in, and it is the state in which a registry
has the least to say.

WHAT IT REFUSES TO ACCEPT AS A PASS:

  1. A NON-EMPTY LIST THAT ANSWERS NOTHING. "The endpoint returns tools" is satisfied by four
     tools that all reply "nothing stored yet". So this file calls every tool it is shown and
     requires that at least one comes back with a REAL answer — not a not_configured state — on
     a box that has done nothing.

  2. "unknown" AS A BOX TYPE. A base machine is a product we sell, not an unrecognised box. An
     agent handed `unknown` tells its buyer their box is unidentified.

  3. SPEND REACHING A READ SEAT. core/report_tools.py withholds the meters segment from a read
     seat, loudly and on purpose. A spend tool that any read seat can list or call would give the
     same number back through a second door, and a field withheld in one place only is not
     withheld. Asserted from both sides: invisible in the list, refused on the call.

  4. A GREEN LIGHT COMPUTED FROM DATA WE COULD NOT READ. health() withholds `ok` rather than
     guessing it whenever an input was unreadable or a component is itself unknown.

Run: python tests/test_the_base_machine_answers.py
"""
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = tempfile.mkdtemp() + "/base.db"
os.environ["DISPATCH_BEARER_TOKEN"] = "the-wide-token"

from core import state  # noqa: E402

state.init_db()

from core.connector import manifest, seats, tools  # noqa: E402
from core import dispatch  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


client = dispatch.app.test_client()
read_id, read_cred = seats.mint("a buyer's agent", "read")
act_id, act_cred = seats.mint("the owner's agent", "act")
RH = {"Authorization": f"Bearer {read_cred}"}
AH = {"Authorization": f"Bearer {act_cred}"}


def call(tool, headers=RH, **args):
    """POST a tool call and unwrap it. A real answer arrives under `result`; a typed state
    (not_configured) and an error arrive at the top level, which is the envelope's whole job."""
    r = client.post("/api/v1/call", headers=headers, json={"tool": tool, "args": args})
    body = r.get_json()
    if isinstance(body, dict) and "result" in body:
        body = body["result"]
    return r.status_code, body


# ── the endpoint is not empty, and it is not empty of ANSWERS ─────────────────────────────
print("\n— what a base box offers before anything has happened —")

listed = client.get("/api/v1/tools", headers=RH).get_json()["tools"]
names = [t["name"] for t in listed]
ok("a read seat is shown a non-empty tool list", bool(names), str(names))
ok("core's own tools are in it — the box can be asked about itself",
   {"aios.core.manifest", "aios.core.health"} <= set(names), str(names))

# Every tool that takes no required argument is CALLED. The ones that need an id or a query are
# not invented arguments for — a made-up id proves the validator works, not that the tool does —
# so they are checked for a validator refusal instead, which is its own guarantee.
answered, deferred, needs_args = [], [], []
by_name = {t["name"]: t for t in listed}
for n in names:
    required = [a for a, spec in (by_name[n].get("args") or {}).items() if spec.get("required")]
    if required:
        code, body = call(n)
        ok(f"{n} refuses a call missing {required[0]}, rather than guessing",
           code == 400 and (body or {}).get("error") == "missing_arg", f"HTTP {code}: {body}")
        needs_args.append(n)
        continue
    code, body = call(n)
    ok(f"{n} runs", code == 200, f"HTTP {code}: {body}")
    (deferred if (body or {}).get("not_configured") else answered).append(n)

# THE CHECK THIS FILE EXISTS FOR. Before core registered health, every answering tool on a base
# box was the manifest — a description of capability. A box that can only describe itself is the
# empty endpoint the list length hides.
ok("and at least one gives a REAL answer, not a 'nothing yet' state",
   len(answered) >= 2 and "aios.core.health" in answered,
   f"answered={answered} deferred={deferred}")


# ── it knows WHAT IT IS ───────────────────────────────────────────────────────────────────
print("\n— a base machine is a product, not an unrecognised box —")

# THE MODULE LIST IS THE ONLY THING THAT MAKES A BOX A BASE MACHINE, and it is what the exporter
# writes: `modules: []`. This suite runs inside the development tree, whose config loads every
# machine, so the three identity checks below substitute the config a base box actually ships
# with. Substituting the INPUT rather than the answer is the point — box_type() is still the
# shipped function deriving the shipped result. The box built by `export_box.sh core` is measured
# for real in tests/test_box_boots.py, which runs this same endpoint inside it.
_real = manifest._modules
manifest._modules = lambda: []
ok("box_type is 'base', never 'unknown'", manifest.box_type() == "base", manifest.box_type())
ok("with no machines claimed", manifest.machines() == [], str(manifest.machines()))

# A config we cannot read is NOT a base machine. Collapsing the two is how a broken box
# introduces itself confidently as a product it may not be.
manifest._modules = lambda: None
ok("but a config that will not read is 'unknown', not 'base'", manifest.box_type() == "unknown")

# Modules present that we do not recognise — a buyer's own machine — stays unknown, because that
# is a box running something we did not write and it is a different sentence.
manifest._modules = lambda: ["their_company.their_machine"]
ok("and a machine we did not write is 'unknown' too, not 'base'",
   manifest.box_type() == "unknown", manifest.box_type())
manifest._modules = _real

ok("and it is still labelled derived, not stamped",
   "derived" in manifest.build(seat={"role": "read", "id": read_id})["box_type_source"])


# ── health answers on a box where nothing has run ─────────────────────────────────────────
print("\n— health, on a box four minutes old —")

code, h = call("aios.core.health")
ok("health runs for a read seat", code == 200, f"HTTP {code}")
ok("never-beaten is its OWN state, not a dead worker",
   h["worker"]["state"] == "no_beat_yet" and h["worker"]["ok"] is None, str(h["worker"]))
ok("an unprobed brain is not an outage either",
   h["brain"]["state"] == "not_probed" and h["brain"]["ok"] is None, str(h["brain"]))
ok("the queue is reported as numbers, not prose",
   set(h["queue"]) == {"queued", "running", "failed_24h"}, str(h.get("queue")))
ok("`ok` is WITHHELD rather than guessed while components are unknown",
   h["ok"] is None and "withheld" in h["ok_note"], str(h.get("ok")))
ok("and health carries NO money field anywhere",
   not any(k in str(h) for k in ("ceiling_usd", "cycle_to_date_usd", "usd_per_unit")), str(h))

ok("it names the box it is speaking for", h["box_id"] == manifest.box_id())


# ── spend is the owner's, through every door ──────────────────────────────────────────────
print("\n— the one number a read seat may not have —")

ok("a read seat is not even SHOWN spend", "aios.core.spend" not in names, str(names))
code, body = call("aios.core.spend", headers=RH)
ok("and asking for it anyway is refused, not answered empty",
   code == 403 and body.get("error") == "forbidden", f"HTTP {code}: {body}")

act_names = [t["name"] for t in client.get("/api/v1/tools", headers=AH).get_json()["tools"]]
ok("an act seat IS shown it", "aios.core.spend" in act_names, str(act_names))
code, s = call("aios.core.spend", headers=AH)
ok("and gets the ceiling with the window it is measured over",
   code == 200 and s["claude"]["ceiling_usd"] > 0 and s["claude"]["cycle_started"],
   f"HTTP {code}: {s}")
ok("a vendor list and its note are never both present or both absent",
   bool(s["vendors"]) != bool(s["vendors_note"]), str(s))

# The development tree HAS metered vendors, so the base machine's normal state — none configured
# — is reached by removing them. An empty list with nothing said reads as "nothing was spent",
# which is a different and much worse sentence than "no meter is configured here".
from core import box_tools, cost_guard  # noqa: E402
_real_vendors = cost_guard.metered_vendors
cost_guard.metered_vendors = lambda: []
_s = box_tools.spend()
ok("with none configured, that is SAID rather than left as an empty list",
   _s["vendors"] == [] and "no metered vendor is configured" in (_s["vendors_note"] or ""),
   str(_s.get("vendors_note")))
cost_guard.metered_vendors = _real_vendors


# ── and the screen that hands out the key says so ────────────────────────────────────────
print("\n— the consent screen names what it is granting —")

# A permission list a buyer reads before handing over a key has to name everything the key can
# see. `act` now carries the billing figure, and a role sold as "read and draft replies" while it
# can also read what the box spends is a consent screen that misleads. Pinned on the two
# sentences that would be wrong, not on the whole copy.
from core.dash import box_settings  # noqa: E402
_choices = {r: d for r, _t, d in box_settings._ROLE_CHOICES}
ok("read-only says out loud that it CANNOT see spending",
   "cannot see what the box is spending" in _choices["read"], _choices["read"])
ok("and the role that CAN see it says so before the key is minted",
   "spent against its monthly ceiling" in _choices["act"], _choices["act"])
ok("every role offered on that screen exists in the capability table",
   all(r in tools.ROLE_RANK for r in _choices), str(list(_choices)))


# ── the refusal is on the ledger, like every other call ───────────────────────────────────
print("\n— audited —")
with state.connect() as c:
    rows = [dict(x) for x in c.execute(
        "SELECT seat_id, tool, outcome FROM seat_actions")]
ok("the read seat's spend attempt is recorded as denied",
   any(r["seat_id"] == read_id and r["tool"] == "aios.core.spend" and r["outcome"] == "denied"
       for r in rows), str(rows))
ok("and the act seat's as ok",
   any(r["seat_id"] == act_id and r["tool"] == "aios.core.spend" and r["outcome"] == "ok"
       for r in rows), str(rows))


# ── core registered these WITHOUT the registry or the transport being edited ──────────────
print("\n— the registry is still a registry —")
for f in ("tools.py", "manifest.py", "http.py", "mcp.py"):
    src = (ROOT / "core" / "connector" / f).read_text()
    ok(f"core/connector/{f} names none of core's own tools",
       not any(n in src for n in ("aios.core.health", "aios.core.spend", "box_tools")), f)

print("\nALL BASE-MACHINE CHECKS PASS" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
