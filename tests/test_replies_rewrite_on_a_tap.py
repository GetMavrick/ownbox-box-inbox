"""Replies to send rewrite on a tap: "Rewrite with current settings", for one waiting reply or all (OSDev1, 2026-10-08).

OSDev1, 2026-10-08, from the owner's waiting replies that kept an old closing line: a button on Replies to send that
puts replies on the drafter's rewrite list (drafter/store.request_rewrites, #2057), the list
inbox.propose_rewrite_drafts fills on the owner's approval; and "Written by your own AI" on a reply the owner's own
AI wrote, which the box never rewrites by itself.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * a tap on Rewrite sends anything, even with replies ticked in the same form;
  * "Rewrite all" misses a waiting reply, or queues one twice, or a single Rewrite queues another reply;
  * a reply already being rewritten still offers the button, or doesn't say so;
  * a reply the owner's own AI wrote isn't marked as such;
  * someone not signed in can queue a rewrite.

No network, no model.

Run: python tests/test_replies_rewrite_on_a_tap.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "rewrite-tap.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "dash-pw"

from core import dash, state  # noqa: E402

state.init_db()
try:
    import marketing.customer_voice.app as inbox_app  # noqa: E402
except ImportError:
    print("  --   no customer_voice on this box")
    sys.exit(0)
from core.dispatch import app  # noqa: E402
from marketing.customer_voice.drafter import store as drafts  # noqa: E402
from marketing.customer_voice.inbox import reply as _reply  # noqa: E402
from marketing.customer_voice.inbox import store  # noqa: E402

SPACE = inbox_app._space()
FAILS: list[str] = []


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:400]}"))
    if not cond:
        FAILS.append(label)


def waiting_reply(zcid: str, who: str, said: str, body: str, *, kept: bool = False) -> str:
    store.upsert_conversation(space=SPACE, zcid=zcid, participant=who, platform="instagram",
                              last_inbound_at="2026-10-08T03:00:00Z")
    mid = f"m-{zcid}"
    with state.connect() as c:
        c.execute("INSERT INTO inbox_messages (id, space, zernio_conversation_id, zernio_message_id, direction, sent_by,"
                  " body, created_at) VALUES (?,?,?,?,?,?,?,?)",
                  (str(uuid.uuid4()), SPACE, zcid, mid, "in", "contact", said, "2026-10-08T03:00:00Z"))
    drafts.put(space=SPACE, zcid=zcid, in_reply_to=mid, body=body, rules=drafts.KEEP if kept else "")
    return next(str(d["id"]) for d in drafts.waiting(SPACE, limit=50) if d["zcid"] == zcid)


priya = waiting_reply("ig-priya", "Priya Shah", "Is parking free?", "Yes, parking is free right outside the door.")
marcus = waiting_reply("ig-marcus", "Marcus Reed", "What does a consultation cost?",
                       "Hi Marcus, a first consultation is $75. Want me to hold Thursday at 4?", kept=True)
sam = waiting_reply("ig-sam", "Sam Okafor", "Do you do gift cards?", "We do! Any amount, by email or in person.")
SENT = []
_reply.send_reply = lambda **kw: SENT.append(kw) or {"status": "ok"}

o = app.test_client()
o.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))

print("test_the_buttons_and_who_wrote_it")
page = o.get("/inbox/waiting").get_data(as_text=True)
ok("Rewrite all, above the list, in a form of its own that posts to the rewrite route",
   re.search(r'<form method="post" action="/inbox/waiting/rewrite"[^>]*><input type="hidden" name="all" value="1">',
             page) and page.index('name="all"') < page.index('name="pick"'))
ok("one Rewrite per waiting reply, each posting to the rewrite route, never to Send",
   sorted(re.findall(r'formaction="/inbox/waiting/rewrite" formnovalidate name="draft" value="([^"]+)"', page))
   == sorted([priya, marcus, sam]))
ok("the reply the owner's own AI wrote says so, and only that one",
   page.count("Written by your own AI") == 1
   and page.index("Marcus Reed") < page.index("Written by your own AI")
   and page.index("Written by your own AI") < page.index("Sam Okafor"))

print("\ntest_one_reply")
r = o.post("/inbox/waiting/rewrite", data={"draft": marcus, "pick": ["ig-priya", "ig-marcus", "ig-sam"]})
ok("Rewrite on one: that reply alone goes on the list, and the ticks in the same form send nothing",
   r.status_code == 303 and r.headers["Location"].endswith("/inbox/waiting?rewriting=1")
   and drafts.requested_rewrites(SPACE) == [marcus] and SENT == [], (drafts.requested_rewrites(SPACE), SENT))
page = o.get("/inbox/waiting?rewriting=1").get_data(as_text=True)
ok("...the page says it is rewriting one, and that nothing is sent", "Rewriting 1 reply." in page
   and "Nothing is sent." in page)
ok("...and that reply says it is being rewritten, with no button of its own",
   page.count("Being rewritten under your current settings.") == 1 and f'name="draft" value="{marcus}"' not in page)

print("\ntest_all_of_them")
ok("Rewrite all now offers the other two", "Rewrite the other 2 with current settings" in page)
r = o.post("/inbox/waiting/rewrite", data={"all": "1"})
ok("Rewrite all: every waiting reply is on the list once, and the count is the ones it added",
   r.headers["Location"].endswith("?rewriting=2") and sorted(drafts.requested_rewrites(SPACE))
   == sorted([priya, marcus, sam]) and len(drafts.requested_rewrites(SPACE)) == 3, drafts.requested_rewrites(SPACE))
page = o.get("/inbox/waiting").get_data(as_text=True)
ok("...with all three being rewritten, the button is gone", 'name="all"' not in page
   and page.count("Being rewritten under your current settings.") == 3)
ok("...and nothing was sent", SENT == [])
r = o.post("/inbox/waiting/rewrite", data={"draft": "not-a-reply"})
ok("an id that isn't waiting queues nothing", r.headers["Location"].endswith("?rewriting=0")
   and len(drafts.requested_rewrites(SPACE)) == 3)

print("\ntest_signed_out")
out = app.test_client()
r = out.post("/inbox/waiting/rewrite", data={"all": "1"})
ok("signed out, it changes nothing", r.status_code in (302, 303, 401, 403) and len(drafts.requested_rewrites(SPACE)) == 3,
   r.status_code)

print("\nALL REWRITE-ON-A-TAP CHECKS PASS" if not FAILS else f"\n{len(FAILS)} REWRITE-ON-A-TAP CHECK(S) FAILED")
sys.exit(1 if FAILS else 0)
