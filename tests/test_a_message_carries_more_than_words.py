"""A message keeps what it carries beside its words, and their screens show it (#1990 Phase 1, step 1.6).

Zernio's thread shows photos and files, sent/delivered/read ticks with the platform's error when one fails, reactions,
and edited or deleted messages. Our store kept only the words. Now the poller keeps the rest, current on every read,
and the box's routes hand it to their screens; opening a thread marks it read on the platform too.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * an attachment, a tick, a reaction, an edit or a deletion Zernio sent is lost on the way to their screen;
  * a link that is not http(s) (a script, a data URL) reaches the screen;
  * a tick that changes (delivered, then read) stays as it was first seen;
  * opening an Instagram thread does not mark it read on Instagram, or asks with our key instead of Zernio's id;
  * opening an email marks anything in the owner's mailbox (the box opens it read-only).

No network: Zernio is stood in for.

Run: python tests/test_a_message_carries_more_than_words.py
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
T = pathlib.Path(tempfile.mkdtemp(prefix="extras_"))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")
os.environ["AIOS_MY_MACHINES"] = str(T / "machines")
os.environ["DASH_TOKEN"] = "test-dash-pw"
os.environ.pop("ZERNIO_API_KEY", None)

from core.vendors import zernio  # noqa: E402


class FakeInbox:
    status = "delivered"

    def __init__(self):
        self.read = []

    def list(self, **k):
        return {"conversations": []}

    def messages(self, cid, account_id, *, limit=50):
        return {"messages": [
            {"id": "m1", "direction": "incoming", "message": "Here is the photo", "createdAt": "2026-10-06T08:00:00Z",
             "attachments": [{"type": "image", "url": "https://cdn.example/p.jpg", "mimeType": "image/jpeg"},
                             {"type": "file", "url": "javascript:alert(1)", "name": "evil.pdf"}],
             "reactions": [{"emoji": "❤️", "fromMe": True}]},
            {"id": "m2", "direction": "outgoing", "message": "Thanks!", "createdAt": "2026-10-06T08:01:00Z",
             "deliveryStatus": self.status, "isEdited": True},
            {"id": "m3", "direction": "outgoing", "message": "Nope", "createdAt": "2026-10-06T08:02:00Z",
             "deliveryStatus": "failed", "deliveryError": {"message": "Outside the 24-hour window"}},
            {"id": "m4", "direction": "incoming", "message": "", "createdAt": "2026-10-06T08:03:00Z",
             "isDeleted": True}]}

    def mark_read(self, cid, account_id):
        self.read.append((cid, account_id))
        return True


INBOX = FakeInbox()
zernio.client = lambda space: type("S", (), {"inbox": INBOX})()
zernio.is_configured = lambda key: bool(key)

from core import dash, spaces as _spaces, state  # noqa: E402

state.init_db()
_spaces.space_by_name = lambda name, allow_default_alias=True: {"name": name, "key": "k"}
SPACE = _spaces.DEFAULT

from core.dispatch import app  # noqa: E402
from marketing.customer_voice.inbox import poller, store  # noqa: E402

state.init_db()
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -- {str(detail)[:400]}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def sweep(updated):
    ch = type("Ch", (), {"key": "instagram", "vendor": "instagram"})()
    poller._sweep_channel({"name": SPACE, "zernio_key": "k"}, zernio.client({}), ch, {"conversations": [
        {"id": "ig-9", "accountId": "ig-acct", "participantName": "Dana", "updatedTime": updated}]})


o = app.test_client()
o.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))


def thread():
    got = o.get("/inbox/api/conversations/ig-9/messages").get_json() or {}
    return {m["id"]: m for m in got.get("messages") or []}


print("test_what_a_message_carries")
sweep("1")
t = thread()
ok("a photo comes through with its link and type", t.get("m1", {}).get("attachments")
   == [{"type": "image", "url": "https://cdn.example/p.jpg", "name": "", "mimeType": "image/jpeg"},
       {"type": "file", "url": "", "name": "evil.pdf", "mimeType": ""}], t.get("m1"))
ok("...and a link that is not http(s) never reaches the screen", "javascript" not in str(t))
ok("a reaction comes through", t["m1"].get("reactions") == [{"emoji": "❤️", "fromMe": True}], t["m1"])
ok("a tick and an edit come through", t["m2"].get("deliveryStatus") == "delivered" and t["m2"].get("isEdited") is True,
   t["m2"])
ok("a failed send says why, in the platform's words", t["m3"].get("deliveryStatus") == "failed"
   and t["m3"].get("deliveryError") == {"message": "Outside the 24-hour window"}, t["m3"])
ok("a deleted message reads as deleted", t["m4"].get("isDeleted") is True, t["m4"])

print("\ntest_a_tick_moves_on")
FakeInbox.status = "read"
sweep("2")
ok("delivered, then read: the screen follows", thread()["m2"].get("deliveryStatus") == "read", thread()["m2"])

print("\ntest_opening_marks_it_read_where_it_lives")
o.post("/inbox/api/conversations/ig-9/read", json={})
ok("opening an Instagram thread marks it read on Instagram, with Zernio's id and the account",
   INBOX.read == [("ig-9", "ig-acct")], INBOX.read)
store.upsert_conversation(space=SPACE, zcid="mail-9", platform="email", participant="Priya", account_id="me@x.com")
store.record_message(space=SPACE, zcid="mail-9", zmid="<p@x>", direction="in", sent_by="contact", body="Hi")
INBOX.read.clear()
o.post("/inbox/api/conversations/mail-9/read", json={})
ok("opening an email marks nothing in the owner's mailbox", INBOX.read == [], INBOX.read)

print("\nALL MESSAGE-EXTRAS CHECKS PASS" if not _failed else f"\n{_failed} MESSAGE-EXTRAS CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
