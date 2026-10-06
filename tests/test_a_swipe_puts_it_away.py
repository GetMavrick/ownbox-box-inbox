"""The list's swipe actions: Done, Delete and a disposition (owner 2026-10-06, #1990).

Owner, 2026-10-06: "One of the most massive features that I am praying is included is Message list actions such as
slide a message to change the disposition archive or Delete." Zernio's screens have none of it, so the box gives
their list the routes: `PUT /inbox/api/conversations/<id>` {status, disposition} in the shape of Zernio's own
conversation update, and `GET /inbox/api/conversations?status=` for the inbox, Done, Trash and Junk.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * a conversation marked Done or deleted stays in the inbox, or cannot be found again (Done, Trash) and put back;
  * Done outlasts the person writing again, so a customer is lost in an archive;
  * Delete does not last (owner, 2026-10-06: Delete is gone for good, like Junk), or cannot be restored;
  * an old message mirrored late counts as them writing again;
  * Junk comes back when they write again, or a Junk, Done or deleted conversation still counts as waiting;
  * Delete removes anything (the row, its messages) instead of putting it in Trash;
  * a disposition outside the list, or a status outside active/archived/deleted, is stored;
  * the screens that never offered these actions lose a conversation.

No network.

Run: python tests/test_a_swipe_puts_it_away.py
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
T = pathlib.Path(tempfile.mkdtemp(prefix="swipe_"))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")
os.environ["AIOS_MY_MACHINES"] = str(T / "machines")
os.environ["DASH_TOKEN"] = "test-dash-pw"
os.environ.pop("ZERNIO_API_KEY", None)

from core import dash, spaces as _spaces, state  # noqa: E402

state.init_db()
SPACE = _spaces.DEFAULT

from core.dispatch import app  # noqa: E402
from marketing.customer_voice.inbox import store  # noqa: E402

state.init_db()
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -- {str(detail)[:400]}" if not cond and detail else ""))
    if not cond:
        _failed += 1


o = app.test_client()
o.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
N = [0]


def said(zcid, *, sent_at=None):
    N[0] += 1
    store.record_message(space=SPACE, zcid=zcid, zmid=f"{zcid}-{N[0]}", direction="in", sent_by="contact",
                         body=f"message {N[0]}", sent_at=sent_at)


def convo(zcid, who):
    store.upsert_conversation(space=SPACE, zcid=zcid, platform="instagram", participant=who, account_id="acct",
                              last_inbound_at=datetime.now(timezone.utc).isoformat())
    said(zcid)


def listed(status=None, **q):
    args = {**({"status": status} if status else {}), **q}
    got = o.get("/inbox/api/conversations", query_string=args).get_json() or {}
    return {c["id"]: c for c in got.get("data") or []}


def put(zcid, **body):
    return o.put(f"/inbox/api/conversations/{zcid}", json=body)


for z, who in (("ig-1", "Dana"), ("ig-2", "Sam"), ("ig-3", "Lee"), ("ig-4", "Kim")):
    convo(z, who)
waiting0 = store.awaiting_reply(SPACE)

print("test_done_and_back")
r = put("ig-1", status="archived")
ok("swiping Done answers with the new state", r.status_code == 200
   and r.get_json() == {"success": True, "status": "archived", "disposition": None}, r.get_json())
ok("...it leaves the inbox", "ig-1" not in listed() and {"ig-2", "ig-3", "ig-4"} <= set(listed()), list(listed()))
ok("...and is under Done, saying so", listed("archived").get("ig-1", {}).get("status") == "archived",
   list(listed("archived")))
ok("...and no longer counts as waiting on you", store.awaiting_reply(SPACE) == waiting0 - 1,
   (waiting0, store.awaiting_reply(SPACE)))
said("ig-1", sent_at=(datetime.now(timezone.utc) - timedelta(days=3)).isoformat())
ok("an old message mirrored late is not them writing again", "ig-1" not in listed(), list(listed()))
said("ig-1")
ok("when they write again it is back in the inbox, and waiting", "ig-1" in listed()
   and listed()["ig-1"]["status"] == "active" and "ig-1" not in listed("archived")
   and store.awaiting_reply(SPACE) == waiting0, list(listed("archived")))
put("ig-1", status="archived")
r = put("ig-1", status="active")
ok("Move to inbox undoes Done", r.get_json().get("status") == "active" and "ig-1" in listed(), r.get_json())

print("\ntest_delete_is_trash")
with state.connect() as c:
    before = c.execute("SELECT COUNT(*) FROM inbox_messages WHERE zernio_conversation_id = 'ig-2'").fetchone()[0]
put("ig-2", status="deleted")
ok("Delete takes it out of the inbox and into Trash", "ig-2" not in listed()
   and listed("deleted").get("ig-2", {}).get("status") == "deleted", list(listed("deleted")))
with state.connect() as c:
    after = c.execute("SELECT COUNT(*) FROM inbox_messages WHERE zernio_conversation_id = 'ig-2'").fetchone()[0]
ok("...and removes nothing: the conversation and every message are still on the box",
   store.get_conversation(SPACE, "ig-2") is not None and after == before == 1, (before, after))
put("ig-2", status="active")
ok("Restore brings it back", "ig-2" in listed() and "ig-2" not in listed("deleted"))
put("ig-2", status="deleted")
said("ig-2")
ok("Delete is gone for good: writing again leaves it in Trash, and not waiting (owner, 2026-10-06)",
   "ig-2" not in listed() and "ig-2" in listed("deleted") and store.awaiting_reply(SPACE) == waiting0 - 1,
   (list(listed("deleted")), store.awaiting_reply(SPACE)))
put("ig-2", status="active")
ok("...until a person restores it", "ig-2" in listed() and "ig-2" not in listed("deleted"))

print("\ntest_disposition")
r = put("ig-3", disposition="lead")
ok("a disposition is set and shown on the row", r.get_json().get("disposition") == "lead"
   and listed()["ig-3"]["metadata"]["aios"]["disposition"] == "lead", r.get_json())
ok("...the list narrows to it", set(listed(disposition="lead")) == {"ig-3"}, list(listed(disposition="lead")))
put("ig-3", status="archived")
ok("...and Done keeps it", listed("archived").get("ig-3", {}).get("metadata", {}).get("aios", {}).get("disposition")
   == "lead")
put("ig-3", status="active")
r = put("ig-4", disposition="junk")
ok("Junk leaves the inbox and the waiting count, and is under Junk",
   "ig-4" not in listed() and "ig-4" in listed("junk") and store.awaiting_reply(SPACE) == waiting0 - 1,
   (list(listed("junk")), store.awaiting_reply(SPACE)))
said("ig-4")
ok("...and stays out when they write again", "ig-4" not in listed() and "ig-4" in listed("junk"))
put("ig-4", disposition=None)
ok("clearing the disposition brings it back", "ig-4" in listed() and listed()["ig-4"]["metadata"]["aios"]["disposition"]
   is None)

print("\ntest_only_what_the_list_offers")
for body, why in (({"status": "spam"}, "a status outside active, archived, deleted"),
                  ({"disposition": "vip"}, "a disposition outside the list"), ({}, "nothing to change")):
    r = put("ig-3", **body)
    ok(f"refused: {why}", r.status_code == 400 and r.get_json().get("code") == "invalid_field_value", r.get_json())
ok("...and nothing changed", listed()["ig-3"]["metadata"]["aios"]["disposition"] == "lead")
ok("a conversation not on this box is a 404", put("nope", status="archived").status_code == 404)
ok("a view the list does not have is refused", o.get("/inbox/api/conversations?status=spam").status_code == 400)
ok("someone not signed in changes nothing", app.test_client().put("/inbox/api/conversations/ig-3",
   json={"status": "deleted"}).status_code in (302, 401, 403) and "ig-3" in listed())

print("\ntest_the_old_screens_lose_nothing")
put("ig-1", status="archived")
put("ig-2", status="deleted")
ok("the screens that never offered these still list every conversation",
   {"ig-1", "ig-2", "ig-3", "ig-4"} <= {r["zernio_conversation_id"] for r in store.list_conversations(SPACE)})

print("\nALL SWIPE CHECKS PASS" if not _failed else f"\n{_failed} SWIPE CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
