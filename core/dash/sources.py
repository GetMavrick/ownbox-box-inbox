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
from urllib.parse import urlsplit

from flask import redirect, request

from core.connections import oauth, store
from core.dash import blueprint
from core.dash.box_settings import _admit, _back, _esc, _is_owner, _who
from core.dash.home import chrome

DOOR = "/settings/sources"
_TITLE = "Data Sources"          # the menu row's name, so the page and its row agree
LEDE = "Connect the apps your business runs on, so your coworkers can read from them."

# THE CONNECTED APPS ARE ONE TABLE, A ROW EACH. Owner, 2026-10-01, with one app connected and more coming: "I
# would like for the connected list to be tighter and on just a few lines in a table ... we are going to have
# multiple connections so it needs to be tighter list like in a table format." A card per app spent a screen on
# each. Now a row says which app, where, since when and how many tools are on, and opens to the ticks and the
# buttons. Columns with a heading on a wide screen; on a mobile, two lines — the name, then the rest.
_CSS = """<style>
.src{padding-bottom:6px}
.src-head,.card .src-row>summary{display:flex;flex-wrap:wrap;align-items:center;gap:2px 10px}
.src-head{display:none}
.card details.src-row{margin:0;border-top:1px solid var(--hairline)}
.card .src-row>summary{list-style:none;cursor:pointer;padding:12px 0;min-height:48px;color:var(--ink);
font-weight:400}
.card .src-row>summary::-webkit-details-marker{display:none}
.card .src-row>summary::after{content:none;display:none}
.src-name{flex:1 1 calc(100% - 34px);min-width:0;order:1;font-weight:600;font-size:calc(17 * var(--px, 1px));
overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.src-chev{order:2;width:24px;display:flex;justify-content:flex-end;color:var(--ink-3)}
.src-chev svg{transition:transform .15s}
.src-row[open] .src-chev svg{transform:rotate(90deg)}
.src-host,.src-when,.src-on,.src-meta{color:var(--ink-2);font-size:calc(15 * var(--px, 1px))}
.src-host{order:3;flex:1 1 100%;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.src-when,.src-on{display:none}
.src-meta{order:4;flex:1 1 100%}
.src-body{padding:0 0 18px}
.src-body form>button,.src-acts button{width:auto;padding:0 20px;margin-top:16px}
.src-acts{display:flex;flex-wrap:wrap;gap:0 10px}
.src-acts form{margin:0}
@media (min-width:720px){
.src-head,.card .src-row>summary{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.4fr) 9em 11em 24px;
gap:16px}
.src-head{padding:0 0 8px;color:var(--ink-3);font-size:calc(14 * var(--px, 1px));font-weight:600}
.src-name,.src-chev,.src-host{order:0;flex:none}
.src-when,.src-on{display:block;white-space:nowrap}
.src-meta{display:none}
}
</style>"""
_CHEVRON = ('<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
            'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>')


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


def _app_row(slug: str, rec: dict, opened: bool = False) -> str:
    """One connected app, as one row of the table. It opens to the app's tools and its three buttons."""
    on, tools = set(rec.get("enabled") or []), rec.get("tools") or []
    asks = set(rec.get("ask_first") or [])
    reads = [t for t in tools if t.get("read_only")]
    rows = []
    for t in tools:
        words, said = _esc(t.get("title") or t.get("name")), _esc(_first_sentence(t.get("description")))
        if t.get("read_only"):
            rows.append(f'<label class="consent"><input type="checkbox" name="tool" value="{_esc(t["id"])}"'
                        f'{" checked" if t["id"] in on else ""}><span>{words}'
                        + (f'<br><span class="quiet">{said}</span>' if said else "") + '</span></label>')
        else:
            # A TOOL THAT CHANGES THINGS IS NEVER SIMPLY ON: ticked, a coworker may ASK for it, and each ask waits
            # for you on Waiting for you (core/approvals.py).
            app = _esc(rec.get("name"))
            why = (f"Changes things in {app}." if t.get("changes") else
                   f"{app} doesn't say whether this only reads, so the box treats it as changing things.")
            rows.append(f'<label class="consent"><input type="checkbox" name="ask" value="{_esc(t["id"])}"'
                        f'{" checked" if t["id"] in asks else ""}><span>{words}<br><span class="quiet">{why} '
                        'Ticked, a coworker may ask to do it, and you approve each one.</span></span></label>')
    form = (f'<form method="post" action="{DOOR}"><input type="hidden" name="do" value="enable">'
            f'<input type="hidden" name="app" value="{_esc(slug)}">' + "".join(rows)
            # ONE INK PILL PER SCREEN (docs/SCOPE_DESIGN_LANGUAGE.md): Connect is it, so each app's Save is
            # the outline.
            + '<button class="ghost" type="submit">Save</button></form>') if tools else (
        '<p class="quiet">It lists no tools yet. If you change that in the app, check again.</p>')
    name = _esc(rec.get("name"))
    # "TOOLS ON" IS THE READING TOOLS, as it always said; the ones that change things are counted apart, since
    # ticked they only let a coworker ask.
    count = (f"{len(on)} of {len(reads)}"
             + (f" &middot; {len(asks)} {'asks' if len(asks) == 1 else 'ask'} first" if asks else ""))
    when = _esc(_when(rec.get("added_at")))
    # A MOBILE HAS NO ROOM FOR COLUMNS, so the date and the count are one line of words there, which wraps
    # between words; the columns are display:none on a mobile and this is display:none on a wide screen, so
    # each reader, and each screen reader, gets exactly one of them.
    meta = f"Connected {when} &middot; {len(on)} of {len(reads)} tools on" + (
        f" &middot; {len(asks)} {'asks' if len(asks) == 1 else 'ask'} first" if asks else "")
    return (f'<details class="src-row" id="app-{_esc(slug)}"{" open" if opened else ""}><summary>'
            f'<span class="src-name">{name}</span>'
            f'<span class="src-host">{_esc(rec.get("host"))}</span>'
            f'<span class="src-when">{when}</span><span class="src-on">{count}</span>'
            f'<span class="src-meta">{meta}</span>'
            f'<span class="src-chev">{_CHEVRON}</span></summary>'
            '<div class="src-body">' + form +
            '<div class="src-acts">'
            f'<form method="post" action="{DOOR}"><input type="hidden" name="do" '
            f'value="check"><input type="hidden" name="app" value="{_esc(slug)}">'
            '<button class="ghost" type="submit">Check its tools again</button></form>'
            f'<form method="post" action="{DOOR}" '
            f'onsubmit="return confirm(\'Disconnect {name}? Your coworkers stop reading from it.\')">'
            f'<input type="hidden" name="do" value="remove"><input type="hidden" name="app" value="{_esc(slug)}">'
            '<button class="danger" type="submit">Disconnect</button></form></div></div></details>')


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
                    go = oauth.begin(f.get("name"), f.get("url"), box_host=_box_host(), challenge=e.challenge,
                                     user_id=who)
                    login, typed = urlsplit(go).hostname or "", urlsplit(str(f.get("url") or "")).hostname or ""
                    if oauth.same_company(login, typed):
                        return redirect(go, code=303)
                    return _ask_first(f.get("name"), typed, login, go)
                return redirect(f"{DOOR}?added={rec['slug']}", code=303)
            if action == "enable":
                store.set_enabled(slug, f.getlist("tool"), by=who, asks=f.getlist("ask"))
                return redirect(f"{DOOR}?saved={slug}", code=303)
            if action == "cancel_signin":
                oauth.cancel(user_id=who)
                return redirect(DOOR, code=303)
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


def _ask_first(name, typed: str, login: str, go: str):
    """THE LOGIN IS ANOTHER COMPANY'S ADDRESS: say so before sending the owner there. Some apps use a sign-in
    service on its own domain, which is fine; a fake connection address borrowing a real login page looks exactly
    the same, so the person decides, knowing which two addresses are involved. (The box also checks that the
    sign-in is about the address typed, before this: core/connections/oauth.discover.)"""
    body = ('<div class="card"><h2>Sign in at ' + _esc(login) + '?</h2>'
            f'<p>You are connecting <b>{_esc(name)}</b> at <b>{_esc(typed)}</b>, but its sign-in page is at '
            f'<b>{_esc(login)}</b>, a different company\'s address.</p>'
            '<p class="quiet">That is normal for an app that uses a sign-in service. It is also how a fake address '
            'would borrow a real login page. Continue only if you trust both, and the address came from the '
            'app itself.</p>'
            f'<p style="margin-top:16px"><a href="{_esc(go)}">Continue to {_esc(login)} &rarr;</a></p>'
            f'<form method="post" action="{DOOR}" style="margin-top:12px"><input type="hidden" name="do" '
            'value="cancel_signin"><button class="ghost" type="submit">Cancel</button></form></div>')
    return chrome(DOOR, title=_TITLE, lede=LEDE, body=body), 200


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
    # THE ROW JUST CONNECTED, SAVED OR CHECKED IS OPEN, so what changed is in front of the owner, not a tap away.
    touched = {str(request.args.get(k) or "") for k in ("added", "saved", "checked")} - {""}
    rows = [_app_row(slug, rec, opened=slug in touched)
            for slug, rec in sorted(items.items(), key=lambda kv: str(kv[1].get("name") or "").lower())
            if isinstance(rec, dict)]
    if rows:
        body.append('<div class="card src"><h2>Connected apps</h2>'
                    '<div class="src-head" aria-hidden="true"><span>App</span><span>Address</span>'
                    '<span>Connected</span><span>Tools on</span><span></span></div>' + "".join(rows) + '</div>')
    if items:
        body.append('<div class="card"><p class="quiet">To let a coworker read from these, open its shift and '
                    'tick <b>Read from the apps you connected</b>. Each call it makes is on its run\'s '
                    'receipt. Anything a coworker asks to change waits for you.</p>'
                    '<p><a href="/settings/shifts">Shifts &rarr;</a> &nbsp; '
                    '<a href="/approvals">Waiting for you &rarr;</a></p></div>')
    body.append(_connect_form(kept))
    body.append(_back())
    return chrome(DOOR, title=_TITLE, lede=LEDE, body=_CSS + "".join(body)), (400 if note else 200)
