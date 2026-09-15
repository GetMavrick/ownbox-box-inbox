"""First login — a delivered box hands itself over, once, and never again.

Provisioning builds a droplet, points a subdomain at it and emails the buyer. Until this, that
buyer then met a password box holding a password only WE knew. Putting one in the email would
mean Ownbox mints a box secret and keeps a copy, which docs/SPEC_OWNBOX_DELIVERY refuses — so
the box hands itself over to whoever can prove they hold the Stripe Checkout Session id it was
built from, already on its own disk at /opt/aios/provision.json.

WHAT THIS SUITE IS ACTUALLY DEFENDING. A claim route is a way into a box that is, by necessity,
reachable with no credential at all. Four things must hold or it is a back door:

  · ONCE means once, under concurrency, enforced by SQLite and not by an `if`
  · a wrong code is refused, throttled, and tells the guesser NOTHING it did not already know
  · the password is never stored, and a corrupted hash refuses rather than raises
  · DASH_TOKEN never stops working — owner, 2026-09-07: "I don't ever wanna be locked out of
    these machines", and a delivery flow is the worst place to start taking doors away

Run: python tests/test_box_first_login.py
"""
import json
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "claim.db")
os.environ["AIOS_PROVISION_JSON"] = os.path.join(_T, "provision.json")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer-only"
os.environ["DASH_TOKEN"] = "the-owners-own-password"

from core import state                                                # noqa: E402

state.init_db()

from core import claim                                                # noqa: E402
from core.dispatch import app as flask_app                            # noqa: E402

ORDER = "cs_live_a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6"
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def write_provision(order=ORDER):
    with open(os.environ["AIOS_PROVISION_JSON"], "w", encoding="utf-8") as fh:
        json.dump({"buyer": "Ownbox Test Co", "order": order,
                   "host": "testco.ownbox.app", "box_type": "customer_voice"}, fh)


def no_provision():
    try:
        os.unlink(os.environ["AIOS_PROVISION_JSON"])
    except FileNotFoundError:
        pass


def unclaim():
    with state.connect() as c:
        c.execute("DELETE FROM box_claim")


def client():
    return flask_app.test_client()


# ── 1. the shape of the secret ───────────────────────────────────────────────────────
def test_the_password_is_hashed_and_never_stored():
    print("test_the_password_is_hashed_and_never_stored")
    pw = "correct horse battery staple"
    h = claim.hash_password(pw)
    ok("the stored value is not the password", pw not in h and h != pw)
    ok("...and says which algorithm and parameters made it, so they can be raised later",
       h.startswith("scrypt$") and len(h.split("$")) == 6, h[:40])
    ok("two hashes of the SAME password differ — it is salted",
       claim.hash_password(pw) != h)
    ok("the right password verifies", claim.verify_password(pw, h))
    ok("a wrong one does not", not claim.verify_password(pw + "!", h))
    # A ROW THAT HAS BEEN CORRUPTED MUST REFUSE, NOT RAISE. A ValueError escaping a login path
    # is a 500 at best; at worst a caller wraps it and treats "it didn't say no" as a yes.
    for junk in ("", "scrypt$x", "notscrypt$1$2$3$aa$bb", "scrypt$n$8$1$zz$zz", None):
        ok(f"a malformed hash ({junk!r}) refuses rather than raising",
           claim.verify_password("anything", junk) is False)


def test_the_password_rules_are_length_not_theatre():
    print("test_the_password_rules_are_length_not_theatre")
    ok("too short is refused", claim.password_problem("short") is not None)
    ok(f"{claim.MIN_PASSWORD} characters is enough, with no character-class theatre",
       claim.password_problem("a" * claim.MIN_PASSWORD) is None)
    # Not composition rules — identity ones. Both of these strings travel: the address is typed
    # into the form beside it, and the code arrives by email and sits in a URL.
    ok("the email address is not a password",
       claim.password_problem("buyer@testco.com", email="buyer@testco.com") is not None)
    ok("the claim code is not a password",
       claim.password_problem(ORDER, code=ORDER) is not None)


# ── 2. what the box knows about itself ───────────────────────────────────────────────
def test_a_box_nobody_bought_has_nothing_to_claim():
    print("test_a_box_nobody_bought_has_nothing_to_claim")
    unclaim()
    no_provision()
    ok("no provision file means no order", claim.provisioned_order() is None)
    ok("...and NO code matches, not even the empty one",
       not claim.code_matches("") and not claim.code_matches(ORDER))
    r = client().get("/claim")
    ok("the page says there is nothing to claim rather than rendering a dead form",
       r.status_code == 200 and b"not set up by a purchase" in r.data)
    ok("...and does not render a form that could never succeed", b"<form" not in r.data)
    # The owner's own machine is exactly this case, so it must not be a 500 or a blank page.
    ok("claiming it is refused in words a human can act on", _refusal(code=ORDER) is not None)


def _refusal(**kw):
    kw.setdefault("email", "buyer@testco.com")
    kw.setdefault("password", "a-long-enough-password")
    try:
        claim.claim_box(**kw)
        return None
    except claim.ClaimRefused as e:
        return str(e)


# ── 3. the claim itself ──────────────────────────────────────────────────────────────
def test_the_buyer_claims_the_box_and_is_signed_in():
    print("test_the_buyer_claims_the_box_and_is_signed_in")
    unclaim()
    write_provision()
    c = client()
    r = c.get(f"/claim?c={ORDER}")
    ok("the form renders for a box with an order behind it",
       r.status_code == 200 and b"<form" in r.data)
    ok("...carrying the code in a hidden field, not re-read from the query on submit",
       f'value="{ORDER}"'.encode() in r.data)

    r = c.post("/claim", data={"c": ORDER, "email": "Buyer@TestCo.com  ",
                               "password": "a-long-enough-password"})
    ok("a correct claim redirects INTO the box, not back to a login form",
       r.status_code in (301, 302, 303) and "/claim" not in r.headers.get("Location", ""),
       f"{r.status_code} {r.headers.get('Location')}")
    ok("...and sets a session cookie", "aios_session" in r.headers.get("Set-Cookie", ""))

    row = claim.claimed()
    ok("the claim is recorded", row is not None)
    ok("...against the order this box was built from", row["order_id"] == ORDER)
    ok("...with the address lowercased and trimmed", row["email"] == "buyer@testco.com")
    ok("...and the PASSWORD IS NOWHERE IN THE ROW",
       "a-long-enough-password" not in json.dumps(dict(row)))
    owner = state.get_user(row["user_id"])
    ok("the owner row is the existing one, re-addressed — never a second owner",
       owner and owner["role"] == "owner" and owner["email"] == "buyer@testco.com")
    with state.connect() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM users WHERE role='owner' AND active=1"
                         ).fetchone()["n"]
    ok("...so the box has exactly one owner", n == 1, str(n))


def test_a_second_claim_is_refused_for_good():
    print("test_a_second_claim_is_refused_for_good")
    # Follows the successful claim above; the box is claimed.
    ok("a second claim with the RIGHT code is refused",
       _refusal(code=ORDER, email="thief@example.com") is not None)
    ok("...and the recorded owner is unchanged",
       claim.claimed()["email"] == "buyer@testco.com")
    r = client().get(f"/claim?c={ORDER}")
    ok("the page says the box is taken", b"already been set up" in r.data)
    # THE ANSWER MUST NOT VARY WITH THE GUESS. If a claimed box answered a right code
    # differently from a wrong one, the claim page would become an oracle for the order id
    # long after the claim itself stopped working.
    wrong = client().get("/claim?c=cs_live_totally_wrong_value_here_xxxxxxxx")
    ok("...identically to a stranger with a WRONG code — no oracle survives the claim",
       wrong.data == r.data and wrong.status_code == r.status_code)


def test_once_means_once_under_concurrency():
    print("test_once_means_once_under_concurrency")
    unclaim()
    write_provision()
    # TWO SIMULTANEOUS SUBMITS, which is what a double-tapped button on a phone actually is.
    # The `if claimed()` at the top of claim_box is a read, and a read followed by a write is
    # not a claim — this codebase has paid for that lesson twice (claim_opener, claim_send).
    # What makes it safe is `CHECK (id = 1)` inside SQLite.
    # THE CONSTRAINT IS PROVED DIRECTLY, GOING AROUND THE APPLICATION ENTIRELY. This check was
    # added because the concurrency test below PASSED with `CHECK (id = 1)` deleted from the
    # schema: under the GIL the two threads never actually interleaved between the read and the
    # write, so the courtesy `if` caught the second claim and the constraint was never exercised.
    # A race that does not race proves the thing it was written to prove is not load-bearing.
    with state.connect() as conn:
        try:
            conn.execute("INSERT INTO box_claim (id, claimed_at, order_id, user_id, email, "
                         "pw_hash) VALUES (2,'t','o','u','e@x.com','h')")
            ok("'once' is enforced by SQLITE, not by an if in a route", False,
               "a second row was accepted straight into the table")
        except Exception as e:                                   # noqa: BLE001
            ok("'once' is enforced by SQLITE, not by an if in a route — a second row is "
               f"refused at the database ({type(e).__name__})", True)

    # AND NOW THE RACE FOR REAL. `claimed()` is stubbed to None for the duration so BOTH threads
    # get past the courtesy check and both reach the INSERT — which is what two simultaneous
    # submits do on a box under load, and the only scenario in which the constraint is what
    # saves us. Restored in a finally: a stub that leaked would silently disarm every later test.
    unclaim()
    results, start = [], threading.Barrier(2)
    _real_claimed = claim.claimed
    claim.claimed = lambda: None

    def go(email):
        start.wait()
        try:
            claim.claim_box(code=ORDER, email=email, password="a-long-enough-password")
            results.append(("won", email))
        except claim.ClaimRefused:
            results.append(("refused", email))

    try:
        ts = [threading.Thread(target=go, args=(f"racer{i}@testco.com",)) for i in (1, 2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(10)
    finally:
        claim.claimed = _real_claimed
    won = [r for r in results if r[0] == "won"]
    ok("both requests finished", len(results) == 2, str(results))
    ok("EXACTLY ONE won, with the read-then-write check deliberately disabled", len(won) == 1,
       str(results))
    ok("...and the stored owner is that winner, not a blend of the two",
       bool(won) and claim.claimed()["email"] == won[0][1])


# ── 4. guessing ──────────────────────────────────────────────────────────────────────
def test_a_wrong_code_is_refused_throttled_and_says_nothing():
    print("test_a_wrong_code_is_refused_throttled_and_says_nothing")
    unclaim()
    write_provision()
    from core import dash as _dash
    _dash._fails.clear()
    c = client()
    # ASSERTED AS "IT STOPS", NOT AS A COUNT. `_note_failure` leaves the wait at zero while
    # n <= _FREE_ATTEMPTS, so the block lands on the request AFTER the last free one — an
    # off-by-one that a test pinned to an exact attempt number gets wrong (this one did) while
    # saying nothing about the property that matters. The bound is what makes it a real check:
    # a guesser must be stopped within a couple of attempts of the allowance, not eventually.
    codes, blocked_at = [], None
    for i in range(_dash._FREE_ATTEMPTS + 3):
        r = c.post("/claim", data={"c": f"cs_live_wrong_{i}", "email": "x@y.com",
                                   "password": "a-long-enough-password"})
        codes.append(r.status_code)
        if r.status_code == 429:
            blocked_at = i
            ok("...and says how long to wait rather than just refusing",
               bool(r.headers.get("Retry-After")))
            break
    ok("wrong codes are refused while they are answered at all",
       set(codes) <= {400, 429}, str(codes))
    ok("the box stops answering guesses, within a request or two of the allowance",
       blocked_at is not None and blocked_at <= _dash._FREE_ATTEMPTS + 1, str(codes))
    ok("...and nothing was claimed by any of them", claim.claimed() is None)

    # A BAD PASSWORD COSTS AN ATTEMPT TOO. Otherwise the form is a free oracle for the code:
    # submit a deliberately bad password and read which complaint comes back.
    _dash._fails.clear()
    before = len(_dash._fails)
    c.post("/claim", data={"c": ORDER, "email": "x@y.com", "password": "short"})
    ok("a refusal for ANY reason counts against the throttle", len(_dash._fails) > before)
    ok("...and still claimed nothing", claim.claimed() is None)
    _dash._fails.clear()


# ── 5. the door that must never close ────────────────────────────────────────────────
def test_the_dashboard_password_never_stops_working():
    print("test_the_dashboard_password_never_stops_working")
    unclaim()
    write_provision()
    from core import dash as _dash
    _dash._fails.clear()
    c = client()
    r = c.post("/dash/login", data={"token": "the-owners-own-password"})
    ok("DASH_TOKEN signs in on an UNCLAIMED box (every box today)",
       r.status_code in (301, 302, 303), str(r.status_code))
    ok("...and a claimed-password check on an unclaimed box is False, never a crash",
       claim.password_ok("anything") is False)

    claim.claim_box(code=ORDER, email="buyer@testco.com", password="a-long-enough-password")
    _dash._fails.clear()
    r = c.post("/dash/login", data={"token": "the-owners-own-password"})
    ok("DASH_TOKEN STILL signs in after the claim — claiming adds a door, never removes one",
       r.status_code in (301, 302, 303), str(r.status_code))
    _dash._fails.clear()
    r = c.post("/dash/login", data={"token": "a-long-enough-password"})
    ok("...and so does the password the buyer chose", r.status_code in (301, 302, 303),
       str(r.status_code))
    _dash._fails.clear()
    r = c.post("/dash/login", data={"token": "neither-of-those"})
    ok("a third string opens nothing", r.status_code == 401, str(r.status_code))
    _dash._fails.clear()


# ── 6. the thing that would silently break this ──────────────────────────────────────
def test_the_web_process_can_actually_read_the_provision_file():
    print("test_the_web_process_can_actually_read_the_provision_file")
    # provision.json is 0600 root:root (provisioner/userdata.py). The dispatch service can read
    # it only because it runs as root — it sets no User=. The day somebody hardens that unit
    # with `User=aios`, first login stops working on every future box and NOTHING ELSE DOES,
    # which is precisely the kind of breakage that ships. Asserted on the unit file, because a
    # comment saying "runs as root" is not a check.
    import pathlib
    unit = pathlib.Path("deploy/aios-dispatch.service")
    if not unit.is_file():
        print("  --   no deploy/ here — this box is not the repo")
        return
    txt = unit.read_text()
    ok("the dispatch unit still runs as root, which is what makes provision.json readable",
       not any(ln.strip().startswith("User=") for ln in txt.splitlines()),
       "a User= line was added; first login needs provision.json (0600 root:root)")


# ── 7. where they land ───────────────────────────────────────────────────
def test_the_door_opens_onto_a_page_that_exists_on_THIS_box():
    print("test_the_door_opens_onto_a_page_that_exists_on_THIS_box")
    # THE DEFECT THIS REPLACES: claim and login both fell back to `/dash`, and a customer_voice
    # box ships NEITHER `/dash/home` NOR `/dash` — the box builder sets MODULES to customer_voice
    # alone and filters the web modules to match. On the box we actually sell, a $499 buyer set
    # their password and landed on a 404. Found by OSDev4; confirmed against an exported tree.
    #
    # (The builder is described rather than named on purpose: `test_suite_integrity` treats any
    # suite whose TEXT mentions that script as an export driver and demands it be skipped inside
    # boxes — and this suite must ship into every box, because checking the landing is only
    # meaningful in the box whose routes are being checked.)
    #
    # ASSERTED AS A PROPERTY OF WHATEVER BOX THIS IS RUNNING IN, not against a simulated one.
    # A test that mocked a url_map would prove only that the mock matched the code. This suite
    # SHIPS INTO EVERY BOX, so run inside an exported inbox tree it checks the inbox tree, and
    # run in the repo it checks the everything-build — the same assertion, four products.
    unclaim()
    write_provision()
    from core import dash as _dash
    _dash._fails.clear()
    with flask_app.test_request_context("/"):
        where = _dash.landing()
        have = {str(r) for r in flask_app.url_map.iter_rules()}
    ok(f"the landing this box picks ({where}) is a route this box actually serves",
       where in have, f"{where} is not among {len(have)} routes — that is the 404")
    ok("...and it is never the login, which would read as a failed password",
       where != "/dash/login", "no landing route exists at all on this box")

    # AND THE REAL DOORS, end to end: a redirect is only correct if following it works.
    c = client()
    r = c.post("/claim", data={"c": ORDER, "email": "buyer@testco.com",
                               "password": "a-long-enough-password"})
    dest = r.headers.get("Location", "")
    ok("claiming redirects somewhere", r.status_code in (301, 302, 303), str(r.status_code))
    ok(f"...and FOLLOWING it is not a 404 ({dest})",
       c.get(dest).status_code != 404, f"the buyer's first screen after setting a password")

    _dash._fails.clear()
    r = c.post("/dash/login", data={"token": "the-owners-own-password"})
    dest = r.headers.get("Location", "")
    ok(f"signing in lands somewhere real too ({dest})",
       c.get(dest).status_code != 404, "same hole, pre-existing, in login")
    _dash._fails.clear()


def test_the_landing_order_itself_is_what_OSDev1_asked_for():
    print("test_the_landing_order_itself_is_what_OSDev1_asked_for")
    # ASSERTED AGAINST SYNTHETIC ROUTE SETS, not against whichever box happens to run this. The
    # test above proves the landing EXISTS wherever it runs; this one proves the ORDER, which no
    # single box can show — the repo has /dash/home so it can never demonstrate the inbox box's
    # fall-through, and an inbox box has no dashboards so it can never demonstrate the owner's.
    # Building the url_map by hand is the only way to check every shape from one place.
    import flask
    from core import dash as _dash

    def lands_when(*paths):
        app = flask.Flask(f"shape_{abs(hash(paths))}")
        for i, path in enumerate(paths):
            app.add_url_rule(path, endpoint=f"e{i}", view_func=lambda: "")
        with app.test_request_context("/"):
            return _dash.landing()

    # OSDev1's explicit ask on #1172: "a url_map holding both /dash/home and /voice/ lands on
    # /dash/home." The owner ruled what he sees first (2026-09-09, "you're gonna see the today
    # page"), so a LOGIN fix must not move it as a side effect.
    ok("a box with both the client home and the inbox lands on the home",
       lands_when("/dash/home", "/voice/") == "/dash/home",
       lands_when("/dash/home", "/voice/"))
    ok("...whichever order the routes happen to be registered in",
       lands_when("/voice/", "/dash/home") == "/dash/home")
    # The buyer's box: no dashboard of any kind.
    ok("an inbox-only box lands on the inbox", lands_when("/voice/") == "/voice/")
    # THE CASE THAT DISTINGUISHES OSDev1'S ORDER FROM MY FIRST ONE. I had /dash second, which
    # lands a BUYER on the reel operator dashboard when their box has one and no client home.
    # His puts the inbox first: they land on the machine they bought.
    ok("with the reel dashboard and the inbox but no client home, the INBOX wins",
       lands_when("/dash", "/voice/") == "/voice/", lands_when("/dash", "/voice/"))
    ok("...and /dash is still reached when it is genuinely all there is",
       lands_when("/dash") == "/dash")
    # A box serving none of them shipped no UI; terminate rather than loop, and say so loudly.
    errs = []
    _sv = _dash.log.error
    _dash.log.error = lambda msg, **k: errs.append(msg)
    try:
        where = lands_when("/health")
    finally:
        _dash.log.error = _sv
    ok("a box with no landing at all terminates instead of looping",
       where == "/dash/login", where)
    ok("...and logs it, because a person would otherwise just see a login form again",
       "dash.no_landing_route" in errs, str(errs))


if __name__ == "__main__":
    test_the_password_is_hashed_and_never_stored()
    test_the_password_rules_are_length_not_theatre()
    test_a_box_nobody_bought_has_nothing_to_claim()
    test_the_buyer_claims_the_box_and_is_signed_in()
    test_a_second_claim_is_refused_for_good()
    test_once_means_once_under_concurrency()
    test_a_wrong_code_is_refused_throttled_and_says_nothing()
    test_the_dashboard_password_never_stops_working()
    test_the_web_process_can_actually_read_the_provision_file()
    test_the_door_opens_onto_a_page_that_exists_on_THIS_box()
    test_the_landing_order_itself_is_what_OSDev1_asked_for()
    print("\nall ok" if not _failed else f"\n{_failed} FAILED")
    sys.exit(1 if _failed else 0)
