"""A machine's Keys page: one field for each secret it declared with `m.secret` (core/machine_secrets.py).

OWNER ONLY, because a key acts as the owner in another service. A saved value is NEVER shown back, not even in
part: the page says "Saved" and offers Replace and Remove. Mobile first: every field is full width and 16px, so a
phone doesn't zoom on focus, and Remove is a quiet link under its own field, never the easiest thing to tap.
"""
from __future__ import annotations

import re

from flask import abort, redirect, request

from core import box_secrets, machine_secrets, sdk
from core.dash import blueprint
from core.dash.box_settings import _admit, _esc, _is_owner, _who
from core.dash.home import chrome

_SLUG = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
LEDE = "The keys this machine uses to reach your other services. Saved keys are never shown again."


def _door(slug: str) -> str:
    return f"/settings/machines/{slug}/keys"


def _field(slug: str, name: str, spec: dict, saved: bool) -> str:
    door = _esc(_door(slug))
    state = '<p class="quiet">Saved.</p>' if saved else '<p class="quiet">Not set yet.</p>'
    help_ = f'<p class="sub">{_esc(spec["help"])}</p>' if spec.get("help") else ""
    remove = (f'<form method="post" action="{door}" style="margin-top:14px">'
              f'<input type="hidden" name="name" value="{_esc(name)}"><input type="hidden" name="do" value="remove">'
              '<button class="ghost" type="submit">Remove this key</button></form>') if saved else ""
    return (f'<div class="card"><h2>{_esc(spec["label"])}</h2>{help_}{state}'
            f'<form method="post" action="{door}"><input type="hidden" name="name" value="{_esc(name)}">'
            f'<input type="hidden" name="do" value="save">'
            f'<label for="k-{_esc(name)}">{"Replace it" if saved else "Paste it"}</label>'
            f'<input id="k-{_esc(name)}" name="value" type="password" autocomplete="off" spellcheck="false" '
            f'style="width:100%;font-size:16px">'
            '<button type="submit" style="margin-top:10px">Save</button></form>'
            f'{remove}</div>')


@blueprint.route("/settings/machines/<slug>/keys", methods=["GET", "POST"])
def machine_keys(slug: str):
    if not _SLUG.match(str(slug or "")):
        abort(404)
    refuse = _admit(owner_only=True)
    if refuse is not None:
        return refuse
    door = _door(slug)
    key = sdk._key(slug)
    wanted = machine_secrets.declared(key)
    if not wanted:
        abort(404)
    title = "Keys"
    if not _is_owner():
        return chrome(door, title=title, lede=LEDE,
                      body='<div class="card"><p>Only the owner of this box can see or change its keys.</p></div>'), 403
    note = ""
    if request.method == "POST":
        name, do = str(request.form.get("name") or ""), str(request.form.get("do") or "")
        who = str(_who().get("email") or _who().get("id") or "owner")
        if name not in wanted:
            note = "That key isn't one this machine asks for."
        elif do == "remove":
            machine_secrets.clear(key, name, user_id=who)
            return redirect(f"{door}?removed={name}", code=303)
        else:
            try:
                machine_secrets.put(key, name, request.form.get("value") or "", user_id=who)
                return redirect(f"{door}?saved={name}", code=303)
            except box_secrets.SecretRejected as e:
                note = str(e)
    body = []
    if note:
        body.append(f'<div class="card"><p>{_esc(note)}</p></div>')
    done = request.args.get("saved") or request.args.get("removed")
    if done in wanted and not note:
        verb = "saved" if request.args.get("saved") else "removed"
        body.append(f'<div class="card"><p>{_esc(wanted[done]["label"])} {verb}.</p></div>')
    body += [_field(slug, n, spec, machine_secrets.is_set(key, n)) for n, spec in sorted(wanted.items())]
    return chrome(door, title=title, lede=LEDE, body="".join(body)), (400 if note else 200)
