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
import re
from datetime import date as _date
from datetime import datetime
from datetime import timedelta as _timedelta

from flask import redirect, request

from core import dash as _dash
from core import report
from core.dash import blueprint

TITLES = {"customer_voice": "Unified Inbox", "content_machine": "Content", "lead_machine": "Lead"}

# THE NOTES BELOW STAY IN THE SOURCE AND NEVER SHIP. The page is a buyer's screen, and the walk
# (tests/test_a_buyer_can_walk_every_screen.py) reads every byte a buyer is served for the words
# this box never says; one of the owner's quotes in a note used one. Stripped once, at import.
_CSS_SRC = """
.rv{--rv-bg:#15181C;--rv-ink:#e6e6e6;--rv-strong:#C2F3F4;--rv-accent:#C2F3F4;--rv-spark:#7fd1d3;
--rv-edge:#23282f;--rv-rule:#1f242b;--rv-field:#2a2f36;--rv-dim:#9aa3ad;--rv-faint:#6f7883;
--rv-text:#cfd5db;--rv-bad:#e05b5b;--rv-warn-edge:#6b4a1a;--rv-warn-bg:#241b0e;--rv-warn-ink:#f0c674;
--rv-pill:rgba(255,255,255,.06);--rv-pill-ink:rgba(255,255,255,.5);
--rv-green:rgba(20,83,45,.4);--rv-green-ink:#86efac;--rv-blue:rgba(30,58,138,.4);--rv-blue-ink:#93c5fd;
--rv-red:rgba(127,29,29,.4);--rv-red-ink:#fca5a5;--rv-amber:rgba(120,53,15,.4);--rv-amber-ink:#fcd34d;
--rv-radius:12px;--rv-mono:ui-monospace,Menlo,monospace}
.rv.on-box{--rv-bg:var(--card);--rv-ink:var(--ink);--rv-strong:var(--ink);--rv-accent:var(--link);
--rv-spark:var(--ink-3);--rv-edge:var(--card-edge);--rv-rule:var(--hairline);--rv-field:var(--line);
--rv-dim:var(--ink-2);--rv-faint:var(--ink-3);--rv-text:var(--ink-2);--rv-bad:var(--bad);
--rv-warn-edge:var(--warn);--rv-warn-bg:var(--card);--rv-warn-ink:var(--ink);
--rv-pill:var(--wash);--rv-pill-ink:var(--ink-2);--rv-green:var(--wash);--rv-green-ink:var(--ok);
--rv-blue:var(--wash);--rv-blue-ink:var(--blue, var(--ink-2));--rv-red:var(--wash);--rv-red-ink:var(--bad);
--rv-amber:var(--wash);--rv-amber-ink:var(--warn);--rv-radius:var(--r-md);--rv-mono:var(--mono)}
.rv.on-box .seg{padding:22px}
.rv.on-box .rv-warn{border-left-width:3px}
.rv-top{display:flex;align-items:baseline;justify-content:space-between;gap:16px;flex-wrap:wrap;margin:6px 0 14px}
.rv-top h1{margin:0}
.rv-top form{margin:0}
.rv-top select{background:var(--rv-bg);color:var(--rv-ink);border:1px solid var(--rv-field);border-radius:8px;padding:8px 12px;font-size:max(16px, calc(15 * var(--px, 1px)))}
.rv-lede{color:var(--rv-dim);margin:0 0 18px;font-size:calc(15 * var(--px, 1px))}
.rv-lede b{color:var(--rv-strong)}
.seg{border:1px solid var(--rv-edge);border-radius:var(--rv-radius);padding:16px 18px;margin:0 0 14px;background:var(--rv-bg)}
.seg .hd{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:10px}
.seg .hd h2{margin:0;font-size:calc(14 * var(--px, 1px));letter-spacing:.08em;text-transform:uppercase;color:var(--rv-faint)}
.seg .big{font-size:calc(29 * var(--px, 1px));font-weight:600;line-height:1.2;color:var(--rv-ink);font-variant-numeric:tabular-nums;margin:0 0 10px}
.seg .big small{font-size:calc(16 * var(--px, 1px));color:var(--rv-dim);font-weight:400;margin-left:8px}
.seg .delta{font-size:calc(14 * var(--px, 1px));color:var(--rv-dim);margin-left:8px;font-weight:400}
.rv-when{color:var(--rv-faint);white-space:nowrap}
.rv-needs ul{list-style:none;margin:0;padding:0}
.rv-needs li{padding:10px 0;border-top:1px solid var(--rv-rule);font-size:calc(17 * var(--px, 1px))}
.rv-needs li:first-child{border-top:0;padding-top:0}
.rv-needs li:last-child{padding-bottom:0}
.rv-rail ul.col{display:block}
.rv-rail ul.col li{margin:0 0 6px}
.seg .dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin:0 8px 1px 0;background:var(--rv-amber-ink)}
.seg .dot.fail{background:var(--rv-red-ink)}
.seg .dot.connect{background:var(--rv-blue-ink)}
.rv-rail{display:grid;grid-template-columns:160px 1fr;gap:6px 14px;align-items:baseline;padding:6px 0;border-top:1px solid var(--rv-rule)}
.rv-rail .k{font-size:calc(13 * var(--px, 1px));letter-spacing:.06em;text-transform:uppercase;color:var(--rv-faint)}
.rv-rail ul{list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;gap:6px 18px}
.rv-rail li{font-size:calc(15 * var(--px, 1px));color:var(--rv-text)}
.rv-rail li b{color:var(--rv-ink);font-variant-numeric:tabular-nums}
.rv-note{font-size:calc(13 * var(--px, 1px));color:var(--rv-faint);margin-top:8px}
.rv-warn{border:1px solid var(--rv-warn-edge);background:var(--rv-warn-bg);color:var(--rv-warn-ink);border-radius:10px;padding:12px 16px;margin:0 0 14px}
.rv-empty{color:var(--rv-dim);font-size:calc(15 * var(--px, 1px))}

/* THE BODY BRINGS ITS OWN LINK STYLE, because it is rendered in two different shells and the
   lead app's chrome does not define `.dlink`. Scoped under .seg so it cannot restyle a host page. */
.seg .dlink{color:var(--rv-accent);font-weight:500;text-decoration:none}
.seg .dlink:hover{text-decoration:underline}
/* THE SPARKLINE. currentColor so it inherits whichever shell it is drawn in — the app's
   chrome and the dash chrome have different palettes and the chart must not pick one.
   preserveAspectRatio=none lets it stretch to the column width, which is fine for a
   sparkline (the shape carries the trend, the printed range carries the scale) and would
   not be for an axis chart. */
.seg .spark{display:flex;align-items:center;gap:10px;margin:2px 0 12px;color:var(--rv-spark)}
.seg .spark svg{flex:1;min-width:0;height:34px;overflow:visible}
.seg .spark circle{fill:currentColor}
.seg .spark circle.tip{fill:var(--rv-strong)}
.seg .spark .sk{flex:none;font-size:calc(12 * var(--px, 1px));letter-spacing:.04em;color:var(--rv-faint);font-variant-numeric:tabular-nums}

/* THE PHONE. Owner, 2026-09-07, about this app: "From my mobile phone this is virtually
   unusable... the buttons are not visible." This is the page he opens at breakfast, so it gets
   the same treatment the leads table got: the 110px label column collapses (it costs two thirds
   of a 390px screen), the day picker goes full width so it can be hit with a thumb, and the
   headline stops competing with the title for one line. LAST IN THE FILE ON PURPOSE — an
   equal-specificity rule later in a stylesheet wins, so a media block written above these rules
   would silently do nothing. That is a bug this app has already shipped once. */
@media (max-width:760px){
  .rv-top{gap:10px}
  .rv-top h1{font-size:calc(22 * var(--px, 1px))}
  /* 44px, the target the rest of this app settled on for a thumb — the day picker is the
     only control on the page and it was 36. */
  .rv-top form,.rv-top select{width:100%}
  .rv-top select{min-height:44px}
  .seg{padding:14px}
  .seg .delta{display:block;margin:4px 0 0}
  .rv-rail{grid-template-columns:1fr;gap:4px;padding:10px 0}
  .rv-rail ul{gap:6px 14px}
  .rv-rail li{line-height:1.5}
  /* 390px: the legend under the line, not beside it — at that width a flex row leaves
     the chart about 120px, which is not enough shape to read. */
  .seg .spark{display:block}
  .seg .spark svg{width:100%;height:40px}
  .seg .spark .sk{display:block;margin-top:4px}
}
"""
CSS = re.sub(r"\s*/\*.*?\*/", "", _CSS_SRC, flags=re.S)


def _esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.0f}"
    return str(v)


# ONE RULE FOR "IS THERE ANYTHING TO SHOW", owned by the report so the page, the email and the
# morning message draw the same lines (owner, 2026-09-29). Re-exported: the dashboard reads it here.
has_value = report.has_value


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
        f'{"Today — " if d["live"] else ""}{_esc(d["label"])}</option>' for d in v["days"])
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
    if len(pts) < 2 or not any(p["value"] for p in pts):
        # A MONTH AT ZERO IS NOT A TREND, it is a flat line saying nothing happened (2026-09-29).
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


_STATE_WORD = {"fail": "Needs attention", "warn": "Worth a look", "connect": "To connect"}


def _segment(r: dict, back: int = 30, day: str = "") -> str:
    """One machine's section, drawing ONLY WHAT HAS A VALUE (`has_value`, owner 2026-09-29).

    WHAT NEEDS HIM IS NOT HERE. Every machine's "needs you" lines lead the page in one card
    (`_needs`), the way the Base Machine's "Waiting on you" card does; repeated under each machine
    they were the same sentence twice, and "Nothing." under every quiet one.

    A line with nothing in it is not drawn; a rail with no lines is not drawn; a machine with no
    rails is not drawn. "ok" watch lines are not drawn either — "Running" is what a machine with
    no warning already is. What is left says something, or the section is absent.
    """
    title = _esc(r.get("title") or TITLES.get(r.get("machine"), r.get("machine")))
    if r.get("error"):
        # A SOURCE THAT FAILED MUST NOT LOOK LIKE A SOURCE WITH NOTHING TO SAY (report §1.11), so
        # this one is drawn even though it carries no figure.
        return (f'<section class="seg"><div class="hd"><h2>{title}</h2></div>'
                f'<p class="rv-empty"><span class="dot fail"></span>{_esc(r["error"])}</p></section>')
    h = r.get("headline") or {}
    head = ""
    if has_value(h.get("value")) and str(h.get("label") or "").strip():
        delta = h.get("delta")
        dtxt = (f'<span class="delta">{"+" if delta > 0 else ""}{_fmt(delta)} vs the day before</span>'
                if isinstance(delta, (int, float)) and delta else "")
        head = (f'<div class="big">{_esc(_fmt(h["value"]))}<small>{_esc(h.get("label", ""))}</small>'
                f'{dtxt}</div>'
                # THE CHART SITS UNDER THE NUMBER IT CHARTS. Owner, 2026-09-09, asked for "data and
                # graphs and charts"; it is the headline's own last month, absent when there is none.
                + _spark(r.get("history"), back, day))
    rails = []
    happened = [x for x in (r.get("happened") or [])
                if str(x.get("text") or "").strip()
                and (x.get("value") in (None, "") or has_value(x.get("value")))]
    if happened:
        items = "".join(
            f'<li><b>{_esc(_fmt(x["value"]))}</b> {_esc(x.get("text"))}</li>'
            if x.get("value") not in (None, "") else f'<li>{_esc(x.get("text"))}</li>'
            for x in happened)
        rails.append(f'<div class="rv-rail"><span class="k">What happened</span><ul>{items}</ul></div>')
    figures = [(k, f) for k, f in (r.get("figures") or {}).items()
               if isinstance(f, dict) and has_value(f.get("value"))]
    if figures:
        items = "".join(
            f'<li><b>{_esc(_fmt(f.get("value")))}</b> {_esc(f.get("label") or k)}</li>'
            for k, f in figures)
        rails.append(f'<div class="rv-rail"><span class="k">Where things stand</span><ul>{items}</ul></div>')
    watch = [w for w in (r.get("watch") or [])
             if w.get("state") != "ok" and str(w.get("text") or "").strip()]
    if watch:
        items = "".join(
            f'<li><span class="dot {_esc(w.get("state") or "warn")}" role="img" '
            f'aria-label="{_esc(_STATE_WORD.get(w.get("state"), "Worth a look"))}"></span>'
            + (f'<a class="dlink" href="{_esc(w.get("href"))}">{_esc(w.get("text"))}</a>'
               if w.get("href") else _esc(w.get("text")))
            + "</li>"
            for w in watch)
        rails.append(f'<div class="rv-rail"><span class="k">Worth watching</span><ul class="col">{items}</ul></div>')
    notes = "".join(f'<div class="rv-note">{_esc(n)}</div>' for n in (r.get("notes") or [])
                    if str(n or "").strip())
    if not (head or rails or notes):
        return ""
    return (f'<section class="seg"><div class="hd"><h2>{title}</h2></div>{head}'
            f'{"".join(rails)}{notes}</section>')


def _needs(segments: list) -> str:
    """EVERY MACHINE'S "NEEDS YOU" LINES, FIRST AND TOGETHER — the reason he opens the page.
    Absent when nothing needs him; the sentence above the page already says so."""
    items = []
    for r in segments or []:
        for n in r.get("needs_you") or []:
            text = str(n.get("text") or "").strip()
            if not text:
                continue
            items.append('<li>' + (f'<a class="dlink" href="{_esc(n.get("href"))}">{_esc(text)}</a>'
                                   if n.get("href") else _esc(text)) + '</li>')
    if not items:
        return ""
    return (f'<section class="seg rv-needs"><div class="hd"><h2>Needs you</h2></div>'
            f'<ul>{"".join(items)}</ul></section>')


def _meters(r: dict | None) -> str:
    if not r or r.get("error"):
        return ""
    h = r.get("headline") or {}
    # A METER NOTHING HAS USED HAS NO DATA (owner, 2026-09-29), so only the ones in use are listed,
    # and a box that has spent nothing this cycle shows no Spend section at all.
    used = [x for x in (r.get("happened") or [])
            if str(x.get("text") or "").strip() and has_value(x.get("value"))]
    warn = [w for w in (r.get("watch") or []) if w.get("state") != "ok" and str(w.get("text") or "").strip()]
    spent = has_value(h.get("value"))
    if not (spent or used or warn):
        return ""
    items = "".join(f'<li><b>{_esc(x.get("text"))}</b> {_esc(x.get("value", ""))}</li>' for x in used)
    warns = "".join(f'<li><span class="dot {_esc(w.get("state") or "warn")}" role="img" '
                    f'aria-label="{_esc(_STATE_WORD.get(w.get("state"), "Worth a look"))}"></span>'
                    f'{_esc(w.get("text"))}</li>' for w in warn)
    return (f'<section class="seg"><div class="hd"><h2>Spend</h2></div>'
            + (f'<div class="big">{_esc(h.get("value", ""))}<small>{_esc(h.get("label", ""))}</small></div>'
               if spent else "")
            + (f'<div class="rv-rail"><span class="k">This cycle</span><ul>{items}</ul></div>' if items else "")
            + (f'<div class="rv-rail"><span class="k">Worth watching</span><ul class="col">{warns}</ul></div>'
               if warns else "")
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
         show_money: bool = False,
         box: bool = False) -> tuple[str, int]:
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
    # INSIDE THE BOX, THE BOX'S SHELL SAYS THE TITLE. `chrome()` draws one h1 and the menu; the
    # review keeps only its day picker, and its colours come from the box's tokens (`on-box`). In
    # the lead app's tab it keeps its own heading and its own dark defaults, unchanged.
    head = (f'<div class="rv-top">{_picker(v, action)}</div>' if box else
            f'<div class="rv-top"><h1>{_esc(heading)}</h1>{_picker(v, action)}</div>')
    open_ = f'<style>{CSS}</style><div class="rv{" on-box" if box else ""}">'

    if not v["exists"]:
        lede = f'<p class="rv-lede">{_esc(v["empty_line"])}</p>'
        return f"{open_}{head}{lede}</div>", (200 if v["live"] else 404)

    n = v["needs"]
    lede = ""
    if v["stale"]:
        lede = (f'<div class="rv-warn">No report since {_esc(v["stale"])} — the machine has not '
                f'written one in over {report.STALE_AFTER_S // 60} minutes. These numbers are from '
                f'then, not from now.</div>')
    back = int(v.get("history_days") or 30)
    drawn = "".join(_segment(r, back, v.get("day") or "") for r in v["segments"])
    # A QUIET DAY IS ONE CALM SENTENCE, never a page of empty sections (owner, 2026-09-29).
    quiet = "" if (drawn or n) else (" All quiet so far today." if v["live"] else " A quiet day.")
    if v["stale"]:
        pass
    elif v["live"]:
        said = ("<b>Nothing needs you right now.</b>" if not n
                else f'<b>{n}</b> {"thing needs" if n == 1 else "things need"} you.')
        lede = f'<p class="rv-lede">{said}{quiet} <span class="rv-when">Updated at {_esc(v["as_of"])}</span></p>'
    else:
        said = ("<b>Nothing needed you.</b>" if not n
                else f'<b>{n}</b> {"thing needed" if n == 1 else "things needed"} you.')
        lede = f'<p class="rv-lede">{said}{quiet} <span class="rv-when">{_esc(v["label"])}, final</span></p>'

    segs = _needs(v["segments"]) + (drawn if v["segments"] else
                                    '<p class="rv-empty">No machine reported that day.</p>')
    # WITHHELD OUT LOUD. A rail that simply vanishes for a stranger is indistinguishable from a
    # box that spent nothing, and the owner reading his own page has to be able to tell "you are
    # not signed in" from "there is nothing here" — the same distinction the floor page draws
    # between an empty book and an empty machine.
    money = (_meters(v["meters"]) if show_money else
             '<section class="seg"><div class="hd"><h2>Spend</h2></div>'
             '<p class="rv-empty">What this box spends is not shown without a key. '
             'Sign in, or open this page with yours.</p></section>')
    return f"{open_}{head}{lede}{segs}{money}</div>", 200


def render(day: str, now: datetime | None = None) -> tuple[str, int]:
    """The page for one day, in the dash chrome. ONE read — `report.view` — which has already
    decided live/final/stale, built the picker and counted what needs him."""
    v = report.view(day, now)
    inner, status = body(v, show_money=_dash.privileged(), box=True)
    # THE BOX'S SHELL, NOT THE OPERATOR CONSOLE. This is the page the 8 AM notification opens on a
    # buyer's box; it wore `page()`, the dark console, while every other buyer screen wears the box's
    # look (owner's order, 2026-09-24, relayed by OSDev1). Now it has the menu and the tokens.
    from core.dash.home import chrome
    return chrome("/app/review", title="Morning Review",
                  lede="What needs you, and what your box did.", body=inner), status


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
