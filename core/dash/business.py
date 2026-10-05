"""System Settings → Business → Your Business: what the business does, who its customers are, how it plans to grow.

Owner, 2026-10-04: "These people need to have a valuable machine on day one. Not a dumb box." and "By Business
context, I mean information about what the business does, who its customers are, what plans it has to grow with
existing and future products and services and campaigns." Scope: docs/SCOPE_BUSINESS_CONTEXT_AT_SIGNUP.md (#1957), C2.

ONE SCREEN, THE FIRST GROUP IN SYSTEM SETTINGS. Every answer is stored through `core.business_context.put`, the one
home (#1960); this file never touches the store. The light scan's suggestion is shown as a question, "Is this your
website?", and fills nothing until a person says yes.

EVERYTHING ON IT IS ESCAPED. The suggestion is text read from a web page, and the answers are what a person typed.

MOBILE FIRST: one column, every choice a full-width 48px chip until a pointer exists, 16px type in every field. Owner
edits; anyone else on the box reads.

THE HOME CARD. Until the owner has answered (goals, a stage or what they sell), the Base Machine's home screen carries
one card pointing here. It can be dismissed, and dismissed stays dismissed (the no-nag rule).
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from flask import redirect, request

from core import box_settings
from core import business_context as bc
from core.dash import blueprint
from core.dash.box_settings import _admit, _back, _esc, _is_owner, _who
from core.dash.home import chrome

DOOR = "/settings/business"
_TITLE = "Your Business"
# THE PROMISE THAT IS TRUE ONCE THE WELCOME REVIEW READS THE CONTEXT (#1957 C4; OSDev1 on #1963: say only what's true).
_LEDE = "What your business does, who it serves and where it is going. Your Morning Review starts from it."
PLAN_ROWS = 5
# THE HOME CARD'S DISMISSAL is about this screen, not about the business, so it is core's, never the context's.
CARD_NS, CARD_KEY = "core", "business_card_dismissed"

_CSS = (
    ".bz-chips{display:flex;flex-direction:column;gap:8px;margin:8px 0 4px}"
    ".bz-chips label{display:flex;align-items:center;gap:10px;margin:0;min-height:48px;padding:0 14px;"
    "border:1px solid var(--line);border-radius:12px;background:var(--card);font-weight:500;cursor:pointer}"
    ".bz-chips input{width:20px;height:20px;margin:0;flex:none}"
    ".bz-chips label:has(input:checked){border-color:var(--link);background:var(--wash)}"
    ".bz-plan{display:flex;flex-direction:column;gap:8px;margin-bottom:12px}"
    ".bz-hint{color:var(--ink-2);margin:4px 0 0;font-size:calc(15 * var(--px, 1px))}"
    ".bz-err{color:var(--danger)}"
    ".bz-read p{margin:6px 0}"
    "@media (min-width:720px){.bz-chips{flex-direction:row;flex-wrap:wrap}"
    ".bz-chips label{min-height:40px;border-radius:999px}.bz-plan{flex-direction:row}}"
)


def _chips(name: str, choices, chosen, *, kind: str = "checkbox") -> str:
    have = {str(x).lower() for x in (chosen if isinstance(chosen, (list, tuple, set)) else [chosen]) if x}
    return '<div class="bz-chips">' + "".join(
        f'<label><input type="{kind}" name="{name}" value="{_esc(c)}"'
        f'{" checked" if str(c).lower() in have else ""}> {_esc(c[:1].upper() + c[1:])}</label>'
        for c in choices) + "</div>"


def _sells_text(sells) -> str:
    return "\n".join(f"{s['name']} - {s['price']}" if s.get("price") else s["name"] for s in sells or [])


def _parse_sells(text: str) -> list[dict]:
    """One per row: "Botox - $12 a unit", "Hydrafacial: $189" or just "Gift cards"."""
    out = []
    for row in str(text or "").splitlines():
        row = row.strip()
        if not row:
            continue
        m = re.match(r"^(.+?)(?:\s+[-–—]\s+|:\s+)(.+)$", row)
        out.append({"name": m.group(1), "price": m.group(2)} if m else {"name": row, "price": ""})
    return out


def _suggestion_card(ctx: dict) -> str:
    s = ctx.get("suggested") or {}
    if not s.get("website") or ctx.get("website"):
        return ""
    said = "".join(f"<p>{_esc(label)}: {_esc(s[k])}</p>" for k, label in
                   (("name", "Name"), ("area", "Where"), ("hours", "Hours"), ("description", "About"))
                   if s.get(k))
    return ('<div class="card"><h2>Is this your website?</h2>'
            f'<p><b>{_esc(s["website"].removeprefix("https://"))}</b>, from the address you signed in with. '
            'Here is what it says about you:</p>' + said +
            f'<form method="post" action="{DOOR}"><input type="hidden" name="action" value="confirm">'
            '<button type="submit">Yes, that\'s us</button></form>'
            f'<form method="post" action="{DOOR}"><input type="hidden" name="action" value="reject">'
            '<button type="submit" class="ghost">No, it isn\'t</button></form></div>')


def _err(errors: dict, *fields) -> str:
    return "".join(f'<p class="bz-err">{_esc(errors[f])}</p>' for f in fields if errors.get(f))


def _form(ctx: dict, errors: dict) -> str:
    # THE PLANS SO FAR, AND TWO EMPTY ROWS (up to PLAN_ROWS): five blank rows made the page twice as long on a mobile.
    have = list(ctx.get("coming") or [])
    plans = (have + [{"what": "", "month": ""}] * 2)[:max(PLAN_ROWS, len(have))]
    plan_rows = "".join(
        '<div class="bz-plan">'
        f'<input type="text" name="plan_what" aria-label="Planned product, service or campaign" '
        + (f'placeholder="Spring skin package" ' if i == len(have) else "")
        + f'value="{_esc(p.get("what"))}">'
        f'<input type="month" name="plan_month" aria-label="The month it starts" value="{_esc(p.get("month"))}">'
        '</div>' for i, p in enumerate(plans))
    sold = [s["name"] for s in ctx.get("sells") or []]
    err = ('<p class="bz-err">Some answers weren\'t saved. Each one says why, below; everything else was.</p>'
           if errors else "")
    industry = "".join(f'<option value="{_esc(i)}"{" selected" if ctx.get("industry") == i else ""}>'
                       f'{_esc(i[:1].upper() + i[1:])}</option>' for i in bc.INDUSTRIES)
    return (
        f'<form method="post" action="{DOOR}"><input type="hidden" name="action" value="save">'
        + (f'<div class="card">{err}</div>' if err else "")
        + '<div class="card"><h2>What your business does</h2>'
        f'<label for="bz-web">Your website</label><input id="bz-web" type="url" name="website" '
        f'inputmode="url" placeholder="yourbusiness.com" value="{_esc(ctx.get("website"))}">'
        + _err(errors, "website") +
        f'<label for="bz-name">Business name</label><input id="bz-name" type="text" name="name" '
        f'value="{_esc(ctx.get("name"))}">'
        f'<label for="bz-ind">Industry</label><select id="bz-ind" name="industry">'
        f'<option value="">Choose one</option>{industry}</select>' + _err(errors, "industry") +
        f'<label for="bz-area">Where you serve</label><input id="bz-area" type="text" name="area" '
        f'placeholder="South Austin, or online" value="{_esc(ctx.get("area"))}">'
        f'<label for="bz-sells">What you sell</label><textarea id="bz-sells" name="sells" rows="5" '
        f'placeholder="Botox - $12 a unit&#10;Hydrafacial - $189">{_esc(_sells_text(ctx.get("sells")))}</textarea>'
        '<p class="bz-hint">One per row. A price is optional.</p>' + _err(errors, "sells") + '</div>'
        '<div class="card"><h2>Who your customers are</h2>'
        f'<label for="bz-cust">In a few words</label><input id="bz-cust" type="text" name="customers" '
        f'placeholder="Busy professionals who want to look rested" value="{_esc(ctx.get("customers"))}">'
        '<label>Mostly</label>' + _chips("customer_kinds", bc.CUSTOMER_KINDS, ctx.get("customer_kinds")) + '</div>'
        '<div class="card"><h2>How you plan to grow</h2>'
        '<label>What\'s coming</label><p class="bz-hint">A new product, service or campaign, and the month it starts. '
        'Your box prepares for it, and never mentions it to a customer before its month.</p>' + plan_rows
        + _err(errors, "coming")
        + (('<label>What to push now</label>' + _chips("push", sold, ctx.get("push")) + _err(errors, "push"))
           if sold else "")
        + '<label>What do you want most this year?</label><p class="bz-hint">Pick up to 3.</p>'
        + _chips("goals", bc.GOALS, ctx.get("goals")) + _err(errors, "goals")
        + '<label>What should your box take off your hands?</label>'
        + _chips("workflows", bc.WORKFLOWS, ctx.get("workflows")) + '</div>'
        '<div class="card"><h2>Where the business is</h2>'
        + _chips("stage", bc.STAGES, ctx.get("stage"), kind="radio")
        + '<label>Team size</label>' + _chips("team_size", bc.TEAM_SIZES, ctx.get("team_size"), kind="radio")
        + '</div><button type="submit">Save</button></form>')


def _page(url: str) -> str:
    """The page a line came from, as a person reads it: "pricing", or "home page"."""
    path = urlsplit(str(url or "")).path.strip("/")
    return path.rsplit("/", 1)[-1].replace("-", " ") or "home page"


def _profile_card(ctx: dict) -> str:
    """C3: what the business's own website says, each line quoted with the page it came from. '' until the box has
    read the website (core/business_context.py, full_scan)."""
    prof = [p for p in (ctx.get("profile") or []) if str(p.get("source") or "").startswith(("https://", "http://"))]
    if not prof:
        return ""
    groups = []
    for field, heading, _ in bc.PROFILE_FIELDS:
        lines = [p for p in prof if p.get("field") == field]
        if lines:
            groups.append(f"<h3>{_esc(heading)}</h3>" + "".join(
                f'<p>{_esc(p["line"])} <a href="{_esc(p["source"])}" target="_blank" rel="noopener nofollow">'
                f'{_esc(_page(p["source"]))}</a></p>' for p in lines))
    at = str(bc.full_scan_state().get("at") or "")[:10]
    return ('<div class="card bz-read"><h2>What your website says</h2>'
            f'<p class="bz-hint">Read from your website{" on " + _esc(at) if at else ""}. Each line is quoted from '
            'your own site, with the page it came from. The box uses them when it writes for you.</p>'
            + "".join(groups) + "</div>")


def _read_only(ctx: dict) -> str:
    rows = []
    for key, label in (("website", "Website"), ("name", "Name"), ("industry", "Industry"), ("area", "Where"),
                       ("customers", "Customers"), ("stage", "Stage"), ("team_size", "Team")):
        if ctx.get(key):
            rows.append(f"<p>{_esc(label)}: {_esc(ctx[key])}</p>")
    for key, label in (("goals", "Goals"), ("workflows", "Taking off their hands")):
        if ctx.get(key):
            rows.append(f"<p>{_esc(label)}: {_esc(', '.join(ctx[key]))}</p>")
    if ctx.get("sells"):
        rows.append("<p>Sells: " + _esc(", ".join(s["name"] for s in ctx["sells"])) + "</p>")
    body = "".join(rows) or "<p>Nothing yet. The owner fills this in.</p>"
    return f'<div class="card bz-read">{body}<p class="bz-hint">Only the owner can change these.</p></div>'


def _answers(form) -> list[tuple]:
    plans = [{"what": w, "month": m} for w, m in zip(form.getlist("plan_what"), form.getlist("plan_month"))
             if str(w or "").strip()]
    return [
        ("website", form.get("website")), ("name", form.get("name")), ("industry", form.get("industry")),
        ("area", form.get("area")), ("sells", _parse_sells(form.get("sells"))),
        ("customers", form.get("customers")), ("customer_kinds", form.getlist("customer_kinds")),
        ("coming", plans), ("goals", form.getlist("goals")), ("workflows", form.getlist("workflows")),
        ("stage", form.get("stage") or ""), ("team_size", form.get("team_size") or ""),
        ("push", form.getlist("push")),                  # last, so a just-added offer can be pushed
    ]


def _save(form) -> dict:
    """Store every answer on the form. -> {field: the sentence} for what could not be stored; the rest are kept."""
    by = str(_who().get("id") or "owner")
    errors = {}
    for field, value in _answers(form):
        try:
            bc.put(field, value, by=by)
        except ValueError as e:
            errors[field] = str(e)
    return errors


@blueprint.route(DOOR, methods=["GET", "POST"])
def box_business_screen():
    """Show the business context; the owner changes it."""
    refuse = _admit(owner_only=False)
    if refuse is not None:
        return refuse
    owner = _is_owner()
    if request.method == "POST" and not owner:
        return chrome(DOOR, title=_TITLE, lede=_LEDE, body=_read_only(bc.get()) + _back()), 403
    errors: dict = {}
    if request.method == "POST":
        action = request.form.get("action")
        if action == "confirm":
            bc.confirm_suggested(by=str(_who().get("id") or "owner"))
            return redirect(DOOR + "?saved=1", code=303)
        if action == "reject":
            bc.reject_suggested(by=str(_who().get("id") or "owner"))
            return redirect(DOOR, code=303)
        if action == "dismiss":
            box_settings.put(CARD_NS, CARD_KEY, True, set_by=str(_who().get("id") or "") or None)
            return redirect("/dashboard", code=303)
        errors = _save(request.form)
        if not errors:
            return redirect(DOOR + "?saved=1", code=303)
    ctx = bc.get()
    if errors:                                           # WHAT THEY TYPED, NOT THE STORE: nothing vanishes on a refusal
        ctx.update({f: v for f, v in _answers(request.form)})
    saved = ('<div class="card"><h2>Saved.</h2><p>Your next Morning Review starts from it.</p></div>'
             if request.args.get("saved") and not errors else "")
    body = f"<style>{_CSS}</style>" + saved + (
        _suggestion_card(ctx) + _profile_card(ctx) + _form(ctx, errors) if owner
        else _read_only(ctx) + _profile_card(ctx)) + _back()
    return chrome(DOOR, title=_TITLE, lede=_LEDE, body=body), (200 if not errors else 400)


def answered(ctx: dict | None = None) -> bool:
    """Has the owner told the box about the business? Goals, a stage or what it sells is enough."""
    ctx = ctx or bc.get()
    return bool(ctx.get("goals") or ctx.get("stage") or ctx.get("sells"))


def home_card() -> str:
    """The Base Machine home screen's card, until the owner has answered or dismissed it. '' otherwise."""
    try:
        if not _is_owner() or box_settings.get(CARD_NS, CARD_KEY, default=False):
            return ""
        ctx = bc.get()
        if answered(ctx):
            return ""
    except Exception:                                    # noqa: BLE001 — a home card never breaks the home screen
        return ""
    site = (ctx.get("suggested") or {}).get("website") if not ctx.get("website") else ""
    ask = (f'Is <b>{_esc(site.removeprefix("https://"))}</b> your website? ' if site else "")
    return ('<div class="card"><h2>Tell your box about your business</h2>'
            f'<p>{ask}Two minutes: what you sell, who your customers are, and what you want most this year. Your '
            'Morning Review starts from it.</p>'
            # OUTLINED, NOT INK: the dashboard keeps one ink pill (Upgrade, tests/test_the_upgrade_button.py).
            f'<form method="get" action="{DOOR}"><button type="submit" class="ghost">Tell your box</button></form>'
            f'<form method="post" action="{DOOR}"><input type="hidden" name="action" value="dismiss">'
            '<button type="submit" class="ghost">Not now</button></form></div>')
