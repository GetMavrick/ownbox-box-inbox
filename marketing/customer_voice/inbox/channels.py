"""The channel vocabulary — the one place a channel's three names meet.

A channel is called three different things by three different systems, and every
bug this module exists to prevent is a place where two of them were assumed equal:

  `vendor`  what Zernio's `platform=` argument wants.  Messenger DMs are "facebook"
            (LIVE-VERIFIED: "messenger" → [400] PLATFORM_NOT_SUPPORTED, F-2), and IG
            DMs return ONLY under "instagram" (facebook → 0 conversations).
  `key`     what we STORE in `inbox_conversations.platform`, and therefore what the
            screen filters on, what `window._RULES` is keyed by, and what a chip
            renders from. NOT the vendor token: the store has said "messenger" since
            the first row and rewriting history to say "facebook" buys nothing.
  `label`   what a human reads, in Slack and on the screen — `NAMES` below.

WHY THIS IS A MODULE AND NOT THREE STRING LITERALS. Instagram is the second channel,
and the first one never needed a translation because a single channel cannot
disagree with itself. The moment there are two, "facebook" appears in the poll call
and "messenger" in the row it writes, and nothing in the code says those are the
same thing — so the next reader either guesses or greps. This file says it once.

THIS MODULE IS A LEAF ON PURPOSE. It imports nothing from the package (the package
`__init__` registers the worker handler, so anything it imports cannot import back),
which is what lets the poller, the handler, and eventually the app all share one
vocabulary instead of keeping three copies that drift.

ADDING A CHANNEL IS NOT ENOUGH TO SEND ON IT. `window._RULES` is keyed by `key` and
refuses an unrecognised platform, so a channel added here with no send rule written
will INGEST and never auto-reply — which is the correct order to grow in, and is
exactly what `window.py` means by "adding a platform string to the poller must never
be enough, by itself, to authorise a send on it". tests/test_inbox_instagram.py
holds that pairing to the wall.
"""
from typing import NamedTuple


# The transport for email. Not a Zernio `platform=` token — the poller branches on it, and the
# suites that pair POLLED against the vendor filter on it. Named once, here, so the poller and the
# tests cannot drift apart about what "not a Zernio channel" means.
IMAP = "imap"

# NOT A PLATFORM, A RESOURCE — the same kind of marker `IMAP` is. Comments do not arrive from a
# "comments network": they arrive from Instagram and Facebook, through a different pair of vendor
# calls than DMs use. This token is what tells the sweep to take that branch, and naming it here
# rather than spelling it in the poller is what keeps the poller and its tests from drifting
# about which channels are not `inbox.list`.
COMMENTS = "comments"

# THE CHANNELS THAT DO NOT GO THROUGH `inbox.list`, named once so the poller and the suites cannot
# drift about it — the same reason `IMAP` is named here rather than spelled in the poller.
#
# `test_inbox_instagram` asserts that every channel the vendor serves is asked for BY NAME, so
# none of them rides the client's Messenger default. Email was excluded from that list by hand
# when it arrived, and comments would have needed the same hand-edit — a second exception written
# in a second place is how the third one gets forgotten. The property the test defends is
# unchanged: these two make no `inbox.list` call at all, so there is no default for them to ride.
NOT_INBOX_LIST = (IMAP, COMMENTS)


class Channel(NamedTuple):
    vendor: str          # Zernio's `platform=` token
    key: str             # stored in inbox_conversations.platform; keys window._RULES


# THE NAMES COVER MORE THAN THE POLLED CHANNELS, and that is the point. The screen renders
# whatever `platform` values the box has rows for — reviews and comments arrive by other
# machines entirely, and email/SMS are channels the inbox is growing into — so a name table
# scoped to what the poller polls would render half the screen in raw column values. The
# poller's list is `POLLED`; this is every channel this system can SAY.
#
# Not derivable, either: `.title()` gives "Sms" and "Whatsapp", which is how a product looks
# when nobody read its own screen.
NAMES = {"messenger": "Messenger", "instagram": "Instagram", "email": "Email",
         "sms": "SMS", "whatsapp": "WhatsApp", "review": "Reviews", "comment": "Comments"}


# THE SWEEP ORDER IS THIS ORDER. Messenger stays first: it is the channel with live
# conversations on the box today, and a sweep that put the new channel first would
# let an Instagram outage (or a box with no IG account connected, which errors every
# single sweep) delay the intake that is already carrying real leads.
POLLED: tuple[Channel, ...] = (
    Channel("facebook", "messenger"),
    Channel("instagram", "instagram"),
    # EMAIL IS ON. The rule it was waiting for is written (`window._RULES["email"]`, with its
    # citations), so the pairing `test_inbox_instagram` enforces is satisfied and this line is the
    # switch the comment here promised it would be.
    #
    # THE RULE STILL SAYS NOTHING AUTO-SENDS, and that is why turning it on was safe rather than
    # merely permitted. Email has no platform window at all; what `decide` refuses is the box
    # mailing a customer UNATTENDED, and delay-send is the owner's opt-in Phase 2.
    #
    # THE OTHER HALF CHANGED ON 2026-09-22. This used to read "the reason it is blocked is that
    # this box has no SMTP path anywhere" and end "a person sends from their own mail app". The
    # owner ruled, `email_channel.send` is the path, and a person now sends from the box itself —
    # from their own address, through their own mailbox. The channel still ingests, the drafter
    # still writes without ever consulting the window, and nothing sends with nobody reading it.
    Channel(IMAP, "email"),
    # COMMENTS ARE ON. Measured against the live vendor by OSDev2 on 2026-09-16:
    # `comments.list_inbox_comments` returned 22 real rows across Instagram and LinkedIn, which
    # is why this channel went first and reviews second — reviews answered n=0, having no Google
    # Business Profile connected anywhere to answer about.
    #
    # LAST IN THE SWEEP ORDER, deliberately, on the rule the top of this tuple already sets:
    # the channels carrying live conversations go first, and a new one must never delay them.
    # Comments are also the most expensive sweep here — a call per platform, then a call per
    # post — so it is the one that should yield, not the one that should be waited on.
    #
    # ITS RULE IS `no_send_lane` (window._RULES["comment"]), so nothing auto-sends: the box
    # reads the comment and the drafter writes a suggestion a person sends by hand.
    Channel(COMMENTS, "comment"),
)


def name(key: str | None, *, fallback: str) -> str:
    """The human name for a stored `platform` value.

    `fallback` IS REQUIRED AND HAS NO DEFAULT, deliberately. What to say when the value
    is empty is genuinely different in the two places this is called from — a heading
    over a thread says "Unknown", a Slack sentence says "this channel" — and a function
    that quietly picked one of them would be right in one caller and wrong in the other
    with nothing at the call site to show it. Making it a required argument is what
    keeps one name table serving both without either one lying.

    A value WE DO NOT KNOW falls through to itself, title-cased, never to a named
    channel. A default that names a specific channel is how a notification about an
    Instagram thread ends up telling the owner it was a Messenger thread — the exact bug
    this table was pulled out of two files to remove, not one to re-introduce as a
    fallback. And a channel the box is receiving that we cannot name is a thing to SHOW
    and go fix, not to hide inside an "Other" bucket.
    """
    v = str(key or "").strip()
    if not v:
        return fallback
    return NAMES.get(v.lower(), v.title())


def label(key: str | None) -> str:
    """The SENTENCE form — "New {label} message", for Slack and the worker's logs.

    A thin wrapper on purpose: the sentence fallback belongs in one place too, or the
    next notification to need it invents its own wording.
    """
    return name(key, fallback="this channel")
