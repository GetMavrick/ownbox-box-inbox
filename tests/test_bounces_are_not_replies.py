"""The words say who wrote: a customer's message reads "They wrote", and a bounce is a mail server, not a reply.

OSDev1, 2026-10-04 (daily check on a live box): inbox.read_conversation showed mailer-daemon bounces as "You
replied" while its raw data said incoming. The cause was wider than bounces: the tool returned each message's
direction as the store keeps it ('in', 'out') and the renderer compared it with "inbound", so EVERY message a
customer sent was read out as "You replied", and the box never said anybody was waiting. Owner, 2026-10-04: an
"absolute necessity". Measured here, with no network:
  * a customer's message reads "They wrote"; the business's reads "You replied"; whose message is last is right
  * a bounce, by its sender or by its first words, is named a delivery failure and never a reply, and nobody is
    said to be waiting on it
  * the tool's data says "inbound"/"outbound", the words every fixture was written against

Run: python tests/test_bounces_are_not_replies.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "bounces.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"

from core import state  # noqa: E402

state.init_db()
from core.dispatch import app  # noqa: E402,F401
from marketing.customer_voice.inbox import store  # noqa: E402
from marketing.customer_voice.inbox import tools as inbox_tools  # noqa: E402

FAILS: list[str] = []
SPACE = inbox_tools._space()


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:400]}"))
    if not cond:
        FAILS.append(label)


def thread(zcid: str, participant: str, *msgs) -> None:
    store.upsert_conversation(space=SPACE, zcid=zcid, platform="email", participant=participant,
                              last_inbound_at="2026-10-04T09:00:00Z", account_id="me@example-roofing.com")
    for i, (direction, by, body) in enumerate(msgs):
        store.record_message(space=SPACE, zcid=zcid, zmid=f"{zcid}-m{i}", direction=direction, sent_by=by, body=body)


def read(zcid: str) -> tuple[dict, str]:
    r = inbox_tools.read_conversation(id=zcid)
    return r, inbox_tools._render_read(r)


print("\nA customer and the business\n")
thread("t1", "Dana Whitfield", ("in", "dana@example.com", "there is a leak over the porch"),
       ("out", "me@example-roofing.com", "On it, I can come Tuesday."))
r, said = read("t1")
ok("the data says inbound then outbound", [m["direction"] for m in r["messages"]] == ["inbound", "outbound"], r)
ok("...and no message is a bounce", not any(m.get("bounce") for m in r["messages"]))
ok("the words: they wrote, you replied", "They wrote: " in said and "You replied: " in said
   and said.index("They wrote") < said.index("You replied"), said)
ok("...and your reply is the last message", "Your reply is the last message." in said
   and "so they are waiting on a reply" not in said, said)
thread("t2", "Marco Ruiz", ("in", "marco@example.com", "How much for a gutter clean?"))
r, said = read("t2")
ok("a customer's message alone: they wrote, and they are waiting", "They wrote: " in said
   and "You replied" not in said and "they are waiting on a reply" in said, said)

print("\nA bounce is a mail server, not a person\n")
thread("b1", "Mail Delivery Subsystem", ("in", "mailer-daemon@googlemail.com",
                                         "Address not found. Your message wasn't delivered to x@nowhere.example "
                                         "because the address couldn't be found."))
r, said = read("b1")
ok("by its sender: the data marks it a bounce, inbound", r["messages"][0].get("bounce") is True
   and r["messages"][0]["direction"] == "inbound", r)
ok("...the words name a delivery failure, never a reply, never you", "Delivery failed, not a reply: " in said
   and "You replied" not in said and "They wrote" not in said, said)
ok("...and nobody is waiting on it", "nobody is waiting" in said and "so they are waiting on a reply" not in said, said)
thread("b2", "Example Mail", ("in", "noreply@mail.example.net",
                             "Delivery Status Notification (Failure)\nThe following address failed: a@b.example"))
r, said = read("b2")
ok("by its first words too, from any address", r["messages"][0].get("bounce") is True
   and "Delivery failed, not a reply" in said, said)
thread("t3", "Postmaster General", ("in", "pat@example.com", "Hi, I am the postmaster at our co-op, can you quote?"))
r, said = read("t3")
ok("a person who mentions a mail server is still a person", not r["messages"][0].get("bounce")
   and "They wrote: " in said, said)
ok("the business's own words are never a bounce", not inbox_tools._is_bounce("me@example-roofing.com", "Delivered!"))

print("\nALL BOUNCE CHECKS PASS" if not FAILS else f"\n{len(FAILS)} BOUNCE CHECK(S) FAILED")
sys.exit(1 if FAILS else 0)
