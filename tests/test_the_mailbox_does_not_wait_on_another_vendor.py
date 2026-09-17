"""A buyer's mailbox is read whether or not an unrelated vendor's SDK verifies.

WHAT WAS TRUE BEFORE THIS (OSDev4, measured 2026-09-17 on main). `register_periodic(
poller.poll_sweep, ...)` sat inside `if _sdk_ok:` — the Zernio SDK gate — and `poll_sweep` is
where the MAILBOX is swept too (`_sweep_email`). Email is IMAP: it touches no Zernio SDK, no
Zernio key, no Zernio account. So a drifted vendor SDK stopped a buyer's Gmail from being read.

WHY THAT IS THE ONBOARDING BUG AND NOT A TIDINESS ONE. The app password is step ONE of set-up: the
longest instructions on the screen, and the only step that needs no new account and no card. A
buyer who did it perfectly got silence, on the first day they owned the box, for a reason that had
nothing to do with them — and nothing on any screen said why.

WHAT IS NOT LOOSENED, and this suite pins it as hard as the fix: `handler.handle` SENDS, and it
stays behind the gate. A drifted SDK still means the box sends nothing through Zernio and polls
nothing from it. Intake for the vendor stops; intake for the mailbox does not.

Run: python tests/test_the_mailbox_does_not_wait_on_another_vendor.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "mail.db")
os.environ.pop("ZERNIO_API_KEY", None)

from core import state  # noqa: E402

state.init_db()

from core.vendors import zernio  # noqa: E402
from marketing.customer_voice.inbox import channels, poller  # noqa: E402

FAILS = []
SPACE = "acme"


def ok(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        FAILS.append(label)


def with_sdk(ok_flag, detail="version drift: installed 9.9.9, pinned 1.0.0"):
    """Pretend the vendor SDK verifies, or does not. Resets the poller's one-time answer."""
    poller._VENDOR_OK = None
    zernio.verify_sdk = lambda: (ok_flag, "" if ok_flag else detail)


_real_verify = zernio.verify_sdk


# ── 1. the decision, asked directly ──────────────────────────────────────────────────────
print("\ntest_the_vendor_gate_answers_for_the_vendor_only")

with_sdk(True)
ok("a verifying SDK permits vendor intake", poller._vendor_intake_allowed() is True)
with_sdk(False)
ok("A DRIFTED SDK REFUSES IT — Zernio is not loosened by this change",
   poller._vendor_intake_allowed() is False)

# ASKED ONCE, NOT EVERY 45 SECONDS. verify_sdk imports the SDK, compares a pinned version and
# inspects three signatures; the answer cannot change without a restart.
calls = []
zernio.verify_sdk = lambda: (calls.append(1), (True, ""))[1]
poller._VENDOR_OK = None
poller._vendor_intake_allowed()
poller._vendor_intake_allowed()
poller._vendor_intake_allowed()
ok("the SDK is verified once and the answer kept", len(calls) == 1, f"{len(calls)} calls")


# ── 2. the mailbox is swept with the vendor SDK broken ───────────────────────────────────
print("\ntest_a_broken_vendor_sdk_does_not_stop_the_mailbox")

swept = []
_real_email = poller._sweep_email
poller._sweep_email = lambda space, ch: (swept.append(space), (1, 1, True))[1]
_real_spaces = poller._spaces
# A box with BOTH a mailbox and a Zernio key — the shape where the old gate did its damage.
poller._spaces = lambda: [{"name": SPACE, "zernio_key": "zk-live"}]
_built = []
_real_client = zernio.client


class _FakeZ:
    """Enough client to be built and asked for a page, and no more — the assertion here is
    WHETHER one is built, not what it returns."""
    class inbox:                                          # noqa: N801
        @staticmethod
        def list(limit=None, platform=None):
            return {"conversations": []}


zernio.client = lambda sp: (_built.append(sp), _FakeZ())[1]

try:
    with_sdk(False)
    swept.clear(); _built.clear()
    poller.poll_sweep()
    ok("THE MAILBOX IS STILL SWEPT — step one of set-up pays off on its own",
       swept == [SPACE], str(swept))
    ok("...and no Zernio client is built, so nothing polls the vendor on a drifted SDK",
       _built == [], str(_built))

    with_sdk(True)
    swept.clear(); _built.clear()
    poller.poll_sweep()
    ok("with the SDK verifying, the mailbox is swept as before", swept == [SPACE], str(swept))
    ok("...and the Zernio client IS built", len(_built) == 1, str(_built))
finally:
    poller._sweep_email = _real_email
    poller._spaces = _real_spaces
    zernio.client = _real_client
    zernio.verify_sdk = _real_verify
    poller._VENDOR_OK = None


# ── 3. what must stay behind the gate ────────────────────────────────────────────────────
print("\ntest_sending_is_still_fail_closed")

import inspect  # noqa: E402
import pathlib  # noqa: E402

init = (pathlib.Path(__file__).resolve().parents[1]
        / "marketing/customer_voice/inbox/__init__.py").read_text()
# ASSERTED ON INDENTATION, NOT ON SLICING THE FILE. My first version took the text between
# `if _sdk_ok:` and the first `register_periodic(` and asserted the periodic was not in it — which
# is true WHATEVER the file says, because that slice ends at the thing it is looking for. Putting
# the old gate back left the suite green, which is how I found it. Indentation is the real signal:
# inside the `if` the call is indented, at module level it is not.
_lines = init.splitlines()
_periodic = [ln for ln in _lines if "register_periodic(poller.poll_sweep" in ln]
_handler = [ln for ln in _lines if 'register("inbox", handler.handle)' in ln]
ok("the intake periodic is registered at MODULE LEVEL, outside any gate",
   len(_periodic) == 1 and not _periodic[0].startswith((" ", "\t")), str(_periodic))
ok("`handler.handle` — the SEND path — is still INDENTED, i.e. inside the SDK gate",
   len(_handler) == 1 and _handler[0].startswith("    "), str(_handler))
ok("the drift is still logged at ERROR, so a silent box is impossible",
   "inbox.disabled_sdk_drift" in init)

# The email channel must never be reachable from the vendor gate's reasoning: it takes no key.
src = inspect.getsource(poller.poll_sweep)
ok("email is swept before any Zernio client is required",
   src.index("_sweep_email") < src.index("if z is None"), "order changed")


print("\n— and this file cannot silently fall out of CI —")
_wf = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml"
if _wf.is_file():
    ok("test_the_mailbox_does_not_wait_on_another_vendor is in the workflow's suite list",
       "test_the_mailbox_does_not_wait_on_another_vendor" in _wf.read_text())
else:
    print("  --   no workflow file here (a box, not the repo) — nothing to check")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:")
    [print("   -", f) for f in FAILS]
    sys.exit(1)
print("ALL OK")
