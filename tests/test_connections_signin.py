"""Connecting an app that signs people in on its own page (core/connections/oauth.py, MCP authorization).

Against a real local app: an MCP server that answers 401 naming its login, and an authorization server with
metadata, registration, PKCE and refresh tokens that are good once.

  · Connect on Data Sources sends the owner to the app's own login page: the box registered itself first, with its
    own callback, and asks with PKCE, a state, and the MCP server named as the resource;
  · back on the callback, the code becomes tokens and the app is connected, with its tools listed using them; the
    tokens are in box_secrets, never in settings; the same callback twice is refused;
  · a token near its end is refreshed before a call, and the app's new refresh token is kept; a token the app
    stopped taking is refreshed once and the call asked again; an app that signed the box out says so, in words;
  · two workers needing a fresh token at once refresh it once;
  · saying no on the app's page, a state from nowhere, a sign-in left too long, and a login without PKCE all end
    with a sentence, never a half-connected app.

Run: python tests/test_connections_signin.py
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
T = pathlib.Path(tempfile.mkdtemp())
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")
os.environ["DISPATCH_BEARER_TOKEN"], os.environ["DASH_TOKEN"] = "bearer", "pw"

from core import box_secrets, box_settings, net, state  # noqa: E402
from core.connections import client, oauth, store  # noqa: E402
from core.connector import mcp  # noqa: E402

state.init_db()
from core import dash  # noqa: E402
from core.dispatch import app  # noqa: E402

FAILS: list[str] = []


def ok(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


# ── a small, real app: an MCP server and the authorization server that signs people in to it ─────────────
A = {"codes": {}, "access": set(), "refresh": set(), "refreshes": 0, "registered": [], "token_forms": []}
lock = threading.Lock()
TOOLS = [{"name": "search_notes", "title": "Search notes", "inputSchema": {"type": "object"},
          "annotations": {"readOnlyHint": True}}]


class App(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, status, body=None, headers=None):
        data = b"" if body is None else json.dumps(body).encode()
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/.well-known/oauth-protected-resource/notes-mcp":
            return self._send(200, {"resource": f"{BASE}/notes-mcp", "authorization_servers": [f"{BASE}/auth"],
                                    "scopes_supported": ["notes.read"]})
        if self.path == "/.well-known/oauth-protected-resource/evil-mcp":
            # A HOSTILE ADDRESS: a real login (this suite's authorization server) and ANOTHER service's resource.
            return self._send(200, {"resource": "https://mcp.realapp.example/mcp",
                                    "authorization_servers": [f"{BASE}/auth"]})
        if self.path == "/.well-known/oauth-protected-resource/nopkce-mcp":
            return self._send(200, {"resource": f"{BASE}/nopkce-mcp", "authorization_servers": [f"{BASE}/old"]})
        if self.path == "/.well-known/oauth-authorization-server/auth":
            return self._send(200, {"issuer": f"{BASE}/auth", "authorization_endpoint": f"{BASE}/auth/authorize",
                                    "token_endpoint": f"{BASE}/auth/token",
                                    "registration_endpoint": f"{BASE}/auth/register",
                                    "code_challenge_methods_supported": ["S256"]})
        if self.path == "/.well-known/oauth-authorization-server/old":
            return self._send(200, {"issuer": f"{BASE}/old", "authorization_endpoint": f"{BASE}/old/authorize",
                                    "token_endpoint": f"{BASE}/old/token", "registration_endpoint": f"{BASE}/old/r"})
        return self._send(404, {"error": "not here"})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path == "/auth/register":
            body = json.loads(raw)
            A["registered"].append(dict(body, ua=self.headers.get("User-Agent") or ""))
            return self._send(201, {"client_id": "client-ownbox-1", "redirect_uris": body["redirect_uris"]})
        if self.path == "/auth/token":
            form = dict(urllib.parse.parse_qsl(raw.decode()))
            A["token_forms"].append(form)
            with lock:
                if form.get("grant_type") == "authorization_code":
                    want = A["codes"].pop(form.get("code"), None)
                    proof = base64.urlsafe_b64encode(hashlib.sha256(form.get("code_verifier", "").encode())
                                                     .digest()).decode().rstrip("=")
                    if (not want or want["client_id"] != form.get("client_id") or proof != want["challenge"]
                            or want["redirect_uri"] != form.get("redirect_uri")
                            or form.get("resource") != f"{BASE}/notes-mcp"):
                        return self._send(400, {"error": "invalid_grant"})
                    return self._send(200, self._issue(lasts=1))
                if form.get("grant_type") == "refresh_token":
                    if form.get("refresh_token") not in A["refresh"]:
                        return self._send(400, {"error": "invalid_grant"})
                    A["refresh"].discard(form["refresh_token"])            # good once
                    A["refreshes"] += 1
                    time.sleep(0.3)
                    return self._send(200, self._issue(lasts=3600))
            return self._send(400, {"error": "unsupported_grant_type"})
        if self.path in ("/notes-mcp", "/nopkce-mcp", "/evil-mcp"):
            token = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
            if token not in A["access"] or self.path in ("/nopkce-mcp", "/evil-mcp"):
                meta = f"{BASE}/.well-known/oauth-protected-resource{self.path}"
                return self._send(401, {"error": "unauthorized"},
                                  {"WWW-Authenticate": f'Bearer resource_metadata="{meta}", scope="notes.read"'})
            msg = json.loads(raw or b"{}")
            rid, method = msg.get("id"), msg.get("method")
            if method == "initialize":
                return self._send(200, {"jsonrpc": "2.0", "id": rid, "result": {
                    "protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "Notes"}}})
            if method == "notifications/initialized":
                return self._send(202)
            if method == "tools/list":
                return self._send(200, {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
            if method == "tools/call":
                return self._send(200, {"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": f"3 notes match, read with {token}"}]}})
        return self._send(404, {"error": "not here"})

    @staticmethod
    def _issue(lasts):
        n = len(A["access"]) + A["refreshes"] + 1
        access, refresh = f"tok-{n}-{time.time_ns()}", f"ref-{n}-{time.time_ns()}"
        A["access"].add(access)
        A["refresh"].add(refresh)
        return {"access_token": access, "refresh_token": refresh, "token_type": "Bearer", "expires_in": lasts}


srv = ThreadingHTTPServer(("127.0.0.1", 0), App)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"
net.url_is_public = lambda url, resolve=None: str(url).startswith(BASE)      # the suite's app is local http
client.SCHEMES = ("http", "https")


def login(authorize_url: str) -> tuple[str, str]:
    """What the app's login page does when the owner signs in: a code bound to what the box asked for."""
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(authorize_url).query))
    code = f"code-{time.time_ns()}"
    A["codes"][code] = {"client_id": q["client_id"], "challenge": q["code_challenge"],
                        "redirect_uri": q["redirect_uri"]}
    return code, q["state"]


owner = app.test_client()
owner.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
RUN = {"id": "seat_run_shift", "role": "read", "capabilities": ["read:apps"]}

print("\nConnect goes to the app's own login —")
r = owner.post("/settings/sources", data={"do": "add", "name": "Signed Notes", "url": f"{BASE}/notes-mcp"})
go = r.headers.get("Location") or ""
q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(go).query))
ok("Connect sends the owner to the app's login page", r.status_code == 303 and go.startswith(f"{BASE}/auth/authorize?"),
   f"{r.status_code} {go[:120]}")
ok("...the box registered itself first, with its own callback",
   A["registered"] and A["registered"][-1]["redirect_uris"] == ["https://localhost/settings/sources/callback"]
   and A["registered"][-1]["token_endpoint_auth_method"] == "none" and q.get("client_id") == "client-ownbox-1"
   and A["registered"][-1]["ua"].startswith("Ownbox/"),
   str(A["registered"][-1:]))
ok("...and asks with PKCE, a state, the server as the resource, and the scope the app named",
   q.get("code_challenge_method") == "S256" and len(q.get("code_challenge", "")) >= 43 and len(q.get("state", "")) >= 20
   and q.get("resource") == f"{BASE}/notes-mcp" and q.get("scope") == "notes.read"
   and q.get("redirect_uri") == "https://localhost/settings/sources/callback", str(q))

print("\nback from the login —")
code, st = login(go)
r = owner.get(f"/settings/sources/callback?code={code}&state={st}")
ok("the code becomes tokens and the app is connected", r.status_code == 303
   and r.headers["Location"].endswith("/settings/sources?added=signed_notes"), f"{r.status_code} {r.headers.get('Location')}")
conn = store.secret("signed_notes")
ok("...the PKCE verifier proved itself to the app (it checked the hash)", A["token_forms"][-1].get("code_verifier"))
ok("...its tools were listed with the new token, and the read one is on",
   store.get("signed_notes") and store.get("signed_notes")["enabled"] == ["search_notes"])
ok("...the tokens are in box_secrets, never in settings", conn.get("refresh") and conn.get("token")
   and conn["token"] not in json.dumps(box_settings.get("connections", "apps"))
   and conn["refresh"] not in json.dumps(box_settings.get("connections", "apps")))
r = owner.get(f"/settings/sources/callback?code={code}&state={st}")
ok("the same callback twice is refused, in words", r.status_code == 400
   and "already used" in r.get_data(as_text=True))

print("\nkeeping it fresh —")
first_refresh = conn["refresh"]
out = mcp._handle("tools/call", {"name": "app_signed_notes.search_notes", "arguments": {}}, 1, RUN)["result"]
ok("a token near its end is refreshed before the call, and the call works", not out["isError"]
   and A["refreshes"] == 1 and "3 notes match" in out["structuredContent"]["text"], str(out)[:200])
ok("...the app's new refresh token is kept (the old one was good once)",
   store.secret("signed_notes")["refresh"] not in ("", first_refresh))
A["access"].clear()                                         # the app stops taking the token it gave
out = mcp._handle("tools/call", {"name": "app_signed_notes.search_notes", "arguments": {}}, 2, RUN)["result"]
ok("a token the app stopped taking is refreshed once and the call asked again", not out["isError"]
   and A["refreshes"] == 2, f"{A['refreshes']} {str(out)[:160]}")
c = store.secret("signed_notes")
c["expires_at"] = int(time.time()) - 5
store.save_secret("signed_notes", c)
got = []
threads = [threading.Thread(target=lambda: got.append(oauth.token_for("signed_notes", store.secret("signed_notes"))))
           for _ in range(2)]
[t.start() for t in threads]
[t.join() for t in threads]
ok("two workers needing a fresh token at once refresh it once, and both use it",
   A["refreshes"] == 3 and len(set(got)) == 1 and got[0] in A["access"], f"{A['refreshes']} {got}")
A["access"].clear()
A["refresh"].clear()                                        # the app signs the box out entirely
out = mcp._handle("tools/call", {"name": "app_signed_notes.search_notes", "arguments": {}}, 3, RUN)["result"]
ok("an app that signed the box out says so, in words", out["isError"]
   and "signed the box out" in out["structuredContent"]["text"], str(out)[:200])

print("\nendings that connect nothing —")
r = owner.post("/settings/sources", data={"do": "add", "name": "Other Notes", "url": f"{BASE}/notes-mcp"})
r = owner.get("/settings/sources/callback?error=access_denied&state=x")
ok("saying no on the app's page connects nothing, and says so", r.status_code == 400
   and "you chose not to allow it" in r.get_data(as_text=True) and store.get("other_notes") is None
   and box_secrets.get(oauth.PENDING) == "")
r = owner.post("/settings/sources", data={"do": "add", "name": "Third Notes", "url": f"{BASE}/notes-mcp"})
code, st = login(r.headers["Location"])
r = owner.get(f"/settings/sources/callback?code={code}&state=made-up")
ok("a state that isn't this sign-in's is refused, while one is under way", r.status_code == 400
   and "started from this box" in r.get_data(as_text=True) and store.get("third_notes") is None)
raw = box_secrets.get(oauth.PENDING)
r = owner.get(f"/settings/sources/callback?code={code}&state={st}")
ok("...and the real one still finishes", r.status_code == 303 and store.get("third_notes") is not None)
real_get = box_secrets.get
box_secrets.get = lambda name: raw if name == oauth.PENDING else real_get(name)   # read before the first took it
try:
    oauth.finish(code, st, by="owner")
    ok("two callbacks at once: the second is refused", False, "it finished too")
except oauth.SignInFailed as e:
    ok("two callbacks at once: the one that read the sign-in before the other took it is refused, in words",
       "already used" in str(e), str(e))
finally:
    box_secrets.get = real_get
stale = store.secret("third_notes")
stale["expires_at"] = int(time.time()) - 5
store.remove("third_notes", by="owner")
try:
    oauth.token_for("third_notes", stale)
    ok("a token is never refreshed for an app disconnected meanwhile", False, "it refreshed")
except oauth.SignInFailed as e:
    ok("a token is never refreshed for an app disconnected meanwhile, and says so", "disconnected" in str(e))
ok("...and no credential is written back for it", box_secrets.get(store.SECRET + "third_notes") == "")
r = owner.post("/settings/sources", data={"do": "add", "name": "Slow Notes", "url": f"{BASE}/notes-mcp"})
code, st = login(r.headers["Location"])
pending = oauth._unpack(box_secrets.get(oauth.PENDING))
pending["at"] -= oauth.PENDING_TTL_S + 5
box_secrets.put(oauth.PENDING, oauth._pack(pending))
r = owner.get(f"/settings/sources/callback?code={code}&state={st}")
ok("a sign-in left too long is refused", r.status_code == 400 and "took too long" in r.get_data(as_text=True)
   and store.get("slow_notes") is None)
r = owner.post("/settings/sources", data={"do": "add", "name": "Old Notes", "url": f"{BASE}/nopkce-mcp"})
ok("a login without PKCE is refused before anyone is sent there", r.status_code == 400
   and "PKCE" in r.get_data(as_text=True) and not (r.headers.get("Location") or ""), r.get_data(as_text=True)[-300:])

print("\nthe metadata must be about the address typed —")
before = len(A["registered"])
r = owner.post("/settings/sources", data={"do": "add", "name": "Lookalike", "url": f"{BASE}/evil-mcp"})
html = r.get_data(as_text=True)
ok("metadata naming another service's address is refused before the box registers anywhere, in words",
   r.status_code == 400 and "about a different address" in html and "mcp.realapp.example" in html
   and len(A["registered"]) == before and not r.headers.get("Location") and box_secrets.get(oauth.PENDING) == "",
   html[-300:])
same = oauth.same_resource
ok("...the same address, or its origin, passes", same("https://mcp.app.example/mcp", "https://mcp.app.example/mcp")
   and same("https://mcp.app.example", "https://mcp.app.example/sse")
   and same("https://mcp.app.example/", "https://mcp.app.example/v1/mcp")
   and same("https://mcp.app.example:443/mcp", "https://mcp.app.example/mcp"))
ok("...another host, scheme or port, a sibling path, or nothing at all doesn't",
   not any(same(r_, "https://mcp.app.example/mcp") for r_ in (
       "https://mcp.other.example/mcp", "http://mcp.app.example/mcp", "https://mcp.app.example:8443/mcp",
       "https://mcp.app.example/mc", "https://mcp.app.example/other", "https://u:p@mcp.app.example/mcp",
       "https://mcp.app.example/mcp#x", "", "not a url")))

srv.shutdown()
print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    sys.exit(1)
print("all passed")
