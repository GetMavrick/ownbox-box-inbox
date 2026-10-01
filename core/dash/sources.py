"""Data Sources: the apps a business runs on, connected through each app's own MCP server.

docs/SCOPE_CONNECTIONS_MCP_FIRST.md, phase 1, owner-approved 2026-10-01 ("Approve all four with your
recommendations"). The owner names the app and pastes its MCP address, and a token when the app uses one. The box
lists the app's tools for real, so a wrong address or token is said here, where it was typed, and turns on the
tools the app says only read. A coworker reads from them once its shift is given "Read from the apps you
connected".

OWNER ONLY: a connection reads the business's own apps with the owner's token. The token is never drawn back onto
this page; the address is kept with it in box_secrets (core/connections/store.py).
"""
from __future__ import annotations

from datetime import datetime

from flask import redirect, request

from core.connections import oauth, store
from core.dash import blueprint
from core.dash.box_settings import _admit, _back, _esc, _is_owner, _who
from core.dash.home import chrome

DOOR = "/settings/sources"
_TITLE = "Data Sources"          # the menu row's name, so the page and its row agree
LEDE = "Connect the apps your business runs on, so your coworkers can read from them."


def _when(iso: str) -> str:
    try:
        d = datetime.fromisoformat(str(iso))
    except ValueError:
        return ""
    return f"{d:%b} {d.day}, {d.year}"


def _first_sentence(text: str) -> str:
    text = " ".join(str(text or "").split())
    cut = text.find(". ")
    text = text[:cut + 1] if 0 < cut < 160 else text
    return text if len(text) <= 160 else text[:157].rstrip() + "..."


def _app_card(slug: str, rec: dict) -> str:
    on, tools = set(rec.get("enabled") or []), rec.get("tools") or []
    reads = [t for t in tools if t.get("read_only")]
    rows = []
    for t in tools:
        words, said = _esc(t.get("title") or t.get("name")), _esc(_first_sentence(t.get("description")))
        if t.get("read_only"):
            rows.append(f'<label class="consent"><input type="checkbox" name="tool" value="{_esc(t["id"])}"'
                        f'{" checked" if t["id"] in on else ""}><span>{words}'
                        + (f'<br><span class="quiet">{said}</span>' if said else "") + '</span></label>')
        else:
            app = _esc(rec.get("name"))
            why = (f"Changes things in {app}." if t.get("changes") else
                   f"{app} doesn't say whether this only reads, so the box treats it as changing things.")
            rows.append('<label class="consent"><input type="checkbox" disabled><span>'
                        f'{words}<br><span class="quiet">{why} Comes in the next step, with your approval '
                        'each time.</span></span></label>')
    form = (f'<form method="post" action="{DOOR}"><input type="hidden" name="do" value="enable">'
            f'<input type="hidden" name="app" value="{_esc(slug)}">' + "".join(rows)
            # ONE INK PILL PER SCREEN (docs/SCOPE_DESIGN_LANGUAGE.md): Connect is it, so each app's Save is
            # the outline.
            + '<button class="ghost" type="submit" style="margin-top:16px">Save</button></form>') if tools else ""
    name = _esc(rec.get("name"))
    return (f'<div class="card"><h2>{name}</h2>'
            f'<p class="sub">{_esc(rec.get("host"))} &middot; connected {_esc(_when(rec.get("added_at")))} '
            f'&middot; {len(on)} of {len(reads)} reading tools on</p>'
            + form +
            f'<form method="post" action="{DOOR}" style="margin-top:10px"><input type="hidden" name="do" '
            f'value="check"><input type="hidden" name="app" value="{_esc(slug)}">'
            '<button class="ghost" type="submit">Check its tools again</button></form>'
            f'<form method="post" action="{DOOR}" style="margin-top:10px" '
            f'onsubmit="return confirm(\'Disconnect {name}? Your coworkers stop reading from it.\')">'
            f'<input type="hidden" name="do" value="remove"><input type="hidden" name="app" value="{_esc(slug)}">'
            '<button class="danger" type="submit">Disconnect</button></form></div>')


def _connect_form(kept: dict) -> str:
    return (
        '<div class="card"><h2>Connect an app</h2>'
        "<p class=\"quiet\">Use the app's own MCP server. Its address is in the app's settings or help pages, "
        "usually under MCP, AI or connectors. If the app has its own sign-in, Connect takes you there and "
        'back. Your coworkers can read from it. Actions come in the next step, with your approval each '
        'time.</p>'
        f'<form method="post" action="{DOOR}"><input type="hidden" name="do" value="add">'
        '<label for="c-name">App</label>'
        '<input id="c-name" name="name" type="text" required maxlength="30" placeholder="Notion" '
        f'value="{_esc(kept.get("name"))}">'
        '<label for="c-url">MCP address</label>'
        '<input id="c-url" name="url" type="url" required autocapitalize="off" autocorrect="off" '
        f'spellcheck="false" placeholder="https://mcp.example.com/mcp" value="{_esc(kept.get("url"))}">'
        '<label for="c-token">Token, only if the app gives you one instead of a sign-in</label>'
        '<input id="c-token" name="token" type="password" autocomplete="off" placeholder="It stays on this box">'
        '<button type="submit" style="margin-top:16px">Connect</button></form></div>')


@blueprint.route(DOOR, methods=["GET", "POST"])
def box_sources():
    refuse = _admit(owner_only=True)
    if refuse is not None:
        return refuse
    if not _is_owner():
        return chrome(DOOR, title=_TITLE, lede=LEDE,
                      body='<div class="card"><p>Only the owner of this box can connect apps.</p></div>'
                           + _back()), 403
    who = str(_who().get("id") or "owner")
    note, kept = "", {}
    if request.method == "POST":
        f = request.form
        action, slug = str(f.get("do") or ""), str(f.get("app") or "")
        try:
            if action == "add":
                try:
                    rec = store.add(f.get("name"), f.get("url"), f.get("token"), by=who)
                except store.NeedsSignIn as e:
                    # THE APP SIGNS PEOPLE IN ON ITS OWN PAGE: go there, and come back to the callback below.
                    return redirect(oauth.begin(f.get("name"), f.get("url"), box_host=_box_host(),
                                                challenge=e.challenge, user_id=who), code=303)
                return redirect(f"{DOOR}?added={rec['slug']}", code=303)
            if action == "enable":
                store.set_enabled(slug, f.getlist("tool"), by=who)
                return redirect(f"{DOOR}?saved={slug}", code=303)
            if action == "check":
                store.check(slug, by=who)
                return redirect(f"{DOOR}?checked={slug}", code=303)
            if action == "remove":
                store.remove(slug, by=who)
                return redirect(DOOR, code=303)
        except (store.Refused, oauth.SignInFailed) as e:
            note = str(e)
            if action == "add":
                kept = {"name": f.get("name") or "", "url": f.get("url") or ""}
    return _render(note, kept)


def _box_host() -> str:
    from core import claim
    return claim.provisioned_host() or (request.host or "").split(":", 1)[0]


@blueprint.get(oauth.CALLBACK)
def box_sources_signed_in():
    """Back from an app's login page. The owner's own browser brings the code; the state proves this box sent
    them, once."""
    refuse = _admit(owner_only=True)
    if refuse is not None:
        return refuse
    if not _is_owner():
        return chrome(DOOR, title=_TITLE, lede=LEDE,
                      body='<div class="card"><p>Only the owner of this box can connect apps.</p></div>'
                           + _back()), 403
    who = str(_who().get("id") or "owner")
    if request.args.get("error"):
        oauth.cancel(user_id=who)
        said = "you chose not to allow it" if request.args.get("error") == "access_denied" else "it said no"
        return _render(f"Nothing was connected: on the app's own page, {said}.", {})
    try:
        rec = oauth.finish(request.args.get("code") or "", request.args.get("state") or "", by=who, user_id=who)
    except (store.Refused, oauth.SignInFailed) as e:
        return _render(str(e), {})
    return redirect(f"{DOOR}?added={rec['slug']}", code=303)


def _render(note: str, kept: dict):
    items = store.load()["items"]
    body = []
    if note:
        body.append(f'<div class="card"><p>{_esc(note)}</p></div>')
    for flag, said in (("added", "Connected. The tools that only read are on."), ("saved", "Saved."),
                       ("checked", "Checked. Its tools are up to date.")):
        rec = items.get(str(request.args.get(flag) or ""))
        if rec and not note:
            body.append(f'<div class="card"><p><b>{_esc(rec.get("name"))}</b>: {said}</p></div>')
    for slug, rec in sorted(items.items(), key=lambda kv: str(kv[1].get("name") or "").lower()):
        if isinstance(rec, dict):
            body.append(_app_card(slug, rec))
    if items:
        body.append('<div class="card"><p class="quiet">To let a coworker read from these, open its shift and '
                    'tick <b>Read from the apps you connected</b>. Each call it makes is on its run\'s '
                    'receipt.</p><p><a href="/settings/shifts">Shifts &rarr;</a></p></div>')
    body.append(_connect_form(kept))
    body.append(_back())
    return chrome(DOOR, title=_TITLE, lede=LEDE, body="".join(body)), (400 if note else 200)
