"""Email is ON — the rule that switched it on, and the send it must never authorise.

WHAT CHANGED. `email_channel.py` has read a mailbox since #1230, but the channel sat outside
`channels.POLLED` because `test_inbox_instagram` requires a send rule beside every polled channel
— deliberately, so auto-reply on a new channel can never arrive by accident. This suite covers the
rule that closed that pairing, and the three properties that make switching it on safe.

THE RULE IS NOT A WINDOW. Email has no platform clock: nobody revokes your right to answer an
email, there is no 24-hour rule and no tag lane, because there is no platform between two
mailboxes to impose one. What blocks the send is THIS BOX — there is no SMTP path anywhere in the
repository, `inbox/reply.py` sends through the social vendor and nothing else, and send policy is
the owner's word (CLAUDE.md), not a value a dev picks while wiring a channel.

AND THE DRAFTER NEVER CONSULTS THE WINDOW, which is why blocking the send costs nothing: the box
ingests and drafts exactly as it does on every other channel, and a person sends from their own
mail app. That asymmetry is asserted here so nobody "fixes" it into a gate later.

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

# ── the rule refuses, and says why ───────────────────────────────────────────────────────────
print("\n— it refuses for the BOX's reason, never a closed clock —")
fresh = window.decide("email", NOW.isoformat(), NOW)
ok("a message that arrived this instant still cannot be auto-sent",
   fresh["decision"] == window.BLOCKED, str(fresh))
ok("...and the result SAYS it is a missing lane, not a shut window",
   fresh.get("no_send_lane") is True, str(fresh))
ok("...with a reason a person could act on", "mail app" in fresh["reason"], fresh["reason"])
ok("...and a citation, like every other rule here", bool(fresh.get("cite")), str(fresh))

old = window.decide("email", (NOW - timedelta(days=400)).isoformat(), NOW)
ok("a year-old thread gets the SAME answer — there is no clock to expire",
   old["decision"] == window.BLOCKED and old["reason"] == fresh["reason"], str(old))

ok("allowed_send refuses it too — the door handler.py opens",
   window.allowed_send(NOW.isoformat(), NOW, platform="email") == window.BLOCKED)

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

from marketing.customer_voice.drafter import draft as _draft  # noqa: E402
from marketing.customer_voice.drafter import store as _dstore  # noqa: E402

src = inspect.getsource(_draft) + inspect.getsource(_dstore)
ok("THE DRAFTER NEVER IMPORTS THE WINDOW — so email ingests AND drafts",
   "window" not in src, "the drafter now consults window.py; email would stop drafting")
ok("...and it still cannot send, which is what makes that safe",
   "send_reply" not in src and "smtplib" not in src)

# ── nothing in the repo can put mail in an outbox ────────────────────────────────────────────
print("\n— there is no SMTP path, which is the rule's stated reason —")
hits = []
for f in list((ROOT / "marketing" / "customer_voice").rglob("*.py")) + \
         list((ROOT / "core").rglob("*.py")):
    try:
        t = f.read_text()
    except Exception:                        # noqa: BLE001
        continue
    if "smtplib" in t or "SMTP_SSL" in t:
        hits.append(str(f.relative_to(ROOT)))
ok("no smtplib anywhere in the box's inbox or core — the rule's reason is still true",
   not hits, str(hits))

# ── the screen tells the two refusals apart ──────────────────────────────────────────────────
print("\n— and the screen does not report a finished decision as a gap —")
from marketing.customer_voice import app as voice_app  # noqa: E402

ok("email reads as a written rule with the send elsewhere", voice_app._no_send_lane("email"))
ok("...and a channel nobody has ruled on does NOT", not voice_app._no_send_lane("whatsapp"))
ok("messenger is neither — it has a real window", not voice_app._no_send_lane("messenger"))

tags = dict((t, cls) for t, cls in voice_app._tag_list(
    {"platform": "email", "last_inbound_at": NOW.isoformat(), "message_count": 2}))
ok("an email row says where the send happens", "Send in your mail app" in tags, str(tags))
ok("...and does NOT say there is no rule", "No reply rule" not in tags, str(tags))
ok("...and is not painted as an error", tags.get("Send in your mail app") == "warn", str(tags))

print("\n" + ("FAILED: " + ", ".join(FAILS) if FAILS else "ALL OK"))
sys.exit(1 if FAILS else 0)
