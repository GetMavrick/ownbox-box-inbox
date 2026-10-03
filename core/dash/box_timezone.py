"""System Settings → General → Time Zone: the box keeps the buyer's time (plan #1857 H9).

Every sold box shipped `cost.timezone: UTC` and no page set it, so the owner's Morning Review read "As of 1:57 AM".
The zone the buyer's browser gave at claim is the default now (core/report.py `tz_name`); this page is where the
owner changes it. One zone for the whole box: the review's 8 AM, every "As of", the brief and the cost cycle.

US zones first (the buyers are), then every zone the box knows. The current one is selected.
"""
from __future__ import annotations

from flask import request

from core import box_settings, report
from core.dash import blueprint
from core.dash.box_settings import _admit, _back, _esc, _is_owner, _who
from core.dash.home import chrome

DOOR = "/settings/timezone"
_TITLE = "Time Zone"
US = (("America/New_York", "Eastern"), ("America/Chicago", "Central"), ("America/Denver", "Mountain"),
      ("America/Phoenix", "Arizona"), ("America/Los_Angeles", "Pacific"), ("America/Anchorage", "Alaska"),
      ("Pacific/Honolulu", "Hawaii"))


def _all_zones() -> list[str]:
    try:
        from zoneinfo import available_timezones
        zones = available_timezones()
    except Exception:                                    # noqa: BLE001 — no tzdata: the US list still works
        zones = set()
    us = {z for z, _ in US}
    return sorted(z for z in zones if "/" in z and not z.startswith(("Etc/", "SystemV/", "posix/", "right/"))
                  and z not in us)


def _picker(current: str) -> str:
    def opt(z: str, label: str) -> str:
        return f'<option value="{_esc(z)}"{" selected" if z == current else ""}>{_esc(label)}</option>'
    us = "".join(opt(z, f"{name} ({z.split('/')[-1].replace('_', ' ')})") for z, name in US)
    rest = "".join(opt(z, z.replace("_", " ")) for z in _all_zones())
    return ('<form method="post" action="/settings/timezone" class="card">'
            '<label for="tz">Your time zone</label>'
            f'<select id="tz" name="tz"><optgroup label="United States">{us}</optgroup>'
            f'<optgroup label="Everywhere else">{rest}</optgroup></select>'
            '<button type="submit">Save</button></form>')


@blueprint.route(DOOR, methods=["GET", "POST"])
def box_timezone_screen():
    """Show and change the box's one time zone."""
    refuse = _admit(owner_only=False)
    if refuse is not None:
        return refuse
    if not _is_owner():
        return chrome(DOOR, title=_TITLE, lede="This one is the owner's.",
                      body='<div class="card"><p>Only the owner of this box can change its time zone, because '
                           'every time on the box follows it.</p></div>' + _back()), 403
    note = ""
    if request.method == "POST":
        want = str(request.form.get("tz") or "").strip()
        valid = {z for z, _ in US} | set(_all_zones())
        if want in valid:
            box_settings.put(report.TZ_NS, report.TZ_KEY, want, set_by=str(_who().get("id") or "") or None)
            note = (f'<div class="card"><h2>Saved</h2><p>Every time on the box now reads in {_esc(want)}.</p>'
                    '</div>')
        else:
            note = ('<div class="card"><h2>That was not saved</h2><p>Choose a time zone from the list.</p>'
                    '</div>')
    now = report.tz_name()
    body = (note + f'<div class="card"><p>Your Morning Review goes out at 8 AM in this time zone, and every time '
            f'the box shows you is in it. Now: <b>{_esc(now)}</b>, {_esc(report.now_local().strftime("%-I:%M %p"))}.'
            '</p></div>' + _picker(now) + _back())
    return chrome(DOOR, title=_TITLE, lede="The time your box keeps.", body=body), 200
