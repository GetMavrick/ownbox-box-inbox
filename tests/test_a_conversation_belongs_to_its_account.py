"""A conversation is keyed by the account that owns it (#1990 Phase 1, step 1.0).

Zernio's own inbox says a conversation id is unique only within its account ("two accounts can both have a thread id
'123'", zernio-dev/unified-inbox src/lib/merge.ts). Every table here keyed a conversation by (space, id) alone, so two
connected accounts sharing an id would merge two people into one thread: one person's messages under another's name,
and a reply sent to the wrong one.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * a new channel's conversation is stored under the bare id, so two accounts' threads merge;
  * an Instagram or Messenger conversation already on a box gets a new key (every row would have to move);
  * a second account bringing an id the box already holds for another account is merged into that thread;
  * Zernio is called with our stored key instead of its own id (reading messages, sending a reply).

No network: Zernio is stood in for.

Run: python tests/test_a_conversation_belongs_to_its_account.py
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
T = pathlib.Path(tempfile.mkdtemp(prefix="conv_key_"))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")
os.environ["AIOS_MY_MACHINES"] = str(T / "machines")
os.environ.setdefault("DASH_TOKEN", "test-dash-pw")
os.environ.pop("ZERNIO_API_KEY", None)

from core.vendors import zernio  # noqa: E402


class FakeInbox:
    def __init__(self):
        self.read, self.sent = [], []

    def list(self, **k):
        return {"conversations": []}

    def messages(self, cid, account_id, *, limit=50):
        self.read.append((cid, account_id))
        return {"messages": [{"id": f"m-{account_id}-{cid}", "direction": "incoming", "message": f"hi from {account_id}",
                              "createdAt": "2026-10-06T08:00:00Z"}]}

    def send(self, cid, account_id, text, **k):
        self.sent.append((cid, account_id, text))
        return {"message_id": f"out-{len(self.sent)}"}


INBOX = FakeInbox()
zernio.client = lambda space: type("S", (), {"inbox": INBOX})()
zernio.is_configured = lambda key: bool(key)

from core import spaces as _spaces, state  # noqa: E402

state.init_db()
_spaces.space_by_name = lambda name, allow_default_alias=True: {"name": name, "key": "k"}
_spaces.all_spaces = lambda: [{"name": _spaces.DEFAULT, "zernio_key": "k"}]
SPACE = _spaces.DEFAULT

import marketing.customer_voice  # noqa: E402,F401 — registers the machine's tables
from marketing.customer_voice.inbox import channels, poller, store  # noqa: E402

state.init_db()
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -- {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def ch(key):
    return type("Ch", (), {"key": key, "vendor": key})()


def rows(vid):
    with state.connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT zernio_conversation_id k, account_id a, participant p FROM inbox_conversations "
            "WHERE zernio_conversation_id LIKE ? ORDER BY k", (f"%{vid}",)).fetchall()]


print("test_the_key")
ok("a new channel's conversation carries its account", channels.conversation_key("telegram", "acct-a", "123")
   == "acct-a::123")
ok("Instagram and Messenger keep the bare id they were stored under, so no row moves",
   channels.conversation_key("instagram", "acct-a", "123") == "123"
   and channels.conversation_key("messenger", "acct-a", "t_9") == "t_9")
ok("...unless the id clashes", channels.conversation_key("instagram", "acct-b", "123", clash=True) == "acct-b::123")
ok("Zernio's own id comes back out of any key", channels.vendor_id("acct-a::123") == "123"
   and channels.vendor_id("123") == "123")
ok("no id is no key", channels.conversation_key("telegram", "acct-a", "") == "")

print("\ntest_two_accounts_one_id_are_two_people")
poller._sweep_channel({"name": SPACE, "zernio_key": "k"}, zernio.client({}), ch("telegram"), {"conversations": [
    {"id": "777", "accountId": "bot-a", "participantName": "Ana", "updatedTime": "1"},
    {"id": "777", "accountId": "bot-b", "participantName": "Ben", "updatedTime": "1"}]})
got = rows("777")
ok("two rows, one per account, each with its own person", [(r["k"], r["a"], r["p"]) for r in got]
   == [("bot-a::777", "bot-a", "Ana"), ("bot-b::777", "bot-b", "Ben")], got)
ok("Zernio was asked for its own id, with each account", sorted(INBOX.read) == [("777", "bot-a"), ("777", "bot-b")],
   INBOX.read)

print("\ntest_a_clash_on_a_bare_channel_is_caught")
INBOX.read.clear()
poller._sweep_channel({"name": SPACE, "zernio_key": "k"}, zernio.client({}), ch("instagram"), {"conversations": [
    {"id": "555", "accountId": "ig-a", "participantName": "Cara", "updatedTime": "1"}]})
poller._sweep_channel({"name": SPACE, "zernio_key": "k"}, zernio.client({}), ch("instagram"), {"conversations": [
    {"id": "555", "accountId": "ig-b", "participantName": "Dev", "updatedTime": "1"}]})
got = rows("555")
ok("the first keeps the bare id; the second account's same id gets its own thread, never merged",
   [(r["k"], r["a"], r["p"]) for r in got] == [("555", "ig-a", "Cara"), ("ig-b::555", "ig-b", "Dev")], got)
ok("...and the same account seen again stays on its row",
   store.conversation_key(SPACE, "instagram", "ig-a", "555") == "555")

print("\ntest_a_reply_goes_to_zernios_id")
from marketing.customer_voice.inbox import reply  # noqa: E402
sent = reply._deliver(space=SPACE, zcid="bot-b::777", conv=store.get_conversation(SPACE, "bot-b::777"),
                      account_id="bot-b", text="Hello Ben", idem="t-1") if hasattr(reply, "_deliver") else None
ok("the send reaches Zernio with its own id and the owning account",
   ("777", "bot-b", "Hello Ben") in INBOX.sent, (sent, INBOX.sent))

print("\nALL CONVERSATION-KEY CHECKS PASS" if not _failed else f"\n{_failed} CONVERSATION-KEY CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
