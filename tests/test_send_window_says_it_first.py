"""The box says what the send window is BEFORE somebody types, and never blocks them.

OPTION C OF #1170, AND ONLY OPTION C. That proposal put a question to the owner that no developer
may answer for him — "if our clock says the window is shut and the person wants to send anyway,
does the box let them try?" — and recommended shipping the half that does not need the answer.
So the thing this suite guards hardest is a NEGATIVE: nothing here may ever stop a send.

WHY NOT THE OBVIOUS FIX. Making `reply.py` symmetric with the automated opener was the expected
answer and is the wrong one. Our window state is an INFERENCE from `last_inbound_at`, a column a
poller fills — it lags, skips channels, carries stale watermarks. The vendor's answer is the
FACT. On the bot path refusing on the inference is right, because nobody is watching. On the
human path a false block tells a business owner he may not answer his own customer, on our
arithmetic, with no override — and it still does not make the message send. It only converts
"you tried and Meta said no" into "we said no".

THE LINE THAT MATTERS MOST IS THE DISAGREEMENT CASE. The plain-language sentence is used only
when the vendor refused for a window reason AND our own clock agrees. When they disagree, the
inference is the thing that is wrong, and the person gets the vendor's own words back.

Run: python tests/test_send_window_says_it_first.py
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "sendwindow.db")
os.environ.pop("ZERNIO_API_KEY", None)

from core.vendors import zernio                                      # noqa: E402
from core.vendors.zernio.errors import ZernioError                   # noqa: E402


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

from core import state                                               # noqa: E402
from core import spaces as _spaces                                   # noqa: E402

state.init_db()
_spaces.space_by_name = lambda name, allow_default_alias=True: {"name": name, "key": "k"}

from marketing.customer_voice.inbox import reply, store, window      # noqa: E402

U = state.add_user("dana@example.com", name="Dana")["id"]
SPACE = "acme"
# REAL WALL CLOCK, DELIBERATELY. `reply._refusal_sentence` re-reads the window with the real
# `now` — it has no injection point, and giving it one would be a seam that exists only for a
# test. A fixture pinned to a fixed hour made every row look future-dated to the send path, which
# is how this suite caught its own bug rather than the product's.
NOW = datetime.now(timezone.utc)
FAILS = []


def ok(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        FAILS.append(label)


def ago(hours):
    return (NOW - timedelta(hours=hours)).isoformat()


def _conv(zcid, platform="instagram", hours=1):
    store.upsert_conversation(space=SPACE, zcid=zcid, platform=platform, participant="Dana",
                              account_id="acct-1", last_inbound_at=ago(hours))
    return store.get_conversation(SPACE, zcid)


# ── 1. the sentence, before anybody types ────────────────────────────────────────────────
print("\ntest_the_state_is_said_in_words_a_buyer_can_act_on")

o = window.explain("instagram", ago(1), NOW)
ok("an open window says so plainly", o["state"] == "open" and "reply now" in o["headline"], str(o))

c = window.explain("instagram", ago(30), NOW)
ok("a shut window names the reason, not a code",
   c["state"] == "closed" and "24 hours" in c["headline"], str(c))
ok("...AND IT NEVER SAYS YOU MAY NOT", c["can_try"] is True and "still send" in c["detail"], str(c))

t = window.explain("messenger", ago(30), NOW)
ok("the tagged lane warns about promotion, which code cannot enforce",
   t["state"] == "tagged" and "No offers" in t["detail"], str(t))

m = window.explain("messenger", ago(24 * 9), NOW)
ok("past the 7-day tag lane it names THE LAST DOOR THAT SHUT, not the first",
   "7 days" in m["headline"], m["headline"])

lim = window.explain("tiktok", ago(60), NOW)
ok("TikTok degrades rather than closing, and the sentence says how much is left",
   lim["state"] == "limited" and "3 more" in lim["headline"], str(lim))

e = window.explain("email", ago(1), NOW)
ok("email says the box drafts and the PERSON sends — a product fact, not a permission",
   e["state"] == "no_lane" and e["can_try"] is False and "you send it" in e["headline"].lower(),
   str(e))

u = window.explain("whatsapp", ago(1), NOW)
ok("AN UNWRITTEN RULE IS OURS, AND IS SAID AS OURS — never 'wait out a window'",
   u["state"] == "unknown" and "not switched on" in u["headline"]
   and "window" not in u["detail"].lower(), str(u))
ok("...and the channel is named the way a person writes it", "Whatsapp" in u["headline"], u["headline"])

n = window.explain("instagram", None, NOW)
ok("no inbound on record explains reply-only, and still lets them try",
   n["state"] == "closed" and n["can_try"] is True, str(n))

f = window.explain("instagram", (NOW + timedelta(hours=5)).isoformat(), NOW)
ok("a future timestamp is admitted as unreadable, never dressed up as a window",
   "cannot read the time" in f["headline"], str(f))

ok("every state a screen can meet is one of six known strings",
   {window.explain(p, ago(h), NOW)["state"]
    for p, h in (("instagram", 1), ("instagram", 30), ("messenger", 30), ("tiktok", 60),
                 ("email", 1), ("whatsapp", 1))} <= {"open", "tagged", "limited", "closed",
                                                     "no_lane", "unknown"})

print("\ntest_the_developer_sentence_and_the_buyer_sentence_stay_separate")
d = window.decide("whatsapp", ago(1), NOW)
ok("`decide`'s reason is unchanged — it is what a dev reads in a queue row",
   "nobody has read" in d["reason"], d["reason"])
_x = window.explain("whatsapp", ago(1), NOW)
ok("...and neither sentence a SCREEN binds to quotes it back at them",
   "nobody has read" not in _x["headline"] + _x["detail"], _x["headline"] + _x["detail"])
ok("...while `reason` still rides through for the log, where that wording belongs",
   "nobody has read" in _x["reason"])


# ── 2. THE NEGATIVE: none of this may ever stop a send ───────────────────────────────────
print("\ntest_nothing_here_blocks_a_send")

_conv("c-shut", "instagram", hours=72)          # our clock says the window shut three days ago
before = len(INBOX.sent)
res = reply.send_reply(space=SPACE, zcid="c-shut", text="hello", user_id=U, nonce="n-shut")
ok("A SEND OUTSIDE OUR WINDOW STILL REACHES THE VENDOR", len(INBOX.sent) == before + 1)
ok("...and is reported as sent, because it was", res["status"] == "ok", str(res))

src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "marketing/customer_voice/inbox/reply.py")).read()
pre_send = src.split("zernio.client(sp).inbox.send")[0]
ok("and no window check was added BEFORE the vendor call, in the source itself",
   "window.decide" not in pre_send and "window.explain" not in pre_send)


# ── 3. the refusal, in words — and only when our clock agrees ────────────────────────────
print("\ntest_a_window_refusal_is_translated_only_when_our_own_clock_agrees")


def _refuse_with(msg):
    INBOX.send_impl = lambda cid, aid, text, tag: (_ for _ in ()).throw(ZernioError(msg))


_conv("c-agree", "instagram", hours=72)
_refuse_with("(#10) This message is sent outside of allowed window. code 2018278")
try:
    reply.send_reply(space=SPACE, zcid="c-agree", text="hi", user_id=U, nonce="n-agree")
    said = ""
except reply.ReplyRefused as ex:
    said = str(ex)
ok("the vendor's window refusal becomes a sentence about time",
   "24 hours since they wrote to you" in said, said)
ok("...and the raw vendor string is not what the person reads",
   "2018278" not in said and "(#10)" not in said, said)
ok("...and it names who refused", "Instagram would not take" in said, said)

# THE CASE THAT DECIDES THE DESIGN. The vendor says window; our column says the customer wrote an
# hour ago. Our inference is the thing that is wrong — a lagging poller, a stale watermark — and
# inventing a confident explanation from it would send somebody looking at the wrong problem.
_conv("c-disagree", "instagram", hours=1)
_refuse_with("(#10) This message is sent outside of allowed window. code 2018278")
try:
    reply.send_reply(space=SPACE, zcid="c-disagree", text="hi", user_id=U, nonce="n-dis")
    said = ""
except reply.ReplyRefused as ex:
    said = str(ex)
ok("WHEN OUR CLOCK DISAGREES WITH THE VENDOR, THE VENDOR'S OWN WORDS COME BACK",
   "2018278" in said and "24 hours since" not in said, said)

_conv("c-other", "instagram", hours=72)
_refuse_with("(#200) Requires pages_messaging permission")
try:
    reply.send_reply(space=SPACE, zcid="c-other", text="hi", user_id=U, nonce="n-other")
    said = ""
except reply.ReplyRefused as ex:
    said = str(ex)
ok("a refusal that is NOT about the window is never dressed as one",
   "pages_messaging" in said and "since they wrote" not in said, said)

# A recogniser that swallowed an indeterminate would be a second message to a customer.
_conv("c-indet", "instagram", hours=72)
INBOX.send_impl = lambda cid, aid, text, tag: (_ for _ in ()).throw(
    ZernioError("timed out, outside of allowed window", indeterminate=True))
try:
    reply.send_reply(space=SPACE, zcid="c-indet", text="hi", user_id=U, nonce="n-indet")
    kind = "sent"
except reply.ReplyIndeterminate:
    kind = "indeterminate"
except reply.ReplyRefused:
    kind = "refused"
ok("AN INDETERMINATE IS STILL INDETERMINATE, whatever words it carries", kind == "indeterminate", kind)

INBOX.send_impl = lambda cid, aid, text, tag: {"message_id": f"out-{len(INBOX.sent)}"}

print("\n— and this file cannot silently fall out of CI —")
import pathlib  # noqa: E402

_wf = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml"
if _wf.is_file():
    ok("test_send_window_says_it_first is in the workflow's suite list",
       "test_send_window_says_it_first" in _wf.read_text())
else:
    print("  --   no workflow file here (a box, not the repo) — nothing to check")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
