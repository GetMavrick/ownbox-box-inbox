"""Cold Pitches: the owner's switch and link for turning cold sales email around (drafter/draft.py PITCH_BACK).

Owner, 2026-10-02: *"I get tons of cold email and I want to advertise right back to them and turn it right around on
them."* He chose: the box drafts each one and he approves (nothing auto-sends), and the link is www.ownbox.io, with no
promo code yet. On 2026-10-03: *"Yes, proceed and do numbers one and then two. Finish them."*

Two box settings the drafter reads directly (it may not import this directory): `inbox / pitch_back.enabled` and
`inbox / pitch_back.link`. Off until the owner turns it on with a link. The buyer's own link, never one we ship.
"""
from __future__ import annotations

import re

NS = "inbox"
ON, LINK = "pitch_back.enabled", "pitch_back.link"
LINK_MAX = 200
_LINK = re.compile(r"^(https?://)?([a-z0-9-]+\.)+[a-z]{2,}(/\S*)?$", re.I)


def clean_link(link) -> str:
    """The link as typed, trimmed; "" stays "". Raises ValueError with a sentence when it isn't a web address."""
    t = str(link or "").strip()
    if not t:
        return ""
    if len(t) > LINK_MAX or " " in t or not _LINK.match(t):
        raise ValueError("That doesn't look like a web address. Try something like www.yourwebsite.com.")
    return t


def get() -> dict:
    from core import box_settings
    try:
        return {"on": bool(box_settings.get(NS, ON, default=False)),
                "link": str(box_settings.get(NS, LINK, default="") or "")}
    except Exception:                                    # noqa: BLE001
        return {"on": False, "link": ""}


def put(on: bool, link, *, by: str | None = None) -> dict:
    """Save both. Turning it on needs a link: the reply points them there."""
    from core import box_settings
    t = clean_link(link)
    if on and not t:
        raise ValueError("Add your website first: every turned-around reply points them to it.")
    box_settings.put(NS, LINK, t, set_by=by)
    box_settings.put(NS, ON, bool(on), set_by=by)
    return get()
