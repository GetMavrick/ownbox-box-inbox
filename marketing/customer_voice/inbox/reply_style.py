"""Reply Style: how the box drafts, chosen per channel (drafter/draft.py STYLE_CASES).

Owner, 2026-10-04: *"I wish there was a setting of a style of response that we could select per channel. Like for me I
would choose sales focus for email and DM's. Other people might choose customer service for emails and sales for DMS.
I just always want to be trying to get more business so want to always be closing ABC."*

Two box settings the drafter reads directly (it may not import this directory): `inbox / reply_style.email` and
`inbox / reply_style.dms`, each "sales" or "service". Email is email; every other channel the inbox drafts for
(Instagram, Messenger and the rest) is a DM. Customer service until the owner chooses (proposal, not yet
owner-approved), which is the drafting every box already had.
"""
from __future__ import annotations

NS = "inbox"
CHANNELS = ("email", "dms")
# THREE LEVELS (owner, 2026-10-04: "That's why we should have levels and settings."), gentlest first. "sales" stays the
# stored value of Strong sales, so a box that chose Sales before the levels keeps exactly what it chose.
STYLES = {"service": "Customer service", "subtle": "Subtle sales", "sales": "Strong sales"}
_ALIASES = {"customer service": "service", "customer_service": "service", "subtle sales": "subtle",
            "strong": "sales", "strong sales": "sales", "aggressive": "sales"}
DEFAULT = "service"
CHANNEL_WORDS = {"email": "Email", "dms": "Direct messages"}


def channel_of(platform) -> str:
    return "email" if str(platform or "").strip().lower() == "email" else "dms"


def clean(style) -> str:
    """The style as stored. Raises ValueError with a sentence for anything but the two."""
    s = str(style or "").strip().lower()
    s = _ALIASES.get(s, s)
    if s not in STYLES:
        raise ValueError("Choose Customer service, Subtle sales or Strong sales.")
    return s


def get() -> dict:
    """{"email": style, "dms": style}. Customer service when unset or unreadable."""
    from core import box_settings
    out = {}
    for ch in CHANNELS:
        try:
            v = str(box_settings.get(NS, f"reply_style.{ch}", default=DEFAULT) or DEFAULT)
        except Exception:                                # noqa: BLE001
            v = DEFAULT
        out[ch] = v if v in STYLES else DEFAULT
    return out


def put(*, email=None, dms=None, by: str | None = None) -> dict:
    """Save one or both. Validates both before writing either."""
    from core import box_settings
    want = {ch: clean(v) for ch, v in (("email", email), ("dms", dms)) if v is not None}
    for ch, v in want.items():
        box_settings.put(NS, f"reply_style.{ch}", v, set_by=by)
    return get()
