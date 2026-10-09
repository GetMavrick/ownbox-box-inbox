"""The one outbound request a sandboxed machine can make: the box makes it, to a host the owner approved (D2).

The machine has no network of its own (unit.py), so `m.fetch` is a call through its door, and this is what the
box does with it:

  * https only, port 443, no user:password in the address;
  * the host must be one the owner approved for this machine, by exact name (grants.py);
  * the name is resolved ONCE, every address must be public, and the connection goes to the address that was
    checked (core/net.py's pinned connection, the same door the OAuth sign-in uses). A host that points at the
    box itself or at the cloud's metadata address is refused (T6), and so is one that changes its answer between
    the check and the connect;
  * a redirect is NEVER followed. The machine gets the 3xx and its Location, and may fetch that address only if
    its host is approved too;
  * a body out of at most 1 MB, a body back of at most 4 MB, at most 30 seconds;
  * a key, when named, is attached by the box, only for the host it was entered for, and every plain copy of it in
    the answer is blanked before the machine sees it (D8), so an endpoint that echoes headers cannot hand it back.

Nothing here knows which machine is asking: the door (broker.py) checks the grant, then calls `request`.
"""
from __future__ import annotations

import base64
import html
import re
import ssl
import urllib.parse

from core import net

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
# THE HEADERS A MACHINE MAY SET ARE AN ALLOWLIST (OSDev4's review of #2066). With a denylist, a machine could ask an
# endpoint that echoes its request for the answer compressed (Accept-Encoding) or in slices (Range), and an echoed
# key would come back in a form the blanking below never matches. The box sends `Accept-Encoding: identity` itself.
MACHINE_HEADERS = frozenset({"accept", "accept-language", "authorization", "cache-control", "content-type",
                             "idempotency-key", "if-match", "if-modified-since", "if-none-match",
                             "if-unmodified-since", "user-agent"})
NOT_EVEN_X = frozenset({"x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip",
                        "x-http-method-override", "x-original-url", "x-rewrite-url"})
_HEADER_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,60}$")
# A KEY IS BLANKED IN PIECES TOO: any run of this many of its characters, and its base64 and percent-encoded forms.
KEY_RUN = 12
MAX_BODY_OUT = 1 << 20
MAX_BODY_IN = 4 << 20
MAX_TIMEOUT = 30
MAX_HEADERS = 30
REDACTED = "[the key]"


class FetchRefused(Exception):
    def __init__(self, code: str, error: str):
        super().__init__(error)
        self.code, self.error = code, error


def machine_headers(headers) -> dict:
    """The headers a machine asked to send, checked: names from MACHINE_HEADERS or `X-...`, one line of text each.
    -> them, or FetchRefused("bad_headers"). The same check in-process and in the sandbox."""
    if headers is None:
        return {}
    if not isinstance(headers, dict) or len(headers) > MAX_HEADERS:
        raise FetchRefused("bad_headers", f"headers are a mapping of at most {MAX_HEADERS}")
    out = {}
    for k, v in headers.items():
        low = str(k).lower()
        if not isinstance(k, str) or not _HEADER_NAME.match(k) or not (
                low in MACHINE_HEADERS or (low.startswith("x-") and low not in NOT_EVEN_X)):
            raise FetchRefused("bad_headers", f"a machine may not set the {str(k)[:40]!r} header")
        if not isinstance(v, str) or len(v) > 8192 or any(c in v for c in "\r\n\0"):
            raise FetchRefused("bad_headers", f"the {k} header's value must be one line of text")
        out[k] = v
    return out


def _forms(secret: str) -> list[bytes]:
    raw = secret.encode()
    forms = {raw, urllib.parse.quote(secret, safe="").encode(), base64.b64encode(raw),
             base64.urlsafe_b64encode(raw), base64.b64encode(raw).rstrip(b"="), base64.urlsafe_b64encode(raw).rstrip(b"=")}
    return sorted(forms, key=len, reverse=True)


def _runs(secret: str) -> list[bytes]:
    raw = secret.encode()
    w = min(len(raw), KEY_RUN)
    return [raw[i:i + w] for i in range(len(raw) - w + 1)]


def still_there(data: bytes, secret: str) -> bool:
    """After blanking: does any piece of the key show once the answer is decoded the common ways (percent escapes,
    HTML entities, \\u escapes)? Then the answer is refused whole, rather than guessed at."""
    if not secret or not data:
        return False
    text = data.decode("latin-1")
    views = (urllib.parse.unquote_to_bytes(data), html.unescape(text).encode("latin-1", "replace"),
             re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text).encode("latin-1", "replace"))
    return any(piece in view for view in views for piece in _runs(secret))


def blank(data: bytes, secret: str) -> bytes:
    """`data` with every copy of the key blanked: whole, encoded, or any run of KEY_RUN of its characters."""
    if not secret or not data:
        return data
    spans = []
    for form in _forms(secret):
        w = min(len(form), KEY_RUN)
        for i in range(len(form) - w + 1):
            piece = form[i:i + w]
            at = data.find(piece)
            while at != -1:
                spans.append((at, at + w))
                at = data.find(piece, at + 1)
    if not spans:
        return data
    spans.sort()
    merged = [list(spans[0])]
    for a, b in spans[1:]:
        if a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    out, last = [], 0
    for a, b in merged:
        out.append(data[last:a])
        out.append(REDACTED.encode())
        last = b
    out.append(data[last:])
    return b"".join(out)


def host_of(url: str) -> str:
    """The lower-case host of an https address on the default port, or FetchRefused."""
    try:
        u = urllib.parse.urlsplit(str(url or ""))
        port = u.port
    except ValueError:
        raise FetchRefused("bad_url", "that is not an address the box can read") from None
    if u.scheme != "https":
        raise FetchRefused("https_only", "the box fetches https addresses only")
    if u.username or u.password or "@" in (u.netloc or ""):
        raise FetchRefused("bad_url", "an address may not carry a user name or password: name a key instead")
    if port not in (None, 443):
        raise FetchRefused("bad_url", "the box fetches on the standard https port only")
    if not u.hostname:
        raise FetchRefused("bad_url", "that address has no host")
    return u.hostname.lower().rstrip(".")


def request(url: str, *, method: str = "GET", headers: dict | None = None, body: bytes = b"",
            timeout: float = 20, secret: str = "", resolve=None, connect=None) -> dict:
    """Make the request. -> {"status", "headers": {lower-case name: value}, "body": bytes}. Raises FetchRefused."""
    host = host_of(url)
    method = str(method or "GET").upper()
    if method not in METHODS:
        raise FetchRefused("bad_method", f"the box sends {', '.join(METHODS)} only")
    if len(body) > MAX_BODY_OUT:
        raise FetchRefused("too_large", f"a request body is at most {MAX_BODY_OUT >> 20} MB")
    timeout = max(1.0, min(float(timeout or 20), MAX_TIMEOUT))
    addrs = net.public_addresses(host, resolve)
    if not addrs:
        raise FetchRefused("not_public", f"{host} does not point at a public address")
    u = urllib.parse.urlsplit(url)
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    headers = {**(headers or {}), "Accept-Encoding": "identity"}
    if connect is not None:
        conn = connect(host, str(addrs[0]), timeout)
    else:
        conn = net.PinnedHTTPSConnection(host, str(addrs[0]), 443, timeout=timeout,
                                         context=ssl.create_default_context())
    try:
        conn.request(method, path, body=body or None, headers=headers or {})
        r = conn.getresponse()
        data = r.read(MAX_BODY_IN + 1)
        got = {k.lower(): v for k, v in r.getheaders() if k.lower() != "set-cookie"}
        status = int(r.status)
    except FetchRefused:
        raise
    except Exception as e:                               # noqa: BLE001 — transport, TLS, timeout: a reason, not a crash
        raise FetchRefused("unreachable", f"{type(e).__name__} reaching {host}") from None
    finally:
        conn.close()
    if len(data) > MAX_BODY_IN:
        raise FetchRefused("too_large", f"{host} answered with more than {MAX_BODY_IN >> 20} MB")
    if secret:
        # A KEY WENT OUT, SO ONLY A PLAIN, WHOLE ANSWER COMES BACK: one compressed or in slices could carry it in a
        # form the blanking can't see, whatever the machine asked for.
        if str(got.get("content-encoding") or "identity").strip().lower() not in ("", "identity") or status == 206 \
                or "content-range" in got:
            raise FetchRefused("encoded_answer", f"{host} answered in a form the box can't check for your key, so "
                                                 f"the answer was not passed on")
        data = blank(data, secret)
        got = {k: blank(v.encode("latin-1", "replace"), secret).decode("latin-1") for k, v in got.items()}
        if still_there(data, secret) or any(still_there(v.encode("latin-1", "replace"), secret) for v in got.values()):
            raise FetchRefused("encoded_answer", f"{host} answered with your key in a form the box can't blank, so "
                                                 f"the answer was not passed on")
    return {"status": status, "headers": got, "body": data}
