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

from core import source_cards
from core.connections import ideas as _ideas
from core.connections import oauth, store
from core.dash import blueprint
from core.dash.box_settings import _admit, _back, _esc, _is_owner, _who
from core.dash.home import chrome
from core.logging import get_logger

log = get_logger(__name__)

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
.ideas{width:100%;border-collapse:collapse}
.ideas th{text-align:left;font-size:calc(13 * var(--px, 1px));font-weight:600;color:var(--ink-3);padding:0 12px 8px 0}
.ideas td{border-top:1px solid var(--hairline);padding:12px 12px 12px 0;vertical-align:top}
.ideas td.app{width:14em}
.ideas td.app b{display:block;font-size:calc(16 * var(--px, 1px))}
.ideas td.app span{color:var(--ink-3);font-size:calc(13 * var(--px, 1px))}
.ideas td.what{color:var(--ink-2);font-size:calc(15 * var(--px, 1px));line-height:1.45}
.ideas td.go{width:6em;text-align:right;white-space:nowrap;padding-right:0}
.ideas td.go a{color:var(--link);font-weight:600;text-decoration:none}
.ideas .tag{display:inline-block;margin-left:6px;padding:0 6px;border:1px solid var(--hairline);border-radius:999px;
font-size:calc(11 * var(--px, 1px));color:var(--ink-3);font-weight:600;vertical-align:1px}
@media (max-width:640px){.ideas td.app{width:auto}.ideas tr{display:grid;grid-template-columns:1fr auto;
border-top:1px solid var(--hairline);padding:8px 0}.ideas td{border-top:0;padding:4px 0}
.ideas td.what{grid-column:1 / -1}.ideas thead{display:none}}
.src-head,.card .src-row>summary,.card a.src-link{display:flex;flex-wrap:wrap;align-items:center;gap:2px 10px}
.card a.src-link{border-top:1px solid var(--hairline);padding:12px 0;min-height:48px;color:var(--ink);
text-decoration:none}
.card a.src-link:hover .src-name{text-decoration:underline}
.card .src-head+a.src-link,.card .src-head+details.src-row{border-top:0}
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
.src-head,.card .src-row>summary,.card a.src-link{display:grid;
grid-template-columns:minmax(0,1fr) minmax(0,1.4fr) 9em 11em 24px;
gap:16px}
.src-head{padding:0 0 8px;color:var(--ink-3);font-size:calc(14 * var(--px, 1px));font-weight:600}
.src-name,.src-chev,.src-host{order:0;flex:none}
.src-when,.src-on{display:block;white-space:nowrap}
.card .src-head+a.src-link,.card .src-head+details.src-row{border-top:1px solid var(--hairline)}
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
    from core.connections import gateway as _gateway
    rows = []
    for t in tools:
        words, said = _esc(t.get("title") or t.get("name")), _esc(_first_sentence(t.get("description")))
        # NAMED WHERE IT IS TICKED: the same check the list uses, so the page and the list never disagree.
        left_off = _gateway.unlistable(slug, t)
        off_note = _esc(f"Your AI isn't offered this one: {left_off}.") if left_off else ""
        said = off_note or said
        if t.get("read_only"):
            rows.append(f'<label class="consent"><input type="checkbox" name="tool" value="{_esc(t["id"])}"'
                        f'{" checked" if t["id"] in on else ""}><span>{words}'
                        + (f'<br><span class="quiet">{said}</span>' if said else "") + '</span></label>')
        else:
            # A TOOL THAT CHANGES THINGS IS NEVER SIMPLY ON: ticked, a coworker may ASK for it, and each ask waits
            # for you on Approvals (core/approvals.py).
            app = _esc(rec.get("name"))
            why = (f"Changes things in {app}." if t.get("changes") else
                   f"{app} doesn't say whether this only reads, so the box treats it as changing things.")
            rows.append(f'<label class="consent"><input type="checkbox" name="ask" value="{_esc(t["id"])}"'
                        f'{" checked" if t["id"] in asks else ""}><span>{words}<br><span class="quiet">{why} '
                        'Ticked, a coworker may ask to do it, and you approve each one.'
                        + (f' {off_note}' if off_note else '') + '</span></span></label>')
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
    # PRESSING CONNECT ON AN IDEA FILLS THIS IN (owner, 2026-10-02, approved from a preview): its name and its
    # vendor's MCP address, from core/connections/ideas.py, never from anything typed in the address bar.
    picked = _ideas.get(str(request.args.get("idea") or ""))
    if picked and not kept:
        kept = {"name": picked["name"], "url": picked["url"]}
    return (
        '<div class="card" id="connect"><h2>Connect any app</h2>'
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
    if request.method == "POST" and source_cards.get(request.form.get("card")):
        # A BUILT-IN CARD'S OWN FORM (core/source_cards.py): its handler acts and says what happened, on the card.
        key = str(request.form.get("card"))
        try:
            said = source_cards.get(key)["handle"](str(request.form.get("do") or ""), request.form, who)
        except Exception as e:                   # noqa: BLE001 — a card's failure is said on it, never a 500
            log.warning("sources.card_failed", card=key, error=type(e).__name__)
            said = (False, "That did not work, and nothing was changed. Try again in a minute.")
        return _card_page(key, said)
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


def _search_console_on() -> bool:
    """The box's own Google Search Console connection (core/vendors/google_search_console.py), on every box."""
    try:
        from core.vendors import google_search_console as gsc
        return bool(gsc.status().get("connected"))
    except Exception:                            # noqa: BLE001 — a status, never a failure
        return False


def _search_console_row() -> str:
    try:
        from core.vendors import google_search_console as gsc
        prop = str(gsc.status().get("property") or "")
    except Exception:                            # noqa: BLE001
        prop = ""
    what = prop.removeprefix("sc-domain:") if prop else "No site picked yet"
    return (f'<a class="src-link" href="{_ideas.SEARCH_CONSOLE}"><span class="src-name">Google Search Console</span>'
            f'<span class="src-host">{_esc(what)}</span><span class="src-when"></span>'
            f'<span class="src-on">Connected</span><span class="src-meta">Connected</span>'
            f'<span class="src-chev">{_CHEVRON}</span></a>')


def _ideas_card(items: dict, on: set) -> str:
    """IDEAS FOR WHAT TO CONNECT (owner, 2026-10-02: "a clean Page for customers to arrive at which gives them lots of
    ideas for things to connect", "a table format where it's like name of the app and then a sentence on what you can
    do with it and what business use case and outcome"; approved from a preview). The box's own sources not yet set up
    come first, then the official MCP servers of popular apps (core/connections/ideas.py), each leaving the list once
    it is connected."""
    rows = []
    if not _search_console_on():
        name, cat, what = _ideas.SEARCH_CONSOLE_IDEA
        rows.append((name, cat, what, _ideas.SEARCH_CONSOLE, "Set up", True))
    for key, spec in source_cards.cards():
        if key not in on and spec.get("idea"):
            rows.append((spec["title"], spec.get("category") or "", spec["idea"], f"{DOOR}/{key}", "Set up", True))
    hosts = {str(r.get("host") or "").lower() for r in items.values() if isinstance(r, dict)}
    for i in _ideas.not_connected(hosts):
        rows.append((i["name"], i["category"], i["what"], f"{DOOR}?idea={i['slug']}#connect", "Connect", False))
    if not rows:
        return ""
    body = "".join(
        f'<tr><td class="app"><b>{_esc(name)}{"<span class=tag>Built in</span>" if built else ""}</b>'
        f'<span>{_esc(cat)}</span></td><td class="what">{_esc(what)}</td>'
        f'<td class="go"><a href="{_esc(href)}">{press}</a></td></tr>'
        for name, cat, what, href, press, built in rows)
    return ('<div class="card"><h2>Ideas for what to connect</h2><p class="quiet">Popular apps with their own secure '
            'connection, an MCP server. Your coworkers can read from them, and change nothing without your OK.</p>'
            '<table class="ideas"><thead><tr><th>App</th><th>What it does for your business</th><th></th></tr></thead>'
            f'<tbody>{body}</tbody></table></div>')


def _source_row(key: str, spec: dict) -> str:
    """A built-in source as one row of the table, opening its card's own page."""
    s = source_cards.summary(key)
    # ON A MOBILE THE SECOND LINE IS WHEN AND HOW, the first already says what (the columns are hidden there).
    meta = " &middot; ".join(_esc(x) for x in (s["when"], s["status"]) if x)
    return (f'<a class="src-link" href="{DOOR}/{_esc(key)}"><span class="src-name">{_esc(spec["title"])}</span>'
            f'<span class="src-host">{_esc(s["what"])}</span><span class="src-when">{_esc(s["when"])}</span>'
            f'<span class="src-on">{_esc(s["status"])}</span><span class="src-meta">{meta}</span>'
            f'<span class="src-chev">{_CHEVRON}</span></a>')


def _built_in(key: str, said) -> str:
    """One card registered by code that is not core (core/source_cards.py), drawn by its owner. One that fails to
    draw says so in a sentence; the page still opens."""
    spec = source_cards.get(key)
    try:
        return spec["render"](said)
    except Exception as e:                       # noqa: BLE001
        log.warning("sources.card_unavailable", card=key, error=type(e).__name__)
        return (f'<div class="card"><h2>{_esc(spec["title"])}</h2><p>This could not be shown just now. '
                'Nothing about it has changed; open this page again in a minute.</p></div>')


def _card_page(key: str, said=None):
    """A built-in source on its own page, the card and nothing else, with the way back to the table."""
    spec = source_cards.get(key)
    body = (_CSS + _built_in(key, said)
            + f'<div class="foot"><a href="{DOOR}">&larr; {_TITLE}</a></div>')
    return chrome(f"{DOOR}/{key}", title=spec["title"], lede=LEDE, body=body), (400 if said and not said[0] else 200)


@blueprint.route(f"{DOOR}/<key>", methods=["GET", "POST"])
def box_source(key: str):
    """A built-in source's own page (owner, 2026-10-02): its card, and its form posts here."""
    refuse = _admit(owner_only=True)
    if refuse is not None:
        return refuse
    if not _is_owner():
        return chrome(DOOR, title=_TITLE, lede=LEDE,
                      body='<div class="card"><p>Only the owner of this box can connect apps.</p></div>'
                           + _back()), 403
    if source_cards.get(key) is None:
        return chrome(DOOR, title=_TITLE, lede=LEDE,
                      body='<div class="card"><p>There is no source by that name on this box.</p>'
                           f'<p><a href="{DOOR}">&larr; {_TITLE}</a></p></div>'), 404
    said = None
    if request.method == "POST":
        try:
            said = source_cards.get(key)["handle"](str(request.form.get("do") or ""), request.form,
                                                   str(_who().get("id") or "owner"))
        except Exception as e:                   # noqa: BLE001 — a card's failure is said on it, never a 500
            log.warning("sources.card_failed", card=key, error=type(e).__name__)
            said = (False, "That did not work, and nothing was changed. Try again in a minute.")
    return _card_page(key, said)


def _render(note: str, kept: dict, cards: dict | None = None):
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
    # ONE TABLE, A ROW PER SOURCE (owner, 2026-10-02: "This page is going to get out of control ... I wanna make sure
    # we're being smart about where we put this", then, from a preview, one table, each row opening its own page).
    # The built-in sources come first, as rows that open their cards' own pages; then the apps connected through
    # their own MCP servers, which open in place as before. Core names no source: each built-in row is its card's
    # own summary (core/source_cards.py).
    #
    # ONLY WHAT IS CONNECTED IS A ROW (owner, 2026-10-02: "We need to suggest things, but not assume everybody is going
    # to want to connect"); everything else is an idea, below.
    on = {k for k, _ in source_cards.cards() if source_cards.summary(k)["connected"] is not False}
    built = [_source_row(key, spec) for key, spec in source_cards.cards() if key in on]
    if _search_console_on():
        built.insert(0, _search_console_row())
    if built or rows:
        body.append('<div class="card src"><h2>Connected</h2>'
                    '<div class="src-head" aria-hidden="true"><span>Source</span><span>Reads from</span>'
                    '<span>Last read</span><span>Status</span><span></span></div>'
                    + "".join(built) + "".join(rows) + '</div>')
    if items:
        body.append('<div class="card"><p class="quiet">To let a coworker read from these, open its shift and '
                    'tick <b>Read from the apps you connected</b>. Each call it makes is on its run\'s '
                    'receipt. Anything a coworker asks to change waits for you.</p>'
                    '<p><a href="/settings/shifts">Coworkers &rarr;</a> &nbsp; '
                    '<a href="/approvals">Approvals &rarr;</a></p></div>')
    body.append(_ideas_card(items, on))
    body.append(_connect_form(kept))
    body.append(_back())
    bad = note
    return chrome(DOOR, title=_TITLE, lede=LEDE, body=_CSS + "".join(body)), (400 if bad else 200)
