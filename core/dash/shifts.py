"""Shifts: the owner's coworkers, when they work, and what became of each run.

docs/SCOPE_SHIFTS.md §7, piece 4 (this screen) and piece 5's permission sheet. Its done-when: *a
buyer creates the gym example on a mobile device and gets its first report without touching a
file.* So everything a coworker file holds that an owner decides is here, in plain words: its name,
its job, its times, what it may touch, the §4.3 OK, on or off, and a trial run. What a partner
decides (steps, limits, `from:`) is shown, never edited, and survives every save.

NOTHING HERE RUNS ANYTHING. The clock and the runner are OSDev4's (`core.coworkers.runner`); this
screen writes the files they read and reads the ledger they write. Run now only queues a trial,
which the next tick starts like any shift, so a tap here can never run beside another coworker.

THREE STATES, FROM THE PLAN (docs/SCOPE_TIERS.md §2.1, owner-approved 2026-09-26; OSDev1 assigned
them to this screen). *Paid for is not the same as ready*, so the screen tells them apart:
  · not in your plan: "Coworkers come with Pro", and the upgrade. Never a 404 and never hidden,
    because this screen is where the upgrade explains itself (§2.3);
  · in your plan, needs setting up: it names what is missing, the same things the runner's own
    preflight checks for the whole box (a working AI account, the shift clock);
  · working.
The plan is asked of `core.tiers` only, `allows("coworkers")` and `current()`, never a tier's name:
§0's rule, so a tier that gains or loses Coworkers is a change to one table and not to this file.

OWNER ONLY, WHOLLY. A coworker is granted the box's data, so choosing what it may read is the
owner's alone, and a member is refused the way the AI coworkers page refuses one.
"""
from __future__ import annotations

import html as _html
import os
import re
import shutil
from datetime import datetime, timedelta

from flask import redirect, request

from core.dash import blueprint
from core.dash.home import chrome
from core.logging import get_logger

log = get_logger(__name__)

HOME = "/settings/shifts"
NEW = HOME + "/new"
REPORT_LINK = "/shifts/"          # where the runner's report notification opens (runner.report)
# ITS MENU ROW IS IN SYSTEM SETTINGS, beside AI coworkers (`core/dash/home.py`), so its pages live
# under /settings, which is how the menu knows where they are. A row of its own
# in the menu's top group would change the order the owner set on 2026-09-24 (Base Machine, System
# Settings, then the add-on machines), which `test_the_menu_has_the_box_then_its_machines` holds.
# Promoting it is a proposal for him, not a change made here.

_DAY_CHOICES = (("Mon-Fri", "Weekdays"), ("Daily", "Every day"), ("Sat-Sun", "Weekends"),
                ("Mon", "Mondays"), ("Tue", "Tuesdays"), ("Wed", "Wednesdays"),
                ("Thu", "Thursdays"), ("Fri", "Fridays"), ("Sat", "Saturdays"), ("Sun", "Sundays"))
_DAY = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_WEB = ("web:search", "web:read")
_BLANK_ROWS = 1                  # an empty shift row under the filled ones: "add another time"
_SHIFT_ROWS_MAX = 6              # the screen's own bound; the file allows 24, set by hand

# THE EXAMPLE IS A STUDIO'S, the gym example the done-when names (the scope never wrote it down;
# OSDev5's reading, asked on the wall): trial members' messages, answered each weekday morning.
EXAMPLE_TITLE = "Trial follow-up"
EXAMPLE_JOB = ("Every weekday morning, read the messages that came in since yesterday from people "
               "on a free trial. For each one that asks about classes, prices or times, draft a "
               "friendly reply for me to approve. Tell me who you drafted for.")

_OUTCOME = {"DONE": ("ok", "Done"), "FAILED": ("bad", "Failed"), "MISSED": ("warn", "Missed"),
            "queued": ("ink", "Waiting"), "starting": ("ink", "Starting"),
            "running": ("ink", "Running")}

# ONE SMALL BLOCK FOR THE RUN LIST, written without the reserved nouns (see the vocabulary test):
# `font` shorthand instead of the two-word height property, `var(--faint)` for the quiet dot.
_CSS = """<style>
.sh-list{padding:4px 16px}
.sh{display:grid;grid-template-columns:auto 1fr auto;gap:2px 10px;padding:10px 0;
border-bottom:1px solid var(--hairline);color:inherit;text-decoration:none}
.sh:last-child{border-bottom:0}
.sh-d{grid-row:1/4;width:9px;height:9px;border-radius:99px;margin-top:7px;background:var(--faint)}
.sh-d.ok{background:var(--ok)}.sh-d.warn{background:var(--warn)}.sh-d.bad{background:var(--bad)}
.sh-d.ink{background:var(--ink)}
.sh-t{grid-column:2;font:600 15.5px/1.3 var(--sans);min-width:0;overflow:hidden;
text-overflow:ellipsis;white-space:nowrap}
.sh-w{grid-column:3;font-size:13px;color:var(--ink-3);white-space:nowrap;padding-top:2px}
.card .sh-p{grid-column:2/4;margin:0;font:400 15px/1.4 var(--sans);color:var(--ink-2);
max-height:2.8em;overflow:hidden}
.sh-s{grid-column:2/4;font-size:14px;color:var(--ink-3)}
.sh-when{display:grid;grid-template-columns:1fr 1fr;gap:0 12px;padding:8px 0 4px;
border-top:1px solid var(--hairline)}
.sh-when:first-of-type{border-top:0}
.sh-when .full{grid-column:1/3}
.sh-when label{margin:10px 0 6px}
.sh-h{margin:22px 2px 10px;font-size:17px;font-weight:600}
.card .sh-p.full{max-height:none}
.sh-sheet h3{margin:16px 0 4px}
.sh-sheet ul{margin:0 0 8px}
</style>"""


def _esc(v) -> str:
    return _html.escape(str(v if v is not None else ""), quote=True)


def _cw():
    """The coworker modules, imported when a page is served: they reach into the runner, which
    reaches into brain and notify, and none of that belongs in the dashboard's import."""
    from core.coworkers import contract, hire, runner, runs, schedule
    return contract, hire, runner, runs, schedule


def _who() -> dict:
    from core import dash
    try:
        return dash.session_user(request) or {}
    except Exception:                                # noqa: BLE001 — unreadable is nobody
        return {}


def _by() -> str:
    """Who is acting, for the OK's audit line (hire.acknowledge(by=...))."""
    return str(_who().get("id") or "owner")


def _gate(path: str, title: str, *, change: bool = False):
    """(refusal, why-not-pro, is_owner). Anyone signed in may look; only the owner changes.

    A MEMBER SEES, AND CHANGES NOTHING. Choosing what a coworker may read is the owner's alone, so
    every form and button here is the owner's, and a member is refused at each (`change=True`).
    Looking is open because the menu row is: a row that refuses a member is a door the walk test
    forbids, and a member's box has the same Settings menu.
    """
    from core.dash import review as _review
    refuse = _review._admit(owner_only=False)
    if refuse is not None:
        return refuse, "", False
    owner = (_who().get("role") or "") == "owner"
    if change and not owner:
        return (chrome(path, title=title, lede="This one is the owner's.",
                       body='<div class="card"><p>Only the owner of this box can change its '
                            'coworkers, because each one is given some of the box\'s data.</p>'
                            '</div>'), 403), "", False
    return None, _not_in_plan(), owner


def _page(path: str, title: str, lede: str, body: str):
    return chrome(path, title=title, lede=lede, body=_CSS + body)


# WHERE THE UPGRADE IS BOUGHT. The upgrade itself (§2.5) and all Stripe work are OSDev1's; until
# that scope gives it an address of its own, this is where Ownbox's plans are compared.
UPGRADE_URL = "https://www.ownbox.io/#pricing"
FEATURE = "coworkers"


def _not_in_plan() -> str:
    """"" when this box's plan includes coworkers, else the name of the plan it is on ("Base").

    BEFORE core/tiers.py EXISTS there is no plan to ask, and the answer is "not in your plan": a
    screen that sells a feature the box can't be shown to have is the smaller harm. This branch
    only matters until OSDev4's tiers PR lands, which merges before this screen does.
    """
    try:
        from core import tiers
    except ImportError:
        return "this plan"
    try:
        if tiers.allows(FEATURE):
            return ""
        cur = tiers.current() or {}
        return (cur.get("name") or (getattr(tiers, "TIERS", {}).get(cur.get("tier")) or {})
                .get("name") or "this plan")
    except Exception:                                # noqa: BLE001 — a settings screen never 500s
        log.exception("shifts.plan_unreadable")
        return "this plan"


def _upgrade_card(plan: str) -> str:
    """Not in your plan: what coworkers are, and the way to them. Its link is the one ink pill."""
    on = f"This box is on {plan}." if plan != "this plan" else ""
    return ('<div class="card"><h2>Coworkers come with Pro</h2>'
            f'<p>{_esc(on)} With Pro, you write a coworker\'s job in plain words and give it its '
            'times. It starts on time, every time, and tells you what it did.</p>'
            f'<a class="btn" href="{_esc(UPGRADE_URL)}" target="_blank" rel="noopener">'
            'See Pro</a></div>')


def _missing(owner: bool) -> list:
    """In the plan, but what the box still needs before any coworker can work: [(sentence, link)].

    THE SAME CHECKS THE RUNNER MAKES for the whole box before any run (its preflight), asked here
    so the owner hears about them before a shift fails on them rather than after.
    """
    out = []
    try:
        from core import brain
        ready, _ = brain.can_think()
    except Exception:                                # noqa: BLE001 — unreadable is not ready
        ready = False
    if not ready:
        out.append(("Connect an AI account. Coworkers do their jobs with it.",
                    "/settings/ai" if owner else ""))
    _, _, runner, _, _ = _cw()
    try:
        pulse = runner.health() or {}
    except Exception:                                # noqa: BLE001
        pulse = {}
    if pulse.get("tick") in ("stale", "off", "error"):
        out.append(("The shift clock isn't running on this box, so no coworker can start. "
                    "Updating the box starts it; if it stays off, ask Ownbox support.", ""))
    return out


def _setup_card(missing: list, *, pill: bool = True) -> str:
    """In your plan, needs setting up. On the list, the first thing to do is the one ink pill; on
    a coworker's own page, whose pill is already taken, it is a link."""
    items = "".join(f"<li>{_esc(t)}</li>" for t, _ in missing)
    first = next((h for _, h in missing if h), "")
    btn = ""
    if first:
        btn = (f'<a class="btn" href="{_esc(first)}">Connect an AI account</a>' if pill else
               f'<div class="foot"><a href="{_esc(first)}">Connect an AI account &rarr;</a></div>')
    return ('<div class="card notice"><h2>Coworkers are in your plan. Before they can work:</h2>'
            f'<ul>{items}</ul>{btn}</div>')


# ── words ────────────────────────────────────────────────────────────────────────────────────────

def _days_word(days: frozenset) -> str:
    if days == frozenset(range(7)):
        return "Every day"
    if days == frozenset(range(5)):
        return "Weekdays"
    if days == frozenset({5, 6}):
        return "Weekends"
    return ", ".join(_DAY[d] for d in sorted(days))


def _days_value(days: frozenset) -> str:
    """The file's spelling for a set of days, so an untouched shift saves as it was."""
    if days == frozenset(range(7)):
        return "Daily"
    if days == frozenset(range(5)):
        return "Mon-Fri"
    if days == frozenset({5, 6}):
        return "Sat-Sun"
    return ",".join(_DAY[d] for d in sorted(days))


def _when_words(cw) -> str:
    """'Weekdays at 07:30 and 17:00; Sat at 09:00': one phrase per set of days, in file order."""
    groups: dict = {}
    for s in cw.shifts:
        groups.setdefault(s.days, []).append(s.start)
    return "; ".join(f"{_days_word(d)} at " + " and ".join(ts) for d, ts in groups.items())


def _stamp(iso: str | None, tz) -> str:
    """'Today 07:41', 'Mon 28 Sep 07:41': the owner's own clock."""
    if not iso:
        return ""
    try:
        d = datetime.fromisoformat(iso).astimezone(tz)
    except (TypeError, ValueError):
        return ""
    now = datetime.now(tz)
    if d.date() == now.date():
        return f"Today {d:%H:%M}"
    if d.date() == (now + timedelta(days=1)).date():
        return f"Tomorrow {d:%H:%M}"
    return f"{d:%a} {d.day} {d:%b %H:%M}"


def _next_start(cw, tz):
    """The next start this coworker will get, looking a week ahead. None when it is off."""
    _, _, _, _, schedule = _cw()
    now = datetime.now(tz)
    for i in range(8):
        for s in schedule.slots_on(cw, (now + timedelta(days=i)).date(), tz):
            if s.start > now:
                return s.start
    return None


def _runs_of(slug: str, limit: int = 10) -> list:
    """This coworker's runs, newest first. The ledger's own reader, never a second query."""
    _, _, _, runs, _ = _cw()
    rows = [r for r in runs.by_status("queued", *runs.ACTIVE, *runs.FINAL) if r["coworker"] == slug]
    rows.sort(key=lambda r: (r.get("window_start") or "", r["slot"]), reverse=True)
    return rows[:limit]


def _receipt(row: dict) -> dict:
    import json
    try:
        return json.loads(row.get("receipt") or "{}") or {}
    except ValueError:
        return {}


def _capabilities(chosen=()) -> list:
    """[(capability, words)] the owner may grant: every read the box's tools offer, drafting for
    approval, and the web. Anything already granted is kept in the list, so a capability whose
    machine went away is still visible, and can be taken away."""
    _, hire, _, _, _ = _cw()
    caps = set()
    try:
        from core.connector import tools
        for spec in tools.registry().values():
            c = str(spec.get("capability") or "")
            if c.startswith("read:") or c == "write:proposals":
                caps.add(c)
    except Exception:                                # noqa: BLE001 — a form never 500s
        pass
    caps.update(c for c in chosen if not c.startswith("act:"))
    reads = sorted(c for c in caps if c.startswith("read:"))
    # `hire._want` holds the owner's words for each capability, and one list of them is the point:
    # the permission sheet and this form must never describe the same grant two ways.
    return [(c, hire._want(c)) for c in reads + ["write:proposals"] + list(_WEB)]


# ── the list ─────────────────────────────────────────────────────────────────────────────────────

def _row(href: str, tone: str, title: str, word: str, text: str = "", detail: str = "",
         *, link: bool = True, full: bool = False) -> str:
    tag, attr = ("a", f' href="{_esc(href)}"') if link else ("div", "")
    return (f'<{tag} class="sh"{attr}><span class="sh-d {tone}"></span>'
            f'<span class="sh-t">{_esc(title)}</span><span class="sh-w">{_esc(word)}</span>'
            + (f'<p class="sh-p{" full" if full else ""}">{_esc(text)}</p>' if text else "")
            + (f'<span class="sh-s">{_esc(detail)}</span>' if detail else "") + f'</{tag}>')


@blueprint.route("/shifts", methods=["GET"])
@blueprint.route(REPORT_LINK, methods=["GET"])
def shifts_report_link():
    """A report's notification opens /shifts/ (SCOPE_SHIFTS §7: the push allowlist names it)."""
    return redirect(HOME, code=302)


@blueprint.route(HOME, methods=["GET"])
def shifts_home():
    refuse, not_in_plan, owner = _gate(HOME, "Shifts")
    if refuse is not None:
        return refuse
    lede = "Your AI coworkers, when they work, and what each one did."
    if not_in_plan:
        return _page(HOME, "Shifts", lede, _upgrade_card(not_in_plan)), 200
    contract, hire, runner, _, _ = _cw()
    tz = runner.box_tz()
    good, bad = runner.discover()
    body = _said()
    missing = _missing(owner)
    if missing:
        body += _setup_card(missing)

    rows = []
    for cw in good:
        last = next((r for r in _runs_of(cw.slug, 3)), None)
        tone, said = _OUTCOME.get(last["status"], ("", "")) if last else ("", "")
        if not cw.enabled:
            word = "Off"
        elif not hire.acknowledged(cw):
            word, tone = "Needs your OK", "warn"
        else:
            word = "On"
        nxt = _next_start(cw, tz) if cw.enabled else None
        bits = [f"Next: {_stamp(nxt.isoformat(), tz)}" if nxt else ""]
        if last:
            bits.append(f"Last: {said}, {_stamp(last.get('ended_at') or last['window_start'], tz)}")
        rows.append(_row(f"{HOME}/{cw.slug}", tone, cw.title, word, _when_words(cw),
                         " · ".join(b for b in bits if b)))
    for slug, why in bad:
        rows.append(_row("", "bad", slug, "Can't run", "; ".join(why[:2]),
                         "Fix its file, or remove its folder.", link=False))
    if rows:
        body += '<div class="card sh-list">' + "".join(rows) + '</div>'
    else:
        body += ('<div class="card"><h2>No coworkers yet</h2><p>A coworker is an AI worker with '
                 'a job in plain words and its own times. It starts on time, stays inside what '
                 'you let it touch, and tells you what it did.</p></div>')

    offers = [o for o in hire.offers() if not o["hired"]] if owner else []
    if offers:
        body += '<h2 class="sh-h">Ready to hire</h2><div class="card sh-list">'
        for o in offers:
            cw = o["coworker"]
            if cw is None:
                body += _row("", "", o["name"], "Can't hire", o["reason"],
                             f"Shipped by {o['machine']}", link=False)
            else:
                body += _row(f"{HOME}/hire/{o['machine']}/{o['name']}", "", cw.title, "Hire",
                             _when_words(cw), f"Shipped by {o['machine']}")
        body += '</div>'

    if owner:
        # ONE INK PILL: setting the box up comes first, so New coworker waits as a link until then.
        body += ('<div class="card"><a class="btn" href="' + NEW + '">New coworker</a></div>'
                 if not any(h for _, h in missing) else
                 f'<div class="foot"><a href="{NEW}">New coworker &rarr;</a></div>')
    return _page(HOME, "Shifts", lede, body), 200


_SAID = {"saved": "Saved.", "on": "Switched on. It starts at its next time.",
         "off": "Switched off. It won't start until you switch it on.",
         "queued": ("Its trial run is queued, and starts within a minute or two. Its steps do "
                    "nothing outward, and you'll get its report."),
         "okd": "Thanks. It can run now.", "hired": "Hired, and switched on.",
         "removed": "Removed."}


def _said() -> str:
    s = request.args.get("said") or ""
    return f'<div class="card"><p>{_esc(_SAID[s])}</p></div>' if s in _SAID else ""


# ── one coworker ─────────────────────────────────────────────────────────────────────────────────

def _load(slug: str):
    contract, _, runner, _, _ = _cw()
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,40}", slug or ""):
        return None, ["no such coworker"]
    folder = runner.coworkers_dir() / slug
    if not (folder / contract.FILE).is_file():
        return None, ["no such coworker"]
    return contract.load(folder)


def _post(action: str, do: str, label: str, cls: str = "") -> str:
    c = f' class="{cls}"' if cls else ""
    return (f'<form method="post" action="{_esc(action)}"><input type="hidden" name="do" '
            f'value="{do}"><button type="submit"{c}>{_esc(label)}</button></form>')


def _sheet_html(sheet: dict) -> str:
    def lst(items):
        return "<ul>" + "".join(f"<li>{_esc(i[:1].upper() + i[1:])}</li>" for i in items) + "</ul>"
    out = '<div class="sh-sheet">'
    out += "<h3>It may</h3>" + (lst(sheet["wants"]) if sheet["wants"] else
                                "<p>Nothing on the box. Only its job.</p>")
    out += "<h3>It can't</h3>" + lst(sheet["cannot"])
    if sheet["box_does"]:
        out += "<h3>The box does for it</h3>" + lst(sheet["box_does"])
    out += "<h3>It works</h3>" + lst(sheet["works"])
    return out + "</div>"


@blueprint.route(HOME + "/<slug>", methods=["GET", "POST"])
def shifts_one(slug: str):
    path = f"{HOME}/{slug}"
    refuse, not_in_plan, owner = _gate(path, "Shifts", change=request.method == "POST")
    if refuse is not None:
        return refuse
    if not_in_plan:
        return _page(path, "Shifts", "", _upgrade_card(not_in_plan)), 200
    contract, hire, runner, runs, _ = _cw()
    cw, why = _load(slug)
    if cw is None:
        return _page(path, "Not found", "There is no coworker by that name on this box.",
                     f'<div class="card"><a href="{HOME}">&larr; Shifts</a></div>'), 404
    if request.method == "POST":
        do = (request.form.get("do") or "").strip()
        try:
            if do in ("on", "off"):
                _set_enabled(cw, do == "on")
            elif do == "ok":
                hire.acknowledge(cw, by=_by())
            elif do == "run":
                if not hire.acknowledged(cw):
                    raise ValueError("It needs your OK before it can run.")
                runner.start_now(cw.slug, dry_run=True)
            elif do == "remove":
                _remove(cw)
                return redirect(f"{HOME}?said=removed", code=303)
            else:
                return redirect(path, code=303)
        except ValueError as e:
            return _detail(cw, note=str(e), owner=True), 400
        said = {"on": "on", "off": "off", "ok": "okd", "run": "queued"}[do]
        return redirect(f"{path}?said={said}", code=303)
    return _detail(cw, owner=owner), 200


def _detail(cw, *, note: str = "", owner: bool = True) -> str:
    contract, hire, runner, _, _ = _cw()
    tz = runner.box_tz()
    path = f"{HOME}/{cw.slug}"
    body = _said() + (f'<div class="card notice"><p>{_esc(note)}</p></div>' if note else "")
    missing = _missing(owner)
    if missing:
        body += _setup_card(missing, pill=False)
    needs_ok = not hire.acknowledged(cw)

    # THE ONE INK PILL is the thing to do next: the OK if it needs one, else switching it on,
    # else trying it. Everything else on the screen is a ghost, a link, or folded away.
    if needs_ok:
        body += ('<div class="card notice"><h2>It needs '
                 + ("your" if owner else "the owner\'s") + ' OK to run</h2>'
                 f'<p>{_esc(hire.RISK)}</p>'
                 + (_post(path, "ok", "OK, let it run") if owner else "") + '</div>')
    state_card = '<div class="card">'
    nxt = _next_start(cw, tz) if cw.enabled else None
    if cw.enabled:
        state_card += (f'<h2>On</h2><p>{_esc(_when_words(cw))}.'
                       + (f' Next: {_esc(_stamp(nxt.isoformat(), tz))}.' if nxt else "") + '</p>')
        if not needs_ok and owner:
            state_card += ('<p class="quiet">Try it now for a trial run: it does its job, its '
                           'steps do nothing outward, and you get its report.</p>'
                           + _post(path, "run", "Try it now"))
        state_card += _post(path, "off", "Switch off", "ghost") if owner else ""
    else:
        state_card += (f'<h2>Off</h2><p>{_esc(_when_words(cw))}, once it is switched on.</p>'
                       + (_post(path, "on", "Switch on", "ghost" if needs_ok else "") if owner else ""))
    body += state_card + '</div>'

    rows = []
    for r in _runs_of(cw.slug):
        tone, word = _OUTCOME.get(r["status"], ("", r["status"]))
        rc = _receipt(r)
        when = _stamp(r.get("started_at") or r["window_start"], tz)
        bits = ["Trial run" if r.get("dry_run") else "Shift"]
        u = rc.get("usage") or {}
        if rc.get("outcome") in ("DONE", "FAILED") and u:
            bits.append(f"{u.get('minutes', 0):.0f} min, {u.get('turns', 0)} turns")
        text = rc.get("reason") or (r.get("note") or "")
        # IN FULL HERE: the list keeps two rows of it, and this page is where it is read.
        rows.append(_row("", tone, when, word, text, " · ".join(bits), link=False, full=True))
    body += '<h2 class="sh-h">Last runs</h2>'
    body += ('<div class="card sh-list">' + "".join(rows) + '</div>' if rows else
             '<div class="card"><p class="quiet">No runs yet. Each run ends Done, Failed or '
             'Missed, and you get a report for every one.</p></div>')

    sheet = hire.sheet(cw, machine=cw.source)
    body += ('<div class="card"><h2>What it may do</h2>' + _sheet_html(sheet)
             + f'<details><summary>Its job</summary><p style="white-space:pre-wrap">'
             f'{_esc(_job_text(cw))}</p></details>'
             + (f'<div class="foot"><a href="{path}/edit">Change its job, times or access '
                '&rarr;</a></div>' if owner else "") + '</div>')
    if owner:
        body += ('<div class="card"><details><summary>Remove this coworker</summary>'
                 '<p>Its file and job are deleted from this box. Its past reports stay in your '
                 'email.</p>' + _post(path, "remove", "Remove " + cw.title, "danger")
                 + '</details></div>')
    body += f'<div class="foot"><a href="{HOME}">&larr; Shifts</a></div>'
    return _page(path, cw.title, "Shipped by " + cw.source if cw.source != "my"
                 else "One of your coworkers.", body)


def _job_text(cw) -> str:
    _, _, runner, _, _ = _cw()
    try:
        return (runner.coworkers_dir() / cw.slug / cw.job).read_text(encoding="utf-8")
    except OSError:
        return ""


# ── writing the file ─────────────────────────────────────────────────────────────────────────────

def _raw(slug: str) -> dict:
    import yaml
    _, _, runner, _, _ = _cw()
    return yaml.safe_load((runner.coworkers_dir() / slug / "coworker.yaml")
                          .read_text(encoding="utf-8")) or {}


def _save(slug: str, data: dict, job_name: str, job_text: str, *, new: bool):
    """Write the two files only if they validate together. (Coworker, []) or (None, reasons).

    CHECKED BEFORE ANYTHING CHANGES: both files are written to a dot-folder the tick never reads
    and loaded by the contract there. Only then do they replace the real ones, each in one rename,
    so the tick never reads half a coworker. The folder itself is never swapped, so anything else
    in it (its workspace, a file set by hand) survives an edit.
    """
    import yaml
    contract, _, runner, _, _ = _cw()
    base = runner.coworkers_dir()
    stage = base / ".saving" / slug
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    try:
        (stage / contract.FILE).write_text(yaml.safe_dump(data, sort_keys=False),
                                           encoding="utf-8")
        (stage / job_name).write_text(job_text, encoding="utf-8")
        cw, why = contract.load(stage)
        if cw is None:
            return None, why
        target = base / slug
        if new:
            if target.exists():
                return None, [f"There is already a coworker called {slug}."]
            stage.rename(target)
        else:
            os.replace(stage / job_name, target / job_name)
            os.replace(stage / contract.FILE, target / contract.FILE)
        return contract.load(target)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _set_enabled(cw, on: bool) -> None:
    data = _raw(cw.slug)
    data["enabled"] = bool(on)
    saved, why = _save(cw.slug, data, cw.job, _job_text(cw), new=False)
    if saved is None:
        raise ValueError("It could not be switched: " + "; ".join(why[:2]))


def _remove(cw) -> None:
    from core import box_settings
    _, hire, runner, _, _ = _cw()
    shutil.rmtree(runner.coworkers_dir() / cw.slug)
    # THE OK GOES WITH IT. It is kept per name, so a later coworker given the same name must be
    # asked again rather than inherit this one's.
    box_settings.clear("coworkers", hire._key(cw))


def _slug_for(title: str) -> str:
    _, _, runner, _, _ = _cw()
    s = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")[:36].strip("-")
    if not s or not s[0].isalpha():
        s = ("coworker-" + s).strip("-")
    if len(s) < 2:
        s = "coworker"
    base, n = s, 2
    # "new" and "hire" are this screen's own addresses; a coworker named either would be unreachable
    while s in ("new", "hire") or (runner.coworkers_dir() / s).exists():
        s = f"{base}-{n}"
        n += 1
    return s


_HHMM = re.compile(r"([01]?[0-9]|2[0-3]):[0-5][0-9]")


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _read_form() -> tuple:
    """(fields, reasons). The plain-word checks a person needs; the contract checks the rest."""
    f = request.form
    why = []
    title = (f.get("title") or "").strip()
    job = (f.get("job") or "").replace("\r\n", "\n").strip()
    if not title:
        why.append("Give it a name.")
    if not job:
        why.append("Say what its job is, in a sentence or two.")
    shifts = []
    for i in range(_SHIFT_ROWS_MAX):
        days, start, latest = (f.get(f"days{i}") or "", (f.get(f"start{i}") or "").strip(),
                               (f.get(f"latest{i}") or "").strip())
        if not start and not latest:
            continue
        if not _HHMM.fullmatch(start):
            why.append(f"Time {i + 1}: give a start time, like 07:30.")
            continue
        if not latest:                               # left blank: a quarter of an hour's grace
            m = min(_minutes(start) + 15, 23 * 60 + 59)
            latest = f"{m // 60:02d}:{m % 60:02d}"
        if not _HHMM.fullmatch(latest) or _minutes(latest) - _minutes(start) < 5:
            why.append(f"Time {i + 1}: the skip time must be at least 5 minutes after the start, "
                       "on the same day.")
            continue
        shifts.append({"days": days or "Mon-Fri", "start": start.zfill(5), "latest": latest.zfill(5)})
    if not shifts:
        why.append("Give it at least one time to start.")
    may = sorted(set(f.getlist("may")))
    return {"title": title, "job": job, "shifts": shifts, "may": may,
            "enabled": bool(f.get("enabled")), "risk_ok": bool(f.get("risk_ok"))}, why


def _form(action: str, v: dict, *, cw=None, errors=()) -> str:
    contract, hire, _, _, _ = _cw()
    err = ""
    if errors:
        err = ('<div class="card notice"><p><b>Not saved yet.</b></p><ul>'
               + "".join(f"<li>{_esc(e)}</li>" for e in errors) + '</ul></div>')
    out = err + f'<form method="post" action="{_esc(action)}">'
    out += ('<div class="card"><h2>Its job</h2>'
            '<label for="title">Name</label>'
            f'<input id="title" name="title" maxlength="{contract.TITLE_MAX}" required '
            f'placeholder="{_esc(EXAMPLE_TITLE)}" value="{_esc(v.get("title"))}">'
            '<label for="job">What it does, in plain words</label>'
            f'<textarea id="job" name="job" rows="7" required placeholder="{_esc(EXAMPLE_JOB)}">'
            f'{_esc(v.get("job"))}</textarea></div>')

    rows = list(v.get("shifts") or []) + [{} for _ in range(_BLANK_ROWS)]
    rows = rows[:_SHIFT_ROWS_MAX]
    out += ('<div class="card"><h2>When it works</h2><p class="sub">In this box\'s time. If it '
            'can\'t start by the skip time, that run is skipped and you are told why.</p>')
    for i, s in enumerate(rows):
        chosen = s.get("days") or "Mon-Fri"
        choices = list(_DAY_CHOICES)
        if chosen not in dict(choices):
            choices.insert(0, (chosen, chosen.replace(",", ", ")))
        opts = "".join(f'<option value="{_esc(k)}"{" selected" if k == chosen else ""}>'
                       f'{_esc(t)}</option>' for k, t in choices)
        blank = not s
        out += (f'<div class="sh-when"><div class="full"><label for="days{i}">'
                f'{"Another time (optional)" if blank and i else "Days"}</label>'
                f'<select id="days{i}" name="days{i}">{opts}</select></div>'
                f'<div><label for="start{i}">Start at</label><input type="time" id="start{i}" '
                f'name="start{i}" value="{_esc(s.get("start"))}"'
                f'{" required" if i == 0 else ""}></div>'
                f'<div><label for="latest{i}">Skip if not started by</label><input type="time" '
                f'id="latest{i}" name="latest{i}" value="{_esc(s.get("latest"))}"></div></div>')
    out += '</div>'

    chosen_may = set(v.get("may") or ())
    caps = _capabilities(chosen_may)
    out += ('<div class="card"><h2>What it may do</h2><p class="sub">Only what you tick. It can '
            'never send, publish or pay for anything, or see your keys.</p>')
    for c, words in caps:
        out += (f'<label class="consent"><input type="checkbox" name="may" value="{_esc(c)}"'
                f'{" checked" if c in chosen_may else ""}><span>{_esc(words[:1].upper() + words[1:])}'
                '</span></label>')
    out += (f'<p class="quiet" style="margin-top:16px">{_esc(hire.RISK)} If you let it do both, '
            'tick this too:</p><label class="consent"><input type="checkbox" name="risk_ok" '
            f'value="1"{" checked" if v.get("risk_ok") else ""}><span>I understand, and it may '
            'read the box and reach the web.</span></label></div>')

    out += ('<div class="card"><label class="consent" style="margin-top:0"><input type="checkbox" '
            f'name="enabled" value="1"{" checked" if v.get("enabled", True) else ""}><span>'
            'Switched on: it starts at its times</span></label>'
            '<button type="submit">Save</button></div></form>')
    return out


@blueprint.route(NEW, methods=["GET", "POST"])
def shifts_new():
    title = "New coworker"
    lede = "Write its job in plain words, give it its times, and choose what it may touch."
    refuse, not_in_plan, _ = _gate(NEW, title, change=True)
    if refuse is not None:
        return refuse
    if not_in_plan:
        return _page(NEW, title, lede, _upgrade_card(not_in_plan)), 200
    if request.method == "GET":
        start = {"title": "", "job": "", "may": ["write:proposals"], "enabled": True,
                 "shifts": [{"days": "Mon-Fri", "start": "07:30", "latest": "07:45"}]}
        return _page(NEW, title, lede, _form(NEW, start)), 200
    v, why = _read_form()
    data, why = _data_from(v, why)
    if why:
        return _page(NEW, title, lede, _form(NEW, v, errors=why)), 400
    slug = _slug_for(v["title"])
    cw, bad = _save(slug, data, "job.md", v["job"] + "\n", new=True)
    if cw is None:
        return _page(NEW, title, lede, _form(NEW, v, errors=bad)), 400
    _, hire, _, _, _ = _cw()
    if v["risk_ok"]:
        hire.acknowledge(cw, by=_by())
    log.info("shifts.created", extra={"coworker": slug})
    return redirect(f"{HOME}/{slug}?said=saved", code=303)


def _data_from(v: dict, why: list, base: dict | None = None) -> tuple:
    """The file's contents from the form, keeping what the owner doesn't edit here."""
    contract, _, _, _, _ = _cw()
    data = dict(base or {"coworker": contract.VERSION, "job": "job.md", "from": "my"})
    data.update({"title": v["title"], "may": v["may"], "shifts": v["shifts"],
                 "enabled": v["enabled"]})
    reads = any(c.startswith("read:") for c in v["may"])
    webs = any(c in _WEB for c in v["may"])
    if reads and webs and not v["risk_ok"]:
        why = list(why) + ["It may read the box and reach the web. Tick the sentence under What "
                           "it may do, or untick one of them."]
    return data, why


@blueprint.route(HOME + "/<slug>/edit", methods=["GET", "POST"])
def shifts_edit(slug: str):
    path = f"{HOME}/{slug}/edit"
    refuse, not_in_plan, _ = _gate(path, "Change", change=True)
    if refuse is not None:
        return refuse
    if not_in_plan:
        return _page(path, "Change", "", _upgrade_card(not_in_plan)), 200
    _, hire, _, _, _ = _cw()
    cw, _why = _load(slug)
    if cw is None:
        return _page(path, "Not found", "There is no coworker by that name on this box.", ""), 404
    title, lede = f"Change {cw.title}", "Its job, its times, and what it may touch."
    if request.method == "GET":
        v = {"title": cw.title, "job": _job_text(cw).rstrip("\n"), "may": list(cw.may),
             "enabled": cw.enabled,
             "risk_ok": bool(_cw()[0].risks(cw)) and hire.acknowledged(cw),
             "shifts": [{"days": _days_value(s.days), "start": s.start, "latest": s.latest}
                        for s in cw.shifts]}
        return _page(path, title, lede, _form(path, v, cw=cw)), 200
    v, why = _read_form()
    data, why = _data_from(v, why, base=_raw(slug))
    if why:
        return _page(path, title, lede, _form(path, v, cw=cw, errors=why)), 400
    saved, bad = _save(slug, data, cw.job, v["job"] + "\n", new=False)
    if saved is None:
        return _page(path, title, lede, _form(path, v, cw=cw, errors=bad)), 400
    if v["risk_ok"]:
        hire.acknowledge(saved, by=_by())
    return redirect(f"{HOME}/{slug}?said=saved", code=303)


# ── hiring a machine's coworker (§5's sheet) ─────────────────────────────────────────────────────

@blueprint.route(HOME + "/hire/<machine>/<name>", methods=["GET", "POST"])
def shifts_hire(machine: str, name: str):
    path = f"{HOME}/hire/{machine}/{name}"
    refuse, not_in_plan, _ = _gate(path, "Hire", change=True)
    if refuse is not None:
        return refuse
    if not_in_plan:
        return _page(path, "Hire", "", _upgrade_card(not_in_plan)), 200
    _, hire, _, _, _ = _cw()
    offer = next((o for o in hire.offers() if o["machine"] == machine and o["name"] == name), None)
    if offer is None or offer["coworker"] is None:
        why = offer["reason"] if offer else "no machine on this box offers it"
        return _page(path, "Can't hire", f"This coworker can't be hired: {why}.",
                     f'<div class="card"><a href="{HOME}">&larr; Shifts</a></div>'), 404
    cw = offer["coworker"]
    note = ""
    if request.method == "POST":
        try:
            # THE SHEET THE OWNER READ, by its fingerprint: if the machine changed the offer since,
            # hire() refuses and the sheet below is drawn again from what it asks for now.
            hire.hire(machine, name, fingerprint=request.form.get("fingerprint") or "",
                      by=_by(), accept_risk=bool(request.form.get("risk_ok")))
            return redirect(f"{HOME}/{name}?said=hired", code=303)
        except ValueError as e:
            note = f'<div class="card notice"><p>{_esc(str(e))}</p></div>'
    sheet = hire.sheet(cw, machine=machine)
    risk = ""
    if sheet["risk"]:
        risk = (f'<p class="quiet" style="margin-top:16px">{_esc(sheet["risk"])}</p>'
                '<label class="consent"><input type="checkbox" name="risk_ok" value="1"><span>'
                'I understand, and it may read the box and reach the web.</span></label>')
    body = (note + f'<div class="card"><h2>{_esc(sheet["who"])}</h2>' + _sheet_html(sheet)
            + f'<p class="quiet">Hiring copies its job to your box, switched on. You can change '
            f'its job and times after, and an update to {_esc(machine)} never overwrites them.'
            f'</p><form method="post" action="{_esc(path)}">'
            f'<input type="hidden" name="fingerprint" value="{_esc(sheet["fingerprint"])}">{risk}'
            '<button type="submit">Hire</button></form></div>'
            f'<div class="foot"><a href="{HOME}">&larr; Shifts</a></div>')
    return _page(path, f"Hire {cw.title}", f"Shipped by {machine}.", body), (400 if note else 200)
