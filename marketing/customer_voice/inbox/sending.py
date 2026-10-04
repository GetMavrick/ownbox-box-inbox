"""Sending: the two things the box sends on its own, settable on a screen (Inbox Settings, Sending).

Owner, 2026-10-04: *"Seems like there's some things that are not even built out yet"* — on the Unified Inbox
Machine. These two were: the first message (`inbox.autonomy: opener` plus `inbox.opener_template`) and the hourly
send cap (`inbox.hourly_send_cap`) lived only in the box's configuration file, which inbox.settings reported as
"not on a screen yet". A buyer cannot edit a file on their Droplet (CLAUDE.md rule 8), so for them the two did
not exist: the first message could never be turned on, and the cap could never be changed.

Three box settings, read by the handler, the reply path and the tools: `inbox / sending.first_message` ("on" or
"off"), `inbox / sending.first_message_text` and `inbox / sending.hourly_cap`. The configuration file is the
fallback for each, so every box behaves exactly as it did until its owner touches the screen, and the fail-closed
rule stands: the box sends a first message only when the setting says "on" (or, unset, when the file says
`opener`). Every reader takes the inbox config as an argument so a test that stands in for `handler._cfg` keeps
working.
"""
from __future__ import annotations

NS = "inbox"
KEY_ON, KEY_TEXT, KEY_CAP = "sending.first_message", "sending.first_message_text", "sending.hourly_cap"
DEFAULT_CAP = 40
MIN_CAP, MAX_CAP = 1, 200
MAX_TEXT = 600


def _cfg(cfg=None) -> dict:
    if cfg is not None:
        return cfg
    from core.config import get_config
    return get_config().get("inbox") or {}


def _setting(key: str):
    from core import box_settings
    try:
        return box_settings.get(NS, key, default=None)
    except Exception:                                    # noqa: BLE001 — a store that cannot be read is "unset"
        return None


def autonomy(cfg=None) -> str:
    """"opener" when the box sends a first message on its own, else the configuration's own word ("off")."""
    v = _setting(KEY_ON)
    if v in ("on", "off"):
        return "opener" if v == "on" else "off"
    return str(_cfg(cfg).get("autonomy", "unset")).strip().lower()


def first_message_on(cfg=None) -> bool:
    return autonomy(cfg) == "opener"


def first_message_text(cfg=None, space: dict | None = None) -> str:
    """The first message, as the owner wrote it on the screen; else the Space's, else the configuration's."""
    v = _setting(KEY_TEXT)
    if isinstance(v, str) and v.strip():
        return v.strip()
    return str((space or {}).get("opener_template") or _cfg(cfg).get("opener_template") or "").strip()


def hourly_cap(cfg=None) -> int:
    """The most messages the box sends in an hour, counted across every send."""
    v = _setting(KEY_CAP)
    try:
        if v is not None and MIN_CAP <= int(v) <= MAX_CAP:
            return int(v)
    except (TypeError, ValueError):
        pass
    # A 0 IN THE FILE MEANS "SEND NOTHING", and the handler always honoured it: `or DEFAULT_CAP` would turn a stop
    # into 40 an hour (OSDev1, #1945). Only a missing or unreadable value takes the default.
    raw = _cfg(cfg).get("hourly_send_cap")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return DEFAULT_CAP
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_CAP


def clean_cap(value) -> int:
    """The cap as stored. Raises ValueError with a sentence for anything outside 1 to 200."""
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"The hourly cap is a whole number from {MIN_CAP} to {MAX_CAP}.") from None
    if not MIN_CAP <= n <= MAX_CAP:
        raise ValueError(f"The hourly cap is a whole number from {MIN_CAP} to {MAX_CAP}.")
    return n


def clean_on(value) -> str:
    s = str(value).strip().lower()
    if s in ("on", "true", "yes", "1"):
        return "on"
    if s in ("off", "false", "no", "0", ""):
        return "off"
    raise ValueError("The first message is on or off.")


def clean_text(value) -> str:
    t = " ".join(str(value or "").split())
    if len(t) > MAX_TEXT:
        raise ValueError(f"Keep the first message under {MAX_TEXT} characters.")
    return t


def get() -> dict:
    """{"first_message": "on"|"off", "text": str, "hourly_cap": int}, as the box runs right now."""
    return {"first_message": "on" if first_message_on() else "off",
            "text": first_message_text(), "hourly_cap": hourly_cap()}


def put(*, first_message=None, text=None, hourly_cap=None, by: str | None = None) -> dict:
    """Save any of the three. Validates everything before writing anything; a first message turned on with no
    words to send is refused, since an empty message is not a first message."""
    from core import box_settings
    want = {}
    if first_message is not None:
        want[KEY_ON] = clean_on(first_message)
    if text is not None:
        want[KEY_TEXT] = clean_text(text)
    if hourly_cap is not None:
        want[KEY_CAP] = clean_cap(hourly_cap)
    on = want.get(KEY_ON, "on" if first_message_on() else "off")
    words = want[KEY_TEXT] if KEY_TEXT in want else first_message_text()
    if on == "on" and not words:
        raise ValueError("Write the first message before turning it on.")
    for k, v in want.items():
        box_settings.put(NS, k, v, set_by=by)
    return get()
