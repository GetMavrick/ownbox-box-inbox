"""The connect surface must be able to say a thing STARTED WORKING AGAIN.

WHY THIS EXISTS. Every status row a buyer reads on Settings is written on the way DOWN — the
poller records why a sign-in was refused, and the screen quotes it back. Nothing was ever written
on the way UP. Both halves of that came off OSDev1's wall post of 2026-09-17, and they are the
last two rows on the screen the owner walks through as customer #1.

WHAT IT PINS, and neither is hypothetical:

1. A MAILBOX THAT RECOVERS WITHOUT ANYBODY RE-PASTING. `put_email` clears the status when a buyer
   pastes a new app password, so that path already recovers. The stuck one is the credential that
   was REFUSED BUT NEVER REVOKED: a Workspace administrator switches app passwords back on and the
   stored password starts working on its own. Until this fix the box read mail perfectly while the
   screen told its owner their administrator had turned app passwords off.

2. A ZERNIO KEY THE VENDOR ACCEPTED IS NOT REJECTED BY US. Measured against the live endpoint on
   2026-09-17, both arms: a good key answers 200 {valid, userId, authType, scope}, a bad key
   answers 401 and the SDK raises. So anything reaching the body check has already been accepted,
   and only an EXPLICIT `valid: false` may refuse it. An empty body — what the SDK hands back for a
   204 — used to reject a working key on the one screen a buyer cannot get past.

Run: python tests/test_connect_recovers.py
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
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "recover.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state  # noqa: E402

state.init_db()

from core import box_secrets as bs  # noqa: E402
from core.vendors.zernio import verify as zverify  # noqa: E402

FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


def quiet(fn, *a, **k):
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        return fn(*a, **k)


# ---------------------------------------------------------------- 1. the mailbox

# Stored DIRECTLY rather than through `put_email`, which signs in to Google. A test that opens a
# real socket is a test that fails on a laptop in a tunnel — and this suite is about what happens
# AFTER a credential exists, not about storing one.
bs.put(bs.EMAIL, json.dumps({"host": "imap.gmail.com", "user": "a@b.co", "password": "x" * 16}))

from marketing.customer_voice.inbox import poller  # noqa: E402


class _Ch:
    key = "email"


def _sweep_returns(n):
    """Swap the channel module's sweep for one that answers however this case needs."""
    from marketing.customer_voice.inbox import email_channel
    email_channel.sweep = n                                    # type: ignore[assignment]


print("\n— a mailbox that starts working again is allowed to say so —")

quiet(bs.note_email_status, "admin_disabled",
      "Your Google administrator has turned off app passwords for your organisation.")
ok("the screen reports the refusal while it is true",
   bs.email_state()["status"] == "admin_disabled")
ok("...with the sentence the buyer can act on",
   "administrator" in (bs.email_state()["detail"] or ""))

_sweep_returns(lambda space: (4, 2))
res = quiet(poller._sweep_email, "default", _Ch())
ok("a sweep that WORKS is reported as a success", res == (4, 2, True), str(res))
ok("THE STATUS RECOVERS WITHOUT ANYBODY RE-PASTING ANYTHING",
   bs.email_state()["status"] == "connected", bs.email_state()["status"])
ok("...and last week's refusal is CLEARED, not left under a row that says Connected",
   not (bs.email_state()["detail"] or "").strip(), repr(bs.email_state()["detail"]))

print("\n— and it still fails alone, and still fails loudly —")


class _Auth(Exception):
    status = "needs_reauth"
    detail = "Google refused the app password."


from marketing.customer_voice.inbox import email_channel as _ec  # noqa: E402

_ec.EmailAuthError = _Auth                                      # type: ignore[assignment]


def _refuse(space):
    raise _Auth()


_sweep_returns(_refuse)
res = quiet(poller._sweep_email, "default", _Ch())
ok("an auth refusal returns listed_ok False rather than raising", res == (0, 0, False), str(res))
ok("...and the screen is told which refusal it was",
   bs.email_state()["status"] == "needs_reauth", bs.email_state()["status"])


def _network(space):
    raise OSError("[Errno -2] Name or service not known")


_sweep_returns(_network)
res = quiet(poller._sweep_email, "default", _Ch())
ok("A NETWORK PROBLEM IS NOT A BAD PASSWORD — the sweep fails and the status is untouched",
   res == (0, 0, False) and bs.email_state()["status"] == "needs_reauth",
   f"{res} {bs.email_state()['status']}")

# The write is skipped when nothing changed, because this runs on every poll. Asserted by counting
# writes rather than by reading the value, since the value is identical either way.
_sweep_returns(lambda space: (1, 0))
quiet(poller._sweep_email, "default", _Ch())            # recovers: one write
_writes: list[str] = []
_real_note = bs.note_email_status
bs.note_email_status = lambda s, d="", **k: (_writes.append(s), _real_note(s, d, **k))[1]  # type: ignore
for _ in range(3):
    quiet(poller._sweep_email, "default", _Ch())
bs.note_email_status = _real_note                                # type: ignore[assignment]
ok("an already-connected mailbox is not re-written once per poll", _writes == [], str(_writes))

print("\n— an unconnected mailbox is not a broken one —")
bs.clear(bs.EMAIL)
_sweep_returns(lambda space: (0, 0))
res = quiet(poller._sweep_email, "default", _Ch())
ok("no credential: nothing read, nothing claimed", res == (0, 0, False), str(res))
ok("...and the screen says not_connected, never connected",
   bs.email_state()["status"] == "not_connected", bs.email_state()["status"])


# ---------------------------------------------------------------- 2. the Zernio key

class _FakeKeys:
    def __init__(self, answer):
        self._answer = answer

    def verify_credential(self):
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


class _FakeClient:
    def __init__(self, answer):
        self.api_keys = _FakeKeys(answer)


def _answers(answer):
    from core.vendors.zernio import transport
    transport.raw_client = lambda key, **k: _FakeClient(answer)   # type: ignore[assignment]


print("\n— a key the vendor accepted is never refused by us —")

# The exact body measured on the live endpoint, 2026-09-17.
_answers({"valid": True, "userId": "u_1", "authType": "api_key", "scope": None})
ok("the live 200 shape is accepted", zverify.verify_key("k") == (True, ""))

# What the SDK hands back for a 204 or any empty body: `{}`. The vendor did not refuse — it raises
# on 401 — so neither may we.
_answers({})
got = zverify.verify_key("k")
ok("AN EMPTY BODY IS NOT A REFUSAL — the vendor answered 2xx", got == (True, ""), str(got))

_answers({"userId": "u_1", "authType": "api_key"})
got = zverify.verify_key("k")
ok("...nor is the field simply being renamed on the vendor's side", got == (True, ""), str(got))

_answers({"valid": False})
got = zverify.verify_key("k")
ok("an EXPLICIT valid:false is still a refusal", got[0] is False and "recognise" in got[1], str(got))

from late.client.exceptions import (  # noqa: E402
    LateAPIError,
    LateAuthenticationError,
    LateConnectionError,
)

_answers(LateAuthenticationError("Invalid API key"))
got = zverify.verify_key("k")
ok("a 401 reads as a bad key, in words a buyer can act on",
   got[0] is False and "recognise" in got[1], str(got))

_answers(LateAPIError("Payment required", status_code=402))
got = zverify.verify_key("k")
ok("A 402 IS NOT A BAD KEY — it names the payment method",
   got[0] is False and "payment method" in got[1] and "recognise" not in got[1], str(got))

_answers(LateConnectionError("Connection failed"))
got = zverify.verify_key("k")
ok("an outage reads as an outage, never as a condemned key",
   got[0] is False and "Could not reach" in got[1] and "recognise" not in got[1], str(got))

ok("an empty key never costs a round trip", zverify.verify_key("  ")[0] is False)

print("\n— and this file cannot silently fall out of CI —")
# REPORTED WHEN THE FILE IS MISSING, NEVER RAISED. This suite ships INSIDE a sold box, and a box
# has no .github/workflows/tests.yml — OSDev5 measured two suites taking a buyer's box down on
# exactly that read (#1279). There is no hand-maintained list in a box, so there is nothing to
# drift and nothing to check; the guard is for THIS repo.
_wf = ROOT / ".github/workflows/tests.yml"
if _wf.is_file():
    ok("test_connect_recovers is in the workflow's suite list",
       "test_connect_recovers" in _wf.read_text())
else:
    print("  --   no workflow file here (a box, not the repo) — nothing to check")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
