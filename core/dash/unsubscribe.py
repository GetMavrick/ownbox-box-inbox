"""The box's own one-click unsubscribe page, on every box (`compliance.box_unsubscribe_url`).

WHY CORE. The suppression list is core (`core/compliance.py`), and the only page that wrote to it belonged to one
machine, so a box without that machine could send a person a link that went nowhere. Email a person asked for can
be sent from any box, so its opt-out has to be served by every box.

NO SIGN-IN, ON PURPOSE. The person clicking is exercising a legal right and has no account here; the HMAC signature
in the link is the capability, and over-suppression only ever errs toward not contacting someone.

SUPPRESSES ONLY ON POST, for the reason the older unsubscribe page gives: corporate mail gateways open every GET link in a
message before a person reads it, and suppression is permanent. A person confirms with the button; a mail client's
RFC 8058 one-click POST gets a bare 200.
"""
from __future__ import annotations

import html
import urllib.parse

from flask import request

from core import compliance
from core.dash import blueprint
from core.logging import get_logger

log = get_logger(__name__)

PATH = "/unsubscribe"
_CSS = ("font:16px/1.6 -apple-system,system-ui,sans-serif;max-width:520px;margin:12vh auto;padding:0 16px;"
        "color:#1a1a1a;background:#fff;text-align:center")
_BUTTON = ("font:inherit;font-size:17px;width:100%;min-height:48px;padding:10px 22px;border-radius:8px;"
           "border:1px solid #1a1a1a;background:#1a1a1a;color:#fff;cursor:pointer")


def _page(body: str) -> str:
    from core import dash
    return (f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Unsubscribe</title><div style="{_CSS}"><h2>{html.escape(dash.brand())}</h2>{body}</div>')


@blueprint.route(PATH, methods=["GET", "POST"])
def unsubscribe():
    email = request.args.get("e", "") or request.form.get("e", "")
    sig = request.args.get("s", "") or request.form.get("s", "")
    if not compliance.verify_unsubscribe(email, sig):
        log.warning("box.unsubscribe_bad_sig")
        return _page("<p>This unsubscribe link isn't valid. If you keep getting email you don't want, reply to it "
                     "and ask to be removed.</p>"), 400
    if request.method == "POST":
        res = compliance.suppress(email, "unsubscribe", note="box unsubscribe page")
        log.info("box.unsubscribed", new=res.get("new"))
        if request.form.get("confirm") == "1":
            return _page("<p>You're unsubscribed. You won't get email from us again.</p>")
        return "", 200
    action = f"{PATH}?e={urllib.parse.quote(email)}&s={urllib.parse.quote(sig)}"
    return _page(f'<form method="post" action="{html.escape(action)}"><input type="hidden" name="confirm" value="1">'
                 f'<p>Stop all email to <b>{html.escape(email)}</b>?</p>'
                 f'<button type="submit" style="{_BUTTON}">Unsubscribe</button></form>')
