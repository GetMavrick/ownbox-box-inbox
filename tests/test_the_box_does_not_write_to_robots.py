"""The box does not draft replies to machines, and the Drafts tab does not offer them.

MEASURED ON THE OWNER'S BOX (OSDev1, 2026-09-22): 35 of 62 email drafts were addressed to
automated senders — four LinkedIn job alerts, jm@mercola.com, system@polsia.com,
alert@spaceship.com, invitations@linkedin.com, noreply@mail.heymerit.ai, and the box's OWN
Morning Review. Pressing "send the ones I ticked" would have mailed his business address to 35
robots. A model had already been paid to write every one of those replies.

Independent of any transport, and a launch blocker on its own.

THE ORDER OF EVIDENCE IS THE DESIGN. Headers first — `Auto-Submitted` (RFC 3834 §5) exists so
software can say "do not reply to this", and `List-Id` / `List-Unsubscribe` mark bulk mail. A
sender that sets one has TOLD us. Only with no header do we look at the address, and then
conservatively: `info@`, `sales@`, `hello@`, `contact@`, `support@` and `alerts@` are NOT robots,
because those are exactly the addresses a small business writes from, and never answering a real
customer is the more expensive mistake.

AND THE SQL IS WHERE IT HAS TO HAPPEN. `needs_a_draft` is capped — it is a spend bound — so a
Python filter applied after LIMIT 5 would hand back the same five robots every sweep and starve
every real customer behind them. That is the head-block shape #1436 and #1437 each cost a day.

Run: python tests/test_the_box_does_not_write_to_robots.py
"""
from __future__ import annotations

import email as email_mod
import imaplib
import io
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "robots.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state  # noqa: E402

state.init_db()

from core import box_secrets as bs  # noqa: E402
from marketing.customer_voice.drafter import store as drafts  # noqa: E402
from marketing.customer_voice.inbox import email_channel as ec  # noqa: E402
from marketing.customer_voice.inbox import store  # noqa: E402

FAILS: list[str] = []
SPACE = "acme"
OWNER = "owner@acme.com"
APP_PW = "abcdefghijklmnop"


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


def quiet(fn, *a, **k):
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        return fn(*a, **k)


def msg(mid: str, frm: str, extra: str = "") -> bytes:
    return (f"From: {frm}\nTo: {OWNER}\nSubject: Something\nMessage-ID: {mid}\n{extra}"
            f"Date: Tue, 22 Sep 2026 10:00:00 +0000\nContent-Type: text/plain\n\n"
            f"body text\n").encode()


class FakeIMAP:
    def __init__(self, host, **kw):
        self.messages: dict[int, bytes] = {}

    def login(self, u, p):
        return ("OK", [b""])

    def select(self, folder, readonly=False):
        assert readonly is True
        return ("OK", [b"1"])

    def response(self, name):
        return (name, [b"100"])

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            return ("OK", [b" ".join(str(u).encode() for u in sorted(self.messages))])
        if cmd == "FETCH":
            assert "PEEK" in args[1]
            return ("OK", [(b"1 (UID x)", self.messages[int(args[0])])])
        return ("NO", [b""])

    def logout(self):
        return ("BYE", [b""])


HOLD: dict = {}


def install(messages: dict) -> None:
    def factory(host, **kw):
        f = FakeIMAP(host, **kw)
        f.messages = messages
        HOLD["f"] = f
        return f
    imaplib.IMAP4_SSL = factory                                   # type: ignore[assignment]


install({})
quiet(bs.put_email, host="imap.gmail.com", user=OWNER, password=APP_PW)

# ── THE OWNER'S ACTUAL SENDERS, THROUGH THE REAL SWEEP ──────────────────────────────────────
#
# Every address below is one OSDev1 read off his Drafts tab, plus two that must NOT be caught.
print("\n— the senders on his box, judged by the real ingest —")
ROBOTS = {
    1: ("<a1@x>", "jobalerts-noreply@linkedin.com", ""),
    2: ("<a2@x>", "invitations@linkedin.com", ""),
    3: ("<a3@x>", "system@polsia.com", ""),
    4: ("<a4@x>", "noreply@mail.heymerit.ai", ""),
    5: ("<a5@x>", "jm@mercola.com", "List-Unsubscribe: <mailto:u@mercola.com>\n"),
    6: ("<a6@x>", "alert@spaceship.com", "Auto-Submitted: auto-generated\n"),
}
HUMANS = {
    7: ("<h1@x>", "dana@whitfield.co", ""),
    # THE TWO THAT MUST SURVIVE. A small business whose enquiries arrive at info@ or alerts@ is
    # the false positive that costs a customer, and it costs it silently.
    8: ("<h2@x>", "info@ridgelinedental.com", ""),
    9: ("<h3@x>", "alerts@realbusiness.com", ""),
}
install({k: msg(m, f, x) for k, (m, f, x) in {**ROBOTS, **HUMANS}.items()})
scanned, stored = quiet(ec.sweep, SPACE)
ok(f"all {len(ROBOTS) + len(HUMANS)} messages are ingested — nothing is hidden from the inbox",
   stored == len(ROBOTS) + len(HUMANS), f"stored={stored}")

for _k, (mid, frm, _x) in ROBOTS.items():
    row = store.get_conversation(SPACE, mid) or {}
    ok(f"{frm} is marked automated", row.get("automated") == 1, str(row.get("automated")))
for _k, (mid, frm, _x) in HUMANS.items():
    row = store.get_conversation(SPACE, mid) or {}
    ok(f"{frm} is NOT — a real business writes from there", row.get("automated") == 0,
       str(row.get("automated")))

# ── THE DRAFTER DOES NOT PAY TO ANSWER THEM ─────────────────────────────────────────────────
print("\n— no model is paid to write to a machine —")
waiting = drafts.needs_a_draft(SPACE, limit=20)
zcids = {w["zcid"] for w in waiting}
ok("every human thread is queued for a draft",
   all(m in zcids for m, _f, _x in HUMANS.values()), str(sorted(zcids)))
ok("...and not one robot is", not any(m in zcids for m, _f, _x in ROBOTS.values()),
   str(sorted(zcids)))

# THE HEAD-BLOCK, WHICH IS THE REASON THIS IS SQL AND NOT A FILTER. Six robots in front of three
# customers, and the cap is five: filtered after the query, the customers are never reached.
capped = drafts.needs_a_draft(SPACE, limit=5)
ok("with the cap at 5 and SIX robots ahead of them, real customers still come back",
   len(capped) == 3 and all(w["zcid"] in {m for m, _f, _x in HUMANS.values()} for w in capped),
   str([w["zcid"] for w in capped]))

# ── AND THE SCREEN CANNOT OFFER ONE ─────────────────────────────────────────────────────────
#
# BOTH GATES ARE NEEDED. A draft written before the column existed is still sitting in that
# table, so this proves the Drafts tab refuses to list one even when the row already exists.
print("\n— and the Drafts tab will not offer a robot, even one already drafted —")
drafts.put(space=SPACE, zcid=ROBOTS[1][0], in_reply_to=ROBOTS[1][0], body="Thanks for the alert!")
drafts.put(space=SPACE, zcid=HUMANS[0 + 7][0], in_reply_to=HUMANS[7][0], body="Tuesday works.")
offered = {w["zcid"] for w in drafts.waiting(SPACE, limit=50)}
ok("the human draft is offered", HUMANS[7][0] in offered, str(sorted(offered)))
ok("...and the robot draft is NOT, though the row exists", ROBOTS[1][0] not in offered,
   str(sorted(offered)))

# ── THE ROWS THAT WERE ALREADY THERE ────────────────────────────────────────────────────────
#
# Migration 56 leaves `automated` NULL on every existing conversation, and the 35 drafts on his
# box are all in that set. A column that only judged new mail would not have removed one of them.
print("\n— the backlog that predates the column is judged too —")
with state.connect() as c:
    c.execute("UPDATE inbox_conversations SET automated = NULL WHERE space = ?", (SPACE,))
ok("...starting from nothing judged",
   all((store.get_conversation(SPACE, m) or {}).get("automated") is None
       for m, _f, _x in {**ROBOTS, **HUMANS}.values()))
install({})
quiet(ec.sweep, SPACE)
ok("a sweep judges the backlog by address", 
   (store.get_conversation(SPACE, ROBOTS[1][0]) or {}).get("automated") == 1,
   str((store.get_conversation(SPACE, ROBOTS[1][0]) or {}).get("automated")))
ok("...and does not sweep a real customer up with them",
   (store.get_conversation(SPACE, HUMANS[7][0]) or {}).get("automated") == 0,
   str((store.get_conversation(SPACE, HUMANS[7][0]) or {}).get("automated")))
# THE HEADER-ONLY ROBOTS CANNOT BE BACKFILLED — their address looks human and the headers are
# gone. Marked 0, and the NEXT message they send is judged with the full evidence. Said out
# loud because it is the one limit of the backfill and it should not be a surprise later.
ok("a header-only robot the backfill cannot see is left answerable, not guessed at",
   (store.get_conversation(SPACE, ROBOTS[5][0]) or {}).get("automated") == 0,
   str((store.get_conversation(SPACE, ROBOTS[5][0]) or {}).get("automated")))

print()
if FAILS:
    print(f"FAILED {len(FAILS)}: " + "; ".join(FAILS))
    sys.exit(1)
print("all ok")
