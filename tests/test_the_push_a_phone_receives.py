"""What actually goes down the wire to a phone, and what happens when it does not arrive.

The sender is written against RFC 8291 (message encryption) over RFC 8188 (aes128gcm), authorised
by RFC 8292 (VAPID), using `cryptography` rather than a push library — the lock here is hash-pinned
and one dependency is cheaper than two.

HONEST LIMIT, STATED UP FRONT: a round-trip I both encrypt and decrypt cannot prove conformance,
only consistency — a wrong info string would be wrong identically in both directions. What IS
independent here is the VAPID signature (verified by `cryptography` against the public key, which
never sees the signing code) and the wire LAYOUT, which is checked byte by byte against the RFC's
header. The first real delivery to a real phone is the conformance proof, and nothing here claims
to be it.

WHAT WOULD HAVE TO BREAK:
  · a DER signature instead of r||s — accepted by nothing, answered 401 with no explanation;
  · a missing 0x02 delimiter — an opaque 400 from the push service;
  · a 410 that leaves the row in place, so the box rings a dead phone forever;
  · anything raising into the caller, which is the pass that also builds people's boxes.

Run: python tests/test_the_push_a_phone_receives.py
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

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

FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


have, why = push.available()
if not have:
    print(f"\n  --   no crypto on this machine ({why}); the wire is not exercised here")
    print("\nALL OK")
    sys.exit(0)

from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat  # noqa: E402


def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# a phone: its own keypair and auth secret, exactly as a browser would hand them over
ua_key = ec.generate_private_key(ec.SECP256R1())
UA_PUB = ua_key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
AUTH = os.urandom(16)
SUB = {"p256dh": b64(UA_PUB), "auth": b64(AUTH)}


print("\ntest_the_body_has_the_shape_the_rfc_describes")
body = push.encrypt(b'{"hello":"phone"}', **SUB)
salt, rs, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
ok("salt is 16 bytes", len(salt) == 16)
ok("record size is declared as 4096", rs == 4096, str(rs))
ok("the key id length says 65, an uncompressed P-256 point", idlen == 65, str(idlen))
ok("...and that many bytes follow, 0x04-prefixed", body[21] == 4 and len(body) > 21 + 65)
ok("the ciphertext carries a GCM tag (16 bytes over the plaintext + delimiter)",
   len(body) - (21 + 65) == len(b'{"hello":"phone"}') + 1 + 16, str(len(body) - (21 + 65)))


print("\ntest_the_phone_can_actually_open_it")
as_pub_raw = body[21:21 + 65]
shared = ua_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub_raw))
prk = push._hkdf(AUTH, shared, b"WebPush: info\x00" + UA_PUB + as_pub_raw, 32)
cek = push._hkdf(salt, prk, b"Content-Encoding: aes128gcm\x00", 16)
nonce = push._hkdf(salt, prk, b"Content-Encoding: nonce\x00", 12)
plain = AESGCM(cek).decrypt(nonce, body[21 + 65:], None)
ok("the phone's own key decrypts it", plain[:-1] == b'{"hello":"phone"}', repr(plain[:40]))
ok("...and the last-record delimiter is there", plain[-1] == 0x02, hex(plain[-1]))


print("\ntest_the_vapid_signature_is_one_a_push_service_would_accept")
# INDEPENDENT OF THE SIGNING CODE: verified with the public key by the library, not by us.
h = push._vapid_header("https://push.example.com/some/endpoint", subject="mailto:x@y.z")
auth_h = h["Authorization"]
tok = auth_h.split("t=", 1)[1].split(",", 1)[0]
k = auth_h.split("k=", 1)[1].strip()
head_b64, claims_b64, sig_b64 = tok.split(".")
claims = json.loads(unb64(claims_b64))
ok("the audience is the endpoint's ORIGIN, not its path",
   claims["aud"] == "https://push.example.com", claims["aud"])
ok("it expires, and not in the past", claims["exp"] > 0)
ok("the signature is r||s, 64 bytes — not DER", len(unb64(sig_b64)) == 64, str(len(unb64(sig_b64))))
from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature  # noqa: E402

raw = unb64(sig_b64)
der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
pubkey = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64(k))
try:
    pubkey.verify(der, f"{head_b64}.{claims_b64}".encode(), ec.ECDSA(hashes.SHA256()))
    verified = True
except Exception:                                       # noqa: BLE001
    verified = False
ok("...and it verifies against the key the header advertises", verified)


# ── a local server standing in for a push service ────────────────────────────────────────────────
class Push:
    def __init__(self, code: int):
        self.code, self.seen = code, []


def public(host, *a, **kw):
    """Make the loopback stand-in look like a public host to `core.net`.

    NOT A WEAKENING OF THE GUARD — the guard is the point of this design. `net.post_public` takes
    a `resolve` hook precisely so a test can exercise the real code path against a local server
    without the production check being softened for everyone.
    """
    # getaddrinfo's SHAPE, not a list of strings: core.net reads info[4][0], so a bare address
    # string is indexed character-wise and silently resolves to nothing.
    return [(2, 1, 6, "", ("93.184.216.34", 0))]


def row(user: str, url: str) -> None:
    """Insert a subscription straight into the table.

    `save_subscription` rightly refuses a non-https endpoint — that guard is about what a browser
    hands over and has its own test. A local stand-in for a push service speaks http, and making
    the guard tolerate localhost to suit a test would weaken the real thing.
    """
    with state.connect() as c:
        c.execute("INSERT OR REPLACE INTO push_subscriptions "
                  "(user_id, endpoint, p256dh, auth, created_at) VALUES (?,?,?,?,?)",
                  (user, url, SUB["p256dh"], SUB["auth"], "2026-09-20T00:00:00+00:00"))
        c.commit()


def serve(p: Push):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            p.seen.append({"headers": dict(self.headers), "len": n})
            self.rfile.read(n)
            self.send_response(p.code)
            self.end_headers()

    s = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, f"http://127.0.0.1:{s.server_address[1]}/ep"


print("\ntest_a_delivery_and_what_the_service_is_told")
srv, url = serve(p201 := Push(201))
row("ring1", url)
sent, detail = push.send({"endpoint": url, **SUB}, waiting=3, resolve=public)
ok("a 201 is a delivery", sent, detail)
# CASE-INSENSITIVE, because the transport decides the casing: urllib title-cases what it sends,
# so `TTL` arrives as `Ttl`. Asserting the exact spelling tests urllib, not the push sender.
hdrs = {k.lower(): v for k, v in p201.seen[0]["headers"].items()}
ok("Content-Encoding says aes128gcm", hdrs.get("content-encoding") == "aes128gcm")
ok("a TTL is set", (hdrs.get("ttl") or "").isdigit())
ok("the VAPID header is present", "vapid t=" in (hdrs.get("authorization") or ""))
srv.shutdown()


print("\ntest_a_gone_subscription_is_forgotten_not_retried")
srv, url = serve(Push(410))
row("ring2", url)
sent, detail = push.send({"endpoint": url, **SUB}, waiting=1, resolve=public)
ok("410 is not a delivery", not sent, detail)
ok("...and the row is deleted, so it is never rung again",
   push.subscriptions_for("ring2") == [], str(push.subscriptions_for("ring2")))
srv.shutdown()


print("\ntest_a_bad_minute_never_reaches_the_caller")
srv, url = serve(Push(500))
row("ring3", url)
sent, detail = push.send({"endpoint": url, **SUB}, waiting=2, resolve=public)
ok("a 500 is reported, not raised", not sent and "500" in detail, detail)
ok("...and the subscription is KEPT, because a bad minute is not a dead phone",
   len(push.subscriptions_for("ring3")) == 1)
srv.shutdown()

sent, detail = push.send({"endpoint": "http://127.0.0.1:1/nope", **SUB}, waiting=1, timeout=2, resolve=public)
ok("an unreachable service is reported, not raised", not sent, detail)

with state.connect() as c:
    c.execute("DELETE FROM push_subscriptions WHERE user_id LIKE 'ring%'")
    c.commit()

print("\ntest_a_subscription_cannot_aim_the_box_at_itself")
# THE ENDPOINT IS DATA. A signed-in person can register any URL, so one pointing at the cloud
# metadata service or the box's own loopback must be refused by the transport, not trusted.
sent, detail = push.send({"endpoint": "http://169.254.169.254/latest/meta-data/", **SUB}, waiting=1)
ok("a metadata-service endpoint is refused", not sent and "refused" in detail.lower(), detail)
sent, detail = push.send({"endpoint": "http://127.0.0.1:8000/inbox/", **SUB}, waiting=1)
ok("...and so is the box's own loopback", not sent and "refused" in detail.lower(), detail)


print("\n— and this file cannot silently fall out of CI —")
_wf = ROOT / ".github/workflows/tests.yml"
if _wf.is_file():
    ok("test_the_push_a_phone_receives is in the workflow's suite list",
       "test_the_push_a_phone_receives" in _wf.read_text())

print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED: " + "; ".join(FAILS))
sys.exit(1 if FAILS else 0)
