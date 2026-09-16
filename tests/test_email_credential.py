"""The mailbox credential (core/box_secrets.py) — the contract the connect screen binds to.

SPEC #1226 §4. The buyer pastes a Google app password and this holds it. Two things would hurt them if
they went wrong quietly: their real Google password ending up in our table, and a revoked credential
leaving a box that has silently stopped reading mail with nothing on screen to say so.

The screen (OSDev5) reads `email_state()` and posts to `put_email()`. It touches no table and speaks no
IMAP — so everything a screen can possibly need is asserted here, and the password is asserted absent.

Run: python tests/test_email_credential.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "cred.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state  # noqa: E402

state.init_db()

from core import box_secrets as bs  # noqa: E402

FAILS: list[str] = []
APP_PW = "abcdefghijklmnop"


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


def quiet(fn, *a, **kw):
    """Run it with the structured log captured, so a test run is readable and the log is inspectable."""
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        out = fn(*a, **kw)
    return out, buf.getvalue()


print("\n— the four states a screen has to render —")
ok("a box nobody has connected is not_connected, with no user and no error",
   bs.email_state() == {"status": "not_connected", "user": None, "detail": ""}, str(bs.email_state()))
quiet(bs.put_email, host="imap.gmail.com", user="owner@acme.com", password=APP_PW)
ok("...connected once a credential is stored, and it names the mailbox",
   bs.email_state()["status"] == "connected" and bs.email_state()["user"] == "owner@acme.com")
quiet(bs.note_email_status, "needs_reauth", "Google refused the app password.")
ok("needs_reauth when Google refuses — the state that exists because a password change revokes it",
   bs.email_state()["status"] == "needs_reauth" and "refused" in bs.email_state()["detail"])
quiet(bs.note_email_status, "admin_disabled", "Your administrator has turned off app passwords.")
ok("admin_disabled is its own state, not a generic auth error",
   bs.email_state()["status"] == "admin_disabled" and "administrator" in bs.email_state()["detail"])
try:
    quiet(bs.note_email_status, "banana")
    refused_unknown = False
except ValueError:
    refused_unknown = True
ok("an unknown status is refused rather than stored — the screen renders a fixed set of states",
   refused_unknown and bs.email_state()["status"] == "admin_disabled", bs.email_state()["status"])


print("\n— THE PASSWORD NEVER LEAVES THIS MODULE —")
st = bs.email_state()
ok("email_state() carries no password, in any field", APP_PW not in json.dumps(st), json.dumps(st))
_, logged = quiet(bs.put_email, host="imap.gmail.com", user="owner@acme.com", password=APP_PW)
ok("...and storing one logs the NAME that changed, never the value", APP_PW not in logged, logged[:200])
ok("box_secrets is a scrubbed table, so an export cannot carry it either",
   "box_secrets" in state.SECRET_TABLES, str(state.SECRET_TABLES))

print("\n— what a buyer actually pastes —")
quiet(bs.put_email, host="imap.gmail.com", user="owner@acme.com", password="abcd efgh ijkl mnop")
ok("GOOGLE SHOWS IT AS FOUR GROUPS OF FOUR, so a pasted space is normal, not an error",
   bs.email_credential()["password"] == APP_PW, bs.email_credential().get("password", ""))
ok("the host defaults rather than demanding a buyer know what IMAP is",
   (quiet(bs.put_email, host="", user="owner@acme.com", password=APP_PW),
    bs.email_credential()["host"])[1] == "imap.gmail.com")

print("\n— refusals, each with a sentence a person can act on —")
for kw, why, expect in (
    ({"host": "", "user": "notanemail", "password": APP_PW}, "an address with no @", "full email address"),
    ({"host": "", "user": "a@b.com", "password": ""}, "an empty password", "app password Google gave you"),
    ({"host": "", "user": "a@b.com", "password": "MyRealPassword1!"}, "THEIR ACTUAL GOOGLE PASSWORD",
     "not an app password"),
    ({"host": "", "user": "a@b.com", "password": "short"}, "something far too short", "16 letters"),
):
    try:
        quiet(bs.put_email, **kw)
        ok(f"{why} is refused", False, "stored it")
    except bs.SecretRejected as e:
        ok(f"{why} is refused, and says what to fix", expect in str(e), str(e))

print("\n— the credential is ONE row, so it can never be half-updated —")
before = bs.email_credential()
try:
    quiet(bs.put_email, host="imap.gmail.com", user="new@acme.com", password="bad")
except bs.SecretRejected:
    pass
after = bs.email_credential()
ok("a refused change leaves the working credential exactly as it was", before == after,
   f"{before.get('user')} -> {after.get('user')}")
ok("...and the mailbox is still the old one, not a half-written new one",
   after.get("user") == before.get("user"))

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
