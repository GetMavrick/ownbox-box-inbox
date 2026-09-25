"""/settings/seo/google — connect Google Search Console with one button, then pick the site.

OWNER, 2026-09-25: a button on the settings page "that has them login into their Google account and
choose a property/website as their primary." No keys, no Google Cloud, no screen share. The work
is in core/vendors/google_search_console.py; this screen only says where things stand and offers
the one next step.

OWNER-ONLY, like Outbound Email: it connects the business's Google account.

THE RETURN FROM GOOGLE REDIRECTS, IT DOES NOT RENDER. /done carries a single-use code in its
address; rendering there would make a reload redeem a spent code and show a false failure. It
lands back on the screen with a short word naming what happened, and only words from _SAID are
ever shown, so nothing in that address reaches the page.
"""
from __future__ import annotations

from flask import redirect, request

from core import claim
from core.dash import blueprint
from core.dash.box_settings import _admit, _esc, _is_owner, _who
from core.dash.home import chrome
from core.vendors import google_search_console as gsc

DOOR = "/settings/seo/google"
DONE = "/settings/seo/google/done"
_TITLE = "Google Search Console"
_LEDE = "See what your website is found for on Google. Sign in with Google and pick your site."

# (headline, sentence, good?) for each outcome the screen can report.
_SAID = {
    "connected": ("Google is connected", "Now pick which site this box works on.", True),
    "chosen": ("Primary site saved", "The SEO machine reads Search Console for this site.", True),
    "disconnected": ("Google is disconnected", "This box no longer reads your Search Console.", True),
    "not_ours": ("That sign-in was not started here", "Press Connect again from this page.", False),
    "too_slow": ("That sign-in took too long", "Press Connect again and finish it within ten minutes.", False),
    "cancelled": ("Google was not connected", "The sign-in was cancelled on Google's page.", False),
    "no_search_console": ("Search Console was not allowed",
                          "Press Connect again and leave the Search Console permission ticked.", False),
    "not_switched_on": ("Google sign-in is not switched on yet",
                        "Ownbox is finishing its side of this. Try again later today.", False),
    "handoff_down": ("Google could not be reached", "Nothing was changed. Try again in a minute.", False),
    "google_said_no": ("Google did not accept that", "Press Connect again.", False),
    "signed_out": ("Google has signed this box out",
                   "Ownbox was removed from your Google account, or Google ended the sign-in. "
                   "Connect again.", False),
    "not_a_site": ("That site is not in this Google account", "Pick one from the list.", False),
    "not_a_sold_box": ("This box cannot sign in to Google",
                       "Google sign-in works on boxes bought at ownbox.io.", False),
    "wrong_host": ("Open this page at your box's own address",
                   "Google sign-in works from your box's ownbox.app address.", False),
}


def _note(key: str) -> str:
    said = _SAID.get(key)
    if not said:
        return ""
    head, text, good = said
    tone = "" if good else ' style="border-color:var(--danger)"'
    return f'<div class="card"{tone}><h2>{_esc(head)}</h2><p>{_esc(text)}</p></div>'


def _host() -> str:
    return claim.provisioned_host() or (request.host or "").split(":", 1)[0]


def _post(do: str, label: str, *, cls: str = "", extra: str = "") -> str:
    klass = f' class="{cls}"' if cls else ""
    return (f'<form method="post" action="{DOOR}"><input type="hidden" name="do" value="{do}">'
            f'{extra}<button{klass} type="submit">{_esc(label)}</button></form>')


def _connect_card() -> str:
    if not gsc.host_ok(_host()):
        head, text, _ = _SAID["wrong_host"]
        return f'<div class="card"><h2>{_esc(head)}</h2><p>{_esc(text)}</p></div>'
    return ('<div class="card"><h2>Not connected</h2>'
            '<p>You sign in on Google\'s own page, then choose your website here. The box can '
            'only read Search Console. It cannot change anything in your Google account.</p>'
            + _post("connect", "Connect Google Search Console") + '</div>')


def _picker(rows: list[dict], current: str, *, primary: bool) -> str:
    if not rows:
        return ('<p>This Google account has no websites in Search Console yet. Add your site at '
                'search.google.com/search-console, then come back here.</p>')
    radios = "".join(
        f'<label style="display:flex;gap:8px;align-items:center;margin:6px 0">'
        f'<input type="radio" name="site" value="{_esc(r["url"])}"'
        f'{" checked" if r["url"] == current else ""} required> {_esc(r["url"])}</label>'
        for r in rows)
    return _post("choose", "Make this the primary site", cls="" if primary else "ghost", extra=radios)


def _connected_card(st: dict) -> str:
    who = f" as <b>{_esc(st['account'])}</b>" if st.get("account") else ""
    out = [f'<div class="card"><h2>Connected</h2><p>Google Search Console is connected{who}.</p>']
    try:
        rows = gsc.sites()
    except gsc.Refused as e:
        out.append(f"<p>{_esc(_SAID.get(e.key, _SAID['google_said_no'])[1])}</p>")
        if e.key == "signed_out":
            out.append(_post("connect", "Connect again"))
        rows = None
    if rows is not None:
        if st.get("property"):
            out.append(f'<p>Primary site: <b>{_esc(st["property"])}</b></p>'
                       '<details><summary>Choose a different site</summary>'
                       + _picker(rows, st["property"], primary=False) + '</details>')
        else:
            out.append('<p>Pick the site this box should work on.</p>'
                       + _picker(rows, "", primary=True))
    out.append('<div style="margin-top:12px">'
               + _post("disconnect", "Disconnect Google", cls="ghost") + '</div></div>')
    return "".join(out)


def _back() -> str:
    return '<div class="foot"><a href="/settings">&larr; Settings</a></div>'


@blueprint.route(DOOR, methods=["GET", "POST"])
def google_search_screen():
    refuse = _admit(owner_only=False)
    if refuse is not None:
        return refuse
    if not _is_owner():
        return chrome(DOOR, title=_TITLE, lede="This one is the owner's.",
                      body='<div class="card"><p>Only the owner of this box can connect the '
                           'business\'s Google account.</p></div>' + _back()), 403
    uid = str(_who().get("id") or "")
    if request.method == "POST":
        do = (request.form.get("do") or "").strip()
        try:
            if do == "connect":
                return redirect(gsc.begin(_host(), user_id=uid), code=303)
            if do == "choose":
                gsc.choose(request.form.get("site") or "", user_id=uid)
                return redirect(f"{DOOR}?said=chosen", code=303)
            if do == "disconnect":
                gsc.disconnect(user_id=uid)
                return redirect(f"{DOOR}?said=disconnected", code=303)
        except gsc.Refused as e:
            return redirect(f"{DOOR}?said={e.key}", code=303)
        return redirect(DOOR, code=303)

    st = gsc.status()
    body = _note(request.args.get("said") or "")
    body += _connected_card(st) if st["connected"] else _connect_card()
    return chrome(DOOR, title=_TITLE, lede=_LEDE, body=body + _back()), 200


@blueprint.route(DONE, methods=["GET"])
def google_search_done():
    refuse = _admit(owner_only=False)
    if refuse is not None:
        return refuse
    if not _is_owner():
        return redirect(DOOR, code=303)
    if request.args.get("error"):
        return redirect(f"{DOOR}?said=cancelled", code=303)
    try:
        gsc.finish(request.args.get("code") or "", request.args.get("state") or "",
                   user_id=str(_who().get("id") or ""))
    except gsc.Refused as e:
        return redirect(f"{DOOR}?said={e.key}", code=303)
    return redirect(f"{DOOR}?said=connected", code=303)
