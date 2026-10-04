"""Approvals: what a coworker asked to do that changes something, for the owner to approve or decline.

core/approvals.py holds the rules (nothing runs without a person; once; a week to decide). This is the one screen
that decides: each proposal shows exactly what will run, in the app's own words, with who asked and when. Approve
runs it and shows what the app answered; Decline changes nothing.

OWNER ONLY: approving acts in the owner's apps with the owner's connections.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from flask import redirect, request

from core import approvals
from core.dash import blueprint
from core.dash.box_settings import _admit, _esc, _is_owner, _who
from core.dash.home import chrome

DOOR = approvals.PAGE
_ID = re.compile(r"^ap_[0-9a-f]{20}$")
_TITLE = "Approvals"   # ONE NAME (plan #1857 F2, D4 ruled by the owner 10-03)
LEDE = "What your coworkers asked to do. Nothing changes until you approve it."


def _when(iso: str) -> str:
    try:
        d = datetime.fromisoformat(str(iso))
    except ValueError:
        return ""
    return f"{d:%b} {d.day}, {d:%H:%M} UTC"


def _value(v) -> str:
    text = str(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
    return _esc(text if len(text) <= 600 else text[:597] + "...")


def _what(a: dict) -> str:
    """Exactly what will run: the app, the action, and every argument as the app will receive it."""
    d = a.get("detail") or {}
    args = d.get("arguments") or {}
    rows = "".join(f'<div class="row"><b style="min-width:120px">{_esc(k)}</b><span>{_value(v)}</span></div>'
                   for k, v in args.items()) or '<p class="quiet">No details: the action runs as named.</p>'
    who = a.get("proposed_by") or "a coworker"
    return (f'<p class="sub">In {_esc(d.get("app") or a.get("machine"))} &middot; asked by {_esc(who)} '
            f'&middot; {_esc(_when(a.get("created_at")))} &middot; waits until {_esc(_when(a.get("expires_at")))}</p>'
            + rows)


def _waiting_card(a: dict) -> str:
    aid = _esc(a["id"])
    return (f'<div class="card"><h2>{_esc(a["title"])}</h2>' + _what(a) +
            f'<form method="post" action="{DOOR}" style="margin-top:14px"><input type="hidden" name="id" value="{aid}">'
            '<input type="hidden" name="do" value="approve"><button type="submit">Approve</button></form>'
            f'<form method="post" action="{DOOR}" style="margin-top:10px"><input type="hidden" name="id" value="{aid}">'
            '<input type="hidden" name="do" value="decline"><button class="ghost" type="submit">Decline</button></form>'
            '</div>')


_SAID = {"done": "Done", "failed": "Tried, and it failed", "declined": "Declined", "expired": "Expired",
         "approved": "Approved"}


def _history(rows: list) -> str:
    if not rows:
        return ""
    out = ['<div class="card"><h2>Decided</h2>']
    for a in rows:
        text = ((a.get("result") or {}).get("text") or "").strip()
        out.append(f'<div class="row" style="display:block"><b>{_esc(a["title"])}</b>'
                   f'<span class="quiet">{_esc(_SAID.get(a["status"], a["status"]))}'
                   f'{" by " + _esc(a["decided_by"]) if a.get("decided_by") else ""}'
                   f' &middot; {_esc(_when(a.get("decided_at") or a.get("expires_at")))}</span>'
                   + (f'<p class="quiet" style="margin-top:4px">{_esc(text[:300])}</p>' if text else "")
                   + '</div>')
    return "".join(out) + "</div>"


def card() -> str:
    """For the Dashboard: what's waiting, while something is. '' otherwise. Never raises."""
    try:
        n = len(approvals.waiting())
    except Exception:                                    # noqa: BLE001 — a card never costs the page
        return ""
    if not n:
        return ""
    return (f'<div class="card"><h2>Approvals</h2><p class="sub">{n} '
            f'{"thing a coworker asked" if n == 1 else "things your coworkers asked"} to do. Nothing changes '
            f'until you approve.</p><p><a href="{DOOR}">Review &rarr;</a></p></div>')


@blueprint.route(DOOR, methods=["GET", "POST"])
def box_approvals():
    refuse = _admit(owner_only=True)
    if refuse is not None:
        return refuse
    if not _is_owner():
        return chrome(DOOR, title=_TITLE, lede=LEDE,
                      body='<div class="card"><p>Only the owner of this box can approve what coworkers ask.</p>'
                           '</div>'), 403
    note = ""
    if request.method == "POST":
        aid = str(request.form.get("id") or "")
        if not _ID.match(aid):
            note = "There's nothing waiting with that id."
        else:
            who = str(_who().get("email") or _who().get("id") or "owner")
            out = approvals.decide(aid, request.form.get("do") == "approve", by=who)
            if out.get("ok") or out.get("status") in ("failed", "declined"):
                return redirect(f"{DOOR}?decided={aid}", code=303)
            note = out.get("text") or ""
    body = []
    if note:
        body.append(f'<div class="card"><p>{_esc(note)}</p></div>')
    asked = str(request.args.get("decided") or "")
    decided = approvals.get(asked) if _ID.match(asked) else None
    if decided and not note:
        text = ((decided.get("result") or {}).get("text") or "").strip()
        body.append(f'<div class="card"><h2>{_esc(decided["title"])}: '
                    f'{_esc(_SAID.get(decided["status"], decided["status"]))}</h2>'
                    + (f'<p class="quiet">{_esc(text[:1200])}</p>' if text else "") + '</div>')
    waiting = approvals.waiting()
    if waiting:
        body += [_waiting_card(a) for a in waiting]
    else:
        body.append('<div class="card"><p>Nothing is waiting. When a coworker asks to change something in an app '
                    'you connected, it shows here and on your phone.</p>'
                    '<p><a href="/settings/sources">Data Sources &rarr;</a></p></div>')
    body.append(_history(approvals.recent()))
    return chrome(DOOR, title=_TITLE, lede=LEDE, body="".join(body)), (400 if note else 200)
