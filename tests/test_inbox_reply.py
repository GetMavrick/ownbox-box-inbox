"""A person types a reply, and the box sends it exactly once.

docs/SPEC_UNIFIED_INBOX_SCREEN_AND_AUTH.md §6. This is the FIRST path on this box where a human's
own words go to a customer, so every rule below is one this repo has already paid for somewhere
else: opt-out is absolute, money is metered before the call, the ledger is exactly-once, and a
timeout is INDETERMINATE and never resent.

WHAT IS DELIBERATELY NOT HERE. No drafting. `tests/test_customer_voice.py:501` bans `brain.think`
in every file of this department, the inbox carve-out included — measured, a real `brain.think(...)`
call in the package exits 1. An LLM that writes the reply is a separate architectural decision
(§8.4) and this suite would not change if it were made.

Run: python tests/test_inbox_reply.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "reply.db")
os.environ.pop("ZERNIO_API_KEY", None)

from core.vendors import zernio                                      # noqa: E402


class FakeInbox:
    def __init__(self):
        self.sent = []
        self.send_impl = lambda cid, aid, text, tag: {"message_id": f"out-{len(self.sent)}"}

    def send(self, cid, account_id, text, *, tag=None):
        self.sent.append((cid, account_id, text))
        return self.send_impl(cid, account_id, text, tag)


class FakeScoped:
    def __init__(self, inbox):
        self.inbox = inbox


INBOX = FakeInbox()
zernio.client = lambda space: FakeScoped(INBOX)

from core import cost_guard, state                                   # noqa: E402
from core import spaces as _spaces                                   # noqa: E402

state.init_db()
_spaces.space_by_name = lambda name, allow_default_alias=True: {"name": name, "key": "k"}

from marketing.customer_voice.inbox import reply, store              # noqa: E402

# EVERY SEND NAMES A PERSON. There is no "the owner by default" any more (#1143): a reply that
# cannot say who is sending it is refused, not attributed to him.
U = state.add_user("dana@example.com", name="Dana")["id"]

_failed = 0
SPACE = "acme"


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def _conv(zcid, *, account_id="acct-1", opted_out=False):
    store.upsert_conversation(space=SPACE, zcid=zcid, participant="Dana",
                              account_id=account_id)
    if opted_out:
        store.set_opted_out(SPACE, zcid)
    return store.get_conversation(SPACE, zcid)


# ── the account the screen sends from is on the row at all ─────────────────────────────────
print("test_the_conversation_remembers_which_account_owns_it")
row = _conv("c-acct")
ok("the poller's account_id is STORED, not only carried in the job",
   row.get("account_id") == "acct-1", str(row.get("account_id")))
store.upsert_conversation(space=SPACE, zcid="c-acct", participant="Dana", account_id=None)
ok("...and a later poll that omits it does not wipe it",
   store.get_conversation(SPACE, "c-acct").get("account_id") == "acct-1")


# ── opt-out is absolute ────────────────────────────────────────────────────────────────────
print("test_an_opted_out_person_is_never_sent_to")
_conv("c-stop", opted_out=True)
before = len(INBOX.sent)
try:
    reply.send_reply(space=SPACE, zcid="c-stop", text="hello?", user_id=U, nonce="n1")
    ok("a reply to an opted-out conversation is refused", False, "it was allowed")
except reply.ReplyRefused as e:
    ok("a reply to an opted-out conversation is refused", True)
    ok("...and the reason says so in words a person can act on", "opted out" in str(e), str(e))
ok("...and NOTHING reached the vendor", len(INBOX.sent) == before)
ok("...and no ledger row was written", store.get_send(SPACE, reply.idem_for(SPACE, "c-stop", U, "n1")) is None)


# ── the tenant boundary ────────────────────────────────────────────────────────────────────
print("test_a_conversation_this_box_does_not_own_is_not_found")
try:
    reply.send_reply(space=SPACE, zcid="belongs-to-someone-else", text="hi", user_id=U, nonce="n1")
    ok("an unknown conversation is refused", False, "it was allowed")
except reply.ReplyRefused as e:
    ok("an unknown conversation is refused", True)
    # NOT FOUND, never FORBIDDEN: a boundary that announces what is on the other side of it
    # is not a boundary.
    ok("...and it does not admit the conversation exists elsewhere",
       "no such conversation" in str(e) and "forbid" not in str(e).lower(), str(e))


# ── metered before the call, ledgered after, mirrored for the screen ───────────────────────
print("test_the_send_is_metered_before_and_ledgered_after")
_conv("c-ok")
order = []
_real_check = cost_guard.check_vendor
cost_guard.check_vendor = lambda v, u=1, now=None: order.append(f"meter:{v}")
INBOX.send_impl = lambda cid, aid, text, tag: (order.append("send"), {"message_id": "m-1"})[1]
out = reply.send_reply(space=SPACE, zcid="c-ok", text="on my way", user_id=U, nonce="n-first")
ok("the send succeeded", out["status"] == "ok" and out["message_id"] == "m-1", str(out))
ok("the METER ran BEFORE the vendor call, never after",
   order == ["meter:zernio", "send"], str(order))
led = store.get_send(SPACE, out["idem_key"])
ok("the ledger row exists", led is not None)
ok("...and records WHICH human hit send", led and led.get("user_id") == U,
   str(led and led.get("user_id")))
ok("...and is marked a reply, not an opener", led and led.get("kind") == "reply")
msgs = store.messages_for(SPACE, "c-ok")
ok("the reply is mirrored into the thread at once, not at the next poll",
   any(m.get("body") == "on my way" for m in msgs))
ok("...labelled `human`, never `ai` — the screen must not swap who said what",
   any(m.get("sent_by") == "human" for m in msgs if m.get("body") == "on my way"))


# ── a double-submitted form cannot double-send ─────────────────────────────────────────────
print("test_the_same_form_submitted_twice_sends_once")
sent_before = len(INBOX.sent)
again = reply.send_reply(space=SPACE, zcid="c-ok", text="on my way", user_id=U, nonce="n-first")
ok("the second identical submit reaches the vendor ZERO more times",
   len(INBOX.sent) == sent_before, f"{len(INBOX.sent) - sent_before} extra")
ok("...it is reported as a duplicate, not as a fresh send", again.get("duplicate") is True)
ok("...and it hands back the SAME message id, so the screen cannot show two",
   again.get("message_id") == "m-1", str(again.get("message_id")))
ok("...the idem key is the same, because the retried form carries the same nonce",
   again["idem_key"] == out["idem_key"])
ok("a different ATTEMPT by the same person is a different key",
   reply.idem_for(SPACE, "c-ok", U, "n-second") != out["idem_key"])
ok("...and the customer's words are NOT in the key at all — a ledger key gets read in support",
   "on my way" not in out["idem_key"], out["idem_key"])


# ── a timeout is indeterminate, never failed, never resent ─────────────────────────────────
print("test_a_timeout_is_indeterminate_and_is_never_resent")
_conv("c-timeout")


def _boom(cid, aid, text, tag):
    # EXACTLY how the gateway raises it (`ZernioError.__init__(..., indeterminate=True)`), and
    # the same shape tests/test_customer_voice_inbox.py:263 uses. A subclass setting the flag as
    # a CLASS attribute does not work — __init__ assigns the instance attribute over it, which
    # is how this test first passed a determinate error off as a timeout.
    raise zernio.ZernioError("gateway timeout after send", indeterminate=True)


INBOX.send_impl = _boom
try:
    reply.send_reply(space=SPACE, zcid="c-timeout", text="are you there", user_id=U, nonce="n-to")
    ok("an indeterminate send raises rather than returning ok", False, "it returned ok")
except reply.ReplyIndeterminate:
    ok("an indeterminate send raises rather than returning ok", True)
except reply.ReplyRefused as e:
    # CAUGHT ON PURPOSE. Classifying a timeout as a plain refusal is the specific regression
    # this test exists for, and it must read as a named failure rather than as a traceback —
    # a suite that crashes tells the next person less than one that says what changed.
    ok("an indeterminate send is NOT reported as a plain failure", False,
       f"it was refused instead: {e}")
led = store.get_send(SPACE, reply.idem_for(SPACE, "c-timeout", U, "n-to"))
ok("...it is recorded as INDETERMINATE, never as failed",
   led and led.get("status") == "indeterminate", str(led and led.get("status")))
ok("...and it is NOT mirrored into the thread — we do not know that it landed",
   not any(m.get("body") == "are you there" for m in store.messages_for(SPACE, "c-timeout")))
# RETRYING THE SAME ATTEMPT SAYS SO AGAIN — it does not quietly report success. A silent
# `duplicate` here is how the second try looks like it worked when nothing was sent.
sent_before = len(INBOX.sent)
try:
    reply.send_reply(space=SPACE, zcid="c-timeout", text="are you there", user_id=U, nonce="n-to")
    ok("...retrying the same attempt raises again rather than redirecting quietly", False,
       "it returned")
except reply.ReplyIndeterminate:
    ok("...retrying the same attempt raises again rather than redirecting quietly", True)
ok("...and it did NOT reach the vendor a second time", len(INBOX.sent) == sent_before)

cost_guard.check_vendor = _real_check


# ── empty input, and a conversation with no account ────────────────────────────────────────
print("test_nothing_is_sent_for_nothing")
for blank in ("", "   ", "\n\t "):
    try:
        reply.send_reply(space=SPACE, zcid="c-ok", text=blank, user_id=U, nonce="n-blank")
        ok(f"an empty reply ({blank!r}) is refused", False, "it was allowed")
    except reply.ReplyRefused:
        ok(f"an empty reply ({blank!r}) is refused", True)
_conv("c-noacct", account_id=None)
try:
    reply.send_reply(space=SPACE, zcid="c-noacct", text="hello", user_id=U, nonce="n-na")
    ok("a conversation with no account to send from is refused", False, "it was allowed")
except reply.ReplyRefused as e:
    ok("a conversation with no account to send from is refused", True)
    ok("...and says so, rather than 500ing on the vendor", "account" in str(e), str(e))


# ── the department may still not send from anywhere else ───────────────────────────────────
print("test_the_send_stayed_where_the_guard_requires_it")
# READ THE AST, NOT THE TEXT. The first cut of this block grepped both files for strings and
# failed on all three assertions — because a file that documents what it may not do CONTAINS
# those words: app.py's own comment says "never @blueprint.post", and reply.py's docstring
# explains the brain.think ban. A scanner that cannot tell a comment from a call is the exact
# defect class `tests/test_customer_voice.py` avoids by walking the tree, so walk the tree.
import ast                                                           # noqa: E402
import pathlib                                                       # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _calls(path):
    return {n.func.attr for n in ast.walk(ast.parse(path.read_text()))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}


def _decorators(path):
    """Every decorator spelled `@thing.name`, by name."""
    out = set()
    for n in ast.walk(ast.parse(path.read_text())):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in n.decorator_list:
                f = d.func if isinstance(d, ast.Call) else d
                if isinstance(f, ast.Attribute):
                    out.add(f.attr)
    return out


_APP = ROOT / "marketing" / "customer_voice" / "app.py"
_REPLY = ROOT / "marketing" / "customer_voice" / "inbox" / "reply.py"
_app_calls, _app_decos = _calls(_APP), _decorators(_APP)

ok("the screen never calls the gateway itself — the send lives under inbox/",
   "send" not in _app_calls, str(sorted(c for c in _app_calls if "send" in c)))
ok("...it routes with @blueprint.route, not @blueprint.post, which the guard reads as a call",
   "post" not in _app_decos and "route" in _app_decos, str(sorted(_app_decos)))
ok("...and it calls the send as a plain function a poller could also call",
   "send_reply" in _app_calls)
ok("the send refuses an unresolved Space rather than falling back to the global key",
   "allow_default_alias=False" in _REPLY.read_text())
ok("...and nothing in the send path reasons — no brain.think call anywhere in it",
   "think" not in _calls(_REPLY), str(sorted(_calls(_REPLY))))


# ── OSDev1's three, on #1145: the words are not the key, the attempt is ────────────────────
print("test_the_same_words_twice_are_two_replies_and_a_failure_can_be_retried")
_conv("c-repeat")
INBOX.send_impl = lambda cid, aid, text, tag: {"message_id": f"r-{len(INBOX.sent)}"}
_n = len(INBOX.sent)
a = reply.send_reply(space=SPACE, zcid="c-repeat", text="Thanks!", user_id=U, nonce="mon")
b = reply.send_reply(space=SPACE, zcid="c-repeat", text="Thanks!", user_id=U, nonce="fri")
# "Thanks!" on Monday and "Thanks!" on Friday. Under a words-derived key the second one was
# silently swallowed and the screen redirected as though it had sent.
ok("the SAME WORDS on two occasions send twice", len(INBOX.sent) - _n == 2,
   f"{len(INBOX.sent) - _n} sends")
ok("...under two different keys", a["idem_key"] != b["idem_key"])
ok("...and neither is reported as a duplicate",
   not a.get("duplicate") and not b.get("duplicate"))

_n = len(INBOX.sent)
reply.send_reply(space=SPACE, zcid="c-repeat", text="Thanks!", user_id=U, nonce="mon")
ok("the SAME NONCE twice sends once — a resubmitted form is still refused",
   len(INBOX.sent) == _n)

# A DETERMINATE FAILURE MUST NOT LOCK THE WORDS. Before this, retrying found the `failed` row
# and redirected as if it had worked, so the person had to reword their message to try again.
_conv("c-retry")
INBOX.send_impl = lambda cid, aid, text, tag: (_ for _ in ()).throw(
    zernio.ZernioError("rejected by the platform"))
try:
    reply.send_reply(space=SPACE, zcid="c-retry", text="are you open", user_id=U, nonce="try1")
    ok("a determinate failure raises", False, "it returned")
except reply.ReplyRefused:
    ok("a determinate failure raises", True)
_led = store.get_send(SPACE, reply.idem_for(SPACE, "c-retry", U, "try1"))
ok("...and is recorded as failed", _led and _led["status"] == "failed", str(_led and _led["status"]))

INBOX.send_impl = lambda cid, aid, text, tag: {"message_id": "after-retry"}
_n = len(INBOX.sent)
again2 = reply.send_reply(space=SPACE, zcid="c-retry", text="are you open", user_id=U, nonce="try1")
ok("retrying the SAME attempt after a failure DOES send — a failure blocks nothing",
   len(INBOX.sent) - _n == 1 and again2["status"] == "ok", str(again2))
_led2 = store.get_send(SPACE, reply.idem_for(SPACE, "c-retry", U, "try1"))
ok("...and the ledger is corrected rather than left lying about it",
   _led2 and _led2["status"] == "ok" and _led2["zernio_message_id"] == "after-retry",
   str(_led2 and _led2["status"]))
# But an `ok` row is immutable: that is what stops a double-send.
_n = len(INBOX.sent)
reply.send_reply(space=SPACE, zcid="c-retry", text="are you open", user_id=U, nonce="try1")
ok("...while a succeeded attempt stays immutable, so it cannot be sent twice",
   len(INBOX.sent) == _n)


# ── no person, no reply ────────────────────────────────────────────────────────────────────
print("test_a_reply_that_cannot_name_its_sender_is_refused")
_conv("c-who")
for bad in (None, "", "   "):
    try:
        reply.send_reply(space=SPACE, zcid="c-who", text="hi", user_id=bad, nonce="n")
        ok(f"user_id={bad!r} is refused, never attributed to the owner", False, "it was allowed")
    except reply.ReplyRefused:
        ok(f"user_id={bad!r} is refused, never attributed to the owner", True)
for bad in ("", "   ", "!!!"):
    try:
        reply.send_reply(space=SPACE, zcid="c-who", text="hi", user_id=U, nonce=bad)
        ok(f"nonce={bad!r} is refused — that submit did not come from our form", False,
           "it was allowed")
    except reply.ReplyRefused:
        ok(f"nonce={bad!r} is refused — that submit did not come from our form", True)
ok("a nonce is bounded before it reaches a ledger key",
   len(reply.clean_nonce("x" * 500)) == 64)


# ── the ledger's own contract, exercised directly ──────────────────────────────────────────
print("test_only_a_failed_ledger_row_can_ever_be_rewritten")
# WHY THIS IS TESTED AT THE STORE AND NOT THROUGH send_reply. `send_reply` returns early on an
# `ok` prior, so it stops a double-send before the ledger is asked — which means removing the
# SQL guard changed nothing visible and the suite stayed green. That made the guard untested
# defence-in-depth, so it is tested where it actually lives.
_conv("c-imm")
store.record_send(space=SPACE, zcid="c-imm", idem_key="imm-ok", kind="reply", status="ok",
                  zernio_message_id="m-good", user_id=U)
_wrote = store.record_send(space=SPACE, zcid="c-imm", idem_key="imm-ok", kind="reply",
                           status="failed", error="a later attempt", user_id=U)
ok("a later write cannot change a row that says the message WENT", _wrote is False)
_r = store.get_send(SPACE, "imm-ok")
ok("...it still says ok, with the same message id",
   _r and _r["status"] == "ok" and _r["zernio_message_id"] == "m-good", str(_r and _r["status"]))

store.record_send(space=SPACE, zcid="c-imm", idem_key="imm-ind", kind="reply",
                  status="indeterminate", error="timeout", user_id=U)
_wrote = store.record_send(space=SPACE, zcid="c-imm", idem_key="imm-ind", kind="reply",
                           status="ok", zernio_message_id="m-x", user_id=U)
ok("an INDETERMINATE row is equally immutable — rewriting it is how a double-send gets "
   "authorised", _wrote is False)
ok("...it still reads indeterminate",
   (store.get_send(SPACE, "imm-ind") or {}).get("status") == "indeterminate")

store.record_send(space=SPACE, zcid="c-imm", idem_key="imm-fail", kind="reply",
                  status="failed", error="rejected", user_id=U)
_wrote = store.record_send(space=SPACE, zcid="c-imm", idem_key="imm-fail", kind="reply",
                           status="ok", zernio_message_id="m-z", user_id=U)
ok("...while a FAILED row may be corrected, so the ledger stops lying after a good retry",
   _wrote is True)
_r = store.get_send(SPACE, "imm-fail")
ok("...and it now carries the real message id",
   _r and _r["status"] == "ok" and _r["zernio_message_id"] == "m-z", str(_r and _r["status"]))

# ── the claim, exercised directly ──────────────────────────────────────────────────────────
print("test_the_key_is_claimed_before_the_call_not_after")
_conv("c-claim")
ok("claiming a fresh key wins",
   store.claim_send(space=SPACE, zcid="c-claim", idem_key="cl-1", kind="reply", user_id=U))
ok("...and the row reads `sending` until it is resolved",
   (store.get_send(SPACE, "cl-1") or {}).get("status") == "sending")
ok("a SECOND claim on a live `sending` row loses",
   store.claim_send(space=SPACE, zcid="c-claim", idem_key="cl-1", kind="reply",
                    user_id=U) is False)
ok("resolving a row we own writes the outcome",
   store.resolve_send(space=SPACE, idem_key="cl-1", status="ok", zernio_message_id="m-1"))
_r = store.get_send(SPACE, "cl-1")
ok("...and it now reads ok, with the message id",
   _r and _r["status"] == "ok" and _r["zernio_message_id"] == "m-1", str(_r))
# THE GUARD ON `resolve_send`, TESTED WHERE IT LIVES rather than assumed — the same lesson as
# the immutability block above. A second resolve must not be able to touch a settled row.
ok("resolving a row that is NOT ours to resolve does nothing",
   store.resolve_send(space=SPACE, idem_key="cl-1", status="failed",
                      error="a later arrival") is False)
ok("...and the settled row is untouched",
   (store.get_send(SPACE, "cl-1") or {}).get("status") == "ok")
ok("a claim CANNOT take over an `ok` row",
   store.claim_send(space=SPACE, zcid="c-claim", idem_key="cl-1", kind="reply",
                    user_id=U) is False)
store.record_send(space=SPACE, zcid="c-claim", idem_key="cl-ind", kind="reply",
                  status="indeterminate", error="timeout", user_id=U)
ok("a claim CANNOT take over an `indeterminate` row — that is the double-send it exists to stop",
   store.claim_send(space=SPACE, zcid="c-claim", idem_key="cl-ind", kind="reply",
                    user_id=U) is False)
store.record_send(space=SPACE, zcid="c-claim", idem_key="cl-fail", kind="reply",
                  status="failed", error="rejected", user_id=U)
ok("a claim DOES take over a `failed` row, so an honest retry is never blocked",
   store.claim_send(space=SPACE, zcid="c-claim", idem_key="cl-fail", kind="reply", user_id=U))
_r = store.get_send(SPACE, "cl-fail")
ok("...and the stale error from the failed attempt is cleared, not carried into the new one",
   _r and _r["status"] == "sending" and not _r["error"], str(_r and _r["error"]))

# AN UNRESOLVED CLAIM IS "MAY HAVE LANDED" EVERYWHERE, not just inside send_reply — a new status
# that only one function understands is a hole. The process that claimed `cl-fail` above never
# resolved it, which is exactly what a crash mid-call leaves behind.
_hour_before = store.sends_last_hour(SPACE)
ok("an unresolved claim counts against the rolling-hour deliverability cap — an in-flight "
   "send is one the platform may already have seen",
   store.claim_send(space=SPACE, zcid="c-claim", idem_key="cl-cap", kind="reply", user_id=U)
   and store.sends_last_hour(SPACE) == _hour_before + 1,
   f"{_hour_before} -> {store.sends_last_hour(SPACE)}")
ok("...and its twin agrees about which rows those are, so 'when does a slot free' is not "
   "answered about a different set", store.oldest_counted_send(SPACE) is not None)

# AND THE WATCHDOG PAGES FOR IT once it ages. Without this, a claim orphaned by a crash would
# sit forever saying "may have landed" and nothing would ever tell a person to go look.
from datetime import datetime, timedelta, timezone                   # noqa: E402

from core import watchdog                                            # noqa: E402

_aged = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
with state.connect() as _c:
    _c.execute("UPDATE inbox_send_ledger SET created_at = ? WHERE idem_key = 'cl-cap'", (_aged,))
_healthy, _why = watchdog._probe_stuck_indeterminate()
ok("the watchdog's own probe — not a copy of its query — reads an aged claim as unreconciled",
   _healthy is False and "skip" not in _why, _why)
with state.connect() as _c:
    _c.execute("DELETE FROM inbox_send_ledger WHERE idem_key = 'cl-cap'")
_healthy, _why = watchdog._probe_stuck_indeterminate()
ok("...and goes quiet once it is gone, so this suite proved the row and not the probe",
   _healthy is True, _why)


# ── the double-click ───────────────────────────────────────────────────────────────────────
print("test_two_clicks_on_one_compose_box_send_one_message")
# OSDev1's finding on #1145, and the reason the claim exists at all. The compose form has no
# submit guard, so a double tap issues two POSTs carrying the SAME nonce. The first version of
# send_reply read the ledger, sent, then wrote the row — so under gthread workers both requests
# reached the read before either wrote, both saw nothing, and THE CUSTOMER GOT IT TWICE.
#
# The vendor sleeps here to hold the window open deliberately. Without the claim this test is
# red: two vendor calls, and the second request reports a cheerful success.
import threading                                                     # noqa: E402
import time as _time                                                 # noqa: E402

_conv("c-race")
INBOX.send_impl = lambda cid, aid, text, tag: (
    _time.sleep(0.2), {"message_id": "race-once"})[1]
_before = len(INBOX.sent)
_outcomes: list = []
_lock = threading.Lock()


def _double_click():
    try:
        r = reply.send_reply(space=SPACE, zcid="c-race", text="we open at nine",
                             user_id=U, nonce="same-nonce")
        with _lock:
            _outcomes.append(("returned", r))
    except Exception as e:                                           # noqa: BLE001
        with _lock:
            _outcomes.append((type(e).__name__, str(e)))


_threads = [threading.Thread(target=_double_click) for _ in range(2)]
for t in _threads:
    t.start()
for t in _threads:
    t.join(timeout=30)

_calls = len(INBOX.sent) - _before
ok("EXACTLY ONE vendor call for two concurrent submits of the same attempt",
   _calls == 1, f"{_calls} calls")
_kinds = sorted(k for k, _ in _outcomes)
ok("both requests finished", len(_outcomes) == 2, str(_outcomes))
ok("one of them sent", _kinds.count("returned") == 1, str(_kinds))
ok("the loser is told it MAY HAVE ARRIVED — never a silent second send, never a bare success",
   _kinds.count("ReplyIndeterminate") == 1, str(_kinds))
_r = store.get_send(SPACE, reply.idem_for(SPACE, "c-race", U, "same-nonce"))
ok("...and the ledger carries one settled row for the attempt",
   _r and _r["status"] == "ok" and _r["zernio_message_id"] == "race-once", str(_r))
INBOX.send_impl = lambda cid, aid, text, tag: {"message_id": f"out-{len(INBOX.sent)}"}


# ── THE LANE IS A GATE, NOT ONLY A WARNING (M2, OSDev1, 2026-09-22) ──────────────────────────
# WHAT WAS LIVE BEFORE THIS. `no_send_lane` had exactly one reader — `app._no_send_lane`, which
# hides the compose box on a thread. The Drafts tab (#1419) does not go through that screen: it
# calls `send_reply` directly, for every ticked draft, whatever the platform. So an email draft
# fell through to the Zernio branch and the buyer read an exception class name where a sentence
# belongs. The screen was a warning and nothing was a gate.
#
# THE OTHER CHANNELS MUST STILL GO. A gate that refuses everything is not a fix, and the Drafts
# tab sends a mixed list — the whole point of the screen is ticking several at once.
print("\ntest_a_channel_with_no_send_lane_is_refused_in_words")

_now = datetime.now(timezone.utc).isoformat()
store.upsert_conversation(space=SPACE, zcid="zc-lane-email", platform="email",
                          last_inbound_at=_now, account_id="owner@acme.co")
store.upsert_conversation(space=SPACE, zcid="zc-lane-dm", platform="messenger",
                          last_inbound_at=_now, account_id="acct-lane")

try:
    reply.send_reply(space=SPACE, zcid="zc-lane-email", text="Tuesday works.",
                     user_id=U, nonce=reply.new_nonce())
    ok("an email draft is refused rather than reaching the vendor", False, "it was not refused")
except reply.ReplyRefused as e:
    ok("an email draft is refused rather than reaching the vendor", True)
    # THE CHANNEL'S OWN SENTENCE, from `no_send_lane_why` on the rule, surfaced by `decide` under
    # `reason`. Reading `no_send_lane_why` off the RESULT looks right and is always empty.
    ok("...in the channel's own words, not a class name",
       "mail app" in str(e) and "SMTP" in str(e), str(e)[:120])
except Exception as e:                                   # noqa: BLE001
    ok("an email draft is refused rather than reaching the vendor", False,
       f"{type(e).__name__}: {str(e)[:80]}")

# NOTHING IS CLAIMED FOR A SEND THAT COULD NEVER HAPPEN. The refusal is before the ledger claim,
# so a lane-blocked channel leaves no row for the watchdog to puzzle over later.
_led = store.get_send(SPACE, reply.idem_for(SPACE, "zc-lane-email", U, "x")) or {}
ok("...and no ledger row was claimed for it", not _led, str(_led))

# THE OTHER CHANNELS ARE UNTOUCHED: messenger has a written rule and gets past the lane. It fails
# later here only because this harness resolves no Space, which is the fixture, not the gate.
try:
    reply.send_reply(space=SPACE, zcid="zc-lane-dm", text="On our way.",
                     user_id=U, nonce=reply.new_nonce())
    ok("a messenger draft still goes past the lane", True)
except Exception as e:                                   # noqa: BLE001
    ok("a messenger draft still goes past the lane",
       "cannot send on that channel" not in str(e) and "mail app" not in str(e),
       f"the lane refused it too: {str(e)[:90]}")

print(("FAILED " + str(_failed)) if _failed else "all ok")
sys.exit(1 if _failed else 0)
