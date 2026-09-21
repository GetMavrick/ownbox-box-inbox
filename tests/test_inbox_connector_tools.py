"""The inbox is reachable through the connector — the $499 card's bullet 4.

WHAT WAS TRUE BEFORE. `docs/AUDIT_499_CARD.md` §4, measured on an exported box: a sold
customer_voice box exposed four connector tools and all four were the manifest or the Morning
Review. ZERO were the inbox. A buyer who connected Claude to their unified inbox could ask it for
a morning report and for the list of tools, and nothing about a single message. The door was built
and correctly shut; there was nothing behind it.

WHAT THIS SUITE DEFENDS:
  · the three readers actually register on a box that has this lane
  · they register even when the vendor SDK is INERT, because they read this box's own rows
  · read only — nothing here can send, draft or reply as the business
  · a seat cannot choose its Space, and cannot ask for the whole box in one call

Run: python tests/test_inbox_connector_tools.py
"""
import os
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "ctools.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"

from core import state                                                # noqa: E402

state.init_db()

try:
    from marketing.customer_voice.schema import DDL
    from marketing.customer_voice.inbox import store, tools as inbox_tools
except ImportError:
    print("  --   no customer_voice on this box — it has no inbox to offer the connector")
    sys.exit(0)

from core.connector import tools as registry                          # noqa: E402

with state.connect() as c:
    c.executescript(DDL)

_failed = 0
SPACE = inbox_tools._space()


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def seed():
    store.upsert_conversation(space=SPACE, zcid="c1", participant="Dana Roofing",
                              last_inbound_at="2026-09-01T00:00:00Z")
    with state.connect() as c:
        c.execute("INSERT INTO inbox_messages (id, space, zernio_conversation_id, direction,"
                  " sent_by, body, created_at) VALUES (?,?,?,?,?,?,?)",
                  (str(uuid.uuid4()), SPACE, "c1", "in", "contact",
                   "there is a leak under the sink", "2026-09-01T00:00:00Z"))
    # another tenant, same words — the boundary a connector seat must never cross
    store.upsert_conversation(space="notthisbox", zcid="x1", participant="Someone Else",
                              last_inbound_at="2026-09-02T00:00:00Z")
    with state.connect() as c:
        c.execute("INSERT INTO inbox_messages (id, space, zernio_conversation_id, direction,"
                  " sent_by, body, created_at) VALUES (?,?,?,?,?,?,?)",
                  (str(uuid.uuid4()), "notthisbox", "x1", "in", "contact",
                   "there is a leak here too", "2026-09-02T00:00:00Z"))


INBOX_TOOLS = ("aios.inbox.list_conversations", "aios.inbox.search", "aios.inbox.read_conversation")
WRITE_TOOL = "aios.inbox.draft_reply"


def test_the_inbox_is_actually_offered():
    print("test_the_inbox_is_actually_offered")
    names = set(registry.registry())
    for t in INBOX_TOOLS:
        ok(f"{t} is registered", t in names, str(sorted(n for n in names if "inbox" in n)))
    ok("...and none of them is absent for a recorded reason",
       not [a for a in registry.absent() if "inbox" in str(a)], str(registry.absent()))


def test_a_seat_can_actually_SEE_them():
    """THE ASSERTION THIS FILE WAS MISSING, and its absence hid a real defect.

    The check above passes BY CONSTRUCTION: `tools.register` ran at import, so of course the names
    are in the registry. What it never asked was whether any seat can reach them — and they could
    not. The three tools shipped with `capability="read:inbox"` while `_ROLE_CAPABILITIES` granted
    that capability to NO role, so `visible_to` hid them from every seat, `tools/list` kept
    answering four, and card bullet 4 stayed false with a green suite underneath it (found by
    OSDev1 on #1205, 2026-09-15).

    A capability nobody holds is a tool nobody has. Registration is the cheap half; visibility is
    the half that decides whether the feature exists.
    """
    print("test_a_seat_can_actually_SEE_them")
    for role in ("read", "act", "service"):
        seen = {s["name"] for s in registry.visible_to({"role": role})}
        missing = [t for t in INBOX_TOOLS if t not in seen]
        ok(f"a {role} seat sees all three inbox tools", not missing,
           f"hidden from {role}: {missing} — is read:inbox granted to it?")
    # AND THE CONTRAST THAT PROVES THE CHECK MEANS SOMETHING. A role nobody defined holds nothing,
    # so a test that passed for every conceivable seat would not be testing the gate at all.
    ok("a seat with no role sees nothing", registry.visible_to({"role": "nonexistent"}) == [])
    ok("...and a seat with no role at all sees nothing", registry.visible_to({}) == [])


def test_a_real_call_through_the_connector_returns_the_inbox():
    """Not the function — the CALL PATH, the one an assistant actually takes.

    `tools.call` is where the role gate, the argument validation and the seat all meet. Calling
    the python function directly (as the tests below do, deliberately, to check its shapes) would
    have kept passing through the whole time these tools were invisible.
    """
    print("test_a_real_call_through_the_connector_returns_the_inbox")
    seat = {"id": "seat_test", "role": "read", "label": "a test seat"}

    def result(name, args):
        """`call` answers {"tool": …, "result": …}; the payload is under `result`.

        UNWRAPPED HERE RATHER THAN ASSUMED. My first version of this test read the payload off
        the top level and failed against correct data — which is the small version of the same
        lesson as the defect this file now guards: the call path has a shape, and code that never
        exercises it is guessing at that shape.
        """
        out, code = registry.call(name, args, seat)
        return (out or {}).get("result", out), code

    out, code = result("aios.inbox.list_conversations", {"limit": 5})
    ok("a read seat's call is answered, not refused", code == 200, f"{code}: {str(out)[:120]}")
    convs = (out or {}).get("conversations")
    ok("...and it returns this box's conversations",
       isinstance(convs, list) and [c["who"] for c in convs] == ["Dana Roofing"], str(out)[:160])
    out, code = result("aios.inbox.search", {"query": "leak"})
    ok("search answers through the call path too", code == 200, f"{code}: {str(out)[:120]}")
    ok("...with the conversation that said it",
       [c["who"] for c in (out or {}).get("conversations", [])] == ["Dana Roofing"], str(out)[:160])
    out, code = result("aios.inbox.read_conversation", {"id": "c1"})
    ok("read_conversation answers through the call path", code == 200, f"{code}: {str(out)[:120]}")
    ok("...and returns the thread's text",
       (out or {}).get("messages") and out["messages"][0]["text"].startswith("there is a leak"),
       str(out)[:160])
    ok("...and a required argument that is missing is refused, not guessed",
       registry.call("aios.inbox.read_conversation", {}, seat)[1] != 200)


def test_nothing_here_can_speak_as_the_business():
    print("test_nothing_here_can_speak_as_the_business")
    # THIS GUARD USED TO SAY "no draft either", AND IT WAS NARROWED ON 2026-09-21 RATHER THAN
    # DELETED. Its real job is the sentence in its own name: nothing reachable from a connector
    # seat may SPEAK AS THE BUSINESS. A draft does not speak — it lands on the screen under the
    # send button a person was always going to press. The owner's instruction that day was that
    # an AI coworker has to be able to operate the box, not only look at it.
    #
    # SO THE BAR MOVED UP, NOT DOWN. Before, one line asserted "no write exists". Now four lines
    # assert what a write may be: act-role, write:proposals, invisible to a read seat, and
    # REFUSED to one that asks anyway. A deleted guard would have proven none of that.
    inbox = {n: t for n, t in registry.registry().items() if ".inbox." in n}
    banned = [n for n in inbox if any(w in n for w in ("send", "publish", "post"))]
    ok("no tool name offers to send or publish", not banned, str(banned))

    readers = {n: t for n, t in inbox.items() if n in INBOX_TOOLS}
    ok("every reader is still read-role", all(t.get("min_role") == "read" for t in readers.values()),
       str({n: t.get("min_role") for n, t in readers.items()}))
    ok("...and every reader declares a read capability",
       all(str(t.get("capability", "")).startswith("read:") for t in readers.values()),
       str({n: t.get("capability") for n, t in readers.items()}))

    writers = {n: t for n, t in inbox.items() if n not in INBOX_TOOLS}
    ok("the only write on this lane is draft_reply", set(writers) == {WRITE_TOOL}, str(sorted(writers)))
    ok("...and it is act-role, on write:proposals",
       all(t.get("min_role") == "act" and t.get("capability") == "write:proposals"
           for t in writers.values()),
       str({n: (t.get("min_role"), t.get("capability")) for n, t in writers.items()}))

    # A READ SEAT MUST NOT EVEN SEE IT. `visible_to` is the thing `tools/list` answers with, and
    # the note on it says why hiding beats refusing: a model that can see a tool will try it and
    # then explain the refusal to a customer as if the box were broken.
    seen_read = {t["name"] for t in registry.visible_to({"role": "read"})}
    seen_act = {t["name"] for t in registry.visible_to({"role": "act"})}
    ok("a read seat cannot SEE draft_reply", WRITE_TOOL not in seen_read,
       str(sorted(n for n in seen_read if "inbox" in n)))
    ok("...and an act seat can", WRITE_TOOL in seen_act,
       str(sorted(n for n in seen_act if "inbox" in n)))

    # AND SEEING IS NOT THE ONLY DOOR. A seat that names the tool directly, without listing, is
    # refused by `call` on the same rank check — belt and braces, because the two have been
    # allowed to disagree before.
    _payload, status = registry.call(WRITE_TOOL, {"id": "c1", "body": "hello"},
                                  {"id": "seat_readonly", "role": "read"})
    ok("a read seat's direct call to draft_reply is refused", status >= 400, f"status {status}")


def test_the_readers_answer_with_this_boxs_own_rows():
    print("test_the_readers_answer_with_this_boxs_own_rows")
    got = inbox_tools.list_conversations()["conversations"]
    ok("list returns this box's conversation", [c["who"] for c in got] == ["Dana Roofing"], str(got))
    ok("...shaped by named fields, not the raw row",
       got and set(got[0]) == {"id", "channel", "who", "last_inbound_at", "messages", "opted_out"},
       str(sorted(got[0])) if got else "")
    found = inbox_tools.search(query="leak")["conversations"]
    ok("search finds it by what was said", [c["who"] for c in found] == ["Dana Roofing"], str(found))
    msgs = inbox_tools.read_conversation(id="c1")["messages"]
    ok("read returns the thread's text", msgs and msgs[0]["text"].startswith("there is a leak"),
       str(msgs))


def test_a_seat_cannot_widen_what_it_sees():
    print("test_a_seat_cannot_widen_what_it_sees")
    # NO SPACE ARGUMENT EXISTS. A connector seat that could name its own Space would be the one
    # place an outsider picks which client's conversations to read.
    import inspect
    for fn in (inbox_tools.list_conversations, inbox_tools.search, inbox_tools.read_conversation):
        params = set(inspect.signature(fn).parameters)
        ok(f"{fn.__name__} takes no space argument", not (params & {"space", "tenant", "box"}),
           str(params))
    ok("the other tenant's matching conversation is not returned by search",
       [c["who"] for c in inbox_tools.search(query="leak")["conversations"]] == ["Dana Roofing"])
    ok("...nor by list", "Someone Else" not in
       [c["who"] for c in inbox_tools.list_conversations(limit=100)["conversations"]])
    ok("...and its conversation id reads as empty, not as someone else's thread",
       inbox_tools.read_conversation(id="x1")["messages"] == [])


def test_a_coworker_can_leave_a_draft_and_nothing_more():
    """The one write: a draft waiting on the screen, and NOTHING leaves the box.

    RUNS LAST ON PURPOSE. It has to give the seeded inbound message a `zernio_message_id` —
    `newest_inbound` will not answer without one, because `put()` keys its uniqueness on that id —
    and the readers above assert on exactly what this box holds.
    """
    print("test_a_coworker_can_leave_a_draft_and_nothing_more")
    from marketing.customer_voice.drafter import store as drafts
    with state.connect() as c:
        c.execute("UPDATE inbox_messages SET zernio_message_id = 'm1' "
                  " WHERE space = ? AND zernio_conversation_id = 'c1'", (SPACE,))

    first = inbox_tools.draft_reply(id="c1", body="We can be there Thursday morning.")
    ok("a draft is written", first.get("written") is True, str(first))
    ok("...against the message that was actually waiting", first.get("replying_to") == "m1", str(first))
    held = drafts.for_inbound(SPACE, "m1")
    ok("...and it is on the screen, in the buyer's own words", 
       held and held.get("body") == "We can be there Thursday morning.", str(held))

    # A MODEL RETRIES. `put()` is INSERT OR IGNORE on UNIQUE (space, in_reply_to), so the second
    # call must leave ONE draft and say so, rather than raising at a caller that did nothing wrong.
    again = inbox_tools.draft_reply(id="c1", body="A completely different answer.")
    ok("a second call leaves one draft, not two", again.get("written") is False, str(again))
    with state.connect() as c:
        n = c.execute("SELECT COUNT(*) FROM inbox_drafts WHERE space = ? AND in_reply_to = 'm1'",
                      (SPACE,)).fetchone()[0]
    ok("...and the table agrees there is exactly one", n == 1, f"{n} rows")

    # NOTHING LEFT THE BOX. The ledger is where a send would be recorded; a draft must never
    # reach it. This is the assertion that would go red if draft_reply ever grew a send.
    with state.connect() as c:
        sends = c.execute("SELECT COUNT(*) FROM inbox_send_ledger").fetchone()[0]
    ok("nothing was sent", sends == 0, f"{sends} rows in inbox_send_ledger")

    # THE REFUSALS, each saying which kind of nothing it is.
    unknown = inbox_tools.draft_reply(id="nosuch", body="hello")
    ok("an unknown conversation is refused, not invented", unknown.get("written") is False
       and "no conversation" in str(unknown.get("note", "")), str(unknown))
    other = inbox_tools.draft_reply(id="x1", body="hello")
    ok("another tenant's conversation reads as absent", other.get("written") is False, str(other))
    store.set_opted_out(SPACE, "c1")
    quiet = inbox_tools.draft_reply(id="c1", body="one more thing")
    ok("somebody who asked us to stop gets no draft written for them",
       quiet.get("written") is False and "not to be contacted" in str(quiet.get("note", "")),
       str(quiet))
    ok("...and an empty body is refused rather than stored",
       "error" in inbox_tools.draft_reply(id="c1", body="   "), "")


def test_a_caller_cannot_ask_for_the_whole_box_at_once():
    print("test_a_caller_cannot_ask_for_the_whole_box_at_once")
    ok("a huge limit is clamped", inbox_tools._clamp(99999, 25) == inbox_tools.MAX_LIMIT)
    ok("a zero or negative limit still returns something", inbox_tools._clamp(-4, 25) == 1)
    ok("junk falls back to the default", inbox_tools._clamp("nonsense", 25) == 25)
    ok("an empty search returns nothing and says so",
       inbox_tools.search(query="   ")["conversations"] == [])
    ok("read without an id explains itself rather than guessing",
       "error" in inbox_tools.read_conversation())


if __name__ == "__main__":
    seed()
    test_the_inbox_is_actually_offered()
    test_a_seat_can_actually_SEE_them()
    test_a_real_call_through_the_connector_returns_the_inbox()
    test_nothing_here_can_speak_as_the_business()
    test_the_readers_answer_with_this_boxs_own_rows()
    test_a_seat_cannot_widen_what_it_sees()
    test_a_caller_cannot_ask_for_the_whole_box_at_once()
    test_a_coworker_can_leave_a_draft_and_nothing_more()
    print("\nall ok" if not _failed else f"\n{_failed} FAILED")
    sys.exit(1 if _failed else 0)
