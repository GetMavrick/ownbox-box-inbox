"""The drafted reply lands in the BUYER'S OWN Gmail Drafts, inside the customer's thread.

§1.4 of docs/SCOPE_EMAIL_SEND_AND_MOBILE_NOTIFICATIONS.md, and the owner's words behind it
(2026-09-22): *"If we can't auto draft emails and then actually go ahead and send them, this is a
completely worthless app."* Sending from the box is §1.2. This is the half that needs NO SMTP at
all — the box already holds an authenticated IMAP session, and RFC 3501 APPEND writes a message
into a folder. The buyer opens the Gmail app they already have, reads the customer's email where
they always read it, and the answer is sitting under it, already written, one tap from sent.

WHAT THIS SUITE IS ACTUALLY FOR. Three things can go wrong here and all three are silent:

  1. The folder is HARD-CODED. `[Gmail]/Drafts` is localised — a French account has
     `[Gmail]/Brouillons` — so the naive version works on every account its author tested and on
     no other. The fake server below names its Drafts folder something nobody would guess, and
     the test passes only if the code found it by its `\\Drafts` attribute (RFC 6154).
  2. The draft is APPENDED TWICE. Two identical replies under one customer message reads as the
     box being broken. Worse, a draft the buyer DELETED comes back.
  3. The draft carries the box's own `X-Ownbox` mark, which would make ingest skip the buyer's
     real outbound the moment they tap Send — the one message in the thread that matters most.

Run: python tests/test_the_draft_is_in_your_gmail.py
"""
from __future__ import annotations

import email as email_mod
import email.policy
import imaplib
import io
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "gmail_drafts.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state  # noqa: E402

state.init_db()

from core import box_secrets as bs  # noqa: E402
from marketing.customer_voice.drafter import store as drafts  # noqa: E402
from marketing.customer_voice.inbox import email_channel as ec  # noqa: E402
from marketing.customer_voice.inbox import mailbox_drafts as md  # noqa: E402
from marketing.customer_voice.inbox import store  # noqa: E402

FAILS: list[str] = []
SPACE = "acme"
OWNER = "owner@acme.com"
APP_PW = "abcdefghijklmnop"

# NOT `[Gmail]/Drafts`, DELIBERATELY. If this string ever appears in the code under test, this
# suite is measuring nothing. The only way to reach this folder is to read the \Drafts attribute.
DRAFTS_FOLDER = "[Gmail]/Brouillons"


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


def quiet(fn, *a, **k):
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        return fn(*a, **k)


class FakeIMAP:
    """A mailbox that answers LIST, SEARCH, FETCH and APPEND — and refuses to be written to.

    Inherits the read-only discipline of tests/test_inbox_email_channel.py: STORE is not
    implemented at all, so a future edit that marked the buyer's own mail as read fails here
    rather than in the buyer's own mail app.
    """

    def __init__(self, host, **kw):
        self.host, self.kw = host, kw
        self.commands: list[str] = []
        self.logins = 0
        self.readonly = None
        self.appended: list[tuple[str, str, bytes]] = []
        self.subjects: dict[str, str] = {}      # Message-ID -> Subject, for the reply line
        self.searches: list[str] = []           # every SEARCH key, verbatim, for the guard below
        self.folders = [b'(\\HasNoChildren \\Drafts) "/" "' + DRAFTS_FOLDER.encode() + b'"',
                        b'(\\HasNoChildren) "/" "INBOX"']
        self.append_reply = "OK"

    def login(self, user, password):
        self.logins += 1
        self.commands.append("LOGIN")
        return ("OK", [b""])

    def select(self, folder, readonly=False):
        self.commands.append(f"SELECT readonly={readonly}")
        self.readonly = readonly
        return ("OK", [b"1"])

    def response(self, name):
        return (name, [b"100"])

    def list(self, *a):
        self.commands.append("LIST")
        return ("OK", list(self.folders))

    def uid(self, cmd, *args):
        self.commands.append(f"UID {cmd}")
        if cmd == "SEARCH":
            self.searches.append(str(args[-1]))
            return ("OK", [b" ".join(str(u).encode() for u in self._search(str(args[-1])))])
        if cmd == "FETCH":
            if "PEEK" not in args[1]:
                raise AssertionError(f"fetched without PEEK: {args[1]}")
            mid, subj = list(self.subjects.items())[int(args[0]) - 1]
            head = f"Subject: {subj}\r\n"
            # MESSAGE-ID IS RETURNED WHENEVER IT IS ASKED FOR, so the code's own check that it
            # got the message it asked about is exercised rather than assumed.
            if "MESSAGE-ID" in args[1].upper():
                head += f"Message-ID: {mid}\r\n"
            return ("OK", [(b"1 (UID x)", (head + "\r\n").encode())])
        return ("NO", [b""])

    # ── A REAL RFC 3501 SEARCH KEY PARSER, not a substring test ───────────────────────────
    #
    # THIS IS THE WHOLE POINT OF THIS SUITE'S SECOND HALF. A fake that answered `if mid in want`
    # cannot tell a correctly escaped key from an injected one — both "contain" the id — so it
    # would have passed the injected build. This one parses the quoted string the way a server
    # does: a bare `"` ENDS the string, and what follows is another search key. `OR`/`ALL` are
    # honoured exactly so an injected key really does match the whole mailbox here.
    def _search(self, raw: str) -> list[int]:
        rest = raw.strip()
        if not rest.upper().startswith("HEADER MESSAGE-ID "):
            return []
        rest = rest[len("HEADER MESSAGE-ID "):].strip()
        if not rest.startswith('"'):
            return []
        wanted, i, escaped = [], 1, False
        while i < len(rest):
            c = rest[i]
            if escaped:
                wanted.append(c)
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                break
            else:
                wanted.append(c)
            i += 1
        else:
            return []                                    # unterminated string: a server would error
        trailing = rest[i + 1:].strip()
        hits = [n for n, mid in enumerate(self.subjects, start=1) if mid == "".join(wanted)]
        # WHAT THE ATTACKER WAS AFTER. An injected `OR ... ALL` widens the result set to the whole
        # mailbox, and the caller's `split()[-1]` then takes the NEWEST — a stranger's message.
        if "ALL" in trailing.upper():
            hits = list(range(1, len(self.subjects) + 1))
        return hits

    def append(self, folder, flags, date, message):
        self.commands.append("APPEND")
        if self.append_reply != "OK":
            return (self.append_reply, [b"refused"])
        self.appended.append((folder, flags, message))
        return ("OK", [b"[APPENDUID 1 9] APPEND completed"])

    def store(self, *a):
        raise AssertionError("STORE issued — this would mark the buyer's own mail as read")

    def logout(self):
        self.commands.append("LOGOUT")
        return ("BYE", [b""])


HOLDER: dict[str, FakeIMAP] = {}


def install(**kw) -> None:
    # CLEARED, NOT JUST REPLACED. A sweep that finds nothing to do never opens a connection, so
    # a stale fake left in HOLDER would answer "yes, something was appended" on behalf of the
    # PREVIOUS scenario — which is how an exactly-once check passes while appending twice.
    HOLDER.clear()

    def factory(host, **kwargs):
        f = FakeIMAP(host, **kwargs)
        for k, v in kw.items():
            setattr(f, k, v)
        HOLDER["f"] = f
        return f
    imaplib.IMAP4_SSL = factory                                   # type: ignore[assignment]


def seed(zcid: str, *, mid: str, platform: str = "email", frm: str = "jane@buyer.com",
         body: str = "Are you open Sunday?") -> None:
    store.upsert_conversation(space=SPACE, zcid=zcid, platform=platform,
                              participant=frm, account_id=OWNER)
    store.record_message(space=SPACE, zcid=zcid, zmid=mid, direction="in",
                         sent_by=frm, body=body)


def last_message():
    # POLICY=default, so this reads the message the way a mail client does (and gives us
    # `get_content`). The bytes under test are the bytes the server was handed.
    return email_mod.message_from_bytes(HOLDER["f"].appended[-1][2], policy=email.policy.default)


install()
quiet(bs.put_email, host="imap.gmail.com", user=OWNER, password=APP_PW)

# ── THE DRAFT ARRIVES IN THE THREAD, IN THE FOLDER THE SERVER NAMES ─────────────────────────
print("\n— THE ANSWER IS WAITING UNDER THE CUSTOMER'S EMAIL —")
install(subjects={"<q1@buyer.com>": "Sunday hours?"})
seed("<q1@buyer.com>", mid="<q1@buyer.com>")
drafts.put(space=SPACE, zcid="<q1@buyer.com>", in_reply_to="<q1@buyer.com>",
           body="Yes — we're open 9 to 4 on Sunday. Happy to book you in.")
res = quiet(md.sweep, SPACE)
f = HOLDER["f"]
ok("one draft was appended — once, not once per retry",
   res.get("appended") == 1 and len(f.appended) == 1, f"{res} / {len(f.appended)}")
ok(f"...into the folder the SERVER named ({DRAFTS_FOLDER}), not a hard-coded [Gmail]/Drafts",
   bool(f.appended) and f.appended[-1][0] == DRAFTS_FOLDER,
   str(f.appended[-1][0]) if f.appended else "nothing appended")
ok("...with the \\Draft flag, so Gmail opens it in the composer instead of listing it",
   bool(f.appended) and "\\Draft" in f.appended[-1][1], str(f.appended[-1][1]) if f.appended else "")

m = last_message()
ok("From: is the buyer's own address — the reply goes out as them", m["From"] == OWNER, str(m["From"]))
ok("To: is the customer who actually wrote the message being answered",
   m["To"] == "jane@buyer.com", str(m["To"]))
ok("In-Reply-To: names the message it answers", m["In-Reply-To"] == "<q1@buyer.com>",
   str(m["In-Reply-To"]))
ok("References: carries the thread, so every client files it under the original",
   "<q1@buyer.com>" in str(m["References"]), str(m["References"]))
ok("Subject: reads as a reply, fetched from the message itself",
   m["Subject"] == "Re: Sunday hours?", str(m["Subject"]))
ok("the body is the drafted reply, unedited",
   "open 9 to 4 on Sunday" in m.get_content(), m.get_content()[:80])

# THE ONE THAT WOULD BE SILENT. `core.box_mail` marks everything the BOX sends so ingest can skip
# it; a draft is not the box's mail. Mark it and the buyer taps Send, the message comes back
# round, and the sweep throws away the only outbound that matters.
ok("NO X-Ownbox mark on a draft — the buyer's real send must not be skipped by ingest",
   m.get("X-Ownbox") is None, str(m.get("X-Ownbox")))

ok("the mailbox was opened READ-ONLY", f.readonly is True, str(f.readonly))
ok("...and no STORE was issued, so nothing in their mailbox was marked read",
   not any(c.startswith("STORE") for c in f.commands), str(f.commands))
ok("one login for the sweep, not one per draft", f.logins == 1, str(f.logins))

# ── EXACTLY ONCE, AND A DELETED DRAFT STAYS DELETED ─────────────────────────────────────────
print("\n— IT IS APPENDED ONCE, EVER —")
install(subjects={"<q1@buyer.com>": "Sunday hours?"})
res2 = quiet(md.sweep, SPACE)
ok("a second sweep appends nothing — the row carries its own stamp",
   res2.get("appended") == 0 and "f" not in HOLDER, str(res2))
row = drafts.for_inbound(SPACE, "<q1@buyer.com>")
ok("...and the stamp says WHEN, not merely that it happened",
   bool(row and row.get("mailbox_at")), str(row and row.get("mailbox_at")))

# The buyer swipes the draft away in Gmail. Nothing tells the box, and nothing should: the stamp
# is written once and never reconsidered. Software that puts back what you threw away is
# software people uninstall.
install(subjects={"<q1@buyer.com>": "Sunday hours?"})
for _ in range(3):
    quiet(md.sweep, SPACE)
ok("a draft the buyer DELETED in Gmail is never put back, however often the rail runs",
   "f" not in HOLDER, str(HOLDER.get("f") and len(HOLDER["f"].appended)))

# ── A SERVER WITH NO \Drafts COSTS NOTHING ──────────────────────────────────────────────────
print("\n— A MISSING FOLDER NEVER COSTS A DRAFT —")
install(subjects={"<q2@buyer.com>": "Quote please"},
        folders=[b'(\\HasNoChildren) "/" "INBOX"'])
seed("<q2@buyer.com>", mid="<q2@buyer.com>")
drafts.put(space=SPACE, zcid="<q2@buyer.com>", in_reply_to="<q2@buyer.com>", body="Sure — one sec.")
res3 = quiet(md.sweep, SPACE)
ok("nothing is appended when the server offers no \\Drafts mailbox",
   res3.get("appended") == 0 and not HOLDER["f"].appended, str(res3))
ok("...and the draft is NOT stamped, so it is still the box's to show and to retry",
   not (drafts.for_inbound(SPACE, "<q2@buyer.com>") or {}).get("mailbox_at"))

# The same row, on a server that HAS the folder, lands. This is what makes the check above a
# release rather than a loss: the retry actually happens.
install(subjects={"<q2@buyer.com>": "Quote please"})
res4 = quiet(md.sweep, SPACE)
ok("the very next sweep, on a server that has the folder, lands it",
   res4.get("appended") == 1, str(res4))

# ── A SERVER THAT REFUSES THE APPEND IS RETRIED, NOT SWALLOWED ──────────────────────────────
print("\n— A REFUSED APPEND IS RELEASED, NOT LOST —")
install(subjects={"<q3@buyer.com>": "Hours"}, append_reply="NO")
seed("<q3@buyer.com>", mid="<q3@buyer.com>")
drafts.put(space=SPACE, zcid="<q3@buyer.com>", in_reply_to="<q3@buyer.com>", body="We're open 9-5.")
res5 = quiet(md.sweep, SPACE)
ok("a server that answers NO appends nothing", res5.get("appended") == 0, str(res5))
ok("...and the claim is RELEASED, so the draft is tried again rather than lost silently",
   not (drafts.for_inbound(SPACE, "<q3@buyer.com>") or {}).get("mailbox_at"))
install(subjects={"<q3@buyer.com>": "Hours"})
ok("...proved: the next sweep lands it", quiet(md.sweep, SPACE).get("appended") == 1)

# ── WHAT IS NEVER PUT IN SOMEBODY'S MAILBOX ─────────────────────────────────────────────────
print("\n— WHAT NEVER REACHES THE MAILBOX —")
install(subjects={"<dm1>": "dm"})
seed("<dm1>", mid="<dm1>", platform="instagram", frm="jane.ig")
drafts.put(space=SPACE, zcid="<dm1>", in_reply_to="<dm1>", body="Thanks for the DM!")
ok("an Instagram draft is not appended to a mail folder — there is no thread to put it in",
   quiet(md.sweep, SPACE).get("appended") == 0)

install(subjects={"<q4@buyer.com>": "Stop"})
seed("<q4@buyer.com>", mid="<q4@buyer.com>")
drafts.put(space=SPACE, zcid="<q4@buyer.com>", in_reply_to="<q4@buyer.com>", body="Sorry to hear it.")
store.set_opted_out(SPACE, "<q4@buyer.com>")
ok("a draft for somebody who asked us to stop is never put one tap from being sent",
   quiet(md.sweep, SPACE).get("appended") == 0)

install(subjects={"<q5@buyer.com>": "Booking"})
seed("<q5@buyer.com>", mid="<q5@buyer.com>")
drafts.put(space=SPACE, zcid="<q5@buyer.com>", in_reply_to="<q5@buyer.com>", body="Booked you in.")
store.record_message(space=SPACE, zcid="<q5@buyer.com>", zmid="<out5@acme.com>",
                     direction="out", sent_by=OWNER, body="Already answered by hand.")
ok("a draft somebody has already answered past is STALE and never appended",
   quiet(md.sweep, SPACE).get("appended") == 0)

# ── A STRANGER'S MESSAGE-ID IS NOT A COMMAND ────────────────────────────────────────────────
#
# Found by OSDev1 on #1434 before it landed. `in_reply_to` is the CUSTOMER'S OWN `Message-ID:`
# header, copied off the wire by the sweep, and it was interpolated into an IMAP search key
# unescaped. A sender who puts a double quote in it closes our string and writes search keys of
# their own; one that matches the whole mailbox, plus a reader that took the newest hit, puts an
# UNRELATED customer's subject on a draft addressed to this one — and the buyer taps Send.
print("\n— AN INJECTED MESSAGE-ID CANNOT REACH THE SEARCH —")

EVIL = '<a" OR ALL "@evil.com>'
# THE PRIVATE MESSAGE IS LAST ON PURPOSE. The injected key matches the whole mailbox and the
# reader took `split()[-1]` — the NEWEST hit. Put the attacker's own message last and the attack
# lands on their own subject by luck, and this check passes against the broken build. Ordered
# this way it reproduces the leak: verified by reverting the fix, which fails right here.
install(subjects={EVIL: "hello",
                  "<private@other-customer.com>": "Invoice #4021 — Ridgeline Dental"})
seed(EVIL, mid=EVIL, frm="attacker@evil.com")
drafts.put(space=SPACE, zcid=EVIL, in_reply_to=EVIL, body="Thanks for getting in touch.")
res6 = quiet(md.sweep, SPACE)
f = HOLDER["f"]
ok("the draft still lands — the guard costs a subject line, never the feature",
   res6.get("appended") == 1, str(res6))
m = last_message()
ok("...and it did NOT pick up another customer's subject",
   "Ridgeline" not in str(m["Subject"]) and "4021" not in str(m["Subject"]), str(m["Subject"]))
# AND IT TOOK THEIR OWN. The escaped key matched the attacker's message LITERALLY, quote and
# all, so the reply to them carries their subject — which is correct, and is the proof that
# escaping fixed this rather than merely disabling the lookup.
ok("...it used the attacker's OWN subject, because the quote matched literally",
   m["Subject"] == "Re: hello", str(m["Subject"]))
ok("the quote reached the server ESCAPED, so the string was never closed early",
   any('\\"' in q for q in f.searches), str(f.searches))
ok("...and the key is ONE well-formed quoted string — nothing trails it",
   all(f._search(q) is not None and q.rstrip().endswith('"') for q in f.searches),
   str(f.searches))

# THE SAME TRICK WITH A NEWLINE, which is worse: a header value carrying CR/LF writes headers of
# the attacker's choosing into a message the buyer is one tap from sending — a `Bcc:`, say.
BCC = "<b@x.com>\r\nBcc: attacker@evil.com"
install(subjects={BCC: "hi"})
seed(BCC, mid=BCC, frm="attacker@evil.com")
drafts.put(space=SPACE, zcid=BCC, in_reply_to=BCC, body="Hello.")
res7 = quiet(md.sweep, SPACE)
ok("a Message-ID carrying CR/LF appends nothing — no Bcc is written into the buyer's draft",
   res7.get("appended") == 0 and not HOLDER["f"].appended, str(res7))
# AND IT IS REFUSED FOR GOOD, not retried. Otherwise a handful of these fill the rail's limit
# every sweep and starve the drafts that would have landed.
ok("...and it is refused FOREVER, so it cannot starve the queue behind it",
   bool((drafts.for_inbound(SPACE, BCC) or {}).get("mailbox_at")),
   str((drafts.for_inbound(SPACE, BCC) or {}).get("mailbox_at")))
install(subjects={BCC: "hi"})
ok("...proved: the next sweep does not even consider it",
   quiet(md.sweep, SPACE).get("considered") == 0)

# THE SECOND GUARD, ON ITS OWN. Even if a search ever goes wrong again, the subject is taken only
# from the message we actually asked about.
print("\n— THE SUBJECT COMES FROM THE MESSAGE WE ASKED ABOUT —")
ok("_imap_quoted refuses what no quoted string can hold, and escapes what one can",
   ec._imap_quoted('<a"b>') == '"<a\\"b>"'
   and ec._imap_quoted("<a\\b>") == '"<a\\\\b>"'
   and ec._imap_quoted("<a\r\nb>") == ""
   and ec._imap_quoted("<a\x00b>") == ""
   and ec._imap_quoted("<Ünicode>") == "",
   repr(ec._imap_quoted('<a"b>')))


class WrongMessage(FakeIMAP):
    """A server whose SEARCH answers with the wrong message, however it was asked.

    NOT A REALISTIC SERVER — the point is that the code does not have to trust one. This stands
    in for every future way the search could go wrong, including one nobody has thought of.
    """

    def _search(self, raw):
        return [1]


def install_wrong(**kw) -> None:
    HOLDER.clear()

    def factory(host, **kwargs):
        f = WrongMessage(host, **kwargs)
        for k, v in kw.items():
            setattr(f, k, v)
        HOLDER["f"] = f
        return f
    imaplib.IMAP4_SSL = factory                                   # type: ignore[assignment]


install_wrong(subjects={"<someone-else@x.com>": "Invoice #4021 — Ridgeline Dental"})
seed("<q9@buyer.com>", mid="<q9@buyer.com>")
drafts.put(space=SPACE, zcid="<q9@buyer.com>", in_reply_to="<q9@buyer.com>", body="On our way.")
res8 = quiet(md.sweep, SPACE)
m = last_message()
ok("a server that answers with the WRONG message yields no subject, not a stranger's",
   res8.get("appended") == 1 and m["Subject"] == "Re: your message", str(m["Subject"]))

# ── FIVE ROWS THAT CAN NEVER WORK MUST NOT HIDE THE ONE THAT CAN ────────────────────────────
#
# OSDev1 found this shape in the DRAFTER on 2026-09-22, an hour before this rail was written:
# `needs_a_draft` returned the same five rows every sweep, none of them draftable, so 72
# conversations with real text were never reached — for seven hours, with /health ok, systemd
# active and every counter reading zero-because-quiet rather than zero-because-stuck.
#
# THIS RAIL HAS THE IDENTICAL SHAPE. `waiting()` is LIMIT 5 and a "not this time" releases the
# claim, so anything permanently unappendable comes straight back to the front of the queue. The
# defence is that a fact about the ROW answers `None` and keeps its claim — and the only honest
# way to test that is to put five of them in front of a real one.
print("\n— A PERMANENTLY UNAPPENDABLE ROW LEAVES THE QUEUE —")
install(subjects={})
for i in range(5):
    zc = f"<blocker{i}@buyer.com>"
    # NO INBOUND MESSAGE ROW, so `_recipient` can never resolve an address for it — the exact
    # permanent condition, arrived at the way a box would arrive at it rather than by stubbing.
    store.upsert_conversation(space=SPACE, zcid=zc, platform="email",
                              participant="ghost", account_id=OWNER)
    store.record_message(space=SPACE, zcid=zc, zmid=f"<other{i}@x.com>", direction="in",
                         sent_by="ghost@buyer.com", body="hi")
    drafts.put(space=SPACE, zcid=zc, in_reply_to=f"<missing{i}@buyer.com>", body="Hello there.")

ok("five unappendable rows are enough to fill the sweep's limit",
   len(md.waiting(SPACE)) == 5, str(len(md.waiting(SPACE))))
install(subjects={})
first = quiet(md.sweep, SPACE)
ok("...the sweep appends none of them", first.get("appended") == 0, str(first))
ok("...and they are OUT of the queue afterwards, not back at the front",
   md.waiting(SPACE) == [], str(len(md.waiting(SPACE))))

# NOW THE ROW THAT WOULD HAVE BEEN STARVED. On the old code this never ran: the five came back
# every sweep, filled the limit, and this draft sat behind them until somebody looked.
install(subjects={"<real@buyer.com>": "Can you quote me?"})
seed("<real@buyer.com>", mid="<real@buyer.com>")
drafts.put(space=SPACE, zcid="<real@buyer.com>", in_reply_to="<real@buyer.com>",
           body="Happy to — what size is the job?")
res_real = quiet(md.sweep, SPACE)
ok("the real draft behind them is reached on the very next sweep",
   res_real.get("appended") == 1, str(res_real))
ok("...and it is the RIGHT one", "<real@buyer.com>" == last_message()["In-Reply-To"],
   str(last_message()["In-Reply-To"]))

# AND THE DISTINCTION HOLDS THE OTHER WAY: a server having a bad minute is still a retry, which
# the "REFUSED APPEND IS RELEASED" block above already proves. Both halves, or this is just a
# rule that drops work.

# ── AND THE RAIL IS WIRED, NOT MERELY WRITTEN ───────────────────────────────────────────────
print("\n— THE RAIL RUNS ON THE BOX —")
src = (ROOT / "marketing" / "customer_voice" / "__init__.py").read_text()
ok("the periodic is registered, so this happens on a box nobody is watching",
   "voice_drafts_to_mailbox" in src)
drafter_src = (ROOT / "marketing" / "customer_voice" / "drafter" / "draft.py").read_text()
ok("...and the DRAFTER still imports nothing that can reach a mailbox",
   "email_channel" not in drafter_src and "mailbox_drafts" not in drafter_src)
# THE COMMENTS MAY SAY `[Gmail]/Drafts` — they explain why it is never used. The CODE may not.
code = [ln for ln in (ROOT / "marketing" / "customer_voice" / "inbox"
                      / "email_channel.py").read_text().splitlines()
        if not ln.lstrip().startswith("#")]
ok("the folder is never named in the code — only found by its \\Drafts attribute",
   not any("[Gmail]" in ln for ln in code),
   next((ln.strip() for ln in code if "[Gmail]" in ln), ""))

print()
if FAILS:
    print(f"FAILED {len(FAILS)}: " + "; ".join(FAILS))
    sys.exit(1)
print("all ok")
