"""Channels: how the box answers each place people message the owner (Inbox Settings, Channels).

Owner, 2026-10-07: *"We also need to build different settings per channel. For example, DM's get an auto reply whereas
email just gets an auto draft. Have we scoped that out and where would that live in the settings? We need to think
about the most optimal look for how it's displayed."* Shown a one-screen mockup, the same day: *"OK, this is brilliant
Design work by you. But I don't want you to build pages and this doesn't really fit our design token scheme. Please
just get everything ready for Webdev2 our app developer."* The page is WebDev2's; the hand-off is
docs/SCOPE_CHANNELS_SCREEN.md.

THE ONE READ AND THE ONE WRITE THE PAGE NEEDS. Per channel, two choices:
  * WHEN A MESSAGE COMES IN: "off" (Leave to me: no draft), "draft" (a draft waits for the owner), or "auto"
    (Auto-reply: the draft sends itself, inbox/autosend.py). Email never offers "auto" (owner, same day: "Emails are in
    frequent and there's a finality to them").
  * REPLY STYLE: Customer service, Subtle sales or Strong sales (inbox/reply_style.py STYLES).

NO NEW STATE BEHIND IT: every choice is a box setting an existing reader already obeys, so nothing changes on a box
until its owner presses something here.
  * "auto"  -> `sending.auto_reply.<ch>` on (sending.put stamps since-when), and `drafts.<ch>` on;
  * "draft" -> auto-reply off, `drafts.<ch>` on;
  * "off"   -> auto-reply off, `drafts.<ch>` off (drafter/draft.channel_drafting; the drafter skips the channel).
  * style   -> `reply_style.email` for email; `reply_style.<ch>` for a DM channel (drafter/draft.reply_style reads it
    before the all-DMs `reply_style.dms`, which stays the fallback).
A channel nobody set here follows the box-wide drafting switch, exactly as before.

WHAT ALWAYS WAITS FOR A PERSON, whatever is chosen here: a reply to a cold pitch, a conversation a machine holds or has
reserved (claims.py), a robot's, an opted-out person's. Those are autosend's and the drafter's rules, not settings.
"""
from __future__ import annotations

from . import reply_style, sending

NS = "inbox"
CHANNELS = (("email", "Email"), ("instagram", "Instagram"), ("messenger", "Messenger"))
MODES = {"off": "Leave to me", "draft": "Draft", "auto": "Auto-reply"}
KEY_DRAFTS = "drafts.{ch}"                       # "on" | "off": the drafter reads it (drafter/draft.channel_drafting)


def modes_for(channel: str) -> tuple:
    """The choices a channel offers, in the order the page shows them."""
    return ("off", "draft", "auto") if channel in dict(sending.AUTO_CHANNELS) else ("off", "draft")


def _get(key: str):
    from core import box_settings
    try:
        return box_settings.get(NS, key, default=None)
    except Exception:                                    # noqa: BLE001 — unreadable is "unset"
        return None


def _drafting(channel: str) -> bool:
    v = _get(KEY_DRAFTS.format(ch=channel))
    if v in ("on", "off"):
        return v == "on"
    # THE BOX-WIDE SWITCH, read the way drafter/draft.enabled reads it: this directory may not import the drafter.
    from core import box_settings
    try:
        said = box_settings.describe(NS, "drafts.enabled")
        if said.get("source") == "box":
            return bool(said.get("value"))
    except Exception:                                    # noqa: BLE001
        pass
    from core.config import get_config
    return bool(((get_config().get("inbox") or {}).get("drafts") or {}).get("enabled", True))


def _style(channel: str) -> str:
    keys = ("email",) if channel == "email" else (channel, "dms")
    for k in keys:
        v = str(_get(f"reply_style.{k}") or "")
        if v in reply_style.STYLES:
            return v
    return reply_style.DEFAULT


def get() -> list[dict]:
    """One row per channel, in the page's order:
    {"channel", "label", "mode", "modes": [{"value", "label"}], "style", "styles": [{"value", "label"}], "since"}.
    `since` is when Auto-reply was turned on (ISO, UTC), or "" when it is off."""
    out = []
    for ch, label in CHANNELS:
        since = sending.auto_reply_since(ch)
        mode = "auto" if since else ("draft" if _drafting(ch) else "off")
        out.append({"channel": ch, "label": label, "mode": mode,
                    "modes": [{"value": m, "label": MODES[m]} for m in modes_for(ch)],
                    "style": _style(ch),
                    "styles": [{"value": v, "label": w} for v, w in reply_style.STYLES.items()],
                    "since": since})
    return out


def clean_mode(channel: str, mode) -> str:
    """The mode as stored. Raises ValueError with a sentence the page can show."""
    m = str(mode or "").strip().lower()
    if m not in modes_for(channel):
        if m == "auto":
            raise ValueError("Email always waits for you: choose Leave to me or Draft.")
        raise ValueError("Choose " + " or ".join(MODES[x] for x in modes_for(channel)) + ".")
    return m


def put(channel: str, *, mode=None, style=None, by: str | None = None) -> list[dict]:
    """Save one channel's mode, style or both. Validates both before writing either. -> get()."""
    from core import box_settings
    ch = str(channel or "").strip().lower()
    if ch not in dict(CHANNELS):
        raise ValueError("Choose Email, Instagram or Messenger.")
    m = clean_mode(ch, mode) if mode is not None else None
    s = reply_style.clean(style) if style is not None else None
    if m is not None:
        if ch in dict(sending.AUTO_CHANNELS):
            sending.put(auto_reply={ch: "on" if m == "auto" else "off"}, by=by)
        box_settings.put(NS, KEY_DRAFTS.format(ch=ch), "off" if m == "off" else "on", set_by=by)
    if s is not None:
        box_settings.put(NS, f"reply_style.{ch}", s, set_by=by)
    return get()


def in_words() -> str:
    """One line for the settings overview, the inbox tools and the morning review: "Email: Draft · Instagram:
    Auto-reply · Messenger: Draft"."""
    return " · ".join(f"{r['label']}: {MODES[r['mode']]}" for r in get())
