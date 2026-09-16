"""Bring-your-own Zernio key (owner, 2026-09-16) — the contract, and the isolation it must not break.

WHY THIS EXISTS. Zernio has no organisation, workspace or billing-transfer API: a profile is a
folder inside ONE account on ONE bill, so every profile the provisioner mints stays on Ownbox's
bill forever. The owner does not pay for a customer's vendor, so the buyer brings their own key —
the same shape as the AI key and the mailbox credential.

THE DANGEROUS PART IS THE RESOLVER, NOT THE STORE. `spaces._norm` carries a rule with a reason
written next to it: a Space that NAMES its own key gets THAT key or NOTHING, because silently
inheriting a global key would let one client's reel post to another client's socials. Preferring
the box's own key must not weaken that by one inch, so it is asserted here from both directions.

Run: python tests/test_zernio_byo_key.py
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "byo.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
os.environ["ZERNIO_API_KEY"] = "PROVISIONER_WROTE_THIS"

from core import state  # noqa: E402

state.init_db()

from core import box_secrets as bs  # noqa: E402
from core import spaces  # noqa: E402
from core.vendors.zernio import verify as zverify  # noqa: E402

FAILS: list[str] = []
BUYERS_OWN = "BUYER_PASTED_THIS_ONE"


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


def quiet(fn, *a, **k):
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        return fn(*a, **k)


print("\n— the key is checked WITH THE VENDOR, not against a shape we invented —")
zverify.verify_key = lambda key: (True, "")                       # type: ignore[assignment]
ok("a key the vendor accepts is stored", (quiet(bs.put_zernio, BUYERS_OWN),
                                          bs.zernio_key())[1] == BUYERS_OWN)
zverify.verify_key = lambda key: (False, "Zernio did not recognise that key.")  # type: ignore
try:
    quiet(bs.put_zernio, "a-key-the-vendor-refuses")
    refused = False
except bs.SecretRejected as e:
    refused = "did not recognise" in str(e)
ok("a key the vendor REFUSES never reaches the table, and says so", refused)
ok("...and the working key is untouched by the refusal", bs.zernio_key() == BUYERS_OWN)

zverify.verify_key = lambda key: (                                # type: ignore[assignment]
    False, "That key works, but your Zernio account needs a payment method before it can "
           "connect another account. Add one in Zernio, then try again.")
try:
    quiet(bs.put_zernio, "good-key-unpaid-account")
    said = ""
except bs.SecretRejected as e:
    said = str(e)
ok("A 402 IS NOT A BAD KEY — it names the payment method, or they re-paste a good key forever",
   "payment method" in said and "did not recognise" not in said, said)

print("\n— the screen never sees the key —")
zverify.verify_key = lambda key: (True, "")                       # type: ignore[assignment]
quiet(bs.put_zernio, BUYERS_OWN)
ok("zernio_state() carries a status and no credential", BUYERS_OWN not in str(bs.zernio_state()))
quiet(bs.note_zernio_status, "payment_required", "Add a payment method in Zernio.")
ok("...and a poller refusal reaches it as its own state",
   bs.zernio_state()["status"] == "payment_required")
try:
    quiet(bs.note_zernio_status, "banana")
    closed = False
except ValueError:
    closed = True
ok("the status set is CLOSED, so a screen renders known states only", closed)

print("\n— THE BUYER'S KEY WINS OVER THE ONE THE PROVISIONER WROTE —")
sp = spaces._default_space()
ok("a single-tenant box uses the buyer's own key, not the env value",
   sp["zernio_key"] == BUYERS_OWN, sp["zernio_key"])
with state.connect() as _c:
    _c.execute("DELETE FROM box_secrets WHERE name = ?", (bs.ZERNIO,))
ok("...and falls back to the provisioned env key when the buyer has not connected one",
   spaces._default_space()["zernio_key"] == "PROVISIONER_WROTE_THIS")

print("\n— and the multi-tenant isolation rule is NOT weakened —")
quiet(bs.put_zernio, BUYERS_OWN)
os.environ["CLIENT_B_KEY"] = "client-b-key"
named = spaces._norm({"name": "b", "airtable_base": "app", "zernio_key_env": "CLIENT_B_KEY"})
ok("a Space that NAMES its own key still gets THAT key", named["zernio_key"] == "client-b-key")
del os.environ["CLIENT_B_KEY"]
missing = spaces._norm({"name": "b", "airtable_base": "app", "zernio_key_env": "CLIENT_B_KEY"})
# THE WHOLE POINT: a named-but-missing key must resolve to NOTHING. If it fell through to the
# box's own key, one client's reel could post to another client's socials — the exact failure
# the comment in spaces.py was written to prevent.
ok("...and a NAMED key that is missing resolves to NOTHING, never the box's own",
   missing["zernio_key"] is None, str(missing["zernio_key"]))

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
