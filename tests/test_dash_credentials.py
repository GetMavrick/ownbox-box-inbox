"""No-cost, no-network proof that the dashboard password is not the command credential,
and that a FRESH box can log in at all.

Two defects, one file:

  1. scripts/install.sh minted only UNSUB_SIGNING_KEY, so a fresh box had an empty
     DISPATCH_BEARER_TOKEN. An empty bearer refuses BOTH front doors — /dispatch rejects
     every request AND dashboard.py's login rejects every password — so a buyer's first
     act after install was failing the login form of the thing they had just bought.
  2. The dashboard password WAS the dispatch bearer. Correct for one operator on one box;
     wrong the moment the person logging in is not the person who owns it, because showing
     someone the dashboard then means handing them the ability to drive the whole box.

The fallback is what makes (2) safe to ship: settings.dash_token defaults to the bearer, so
an existing box changes nothing. That claim is ASSERTED here rather than assumed — it is the
whole reason this needs no migration.

Run: python tests/test_dash_credentials.py
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


# THE DEVELOPER'S OWN SHELL IS NOT THE BOX. Every case below spawns a child that builds its own
# credentials and then asserts what the login accepts. A child inherits the parent's environment,
# so a DASH_TOKEN exported in whatever shell runs the tests silently becomes the box's dash
# password and the assertions measure that instead. Measured 2026-09-15 inside an exported
# customer_voice box on a machine with DASH_TOKEN set: the correct password came back 401 and the
# suite reported "one stranger cannot lock the owner out of his own box" — a sentence that reads
# like a live security hole and had nothing to do with the throttle. A test whose verdict depends
# on who is running it is worse than no test, because the failure text points somewhere real.
_CREDENTIAL_ENV = ("DASH_TOKEN", "DISPATCH_BEARER_TOKEN", "AIOS_DB_PATH", "AIOS_RELEASE_FILE",
                   "UNSUB_SIGNING_KEY", "AIOS_DEPLOY_TOKEN")


def _clean_env(**extra):
    """The parent environment with every credential this suite sets for itself removed."""
    env = {k: v for k, v in os.environ.items() if k not in _CREDENTIAL_ENV}
    env.update(extra)
    return env



def ok(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        globals()["_failed"] = globals().get("_failed", 0) + 1


# ── 1. install.sh mints the bearer, and never touches one that exists ─────────────────
# Exercising the real `mint` from the real script: a copy of the shell would prove nothing
# about the file that actually runs on a buyer's box.

def _mint(env_body, var):
    """Run install.sh's own mint() against a temp .env and return that file's contents."""
    src = (ROOT / "scripts/install.sh").read_text()
    body = src[src.index("mint() {"):src.index("mint UNSUB_SIGNING_KEY")]
    with tempfile.TemporaryDirectory() as d:
        env = Path(d) / ".env"
        env.write_text(env_body)
        script = f'cd {d}\nVPY={sys.executable!r}\n{body}\nmint {var} "why"\n'
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                           env=_clean_env())
        assert r.returncode == 0, r.stderr
        return env.read_text(), r.stdout


def test_install_mints_the_bearer():
    out, said = _mint("SOMETHING=else\n", "DISPATCH_BEARER_TOKEN")
    minted = [l for l in out.splitlines() if l.startswith("DISPATCH_BEARER_TOKEN=")]
    ok("a fresh .env gets a bearer", len(minted) == 1, out)
    ok("and it is a real 32-byte secret", bool(_HEX64.match(minted[0].split("=", 1)[1])))
    ok("and install.sh says so out loud", "generated DISPATCH_BEARER_TOKEN" in said)

    # THE PART THAT MUST NEVER REGRESS. UNSUB_SIGNING_KEY has to outlive every email it
    # signed (CAN-SPAM keeps opt-out links live ≥30 days), so re-running install must not
    # rotate a secret. An installer that quietly re-keys a box breaks opt-out links in mail
    # already delivered — silently, and only for recipients who click.
    out2, said2 = _mint("DISPATCH_BEARER_TOKEN=keepme\n", "DISPATCH_BEARER_TOKEN")
    ok("an existing secret is left EXACTLY as it was", out2 == "DISPATCH_BEARER_TOKEN=keepme\n", out2)
    ok("and it reports 'present', not 'generated'", "present" in said2 and "generated" not in said2)

    # An empty `KEY=` line (which is what .env.example ships) must be replaced, not doubled.
    out3, _ = _mint("DISPATCH_BEARER_TOKEN=\nOTHER=x\n", "DISPATCH_BEARER_TOKEN")
    ok("an EMPTY key line is filled, not duplicated",
       len([l for l in out3.splitlines() if l.startswith("DISPATCH_BEARER_TOKEN=")]) == 1, out3)
    ok("and the rest of .env survives", "OTHER=x" in out3)


# ── 2 & 3. the login, in subprocesses: Settings reads env AT IMPORT ──────────────────
# The split cannot be tested by setting os.environ after importing settings — the class body
# has already run. Each case therefore gets its own interpreter, which is also the honest
# shape of the claim: this is about how a box BOOTS, not about runtime mutation.

# THE DASH DOES NOT SHIP ON EVERY BOX. dashboard.py lives in marketing/content_machine, so an
# exported LEAD box has no /dash at all — only the unsubscribe page. This suite runs inside such
# a box (test_recipe_ships runs all 50 shipped suites against a fresh export), and asserting a
# login route there fails on a box that is behaving correctly. So the credential itself — which
# is core/config.py and ships everywhere — is asserted unconditionally, and the HTTP behaviour
# only where there is something to log into. Giving the Lead box a dash is Phase 3, not this PR.
_CASE = r'''
import os, sys, tempfile
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = tempfile.mkdtemp() + "/dash.db"
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer-secret"
{dash}
sys.path.insert(0, {root!r})
from core import state; state.init_db()
from core.config import settings
from core.dispatch import app
app.testing = True
c = app.test_client()
print("DASH_TOKEN_IS", settings.dash_token)
if any(str(r) == "/dash/login" for r in app.url_map.iter_rules()):
    print("DASH_PRESENT yes")
    print("BEARER", c.post("/dash/login", data={{"token": "bearer-secret"}}).status_code)
    print("DASH", c.post("/dash/login", data={{"token": "dash-secret"}}).status_code)
    print("WRONG", c.post("/dash/login", data={{"token": "nope"}}).status_code)
else:
    print("DASH_PRESENT no")
'''


def _run(dash_line):
    r = subprocess.run([sys.executable, "-c", _CASE.format(dash=dash_line, root=str(ROOT))],
                       capture_output=True, text=True, cwd=ROOT, env=_clean_env())
    assert r.returncode == 0, r.stdout + r.stderr
    return dict(l.split(" ", 1) for l in r.stdout.splitlines() if l.startswith(
        ("DASH_TOKEN_IS", "DASH_PRESENT", "BEARER", "DASH ", "WRONG")))


_THROTTLE = r'''
import os, sys, tempfile
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = tempfile.mkdtemp() + "/dash.db"
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer-secret"
sys.path.insert(0, {root!r})
from core import state; state.init_db()
# A FROZEN CLOCK. The throttle's first block is 2 SECONDS (2 ** (6 - _FREE_ATTEMPTS)), and this
# script then makes three more requests that must all land inside it. On a fast machine that is
# milliseconds; on a loaded cloud sandbox it is not, and the block lifts mid-test. Reproduced
# 2026-09-08 by inserting a 2.5s delay: Retry-After came back empty and the case failed, which
# is what OSDev5 was seeing on origin/main while the same commit passed here.
#
# The fix is not a longer sleep — a blocked request is refused BEFORE _note_failure, so extra
# attempts never lengthen the window. Time is the input, so the test controls it. This asserts
# the real _block_seconds/_note_failure arithmetic more precisely than a race did.
import core.dash as dash
class _Clock:
    now = 1_000_000.0
    @staticmethod
    def time(): return _Clock.now
dash.time = _Clock
from core.dispatch import app
app.testing = True
c = app.test_client()
if not any(str(r) == "/dash/login" for r in app.url_map.iter_rules()):
    print("DASH_PRESENT no"); raise SystemExit(0)
print("DASH_PRESENT yes")
H = {{"X-Forwarded-For": "203.0.113.9"}}
codes = [c.post("/dash/login", data={{"token": "nope"}}, headers=H).status_code for _ in range(7)]
print("CODES", ",".join(str(x) for x in codes))
blocked = c.post("/dash/login", data={{"token": "nope"}}, headers=H)
print("RETRY_AFTER", blocked.headers.get("Retry-After", ""))
# the RIGHT password from the SAME address is refused too — a throttle that still checks the
# guess is a counter, not a throttle
print("RIGHT_WHILE_BLOCKED", c.post("/dash/login", data={{"token": "bearer-secret"}}, headers=H).status_code)
# a DIFFERENT address is untouched: one stranger must not lock the owner out of his own box
other = {{"X-Forwarded-For": "198.51.100.4"}}
print("OTHER_IP", c.post("/dash/login", data={{"token": "bearer-secret"}}, headers=other).status_code)
# and a success clears that address's record
print("CLEARED", str(dash._fails.get("198.51.100.4")))
# AND THE BLOCK LIFTS ON ITS OWN. Never asserted before, and it is the half that matters most:
# a throttle that does not expire is a lockout, and one stranger guessing at the owner's box
# would shut HIM out of it. Only a controlled clock can check this without sleeping.
_Clock.now += 30
print("AFTER_WAIT", c.post("/dash/login", data={{"token": "nope"}}, headers=H).status_code)
'''


def test_the_login_stops_answering_unlimited_guesses():
    """MEASURED against the live box 2026-09-06: twelve wrong passwords in 2.2s, no backoff.
    compare_digest stops a timing oracle, not a guessing machine, and ten boxes are about to sit
    on public subdomains."""
    r = subprocess.run([sys.executable, "-c", _THROTTLE.format(root=str(ROOT))],
                       capture_output=True, text=True, cwd=ROOT, env=_clean_env())
    assert r.returncode == 0, r.stdout + r.stderr
    o = dict(l.split(" ", 1) for l in r.stdout.splitlines() if l.startswith(
        ("DASH_PRESENT", "CODES", "RETRY_AFTER", "RIGHT_WHILE_BLOCKED", "OTHER_IP", "CLEARED",
         "AFTER_WAIT")))
    if o.get("DASH_PRESENT") == "no":
        print("ok   no /dash on this box (a Lead export) — nothing to throttle")
        return
    codes = o["CODES"].split(",")
    ok("the first attempts are answered normally (a fat-fingered owner never meets the wall)",
       codes[:5] == ["401"] * 5, o["CODES"])
    ok("past the free attempts the box REFUSES to check at all (429)", "429" in codes[5:], o["CODES"])
    ok("and it says how long to wait (Retry-After)", o["RETRY_AFTER"].isdigit() and int(o["RETRY_AFTER"]) > 0, o["RETRY_AFTER"])
    ok("the RIGHT password is refused too while blocked — a throttle that still checks is a counter",
       o["RIGHT_WHILE_BLOCKED"] == "429", o["RIGHT_WHILE_BLOCKED"])
    ok("a DIFFERENT address is untouched — one stranger cannot lock the owner out of his own box",
       o["OTHER_IP"] == "302", o["OTHER_IP"])
    ok("a successful login clears that address's record", o["CLEARED"] == "None", o["CLEARED"])
    ok("and the block LIFTS on its own — a throttle that never expires is a lockout",
       o["AFTER_WAIT"] == "401", o.get("AFTER_WAIT"))


def test_no_dash_token_behaves_exactly_as_before():
    """The no-migration claim, asserted. An existing box has never heard of DASH_TOKEN."""
    o = _run('os.environ.pop("DASH_TOKEN", None)')
    ok("dash_token falls back to the bearer", o["DASH_TOKEN_IS"] == "bearer-secret")
    if o["DASH_PRESENT"] == "no":
        print("  ·    no /dash on this box (Lead-only export) — login assertions skipped")
        return
    ok("the bearer still logs in — nothing changes for an existing box", o["BEARER"] == "302")
    ok("a wrong token is still 401", o["WRONG"] == "401")


def test_dash_token_set_refuses_the_bearer():
    """The point of the split. Not 'both work' — the command credential STOPS working here."""
    o = _run('os.environ["DASH_TOKEN"] = "dash-secret"')
    ok("dash_token is its own secret", o["DASH_TOKEN_IS"] == "dash-secret")
    if o["DASH_PRESENT"] == "no":
        print("  ·    no /dash on this box (Lead-only export) — login assertions skipped")
        return
    ok("the dash token logs in", o["DASH"] == "302")
    ok("and the DISPATCH BEARER IS REFUSED", o["BEARER"] == "401",
       "a dash password that also drives /dispatch cannot be shown to anyone else")


if __name__ == "__main__":
    for fn in (test_install_mints_the_bearer,
               test_no_dash_token_behaves_exactly_as_before,
               test_dash_token_set_refuses_the_bearer,
               test_the_login_stops_answering_unlimited_guesses):
        print(fn.__name__)
        fn()
    n = globals().get("_failed", 0)
    print(f"{n} FAILED" if n else "all ok")
    sys.exit(1 if n else 0)
