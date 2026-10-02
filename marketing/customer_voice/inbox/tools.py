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
    title="See who has messaged your business",
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
    title="Search your inbox",
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
    title="Read a conversation in your inbox",
    fn=read_conversation, machine=MACHINE, min_role="read",
    capability="read:inbox",
    description="Every message in one conversation, oldest first.",
    args={"id": {"type": "string", "required": True,
                 "description": "The conversation id from list_conversations or search."},
          "limit": {"type": "integer", "required": False,
                    "description": "How many messages, 1-500. Defaults to 100."}},
)


# ── STEP 1 OF docs/SCOPE_INBOX_CONNECTOR.md: what the screens show, readable from a chat ───────
# Owner, 2026-10-02: a buyer runs this box from their own AI, "they're outsiders and they need full
# control capabilities". Before anyone can act from a chat they have to be able to SEE what the
# screens see: who is waiting, whether the box is running, and how it is set. Each reader below asks
# the same function its screen asks, so the chat and the screen cannot disagree about one fact.


def _drafting() -> bool | None:
    """The Settings row's own answer (`app._drafting_on` reads exactly this), or None when it can't be read.

    NONE, NOT TRUE, ON AN ERROR (OSDev1's review of #1803): a reader that says "on" when it could not look is a
    status line that lies in the one direction a person would act on."""
    try:
        from marketing.customer_voice.drafter import draft as _draft
        return bool(_draft.enabled())
    except Exception:                    # noqa: BLE001 — a status line, never a failed tool
        return None


def _onoff(v) -> str:
    return "unknown" if v is None else ("on" if v else "off")


def _inbox_cfg() -> dict:
    from core.config import get_config
    return get_config().get("inbox") or {}


def _connections() -> dict:
    """Which doors the inbox reads through, and whether each one works. Stored status only: nothing
    asks a vendor, for the reason `app._channels_row` gives (a slow vendor must not hang a read).

    NEVER A KEY, A PASSWORD OR A VENDOR'S ERROR TEXT. The states are the closed sets the screens
    already show; the mailbox's address is shown because the screen shows it to everyone too."""
    from core import box_secrets
    mail = box_secrets.email_state()
    social = box_secrets.zernio_state()
    ai = box_secrets.anthropic_state()
    return {
        "mailbox": {"status": mail.get("status"), "address": mail.get("user"),
                    "can_send": mail.get("send")},
        "social_accounts": {"status": social.get("status")},
        "ai_account": {"status": ai.get("status")},
        "set_up_at": "/inbox/settings",
    }


def waiting(limit=None):
    """Who is waiting on a person, and the replies already written for them — the Waiting screen."""
    from marketing.customer_voice.drafter import store as drafts
    from marketing.customer_voice.inbox import store
    space = _space()
    n = _clamp(limit, 25)
    rows = store.list_conversations(space, limit=n, waiting=True)
    ready = drafts.waiting(space, limit=n)
    return {
        "waiting_on_you": store.awaiting_reply(space),
        "conversations": [_shape(r) for r in rows],
        "drafts_ready": [{"conversation": d.get("zcid"), "who": d.get("participant"),
                          "channel": d.get("platform"), "they_said": d.get("asked"),
                          "they_said_at": d.get("asked_at"), "draft": d.get("body"),
                          "draft_id": d.get("id")} for d in ready],
        "note": ("waiting means their message was the last one; a conversation an automation is "
                 "running is not counted. drafts_ready are replies written and not yet sent, "
                 "oldest first: nothing here has been sent"),
    }


def _channels_seen(space: str) -> list:
    from core import state
    with state.connect() as c:
        rows = c.execute(
            "SELECT platform, COUNT(*) n, MAX(last_inbound_at) last FROM inbox_conversations "
            " WHERE space = ? GROUP BY platform ORDER BY n DESC", (space,)).fetchall()
    from marketing.customer_voice.inbox import channels as _ch
    return [{"channel": r["platform"], "name": _ch.name(r["platform"], fallback="Unknown"),
             "conversations": int(r["n"]), "last_message_in": r["last"]} for r in rows]


def _held(space: str, limit: int) -> list:
    """Conversations an automation is running right now; the Inbox doesn't draft these."""
    from core import state
    from marketing.customer_voice import claims
    with state.connect() as c:
        rows = c.execute(
            "SELECT c.zernio_conversation_id id, c.title, c.claimed_at, c.expires_at, k.participant "
            "  FROM inbox_claims c LEFT JOIN inbox_conversations k ON k.space = c.space "
            "   AND k.zernio_conversation_id = c.zernio_conversation_id "
            f" WHERE c.space = ? AND {claims._ACTIVE} ORDER BY c.claimed_at DESC LIMIT ?",
            (space, int(limit))).fetchall()
    return [{"conversation": r["id"], "who": r["participant"], "handled_by": r["title"],
             "since": r["claimed_at"], "until": r["expires_at"]} for r in rows]


def status():
    """Is the box running, is it writing replies, what has it sent this hour, which channels work."""
    from core import pause
    from marketing.customer_voice.inbox import store
    space = _space()
    cfg = _inbox_cfg()
    cap = int(cfg.get("hourly_send_cap") or 40)
    stopped = pause.is_paused()
    return {
        "box": "stopped" if stopped else "running",
        "box_note": ("stopped: no new messages arrive and nothing sends on its own; a person can "
                     "still reply by hand" if stopped else "running"),
        "writing_replies": _onoff(_drafting()),
        "sent_this_hour": store.sends_last_hour(space),
        "hourly_send_cap": cap,
        "waiting_on_you": store.awaiting_reply(space),
        "channels": _channels_seen(space),
        "connections": _connections(),
        "handled_by_automations": _held(space, MAX_LIMIT),
    }


def settings():
    """Every Inbox setting, its value, and what it means — the Settings screen, in words."""
    from marketing.customer_voice.inbox import mailbox_drafts
    cfg = _inbox_cfg()
    autonomy = str(cfg.get("autonomy") or "off").strip().lower()
    return {
        "settings": [
            {"name": "writing_replies", "value": _onoff(_drafting()),
             "means": "the box writes a reply for each new message, for a person to read and send; "
                      "a written reply never sends on its own",
             "changed_at": "/inbox/settings"},
            {"name": "opener", "value": "on" if autonomy == "opener" else "off",
             "means": "when on, the box sends one fixed first message to a new conversation by "
                      "itself, within the hourly cap",
             "changed_at": "the box's configuration (not on a screen yet)"},
            {"name": "hourly_send_cap", "value": int(cfg.get("hourly_send_cap") or 40),
             "means": "the most messages the box sends in any hour, counted across every send",
             "changed_at": "the box's configuration (not on a screen yet)"},
            {"name": "mailbox_drafts", "value": "on" if mailbox_drafts.enabled() else "off",
             "means": "replies to email are also left in the mailbox's own Drafts folder",
             "changed_at": "the box's configuration (not on a screen yet)"},
        ],
        "connections": _connections(),
        "note": "reading only: nothing here changes a setting",
    }


tools.register(
    "waiting",
    title="See who is waiting on a reply",
    fn=waiting, machine=MACHINE, min_role="read",
    capability="read:inbox",
    description="Who is waiting on a person (their message was the last one), and every reply "
                "already written for them and not yet sent, oldest first. Nothing is sent.",
    args={"limit": {"type": "integer", "required": False,
                    "description": "How many of each, 1-100. Defaults to 25."}},
)

tools.register(
    "status",
    title="See how your inbox is running",
    fn=status, machine=MACHINE, min_role="read",
    capability="read:inbox",
    description="Whether the box is running or stopped, whether it is writing replies, how many "
                "messages it sent this hour against its cap, each channel and whether it works, "
                "and the conversations an automation is handling.",
)

tools.register(
    "settings",
    title="See your inbox settings",
    fn=settings, machine=MACHINE, min_role="read",
    capability="read:inbox",
    description="Every Inbox setting with its value, what it means and where it is changed, plus "
                "whether the mailbox, social accounts and AI account are connected. Changes nothing.",
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
    title="Suggest a reply for you to approve",
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


# ── STEP 3 OF docs/SCOPE_INBOX_CONNECTOR.md: a reply from a chat, sent when a person taps Approve ──
# Owner, 2026-10-02, choosing how a reply leaves the box from an outside chat: "One-tap approve". The
# AI writes it; the mobile app shows the exact words; the owner taps Approve and it goes.
#
# NAMED `propose_`, NEVER `send_`. Calling one sends nothing: it puts the exact words in the box's one
# approvals queue (core/approvals.py), the owner's mobile app is told, and only `approvals.decide`,
# which needs a person, runs `_run_send`. A model reading the tool list must not believe it sent.
#
# ONE SEND PATH. `_run_send` calls `reply.send_reply`, the function the thread's reply button and the
# Waiting screen's button call, so opt-out, the channel's own refusals and exactly-once hold as they
# do on the screen. The person who approves is the owner (the approvals page is owner-only), so the
# reply is sent as the owner's, the same as pressing send.

KIND = "inbox_send"
MAX_SENDS = 25        # one approval a person can actually read on a mobile screen
MAX_CHARS = 2000


def _nonce(zcid: str, inbound: str, text: str) -> str:
    """The same words to the same message are one proposal and one send, however often a model asks."""
    import hashlib
    return "chat-" + hashlib.sha256(f"{zcid}\n{inbound}\n{text}".encode()).hexdigest()[:24]


def _ask(sends: list, title: str, seat) -> dict:
    from core import approvals
    words = ({"To": sends[0]["who"], "On": sends[0]["channel"], "Message": sends[0]["text"]}
             if len(sends) == 1 else
             {f"{i}. {s['who']} ({s['channel']})": s["text"] for i, s in enumerate(sends, 1)})
    try:
        a = approvals.propose(KIND, machine=MACHINE, title=title,
                              detail={"app": "Inbox", "arguments": words, "sends": sends},
                              seat_id=str((seat or {}).get("label") or (seat or {}).get("id") or ""))
    except ValueError:
        # THE QUEUE'S OWN SIZE LIMIT, SAID AS WHAT TO DO (OSDev1's review of #1803): an approval has to fit on a
        # mobile screen, so a batch too long for it is a smaller ask, not an error the model can't read.
        return {"asked": False, "error": "that is too much for one approval; ask for fewer replies at a time"}
    return {"asked": True, "approval": a["id"], "repeat": bool(a.get("repeat")),
            "note": ("waiting for the owner, who sees these exact words in the mobile app and approves "
                     "or declines. Nothing has been sent. Don't ask again for the same reply.")}


def propose_reply(id=None, body=None, seat=None):
    """Ask the owner to approve sending these exact words to one conversation."""
    from marketing.customer_voice.drafter import store as drafts
    from marketing.customer_voice.inbox import channels as _ch, store
    zcid, text = str(id or "").strip(), str(body or "").strip()
    if not zcid:
        return {"error": "give the conversation id from list_conversations, search or waiting"}
    if not text:
        return {"error": "give the reply to send, as body"}
    if len(text) > MAX_CHARS:
        return {"error": f"a reply is at most {MAX_CHARS} characters"}
    space = _space()
    convo = store.get_conversation(space, zcid)
    if convo is None:
        return {"id": zcid, "asked": False, "note": "no conversation on this box with that id"}
    if convo.get("opted_out"):
        return {"id": zcid, "asked": False,
                "note": "this person asked not to be contacted, so nothing was asked for"}
    inbound = (drafts.newest_inbound(space, zcid) or {}).get("id") or ""
    who = str(convo.get("participant") or "this person")
    return _ask([{"conversation": zcid, "who": who,
                  "channel": _ch.name(convo.get("platform"), fallback="Unknown"),
                  "text": text, "nonce": _nonce(zcid, inbound, text)}],
                f"Send a reply to {who}"[:120], seat)


def propose_drafts(ids=None, seat=None):
    """Ask the owner to approve sending the replies already written for these conversations, as written."""
    from marketing.customer_voice.drafter import store as drafts
    from marketing.customer_voice.inbox import channels as _ch
    wanted = [x.strip() for x in str(ids or "").split(",") if x.strip()]
    if not wanted:
        return {"error": "give ids: the conversation ids from waiting's drafts_ready, separated by commas"}
    if len(wanted) > MAX_SENDS:
        return {"error": f"at most {MAX_SENDS} in one approval, so a person can read them all"}
    ready = {d["zcid"]: d for d in drafts.waiting(_space(), limit=200)}
    sends, skipped = [], []
    for zcid in dict.fromkeys(wanted):
        d = ready.get(zcid)
        if d is None:
            skipped.append(zcid)
            continue
        # THE WAITING SCREEN'S OWN NONCE, so a draft the owner sends from the screen meanwhile is the same
        # ledger key and goes once, whichever button is pressed first.
        sends.append({"conversation": zcid, "who": str(d.get("participant") or "this person"),
                      "channel": _ch.name(d.get("platform"), fallback="Unknown"),
                      "text": str(d.get("body") or ""), "nonce": f"waiting:{d['id']}"})
    if not sends:
        return {"asked": False, "skipped": skipped,
                "note": "none of those has a written reply waiting; see waiting's drafts_ready"}
    n = len(sends)
    out = _ask(sends, (f"Send the reply to {sends[0]['who']}" if n == 1 else f"Send {n} replies")[:120], seat)
    return out | {"skipped": skipped}


def _run_send(detail: dict) -> dict:
    """An approved proposal, carried out: every send through the screen's own send function."""
    from core import state
    from marketing.customer_voice.inbox import reply
    space, owner = _space(), state.owner_user()["id"]
    sent, missed = [], []
    for s in list((detail or {}).get("sends") or [])[:MAX_SENDS]:
        who = str(s.get("who") or "One reply")
        try:
            out = reply.send_reply(space=space, zcid=str(s.get("conversation") or ""),
                                   text=str(s.get("text") or ""), user_id=owner,
                                   nonce=str(s.get("nonce") or ""))
        except reply.ReplyIndeterminate:
            missed.append(f"{who}: it may have gone, so it was not sent again")
            continue
        except reply.ReplyRefused as e:
            missed.append(f"{who}: {e}")
            continue
        except Exception as e:                           # noqa: BLE001 — one bad send, not all of them
            log.error("inbox.approved_send_failed", conversation=s.get("conversation"),
                      error=type(e).__name__)
            missed.append(f"{who}: it could not be sent just now")
            continue
        (sent if str(out.get("status")) == "ok" else missed).append(who)
    bits = [f"Sent to {', '.join(sent)}." if sent else "Nothing was sent."] + [f"{m}." for m in missed]
    return {"ok": bool(sent) and not missed, "text": " ".join(bits)}


def _register_kind():
    from core import approvals
    approvals.register_kind(KIND, run=_run_send)


_register_kind()

tools.register(
    "propose_reply",
    title="Ask before sending a reply",
    fn=propose_reply, machine=MACHINE, min_role="act",
    capability="write:proposals", wants_seat=True,
    description="Ask the owner to approve sending these exact words to one conversation. This does NOT "
                "send: the owner sees the words in the mobile app and approves or declines, and only an "
                "approval sends it, once. Ask once per reply.",
    args={"id": {"type": "string", "required": True,
                 "description": "The conversation id from list_conversations, search or waiting."},
          "body": {"type": "string", "required": True,
                   "description": "The reply, exactly as it should be sent, in the business's own voice."}},
)

tools.register(
    "propose_drafts",
    title="Ask before sending written replies",
    fn=propose_drafts, machine=MACHINE, min_role="act",
    capability="write:proposals", wants_seat=True,
    description="Ask the owner to approve sending replies already written for these conversations, word for "
                "word, as one approval. This does NOT send: only the owner's approval does, once each.",
    args={"ids": {"type": "string", "required": True,
                  "description": "Conversation ids from waiting's drafts_ready, separated by commas (at most 25)."}},
)


# ── STEP 4 OF docs/SCOPE_INBOX_CONNECTOR.md: the rest of the screen's controls, from a chat ───────
# The same rule as the replies above: the AI asks, the owner taps Approve, and the screen's own function
# runs. Even the controls that only ever stop something (turning drafting off, throwing a draft away,
# marking somebody opted out) are approvals today. Owner, 2026-10-02: "For right now we want to draft."
# Letting an AI act on its own without a tap is a new capability for core to grant on purpose, later.

CONTROL = "inbox_control"


def _ask_control(action: str, target: dict, words: dict, title: str, seat) -> dict:
    from core import approvals
    try:
        a = approvals.propose(CONTROL, machine=MACHINE, title=title[:120],
                              detail={"app": "Inbox", "arguments": words, "action": action} | target,
                              seat_id=str((seat or {}).get("label") or (seat or {}).get("id") or ""))
    except ValueError:
        return {"asked": False, "error": "that is too much for one approval; ask for less at a time"}
    return {"asked": True, "approval": a["id"], "repeat": bool(a.get("repeat")),
            "note": "waiting for the owner, who approves or declines in the mobile app. Nothing has changed yet."}


def propose_drafting(on=None, seat=None):
    """Ask the owner to turn writing replies on or off (the Settings screen's one switch)."""
    if not isinstance(on, bool):
        return {"error": "give on: true to turn writing replies on, false to turn it off"}
    if _drafting() == on:
        return {"asked": False, "note": f"writing replies is already {'on' if on else 'off'}"}
    verb = "on" if on else "off"
    return _ask_control("drafting", {"on": on}, {"Change": f"Turn writing replies {verb}"},
                        f"Turn writing replies {verb}", seat)


def propose_discard_draft(id=None, seat=None):
    """Ask the owner to throw away the reply written for one conversation, unsent."""
    from marketing.customer_voice.drafter import store as drafts
    zcid = str(id or "").strip()
    d = next((x for x in drafts.waiting(_space(), limit=200) if x["zcid"] == zcid), None) if zcid else None
    if d is None:
        return {"asked": False, "note": "no written reply is waiting for that conversation; see waiting's drafts_ready"}
    who = str(d.get("participant") or "this person")
    return _ask_control("discard", {"conversation": zcid, "draft_id": d["id"]},
                        {"Throw away the reply to": who, "It said": str(d.get("body") or "")},
                        f"Throw away the reply to {who}", seat)


def propose_opt_out(id=None, seat=None):
    """Ask the owner to mark one person as opted out: nothing is ever sent to them again."""
    from marketing.customer_voice.inbox import store
    zcid = str(id or "").strip()
    convo = store.get_conversation(_space(), zcid) if zcid else None
    if convo is None:
        return {"asked": False, "note": "no conversation on this box with that id"}
    if convo.get("opted_out"):
        return {"asked": False, "note": "this person is already opted out"}
    who = str(convo.get("participant") or "this person")
    return _ask_control("opt_out", {"conversation": zcid},
                        {"Opt out": who, "Means": "nothing is ever sent to them from this box again"},
                        f"Opt out {who}", seat)


def connect():
    """How each channel is connected, and the page a person signs in on. A sign-in happens in a browser."""
    return {
        "connections": _connections(),
        "how": [
            {"what": "your email inbox", "open": "/inbox/mailbox",
             "steps": "Open it, choose your email provider, and enter the address and app password it asks for."},
            {"what": "Instagram and Messenger", "open": "/inbox/connect",
             "steps": "Open it and sign in to your social account; the box shows each account once it is connected."},
            {"what": "the AI account that writes replies", "open": "/settings/ai",
             "steps": "Open it and sign in to Claude, or paste a key."},
        ],
        "note": ("each page is on this box's own address, the same one this connection uses, without /mcp. "
                 "A sign-in has to be finished by the owner in a browser; a chat cannot finish it for them."),
    }


def _who_approved() -> str:
    from core import approvals
    return approvals.decider() or "approval"


def _run_control(detail: dict) -> dict:
    """An approved control, carried out by the same function the screen's button calls."""
    from marketing.customer_voice.drafter import store as drafts
    from marketing.customer_voice.inbox import store
    detail = detail or {}
    action, space = str(detail.get("action") or ""), _space()
    if action == "drafting":
        from core import box_settings
        on = bool(detail.get("on"))
        from core import approvals
        box_settings.put("inbox", "drafts.enabled", on, set_by=approvals.decider() or "approval")
        return {"ok": True, "text": f"Writing replies is {'on' if on else 'off'}."}
    if action == "discard":
        drafts.dismiss(space, str(detail.get("draft_id") or ""))
        log.info("inbox.draft_discarded_on_approval", conversation=detail.get("conversation"),
                 by=_who_approved())
        return {"ok": True, "text": "Thrown away. Nothing was sent."}
    if action == "opt_out":
        store.set_opted_out(space, str(detail.get("conversation") or ""))
        log.info("inbox.opted_out_on_approval", conversation=detail.get("conversation"), by=_who_approved())
        return {"ok": True, "text": "Opted out. Nothing will be sent to them again."}
    return {"ok": False, "text": "This box doesn't know that change, so nothing was done."}


def _register_control():
    from core import approvals
    approvals.register_kind(CONTROL, run=_run_control)


_register_control()

tools.register(
    "propose_drafting",
    title="Ask before turning writing replies on or off",
    fn=propose_drafting, machine=MACHINE, min_role="act",
    capability="write:proposals", wants_seat=True,
    description="Ask the owner to turn the box's reply writing on or off. Nothing changes until the owner approves.",
    args={"on": {"type": "boolean", "required": True,
                 "description": "true to turn writing replies on, false to turn it off."}},
)

tools.register(
    "propose_discard_draft",
    title="Ask before throwing away a written reply",
    fn=propose_discard_draft, machine=MACHINE, min_role="act",
    capability="write:proposals", wants_seat=True,
    description="Ask the owner to throw away the reply written for one conversation, unsent. Nothing changes "
                "until the owner approves.",
    args={"id": {"type": "string", "required": True,
                 "description": "The conversation id from waiting's drafts_ready."}},
)

tools.register(
    "propose_opt_out",
    title="Ask before opting someone out",
    fn=propose_opt_out, machine=MACHINE, min_role="act",
    capability="write:proposals", wants_seat=True,
    description="Ask the owner to mark one person as opted out, so nothing is ever sent to them again. Nothing "
                "changes until the owner approves.",
    args={"id": {"type": "string", "required": True,
                 "description": "The conversation id from list_conversations, search or waiting."}},
)

tools.register(
    "connect",
    title="See how to connect your channels",
    fn=connect, machine=MACHINE, min_role="read",
    capability="read:inbox",
    description="Whether the mailbox, social accounts and AI account are connected, and the page the owner "
                "opens to connect each one. A sign-in is finished by the owner in a browser.",
)
