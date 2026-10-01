"""Waiting for you: a coworker asks, a person approves, the box does it once (core/approvals.py, connections).

docs/SCOPE_CONNECTIONS_MCP_FIRST.md phase 1: a shift uses a connected app "with approvals and receipts". Against a
real local MCP server:

  · a tool that changes things is never simply on: ticked on Data Sources, a coworker may only ASK for it;
  · asking writes one proposal and tells the owner's phone once; the app is not called; asking again is the same one;
  · only a seat that may draft for approval (act, write:proposals) sees the tool; a read-only key never does;
  · Waiting for you shows exactly what will run, who asked and until when; the Dashboard says something waits;
  · approve runs it once, with exactly the arguments shown, and shows what the app said; a second approve, a
    decline, an expired proposal, a disconnected app and a forged id never call the app;
  · the ask itself is on the coworker's receipt;
  · only the owner decides.

Run: python tests/test_approvals.py
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
T = pathlib.Path(tempfile.mkdtemp())
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")
os.environ["DISPATCH_BEARER_TOKEN"], os.environ["DASH_TOKEN"] = "bearer", "pw"

from core import approvals, net, push, state  # noqa: E402
from core.connections import client, store  # noqa: E402
from core.connector import mcp, tools  # noqa: E402

state.init_db()
from core import dash  # noqa: E402
from core.dispatch import app  # noqa: E402

FAILS: list[str] = []


def ok(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


TOKEN = "notes-token-1"
CALLS: list = []
TOOLS = [
    {"name": "search_notes", "title": "Search notes", "inputSchema": {"type": "object"},
     "annotations": {"readOnlyHint": True}},
    {"name": "create_note", "title": "Create a note", "description": "Make a new note.",
     "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}, "body": {"type": "string"}}},
     "annotations": {"readOnlyHint": False}},
]


class App(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, status, body=None):
        data = b"" if body is None else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        msg = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self._send(401, {"error": "bad token"})
        rid, method = msg.get("id"), msg.get("method")
        if method == "initialize":
            return self._send(200, {"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "Notes"}}})
        if method == "notifications/initialized":
            return self._send(202)
        if method == "tools/list":
            return self._send(200, {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        if method == "tools/call":
            CALLS.append(msg["params"])
            return self._send(200, {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": f"Created note n_{len(CALLS)}"}]}})
        return self._send(200, {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no"}})


srv = ThreadingHTTPServer(("127.0.0.1", 0), App)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"
net.url_is_public = lambda url, resolve=None: str(url).startswith(BASE)
client.SCHEMES = ("http", "https")

PUSHED: list = []
push.subscriptions_for = lambda user_id: [{"endpoint": "https://push.example/1"}]
push.send = lambda sub, **kw: PUSHED.append(kw) or (True, "")

owner = app.test_client()
owner.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
member = app.test_client()
member.set_cookie(dash.COOKIE, dash.new_session(state.add_user("sam@glowmedspa.co", name="Sam", role="member")["id"]))

RUN = {"id": "seat_run_1", "label": "coworker weekly-digest run_1", "role": "act",
       "capabilities": ["read:apps", "write:proposals"]}
NOASK = {"id": "seat_run_2", "label": "coworker reader run_2", "role": "act", "capabilities": ["read:apps"]}
READ = {"id": "seat_read", "role": "read", "capabilities": None}

print("\nan action is only ever asked for —")
store.add("Clinic Notes", f"{BASE}/mcp", TOKEN, by="owner")
try:
    store.set_enabled("clinic_notes", ["search_notes"], by="owner", asks=["search_notes"])
    ok("a tool that only reads can't be 'asked for'", False)
except store.Refused as e:
    ok("a tool that only reads can't be 'asked for'; it's simply on or off", "simply on or off" in str(e))
store.set_enabled("clinic_notes", ["search_notes"], by="owner", asks=["create_note"])


def listed(seat):
    return {t["name"]: t for t in mcp._handle("tools/list", {}, 1, seat)["result"]["tools"]}


mine = listed(RUN)
ask = mine.get("app_clinic_notes.create_note", {})
ok("a coworker that may draft for approval sees it, titled with the promise",
   ask.get("title") == "Create a note in Clinic Notes (asks you first)" and tools.plain_title(ask.get("title", "")),
   str(ask.get("title")))
ok("...not as read-only", ask.get("annotations", {}).get("readOnlyHint") is False)
ok("a coworker without write:proposals doesn't see it", "app_clinic_notes.create_note" not in listed(NOASK))
ok("a read-only key never does", "app_clinic_notes.create_note" not in listed(READ))
ODD = {"id": "seat_odd", "role": "read", "capabilities": ["read:apps", "write:proposals"]}
payload, status = tools.call("app_clinic_notes.create_note", {"title": "x"}, ODD)
ok("...and a seat whose role is read-only can't ask, whatever it holds", status == 403 and not approvals.waiting()
   and CALLS == [], f"{status} {payload}")

print("\nasking —")
args = {"title": "Aftercare FAQ", "body": "No makeup for 24 hours."}
out = mcp._handle("tools/call", {"name": "app_clinic_notes.create_note", "arguments": args}, 2, RUN)["result"]
got = out.get("structuredContent") or {}
ok("asking answers 'asked', with the proposal, and nothing is done in the app", not out["isError"]
   and got.get("asked") is True and got.get("approval", "").startswith("ap_") and CALLS == [], str(out)[:240])
again = mcp._handle("tools/call", {"name": "app_clinic_notes.create_note", "arguments": args}, 3, RUN)["result"]
ok("asking again for the same thing is the same proposal",
   (again.get("structuredContent") or {}).get("approval") == got.get("approval") and len(approvals.waiting()) == 1)
ok("the owner's phone is told once, naming no app on the lock screen", len(PUSHED) == 1
   and PUSHED[0].get("navigate") == "/approvals" and "Clinic" not in PUSHED[0].get("body", ""), str(PUSHED))
with state.connect() as c:
    receipt = [dict(r) for r in c.execute("SELECT tool, outcome FROM seat_actions WHERE seat_id='seat_run_1'")]
ok("the ask itself is on the coworker's receipt", {"tool": "app_clinic_notes.create_note", "outcome": "ok"}
   in receipt, str(receipt))

print("\nWaiting for you —")
page = owner.get("/approvals").get_data(as_text=True)
ok("it shows exactly what will run, who asked, and until when", "Create a note in Clinic Notes" in page
   and "Aftercare FAQ" in page and "No makeup for 24 hours." in page and "coworker weekly-digest run_1" in page
   and "waits until" in page)
ok("the Dashboard says something is waiting", "Waiting for you" in owner.get("/dashboard").get_data(as_text=True))
r = member.get("/approvals")
ok("a member can't open it", r.status_code in (302, 303, 403), str(r.status_code))
r = member.post("/approvals", data={"do": "approve", "id": got["approval"]})
ok("...or approve", r.status_code in (302, 303, 403) and CALLS == [], f"{r.status_code} {CALLS}")

print("\napproving —")
r = owner.post("/approvals", data={"do": "approve", "id": got["approval"]})
ok("approve runs it once, with exactly the arguments shown", r.status_code == 303 and len(CALLS) == 1
   and CALLS[0] == {"name": "create_note", "arguments": args}, f"{r.status_code} {CALLS}")
a = approvals.get(got["approval"])
ok("...done, with what the app said, and who approved", a["status"] == "done"
   and a["result"]["text"] == "Created note n_1" and a["decided_by"], str(a)[:240])
ok("...and the page shows it", "Created note n_1" in owner.get(r.headers["Location"]).get_data(as_text=True))
r = owner.post("/approvals", data={"do": "approve", "id": got["approval"]})
ok("a second approve does nothing, and says so", r.status_code == 400 and "already done" in r.get_data(as_text=True)
   and len(CALLS) == 1)

print("\nwhat never runs —")


def ask_for(title):
    return mcp._handle("tools/call", {"name": "app_clinic_notes.create_note",
                                      "arguments": {"title": title}}, 9, RUN)["result"]["structuredContent"]["approval"]


no = ask_for("Declined note")
owner.post("/approvals", data={"do": "decline", "id": no})
ok("declined: never runs", approvals.get(no)["status"] == "declined" and len(CALLS) == 1)
old = ask_for("Old note")
with state.connect() as c:
    c.execute("UPDATE approvals SET expires_at='2026-01-01T00:00:00+00:00' WHERE id=?", (old,))
r = owner.post("/approvals", data={"do": "approve", "id": old})
ok("expired after a week: never runs", "expired" in r.get_data(as_text=True)
   and approvals.get(old)["status"] == "expired" and len(CALLS) == 1)
r = owner.post("/approvals", data={"do": "approve", "id": "ap_<script>"})
ok("a forged id is refused, in words", r.status_code == 400 and "nothing waiting" in r.get_data(as_text=True))
ok("only a person decides", approvals.decide(ask_for("No one"), True, by="")["ok"] is False and len(CALLS) == 1)
gone = ask_for("After disconnect")
store.remove("clinic_notes", by="owner")
r = owner.post("/approvals", data={"do": "approve", "id": gone})
ok("an app disconnected meanwhile: nothing runs, and it says so", approvals.get(gone)["status"] == "failed"
   and "disconnected" in approvals.get(gone)["result"]["text"] and len(CALLS) == 1, str(approvals.get(gone))[:200])
try:
    approvals.propose("nobody_registered_this", machine="x", title="x", detail={})
    ok("an unknown kind can't be proposed", False)
except ValueError:
    ok("an unknown kind can't be proposed", True)

srv.shutdown()
print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    sys.exit(1)
print("all passed")
