"""Upgrade your box to Pro: the dashboard's button, the sheet it opens, and the progress line.

docs/SCOPE_UPGRADE_TO_PRO.md §2.2 and §2.4 (owner-approved 2026-09-26), piece 4, assigned to OSDev5
by OSDev1. Owner, 2026-09-26: *"For now we could just have it be a button inside the app dashboard
that says upgrade your box to pro now. It will take X number of minutes of downtime."*

WHAT THIS FILE DOES, AND ONLY THIS. It draws; it never pays, resizes or switches anything:
  · the dashboard card: the button, with the downtime under it, while the plan lacks what Pro adds;
  · the sheet at /dashboard/upgrade: what Pro adds (from the Tiers table, so it is never out of
    date), the price, the restart in plain words, and the way to Stripe in a new tab, carrying
    this box's order id and the owner's email;
  · the progress line, from what the provisioner last told the box (`core.upgrade.status()`).
Stripe, the link itself, the provisioner and the resize are OSDev1's (§4).

OWNER ONLY. The payment page signs in whoever pays, so nobody else on the box can start one; a
member sees neither the card nor the sheet.

ONE HOME. The Settings plan line and the Shifts screen's "Coworkers come with Pro" link here and
have no buttons of their own (§2.2).
"""
from __future__ import annotations

import html as _html
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from flask import request

from core.dash import blueprint
from core.logging import get_logger

log = get_logger(__name__)

SHEET = "/dashboard/upgrade"

# THE ONE NUMBER FOR THE DOWNTIME. 3 until OSDev1 measures it in the test-mode rehearsal (§4, piece
# 5); then it changes here and nowhere else.
OFFLINE_MINUTES = 3

# THE OWNER'S FIGURE (§2.1), charged once.
PRICE = "$1,100"

# THE UPGRADE'S PAYMENT LINK. OSDev1's alone to set (all Stripe work is theirs, owner 2026-09-26):
# the test-mode link for the rehearsal, then the live one after the owner says go. While it is
# empty the sheet says the payment page is not open yet, and offers no button.
UPGRADE_LINK = ""

# WHAT THE BUTTON UPGRADES TO. The one place a tier is named here, because the product is "Upgrade
# to Pro"; what it ADDS is still read from the Tiers table, never listed by hand.
TARGET = "pro"

# A PERSON'S WORDS FOR A FEATURE. A feature not listed is shown by its own name, never blank.
FEATURE_WORDS = {
    "coworkers": ("Coworkers", "AI coworkers that work on a schedule, stay inside what you let "
                               "them touch, and tell you what they did"),
    "machine:aeo": ("AEO Machine", "Helps your business show up in search and in AI answers"),
    "machine:inbox": ("Unified Inbox", "Every customer message in one place, with replies drafted "
                                       "for you"),
}

# §2.4, word for word where the scope gives the words.
_PROGRESS = {
    "paid": ("Payment received", "Your box restarts in about a minute."),
    "restarting": ("Upgrading", "Your box is restarting to finish the upgrade. It's back in "
                                f"about {OFFLINE_MINUTES} minutes."),
    "held": ("Your upgrade is taking longer than it should", "Ownbox has been told."),
    "failed": ("Your upgrade is taking longer than it should", "Ownbox has been told."),
}
DONE_SHOWN_FOR = timedelta(days=1)    # "You're on Pro" stays on the dashboard for a day, then goes


def _esc(v) -> str:
    return _html.escape(str(v if v is not None else ""), quote=True)


def _is_owner() -> bool:
    from core.dash import session_user
    try:
        return ((session_user(request) or {}).get("role") or "") == "owner"
    except Exception:                                # noqa: BLE001 — unreadable is not the owner
        return False


def _tiers():
    """core.tiers (Tiers piece 1), or None on a box that doesn't have it yet."""
    try:
        from core import tiers
        return tiers
    except ImportError:
        return None


def _status() -> dict:
    """What the provisioner last told this box about its upgrade (core.upgrade, #1622), or {}."""
    try:
        from core import upgrade
        return upgrade.status() or {}
    except Exception:                                # noqa: BLE001 — unknown is "nothing said"
        return {}


def adds() -> list:
    """[(title, sentence)] for what Pro gives this box that its plan doesn't have yet. [] when there
    is nothing to add (it is on Pro already, or the plan can't be read)."""
    tiers = _tiers()
    if tiers is None:
        return []
    try:
        cur = tiers.current() or {}
        target = tiers.TIERS[TARGET]
    except Exception:                                # noqa: BLE001 — a dashboard never 500s
        log.exception("upgrade.plan_unreadable")
        return []
    if cur.get("tier") == TARGET:
        return []
    out = []
    for f in sorted(set(target["features"]) - set(cur.get("features") or ())):
        out.append(FEATURE_WORDS.get(f, (f.replace("_", " ").title(), "")))
    have, get = cur.get("people"), target.get("people")
    if get == 0 and have != 0:                        # 0 is unlimited, in the Tiers table's terms
        out.append(("Unlimited people", "Everyone on your team gets their own sign-in"))
    elif isinstance(get, int) and get and isinstance(have, int) and have and get > have:
        out.append((f"Up to {get} people", "Everyone on your team gets their own sign-in"))
    return out


def _name_with_features() -> str:
    """'Pro, including Coworkers', read from the plan the box now holds (§2.4)."""
    tiers = _tiers()
    try:
        cur = tiers.current() if tiers else {}
    except Exception:                                # noqa: BLE001
        cur = {}
    # THE PLAN LINE NAMES WHAT THE TIER SWITCHES ON, not the add-on machines (SCOPE_TIERS §2.7): those
    # have their own screens, and "Pro, including Coworkers" is the line the scope gives.
    names = [FEATURE_WORDS.get(f, (f.title(), ""))[0] for f in sorted(cur.get("features") or ())
             if not str(f).startswith("machine:")]
    return (cur.get("name") or "Pro") + (", including " + " and ".join(names) if names else "")


def _recent(at: str, within: timedelta) -> bool:
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(str(at)) <= within
    except (TypeError, ValueError):
        return False


def dashboard_card() -> str:
    """The card on the dashboard, for the owner: the upgrade under way, done, or the button."""
    if not _is_owner():
        return ""
    st = _status()
    stage = st.get("stage")
    if stage in _PROGRESS:
        head, line = _PROGRESS[stage]
        cls = "card notice" if stage in ("held", "failed") else "card"
        return f'<div class="{cls}" id="upgrade"><h2>{_esc(head)}</h2><p>{_esc(line)}</p></div>'
    if stage == "done" and _recent(st.get("at"), DONE_SHOWN_FOR) and not adds():
        return ('<div class="card" id="upgrade"><h2>You\'re on Pro</h2>'
                f'<p>{_esc(_name_with_features())}.</p></div>')
    if not adds():
        return ""
    return ('<div class="card" id="upgrade"><h2>Pro</h2>'
            f'<a class="btn" href="{SHEET}">Upgrade your box to Pro now</a>'
            f'<p class="quiet" style="margin-top:10px">Your box will be offline for about '
            f'{OFFLINE_MINUTES} minutes.</p></div>')


def _pay_url(order: str, email: str) -> str:
    """The upgrade link, carrying this box's order id and the owner's email into the checkout."""
    q = {"client_reference_id": order}
    if email:
        q["prefilled_email"] = email
    return UPGRADE_LINK + ("&" if "?" in UPGRADE_LINK else "?") + urlencode(q)


@blueprint.route(SHEET, methods=["GET"])
def upgrade_sheet():
    from core import claim, state
    from core.dash import review as _review
    from core.dash.home import chrome
    title = "Upgrade to Pro"
    refuse = _review._admit(owner_only=False)
    if refuse is not None:
        return refuse
    if not _is_owner():
        return chrome(SHEET, title=title, lede="This one is the owner's.",
                      body='<div class="card"><p>Only the owner of this box can upgrade it, '
                           'because the payment is theirs.</p></div>'), 403
    lede = "What Pro adds to this box, what it costs, and what happens when you pay."
    back = '<div class="foot"><a href="/dashboard">&larr; Base Machine</a></div>'
    st = _status()
    if st.get("stage") in _PROGRESS:
        head, line = _PROGRESS[st["stage"]]
        return chrome(SHEET, title=title, lede=lede,
                      body=f'<div class="card"><h2>{_esc(head)}</h2><p>{_esc(line)}</p></div>'
                           + back), 200
    what = adds()
    if not what:
        return chrome(SHEET, title=title, lede=lede,
                      body=f'<div class="card"><p>This box is on {_esc(_name_with_features())} '
                           'already.</p></div>' + back), 200

    items = "".join(f"<li><b>{_esc(t)}</b>" + (f". {_esc(s)}." if s else "") + "</li>"
                    for t, s in what)
    body = ('<div class="card"><h2>What Pro adds</h2>'
            f'<ul>{items}</ul></div>'
            '<div class="card"><h2>What happens when you pay</h2>'
            f'<p><b>{_esc(PRICE)}</b>, paid once. Your box keeps everything on it.</p>'
            f'<p>Your box restarts once, for about {OFFLINE_MINUTES} minutes, right after you '
            'pay. We email you when Pro is on.</p>')
    order = claim.provisioned_order() or ""
    try:
        email = str(state.owner_user().get("email") or "")
    except Exception:                                # noqa: BLE001 — the checkout asks instead
        email = ""
    if not order:
        # A BOX WITH NO ORDER CAN'T BE NAMED IN A PAYMENT, and a payment that names no box is held
        # by the provisioner (§2.3). Better said here than discovered after paying.
        body += ('<p>This box has no order on record, so it can\'t be upgraded from here. '
                 'Ask Ownbox support and we\'ll do it for you.</p></div>')
    elif not UPGRADE_LINK:
        body += '<p class="quiet">The payment page isn\'t open yet.</p></div>'
    else:
        body += ('<p class="quiet">When you\'ve paid, your dashboard shows the upgrade\'s '
                 'progress.</p>'
                 f'<a class="btn" href="{_esc(_pay_url(order, email))}" target="_blank" '
                 'rel="noopener">Go to payment</a></div>')
    return chrome(SHEET, title=title, lede=lede, body=body + back), 200
