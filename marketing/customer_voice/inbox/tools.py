"""The inbox, offered to the connector — READ ONLY.

WHAT WAS TRUE BEFORE THIS. The `$499` card says "Claude and ChatGPT, connected", and a sold
customer_voice box exposed exactly four connector tools: the manifest and three Morning Review
readers (`docs/AUDIT_499_CARD.md` §4, measured on an exported box). **Zero of them were the
inbox.** A buyer who connected Claude to their unified inbox could ask it for a morning report
and for the list of tools, and nothing about a single message. The door was built and correctly
shut — `core/dispatch._seat_authorized` is a real per-seat credential with per-seat revocation —
there was simply nothing behind it.

READ ONLY, AND THAT IS A DECISION RATHER THAN A FIRST STEP. There is no send, no draft and no
reply here. A seat that can speak as the business is a different thing from a seat that can read
it: the owner's standing rule is that a reply goes out when a person presses send, and a tool
that let an assistant answer a customer would move that decision into a model without anyone
choosing to. The reply path stays on the screen, where a human is looking at it.

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
