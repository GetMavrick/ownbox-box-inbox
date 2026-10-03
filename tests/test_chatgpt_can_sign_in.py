"""ChatGPT signs in to the box with OAuth, the way OpenAI documents it (developers.openai.com/apps-sdk/build/auth).

Owner, 2026-10-02: "nobody wants to enter an API key because that's really scary because it could get really
expensive." So the way a buyer connects ChatGPT is OAuth, with no key, and this walks ChatGPT's own flow end to end:

  · it asks /mcp with no token, gets 401 and a WWW-Authenticate naming the resource metadata;
  · it reads both well-known documents and finds DCR, S256 and an auth method it supports (`none`);
  · it registers itself (DCR) with its own redirect, asking for refresh_token too, as a public client;
  · it sends the person to /oauth/authorize with PKCE, a `scope` and the RFC 8707 `resource` it always appends;
  · the owner approves; it exchanges the code (again with `resource`) and gets a bearer token;
  · with that token it initializes and lists the box's tools.

Run: python tests/test_chatgpt_can_sign_in.py
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import sys
import tempfile
from urllib.parse import parse_qs, urlencode, urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "chatgpt_oauth.db")
os.environ.setdefault("DISPATCH_BEARER_TOKEN", "bearer")

from core import state  # noqa: E402

state.init_db()

from core import dash  # noqa: E402
from core.dispatch import app  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -- {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


ROOT = "http://localhost"
RESOURCE = f"{ROOT}/mcp"
anon = app.test_client()


def owner():
    c = app.test_client()
    c.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]), domain="localhost")
    return c


def rpc(c, method, params=None, token=None, rid=1):
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return c.post("/mcp", json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}, headers=h)


print("\n— discovery, as ChatGPT does it —")
r = rpc(anon, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                             "clientInfo": {"name": "openai-mcp", "version": "1.0.0"}})
www = r.headers.get("WWW-Authenticate", "")
ok("no token: 401 naming the resource metadata", r.status_code == 401 and "resource_metadata=" in www, (r.status_code, www))
prm = anon.get("/.well-known/oauth-protected-resource").get_json() or {}
ok("protected resource: resource and authorization_servers", prm.get("resource", "").endswith("/mcp")
   and prm.get("authorization_servers"), prm)
asm = anon.get("/.well-known/oauth-authorization-server").get_json() or {}
ok("authorization server: issuer, endpoints, DCR", all(asm.get(k) for k in (
    "issuer", "authorization_endpoint", "token_endpoint", "registration_endpoint")), asm)
ok("...S256, which ChatGPT requires", "S256" in (asm.get("code_challenge_methods_supported") or []), asm)
ok("...and an auth method ChatGPT supports (none)", "none" in (asm.get("token_endpoint_auth_methods_supported") or []),
   asm)

for redirect in ("https://chatgpt.com/connector/oauth/cb_7f3a9", "https://chatgpt.com/connector_platform_oauth_redirect"):
    print(f"\n— ChatGPT signs in, redirect {redirect.split('chatgpt.com')[1]} —")
    reg = anon.post("/oauth/register", json={
        "client_name": "ChatGPT", "redirect_uris": [redirect], "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"], "token_endpoint_auth_method": "none"})
    body = reg.get_json() or {}
    ok("ChatGPT registers itself (DCR)", reg.status_code in (200, 201) and body.get("client_id"), (reg.status_code, body))
    ok("...as a public client, which ChatGPT supports", body.get("token_endpoint_auth_method") == "none", body)
    cid = body.get("client_id", "")

    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    q = {"response_type": "code", "client_id": cid, "redirect_uri": redirect, "state": "st_42",
         "code_challenge": challenge, "code_challenge_method": "S256", "scope": "read act", "resource": RESOURCE}
    page = owner().get("/oauth/authorize?" + urlencode(q))
    ok("the approve screen opens for ChatGPT's request, resource and all", page.status_code == 200
       and "allow" in page.get_data(as_text=True).lower(), page.status_code)
    back = owner().post("/oauth/authorize?" + urlencode(q), data={**q, "decision": "allow"})
    loc = back.headers.get("Location", "")
    got = parse_qs(urlsplit(loc).query)
    ok("approving sends ChatGPT back to its own redirect with a code and its state", back.status_code in (302, 303)
       and loc.startswith(redirect) and got.get("code") and got.get("state") == ["st_42"], (back.status_code, loc[:120]))

    tok = anon.post("/oauth/token", data={"grant_type": "authorization_code", "code": (got.get("code") or [""])[0],
                                          "redirect_uri": redirect, "client_id": cid, "code_verifier": verifier,
                                          "resource": RESOURCE})
    tb = tok.get_json() or {}
    ok("the code becomes a bearer token", tok.status_code == 200 and tb.get("access_token")
       and tb.get("token_type") == "Bearer", (tok.status_code, {k: v for k, v in tb.items() if k != "access_token"}))
    at = tb.get("access_token", "")

    r = rpc(anon, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "openai-mcp", "version": "1.0.0"}}, token=at)
    ok("with it, ChatGPT initializes", r.status_code == 200 and (r.get_json() or {}).get("result"), r.status_code)
    r = rpc(anon, "tools/list", token=at, rid=2)
    names = [t["name"] for t in ((r.get_json() or {}).get("result") or {}).get("tools", [])]
    ok("...and sees the box's tools", r.status_code == 200 and "core.health" in names, names[:5])
    listed = ((r.get_json() or {}).get("result") or {}).get("tools", [])
    ok("...each saying it needs the owner's sign-in, as ChatGPT reads it (securitySchemes oauth2)",
       listed and all({"type": "oauth2", "scopes": []} in (t.get("securitySchemes") or []) for t in listed),
       [t.get("securitySchemes") for t in listed[:2]])
    wrong = anon.post("/oauth/token", data={"grant_type": "authorization_code", "code": (got.get("code") or [""])[0],
                                            "redirect_uri": redirect, "client_id": cid, "code_verifier": verifier})
    ok("the same code never works twice", wrong.status_code == 400, wrong.status_code)

print("\n— ChatGPT names itself by a URL (Client ID Metadata Document), its preferred way —")
from core.connector import oauth  # noqa: E402
asm = anon.get("/.well-known/oauth-authorization-server").get_json() or {}
ok("the box says it accepts a client named by its document", asm.get("client_id_metadata_document_supported") is True,
   asm)
# CHATGPT'S REAL DOCUMENT, AS OPENAI PUBLISHES IT (developers.openai.com/apps-sdk/build/auth, 2026-10-03): its URL
# is its client_id, and it carries BOTH auth fields, the legacy singular one preferring private_key_jwt. A stand-in
# with "none" alone passed while the box refused the real ChatGPT (OSDev4's review of #1853).
CID = "https://chatgpt.com/oauth/client.json"
REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect"
JWT_ONLY = "https://jwt-only.example/client.json"
DOCS = {CID: {"client_id": CID, "client_name": "ChatGPT", "redirect_uris": [REDIRECT],
              "token_endpoint_auth_methods_supported": ["none", "private_key_jwt"],
              "token_endpoint_auth_method": "private_key_jwt",
              "grant_types": ["authorization_code", "refresh_token"]},
        JWT_ONLY: {"client_id": JWT_ONLY, "redirect_uris": ["https://jwt-only.example/cb"],
                   "token_endpoint_auth_methods_supported": ["private_key_jwt"],
                   "token_endpoint_auth_method": "private_key_jwt"},
        "https://evil.example/doc.json": {"client_id": "https://chatgpt.com/oauth/client.json",
                                          "redirect_uris": ["https://evil.example/cb"]}}
_real_fetch = oauth._fetch_document
oauth._fetch_document = lambda url: DOCS.get(url)          # the network stays out of the suite
verifier = secrets.token_urlsafe(48)
challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
q = {"response_type": "code", "client_id": CID, "redirect_uri": REDIRECT, "state": "cimd",
     "code_challenge": challenge, "code_challenge_method": "S256", "scope": "read act", "resource": RESOURCE}
page = owner().get("/oauth/authorize?" + urlencode(q))
ok("no registration step: the approve screen opens for a URL client_id", page.status_code == 200, page.status_code)
back = owner().post("/oauth/authorize?" + urlencode(q), data={**q, "decision": "allow"})
got = parse_qs(urlsplit(back.headers.get("Location", "")).query)
tok = anon.post("/oauth/token", data={"grant_type": "authorization_code", "code": (got.get("code") or [""])[0],
                                      "redirect_uri": REDIRECT, "client_id": CID, "code_verifier": verifier,
                                      "resource": RESOURCE})
at = (tok.get_json() or {}).get("access_token", "")
ok("...and the code becomes a token that lists the tools", at and rpc(anon, "tools/list", token=at, rid=3).status_code
   == 200, tok.status_code)
bad = owner().get("/oauth/authorize?" + urlencode({**q, "client_id": "https://evil.example/doc.json",
                                                  "redirect_uri": "https://evil.example/cb"}))
ok("a document that names a different client is refused", bad.status_code == 400, bad.status_code)
bad = owner().get("/oauth/authorize?" + urlencode({**q, "redirect_uri": "https://evil.example/cb"}))
ok("a redirect the document doesn't list is refused", bad.status_code == 400, bad.status_code)
ok("a client that can only sign with a key (no 'none') is refused: our metadata offers no other way",
   oauth.client(JWT_ONLY, redirect_uri="https://jwt-only.example/cb") is None)
oauth._fetch_document = _real_fetch
for u in ("http://chatgpt.com/x.json", "https://127.0.0.1/x.json", "https://localhost/x.json",
          "https://10.0.0.5/x.json", "https://chatgpt.com:8443/x.json", "https://u:p@chatgpt.com/x.json"):
    ok(f"never fetched (SSRF): {u}", oauth._fetch_document(u) is None)

print()
if _failed:
    print(f"{_failed} FAILED")
    sys.exit(1)
print("all passed")
