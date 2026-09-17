"""The Morning Review page — a rendering of stored rows, and nothing else.

THIS PAGE COMPUTES NOTHING (docs/PLAN_MORNING_REVIEW.md §1.1). It calls `core.report.read` and
`core.report.days`, both pure SQL, and imports no machine: the web process does not load the
worker's modules, so any live path here would render blanks for machines the worker reported
on. tests/test_dash_boundary.py fails the build if this file imports a department.

Three rules that keep it honest:
  · Today's rows are refused if their `written_at` is older than three refresh intervals —
    the page says "no report since 08:15" instead of yesterday's numbers under today's date
    (§2.2b). Stale-and-labelled is survivable; stale-and-confident is not.
  · The picker IS the table. Live (today) first, then every stored day newest-first. No day
    can be offered that has nothing behind it, and a deep link to a day with no row says so.
  · Segments sit in a FIXED order — Unified Inbox, Content, Lead — whatever is installed. A
    machine with no row renders nothing (owner: "that section is just blank for now"); a rail
    the client owns but has not connected renders its `connect` line, never a failure (§2.4).

The message (core.report.render) orders by URGENCY; this page orders by MACHINE. Different
surfaces, on purpose — somebody will one day "fix" one to match the other, and the fix is the
bug (spec §2.4).
"""
import html
from datetime import date as _date
from datetime import datetime
from datetime import timedelta as _timedelta

from flask import redirect, request

from core import dash as _dash
from core import report
from core.dash import blueprint, page

TITLES = {"customer_voice": "Unified Inbox", "content_machine": "Content", "lead_machine": "Lead"}
_PILL = {"ok": "green", "warn": "amber", "fail": "red", "connect": "blue"}

CSS = """
.rv-top{display:flex;align-items:baseline;justify-content:space-between;gap:16px;flex-wrap:wrap;margin:6px 0 14px}
.rv-top h1{margin:0}
.rv-top form{margin:0}
.rv-top select{background:#15181C;color:#e6e6e6;border:1px solid #2a2f36;border-radius:8px;padding:8px 12px;font-size:14px}
.rv-lede{color:#9aa3ad;margin:0 0 18px;font-size:14px}
.rv-lede b{color:#C2F3F4}
.seg{border:1px solid #23282f;border-radius:12px;padding:16px 18px;margin:0 0 14px;background:#15181C}
.seg .hd{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:10px}
.seg .hd h2{margin:0;font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:#9aa3ad}
.seg .hd .big{font-size:22px;font-weight:600;color:#e6e6e6;font-variant-numeric:tabular-nums}
.seg .hd .big small{font-size:13px;color:#9aa3ad;font-weight:400;margin-left:6px}
.seg .hd .delta{font-size:12px;color:#9aa3ad;margin-left:8px}
.rail{display:grid;grid-template-columns:110px 1fr;gap:6px 14px;align-items:baseline;padding:6px 0;border-top:1px solid #1f242b}
.rail .k{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:#6f7883}
.rail ul{list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;gap:6px 18px}
.rail li{font-size:14px;color:#cfd5db}
.rail li b{color:#e6e6e6;font-variant-numeric:tabular-nums}
.rail li b.zero{color:#e05b5b}
.rail li.need a{color:#C2F3F4}
.rail .pill{margin-right:6px}
.rv-note{font-size:12px;color:#6f7883;margin-top:8px}
.rv-warn{border:1px solid #6b4a1a;background:#241b0e;color:#f0c674;border-radius:10px;padding:12px 16px;margin:0 0 14px}
.rv-empty{color:#9aa3ad;font-size:14px}

/* THE BODY BRINGS ITS OWN PRIMITIVES, because it is rendered in two different shells. `.pill`
   and `.dlink` are defined in core.dash's stylesheet and the lead app's chrome has neither, so
   the four watch states rendered as bare grey words on the tab the owner opens every morning —
   ok and fail looking identical is the exact opposite of what a status pill is for. Scoped
   under .seg/.rv-top so this cannot reach out and restyle a host page that has its own. */
.seg .pill,.rv-top .pill{display:inline-block;font-size:11px;padding:4px 10px;border-radius:9999px;
flex:none;font-family:ui-monospace,Menlo,monospace;background:rgba(255,255,255,.06);color:rgba(255,255,255,.5)}
.seg .pill.green{background:rgba(20,83,45,.4);color:#86efac}
.seg .pill.blue{background:rgba(30,58,138,.4);color:#93c5fd}
.seg .pill.red{background:rgba(127,29,29,.4);color:#fca5a5}
.seg .pill.amber{background:rgba(120,53,15,.4);color:#fcd34d}
.seg .dlink{color:#C2F3F4;font-weight:500;text-decoration:none}
.seg .dlink:hover{text-decoration:underline}
/* THE SPARKLINE. currentColor so it inherits whichever shell it is drawn in — the app's
   chrome and the dash chrome have different palettes and the chart must not pick one.
   preserveAspectRatio=none lets it stretch to the column width, which is fine for a
   sparkline (the shape carries the trend, the printed range carries the scale) and would
   not be for an axis chart. */
.seg .spark{display:flex;align-items:center;gap:10px;margin:2px 0 12px;color:#7fd1d3}
.seg .spark svg{flex:1;min-width:0;height:34px;overflow:visible}
.seg .spark circle{fill:currentColor}
.seg .spark circle.tip{fill:#C2F3F4}
.seg .spark .sk{flex:none;font-size:11px;letter-spacing:.04em;color:#6f7883;font-variant-numeric:tabular-nums}

/* THE PHONE. Owner, 2026-09-07, about this app: "From my mobile phone this is virtually
   unusable... the buttons are not visible." This is the page he opens at breakfast, so it gets
   the same treatment the leads table got: the 110px label column collapses (it costs two thirds
   of a 390px screen), the day picker goes full width so it can be hit with a thumb, and the
   headline stops competing with the title for one line. LAST IN THE FILE ON PURPOSE — an
   equal-specificity rule later in a stylesheet wins, so a media block written above these rules
   would silently do nothing. That is a bug this app has already shipped once. */
@media (max-width:760px){
  .rv-top{gap:10px}
  .rv-top h1{font-size:22px}
  /* 44px, the target the rest of this app settled on for a thumb — the day picker is the
     only control on the page and it was 36. */
  .rv-top form,.rv-top select{width:100%}
  .rv-top select{min-height:44px}
  .seg{padding:14px}
  .seg .hd{display:block}
  .seg .hd .big{display:block;margin-top:6px}
  .seg .hd .delta{display:block;margin:4px 0 0}
  .rail{grid-template-columns:1fr;gap:4px;padding:10px 0}
  .rail ul{gap:6px 14px}
  .rail li{line-height:1.5}
  /* 390px: the legend under the line, not beside it — at that width a flex row leaves
     the chart about 120px, which is not enough shape to read. */
  .seg .spark{display:block}
  .seg .spark svg{width:100%;height:40px}
  .seg .spark .sk{display:block;margin-top:4px}
}
"""


def _esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.0f}"
    return str(v)


def _picker(v: dict, action: str = "/app/review") -> str:
    """Live first, then the stored days newest-first — nothing else. NOT core.dash.options(): its
    empty-case fallback names reel avatars (§1.9). The LIST comes from `report.view`, so this
    function chooses nothing; it draws what it was handed.

    `action` IS THE SURFACE THE PICKER STAYS ON. The same review is drawn on two addresses — the
    app's first tab and the canonical `/app/review` the 8 AM message deep-links to — and a picker
    hardcoded to one of them throws the reader off the page he was reading the moment he changes
    the day. On the tab that means landing on a page with no nav, which is how a daily surface
    becomes a dead end."""
    opts = "".join(
        f'<option value="{_esc(d["day"])}"{" selected" if d["day"] == v["day"] else ""}>'
        f'{"Live — " if d["live"] else ""}{_esc(d["label"])}</option>' for d in v["days"])
    return (f'<form method="get" action="{_esc(action)}"><select name="day" onchange="this.form.submit()">'
            + opts + '</select><noscript><button type="submit">Go</button></noscript></form>')



def _spark(points: list, back: int = 30, day: str = "") -> str:
    """A month of the segment's headline, as one inline SVG. No library, no script, no SQL.

    THE TWO RULES THAT CARRY MEANING, and both are about not drawing a lie:

    · A DAY WITH NO ROW IS A GAP, NOT A ZERO. `report.history` omits days the box did not
      report, and a box that was off on Sunday did not send zero emails on Sunday — it did not
      say. So x is the day's POSITION IN THE WINDOW, never its index in the list: plotting by
      index closes the hole and draws a continuous line through days nobody measured. Each run of
      consecutive days is its own polyline, so the gap is visible as a gap.

    · AN EMPTY SERIES DRAWS NOTHING. The meters headline is "$11", a string on purpose, and
      `history` hands back an empty list for any headline that is not a number. A flat line at
      zero and no data at all look identical and mean opposite things, so the absent chart is the
      honest rendering (OSDev1, 2026-09-09).

    Fewer than two points is also nothing: one dot is not a trend, and a chart of it invites a
    reading the data cannot support.

    THE RANGE IS PRINTED BESIDE IT. A sparkline with no scale is decoration — the reader cannot
    tell a climb from 0-to-3 from a climb from 300-to-900, and both are the same picture.
    """
    pts = [p for p in (points or []) if isinstance(p.get("value"), (int, float))]
    if len(pts) < 2:
        return ""
    try:
        end = _date.fromisoformat(day) if day else _date.fromisoformat(pts[-1]["day"])
    except ValueError:
        return ""
    # THE WINDOW IS WHAT WE ACTUALLY HAVE, capped at `back`, and the legend says which.
    #
    # Drawing a fixed 30-day axis was the first version and it looked broken: a box twelve days
    # old spent more than half its chart on empty space for days that never existed. Honest, and
    # unreadable — the eye reads the blank as a machine that stopped, which is the opposite of
    # what it means. So the axis starts at the first day there IS a report (never earlier than
    # `back` ago) and the legend prints the real span, so "12 days" is never a 30-day claim.
    #
    # This does NOT soften the gap rule: x is still the day's position in that window, so a
    # Sunday with no row is still a hole in the line rather than a point pulled onto its
    # neighbour.
    first = min((p.get("day") or "") for p in pts)
    try:
        start = max(_date.fromisoformat(first), end - _timedelta(days=int(back) - 1))
    except ValueError:
        start = end - _timedelta(days=int(back) - 1)
    span = max(1, (end - start).days)

    xy = []
    for p in pts:
        try:
            d = _date.fromisoformat(str(p["day"]))
        except (ValueError, KeyError, TypeError):
            continue
        off = (d - start).days
        if 0 <= off <= span:
            xy.append((off, float(p["value"])))
    if len(xy) < 2:
        return ""

    vals = [v for _, v in xy]
    lo, hi = min(vals), max(vals)
    W, H, PAD = 240.0, 34.0, 3.0

    def px(off: int) -> float:
        return round(off / span * W, 2)

    def py(v: float) -> float:
        if hi == lo:                             # never moved: a flat line is the true shape
            return round(H / 2, 2)
        return round(H - PAD - (v - lo) / (hi - lo) * (H - 2 * PAD), 2)

    # CONSECUTIVE DAYS ONLY. A break in the dates is a break in the line.
    runs: list[list] = [[xy[0]]]
    for prev, cur in zip(xy, xy[1:]):
        if cur[0] == prev[0] + 1:
            runs[-1].append(cur)
        else:
            runs.append([cur])

    body = ""
    for run in runs:
        if len(run) == 1:                        # a lone day still happened: draw the point
            body += f'<circle cx="{px(run[0][0])}" cy="{py(run[0][1])}" r="1.6"/>'
        else:
            body += ('<polyline fill="none" stroke="currentColor" stroke-width="1.5" '
                     'stroke-linejoin="round" stroke-linecap="round" points="'
                     + " ".join(f"{px(o)},{py(v)}" for o, v in run) + '"/>')
    last = xy[-1]
    body += f'<circle cx="{px(last[0])}" cy="{py(last[1])}" r="2.4" class="tip"/>'

    rng = f"{_fmt(lo)}\u2013{_fmt(hi)}" if hi != lo else _fmt(lo)
    shown = span + 1
    label = f"{shown} days" if shown != 1 else "1 day"
    return (f'<div class="spark"><svg viewBox="0 0 {W:.0f} {H:.0f}" preserveAspectRatio="none" '
            f'role="img" aria-label="{_esc(label)}, {_esc(rng)}">{body}</svg>'
            f'<span class="sk">{_esc(label)} &middot; {_esc(rng)}</span></div>')


def _segment(r: dict, back: int = 30, day: str = "") -> str:
    title = _esc(r.get("title") or TITLES.get(r.get("machine"), r.get("machine")))
    if r.get("error"):
        return (f'<section class="seg"><div class="hd"><h2>{title}</h2></div>'
                f'<p class="rv-empty">⚠ {_esc(r["error"])}</p></section>')
    h = r.get("headline") or {}
    delta = h.get("delta")
    dtxt = (f'<span class="delta">{"+" if delta > 0 else ""}{_fmt(delta)} vs the day before</span>'
            if isinstance(delta, (int, float)) and delta else "")
    head = (f'<div class="hd"><h2>{title}</h2><div class="big">{_esc(_fmt(h.get("value", 0)))}'
            f'<small>{_esc(h.get("label", ""))}</small>{dtxt}</div></div>'
            # THE CHART SITS UNDER THE NUMBER IT CHARTS. Owner, 2026-09-09, asked the Today page
            # for "data and graphs and charts"; this is the headline's own last month, and it is
            # absent for any machine whose headline is not a number.
            + _spark(r.get("history"), back, day))
    needs = r.get("needs_you") or []
    if needs:
        items = "".join(
            f'<li class="need">→ ' + (f'<a class="dlink" href="{_esc(n.get("href"))}">{_esc(n.get("text"))}</a>'
                                      if n.get("href") else _esc(n.get("text"))) + "</li>"
            for n in needs)
    else:
        items = '<li>Nothing.</li>'
    rails = [f'<div class="rail"><span class="k">Needs you</span><ul>{items}</ul></div>']
    happened = r.get("happened") or []
    if happened:
        items = "".join(
            f'<li><b class="{"zero" if x.get("value") in (0, 0.0) else ""}">{_esc(_fmt(x.get("value", "")))}</b> {_esc(x.get("text"))}</li>'
            if x.get("value") not in (None, "") else f'<li>{_esc(x.get("text"))}</li>'
            for x in happened)
        rails.append(f'<div class="rail"><span class="k">Happened</span><ul>{items}</ul></div>')
    watch = r.get("watch") or []
    if watch:
        items = "".join(
            f'<li><span class="pill {_PILL.get(w.get("state"), "amber")}">{_esc(w.get("state"))}</span>'
            + (f'<a class="dlink" href="{_esc(w.get("href"))}">{_esc(w.get("text"))}</a>' if w.get("href") else _esc(w.get("text")))
            + "</li>"
            for w in watch)
        rails.append(f'<div class="rail"><span class="k">Watch</span><ul>{items}</ul></div>')
    figures = r.get("figures") or {}
    if figures:
        items = "".join(
            f'<li><b>{_esc(_fmt(f.get("value", "")))}</b> {_esc(f.get("label") or k)}</li>'
            for k, f in figures.items())
        rails.append(f'<div class="rail"><span class="k">Numbers</span><ul>{items}</ul></div>')
    notes = "".join(f'<div class="rv-note">{_esc(n)}</div>' for n in (r.get("notes") or []))
    return f'<section class="seg">{head}{"".join(rails)}{notes}</section>'


def _meters(r: dict | None) -> str:
    if not r or r.get("error"):
        return ""
    h = r.get("headline") or {}
    items = "".join(f'<li><b>{_esc(x.get("text"))}</b> {_esc(x.get("value", ""))}</li>' for x in r.get("happened") or [])
    warn = "".join(f'<li><span class="pill {_PILL.get(w.get("state"), "amber")}">{_esc(w.get("state"))}</span>{_esc(w.get("text"))}</li>'
                   for w in r.get("watch") or [])
    return (f'<section class="seg"><div class="hd"><h2>Meters</h2><div class="big">{_esc(h.get("value", ""))}'
            f'<small>{_esc(h.get("label", ""))}</small></div></div>'
            f'<div class="rail"><span class="k">This cycle</span><ul>{items or "<li>No metered vendor on this box.</li>"}</ul></div>'
            + (f'<div class="rail"><span class="k">Watch</span><ul>{warn}</ul></div>' if warn else "")
            + "</section>")


def _admit(*, owner_only: bool = True):
    """THE REVIEW IS AN OWNER SURFACE, so it asks for a credential on EVERY host — including a
    public label, which is the whole difference between this page and the app beside it.

    WHY THIS IS NOT machine_app._gate, though it was, and shipped, and leaked. That gate OPENS on
    a label named in `dash.public_labels` — correctly, because the app is a SALES surface whose
    rows are scoped to that one label. This page is neither: it is cross-machine and it carries
    money. MEASURED ON THE LIVE BOX 2026-09-09, no session and no token, by OSDev5 and confirmed
    by me: `health-and-wellness.nlvl.co/app/review` and `funded-companies.nlvl.co/app/review` both
    returned 200 with the meters, the monthly ceiling, the AT-CAP line and every machine's totals.
    Five labels are public on that box. Mirroring a gate is not the same as inheriting its
    reasoning, and the reasoning is what did not transfer.

    IT ALSO MUST NOT LOCK HIM OUT — his own box IS the public label, and the session cookie is
    host-only, so a session from `aios.nlvl.co` does not travel to a demo subdomain. Hence a
    REDIRECT to that host's own `/dash/login` (mounted on every host by the kernel), never a 404:
    one sign-in on that address and he is in for thirty days. `dash.app_token` opens it too.

    Returns a response to send instead, or None to proceed.
    """
    zone = _dash.demo_zone()
    try:
        host = (request.host or "").split(":")[0].strip().lower().rstrip(".")
    except Exception:                            # noqa: BLE001 — no request context: refuse
        return redirect("/dash/login")
    in_zone = bool(zone) and host.endswith("." + zone)
    label = host[: -len(zone) - 1] if in_zone else ""

    # THE ADDRESS IS JUDGED FIRST HERE, unlike machine_app, and it is safe to: this page refuses
    # every stranger anyway, so the order cannot leak which labels exist the way it could there.
    # A SPACE'S LABEL COUNTS — machine_app answers only on a `dash.labels` address because its
    # rows are leads; this page is every machine's numbers, so a Content brand's own subdomain is
    # an address it may answer on. Both registers are read from config by core.dash.
    if in_zone and label not in _dash.label_sources() and label not in _dash.space_labels():
        return "", 404
    # WHO, NOT WHERE — and the two questions are deliberately separate. Everything above decides
    # which ADDRESSES this page answers on at all, and every caller wants the same answer to that.
    # Only this last line differs, so it is a flag rather than a second copy of the host rules:
    # duplicating them is how `/app/review` came to be gated by a rule written for a sales page.
    #
    # `owner_only=False` IS FOR A PAGE THAT CARRIES NO MONEY, and `/dashboard` is the only one so
    # far. The owner-only rule here exists because the REVIEW publishes the meters, the monthly
    # ceiling and the AT-CAP line; a page with none of that on it does not inherit the rule just
    # because it reuses this function. That inheritance is exactly what broke a member's login.
    if owner_only:
        if not _dash.viewer_is_owner(request):
            return redirect("/dash/login")
    elif _dash.session_user(request) is None and not _dash.viewer_is_owner(request):
        # A REAL PERSON OR THE APP TOKEN. `session_user` refuses a session with no user on it, so
        # "signed in" cannot mean "carries any cookie that parses" — the distinction
        # `viewer_is_owner` spells out above, kept here for the same reason.
        return redirect("/dash/login")
    return None


def body(v: dict, *, action: str = "/app/review", heading: str = "Morning Review",
         show_money: bool = False) -> tuple[str, int]:
    """The review itself — everything inside the chrome, and the status it should be served with.

    ONE BUILDER, TWO SURFACES. The same review is drawn on the app's first tab and on
    `/app/review`, which the 8 AM message deep-links to and which is the only address that exists
    on a box with no lead app. Two renderers of one report is precisely the drift
    docs/CONTRACT_MORNING_REVIEW.md §7 warns about — so the shells differ and the body does not.
    The caller supplies the chrome; this function decides nothing about it beyond where the
    picker posts.

    `show_money` IS THE CALLER'S TO DECIDE and it defaults to FALSE, because the one thing this
    page must never do by accident is publish what the box spends. The gate above it opens
    wholesale on a public label — that is correct for a sales surface of public record with the
    people masked, and it is how `/app/review` served the owner's meters, his $90 cap and an AT
    CAP line to anyone holding the URL (measured on the live box, 2026-09-09, no session, no
    token). The counts stay public, exactly as the floor page's counts always have been; the
    money asks for the same key the names already ask for.

    Returns (html, status): 404 for a stored day with nothing behind it, so a link to a day that
    never existed says so rather than rendering a blank.
    """
    head = f'<div class="rv-top"><h1>{_esc(heading)}</h1>{_picker(v, action)}</div>'

    if not v["exists"]:
        lede = f'<p class="rv-lede">{_esc(v["empty_line"])}</p>'
        return f"<style>{CSS}</style>{head}{lede}", (200 if v["live"] else 404)

    n = v["needs"]
    if v["stale"]:
        lede = (f'<div class="rv-warn">No report since {_esc(v["stale"])} — the machine has not '
                f'written one in over {report.STALE_AFTER_S // 60} minutes. These numbers are from '
                f'then, not from now.</div>')
    elif v["live"]:
        said = ("<b>Nothing needs you this morning.</b>" if not n
                else f'<b>{n}</b> {"thing needs" if n == 1 else "things need"} you.')
        lede = f'<p class="rv-lede">{said} &nbsp;·&nbsp; as of {_esc(v["as_of"])}, and it moves</p>'
    else:
        said = ("<b>Nothing needed you.</b>" if not n
                else f'<b>{n}</b> {"thing needed" if n == 1 else "things needed"} you.')
        lede = f'<p class="rv-lede">{said} &nbsp;·&nbsp; {_esc(v["label"])}, final</p>'

    back = int(v.get("history_days") or 30)
    segs = ("".join(_segment(r, back, v.get("day") or "") for r in v["segments"])
            or '<p class="rv-empty">No machine reported that day.</p>')
    # WITHHELD OUT LOUD. A rail that simply vanishes for a stranger is indistinguishable from a
    # box that spent nothing, and the owner reading his own page has to be able to tell "you are
    # not signed in" from "there is nothing here" — the same distinction the floor page draws
    # between an empty book and an empty machine.
    money = (_meters(v["meters"]) if show_money else
             '<section class="seg"><div class="hd"><h2>Meters</h2></div>'
             '<p class="rv-empty">What this box spends is not shown without a key. '
             'Sign in, or open this page with yours.</p></section>')
    return f"<style>{CSS}</style>{head}{lede}{segs}{money}", 200


def render(day: str, now: datetime | None = None) -> tuple[str, int]:
    """The page for one day, in the dash chrome. ONE read — `report.view` — which has already
    decided live/final/stale, built the picker and counted what needs him."""
    v = report.view(day, now)
    inner, status = body(v, show_money=_dash.privileged())
    return page("Morning Review", "wide", inner, title="Morning Review"), status


@blueprint.get("/app/review")
def review_live():
    refused = _admit()
    if refused is not None:
        return refused
    day = (request.args.get("day") or "").strip()
    if day:
        return redirect(f"/app/review/{day}")
    body, status = render(report.today().isoformat())
    return body, status


@blueprint.get("/app/review/<day>")
def review_day(day: str):
    refused = _admit()
    if refused is not None:
        return refused
    body, status = render(day)
    return body, status


@blueprint.get("/dash/review")
def review_alias():
    return redirect("/app/review")
