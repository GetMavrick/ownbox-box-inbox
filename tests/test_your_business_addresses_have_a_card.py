"""Your business's addresses have a card: Inbox Settings, Mailbox (owner, 2026-10-07).

Owner, 2026-10-07: "Yes rename the field and make sure that it's adequately explained on screen." The backend is
inbox/store.py put_business_addresses (#2048, tests/test_no_draft_answers_page_code_or_the_business.py); this holds the
card on the Mailbox screen (docs/SCOPE_BUSINESS_ADDRESSES.md) and its one-tap tool lives in
tests/test_inbox_connector_tools.py.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * the card is missing under a connected mailbox, or isn't explained before anything is added;
  * adding an address doesn't reach put_business_addresses, or the mail already here from it isn't filed, or the
    owner isn't told how many messages moved;
  * a refused address (not an address, the mailbox's own, the 21st) writes anything, or isn't said next to the field
    with what was typed kept;
  * removing one keeps it, or touches the others;
  * a member can change the list, or reads the owner's addresses.

No network, no model: the mailbox reads as connected without signing in anywhere.

Run: python tests/test_your_business_addresses_have_a_card.py
"""
from __future__ import annotations

import html
import os
import re
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "business-addresses.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "dash-pw"

from core import box_secrets, dash, state  # noqa: E402

state.init_db()
try:
    import marketing.customer_voice.app  # noqa: F401,E402
except ImportError:
    print("  --   no customer_voice on this box")
    sys.exit(0)
from core.dispatch import app  # noqa: E402
from marketing.customer_voice.inbox import store  # noqa: E402

MAILBOX, SPACE = "hello@glowmedspa.com", "default"
box_secrets.email_state = lambda: {"status": "connected", "user": MAILBOX, "detail": ""}
FAILS: list[str] = []


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:400]}"))
    if not cond:
        FAILS.append(label)


def card(page: str) -> str:
    m = re.search(r'<div class="card biz" id="addresses">.*?</form></div>', page, re.S)
    return m.group(0) if m else ""


def text(page: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", card(page)))


def listed(page: str) -> list:
    return [html.unescape(a) for a in re.findall(r'<span class="a">([^<]+)</span>', card(page))]


o = app.test_client()
o.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))

print("test_the_card_is_under_the_connected_mailbox")
page = o.get("/inbox/mailbox").get_data(as_text=True)
c = card(page)
ok("the owner sees the card, after the password and before stopping", c and
   page.index('id="addresses"') > page.index("Save this password") and page.index('id="addresses"')
   < page.index("Stop reading this inbox"), page[:300])
ok("...explained before anything is added: the mailbox it reads, named", MAILBOX in text(page)
   and text(page).index(MAILBOX) < text(page).index("Add an address"))
ok("...empty, it says only the mailbox counts so far", not listed(page) and text(page).count(MAILBOX) == 2)
ok("one email field, 16px, with an Add button", 'type="email"' in c and 'autocomplete="email"' in c
   and "max(16px" in page and ">Add</button>" in c)

print("\ntest_adding_one_files_the_mail_already_here")
store.upsert_conversation(space=SPACE, zcid="mail-kim", participant="Kim Lee", account_id=MAILBOX,
                          platform="email", last_inbound_at="2026-10-07T15:00:00Z")
with state.connect() as db:
    db.execute("INSERT INTO inbox_messages (id, space, zernio_conversation_id, zernio_message_id, direction, sent_by,"
               " body, created_at) VALUES (?,?,?,?,?,?,?,?)",
               (str(uuid.uuid4()), SPACE, "mail-kim", "<fd1@glowmedspa.com>", "in", "frontdesk@glowmedspa.com",
                "Hi Kim, you're booked for Tuesday at 10.", "2026-10-07T15:00:00Z"))
r = o.post("/inbox/mailbox/addresses", data={"add": "  FrontDesk@GlowMedSpa.com "})
page = r.get_data(as_text=True)
ok("added: saved trimmed and in lower case", r.status_code == 200
   and store.business_addresses() == {"frontdesk@glowmedspa.com"} and listed(page) == ["frontdesk@glowmedspa.com"],
   store.business_addresses())
with state.connect() as db:
    moved = db.execute("SELECT direction FROM inbox_messages WHERE zernio_message_id = '<fd1@glowmedspa.com>'"
                       ).fetchone()["direction"]
ok("...the mail already here from it is filed as the business's, and the owner is told how many",
   moved == "out" and re.search(r"\b1 message\b", text(page)), (moved, text(page)[-300:]))
r = o.post("/inbox/mailbox/addresses", data={"add": "sales@glowmedspa.com"})
ok("a second one joins the first", store.business_addresses() == {"frontdesk@glowmedspa.com", "sales@glowmedspa.com"}
   and listed(r.get_data(as_text=True)) == ["frontdesk@glowmedspa.com", "sales@glowmedspa.com"])

print("\ntest_a_refusal_writes_nothing")
before = store.business_addresses()
for bad, why in (("not an address", "not an address"), (MAILBOX, "the mailbox's own"), ("", "nothing typed")):
    r = o.post("/inbox/mailbox/addresses", data={"add": bad})
    page = r.get_data(as_text=True)
    ok(f"refused ({why}): said next to the field, what was typed kept, nothing saved",
       r.status_code == 200 and '<p class="err" role="alert">' in card(page)
       and f'value="{html.escape(bad, quote=True)}"' in card(page) and store.business_addresses() == before,
       card(page)[-400:])
store.put_business_addresses(sorted(before) + [f"staff{i}@glowmedspa.com" for i in range(18)], by="owner",
                             own_address=MAILBOX)
r = o.post("/inbox/mailbox/addresses", data={"add": "one-too-many@glowmedspa.com"})
ok("the 21st is refused, and the twenty stay", '<p class="err"' in card(r.get_data(as_text=True))
   and len(store.business_addresses()) == 20)
store.put_business_addresses(sorted(before), by="owner", own_address=MAILBOX)

print("\ntest_removing_one")
r = o.post("/inbox/mailbox/addresses", data={"remove": "sales@glowmedspa.com"})
page = r.get_data(as_text=True)
ok("removed, the other kept, and the owner told which", store.business_addresses() == {"frontdesk@glowmedspa.com"}
   and listed(page) == ["frontdesk@glowmedspa.com"] and "sales@glowmedspa.com" in text(page), text(page)[-300:])
ok("each row's Remove is its own 48px button", 'class="rm"' in card(page) and "min-height:48px" in page
   and 'name="remove" value="frontdesk@glowmedspa.com"' in card(page))

print("\ntest_a_member_neither_reads_nor_changes_it")
m = app.test_client()
m.set_cookie(dash.COOKIE, dash.new_session(state.add_user("sam@glowmedspa.com", name="Sam", role="member")["id"]))
page = m.get("/inbox/mailbox").get_data(as_text=True)
ok("a member's Mailbox screen shows no addresses", 'id="addresses"' not in page
   and "frontdesk@glowmedspa.com" not in page)
r = m.post("/inbox/mailbox/addresses", data={"add": "kim@client.com"})
ok("...and can't add one", r.status_code == 403 and store.business_addresses() == {"frontdesk@glowmedspa.com"})

print("\nALL BUSINESS-ADDRESS CARD CHECKS PASS" if not FAILS else f"\n{len(FAILS)} BUSINESS-ADDRESS CARD CHECK(S) FAILED")
sys.exit(1 if FAILS else 0)
