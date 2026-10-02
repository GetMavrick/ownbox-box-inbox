"""Apps connected through their own MCP server (core/connections, docs/SCOPE_CONNECTIONS_MCP_FIRST.md phase 1).

Against a real local MCP server speaking Streamable HTTP (a session header, pagination, an answer as JSON and an
answer as a stream of events held open after the answer):

  · an address that isn't public https, or carries a password, is refused with what to fix; a wrong token and an
    app that signs in with its own page are told apart;
  · connecting lists the app's tools for real; the ones the app says only read start on, the rest stay off and
    can't be turned on yet; the address and token live in box_secrets, never in settings;
  · the box's own MCP lists the tools that are on, current as of the request, titled in plain words, with the
    app's own argument schema;
  · a seat that holds read:apps calls one and gets the app's answer as data; one that doesn't can't see or call
    them; an app's own "that failed" stays a tool error in its words;
  · THE PHASE 1 DONE-WHEN, in miniature: a coworker's run seat reads from the connected app and the receipt
    names the call;
  · turning a tool off or disconnecting the app takes it off the box on the next request.

Run: python tests/test_connections.py
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
T = pathlib.Path(tempfile.mkdtemp())
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")

from core import box_secrets, box_settings, net, state  # noqa: E402
from core.connections import client, gateway, store  # noqa: E402
from core.connector import mcp, tools  # noqa: E402
from core.coworkers import hire  # noqa: E402

state.init_db()
FAILS: list[str] = []


def ok(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


# ── a small, real MCP server ─────────────────────────────────────────────────────────────────────────────
TOKEN, SESSION = "good-token-123", "sess-7f3a"
SEEN: list = []
TOOLS_P1 = [
    {"name": "search_notes", "title": "Search notes", "description": "Find notes by words in them.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
     "annotations": {"readOnlyHint": True}},
    {"name": "delete_note", "description": "Delete a note.", "inputSchema": {"type": "object"},
     "annotations": {"destructiveHint": True}},
]
TOOLS_P2 = [
    {"name": "get-note", "description": "One note by id.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}},
     "annotations": {"readOnlyHint": True}},
    {"name": "bad name!", "description": "a name the box can't carry", "annotations": {"readOnlyHint": True}},
    {"name": "export_all", "description": "Export every note."},
]


class App(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, status, body=None, headers=None):
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        data = b"" if body is None else json.dumps(body).encode()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        msg = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        SEEN.append({"path": self.path, "method": msg.get("method"), "session": self.headers.get("Mcp-Session-Id"),
                     "ua": self.headers.get("User-Agent") or "",
                     "version": self.headers.get("MCP-Protocol-Version"), "params": msg.get("params")})
        if self.path == "/oauth":
            return self._send(401, {"error": "unauthorized"}, {
                "WWW-Authenticate": 'Bearer resource_metadata="https://app.example/.well-known/oauth"'})
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self._send(401, {"error": "bad token"})
        rid, method = msg.get("id"), msg.get("method")
        if method == "initialize":
            return self._send(200, {"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                "serverInfo": {"name": "Fake Notes", "version": "1.0"}}}, {"Mcp-Session-Id": SESSION})
        if self.headers.get("Mcp-Session-Id") != SESSION:
            return self._send(400, {"error": "no session"})
        if method == "notifications/initialized":
            return self._send(202)
        if method == "tools/list":
            cursor = (msg.get("params") or {}).get("cursor")
            page = {"tools": TOOLS_P2} if cursor == "p2" else {"tools": TOOLS_P1, "nextCursor": "p2"}
            return self._send(200, {"jsonrpc": "2.0", "id": rid, "result": page})
        if method == "tools/call":
            name = msg["params"]["name"]
            if name == "search_notes":
                # AN ANSWER AS A STREAM OF EVENTS, a progress note first, and the stream held open afterwards.
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                note = {"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 1}}
                answer = {"jsonrpc": "2.0", "id": rid, "result": {"content": [
                    {"type": "text", "text": "Microneedling aftercare: no makeup for 24 hours."},
                    {"type": "image", "data": "aGk=", "mimeType": "image/png"}]}}
                self.wfile.write(f"event: message\ndata: {json.dumps(note)}\n\n".encode())
                self.wfile.write(f"event: message\ndata: {json.dumps(answer)}\n\n".encode())
                self.wfile.flush()
                time.sleep(6)
                return
            return self._send(200, {"jsonrpc": "2.0", "id": rid, "result": {
                "isError": True, "content": [{"type": "text", "text": "No note with id n_404."}]}})
        return self._send(200, {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no such method"}})


srv = ThreadingHTTPServer(("127.0.0.1", 0), App)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"

print("\naddresses —")
for url, words in (("http://example.com/mcp", "https://"), ("https://user:pw@example.com/mcp", "token box"),
                   ("https://127.0.0.1/mcp", "public internet"), ("not a url", "isn't a web address")):
    try:
        client.check_address(url)
        ok(f"{url!r} is refused", False, "accepted")
    except client.ConnectionFailed as e:
        ok(f"{url!r} is refused, saying what to fix", words in str(e), str(e))

# THE SUITE'S SERVER IS LOCAL HTTP. Everything below talks to it, so the public-https door is opened for it only.
real_public = net.url_is_public
net.url_is_public = lambda url, resolve=None: url.startswith(BASE)
client.SCHEMES = ("http", "https")

print("\nconnecting —")
for token, url, words in (("wrong-token", f"{BASE}/mcp", "refused the token"),
                          ("good-token-123", f"{BASE}/oauth", "signs in with its own login page")):
    try:
        store.add("Fake Notes", url, token, by="owner")
        ok(f"{words}: refused", False, "connected")
    except store.Refused as e:
        ok(f"a connection that can't work is refused on the spot: {words}", words in str(e)
           and token not in str(e), str(e))
for name in ("", "x", "Notes & More!"):
    try:
        store.add(name, f"{BASE}/mcp", TOKEN, by="owner")
        ok(f"the name {name!r} is refused", False)
    except store.Refused as e:
        ok(f"the name {name!r} is refused, asking for plain words", "plain words" in str(e))

rec = store.add("Fake Notes", f"{BASE}/mcp", TOKEN, by="owner")
ok("connecting lists the app's tools for real, every page of them",
   [t["name"] for t in rec["tools"]] == ["search_notes", "delete_note", "get-note", "export_all"],
   str(rec["tools"]))
ok("...as a named client: Cloudflare, in front of many apps, refuses Python's default with a 403",
   SEEN and all(x["ua"].startswith("Ownbox/") for x in SEEN), str({x["ua"] for x in SEEN}))
ok("...a session was started and used, with the agreed version",
   any(s["method"] == "tools/list" and s["session"] == SESSION and s["version"] == "2025-06-18" for s in SEEN))
ok("the tools the app says only read start on; one that deletes, and one it says nothing about, stay off",
   rec["enabled"] == ["search_notes", "get-note"], str(rec["enabled"]))
saved = json.dumps(box_settings.get("connections", "apps"))
ok("the address and token are in box_secrets, never in settings",
   TOKEN not in saved and "/mcp" not in saved and TOKEN in box_secrets.get("app_conn_fake_notes"))
try:
    store.add("Fake notes", f"{BASE}/mcp", TOKEN, by="owner")
    ok("the same app twice is refused", False)
except store.Refused as e:
    ok("the same app twice is refused, saying so", "already connected" in str(e), str(e))

print("\nthe box's own MCP —")
READ = {"id": "seat_read", "role": "read", "capabilities": None}
RUN = {"id": "seat_run_shift", "role": "read", "capabilities": ["read:inbox", "read:apps"]}
NOAPPS = {"id": "seat_run_noapps", "role": "read", "capabilities": ["read:inbox"]}
listed = mcp._handle("tools/list", {}, 1, READ)["result"]["tools"]
mine = {t["name"]: t for t in listed if t["name"].startswith("app_")}
ok("the tools that are on are listed with no restart, and only those",
   sorted(mine) == ["app_fake_notes.get-note", "app_fake_notes.search_notes"], str(sorted(mine)))
s = mine.get("app_fake_notes.search_notes", {})
ok("...titled in plain words, with the app's name", s.get("title") == "Search notes in Fake Notes"
   and mine.get("app_fake_notes.get-note", {}).get("title") == "Get-note in Fake Notes"
   and all(tools.plain_title(t["title"]) for t in mine.values()), str([t.get("title") for t in mine.values()]))
ok("...read only, and taking the app's own arguments", s.get("annotations", {}).get("readOnlyHint") is True
   and s.get("inputSchema", {}).get("required") == ["query"], str(s))
ok("a refresh with nothing changed changes nothing", gateway.refresh() is False)
# THE GATEWAY IS THE LOCK, not the screen: a settings row edited by anything else still can't turn on a tool the
# app didn't call read-only.
raw = box_settings.get("connections", "apps")
raw["items"]["fake_notes"]["enabled"].append("delete_note")
box_settings.put("connections", "apps", raw, set_by="not-the-screen")
names = [t["name"] for t in mcp._handle("tools/list", {}, 9, READ)["result"]["tools"]]
ok("a tool the app didn't call read-only never reaches the box, whatever settings say",
   "app_fake_notes.delete_note" not in names and "app_fake_notes.search_notes" in names, str(names))
_titles = sorted(sp["title"] for sp in tools._REGISTRY.values() if sp["machine"] == "app_fake_notes")
ok("...and a refresh keeps every title as it was: an app's own titles never count against it",
   _titles == ["Get-note in Fake Notes", "Search notes in Fake Notes"], str(_titles))
ok("a coworker can be granted it, in plain words", hire._WANTS.get("read:apps") == "read from the apps you connected")

print("\ncalling —")
t0 = time.monotonic()
out = mcp._handle("tools/call", {"name": "app_fake_notes.search_notes", "arguments": {"query": "aftercare"}},
                  2, RUN)["result"]
took = time.monotonic() - t0
got = out.get("structuredContent", {})
ok("a coworker's run seat holding read:apps gets the app's answer as data", not out["isError"]
   and "no makeup for 24 hours" in got.get("text", "") and got.get("app") == "Fake Notes", str(out)[:300])
ok("...the arguments reached the app as given", any(x["method"] == "tools/call" and x["params"]["arguments"]
                                                    == {"query": "aftercare"} for x in SEEN))
ok("...the stream is read to the answer, not to its end (held open 6s)", took < 4, f"{took:.1f}s")
ok("...and what the box can't show yet is named, not dropped", "1 item" in got.get("not_shown", ""), str(got))
with state.connect() as c:
    rows = [dict(r) for r in c.execute("SELECT seat_id, tool, outcome, args_json FROM seat_actions "
                                       "WHERE seat_id='seat_run_shift'")]
ok("THE PHASE 1 DONE-WHEN: the run's receipt names the call", rows and rows[-1]["tool"] ==
   "app_fake_notes.search_notes" and rows[-1]["outcome"] == "ok" and "aftercare" in rows[-1]["args_json"], str(rows))
bad = mcp._handle("tools/call", {"name": "app_fake_notes.get-note", "arguments": {"id": "n_404"}}, 3, RUN)["result"]
ok("the app's own \"that failed\" stays a tool error, in its words",
   bad["isError"] is True and "No note with id n_404" in bad["content"][0]["text"], str(bad)[:200])
ok("a seat without read:apps can't see them", not any(t["name"].startswith("app_") for t in
                                                     mcp._handle("tools/list", {}, 4, NOAPPS)["result"]["tools"]))
payload, status = tools.call("app_fake_notes.search_notes", {"query": "x"}, NOAPPS)
ok("...or call one by name", status == 403, f"{status} {payload}")
payload, status = tools.call("app_fake_notes.search_notes", {"query": "x" * 20_000}, RUN)
ok("arguments too large to send are refused before they leave", status == 400, f"{status}")

print("\nswapping a connection's tools —")


class Watch(dict):
    """The registry, watched: what was ever removed from it."""
    popped: list = []

    def pop(self, key, *default):
        Watch.popped.append(key)
        return super().pop(key, *default)

    def __delitem__(self, key):
        Watch.popped.append(key)
        super().__delitem__(key)


real_registry = tools._REGISTRY
tools._REGISTRY = Watch(real_registry)
try:
    store.set_enabled("fake_notes", ["search_notes"], by="owner")       # get-note goes, search_notes stays
    mcp._handle("tools/list", {}, 7, READ)
    ok("a tool that stays is never taken off the box while another goes (no moment without it)",
       "app_fake_notes.search_notes" not in Watch.popped and "app_fake_notes.get-note" in Watch.popped,
       str(Watch.popped))
    ok("...and it keeps its own title, not a numbered copy of it",
       tools._REGISTRY["app_fake_notes.search_notes"]["title"] == "Search notes in Fake Notes",
       tools._REGISTRY["app_fake_notes.search_notes"]["title"])
finally:
    watched, tools._REGISTRY = tools._REGISTRY, real_registry
    real_registry.clear()
    real_registry.update(watched)

print("\nturning off and disconnecting —")
try:
    store.set_enabled("fake_notes", ["search_notes", "delete_note"], by="owner")
    ok("a tool that changes things can't be turned on yet", False)
except store.Refused as e:
    ok("a tool that changes things can't be turned on yet, and says when", str(e) == store.ACTIONS_LATER)
store.set_enabled("fake_notes", ["search_notes"], by="owner")
names = [t["name"] for t in mcp._handle("tools/list", {}, 5, READ)["result"]["tools"]]
ok("a tool turned off leaves the box on the next request", "app_fake_notes.search_notes" in names
   and "app_fake_notes.get-note" not in names)
ok("disconnecting forgets the token", store.remove("fake_notes", by="owner")
   and box_secrets.get("app_conn_fake_notes") == "")
names = [t["name"] for t in mcp._handle("tools/list", {}, 6, READ)["result"]["tools"]]
ok("...and the app's tools leave the box", not any(n.startswith("app_") for n in names))
payload, status = tools.call("app_fake_notes.search_notes", {"query": "x"}, RUN)
ok("...a call to one is an unknown tool", status == 404, f"{status}")

print("\nthe Data Sources page —")
os.environ["DISPATCH_BEARER_TOKEN"], os.environ["DASH_TOKEN"] = "bearer", "pw"
from core import dash  # noqa: E402
from core.dispatch import app  # noqa: E402

owner = app.test_client()
owner.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
member = app.test_client()
member.set_cookie(dash.COOKIE, dash.new_session(state.add_user("sam@glowmedspa.co", name="Sam", role="member")["id"]))
page = owner.get("/settings/sources")
ok("the owner opens Data Sources, with the form to connect an app", page.status_code == 200
   and "Connect an app" in page.get_data(as_text=True) and "MCP address" in page.get_data(as_text=True))
ok("a member can't", member.get("/settings/sources").status_code in (302, 303, 403))
r = owner.post("/settings/sources", data={"do": "add", "name": "Clinic Notes", "url": f"{BASE}/mcp",
                                          "token": "wrong-token"})
html = r.get_data(as_text=True)
ok("a wrong token is said on the page, the name and address kept, the token not",
   r.status_code == 400 and "refused the token" in html and "Clinic Notes" in html and "wrong-token" not in html)
r = owner.post("/settings/sources", data={"do": "add", "name": "Clinic Notes", "url": f"{BASE}/mcp",
                                          "token": TOKEN})
ok("connecting through the page lands back on it, saying so", r.status_code == 303
   and r.headers["Location"].endswith("/settings/sources?added=clinic_notes"), str(r.headers.get("Location")))
html = owner.get("/settings/sources?added=clinic_notes").get_data(as_text=True)
ok("...the app is listed with its tools: reads ticked, the one that changes things can't be",
   "Clinic Notes" in html and 'value="search_notes" checked' in html and "Changes things in Clinic Notes" in html
   and TOKEN not in html, html[html.find("Clinic Notes"):][:400])
# ONE ROW PER APP IN A TABLE (owner, 2026-10-01: "it needs to be tighter list like in a table format"): the row
# says which app, where, since when and how many tools are on, and opens to the ticks and buttons.
# ONE TABLE FOR EVERY SOURCE since 2026-10-02 (owner, from a preview): the apps and the built-in sources share it.
ok("...as one row of the Data Sources table, its columns headed",
   html.count('<details class="src-row"') == 1
   and all(f"<span>{h}</span>" in html for h in ("Source", "Reads from", "Last read", "Status")))
ok("...open, since it was just connected", '<details class="src-row" id="app-clinic_notes" open>' in html)
ok("...and closed when nothing has just happened to it",
   '<details class="src-row" id="app-clinic_notes">' in owner.get("/settings/sources").get_data(as_text=True))
ok("...and one the app says nothing about is named as such, not called a change",
   "Clinic Notes doesn&#x27;t say whether this only reads" in html or "Clinic Notes doesn't say whether" in html)
import re  # noqa: E402

pills = re.findall(r'<button(?![^>]*class="(?:ghost|danger)")[^>]*>([^<]*)</button>', html)
ok("...with one ink pill on the screen, Connect, however many apps are connected", pills == ["Connect"], str(pills))
r = owner.post("/settings/sources", data={"do": "enable", "app": "clinic_notes", "tool": ["search_notes"]})
ok("turning a tool off is saved", r.status_code == 303 and store.get("clinic_notes")["enabled"] == ["search_notes"])
from core import tiers  # noqa: E402

real_allows, tiers.allows = tiers.allows, (lambda feature: True)      # shifts are a Pro feature
shift_form = owner.get("/settings/shifts/new").get_data(as_text=True)
tiers.allows = real_allows
ok("a shift can now be given the apps, in plain words", "Read from the apps you connected" in shift_form,
   "missing" if "Read from" not in shift_form else "")
r = owner.post("/settings/sources", data={"do": "remove", "app": "clinic_notes"})
ok("disconnecting through the page forgets it", r.status_code == 303 and store.get("clinic_notes") is None)

print("\nwords —")
ok("titles: an app's title and name", gateway.title_for({"name": "x", "title": "Search pages"}, "Notion")
   == "Search pages in Notion")
ok("...a name already naming the app isn't repeated", gateway.title_for({"name": "notion_search"}, "Notion")
   == "Notion search")
ok("...a name that starts with a number still reads as words", tools.plain_title(
    gateway.title_for({"name": "2fa_status"}, "Okta")), gateway.title_for({"name": "2fa_status"}, "Okta"))
long = gateway.title_for({"name": "x", "title": "List every open support ticket assigned to me this week"}, "Zendesk")
ok("...a long one is cut and keeps the app", tools.plain_title(long) and long.endswith("in Zendesk"), long)
net.url_is_public = real_public

srv.shutdown()
print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    sys.exit(1)
print("all passed")
