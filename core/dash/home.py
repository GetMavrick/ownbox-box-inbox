"""`/dashboard` — the box's home, and the first screen a buyer lands on after signing in.

THE OWNER RULED IT (2026-09-17, in OSDev1's session, quoted on the wall): *"They should go to
/dashboard and then they should see some helpful information and can click settings to set things
up."* Two halves, and this file is the first: something worth reading, with the rail beside it.

IT COMPUTES NOTHING, exactly as `core/dash/review.py` says of itself and for the same reason: the
web process does not load the worker's modules, so a live read here would render blanks for every
machine the worker reported on. `report.view()` is the whole data layer — pure SQL, imports no
machine, and built on the owner's own instruction that a page should be able to wire it "without
thinking too hard".

WHY IT IS NOT `core.dash.page()`. That chrome is the OPERATOR console — dark, breadcrumbed,
`#15181C` — and it is the right chrome for the Morning Review, which the owner reads. This screen
belongs to the person who BOUGHT the box, and the ruling there is the other way round (owner,
2026-09-16: "we want white screens first and foremost... and then we'll probably have a dark toggle
later"). Reusing the operator chrome here would hand a plumber the admin panel, so the two are
deliberately separate surfaces rather than one chrome with a flag.

NOTHING ON IT IS A NUMBER WE INVENTED. `view()` distinguishes "no report yet" from "reported
zero", and this page keeps that distinction: `exists` False renders a sentence, never a wall of
zeros, which is the same rule the morning report keeps and for the same reason — "0" and "nothing
to report" are different claims, and a dashboard that conflates them starts lying quietly.
"""
import html as _html

from flask import request

from core import pause, report, shell
from core.dash import blueprint, brand

CSS = """
:root{--bg:#f4f5f7;--card:#fff;--ink:#14171a;--dim:#6b7480;--faint:#98a1ac;--line:#e6e9ec;
--hover:#f0f2f4;--sel:#eaedf1;--accent:#1a6ef5;--good:#0f8a4d;--warn:#9a6400;--danger:#e0392b;
--rail:#fbfbfc;--scrim:rgba(16,20,26,.42)}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,
"Segoe UI",Roboto,Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration:none}
svg{flex:none}

/* the checkbox that opens the drawer — focusable, never visible. A drawer built on a hidden
   input cannot be reached by keyboard, so this is offscreen rather than `hidden`. */
.navtoggle{position:absolute;width:1px;height:1px;opacity:0;margin:0;pointer-events:none}

.topbar{display:none;position:sticky;top:env(safe-area-inset-top,0px);z-index:30;
align-items:center;gap:12px;height:52px;padding:0 6px 0 4px;background:var(--card);
border-bottom:1px solid var(--line)}
.ham{display:flex;align-items:center;justify-content:center;width:42px;height:42px;
border-radius:10px;cursor:pointer;color:var(--ink)}
.ham:hover{background:var(--hover)}
.navtoggle:focus-visible~.topbar .ham{outline:2px solid var(--accent);outline-offset:-2px}
.mark{font-weight:650;letter-spacing:-.01em}
.grow{flex:1}

.scrim{display:none;position:fixed;inset:0;z-index:35;background:var(--scrim);opacity:0;
pointer-events:none;transition:opacity .2s ease}

.lay{display:flex;min-height:100%;align-items:stretch}

.rail{width:272px;flex:0 0 272px;background:var(--rail);border-right:1px solid var(--line);
padding:0 10px 14px;display:flex;flex-direction:column}
.who{display:flex;align-items:center;gap:11px;padding:15px 8px 13px}
.who .av{width:38px;height:38px;border-radius:11px;flex:none;display:flex;align-items:center;
justify-content:center;font-weight:700;font-size:16px;color:#7a5a14;
background:linear-gradient(145deg,#ffe4a3,#f7c7a8)}
.who .id{min-width:0;display:flex;flex-direction:column;line-height:1.25}
.who b{font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.who span{color:var(--dim);font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

.back{display:flex;align-items:center;gap:10px;padding:10px 8px;margin:2px 0 8px;
border-radius:9px;font-weight:600;color:var(--ink)}
.back:hover{background:var(--hover)}

.nav{display:flex;flex-direction:column;gap:1px}
.nav a{display:flex;align-items:center;gap:11px;min-height:42px;padding:8px 10px;
border-radius:9px;color:#333940;font-size:15px}
.nav a:hover{background:var(--hover)}
.nav a[aria-current]{background:var(--sel);color:var(--ink);font-weight:600}
.nav a.danger{color:var(--danger)}
.nav a .lbl{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.nav a .out{color:var(--faint);margin-left:2px}
.nav a .ic{color:var(--faint)}
.nav a[aria-current] .ic{color:var(--ink)}

.railfoot{margin-top:auto;padding:12px 10px 2px;border-top:1px solid var(--line);
color:var(--faint);font-size:12.5px;line-height:1.4}
.railfoot b{display:block;color:var(--dim);font-weight:600;font-size:12.5px}

.main{flex:1;min-width:0;padding:22px 26px 56px;max-width:1000px}
.crumb{color:var(--dim);font-size:13px;margin-bottom:10px}
.crumb b{color:var(--ink);font-weight:600}
h1{margin:0 0 4px;font-size:26px;letter-spacing:-.01em}
.lede{margin:0 0 22px;color:var(--dim)}
.card{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:16px 18px;
margin-bottom:14px}
.card h2{margin:0 0 2px;font-size:15px}
.card .sub{color:var(--dim);font-size:13px;margin:0 0 12px}
.row{display:flex;align-items:baseline;gap:10px;padding:8px 0;border-top:1px solid var(--line)}
.row:first-of-type{border-top:0}
a.row:hover{color:var(--accent)}
.n{font-variant-numeric:tabular-nums;font-weight:650;min-width:2.2em}
.big{font-size:32px;font-weight:680;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.quiet{color:var(--dim)}
.stale{color:var(--warn)}

@media (prefers-reduced-motion: reduce){.rail,.scrim{transition:none}}

/* THE PHONE — A DRAWER OVER THE PAGE, NOT A BLOCK ABOVE IT.
   Measured first, in Chromium at 375x780: stacked, the owner's own ten-item Settings menu is
   538px tall, which put the page title at 585px and the first field card at 673px. A buyer who
   tapped Settings -> Profile scrolled a full screen of menu to reach the one thing he opened it
   to change. The drawer is the owner's reference and it is better than the two answers I reached
   on my own: the menu keeps BOTH levels and all its choices, and it costs the page no height at
   all, because it is not in the page's flow until it is asked for.
   No JavaScript: one offscreen checkbox, a label for the hamburger and a label for the scrim, so
   tapping outside closes it exactly as tapping the button does. */
@media (max-width:820px){
 .topbar{display:flex}
 .scrim{display:block}
 .lay{display:block}
 .rail{position:fixed;top:0;bottom:0;left:0;width:86vw;max-width:340px;z-index:40;
  border-right:1px solid var(--line);transform:translateX(-101%);transition:transform .22s ease;
  overflow-y:auto;padding-top:env(safe-area-inset-top,0px);box-shadow:0 0 0 rgba(0,0,0,0)}
 .navtoggle:checked~.lay .rail{transform:none;box-shadow:0 12px 40px rgba(16,20,26,.18)}
 .navtoggle:checked~.scrim{opacity:1;pointer-events:auto}
 .main{padding:18px 16px 48px;max-width:none}
}
"""


def _esc(s) -> str:
    """ZERO IS A VALUE, NOT AN ABSENCE — and `str(s or "")` is how a dashboard loses every one of
    them. Written that way first and caught by rendering: a machine reporting "0 emails sent" drew
    a row with the words and no number, because `0 or ""` is `""`. On a page whose entire job is to
    be believed, a silently missing figure is worse than a wrong one; nobody can even see it go.

    `False` is coerced for the same reason. Only `None` is an absence here."""
    return _html.escape("" if s is None else str(s), quote=True)


def _serving() -> set:
    """Every path THIS box actually answers. `core.dash.landing()` asks the same question and
    gives the reason: "which pages exist is a per-box fact, never a constant. The same binary
    ships as four different products." A rail entry for a route this box does not serve is a 404
    handed to somebody in their first five minutes."""
    try:
        from flask import current_app
        return {str(r) for r in current_app.url_map.iter_rules()}
    except Exception:                            # noqa: BLE001 — no app, no filtering
        return set()


_CHEV_LEFT = "M15 18l-6-6 6-6"
# LEAVES THE BOX. Drawn, not typed: a CSS `content:"\2197"` renders as tofu wherever the face
# lacks the glyph, which is exactly what a headless render showed — a missing-character box
# followed by a stray "97" beside the word Billing. Every other mark in this rail is an SVG for
# the same reason, and an icon that depends on the user's installed fonts is not an icon.
_OUT_ARROW = ('<svg class="out" width="13" height="13" viewBox="0 0 24 24" fill="none" '
              'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
              'stroke-linejoin="round" aria-hidden="true"><path d="M7 17 17 7M9 7h8v8"/></svg>')
# CORE'S OWN SECTION IS THE ONLY ICON THIS FILE HOLDS. Every other one arrives with the machine
# that registered the section, the same way its title does.
_HOME_ICON = "M3 10.5 12 3l9 7.5M5.5 9.5V21h13V9.5"


def _svg(d: str, size: int = 19) -> str:
    """One stroke icon. A section with no icon renders a fixed-width blank instead of nothing, so
    the labels stay on one optical column — a list where some rows indent and others do not reads
    as a mistake before it reads as a list."""
    if not d:
        return f'<span class="ic" style="width:{size}px" aria-hidden="true"></span>'
    return (f'<svg class="ic" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
            f'stroke="currentColor" stroke-width="1.7" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true"><path d="{_esc(d)}"/></svg>')


def _initial(name: str) -> str:
    n = (name or "").strip()
    return n[0].upper() if n else "•"


def _account(who: str, email: str) -> str:
    """The block at the top of the rail: which box this is, and who is signed in.

    IT NAMES THE BOX, NOT THE PERSON. `dash.brand()` already answers "whose box is this" and
    answers it correctly on a sold one — the buyer's own company, not ours. A rail headed with an
    individual's name reads as a personal account on a product whose entire pitch is that a
    business owns it.
    """
    return (f'<div class="who"><span class="av" aria-hidden="true">{_esc(_initial(who))}</span>'
            f'<span class="id"><b>{_esc(who)}</b>'
            + (f'<span>{_esc(email)}</span>' if email else "") + '</span></div>')


def _foot() -> str:
    """The bottom of the rail. ONLY WHAT IS TRUE OF THIS BOX — the reference carries a credits
    balance and a docs link because that product has them; inventing our own would be a number on
    a screen that nothing can back. The box's own address is real, it is useful (people forget the
    URL of a thing they own), and it is the one line that says out loud whose machine this is."""
    try:
        host = str(request.host or "").strip()
    except Exception:                            # noqa: BLE001 — no request, no host
        host = ""
    if not host:
        return ""
    return f'<div class="railfoot"><b>Your box</b>{_esc(host)}</div>'


def rail_html(path: str, *, who: str = "", email: str = "") -> str:
    """The rail, at whichever level `path` puts it. ONE renderer, so the dashboard, the settings
    screens and the inbox cannot drift into three menus that look almost the same."""
    got = shell.rail(path)
    have = _serving()
    rows = []
    for it in got.items:
        if have and it.href not in have and it.tone != "away":
            # SILENTLY ABSENT, not greyed out. A disabled row still promises the thing exists.
            continue
        cls = " ".join(c for c in (it.tone,) if c)
        cur = ' aria-current="page"' if shell.is_current(it, path) else ""
        rows.append(f'<a href="{_esc(it.href)}"'
                    + (f' class="{_esc(cls)}"' if cls else "")
                    + f'{cur}>{_svg(it.icon)}<span class="lbl">{_esc(it.label)}</span>'
                    + (_OUT_ARROW if it.tone == "away" else "") + '</a>')

    head = _account(who or brand(), email)
    if got.level == 2:
        # ONE WAY BACK, AND THE DRAWER IS WHY THERE IS ONLY ONE.
        #
        # An earlier pass here rendered TWO — a wide one home and a narrow one to the section's
        # own index — because the phone stacked the menu above the page and a ten-item Settings
        # list buried the form under 538px of navigation (measured, Chromium 375x780: title at
        # 585px, first field card at 673px). Two labels, two destinations, one breakpoint choosing.
        #
        # The drawer deletes that problem rather than managing it. A menu that is not in the
        # page's flow until it is asked for costs the page no height at all, so the phone can hold
        # the SAME menu the desktop holds, and back can mean the one thing it means on both: leave
        # this section. The owner's reference shows exactly one arrow, and now so does this.
        # THE LABEL NAMES WHERE IT GOES, not where it is. This read `got.title` — the section the
        # person is standing in — so the arrow out of the inbox said "Inbox" and the arrow out of
        # Settings said "Settings", each naming the room being left while pointing at a different
        # one. Owner, 2026-09-17: *"they can go back into the dashboard so there should be a back
        # arrow with a dashboard label."* `back_label` resolves through the same chain as `back`,
        # so the words and the link cannot come apart.
        head += (f'<a class="back" href="{_esc(got.back)}">{_svg(_CHEV_LEFT, 18)}'
                 f'<span class="lbl">{_esc(got.back_label or got.title)}</span></a>')
    return (f'<nav class="rail" id="railnav" aria-label="Sections">{head}'
            f'<div class="nav">{"".join(rows)}</div>{_foot()}</nav>')


_HAM = ('<svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.8" stroke-linecap="round" aria-hidden="true">'
        '<path d="M4 7h16M4 12h16M4 17h16"/></svg>')


def chrome(path: str, *, title: str, lede: str, body: str,
           who: str = "", email: str = "") -> str:
    """The buyer-facing page: rail, breadcrumb, one title, one plain sentence, then cards."""
    parts = shell.crumb(path)
    crumb = ""
    if parts:
        crumb = ('<div class="crumb">'
                 + ' / '.join(_esc(p) for p in parts[:-1])
                 + (' / ' if len(parts) > 1 else '')
                 + f'<b>{_esc(parts[-1])}</b></div>')
    # WHICH OF THE THREE SHAPES THIS PAGE IS, decided from the path like everything else here.
    # `crumb()` already answers it: two parts means an item inside a section, one part means the
    # section's own index, none means level 1. The stylesheet needs it and nothing else does.
    shape = "lvl1" if not parts else ("lvl2-item" if len(parts) > 1 else "lvl2-index")
    name = who or brand()
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow">
<meta name="theme-color" content="#ffffff">
<title>{_esc(name)} · {_esc(title)}</title><style>{CSS}</style></head><body>
<input class="navtoggle" type="checkbox" id="navtoggle" aria-controls="railnav">
<div class="topbar">
<label class="ham" for="navtoggle" role="button" aria-label="Menu">{_HAM}</label>
<span class="mark">{_esc(name)}</span><span class="grow"></span></div>
<label class="scrim" for="navtoggle" aria-label="Close menu"></label>
<div class="lay {shape}">{rail_html(path, who=who, email=email)}
<main class="main">{crumb}<h1>{_esc(title)}</h1><p class="lede">{_esc(lede)}</p>
{body}</main></div></body></html>"""


def _needs_card(view: dict) -> str:
    """WHAT IS STILL WAITING — the only rows on this page a person can act on, so they lead.

    IT LISTS THE ITEMS AND DERIVES NOTHING, and that is the whole point of this function. The
    first version printed `view()["needs"]` as "2 people are waiting on a reply" above a segment
    row reading "3 people are waiting on a reply", and both were on the screen at once. `needs` is
    a count of needs_you ITEMS across every machine, not of people — two facts needing attention,
    one of which happened to be about three people. Rendering it as people invented a number, put
    it in the largest type on the buyer's first screen, and had it contradicted eight lines below.
    Found by rendering, 2026-09-17.

    So the machines' own sentences are the card. They already carry the true number, in the words
    the machine that counted it chose, and no arithmetic here can disagree with them.

    THE LINK IS RESOLVED, NEVER TRUSTED. `href` arrives from a REPORTER — a different module with
    no idea which pages this box serves, and the kind of producer that outlives the screen it was
    written for. An unserved path renders as the plain row it would have been.
    """
    have = _serving()
    rows = []
    for seg in view.get("segments") or []:
        if seg.get("error"):
            continue
        for it in seg.get("needs_you") or []:
            v = it.get("value")
            inner = ((f'<span class="n">{_esc(v)}</span>' if v is not None else "")
                     + f'<span>{_esc(it.get("text") or "")}</span>')
            href = str(it.get("href") or "")
            rows.append(f'<a class="row" href="{_esc(href)}">{inner}</a>'
                        if href and href in have else f'<div class="row">{inner}</div>')
    if not rows:
        return ('<div class="card"><h2>Nothing is waiting on you</h2>'
                '<p class="sub">Everything the box knows about has been dealt with.</p></div>')
    return ('<div class="card"><h2>Waiting on you</h2>'
            '<p class="sub">The box found these and stopped — they need a person.</p>'
            + "".join(rows) + '</div>')


def _segments(view: dict) -> str:
    """One card per machine that actually said something.

    A MACHINE WITH NOTHING TO SAY RENDERS NOTHING, which `core/dash/review.py` already settled on
    the owner's word — "that section is just blank for now". Rendering found why it matters here
    too: a box with three machines installed and two of them quiet drew two cards carrying a
    heading, a subtitle and no rows at all, which reads as a screen that is broken rather than a
    machine that is idle.
    """
    out = []
    for seg in view.get("segments") or []:
        head = seg.get("headline") or {}
        # `label`, NOT `text` — `report._normalize` is the authority and it writes
        # {"value", "label", "delta"}. Read as `text` first, which is always absent, so every
        # headline on this page rendered as a bare number with no words beside it.
        val, txt = head.get("value"), str(head.get("label") or "").strip()
        rows = []
        # WHAT "NOTHING TO SAY" ACTUALLY LOOKS LIKE. `_normalize` fills `value` with 0 whether the
        # reporter gave a headline or not, so `value is not None` is true for every machine ever
        # registered and a guard written on it can never fire — which is exactly what happened:
        # two idle machines each drew a card with a heading, a subtitle and no rows. The LABEL is
        # the signal, because only a reporter that meant to say something sets one.
        if txt:
            rows.append('<div class="row">'
                        + (f'<span class="n">{_esc(val)}</span>' if val is not None else "")
                        + f'<span>{_esc(txt)}</span></div>')
        if seg.get("error"):
            # A SOURCE THAT FAILED MUST NOT LOOK LIKE A SOURCE WITH NOTHING TO SAY (report §1.11).
            rows.append(f'<div class="row"><span class="quiet">{_esc(seg["error"])}</span></div>')
        if not rows:
            continue
        out.append(f'<div class="card"><h2>{_esc(seg.get("title") or "")}</h2>'
                   + "".join(rows) + '</div>')
    return "".join(out)


def _stop_card() -> str:
    """The one control a person reaches for when something is going wrong.

    IT LIVES HERE BECAUSE THE BOX WE SELL HAD NONE (#1332). `core/pause.py` shipped on every box
    and the only buttons that called it were the lead machine's, so a buyer of the inbox box could
    not stop their own box from any screen.

    OWNER ONLY, and the button is simply not drawn for anyone else — the route refuses them too
    (`core/dash` stop_everything), which is the half that matters: hiding a control is a courtesy,
    and a form can be posted without ever loading the page that would have hidden it.

    ONE BUTTON, BOTH WAYS. Same shape the lead machine's Home has always had: a POST either way,
    so nothing but a person pressing it can flip the box.
    """
    from flask import request as _rq
    from core.dash import session_user as _who
    person = _who(_rq)
    if not person or person.get("role") != "owner":
        return ""
    paused = pause.is_paused()
    word = "Start it again" if paused else "Stop everything"
    sub = ("Nothing is running. Every machine on this box is stopped until you start it again."
           if paused else
           "Stops every machine on this box. Your data and your messages stay exactly as they are.")
    return (f'<div class="card"><h2>{"Your box is stopped" if paused else "Stop everything"}</h2>'
            f'<p class="sub">{_esc(sub)}</p>'
            f'<form method="post" action="/dash/{"resume" if paused else "stop"}">'
            f'<button type="submit">{_esc(word)}</button></form></div>')


def _home() -> str:
    view = report.view()
    if not view.get("exists"):
        # NOT ZEROS. `view()` draws this line itself precisely so a page cannot invent a number
        # for a day nothing has been written for yet.
        body = (f'<div class="card"><h2>Nothing to report yet</h2>'
                f'<p class="sub">{_esc(view.get("empty_line") or "")}</p></div>')
    else:
        stale = view.get("stale")
        note = (f'<div class="card"><span class="stale">No report since {_esc(stale)} — '
                'the numbers below are the last ones written, not this minute\'s.</span></div>'
                if stale else "")
        body = note + _needs_card(view) + _segments(view)
    # LAST ON THE PAGE, deliberately: it is the control you want findable and never the one you
    # want your thumb near while reading the morning's numbers on a phone.
    body += _stop_card()
    return chrome("/dashboard", title="Dashboard",
                  lede=f"What your box did — {view.get('label') or 'today'}.",
                  body=body)


@blueprint.route("/dashboard")
def dashboard():
    """THE SAME GATE THE MORNING REVIEW USES, and for the reason its own docstring records rather
    than one reasoned out fresh here.

    OSDev1 held this PR for exactly this (2026-09-17) and he was right: the route shipped with no
    gate of any kind. This page is CROSS-MACHINE — it carries every installed machine's totals and
    the list of people waiting on a reply — which is the same shape as `/app/review`, and that page
    was measured returning 200 with the meters and every machine's totals to a stranger on a
    demo-zone host, with no session and no token (2026-09-09). Five labels were public on that box.

    `review._admit()` is REUSED rather than mirrored, which is the actual lesson from that
    incident: its own docstring says "mirroring a gate is not the same as inheriting its
    reasoning, and the reasoning is what did not transfer". It refuses a stranger, 404s an unknown
    label inside the demo zone, and REDIRECTS rather than 404s for the owner — his own box is a
    public label and the session cookie is host-only, so a 404 here would lock him out of his own
    screen.

    BUT NOT `owner_only`, AND THAT DEFAULT SHIPPED BROKEN. Reusing the function was right; taking
    its LAST line along with the rest was not, and it is the same mistake as mirroring, arriving
    from the other side — I inherited a reasoning without checking that it applied here.

      · `/app/review` is owner-only because it publishes the meters, the monthly ceiling and the
        AT-CAP line. That is a real reason and it still holds, for that page.
      · THIS page carries no money. Measured on the rendered HTML, not assumed: no "$", no
        "spend", no "cost", no "cap", no "ceiling". It is machine headlines and who is waiting.

    WHAT IT COST, measured after OSDev4 reported it: a signed-in MEMBER got `/` -> `/dashboard`
    -> `/dash/login` — shown a login page while holding a valid session, which reads as "my
    password does not work". `/dash/home` returned 200 for that same member, so the bounce
    arrived the moment #1331 moved the front door onto this route. The $499 card sells "each with
    their own login"; this is the screen that login lands on.
    """
    from core.dash import review as _review
    refuse = _review._admit(owner_only=False)
    if refuse is not None:
        return refuse
    return _home(), 200


# THE BOX'S HOME IS A CORE SECTION, and it is the only one core registers. Everything else in the
# rail is a machine's to declare, which is what keeps this file from becoming the list of every
# product we sell.
shell.register_section("dashboard", order=0, machine="core", title="Dashboard",
                       href="/dashboard", home=True)
