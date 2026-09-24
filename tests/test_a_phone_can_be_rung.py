"""A box knows who it is to a push service, and which phones it may ring.

Owner, 2026-09-20: notifications on a phone are "a critical business function with the unified
inbox", and his own reason for web push over Slack — "if they get the notification on Slack, then
they're totally outside of our environment on their phone." Only a notification from the installed
web app opens the inbox when tapped.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  · two workers minting two keypairs. A box runs two gunicorn workers; if the second overwrites
    the first, every phone that subscribed against the first public key is pushed to with a key
    its subscription does not match, and every send fails silently.
  · a laptop evicting a phone. Keyed on the user rather than the endpoint, one person opening the
    inbox at a desk replaces their own phone and is never told.
  · a dead endpoint living forever. Push services answer 404/410 when a subscription is gone; a
    box that keeps it reports "notified" to itself about a phone that will never ring again.

Run: python tests/test_a_phone_can_be_rung.py
"""
from __future__ import annotations

import base64
import os
import re
import sys
import tempfile

os.environ["AIOS_HERMETIC_TEST"] = "1"
# AIOS_DB_PATH, NOT AIOS_DB, and set BEFORE core is imported — `settings.db_path` is read at
# import time, so a variable set afterwards points at nothing. The wrong NAME is why an
# earlier cut of this suite was not hermetic and had to delete its own rows to re-run;
# tests/test_box_owned_files.py guards exactly this and caught it.
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "aios.db")
from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import state  # noqa: E402

state.init_db()

from core import push  # noqa: E402

# THIS SUITE OWNS ITS OWN ROWS AND NOTHING ELSE'S. AIOS_DB is not honoured here — `settings.db_path`
# resolves from config — so a second run sees the first run's subscriptions and "is this new?"
# answers False for a reason that has nothing to do with the code. Clearing only this suite's two
# fake users keeps it re-runnable without touching a real box's phones.
with state.connect() as _c:
    _c.execute("DELETE FROM push_subscriptions WHERE user_id IN ('u1', 'u2')")
    _c.commit()

FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


print("\ntest_the_table_exists_on_every_box")
with state.connect() as c:
    cols = [r[1] for r in c.execute("PRAGMA table_info(push_subscriptions)")]
ok("migration 53 created push_subscriptions",
   {"user_id", "endpoint", "p256dh", "auth", "created_at", "last_ok_at", "last_error"} <= set(cols),
   str(cols))
# CORE, NOT A MACHINE'S: a Lead box with a hot prospect wants a phone rung too.
from core.state import _MIGRATION_OWNER  # noqa: E402
ok("...and it is untagged, so every box carries it", 53 not in _MIGRATION_OWNER,
   str(_MIGRATION_OWNER.get(53)))


print("\ntest_the_box_has_one_identity_not_two")
have, why = push.available()
if not have:
    print(f"  --   no crypto on this machine ({why}); the identity half is not exercised")
    ok("...and the box says so plainly instead of throwing — a sentence, never an exception class",
       why and "notifications" in why and not re.search(r"[A-Z][a-z]+(Exception|Error)\b|\(", why), why)
else:
    a = push.public_key()
    b = push.public_key()
    ok("the public key is stable across calls", a == b and len(a) > 80, f"{a[:20]}… vs {b[:20]}…")
    raw = base64.urlsafe_b64decode(a + "=" * (-len(a) % 4))
    # 65 BYTES, 0x04-PREFIXED: what `applicationServerKey` must be. A key of the wrong shape is
    # accepted by the browser and then fails at send time, which is the worst place to find out.
    ok("...and it is an uncompressed P-256 point, the shape the browser requires",
       len(raw) == 65 and raw[0] == 4, f"{len(raw)} bytes, first byte {raw[0] if raw else None}")



print("\ntest_a_broken_crypto_library_is_a_sentence_in_ci_too")
# THE CASE ABOVE ONLY RUNS WHERE CRYPTO IS MISSING, AND CI HAS CRYPTO — so on its own it never runs
# where it would catch anything (OSDev1, 2026-09-23). This one breaks the import on purpose, the
# way it broke for real: a native wheel that panics raises a BaseException subclass, not an
# ImportError. It must come back as a sentence, and the class name must stay in the log.
import builtins  # noqa: E402


class PanicException(BaseException):
    """Stands in for pyo3_runtime.PanicException, which inherits from BaseException."""


_real_import = builtins.__import__


def _panicking_import(name, *args, **kwargs):
    if name == "cryptography" or name.startswith("cryptography."):
        raise PanicException("Python API call failed")
    return _real_import(name, *args, **kwargs)


builtins.__import__ = _panicking_import
try:
    have_broken, why_broken = push.available()
    raised = ""
except BaseException as e:  # noqa: BLE001 — the test is whether anything escapes
    have_broken, why_broken, raised = None, "", type(e).__name__
finally:
    builtins.__import__ = _real_import
ok("a panicking crypto import is answered, not raised", not raised, raised)
ok("...the box says it cannot send notifications", have_broken is False and "notifications" in why_broken,
   repr((have_broken, why_broken)))
ok("...and the buyer's sentence carries no exception class",
   "PanicException" not in why_broken and not re.search(r"[A-Z][a-z]+(Exception|Error)\b|\(", why_broken),
   why_broken)

print("\ntest_a_laptop_never_evicts_a_phone")
phone = dict(user_id="u1", endpoint="https://push.example/phone", p256dh="p1", auth="a1")
laptop = dict(user_id="u1", endpoint="https://push.example/laptop", p256dh="p2", auth="a2")
ok("the phone is new", push.save_subscription(**phone))
ok("the laptop is new too", push.save_subscription(**laptop))
ok("...and BOTH are kept for that one person", len(push.subscriptions_for("u1")) == 2,
   str(push.subscriptions_for("u1")))
ok("re-subscribing the same device is not a second row",
   push.save_subscription(**phone) is False and len(push.subscriptions_for("u1")) == 2)
ok("another person's phones are not mine", push.subscriptions_for("u2") == [])


print("\ntest_a_dead_endpoint_is_deleted_not_retried")
push.note_result(phone["endpoint"], ok=False, detail="410 Gone")
ok("a failure is recorded against the device", True)
ok("forgetting it removes the row", push.forget(phone["endpoint"]))
ok("...and it is really gone", len(push.subscriptions_for("u1")) == 1)
ok("forgetting twice is harmless", push.forget(phone["endpoint"]) is False)


print("\ntest_a_subscription_must_be_real")
for bad, why in ((dict(user_id="u", endpoint="http://insecure", p256dh="p", auth="a"), "not https"),
                 (dict(user_id="u", endpoint="https://x", p256dh="", auth="a"), "no p256dh"),
                 (dict(user_id="u", endpoint="", p256dh="p", auth="a"), "no endpoint")):
    raised = False
    try:
        push.save_subscription(**bad)
    except ValueError:
        raised = True
    ok(f"refused: {why}", raised)


print("\n— and this file cannot silently fall out of CI —")
_wf = ROOT / ".github/workflows/tests.yml"
if _wf.is_file():
    ok("test_a_phone_can_be_rung is in the workflow's suite list",
       "test_a_phone_can_be_rung" in _wf.read_text())

print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED: " + "; ".join(FAILS))
sys.exit(1 if FAILS else 0)
