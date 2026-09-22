"""Email is ON — the rule that switched it on, and the send it may now authorise, by hand only.

WHAT CHANGED. `email_channel.py` has read a mailbox since #1230, but the channel sat outside
`channels.POLLED` because `test_inbox_instagram` requires a send rule beside every polled channel
— deliberately, so auto-reply on a new channel can never arrive by accident. This suite covers the
rule that closed that pairing, and the three properties that make switching it on safe.

THE RULE IS NOT A WINDOW. Email has no platform clock: nobody revokes your right to answer an
email, there is no 24-hour rule and no tag lane, because there is no platform between two
mailboxes to impose one.

WHAT USED TO BLOCK THE SEND WAS THIS BOX, and it no longer does. The rule carried
`no_send_lane: True` with its reason as data — "there is no SMTP path at all, and the owner has
not ruled on an email send policy" — and both halves are spent. Owner, 2026-09-22: *"There's
gotta be a way to send email as well. Something is not built correctly."* `email_channel.send`
is the path, over the buyer's own mailbox, from their own address.

SO THE ASYMMETRY THIS SUITE GUARDS HAS MOVED, AND IT STILL HAS TWO SIDES. A PERSON may reply
whenever they like, because there is no window to wait out and never was. The BOX still may not
send on this channel unattended: `decide` — the one question `handler.py`'s opener door asks —
refuses email and says which "no" it is. Delay-send is the owner's opt-in Phase 2 and has not
shipped. Both halves are asserted below, because a suite that only checked one of them would
pass while the box quietly gained the ability to mail a stranger on its own.

Run: python tests/test_email_channel_live.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "emailrule.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state  # noqa: E402

state.init_db()

from marketing.customer_voice.inbox import channels, window  # noqa: E402

FAILS: list[str] = []
NOW = datetime.now(timezone.utc)


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


# ── the pairing ──────────────────────────────────────────────────────────────────────────────
print("\n— polled AND ruled, or neither —")
ok("email is polled", any(c.key == "email" for c in channels.POLLED),
   str([c.key for c in channels.POLLED]))
ok("...over IMAP, not as a vendor platform token",
   next(c for c in channels.POLLED if c.key == "email").vendor == channels.IMAP)
ok("...and a send rule is written beside it", "email" in window._RULES)
missing = [c.key for c in channels.POLLED if c.key not in window._RULES]
ok("EVERY polled channel still has one — the guard this had to satisfy", not missing, str(missing))

# ── the UNATTENDED box still may not send, and says which "no" that is ───────────────────────
print("\n— the box does not mail anyone on its own, and never on a clock —")
fresh = window.decide("email", NOW.isoformat(), NOW)
ok("a message that arrived this instant still cannot be auto-sent",
   fresh["decision"] == window.BLOCKED, str(fresh))
ok("...and the result says WHICH no: there is no window, not that a window shut",
   fresh.get("no_window") is True and fresh.get("no_send_lane") is None, str(fresh))
# THE REASON MATTERS AS MUCH AS THE VERDICT. The generic branch below this one means "nobody has
# documented this channel", and borrowing its sentence would read as our homework being late
# rather than as a decision the owner has taken.
ok("...with a reason that names the real one — unattended sending, not a missing rule",
   "unattended" in fresh["reason"] and "documented" not in fresh["reason"], fresh["reason"])
ok("...and a citation, like every other rule here", bool(fresh.get("cite")), str(fresh))

old = window.decide("email", (NOW - timedelta(days=400)).isoformat(), NOW)
ok("a year-old thread gets the SAME answer — there is no clock to expire",
   old["decision"] == window.BLOCKED and old["reason"] == fresh["reason"], str(old))

ok("allowed_send refuses it too — the door handler.py opens",
   window.allowed_send(NOW.isoformat(), NOW, platform="email") == window.BLOCKED)

# ── AND A PERSON MAY SEND, WHICH IS THE HALF THAT JUST CHANGED ───────────────────────────────
print("\n— but a person can answer their own customer, whenever they like —")
ok("the send path is no longer refused a lane on email",
   window.no_send_lane_why("email") == "", repr(window.no_send_lane_why("email")))
for _age in (0, 26, 24 * 400):
    _e = window.explain("email", (NOW - timedelta(hours=_age)).isoformat(), NOW)
    ok(f"...at {_age}h old the screen says they can reply, not that a window shut",
       _e["state"] == "open" and _e["can_try"] is True, str(_e["state"]))
    ok(f"...and says nothing about a window closing ({_age}h)",
       "refuses" not in _e["detail"] and "while since" not in _e["headline"], _e["headline"])
_e = window.explain("email", (NOW - timedelta(days=400)).isoformat(), NOW)
ok("...and it says the reply goes from THEIR address, which is the fact that changed",
   "own address" in _e["detail"], _e["detail"])

# ── the branches the header documented and the code did not have ─────────────────────────────
print("\n— free_hours: None no longer raises inside the compliance spine —")
window._RULES["__probe__"] = {"free_hours": None, "tag_hours": None, "cite": "x"}
try:
    d = window.decide("__probe__", NOW.isoformat(), NOW)
    ok("a rule with no documented window RETURNS, rather than raising a TypeError",
       d["decision"] == window.BLOCKED, str(d))
    ok("...and does not claim to be the no-rule-written branch",
       "no send window is documented" in d["reason"], d["reason"])
    ok("...nor the no-send-lane one", d.get("no_send_lane") is None, str(d))
except Exception as e:                       # noqa: BLE001 — this is the whole assertion
    ok("a rule with no documented window RETURNS, rather than raising a TypeError", False, repr(e))
finally:
    window._RULES.pop("__probe__", None)

print("\n— and an unknown channel still refuses, which is the most important line in that file —")
for plat in ("whatsapp", "sms", "", "EMAIL_TYPO"):
    d = window.decide(plat, NOW.isoformat(), NOW)
    ok(f"{plat!r} is blocked", d["decision"] == window.BLOCKED, str(d))
    ok(f"...and is NOT mistaken for the written-rule case ({plat!r})",
       d.get("no_send_lane") is None, str(d))

# ── the drafter does not consult the window, on purpose ──────────────────────────────────────
print("\n— blocking the send does not block the draft —")
import inspect  # noqa: E402
import re as _re_guard  # noqa: E402

from marketing.customer_voice.drafter import draft as _draft  # noqa: E402
from marketing.customer_voice.drafter import store as _dstore  # noqa: E402

src = inspect.getsource(_draft) + inspect.getsource(_dstore)
# THE RULE IS ABOUT AN IMPORT, NOT ABOUT A WORD. This was `"window" not in src`, which is a
# substring scan over the whole source — so the day somebody wrote "a window of rows" in a
# COMMENT it went red for prose while the real rule was never in danger (2026-09-22, OSDev1).
# A guard that fails on English is a guard people learn to work around, and the rule it protects
# is worth keeping sharp: the drafter must not consult `window.py`, because that module carries
# `no_send_lane: True` for email and consulting it would stop email being DRAFTED — which is a
# different thing from being sent, and the whole reason drafting is safe on a channel we cannot
# send on yet.
_WINDOW_USE = _re_guard.compile(r"^\s*(from\s+\S*\bwindow\b|import\s+\S*\bwindow\b|"
                                r"from\s+\S+\s+import\s+[^\n]*\bwindow\b)", _re_guard.M)
ok("THE DRAFTER NEVER IMPORTS THE WINDOW — so email ingests AND drafts",
   _WINDOW_USE.search(src) is None and "window." not in src,
   "the drafter now consults window.py; email would stop drafting")
ok("...and it still cannot send, which is what makes that safe",
   "send_reply" not in src and "smtplib" not in src)

# ── EXACTLY ONE PLACE MAY PUT MAIL IN AN OUTBOX ──────────────────────────────────────────────
#
# THIS CHECK HAS NOW OUTLIVED TWO PREMISES, AND THE PROPERTY UNDER IT IS THE SAME ONE.
#
#   v1  `"smtplib" not in t` anywhere — a PROXY for "email cannot send", correct while the
#       answer was zero.
#   v2  #1435 added a credential check that opens an SMTP session to ask whether the buyer's
#       password may send at all, and stops at AUTH. The check became "nothing holds a send
#       CALL", which is the capability rather than the import.
#   v3  this PR. The box sends email now, so "nothing sends" is simply false.
#
# WHAT SURVIVES ALL THREE: there is exactly ONE send path, it is named, and everything else is
# refused. That is the property that was ever worth guarding — a second way to put a message on
# the wire is how a customer gets the same reply twice, and it would not go through the ledger
# claim, the opt-out check or the idempotency key that `reply.py` spends fifty lines on.
print("\n— exactly one place may hand a message to a mail server —")
MAY_SEND = {
    # The send itself, reached ONLY through reply.py, which claims the ledger key first.
    "marketing/customer_voice/inbox/email_channel.py",
}
MAY_OPEN_A_SESSION = MAY_SEND | {
    # Asks whether a credential MAY send. Cannot send: no sendmail, no send_message, no recipient.
    "core/vendors/mailbox/verify.py",
}
openers, senders = [], []
for f in list((ROOT / "marketing" / "customer_voice").rglob("*.py")) + \
         list((ROOT / "core").rglob("*.py")):
    try:
        t = f.read_text()
    except Exception:                        # noqa: BLE001
        continue
    rel = str(f.relative_to(ROOT))
    if ("smtplib" in t or "SMTP_SSL" in t) and rel not in MAY_OPEN_A_SESSION:
        openers.append(rel)
    if (".sendmail(" in t or ".send_message(" in t) and rel not in MAY_SEND:
        senders.append(rel)
ok("only the mail channel may hand a message to a mail server", not senders, str(senders))
ok("...and only it and the credential check may even open an SMTP session",
   not openers, str(openers))
# THE CREDENTIAL CHECK IS HELD TO THE OLD RULE STILL. Being allowed to open a session is not
# being allowed to send down it, and #1435's whole safety argument is that it stops at AUTH.
_verify = (ROOT / "core" / "vendors" / "mailbox" / "verify.py").read_text()
ok("...and the credential check still cannot send — it authenticates and quits",
   not any(c in _verify for c in (".sendmail(", ".send_message(")))
# AND THE ONE SEND PATH GOES THROUGH THE LEDGER. `reply.py` is the only caller, which is what
# puts every send behind the claim, the opt-out refusal and the idempotency key.
_callers = [str(f.relative_to(ROOT))
            for f in (ROOT / "marketing" / "customer_voice").rglob("*.py")
            if "email_channel.send(" in f.read_text()
            or "email_channel.send (" in f.read_text()]
ok("...and `reply.py` is its only caller, so no send skips the ledger",
   _callers == ["marketing/customer_voice/inbox/reply.py"], str(_callers))


# ── the screen tells the two refusals apart ──────────────────────────────────────────────────
print("\n— and the screen does not report a finished decision as a gap —")
from marketing.customer_voice import app as voice_app  # noqa: E402

# THE SCREEN NO LONGER SENDS ANYONE ELSEWHERE. `_no_send_lane` is derived from the rule rather
# than hard-coded, so retiring the flag retires the pill and un-hides the compose box with no
# screen edit — which is exactly why it was written that way.
ok("email is no longer a channel whose send happens somewhere else",
   not voice_app._no_send_lane("email"))
ok("...and a channel nobody has ruled on is still NOT that either — it is a gap, said as a gap",
   not voice_app._no_send_lane("whatsapp"))
ok("messenger is neither — it has a real window", not voice_app._no_send_lane("messenger"))

tags = dict((t, cls) for t, cls in voice_app._tag_list(
    {"platform": "email", "last_inbound_at": NOW.isoformat(), "message_count": 2,
     "has_inbound": True}))
ok("an email row no longer tells the buyer to go to their mail app",
   "Send in your mail app" not in tags, str(tags))
ok("...and does NOT say there is no rule — there is one, and it is written",
   "No reply rule" not in tags, str(tags))
ok("...and carries no window pill either, because email has no window",
   "Window closed" not in tags and not any("while" in t for t in tags), str(tags))

# AND THE MACHINERY THAT DREW THAT PILL IS STILL CORRECT, just unused. It is the shape the next
# channel with a written no-send rule will need, and `decide` still carries the flag for it — so
# this asserts the branch rather than deleting a thing that works.
ok("the no-send-lane branch still fires when a rule actually says so",
   voice_app._tag_list.__doc__ is not None
   and "no_send_lane" in open(os.path.join(ROOT, "marketing/customer_voice/inbox/window.py")).read())

print("\n" + ("FAILED: " + ", ".join(FAILS) if FAILS else "ALL OK"))
sys.exit(1 if FAILS else 0)
