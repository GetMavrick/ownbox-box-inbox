"""The AI apps' own web pages may reach the connector and its sign-in, and nothing else may.

MEASURED ON THE OWNER'S BOX, 2026-10-03: ChatGPT's "New custom plugin" screen said "OAuth setup is unavailable in
this environment" and kept Create greyed out, while the box answered every OAuth document correctly to a server
(curl, Claude's connector). The difference is WHERE the asking happens. ChatGPT checks a server from its own web
page, so its browser sends `Origin: https://chatgpt.com` and a CORS preflight first. The box answered the preflight
on /mcp with 401, sent no Access-Control-* header anywhere, and `mcp._origin_ok` refused every cross-origin request.
To ChatGPT's page every probe failed, which it reports as "OAuth unavailable". Owner: "their system can sense if
oauth is available and it's not."

THE ALLOWLIST IS THE SECURITY. The MCP spec requires refusing cross-origin requests to stop DNS rebinding: a page a
buyer visits must not drive their box through their browser. Naming the AI apps' own origins keeps that true for
every other site. Nothing here carries cookies (no Allow-Credentials), the connector authenticates by bearer token,
and /oauth/register and the metadata are public by design, so the named apps get exactly what a server already gets.
"""
from __future__ import annotations

# The AI apps a buyer connects their box from, by the domains their own pages are served from. Owner, 2026-10-03:
# "Make sure to add openai.com as well. Because there's two areas that you can add an MCP… they use both domains."
# A domain covers itself and its subdomains (chatgpt.com, chat.openai.com, platform.openai.com), https only, default
# port only. Those domains are the vendors' own; no other site can send their Origin.
TRUSTED_DOMAINS = ("chatgpt.com", "openai.com", "claude.ai")

# The connector and its sign-in, which those pages ask from the browser. Matched exactly, never by prefix.
PATHS = frozenset({
    "/mcp",                                            # the address a buyer pastes; the long form is for servers
    "/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp",
    "/.well-known/oauth-authorization-server", "/.well-known/oauth-authorization-server/mcp",
    "/.well-known/openid-configuration",
    "/oauth/register", "/oauth/token",
})

_ALLOW_HEADERS = "Authorization, Content-Type, Accept, Mcp-Protocol-Version, Mcp-Session-Id, Last-Event-ID"
# The browser hides response headers from the page unless named: WWW-Authenticate is how the page finds sign-in.
_EXPOSE_HEADERS = "WWW-Authenticate, Mcp-Session-Id, Mcp-Protocol-Version"


def trusted(origin: str | None) -> bool:
    """An https Origin whose host is one of TRUSTED_DOMAINS or under one, with no port and no path."""
    o = (origin or "").strip().rstrip("/")
    if not o.startswith("https://"):
        return False
    host = o[len("https://"):].lower()
    if not host or any(c in host for c in "/:@?#"):
        return False
    return any(host == d or host.endswith("." + d) for d in TRUSTED_DOMAINS)


def applies(path: str, origin: str | None) -> bool:
    return path in PATHS and trusted(origin)


def headers(origin: str) -> dict:
    """The response headers for a trusted origin. Echoed, never `*`, so `Vary: Origin` keeps caches honest."""
    return {"Access-Control-Allow-Origin": origin.rstrip("/"),
            "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": _ALLOW_HEADERS,
            "Access-Control-Expose-Headers": _EXPOSE_HEADERS,
            "Access-Control-Max-Age": "600",
            "Vary": "Origin"}
