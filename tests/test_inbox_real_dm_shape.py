"""A real Zernio DM reaches the box — the shape the vendor actually sends.

WHY THIS EXISTS. OSDev1's read-only probe of the live keys, 2026-09-16: messages carry
`direction: "incoming" | "outgoing"` and NO `fromMe` field. `_inbound_msgs` accepted only
("in", "inbound", "received") and otherwise fell back to `fromMe is False`, which on an absent
field evaluates `None is False`. It kept **0 of 22** real messages across three live accounts.

Two consequences, and the second is worse than the first:
  · the live box showed zero conversations — the product looked broken to its first customer
  · the opt-out scan reads the SAME list, so a STOP sent by DM was never seen. Not a missing
    feature: continuing to message somebody who said stop.

Every fixture below uses the field names from that probe — accountId, conversationId, direction,
senderId, message, sentAt — rather than names invented here, because the whole bug was a
difference between what we assumed and what arrives.

Run: python tests/test_inbox_real_dm_shape.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "dm.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state  # noqa: E402

state.init_db()

from marketing.customer_voice.inbox import poller  # noqa: E402

FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


def live_msg(direction: str, text: str, mid: str = "m1") -> dict:
    """A message in the shape the VENDOR sends — no fromMe, direction spelled their way.

    BOTH CLOCKS, BECAUSE THE VENDOR SENDS BOTH. OSDev1 measured the live account on 2026-09-17:
    `createdAt` is present on all 58 messages, ISO-8601 Z, and EQUALS `sentAt` on every one. This
    fixture originally carried `sentAt` alone, which made it look as though reading `createdAt`
    would find nothing — and that inference, which I published, was wrong. A fixture thinner than
    the real payload is how the last bug hid; it is not allowed to be the way the next one does.
    """
    when = "2026-09-16T10:00:00.000Z"
    return {"_id": mid, "accountId": "acc_live", "conversationId": "conv_live",
            "direction": direction, "senderId": "sender_live",
            "message": text, "sentAt": when, "createdAt": when}


# ── the bug, in one assertion ────────────────────────────────────────────────────────────────
print("\n— the shape that was silently dropped —")
incoming = live_msg("incoming", "do you take walk-ins?")
ok("A REAL INCOMING DM IS INBOUND", poller._is_inbound(incoming), str(incoming))
ok("...and it survives the list filter that feeds intake AND the STOP scan",
   poller._inbound_msgs([incoming]) == [incoming])

outgoing = live_msg("outgoing", "we do, until 6pm", mid="m2")
ok("an outgoing reply of OURS is never inbound", not poller._is_inbound(outgoing), str(outgoing))

page = [incoming, outgoing]
ok("a mixed page keeps exactly the customer's half",
   [m["_id"] for m in poller._inbound_msgs(page)] == ["m1"],
   str([m["_id"] for m in poller._inbound_msgs(page)]))
ok("...and the newest inbound is the customer's message, not ours",
   (poller._newest_inbound(page) or {}).get("_id") == "m1")

# ── "outgoing" contains "in" ─────────────────────────────────────────────────────────────────
print("\n— the substring trap, which is why this matches by equality —")
ok("'outgoing' is NOT read as inbound despite containing the letters 'in'",
   not poller._is_inbound({"direction": "outgoing", "message": "ours"}))
ok("'OUTGOING' in any case is still outbound",
   not poller._is_inbound({"direction": "OUTGOING", "message": "ours"}))
ok("...and whitespace does not smuggle one through",
   not poller._is_inbound({"direction": "  outgoing  ", "message": "ours"}))

# ── outbound is authoritative ────────────────────────────────────────────────────────────────
print("\n— a contradictory pair resolves OUTBOUND, so the box can never answer itself —")
ok("direction=outgoing WINS over fromMe=False",
   not poller._is_inbound({"direction": "outgoing", "fromMe": False, "message": "ours"}),
   "an outbound message read as inbound makes the box reply to its own words")

# ── the older shape still works, and unknowns fail closed ────────────────────────────────────
print("\n— the shapes that worked before still do, and anything unreadable does not —")
for d in ("in", "inbound", "received", "incoming"):
    ok(f"direction={d!r} is inbound", poller._is_inbound({"direction": d, "message": "x"}))
ok("fromMe=False with no direction is still inbound (the older shape)",
   poller._is_inbound({"fromMe": False, "message": "x"}))
ok("fromMe=True is not", not poller._is_inbound({"fromMe": True, "message": "x"}))
ok("A MESSAGE WE CANNOT READ AT ALL IS NOT INBOUND — fail closed",
   not poller._is_inbound({"message": "x"}),
   "a false positive makes the box talk to itself; a false negative delays one reply")
ok("...and neither is an unrecognised direction",
   not poller._is_inbound({"direction": "carrier-pigeon", "message": "x"}))

# ── the compliance half: a STOP by DM is seen again ──────────────────────────────────────────
print("\n— a STOP sent by DM reaches the opt-out scan —")
from marketing.customer_voice.inbox.handler import _is_stop  # noqa: E402

stop = live_msg("incoming", "STOP", mid="m3")
found = next((m for m in poller._inbound_msgs([outgoing, stop]) if _is_stop(poller._body(m))), None)
ok("A STOP IN THE VENDOR'S OWN SHAPE IS FOUND", found is not None and found["_id"] == "m3",
   "this is the scan that was blind — continuing to message someone who said stop")
ok("...and the body is read from the vendor's `message` key",
   poller._body(stop) == "STOP", poller._body(stop))
ok("...while a STOP we sent OURSELVES is not mistaken for theirs",
   next((m for m in poller._inbound_msgs([live_msg("outgoing", "STOP", mid="m4")])
         if _is_stop(poller._body(m))), None) is None)

# ── the clock, which decides whether ANY reply is allowed ───────────────────────────────────
print("\n— the vendor's own timestamp is read, or every conversation refuses to reply —")
older = live_msg("incoming", "first", mid="m10")
newer = live_msg("incoming", "second", mid="m11")
newer["sentAt"] = newer["createdAt"] = "2026-09-16T11:00:00.000Z"
ok("the NEWEST inbound is picked by the vendor's sentAt, not by list order",
   (poller._newest_inbound([newer, older]) or {}).get("_id") == "m11",
   "an empty sort key makes max() return whichever the vendor listed first")
ok("...and reversing the page does not change the answer",
   (poller._newest_inbound([older, newer]) or {}).get("_id") == "m11")
# THE CORRECTION, PINNED. The live payload carries BOTH clocks and they agree (OSDev1, 58/58 on
# 2026-09-17), so a fallback that only ever saw `sentAt` is untested dead weight — and the claim
# that `createdAt` found nothing on real data was mine and was wrong. Both arms are exercised.
_created_only = {k: v for k, v in newer.items() if k != "sentAt"}
ok("a message carrying ONLY createdAt still reads a clock — the fallback is not decoration",
   str(poller._f(_created_only, *poller._SENT_AT_KEYS)).startswith("2026-09-16T11"),
   str(poller._f(_created_only, *poller._SENT_AT_KEYS)))
ok("...and on the live shape the two agree, so the order changes nothing it reads",
   poller._f(newer, "sentAt") == poller._f(newer, "createdAt"))
ok("a message with NO readable clock sorts on empty — the failure the chain exists to prevent",
   poller._f({"_id": "x"}, *poller._SENT_AT_KEYS) is None)
ok("sentAt is read at all", str(poller._f(newer, *poller._SENT_AT_KEYS)).startswith("2026-09-16T11"),
   str(poller._f(newer, *poller._SENT_AT_KEYS)))
ok("...and the older createdAt shape still works, so nothing that polled before breaks",
   str(poller._f({"createdAt": "2026-01-01T00:00:00Z"}, *poller._SENT_AT_KEYS)).startswith("2026-01-01"))

# AN EMPTY INBOUND CLOCK IS NOT A COSMETIC BUG. `window.decide` reads it as "no inbound message on
# record — every channel here is reply-only" and BLOCKS. That is every real conversation refusing
# to send, with "No inbound yet" on every row.
from marketing.customer_voice.inbox import window  # noqa: E402

# THE CLOCK HERE IS RELATIVE TO NOW, not the fixed date above. That date is fine for proving the
# poller READS `sentAt`; it is not fine for asking whether the window is open, because the window
# is measured from today. Pinned to 2026-09-16 this passed for seven days and then failed every PR
# on 2026-09-23, the day Messenger's 7-day window closed on it. Same shape, a fresh clock.
from datetime import datetime, timedelta, timezone  # noqa: E402

_stamp = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
_live = dict(newer, sentAt=_stamp, createdAt=_stamp)
ok("A CONVERSATION WITH A REAL sentAt IS ALLOWED TO REPLY",
   window.decide("messenger", poller._f(_live, *poller._SENT_AT_KEYS))["decision"] != window.BLOCKED)
ok("...whereas the empty clock this used to store is BLOCKED, which is what shipped",
   window.decide("messenger", "")["decision"] == window.BLOCKED)

print("\n" + ("FAILED: " + ", ".join(FAILS) if FAILS else "ALL OK"))
sys.exit(1 if FAILS else 0)
