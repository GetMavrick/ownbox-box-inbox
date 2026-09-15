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
  `label`   what a human reads, in Slack and on the screen.

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


class Channel(NamedTuple):
    vendor: str          # Zernio's `platform=` token
    key: str             # stored in inbox_conversations.platform; keys window._RULES
    label: str           # what a person reads


# THE SWEEP ORDER IS THIS ORDER. Messenger stays first: it is the channel with live
# conversations on the box today, and a sweep that put the new channel first would
# let an Instagram outage (or a box with no IG account connected, which errors every
# single sweep) delay the intake that is already carrying real leads.
POLLED: tuple[Channel, ...] = (
    Channel("facebook", "messenger", "Messenger"),
    Channel("instagram", "instagram", "Instagram"),
)

_BY_KEY = {c.key: c for c in POLLED}


def label(key: str | None) -> str:
    """A human-readable channel name for a stored `platform` value.

    FALLS BACK TO THE RAW VALUE, TITLE-CASED, rather than to "Messenger". A default
    that names a specific channel is how a notification about an Instagram thread
    ends up telling the owner it was a Messenger thread — the bug this function was
    written to remove, not a bug to re-introduce as a fallback. A channel this file
    has not met yet reads slightly plainly; it never reads wrongly.
    """
    key = (key or "").strip()
    ch = _BY_KEY.get(key.lower())
    return ch.label if ch else (key.title() or "this channel")
