"""Secrets the BUYER sets from a screen, because they have no other way to set them.

THE ONE THAT MATTERS TODAY is the AI key. A delivered box ships `brain.backend: api` — bring
your own — with no key from install.sh and none from the provisioner, so `drafter/draft.py`
returns {"skipped": "unconfigured"} and the box files messages and drafts nothing. The buyer has
no shell and no `.env`; a screen is the only surface they have.

WHY A TABLE AND NOT THE ENVIRONMENT. `core/config.Settings` reads `os.environ` at import, so
anything set after boot is invisible to the process that set it — and the drafter does not run in
the web process at all. It runs in the WORKER, a separate unit. Nothing the web app puts in its
own environment can reach it. The database is the only medium the two already share.

THE ENVIRONMENT STILL WINS. A box whose `.env` carries ANTHROPIC_API_KEY behaves exactly as it
does today and never consults this table: the operator's own box, every box in CI, and any
deployment where someone has already made a deliberate choice. This only ever fills a hole.
"""
from __future__ import annotations

import json

from core import state
from core.vendors.mailbox import providers as _mailbox_providers
from core.logging import get_logger

log = get_logger(__name__)

ANTHROPIC = "anthropic_api_key"
# WHAT THE VENDOR LAST SAID ABOUT THAT KEY, so a screen never has to guess from its presence.
# `is_set(ANTHROPIC)` answers "is there a key", which is not the question a buyer is asking; the
# question is "will a draft arrive", and those two parted company the moment a key could be
# revoked. Same shape as EMAIL_STATUS and ZERNIO_STATUS, and read through `anthropic_state()`.
ANTHROPIC_STATUS = "anthropic_status"
ANTHROPIC_DETAIL = "anthropic_detail"
# THE BUYER'S CLAUDE SUBSCRIPTION, AS AN ALTERNATIVE TO AN API KEY (owner, 2026-09-18). Many of the
# people we sell to are one person with a Claude subscription and no API account at all — the owner
# is the first of them: "I am going to sign up as Customer number one and that must be available
# when I set up my box... I don't have an API key."
#
# WHOSE DECISION THIS IS, RECORDED BECAUSE IT WAS CHANGED DELIBERATELY. Anthropic's consumer terms
# are per-person, so an earlier reading of them kept this off any box but the owner's. He reviewed
# the terms himself and ruled otherwise, 2026-09-18: "that should be the choice of the client. They
# are taking ownership of these boxes and they're using them most often for an individual so they
# have to review their own terms and be responsible for themselves. We are not the police." The box
# therefore OFFERS the choice, LINKS the terms, and does not make it for anyone.
CLAUDE_OAUTH = "claude_code_oauth_token"
# THE RECORD THAT THE PERSON TICKED THE BOX, kept because legal counsel asked for the gate and a
# gate nobody can evidence afterwards is decoration (owner, 2026-09-18, relaying his counsel). It
# holds WHEN and WHO, never the token.
#
# IT IS RECORDED, NEVER REQUIRED — and this comment said the opposite until 2026-09-22, when it
# still described the store as refusing a token that arrived without the tick. That was true of
# the first cut and the owner overruled it the same day: a box does not hold its owner's
# credential hostage to our comfort (the long note in `put_claude_oauth` carries his ruling in
# full). The stale sentence outlived the behaviour it described by months and was read back as
# fact by the next person in the file — which is the whole reason a comment about a rule belongs
# next to the code that enforces the rule, never next to the constant it names.
CLAUDE_OAUTH_CONSENT = "claude_code_oauth_consent"
CLAUDE_OAUTH_STATUS = "claude_code_oauth_status"
CLAUDE_OAUTH_DETAIL = "claude_code_oauth_detail"
# SIGN IN WITH CHATGPT. There is no token row: the credential is the Codex CLI's own auth.json
# under CODEX_HOME, written by `codex login --device-auth` and read by `codex exec`. The box
# keeps only a status beside it, the way it keeps one beside the mailbox and Zernio.
CODEX_STATUS = "codex_login_status"
CODEX_DETAIL = "codex_login_detail"
# THE SAME RECORD, FOR THE OTHER SUBSCRIPTION. A buyer signing in with ChatGPT is doing exactly
# what a buyer signing in with Claude is doing — running a box on a consumer subscription — so the
# tick counsel asked for belongs on both screens or on neither. It shipped on one: #1423 landed
# the ChatGPT sign-in with no consent row and no tick, so a box could be drafting on somebody's
# ChatGPT subscription with nothing on record that they were ever shown the terms.
#
# Same shape and same rule as CLAUDE_OAUTH_CONSENT: WHEN and WHO, never a credential, written
# only when the tick is given, and never a condition of signing in.
CODEX_CONSENT = "codex_login_consent"
# Anthropic's own terms, linked on the set-up screen so the choice is made with them in front of
# the person making it rather than described second-hand by us.
ANTHROPIC_TERMS_URL = "https://www.anthropic.com/legal/consumer-terms"
# BOTH VENDORS, because a buyer's subscription may be either and the point is that they can read
# the terms of THEIRS without going looking. Owner, 2026-09-18: "a link to Anthropic terms and to
# OpenAI terms right there on the page so they can review the terms of their subscription in
# particular."
OPENAI_TERMS_URL = "https://openai.com/policies/terms-of-use"
TERMS_LINKS = (("Anthropic's consumer terms", ANTHROPIC_TERMS_URL),
               ("OpenAI's terms of use", OPENAI_TERMS_URL))
# THE BUYER'S MAILBOX, FOR READING (SPEC #1226). Three values useless apart, so they are stored and
# replaced as ONE row: a half-updated credential is an auth failure nobody can explain.
# THE BUYER'S OWN ZERNIO ACCOUNT (owner, 2026-09-16). Ruled after checking all 60 SDK resources:
# Zernio has no organization, workspace or billing-transfer API — a profile is a FOLDER inside one
# account on one bill, so the DigitalOcean provision-then-hand-over model has no equivalent and
# every profile we mint stays on OUR bill forever. The owner does not pay for a customer's vendor,
# so this is bring-your-own-key, exactly like ANTHROPIC above and the mailbox below.
ZERNIO = "zernio_api_key"
# Written when a key is stored or later refused, read by the screen. Never holds a credential.
ZERNIO_STATUS = "zernio_api_key_status"
ZERNIO_DETAIL = "zernio_api_key_detail"
# THE PROFILE INSIDE THE BUYER'S OWN ACCOUNT that this box's connected accounts hang under. An id,
# not a credential — but it belongs in this table rather than the config because it is DISCOVERED
# from whichever account the buyer's key opens, and it is meaningless the moment that key changes.
# It is stored beside the key it was resolved with, and cleared with it, because the two are ONE
# binding: a profile id from our Zernio account handed to a buyer's key names a folder that does
# not exist there — or, far worse on a shared account, one that belongs to somebody else.
ZERNIO_PROFILE = "zernio_profile_id"
EMAIL = "email_imap"
# Written by the poller when Google refuses, read by the screen. Never holds a credential.
EMAIL_STATUS = "email_imap_status"
EMAIL_DETAIL = "email_imap_detail"
# CAN THAT SAME PASSWORD *SEND*? A separate question with a separate answer, because a Workspace
# administrator can leave IMAP on and turn SMTP off — so the credential that reads this mailbox
# perfectly may be unable to send one message. Recorded at set-up, never enforced there: reading
# is most of what this box does, and refusing a working mailbox over a send check would break the
# feature that works to protect one that has not shipped yet.
EMAIL_SEND = "email_smtp_status"        # can_send | refused | unknown; "" = never asked
EMAIL_SEND_DETAIL = "email_smtp_detail"


def get(name: str) -> str:
    """The stored value, or "". Never raises — a box too old to have the table has no secrets."""
    try:
        with state.connect() as c:
            row = c.execute("SELECT value FROM box_secrets WHERE name = ?", (name,)).fetchone()
    except Exception:                       # noqa: BLE001 — an unreadable table is "not set"
        return ""
    return str(row["value"]) if row else ""


def is_set(name: str) -> bool:
    return bool(get(name))


class SecretRejected(ValueError):
    """The value was not stored. The message is shown to the buyer, so it says what to fix."""


# WHAT AN ANTHROPIC KEY LOOKS LIKE. Deliberately loose: a shape check, not a validation of the
# key itself — only Anthropic can say whether a key works, and a box that refused a valid key
# because our regex was a month out of date would be worse than one that accepted a typo.
# What this catches is the mistake people actually make: pasting the wrong thing entirely —
# an email, a URL, a password, half a key with the front chewed off by a copy.
_ANTHROPIC_SHAPE = ("sk-ant-", 40)

# A SUBSCRIPTION TOKEN IS A DIFFERENT THING WITH A LONGER PREFIX, and the prefix is what lets one
# field take either. `claude setup-token` mints `sk-ant-oat...`; an API key is `sk-ant-api...`. Both
# start `sk-ant-`, so the ORDER of the test matters: the longer prefix is asked about first, or
# every subscription token is misfiled as an API key and sent to an endpoint that will refuse it.
_CLAUDE_OAUTH_SHAPE = ("sk-ant-oat", 40)

# WHAT A GOOGLE APP PASSWORD LOOKS LIKE: sixteen letters, which Google DISPLAYS as four groups of four.
# People paste it with the spaces in, every time, because that is how it is shown to them. Stripping
# them is not leniency — rejecting the format Google itself renders would be our bug dressed up as
# their mistake. Same rule as the key above: catch the wrong thing entirely, never judge validity.
_APP_PASSWORD_LEN = 16


def _clean_app_password(value: str) -> str:
    return "".join(str(value or "").split())


def zernio_key() -> str:
    """This box's own Zernio key, or "" when the buyer has not connected one."""
    return get(ZERNIO)


def put_anthropic(value: str, *, user_id: str | None = None) -> None:
    """Store the buyer's AI key — after asking the vendor whether it works.

    THE LAST CREDENTIAL ON THIS BOX THAT NOBODY CHECKED. Zernio is verified below and the mailbox
    signs in (`put_email`); this one was `put(ANTHROPIC, ...)` straight into the table on a prefix
    and a length. Measured on an exported customer_voice box, 2026-09-17: `sk-ant-api03-` plus
    eighty characters was accepted, the screen redirected as though it had worked, `brain.
    can_think()` answered `(True, 'api')`, and `/inbox/settings` then stated — as settled fact —
    "Drafts On. Ownbox writes a reply for every message that arrives." On that box it never would.

    AND THE THREAD SAID NOTHING, which is what made it silent rather than merely wrong. §2.6's
    "Drafts are off" note hangs off `is_set(ANTHROPIC)`, and the key IS set — so the one line that
    exists to explain a missing draft is suppressed by the very thing that broke it. The buyer
    gets an ordinary empty reply box, on every thread, forever, and no sentence anywhere.

    THE SHAPE CHECK STAYS AND RUNS FIRST. `validate` catches the wrong THING entirely — a Zernio
    key, a password, a line of a receipt — without spending a round trip, and it is the same rule
    whoever writes a key. The probe is for the thing a shape can never see: a well-formed key that
    was revoked, typed one character short, or belongs to an account with no credit left.
    """
    value = validate(ANTHROPIC, value)           # shape first: free, and it is the same rule
    from core import brain                       # lazily — `core.brain` imports config and state
    ok, detail = brain.verify_key(value)
    if not ok:
        # UNREACHABLE IS NOT REFUSED. `verify_key` has already decided which of the two this is
        # and written the sentence for it; a box that could not get to the API stores nothing and
        # condemns nothing, so nobody is sent off to mint a key they did not need.
        raise SecretRejected(detail)
    put(ANTHROPIC, value, user_id=user_id)
    put(ANTHROPIC_STATUS, "connected", user_id=user_id)
    # CLEARED, NOT OVERWRITTEN WITH A SENTINEL — the same correction `put_email` carries, and for
    # the same reason: a successful reconnect must not leave last week's refusal sitting under a
    # row that now says Connected.
    clear(ANTHROPIC_DETAIL, user_id=user_id)


def put_zernio(value: str, *, user_id: str | None = None) -> None:
    """Store the buyer's Zernio key. Raises SecretRejected with a sentence they can act on.

    VERIFIED WITH THE VENDOR, NOT MATCHED AGAINST A SHAPE. `put_anthropic` above now asks its vendor
    too, but it can shape-check first because an Anthropic key's prefix is published and stable.
    Zernio's is not: it is 67 characters with no
    documented prefix, so a regex here would be a rule we invented, and the failure it produced
    would be OUR bug wearing the buyer's name. Asking the vendor costs one round trip at the only
    moment a person is present to fix it, and it catches the thing a shape check never could — a
    well-formed key that has been revoked."""
    value = str(value or "").strip()
    if not value:
        raise SecretRejected("Paste the API key from your Zernio account.")
    if any(ch.isspace() for ch in value):
        raise SecretRejected("That key has a space or a line break in it. Copy it again.")
    from core.vendors.zernio import verify as _zverify
    ok, detail = _zverify.verify_key(value)
    if not ok:
        raise SecretRejected(detail)
    put(ZERNIO, value, user_id=user_id)
    put(ZERNIO_STATUS, "connected", user_id=user_id)
    put(ZERNIO_DETAIL, " ", user_id=user_id)


def note_zernio_status(status: str, detail: str = "", *, user_id: str | None = None) -> None:
    """The poller says why it could not use the key, so the screen can say something actionable."""
    if status not in ("connected", "needs_reauth", "payment_required"):
        raise ValueError(f"unknown zernio status {status!r}")
    put(ZERNIO_STATUS, status, user_id=user_id)
    put(ZERNIO_DETAIL, detail[:300] if detail else " ", user_id=user_id)


def zernio_state() -> dict:
    """What a screen binds to. Never the key itself.

    `payment_required` is its own state because it is the one a buyer can actually fix and the one
    that reads as broken otherwise: Zernio caps connected accounts until a payment method is on the
    ACCOUNT, and the API says so with a 402. Told "authentication failed" they would re-paste a
    perfectly good key forever."""
    if not zernio_key():
        return {"status": "not_connected", "detail": ""}
    return {"status": get(ZERNIO_STATUS) or "connected", "detail": get(ZERNIO_DETAIL)}

def _mailbox_verify(host: str, user: str, password: str):
    """Ask the mail server. Imported lazily so this module still loads where imaplib is unusual,
    and pointed at `core.vendors.mailbox` rather than the inbox machine — core imports no machine,
    and a Lead box has no `marketing/customer_voice` to import from at all."""
    from core.vendors.mailbox import verify_credential
    return verify_credential(host, user, password)


def _mailbox_verify_send(host: str, user: str, password: str):
    """Ask whether the same credential may SEND. Lazily imported for the reason above."""
    from core.vendors.mailbox import verify_send
    return verify_send(host, user, password)


def zernio_profile() -> str:
    """The Profile id this box uses inside the buyer's own Zernio account, or "".

    PAIRED WITH THE KEY, NEVER READ ALONE. `core.spaces` resolves the two together for exactly
    this reason, and every caller should take the pair from there rather than reading this."""
    return get(ZERNIO_PROFILE)


def put_zernio_profile(value: str, *, user_id: str | None = None) -> None:
    """Remember the Profile resolved inside the buyer's account. Machine-written, never pasted."""
    value = str(value or "").strip()
    if not value:
        raise SecretRejected("No profile id to store.")
    put(ZERNIO_PROFILE, value, user_id=user_id)


def clear_zernio(*, user_id: str | None = None) -> None:
    """Disconnect the buyer's Zernio account — the key AND everything resolved with it.

    THE PROFILE GOES WITH THE KEY. Leaving the id behind is how the next key pasted into this box
    would inherit a folder id from the previous account: the connect screen would then send a
    person's Instagram consent at a profile that is not theirs. Clearing is the whole of
    disconnecting, the same contract as turning drafts off (`/inbox/drafts?off=1`)."""
    for name in (ZERNIO, ZERNIO_PROFILE, ZERNIO_STATUS, ZERNIO_DETAIL):
        clear(name, user_id=user_id)


def email_credential() -> dict:
    """This box's mailbox credential as {host, user, password}, or {} when none is set.

    Returning {} rather than raising is deliberate: "not connected" is an ordinary state."""
    raw = get(EMAIL)
    if not raw:
        return {}
    try:
        got = json.loads(raw)
    except ValueError:
        return {}
    return got if isinstance(got, dict) and got.get("user") and got.get("password") else {}


def put_email(*, host: str, user: str, password: str, user_id: str | None = None) -> None:
    """Store the mailbox credential as one row. Raises SecretRejected with a sentence for the buyer."""
    _providers = _mailbox_providers
    host = str(host or "").strip().lower() or "imap.gmail.com"
    user = str(user or "").strip()
    if not user or "@" not in user:
        raise SecretRejected("Put in the full email address of the mailbox you want read.")
    # MICROSOFT IS REFUSED BEFORE A PASSWORD IS SENT ANYWHERE (#1483 finding 1; see
    # core/vendors/mailbox/providers.py for the source). Told "wrong password" after a twenty-second
    # wait, a buyer makes a new one and it fails the same way; told why, they know what to do.
    if _providers.is_microsoft(host, user):
        raise SecretRejected(_providers.MICROSOFT_SAID)
    if _providers.provider_of(host) == "other":
        try:
            host = _providers.host_for("other", host)       # a host name, never a URL or a port
        except ValueError as e:
            raise SecretRejected(str(e)) from None
    if not _providers.is_google(host):
        # EVERY OTHER PROVIDER HAS ITS OWN PASSWORD SHAPE — iCloud's has dashes, Zoho's is twelve,
        # a web host's is whatever the buyer chose — so the only check is that there is one. The
        # server is the judge, a line below. Surrounding space is a paste artefact; inner space is
        # left alone, because on these providers it may be part of the password.
        password = str(password or "").strip()
        if not password:
            raise SecretRejected("Paste the password for this mailbox — for most providers an app "
                                 "password made in your account's security settings.")
        if len(password) > 256:
            raise SecretRejected("That is too long to be a password. Paste only the password.")
    else:
        password = _clean_app_password(password)
    if _providers.is_google(host) and not password:
        raise SecretRejected("Paste the app password Google gave you.")
    if _providers.is_google(host) and (len(password) != _APP_PASSWORD_LEN or not password.isalnum()):
        # The mistake people actually make is pasting their ACCOUNT password — the one thing that must
        # never reach this table. It is far broader than the box needs, and it would keep working after
        # they revoked the thing they believed they had given us.
        raise SecretRejected("That looks like your Google password, not an app password. An app "
                             "password is 16 letters, shown as four groups of four.")
    # VERIFIED BY SIGNING IN, NOT BY COUNTING CHARACTERS. Found by OSDev5, 2026-09-16: this wrote
    # `status = connected` straight after the shape check above and never once opened the mailbox,
    # so a buyer who pasted a revoked app password — which is what Google silently does to every
    # app password whenever the account password changes — read "Connected" on the screen and
    # found out hours later from an inbox that stayed empty. The shape checks above still earn
    # their place: they catch the wrong THING entirely without spending a round trip.
    ok, status, detail = _mailbox_verify(host, user, password)
    if not ok:
        # UNREACHABLE IS NOT REFUSED. If the box simply could not get to the mail server, the
        # credential is not stored and it is not condemned either — the sentence says to try
        # again rather than sending somebody off to make a new password they did not need.
        raise SecretRejected(detail)
    put(EMAIL, json.dumps({"host": host, "user": user, "password": password}), user_id=user_id)
    put(EMAIL_STATUS, status, user_id=user_id)
    # CLEARED, NOT OVERWRITTEN WITH A SENTINEL. A successful reconnect must not leave last week's
    # "Google refused the app password" sitting under a row that now says Connected, and `clear`
    # says "there is no error" without inventing a value to mean it.
    clear(EMAIL_DETAIL, user_id=user_id)

    # AND THE SECOND QUESTION, ASKED ONLY ONCE THE FIRST HAS BEEN ANSWERED YES.
    #
    # ORDER IS THE POINT. Reading is verified first and stored first, so a mailbox that reads is
    # connected the instant it is proven to read — whatever the send check goes on to say. It also
    # makes the refusal sentence honest: the password demonstrably works, seconds ago, on this same
    # account, so "wrong password" is the one explanation already ruled out.
    #
    # ITS FAILURE IS RECORDED, NEVER RAISED. `put_email` raises to mean "not stored, fix this and
    # try again", and a box that cannot send can still read every message a customer writes.
    # Raising here would take that away over a feature the buyer has not been offered yet.
    can_send, send_status, send_detail = _mailbox_verify_send(host, user, password)
    put(EMAIL_SEND, send_status, user_id=user_id)
    if can_send or not send_detail:
        clear(EMAIL_SEND_DETAIL, user_id=user_id)
    else:
        put(EMAIL_SEND_DETAIL, send_detail, user_id=user_id)


def clear_email(*, user_id: str | None = None) -> None:
    """Disconnect the mailbox — the credential AND every verdict recorded about it.

    ONE PLACE THAT KNOWS WHAT A MAILBOX CONNECTION IS MADE OF, for the reason `clear_zernio` above
    exists: the screen used to name these rows one by one, so every row added since has been a row
    somebody had to remember to add there too. The send verdict was going to be the first one
    forgotten, and a left-behind "can send" on a mailbox this box no longer reads is a screen
    telling a buyer they can do something the box cannot even attempt.
    """
    for name in (EMAIL, EMAIL_STATUS, EMAIL_DETAIL, EMAIL_SEND, EMAIL_SEND_DETAIL):
        clear(name, user_id=user_id)


def email_state() -> dict:
    """WHAT A SCREEN BINDS TO, and nothing a screen should not have: never the password.

    `status` is one of: not_connected | connected | needs_reauth | admin_disabled. The last two exist
    because Google revokes an app password whenever the account password changes, and because a
    Workspace administrator can switch them off entirely — both arrive as the same opaque IMAP refusal,
    and a buyer told "authentication failed" learns nothing they can act on."""
    cred = email_credential()
    if not cred:
        # THE SAME KEYS ON EVERY PATH. A screen binds to this dict once; an early return that
        # leaves two of them out turns "no mailbox yet" — the state every box starts in — into a
        # KeyError on the set-up page, which is the one page that has to work before anything else
        # does. Caught by the suite, which asked a disconnected box what it could send.
        return {"status": "not_connected", "user": None, "detail": "",
                "send": "unknown", "send_detail": ""}
    # `send` IS A THIRD THING, NOT A SHADE OF `status`. A mailbox can be perfectly connected and
    # unable to send, and folding the two into one word would force a screen to either call a
    # working mailbox broken or hide the one fact a buyer needs before they rely on sending.
    #
    # "unknown" IS THE HONEST DEFAULT and it covers two different pasts: a box connected before
    # this check existed, and one where the check could not reach the server. Neither is "no".
    return {"status": get(EMAIL_STATUS) or "connected", "user": cred.get("user"),
            "detail": get(EMAIL_DETAIL),
            "send": get(EMAIL_SEND) or "unknown",
            "send_detail": get(EMAIL_SEND_DETAIL)}


def note_email_status(status: str, detail: str = "", *, user_id: str | None = None) -> None:
    """The poller says WHY it could not sign in, so the screen can say something a person can act on.

    "connected" CLEARS THE DETAIL RATHER THAN WRITING ONE. This is a correction, not a tidy-up:
    the line was `put(EMAIL_DETAIL, detail[:300] if detail else " ")`, and that single space was
    there because `validate` rejects an empty value. `validate` also STRIPS, so the space became
    "" and the call raised `SecretRejected("Paste the key from your AI account, then turn drafts
    on.")` — a sentence about an AI key, thrown while recording a mailbox login, inside the
    poller, where nobody is looking. It has never fired in production only because the poller
    calls this on FAILURE alone, always with a detail; the success path it documents has no
    caller yet. It would have raised for the first person who wrote one.

    Clearing is also the right behaviour independent of the bug. `detail` is the reason a sign-in
    FAILED, so leaving the last failure's sentence on a mailbox that now works is how a screen
    quotes a month-old error at somebody whose inbox is fine.
    """
    if status not in ("needs_reauth", "admin_disabled", "connected"):
        raise ValueError(f"unknown email status {status!r}")
    put(EMAIL_STATUS, status, user_id=user_id)
    # NO DETAIL MEANS NO ROW. The `" "` sentinel is gone entirely rather than moved: it existed
    # only to get past `validate`'s empty check, and `validate` STRIPS before that check, so the
    # space was never a value — it was an exception waiting for an empty argument.
    #
    # THE FIRST VERSION OF THIS FIX ONLY COVERED "connected", AND OSDEV1 CAUGHT THAT. The failure
    # branches still carried the identical trap, and it is the same wrong sentence — one about an
    # AI key — thrown while recording why a MAILBOX was refused. Narrow today, because
    # `EmailAuthError` always carries a detail, and exactly the kind of narrow that stops being
    # narrow the moment somebody adds a caller. Reproduced on both arms before changing it.
    if detail and detail.strip():
        put(EMAIL_DETAIL, detail[:300], user_id=user_id)
    else:
        clear(EMAIL_DETAIL, user_id=user_id)


def validate(name: str, value: str) -> str:
    """The cleaned value, or raise `SecretRejected` saying what to fix.

    VALIDATED ON SAVE, NOT ON USE, and the difference is where the person is standing. A key
    rejected here is rejected while they are looking at the field they pasted it into. The same
    key rejected on use fails hours later, inside a worker, on a draft nobody is watching — and
    the buyer's experience is that drafting silently does not work, which is the exact failure
    §2.6 exists to end.
    """
    # NOT EVERY ROW IN THIS TABLE IS A PASTED KEY, AND THE EXEMPTION HAS TO COME FIRST.
    # Two kinds of value here are not keys: the mailbox credential, stored as one JSON object so it
    # can never be half-updated, and a status detail, which is a SENTENCE written for the buyer.
    # The key rules below would reject both at runtime, inside the poller, where nobody is looking.
    #
    # THIS BLOCK USED TO SIT BELOW THE EMPTY CHECK, WHICH MADE IT UNREACHABLE FOR THE CASE IT WAS
    # WRITTEN FOR. `note_email_status(status)` with no detail stores " " as a deliberate sentinel;
    # `.strip()` turned that into "" and the empty check raised — after the status row had already
    # been written, so the box was left with a half-written status AND an exception in the poller.
    # Reproduced, then moved. A guard placed after the thing it guards is not a guard.
    if name in (EMAIL, EMAIL_DETAIL, EMAIL_SEND_DETAIL, ZERNIO_DETAIL, ZERNIO_PROFILE,
                ANTHROPIC_DETAIL, CLAUDE_OAUTH_DETAIL, CLAUDE_OAUTH_CONSENT,
                CODEX_DETAIL, CODEX_CONSENT):
        return str(value or "")
    value = str(value or "").strip()
    if not value:
        raise SecretRejected("Paste the key from your AI account, then turn drafts on.")
    if any(ch.isspace() for ch in value):
        # A pasted key with a newline or a stray space fails at the vendor with an opaque
        # error; caught here it is one sentence at the moment they can fix it.
        raise SecretRejected("That has a space or a line break in it — paste the key on its own.")
    if name == ANTHROPIC:
        prefix, least = _ANTHROPIC_SHAPE
        if not value.startswith(prefix) or len(value) < least:
            raise SecretRejected(f"That does not look like an Anthropic key — they start "
                                 f"with {prefix} and are longer than that.")
    if name == CLAUDE_OAUTH:
        prefix, least = _CLAUDE_OAUTH_SHAPE
        if not value.startswith(prefix) or len(value) < least:
            raise SecretRejected("That does not look like a Claude subscription token — run "
                                 "`claude setup-token` and paste the sk-ant-oat... value.")
    return value


def looks_like_subscription(value: str) -> bool:
    """Is this pasted credential a subscription token rather than an API key?

    THE ROUTER FOR ONE FIELD TAKING TWO THINGS. The set-up screen asks for "your Anthropic key or
    your Claude subscription token" in a single box, because a buyer knows which of the two they
    have and does not want to pick a radio button to prove it. A wrong answer here is not a
    cosmetic slip: an API key sent down the subscription path, or the reverse, fails at the vendor
    with a message about the wrong credential entirely.
    """
    return str(value or "").strip().startswith(_CLAUDE_OAUTH_SHAPE[0])


def put(name: str, value: str, *, user_id: str | None = None) -> None:
    """Set or replace a secret. The VALUE IS NEVER LOGGED — only that it changed, and by whom."""
    value = validate(name, value)
    with state.connect() as c:
        c.execute("INSERT INTO box_secrets (name, value, set_at, set_by) VALUES (?,?,?,?) "
                  "ON CONFLICT(name) DO UPDATE SET value = excluded.value, "
                  "set_at = excluded.set_at, set_by = excluded.set_by",
                  (name, value, state._now(), user_id))
    log.info("box_secret.set", name=name, user=user_id)


def clear(name: str, *, user_id: str | None = None) -> bool:
    """Remove it. Returns whether there was one. "Turn off" in the Settings row lands here."""
    with state.connect() as c:
        cur = c.execute("DELETE FROM box_secrets WHERE name = ?", (name,))
    gone = cur.rowcount > 0
    if gone:
        log.info("box_secret.cleared", name=name, user=user_id)
    return gone


def anthropic_key() -> str:
    """The key `core.brain` should use, environment first.

    ENVIRONMENT FIRST IS THE WHOLE COMPATIBILITY STORY. Every box running today has its key in
    `.env` and keeps behaving identically; this is consulted only where that is empty, which is
    exactly the delivered-box hole and nowhere else.

    `os.environ` IS READ LIVE, ahead of `settings`, and that is a correction rather than a
    flourish. `core.config.Settings` captures the environment at IMPORT, so a value set after
    that is invisible through it — and `brain.can_think()` has always read os.environ directly,
    which is why `tests/test_brain_readiness` sets the variable at runtime and expects it to
    count. Routing this through `settings` alone quietly broke that, and the suite said so.
    Reading both keeps every existing caller's behaviour and covers a `settings` that was
    deliberately overridden in-process.
    """
    import os

    from core.config import settings
    return ((os.environ.get("ANTHROPIC_API_KEY") or "").strip()
            or (getattr(settings, "anthropic_api_key", "") or "").strip()
            or get(ANTHROPIC))

def claude_oauth_token() -> str:
    """The buyer's Claude subscription token, environment first — the same order as the API key.

    ENVIRONMENT FIRST KEEPS THE OWNER BOX EXACTLY AS IT IS. It already carries
    CLAUDE_CODE_OAUTH_TOKEN in /opt/aios/.env, installed by scripts/install_claude_code.sh, and
    nothing about that changes. The table is consulted only where the environment is empty, which
    is precisely the delivered-box hole this is here to close.
    """
    import os
    return (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or "").strip() or get(CLAUDE_OAUTH)


def put_claude_oauth(value: str, *, consented: bool = False,
                     user_id: str | None = None) -> None:
    """Store the buyer's subscription token, and say plainly what was NOT checked.

    NO VENDOR PROBE, AND THAT IS NOT AN OVERSIGHT. `put_anthropic` asks Anthropic whether an API
    key works, because `models.list` is free and answers in a second. There is no equivalent for a
    subscription token: the only way to test one is to spend a real inference call through the CLI,
    which is slow, is charged against the person's own subscription quota, and needs the CLI
    already installed. Spending somebody's quota to decorate a form is the wrong trade.

    So the status written here is `saved`, NOT `connected` — the distinction this file already
    draws for the mailbox (`put_email`), for exactly this reason. A screen that says "connected"
    about something nobody has tested is the claim this repo has spent whole nights deleting. The
    watchdog's backend probe is what turns `saved` into a verdict, on the box, against the real CLI.
    """
    value = validate(CLAUDE_OAUTH, value)
    # NOTHING IS REFUSED HERE, AND THAT IS THE RULING. An earlier cut of this made the tick a
    # CONDITION and would not store a token without it. The owner ruled otherwise on 2026-09-18: we
    # do not police what a person does with property they bought, the terms of their own subscription
    # are theirs to review, and what we owe them is the information — in his words, "a link to
    # Anthropic terms and to OpenAI terms right there on the page."
    #
    # THE QUOTE IS TRIMMED ON PURPOSE AND THE RULING IS NOT. `core/` ships wholesale into every box, so
    # a buyer who opens the source of the thing they own reads these comments — which is a line we sell
    # on. His decision belongs here; his phrasing that afternoon does not.
    #
    # He is right about whose box it is. A buyer owns this machine outright; a vendor that refused
    # to store their own credential until they ticked our box would be holding their property
    # hostage to our comfort. What we owe them is the INFORMATION — both vendors' terms, one click
    # away, at the moment they are deciding — and that is what the screen now gives.
    put(CLAUDE_OAUTH, value, user_id=user_id)
    # The tick is still RECORDED when it is given, because it costs the buyer nothing and answers
    # the question later if anyone asks it. It is never required, and never checked before a store.
    if consented:
        put(CLAUDE_OAUTH_CONSENT, f"{state._now()} {user_id or 'unknown'}"[:300], user_id=user_id)
    put(CLAUDE_OAUTH_STATUS, "saved", user_id=user_id)
    clear(CLAUDE_OAUTH_DETAIL, user_id=user_id)
    # THE TWO CREDENTIALS ARE EXCLUSIVE, AND LEAVING THE OLD ONE BEHIND IS HOW A BOX ENDS UP
    # THINKING ON A BILL ITS OWNER THOUGHT THEY HAD STOPPED USING. Someone moving from an API key
    # to their subscription means it; the key does not sit there as a silent fallback.
    for name in (ANTHROPIC, ANTHROPIC_STATUS, ANTHROPIC_DETAIL):
        clear(name, user_id=user_id)
    log.info("box_secret.claude_oauth_saved", user=user_id)


def note_claude_oauth_status(status: str, detail: str = "", *, user_id: str | None = None) -> None:
    """What the box learned about that token when it actually tried to think with it."""
    if status not in ("saved", "connected", "needs_reauth", "no_cli"):
        raise ValueError(f"unknown claude_code status {status!r}")
    put(CLAUDE_OAUTH_STATUS, status, user_id=user_id)
    if detail and status != "connected":
        put(CLAUDE_OAUTH_DETAIL, detail[:300], user_id=user_id)
    else:
        clear(CLAUDE_OAUTH_DETAIL, user_id=user_id)


def clear_claude_oauth(*, user_id: str | None = None) -> None:
    """Forget the subscription token — the box falls back to an API key, or to nothing."""
    for name in (CLAUDE_OAUTH, CLAUDE_OAUTH_STATUS, CLAUDE_OAUTH_DETAIL, CLAUDE_OAUTH_CONSENT):
        clear(name, user_id=user_id)


def claude_oauth_state() -> dict:
    """What the set-up and settings screens read. `saved` until something has really thought."""
    return {"status": get(CLAUDE_OAUTH_STATUS) or ("saved" if get(CLAUDE_OAUTH) else ""),
            "detail": get(CLAUDE_OAUTH_DETAIL)}


def put_ai_credential(value: str, *, consented: bool = False,
                      user_id: str | None = None) -> str:
    """ONE FIELD, EITHER CREDENTIAL. Returns which path was taken: "subscription" or "api".

    The buyer knows whether they have an API key or a Claude subscription; they should not have to
    tell a form which, when the credential itself says so. `sk-ant-oat...` is a subscription token
    and nothing else is, so the routing is exact rather than a guess.
    """
    if looks_like_subscription(value):
        put_claude_oauth(value, consented=consented, user_id=user_id)
        return "subscription"
    # AN API KEY IS NOT GATED, and letting the tick leak across to it would be a worse bug than a
    # missing gate: it would turn a box a buyer is entitled to use into one that refuses them over
    # a condition that does not apply to their credential at all.
    put_anthropic(value, user_id=user_id)
    return "api"


def oauth_consent_record() -> str:
    """When the subscription tick was given and by whom, for whoever has to answer that later."""
    return get(CLAUDE_OAUTH_CONSENT)


def codex_home() -> str:
    """Where the Codex CLI keeps the buyer's ChatGPT sign-in. NEVER under /tmp: the CLI refuses to
    place helpers under a temporary directory and warns on every run (measured, 0.155.1)."""
    import os
    return (os.environ.get("AIOS_CODEX_HOME") or "/var/lib/aios/codex").strip()


def codex_status() -> str:
    """The recorded verdict on the ChatGPT sign-in, or "" when nobody has signed in."""
    import pathlib
    st = get(CODEX_STATUS)
    if not st:
        return ""
    if not (pathlib.Path(codex_home()) / "auth.json").is_file():
        return ""                      # the CLI's file is gone: whatever we recorded is history
    return st


def codex_connected() -> bool:
    """True when the box can draft on a ChatGPT subscription right now."""
    return codex_status() in ("connected", "saved")


def note_codex_status(status: str, detail: str = "", *, user_id: str | None = None) -> None:
    """What the CLI last said about the ChatGPT sign-in. Same three verdicts as Anthropic's."""
    if status not in ("connected", "needs_reauth", "payment_required"):
        raise ValueError(f"unknown codex status {status!r}")
    put(CODEX_STATUS, status, user_id=user_id)
    if detail and detail.strip():
        put(CODEX_DETAIL, detail[:300], user_id=user_id)
    else:
        clear(CODEX_DETAIL, user_id=user_id)


def note_codex_consent(*, user_id: str | None = None) -> None:
    """Write down that the tick was given for the ChatGPT sign-in. WHEN and WHO, nothing else.

    THE TWIN OF THE LINE IN `put_claude_oauth`, and deliberately a separate call rather than an
    argument on `note_codex_status`. The status is written by the CLI helper every time it learns
    something — including on a re-auth months later, where nobody has been shown a tick — and a
    consent row that could be rewritten by a background probe would record a decision the person
    never made twice over.
    """
    put(CODEX_CONSENT, f"{state._now()} {user_id or 'unknown'}"[:300], user_id=user_id)


def codex_consent_record() -> str:
    """When the ChatGPT tick was given and by whom, for whoever has to answer that later."""
    return get(CODEX_CONSENT)


def clear_codex(*, user_id: str | None = None) -> None:
    # THE CONSENT GOES WITH THE SIGN-IN IT WAS GIVEN FOR, exactly as `clear_claude_oauth` does it.
    # Leaving it behind would let the next person's sign-in inherit a tick somebody else gave.
    clear(CODEX_STATUS, user_id=user_id)
    clear(CODEX_DETAIL, user_id=user_id)
    clear(CODEX_CONSENT, user_id=user_id)


def note_anthropic_status(status: str, detail: str = "", *, user_id: str | None = None) -> None:
    """The drafter says why Anthropic turned it away, so a screen can say something actionable.

    CALLED ON REFUSAL, NEVER ON A BAD MINUTE. The drafter runs every few minutes on a box with no
    one watching; if a rate limit or a dropped connection wrote `needs_reauth` here, a buyer would
    come back to a box telling them their working key was broken. `core.brain._is_transient` is
    the one place that judgement lives and the caller asks it there.

    `connected` CLEARS THE DETAIL rather than writing one, exactly as `note_email_status` does —
    see the long note there for the sentinel bug that arrangement fixed."""
    if status not in ("connected", "needs_reauth", "payment_required"):
        raise ValueError(f"unknown anthropic status {status!r}")
    put(ANTHROPIC_STATUS, status, user_id=user_id)
    if detail and detail.strip():
        put(ANTHROPIC_DETAIL, detail[:300], user_id=user_id)
    else:
        clear(ANTHROPIC_DETAIL, user_id=user_id)


def clear_anthropic(*, user_id: str | None = None) -> None:
    """Turn drafting off — the key AND what the vendor last said about it.

    A STATUS LEFT BEHIND IS THE NEXT KEY'S PROBLEM. `clear_zernio` exists for exactly this reason
    and the failure is the same shape here: turn drafting off while the row reads `needs_reauth`,
    paste a fresh working key, and the screen greets it with the last one's refusal."""
    for name in (ANTHROPIC, ANTHROPIC_STATUS, ANTHROPIC_DETAIL):
        clear(name, user_id=user_id)


def anthropic_state() -> dict:
    """What the set-up screen says about the AI key. Never the key itself.

    STILL TWO STATES, FOR A DIFFERENT REASON THAN WHEN THIS WAS WRITTEN. It used to say only two
    were honest because an Anthropic key was never verified at all — `put` checked its shape and
    stopped, so "connected" meant no more than "something key-shaped is in the table". Since
    `put_anthropic` the vendor is asked before the row is written, so `connected` now means the
    same thing it means under the mailbox and Zernio: this credential worked.

    WHAT IS STILL MISSING IS THE LATER REFUSAL, and it is worth naming rather than implying. The
    mailbox has `note_email_status` and Zernio has `note_zernio_status`, so a poller that gets
    turned away can move the row to `needs_reauth` days after it was set. Nothing writes an
    equivalent for this key yet, so a key revoked or run out of credit NEXT week still reads
    `connected` here. That is a following change, not a state to invent now — a third status no
    code can ever produce is a screen that lies in a new direction.

    ENVIRONMENT COUNTS AS CONNECTED, because `brain` uses it: `anthropic_key()` reads `.env` ahead
    of the store, so a box whose key is in its environment IS able to draft. A screen that showed
    "not connected" there would send its owner to buy a key he already has, and the row would stay
    red forever no matter what he pasted.
    """
    # THE STEP IS ONE STEP AND IT TAKES TWO CREDENTIALS, so this must ask about BOTH. Measured
    # 2026-09-18 by posting a real subscription token through the real form: it stored, the brain
    # switched to claude_code, `can_think()` said ready — and this still returned `not_connected`,
    # because it asked only about the API key. So the screen read "Not connected yet" and the
    # dashboard kept counting three things to connect, over a box that was working.
    #
    # THAT IS THE WORST SHAPE A BUG CAN HAVE ON THIS SCREEN: the product works and tells its owner
    # it does not. He pastes the token he was asked for, nothing on screen changes, and the only
    # sane conclusion is that it failed. He would have filmed exactly that.
    # A CHATGPT SIGN-IN IS AN AI ACCOUNT TOO, and this is the one reader every screen asks, so it
    # answers for all of them. The Claude token still wins in `brain._backend()` when both exist;
    # here the question is only "can this box draft", and the answer is the same.
    cst = codex_status()
    if cst and not claude_oauth_token() and not anthropic_key():
        return {"status": "connected" if cst == "saved" else cst, "user": None,
                "detail": get(CODEX_DETAIL), "provider": "chatgpt"}
    if claude_oauth_token():
        st = get(CLAUDE_OAUTH_STATUS)
        # `saved` is this store's honest word for "not tested yet" — see `put_claude_oauth`. The
        # SCREEN's vocabulary is a closed set that has no such member, and inventing one here is a
        # blank row in front of a paying customer. It is connected as far as this screen is
        # concerned: the credential is stored and the brain is using it.
        return {"status": "connected" if st in ("saved", "connected", "", None) else st,
                "user": None, "detail": get(CLAUDE_OAUTH_DETAIL)}
    if not anthropic_key():
        return {"status": "not_connected", "user": None, "detail": ""}
    # A KEY WITH NO STATUS ROW IS CONNECTED, and that default is what keeps every box already in
    # the field working. A key in `.env` never passes through `put_anthropic`, and so does a key
    # stored before this existed; neither has a row, and both drive a box that drafts perfectly
    # well. Defaulting the other way would paint a working box red and send its owner shopping.
    return {"status": get(ANTHROPIC_STATUS) or "connected", "user": None,
            "detail": get(ANTHROPIC_DETAIL)}


# ── THE SET-UP SCREEN, IN THE ORDER THE OWNER ASKED FOR ─────────────────────────────────────────
#
# THE DATA IS OSDev1's, FROM #1250, CARRIED VERBATIM. He cites the owner for the order — Gmail
# first with its fields and instructions, then the Zernio key — and I have not reworded a
# sentence of it. What changed is only what it is WIRED to: #1250 predates #1245, so its own
# `put_zernio` was a shape check and its status came from `is_set(ZERNIO)`. Both of those now
# exist on main in a better form (verified with the vendor), and OSDev4's instruction on the wall
# was explicit: build setup_state() OVER them rather than re-adding the constants. So this is his
# contract on their credentials, and #1250 no longer has to fight #1245 over the same lines.
#
# ONE SHAPE FOR EVERY CREDENTIAL, on purpose. Three different vendors, three different kinds of
# secret, and a buyer who does not care which is which — they care that it is a field with a
# number beside it and a sentence telling them where to get the thing. A screen that loops over
# this cannot drift between sections.
#
# IT NEVER CARRIES A SECRET. Every entry says whether something is set, never what it is.

# WHICH SCREEN A STEP BELONGS TO — `surface`, and it is the reason this contract can be split at
# all. Owner, 2026-09-22: "Step three and step four should be a core setting. The LLM and the
# mobile." A step is "machine" (the default, and what every step was before today) or "box".
#
# THE TEST OF IT IS ONE QUESTION: would a box running a DIFFERENT machine still need this? A
# mailbox and a set of social accounts belong to the inbox and go with it. An AI account is what
# `core.brain` reasons through for every machine; a mobile belongs to a person; an AI coworker is a
# `core.connector` seat. Those three are the box's and are answered in the box's own drawer.
#
# A STEP WITHOUT THE KEY IS A MACHINE STEP. Older machines and every seam step registered before
# today carry no `surface`, and they must keep rendering exactly where they always did — a default
# that silently moved somebody's step into core's Settings would be the worst kind of quiet.
SURFACE_BOX = "box"
SURFACE_MACHINE = "machine"


def surface_of(step) -> str:
    """Which screen renders this step. Unknown or missing reads as the machine's — never the box's."""
    return SURFACE_BOX if str((step or {}).get("surface") or "") == SURFACE_BOX else SURFACE_MACHINE


SETUP_STEPS = (
    {"key": "email", "title": "Your inbox", "surface": SURFACE_MACHINE,
     "why": "Ownbox reads the mail your customers send you, and drafts replies. It never sends "
            "anything and it never marks a message as read.",
     # NOT ONLY GMAIL (#1483 finding 1). The provider is a choice, Gmail first because it is the
     # commonest; "Another provider" takes the IMAP server from the field under it. The steps below
     # stay Google's — most buyers are there — and every other provider has its one line in
     # `alternatives`, with Microsoft's honest refusal as `caveat`.
     "fields": ({"name": "provider", "label": "Where your email lives", "type": "select",
                 "options": tuple((k, v[0]) for k, v in _mailbox_providers.PRESETS.items())},
                {"name": "host", "label": "IMAP server — only if you chose Another provider",
                 "type": "text", "placeholder": "imap.example.com"},
                {"name": "user", "label": "The email address to read", "type": "email",
                 "placeholder": "you@yourcompany.com"},
                {"name": "password", "label": "App password", "type": "password",
                 "placeholder": "the app password for this mailbox"}),
     "steps": ("In your Google Account, open Security and turn on 2-Step Verification.",
               "Search that page for App passwords and open it — or use the link below.",
               "Create one — name it Ownbox — and Google shows sixteen letters.",
               "Paste those here with your email address. The spaces do not matter."),
     # ALWAYS LIVE, unlike `link` (which waits for a key): this is where the password comes FROM.
     # Verified 2026-09-23 against Google's own help article (support.google.com/accounts/answer/
     # 185833, "Create and manage your app passwords"). tests/test_setup_asks_for_the_ai_key.py
     # holds every set-up URL to that standard.
     "help": {"label": "Open Google's App passwords page",
              "url": "https://myaccount.google.com/apppasswords"},
     "alternatives": tuple((v[0], v[2]) for k, v in _mailbox_providers.PRESETS.items()
                           if k != "gmail"),
     "alternatives_title": "Not on Gmail?",
     "caveat": _mailbox_providers.MICROSOFT_SAID,
     "caveat_title": "Outlook, Hotmail or Microsoft 365",
     "note": "If App passwords is missing, your Google administrator has switched it off for your "
             "organisation — ask them to allow it."},
    # NAME THE THING BEFORE ASKING FOR A KEY TO IT. Owner, 2026-09-19: "does that screen explain
    # that it's a necessary step and how to do it? We should explain that that's the API we used to
    # run the communications." It did not. It said messages "reach the box through a Zernio
    # account" and then asked for an API key — which reads, to anyone who has not heard the name,
    # as a paid account for reasons nobody gave. It also read as COMPULSORY: the page says "three
    # things only you can do", and nothing said a box with only Gmail connected is a finished box.
    # It is: `inbox/poller.py` sweeps email on its own credential and says so in its own words —
    # "a buyer who connects Gmail and nothing else is a box with no Zernio key at all".
    {"key": "zernio", "title": "Your social accounts", "surface": SURFACE_MACHINE,
     "why": "Skip this if email is all you need — your box is already finished without it. "
            "Zernio is the service that carries Instagram, Facebook and TikTok messages: those "
            "networks do not hand direct messages to just anybody, so a licensed one relays them, "
            "and Zernio is the one this box speaks. The account is yours, on your own bill, so "
            "your conversations are never on ours.",
     "fields": ({"name": "key", "label": "Zernio API key", "type": "password",
                 "placeholder": "paste the key from your Zernio account"},),
     "steps": ("Create a Zernio account, or sign in to the one you have.",
               "Add a payment method — connecting accounts is billed to you, not to us.",
               "Open API keys, create one, and paste it in the field here.",
               "Then use the button below: it opens Zernio in a new tab, and you connect "
               "Instagram, Messenger, TikTok or any other account there."),
     # THE CONNECTING HAPPENS IN ZERNIO, NOT HERE (owner, 2026-09-16). OAuth with Instagram belongs
     # to the vendor's own screens: they hold the app registration, the consent screen and the
     # tokens. A link out in a NEW TAB is the honest shape — the buyer keeps this page open, does
     # the consent where it actually lives, and comes back to a row that now says connected.
     # A DEEP LINK ONLY WHEN SOMEBODY HAS ACTUALLY SEEN IT. This was `https://zernio.com/accounts`,
     # a path nobody had ever fetched, and the owner hit the 404 mid-onboarding on 2026-09-21 — on
     # the step whose entire job is to send him somewhere, at the moment it was asking him for an
     # API key. `marketing/customer_voice/app.py` already carried the warning ("the site root, not
     # a guessed deep link"); this file had guessed anyway.
     #
     # The URL below is the one the OWNER read off his own Zernio dashboard the same day, which is
     # why it is allowed to be a path at all. `tests/test_setup_asks_for_the_ai_key.py` holds the
     # rule: an outbound set-up link is a site root UNLESS it is on the verified list there, and
     # adding to that list means somebody loaded the page.
     "link": {"label": "Connect your accounts in Zernio",
              "url": "https://zernio.com/dashboard/connections",
              "new_tab": True,
              # THIS SENTENCE WAS FALSE AND THE OWNER CAUGHT IT MID-ONBOARDING (2026-09-21):
              # "connected accounts appear here" — they never could. This step binds to
              # `zernio_state()`, which reads the KEY and its status and nothing else; it has no
              # idea what accounts exist and makes no network call. The screen that lists them is
              # Settings -> Add or remove a channel, which calls `accounts.discover()`.
              #
              # SAY WHERE, NOT "HERE". A buyer who comes back and sees nothing concludes the
              # connection failed and re-does work that already worked.
              "after": "When you are done, your connected accounts show up under Settings, on "
                       "'Add or remove a channel' — not on this page."},
     "note": "A key with no payment method on the account still saves, and says so: connecting an "
             "account is what needs the card, not the key. Leaving this step alone costs you "
             "nothing and breaks nothing — email keeps arriving either way."},
)


# WHY THIS ONE STAYS IN CORE while `core/onboarding.py` exists to move steps OUT of it. That seam
# is for a MACHINE's credential — the inbox's mailbox, the inbox's Zernio key — so that adding a
# machine stops meaning editing core. The AI key belongs to no machine: `core.brain` is the one
# gateway every machine reasons through, and a box with no key cannot draft, summarise or route
# anything, whichever machines it carries. There is nothing to register it, so core asks for it.
# ── WHICH MODEL WRITES, AND WHICH ASSISTANTS CAN CONNECT ─────────────────────────────────────
# TWO DIFFERENT QUESTIONS, and conflating them is how a screen starts lying. The box CALLS the
# drafting model, so that list is limited by what `core/brain.py` can actually talk to — today,
# Anthropic and OpenAI. The assistants in `AGENT_CLIENTS` call the BOX, over MCP, so that list is
# limited only by which of them speak MCP — and all four do, today, with no work from us.
#
# THE GREYED-OUT ONES ARE MARKED `available: False` AND THE SCREEN MUST HONOUR IT. Owner,
# 2026-09-21, asked for the ones that do not work yet shown greyed out, which is honest: it says
# where this is going without claiming to be there. What would NOT be honest is a selectable
# option that stores a key and then never drafts a single reply, which is what an un-greyed list
# would be until `brain.py` grows the road behind it.
#
# THE PAGE CHANGES WITH THE PICK, WHICH IS WHY EACH ENTRY CARRIES ITS OWN COPY. Owner,
# 2026-09-22: *"the page is gonna have to be completely different according to which drop-down is
# chosen… do the research and start writing a copy for each selection."* Connecting these four is
# not one flow with four logos on it — it is four genuinely different errands, and the difference
# is worth telling a buyer BEFORE they go looking:
#
#   Claude   the subscription you already pay for can drive this box. Sign in, no key.
#   ChatGPT  the same answer by a different road — the subscription drives the box through the
#            Codex CLI's device sign-in, so there is no key here either.   (#1423, 2026-09-22)
#   Gemini   Google AI Pro/Ultra do NOT include API access, and there is no equivalent sign-in;
#            a key from AI Studio has a real free tier and needs no card.
#                                                          (ai.google.dev, checked 2026-09-22)
#   Grok     X Premium and SuperGrok do NOT include API access either. console.x.ai is its own
#            billing, with promotional credits to start.    (docs.x.ai, checked 2026-09-22)
#
# THIS TABLE WAS WRONG FOR A DAY AND THE WRONG VERSION READ EXACTLY LIKE RESEARCH. Written on
# 2026-09-22, the ChatGPT row said a Plus subscription cannot drive a box and a developer key is
# the only way in. That was true of OpenAI's *API*, which is what had been checked, and it was
# false about this box within hours, because #1423 landed a sign-in the pricing page does not
# describe. The lesson is not "check the vendor docs" — they were checked. It is that a fact
# about a VENDOR stops being a fact about THIS BOX the moment `brain.py` grows a road the vendor
# never advertised, so every claim below is pinned to what `core/brain.py` can do, and
# `tests/test_the_ai_screen_answers_for_four_models.py` re-checks that pin rather than the copy.
#
# `available` STILL GATES THE CREDENTIAL, AND NOTHING BELOW LOOSENS IT. The picker is selectable
# for all four so the screen can answer "what would this look like" — the box refuses to take a
# key it cannot draft with, which is the opposite of the fake feature the owner named on
# 2026-09-21. `tests/test_setup_asks_for_the_ai_key.py` holds that line against `core/brain.py`.
DRAFTING_MODELS = (
    {"id": "claude", "name": "Claude", "available": True,
     "note": "Sign in with a Pro or Max subscription, or paste an Anthropic key.",
     "vendor": "Anthropic",
     "subscription": True,
     # WHERE PICKING THIS ONE TAKES YOU. Claude is connected on /settings/ai itself, so the screen
     # draws its own cards rather than sending anybody anywhere.
     "connect_href": "/settings/ai",
     "lede": "The one you already pay for. A Claude Pro or Max subscription signs in here and "
             "writes your drafts on it — there is no key to find and nothing to install.",
     "billing": "Your own Claude subscription, or an Anthropic API account if you would rather "
                "pay per word. Your box keeps a hard spending cap on top of either.",
     "key_label": "Anthropic API key or Claude subscription token",
     "key_from": ("Anthropic Console", "https://console.anthropic.com/settings/keys"),
     "key_prefix": "sk-ant-...",
     "steps": ("Press Connect. The box opens a sign-in at claude.com in a new tab.",
               "Sign in and approve access. Claude shows you a short code.",
               "Paste the code back here. That is the whole of it.")},
    {"id": "openai", "name": "ChatGPT", "available": True,
     "note": "Sign in with your ChatGPT subscription — a one-time code, no key.",
     "vendor": "OpenAI",
     # A SUBSCRIPTION DRIVES THIS ONE TOO, which is the fact this row got wrong for a day. The
     # road is the Codex CLI's device sign-in, not the API: `core/codex_login.py` runs it on the
     # box and `core/brain.py:_think_codex` drafts through `codex exec` afterwards.
     "subscription": True,
     "connect_href": "/settings/chatgpt",
     "lede": "The ChatGPT subscription you already pay for. The box shows you a link and a "
             "one-time code; you sign in on your own phone or computer and enter the code "
             "there. No key, and the box never sees your password.",
     "billing": "Your own ChatGPT Plus, Pro or Business subscription — nothing extra, and no "
                "prepaid API credits. Your box keeps a hard spending cap on top of it.",
     # NO `key_label`, `key_from` OR `key_prefix`, AND THAT ABSENCE IS THE DESIGN. There is no key
     # to paste for ChatGPT on this box; a field offered here would be a place to put something
     # that can never be used. The screen draws a key form only for a model that declares one.
     "steps": ("Press Connect. The box shows you a link and a short code.",
               "Open the link on any device, sign in to ChatGPT, and enter the code.",
               "Nothing comes back here — the box notices by itself and returns you to "
               "settings."),
     # THE ONE THING THAT GOES WRONG, SAID BEFORE IT DOES. Device-code sign-in is off by default
     # on some accounts; `codex_login` maps the CLI's refusal to this sentence, and the screen
     # says it up front so a buyer is not sent to support for a switch they own.
     "gotcha": "If ChatGPT refuses the code, turn on device code sign-in under Settings → "
               "Security. A work account needs an administrator to allow it."},
    {"id": "gemini", "name": "Gemini", "available": False,
     "note": "Coming soon — the box cannot draft on Gemini yet.",
     "vendor": "Google",
     "subscription": False,
     "lede": "Google AI Pro and Ultra do not include API access, and there is no sign-in like "
             "Claude's or ChatGPT's. Gemini would draft on a key from Google AI Studio instead — "
             "and that one has a real free tier, so it asks for no card at all.",
     "billing": "Free to start, with daily limits. Paid usage runs through Google Cloud billing "
                "if you outgrow them.",
     "key_label": "Google AI Studio API key",
     "key_from": ("Google AI Studio", "https://aistudio.google.com/apikey"),
     "key_prefix": "AIza...",
     # THE LAST STEP DOES NOT SAY "PASTE IT HERE". There is no field on this panel and there
     # must not be one, so an instruction to paste sends a buyer hunting for a box that was
     # deliberately left out. Read off the rendered page, 2026-09-22.
     "steps": ("Sign in at aistudio.google.com with a Google account.",
               "Open Get API key and create one. No card is asked for.",
               "Keep it somewhere safe. This box will ask you for it the day it can draft on "
               "Gemini — and not before.")},
    {"id": "grok", "name": "Grok", "available": False,
     "note": "Coming soon — the box cannot draft on Grok yet.",
     "vendor": "xAI",
     "subscription": False,
     "lede": "X Premium and SuperGrok are the chat app; neither includes API access. Drafting on "
             "Grok needs a key from xAI's own console, billed on its own account.",
     "billing": "Prepaid credits at console.x.ai, usually with a promotional balance to start.",
     "key_label": "xAI API key",
     "key_from": ("xAI Console", "https://console.x.ai"),
     "key_prefix": "xai-...",
     "steps": ("Sign in at console.x.ai. It is self-serve — there is no waiting list.",
               "Create an API key.",
               "Keep it somewhere safe. This box will ask you for it the day it can draft on "
               "Grok — and not before.")},
)

# EVERY ONE OF THESE IS LIVE. They are MCP clients: they connect to the box's own address with a
# key this box mints, and the box neither calls them nor holds anything of theirs. Adding a fifth
# is a row here, not a feature.
AGENT_CLIENTS = (
    {"id": "claude", "name": "Claude",
     "how": "Settings \u2192 Connectors \u2192 Add custom connector. Paste the address, choose "
            "\u201cSign in now\u201d, then Connect."},
    {"id": "chatgpt", "name": "ChatGPT",
     "how": "Settings \u2192 Connectors \u2192 Add. Paste the address and let it sign in."},
    {"id": "gemini", "name": "Gemini",
     "how": "Add the address as an MCP server and let it sign in."},
    {"id": "grok", "name": "Grok",
     "how": "grok.com/connectors \u2192 New Connector \u2192 Custom. Paste the address and let it "
            "sign in."},
)

# THE THREE STEPS EVERY ONE OF THEM SHARES, in the order a person does them. Written after
# watching the owner do it on 2026-09-22 — he got through it, and then asked for the
# instructions to be changed, which is the most useful kind of pass.
AGENT_STEPS = (
    "Copy the address of this box.",
    "In your assistant, add a connector or MCP server and paste it. Pick the option that says "
    "it will SIGN IN \u2014 not \u201cno sign-in\u201d, and not an API key.",
    "It sends you here to approve. Press Allow, then allow the tools your assistant asks about.",
)


def agent_state() -> dict:
    """Whether any AI coworker has been given a key to this box yet."""
    try:
        from core.connector import seats
        live = [s for s in seats.all_seats() if not s.get("revoked_at")]
    except Exception:                                    # noqa: BLE001 — a step must never 500 set-up
        return {"status": "not_connected", "detail": ""}
    if not live:
        return {"status": "not_connected", "detail": ""}
    return {"status": "connected",
            "detail": ", ".join(str(s.get("label") or "") for s in live[:3])}


_AGENT_STEP = {
    "key": "agent", "title": "Your AI coworkers",
    "surface": SURFACE_BOX,
    # CORE CAN VOUCH FOR ITS OWN DOOR, which is the whole point of the door having moved. This
    # step mints a credential that reads every message on the box, so it is the owner's alone —
    # and saying so HERE, as data, is what lets a screen decline to offer it to a member instead
    # of offering it and then refusing them at it. That dead end was real: a member pressed "Set
    # up" on /settings and met a 403 (caught by test_a_buyer_can_walk_every_screen).
    "owner_only": True,
    # EVERY ENTRY CARRIES `fields`, EMPTY OR NOT. `app._setup_source()` requires the five keys of
    # the contract on every step, and tests/test_setup_sources_merge.py asserts it — a step is a
    # shape, not a special case. This one has no field to type into: the key is MINTED on
    # the step's own screen and shown once, never pasted in.
    "fields": (),
    # THE CONTRACT WANTS `steps` TOO, and this step has real ones — the key is minted here and
    # pasted into the assistant, which is the reverse of every other step on this screen.
    "steps": ("Press the button below — it mints a key for this box and shows it once.",
              "Choose what that coworker may do: read only, or read and draft replies.",
              "Copy the key and this box's address into your assistant's connector settings.",
              "Revoke it whenever you like; the box keeps working, it just stops answering them."),
    "why": "Claude, ChatGPT, Gemini or Grok can read this inbox and draft replies for you, in "
           "whichever one you already pay for. You paste this box's address into your assistant, "
           "approve it here, and that is the whole of it \u2014 there is no key to copy and "
           "nothing to keep safe. They can read your conversations and leave draft replies "
           "waiting for you; none of them can send anything as your business, whatever you "
           "allow. Take any of them back whenever you like.",
    "steps": AGENT_STEPS,
    "optional": True,
    "link": {"label": "Connect an AI coworker",
             "url": "/settings/agent",
             "new_tab": False,
             "after": "The key is shown once. If you lose it, revoke it and make another."},
    "note": "This is the MCP address for this box and nobody else's — your conversations never "
            "pass through Ownbox to get there.",
}


_AI_STEP = {
    # TITLED FOR THE ANSWER, NOT THE FALLBACK. It read "Your AI key" while the button beneath it
    # said "Sign in to Claude", which is the contradiction the owner named in the first place —
    # "There's no key. It's a login." A heading that argues with its own primary control is the
    # thing a person reads first and the thing they film. The step's identity stays `anthropic`;
    # only the words a buyer sees change.
    "key": "anthropic", "title": "Your AI account",
    "surface": SURFACE_BOX,
    # Connecting this bills the whole box and every machine on it drafts through it, so it is the
    # owner's. See `_AGENT_STEP["owner_only"]` for why the gate is data rather than a 403.
    "owner_only": True,
    # DECLARED BY THE STEP, DRAWN BY THE RENDERER — never `if key == "anthropic"` in the screen.
    # test_setup_screen_loop refuses a `_setup_step` that names a step, and it is right to: the
    # whole point of the contract is that a machine can add a step without editing the renderer.
    # A picker is data like a field is data.
    # THE NOTE HAD TO CHANGE WITH THE SCREEN. It read "Only the ones your box can actually use
    # are selectable", which was true of the machine's greyed-out picker and became a lie the
    # moment core's picker let you choose one to read about. A caption that argues with the
    # control beneath it is worse than no caption — found by rendering, 2026-09-22.
    "choose": {"label": "Which model writes your drafts",
               # WHAT THIS SENTENCE MUST NOT DO IS COUNT. It said "Claude works today" and was
               # stale the same day ChatGPT went live; naming the live ones here means two
               # places to keep in step, and the one nobody updates is the one buyers read. The
               # picker already marks each option, so this says only what the OTHERS are for.
               "note": "Every one of these can be picked. The ones your box cannot draft with "
                       "yet show you what connecting them will involve and take nothing from "
                       "you.",
               "options": DRAFTING_MODELS},
    # "YOUR OWN ACCOUNT", NOT "YOUR OWN KEY". This sentence sits above every model's panel, and
    # two of the four are now a sign-in with no key in them anywhere — a buyer reading "your own
    # key" on the ChatGPT page goes looking for one that does not exist. Found by rendering the
    # page and reading it, 2026-09-22.
    "why": "This is what writes the replies. Your box drafts on your own account and your own "
           "bill, so your customers' messages are never on anybody else's account. Nothing is "
           "sent automatically — the box writes, you read it, and you decide.",
    "fields": ({"name": "key", "label": "Anthropic API key or Claude subscription token",
                "type": "password", "placeholder": "sk-ant-..."},),
    # THE BUTTON IS THE ANSWER FOR A SUBSCRIPTION; THE FIELD IS THE FALLBACK. Owner, 2026-09-18:
    # "What am I going to click on to authorize Claude subscription? There's no key. It's a login."
    # He was right. Asking for an sk-ant-oat token asks a person to install a CLI and run a command
    # on their laptop to produce the OUTPUT of a login — which is a wall, not onboarding. The box
    # runs that login itself now and hands them a link (`core/claude_login.py`).
    "action_href": "/settings/ai",
    "action_label": "Sign in to Claude",
    # THE SECOND DOOR, SAME SHAPE, drawn by /settings/ai under the first. Owner, 2026-09-21: "We
    # need the Claude login. And then we're gonna need the ChatGPT login right behind it."
    "alt_action_href": "/settings/chatgpt",
    "alt_action_label": "Sign in to ChatGPT",
    "alt_action_why": "Have a ChatGPT subscription instead? Sign in with a one-time code on your "
                      "phone or computer — no key, and the box never sees your password.",
    "action_why": "Have a Claude Pro or Max subscription? Sign in and this box drafts on it — no "
                  "key to find, nothing to install. You sign in at claude.com; the box never sees "
                  "your password.",
    "steps": ("If you have a Claude Pro or Max subscription, use Sign in to Claude above. It takes "
              "about twenty seconds and there is no key to find.",
              "If you have an Anthropic API account instead: open API keys, create one, add a "
              "payment method, and paste the sk-ant-api… value below. Drafting is billed to you.",
              "Already generated a subscription token yourself? Paste it below — the field takes "
              "either, and your box can tell which you gave it.",
              "Your box keeps a hard spending cap on top of whatever you set at Anthropic."),
    # HIS WORDS, HIS RULING, AND THE LINK IS THE POINT OF IT. Owner, 2026-09-18: "that should be
    # the choice of the client... they have to review their own terms and be responsible for
    # themselves. We are not the police" — plus "we can put a link to Anthropic terms so they can
    # review". So the box states the condition once, links the terms, and does not decide.
    "terms_url": ANTHROPIC_TERMS_URL,
    # THE WARNING, THE LINK AND THE TICK — the shape legal counsel asked for (owner, 2026-09-18).
    # It is only reached by someone pasting a SUBSCRIPTION token; an API key carries no such
    # condition and must not be made to wade through one.
    "terms_warning": "This is your box and your account — how you use your own subscription is "
                     "between you and your provider. Their terms are linked here so you can "
                     "check yours in particular before you connect it.",
    "terms_links": TERMS_LINKS,
    "consent_field": "subscription_consent",
    "consent_label": "I have reviewed my provider's terms and want to use my subscription with "
                     "this box.",
    "terms_note": "Optional — nothing here is withheld either way. If you would rather we set "
                  "this up for you, write to help@ownbox.io.",
    "note": "Without this the box still reads everything and still shows you every message — it "
            "simply will not write the drafts.",
}



# NOTIFICATIONS ON THE MOBILE APP — A CORE STEP, FOR EVERY MACHINE. Owner, 2026-09-20, ruling on where it
# belongs: "Yes in onboarding, all machines will need this." The inbox is what rings first, but a
# Lead box with a hot prospect wants a mobile rung too, so this sits in core beside the AI account
# rather than inside customer_voice.
#
# INSTALLING IS HERE; ASKING IS NOT. The owner's other ruling the same day: ask for permission
# "after the buyer has seen their first real message, never on first load." Those are not in
# tension — they are two different acts with opposite costs. Adding the app to a Home Screen is
# free and reversible, and on iPhone NOTHING can ring without it, so it belongs in onboarding at
# the start. The permission prompt is close to permanent if denied, so it waits for a moment that
# has earned it. This step therefore teaches the install and never fires a prompt.
#
# WHAT THE SERVER CAN HONESTLY SAY IS LIMITED, and the status here reflects that. A subscription
# row proves SOME device is set up, never that the one in your hand is: the same person reading
# this on a laptop has a mobile the box cannot see. So the status answers "has anybody here got
# this working", and the page refines the line for the device actually looking at it.
_MOBILE_STEP = {
    "key": "mobile", "title": "Your mobile app",
    "surface": SURFACE_BOX,
    # NOT OWNER-ONLY, AND DELIBERATELY SO. A mobile belongs to a person, not to whoever bought the
    # box: a member working the inbox all day needs the notification more than the owner does, and
    # `push.subscriptions_for` is keyed to whoever is signed in and never crosses users.
    "owner_only": False,
    "action_href": "/settings/mobile",
    "action_label": "How to install the mobile app",
    "action_why": "It takes about thirty seconds and there is nothing to type. The app has to be "
                  "installed before it can notify you at all.",
    "why": "Install this box as an app on your mobile and it can notify you when a customer writes — "
           "a notification that opens straight into your inbox, not into somebody else's app. "
           "Email keeps working either way; this is the faster way to hear about it.",
    "fields": (),
    # PER PLATFORM, IN THE CONTRACT, because two screens render these words: the step's own page
    # and the sheet a colleague is handed. A second copy laid out for paper is a second copy that
    # DRIFTS, and the one that drifts is the printed one — nobody re-reads a page they already
    # pinned to a wall. So the instructions live here, once, and both surfaces draw them.
    "platforms": ({"title": "On an iPhone or iPad",
                   "steps": ("Open this box in Safari. It has to be Safari — Chrome on an iPhone "
                             "cannot do this.",
                             "Tap the Share button (the square with an arrow coming out of it).",
                             "Scroll down and tap Add to Home Screen, then Add.",
                             "Open the box again from the new icon on your Home Screen."),
                   "note": "A Safari tab cannot receive notifications at all, whatever you answer "
                           "to the prompt. Only the installed app can."},
                  {"title": "On an Android mobile",
                   "steps": ("Open this box in Chrome.",
                             "Tap the ⋮ menu at the top right.",
                             "Tap Install app — or take the install banner if one appears.",
                             "Open the box again from the new icon."),
                   "note": "If you do not see Install app, the page is probably not open in "
                           "Chrome."}),
    "steps": ("On iPhone: open this box in Safari, tap the Share button, then Add to Home Screen. "
              "It has to be the installed app — a Safari tab cannot receive notifications at all.",
              "On Android: open the browser menu and choose Install app, or take the install "
              "banner when it appears.",
              "Open the box from the new icon. It fills the screen, with no browser bar.",
              "Turning notifications ON comes later, on the inbox itself, once you have had a "
              "message worth being told about."),
    # NOT STARTING WITH THE STEP'S OWN TITLE. It read "Your mobile asks you once" directly under a
    # heading that already said so — redundant to read, and it tripped the guard that
    # refuses anything rendered twice on the set-up page.
    "note": "iOS asks you once, and a refusal is hard to undo — so the box waits until it has "
            "something real to show you before it asks.",
    # IT COUNTS FOR NOTHING, AND THAT IS THE POINT. The other three steps are credentials only the
    # buyer can supply and without which the box cannot work; this one is an app the box may never
    # see installed, because email is the floor and plenty of people will never install anything. Counted
    # like the others it would hold every box at "3 of 4" forever — a permanent nag for declining
    # something optional, on a box that is finished. It renders in onboarding (owner, 2026-09-20:
    # "Yes in onboarding, all machines will need this") and is excluded from the count.
    "optional": True,
}


def mobile_state() -> dict:
    """Whether ANY device on this box has the app installed and notifications on — never yours.

    Guarded, because push is inert until `cryptography` is in the lock: a box that cannot mint an
    identity still renders this step, it simply cannot be finished yet.
    """
    try:
        from core import state
        with state.connect() as c:
            n = c.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0]
    except Exception:                                    # noqa: BLE001 — a screen must still draw
        return {"status": "not_connected"}
    return {"status": "connected" if n else "not_connected",
            "detail": f"{n} device{'s' if n != 1 else ''} set up" if n else ""}


SETUP_STEPS = SETUP_STEPS + (_AI_STEP, _MOBILE_STEP, _AGENT_STEP)

# ONE STATE READER PER STEP, BY NAME. This was `email_state() if key == "email" else zernio_state()`
# — a binary that was correct while there were exactly two steps and silently wrong the moment
# there was a third: the AI key would have rendered Zernio's status, so a buyer with a connected
# Zernio account would have read "Connected" under a key he had never pasted. A map cannot do that;
# an unknown key gets nothing rather than the last branch's answer.
_STATE_READERS = {
    "email": email_state, "zernio": zernio_state, "anthropic": anthropic_state,
    "mobile": mobile_state}


def setup_state() -> list[dict]:
    """Every credential the buyer supplies, in screen order, with what is set and what to do.

    The screen renders this list. It never renders a secret: each entry carries a status and a
    sentence, never a value.

    STATUS IS A CLOSED SET, AND IT IS WIDER THAN #1250 ASSUMED:
    not_connected | connected | needs_reauth | admin_disabled | payment_required.
    That last one arrives from `zernio_state()` and is the one a buyer can actually fix — Zernio
    caps connected accounts until a payment method is on the account and says so with a 402.
    #1250's docstring listed four, because it was written against `is_set(ZERNIO)`, which could
    only ever say yes or no. Leaving it at four would hand the screen a status it was told could
    not happen, which is how an unhandled state becomes a blank row in front of a paying customer.
    """
    out = []
    for step in SETUP_STEPS:
        reader = _STATE_READERS.get(step["key"])
        st = reader() if reader else {}
        entry = dict(step, status=st.get("status") or "not_connected",
                     who=st.get("user"), detail=(st.get("detail") or "").strip())
        # The link out is only useful once the key is in: sending someone to connect accounts
        # before the box can read them is a step they would have to repeat.
        if entry.get("link"):
            live = entry["status"] != "not_connected"
            entry["link"] = dict(entry["link"], enabled=live,
                                 disabled_because="" if live else
                                 "Paste your key first, then connect your accounts.")
        out.append(entry)
    return out
