"""Zernio's inbox screens are answered by this box (#1990 Phase 1, step 1.2).

Owner, 2026-10-06: "just plan to mirror their inbox right into our system." Their screens call `/api/...` on their own
server, which forwards to Zernio; on a box the same calls reach `/inbox/api/...` and are answered from our store,
through our send path, behind the inbox's sign-in. OSDev1's conditions for Phase 1 are held here: (3) no Zernio key in
anything returned; (4) a poll is answered from our store, never a Zernio call; (5) never raw HTML.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * someone not signed in reads a conversation;
  * a list or thread poll calls Zernio, or a shape their client reads (data, pagination, messages) is missing;
  * a message carries HTML, or anything returned carries the Zernio key;
  * a send goes anywhere but `reply.send_reply`, or a refusal or an uncertain send reads as success;
  * opening a thread does not mark it read; a not-yet-supported action pretends to work.

No network: Zernio is stood in for, and fails if anything calls it.

Run: python tests/test_the_inbox_screens_read_the_box.py
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
T = pathlib.Path(tempfile.mkdtemp(prefix="inbox_api_"))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")
os.environ["AIOS_MY_MACHINES"] = str(T / "machines")
os.environ["DASH_TOKEN"] = "test-dash-pw"
SECRET = "sk_live_THE_ZERNIO_KEY_123"
os.environ["ZERNIO_API_KEY"] = SECRET

from core.vendors import zernio  # noqa: E402

CALLED = []
zernio.client = lambda space: CALLED.append(space) or (_ for _ in ()).throw(AssertionError("Zernio was called"))

from core import dash, spaces, state  # noqa: E402

state.init_db()
from core.dispatch import app  # noqa: E402
from marketing.customer_voice.inbox import reply, store  # noqa: E402

state.init_db()
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -- {str(detail)[:400]}" if not cond and detail else ""))
    if not cond:
        _failed += 1


SP = spaces.DEFAULT
store.upsert_conversation(space=SP, zcid="ig-1", platform="instagram", participant="Dana Whitfield",
                          account_id="ig-acct", last_inbound_at="2026-10-06T08:00:00+00:00")
store.record_message(space=SP, zcid="ig-1", zmid="m-ig-1", direction="in", sent_by="contact",
                     body="Do you have Saturday openings?", sent_at="2026-10-06T08:00:00+00:00")
store.upsert_conversation(space=SP, zcid="acct-m::t_9", platform="messenger", participant="Ben",
                          account_id="acct-m", last_inbound_at="2026-10-05T08:00:00+00:00")
store.record_message(space=SP, zcid="acct-m::t_9", zmid="m-fb-1", direction="in", sent_by="contact", body="Hi")
store.upsert_conversation(space=SP, zcid="mail-1", platform="email", participant="Priya Raman",
                          account_id="owner@example.com", last_inbound_at="2026-10-04T08:00:00+00:00")
store.record_message(space=SP, zcid="mail-1", zmid="<a@x>", direction="in", sent_by="contact",
                     body="Can I move my appointment?",
                     detail={"body_text": "Can I move my appointment?\n\nThanks, Priya",
                             "body_html": "<p>Can I move</p><script>alert(1)</script>",
                             "headers": {"Subject": "Moving my booking"}})

o = app.test_client()
o.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
anon = app.test_client()

print("test_only_signed_in")
r = anon.get("/inbox/api/conversations")
ok("someone not signed in reads nothing", r.status_code != 200 or b"Dana" not in r.data, r.status_code)

print("\ntest_the_list")
r = o.get("/inbox/api/conversations?sortOrder=desc&limit=100")
body = r.get_json() or {}
data = body.get("data") or []
ok("their shape: data, pagination, meta", r.status_code == 200 and {"data", "pagination", "meta"} <= set(body), body)
ok("newest first, our key as the id, their platform names (Messenger is 'facebook')",
   [(c["id"], c["platform"]) for c in data] == [("ig-1", "instagram"), ("acct-m::t_9", "facebook"),
                                               ("mail-1", "email")], data)
d0 = data[0] if data else {}
ok("each carries its account, person, last words and our facts", d0.get("accountId") == "ig-acct"
   and d0.get("participantName") == "Dana Whitfield" and d0.get("lastMessage") == "Do you have Saturday openings?"
   and d0.get("metadata", {}).get("aios", {}).get("waiting") is True, d0)
r = o.get("/inbox/api/conversations?limit=1")
b = r.get_json()
ok("paged: one at a time, with a cursor to the next", len(b["data"]) == 1 and b["pagination"]["hasMore"] is True
   and b["pagination"]["nextCursor"] == "1", b)
ok("...and the cursor reaches the next", o.get("/inbox/api/conversations?limit=1&cursor=1").get_json()["data"][0]["id"]
   == "acct-m::t_9")
ok("their platform filter: 'facebook' is our Messenger", [c["id"] for c in o.get(
   "/inbox/api/conversations?platform=facebook").get_json()["data"]] == ["acct-m::t_9"])

print("\ntest_the_thread")
r = o.get("/inbox/api/conversations/mail-1/messages?accountId=owner@example.com&sortOrder=desc&limit=50")
msgs = (r.get_json() or {}).get("messages") or []
m0 = msgs[0] if msgs else {}
ok("an email reads as its full plain text, with its subject", m0.get("message")
   == "Can I move my appointment?\n\nThanks, Priya" and m0.get("metadata", {}).get("aios", {}).get("subject")
   == "Moving my booking" and m0.get("direction") == "incoming", m0)
# NEVER RAW HTML, EXCEPT IN THE FRAME (owner, 2026-10-06; OSDev1: "reuse render.safe_frame ... a srcdoc-safe rendering
# through the adapter"). A sender's HTML may ride in ONE place, `metadata.aios.frame.doc`: render.frame_doc's whole
# document, its CSP first, which their screen only ever sets as a sandboxed frame's srcDoc. Everywhere else, none.
import copy as _copy  # noqa: E402
_rest = _copy.deepcopy(msgs)
_docs = [m.get("metadata", {}).get("aios", {}).pop("frame", {}).get("doc", "") for m in _rest]
ok("NEVER RAW HTML: nothing from the HTML body reaches their screen outside the sandboxed frame's document",
   "<script" not in str(_rest) and "<p>" not in str(_rest)
   and all(d.startswith('<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy"')
           for d in _docs if d), (_docs[:1], str(_rest)[:300]))
ok("a key holding '::' opens its thread", o.get("/inbox/api/conversations/acct-m::t_9/messages").status_code == 200)
ok("an unknown conversation is a 404", o.get("/inbox/api/conversations/nope/messages").status_code == 404)

print("\ntest_never_zernio_never_the_key")
blob = b"".join(o.get(p).data for p in ("/inbox/api/conversations", "/inbox/api/conversations/ig-1/messages",
                                         "/inbox/api/accounts", "/inbox/api/settings"))
ok("polling the list and a thread never calls Zernio (OSDev1's condition 4)", CALLED == [], CALLED)
ok("the Zernio key is in nothing returned", SECRET.encode() not in blob)

print("\ntest_sending")
SENT = []


def fake_send(**kw):
    SENT.append(kw)
    if kw["text"] == "refuse me":
        raise reply.ReplyRefused("This person said STOP.")
    if kw["text"] == "unsure":
        raise reply.ReplyIndeterminate("timeout")
    return {"message_id": "out-1"}


_real = reply.send_reply
reply.send_reply = fake_send
r = o.post("/inbox/api/conversations/ig-1/messages", json={"accountId": "ig-acct", "message": "Yes, 10am works!"})
ok("a send goes through reply.send_reply, as the person signed in", r.status_code == 200
   and SENT and SENT[-1]["zcid"] == "ig-1" and SENT[-1]["text"] == "Yes, 10am works!"
   and SENT[-1]["user_id"] == state.owner_user()["id"], (r.status_code, SENT))
r = o.post("/inbox/api/conversations/ig-1/messages", json={"message": "refuse me"})
ok("a refusal is an error in words, never success", r.status_code == 422
   and r.get_json()["error"] == "This person said STOP.", r.get_json())
r = o.post("/inbox/api/conversations/ig-1/messages", json={"message": "unsure"})
ok("an uncertain send says it may have arrived and is not resent", r.status_code == 409
   and "may or may not have arrived" in r.get_json()["error"], r.get_json())
n = len(SENT)
r = anon.post("/inbox/api/conversations/ig-1/messages", json={"message": "sneaky"})
ok("someone not signed in sends nothing", len(SENT) == n and r.status_code != 200, r.status_code)
reply.send_reply = _real

print("\ntest_the_rest")
ok("opening a thread marks it read", o.post("/inbox/api/conversations/ig-1/read", json={}).status_code == 200
   and o.get("/inbox/api/conversations").get_json()["data"][0]["unreadCount"] == 0)
ok("reactions and attachments say they are not here yet, never pretend",
   o.post("/inbox/api/conversations/ig-1/messages/m-ig-1/reactions", json={"emoji": "👍"}).status_code == 501
   and o.get("/inbox/api/media").status_code == 501)
ok("typing is accepted quietly", o.post("/inbox/api/conversations/ig-1/typing", json={}).status_code == 204)
acc = o.get("/inbox/api/accounts").get_json()
ok("the accounts are the box's own, all shown by default", {a["_id"] for a in acc["accounts"]}
   == {"ig-acct", "acct-m", "owner@example.com"} and set(acc["selectedAccountIds"]) == {a["_id"] for a in acc["accounts"]},
   acc)
r = o.put("/inbox/api/settings", json={"selectedAccountIds": ["ig-acct", "not-ours"]})
ok("choosing accounts keeps only the box's own, for this person", r.get_json() == {"selectedAccountIds": ["ig-acct"]}
   and o.get("/inbox/api/settings").get_json()["selectedAccountIds"] == ["ig-acct"], r.get_json())
ok("a malformed choice is refused", o.put("/inbox/api/settings", json={"selectedAccountIds": "x"}).status_code == 400)

print("\nALL INBOX-API CHECKS PASS" if not _failed else f"\n{_failed} INBOX-API CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
