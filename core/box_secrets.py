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
from core.logging import get_logger

log = get_logger(__name__)

ANTHROPIC = "anthropic_api_key"
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


def put_zernio(value: str, *, user_id: str | None = None) -> None:
    """Store the buyer's Zernio key. Raises SecretRejected with a sentence they can act on.

    VERIFIED WITH THE VENDOR, NOT MATCHED AGAINST A SHAPE. The Anthropic key above is checked by
    prefix because its shape is published and stable. Zernio's is not: it is 67 characters with no
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
    host = str(host or "").strip() or "imap.gmail.com"
    user = str(user or "").strip()
    password = _clean_app_password(password)
    if not user or "@" not in user:
        raise SecretRejected("Put in the full email address of the mailbox you want read.")
    if not password:
        raise SecretRejected("Paste the app password Google gave you.")
    if len(password) != _APP_PASSWORD_LEN or not password.isalnum():
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


def email_state() -> dict:
    """WHAT A SCREEN BINDS TO, and nothing a screen should not have: never the password.

    `status` is one of: not_connected | connected | needs_reauth | admin_disabled. The last two exist
    because Google revokes an app password whenever the account password changes, and because a
    Workspace administrator can switch them off entirely — both arrive as the same opaque IMAP refusal,
    and a buyer told "authentication failed" learns nothing they can act on."""
    cred = email_credential()
    if not cred:
        return {"status": "not_connected", "user": None, "detail": ""}
    return {"status": get(EMAIL_STATUS) or "connected", "user": cred.get("user"),
            "detail": get(EMAIL_DETAIL)}


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
    if name in (EMAIL, EMAIL_DETAIL, ZERNIO_DETAIL, ZERNIO_PROFILE):
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
    return value


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

def anthropic_state() -> dict:
    """What the set-up screen says about the AI key. Never the key itself.

    ONLY TWO STATES ARE HONEST HERE, and that is the finding rather than a shortcut. A mailbox and
    a Zernio key are VERIFIED with their vendor before they are stored, so their screens can say
    `needs_reauth` and mean it. An Anthropic key is not: `put` checks its shape and nothing more,
    deliberately — "only Anthropic can say whether a key works, and a box that refused a valid key
    because our regex was a month out of date would be worse than one that accepted a typo".
    So this answers `connected` or `not_connected` and never invents a third.

    ENVIRONMENT COUNTS AS CONNECTED, because `brain` uses it: `anthropic_key()` reads `.env` ahead
    of the store, so a box whose key is in its environment IS able to draft. A screen that showed
    "not connected" there would send its owner to buy a key he already has, and the row would stay
    red forever no matter what he pasted.
    """
    return {"status": "connected" if anthropic_key() else "not_connected",
            "user": None, "detail": ""}


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

SETUP_STEPS = (
    {"key": "email", "title": "Your inbox",
     "why": "Ownbox reads the mail your customers send you, and drafts replies. It never sends "
            "anything and it never marks a message as read.",
     "fields": ({"name": "user", "label": "The email address to read", "type": "email",
                 "placeholder": "you@yourcompany.com"},
                {"name": "password", "label": "App password", "type": "password",
                 "placeholder": "sixteen letters from Google"}),
     "steps": ("In your Google Account, open Security and turn on 2-Step Verification.",
               "Search that page for App passwords and open it.",
               "Create one — name it Ownbox — and Google shows sixteen letters.",
               "Paste those here with your email address. The spaces do not matter."),
     "note": "If App passwords is missing, your Google administrator has switched it off for your "
             "organisation — ask them to allow it."},
    {"key": "zernio", "title": "Your social accounts",
     "why": "Instagram and Facebook messages reach the box through a Zernio account. It is yours, "
            "on your own bill, so your conversations are never on ours.",
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
     "link": {"label": "Connect your accounts in Zernio",
              "url": "https://zernio.com/accounts",
              "new_tab": True,
              "after": "Come back to this page when you are done — connected accounts appear here."},
     "note": "A key with no payment method on the account still saves, and says so: connecting an "
             "account is what needs the card, not the key."},
)


# WHY THIS ONE STAYS IN CORE while `core/onboarding.py` exists to move steps OUT of it. That seam
# is for a MACHINE's credential — the inbox's mailbox, the inbox's Zernio key — so that adding a
# machine stops meaning editing core. The AI key belongs to no machine: `core.brain` is the one
# gateway every machine reasons through, and a box with no key cannot draft, summarise or route
# anything, whichever machines it carries. There is nothing to register it, so core asks for it.
_AI_STEP = {
    "key": "anthropic", "title": "Your AI key",
    "why": "This is what writes the replies. Your box drafts with your own key on your own bill, "
           "so your customers' messages are never on anybody else's account. Nothing is sent "
           "automatically — the box writes, you read it, and you decide.",
    "fields": ({"name": "key", "label": "Anthropic API key", "type": "password",
                "placeholder": "sk-ant-..."},),
    "steps": ("Create an Anthropic account, or sign in to the one you have.",
              "Add a payment method — the drafting is billed to you, not to us.",
              "Open API keys, create one, and copy it.",
              "Paste it here. Your box keeps a hard spending cap on top of whatever you set "
              "at Anthropic."),
    "note": "Without this the box still reads everything and still shows you every message — it "
            "simply will not write the drafts.",
}

SETUP_STEPS = SETUP_STEPS + (_AI_STEP,)

# ONE STATE READER PER STEP, BY NAME. This was `email_state() if key == "email" else zernio_state()`
# — a binary that was correct while there were exactly two steps and silently wrong the moment
# there was a third: the AI key would have rendered Zernio's status, so a buyer with a connected
# Zernio account would have read "Connected" under a key he had never pasted. A map cannot do that;
# an unknown key gets nothing rather than the last branch's answer.
_STATE_READERS = {"email": email_state, "zernio": zernio_state, "anthropic": anthropic_state}


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
