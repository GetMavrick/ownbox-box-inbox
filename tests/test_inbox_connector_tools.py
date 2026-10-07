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


INBOX_TOOLS = ("inbox.list_conversations", "inbox.search", "inbox.read_conversation",
               # step 1 of docs/SCOPE_INBOX_CONNECTOR.md (owner, 2026-10-02): what the screens show
               "inbox.waiting", "inbox.status", "inbox.settings", "inbox.connect",
               # saved replies (#1821, owner 2026-10-02)
               "inbox.saved_replies")
WRITE_TOOL = "inbox.draft_reply"
# STEP 3 (owner, 2026-10-02: "One-tap approve"): proposals, which send nothing until a person approves.
PROPOSE_TOOLS = ("inbox.propose_reply", "inbox.propose_drafts",
                 # step 4: the rest of the screen's controls, each a one-tap approval too
                 "inbox.propose_drafting", "inbox.propose_discard_draft", "inbox.propose_opt_out",
                 # the email signature (owner, 2026-10-02): set from a chat, on one tap
                 "inbox.propose_signature",
                 # cold pitches turned around (owner, 2026-10-02): switched on from a chat, on one tap
                 "inbox.propose_pitch_back",
                 # reply style per channel (owner, 2026-10-04): changed from a chat, on one tap
                 "inbox.propose_reply_style",
                 # each channel answered its own way (2026-10-07): the Channels screen's choice, on one tap
                 "inbox.propose_channel_mode",
                 # sending (owner, 2026-10-04): the first message and the hourly cap, changed from a chat, on one tap
                 "inbox.propose_first_message", "inbox.propose_hourly_cap",
                 # saved replies (#1821): one added from a chat, on one tap
                 "inbox.propose_saved_reply",
                 # the business's other addresses (2026-10-07): added or removed from a chat, on one tap
                 "inbox.propose_business_addresses")


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
        ok(f"a {role} seat sees every inbox reader", not missing,
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

    out, code = result("inbox.list_conversations", {"limit": 5})
    ok("a read seat's call is answered, not refused", code == 200, f"{code}: {str(out)[:120]}")
    convs = (out or {}).get("conversations")
    ok("...and it returns this box's conversations",
       isinstance(convs, list) and [c["who"] for c in convs] == ["Dana Roofing"], str(out)[:160])
    out, code = result("inbox.search", {"query": "leak"})
    ok("search answers through the call path too", code == 200, f"{code}: {str(out)[:120]}")
    ok("...with the conversation that said it",
       [c["who"] for c in (out or {}).get("conversations", [])] == ["Dana Roofing"], str(out)[:160])
    out, code = result("inbox.read_conversation", {"id": "c1"})
    ok("read_conversation answers through the call path", code == 200, f"{code}: {str(out)[:120]}")
    ok("...and returns the thread's text",
       (out or {}).get("messages") and out["messages"][0]["text"].startswith("there is a leak"),
       str(out)[:160])
    ok("...and a required argument that is missing is refused, not guessed",
       registry.call("inbox.read_conversation", {}, seat)[1] != 200)


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
    inbox = {n: t for n, t in registry.registry().items() if n.startswith("inbox.")}
    banned = [n for n in inbox if any(w in n for w in ("send", "publish", "post"))]
    ok("no tool name offers to send or publish", not banned, str(banned))

    readers = {n: t for n, t in inbox.items() if n in INBOX_TOOLS}
    ok("every reader is still read-role", all(t.get("min_role") == "read" for t in readers.values()),
       str({n: t.get("min_role") for n, t in readers.items()}))
    ok("...and every reader declares a read capability",
       all(str(t.get("capability", "")).startswith("read:") for t in readers.values()),
       str({n: t.get("capability") for n, t in readers.items()}))

    writers = {n: t for n, t in inbox.items() if n not in INBOX_TOOLS}
    ok("the only writes on this lane are draft_reply and the proposals",
       set(writers) == {WRITE_TOOL, *PROPOSE_TOOLS}, str(sorted(writers)))
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


def test_a_chat_sees_what_the_screens_show():
    """Step 1 of docs/SCOPE_INBOX_CONNECTOR.md. Owner, 2026-10-02: a buyer runs the box from their own AI,
    so the chat has to see who is waiting, whether the box runs, and how it is set, as the screens do."""
    print("test_a_chat_sees_what_the_screens_show")
    import pathlib
    from core import pause
    from marketing.customer_voice import claims
    from marketing.customer_voice.drafter import store as drafts

    # w1: waiting, with a reply written for it. h1: an automation runs it, so it is not waiting on anyone.
    for zcid, who in (("w1", "Avery Plumbing"), ("h1", "Kai Studio")):
        store.upsert_conversation(space=SPACE, zcid=zcid, participant=who, platform="instagram",
                                  last_inbound_at="2026-09-03T00:00:00Z")
        with state.connect() as c:
            c.execute("INSERT INTO inbox_messages (id, space, zernio_conversation_id, zernio_message_id,"
                      " direction, sent_by, body, created_at) VALUES (?,?,?,?,?,?,?,?)",
                      (str(uuid.uuid4()), SPACE, zcid, f"m-{zcid}", "in", "contact",
                       "do you have time friday", "2026-09-03T00:00:00Z"))
    drafts.put(space=SPACE, zcid="w1", in_reply_to="m-w1", body="Friday at 10 works.")
    claims.claim(SPACE, "h1", machine="lead_magnet", title="Lead Magnet")
    # one an automation finished with: it is no longer "being handled", and status must not say it is
    claims.claim(SPACE, "w1", machine="welcome", title="Welcome")
    claims.release(SPACE, "w1", machine="welcome")

    w = inbox_tools.waiting()
    who = [c["who"] for c in w["conversations"]]
    ok("waiting lists the person whose message was last", "Avery Plumbing" in who, str(who))
    ok("...but not one an automation is running", "Kai Studio" not in who, str(who))
    ok("...and its count is the screen's own count", w["waiting_on_you"] == store.awaiting_reply(SPACE),
       f"{w['waiting_on_you']} vs {store.awaiting_reply(SPACE)}")
    ready = [d for d in w["drafts_ready"] if d["conversation"] == "w1"]
    ok("the written reply is offered in full, with what it answers",
       ready and ready[0]["draft"] == "Friday at 10 works." and ready[0]["they_said"] == "do you have time friday",
       str(w["drafts_ready"]))
    ok("...and another tenant's conversation never appears",
       "Someone Else" not in who and all(d["who"] != "Someone Else" for d in w["drafts_ready"]))

    marker = pathlib.Path(tempfile.mkdtemp()) / "PAUSED"
    real, pause.MARKER = pause.MARKER, marker
    try:
        s = inbox_tools.status()
        ok("status says the box is running", s["box"] == "running", s["box"])
        pause.halt("test")
        s = inbox_tools.status()
        ok("...and says stopped once a person stops it", s["box"] == "stopped", s["box"])
    finally:
        pause.MARKER = real
    ok("status names the automation and whom it is handling",
       [(h["who"], h["handled_by"]) for h in s["handled_by_automations"]] == [("Kai Studio", "Lead Magnet")],
       str(s["handled_by_automations"]))
    ok("...counts sends against the cap", isinstance(s["sent_this_hour"], int) and s["hourly_send_cap"] > 0)
    ok("...lists the channels this box has heard on, by name",
       any(ch["name"] == "Instagram" for ch in s["channels"]), str(s["channels"]))
    ok("...and says whether writing replies is on", s["writing_replies"] in ("on", "off"))

    st = inbox_tools.settings()
    names = [x["name"] for x in st["settings"]]
    ok("settings lists every Inbox setting",
       names == ["writing_replies", "opener", "answering_email", "answering_instagram", "answering_messenger",
                 "hourly_send_cap", "reply_style_email", "reply_style_instagram", "reply_style_messenger",
                 "mailbox_drafts", "business_addresses"], str(names))
    ok("...each with what it means and where it is changed",
       all(x.get("means") and x.get("changed_at") for x in st["settings"]))
    ok("...and the connections, by state only",
       set(st["connections"]) == {"mailbox", "social_accounts", "ai_account", "set_up_at"},
       str(st["connections"]))
    flat = str(st) + str(s)
    ok("nothing secret is in either answer",
       not any(w in flat.lower() for w in ("password", "api_key", "token", "sk-ant")), flat[:200])

    seat = {"id": "seat_test", "role": "read", "label": "a test seat"}
    for name in ("inbox.waiting", "inbox.status", "inbox.settings"):
        _out, code = registry.call(name, {}, seat)
        ok(f"a read seat's {name} is answered through the call path", code == 200, str(code))
    for name in ("inbox.waiting", "inbox.status", "inbox.settings"):
        t = registry.registry()[name]
        ok(f"{name} has a title a person reads", registry.plain_title(t.get("title", "")), t.get("title"))


def test_a_reply_from_a_chat_waits_for_a_tap():
    """Step 3 of docs/SCOPE_INBOX_CONNECTOR.md. Owner, 2026-10-02: "One-tap approve". The AI asks; the owner
    sees the exact words and approves; only then does the screen's own send function run, once."""
    print("test_a_reply_from_a_chat_waits_for_a_tap")
    from core import approvals
    from marketing.customer_voice.drafter import store as drafts
    from marketing.customer_voice.inbox import reply

    calls = []
    real = reply.send_reply

    def fake_send(**kw):
        calls.append(kw)
        return {"status": "ok", "message_id": f"m{len(calls)}", "idem_key": kw["nonce"]}
    reply.send_reply = fake_send
    try:
        store.upsert_conversation(space=SPACE, zcid="p1", participant="Rowan Bakery", platform="instagram",
                                  last_inbound_at="2026-09-04T00:00:00Z")
        with state.connect() as c:
            c.execute("INSERT INTO inbox_messages (id, space, zernio_conversation_id, zernio_message_id,"
                      " direction, sent_by, body, created_at) VALUES (?,?,?,?,?,?,?,?)",
                      (str(uuid.uuid4()), SPACE, "p1", "m-p1", "in", "contact", "are you open sunday",
                       "2026-09-04T00:00:00Z"))

        seen_read = {t["name"] for t in registry.visible_to({"role": "read"})}
        seen_act = {t["name"] for t in registry.visible_to({"role": "act"})}
        ok("a read seat cannot see the proposals", not (set(PROPOSE_TOOLS) & seen_read))
        ok("...and an act seat can", set(PROPOSE_TOOLS) <= seen_act, str(sorted(seen_act)))
        ok("...and neither name offers to send", all("send" not in n for n in PROPOSE_TOOLS))

        seat = {"id": "seat_mine", "role": "act", "label": "My Claude"}
        out, code = registry.call("inbox.propose_reply", {"id": "p1", "body": "Yes, 9 to 2 on Sunday."}, seat)
        got = (out or {}).get("result", out)
        ok("asking is answered", code == 200 and got.get("asked") is True, str(out)[:200])
        ok("...and NOTHING was sent by asking", calls == [], str(calls))
        a = approvals.get(got.get("approval"))
        ok("the owner is shown the exact words, the person and the channel",
           a and a["detail"]["arguments"] == {"To": "Rowan Bakery", "On": "Instagram",
                                              "Message": "Yes, 9 to 2 on Sunday."}, str(a and a["detail"]))
        ok("...and who asked", a and a["proposed_by"] == "My Claude", str(a and a["proposed_by"]))

        again = inbox_tools.propose_reply(id="p1", body="Yes, 9 to 2 on Sunday.")
        ok("the same reply asked twice is one approval", again.get("approval") == a["id"] and again["repeat"])

        approvals.decide(a["id"], False, by="usr_owner")
        ok("declined: nothing is sent", calls == [])

        b = inbox_tools.propose_reply(id="p1", body="Yes, 9 to 2.")
        r1 = approvals.decide(b["approval"], True, by="usr_owner")
        r2 = approvals.decide(b["approval"], True, by="usr_owner")
        ok("approved: sent through the screen's own send function, once",
           len(calls) == 1 and calls[0]["zcid"] == "p1" and calls[0]["text"] == "Yes, 9 to 2."
           and r1["status"] == "done" and r2["ok"] is False, f"{calls} {r1} {r2}")
        ok("...as the owner, with a stable exactly-once key",
           calls[0]["user_id"] == state.owner_user()["id"] and calls[0]["nonce"].startswith("chat-"))
        ok("...and the owner reads what happened", "Sent to Rowan Bakery" in r1["text"], r1["text"])

        ok("an unknown conversation asks for nothing",
           inbox_tools.propose_reply(id="nosuch", body="hi").get("asked") is False)
        ok("an empty reply asks for nothing", "error" in inbox_tools.propose_reply(id="p1", body="  "))

        drafts.put(space=SPACE, zcid="p1", in_reply_to="m-p1", body="We open at 9 on Sunday.")
        d = inbox_tools.propose_drafts(ids="p1, nosuch")
        ok("written replies are asked for as written, unknown ids named",
           d.get("asked") and d["skipped"] == ["nosuch"]
           and approvals.get(d["approval"])["detail"]["sends"][0]["text"] == "We open at 9 on Sunday.", str(d))
        ok("...with the Waiting screen's own key, so the screen's button and this send once between them",
           approvals.get(d["approval"])["detail"]["sends"][0]["nonce"].startswith("waiting:"))
        ok("nothing to send is not an approval",
           inbox_tools.propose_drafts(ids="nosuch").get("asked") is False)

        def refusing(**kw):
            raise reply.ReplyRefused("this person has opted out")
        reply.send_reply = refusing
        e = inbox_tools.propose_reply(id="p1", body="One more thing.")
        r = approvals.decide(e["approval"], True, by="usr_owner")
        ok("a refusal at send time is told in words, not sent", r["status"] == "failed"
           and "opted out" in r["text"], str(r))

        store.set_opted_out(SPACE, "p1")
        ok("somebody who asked us to stop is never asked about",
           inbox_tools.propose_reply(id="p1", body="hello again").get("asked") is False)
    finally:
        reply.send_reply = real


def test_the_business_addresses_change_on_a_tap():
    """2026-10-07, the Mailbox screen: the business's other addresses, asked for from a chat. The owner is told plainly
    that mail FROM them is filed as sent and never answered, so a customer's address isn't approved by mistake.
    Nothing changes until the owner approves, and then only through store.put_business_addresses."""
    print("test_the_business_addresses_change_on_a_tap")
    from core import approvals, box_secrets
    real_state = box_secrets.email_state
    box_secrets.email_state = lambda: {"status": "connected", "user": "owner@acme.co"}
    try:
        desc = registry.registry()["inbox.propose_business_addresses"].get("description", "").lower()
        ok("its description says mail from them is filed as sent and never answered",
           "filed as sent" in desc and "never" in desc and "customer" in desc, desc)
        seat = {"id": "seat_mine", "role": "act", "label": "My Claude"}
        before = store.business_addresses()
        out, code = registry.call("inbox.propose_business_addresses", {"add": "FrontDesk@acme.co, sam@gmail.com"},
                                  seat)
        got = (out or {}).get("result", out)
        ok("asking is answered through the call path, and nothing changes by asking",
           code == 200 and got.get("asked") is True and store.business_addresses() == before, str(out)[:200])
        a = approvals.get(got.get("approval"))
        args = (a or {}).get("detail", {}).get("arguments", {})
        ok("the owner is shown the addresses, and that mail FROM them is filed as sent and never gets a draft",
           args.get("Add") == "frontdesk@acme.co, sam@gmail.com" and "FROM" in args.get("What that means", "")
           and "filed as sent" in args["What that means"] and "never gets a draft" in args["What that means"], args)
        approvals.decide(a["id"], True, by="usr_owner")
        ok("approved: both saved", {"frontdesk@acme.co", "sam@gmail.com"} <= store.business_addresses(),
           store.business_addresses())
        ok("asking for what is already there asks nothing",
           inbox_tools.propose_business_addresses(add="sam@gmail.com").get("asked") is False)
        ok("the mailbox's own address, a bad one, the 21st, or nothing at all asks for nothing",
           all(inbox_tools.propose_business_addresses(**kw).get("asked") is False
               for kw in ({"add": "owner@acme.co"}, {"add": "not an address"}, {},
                          {"add": ",".join(f"s{i}@acme.co" for i in range(19))})))
        b = inbox_tools.propose_business_addresses(remove="sam@gmail.com")
        approvals.decide(b["approval"], False, by="usr_owner")
        ok("declined: nothing changed", "sam@gmail.com" in store.business_addresses())
        calls, real = [], store.put_business_addresses

        def spy(*args_, **kw):
            calls.append((args_, kw))
            return real(*args_, **kw)
        store.put_business_addresses = spy
        try:
            c = inbox_tools.propose_business_addresses(remove="sam@gmail.com")
            r = approvals.decide(c["approval"], True, by="usr_owner")
        finally:
            store.put_business_addresses = real
        ok("approved: carried out once, by store.put_business_addresses, the screen's own write",
           len(calls) == 1 and "sam@gmail.com" not in calls[0][0][0] and "frontdesk@acme.co" in calls[0][0][0]
           and calls[0][1].get("own_address") == "owner@acme.co" and r["status"] == "done"
           and store.business_addresses() == {"frontdesk@acme.co"}, f"{calls} {r}")
        st = {x["name"]: x["value"] for x in inbox_tools.settings()["settings"]}
        ok("inbox.settings lists them", st.get("business_addresses") == ["frontdesk@acme.co"], st)
    finally:
        box_secrets.email_state = real_state


def test_a_channel_is_changed_on_a_tap():
    """2026-10-07, the Channels screen: how a channel is answered, asked for from a chat. Nothing changes until the
    owner approves, and then only through answering.put, the screen's own write. Email never answers on its own."""
    print("test_a_channel_is_changed_on_a_tap")
    from core import approvals
    from marketing.customer_voice.inbox import answering, sending

    def row(ch):
        return next(r for r in answering.get() if r["channel"] == ch)

    desc = registry.registry()["inbox.propose_channel_mode"].get("description", "").lower()
    ok("its description says auto sends with no approval, and that cold pitches still wait",
       "auto" in desc and "no approval" in desc and "cold pitch" in desc, desc)
    seat = {"id": "seat_mine", "role": "act", "label": "My Claude"}
    before = answering.get()
    out, code = registry.call("inbox.propose_channel_mode", {"channel": "instagram", "mode": "auto"}, seat)
    got = (out or {}).get("result", out)
    ok("asking is answered through the call path, and nothing changes by asking",
       code == 200 and got.get("asked") is True and answering.get() == before, str(out)[:200])
    a = approvals.get(got.get("approval"))
    ok("the owner is shown the channel and the choice, and who asked",
       a and a["detail"]["arguments"].get("Channel") == "Instagram"
       and a["detail"]["arguments"].get("When a message comes in") == "Auto-reply"
       and a["proposed_by"] == "My Claude", str(a and a["detail"]))
    approvals.decide(a["id"], True, by="usr_owner")
    ok("approved: Instagram answers on its own, its gate switched on", row("instagram")["mode"] == "auto"
       and sending.auto_reply_since("instagram"), row("instagram"))
    ok("asking for what is already set asks nothing",
       inbox_tools.propose_channel_mode(channel="instagram", mode="auto").get("asked") is False)
    ok("email is never asked for Auto-reply",
       inbox_tools.propose_channel_mode(channel="email", mode="auto").get("asked") is False)
    ok("an unknown channel, a bad style or nothing to change asks for nothing",
       all(inbox_tools.propose_channel_mode(**kw).get("asked") is False
           for kw in ({"channel": "fax", "mode": "draft"}, {"channel": "messenger", "style": "loud"},
                      {"channel": "messenger"})))
    was = row("messenger")
    b = inbox_tools.propose_channel_mode(channel="messenger", mode="off", style="sales")
    approvals.decide(b["approval"], False, by="usr_owner")
    ok("declined: nothing changed", row("messenger") == was, row("messenger"))
    calls, real = [], answering.put

    def spy(*a, **k):
        calls.append((a, k))
        return real(*a, **k)
    answering.put = spy
    try:
        c = inbox_tools.propose_channel_mode(channel="messenger", mode="off", style="sales")
        r = approvals.decide(c["approval"], True, by="usr_owner")
    finally:
        answering.put = real
    ok("approved: carried out once, by answering.put, the screen's own write",
       len(calls) == 1 and calls[0][0] == ("messenger",) and calls[0][1].get("mode") == "off"
       and calls[0][1].get("style") == "sales" and (row("messenger")["mode"], row("messenger")["style"])
       == ("off", "sales") and r["status"] == "done", f"{calls} {r}")


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
    test_a_chat_sees_what_the_screens_show()
    test_a_reply_from_a_chat_waits_for_a_tap()
    test_a_channel_is_changed_on_a_tap()
    test_the_business_addresses_change_on_a_tap()
    print("\nall ok" if not _failed else f"\n{_failed} FAILED")
    sys.exit(1 if _failed else 0)
