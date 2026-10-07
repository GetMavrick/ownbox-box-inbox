"""Sending a reply a PERSON typed. docs/SPEC_UNIFIED_INBOX_SCREEN_AND_AUTH.md §6.

WHY THIS IS A FUNCTION AND NOT A ROUTE BODY, which is two separate reasons and both bind.

First, the department may not send from anywhere else. `tests/test_customer_voice.py:498-500`
fails the build if any file under `marketing/customer_voice/` calls something named `post`,
`post_public`, `create`, `publish`, `update_review` or `reply_to`, and the ONLY carve-out is
`_SENDS_BY_DESIGN = {"inbox"}` — files under this directory. `app.py` is not in it, so the screen
can render the box and take the POST, but the send itself has to live here beside the opener.

Second, §8.4: the auto-responder lands later as a per-channel dial (`draft only | send with a hold
| send now`) and REUSES this exact path. A poller has no request context, so anything that reads
`request` cannot be the send. `send_reply` takes plain arguments for that reason.

WHAT THIS FILE DELIBERATELY DOES NOT DO. It does not draft. Nothing here reasons, and
`tests/test_customer_voice.py:501` bans `brain.think` in every file of this department including
this one — measured, not assumed: a real `brain.think(...)` call in the package exits 1. An LLM
that writes a reply is a separate architectural decision (§8.4) and it is not made here.
"""
from __future__ import annotations

from core import cost_guard, state
from core.logging import get_logger
from core.vendors import zernio

from . import channels, email_channel, store, window

log = get_logger(__name__)


class ReplyRefused(Exception):
    """The reply was not attempted, and nothing was spent. The caller shows the reason.

    `code` is the same fact for a program (a machine deciding to wait or to stop), so it never has to match
    the sentence, which may be reworded: taken_over, opted_out, box_stopped, window_closed, hourly_cap,
    not_claimed, too_old, or "refused" for anything else (a bad call, a vendor refusal)."""

    def __init__(self, message: str = "", *, code: str = "refused"):
        super().__init__(message)
        self.code = code


class ReplyIndeterminate(Exception):
    """The send may have landed. NEVER retried automatically — invariant 4."""


_NONCE_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


def new_nonce() -> str:
    """One per rendered compose box. The unit of "this particular attempt to send"."""
    import secrets
    return secrets.token_urlsafe(16)


def clean_nonce(value: str) -> str:
    """Bound what goes into a ledger key. Empty means the submit did not come from our form."""
    v = "".join(ch for ch in str(value or "") if ch in _NONCE_OK)[:64]
    return v


def idem_for(space: str, zcid: str, user_id: str, nonce: str) -> str:
    """The exactly-once key: this person, this conversation, THIS ATTEMPT.

    KEYED ON A PER-RENDER NONCE, NOT ON THE WORDS, and that correction is OSDev1's. Hashing the
    text looked idempotent and was actually a silencer, in two ordinary cases:

    · THE SAME WORDS TWICE. "Thanks!" on Monday and "Thanks!" on Friday in the same thread
      computed the same key, so Friday's reply found Monday's ledger row, reported itself a
      duplicate, and redirected as though it had sent. Nothing went out and nobody was told.
      Short repeats — "Thanks!", "On my way", "Yes" — are the most common lines in a support
      thread, so this was not an edge case.
    · A RETRY AFTER A REAL FAILURE. A determinate failure writes a `failed` row under the key;
      retrying the same words found it and redirected as if it had worked, so the person had to
      REWORD their message to retry and had no way to know that.

    A nonce fixes both while keeping the property that mattered: a browser retrying a POST (a
    double tap, a flaky connection, the back button) resends the identical form, nonce included,
    so it is still refused. A new reply is a new render, so it sends.

    No text in the key at all now, which is also better: a ledger key is read in logs and in
    support, and a customer's words do not belong in one.
    """
    return f"reply:{space}:{zcid}:{user_id}:{nonce}"


# ONE PHOTO OR VIDEO WITH A REPLY (#1990 step 1.6c), held to what Zernio's own composer sends: JPEG, PNG, GIF or MP4,
# under 25 MB. The type is read from the file's first bytes, never from the name or the browser's word for it.
MAX_ATTACHMENT = 25 * 1024 * 1024
_SNIFF = (("image/jpeg", 0, b"\xff\xd8\xff"), ("image/png", 0, b"\x89PNG\r\n\x1a\n"),
          ("image/gif", 0, b"GIF8"), ("video/mp4", 4, b"ftyp"))
# AN MP4 IS AN `ftyp` BOX WITH AN MP4 BRAND. The same box opens an iPhone photo (HEIC, brand heic/mif1) and a QuickTime
# movie (brand "qt  "), and those went out labelled MP4. The major brand (bytes 8-12) says which; HEIF files can list
# an iso brand among their compatible ones, so the major brand alone decides.
_MP4_BRANDS = {b"isom", b"iso2", b"iso3", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42", b"avc1", b"M4V ", b"mmp4",
               b"dash"}
_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "video/mp4": "mp4"}


def checked_attachment(name: str, content: bytes) -> dict:
    """The file as the gateway sends it ({"name", "content", "mime"}), or ReplyRefused saying what is wrong with it."""
    content = bytes(content or b"")
    if not content:
        raise ReplyRefused("the file is empty", code="attachment_empty")
    if len(content) > MAX_ATTACHMENT:
        raise ReplyRefused("files must be under 25 MB", code="attachment_too_large")
    mime = next((m for m, at, sig in _SNIFF if content[at:at + len(sig)] == sig), None)
    if mime == "video/mp4" and content[8:12] not in _MP4_BRANDS:
        mime = None
    if not mime:
        raise ReplyRefused("a photo (JPEG, PNG or GIF) or an MP4 video can be sent", code="attachment_type")
    stem = "".join(ch for ch in str(name or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1].rsplit(".", 1)[0]
                   if ch.isalnum() or ch in " -_")[:80].strip() or ("video" if mime == "video/mp4" else "photo")
    return {"name": f"{stem}.{_EXT[mime]}", "content": content, "mime": mime}


# ONCE A MESSAGE MAY HAVE GONE, NOTHING HERE RAISES. Every caller turns an exception into "that did not send, nothing
# was delivered" (the reply box, the new screens' send route), so a ledger write or a mirror that fails AFTER the vendor
# took the message would tell a person a delivered reply never went, and they would send it again. These two log
# instead: a ledger row left `sending` is the watchdog's to find, and the next poll mirrors the message anyway.
def _settle(*, space: str, idem_key: str, status: str, **kw) -> None:
    """Resolve a ledger row after the vendor call (sent, or may have landed). Never raises."""
    try:
        store.resolve_send(space=space, idem_key=idem_key, status=status, **kw)
    except Exception as e:                # noqa: BLE001 — the message's fate is already decided
        log.error("inbox.ledger_unresolved", extra={"space": space, "status": status, "error": type(e).__name__})


def _mirror(*, space: str, zcid: str, mid, sent_by: str, body: str) -> None:
    """Put a message that went into the thread. Never raises: the poll mirrors it anyway."""
    try:
        store.record_message(space=space, zcid=zcid, zmid=mid, direction="out", sent_by=sent_by, body=body)
    except Exception as e:                # noqa: BLE001 — bookkeeping never undoes a sent reply
        log.warning("inbox.sent_mirror_failed", extra={"space": space, "conversation": zcid,
                                                       "error": type(e).__name__})


def _deliver(*, space: str, zcid: str, conv: dict, account_id: str, text: str, idem: str,
             buttons: list | None = None, quick_replies: list | None = None,
             attachment: dict | None = None) -> str:
    """Send `text` on a ledger row the caller has ALREADY CLAIMED, and resolve that row. -> the message id.

    Shared by a person's reply (`send_reply`) and a machine's (`send_for_machine`), so both get the same
    transport choice, metering and outcome handling, which were paid for once and must not drift apart.
    Raises ReplyRefused (nothing went) or ReplyIndeterminate (it may have gone), with the row resolved."""
    # FROM HERE WE OWN THE ROW, and every exit below must resolve it. A claim we abandon reads
    # as "may have landed" forever (store.claim_send), which would block the person's honest
    # retry over something that never reached the vendor — so the two pre-send refusals resolve
    # to `failed` first, exactly as `release_opener` releases an opener claim whose send
    # DETERMINATELY failed.
    # WHICH TRANSPORT. Email leaves this box through the buyer's own mailbox and everything else
    # through Zernio, and the difference starts here rather than at the call: email has no vendor
    # to meter (no spend, so nothing to check or record) and no Space to resolve (no Zernio
    # account is involved at all). Metering a vendor that is not in the path would put a charge in
    # the ledger for a message Google sent for free.
    is_email = str(conv.get("platform") or "").strip().lower() == "email"
    sp, cred, tail = None, {}, {}
    try:
        if is_email:
            from core import box_secrets
            cred = box_secrets.email_credential()
            if not cred:
                raise ReplyRefused("this box is not connected to a mailbox")
            tail = email_channel.thread_tail(space, zcid)
            if "@" not in str(tail.get("to") or ""):
                raise ReplyRefused("there is no address on this thread to reply to")
        else:
            cost_guard.check_vendor("zernio", 1)
            sp = _space(space)
            if not sp:
                # FAIL CLOSED ON AN UNRESOLVED SPACE. Never fall through to a default — see _space.
                raise ReplyRefused("this box cannot resolve the Space that owns this conversation")
    except Exception as e:                # noqa: BLE001 — nothing was sent on either path
        store.resolve_send(space=space, idem_key=idem, status="failed", error=str(e))
        raise

    try:
        if is_email:
            # THE SUBJECT IS FETCHED HERE, INSIDE THE CLAIM, and a failure to read it costs a
            # subject line rather than the reply — `subject_for` never raises.
            mid = email_channel.send(
                cred, to=tail["to"],
                subject=email_channel.subject_for(cred, tail.get("in_reply_to") or ""),
                body=text, in_reply_to=tail.get("in_reply_to") or "",
                references=tail.get("references") or "")
            sent = {"message_id": mid}
        else:
            extra = {}
            if buttons:
                extra["buttons"] = buttons
            if quick_replies:
                extra["quick_replies"] = quick_replies
            if attachment:
                extra["attachment"] = attachment
            sent = zernio.client(sp).inbox.send(channels.vendor_id(zcid), account_id, text, **extra)
    except email_channel.EmailSendIndeterminate as e:
        # INVARIANT 4, ON THE MAIL PATH. A timeout or a disconnect after DATA tells us nothing
        # about whether the message was queued, so it is recorded as "may have landed" and
        # nothing here ever resends it.
        _settle(space=space, idem_key=idem, status="indeterminate", error=str(e))
        log.error("inbox.reply_indeterminate", extra={"space": space, "conversation": zcid,
                                                      "error": str(e)[:160]})
        raise ReplyIndeterminate(str(e)) from e
    except email_channel.EmailSendRefused as e:
        # THE SERVER ANSWERED NO. Determinate, so the row says failed and the person may retry —
        # which is exactly what the generic `except Exception` below must NOT do for it, because
        # that arm means "we have no idea" and would leave an honest retry blocked forever.
        store.resolve_send(space=space, idem_key=idem, status="failed", error=str(e))
        raise ReplyRefused(str(e)) from e
    except email_channel.EmailAuthError as e:
        # DETERMINATE: Google refused the credential, so nothing was sent. Recorded on the
        # mailbox row as well, in the same words the set-up screen already renders, because the
        # buyer cannot fix this from the thread — the poller would otherwise be the only thing
        # that ever noticed, on its own schedule.
        store.resolve_send(space=space, idem_key=idem, status="failed", error=str(e))
        try:
            from core import box_secrets
            box_secrets.note_email_status(e.status, e.detail)
        except Exception:                 # noqa: BLE001 — recording a reason never changes the
            pass                          # outcome of a send that provably did not happen
        raise ReplyRefused(e.detail) from e
    except zernio.ZernioError as e:
        if e.indeterminate:              # always set by ZernioError.__init__, as handler.py:169 reads it
            # MAY HAVE LANDED. Record it, tell the caller, and never resend on our own.
            _settle(space=space, idem_key=idem, status="indeterminate", error=str(e))
            log.error("inbox.reply_indeterminate", extra={"space": space, "conversation": zcid,
                                                          "error": str(e)[:160]})
            raise ReplyIndeterminate(str(e)) from e
        store.resolve_send(space=space, idem_key=idem, status="failed", error=str(e))
        raise ReplyRefused(_refusal_sentence(conv, e)) from e
    except Exception as e:                # noqa: BLE001 — an unexpected raise mid-call is UNKNOWN
        # A non-ZernioError escaping the gateway (a bug, a socket the SDK did not wrap) tells us
        # nothing about whether the vendor took the message. The row must not be left `sending`
        # for the watchdog to find hours later when we can say the honest thing right now.
        _settle(space=space, idem_key=idem, status="indeterminate", error=str(e))
        log.error("inbox.reply_unexpected", extra={"space": space, "conversation": zcid,
                                                   "error": str(e)[:160]})
        raise ReplyIndeterminate(str(e)) from e

    mid = sent.get("message_id")
    _settle(space=space, idem_key=idem, status="ok", zernio_message_id=mid)
    return mid


def send_reply(*, space: str, zcid: str, text: str, user_id: str, nonce: str,
               account_id: str | None = None, attachment: dict | None = None) -> dict:
    """Send one human-typed reply. Returns {"status", "message_id"|None, "idem_key"}.

    The order below is §6's, and each step is a rule this repo has already paid for:

    1. REFUSE AN OPTED-OUT CONVERSATION. The list screen flags it because, in its own words, it
       is "the one mistake this screen can help him make". Checked here too — the screen is a
       warning, this is the gate, and only one of them is in the send path.
    2. CLAIM THE LEDGER KEY, atomically, BEFORE anything can spend or send. A double-clicked
       form is two concurrent requests; the claim is what makes one of them the send and the
       other a report. Losing it is never a retry.
    3. METER BEFORE THE CALL (invariant 4), never after.
    4. SEND through the Space-scoped gateway, which enforces the isolation backstop internally.
    5. RESOLVE THE CLAIMED ROW — every exit, including the refusals before the call.
    6. A TIMEOUT IS INDETERMINATE, NEVER FAILED. "Never assume it didn't land." So is an
       abandoned claim, and so is any unexpected raise out of the gateway.
    7. MIRROR into `inbox_messages` as `sent_by='human'` so the thread reads correctly without
       waiting for the next poll.

    `attachment` is one photo or video from `checked_attachment`, on a channel in `channels.ATTACHMENTS`; with one,
    the words are optional.
    """
    text = str(text or "").strip()
    if attachment is not None:
        attachment = checked_attachment(attachment.get("name") or "", attachment.get("content") or b"")
    if not text and not attachment:
        raise ReplyRefused("a reply needs words")
    # WHICH PERSON, REQUIRED. No caller may send as "the owner" by omission: since migration 47
    # every session belongs to a real row, so a missing user means the caller could not say who
    # is sending — and that is a refusal, not a default.
    user_id = str(user_id or "").strip()
    if not user_id:
        raise ReplyRefused("a reply must be sent by a signed-in person")
    nonce = clean_nonce(nonce)
    if not nonce:
        raise ReplyRefused("this form is stale — reopen the conversation and try again")

    conv = store.get_conversation(space, zcid)
    if not conv:
        # The id came from a URL. A conversation this Space does not own is NOT FOUND, never
        # FORBIDDEN: a boundary that announces what exists on the other side is not a boundary.
        raise ReplyRefused("no such conversation on this box")
    if conv.get("opted_out"):
        raise ReplyRefused("this person has opted out — nothing is sent to them", code="opted_out")

    # A CHANNEL THE BOX CANNOT SEND ON AT ALL, REFUSED IN WORDS — and refused HERE, in the send
    # path, not only on the screen.
    #
    # OSDev1 FOUND THIS AND IT WAS LIVE (2026-09-22): `no_send_lane` was read by exactly one
    # caller, `app._no_send_lane`, which hides the compose box on a thread. The Drafts tab (#1419)
    # does not go through that screen — it calls this function directly, for every platform at
    # once — so ticking an email draft fell straight through to the Zernio branch below and the
    # buyer got an exception class name where a sentence belongs. The screen was a warning and
    # nothing was a gate.
    #
    # BEFORE THE CLAIM, unlike the two refusals further down. Those resolve a claimed row to
    # `failed` because they are discovered after claiming; this one is knowable from the
    # conversation alone, so claiming a ledger key for a send that can never happen would write a
    # row about an attempt that was never possible.
    #
    # THE REASON IS THE CHANNEL'S OWN WORDS, carried as data on the rule (`no_send_lane_why`) so
    # the sentence a buyer reads is written once, beside the policy, rather than here.
    #
    # ASKED WITH `no_send_lane_why`, NEVER `decide`, AND THAT IS NOT A STYLE CHOICE.
    # tests/test_send_window_says_it_first.py refuses `decide` and `explain` anywhere before the
    # vendor call in this file, guarding a NEGATIVE that belongs to the owner rather than to any
    # developer (#1170 Option C): nothing here may ever stop a person sending. Our window state is
    # an INFERENCE from `last_inbound_at` — filled late, skipped per channel, stale after a
    # watermark reset — and a false block tells a business owner he may not answer his own
    # customer, on our arithmetic, with no override.
    #
    # A MISSING LANE IS A DIFFERENT KIND OF FACT, not a stricter version of the same one. It is
    # not about time at all: it is that the box cannot reach the channel, so there is nothing for
    # a person to be blocked FROM. Email WAS such a channel and is not any more — the owner ruled
    # on 2026-09-22 and this file now sends it — but the question stays, because the alternative
    # is the fall-through a buyer actually hit: a draft on a channel with no transport reaching
    # the Zernio branch and getting an exception class name where a sentence belongs.
    #
    # The first cut of this called `decide`, and CI was right to refuse it. `no_send_lane_why`
    # takes no timestamp, so it cannot answer "the window shut" — and the suite now asserts that
    # argument list, so a later edit cannot quietly give it one.
    why = window.no_send_lane_why(str(conv.get("platform") or ""))
    if why:
        raise ReplyRefused(why)
    if attachment and conv.get("platform") not in channels.ATTACHMENTS:
        raise ReplyRefused(f"{channels.label(conv.get('platform'))} replies here are words only",
                           code="attachment_channel")

    # THE BUYER'S SIGNATURE ENDS EVERY EMAIL REPLY (signature.py; owner, 2026-10-02). Added HERE, the one door every
    # send comes through, so the mail that leaves and the thread's own copy of it say the same thing. Never twice.
    if str(conv.get("platform") or "").strip().lower() == "email":
        from . import signature
        text = signature.apply(space, text)

    account_id = account_id or (conv.get("account_id") or "")
    if not account_id:
        raise ReplyRefused("this conversation has no account to send from")

    idem = idem_for(space, zcid, user_id, nonce)
    # CLAIM THE KEY BEFORE THE VENDOR CALL. This is the whole exactly-once guarantee and it is
    # OSDev1's required change on #1145. The first version of this function READ the ledger here,
    # sent, and wrote the row afterwards — and a read followed by a write is not a claim. The
    # compose form has no submit guard, so a double-click issues two POSTs carrying the same
    # nonce; under gthread workers both reached this read before either wrote, both saw nothing,
    # and THE CUSTOMER RECEIVED THE MESSAGE TWICE. One atomic statement closes that window:
    # `claim_send` inserts (or takes over a provably-`failed` row) and returns whether we own it.
    #
    # Losing the claim is not an error state, it is an ANSWER, and which answer depends on what
    # the row says. Only an outcome that MIGHT HAVE REACHED THE CUSTOMER absorbs a resubmit:
    #
    #   ok            → it went. Report the same message id; never send twice.
    #   sending       → another request holds the key right now, or one died holding it. Either
    #                   way a call may be on the wire. Never race it — invariant 4 covers the
    #                   unknown, not just the timeout.
    #   indeterminate → it may have gone. Say so AGAIN rather than redirecting quietly, or the
    #                   second attempt looks like a success.
    #   failed        → it provably did not go, so `claim_send` would have TAKEN the row and we
    #                   would not be here. Reaching this arm means another request resolved it
    #                   to failed in between: nothing left the box, so say so and let them retry.
    if not store.claim_send(space=space, zcid=zcid, idem_key=idem, kind="reply",
                            user_id=user_id):
        prior = store.get_send(space, idem) or {}
        status = prior.get("status")
        if status == "ok":
            log.info("inbox.reply_already_sent", extra={"space": space, "idem": idem})
            return {"status": "ok", "message_id": prior.get("zernio_message_id"),
                    "idem_key": idem, "duplicate": True}
        if status == "failed":
            log.info("inbox.reply_prior_failed", extra={"space": space, "idem": idem})
            raise ReplyRefused(str(prior.get("error") or "the message did not send"))
        # `sending`, `indeterminate`, and the row-is-missing case that should be impossible.
        # All three mean THE SAME THING TO A PERSON — do not send this again — and the
        # impossible one lands here deliberately: an unreadable ledger fails toward silence,
        # never toward a second message.
        log.info("inbox.reply_may_have_landed",
                 extra={"space": space, "idem": idem, "prior": status or "missing"})
        raise ReplyIndeterminate(str(prior.get("error") or "the earlier attempt may have landed"))

    # A PERSON IS ANSWERING, SO ANY AUTOMATION HOLDING THIS CONVERSATION STOPS (OSDev1's reviews of #1753 and
    # #1790), and it stops BEFORE the vendor call: a machine send that checked a moment ago re-checks after its
    # own ledger claim (send_for_machine) and finds the claim gone. Ended even if this send then fails: the
    # person meant to take it over. Never raises.
    from marketing.customer_voice import claims as _claims
    _claims.take_over(space, zcid)
    mid = _deliver(space=space, zcid=zcid, conv=conv, account_id=account_id, text=text, idem=idem,
                   attachment=attachment)
    # `sent_by='human'` is the honesty of the screen: the thread says who said every line, and
    # "the machine" and "you" must never be swapped. §3.3 records WHICH human on the ledger.
    _mirror(space=space, zcid=zcid, mid=mid, sent_by="human", body=text)
    if attachment:
        # THE THREAD SHOWS THE FILE AT ONCE, by name and kind; its link arrives with the next poll, which mirrors
        # Zernio's copy of this message over this one. Never raises: the file has already gone.
        try:
            store.record_extras(mid, {"attachments": [{"type": attachment["mime"].split("/")[0], "url": "",
                                                       "name": attachment["name"], "mimeType": attachment["mime"]}]})
        except Exception as e:            # noqa: BLE001 — bookkeeping never undoes a sent reply
            log.warning("inbox.attachment_mirror_failed", extra={"conversation": zcid, "error": type(e).__name__})
    # WHAT THEY SENT, NEXT TO WHAT THE BOX DRAFTED — the one moment both exist. Guarded HERE as
    # well as inside `learn`: the reply has already left, and its success is not hostage to
    # bookkeeping — not to a raising learn, not to a failed import, not to a future edit that
    # drops the inner guard. The suite replaces `learn` with a raise to prove this line.
    try:
        from marketing.customer_voice.drafter import store as _drafts
        if text:
            _drafts.learn(space, zcid, text)
    except Exception as e:                # noqa: BLE001 — bookkeeping never undoes a sent reply
        log.warning("inbox.learn_failed", extra={"space": space, "conversation": zcid,
                                                 "error": type(e).__name__})
    log.info("inbox.reply_sent", extra={"space": space, "conversation": zcid,
                                        "message_id": mid, "user": user_id or "owner"})
    return {"status": "ok", "message_id": mid, "idem_key": idem, "duplicate": False}


# ── saying WHY the vendor refused, in words ──────────────────────────────────────────────

# Meta's own marker for a send outside the messaging window. Carried as the subcode rather than
# a phrase because the subcode is stable and the English around it is not — and because Zernio
# proxies the error, so the wording that reaches us is theirs, not Meta's.
#   developers.facebook.com/docs/messenger-platform/error-codes  (code 10 / subcode 2018278)
_WINDOW_MARKERS = ("2018278", "outside of allowed window", "outside the allowed window",
                   "messaging window", "24-hour window", "24 hour window")


# ── a machine's buttons and quick replies, in the platform's own shape ──────────────────────────────────────────
# A machine passes plain values (core names no vendor): a button is {"title", "url"}, a quick reply is its title.
# Shapes confirmed against Zernio's model (leadmagnet/buttons.py, which this mirrors rather than imports: one
# machine never imports another). A QUICK REPLY's tap comes back as an inbound message with its title, which is
# what lets a poll-only box move a conversation on without a webhook; a URL button just opens its link.
_TITLE_MAX, _MAX_BUTTONS, _MAX_QUICK = 20, 3, 13


def _a_list(value, what: str, example: str) -> list:
    """A machine's list, or a refusal naming the fix. NEVER ITERATE A STRING OR A DICT (OSDev1's review of #1789):
    quick_replies="Yes" would otherwise send three quick replies, "Y", "e" and "s", to a real person."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    raise ReplyRefused(f"{what} is a list, like {example}")


def _https_link(url: str) -> bool:
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme == "https" and "." in (parts.hostname or "") and " " not in url


def _wire_buttons(buttons) -> list:
    out = []
    for b in _a_list(buttons, "buttons", '[{"title": "Get the guide", "url": "https://…"}]')[:_MAX_BUTTONS + 1]:
        if not isinstance(b, dict):
            raise ReplyRefused('each button is {"title": …, "url": "https://…"}')
        title, url = str(b.get("title") or "").strip(), str(b.get("url") or "").strip()
        if not title or not _https_link(url):
            raise ReplyRefused("a button needs a title and a full https:// link")
        out.append({"type": "url", "title": title[:_TITLE_MAX].rstrip(), "url": url})
    if len(out) > _MAX_BUTTONS:
        raise ReplyRefused(f"a message carries at most {_MAX_BUTTONS} buttons")
    return out


def _wire_quick_replies(replies) -> list:
    out = []
    for r in _a_list(replies, "quick_replies", '["Yes"]')[:_MAX_QUICK + 1]:
        if not isinstance(r, str):
            raise ReplyRefused('each quick reply is its title, like "Yes"')
        t = r.strip()[:_TITLE_MAX].rstrip()
        if not t:
            raise ReplyRefused("a quick reply needs a title")
        out.append({"content_type": "text", "title": t, "payload": t})
    if len(out) > _MAX_QUICK:
        raise ReplyRefused(f"a message carries at most {_MAX_QUICK} quick replies")
    return out


def send_for_machine(*, space: str, zcid: str, text: str, machine: str, key: str,
                     buttons: list | None = None, quick_replies: list | None = None) -> dict:
    """Send one message a MACHINE wrote, on a conversation it has claimed. -> {"status", "message_id", ...}.

    docs/PLAN_LEAD_MAGNET_MACHINE.md step 2: an automation's messages go through the Inbox's own send path,
    not straight to the vendor. A person's reply (`send_reply` above) is checked by the person reading the
    thread; nobody reads this one first, so it passes every gate the Inbox's own automatic send (the opener,
    inbox/handler.py) passes, plus one of its own:

      1. THE MACHINE HOLDS THE CONVERSATION (customer_voice/claims.py), AND NO PERSON HAS WRITTEN SINCE IT
         TOOK IT. Without a claim it is a stranger writing into someone's inbox; after a person's reply it
         would be talking over them.
      2. NOT OPTED OUT. Someone who said STOP gets nothing, whatever a machine wants.
      3. THE BOX ISN'T STOPPED. Stop everything halts automations, and this is one.
      4. THE CHANNEL'S WINDOW IS OPEN (`window.allowed_send(...) == "freeform"`): on Instagram, within 24
         hours of the person's own message. The funnel this replaces never checked it.
      5. THE HOURLY CAP the opener keeps (`inbox.hourly_send_cap`), counted over every send on the box.
      6. EMAIL IS NOT SENT BY MACHINES on this path yet: a person's mailbox is theirs to write from.
      7. EXACTLY ONCE, keyed on (machine, conversation, `key`): the machine names the step ("ask_email"), so a
         retry of the same step can never send it twice, and the ledger answers what happened last time.

    The message is mirrored as `sent_by='ai'`, which the thread labels "the machine": who said every line
    is never in doubt. A refusal raises ReplyRefused (nothing went); a may-have-landed raises
    ReplyIndeterminate, and nothing here ever resends it."""
    text = str(text or "").strip()
    if not text:
        raise ReplyRefused("a message needs words")
    key = clean_nonce(key)
    if not key:
        raise ReplyRefused("a machine's message needs a key: letters, digits, dot, dash or underscore")
    wire_buttons, wire_quick = _wire_buttons(buttons), _wire_quick_replies(quick_replies)
    conv = store.get_conversation(space, zcid)
    if not conv:
        raise ReplyRefused("no such conversation on this box")
    from marketing.customer_voice import claims
    held = claims.holder(space, zcid)
    if not held or held.get("machine") != machine:
        raise ReplyRefused("this machine hasn't claimed this conversation", code="not_claimed")
    # A PERSON WROTE SINCE THE CLAIM BEGAN, from the box or from the platform's own app: the conversation is
    # theirs now. The claim ends with a note, and nothing more is sent (OSDev1's review of #1753).
    if claims.person_wrote_since(space, zcid, held.get("claimed_at") or ""):
        claims.take_over(space, zcid)
        raise ReplyRefused("a person has replied on this conversation, so the machine stopped", code="taken_over")
    if conv.get("opted_out"):
        raise ReplyRefused("this person has opted out — nothing is sent to them", code="opted_out")
    try:
        from core import pause
        stopped = pause.is_paused()
    except Exception:                    # noqa: BLE001 — an unreadable switch fails toward silence
        stopped = True
    if stopped:
        raise ReplyRefused("the box is stopped, so no automation sends", code="box_stopped")
    platform = str(conv.get("platform") or "").strip().lower()
    if platform == "email":
        raise ReplyRefused("machines don't send email from this inbox")
    if window.allowed_send(conv.get("last_inbound_at"), platform=platform) != "freeform":
        raise ReplyRefused("this channel's window to reply is closed", code="window_closed")
    from . import sending
    cap = sending.hourly_cap()
    if store.sends_last_hour(space) >= cap:
        raise ReplyRefused(f"the box has sent its {cap} messages for this hour; try again later", code="hourly_cap")
    account_id = conv.get("account_id") or ""
    if not account_id:
        raise ReplyRefused("this conversation has no account to send from")

    idem = f"machine:{machine}:{space}:{zcid}:{key}"
    if not store.claim_send(space=space, zcid=zcid, idem_key=idem, kind="machine", user_id=machine):
        prior = store.get_send(space, idem) or {}
        if prior.get("status") == "ok":
            return {"status": "ok", "message_id": prior.get("zernio_message_id"), "idem_key": idem,
                    "duplicate": True}
        if prior.get("status") == "failed":
            raise ReplyRefused(str(prior.get("error") or "the message did not send"))
        raise ReplyIndeterminate(str(prior.get("error") or "the earlier attempt may have landed"))
    # RE-CHECKED AFTER THE LEDGER CLAIM (OSDev1's review of #1790): a person's reply that landed between the
    # first check and here has already ended the claim (send_reply takes over before its own vendor call), so
    # the machine sends nothing, and the ledger says it provably didn't go.
    now_held = claims.holder(space, zcid)
    if (not now_held or now_held.get("machine") != machine
            or claims.person_wrote_since(space, zcid, now_held.get("claimed_at") or "")):
        claims.take_over(space, zcid)
        store.resolve_send(space=space, idem_key=idem, status="failed",
                           error="a person replied first, so the machine stopped")
        raise ReplyRefused("a person has replied on this conversation, so the machine stopped", code="taken_over")
    mid = _deliver(space=space, zcid=zcid, conv=conv, account_id=account_id, text=text, idem=idem,
                   buttons=wire_buttons, quick_replies=wire_quick)
    _mirror(space=space, zcid=zcid, mid=mid, sent_by="ai", body=text)
    log.info("inbox.machine_sent", extra={"space": space, "conversation": zcid, "machine": machine,
                                          "message_id": mid})
    return {"status": "ok", "message_id": mid, "idem_key": idem, "duplicate": False}


# ── a reply the box sends on its own, on a DM channel the owner switched on (inbox/autosend.py) ─────────────────────
PERSON_QUIET_S = 30 * 60        # a person who wrote on the thread this recently is talking: the box doesn't cut in
# NEVER A LOOP (OSDev1's review of #2037): another business's auto-responder answers every reply at once, and two
# machines answering each other would spend the box's whole hourly cap on one thread. Three of the box's own replies
# on one conversation in a rolling hour, then that conversation waits for a person.
AUTO_PER_CONVERSATION_HOUR = 3


def _auto_sends_last_hour(space: str, zcid: str) -> int:
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with state.connect() as c:
        return int(c.execute("SELECT COUNT(*) FROM inbox_send_ledger WHERE space = ? AND zernio_conversation_id = ? "
                             "AND kind = 'auto' AND status <> 'failed' AND created_at > ?",
                             (space, str(zcid), since)).fetchone()[0])


def send_automatic(*, space: str, zcid: str, text: str, in_reply_to: str, asked_at: str) -> dict:
    """Send the reply the box wrote to `in_reply_to`, with nobody pressing send. -> {"status", "message_id", ...}.

    Owner, 2026-10-07: "I would like automatic instant responses to any Instagram or messenger DM's that come in. I
    don't wanna have to approve those". Nobody reads this one first, so it passes every gate a machine's message
    passes (`send_for_machine` above), with the owner's switch in place of a claim:

      1. THE OWNER TURNED IT ON FOR THIS CHANNEL, and the message came after he did (sending.auto_reply_since).
         Never email.
      2. NO AUTOMATION HOLDS THE CONVERSATION (the lead magnet's funnel speaks for itself there), and NO PERSON
         HAS WRITTEN ON IT IN THE LAST PERSON_QUIET_S: someone chatting live is never talked over. And at most
         AUTO_PER_CONVERSATION_HOUR of these on one conversation in a rolling hour, so two machines never loop.
      3. NOT OPTED OUT, THE BOX ISN'T STOPPED, the channel's window is open, the hourly cap has room.
      4. STILL THE MESSAGE TO ANSWER: nothing went out on the thread since it came in, checked again after the
         ledger claim, so a person's reply or the first message landing in between wins.
      5. EXACTLY ONCE per inbound message (`auto:{space}:{in_reply_to}`); a may-have-landed is never resent.

    Mirrored as sent_by='ai', which the thread labels "the machine"."""
    text = str(text or "").strip()
    if not text:
        raise ReplyRefused("a message needs words")
    conv = store.get_conversation(space, zcid)
    if not conv:
        raise ReplyRefused("no such conversation on this box")
    platform = str(conv.get("platform") or "").strip().lower()
    from . import sending
    since = sending.auto_reply_since(platform)
    if platform == "email" or not since:
        raise ReplyRefused("replying on its own is off for this channel", code="off")
    if str(asked_at or "") < since:
        raise ReplyRefused("this message came in before replying on its own was turned on", code="too_old")
    from marketing.customer_voice import claims
    if claims.holder(space, zcid):
        raise ReplyRefused("an automation is handling this conversation", code="claimed")
    from datetime import datetime, timedelta, timezone
    quiet = (datetime.now(timezone.utc) - timedelta(seconds=PERSON_QUIET_S)).isoformat()
    if claims.person_wrote_since(space, zcid, quiet):
        raise ReplyRefused("a person is talking on this conversation, so the box waits", code="person_active")
    if _auto_sends_last_hour(space, zcid) >= AUTO_PER_CONVERSATION_HOUR:
        raise ReplyRefused(f"the box has answered this conversation {AUTO_PER_CONVERSATION_HOUR} times this hour, so "
                           "the next reply waits for a person", code="conversation_cap")
    if conv.get("opted_out"):
        raise ReplyRefused("this person has opted out — nothing is sent to them", code="opted_out")
    try:
        from core import pause
        stopped = pause.is_paused()
    except Exception:                    # noqa: BLE001 — an unreadable switch fails toward silence
        stopped = True
    if stopped:
        raise ReplyRefused("the box is stopped, so nothing sends on its own", code="box_stopped")
    if window.allowed_send(conv.get("last_inbound_at"), platform=platform) != "freeform":
        raise ReplyRefused("this channel's window to reply is closed", code="window_closed")
    cap = sending.hourly_cap()
    if store.sends_last_hour(space) >= cap:
        raise ReplyRefused(f"the box has sent its {cap} messages for this hour; try again later", code="hourly_cap")
    account_id = conv.get("account_id") or ""
    if not account_id:
        raise ReplyRefused("this conversation has no account to send from")

    def answered() -> bool:
        with state.connect() as c:
            return c.execute("SELECT 1 FROM inbox_messages WHERE space = ? AND zernio_conversation_id = ? "
                             "AND direction = 'out' AND created_at > ? LIMIT 1",
                             (space, str(zcid), str(asked_at))).fetchone() is not None

    if answered():
        raise ReplyRefused("someone already answered this message", code="answered")
    idem = f"auto:{space}:{in_reply_to}"
    if not store.claim_send(space=space, zcid=zcid, idem_key=idem, kind="auto", user_id="auto_reply"):
        prior = store.get_send(space, idem) or {}
        if prior.get("status") == "ok":
            return {"status": "ok", "message_id": prior.get("zernio_message_id"), "idem_key": idem,
                    "duplicate": True}
        if prior.get("status") == "failed":
            raise ReplyRefused(str(prior.get("error") or "the message did not send"))
        raise ReplyIndeterminate(str(prior.get("error") or "the earlier attempt may have landed"))
    if answered() or claims.person_wrote_since(space, zcid, quiet) or claims.holder(space, zcid):
        store.resolve_send(space=space, idem_key=idem, status="failed",
                           error="someone answered first, so the box sent nothing")
        raise ReplyRefused("someone answered first, so the box sent nothing", code="answered")
    mid = _deliver(space=space, zcid=zcid, conv=conv, account_id=account_id, text=text, idem=idem)
    _mirror(space=space, zcid=zcid, mid=mid, sent_by="ai", body=text)
    log.info("inbox.auto_sent", extra={"space": space, "conversation": zcid, "message_id": mid})
    return {"status": "ok", "message_id": mid, "idem_key": idem, "duplicate": False}


# ── a machine's private reply to a comment: how a conversation starts ──────────────────────────────────────────
PRIVATE_REPLY_DAYS = 7          # Instagram accepts a private reply to a comment for 7 days after it was written


def _commenter_opted_out(space: str, author: dict) -> bool:
    """Has this commenter said STOP in a conversation on this box? The conversation row carries only `participant`,
    which on a live box is the person's DISPLAY NAME (read 2026-10-02 through the owner box's own connector: a
    business's name, not an @handle), so the commenter's display name and @handle are both compared, case-insensitive. Two
    people can share a display name; that errs toward NOT sending, never toward sending to someone who said STOP."""
    names = {str(author.get(k) or "").strip().lstrip("@").lower() for k in ("username", "name")} - {""}
    if not names:
        return False
    with state.connect() as c:
        rows = c.execute("SELECT participant FROM inbox_conversations WHERE space = ? AND opted_out = 1 "
                         "AND participant IS NOT NULL", (space,)).fetchall()
    return any(str(r["participant"]).strip().lstrip("@").lower() in names for r in rows)


def _age_days(at: str) -> float | None:
    from datetime import datetime, timezone
    try:
        t = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t).total_seconds() / 86400


def reply_to_comment(*, space: str, machine: str, comment: dict, text: str, key: str,
                     quick_replies: list | None = None) -> dict:
    """A MACHINE's private reply to an Instagram comment: the first message to someone who has never written,
    which is how the Lead Magnet's DM 1 opens a conversation (docs/PLAN_LEAD_MAGNET_MACHINE.md; OSDev1 approved
    the seam 2026-10-02). `comment` is one `m.comments()` returned, unchanged.

    No conversation exists yet, so no claim can gate it. These do:
      1. THE BOX ISN'T STOPPED (Stop everything).
      2. THE COMMENTER HASN'T OPTED OUT on this box. The provider (inbox/conversations.py) finds their conversation
         by id or @handle and reads its opt-out first; `_commenter_opted_out` here is a second net on the names.
      3. THE COMMENT IS UNDER 7 DAYS OLD, Instagram's window for a private reply. A comment with no time we can
         read is refused, not guessed.
      4. THE HOURLY CAP, the same count every send on the box shares.
      5. ONCE PER COMMENT, ACROSS EVERY MACHINE: Instagram allows one private reply per comment, so the ledger
         key is the comment, and `key` only names the step for the machine's own records.
    A refusal raises ReplyRefused (nothing went); a may-have-landed raises ReplyIndeterminate, never resent."""
    text = str(text or "").strip()
    if not text:
        raise ReplyRefused("a message needs words")
    if not clean_nonce(key):
        raise ReplyRefused("a machine's message needs a key: letters, digits, dot, dash or underscore")
    if not isinstance(comment, dict):
        raise ReplyRefused("pass the comment exactly as m.comments() returned it (the whole dict, not its id)")
    comment = dict(comment)
    cid, post, account = (str(comment.get(k) or "").strip() for k in ("id", "post", "account"))
    if not (cid and post and account):
        raise ReplyRefused("pass a comment exactly as m.comments() returned it")
    wire_quick = _wire_quick_replies(quick_replies)
    try:
        from core import pause
        stopped = pause.is_paused()
    except Exception:                    # noqa: BLE001 — an unreadable switch fails toward silence
        stopped = True
    if stopped:
        raise ReplyRefused("the box is stopped, so no automation sends", code="box_stopped")
    author = comment.get("author") if isinstance(comment.get("author"), dict) else {}
    if _commenter_opted_out(space, author):
        raise ReplyRefused("this person has opted out — nothing is sent to them", code="opted_out")
    age = _age_days(comment.get("at"))
    if age is None:
        raise ReplyRefused("this comment has no time on it, so its reply window can't be checked")
    if age > PRIVATE_REPLY_DAYS:
        raise ReplyRefused(f"Instagram only allows a private reply within {PRIVATE_REPLY_DAYS} days of a comment",
                           code="too_old")
    from . import sending
    cap = sending.hourly_cap()
    if store.sends_last_hour(space) >= cap:
        raise ReplyRefused(f"the box has sent its {cap} messages for this hour; try again later", code="hourly_cap")

    idem = f"private_reply:{space}:{cid}"
    zcid = f"comment:{cid}"
    if not store.claim_send(space=space, zcid=zcid, idem_key=idem, kind="machine_comment", user_id=machine):
        prior = store.get_send(space, idem) or {}
        if prior.get("status") == "ok":
            return {"status": "ok", "message_id": prior.get("zernio_message_id"), "idem_key": idem,
                    "duplicate": True}
        if prior.get("status") == "failed":
            raise ReplyRefused(str(prior.get("error") or "the reply did not send"))
        raise ReplyIndeterminate(str(prior.get("error") or "the earlier attempt may have landed"))
    try:
        cost_guard.check_vendor("zernio", 1)
        sp = _space(space)
        if not sp:
            raise ReplyRefused("this box cannot resolve the Space that owns this comment")
    except Exception as e:                # noqa: BLE001 — nothing was sent
        store.resolve_send(space=space, idem_key=idem, status="failed", error=str(e))
        raise
    try:
        sent = zernio.client(sp).comments.send_private_reply(post, cid, account, text,
                                                             quick_replies=wire_quick or None)
    except zernio.ZernioError as e:
        if e.indeterminate:
            _settle(space=space, idem_key=idem, status="indeterminate", error=str(e))
            raise ReplyIndeterminate(str(e)) from e
        store.resolve_send(space=space, idem_key=idem, status="failed", error=str(e))
        raise ReplyRefused(str(e)) from e
    except Exception as e:                # noqa: BLE001 — an unexpected raise mid-call is UNKNOWN
        _settle(space=space, idem_key=idem, status="indeterminate", error=str(e))
        raise ReplyIndeterminate(str(e)) from e
    mid = (sent or {}).get("message_id")
    _settle(space=space, idem_key=idem, status="ok", zernio_message_id=mid)
    log.info("inbox.machine_comment_reply", extra={"space": space, "comment": cid, "machine": machine,
                                                   "message_id": mid})
    return {"status": "ok", "message_id": mid, "idem_key": idem, "duplicate": False}


def _refusal_sentence(conv: dict, exc: Exception) -> str:
    """Turn a determinate vendor refusal into something the person who typed it can act on.

    OPTION C OF #1170 — THE HALF THAT NEEDED NO RULING. The window is never consulted to decide
    whether to CALL the vendor; this runs only after the vendor has already said no, and it
    changes the sentence, never the outcome.

    THE CLOCK CORROBORATES, IT NEVER DECIDES. The plain-language answer is used only when the
    vendor refused for a window reason AND this box's own `last_inbound_at` agrees the window is
    shut. Our state is an inference from a column a poller fills — it lags, skips channels, and
    carries stale watermarks — so when the two disagree the inference is the thing that is wrong,
    and quoting it at somebody would be inventing a reason for a refusal we do not understand.

    FALLS BACK TO THE VENDOR'S OWN WORDS, always. Every path that is not confidently recognised
    returns what today's code returns. A recogniser that guesses wrong costs a person the real
    error text, which is the one thing they could have searched for.
    """
    raw = str(exc)
    low = raw.lower()
    if any(m in low for m in _WINDOW_MARKERS):
        from . import window
        state = window.explain(conv.get("platform") or "", conv.get("last_inbound_at"))
        if state.get("state") in ("closed", "tagged", "limited"):
            log.info("inbox.reply_window_refusal",
                     extra={"platform": conv.get("platform"), "state": state.get("state"),
                            "vendor": raw[:160]})
            who = window.pretty_platform(conv.get("platform") or "")
            return f"{state['headline']} {who} would not take this one."
    return f"the message did not send: {raw[:160]}"


def _space(name: str) -> dict | None:
    """The Space row the gateway keys off — resolved EXACTLY as `handler.py:41` resolves it.

    `allow_default_alias=False` is the whole point and it is copied deliberately rather than
    reinvented: an unknown or "default" Space stamp must NOT alias onto the global Zernio key on
    a SEND path, or a reply typed on one box's screen can leave from another tenant's account.
    Paired with the gateway's fail-closed key guard (a falsy per-Space key refuses to build a
    client) and its isolation backstop, a mis-keyed Space refuses instead of opening from the
    wrong account. A second, subtly different lookup on the second send path is exactly how one
    of them stops matching — the reason `viewer_is_owner` and `unlocked` have a test forcing
    them to agree.
    """
    from core import spaces
    return spaces.space_by_name(name, allow_default_alias=False)
