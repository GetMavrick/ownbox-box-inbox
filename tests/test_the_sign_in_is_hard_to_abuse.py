"""The box's OAuth sign-in is hard to abuse (#1857 H4). Anyone can call /oauth/register and /oauth/authorize, and a
CIMD client id makes the box FETCH a URL a stranger chose, so:
  1. DNS rebinding is closed: the client document is fetched at the address that was checked, every redirect hop
     re-resolved, re-checked and re-pinned (core/net.py `get_public(pin=True)`);
  2. failed fetches are limited PER URL: five in ten minutes refuse that URL for an hour, without a fetch; a trusted
     vendor's host (chatgpt.com, openai.com, claude.ai) is never refused, so bad ids under it lock nobody out;
  3. idle clients are swept: no approval in 30 days and no live seat; a client holding a live seat is kept, and the
     sweep has a caller (the watchdog), which it never had;
  4. the trusted domains are exactly three, so a fourth is a reviewed change.

Run: python tests/test_the_sign_in_is_hard_to_abuse.py
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import os
import secrets
import socket
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "signin.db")

from core import net, state  # noqa: E402

state.init_db()
from core.connector import oauth, seats  # noqa: E402

FAILS: list[str] = []


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:300]}"))
    if not cond:
        FAILS.append(label)


print("\n1. The client document is fetched at the address that was checked\n")
PUBLIC, PRIVATE = "93.184.216.34", "10.0.0.7"


def resolver(answers):
    """getaddrinfo's shape. Each call takes the next answer for that host: a rebinding name answers public first."""
    calls = []

    def resolve(host, port, *a, **k):
        calls.append(host)
        seq = answers[host]
        ip = seq.pop(0) if len(seq) > 1 else seq[0]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]
    return resolve, calls


class FakeResponse:
    def __init__(self, status, body=b"", location=None):
        self.status, self._body, self._loc = status, body, location

    def getheader(self, name):
        return self._loc if name == "Location" else None

    def read(self, n=None):
        return self._body if n is None else self._body[:n]


DIALLED = []


def connector(script):
    """A connection that records the ADDRESS it was told to dial and answers from `script` by host."""
    def connect(u, address, timeout):
        DIALLED.append((u.hostname, address))

        class Conn:
            def request(self, method, path, headers=None):
                self.path = path

            def getresponse(self):
                return script[u.hostname]

            def close(self):
                pass
        return Conn()
    return connect


resolve, calls = resolver({"rebind.example": [PUBLIC, PRIVATE]})
st, body = net._get_pinned("https://rebind.example/client.json", headers={}, timeout=5, max_bytes=1000,
                           resolve=resolve, connect=connector({"rebind.example": FakeResponse(200, b'{"ok":1}')}))
ok("the name is resolved ONCE and the connection dials the address that was checked",
   calls == ["rebind.example"] and DIALLED[-1] == ("rebind.example", PUBLIC) and st == 200, (calls, DIALLED))
resolve, calls = resolver({"evil.example": [PRIVATE]})
DIALLED.clear()
st, _ = net._get_pinned("https://evil.example/c.json", headers={}, timeout=5, max_bytes=1000, resolve=resolve,
                        connect=connector({}))
ok("a name that resolves private is refused, and nothing is dialled", st == 0 and not DIALLED, DIALLED)
resolve, calls = resolver({"a.example": [PUBLIC], "inner.example": [PRIVATE]})
st, _ = net._get_pinned("https://a.example/c.json", headers={}, timeout=5, max_bytes=1000, resolve=resolve,
                        connect=connector({"a.example": FakeResponse(302, location="https://inner.example/x")}))
ok("a redirect to a private address is re-checked and refused, never dialled",
   st == 0 and DIALLED == [("a.example", PUBLIC)], DIALLED)
DIALLED.clear()
resolve, calls = resolver({"a.example": [PUBLIC], "b.example": ["93.184.216.35"]})
st, body = net._get_pinned("https://a.example/c.json", headers={}, timeout=5, max_bytes=1000, resolve=resolve,
                           connect=connector({"a.example": FakeResponse(301, location="https://b.example/d"),
                                              "b.example": FakeResponse(200, b"done")}))
ok("a public redirect is followed, re-resolved and re-pinned", st == 200 and body == "done"
   and DIALLED == [("a.example", PUBLIC), ("b.example", "93.184.216.35")], DIALLED)
resolve, _ = resolver({"loop.example": [PUBLIC]})
st, _ = net._get_pinned("https://loop.example/c", headers={}, timeout=5, max_bytes=1000, resolve=resolve,
                        connect=connector({"loop.example": FakeResponse(302, location="https://loop.example/c")}))
ok("a redirect loop ends, refused", st == 0)
ok("public_addresses is every address or none", net.public_addresses("x", resolver({"x": [PUBLIC]})[0])
   == [ipaddress.ip_address(PUBLIC)] and net.public_addresses("x", resolver({"x": ["127.0.0.1"]})[0]) == [])
SEEN = {}
_get, _pub = net.get_public, net.url_is_public
net.url_is_public = lambda url, resolve=None: True        # the name check passes; what matters is the door asked for
net.get_public = lambda url, **k: SEEN.update(k) or (0, "")
oauth._fetch_document("https://client.example/doc.json")
net.get_public, net.url_is_public = _get, _pub
ok("the sign-in's fetch asks for the pinned door", SEEN.get("pin") is True, SEEN)
from core import key_features  # noqa: E402

c = key_features._Loopback("box.example", 443, timeout=1)
ok("the key-features gate's loopback is the same pinned connection, dialling 127.0.0.1",
   isinstance(c, net.PinnedHTTPSConnection) and c._address == "127.0.0.1" and c.host == "box.example")

print("\n2. Failed fetches are limited per URL; a trusted vendor is never refused\n")
FETCHED = []
_fetch = oauth._fetch_document
oauth._fetch_document = lambda url: FETCHED.append(url) or None
BAD = "https://stranger.example/a.json"
for _ in range(6):
    oauth.client(BAD)
ok("five failures are fetched, the sixth is refused without a fetch", FETCHED.count(BAD) == 5, FETCHED.count(BAD))
oauth.client("https://stranger.example/b.json")
ok("...another URL on the same host is still fetched (per URL, never per host)",
   "https://stranger.example/b.json" in FETCHED)
for _ in range(7):
    oauth.client("https://chatgpt.com/oauth/missing/client.json")
ok("seven failures under chatgpt.com are all fetched: a trusted vendor is never refused",
   FETCHED.count("https://chatgpt.com/oauth/missing/client.json") == 7)
with state.connect() as conn:
    counted = conn.execute("SELECT COUNT(*) FROM oauth_cimd_misses WHERE url LIKE 'https://chatgpt.com/%'").fetchone()[0]
ok("...not even counted", counted == 0, counted)
VENDOR = "https://chatgpt.com/oauth/held/client.json"
with state.connect() as conn:                     # a refusal row from before this rule, or written by hand
    conn.execute("INSERT INTO oauth_cimd_misses (url, misses, first_at, refused_until) VALUES (?,?,?,?)",
                 (VENDOR, 9, datetime.now(timezone.utc).isoformat(),
                  (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()))
oauth.client(VENDOR)
ok("...and a refusal row for a vendor's URL is ignored: it is fetched", VENDOR in FETCHED)
oauth.client("https://chatgpt.com/oauth/real/client.json")
ok("...and its real client is fetched", "https://chatgpt.com/oauth/real/client.json" in FETCHED)
with state.connect() as conn:
    conn.execute("UPDATE oauth_cimd_misses SET refused_until = ? WHERE url = ?",
                 ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), BAD))
oauth.client(BAD)
ok("after its hour, the URL is fetched again", FETCHED.count(BAD) == 6, FETCHED.count(BAD))
SLOW = "https://slow.example/c.json"
for _ in range(4):
    oauth.client(SLOW)
with state.connect() as conn:
    conn.execute("UPDATE oauth_cimd_misses SET first_at = ? WHERE url = ?",
                 ((datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat(), SLOW))
for _ in range(2):
    oauth.client(SLOW)
ok("failures spread past the ten-minute window start a new count, not a refusal", FETCHED.count(SLOW) == 6)
GOOD = "https://good.example/client.json"
DOC = {"client_id": GOOD, "redirect_uris": ["https://good.example/cb"], "token_endpoint_auth_methods_supported": ["none"]}
oauth._fetch_document = lambda url: FETCHED.append(url) or None
for _ in range(4):
    oauth.client(GOOD)
oauth._fetch_document = lambda url: FETCHED.append(url) or (DOC if url == GOOD else None)
ok("a success is a client", (oauth.client(GOOD) or {}).get("client_id") == GOOD)
with state.connect() as conn:
    gone = conn.execute("SELECT 1 FROM oauth_cimd_misses WHERE url = ?", (GOOD,)).fetchone() is None
ok("...and clears its count", gone)
oauth._fetch_document = _fetch

print("\n3. Idle clients are swept; a client with a live seat is kept\n")


def old(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


idle = oauth.register({"client_name": "Idle app", "redirect_uris": ["https://idle.example/cb"]})["client_id"]
fresh = oauth.register({"client_name": "New app", "redirect_uris": ["https://new.example/cb"]})["client_id"]
held = oauth.register({"client_name": "Used app", "redirect_uris": ["https://used.example/cb"]})["client_id"]
verifier = secrets.token_urlsafe(48)
challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
code = oauth.issue_code(client_id=held, redirect_uri="https://used.example/cb", code_challenge=challenge,
                        role="read", label="Used app", user_id=None)
oauth.exchange(code=code, client_id=held, redirect_uri="https://used.example/cb", verifier=verifier)
with state.connect() as conn:
    conn.execute("UPDATE oauth_clients SET created_at = ? WHERE client_id IN (?, ?)", (old(40), idle, held))
    conn.execute("UPDATE oauth_client_use SET last_code_at = ? WHERE client_id = ?", (old(40), held))
oauth.sweep()


def exists(cid):
    with state.connect() as conn:
        return conn.execute("SELECT 1 FROM oauth_clients WHERE client_id = ?", (cid,)).fetchone() is not None


ok("a client nobody approved in 30 days, with no seat, is swept", not exists(idle))
ok("a client registered today is kept", exists(fresh))
ok("an idle client whose seat is live is kept (its connection keeps working)", exists(held))
with state.connect() as conn:
    seat_id = conn.execute("SELECT seat_id FROM oauth_client_seats WHERE client_id = ?", (held,)).fetchone()[0]
seats.revoke(seat_id)
oauth.sweep()
ok("...and swept once that seat is revoked", not exists(held))
with state.connect() as conn:
    left = conn.execute("SELECT COUNT(*) FROM oauth_client_seats WHERE client_id = ?", (held,)).fetchone()[0]
ok("...its bookkeeping goes with it", left == 0)
src = (ROOT / "core" / "watchdog.py").read_text()
ok("the sweep has a caller: the watchdog runs it every pass", "_oauth.sweep()" in src)

print("\n4. The trusted domains\n")
from core.connector import cors  # noqa: E402

ok("exactly chatgpt.com, openai.com and claude.ai: a fourth is a reviewed change",
   cors.TRUSTED_DOMAINS == ("chatgpt.com", "openai.com", "claude.ai"), cors.TRUSTED_DOMAINS)

print("\nALL SIGN-IN HARDENING CHECKS PASS" if not FAILS else f"\n{len(FAILS)} SIGN-IN HARDENING CHECK(S) FAILED")
sys.exit(1 if FAILS else 0)
