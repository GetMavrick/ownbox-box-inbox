"""/settings/email — the owner's own way for this box to send email.

WHY THIS SCREEN EXISTS (owner, 2026-09-23). A sold box ships with no way to send email, on purpose:
our Resend key must never sit on a buyer's box. The Morning Review and the inbox's alerts now reach
the owner in the mobile app regardless ("Go with C"), and this screen is the other half he asked
for in the same breath: *"a screen in System Settings where a business owner can add a resend email
key. And then some brief instructions if they want to add some other type of email, sending SMTP
service."*

WHAT IT DOES NOT SAY IS "CONNECTED". Saving sends a real test to the owner's own address, and the
screen says where it went and to look for it. OSDev1 measured why on 2026-09-23: seven claim emails
were "sent" with a key set and a domain verified, and every one was silently dropped. Configured,
called and no exception is not the same as a person received it.

A SAVE THAT FAILS ITS TEST IS NOT KEPT. A wrong key stored "for now" would fail every morning at
eight, inside a worker, where nobody is looking — so the previous setting is put back and the
refusal is shown while the owner is still looking at the field they typed into.

OWNER-ONLY. The key sends mail as the business; that is the owner's to hand over, not a member's.

IN ITS OWN FILE, not in `box_settings.py`, because two open PRs were rewriting that file the day
this was written. The routes land on the same blueprint and render through the same `chrome()`.
"""
from __future__ import annotations

from flask import request

from core import box_mail
from core.dash import blueprint
from core.dash.box_settings import _admit, _back, _esc, _is_owner, _who
from core.dash.home import chrome

DOOR = "/settings/email"
_TITLE = "Outbound Email"         # owner 2026-09-24: the menu row's name, so the page and its row agree


def _status_card(d: dict) -> str:
    kind, sender = d.get("kind") or "", _esc(d.get("from") or "")
    if not kind:
        return ('<div class="card"><h2>No email is set up</h2>'
                '<p>Your Morning Review and inbox alerts still reach you as notifications in the '
                'mobile app. Add a way to send email below and they arrive in your inbox too.</p>'
                '</div>')
    if d.get("operator"):
        said = f"This box sends with the service it was set up with, from <b>{sender}</b>."
    elif kind == "resend":
        said = f"This box sends through your Resend account, from <b>{sender}</b>."
    else:
        said = (f"This box sends through <b>{_esc(d.get('host'))}</b> as "
                f"<b>{_esc(d.get('user'))}</b>, from <b>{sender}</b>.")
    out = [f'<div class="card"><h2>Email is set up</h2><p>{said}</p>',
           f'<form method="post" action="{DOOR}"><input type="hidden" name="do" value="test">'
           '<button class="ghost" type="submit">Send me a test</button></form>']
    if not d.get("operator"):
        out.append(f'<form method="post" action="{DOOR}" style="margin-top:10px">'
                   '<input type="hidden" name="do" value="remove">'
                   '<button class="danger" type="submit">Stop sending email</button></form>')
    return "".join(out) + "</div>"


def _resend_form() -> str:
    return (
        '<div class="card"><h2>Use Resend</h2>'
        '<p class="quiet">Resend sends email for your business from your own domain. '
        '<b>1.</b> Make a free account at resend.com. '
        '<b>2.</b> Under Domains, add your business domain and finish the DNS steps it shows. '
        '<b>3.</b> Under API Keys, create a key with sending access. '
        '<b>4.</b> Paste it here, with an address at that domain to send from.</p>'
        f'<form method="post" action="{DOOR}"><input type="hidden" name="do" value="resend">'
        '<label for="r-from">Send from</label>'
        '<input id="r-from" name="from" type="email" autocomplete="email" required '
        'placeholder="hello@yourbusiness.com">'
        '<label for="r-key">Resend API key</label>'
        '<input id="r-key" name="key" type="password" autocomplete="off" required '
        'placeholder="re_...">'
        '<button type="submit" style="margin-top:16px">Save and send a test</button></form></div>')


def _smtp_form() -> str:
    return (
        '<div class="card"><details><summary><b>Use another email service (SMTP)</b></summary>'
        '<p class="quiet">Any service that gives you SMTP settings works. Look for "SMTP" in its '
        'help pages; you need four things: a server, a port, a username and a password.</p>'
        '<p class="quiet"><b>Google Workspace or Gmail:</b> server smtp.gmail.com, port 587, your '
        'full address as the username, and an app password as the password (Google Account, then '
        'Security, then App passwords — it needs 2-Step Verification on).</p>'
        '<p class="quiet"><b>Microsoft 365:</b> server smtp.office365.com, port 587. Your '
        'administrator may need to allow SMTP sending for your mailbox.</p>'
        '<p class="quiet"><b>Zoho, Fastmail, Postmark, SendGrid, Mailgun, Amazon SES and '
        'others:</b> use the SMTP server, port and login from their settings page.</p>'
        f'<form method="post" action="{DOOR}"><input type="hidden" name="do" value="smtp">'
        '<label for="s-from">Send from</label>'
        '<input id="s-from" name="from" type="email" autocomplete="email" required '
        'placeholder="hello@yourbusiness.com">'
        '<label for="s-host">SMTP server</label>'
        '<input id="s-host" name="host" type="text" autocapitalize="off" autocorrect="off" '
        'spellcheck="false" required placeholder="smtp.gmail.com">'
        '<label for="s-port">Port</label>'
        '<input id="s-port" name="port" type="text" inputmode="numeric" value="587" required>'
        '<label for="s-user">Username</label>'
        '<input id="s-user" name="user" type="text" autocapitalize="off" autocorrect="off" '
        'spellcheck="false" autocomplete="username" required>'
        '<label for="s-pass">Password</label>'
        '<input id="s-pass" name="password" type="password" autocomplete="off" required>'
        # THE SECOND WAY IS THE OUTLINE. Resend, above, is the one ink pill on this screen; two
        # identical pills for two alternatives read as two things to do (OSDev0, 2026-09-24).
        '<button class="ghost" type="submit" style="margin-top:16px">Save and send a test</button></form>'
        '</details></div>')


def _note(ok: bool, head: str, said: str) -> str:
    tone = "" if ok else ' style="border-color:var(--danger)"'
    return f'<div class="card"{tone}><h2>{_esc(head)}</h2><p>{_esc(said)}</p></div>'


@blueprint.route(DOOR, methods=["GET", "POST"])
def box_email_screen():
    """Add, test or remove the owner's own way for this box to send email."""
    refuse = _admit(owner_only=False)
    if refuse is not None:
        return refuse
    if not _is_owner():
        return chrome(DOOR, title=_TITLE, lede="This one is the owner's.",
                      body='<div class="card"><p>Only the owner of this box can choose how it '
                           'sends email, because that email goes out in the business\'s name.'
                           '</p></div>' + _back()), 403

    who = _who()
    uid = str(who.get("id") or "")
    note = ""
    if request.method == "POST":
        do = (request.form.get("do") or "").strip()
        if do == "test":
            ok, said = box_mail.send_test(uid)
            note = _note(ok, "Test sent" if ok else "The test did not send", said)
        elif do == "remove":
            box_mail.clear_own(user_id=uid)
            note = _note(True, "Email is off",
                         "This box no longer sends email. Notifications in the mobile app carry on.")
        elif do in ("resend", "smtp"):
            from core import box_secrets
            before = box_secrets.get(box_mail.MAIL_OWN)
            fields = {k: request.form.get(k) or "" for k in
                      ("from", "key", "host", "port", "user", "password")}
            try:
                box_mail.put_own({"kind": do, **fields}, user_id=uid)
            except ValueError as e:
                note = _note(False, "That was not saved", str(e))
            else:
                ok, said = box_mail.send_test(uid)
                if ok:
                    note = _note(True, "Saved, and a test is on its way", said)
                else:
                    # PUT BACK WHAT WAS THERE. A setting that cannot send would fail every morning
                    # where nobody is looking; refused now, it fails in front of the person who
                    # can fix it.
                    if before:
                        box_secrets.put(box_mail.MAIL_OWN, before, user_id=uid)
                    else:
                        box_mail.clear_own(user_id=uid)
                    note = _note(False, "Not saved — the test did not send", said)
        else:
            note = _note(False, "Nothing happened", "That button is not one this page knows.")

    d = box_mail.describe()
    body = note + _status_card(d)
    if not d.get("operator"):
        body += _resend_form() + _smtp_form()
    return chrome(DOOR, title=_TITLE,
                  lede="Get your Morning Review and alerts by email as well as in the mobile app.",
                  body=body + _back()), 200
