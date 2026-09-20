"""The box's own push identity, and the phones it is allowed to ring.

Owner, 2026-09-20: *"The beauty of having apps is that people can control when and how
notifications come through on their phone. This is a critical business function with the unified
inbox."* And on Slack as the answer: *"if they get the notification on Slack, then they're totally
outside of our environment on their phone."*

That is the whole argument. Every other channel is read somewhere else — a Slack ping in Slack, a
text in Messages. Only a notification from the installed web app OPENS THE INBOX when it is
tapped. The client half of that has been shipped for weeks: the manifest, the registered worker,
a `push` handler that always calls `showNotification`, and a `notificationclick` that refuses any
target outside `/inbox/`. This file is the other half — who we are, and who we may ring.

THE KEYPAIR IS THE BOX'S, NEVER OURS (invariant 11-7: a clone depends on nothing of ours). One
shared VAPID identity would make every notification on every customer's box traceable to us and
would put us in the middle of a channel the homepage promises is theirs. Generating one costs
milliseconds, once, on first use.

GENERATED UNDER A LOCK, and that is not paranoia. A box serves with two gunicorn workers; two of
them reaching this at the same moment would each mint a keypair and the second would overwrite the
first. Any phone that had already subscribed against the first public key would then be pushed to
with a key the subscription does not match, and every send would fail — silently, because a push
failure is a status code nobody is watching. First writer wins, checked inside the lock.

A DEAD SUBSCRIPTION IS DELETED, NOT RETRIED. Push services answer 404 or 410 when an endpoint is
gone for good. A box that ignores that accumulates dead rows and reports "notified" to itself
forever — the same class of lie as a screen saying 72 people are waiting when nobody is.
"""
from __future__ import annotations

import base64
import fcntl
import os
from datetime import datetime, timezone
from typing import Any

from core import box_secrets, state
from core.logging import get_logger

log = get_logger(__name__)

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


VAPID_PRIVATE = "vapid_private_key"
VAPID_PUBLIC = "vapid_public_key"


class PushUnavailable(RuntimeError):
    """This box cannot do web push yet. The message says why, for a screen to show."""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def available() -> tuple[bool, str]:
    """Can this box mint a VAPID identity at all?

    SAID OUT LOUD RATHER THAN CRASHED. `cryptography` is not in requirements.lock yet, so a box
    running today's release has no ECDSA and no way to make one — the standard library has no
    P-256. A screen asking a buyer to turn on notifications must be able to say that plainly
    instead of throwing a 500 at them.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric import ec  # noqa: F401
    except Exception as e:                                        # noqa: BLE001
        return False, f"this box has no push keys yet ({type(e).__name__})"
    return True, ""


def _lock_path() -> str:
    from core.config import settings
    return os.path.join(os.path.dirname(os.path.abspath(settings.db_path)), ".vapid.lock")


def keys() -> tuple[str, str]:
    """(public, private) for this box, both base64url. Generated once, on first use."""
    pub, priv = box_secrets.get(VAPID_PUBLIC), box_secrets.get(VAPID_PRIVATE)
    if pub and priv:
        return pub, priv
    ok, why = available()
    if not ok:
        raise PushUnavailable(why)
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    with open(_lock_path(), "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            # RE-READ INSIDE THE LOCK. The worker that waited here may find the other one finished; adopting its keypair is what makes this first-writer-wins.
            pub, priv = box_secrets.get(VAPID_PUBLIC), box_secrets.get(VAPID_PRIVATE)
            if pub and priv:
                return pub, priv
            key = ec.generate_private_key(ec.SECP256R1())
            raw_pub = key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
            raw_priv = key.private_numbers().private_value.to_bytes(32, "big")
            pub, priv = _b64(raw_pub), _b64(raw_priv)
            box_secrets.put(VAPID_PRIVATE, priv)
            box_secrets.put(VAPID_PUBLIC, pub)
            log.info("push.identity_created")
            return pub, priv
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def public_key() -> str:
    """What the browser needs as `applicationServerKey`. Empty when push is not available."""
    try:
        return keys()[0]
    except PushUnavailable:
        return ""


# ── the phones ───────────────────────────────────────────────────────────────────────────────────

def save_subscription(*, user_id: str, endpoint: str, p256dh: str, auth: str) -> bool:
    """Remember one browser on one device. Returns True when it is new to us.

    PER PERSON *AND* PER DEVICE. A box seats three, and one person has a phone and a laptop. Keying
    on the user alone silently replaces somebody's phone with their laptop the first time they open
    the inbox at a desk — and they would never find out, because the phone simply stops ringing.
    The endpoint is what the push service issued, so it is the identity of the device.
    """
    endpoint = (endpoint or "").strip()
    if not endpoint.startswith("https://") or not p256dh or not auth:
        raise ValueError("a push subscription needs an https endpoint and both keys")
    with state.connect() as c:
        before = c.execute("SELECT 1 FROM push_subscriptions WHERE endpoint = ?", (endpoint,)).fetchone()
        c.execute(
            "INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, created_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(endpoint) DO UPDATE SET user_id = excluded.user_id, "
            "  p256dh = excluded.p256dh, auth = excluded.auth, last_error = NULL",
            (user_id, endpoint, p256dh, auth, _now()))
        c.commit()
    return before is None


def subscriptions_for(user_id: str) -> list[dict[str, Any]]:
    with state.connect() as c:
        rows = c.execute("SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE user_id = ?",
                         (user_id,)).fetchall()
    return [dict(r) for r in rows]


def forget(endpoint: str) -> bool:
    """Delete a subscription the push service has told us is gone. Never retried."""
    with state.connect() as c:
        n = c.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,)).rowcount
        c.commit()
    if n:
        log.info("push.subscription_forgotten")
    return bool(n)


def note_result(endpoint: str, *, ok: bool, detail: str = "") -> None:
    with state.connect() as c:
        if ok:
            c.execute("UPDATE push_subscriptions SET last_ok_at = ?, last_error = NULL "
                      "WHERE endpoint = ?", (_now(), endpoint))
        else:
            c.execute("UPDATE push_subscriptions SET last_error = ? WHERE endpoint = ?",
                      (detail[:200], endpoint))
        c.commit()


# ── sending one ──────────────────────────────────────────────────────────────────────────────────
#
# WRITTEN AGAINST THE RFCs RATHER THAN A LIBRARY, and that is a dependency decision, not pride.
# `pywebpush` would do this in three lines and pull in its own tree; the lock here is hash-pinned
# and every addition is a deliberate act (scripts/lock_dependencies.py). `cryptography` is already
# needed for the VAPID identity above, so doing the encryption with it too costs one dependency
# instead of two.
#
# RFC 8291 (Message Encryption for Web Push) over RFC 8188 (aes128gcm), authorised by RFC 8292
# (VAPID). The wire format is:
#
#     salt(16) || record_size(4, big-endian) || id_len(1)=65 || ephemeral_public(65) || ciphertext
#
# THE PAYLOAD CARRIES A NUMBER, NEVER A CUSTOMER'S WORDS. A notification renders on a locked
# screen in front of whoever is holding the phone; the message itself belongs behind the login the
# homepage promises. Callers pass a count and a destination, never a body.

TTL_SECONDS = 12 * 3600
_RECORD_SIZE = 4096


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def _unb64(v: str) -> bytes:
    return base64.urlsafe_b64decode(v + "=" * (-len(v) % 4))


def encrypt(payload: bytes, *, p256dh: str, auth: str) -> bytes:
    """One aes128gcm body, encrypted to a single subscription (RFC 8291 §3)."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    ua_public_raw = _unb64(p256dh)
    ua_public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public_raw)
    as_private = ec.generate_private_key(ec.SECP256R1())
    as_public_raw = as_private.public_key().public_bytes(Encoding.X962,
                                                         PublicFormat.UncompressedPoint)
    shared = as_private.exchange(ec.ECDH(), ua_public)

    # THE KEY IS BOUND TO BOTH PARTIES. `WebPush: info` carries the recipient's public key and
    # ours, so a body encrypted for one subscription cannot be replayed at another.
    prk = _hkdf(_unb64(auth), shared, b"WebPush: info\x00" + ua_public_raw + as_public_raw, 32)
    salt = os.urandom(16)
    cek = _hkdf(salt, prk, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, prk, b"Content-Encoding: nonce\x00", 12)

    # 0x02 IS THE LAST-RECORD DELIMITER, not padding we could omit: a body without it is rejected
    # by the push service as a malformed record, and the failure arrives as an opaque 400.
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return (salt + _RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_public_raw)])
            + as_public_raw + ciphertext)


def _vapid_header(endpoint: str, *, subject: str) -> dict[str, str]:
    """The `Authorization: vapid` header proving this box authored the push (RFC 8292)."""
    import json
    import time
    from urllib.parse import urlsplit
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    pub, priv = keys()
    parts = urlsplit(endpoint)
    claims = {"aud": f"{parts.scheme}://{parts.netloc}",
              "exp": int(time.time()) + TTL_SECONDS,
              "sub": subject}
    signing_input = (_b64(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
                     + "." + _b64(json.dumps(claims, separators=(",", ":")).encode()))
    key = ec.derive_private_key(int.from_bytes(_unb64(priv), "big"), ec.SECP256R1())
    der = key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256()))
    # ES256 IS r||s, EACH 32 BYTES — not the DER `cryptography` returns. A DER signature is
    # accepted by nothing and the push service answers 401 without saying why.
    r, s = decode_dss_signature(der)
    sig = _b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return {"Authorization": f"vapid t={signing_input}.{sig}, k={pub}"}


def send(subscription: dict, *, waiting: int | None = None, navigate: str = "/inbox/inbox",
         subject: str = "mailto:support@ownbox.io", timeout: int = 10,
         resolve=None) -> tuple[bool, str]:
    """Ring one device. Returns (delivered, detail); a gone subscription is forgotten here.

    THROUGH `core.net`, NOT `requests`, AND THAT IS A SECURITY BOUNDARY. The endpoint is DATA: the
    browser supplies it and it is stored verbatim, so a signed-in person could register one
    pointing at 169.254.169.254 or a service on the box's own loopback and have the box POST to it
    on a timer. `net.post_public` refuses a non-public host, refuses redirects, and caps the
    response — the guard in tests/ that names every bare fetch caught this one before it shipped.

    NEVER RAISES INTO A CALLER'S PASS. This runs from the same sweep that builds people's boxes and
    reads their mail; a push service having a bad minute must not be what stops either.
    """
    import json

    from core import net

    endpoint = str(subscription.get("endpoint") or "")
    try:
        body = json.dumps({"title": "Unified Inbox",
                           # A COUNT WHERE THERE IS ONE, a neutral line where there is not.
                           # Never the notice's own words: this renders on a locked screen.
                           "body": ("Something is waiting for you" if waiting is None else
                                    ("1 waiting for a reply" if waiting == 1
                                     else f"{waiting} waiting for a reply")),
                           "navigate": navigate}, separators=(",", ":")).encode()
        payload = encrypt(body, p256dh=subscription["p256dh"], auth=subscription["auth"])
        headers = {"Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
                   "TTL": str(TTL_SECONDS), **_vapid_header(endpoint, subject=subject)}
        status, _body = net.post_public(endpoint, data=payload, headers=headers,
                                        timeout=timeout, resolve=resolve)
    except PushUnavailable as e:
        return False, str(e)
    except net.PostRefused as e:
        # A REFUSED HOST IS NOT A BAD MINUTE. It is a subscription pointing somewhere it should
        # never have pointed, and it stays recorded so somebody can see it rather than vanishing.
        note_result(endpoint, ok=False, detail=f"refused: {e}"[:200])
        return False, f"refused: {e}"
    except Exception as e:                               # noqa: BLE001 — see the docstring
        note_result(endpoint, ok=False, detail=f"{type(e).__name__}: {e}")
        return False, f"{type(e).__name__}"
    # 404/410 MEANS GONE FOR GOOD, and the row goes with it. Retrying a dead endpoint is how a box
    # ends up reporting "notified" to itself forever about a phone that will never ring again.
    if status in (404, 410):
        forget(endpoint)
        return False, f"gone ({status})"
    if 200 <= status < 300:
        note_result(endpoint, ok=True)
        return True, str(status)
    note_result(endpoint, ok=False, detail=f"HTTP {status}")
    return False, f"HTTP {status}"
