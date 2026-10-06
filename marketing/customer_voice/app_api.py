"""The routes Zernio's inbox screens talk to, answered from this box (#1990 Phase 1, step 1.2).

Owner, 2026-10-06: "just plan to mirror their inbox right into our system. We can't go wrong if we follow their
development plan and their lead." Their screens (`zernio-dev/unified-inbox`, built into static files by step 1.1) call
`/api/...` on their own Next.js server, which forwards to Zernio. On a box the same calls go to `/inbox/api/...`, in
the same shapes, and are answered HERE:

  * FROM OUR STORE, NEVER A ZERNIO CALL PER POLL (OSDev1's condition 4). Their list polls every 10 seconds and an open
    thread every 5; a box forwarding each of those to Zernio would spend its rate limit on a screen being open. The
    poller already mirrors every conversation and message into our tables, so a read here touches only the box's disk.
  * THROUGH OUR SEND PATH. A send is `reply.send_reply`: the same ledger, approvals, STOP and opt-out refusal, hourly
    caps and indeterminate handling as the thread page's own Send. Email answers here too; their screens never had it.
  * NEVER RAW HTML (OSDev1's condition 5). A message carries its plain text only; `body_html` is never read here.
  * NO ZERNIO KEY in anything returned (OSDev1's condition 3).
  * BEHIND THE INBOX'S SIGN-IN: these routes sit on the app's blueprint, so its `_gate` admits them or nobody.

Shapes follow their client (`src/hooks/useConversations.ts`, `useConversationMessages.ts`, `composer.tsx`). What waits
for Phase 2 (sending reactions and voice notes, which their screens offer only on WhatsApp and Telegram) answers
`501 not_supported`, so their screen shows its own error rather than a broken page. Our own facts ride in `metadata.aios`.
"""
from __future__ import annotations

import json
import re
import uuid

from flask import jsonify, request

from core import dash
from core.logging import get_logger

from .app import _drafting_on, _space, blueprint
from .inbox import channels, store

log = get_logger(__name__)

PAGE = 50
MAX_PAGE = 100
# Their Platform names, for the channels Zernio carries (Messenger is "facebook" there). Email and comments are not
# Zernio platforms (their `vendor` is a transport), so they keep our key: their screens learn "email" from our layer.
_TO_THEIRS = {c.key: c.vendor for c in channels.POLLED if c.vendor not in channels.NOT_INBOX_LIST}
_FROM_THEIRS = {v: k for k, v in _TO_THEIRS.items()}


def _platform(key: str) -> str:
    return _TO_THEIRS.get(str(key or ""), str(key or ""))


def _cursor() -> int:
    try:
        return max(0, int(request.args.get("cursor") or 0))
    except ValueError:
        return 0


def _limit() -> int:
    try:
        return max(1, min(MAX_PAGE, int(request.args.get("limit") or PAGE)))
    except ValueError:
        return PAGE


def _not_yet(what: str):
    return jsonify({"error": f"{what} arrive with the next inbox update.", "code": "not_supported"}), 501


def _conversation(k: dict, ready: dict | None = None) -> dict:
    return {
        "id": k["zernio_conversation_id"],
        "accountId": str(k.get("account_id") or ""),
        "platform": _platform(k.get("platform")),
        "participantName": str(k.get("participant") or "").strip() or "Someone",
        "participantUsername": None,
        "participantPicture": None,
        "lastMessage": str(k.get("preview") or ""),
        "updatedTime": str(k.get("last_inbound_at") or k.get("updated_at") or ""),
        "status": str(k.get("list_status") or "active"),
        "unreadCount": 1 if k.get("unread") else 0,
        "url": None,
        "metadata": {"aios": {"waiting": bool(k.get("awaiting_reply")), "fromAd": bool(k.get("ad_meta_id")),
                              "adTitle": k.get("ad_title") or None, "heldBy": k.get("held_by") or None,
                              "optedOut": bool(k.get("opted_out")),
                              "disposition": k.get("disposition") or None,
                              # THE DRAFT CARD'S DATA (step 1.3): the reply waiting here, or None. Send and Edit are the
                              # send route below; Discard is `…/draft/discard` with this id.
                              "draft": (ready or {}).get(str(k.get("zernio_conversation_id")))}},
    }


def _subject(raw_headers) -> str:
    try:
        h = json.loads(raw_headers) if isinstance(raw_headers, str) else (raw_headers or {})
    except ValueError:
        return ""
    for name, value in (h.items() if isinstance(h, dict) else []):
        if str(name).lower() == "subject":
            return str(value or "")[:300]
    return ""


# WHERE A REPLY'S OWN WORDS END AND THE MAIL IT QUOTES BEGINS: Gmail and Apple's "On <date>, <name> wrote:" (wrapped
# onto two lines at times), Outlook's "-----Original Message-----", or a "From:" line followed by "Sent:" or "Date:".
_QUOTE_START = re.compile(
    r"^(?:On\b[^\n]{0,300}(?:\n[^\n]{0,160})?\bwrote:[ \t]*$"
    r"|-{2,}[ \t]*Original Message[ \t]*-{2,}"
    r"|From:[^\n]*\n(?:Sent|Date):)", re.M | re.I)


def split_quoted(text: str) -> tuple[str, str]:
    """An email's own words and the history it quotes, so the thread can fold the history (#1990 step 1.3).

    Never loses a word: `said` and `quoted` together are the whole text. Nothing is split off when the history would be
    everything (a forward with no note of its own reads as it arrived), and a trailing run of `>` lines is history too.
    """
    text = str(text or "")
    m = _QUOTE_START.search(text)
    cut = m.start() if m else -1
    if cut < 0:
        lines = text.split("\n")
        i = len(lines)
        while i > 0 and (lines[i - 1].startswith(">") or not lines[i - 1].strip()):
            i -= 1
        if any(ln.startswith(">") for ln in lines[i:]):
            cut = len("\n".join(lines[:i]))
    if cut <= 0 or not text[:cut].strip():
        return text, ""
    return text[:cut].rstrip(), text[cut:].strip()


def _message(m: dict, conv: dict) -> dict:
    sent_by = str(m.get("sent_by") or "")
    who = m.get("sender_name") if m.get("direction") == "out" else (conv.get("participant") or None)
    text = str(m.get("full_text") or m.get("body") or "")
    said, quoted = split_quoted(text) if conv.get("platform") == "email" else (text, "")
    return {
        "id": str(m.get("zernio_message_id") or m.get("id")),
        "conversationId": conv["zernio_conversation_id"],
        "accountId": str(conv.get("account_id") or ""),
        "platform": _platform(conv.get("platform")),
        # PLAIN TEXT ONLY: the full text where it was kept, else the stored preview. Never `body_html`.
        "message": text,
        "direction": "incoming" if m.get("direction") == "in" else "outgoing",
        "createdAt": str(m.get("sent_at") or m.get("created_at") or ""),
        "senderName": who,
        "attachments": [a for a in (m.get("x_attachments") or []) if isinstance(a, dict)],
        **({"deliveryStatus": m["x_delivery_status"]} if m.get("x_delivery_status") else {}),
        **({"deliveryError": {"message": m["x_delivery_error"]}} if m.get("x_delivery_error") else {}),
        "reactions": [r for r in (m.get("x_reactions") or []) if isinstance(r, dict)],
        "isEdited": bool(m.get("x_edited")),
        "isDeleted": bool(m.get("x_deleted")),
        "metadata": {"aios": {"sentBy": sent_by or None,
                              "subject": _subject(m.get("headers") or m.get("raw_headers")) or None,
                              # EMAIL ONLY, AND ONLY WHEN IT QUOTES: the reply's own words, and the history to fold.
                              **({"said": said, "quoted": quoted} if quoted else {})}},
    }


def _conv_or_404(zcid: str):
    conv = store.get_conversation(_space(), zcid)
    if not conv:
        return None, (jsonify({"error": "No such conversation.", "code": "not_found"}), 404)
    return conv, None


@blueprint.get("/inbox/api/conversations")
def api_conversations():
    """Their list: `{data, pagination: {hasMore, nextCursor}, meta: {failedAccounts}}`, newest first, from our store."""
    off, n = _cursor(), _limit()
    platform = request.args.get("platform") or ""
    key = _FROM_THEIRS.get(platform, platform) if platform and platform != "all" else None
    # Their `status` (active, archived) plus our Trash and Junk; a disposition narrows any of them.
    view = {"active": "inbox", "archived": "archived", "deleted": "deleted", "junk": "junk"}.get(
        request.args.get("status") or "active")
    disp = request.args.get("disposition") or None
    if view is None or (disp and disp not in store.DISPOSITIONS):
        return jsonify({"error": "status is active, archived, deleted or junk; disposition is one of "
                                 + ", ".join(store.DISPOSITIONS), "code": "invalid_field_value"}), 400
    rows = store.list_conversations(_space(), limit=n + 1, offset=off, platform=key, view=view, disposition=disp)
    ready = {str(d["zcid"]): {"id": str(d["id"]), "body": str(d.get("body") or "")} for d in _waiting()}
    if request.args.get("sortOrder") == "asc":
        rows = rows[::-1]
    acct = request.args.get("accountId") or ""
    page = [r for r in rows[:n] if not acct or str(r.get("account_id") or "") == acct]
    more = len(rows) > n
    return jsonify({"data": [_conversation(r, ready) for r in page],
                    "pagination": {"hasMore": more, "nextCursor": str(off + n) if more else None},
                    "meta": {"failedAccounts": []}})


@blueprint.route("/inbox/api/conversations/<path:zcid>", methods=["PUT"])
def api_mark(zcid: str):
    """The list's swipe actions (owner 2026-10-06: "slide a message to change the disposition archive or Delete"), in
    the shape of Zernio's own `PUT /v1/inbox/conversations/{id}` {status}: `status` active, archived (Done) or deleted
    (Trash), and our `disposition`. Done lasts until they write again; Trash and Junk stay out until a person moves
    them back. Nothing is removed and nothing is sent, and the platform and the mailbox keep their copy."""
    conv, missing = _conv_or_404(zcid)
    if missing:
        return missing
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict) or not ({"status", "disposition"} & set(body)):
        return jsonify({"error": "Send status or disposition.", "code": "invalid_field_value"}), 400
    u = dash.session_user(request) or {}
    try:
        now = store.mark_conversation(_space(), zcid, status=body.get("status"),
                                      disposition=body["disposition"] if "disposition" in body else store._KEEP,
                                      set_by=u.get("id"))
    except ValueError as e:
        return jsonify({"error": str(e), "code": "invalid_field_value"}), 400
    return jsonify({"success": True, "status": now.get("status"), "disposition": now.get("disposition")})


@blueprint.get("/inbox/api/conversations/<path:zcid>/messages")
def api_messages(zcid: str):
    """One thread, newest first as their client asks (it reverses it), from our store."""
    conv, missing = _conv_or_404(zcid)
    if missing:
        return missing
    off, n = _cursor(), _limit()
    newest_first = store.messages_for(_space(), zcid, limit=200)[::-1]
    page = newest_first[off:off + n]
    more = len(newest_first) > off + n
    return jsonify({"messages": [_message(m, conv) for m in page],
                    "pagination": {"hasMore": more, "nextCursor": str(off + n) if more else None}})


# `methods=["POST"]`, not `@blueprint.post`: test_customer_voice reads a call named `post` as a platform post.
@blueprint.route("/inbox/api/conversations/<path:zcid>/messages", methods=["POST"])
def api_send(zcid: str):
    """A person's reply, through `reply.send_reply` exactly as the thread page's Send. Their composer posts JSON, or a
    form with one `attachment` (step 1.6c): the file is read here, bounded, and judged by the send path."""
    from werkzeug.exceptions import RequestEntityTooLarge

    from .inbox import reply as _reply
    conv, missing = _conv_or_404(zcid)
    if missing:
        return missing
    # THIS ROUTE ALONE TAKES A FILE, so it alone raises the app's 256 KB body cap, before anything reads the body
    # (the same move as the box's icon upload, core/dash/box_settings.py), and only for a form: words stay under 256 KB.
    if request.mimetype == "multipart/form-data":
        request.max_content_length = _reply.MAX_ATTACHMENT + 256 * 1024
    try:
        body = request.get_json(silent=True) or request.form or {}
        upload = request.files.get("attachment")
        attachment = {"name": upload.filename or "", "content": upload.read(_reply.MAX_ATTACHMENT + 1)} if upload else None
    except RequestEntityTooLarge:
        return jsonify({"error": "Files must be under 25 MB; nothing was sent.", "code": "attachment_too_large"}), 413
    if str(body.get("voiceNote") or "").lower() == "true":
        return _not_yet("Voice notes")
    text = str(body.get("message") or "").strip()
    u = dash.session_user(request)
    if not u or not u.get("id"):
        return jsonify({"error": "Sign in again; nothing was sent.", "code": "no_session"}), 401
    try:
        out = _reply.send_reply(space=_space(), zcid=zcid, text=text, user_id=u["id"], attachment=attachment,
                                nonce=str(request.headers.get("Idempotency-Key") or uuid.uuid4()))
    except _reply.ReplyRefused as e:
        return jsonify({"error": str(e), "code": "refused"}), 422
    except _reply.ReplyIndeterminate:
        # INVARIANT 4: it may have landed, so it is never sent again and never called a failure.
        return jsonify({"error": "This may or may not have arrived. Check the conversation before sending it again; "
                                 "the box will not resend it.", "code": "indeterminate"}), 409
    return jsonify({"success": True, "messageId": str((out or {}).get("message_id") or "")})


@blueprint.route("/inbox/api/conversations/<path:zcid>/read", methods=["POST"])
def api_read(zcid: str):
    """Opened: marks it read in our store, and on the platform too for a Zernio channel (a person's tap, never a poll,
    so OSDev1's condition 4 holds). Email stays unread in their mailbox: the box opens it read-only, by design."""
    conv, missing = _conv_or_404(zcid)
    if missing:
        return missing
    store.mark_read(_space(), zcid)
    if conv.get("platform") in _TO_THEIRS and conv.get("account_id"):
        try:
            from core import spaces
            from core.vendors import zernio
            zernio.client(spaces.space_by_name(_space()) or {}).inbox.mark_read(
                channels.vendor_id(zcid), str(conv["account_id"]))
        except Exception as e:                   # noqa: BLE001 — a receipt never fails the tap
            log.warning("inbox.api_mark_read_failed", error=type(e).__name__)
    return jsonify({"success": True})


# ── the AI draft card (#1990 step 1.3): our addition above their composer ──────────────────────────────────────────
def _waiting() -> list[dict]:
    """Drafts still waiting on a person: `drafter.store.waiting`, the queue "Replies to send" shows, so a draft already
    answered, dismissed, opted out or claimed by an automation is never offered here either."""
    try:
        from .drafter import store as _drafts
        return _drafts.waiting(_space(), limit=1000)
    except Exception as e:                       # noqa: BLE001 — no draft is not a broken screen
        log.info("inbox.api_drafts_unreadable", error=type(e).__name__)
        return []


def _drafting() -> str:
    """"on", "off" (the owner's switch) or "not_connected" (no working AI account), for the card's empty state."""
    if not _drafting_on():
        return "off"
    try:
        from core import box_secrets
        return "not_connected" if box_secrets.anthropic_state()["status"] == "not_connected" else "on"
    except Exception:                            # noqa: BLE001 — unknown reads as on: the card stays quiet
        return "on"


@blueprint.get("/inbox/api/conversations/<path:zcid>/draft")
def api_draft(zcid: str):
    """The reply the box wrote for this conversation, if one is waiting. It becomes a message only through the send
    route above, with whatever words the person leaves in it: Send posts the body as written, Edit posts their edit.
    That send needs no draft id: `reply.send_reply` hands every sent reply to `drafter.store.learn`, and a reply sent
    after the question spends the draft (`waiting` is a join, not a flag), so the card clears itself."""
    conv, missing = _conv_or_404(zcid)
    if missing:
        return missing
    d = next((x for x in _waiting() if str(x["zcid"]) == str(zcid)), None)
    draft = None if d is None else {"id": str(d["id"]), "body": str(d.get("body") or ""),
                                    "createdAt": str(d.get("created_at") or ""),
                                    "inReplyTo": str(d.get("in_reply_to") or ""), "asked": str(d.get("asked") or "")}
    return jsonify({"draft": draft, "drafting": _drafting()})


@blueprint.route("/inbox/api/conversations/<path:zcid>/draft/discard", methods=["POST"])
def api_draft_discard(zcid: str):
    """Discard: marked dismissed, never deleted (it is the record that the box got one wrong). Only the draft named, and
    only while it still waits on this conversation, so a screen left open cannot dismiss a newer one."""
    conv, missing = _conv_or_404(zcid)
    if missing:
        return missing
    want = str((request.get_json(silent=True) or request.form or {}).get("draftId") or "")
    d = next((x for x in _waiting() if str(x["zcid"]) == str(zcid) and str(x["id"]) == want), None)
    if d is None:
        return jsonify({"error": "That draft is no longer waiting.", "code": "not_found"}), 404
    from .drafter import store as _drafts
    _drafts.dismiss(_space(), want)
    return jsonify({"success": True})


@blueprint.route("/inbox/api/conversations/<path:zcid>/typing", methods=["POST"])
def api_typing(zcid: str):
    return "", 204


@blueprint.route("/inbox/api/conversations/<path:zcid>/messages/<mid>/reactions", methods=["POST", "DELETE"])
def api_reactions(zcid: str, mid: str):
    return _not_yet("Reactions")


@blueprint.get("/inbox/api/media")
def api_media():
    return _not_yet("Attachments")


def _accounts() -> list[dict]:
    from core import state
    with state.connect() as c:
        rows = c.execute("SELECT DISTINCT platform, account_id FROM inbox_conversations "
                         "WHERE space = ? AND account_id IS NOT NULL AND account_id != '' ORDER BY platform",
                         (_space(),)).fetchall()
    return [{"_id": str(r["account_id"]), "platform": _platform(r["platform"]),
             "username": channels.label(r["platform"]),
             "isActive": True, "enabled": True} for r in rows]


def _selected(accounts: list[dict]) -> list[str]:
    from core import box_settings
    u = dash.session_user(request) or {}
    live = [a["_id"] for a in accounts]
    got = box_settings.get("inbox", "ui.selected_accounts", user_id=u.get("id"), default=None)
    return [a for a in got if a in live] if isinstance(got, list) else live


@blueprint.get("/inbox/api/accounts")
def api_accounts():
    accounts = _accounts()
    return jsonify({"accounts": accounts, "profiles": [], "selectedAccountIds": _selected(accounts)})


@blueprint.route("/inbox/api/settings", methods=["GET", "PUT"])
def api_settings():
    """Which accounts the person shows, kept per person (their app keeps it in a cookie)."""
    from core import box_settings
    accounts = _accounts()
    if request.method == "PUT":
        ids = (request.get_json(silent=True) or {}).get("selectedAccountIds")
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            return jsonify({"error": "selectedAccountIds must be an array of strings",
                            "code": "invalid_field_value"}), 400
        live = {a["_id"] for a in accounts}
        keep = [i for i in dict.fromkeys(ids) if i in live]
        u = dash.session_user(request) or {}
        box_settings.put("inbox", "ui.selected_accounts", keep, user_id=u.get("id"), set_by=u.get("id"))
        return jsonify({"selectedAccountIds": keep})
    return jsonify({"selectedAccountIds": _selected(accounts), "hasCookie": True})
