"""Their thread screen gets our AI draft card and reads an email properly (#1990 Phase 1, step 1.3, the box's half).

The draft card sits above Zernio's composer: Send, Edit, Discard. The box hands the screen the draft still waiting on
this conversation, says on the list which conversations have one, and lets a person discard it. Send and Edit are the
ordinary send route with the words the person leaves in. An email also says where the sender's own words end and the
history they quote begins, so the thread can fold the history.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * a waiting draft does not reach the thread, or the list does not flag it;
  * a draft already answered (an outbound message after the question) is still offered;
  * Discard deletes the draft (it is the record the box got one wrong), dismisses one it was not shown, or leaves it
    on the screen;
  * the card cannot tell "drafting is off" or "no AI account" from "nothing to draft";
  * an email's quoted history is not split off, a word is lost in the split, or a chat message is split at all.

No network.

Run: python tests/test_the_thread_offers_the_draft.py
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
T = pathlib.Path(tempfile.mkdtemp(prefix="draftcard_"))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")
os.environ["AIOS_MY_MACHINES"] = str(T / "machines")
os.environ["DASH_TOKEN"] = "test-dash-pw"
os.environ.pop("ZERNIO_API_KEY", None)

from core import box_settings, dash, spaces as _spaces, state  # noqa: E402

state.init_db()
SPACE = _spaces.DEFAULT

from core.dispatch import app  # noqa: E402
from marketing.customer_voice.app_api import split_quoted  # noqa: E402
from marketing.customer_voice.drafter import store as drafts  # noqa: E402
from marketing.customer_voice.inbox import store  # noqa: E402

state.init_db()
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -- {str(detail)[:400]}" if not cond and detail else ""))
    if not cond:
        _failed += 1


o = app.test_client()
o.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))


def convo(zcid, platform, who, words):
    store.upsert_conversation(space=SPACE, zcid=zcid, platform=platform, participant=who, account_id="acct")
    store.record_message(space=SPACE, zcid=zcid, zmid=f"{zcid}-in", direction="in", sent_by="contact", body=words)


def flags():
    got = o.get("/inbox/api/conversations").get_json() or {}
    return {c["id"]: (c["metadata"]["aios"].get("draft") or {}).get("body") for c in got.get("data") or []}


def card(zcid):
    return o.get(f"/inbox/api/conversations/{zcid}/draft").get_json() or {}


print("test_the_waiting_draft_reaches_the_thread")
convo("ig-1", "instagram", "Dana", "Are you open Sunday?")
convo("ig-2", "instagram", "Sam", "How much for a cleaning?")
drafts.put(space=SPACE, zcid="ig-1", in_reply_to="ig-1-in", body="Yes, 9 to 5 on Sundays.")
c = card("ig-1")
ok("the thread gets the draft, its id and the question it answers",
   (c.get("draft") or {}).get("body") == "Yes, 9 to 5 on Sundays." and (c.get("draft") or {}).get("id")
   and c["draft"].get("asked") == "Are you open Sunday?", c)
ok("...a thread with nothing drafted gets none, and the card knows no AI account is connected (this box has none)",
   card("ig-2") == {"draft": None, "drafting": "not_connected"}, card("ig-2"))
ok("the list carries the draft on its conversation, and only there",
   flags() == {"ig-1": "Yes, 9 to 5 on Sundays.", "ig-2": None}, flags())
ok("a conversation that is not on this box is a 404", o.get("/inbox/api/conversations/nope/draft").status_code == 404)

print("\ntest_a_draft_already_answered_is_not_offered")
drafts.put(space=SPACE, zcid="ig-2", in_reply_to="ig-2-in", body="It is $120.")
store.record_message(space=SPACE, zcid="ig-2", zmid="ig-2-out", direction="out", sent_by="owner", body="$120, see you!")
ok("once someone has replied, the draft is spent", card("ig-2").get("draft") is None, card("ig-2"))
ok("...and the list stops carrying it", flags().get("ig-2") is None, flags())

print("\ntest_discard")
did = card("ig-1")["draft"]["id"]
r = o.post("/inbox/api/conversations/ig-1/draft/discard", json={"draftId": "not-this-one"})
ok("discarding a draft the screen was not shown is refused, and the draft stays",
   r.status_code == 404 and (card("ig-1").get("draft") or {}).get("id") == did, r.get_json())
r = o.post("/inbox/api/conversations/ig-1/draft/discard", json={"draftId": did})
ok("discarding the draft shown takes it off the screen", r.status_code == 200 and card("ig-1").get("draft") is None,
   r.get_json())
with state.connect() as conn:
    row = conn.execute("SELECT dismissed_at FROM inbox_drafts WHERE id = ?", (did,)).fetchone()
ok("...and keeps it, marked dismissed, never deleted", row is not None and row["dismissed_at"], dict(row or {}))
ok("discarding it twice is a 404, not a second write",
   o.post("/inbox/api/conversations/ig-1/draft/discard", json={"draftId": did}).status_code == 404)

print("\ntest_sending_from_the_card_needs_no_draft_id")
from marketing.customer_voice.inbox import reply  # noqa: E402
reply._deliver = lambda **kw: "sent-1"                  # the vendor call alone stands in; the rest of the path is real
LEARNED = []
_learn = drafts.learn
drafts.learn = lambda space, zcid, text: LEARNED.append((zcid, text)) or _learn(space, zcid, text)
convo("ig-4", "instagram", "Kim", "Do you do weekends?")
drafts.put(space=SPACE, zcid="ig-4", in_reply_to="ig-4-in", body="We do, Saturdays 9 to 1.")
r = o.post("/inbox/api/conversations/ig-4/messages", json={"accountId": "acct", "message": "We do, Saturdays 9 to 12."})
ok("Edit then Send goes out through the ordinary send route", r.status_code == 200, r.get_json())
ok("...the drafter learns what was actually sent", LEARNED == [("ig-4", "We do, Saturdays 9 to 12.")], LEARNED)
ok("...and the card clears itself", card("ig-4").get("draft") is None and flags().get("ig-4") is None, card("ig-4"))
drafts.learn = _learn

print("\ntest_the_card_knows_drafting_is_off")
box_settings.put("inbox", "drafts.enabled", False, set_by=None)
ok("with the owner's switch off, the card says off rather than nothing to draft",
   card("ig-1").get("drafting") == "off", card("ig-1"))
box_settings.put("inbox", "drafts.enabled", True, set_by=None)

print("\ntest_an_email_folds_what_it_quotes")
mail = ("Tuesday at 10 works.\n\nOn Mon, Oct 5, 2026 at 9:14 AM Priya Shah <priya@x.com>\nwrote:\n"
        "> Could you come Tuesday?\n> Thanks")
said, quoted = split_quoted(mail)
ok("Gmail's 'On ... wrote:' (even wrapped onto two lines) starts the history",
   said == "Tuesday at 10 works." and quoted.startswith("On Mon, Oct 5") and quoted.endswith("> Thanks"),
   (said, quoted))
ok("...and no word is lost in the split", "".join((said + quoted).split()) == "".join(mail.split()))
ok("Outlook's Original Message starts the history too",
   split_quoted("Yes.\n-----Original Message-----\nFrom: a@b.com")[0] == "Yes.")
ok("a trailing run of > lines is history", split_quoted("Sure\n> earlier\n>more\n") == ("Sure", "> earlier\n>more"))
ok("a forward with no note of its own is not emptied",
   split_quoted("On Mon x wrote:\n> only this") == ("On Mon x wrote:\n> only this", ""))
ok("a > in the middle of a sentence is not history", split_quoted("Price: $5 > $4") == ("Price: $5 > $4", ""))
convo("mail-1", "email", "Priya", mail)
m = (o.get("/inbox/api/conversations/mail-1/messages").get_json() or {}).get("messages", [{}])[0]
ok("the thread gets the email's own words and its history, and the whole message as before",
   m["metadata"]["aios"].get("said") == "Tuesday at 10 works." and m["metadata"]["aios"].get("quoted")
   and m.get("message") == mail, m)
convo("ig-3", "instagram", "Lee", "lol\n> that")
m = (o.get("/inbox/api/conversations/ig-3/messages").get_json() or {}).get("messages", [{}])[0]
ok("a chat message is never split", "quoted" not in m["metadata"]["aios"], m)

print("\nALL DRAFT-CARD CHECKS PASS" if not _failed else f"\n{_failed} DRAFT-CARD CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
