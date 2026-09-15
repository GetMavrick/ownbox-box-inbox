"""The connector's door ships SHUT. A new /api/ route must never be world-readable.

WHY THIS TEST EXISTS, MEASURED RATHER THAN ARGUED. `core/dispatch.py`'s `_auth_gate` is an
allowlist of exactly two prefixes — /deploy and /dispatch. Everything else falls through with no
credential checked. That is correct for /health and for the public unsubscribe page, and it is
catastrophic for /api/: the FIRST connector route anyone writes would answer any stranger on the
internet, in full, and no test in the suite would have said a word.

Run against main before the gate existed (2026-09-12, real Flask test client):

    GET /api/v1/probe with NO Authorization header  ->  200 {"leaked":true}

That is the bug. It is not a hypothetical about a route we might write badly; it is the default
that any correctly-written route inherits. docs/PLAN_AIOS_CONNECTOR.md §6 step 1.

WHAT THE GATE MUST AND MUST NOT DO. Narrow, new prefix only. Flipping the whole app to
deny-by-default breaks /gtm/unsubscribe, which is a CAN-SPAM one-click opt-out link already
printed in mail that has already been sent — it MUST answer an unauthenticated stranger, because
that stranger is a recipient exercising a legal right. So this file asserts both directions: the
new prefix is shut, and every public route that was public stays public.

THE PREFIX IS /api/, NOT /api/v1/. Deviation from the plan's wording, deliberate: gating only
/api/v1 leaves /api/v2 to ship world-readable on the day someone writes it, which is this exact
bug with a version bump. Measured first — the app's url_map carries 53 rules and ZERO of them
are under /api/ — so widening the gate to the whole prefix costs nothing today and closes the
repeat.

Run: python tests/test_connector_gate.py
"""
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ.setdefault("AIOS_DB_PATH", tempfile.mkdtemp() + "/t.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "wide-token-for-this-test"

from core import dispatch  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


# Canary routes, registered BEFORE the first request (Flask refuses to add routes after).
# These stand in for the connector routes that do not exist yet — the whole point of step 1 is
# that the prefix is shut BEFORE anything is written under it, so the first real route inherits
# a closed door instead of an open one.
@dispatch.app.get("/api/v1/canary")
def _canary_v1():
    return {"secret": "this must never reach an unauthenticated caller"}


@dispatch.app.get("/api/v2/canary")
def _canary_v2():
    return {"secret": "a future version must inherit the same closed door"}


@dispatch.app.get("/api/canary")
def _canary_bare():
    return {"secret": "the unversioned prefix is shut too"}


client = dispatch.app.test_client()
WIDE = {"Authorization": "Bearer wide-token-for-this-test"}


# ── the door is shut ──────────────────────────────────────────────────────────────────────
for path in ("/api/v1/canary", "/api/v2/canary", "/api/canary"):
    r = client.get(path)
    ok(f"{path} with NO Authorization is 401", r.status_code == 401,
       f"got {r.status_code} {r.get_data(as_text=True).strip()[:80]}")
    ok(f"{path} leaks no body to an unauthenticated caller", "secret" not in r.get_data(as_text=True),
       r.get_data(as_text=True).strip()[:80])

r = client.get("/api/v1/canary", headers={"Authorization": "Bearer not-the-token"})
ok("a wrong bearer is 401", r.status_code == 401, str(r.status_code))

r = client.get("/api/v1/canary", headers={"Authorization": "wide-token-for-this-test"})
ok("a bare token with no 'Bearer ' scheme is 401", r.status_code == 401, str(r.status_code))

# THE ONE THAT MATTERS MOST. DISPATCH_BEARER_TOKEN is not an API key: core/config.py falls back
# to it for DASH_TOKEN, and core/compliance.py derives the unsubscribe SIGNING key from it. It
# unlocks the dashboard and signs every opt-out link on the box. `_deploy_authorized`'s docstring
# already records why a second, narrow token exists for /deploy — "handing that one string to
# anything is handing over all three". The connector must not repeat what /deploy had to undo,
# and there is no per-seat revocation for a secret that is also the dashboard password.
r = client.get("/api/v1/canary", headers=WIDE)
ok("the WIDE dispatch bearer token does NOT open the connector door",
   r.status_code == 401, f"got {r.status_code} — the wide token must not be an API key")


# ── and every public route that was public stays public ───────────────────────────────────
r = client.get("/health")
ok("/health is still public", r.status_code == 200, str(r.status_code))

# The unsubscribe page: a CAN-SPAM one-click opt-out already printed in sent mail. A 401 here is
# a compliance incident, not a test failure, which is why the gate is a prefix and not a default.
rules = {str(rule) for rule in dispatch.app.url_map.iter_rules()}
unsub = [p for p in rules if "unsubscribe" in p]
ok("the unsubscribe route is mounted on this box", bool(unsub), str(sorted(rules))[:120])
for p in unsub:
    r = client.get(p.replace("<email>", "x@example.com"))
    ok(f"{p} answers an UNAUTHENTICATED stranger (opt-out is a legal right)",
       r.status_code != 401, f"got {r.status_code}")


# ── /dispatch and /deploy are untouched ───────────────────────────────────────────────────
ok("/dispatch with no auth is still 401", client.post("/dispatch", json={}).status_code == 401)
r = client.post("/dispatch", json={}, headers=WIDE)
ok("/dispatch with the right bearer still gets IN (400 for a bad body, not 401)",
   r.status_code != 401, str(r.status_code))
ok("/deploy with no auth is still 401", client.post("/deploy", json={}).status_code == 401)


# ── the gate is a real seam, not a hardcoded refusal ──────────────────────────────────────
# A gate that can never open is untestable in the direction that matters: step 2 has to be able
# to open it for a valid seat. Assert the seam EXISTS and that flipping it actually lets a
# request through — otherwise "401 on everything" could be a routing accident rather than a gate.
ok("there is a _seat_authorized seam for step 2 to fill", hasattr(dispatch, "_seat_authorized"))
if hasattr(dispatch, "_seat_authorized"):
    real = dispatch._seat_authorized
    dispatch._seat_authorized = lambda req: True
    try:
        r = client.get("/api/v1/canary")
        ok("with a seat authorised, the SAME route answers 200 — the gate is the only thing "
           "standing between them", r.status_code == 200, str(r.status_code))
    finally:
        dispatch._seat_authorized = real
    ok("and it is shut again the moment the seat goes away",
       client.get("/api/v1/canary").status_code == 401)
    # The SHIPPED seam, called directly. Step 1's version ignored its argument and this line
    # passed None; step 2 made it read a real header, so the stand-in has to be request-shaped.
    # The assertion is unchanged in intent: a box with no seat minted refuses, which is the
    # state every box ships in.
    class _NoHeaders:
        headers = {}

    ok("the shipped seam refuses a request carrying nothing", real(_NoHeaders()) is False)
    ok("and refuses a well-formed bearer that matches no seat",
       real(type("R", (), {"headers": {"Authorization": "Bearer seat_0000000000000000.x"}})())
       is False)


print(("FAILED " + str(_failed)) if _failed else "all ok")
sys.exit(1 if _failed else 0)
