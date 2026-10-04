"""Speak MCP to an app's own server, as a client: the Streamable HTTP transport (spec 2025-06-18).

ONE SESSION PER USE. `open()` initializes; then `list_tools()` or `call_tool()`. A session is never kept between
uses, so a server that restarted or expired one can't break a coworker's shift. The cost is two small requests,
and a shift is not in a hurry.

PUBLIC HTTPS ONLY. The address is one the owner typed, so every request goes through core/net's door: no private
or loopback address, and no redirects (a token must never follow a redirect somewhere else).

THE ANSWER IS JSON OR A STREAM OF EVENTS, the server's choice per request, said in Content-Type. A stream is read
a line at a time and stops at our answer, so a server that holds the stream open can't hold the box.

Errors are `ConnectionFailed`, with a sentence the owner can act on. The token and the address's path are never
in a message or a log: some apps put their secret in the path.
"""
from __future__ import annotations

import json
import re
import urllib.parse

from core import net
from core.logging import get_logger

log = get_logger(__name__)

PROTOCOL = "2025-06-18"
CLIENT_INFO = {"name": "Ownbox", "version": "1"}
# A NAMED CLIENT, NOT PYTHON'S DEFAULT. Measured 2026-10-01: Cloudflare, in front of Notion's MCP server and many
# others, answers Python's default "Python-urllib/3.x" with a 403 before the app ever sees the request.
USER_AGENT = "Ownbox/1 (+https://ownbox.io)"
SCHEMES = ("https",)          # the suite widens this for its local server; nothing else should
RESOLVE = None                # the suite's resolver; None is the system's
TIMEOUT = 30
MAX_BYTES = 2_000_000
PAGES_MAX = 10                # tools/list pages
TOOLS_MAX = 200
_SESSION = re.compile(r"^[\x21-\x7e]{1,256}$")      # the spec: visible ASCII only


class ConnectionFailed(Exception):
    """Shown to the owner on Data sources. Says what to fix; never carries the token or the path."""


class SignInNeeded(ConnectionFailed):
    """The app signs people in on its own login page (core/connections/oauth.py). `challenge` is its
    WWW-Authenticate header, which says where that login is."""

    def __init__(self, message: str, challenge: str = ""):
        super().__init__(message)
        self.challenge = challenge


class TokenRefused(ConnectionFailed):
    """The app refused the token it was given."""


def host_of(url: str) -> str:
    return urllib.parse.urlsplit(str(url or "")).hostname or "that address"


def check_address(url: str) -> str:
    """The address, or ConnectionFailed saying what's wrong with it. Shape only; reaching it is `open()`."""
    url = str(url or "").strip()
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        parts = None
    if not parts or not parts.hostname or len(url) > 500 or any(ch.isspace() for ch in url):
        raise ConnectionFailed("That isn't a web address. Paste the MCP address from the app's own "
                               "instructions, like https://mcp.example.com/mcp.")
    if parts.scheme not in SCHEMES:
        raise ConnectionFailed("The address has to start with https://, so the token travels encrypted. Use the "
                               "https:// address the app gives you.")
    if parts.username or parts.password:
        raise ConnectionFailed("Put the token in the token box, not in the address.")
    if not net.url_is_public(url, RESOLVE):
        raise ConnectionFailed(f"{parts.hostname} isn't on the public internet, so the box can't reach it. Use the "
                               "app's public MCP address.")
    return url


class _Events:
    """Reads a text/event-stream a line at a time. `feed` says when the answer to our request has arrived."""

    def __init__(self, rid):
        self.rid, self.data, self.messages = rid, [], []

    def feed(self, line: str) -> bool:
        line = line.rstrip("\r\n")
        if line.startswith("data:"):
            value = line[5:]
            self.data.append(value[1:] if value.startswith(" ") else value)
            return False
        if line == "":
            self._flush()
            return self.rid is not None and _answer_in(self.messages, self.rid) is not None
        return False

    def _flush(self):
        if not self.data:
            return
        payload, self.data = "\n".join(self.data), []
        try:
            got = json.loads(payload)
        except ValueError:
            return
        self.messages.extend(m for m in (got if isinstance(got, list) else [got]) if isinstance(m, dict))

    def finish(self) -> list:
        self._flush()
        return self.messages


def _answer_in(messages, rid):
    for m in messages:
        if isinstance(m, dict) and m.get("id") == rid and ("result" in m or "error" in m):
            return m
    return None


def _rpc_error(reply: dict) -> str:
    err = reply.get("error") if isinstance(reply.get("error"), dict) else {}
    return str(err.get("message") or "the app refused the request")[:500]


class Client:
    def __init__(self, url: str, token: str = "", *, timeout: int = TIMEOUT):
        self.url = check_address(url)
        self.host = host_of(self.url)
        self._token = str(token or "").strip()
        self._timeout = timeout
        self.session = ""
        self.protocol = ""            # set by open(): the version the server agreed to
        self.server: dict = {}
        self._next = 0

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
             "User-Agent": USER_AGENT}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        if self.session:
            h["Mcp-Session-Id"] = self.session
        if self.protocol:
            h["MCP-Protocol-Version"] = self.protocol
        return h

    def _post(self, message: dict, rid):
        events = _Events(rid)
        try:
            status, headers, body = net.post_public_raw(
                self.url, json=message, headers=self._headers(), timeout=self._timeout,
                max_bytes=MAX_BYTES, resolve=RESOLVE, until=events.feed)
        except net.PostRefused:
            raise ConnectionFailed(f"Couldn't reach {self.host}. Check the address, and that the app is up.") from None
        if status in (401, 403):
            challenge = headers.get("www-authenticate") or ""
            # A LOGIN PAGE, OR A BAD TOKEN. The spec's 401 names where to sign in; an older server's 401 to a
            # request with no token at all means the same, and oauth.discover looks at its origin for it.
            if "resource_metadata" in challenge or (status == 401 and not self._token):
                raise SignInNeeded(f"{self.host} signs in with its own login page.", challenge)
            raise TokenRefused(f"{self.host} refused the token. Check it's current and allowed to use the "
                               "app's MCP server.")
        if status in (404, 405) and not self.protocol:
            raise ConnectionFailed(f"No MCP server answered at that address on {self.host}. Check the address in "
                                   "the app's MCP instructions.")
        if status == 202 and rid is None:
            return None
        if not 200 <= status < 300:
            raise ConnectionFailed(f"{self.host} answered with an error ({status}). Try again in a minute.")
        return status, headers, body, events

    def _request(self, method: str, params: dict) -> dict:
        self._next += 1
        rid = self._next
        status, headers, body, events = self._post(
            {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}, rid)
        if "text/event-stream" in (headers.get("content-type") or ""):
            messages = events.finish()
        else:
            try:
                got = json.loads(body) if body.strip() else None
            except ValueError:
                got = None
            messages = got if isinstance(got, list) else [got]
        reply = _answer_in(messages, rid)
        if reply is None:
            raise ConnectionFailed(f"{self.host} didn't answer like an MCP server. Check the address.")
        if method == "initialize":
            sid = headers.get("mcp-session-id") or ""
            self.session = sid if _SESSION.match(sid) else ""
        return reply

    def open(self) -> dict:
        """Start a session. Returns what the app says about itself ({"name", "version"}, maybe more)."""
        reply = self._request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                             "clientInfo": CLIENT_INFO})
        result = reply.get("result")
        if "error" in reply or not isinstance(result, dict):
            raise ConnectionFailed(f"{self.host} wouldn't start a session: {_rpc_error(reply)}. Check the "
                                   "app's MCP address, then press Connect again.")
        self.protocol = str(result.get("protocolVersion") or PROTOCOL)[:20]
        info = result.get("serverInfo")
        self.server = info if isinstance(info, dict) else {}
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, None)
        log.info("connections.opened", host=self.host, protocol=self.protocol,
                 server=str(self.server.get("name") or "")[:60])
        return self.server

    def list_tools(self) -> list[dict]:
        out, cursor = [], None
        for _ in range(PAGES_MAX):
            reply = self._request("tools/list", {"cursor": cursor} if cursor else {})
            if "error" in reply:
                raise ConnectionFailed(f"{self.host} wouldn't list its tools: {_rpc_error(reply)}. Check "
                                       "the app's MCP address, then press Connect again.")
            result = reply.get("result") if isinstance(reply.get("result"), dict) else {}
            out.extend(t for t in (result.get("tools") or []) if isinstance(t, dict))
            cursor = result.get("nextCursor")
            if not cursor or len(out) >= TOOLS_MAX:
                break
        return out[:TOOLS_MAX]

    def call_tool(self, name: str, arguments: dict | None) -> dict:
        """The app's own result ({"content": [...], "isError": bool, ...}). A refusal comes back as a result
        with isError, in the app's words, so the coworker can correct itself."""
        reply = self._request("tools/call", {"name": name, "arguments": arguments or {}})
        if "error" in reply:
            return {"isError": True, "content": [{"type": "text", "text": _rpc_error(reply)}]}
        result = reply.get("result")
        if not isinstance(result, dict):
            return {"isError": True, "content": [{"type": "text", "text": f"{self.host} sent no result"}]}
        return result
