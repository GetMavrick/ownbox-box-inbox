"""The Drafts tab: the box wrote these, a person reads them, ticks, and they go.

OWNER, 2026-09-22: "a tab in the dashboard called Drafts where we can select groups of messages
and then send them off", and of the drafting itself: "that's one of the killer features for the
unified inbox. The big time-saver. Even Gmail doesn't have that."

WHAT THIS SUITE IS FOR. A screen that sends on behalf of a person, in bulk, under their own name,
to their own customers. The two ways it could hurt them are sending something they did not read
and sending something twice — so the assertions below are about WHAT IS ON THE SCREEN (the full
draft, and the question it answers) and about WHICH PATH SENDS (`inbox.reply.send_reply`, the one
the thread button already uses, and not a second one that would need the same rules kept in step).

"STILL WAITING" IS MEASURED, NOT MARKED. `drafter.store.waiting()` decides by looking for an
outbound message newer than the inbound — so a draft answered by hand, or edited and sent from the
thread, leaves this screen on its own. The tests for that are the ones that would catch a future
`sent_at` column quietly replacing the join.

Run: python tests/test_the_drafts_tab_sends_what_i_ticked.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "waiting.db")
os.environ.pop("ZERNIO_API_KEY", None)

from core import state                                              # noqa: E402
from core import spaces as _spaces                                  # noqa: E402

state.init_db()
_spaces.space_by_name = lambda name, allow_default_alias=True: {"name": name, "key": "k"}

from core import dash as _dash                                      # noqa: E402
from core.dispatch import app as flask_app                          # noqa: E402
from marketing.customer_voice import app as voice                   # noqa: E402
from marketing.customer_voice.drafter import store as drafts        # noqa: E402
from marketing.customer_voice.inbox import reply as _reply          # noqa: E402
from marketing.customer_voice.inbox import store as inbox           # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


with flask_app.test_request_context("/inbox/waiting"):
    SPACE = voice._space()

OWNER = state.owner_user()["id"]


def client():
    c = flask_app.test_client()
    c.set_cookie("aios_session", _dash.new_session(OWNER), domain="localhost")
    return c


def _at(table: str, ident: str, when: str) -> None:
    """Pin one row's created_at. record_message/put both stamp `now`, and this suite is about
    ORDER — which is unobservable when four rows land in the same millisecond."""
    col = "zernio_message_id" if table == "inbox_messages" else "id"
    with state.connect() as c:
        c.execute(f"UPDATE {table} SET created_at = ? WHERE {col} = ?", (when, ident))


def seed(zcid: str, *, who: str, asked: str, draft: str, asked_at: str,
         opted_out: bool = False) -> str:
    """One customer question with one draft answer, at a known time."""
    inbox.upsert_conversation(space=SPACE, zcid=zcid, participant=who, account_id="acct-1")
    if opted_out:
        inbox.set_opted_out(SPACE, zcid)
    mid = f"m-{zcid}"
    inbox.record_message(space=SPACE, zcid=zcid, zmid=mid, direction="in",
                         sent_by=who, body=asked)
    _at("inbox_messages", mid, asked_at)
    drafts.put(space=SPACE, zcid=zcid, in_reply_to=mid, body=draft)
    return mid


# ── 1. the queue ────────────────────────────────────────────────────────────────────────────
print("test_the_people_who_waited_longest_are_at_the_top")
seed("c-newer", who="Priya", asked="Do you deliver on Sundays?",
     draft="We do — Sunday runs leave at 9am.", asked_at="2026-09-20T10:00:00Z")
seed("c-older", who="Dana", asked="Is the blue one still in stock?",
     draft="It is — I can put one aside for you today.", asked_at="2026-09-18T08:00:00Z")

rows = drafts.waiting(SPACE)
ok("both drafts are waiting", len(rows) == 2, str(len(rows)))
ok("...oldest question first, so nobody is buried",
   [r["zcid"] for r in rows] == ["c-older", "c-newer"], str([r["zcid"] for r in rows]))
ok("...and the row carries the QUESTION, not only the answer",
   rows[0]["asked"] == "Is the blue one still in stock?", str(rows[0].get("asked")))
ok("...and who asked it", rows[0]["participant"] == "Dana", str(rows[0].get("participant")))
ok("waiting_count agrees with the list", drafts.waiting_count(SPACE) == 2,
   str(drafts.waiting_count(SPACE)))


print("test_a_draft_leaves_the_screen_when_the_person_is_answered")
# THE JOIN, NOT A COLUMN. Nobody marked this draft sent — an outbound message simply arrived
# after the question it answers, which is what happens when the thread's own reply button is
# used, or an edit of the draft, or a completely different answer typed by hand.
inbox.record_message(space=SPACE, zcid="c-newer", zmid="out-1", direction="out",
                     sent_by="owner", body="Yes, Sundays too.")
_at("inbox_messages", "out-1", "2026-09-20T11:00:00Z")
left = [r["zcid"] for r in drafts.waiting(SPACE)]
ok("the answered one is gone without anyone marking it", "c-newer" not in left, str(left))
ok("...and the unanswered one is still there", "c-older" in left, str(left))


print("test_an_older_outbound_does_not_retire_a_newer_question")
# A REPLY FROM BEFORE THE QUESTION ANSWERS NOTHING. This is the bug a naive "has any outbound"
# check ships: a customer who writes again after being answered would never get a second draft
# on the screen, and the busiest conversations are exactly the ones that come back.
seed("c-again", who="Marco", asked="It arrived damaged — what now?",
     draft="I am sorry. I have a replacement going out today, no charge.",
     asked_at="2026-09-21T09:00:00Z")
inbox.record_message(space=SPACE, zcid="c-again", zmid="out-old", direction="out",
                     sent_by="owner", body="Thanks for the order!")
_at("inbox_messages", "out-old", "2026-09-19T09:00:00Z")
ok("the new question is still waiting", "c-again" in [r["zcid"] for r in drafts.waiting(SPACE)])


print("test_dismissed_and_opted_out_never_appear")
seed("c-drop", who="Sam", asked="Anything on sale?", draft="Plenty — here is the list.",
     asked_at="2026-09-19T08:00:00Z")
drafts.dismiss(SPACE, drafts.for_inbound(SPACE, "m-c-drop")["id"])
seed("c-stop", who="Kit", asked="Take me off this.", draft="Done — you are unsubscribed.",
     asked_at="2026-09-19T09:00:00Z", opted_out=True)
zs = [r["zcid"] for r in drafts.waiting(SPACE)]
ok("a dismissed draft is gone", "c-drop" not in zs, str(zs))
ok("an opted-out person is never queued up to be written to", "c-stop" not in zs, str(zs))


# ── 2. the screen ───────────────────────────────────────────────────────────────────────────
print("test_the_screen_shows_the_whole_draft_and_the_question_it_answers")
r = client().get("/inbox/waiting")
ok("it renders", r.status_code == 200, str(r.status_code))
body = r.get_data(as_text=True)
ok("the WHOLE draft is on the screen, not a subject line",
   "I can put one aside for you today" in body)
ok("...and the question above it, which is how anyone judges the answer",
   "Is the blue one still in stock?" in body)
ok("...with a tick box carrying the conversation", 'name="pick"' in body and 'value="c-older"' in body)
ok("...and one button that sends the ticked ones", "Send the ones I ticked" in body)
ok("...and a way to edit instead of sending", "/inbox/inbox/c-older" in body)

print("test_anonymous_is_refused")
r = flask_app.test_client().get("/inbox/waiting")
ok("no session, no drafts", r.status_code in (302, 303, 401, 403), str(r.status_code))


# ── 3. sending ──────────────────────────────────────────────────────────────────────────────
print("test_ticking_two_sends_exactly_two_through_the_one_send_path")
SENT: list[dict] = []
_real_send = _reply.send_reply


def fake_send(*, space, zcid, text, user_id, nonce=None, **kw):
    SENT.append({"zcid": zcid, "text": text, "user_id": user_id, "nonce": nonce})
    return {"status": "sent", "message_id": f"out-{len(SENT)}"}


voice_reply_mod = sys.modules["marketing.customer_voice.inbox.reply"]
voice_reply_mod.send_reply = fake_send

r = client().post("/inbox/waiting", data={"pick": ["c-older", "c-again"]})
ok("the screen comes back", r.status_code == 200, str(r.status_code))
ok("exactly two sends, no more", len(SENT) == 2, str(len(SENT)))
ok("...each carrying the DRAFT's own words, not a re-render",
   any("put one aside" in s["text"] for s in SENT)
   and any("replacement going out today" in s["text"] for s in SENT),
   str([s["text"][:30] for s in SENT]))
ok("...attributed to the person who pressed the button",
   all(s["user_id"] == OWNER for s in SENT), str([s["user_id"] for s in SENT]))
ok("...each with its own idempotency nonce, so a double-press cannot double-send",
   len({s["nonce"] for s in SENT}) == 2 and all(s["nonce"] for s in SENT),
   str([s["nonce"] for s in SENT]))
ok("...and the screen says how many went", "Sent 2" in r.get_data(as_text=True))


print("test_an_unticked_draft_is_left_alone")
SENT.clear()
seed("c-untouched", who="Ada", asked="Do you ship to Ireland?", draft="We do, 3–5 days.",
     asked_at="2026-09-21T12:00:00Z")
seed("c-ticked", who="Lou", asked="Can I change the address?", draft="Yes — send me the new one.",
     asked_at="2026-09-21T13:00:00Z")
client().post("/inbox/waiting", data={"pick": ["c-ticked"]})
ok("only the ticked one went", [s["zcid"] for s in SENT] == ["c-ticked"],
   str([s["zcid"] for s in SENT]))


print("test_one_bad_send_does_not_swallow_the_rest")
# TEN TICKED DRAFTS ARE TEN INDEPENDENT SENDS. A vendor refusing the first must not silently
# take the nine after it with it, and the person has to be told WHICH one did not go — otherwise
# the only safe thing they can do is send all ten again.
SENT.clear()
seed("c-boom", who="Rae", asked="Where is my order?", draft="It is out for delivery today.",
     asked_at="2026-09-21T14:00:00Z")


def flaky(*, space, zcid, text, user_id, nonce=None, **kw):
    if zcid == "c-boom":
        raise RuntimeError("vendor said no")
    SENT.append({"zcid": zcid})
    return {"status": "sent"}


voice_reply_mod.send_reply = flaky
r = client().post("/inbox/waiting", data={"pick": ["c-boom", "c-untouched"]})
ok("the page still renders", r.status_code == 200, str(r.status_code))
ok("the healthy send still went", [s["zcid"] for s in SENT] == ["c-untouched"],
   str([s["zcid"] for s in SENT]))
page = r.get_data(as_text=True)
ok("...and the screen NAMES who did not go", "Rae" in page and "did not go" in page,
   page[page.find("did not go") - 80:page.find("did not go") + 20] if "did not go" in page else "no notice")
ok("...without leaking the exception text at the customer",
   "vendor said no" not in page)


print("test_a_tick_for_a_draft_that_vanished_is_skipped_not_crashed")
# TWO TABS, OR A SLOW READ. The list was drawn before somebody answered that person from their
# phone; the tick arrives for a draft that is no longer waiting. Sending it would be the double
# reply this screen exists to avoid.
SENT.clear()
voice_reply_mod.send_reply = fake_send
r = client().post("/inbox/waiting", data={"pick": ["c-gone-forever", "c-ticked"]})
ok("no crash", r.status_code == 200, str(r.status_code))
ok("the stale tick sent nothing", "c-gone-forever" not in [s["zcid"] for s in SENT],
   str([s["zcid"] for s in SENT]))

voice_reply_mod.send_reply = _real_send


print("test_an_empty_queue_says_so_in_a_sentence")
with state.connect() as c:
    c.execute("UPDATE inbox_drafts SET dismissed_at = ? WHERE space = ?",
              (state._now(), SPACE))
r = client().get("/inbox/waiting")
body = r.get_data(as_text=True)
ok("it renders", r.status_code == 200, str(r.status_code))
ok("...and says nothing is waiting, in words a buyer can act on",
   "Nothing is waiting" in body, body[:200])


print("\nFAILED" if _failed else "\nALL PASS")
sys.exit(1 if _failed else 0)
