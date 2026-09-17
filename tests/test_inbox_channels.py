"""One screen, every channel — and which human answered.

docs/SPEC_UNIFIED_INBOX_SCREEN_AND_AUTH.md §5.1 and §5.2. The `platform` column has existed
since the table did (`schema.py:99`), so blending channels is a DATA problem — more pollers
writing more values — not a schema one. What was missing was a reader that could filter, a chip
row built from what a box actually has, and a thread that says WHICH person replied.

THE FAILURE THIS SUITE IS WRITTEN AGAINST is a screen that lists five channels on a box that has
one. `marketing/customer_voice/rails.py` refuses that by design — "ownership is declared, never
inferred" — and a chip that filters to nothing is the same dead control.

Run: python tests/test_inbox_channels.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "channels.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "k"
os.environ["DASH_TOKEN"] = "pw"

from core import state                                               # noqa: E402

state.init_db()

from core import dash                                                # noqa: E402
from core.dispatch import app as flask_app                           # noqa: E402
from marketing.customer_voice.inbox import store                     # noqa: E402

_failed = 0
SPACE = "default"


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def _client(user_id=None):
    """Signed in as somebody REAL. Since #1143 a session with no person is nobody, so the
    default here is the box's own owner row rather than a NULL."""
    c = flask_app.test_client()
    c.set_cookie(dash.COOKIE, dash.new_session(user_id or state.OWNER_USER_ID))
    return c


def _page(c, path):
    r = c.get(path)
    return r.status_code, r.get_data(as_text=True)


# ── the chip row is built from what the box HAS ────────────────────────────────────────────
print("test_the_chips_are_what_this_box_actually_has")
store.upsert_conversation(space=SPACE, zcid="m1", platform="messenger",
                          participant="Dana", account_id="a1", last_inbound_at="2026-09-15T06:00:00Z")
c = _client()
_, html = _page(c, "/inbox/inbox")
ok("one channel renders NO chip row — a filter with one option changes nothing",
   'class="chips"' not in html)
# THE ROW STILL NAMES THE CHANNEL, and this assertion was rewritten when the row moved to the
# channel's own logo (2026-09-15, the Kinso-derived redesign). It deliberately does NOT pin the
# markup any more — it pins the GUARANTEE, which is the thing worth keeping: a person must be able
# to tell Messenger from Instagram without relying on colour.
#
# The mark satisfies that twice over. It is a SHAPE first, so it survives greyscale and a
# screenshot, and it carries the channel's name in a visually-hidden span so a screen reader says
# "Messenger" rather than "image". Both are checked, because the old form of this check would pass
# on a bare coloured dot with a title attribute, and that is exactly what it exists to refuse.
_row = html.split('class="conv"', 1)[1].split("</a>", 1)[0]
ok("...and the row still NAMES the channel, not merely colours it",
   ">Messenger<" in _row, _row[-160:])
ok("...with the name in text a screen reader reads, not an attribute it may skip",
   'class="vh">Messenger' in _row)
ok("...and the mark is a shape, drawn as a path rather than a coloured blob",
   "<svg" in _row and "<path" in _row)

store.upsert_conversation(space=SPACE, zcid="i1", platform="instagram",
                          participant="Sam", account_id="a2", last_inbound_at="2026-09-15T07:00:00Z")
_, html = _page(c, "/inbox/inbox")
ok("a second channel brings the chip row with it", 'class="chips"' in html)
ok("...with an All chip", ">All<" in html)
ok("...and one chip per channel present", "Messenger" in html and "Instagram" in html)
ok("...and NO chip for a channel this box has never received",
   "WhatsApp" not in html and "Reviews" not in html and "SMS" not in html)
present = store.platforms_present(SPACE)
ok("the store counts them rather than the screen guessing",
   {p["platform"]: p["n"] for p in present} == {"messenger": 1, "instagram": 1}, str(present))


# ── the filter actually filters, and is a bound predicate ──────────────────────────────────
print("test_a_chip_filters_and_cannot_inject")
only_ig = store.list_conversations(SPACE, platform="instagram")
ok("filtering returns only that channel",
   [k["zernio_conversation_id"] for k in only_ig] == ["i1"], str(only_ig))
ok("...and no filter returns everything", len(store.list_conversations(SPACE)) == 2)
_, html = _page(c, "/inbox/inbox?channel=instagram")
ok("the screen honours the chip", "Sam" in html and "Dana" not in html)
# THE VALUE COMES FROM A QUERY STRING. It is bound, never interpolated — so a fragment of SQL
# is just a string that matches no platform, rather than a fragment of the query.
nasty = "messenger' OR '1'='1"
ok("a SQL fragment in the chip matches nothing instead of matching everything",
   store.list_conversations(SPACE, platform=nasty) == [], str(nasty))
st, html = _page(c, "/inbox/inbox?channel=" + nasty.replace(" ", "%20").replace("'", "%27"))
ok("...and the page says so rather than 500ing", st == 200)
ok("...telling him other channels exist, not that the inbox is empty",
   "tap" in html.lower() and "All" in html)


# ── the thread says which channel, and which human ─────────────────────────────────────────
print("test_the_thread_names_the_channel_and_the_person")
store.record_message(space=SPACE, zcid="i1", zmid="in-1", direction="in",
                     sent_by="contact", body="are you open sunday")
_, html = _page(c, "/inbox/inbox/i1")
ok("the thread header names the channel — replying to a DM as if it were email is the "
   "mistake this prevents", 'class="chan">Instagram' in html)

maria = state.add_user("maria@example.com", name="Maria", role="member")
store.record_message(space=SPACE, zcid="i1", zmid="out-1", direction="out",
                     sent_by="human", body="we are, 10 til 4")
store.record_send(space=SPACE, zcid="i1", idem_key="reply:i1:maria", kind="reply",
                  status="ok", zernio_message_id="out-1", user_id=maria["id"])
msgs = store.messages_for(SPACE, "i1")
sent = [m for m in msgs if m.get("zernio_message_id") == "out-1"][0]
ok("the reader joins the ledger to the message on the vendor id",
   sent.get("sender_user_id") == maria["id"], str(sent.get("sender_user_id")))
ok("...and carries the name", sent.get("sender_name") == "Maria")
_, html = _page(c, "/inbox/inbox/i1")
ok("the thread says WHO — 'Sent by Maria' is the point of having employees on the box",
   "Maria" in html)

# THE NAME MUST SURVIVE REVOCATION. `active = 0` never deletes the row precisely so that a
# message she sent last month still resolves to a person and not to a blank.
state.set_user_active(maria["id"], False)
_, html = _page(c, "/inbox/inbox/i1")
ok("...and it still says Maria after she is revoked — history keeps its names", "Maria" in html)

# AN INBOUND MESSAGE HAS NO LEDGER ROW, and a LEFT JOIN that dropped it would empty the thread.
ok("an inbound message survives the join with no sender attached",
   any(m.get("body") == "are you open sunday" and not m.get("sender_user_id") for m in msgs))
ok("...and the thread still renders it", "are you open sunday" in html)

# NULL user_id is the owner (every session on a live box the day this ships), and reads as
# "you" exactly as it did before there was a second person.
store.record_message(space=SPACE, zcid="m1", zmid="out-owner", direction="out",
                     sent_by="human", body="on my way")
store.record_send(space=SPACE, zcid="m1", idem_key="reply:m1:owner", kind="reply",
                  status="ok", zernio_message_id="out-owner", user_id=None)
_, html = _page(c, "/inbox/inbox/m1")
ok("an owner's own reply still reads 'you', not a blank", ">you ·" in html or "you ·" in html)


# ── NULL vendor ids must not join to each other ────────────────────────────────────────────
print("test_two_messages_with_no_vendor_id_do_not_become_each_other")
store.record_message(space=SPACE, zcid="m1", zmid=None, direction="in",
                     sent_by="contact", body="first no-id")
store.record_message(space=SPACE, zcid="m1", zmid=None, direction="in",
                     sent_by="contact", body="second no-id")
rows = store.messages_for(SPACE, "m1")
noid = [m for m in rows if m.get("body", "").endswith("no-id")]
ok("both rows come back exactly once each — SQLite never matches NULL to NULL",
   len(noid) == 2, str(len(noid)))
ok("...and neither picked up a sender", not any(m.get("sender_user_id") for m in noid))


# ── the tenant boundary is untouched by any of it ──────────────────────────────────────────
print("test_a_chip_can_never_reach_another_space")
store.upsert_conversation(space="other-client", zcid="x1", platform="instagram",
                          participant="NotYours", account_id="a9",
                          last_inbound_at="2026-09-15T08:00:00Z")
ok("listing this Space never returns another's row, filtered or not",
   all(k["space"] == SPACE for k in store.list_conversations(SPACE))
   and all(k["space"] == SPACE for k in store.list_conversations(SPACE, platform="instagram")))
ok("...and the chip counts are this Space's only",
   sum(p["n"] for p in store.platforms_present(SPACE)) == 2,
   str(store.platforms_present(SPACE)))
_, html = _page(c, "/inbox/inbox?channel=instagram")
ok("...and no screen renders it", "NotYours" not in html)

print(("FAILED " + str(_failed)) if _failed else "all ok")
sys.exit(1 if _failed else 0)
