"""No draft answers page code, or the business itself (owner, 2026-10-07).

Owner, 2026-10-07, with a screenshot of drafts that read "Re: your message: Could you resend your message in plain
text?": "It's making a bunch of drafts that are not connected to any email that comes in." Two gaps in the snapshot,
on any box:
  * an email sent as HTML alone was stored as its markup, and the drafter answered `<!doctype html>` by asking a robot
    to resend in plain text;
  * mail the business sends from its other addresses (the owner's personal account, staff, a front desk) reaches the
    inbox by CC, BCC or a forward, and was read as somebody writing in: drafted, and counted as waiting.
Approving "Your business's addresses", the same day: "it looks like you built something good for the Golden snapshot
that fits all customers. Yes rename the field and make sure that it's adequately explained on screen."

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * an HTML-only email is stored, or handed to the model, as markup;
  * page code with no words in it reaches the model as markup, rather than as a wordless message like a photo;
  * the drafts already written to page code are not written again, or are written again more than once;
  * mail from one of the business's addresses is filed as inbound, waits, or keeps its draft;
  * anything is guessed: mail from an address nobody named, even under the owner's own name, is a customer as always;
  * a bad address, or the mailbox's own, is saved.

No network, no model: the mailbox is a fake IMAP server and the model is stood in for.

Run: python tests/test_no_draft_answers_page_code_or_the_business.py
"""
from __future__ import annotations

import email.message
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "pagecode.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"

from core import box_settings, state  # noqa: E402

state.init_db()
from core.dispatch import app  # noqa: E402,F401
from marketing.customer_voice.drafter import draft  # noqa: E402
from marketing.customer_voice.drafter import store as drafts  # noqa: E402
from marketing.customer_voice.inbox import email_channel, store  # noqa: E402

SPACE, MAILBOX = "default", "owner@acme.co"
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {str(detail)[:400]}" if not cond and detail else ""))
    if not cond:
        _failed += 1


PAGE = ('<!doctype html>\n<html><head><title></title><style>h3 { margin-bottom: 16px; }</style>'
        '<!--[if !mso]><!-- --><meta http-equiv="X-UA-Compatible" content="IE=edge"><!--<![endif]--></head>'
        '<body><table><tr><td><p>Hi Sam,</p><p>Your application to Acme Roofing was received &amp; is under '
        'review.</p></td></tr></table></body></html>')
EMPTY_PAGE = '<!DOCTYPE html><html><head><style>body { color: red; }</style></head><body><img src="x.png"></body></html>'


def mail(*, uid, frm, to, subject, body, html=False):
    m = email.message.EmailMessage()
    m["From"], m["To"], m["Subject"] = frm, to, subject
    m["Message-ID"] = f"<m{uid}@acme.co>"
    m.set_content(body, subtype="html" if html else "plain")
    return (uid, m.as_bytes())


class FakeIMAP:
    def __init__(self, messages):
        self.messages = messages

    def login(self, *a):
        return ("OK", [b""])

    def select(self, *a, **k):
        return ("OK", [b"1"])

    def response(self, name):
        return (name, [b"42"])

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            return ("OK", [b" ".join(str(u).encode() for u, _ in self.messages)])
        if cmd == "FETCH":
            for u, raw in self.messages:
                if u == int(args[0]):
                    return ("OK", [(b"1 (BODY[] {%d}" % len(raw), raw)])
            return ("OK", [None])
        return ("NO", [b""])

    def logout(self):
        return ("BYE", [b""])


INBOX = []


def sweep():
    real = email_channel._connect, email_channel.box_secrets.email_credential
    email_channel._connect = lambda cred: FakeIMAP(INBOX)
    email_channel.box_secrets.email_credential = lambda: {"host": "imap.gmail.com", "user": MAILBOX, "password": "x" * 16}
    try:
        email_channel.sweep(SPACE)
    finally:
        email_channel._connect, email_channel.box_secrets.email_credential = real


def message(zmid):
    with state.connect() as c:
        r = c.execute("SELECT * FROM inbox_messages WHERE space = ? AND zernio_message_id = ?", (SPACE, zmid)).fetchone()
    return dict(r) if r else {}


def waiting(zcid):
    return zcid in {r["zernio_conversation_id"] for r in store.list_conversations(SPACE, waiting=True)}


print("test_an_html_only_email_is_stored_as_its_words")
INBOX += [mail(uid=1, frm="Dana Lee <dana@client.com>", to="Sam Rivera <owner@acme.co>",
               subject="Quote?", body="Hi Sam, can you quote a roof on Elm St?"),
          mail(uid=2, frm="Acme Roofing <team@acmeroofing.com>", to=MAILBOX, subject="Received",
               body=PAGE, html=True),
          # THE BUSINESS'S OWN, from an address nobody has named yet: the owner's personal account, BCC'd to the inbox
          mail(uid=3, frm="Sam Rivera <sam.personal@gmail.com>", to="Alison Park <alison@client.com>",
               subject="Your quote", body="Hi Alison, here is the quote we discussed...")]
sweep()
body = message("<m2@acme.co>").get("body", "")
ok("a single-part HTML email's preview is its words", "application to Acme Roofing was received & is under review"
   in body and "<" not in body and "margin-bottom" not in body, body)

print("\ntest_the_drafter_reads_words_out_of_page_code")
ok("page code is read as its words, without the head or the style",
   draft._readable(PAGE) == "Hi Sam,\nYour application to Acme Roofing was received & is under review.",
   draft._readable(PAGE))
ok("plain words are passed as they are", draft._readable("  Do you open Sunday? <3  ") == "Do you open Sunday? <3")
w = draft._words(SPACE, "x", EMPTY_PAGE)
ok("a page with no words in it (an image pasted into an email) is a wordless message, like a photo, never markup",
   "with no words" in w and "<" not in w, w)
ASKED = []
draft._ask_model = lambda **kw: ASKED.append(kw) or "Thanks! We'll be in touch."
store.upsert_conversation(space=SPACE, zcid="z-page", participant="Mailer", account_id=MAILBOX, platform="email")
store.record_message(space=SPACE, zcid="z-page", zmid="m-page", direction="in", sent_by="news@mailer.com",
                     body=EMPTY_PAGE)
draft.draft_one(space=SPACE, zcid="z-page", in_reply_to="m-page", inbound=EMPTY_PAGE, platform="email")
row = drafts.for_inbound(SPACE, "m-page") or {}
said = str((ASKED or [{}])[-1].get("prompt") or "")
ok("...so it is drafted like any wordless message, and the model never sees the markup",
   ASKED and "with no words" in said and "<!doctype" not in said.lower() and "color: red" not in said, said[-400:])
store.upsert_conversation(space=SPACE, zcid="z-page2", participant="Acme", account_id=MAILBOX, platform="email")
store.record_message(space=SPACE, zcid="z-page2", zmid="m-page2", direction="in", sent_by="team@acme.com", body=PAGE)
draft.draft_one(space=SPACE, zcid="z-page2", in_reply_to="m-page2", inbound=PAGE, platform="email")
said = str((ASKED or [{}])[-1].get("prompt") or "")
ok("a page with words: the model is handed the words, never the markup",
   "under review" in said and "<!doctype" not in said.lower() and "margin-bottom" not in said, said[-500:])

print("\ntest_the_drafts_already_written_to_page_code_are_written_again_once")
store.upsert_conversation(space=SPACE, zcid="z-old", participant="Loop", account_id=MAILBOX, platform="email")
store.record_message(space=SPACE, zcid="z-old", zmid="m-old", direction="in", sent_by="ioanna@loop.com",
                     body="\n  " + PAGE)
drafts.put(space=SPACE, zcid="z-old", in_reply_to="m-old", body="Could you resend your message in plain text?",
           rules=draft.rules("email"))
store.upsert_conversation(space=SPACE, zcid="z-fine", participant="Sam", account_id=MAILBOX, platform="email")
store.record_message(space=SPACE, zcid="z-fine", zmid="m-fine", direction="in", sent_by="sam@x.com", body="Open Sunday?")
drafts.put(space=SPACE, zcid="z-fine", in_reply_to="m-fine", body="Yes, 10 to 4!", rules=draft.rules("email"))
store.upsert_conversation(space=SPACE, zcid="z-kept", participant="Kim", account_id=MAILBOX, platform="email")
store.record_message(space=SPACE, zcid="z-kept", zmid="m-kept", direction="in", sent_by="kim@x.com", body=PAGE)
drafts.put(space=SPACE, zcid="z-kept", in_reply_to="m-kept", body="Thanks Kim!", rules=drafts.KEEP)


def stale():
    return {r["zcid"] for r in drafts.stale_waiting(SPACE, email_rules=draft.rules("email"),
                                                    dms_rules=draft.rules("instagram"), limit=50)}


ok("before: none of them is due a rewrite", not ({"z-old", "z-fine", "z-kept"} & stale()), stale())
n = drafts.redo_drafts_from_markup(SPACE)
# (z-page and z-page2 above, drafted from page code in this file, go back too: on a box the redo runs on the first sweep after the
# update, before any draft is written under the fix.)
ok("the draft written to page code is sent back to be written again from the words", n == 3 and "z-old" in stale(),
   (n, stale()))
ok("...never a draft written to words, nor one a person's own AI wrote", not ({"z-fine", "z-kept"} & stale()), stale())
drafts.mark_rules(SPACE, (drafts.for_inbound(SPACE, "m-old") or {}).get("id"), draft.rules("email"))
ok("...and only once on a box: rewritten, it is never sent back again",
   drafts.redo_drafts_from_markup(SPACE) == 0 and "z-old" not in stale(), stale())

from core import spaces as _spaces  # noqa: E402
store.upsert_conversation(space="acme2", zcid="z-2", participant="Loop", account_id=MAILBOX, platform="email")
store.record_message(space="acme2", zcid="z-2", zmid="m-2", direction="in", sent_by="ioanna@loop.com", body=PAGE)
drafts.put(space="acme2", zcid="z-2", in_reply_to="m-2", body="Could you resend it?", rules=draft.rules("email"))
real_spaces, _spaces.all_spaces = _spaces.all_spaces, lambda: [{"name": "acme2"}]
draft.periodic(new_only=True)
ok("the 20-second listener leaves it to the drafter's own periodic",
   not box_settings.get("inbox", "drafts.markup_redone.acme2", default=None))
draft.periodic()
_spaces.all_spaces = real_spaces
ok("...whose next run sends it back on a box nobody is watching",
   box_settings.get("inbox", "drafts.markup_redone.acme2", default=None)
   and (drafts.for_inbound("acme2", "m-2") or {}).get("body") != "Could you resend it?"
   or "z-2" in {r["zcid"] for r in drafts.stale_waiting("acme2", email_rules=draft.rules("email"),
                                                         dms_rules=draft.rules("instagram"), limit=9)})

print("\ntest_your_businesss_addresses_are_the_business_speaking")
own_copy = message("<m3@acme.co>")
cid3 = own_copy.get("zernio_conversation_id")
ok("unnamed, a copy of the business's own mail reads as somebody writing in: nothing is guessed",
   own_copy.get("direction") == "in" and waiting(cid3))
drafts.put(space=SPACE, zcid=cid3, in_reply_to="<m3@acme.co>", body="Hi Sam, thanks for the quote!")
for bad in (["not an address"], ["a@b"], ["a@b.com, c@d.com"], [MAILBOX], [f"x{i}@y.com" for i in range(21)]):
    try:
        store.put_business_addresses(bad, by="owner", own_address=MAILBOX)
        ok(f"refused: {bad[:2]}", False)
    except ValueError:
        pass
ok("anything but a list of real, other addresses is refused, and nothing is saved", store.business_addresses() == set())
got = store.put_business_addresses(["  Sam.Personal@Gmail.com ", "frontdesk@acme.co", "sam.personal@gmail.com"],
                                   by="owner", own_address=MAILBOX)
ok("the owner names two (their personal account, the front desk): saved trimmed, lower case, once each",
   got["addresses"] == ["sam.personal@gmail.com", "frontdesk@acme.co"], got)
ok("...the copy already in from one of them is the business's, the moment it is named",
   got["refiled"] == 1 and message("<m3@acme.co>").get("direction") == "out" and not waiting(cid3), got)
ok("...and the draft the box wrote to it is withdrawn",
   (drafts.for_inbound(SPACE, "<m3@acme.co>") or {}).get("dismissed_at"))
INBOX += [mail(uid=4, frm="Front Desk <frontdesk@acme.co>", to="Kim Lee <kim@client.com>",
               subject="Re: Tuesday", body="Hi Kim, you're booked for Tuesday at 10."),
          mail(uid=5, frm="Sam Rivera <sam@otherbusiness.com>", to=MAILBOX,
               subject="Roof quote", body="Hi, a different Sam Rivera here: can you quote my roof?")]
sweep()
conv4 = store.get_conversation(SPACE, message("<m4@acme.co>").get("zernio_conversation_id")) or {}
ok("new mail from a named address (the front desk, CC'd) is the business speaking, as it arrives",
   message("<m4@acme.co>").get("direction") == "out" and not conv4.get("last_inbound_at"), (message("<m4@acme.co>"), conv4))
ok("a customer who shares the owner's name, from an address nobody named, is a customer, waiting",
   message("<m5@acme.co>").get("direction") == "in" and waiting(message("<m5@acme.co>").get("zernio_conversation_id")))
ok("a customer who wrote in is still waiting", waiting(message("<m1@acme.co>").get("zernio_conversation_id")))
from marketing.customer_voice.drafter import who_wrote  # noqa: E402
email_channel.box_secrets.email_credential = lambda: {"user": MAILBOX, "password": "x" * 16}
ok("the drafter counts a named address as the business's own too, so it is never answered",
   who_wrote.why("frontdesk@acme.co", {}, ours=who_wrote.our_addresses()) == "this box sent it"
   and not who_wrote.why("sam@otherbusiness.com", {}, ours=who_wrote.our_addresses()))
again = store.put_business_addresses(["sam.personal@gmail.com", "frontdesk@acme.co"], by="owner", own_address=MAILBOX)
ok("saving the same list again refiles nothing", again["refiled"] == 0, again)

print("\nALL PAGE-CODE-OR-THE-BUSINESS CHECKS PASS" if not _failed else f"\n{_failed} PAGE-CODE-OR-THE-BUSINESS CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
