"""A seat is an identity you can revoke. Everything here is about that being true.

Step 2 of docs/PLAN_AIOS_CONNECTOR.md §6. Step 1 shut the /api/ door; this is the lock, and a
lock is only worth anything if it opens for exactly one key and stops opening the moment that key
is revoked. Both halves are asserted end to end through the real HTTP gate, not by calling
`verify()` and trusting that the route does the same thing.

WHAT THIS FILE IS GUARDING AGAINST, concretely:

  · a stored secret. A stolen database must yield no working credential, so the plaintext is
    asserted absent from every column of the row.
  · a revoked seat that still works, or a revoked seat whose audit rows lose their actor.
  · the wide DISPATCH_BEARER_TOKEN quietly being accepted here. It is also the dashboard password
    and the unsubscribe signing key, and it has no per-holder revocation.
  · an error that distinguishes "no such seat" from "wrong secret", which is an oracle for
    enumerating seat ids.
  · an audit table that only records successes. A credential being tried and REFUSED is the row
    an incident is reconstructed from.

Run: python tests/test_connector_seats.py
"""
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = tempfile.mkdtemp() + "/seats.db"
os.environ["DISPATCH_BEARER_TOKEN"] = "the-wide-token"

from core import state  # noqa: E402

state.init_db()

from core.connector import seats  # noqa: E402
from core import dispatch  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


# Canary under the gated prefix, registered before the first request.
@dispatch.app.get("/api/v1/whoami")
def _whoami():
    from flask import g
    return {"seat": g.seat["id"], "role": g.seat["role"], "label": g.seat["label"]}


client = dispatch.app.test_client()


# ── minting ───────────────────────────────────────────────────────────────────────────────
seat_id, credential = seats.mint("Mavrick (prod)", "service")
ok("mint returns a seat id and a credential", seat_id.startswith("seat_") and credential)
ok("the credential is <seat_id>.<secret>", credential.startswith(seat_id + "."))
secret = credential.split(".", 1)[1]
ok("the secret is long enough to not be guessable", len(secret) >= 32, str(len(secret)))

# THE ONE THAT MATTERS: a stolen database must be worthless.
with state.connect() as c:
    row = dict(c.execute("SELECT * FROM seats WHERE id = ?", (seat_id,)).fetchone())
ok("the SECRET is nowhere in the stored row",
   not any(secret in str(v) for v in row.values()), str(row))
ok("only a hash is stored", len(row["secret_hash"]) == 64 and row["secret_hash"] != secret)
ok("a fresh seat has never been seen", row["last_seen_at"] is None)
ok("a fresh seat is not revoked", row["revoked_at"] is None)

ok("two mints never collide", seats.mint("Dana — ops", "read")[0] != seat_id)

for bad_role in ("god", "admin", "", "READ"):
    try:
        seats.mint("x", bad_role)
        ok(f"role {bad_role!r} is refused", False, "it was accepted")
    except ValueError:
        ok(f"role {bad_role!r} is refused", True)
try:
    seats.mint("   ", "read")
    ok("an unlabelled seat is refused (it would be unauditable)", False)
except ValueError:
    ok("an unlabelled seat is refused (it would be unauditable)", True)


# ── verifying ─────────────────────────────────────────────────────────────────────────────
got = seats.verify(credential)
ok("a good credential resolves", got is not None and got["id"] == seat_id, str(got))
ok("it carries the role and label the route needs",
   got and got["role"] == "service" and got["label"] == "Mavrick (prod)")
ok("verify never returns the hash or the secret",
   got is not None and set(got) == {"id", "label", "role"}, str(got and set(got)))

for label, bad in [
    ("a wrong secret", f"{seat_id}.wrong"),
    ("an unknown seat id", "seat_deadbeefdeadbeef.anything"),
    ("no dot at all", "just-a-token"),
    ("an empty string", ""),
    ("None", None),
    ("a seat id with no secret", f"{seat_id}."),
    ("a secret with no seat id", ".somesecret"),
    ("an id that is not a seat", f"notaseat.{secret}"),
    ("the WIDE dispatch bearer token", "the-wide-token"),
]:
    ok(f"{label} is refused", seats.verify(bad) is None)

# Swapping one seat's secret onto another's id must not work — the hash is per row, and this is
# the check that catches a lookup that compares against the wrong row.
other_id, other_cred = seats.mint("Third", "read")
ok("one seat's secret does not open another seat",
   seats.verify(f"{seat_id}.{other_cred.split('.', 1)[1]}") is None)


# ── the HTTP gate, end to end ─────────────────────────────────────────────────────────────
r = client.get("/api/v1/whoami")
ok("no credential is 401", r.status_code == 401, str(r.status_code))
r = client.get("/api/v1/whoami", headers={"Authorization": "Bearer the-wide-token"})
ok("the wide bearer token is 401 AT THE DOOR too", r.status_code == 401, str(r.status_code))
r = client.get("/api/v1/whoami", headers={"Authorization": f"Bearer {seat_id}.wrong"})
ok("a wrong secret is 401 at the door", r.status_code == 401, str(r.status_code))

r = client.get("/api/v1/whoami", headers={"Authorization": f"Bearer {credential}"})
ok("a REAL minted credential opens the door", r.status_code == 200, str(r.status_code))
ok("the route is handed the seat's identity, not the raw header",
   r.status_code == 200 and r.get_json() == {"seat": seat_id, "role": "service",
                                             "label": "Mavrick (prod)"},
   str(r.get_json()))

with state.connect() as c:
    seen = c.execute("SELECT last_seen_at FROM seats WHERE id = ?", (seat_id,)).fetchone()[0]
ok("a served request records last_seen_at", seen is not None)


# ── revocation ────────────────────────────────────────────────────────────────────────────
seats.record(seat_id, "whoami", outcome="ok", args={"q": 1})
seats.record(seat_id, "list_leads", outcome="denied")

ok("revoke reports it changed something", seats.revoke(seat_id) is True)
ok("re-revoking is False, not a moved timestamp", seats.revoke(seat_id) is False)
ok("revoking a seat that never existed is False", seats.revoke("seat_nope") is False)

ok("a revoked credential no longer verifies", seats.verify(credential) is None)
r = client.get("/api/v1/whoami", headers={"Authorization": f"Bearer {credential}"})
ok("and the door shuts on it — 401 on the very next request", r.status_code == 401,
   str(r.status_code))

# SOFT delete. The audit trail's only question is "who did this", and a deleted actor answers it
# with nothing.
with state.connect() as c:
    still = c.execute("SELECT label, revoked_at FROM seats WHERE id = ?", (seat_id,)).fetchone()
    rows = c.execute("SELECT tool, outcome FROM seat_actions WHERE seat_id = ? ORDER BY tool",
                     (seat_id,)).fetchall()
ok("the revoked seat's ROW survives, so its audit rows still name an actor", still is not None)
ok("and it still carries its label", still and still["label"] == "Mavrick (prod)")
ok("revoked_at is stamped", still and still["revoked_at"])
ok("its audit rows survive revocation", len(rows) == 2, str(len(rows)))
ok("a DENIED call is on the ledger — a log of successes shows nothing",
   any(r_["outcome"] == "denied" for r_ in rows), str([dict(x) for x in rows]))

# Revoking must not touch anyone else.
ok("another seat is untouched by the revocation", seats.verify(other_cred) is not None)

ok("a revoked seat still appears in the listing", any(s["id"] == seat_id for s in seats.all_seats()))
ok("and is filterable out", not any(s["id"] == seat_id
                                    for s in seats.all_seats(include_revoked=False)))
ok("the listing never carries a secret or a hash",
   all("secret_hash" not in s and "secret" not in s for s in seats.all_seats()),
   str(seats.all_seats()[:1]))


# ── the audit ledger ──────────────────────────────────────────────────────────────────────
seats.record(other_id, "t", outcome="not-a-real-outcome")
with state.connect() as c:
    o = c.execute("SELECT outcome FROM seat_actions WHERE seat_id = ? AND tool = 't'",
                  (other_id,)).fetchone()[0]
ok("an unknown outcome is stored as 'error', never as itself", o == "error", o)

# A failing audit write must never take the request down with it.
_real = state.connect
try:
    state.connect = lambda: (_ for _ in ()).throw(RuntimeError("disk gone"))
    seats.record(other_id, "t2", outcome="ok")
    ok("an audit write that fails does not raise into the request", True)
except Exception as e:                                            # noqa: BLE001
    ok("an audit write that fails does not raise into the request", False, repr(e))
finally:
    state.connect = _real

# Same for verify: a DB error is a 401, never a 500 on the door.
try:
    state.connect = lambda: (_ for _ in ()).throw(RuntimeError("disk gone"))
    ok("verify answers None on a DB error rather than raising", seats.verify(other_cred) is None)
finally:
    state.connect = _real


# ── NO ORPHAN ROWS — the integrity rule a foreign key cannot enforce here ─────────────────
# Raised by OSDev1, 2026-09-12: seat_actions.seat_id has no FK, so a fabricated seat wrote "a
# silent orphan row nothing rejects". Measured the same day: every connection reports
# foreign_keys = 0, so a declared REFERENCES clause would reject nothing while LOOKING like a
# guarantee. The rule therefore has to live in a test, and this is it.
with state.connect() as c:
    _fk = c.execute("PRAGMA foreign_keys").fetchone()[0]
ok("foreign keys really are off, which is WHY this test exists rather than a REFERENCES clause",
   _fk == 0, f"PRAGMA foreign_keys = {_fk} — if this ever becomes 1, a real FK is now an option")

with state.connect() as c:
    _known = {r["id"] for r in c.execute("SELECT id FROM seats")}
    _refs = [dict(r) for r in c.execute(
        "SELECT DISTINCT seat_id FROM seat_actions")]
_orphans = [r["seat_id"] for r in _refs
            if r["seat_id"] not in _known and r["seat_id"] != seats.ORPHAN]
ok("every audit row names a seat that EXISTS — no orphans after a full run of this suite",
   not _orphans, f"orphaned seat_ids: {_orphans}")

# And the marker itself: a seat-less call must still land a row, loudly, under an id that can
# never be mistaken for a seat. Dropping the row would be worse — an audit trail that goes quiet
# exactly when something is wrong is failing at the one job it has.
seats.record("", "ghost_call", outcome="ok")
with state.connect() as c:
    _row = c.execute("SELECT seat_id FROM seat_actions WHERE tool = 'ghost_call'").fetchone()
ok("a seat-less call still writes a row — the ledger must not go quiet when something is wrong",
   _row is not None)
ok("and it is marked ORPHAN, never given an invented seat id",
   _row and _row["seat_id"] == seats.ORPHAN, str(_row and _row["seat_id"]))
ok("the marker cannot collide with a real seat id", not seats.ORPHAN.startswith("seat_"))
ok("and it resolves to no seat, so it can never be read as an actor",
   seats.verify(f"{seats.ORPHAN}.anything") is None)

print(("FAILED " + str(_failed)) if _failed else "all ok")
sys.exit(1 if _failed else 0)
