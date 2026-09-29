"""Appearance, per person: Light, Dark, or System (follow the phone or computer).

WebDev2's app redesign (docs/SCOPE_MOBILE_APP_REDESIGN.md §14.4). Owner, 2026-09-27: dark mode
**and** a system-default setting. The switch is WebDev2's (box.css and the control); core holds the
choice and stamps it on every page before the first paint. Two pieces, and only these:

  · THE CHOICE, one per person, in box_settings under (core, theme, user_id). Each person on a box
    picks their own; nobody's choice changes anyone else's screen.
  · THE STAMP: `html_attr()` goes on `<html>`, `head_tags()` in `<head>`. For Light and Dark the
    server knows the answer and says it. For System the server can't see the phone's
    setting, so the page carries a tiny inline script that sets `data-theme` from
    `prefers-color-scheme` before anything is painted, and follows the phone if it changes.

WHITE UNTIL SOMEONE CHOOSES. Owner, 2026-09-16: *"we want white screens first and foremost... and
then we'll probably have a dark toggle later."* So the default is Light (WebDev2's §14.4 asks the
same), nobody's screen follows the phone until they pick System, and without JavaScript System
renders white too. The default is one constant, DEFAULT, if the owner ever rules otherwise.

ONE HOME. The inbox's older appearance switch (`/inbox/theme`, a cookie) now writes here too, and
the inbox reads here first, so one choice governs every screen (owner, 2026-09-24: one place per
setting).
"""
from __future__ import annotations

from flask import redirect, request

from core.dash import blueprint
from core.logging import get_logger

log = get_logger(__name__)

CHOICES = ("light", "dark", "system")
DEFAULT = "light"
MACHINE, KEY = "core", "theme"
ROUTE = "/settings/theme"

# THE BAR BEHIND THE CLOCK. An installed app paints it with theme-color, so it follows the page's
# ground (box.css --ground in each theme), or a white app gets a black band over it.
GROUND = {"light": "#f6f4ef", "dark": "#111111"}
# DARK IS #111111, the homepage's dark half, as box.css now declares it (WebDev2, PR #1659 §14). It
# was the inbox's #0d0d0d; tests/test_the_box_wears_one_look.py fails if the two drift apart.

# BEFORE THE FIRST PAINT: an inline script in <head> runs before the body is drawn. It also follows
# the phone if the phone switches while the page is open.
_DEVICE_SCRIPT = (
    "<script>(function(){var d=document.documentElement,m=window.matchMedia&&"
    "matchMedia('(prefers-color-scheme: dark)');function s(){d.setAttribute('data-theme',"
    "m&&m.matches?'dark':'light')}s();if(m&&m.addEventListener)m.addEventListener('change',s)})()"
    "</script>")


def _valid(v) -> str:
    v = str(v or "").strip().lower()
    return v if v in CHOICES else ""


def chosen(user_id: str | None) -> str:
    """What this person picked, or "" if they never have. Never raises."""
    if not user_id:
        return ""
    try:
        from core import box_settings
        return _valid(box_settings.get(MACHINE, KEY, user_id=str(user_id), default=""))
    except Exception:                                # noqa: BLE001 — unreadable is "never chose"
        log.exception("theme.unreadable")
        return ""


def get(user_id: str | None) -> str:
    """'light' | 'dark' | 'system' for this person: their choice, else Light."""
    return chosen(user_id) or DEFAULT


def put(user_id: str, choice: str) -> str:
    """Store this person's choice. Raises ValueError for anything but the three choices."""
    v = _valid(choice)
    if not v:
        raise ValueError(f"Appearance must be one of {', '.join(CHOICES)}.")
    if not user_id:
        raise ValueError("Only a signed-in person has an appearance of their own.")
    from core import box_settings
    box_settings.put(MACHINE, KEY, v, user_id=str(user_id))
    log.info("theme.saved", user=str(user_id), theme=v)
    return v


def _user_id(req=None) -> str:
    try:
        from core.dash import session_user
        return str((session_user(req or request) or {}).get("id") or "")
    except Exception:                                # noqa: BLE001 — no request, no person
        return ""


def for_request(req=None) -> str:
    """The theme for whoever is asking. Signed out is Light."""
    return get(_user_id(req))


_LABELS = (("light", "Light"), ("dark", "Dark"), ("system", "Automatic"))


def control(next_path: str) -> str:
    """Light / Dark / Automatic for whoever is asking: one form, three buttons, no script.

    EACH CHOICE IS ITS OWN SUBMIT BUTTON, so one tap saves it and a page with JavaScript blocked
    still switches; the chosen one is marked with aria-pressed, which box.css draws as the ink
    pill. "Automatic" is Apple's word for following the device (owner, 2026-09-27: "system
    default"). Drawn by core so System Settings and the inbox cannot offer two different switches.
    """
    import html as _html
    cur = for_request()
    buttons = "".join(
        f'<button type="submit" name="theme" value="{v}" '
        f'aria-pressed="{"true" if cur == v else "false"}">{label}</button>'
        for v, label in _LABELS)
    return (f'<form class="ui-seg" method="post" action="{ROUTE}" aria-label="Appearance">'
            f'<input type="hidden" name="next" value="{_html.escape(next_path, quote=True)}">'
            f'{buttons}</form>')


def html_attr(choice: str) -> str:
    """For `<html{…}>`. System is stamped light, so no JavaScript still means white."""
    v = _valid(choice) or DEFAULT
    return f' data-theme="{"light" if v == "system" else v}"'


def head_tags(choice: str) -> str:
    """For `<head>`: theme-color, and for System the script that decides before paint."""
    v = _valid(choice) or DEFAULT
    if v != "system":
        return f'<meta name="theme-color" content="{GROUND[v]}">'
    return (f'<meta name="theme-color" media="(prefers-color-scheme: light)" '
            f'content="{GROUND["light"]}">'
            f'<meta name="theme-color" media="(prefers-color-scheme: dark)" '
            f'content="{GROUND["dark"]}">' + _DEVICE_SCRIPT)


@blueprint.route(ROUTE, methods=["POST"])
def theme_save():
    """Save the signed-in person's appearance, then go back where they were.

    Form: `theme` (light | dark | system), optional `next` (a path on this box). A fetch() that
    wants no page back can send `Accept: application/json` and gets {"theme": …}."""
    from core.dash import require_session, safe_next
    gate = require_session()
    if gate is not None:
        return gate
    try:
        v = put(_user_id(), request.form.get("theme"))
    except ValueError as e:
        return {"error": str(e)}, 400
    if "application/json" in (request.headers.get("Accept") or ""):
        return {"theme": v}, 200
    return redirect(safe_next(request.form.get("next") or "") or "/settings", code=303)
