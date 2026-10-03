"""The box's AI is never wasted on a machine, and "waiting on you" is people (plan #1857 H7).

On 10-02 the drafter wrote 35 replies, some to automated mail; "waiting on you" read 120, the oldest 317 days. Two
classifiers judged the same mail: the inbox's (which marks a thread automated at ingest) and the drafter's
(drafter/who_wrote.py). Now there is one, who_wrote, extended.

Held here:
  1. each skip kind is a reason: the box's own address, no-reply, mailer-daemon, a delivery report
     (multipart/report), an auto-reply (Auto-Submitted, X-Autoreply, Precedence: auto_reply); and
     `Auto-Submitted: no`, the one value that means a person, is not;
  2. the inbox asks the same classifier, so its mark and the drafter's skip agree;
  3. "waiting on you" and the waiting list leave out automated threads; a thread never judged still counts;
  4. a cold pitch (its only machine sign List-Unsubscribe, which cold-email tools add) is not waiting and is not
     drafted, until pitch-back is on (OSDev4's F4 #1852): then it reaches the drafter. A newsletter never does.
Run: python tests/test_the_ai_is_never_wasted.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "h7.db")
os.environ["DISPATCH_BEARER_TOKEN"], os.environ["DASH_TOKEN"] = "bearer", "pw"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import spaces as _spaces, state  # noqa: E402

state.init_db()
_spaces.space_by_name = lambda name, allow_default_alias=True: {"name": name, "key": "k"}
SPACE = _spaces.DEFAULT
import marketing.customer_voice  # noqa: E402,F401 — registers the machine's tables
from marketing.customer_voice.drafter import store as drafts, who_wrote  # noqa: E402
from marketing.customer_voice.inbox import email_channel, store  # noqa: E402

state.init_db()
_failed = 0


def ok(label: str, cond: bool, detail="") -> None:
    global _failed
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  — {str(detail)[:400]}"))
    if not cond:
        _failed += 1


OURS = {"maria@glowmedspa.com"}
W = lambda sender, headers=None: who_wrote.why(sender, headers, ours=OURS)  # noqa: E731

print("\n1. each skip kind is a reason —")
for label, sender, headers in (
        ("the box's own address", "maria@glowmedspa.com", None),
        ("a no-reply sender", "no-reply@squarespace.com", None),
        ("mailer-daemon", "mailer-daemon@googlemail.com", None),
        ("a delivery report (a read receipt from a person's own client)", "dana@reyesfitness.com",
         {"content-type": "multipart/report; report-type=disposition-notification"}),
        ("an auto-reply (Auto-Submitted)", "dana@reyesfitness.com", {"auto-submitted": "auto-replied"}),
        ("an auto-reply (X-Autoreply)", "dana@reyesfitness.com", {"x-autoreply": "yes"}),
        ("an auto-reply (Precedence: auto_reply)", "dana@reyesfitness.com", {"precedence": "auto_reply"}),
        ("a newsletter", "news@shopify.com", {"list-unsubscribe": "<mailto:u@x>", "list-id": "<news.shopify.com>"})):
    ok(f"{label}: skipped, with its reason", bool(W(sender, headers)), W(sender, headers))
ok("AUTO-SUBMITTED: NO is a person (RFC 3834), and so is a plain customer",
   W("dana@reyesfitness.com", {"auto-submitted": "no"}) == "" and W("dana@reyesfitness.com") == "")

print("\n2. one classifier: the inbox asks it —")
pitch = EmailMessage()
pitch["From"], pitch["List-Unsubscribe"] = "jordan@growthleads.io", "<https://growthleads.io/u/1>"
news = EmailMessage()
news["From"], news["List-Unsubscribe"], news["List-Id"] = "news@shopify.com", "<mailto:u@x>", "<news.shopify.com>"
bounce = EmailMessage()
bounce["From"], bounce["Content-Type"] = "dana@reyesfitness.com", "multipart/report; report-type=delivery-status"
ok("A COLD PITCH (only List-Unsubscribe) IS LIST_ONLY, a newsletter AUTOMATED, a bounce AUTOMATED, a person PERSON",
   [email_channel.automated_level(m, a) for m, a in ((pitch, "jordan@growthleads.io"), (news, "news@shopify.com"),
                                                     (bounce, "dana@reyesfitness.com"))]
   + [email_channel.automated_level(None, "dana@reyesfitness.com")] == [2, 1, 1, 0])
ok("AN `alerts@` BUSINESS: the drafter won't pay to answer it on its address, but waiting never hides it",
   W("alerts@realbusiness.com") != "" and email_channel.automated_level(None, "alerts@realbusiness.com") == 0
   and email_channel.automated_level(None, "alerts-noreply@linkedin.com") == 1)
ok("...and is_automated agrees with who_wrote", email_channel.is_automated(news, "news@shopify.com")
   and not email_channel.is_automated(None, "dana@reyesfitness.com"))

print("\n3. waiting on you is people —")
NOW = datetime.now(timezone.utc)


def conv(zcid: str, automated, *, platform="email", ago_h=1.0):
    at = (NOW - timedelta(hours=ago_h)).isoformat()
    store.upsert_conversation(space=SPACE, zcid=zcid, platform=platform, participant=zcid, last_inbound_at=at,
                              account_id="acct-1")
    store.record_message(space=SPACE, zcid=zcid, zmid=f"in-{zcid}", direction="in", sent_by="contact",
                         body="Hello, are you open on Saturday?", sent_at=at)
    if automated is not None:
        store.mark_automated(SPACE, zcid, automated)


conv("dana", 0)
conv("newsletter", 1, ago_h=24 * 317)
conv("cold-pitch", 2)
conv("never-judged", None)
waiting = {r["zernio_conversation_id"] for r in store.list_conversations(SPACE, waiting=True)}
ok("WAITING IS THE PERSON AND THE THREAD NOBODY HAS JUDGED, not the newsletter or the cold pitch",
   store.awaiting_reply(SPACE) == 2 and waiting == {"dana", "never-judged"}, (store.awaiting_reply(SPACE), waiting))

print("\ntest_a_cold_pitch_is_still_drafted")
off = {r["zcid"] for r in drafts.needs_a_draft(SPACE, limit=20)}
on = {r["zcid"] for r in drafts.needs_a_draft(SPACE, limit=20, pitch_back=True)}
ok("PITCH-BACK OFF: the cold pitch is not drafted, the newsletter never is", "cold-pitch" not in off
   and "newsletter" not in off and {"dana", "never-judged"} <= off, off)
ok("PITCH-BACK ON: the cold pitch reaches the drafter (F4 #1852 answers it); the newsletter still never does",
   "cold-pitch" in on and "newsletter" not in on, on)

print("\n5. Meters shows what the AI did —")
from core import report  # noqa: E402
from core.dash import review  # noqa: E402

for _ in range(2):
    state.record_spend(task="inbox_draft", model="codex:default", cost_usd=0.0)
state.record_spend(task="brief", model="codex:default", cost_usd=0.0)
with state.connect() as c:                               # one more draft yesterday, inside the cycle
    c.execute("INSERT INTO spend_ledger (ts, task, model, cost_usd) VALUES (?, 'inbox_draft', 'codex:default', 0)",
              ((NOW - timedelta(days=1)).isoformat(),))
today = report.today()
f = report.ai_figures(today, report.now_local())
ok("THINKS PER TASK, TODAY AND THIS CYCLE, from the spend rows' task column",
   f.get("ai:inbox_draft") == {"value": 2, "cycle": 3, "label": "Replies drafted"}
   and f.get("ai:brief", {}).get("value") == 1, f)
html = review._meters({"headline": {"value": "$0", "label": "of $90 this cycle"}, "happened": [], "watch": [],
                       "figures": f})
ok("...ON THE REVIEW'S SPEND SECTION, 'Your AI today', even on a box that spent nothing; counts, no cost",
   "Your AI today" in html and "Replies drafted</b> 2 today" in html and "3 this cycle" in html
   and "$" not in html.split("Your AI today", 1)[1].split("</ul>", 1)[0], html[:600])

print()
print("FAILED" if _failed else "ALL PASSED", _failed)
sys.exit(1 if _failed else 0)
