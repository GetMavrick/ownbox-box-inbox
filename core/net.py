"""Fetching a page whose address we GUESSED.

A machine that derives a domain from a company name requests, by construction, thousands of
domains it does not control — parked, expired, or registered by somebody who noticed what we do.
`urllib` follows redirects by default, so any one of them can answer

    302 -> http://169.254.169.254/metadata/v1.json      (this droplet's own metadata, and its user-data)
    302 -> http://127.0.0.1:8000/…                      (this box's own dispatch)

and the box makes that request itself. The request is ours; only the destination was theirs. That
is the whole trick, and it costs an attacker one DNS record on a domain our name-guesser will
walk into on its own.

`fetch_public` refuses it: http(s) only, and EVERY hop — the first URL and each redirect — must
resolve to a public address. A refusal returns "" like any other dead guess, because to the
caller a domain it may not read is the same as a domain that is not there.

`post_public` is the same door for a WRITE — OAuth token refresh is a POST, and without it the
first Customer Voice source would have reached for a bare `requests.post`, which nothing checks.
It refuses redirects outright rather than following them: a token endpoint that redirects is a
red flag, and urllib silently downgrades a redirected POST to a GET, which would replay the
request without its body to an address someone else chose.

The same door is what BINARIES go through — `read_public_bytes` and `download_public` at the
bottom of this file. They exist because `open_public` decodes its body to text and caps by
truncating, which is right for a guessed web page and wrong for a video: a truncated .mp4
uploads to the CDN looking finished. Those two refuse instead, and they refuse by SIZE as well
as by address, because an unbounded read is how one response fills a 1-vCPU box's disk.

Not covered, deliberately and stated rather than implied: DNS rebinding. We check the name, then
urllib resolves it again to connect, and a hostile resolver can answer differently the second
time. Closing that means pinning the resolved IP into the connection, which is a different piece
of machinery; this closes the redirect door, which is the one a guessed domain walks through.
"""
import contextlib
import ipaddress
import json as _json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request

from core.logging import get_logger

log = get_logger(__name__)

_ALLOWED_SCHEMES = ("http", "https")
DEFAULT_TIMEOUT = 12
DEFAULT_MAX_BYTES = 400_000


def _addresses(host: str, resolve=None):
    resolve = resolve or socket.getaddrinfo
    try:
        return [ipaddress.ip_address(info[4][0]) for info in resolve(host, None)]
    except Exception:  # noqa: BLE001 — an unresolvable host is simply not public
        return []


def is_public_host(host: str, resolve=None) -> bool:
    """True only when the name resolves and EVERY address it resolves to is public.

    Every address, not the first: a name that answers with a public address and a loopback one
    is a name chosen to be read twice differently."""
    host = (host or "").strip().strip("[]")
    if not host:
        return False
    addrs = _addresses(host, resolve)
    if not addrs:
        return False
    return all(not (a.is_private or a.is_loopback or a.is_link_local or a.is_reserved
                    or a.is_multicast or a.is_unspecified) for a in addrs)


def url_is_public(url: str, resolve=None) -> bool:
    from urllib.parse import urlsplit
    p = urlsplit(url or "")
    if p.scheme not in _ALLOWED_SCHEMES:
        return False
    return is_public_host(p.hostname or "", resolve)


class PublicOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Every hop is checked, not just the first — a redirect chain is a series of requests we make."""

    def __init__(self, resolve=None):
        self._resolve = resolve

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not url_is_public(newurl, self._resolve):
            log.warning("net.redirect_refused", to=str(newurl)[:120], code=code)
            return None                      # urllib treats None as "do not follow"
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_public(url: str, *, timeout: int = DEFAULT_TIMEOUT, max_bytes: int = DEFAULT_MAX_BYTES,
                headers: dict | None = None, resolve=None) -> tuple[str, str]:
    """-> (final_url, body); ("", "") on any refusal or failure.

    The final URL matters to a caller that reads the HOST it ended on — an ATS board is
    identified by where the redirect chain stopped, not by where it started."""
    if not url_is_public(url, resolve):
        log.warning("net.fetch_refused", url=str(url)[:120])
        return "", ""
    opener = urllib.request.build_opener(PublicOnlyRedirects(resolve))
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with opener.open(req, timeout=timeout) as r:
            if not (200 <= getattr(r, "status", 200) < 400):
                return "", ""
            return r.geturl(), r.read(max_bytes).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 — a dead guess is just a dead guess
        return "", ""


def fetch_public(url: str, *, timeout: int = DEFAULT_TIMEOUT, max_bytes: int = DEFAULT_MAX_BYTES,
                 headers: dict | None = None, resolve=None) -> str:
    """GET a public URL, following only redirects that stay public. "" on any refusal or failure."""
    return open_public(url, timeout=timeout, max_bytes=max_bytes, headers=headers, resolve=resolve)[1]


# ── WRITING through the same door ────────────────────────────────────────────────────────
# Added 2026-09-09 for Customer Voice Stage 0 (docs/PLAN_CUSTOMER_VOICE.md §1.5b). Every other
# function in this module was read-shaped, so a caller that had to POST — an OAuth refresh, on
# the hot path for all three Google sources — had no sanctioned door and would have used
# `requests.post`, which has no public-host check, no redirect policy and no cap.

class PostRefused(Exception):
    """The POST was not attempted, or its response was not usable. RAISED rather than returned
    empty: `fetch_public` may answer "" because a dead guess and a refused guess are the same
    thing to a caller reading a stranger's web page — but a refused TOKEN REFRESH is not the
    same as an expired token, and a caller that cannot tell them apart logs the wrong cause and
    retries the wrong thing."""


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A POST does not follow redirects. urllib's default turns a 301/302 into a GET WITHOUT the
    body and re-sends it wherever the far end pointed — the request is still ours, the
    destination is now theirs, and the body silently vanishes. An endpoint that redirects a
    token refresh is misconfigured or hostile; either way the caller should hear about it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PostRefused(f"refused a {code} redirect on a POST to {str(newurl)[:120]}")


def get_public(url: str, *, headers: dict | None = None, timeout: int = DEFAULT_TIMEOUT,
               max_bytes: int = DEFAULT_MAX_BYTES, resolve=None) -> tuple[int, str]:
    """GET a public URL and RETURN THE STATUS with the body. -> (status, body).

    WHY THIS EXISTS BESIDE `fetch_public`, WHICH LOOKS THE SAME. `fetch_public` answers "" for a
    refusal, a dead host, a 404 and a 429 alike — correct for its job, which is reading a page at
    a GUESSED address, where every one of those means the same thing to the caller.

    It is wrong for a KNOWN endpoint. MEASURED 2026-09-09: PageSpeed Insights answered
    `429 Quota exceeded for ... Queries per day` and the Customer Voice rail reported "PageSpeed
    answered without a performance score" — a sentence that sends someone to read their own
    parser for a fault that was a quota. `None` versus `set()` (#966) is the same rule one layer
    down: *asked, nothing is held* and *never got an answer* are different facts, and a door that
    collapses them makes every caller above it guess.

    The public-host check, the redirect policy and the byte cap are identical; only the return
    differs. A transport failure that never reached the far end is status 0, because there is no
    status to report and inventing one would be the same collapse in the other direction.
    """
    if not url_is_public(url, resolve):
        log.warning("net.fetch_refused", url=str(url)[:120])
        return 0, ""
    opener = urllib.request.build_opener(PublicOnlyRedirects(resolve))
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with opener.open(req, timeout=timeout) as r:
            return int(getattr(r, "status", 200)), r.read(max_bytes).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:          # a 4xx/5xx IS an answer, and often says why
        try:
            return int(e.code), e.read(max_bytes).decode("utf-8", "replace")
        except Exception:                        # noqa: BLE001
            return int(e.code), ""
    except Exception as e:                       # noqa: BLE001 — nothing reached the far end
        log.info("net.get_failed", url=str(url)[:80], error=type(e).__name__)
        return 0, ""


def post_public(url: str, *, data: dict | bytes | None = None, json: dict | None = None,
                headers: dict | None = None, timeout: int = DEFAULT_TIMEOUT,
                max_bytes: int = DEFAULT_MAX_BYTES, resolve=None) -> tuple[int, str]:
    """POST to a public http(s) URL. -> (status, body). Raises PostRefused on a non-public host,
    a redirect, or a transport failure; the CALLER decides what a 4xx means.

    `data` as a dict is form-encoded (the OAuth token shape); `json` is sent as a JSON body.
    Exactly one of the two, because a request carrying both is a caller that has not decided.

    The response is capped like every other read here: a token endpoint answering with a
    gigabyte is not a token endpoint, and this box has one vCPU.
    """
    if data is not None and json is not None:
        raise PostRefused("pass data or json, not both")
    if not url_is_public(url, resolve):
        log.warning("net.post_refused", url=str(url)[:120])
        raise PostRefused(f"not a public http(s) URL: {str(url)[:120]}")
    hdrs = dict(headers or {})
    if json is not None:
        body = _json.dumps(json).encode()
        hdrs.setdefault("Content-Type", "application/json")
    elif isinstance(data, dict):
        body = urllib.parse.urlencode(data).encode()
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
    else:
        body = data or b""
    opener = urllib.request.build_opener(_NoRedirects())
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    try:
        with opener.open(req, timeout=timeout) as r:
            return int(getattr(r, "status", 200)), r.read(max_bytes).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:          # a 4xx/5xx IS an answer — hand it back
        try:
            return int(e.code), e.read(max_bytes).decode("utf-8", "replace")
        except Exception:                        # noqa: BLE001
            return int(e.code), ""
    except PostRefused:
        raise
    except Exception as e:                       # noqa: BLE001 — transport, DNS, TLS, timeout
        raise PostRefused(f"{type(e).__name__} posting to {str(url)[:120]}") from e


# ── downloading BYTES through the same door ─────────────────────────────────────────────
# `open_public` decodes to str, so it cannot carry a video or a PNG: measured, the 256 distinct
# byte values come back with 128 of them replaced by U+FFFD — half the file gone, and what is
# left does not round-trip (re-encoding the 256 characters yields 512 bytes). The content
# machine fetches binaries — an approved cut on its way to the CDN, a cover image on its
# way to X — and did it with a bare `requests.get(...).content`: no cap, no public-only
# check. `.content` reads whatever the far end sends, so one oversized or slow-drip
# response fills the disk of a 1-vCPU box, and every such read is a request THIS box makes
# to an address someone else chose.
_CHUNK = 65_536


class TooLarge(Exception):
    """A response was bigger than the caller's cap.

    RAISED, never truncated. `open_public` caps by truncation because half a web page is
    still a readable page; half a video is not — it uploads to the CDN looking finished and
    plays as a broken file. The caller must be able to tell "too big" from "here it is"."""


def _open_checked(url: str, *, timeout: int, headers: dict | None, resolve,
                  max_bytes: int):
    """The public-only opener, plus the DECLARED-length refusal — a server that announces
    a gigabyte is turned away before a byte of it is read. Non-2xx raises (urllib's
    HTTPError). The caller still needs the read cap: Content-Length is optional and a
    hostile one lies."""
    if not url_is_public(url, resolve):
        raise ValueError(f"not a public http(s) URL: {str(url)[:120]}")
    opener = urllib.request.build_opener(PublicOnlyRedirects(resolve))
    r = opener.open(urllib.request.Request(url, headers=headers or {}), timeout=timeout)
    declared = r.headers.get("Content-Length")
    try:
        too_big = declared is not None and int(declared) > max_bytes
    except ValueError:          # unparsable header — the read cap below still holds
        too_big = False
    if too_big:
        r.close()
        raise TooLarge(f"declared {declared} bytes, cap is {max_bytes}")
    return r


def _capped_read(r, max_bytes: int, sink) -> int:
    """Feed `sink` chunk by chunk, raising the moment the total crosses the cap. The cap is
    checked AFTER each chunk is counted and BEFORE it is handed on, so nothing over the cap
    is ever written anywhere."""
    total = 0
    while True:
        chunk = r.read(_CHUNK)
        if not chunk:
            return total
        total += len(chunk)
        if total > max_bytes:
            raise TooLarge(f"response exceeds the {max_bytes}-byte cap")
        sink(chunk)


def _content_type(r) -> str:
    return (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()


def read_public_bytes(url: str, *, max_bytes: int, timeout: int = DEFAULT_TIMEOUT,
                      headers: dict | None = None, resolve=None) -> tuple[bytes, str]:
    """-> (bytes, content_type). For something small enough to hold in memory — an image
    on its way to an upload endpoint. Raises ValueError (not public), TooLarge (over cap),
    or urllib's own errors. Bytes are returned EXACTLY as sent; nothing is decoded."""
    buf = bytearray()
    with _open_checked(url, timeout=timeout, headers=headers, resolve=resolve,
                       max_bytes=max_bytes) as r:
        _capped_read(r, max_bytes, buf.extend)
        return bytes(buf), _content_type(r)


def download_public(url: str, dest: str, *, max_bytes: int, timeout: int = DEFAULT_TIMEOUT,
                    headers: dict | None = None, resolve=None) -> tuple[int, str]:
    """Stream a public URL into the file at `dest` -> (bytes_written, content_type).

    STREAMED, so a 250MB cap costs 64KB of memory, not 250MB — the box renders video on
    one vCPU and cannot hold a file it is only passing through. On ANY failure the partial
    file is REMOVED before the exception leaves: a half-written .mp4 left on disk is one
    `if os.path.exists` away from being uploaded as the finished cut."""
    done = False
    try:
        with _open_checked(url, timeout=timeout, headers=headers, resolve=resolve,
                           max_bytes=max_bytes) as r, open(dest, "wb") as fh:
            n, ctype = _capped_read(r, max_bytes, fh.write), _content_type(r)
        done = True
        return n, ctype
    finally:
        if not done:
            with contextlib.suppress(OSError):
                os.unlink(dest)
