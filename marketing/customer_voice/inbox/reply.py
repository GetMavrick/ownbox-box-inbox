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

from core import cost_guard
from core.logging import get_logger
from core.vendors import zernio

from . import store, window

log = get_logger(__name__)


class ReplyRefused(Exception):
    """The reply was not attempted, and nothing was spent. The caller shows the reason."""


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


def send_reply(*, space: str, zcid: str, text: str, user_id: str, nonce: str,
               account_id: str | None = None) -> dict:
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
    """
    text = str(text or "").strip()
    if not text:
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
        raise ReplyRefused("this person has opted out — nothing is sent to them")

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
    # not about time at all: this box has no SMTP path, so there is nothing for a person to be
    # blocked FROM, and the only alternative is the fall-through a buyer actually hit — an email
    # draft reaching the Zernio branch and getting an exception class name where a sentence
    # belongs (OSDev1, 2026-09-22).
    #
    # The first cut of this called `decide`, and CI was right to refuse it. `no_send_lane_why`
    # takes no timestamp, so it cannot answer "the window shut" — and the suite now asserts that
    # argument list, so a later edit cannot quietly give it one.
    why = window.no_send_lane_why(str(conv.get("platform") or ""))
    if why:
        raise ReplyRefused(why)

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

    # FROM HERE WE OWN THE ROW, and every exit below must resolve it. A claim we abandon reads
    # as "may have landed" forever (store.claim_send), which would block the person's honest
    # retry over something that never reached the vendor — so the two pre-send refusals resolve
    # to `failed` first, exactly as `release_opener` releases an opener claim whose send
    # DETERMINATELY failed.
    try:
        cost_guard.check_vendor("zernio", 1)
        sp = _space(space)
        if not sp:
            # FAIL CLOSED ON AN UNRESOLVED SPACE. Never fall through to a default — see _space.
            raise ReplyRefused("this box cannot resolve the Space that owns this conversation")
    except Exception as e:                # noqa: BLE001 — nothing was sent on either path
        store.resolve_send(space=space, idem_key=idem, status="failed", error=str(e))
        raise

    try:
        sent = zernio.client(sp).inbox.send(zcid, account_id, text)
    except zernio.ZernioError as e:
        if e.indeterminate:              # always set by ZernioError.__init__, as handler.py:169 reads it
            # MAY HAVE LANDED. Record it, tell the caller, and never resend on our own.
            store.resolve_send(space=space, idem_key=idem, status="indeterminate", error=str(e))
            log.error("inbox.reply_indeterminate", extra={"space": space, "conversation": zcid,
                                                          "error": str(e)[:160]})
            raise ReplyIndeterminate(str(e)) from e
        store.resolve_send(space=space, idem_key=idem, status="failed", error=str(e))
        raise ReplyRefused(_refusal_sentence(conv, e)) from e
    except Exception as e:                # noqa: BLE001 — an unexpected raise mid-call is UNKNOWN
        # A non-ZernioError escaping the gateway (a bug, a socket the SDK did not wrap) tells us
        # nothing about whether the vendor took the message. The row must not be left `sending`
        # for the watchdog to find hours later when we can say the honest thing right now.
        store.resolve_send(space=space, idem_key=idem, status="indeterminate", error=str(e))
        log.error("inbox.reply_unexpected", extra={"space": space, "conversation": zcid,
                                                   "error": str(e)[:160]})
        raise ReplyIndeterminate(str(e)) from e

    mid = sent.get("message_id")
    store.resolve_send(space=space, idem_key=idem, status="ok", zernio_message_id=mid)
    # `sent_by='human'` is the honesty of the screen: the thread says who said every line, and
    # "the machine" and "you" must never be swapped. §3.3 records WHICH human on the ledger.
    store.record_message(space=space, zcid=zcid, zmid=mid, direction="out",
                         sent_by="human", body=text)
    # WHAT THEY SENT, NEXT TO WHAT THE BOX DRAFTED — the one moment both exist. Guarded HERE as
    # well as inside `learn`: the reply has already left, and its success is not hostage to
    # bookkeeping — not to a raising learn, not to a failed import, not to a future edit that
    # drops the inner guard. The suite replaces `learn` with a raise to prove this line.
    try:
        from marketing.customer_voice.drafter import store as _drafts
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
