"""The inbox, offered to the connector — READ ONLY.

WHAT WAS TRUE BEFORE THIS. The `$499` card says "Claude and ChatGPT, connected", and a sold
customer_voice box exposed exactly four connector tools: the manifest and three Morning Review
readers (`docs/AUDIT_499_CARD.md` §4, measured on an exported box). **Zero of them were the
inbox.** A buyer who connected Claude to their unified inbox could ask it for a morning report
and for the list of tools, and nothing about a single message. The door was built and correctly
shut — `core/dispatch._seat_authorized` is a real per-seat credential with per-seat revocation —
there was simply nothing behind it.

READ, AND SINCE 2026-09-21 ONE WRITE - WHICH IS A DRAFT, NOT A REPLY. The paragraph that stood
here said there was "no send, no draft and no reply", and the reasoning it gave was about a seat
that could SPEAK AS THE BUSINESS. That reasoning survives intact, and `draft_reply` does not
touch it: a draft lands in `inbox_drafts`, on the screen, under the same send button a person
was always going to press. Nothing reachable from a connector seat sends, and the structural
guarantee still holds - tests/test_customer_voice.py bans a CALL named `post` in this whole lane.

WHAT CHANGED IS THE OWNER'S INSTRUCTION, 2026-09-21: an AI coworker has to be able to OPERATE
the box, not only look at it. A connector that can read every customer message and cannot offer
a single sentence back is a demo, not a colleague. `write:proposals` has been granted to the
`act` and `service` roles since the capability list was written and NO TOOL HAS EVER HELD IT -
this is the tool that lane was shaped for, and `annotations_for` already answers for it: our
writes are proposals a human approves.

A `read` SEAT STILL CANNOT REACH IT. `draft_reply` is `min_role="act"`, so the credential a
buyer mints for a read-only assistant does not silently gain a write the day this ships.

WHY THIS FILE IS IMPORTED OUTSIDE THE SDK GATE. `inbox/__init__.py` is fail-closed: a drifted or
unverifiable `zernio-sdk` leaves the department INERT, because sending on a vendor SDK nobody can
verify is the failure that gate exists for. **These tools touch no vendor.** They read rows this
box already holds on its own disk, so making them share the poller's fate would mean a customer
whose SDK drifted also loses the ability to read their own conversations — data they own, sitting
in their own database, withheld over an unrelated fault. Intake stops; reading does not.

SPACE IS THE TENANT BOUNDARY AND THE SEAT DOES NOT CHOOSE IT. Every reader below takes the
Space from the box's own configuration, exactly as the screen does, and there is no argument that
could widen it. A connector seat that could name its own Space would be the one place an outsider
picks which client's conversations to read.
"""
from core.connector import tools
from core.logging import get_logger

log = get_logger(__name__)

MACHINE = "inbox"
MAX_LIMIT = 100


def _space() -> str:
    """The Space this box answers for — `core.spaces`, never the seat and never a guess.

    ONE READER, THE SAME ONE THE SCREEN USES. `app._space()`'s own note says why: two scopes that
    resolve the tenant differently can disagree about whose data is being shown, and on this
    surface that means one client reading another client's customer messages. A connector has no
    request and no host, so there is no label to resolve — which makes this the box's own single
    tenant, `spaces.DEFAULT`.

    MY FIRST VERSION INVENTED ITS OWN RESOLUTION and read `config["spaces"]` as a dict with a
    `default` key. `spaces` is a LIST, so it raised AttributeError the first time a tool ran —
    caught by exercising the tools rather than by reading them. The lesson is the one app._space
    already wrote down: do not add a second way to answer this question.
    """
    from core import spaces
    return str(spaces.DEFAULT)


def _clamp(value, default: int, high: int = MAX_LIMIT) -> int:
    """A limit a caller cannot use to read the whole box in one request, or to ask for none."""
    try:
        n = int(value if value is not None else default)
    except (TypeError, ValueError):
        return default
    return max(1, min(n, high))


def _shape(row: dict) -> dict:
    """One conversation as the connector should see it.

    NAMED FIELDS, NOT `dict(row)`. Passing the row through would publish every column this table
    ever grows — including ones added later for reasons that have nothing to do with a connector
    — and the first time that matters nobody will notice. A list that has to be edited to add a
    field is the point.
    """
    return {
        "id": row.get("zernio_conversation_id"),
        "channel": row.get("platform"),
        "who": row.get("participant"),
        "last_inbound_at": row.get("last_inbound_at"),
        "messages": row.get("message_count"),
        "opted_out": bool(row.get("opted_out")),
    }


def list_conversations(limit=None, channel=None):
    """Who has spoken to this business, most recent first."""
    from marketing.customer_voice.inbox import store
    rows = store.list_conversations(_space(), limit=_clamp(limit, 25),
                                    platform=(str(channel).strip().lower() or None) if channel else None)
    return {"conversations": [_shape(r) for r in rows],
            "note": "newest inbound first; a conversation nobody has replied to yet is still here"}


def search(query=None, limit=None, channel=None):
    """Find a conversation by something that was SAID in it, or by who said it."""
    from marketing.customer_voice.inbox import store
    q = str(query or "").strip()
    if not q:
        # An empty search is a caller mistake, not an instruction to return the inbox.
        return {"conversations": [], "note": "no query given, so nothing was searched"}
    rows = store.search_conversations(_space(), q, limit=_clamp(limit, 25),
                                      platform=(str(channel).strip().lower() or None) if channel else None)
    return {"query": q, "conversations": [_shape(r) for r in rows],
            "note": "matches message text and the participant's name; one row per conversation"}


def read_conversation(id=None, limit=None):
    """Every message in one conversation, oldest first — the thread as a person reads it."""
    from marketing.customer_voice.inbox import store
    zcid = str(id or "").strip()
    if not zcid:
        return {"error": "give the conversation id from list_conversations or search"}
    space = _space()
    rows = store.messages_for(space, zcid, limit=_clamp(limit, 100, 500))
    if not rows:
        # SAYS WHICH KIND OF NOTHING IT IS. An empty thread and an id that belongs to another
        # Space must not read alike to the caller; the second is a boundary, not a result.
        return {"id": zcid, "messages": [],
                "note": "no messages on this box for that conversation id"}
    return {"id": zcid, "messages": [
        {"at": m.get("created_at"), "direction": m.get("direction"),
         "by": m.get("sent_by"), "text": m.get("body")} for m in rows]}


tools.register(
    "list_conversations",
    fn=list_conversations, machine=MACHINE, min_role="read",
    capability="read:inbox",
    description="Who has spoken to this business, most recent first. Returns conversations, not "
                "messages — use read_conversation for the text of one.",
    args={"limit": {"type": "integer", "required": False,
                    "description": "How many, 1-100. Defaults to 25."},
          "channel": {"type": "string", "required": False,
                      "description": "Narrow to one channel, e.g. instagram or messenger."}},
)

tools.register(
    "search",
    fn=search, machine=MACHINE, min_role="read",
    capability="read:inbox",
    description="Find conversations by something said in them, or by who said it. Matches message "
                "text and the participant's name; returns one row per conversation.",
    args={"query": {"type": "string", "required": True,
                    "description": "What to look for. Punctuation is matched literally."},
          "limit": {"type": "integer", "required": False,
                    "description": "How many, 1-100. Defaults to 25."},
          "channel": {"type": "string", "required": False,
                      "description": "Narrow to one channel."}},
)

tools.register(
    "read_conversation",
    fn=read_conversation, machine=MACHINE, min_role="read",
    capability="read:inbox",
    description="Every message in one conversation, oldest first.",
    args={"id": {"type": "string", "required": True,
                 "description": "The conversation id from list_conversations or search."},
          "limit": {"type": "integer", "required": False,
                    "description": "How many messages, 1-500. Defaults to 100."}},
)


def draft_reply(id=None, body=None):
    """Leave a reply WAITING ON THE SCREEN for one conversation. Nothing here sends it.

    THE WHOLE POINT OF THE SHAPE: this writes a row to `inbox_drafts` and stops. The person who
    owns the box opens the conversation, reads what their coworker wrote, edits it or throws it
    away, and presses send themselves. That is the same path the box's own drafter has always
    used, and a connector seat gets no shortcut around it.

    IT CANNOT WRITE TWICE AGAINST ONE MESSAGE. `put()` is `INSERT OR IGNORE` on
    `UNIQUE (space, in_reply_to)`, so a model that retries - and they retry - leaves one draft,
    not two, and finds out which happened from `written`. A refusal here is reported as a fact
    rather than an error for the same reason: a tool that 500s on a duplicate teaches a model to
    treat a working box as broken.

    AN OPTED-OUT CONVERSATION IS REFUSED. `needs_a_draft` already excludes them, with the note
    that drafting for somebody who asked us to stop is work whose only use is a send. A seat
    naming the conversation directly must not get what the sweep is denied.
    """
    from marketing.customer_voice.drafter import store as drafts
    from marketing.customer_voice.inbox import store

    zcid = str(id or "").strip()
    text = str(body or "").strip()
    if not zcid:
        return {"error": "give the conversation id from list_conversations or search"}
    if not text:
        return {"error": "give the reply to leave on the screen, as body"}

    space = _space()
    convo = store.get_conversation(space, zcid)
    if convo is None:
        # Same discipline as read_conversation: says which kind of nothing this is, because an
        # id belonging to another Space must not read like an empty thread.
        return {"id": zcid, "written": False,
                "note": "no conversation on this box with that id"}
    if convo.get("opted_out"):
        return {"id": zcid, "written": False,
                "note": "this person asked not to be contacted, so nothing was drafted"}

    inbound = drafts.newest_inbound(space, zcid)
    if inbound is None:
        return {"id": zcid, "written": False,
                "note": "nobody has written in on this conversation, so there is nothing to reply to"}

    written = drafts.put(space=space, zcid=zcid, in_reply_to=inbound["id"], body=text)
    log.info("inbox.tool_draft_reply", space=space, conversation=zcid, written=written,
             chars=len(text))
    return {"id": zcid, "written": written, "replying_to": inbound["id"],
            "note": ("waiting on the screen - the owner of this box sends it, or does not"
                     if written else
                     "a draft was already waiting for that message, so this one was not added")}


tools.register(
    "draft_reply",
    fn=draft_reply, machine=MACHINE, min_role="act",
    capability="write:proposals",
    description="Leave a suggested reply waiting on the screen for one conversation. It is NOT "
                "sent: the person who owns this box reads it, edits it, and presses send "
                "themselves. One draft per incoming message.",
    args={"id": {"type": "string", "required": True,
                 "description": "The conversation id from list_conversations or search."},
          "body": {"type": "string", "required": True,
                   "description": "The reply to leave on the screen, in the business's own voice."}},
)
