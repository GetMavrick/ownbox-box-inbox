"""Shared DM opt-out detection — ONE wordlist for every surface that messages a human.

W1.4: the lead-magnet funnel advanced on ANY inbound reply, so a viewer typing "stop"
satisfied the drive gate and triggered the NEXT DM — messaging someone on Meta's platform
right after they asked us not to. The unified inbox already had the checker; it lives here
now so no future DM surface can ship without it.
"""

_STOP_WORDS = frozenset({"stop", "unsubscribe", "opt out", "optout"})


def is_stop(text: str) -> bool:
    """True iff the message is an opt-out: the bare word, or the word standing alone
    inside the message (word-boundary match — 'stop' in 'nonstop' does not trigger)."""
    t = (text or "").strip().lower()
    return t in _STOP_WORDS or any(w == t or f" {w} " in f" {t} " for w in _STOP_WORDS)
