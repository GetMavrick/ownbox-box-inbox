"""Customer Voice on a phone — the surface an owner actually checks.

Owner's reasoning, carried in docs/SPEC_CUSTOMER_VOICE_PHONE_APP.md: an 8am email is read once
and then it is gone. What customers said needs to be somewhere he opens without deciding to.

STAGE 1 OF FIVE (spec §6): the blueprint, the gate, the shell and the Today screen. No manifest,
no service worker, no push — those are stages 3 and 4, and they need a credential and a queue.
This stage needs neither, which is why it goes first: a real phone page, behind a real door,
reading numbers the machine already produces.

WHY A SECOND BLUEPRINT RATHER THAN MORE OF `machine_app`. `/app/*` belongs to the Lead Machine and
both mount on one box (core/dispatch.py `_load_web_modules`). Two machines sharing one blueprint is
how a clone that bought only one of them ends up serving the other's routes.

SELF-CONTAINED, on purpose (spec §1.2, invariant 7). Nothing here imports `machine_app` and nothing
here reaches into `sites/`. The CSS is in this module, the markup is in this module. A clone that
installs Customer Voice and not the Lead Machine gets a working phone app, and a change to the lead
app's chrome cannot silently restyle this one.
"""
from __future__ import annotations

import functools
import html
import pathlib
import re
from datetime import date

from flask import Blueprint, jsonify, redirect, request

from core import dash, shell
from core.dash import look as _look
from core.dash.home import RAIL_CSS as _RAIL_CSS
from core.logging import get_logger

log = get_logger(__name__)

blueprint = Blueprint("customer_voice_app", __name__)

TOKEN_COOKIE = "aios_app_k"          # the same cookie machine_app banks; one box, one unlock


def _readable(text: str) -> str:
    """One message body as safe HTML. Falls back to plain escaping if the module is absent.

    A LEAD BOX HAS NO customer_voice, and this file is only loaded where it does — but the
    fallback costs one line and means a future import shuffle degrades to the old rendering
    instead of a 500 on somebody's thread.

    THE IMPLEMENTATION IS OSDev1'S, from #1412, and it replaced mine. Two of us built this the
    same hour because I went from the owner's message straight to code without posting intent
    first — the duplicate is on me. His is the better one: it lives in its own module rather
    than in this file, it elides a long tracking URL's visible text while keeping the whole
    href, and it carries the dependency reasoning I did not have.
    """
    try:
        from marketing.customer_voice.inbox.render import readable
        return readable(text)
    except Exception:                                    # noqa: BLE001 — a thread must still open
        return _esc(text)


def _mail_frame(body_html: str, label: str) -> str:
    """A sender's own HTML in a sandboxed frame, or "" — the same fallback shape as `_readable`.

    THE SAFETY IS IN `render.safe_frame`, NOT HERE. Read the long note there before changing
    anything: the boundary is a `sandbox` iframe with no `allow-scripts` and no
    `allow-same-origin`, plus a CSP inside it that blocks every remote fetch — which is what
    stops a tracking pixel telling the sender the moment the buyer opened their mail.
    """
    try:
        from marketing.customer_voice.inbox.render import safe_frame
        return safe_frame(body_html, label=label)
    except Exception:                                    # noqa: BLE001 — a thread must still open
        return ""


def _esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


# ── the door ────────────────────────────────────────────────────────────────────────────────
# ONE GATE, ON THE BLUEPRINT, for the same reason machine_app's is there: a route added later is
# gated before it is written. Nine chances to forget is how the lead app shipped nine open pages.
#
# AND THERE IS NO PUBLIC TIER HERE — this is the one place this app deliberately does NOT copy
# machine_app. That app has a public path because a list of companies drawn from public records is
# a sales surface: masked, it can be shown to anybody. This app shows what a named customer SAID to
# this business. There is no masked version of that worth publishing, so the answer to an
# unauthenticated request is the login page, not a redacted screen. Narrowing stated rather than
# assumed, because "copy machine_app" would otherwise read as "copy its carve-out too".
def _admitted() -> bool:
    """Session first, then the app token — the same order and the same two credentials as
    `machine_app.unlocked()`, and it fails closed on a blank token exactly as that does.

    THE ORDER IS THE POINT. A dash session is the stronger credential and the one the owner
    actually holds; asking about the token first is how the lead app once let a signed-in owner
    look at his own obscured data.
    """
    import hmac
    # THIS IS THE ONE THAT WIDENS. The other three gates narrowed to the owner; the inbox is the
    # screen employees are hired to use, so any ACTIVE user of this box gets in, member included.
    #
    # It still asks about the USER rather than the session, and that is not decoration: revoking
    # someone sets `active = 0`, and only a question about the user makes that bite on the very
    # next request instead of whenever their 30-day cookie happens to lapse (§10 step 11). Asking
    # `session_ok` here would have left a fired receptionist reading customer messages for a
    # month. `session_ok` is now user-aware too, so this is belt and braces on purpose.
    try:
        if dash.session_user(request) is not None:
            return True
    except Exception:                            # noqa: BLE001 — no session table, no session
        pass
    from core.config import get_config
    want = str((get_config().get("dash") or {}).get("app_token") or "").strip()
    if not want:
        return False
    try:
        got = (request.args.get("k") or request.cookies.get(TOKEN_COOKIE) or "").strip()
    except Exception:                            # noqa: BLE001
        return False
    return bool(got) and hmac.compare_digest(got, want)


@blueprint.before_request
def _gate():
    """No credential, no page. Bounced to the login CARRYING where he was going, so the journey
    resumes instead of restarting — `core.dash.require_session` does that part, and `safe_next`
    re-validates the path on the way back out.

    FIVE PATHS ARE EXEMPT, by an explicit allow-list rather than a prefix or a pattern. A browser
    fetches a manifest WITHOUT cookies unless the link says otherwise, so a gated manifest fails
    to install and says nothing a person could act on. The exemption is safe because those four
    carry no customer data at all — a colour and a title, two icons drawn from constants in this
    module, our own worker, and the tombstone left at the old /voice/sw.js address — and `start_url` still points at `/inbox/`, which is gated, so
    opening the installed app asks for the password exactly as the browser does.

    AN ALLOW-LIST, NOT `startswith`. A prefix rule is how an exemption meant for four files ends
    up covering a route somebody adds under the same folder next year.
    """
    if request.path in PUBLIC_PATHS:
        return None
    if _admitted():
        return None
    return dash.require_session() or redirect("/dash/login")


@blueprint.after_request
def _no_index(resp):
    """A page of customer messages is never a search result, whoever is holding the link."""
    resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return resp


# ── the token never stays in the address ────────────────────────────────────────────────────
# SAME LESSON, SAME FIX as machine_app (#1074): gunicorn runs with `--access-logfile -`, so the
# raw request line — query string included — goes to the journal. A `?k=<token>` therefore lands
# in the log, the address bar and the browser history. Honour it once, bank the cookie, redirect
# to the same path with the token gone.
@blueprint.before_request
def _scrub_token_from_the_url():
    if request.method != "GET" or "k" not in request.args:
        return None
    if not _admitted():
        return None
    clean = request.path
    resp = redirect(clean, code=303)
    resp.set_cookie(TOKEN_COOKIE, request.args["k"].strip(), max_age=30 * 86400,
                    httponly=True, samesite="Lax", secure=request.is_secure)
    log.info("voice.token_banked", extra={"path": clean})
    return resp


# ── the chrome ──────────────────────────────────────────────────────────────────────────────
# PHONE FIRST, and that is a different default from the lead app rather than a smaller version of
# it. That app is a desktop layout with phone rules bolted on at two breakpoints; this one is
# written at 390px and widens, because the whole premise is the screen in his hand.
#
# `dash.brand()` for the name, per box, exactly as every other surface — nothing here spells
# anybody's name (owner, 2026-09-06: "Get Mavrick off this app. It's Brian MacDonald at the top.")
CSS = """
/* ── THE TYPEFACES ARE THE BOX'S ────────────────────────────────────────────────────────────
   Inter for everything, Geist Mono for what a person types or copies: the ownbox.io faces,
   served by core at /ui/font/ and declared once in core/dash/static/box.css, which this page
   links through look.head_tags(). Owner, 2026-09-24 (D1 in docs/SCOPE_DESIGN_LANGUAGE.md),
   superseding Archivo + Public Sans from 2026-09-18. The old files and /inbox/font/ stay, so
   a page cached before this release still draws; nothing here asks for them any more. */
*{box-sizing:border-box;margin:0;padding:0}
/* A BARE BUTTON IS NOT A PILL HERE. box.css makes every <button> the full-width ink pill, which
   is right for a form's one action and wrong for the menu, the search field's button or a chip.
   `:where()` has no specificity, so every rule in this file still wins; this only undoes the
   base where this file says nothing. The primary action is `.btn`, below, and it IS the pill. */
:where(button:not(.btn)){width:auto;min-height:0;padding:0;background:none;color:inherit;
  border:0;border-radius:0;font-weight:inherit;display:inline}

/* ── DUAL TOKEN SET, WHITE BY DEFAULT ───────────────────────────────────────────────────────
   Owner, 2026-09-16, restating a ruling this file already quoted and did not follow: "we want
   white screens first and foremost... and then we'll probably have a dark toggle later."

   THE COMMENT SAID TOGGLE AND THE CODE FOLLOWED THE OS. A `prefers-color-scheme:dark` block sat
   here, so a buyer whose laptop is in dark mode opened a dark product having never asked for one
   — which is the opposite of "white first", and it is what he saw when I rendered these screens
   for him. Dark is now reached ONLY by the switch. Two states:
     :root                      → white. The default, whatever the OS says.
     html[data-theme=dark]      → he used the switch, deliberately.
   `data-theme="light"` is still honoured so an existing cookie keeps working and a future
   three-way control ("System") has somewhere to go back to.
   Every colour on this page resolves through a token. A literal that only works in one theme is
   the classic unreadable-app bug, and tests/test_inbox_design.py refuses one. */
:root{
  /* ── LIGHT IS THE BOX'S LOOK (docs/SCOPE_DESIGN_LANGUAGE.md step 6) ─────────────────────────
     Every colour here is now a name in core/dash/static/box.css, so the inbox and the Base
     Machine cannot drift apart: the cream ground, white cards, the site's ink, the ink pill for
     the one primary action, and rust (--link) for links and a single dot only.
     NOT DECLARED HERE, ON PURPOSE: --ink, --line, --card, --bad and --scrim. box.css defines them
     under the same names, and pointing a name at itself is a cycle that resolves to nothing. Dark,
     below, still sets its own values for all five.
     tests/test_inbox_contrast.py follows each var() into box.css and measures the real value. */
  --bg:var(--ground); --surface:var(--card); --raised:var(--card);
  --dim:var(--ink-2); --dimmer:var(--ink-3);
  --hair:var(--hairline);
  /* THE ACCENT IS THE INK PILL. The site has no accent colour: the one action is ink, and rust
     is only for a link or a dot. So every button, on-chip and step tick goes ink, and the
     rules that are really links read --href instead. */
  --accent:var(--ink); --accent-ink:var(--on-ink); --accent-soft:#ecebe6; --accent-line:var(--line);
  --href:var(--link);
  --bubble-in:var(--wash); --bubble-out:var(--ink); --bubble-out-ink:var(--on-ink);
  --bad-soft:#fdecea;
  --lift:0 1px 2px rgba(17,17,17,.05), 0 8px 24px -12px rgba(17,17,17,.16);
  --tab-bg:rgba(246,244,239,.9);
  /* ── WHAT THE BOX'S RAIL NEEDS, ANSWERED IN THIS APP'S OWN COLOURS ────────────────────────
     `core.dash.home.RAIL_CSS` draws the menu and asks for these eleven names. Every one is an
     ALIAS onto a token already measured above rather than a new colour, which is the whole point
     of importing the stylesheet instead of copying it: the dashboard's rail is blue-grey, this
     one is this app's, and neither had to be drawn twice. The only value invented here is the
     scrim, and it is declared in this block so it counts as palette rather than a stray literal.
     --nav-ink IS WHY THIS COULD NOT BE A COPY. That rule shipped as `#333940`, a light-only
     literal on a rail the dashboard never renders dark; pointed at --dim it reads in both. */
  /* THE RAIL IS CORE'S, SO IT WEARS CORE'S TWO VALUES: the page's ground, and the current row
     lifted on a card (core/dash/home.py _BASE). It was white with a grey pill here and cream
     with a white row one click away on System Settings, the same menu in two colours. */
  --rail:var(--ground); --hover:var(--bubble-in);
  --sel:var(--card); --nav-ink:var(--dim); --faint:var(--dimmer); --danger:var(--bad);
  --drawer-flat:none; --drawer-lift:var(--lift);
  /* ── THE ORB'S GLASS ──────────────────────────────────────────────────────────────────────
     Owner, 2026-09-18: "make it look really cool like glass".

     WHAT SEPARATES GLASS FROM PLASTIC IS THE BOTTOM EDGE, and this was found by rendering four
     versions side by side rather than by reasoning about it. Plastic has one broad diffuse
     highlight and a DARK inside bottom. Glass is dark through the middle and BRIGHT along the
     bottom inside edge, because light passes through the sphere and concentrates there — so
     the caustic is its own gradient under the body, the inside-bottom shadow is inverted to a
     warm rim light, and the specular is small and hard rather than broad and soft.

     Two layers that seemed obvious were measured and dropped: a chromatic warm/cool split at
     the left and right edges is invisible at 54px, and a second pinpoint reflection reads as a
     dead pixel rather than as a reflection. At this size the sphere gets the caustic, one
     specular, a 1px lit rim and a floor shadow, and nothing else earns its bytes.

     EVERY VALUE IS DECLARED HERE because the stylesheet may not carry a colour of its own —
     tests/test_inbox_design.py refuses one, and a shadow is a colour. */
  --orb-glass:radial-gradient(ellipse 58% 40% at 50% 104%, rgba(255,226,206,.95) 0%,
     rgba(255,190,158,0) 62%),
   radial-gradient(circle at 37% 24%, rgba(255,255,255,1) 0%, rgba(255,210,186,.99) 11%,
     rgba(231,104,58,.98) 40%, rgba(131,37,9,.99) 78%, rgba(104,28,5,.99) 100%);
  --orb-sheen:radial-gradient(ellipse 26% 17% at 32% 19%, rgba(255,255,255,1) 0%,
     rgba(255,255,255,.5) 44%, rgba(255,255,255,0) 74%);
  --orb-lift:0 14px 26px -7px rgba(138,44,13,.5), 0 3px 7px rgba(26,29,31,.22),
     inset 0 1px 1.5px rgba(255,255,255,1), inset 0 -2px 3px rgba(255,205,178,.95),
     inset 0 0 0 1px rgba(255,255,255,.28);
  --orb-floor:rgba(138,44,13,.34);
}
:root[data-theme="dark"]{
  /* ONE DARK, AND IT IS box.css's (WebDev2, PR #1659 §14, 2026-09-28). Until now this block was a
     second dark theme: its own greys, its own ink, and coral for every action and sent message.
     box.css now carries the homepage's dark half under the same names, and the light block above
     already points every name this page reads at those names, so the ground, the card, the ink,
     the lines, the pill and rust links all arrive from there with nothing redeclared. Coral is
     gone: the one action is the pill in both themes, and a sent message wears the pill's two
     colours, as it does in light. What is left here is only what box.css has no name for. */
  --accent-soft:#262625; --bad-soft:#2a1613;
  --lift:0 1px 2px rgba(0,0,0,.4), 0 8px 24px -12px rgba(0,0,0,.7);
  --tab-bg:rgba(17,17,17,.88);
  /* The drawer is the card's colour over the ground, lifted by the scrim: the job the shadow does
     in light, which a dark shadow on a dark ground cannot. */
  --rail:var(--card); --sel:var(--accent-soft);
  --drawer-flat:none; --drawer-lift:var(--lift);
  --scrim:rgba(0,0,0,.62);
  /* The same layers, this theme's values — not an inversion of light's. The body barely moves
     (a lit sphere looks the same in either room); what changes is what is around it: the floor
     shadow goes nearly black and the caustic and rim come down, because at light's brightness
     they blow out against a dark bar and the sphere flattens into a sticker. */
  --orb-glass:radial-gradient(ellipse 58% 40% at 50% 104%, rgba(255,214,190,.82) 0%,
     rgba(255,180,146,0) 62%),
   radial-gradient(circle at 37% 24%, rgba(255,255,255,.99) 0%, rgba(255,204,178,.97) 11%,
     rgba(233,110,64,.98) 40%, rgba(120,34,8,.99) 78%, rgba(92,24,4,.99) 100%);
  --orb-sheen:radial-gradient(ellipse 26% 17% at 32% 19%, rgba(255,255,255,.98) 0%,
     rgba(255,255,255,.46) 44%, rgba(255,255,255,0) 74%);
  --orb-lift:0 14px 28px -7px rgba(0,0,0,.7), 0 3px 8px rgba(0,0,0,.5),
     inset 0 1px 1.5px rgba(255,255,255,.9), inset 0 -2px 3px rgba(255,192,162,.8),
     inset 0 0 0 1px rgba(255,255,255,.2);
  --orb-floor:rgba(0,0,0,.58);
}

/* THE SYSTEM FACE IS THE APPLE COPY. Installed to a home screen this renders in SF on iOS and
   Roboto on Android — the same face as every native app beside it, which no webfont can buy. */
body{background:var(--bg);color:var(--ink);
  /* PUBLIC SANS FIRST, THE OLD STACK BEHIND IT. The fallback is not decoration:
     it is what paints during `swap`, and what a box whose font file 404s keeps
     rendering in. */
  font:calc(17 * var(--px, 1px))/1.5 var(--sans);
  -webkit-font-smoothing:antialiased;
  /* THE TAB BAR IS FIXED, so the last row of every screen would sit under it without this —
     AND THE ORB RISES 16px ABOVE THE BAR, which 64px did not account for. Measured on a box
     exported with nothing connected, scrolled to the end: the bead sat on top of "Every figure
     here is read from your own rails." The bar is frosted and content passing under it is
     legible by design; the bead is opaque and simply hides what it covers. 81px = the bar plus
     the bead's overhang, so nothing can come to rest beneath it. */
  padding-bottom:calc(81px + env(safe-area-inset-bottom,0px));}
a{color:inherit;text-decoration:none}
.wrap{max-width:620px;margin:0 auto;padding:0 16px}

/* ── the title bar ─────────────────────────────────────────────────────────────────────────
   STICKY, NOT FIXED, and it clears the notch itself: installed as a PWA there is no browser
   chrome above it, so this bar IS the status area and must not sit under the system clock. */
/* MOBILE-ONLY, THE SAME AS THE DASHBOARD'S. This bar used to show at every width, and
   `core/dash/home.py` records that as deliberate — the shared rail block deliberately does NOT
   carry `.topbar`'s rules "because it is display:none until the breakpoint — the inbox's bar is
   visible at every width and would have vanished on a desktop the moment it imported this."
   That reasoning was sound; the owner has changed the decision it served.
   On a desktop the bar was the whole difference he was pointing at (2026-09-22): a strip
   carrying nothing but a date, and a rail pushed down by its height so the box's crest sat
   lower here than on the dashboard he had just come from. Two screens, one click apart,
   disagreeing about where the box's name lives. Hidden at desktop width, the machine's chrome
   and the dashboard's are the same chrome. The mobile keeps its bar, which is where it earns
   its place: the drawer is shut there and this is the only thing carrying the way out. */
.bar{display:none;position:sticky;top:env(safe-area-inset-top,0px);z-index:10;
  background:var(--tab-bg);
  backdrop-filter:saturate(180%) blur(20px);border-bottom:1px solid var(--hair)}
@media (max-width:820px){ .bar{display:block} }
.bar-in{display:flex;align-items:center;gap:10px;padding:9px 16px;max-width:620px;margin:0 auto}
.bar-in .appmark{width:32px;height:32px}
.brand{font-weight:var(--w-strong);letter-spacing:-.015em}
.day{margin-left:auto;font-size:calc(14 * var(--px, 1px));color:var(--dimmer)}
/* THE DATE, NOW ON THE SCREEN INSTEAD OF IN THE CHROME. Owner, 2026-09-22: *"That date in the
   top needs to get out of there and move down into the today screen."* It sits beside the
   "Today" eyebrow because that is the word it qualifies — "Today" on its own tells a person
   nothing they did not know, and this file already said so about the h1 it replaced. Not bold
   and not a heading: it is the caption on the heading, and it must not compete with the
   sentence underneath. */
h1 .daystamp{font-weight:var(--w-regular);font-size:calc(13 * var(--px, 1px));color:var(--dimmer);letter-spacing:0;
  margin-left:8px;white-space:nowrap}
/* HEADINGS ARE THE ONLY PLACE THE DISPLAY FACE SPEAKS, and there is not much of it: every
   heading in this app is one word — Inbox, Search, Today, Settings. That is precisely why the
   greeting on Today was worth building; it is the one heading long enough to have a shape. */
h1{font-family:var(--sans);
  font-size:calc(22 * var(--px, 1px));font-weight:var(--w-strong);letter-spacing:-.022em;margin:18px 0 2px;color:var(--ink)}
.head .v,.hello h1{font-family:var(--sans)}
h1 .chan{vertical-align:middle}
.sub{color:var(--dim);font-size:calc(16 * var(--px, 1px));margin:0 0 14px}

/* ── the briefing ──────────────────────────────────────────────────────────────────────────
   KINSO'S MOBILE SCREEN IS A BRIEFING — "Good morning, Sarah. You've got 4 new and 9 active
   conversations." Ours already was one; this gives it their shape: a sentence, not a dashboard,
   with the numbers carried in the accent inside running text rather than parked in tiles. */
/* ── the greeting Today opens with ───────────────────────────────────────────────────────────
   Owner, 2026-09-18: build the Today header. It replaces an `<h1>Today</h1>` that was telling
   him the one fact already on the date line above it and on the tab he pressed to get here.
   THE SENTENCE IS THE POINT, not the greeting. "Good morning" is furniture; "you have 4 unread,
   and 9 people waiting on a reply" is the reason to have opened the app — so it gets a readable
   size and the numbers get the weight. */
.hello{padding:14px 2px 4px}
.hello h1{margin:0;font-size:calc(30 * var(--px, 1px));line-height:1.1;font-weight:var(--w-strong);letter-spacing:-.03em}
.hello .line{margin:7px 0 0;font-size:calc(17.5 * var(--px, 1px));line-height:1.42;color:var(--dim)}
.hello .line b{font-weight:var(--w-strong);color:var(--ink)}

.head{padding:8px 2px 18px}
.head .v{font-size:var(--t-title);line-height:1.15;font-weight:var(--w-strong);letter-spacing:-.03em}
.head .v em{font-style:normal;color:var(--accent)}
.head .l{margin-top:6px;color:var(--dim);font-size:calc(16 * var(--px, 1px))}

/* ── grouped lists (Apple) on a tinted ground (Kinso) ──────────────────────────────────────
   Rows live in ONE rounded white card on a grey ground, the iOS inset-grouped table. Kinso
   separates rows with whitespace rather than rules, so the hairline is barely there. */
.card{background:var(--surface);border-radius:16px;padding:2px 14px;margin-top:10px;
  box-shadow:var(--lift)}
.row{display:flex;gap:12px;align-items:baseline;padding:13px 0;border-bottom:1px solid var(--hair)}
.row:last-child{border-bottom:0}
.row .n{font-variant-numeric:tabular-nums;font-weight:var(--w-strong);min-width:2.2em}
.row .t{color:var(--dim);font-size:calc(16 * var(--px, 1px))}
/* A ROW THAT GOES SOMEWHERE LOOKS LIKE ONE. Same row, made an <a>: no underline, no link blue —
   the whole row is the target, exactly as a conversation row already is, with a chevron so the
   affordance is visible rather than discovered by tapping. 44px is kept by the row's own padding.
   `text-decoration:none` alone would leave it indistinguishable from the rows that go nowhere. */
a.row{text-decoration:none;color:inherit}
a.row .t{flex:1}
a.row::after{content:"";width:7px;height:7px;flex:none;align-self:center;
  border-right:2px solid var(--dimmer);border-top:2px solid var(--dimmer);
  transform:rotate(45deg);margin-left:2px}
a.row:active{background:var(--hair);border-radius:10px}
.needs{background:var(--bad-soft);box-shadow:none;border:1px solid var(--bad)}
.needs .t{color:var(--ink)}
.figs{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px}
.figs .fig:last-child:nth-child(odd){grid-column:1/-1}
.fig{background:var(--surface);border-radius:16px;padding:15px;box-shadow:var(--lift)}
.fig .v{font-size:calc(24 * var(--px, 1px));font-weight:var(--w-strong);font-variant-numeric:tabular-nums}
.fig .l{margin-top:3px;color:var(--dim);font-size:calc(14 * var(--px, 1px));line-height:1.35}
.quiet{color:var(--dim);font-size:calc(16 * var(--px, 1px));padding:18px 2px;line-height:1.55}
.row .t.quiet{padding:0}
/* THE SERVER FIELD ONLY WHEN IT MEANS SOMETHING. A Gmail buyer was shown an "IMAP server" box
   they must leave empty. Hidden until "Another provider" is picked; a browser without :has()
   shows it as before, so nothing becomes unsettable. */
.compose:has(select[name="provider"]) .fld-host{display:none!important}
.compose:has(select[name="provider"] option[value="other"]:checked) .fld-host{display:block!important}
/* A DRAFT CARD IS ONE LABEL, so the whole card ticks its box; box.css sets labels bold and
   .quiet carries padding meant for a notice standing alone. Neither belongs inside it. */
.dcard label{font-weight:400;padding:14px 0}
.dcard label .quiet{padding:0}
.dcard .dm{font-size:calc(14 * var(--px, 1px))}
.foot{margin-top:26px;color:var(--dimmer);font-size:calc(14 * var(--px, 1px))}
.foot a{color:var(--href)}

/* ── a conversation row, copied from Kinso ─────────────────────────────────────────────────
   Their structure exactly: avatar, name with the time right beside it, one grey line of preview
   under it, and the channel's own logo far right. 44px minimum and the whole row is the link. */
.conv{display:grid;grid-template-columns:auto 1fr auto;grid-template-rows:auto auto auto;
  gap:2px 12px;padding:11px 0;min-height:44px;border-bottom:1px solid var(--hair);align-items:center}
.conv:last-child{border-bottom:0}
.conv .av{grid-row:1/4;width:calc(42 * var(--px, 1px));height:calc(42 * var(--px, 1px));border-radius:50%;display:flex;align-items:center;
  justify-content:center;font-size:calc(16 * var(--px, 1px));font-weight:var(--w-strong);letter-spacing:.01em;
  background:var(--accent-soft);color:var(--accent);flex:none;
  /* NO PHOTO EXISTS. The vendor sends us a display name and nothing else, so initials are not a
     placeholder for an avatar we failed to load — they are the avatar, the way Apple's Messages
     draws a contact with no picture. */}
.conv .w{grid-row:1;grid-column:2;font-size:calc(16.5 * var(--px, 1px));letter-spacing:-.01em;
  display:flex;align-items:baseline;gap:7px;min-width:0}
.conv .w b{font-weight:var(--w-regular);color:var(--dim);overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
.conv .t{font-weight:400;font-size:calc(14 * var(--px, 1px));color:var(--dimmer);flex:none}
/* ── READ AND UNREAD, THE WAY EVERY INBOX SAYS IT ────────────────────────────────────────────
   Owner, 2026-09-17: "New messages should be in Bold text. Read messages in regular. Just like
   a normal inbox."

   THE UNREAD ROW IS THE DEPARTURE, AND READ IS THE RESTING STATE. Written the other way first —
   bold as the default with a `.read` class lightening it — which is the same pixels and the
   wrong default: a row whose `unread` key is somehow absent then renders as SHOUTING. Unread
   has to be the thing that is added.

   IT WAS ALSO A DEMOTION, NOT JUST A PROMOTION. The name sat at 590 on every row, so nothing
   could be bolder than anything; making unread heavier alone would have left a list where the
   two states were 590 and 700 and nobody could see which was which. Read drops to 500 and
   `--dim`, unread rises to 700 and `--ink`. 200 units and a colour step apart, which is the
   distance the eye actually resolves at 15px.

   TWO CHANNELS ON THE MESSAGE, NOT ONE: weight AND ink. Weight alone is a fine signal on a
   desktop and a poor one on a mobile at arm's length, and both tokens here are already measured
   against both grounds this app draws on.

   AND A WORD FOR THE PEOPLE WHO CANNOT SEE IT AT ALL. Bold is invisible to a screen reader, so
   an unread row carries the word in `.vh` — no pixels, the whole fact. */
.conv.unread .w b{font-weight:var(--w-strong);color:var(--ink)}
.conv.unread .t{font-weight:var(--w-strong);color:var(--dim)}
.conv.unread .p{font-weight:var(--w-regular);color:var(--ink)}
/* THE MESSAGE, TWO LINES. Owner, 2026-09-17: "The conversation should run two lines so people
   can see more information on the screen." It was one clipped line, on the reasoning that a row
   which grows with what somebody wrote makes the list jump about — but the cost of that was the
   thing the list is FOR, cut mid-sentence on the screen he opens most.
   TWO IS A CLAMP, NOT A WRAP, which is what keeps the old reasoning honest: every row is exactly
   one or two lines tall whatever arrives, so the list still cannot jump. Three would be a wrap
   wearing a number. `overflow-wrap` is for the customer who pastes a URL with no spaces in it —
   without it one unbreakable token runs out past the channel logo.
   `--dim` at 15px measures 7.98:1 on the card. */
.conv .p{grid-row:2;grid-column:2;color:var(--dim);font-size:calc(16 * var(--px, 1px));line-height:1.4;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;
  overflow-wrap:anywhere;min-width:0;margin-top:1px}
.conv .p i{font-style:normal;color:var(--dimmer)}
.conv .s{grid-row:3;grid-column:2;display:flex;align-items:center;gap:6px;min-width:0;
  flex-wrap:wrap;row-gap:5px;color:var(--dim);font-size:calc(16 * var(--px, 1px))}
/* A TAG NEVER TRUNCATES AND NEVER OVERLAPS — IT WRAPS. Written first as one non-wrapping line,
   and a 390px render showed both failures at once: "2 messages" cut to "2 mess…" for no reason,
   and a second tag sliding straight under the channel logo, because a `flex:none` pill cannot
   shrink and simply overflowed its grid column. A half-shown tag is a half-shown fact, and
   "Opted ou" is worse than not saying it. So a busy row grows a line instead of hiding one. */
/* AND THE GREY LINE IS WHAT GIVES WAY, which is the half the rule above left out. `min-width:0`
   lets it shrink but flexbox decides to WRAP from each item's BASE size, before any shrinking —
   so a long preview pushed the tag onto a second line even though the preview was the one thing
   on the row that could have yielded. `flex:1 1 0` zeroes its base so it never forces that wrap.

   MIND THE PROSE IN HERE: this stylesheet is INLINED INTO EVERY PAGE, so a CSS comment is shipped
   bytes, and `test_drafts_switch` reads the whole of /inbox/settings looking for fault-report
   language. The first draft of the sentence above used one of the words it bans and turned that
   suite red — a comment about layout, breaking a test about copy, because the guard cannot tell
   the two apart from inside the page. It is RIGHT to scan the whole page; the fix belongs here.
   Read that suite before writing prose in this string. */
/* THE COUNT IS GONE AND SO IS ITS FLOOR. Owner, 2026-09-17: "showing more of the conversation
   rather than having that one message label totally in the way". `.conv .s .sub` and the 6ch
   floor under it existed only to stop "1 message" rendering as "1 ." beside a long tag — a rule
   protecting a label that was costing the row a line. The line it was holding now belongs to the
   message, so the rule went with the label. What is left on this row is tags, which never
   truncate because they wrap. */
/* AND THE MARK SPANS THREE ROWS NOW, not two — the preview took a line of its own,
   which is what made the row worth reading. `1/4` rather than `1/3` is the whole of
   this side of the merge, and getting it wrong leaves the channel logo floating
   against the name with the tags underneath it. */
.conv .mk{grid-row:1/4;grid-column:3;flex:none;display:flex;align-items:center}
/* AND THE AVATAR RECEDES WITH THE REST OF A READ ROW. Demoting the name to 500/--dim left the
   accent monogram as the LOUDEST thing on a row that is meant to be quiet — the eye went to the
   initials instead of the person, which is the hierarchy upside down. Unread rows keep the
   accent; read rows take the neutral ground.

   THIS RULE EXISTED ALREADY, as `.conv.out .av`, and no row in this file has ever been given
   that second class — dead since whatever design dropped it. Repurposed rather than added, but
   NOTE: this comment deliberately does NOT spell the attribute out. The stylesheet is inlined
   into every page, so a comment naming a row's markup is a string that page-scanning tests
   match as though it were a row. An earlier draft of this very comment broke
   test_inbox_channels, which finds the first row by splitting on that attribute.
   NOT copied verbatim: it used `--dimmer`, which is 4.27:1 on `--bubble-in` in dark mode, under
   AA for a 15px monogram (it clears 4.72:1 in light, which is how a rule can look measured and
   only be half-measured). `--dim` is 7.24:1 light and 6.51:1 dark. */
.conv:not(.unread) .av{background:var(--bubble-in);color:var(--dim)}

/* ── tags, and the menu that appears on hover ──────────────────────────────────────────────
   Owner, 2026-09-16: "we're also going to add some different tags on each conversation and an
   action button menu on hover."

   ONE LINK PER ROW. It carried a "…" menu (Reply, and one channel) until the owner removed it on
   2026-09-29: the row itself opens the thread with its reply box, and the channel logos above the
   list filter. The wrapper stays because it carries the hairline. */
.convrow{position:relative;display:flex;align-items:center;gap:4px;
  border-bottom:1px solid var(--hair)}
.convrow:last-child{border-bottom:0}
.convrow .conv{flex:1;min-width:0;border-bottom:0}

/* FOUR MEANINGS, FOUR TREATMENTS, AND NOT ONE OF THEM CARRIED BY COLOUR ALONE — the words
   differ too, because a red pill and a grey pill are the same pill to a colourblind reader
   and this app already refuses colour-only meaning on the channel marks. */
.tag{flex:none;display:inline-flex;align-items:center;border:1px solid transparent;
  border-radius:6px;padding:1px 7px;font-size:calc(12.5 * var(--px, 1px));font-weight:var(--w-strong);letter-spacing:.015em;
  line-height:1.6;white-space:nowrap}
.tag.stop{color:var(--bad);background:var(--bad-soft);border-color:var(--bad-soft)}
.tag.warn{color:var(--dim);background:var(--bg);border-color:var(--accent-line)}
.tag.ad{color:var(--dim);background:var(--bg);border-color:var(--line)}
.tag.draft{color:var(--ink);background:var(--bg);border-color:var(--hair)}

/* The channel mark. Why it is a logo and not a word: see `_mark`. */
.mk svg{display:block}
/* VISUALLY HIDDEN, STILL READ ALOUD. The mark is a shape, so it separates without colour — but a
   shape is not a name, and a screen reader announcing "image" is not an answer to "which channel
   is this". The word travels with every mark and costs no pixels. */
.vh{position:absolute;width:1px;height:1px;margin:-1px;padding:0;overflow:hidden;
  clip:rect(0 0 0 0);clip-path:inset(50%);white-space:nowrap;border:0}
.chan{display:inline-flex;align-items:center;gap:5px;vertical-align:middle;margin-left:7px;
  color:var(--dim);background:var(--bg);border-radius:7px;padding:3px 8px;font-size:calc(13 * var(--px, 1px));
  font-weight:var(--w-regular);letter-spacing:.01em;white-space:nowrap}

/* ── the search field ──────────────────────────────────────────────────────────────────────
   THE CARD SOLD THIS AND THE SCREEN DID NOT HAVE IT. `store.search_conversations` has been
   finished for weeks — space-bound, LIKE-escaped, searching message BODIES and not just the
   name — and its only caller was the connector tool. The assistant could search the buyer's
   inbox and the buyer could not.

   16px ON THE INPUT IS NOT A STYLE CHOICE. Mobile Safari zooms the whole page when a field
   smaller than 16px takes focus, and it does not zoom back out — so a 15px search box leaves a
   person on a mobile looking at a magnified inbox they have to pinch their way out of. */
.find{display:flex;align-items:center;gap:9px;margin:12px 0 2px;background:var(--surface);
  border-radius:13px;padding:0 13px;box-shadow:var(--lift)}
.find svg{flex:none;display:block;color:var(--dimmer)}
.find input{flex:1;min-width:0;border:0;background:transparent;color:var(--ink);font:inherit;
  font-size:max(16px, calc(17 * var(--px, 1px)));padding:13px 0;-webkit-appearance:none}
/* NOT `outline:none`. Written that way first, and test_inbox_design refused it by name — this
   app's accessibility floor is that focus is never removed, only redrawn. Same two lines the
   reply box already uses, so the two fields focus identically. */
.find input:focus{outline:2px solid var(--accent-line);outline-offset:1px;border-radius:4px}
.find input::placeholder{color:var(--dimmer)}
.find input::-webkit-search-decoration,.find input::-webkit-search-cancel-button{
  -webkit-appearance:none}
.find button{flex:none;border:0;background:transparent;color:var(--accent);font:inherit;
  font-size:calc(15.5 * var(--px, 1px));font-weight:var(--w-strong);padding:8px 0 8px 4px;cursor:pointer}
/* THE MESSAGES PAGE'S OWN HEADER (owner, 2026-10-05: "integrating the search into the upper area", like Gmail).
   On a mobile the search is a pill in the bar beside the menu, and the card above the list is not drawn, so the first
   conversation sits a card higher. The bar is hidden from 821px, so the card comes back there: one search at every
   width, never two (the desktop half is in the 821px block below). 48px tall and 16px type, the box's mobile floor. */
.find-wide{display:none}
/* ...AND THE SENTENCE UNDER IT IS ONE LINE, NOT A CARD: `.quiet` pads a block 18px above and below, which on this
   screen was 36px of air between the bar and the first conversation for one line of text. */
.quiet.sum{margin:0;padding:12px 2px 0}
.quiet.sum+.find-wide+.pills{margin-top:10px}
.bar.bar-find{border-bottom-color:transparent}
.barfind{flex:1;min-width:0;display:flex;align-items:center;gap:10px;min-height:48px;padding:0 16px;
  background:var(--surface);border-radius:999px;box-shadow:var(--lift)}
.barfind svg{flex:none;display:block;color:var(--dimmer)}
.barfind input{flex:1;min-width:0;border:0;background:transparent;color:var(--ink);font:inherit;
  font-size:max(16px, calc(17 * var(--px, 1px)));padding:12px 0;-webkit-appearance:none}
.barfind input:focus{outline:2px solid var(--accent-line);outline-offset:1px;border-radius:4px}
.barfind input::placeholder{color:var(--dimmer)}
.barfind input::-webkit-search-decoration,.barfind input::-webkit-search-cancel-button{-webkit-appearance:none}
/* QUIETER THAN THE RESULTS IT COUNTS. Set at 14.5px first and the "Show everything" link wrapped
   onto its own line reading like the next thing to do — the loudest thing on a screen whose job is
   the rows underneath it. */
.found{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;margin:11px 2px 0;
  color:var(--dim);font-size:calc(14.5 * var(--px, 1px));font-variant-numeric:tabular-nums}
.found a{color:var(--href);font-weight:var(--w-regular)}

/* ── Older and Newer ───────────────────────────────────────────────────────────────────────
   AT THE BOTTOM, WHERE HE RUNS OUT OF ROWS, because that is where a person is when they want
   the next page. `space-between` and not a centred pair: with one control it sits on its own
   side — Newer left, Older right — so the direction is in the position and not only the word.
   44px tall for the same reason the rows are. */
.pager{display:flex;justify-content:space-between;gap:10px;margin:14px 0 4px}
/* ONLY `Older` GETS PUSHED RIGHT. Written first as `.pg:only-child{margin-left:auto}`, which
   fires for whichever control is alone — so the LAST page rendered a lone "← Newer" hard right,
   its arrow pointing away from it, and the comment above this block claimed the opposite was
   happening. Rendered page three and there it was. The direction has to be in the class, not in
   how many siblings there happen to be. */
.pager .pg.next:only-child{margin-left:auto}
.pg{display:inline-flex;align-items:center;min-height:44px;padding:0 16px;border-radius:12px;
  background:var(--surface);box-shadow:var(--lift);color:var(--href);font-size:calc(16 * var(--px, 1px));
  font-weight:var(--w-regular)}
.pg:focus-visible{outline:2px solid var(--accent-line);outline-offset:2px}

/* ── channel chips ─────────────────────────────────────────────────────────────────────────
   Kinso's LEFT RAIL is a desktop idiom; on a mobile the same job is a scrolling row. */
.chips{display:flex;gap:8px;margin:12px 0 2px;overflow-x:auto;padding:2px 0 6px;
  -webkit-overflow-scrolling:touch;scrollbar-width:none}
.chips::-webkit-scrollbar{display:none}
.chip{flex:0 0 auto;display:inline-flex;align-items:center;gap:6px;color:var(--dim);
  background:var(--surface);border-radius:999px;padding:7px 13px;font-size:calc(15 * var(--px, 1px));font-weight:var(--w-regular);
  white-space:nowrap;box-shadow:var(--lift)}
.chip.on{color:var(--accent-ink);background:var(--accent)}
.chip.on .n{color:var(--accent-ink);opacity:.75}
.chip .n{color:var(--dimmer);font-size:calc(13 * var(--px, 1px));font-variant-numeric:tabular-nums}
/* THE TWO ROWS ARE NOT THE SAME KIND OF CHOICE, and stacking two identical rows reads as one
   control that wrapped. The filter row asks WHAT STATE; the channel row asks WHERE FROM. So the
   filter row sits tighter to the header it qualifies, and the channel row keeps its own space. */
.chips.pills{margin:12px 0 0}
/* A LOGO CHIP IS THE DISC ITSELF, 44px so a thumb finds it; chosen, it wears the ink edge. */
.chip.mkchip{padding:0;background:transparent;border:0}
.chip.mkchip .ui-disc{width:44px;height:44px}
.chip.mkchip.on{background:transparent}
.chip.mkchip.on .ui-disc{box-shadow:0 0 0 2px var(--ink)}

/* ── the inbox on a screen with room ───────────────────────────────────────────────────────
   Owner, 2026-09-17: "With the social media channels on the left, and they move horizontally at
   the top on Mobile as you have it."

   THE MOBILE IS UNTOUCHED. Everything above this line is the layout he already approved, and it
   stays the default — this block only runs where there is width to spend, which is the one place
   a 620px column was throwing it away. `.ib` is set by the inbox page alone, so no other screen
   in the app widens by accident.

   THE CHIPS BECOME THE RAIL RATHER THAN BEING REPLACED BY ONE. Same links, same counts, same
   current-chip rule — a second markup for the same list is how the two stop agreeing, and the
   filter that works on a mobile has to be the filter that works on a laptop. Only the axis changes.

   IT STICKS, because the list it filters is the thing that scrolls. A channel rail that scrolls
   away with 200 conversations is a rail you have to go back up to use. */
@media (min-width:821px){
  /* THE BAR SPANS THE RAIL AND THE LIST, so its content starts at the left edge like any app
     header rather than floating in the old 620px mobile centre. Measured at 1280px: centred, the
     brand sat 190px in while the rail beneath it started at 0 and the list at 272px — three left
     edges on one screen.
     THE FILTER COLUMN IS GONE, and this is the change the rail paid for. It was a 224px column
     holding two chips with 600px of empty grey under it (rendered at 1280x820 before this), and
     it only ever existed because this screen had no other left edge to use. It has one now, and
     it holds the box's sections — two stacked rails are two answers to "where am I". So the
     chips keep the mobile's shape at every width: a row, above the list they filter, which is
     also where a filter belongs. The 900px grid and the chip-as-list-row styling it needed are
     deleted rather than overridden — an overridden rule for a layout nobody renders is the dead
     code the next person has to reason about. */
  .ib .bar-in{max-width:none;padding-left:20px}
  /* THE BAR IS HIDDEN FROM HERE, so the messages page's search comes back into the page (`_bar_find`), and the
     summary over it gets back the room a desktop has. */
  .find-wide{display:block}
  .quiet.sum{padding-top:18px}
  /* THE LIST KEEPS A BOUNDED WIDTH AND LEAVES THE DEAD SPACE ON THE RIGHT, not in the middle.
     Two things decided this. A row stretched to the full 1008px put Len's whole message on one
     line — the two-line preview the owner asked for only reads as two lines while the column is
     narrow enough to wrap, so an unbounded row quietly undoes the change he approved last night.
     And with a rail on the left, a column centred in what is left has a gutter on both sides and
     reads as floating; aligned to the rail it reads as the second pane of an app, which is what
     it is. 780px is the widest this list gets before the preview stops wrapping at 15px. */
  /* EVERY SCREEN TAKES THE RAIL'S EDGE, NOT ONLY THE LIST. The list was aligned for the reason
     above, and the other screens were left centred in the space beside the rail: at 1280 the
     list, Drafts and a fresh Today began at 296px, while Today, a thread, Settings, set-up and
     both connect pages began at 482px, so the page jumped sideways between two tabs of one app.
     They keep their 620px reading width; only the left edge moves to the one core uses. */
  .wrap{margin:0;padding-left:24px;padding-right:24px}
  .ib .wrap{max-width:780px;margin:0;padding:0 24px}
  nav.tabs{display:none}
  /* THE TAB BAR'S HEIGHT WAS PADDING AT THE FOOT OF THE PAGE, and with the bar gone that padding
     is a blank strip. A mobile's bottom bar pinned to the foot of a desktop window is the single
     thing that made this screen read as unfinished. */
  .wrap{padding-bottom:28px}
  /* ...AND THE SAME PADDING ON THE BODY SHORTENED THE RAIL. `.lay` is min-height:100% of the body's
     content box, so the bar's 81px, kept on a desktop that has no bar, left the white rail
     ending 81px above the foot of the window over a strip of page. Measured at 1280x900: rail
     819px tall, System Settings' 900. */
  body{padding-bottom:0}
  /* AND THE BAR STOPS REPEATING THE BOX'S NAME. With core's `.who` block restored to the top of
     the rail, the name sits eleven pixels from the bar that also carries it — the same name,
     twice, one above the other. The rail is the box's identity on a desktop; the bar keeps the
     day and the room to grow, and says nothing the rail already said. */
  .bar-in .brand{display:none}
}

/* ── the set-up wizard ─────────────────────────────────────────────────────────────────────
   Owner, 2026-09-21: a progress bar across the top, and readability. Four steps, each of which
   is a real errand on somebody else's website, so the screen's job is to say how far in you are
   and to get the finished ones out of the way of the one you still owe. */
.wiz{margin:16px 0 4px}
.wiz-n{margin:0 0 8px;font-size:calc(16 * var(--px, 1px));color:var(--dim)}
.wiz-n b{color:var(--ink);font-weight:var(--w-strong)}
/* A SEGMENT PER STEP. Gap, not a divider, so the unfilled ones read as empty rather than as
   something drawn — a track with hairlines in it looks like it is already partly full. */
.wiz-bar{display:flex;gap:4px}
.wiz-bar .seg{flex:1;height:6px;border-radius:3px;background:var(--hair)}
.wiz-bar .seg.on{background:var(--accent)}

/* NAMED `wstep`, NOT `step`. `.step` was already taken at the top of this stylesheet -- a
   two-column grid for the numbered instruction rows -- so styling the wizard's sections as
   `.step` laid every open one out as `auto 1fr` and wrapped its copy into a column seven
   words wide with a dead gutter beside it. Valid CSS, correct grid, wrong element. Found by
   rendering the three states and reading them; the folded rows looked right the whole time.

   A FINISHED STEP IS ONE ROW UNTIL IT IS ASKED FOR. `<details>` keeps the content in the page
   for a screen reader and for find-in-page; what it hides is four instructions for an errand
   already run. `list-style:none` plus the ::-webkit- rule removes the platform triangle, which
   sits at the wrong end of a row this tall and is not the affordance — the word "Change" is. */
.wstep.done{background:var(--surface);border:1px solid var(--line);border-radius:13px;
  padding:0 16px}
.wstep.done[open]{padding-bottom:16px}
.stepsum{display:flex;align-items:center;gap:12px;padding:14px 0;cursor:pointer;
  list-style:none}
.stepsum::-webkit-details-marker{display:none}
.stepsum:hover .stepsum-v{color:var(--accent)}
.stepsum .tick{flex:none;width:22px;height:22px;border-radius:50%;background:var(--accent);
  color:var(--accent-ink);display:flex;align-items:center;justify-content:center;
  font-size:calc(14 * var(--px, 1px));line-height:1}
.stepsum-t{flex:1;min-width:0;display:flex;flex-direction:column;gap:1px}
.stepsum-t b{font-size:calc(16.5 * var(--px, 1px));font-weight:var(--w-strong)}
.stepsum-s{font-size:calc(14.5 * var(--px, 1px));color:var(--dim);overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
.stepsum-v{flex:none;font-size:calc(14.5 * var(--px, 1px));color:var(--dim)}
/* ── THE SET-UP GUIDE (docs/SCOPE_ONE_PLACE_PER_SETTING.md) ─────────────────────────────────
   One row per step, and the whole row is the link to that step's one home: GOV.UK's task list
   makes the row the target because people tap whatever looks tappable. 56px tall, well past the
   48px floor, so a thumb cannot miss it. Status is plain text, never a chip. */
.guide{display:flex;flex-direction:column;gap:10px;margin-top:18px}
.grow{display:flex;align-items:center;gap:12px;min-height:56px;padding:14px 16px;
  background:var(--surface);border-radius:16px;color:var(--ink);text-decoration:none;
  box-shadow:var(--lift)}
.grow .gn{flex:none;width:calc(28 * var(--px, 1px));height:calc(28 * var(--px, 1px));border-radius:50%;display:flex;align-items:center;
  justify-content:center;border:1px solid var(--line);font-size:calc(15 * var(--px, 1px));font-weight:var(--w-strong)}
.grow.done .gn{background:var(--accent);color:var(--accent-ink);border-color:transparent}
.grow .gt{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px}
.grow .gt b{font-size:calc(17 * var(--px, 1px));font-weight:var(--w-strong)}
.grow .gs{font-size:calc(15 * var(--px, 1px));color:var(--dim)}
.grow .gtag{flex:none;font-size:calc(14 * var(--px, 1px));color:var(--dimmer)}
.grow .chev{flex:none;font-size:calc(21 * var(--px, 1px));color:var(--dimmer)}
/* THE STEP STILL OWED IS THE ONE THAT LOOKS LIKE WORK. Everything above it has folded away, so
   it does not need a highlight to be found — it needs the heading weight the folded rows gave up. */
.wstep:not(.done) > h1{margin-bottom:0}

/* ── the thread ────────────────────────────────────────────────────────────────────────────
   Theirs left, ours right — the iMessage shape, which is the one everybody already reads. */
.thread{margin-top:14px;display:flex;flex-direction:column;gap:3px}
.msg{max-width:82%}
.msg.in{align-self:flex-start}
.msg.out{align-self:flex-end;text-align:right}
/* COMPACT, BECAUSE THESE ARE EMAILS NOW AND NOT ONE-LINE DMs. Owner, 2026-09-23: "things need
   to be much more compact as far as line spacing on these emails." A chat bubble tuned for
   "yes, Thursday works" turns a forty-line email into a page of scrolling: 1.35 line-height and
   14px of side padding are generous per line and ruinous multiplied by forty. 1.28 and tighter
   padding read the same on a short message and give back most of a screen on a long one. */
.msg .b{background:var(--bubble-in);border-radius:19px;padding:8px 13px;white-space:pre-wrap;
  overflow-wrap:anywhere;font-size:calc(17 * var(--px, 1px));line-height:1.28;text-align:left}
/* A LONG MESSAGE IS WIDER THAN A SHORT ONE. 82% of the column is the right bubble width for a
   sentence and the wrong one for an email, where it forces a narrow ragged column down the
   page; an email-length message takes the full width it needs. */
.msg.long{max-width:100%}
.msg.out .b{background:var(--bubble-out);color:var(--bubble-out-ink)}
/* A LINK IN A BUBBLE TAKES THE BUBBLE'S COLOUR and is underlined, because an accent
   colour that reads on the white bubble is unreadable on the tinted one. */
.msg .b a{color:inherit;text-decoration:underline;text-underline-offset:2px;word-break:break-word}
.msg .m{margin-top:3px;margin-bottom:8px;color:var(--dimmer);font-size:calc(13 * var(--px, 1px))}

/* ── the sender's own document ─────────────────────────────────────────────────────────────
   A framed message is a CARD, not a bubble: no tint, no 19px radius, a hairline border. That
   is honest about what it is — their page, shown inside ours — where a chat bubble wrapped
   around somebody else's letterhead reads as ours. */
.msg .b.mail{background:var(--surface);padding:0;border:1px solid var(--line);border-radius:14px;
  overflow:hidden}
/* NO SCRIPT MEANS NO AUTO-HEIGHT. The frame is an opaque origin with `sandbox` and no
   `allow-scripts`, so neither side can measure the other — see `render.safe_frame`. A fixed
   cap that scrolls inside a bordered card is the honest answer, not a shortcoming to fix. */
/* NO BACKGROUND HERE, DELIBERATELY. The frame's own document paints its body white inside the
   srcdoc, because a sender's email is designed for white paper and a themed token would render
   their black text on our dark ground. Setting it out here as well would be an unnameable
   colour in our palette for a surface that is not ours. */
iframe.mail{display:block;width:100%;border:0}
/* The text, folded under it. Kept because a frame cannot be searched, swept with one selection,
   or copied out of the way a person copies an address out of a message. */
.orig{border-top:1px solid var(--line)}
.orig summary{cursor:pointer;color:var(--dimmer);font-size:calc(14 * var(--px, 1px));padding:9px 13px;min-height:24px;
  list-style:none}
.orig summary::-webkit-details-marker{display:none}
.orig summary::after{content:" ▾"}
.orig[open] summary::after{content:" ▴"}
.ot{padding:0 13px 11px;white-space:pre-wrap;overflow-wrap:anywhere;font-size:calc(16 * var(--px, 1px));
  line-height:1.3}

/* ── who really sent it ────────────────────────────────────────────────────────────────────
   The address under the name, then Gmail's caret. Both are absent rather than empty when the
   box kept no headers for this thread — a details panel with nothing in it is worse than none,
   because it invites a tap that answers nothing. */
.addr{margin:-6px 0 0;color:var(--dimmer);font-size:calc(15 * var(--px, 1px));overflow-wrap:anywhere}
.det{margin:10px 0 0}
.det summary{display:inline-block;cursor:pointer;color:var(--dimmer);font-size:calc(14 * var(--px, 1px));
  padding:6px 0;min-height:24px;list-style:none}
.det summary::-webkit-details-marker{display:none}
.det summary::after{content:" ▾"}
.det[open] summary::after{content:" ▴"}
.dt{margin-top:6px;padding:10px 12px;background:var(--surface);border:1px solid var(--line);
  border-radius:12px}
/* LABEL OVER VALUE ON A MOBILE, two columns once there is room. A 90px label column next to a
   long From line leaves about eleven characters per row at 390px, which is not a table, it is
   a stack of fragments. */
.dr{display:block;padding:3px 0;font-size:calc(14 * var(--px, 1px));line-height:1.35}
.dk{display:block;color:var(--dimmer)}
.dv{display:block;overflow-wrap:anywhere;font-family:var(--mono)}
@media (min-width:560px){
  .dr{display:flex;gap:10px}
  .dk{flex:0 0 88px;text-align:right}
  .dv{flex:1 1 auto}
}

/* ── the reply box ─────────────────────────────────────────────────────────────────────────
   16px IS NOT A STYLE CHOICE: iOS Safari zooms the page when a focused input is under 16px, and
   on a thread that shunts the conversation off screen the moment he taps to answer. */
.compose{margin-top:18px;display:flex;flex-direction:column;gap:9px}
.compose textarea{width:100%;font:inherit;font-size:max(16px, calc(17 * var(--px, 1px)));line-height:1.45;color:var(--ink);
  background:var(--surface);border:1px solid var(--line);border-radius:18px;padding:12px 15px;
  resize:vertical;min-height:78px;max-height:60vh;field-sizing:content;box-shadow:var(--lift)}
.compose textarea:focus{outline:2px solid var(--accent-line);outline-offset:1px;
  border-color:transparent}
.compose .btn{align-self:flex-end}
.btn{display:inline-flex;align-items:center;justify-content:center;text-decoration:none;
  font:inherit;font-size:var(--t-body-2);font-weight:var(--w-strong);border:0;
  border-radius:var(--r-pill);padding:12px 26px;background:var(--accent);color:var(--accent-ink);
  cursor:pointer;min-height:var(--control);width:100%}
/* FULL WIDTH ON A MOBILE, ITS LABEL'S WIDTH ONCE A POINTER EXISTS (walk #12, mobile first). */
@media (min-width:720px){.btn{width:auto}}
/* THE SECOND WAY IS THE OUTLINE (OSDev0's drift check, 2026-09-24): one ink pill per screen, the
   thing to do next; every other action on it wears this. */
.btn.ghost{background:transparent;color:var(--ink);box-shadow:inset 0 0 0 1px var(--line)}
/* A SWITCH THAT CHANGES THE BOX IS A POSTED FORM DRAWN AS A LINK (`_post_button`): it sits in a
   sentence, reads in the link colour, and never becomes the screen's ink pill. */
form.inline{display:inline}
button.txt{color:var(--href);cursor:pointer;font:inherit;padding:4px 0}
.drafted{color:var(--dim);font-size:calc(14 * var(--px, 1px));display:flex;align-items:center;gap:7px}
.drafted::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--accent);
  flex:none}

/* ── the tab bar ───────────────────────────────────────────────────────────────────────────
   THE PWA DECISION. Installed to a home screen there is no browser chrome, so the app supplies
   its own furniture, and on a mobile that furniture is a bottom bar — thumb-reachable, the idiom
   every native app on the device already uses. It clears the home indicator with the safe-area
   inset rather than a guessed margin. */
.tabs{position:fixed;left:0;right:0;bottom:0;z-index:20;background:var(--tab-bg);
  backdrop-filter:saturate(180%) blur(20px);border-top:1px solid var(--hair);
  padding-bottom:env(safe-area-inset-bottom,0px)}
.tabs-in{max-width:620px;margin:0 auto;display:flex}
.tab{flex:1;display:flex;flex-direction:column;align-items:center;gap:3px;padding:9px 0 7px;
  min-height:52px;color:var(--dimmer);font-size:min(calc(12.5 * var(--px, 1px)), 14px);font-weight:var(--w-regular);letter-spacing:.01em}
.tab svg{display:block}
.tab.on{color:var(--accent)}
/* THE DOT SAYS SOMETHING IS WAITING, AND SAYS ONLY THAT. Owner, 2026-09-17, picking it over a
   number after seeing both on the real bar.
   POSITIONED OFF THE CENTRE LINE, not off the tab's edge: the tab is a flex column with the
   icon centred, so `left:50%` plus a small offset lands the dot on the icon's shoulder at every
   tab width, where anchoring to `right` drifts as the bar divides by three or by four.
   THE OUTLINE IS NOT DECORATION. The bar is translucent (`--tab-bg`) over whatever scrolls beneath
   it, so an accent dot can land on accent-coloured content and vanish; a 2px outline in the bar's
   own colour keeps its edge on any background.
   AND IT CLEARS THE TWO STROKES, which cost one iteration to get right. Built at 6px/6px the dot
   sat ON the tray icon's upper-right stroke — and the comment above `_TABS` argues those two
   strokes are the whole point of this icon, the streams running into the tray. Three placements
   rendered at 4x and compared: 6/6 covered a stroke, 1/11 floated free of the icon and crowded
   the bar's top edge, 3/9 sits on the icon's shoulder with both strokes readable. It still
   overlaps the svg's BOUNDING BOX, which is mostly empty space — the strokes are what matters. */
/* ── the orb ─────────────────────────────────────────────────────────────────────────────────
   Owner, 2026-09-18: in place, glass, and doing nothing yet. It is a decorative span, so it
   takes `pointer-events:none` — a tap goes through it to the bar, with no press state and no
   destination. See `_tabbar` for why it is not a disabled button.
   IT SITS IN A TAB-WIDTH SLOT and lifts out of the bar rather than growing it: the bar keeps
   its 62px so nothing below the list moves, and the orb overhangs the top edge. */
.tabs-in .orb{flex:1;display:flex;justify-content:center;align-items:flex-start;
  pointer-events:none}
.tabs-in .orb .bead{position:relative;width:54px;height:54px;flex:none;border-radius:50%;
  transform:translateY(-17px);
  background:var(--orb-glass);
  /* THE FILL IS NEAR-OPAQUE ON PURPOSE. The first version was properly translucent and the
     conversation list read straight THROUGH it — legible words inside the bead, which looks
     like a rendering fault rather than like glass. Real glass this thick does not act as a
     window either; it refracts and darkens, which is what the dark middle and bright bottom
     edge are doing. The small blur stays for the rim only. */
  backdrop-filter:blur(8px) saturate(150%);-webkit-backdrop-filter:blur(8px) saturate(150%);
  box-shadow:var(--orb-lift)}
/* THE SPECULAR IS ITS OWN ELEMENT, not a second background layer, so the blur underneath is
   not smeared through it — a highlight that blurs with the glass stops reading as a reflection
   ON the surface and starts reading as paint IN it. */
.tabs-in .orb .bead::before{content:"";position:absolute;inset:0;border-radius:50%;
  background:var(--orb-sheen)}
/* AND A SHADOW ON THE FLOOR. Without it the orb hangs in the air; with it, it sits on the bar. */
.tabs-in .orb .bead::after{content:"";position:absolute;left:50%;bottom:-5px;width:30px;
  height:8px;margin-left:-15px;border-radius:50%;background:var(--orb-floor);filter:blur(4px)}
/* A FLAT ORB ON A DESKTOP WOULD BE A MYSTERY, because the bar it belongs to is gone there. */
@media (min-width:821px){ .tabs-in .orb{display:none} }

.tab{position:relative}
.tab .mark{position:absolute;top:3px;left:50%;margin-left:9px;width:8px;height:8px;
  border-radius:50%;background:var(--href);box-shadow:0 0 0 2px var(--tab-bg)}

/* ── settings ──────────────────────────────────────────────────────────────────────────────
   The appearance switch is core's now (core/dash/theme.py control(), drawn by box.css .ui-seg), a
   form whose three buttons each save a choice, so it still needs no client JavaScript.
   .seg BELOW IS NOT THAT SWITCH. It survives because the set-up wizard's progress bar reuses the
   name (.wiz-bar .seg) and inherits this padding and margin; removing it would move that bar. */
.seg{display:flex;gap:4px;background:var(--bg);border-radius:12px;padding:4px;margin-top:4px}
.setrow{display:flex;flex-direction:column;gap:2px;padding:14px 0;border-bottom:1px solid var(--hair)}
.setrow:last-child{border-bottom:0}
.setrow .acts{display:flex;flex-wrap:wrap;align-items:center;gap:4px 18px;margin:6px 0 0}
.setrow .acts a{color:var(--href);text-decoration:none}
.setrow b{font-weight:var(--w-regular);font-size:calc(16.5 * var(--px, 1px))}
.setrow span{color:var(--dim);font-size:calc(14.5 * var(--px, 1px));line-height:1.45}
/* A <b> INSIDE THE SENTENCE IS EMPHASIS, NOT A SECOND TITLE. `.setrow b` above sizes each row's
   title, and it also caught the address in "Ownbox is reading owner@…", which came out 15.5px
   in a 13.5px sentence and not bold. Seen at 390 and 1280 in the launch sweep, 2026-09-24. */
.setrow span b{font-size:inherit;font-weight:var(--w-strong);color:var(--ink)}

/* ── the install steps ─────────────────────────────────────────────────────────────────────*/
.flow{margin-top:16px}
.step{display:grid;grid-template-columns:auto 1fr;gap:12px;align-items:start}
.stepn{width:calc(29 * var(--px, 1px));height:calc(29 * var(--px, 1px));border-radius:999px;display:flex;align-items:center;
  justify-content:center;font-weight:var(--w-strong);font-size:calc(15 * var(--px, 1px));background:var(--accent-soft);
  color:var(--accent);flex:none}
.stepb{background:var(--surface);border-radius:14px;padding:13px 15px;min-width:0;
  box-shadow:var(--lift)}
.steph b{font-size:calc(16 * var(--px, 1px))}
.steph b b{font-weight:inherit}
.stepb p{color:var(--dim);font-size:calc(15 * var(--px, 1px));margin:5px 0 0}
.stepjoin{width:1px;height:14px;margin:4px 0 4px 14px;background:var(--line)}

@media (min-width:560px){ .figs{grid-template-columns:1fr 1fr 1fr} .figs .fig:last-child:nth-child(odd){grid-column:auto} }
@media (prefers-reduced-motion:reduce){ *{animation:none!important;transition:none!important} }

/* ── WHERE THIS APP PUTS THE BOX'S MENU BUTTON ───────────────────────────────────────────────
   The rail, the drawer and the scrim are imported below from `core.dash.home.RAIL_CSS`, so this
   app and the dashboard cannot drift into two menus that look almost the same. What is NOT shared
   is where the button sits: the dashboard hides its whole top bar above the breakpoint, and this
   app's bar is visible at every width because it carries the brand and the date.
   SO THE BUTTON HIDES, NOT THE BAR — above 820px the rail is on screen and a button that opens
   what you can already see is the dead control this codebase keeps deleting. */
.bar-in .ham{margin-left:-6px}
.navtoggle:focus-visible~.bar .ham{outline:2px solid var(--accent);outline-offset:-2px}

/* THE MENU BUTTON SHOWS ONLY WHERE THE RAIL IS HIDDEN — above the breakpoint the rail is on
   screen, and a button that opens what you can already see is the dead control this codebase
   keeps deleting. The bar itself stays at every width; it carries the brand and the date. */
@media (min-width:821px){ .bar-in .ham{pointer-events:none} .bar-in .menubadge{display:none} }

/* ── TWO RULES THAT LET THE IMPORTED RAIL SIT IN THIS APP'S PAGE ─────────────────────────────
   `.main` is core's flex slot beside the rail, and it brings the dashboard's own page padding
   with it. Here `.wrap` already owns content width and gutters at four breakpoints, so the slot
   is emptied of both rather than fought with. Specificity, not order — core's rule is `.main`
   and this is `.lay .main`, so it wins wherever the stylesheets end up concatenated.
   AND THE RAIL'S HEAD IS A STUTTER ON A DESKTOP, where the bar directly above it already says
   whose box this is. On a mobile the drawer covers that bar, so there it is the only thing naming
   the box and it stays. */
.lay .main{padding:0;max-width:none}
/* AND THE RAIL RUNS THE HEIGHT OF THE WINDOW. Core's `.lay{min-height:100%}` resolves against a
   sized ancestor and this app never set one, so on a short list the rail stopped with the last
   conversation and the page showed grey below a white column that had simply run out. A flex
   column on `body` fixes it without a magic number for the bar's height: the bar takes what it
   needs, `.lay` takes the rest. The tab bar is `position:fixed` and out of flow, so it does not
   enter this calculation. */
body{display:flex;flex-direction:column;min-height:100dvh}
.lay{flex:1}
/* THE BOX NAMES ITSELF AT THE TOP OF THE RAIL, ON A MACHINE'S SCREENS TOO. This hid core's
   `.who` block — crest, box name, account — whenever the rail was drawn inside this app, so the
   dashboard's rail opened with the box's name and a machine's rail opened with a bare row. One
   idiom per level was the intent; what it produced was furniture that appears and disappears as
   you move between screens of the same box. Owner, 2026-09-21: *"that logo at the top should be
   fixed therefore the machines."* Core draws it, every machine wears it. */
"""

# THE BOX'S RAIL, DRAWN BY CORE AND THEMED BY THE TOKENS ABOVE. Appended rather than pasted: one
# copy of the drawer exists, in `core/dash/home.py`, and both consumers render the same furniture.
CSS = CSS + _RAIL_CSS



# THE ONLY SCRIPT ON THIS APP, and it does two things: register the worker, and report whether
# this is running installed. Nothing renders through it — every screen here is server-rendered, so
# a person with script blocked still reads their inbox.
JS = """
(function () {
  // EXPLICIT SCOPE, though the file's own location already implies it. MDN: a worker cannot claim
  // a scope broader than where it is served, so /inbox/sw.js controls /inbox/ and nothing else —
  // which is what we want. It is passed anyway because the failure mode is SILENT: registration
  // succeeds, reports success, and the worker never intercepts a thing.
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/inbox/sw.js', { scope: '/inbox/' }).catch(function () {});
  }
  // DID THE INSTALL ACTUALLY HAPPEN. Spec section 4: a buyer who never completes it has bought a
  // bookmark, so the completion rate is the number that says whether this feature worked at all.
  // standalone is how the page knows: iOS sets navigator.standalone, everyone else answers the
  // display-mode query. Reported once per load with sendBeacon so it cannot delay a paint.
  try {
    var installed = (window.navigator.standalone === true) ||
      (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches);
    if (installed && navigator.sendBeacon) { navigator.sendBeacon('/inbox/installed'); }
  } catch (e) {}
  // A <details> MENU DOES NOT CLOSE ITSELF. Native behaviour keeps it open until its own summary
  // is clicked again, so a reader who taps the next row leaves one hanging over it. Escape and a
  // click elsewhere close it, which is what every other menu on the phone does. This is the only
  // thing on the screen that needs script, and the menu still OPENS without it.
  function shut(except) {
    var open = document.querySelectorAll('details.acts[open]');
    for (var i = 0; i < open.length; i++) {
      if (!except || !open[i].contains(except)) { open[i].open = false; }
    }
  }
  document.addEventListener('click', function (e) { shut(e.target); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') { shut(null); } });
})();
"""


# ── the channels, as their own marks ────────────────────────────────────────────────────────
# THE REAL LOGOS, AT THEIR OFFICIAL COLOURS (owner, 2026-09-15: "yes, of course we have to get
# exact brand logos"). Paths are the official single-colour marks from simple-icons, fetched
# rather than drawn, with each brand's own published hex.
#
# INLINE SVG, NOT A SPRITE OR A CDN. This box may be behind a firewall with no outbound anything,
# and the app must render identically offline once installed — a logo that 404s is worse than no
# logo. Inline costs bytes once and nothing after.
#
# A CHANNEL WITH NO BRAND GETS NO BRAND MARK. Email, SMS, reviews and comments are categories,
# not products; giving them an invented logo would imply a vendor we have not integrated. They
# take a neutral glyph and keep their word.
_MARKS = {
  "messenger": ("#00B2FF", "M.001 11.639C.001 4.949 5.241 0 12.001 0S24 4.95 24 11.639c0 6.689-5.24 11.638-12 11.638-1.21 0-2.38-.16-3.47-.46a.96.96 0 00-.64.05l-2.39 1.05a.96.96 0 01-1.35-.85l-.07-2.14a.97.97 0 00-.32-.68A11.39 11.389 0 01.002 11.64zm8.32-2.19l-3.52 5.6c-.35.53.32 1.139.82.75l3.79-2.87c.26-.2.6-.2.87 0l2.8 2.1c.84.63 2.04.4 2.6-.48l3.52-5.6c.35-.53-.32-1.13-.82-.75l-3.79 2.87c-.25.2-.6.2-.86 0l-2.8-2.1a1.8 1.8 0 00-2.61.48z"),
  "instagram": ("#FF0069", "M7.0301.084c-1.2768.0602-2.1487.264-2.911.5634-.7888.3075-1.4575.72-2.1228 1.3877-.6652.6677-1.075 1.3368-1.3802 2.127-.2954.7638-.4956 1.6365-.552 2.914-.0564 1.2775-.0689 1.6882-.0626 4.947.0062 3.2586.0206 3.6671.0825 4.9473.061 1.2765.264 2.1482.5635 2.9107.308.7889.72 1.4573 1.388 2.1228.6679.6655 1.3365 1.0743 2.1285 1.38.7632.295 1.6361.4961 2.9134.552 1.2773.056 1.6884.069 4.9462.0627 3.2578-.0062 3.668-.0207 4.9478-.0814 1.28-.0607 2.147-.2652 2.9098-.5633.7889-.3086 1.4578-.72 2.1228-1.3881.665-.6682 1.0745-1.3378 1.3795-2.1284.2957-.7632.4966-1.636.552-2.9124.056-1.2809.0692-1.6898.063-4.948-.0063-3.2583-.021-3.6668-.0817-4.9465-.0607-1.2797-.264-2.1487-.5633-2.9117-.3084-.7889-.72-1.4568-1.3876-2.1228C21.2982 1.33 20.628.9208 19.8378.6165 19.074.321 18.2017.1197 16.9244.0645 15.6471.0093 15.236-.005 11.977.0014 8.718.0076 8.31.0215 7.0301.0839m.1402 21.6932c-1.17-.0509-1.8053-.2453-2.2287-.408-.5606-.216-.96-.4771-1.3819-.895-.422-.4178-.6811-.8186-.9-1.378-.1644-.4234-.3624-1.058-.4171-2.228-.0595-1.2645-.072-1.6442-.079-4.848-.007-3.2037.0053-3.583.0607-4.848.05-1.169.2456-1.805.408-2.2282.216-.5613.4762-.96.895-1.3816.4188-.4217.8184-.6814 1.3783-.9003.423-.1651 1.0575-.3614 2.227-.4171 1.2655-.06 1.6447-.072 4.848-.079 3.2033-.007 3.5835.005 4.8495.0608 1.169.0508 1.8053.2445 2.228.408.5608.216.96.4754 1.3816.895.4217.4194.6816.8176.9005 1.3787.1653.4217.3617 1.056.4169 2.2263.0602 1.2655.0739 1.645.0796 4.848.0058 3.203-.0055 3.5834-.061 4.848-.051 1.17-.245 1.8055-.408 2.2294-.216.5604-.4763.96-.8954 1.3814-.419.4215-.8181.6811-1.3783.9-.4224.1649-1.0577.3617-2.2262.4174-1.2656.0595-1.6448.072-4.8493.079-3.2045.007-3.5825-.006-4.848-.0608M16.953 5.5864A1.44 1.44 0 1 0 18.39 4.144a1.44 1.44 0 0 0-1.437 1.4424M5.8385 12.012c.0067 3.4032 2.7706 6.1557 6.173 6.1493 3.4026-.0065 6.157-2.7701 6.1506-6.1733-.0065-3.4032-2.771-6.1565-6.174-6.1498-3.403.0067-6.156 2.771-6.1496 6.1738M8 12.0077a4 4 0 1 1 4.008 3.9921A3.9996 3.9996 0 0 1 8 12.0077"),
  "whatsapp":  ("#25D366", "M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413Z"),
}
_GLYPHS = {
  "email":   ("currentColor", "M1.5 4.5h21v15h-21zM1.5 5.5l10.5 7 10.5-7"),
  "sms":     ("currentColor", "M2 4h20v13H8l-5 4v-4H2z"),
  "review":  ("currentColor", "M12 2l3 6.5 7 .9-5 4.8 1.2 7L12 17.8 5.8 21.2 7 14.2 2 9.4l7-.9z"),
  "comment": ("currentColor", "M2 4h20v13H8l-5 4v-4H2z"),
}


def _mark(platform: str, size: int = 19) -> str:
    """The channel's own logo, or a neutral glyph where the channel is a category not a brand."""
    v = str(platform or "").strip().lower()
    if v in _MARKS:
        hexc, d = _MARKS[v]
        return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" aria-hidden="true">'
                f'<path fill="{hexc}" d="{d}"/></svg>')
    if v in _GLYPHS:
        _, d = _GLYPHS[v]
        return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" aria-hidden="true" '
                f'fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" '
                f'stroke-linejoin="round" style="color:var(--dimmer)"><path d="{d}"/></svg>')
    return ""


def _monogram(name: str) -> str:
    """Initials, because there is no photograph and there is not going to be one.

    The vendor hands us a display name and nothing else — no avatar URL, and `inbox_conversations`
    has no column for one. So this is not a placeholder standing in for an image that failed to
    load; it IS the avatar, drawn the way Messages draws a contact with no picture. Two initials
    from the first and last word, one if that is all there is, and a dash for a nameless thread
    rather than a letter we invented.
    """
    parts = [w for w in str(name or "").replace("_", " ").split() if w]
    if not parts:
        return "&middot;"
    if len(parts) == 1:
        return _esc(parts[0][:2].upper())
    return _esc((parts[0][0] + parts[-1][0]).upper())


# ── the tab bar ─────────────────────────────────────────────────────────────────────────────
# INSTALLED, THERE IS NO BROWSER CHROME. A PWA on a home screen has no back button, no address
# bar and no tabs, so the app has to supply its own furniture — and on a phone that furniture is
# a bottom bar, in reach of a thumb, in the idiom every native app on the device already uses.
# A TAB LABEL GROWS WITH THE READER'S TEXT SIZE ONLY A LITTLE, to 14px, as the system's own tab
# bars do: four labels and the orb share one row, and at 150% they ran together (2026-09-29).
# THE TABS ARE THE MENU'S ROWS: Messages · Replies · Search · Settings (owner, 2026-09-29, IA
# decisions D1 and D5 in docs/SCOPE_APP_IA.md). "Today" went — the day's summary is the Morning
# Review's job and "right now" is the Base Machine's — so the app opens on its messages. A tab bar
# with a dead tab in it is the dead control this codebase keeps deleting.
_TABS = (
  # THE TRAY IS THE ONE EVERY APP DRAWS; WHAT ARRIVES IN IT IS OURS. Owner, 2026-09-16: the
  # fonts and "the inbox and settings icons" are the two places this product stops copying the
  # competitor. A plain tray says mail; a tray with separate streams running into it says the
  # thing the product actually is — messages from different places landing in one.
  #
  # NO COUNT IS ENCODED. Two strokes because two survive at 23px and three turn into a smudge
  # (rendered at tab size and compared before choosing, alongside four other candidates) — not
  # because the box polls two channels. An icon that meant "two" would be wrong the week email
  # lands.
  #
  # AND IT IS DELIBERATELY NOT AN ARROW. A downward arrow into a tray is the download glyph on
  # every platform there is, and the first draft of this icon was exactly that.
  ("/inbox/inbox", "Messages",
   "M3.5 12.5v5.6a1.4 1.4 0 0 0 1.4 1.4h14.2a1.4 1.4 0 0 0 1.4-1.4v-5.6h-4.3l-1.3 2.2H9.1"
   "l-1.3-2.2zM7.6 4.3 9.9 9.6M16.4 4.3 14.1 9.6"),
  # REPLIES: the drafts the box wrote, waiting for a yes. The reply arrow, the one every mail app
  # draws for "answer this".
  ("/inbox/waiting", "Replies",
   "M9.5 14.5 4.5 9.5l5-5M4.5 9.5h10a5.5 5.5 0 0 1 0 11H12"),
  # SEARCH EARNED A TAB WHEN IT EARNED A SCREEN (2026-09-18). It had no address: the field lives
  # on the list, so reaching "search" meant first loading fifty conversations you did not want,
  # and on a phone the field then sits at the top of the screen where a thumb cannot go.
  #
  # THE GLYPH IS `_find`'s OWN, not a second magnifier. Two drawings of one destination is the
  # drift `_TAB_ICON` exists to prevent, and it starts with a convenient local copy.
  #
  # AND IT IS THE FOURTH, WHICH IS A NUMBER THAT MATTERS. The owner chose the competitor's bar
  # with a hero button dead centre (2026-09-18); a centred button needs an EVEN number of tabs
  # around it or it lands on top of the middle one. Three screens could not carry it. Four can.
  ("/inbox/search", "Search",
   "M11 4.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13M16 16l4.2 4.2"),
  # A COG IS A MACHINE'S ICON AND THIS IS NOT A MACHINE HE OPERATES. The old one was the
  # standard 12-tooth gear — the single most-drawn glyph in software, and the exact "basic as
  # hell" the owner named. Two sliders say what this screen is: a small number of his own
  # choices, set where he wants them. It also reads at 23px, which the gear barely did.
  ("/inbox/settings", "Settings",
   "M4 8h9M17 8h3M4 16h1M9 16h11"
   "M17 8a2 2 0 1 0-4 0 2 2 0 1 0 4 0M9 16a2 2 0 1 0-4 0 2 2 0 1 0 4 0"),
)


# ── this machine's own place in the box's menu ───────────────────────────────────────────────
#
# THE FIRST MACHINE EVER TO REGISTER A SECTION, which makes these lines the point where the
# plug-in model stops being a design and starts being behaviour a buyer can see. Owner,
# 2026-09-17: *"Each machine will plug in different menu options"* and *"the Customer machine
# plug-ins will add dashboard areas."* Core registers Dashboard and nothing else; this box grows
# an Inbox row because it carries THIS machine, and a box sold without it has no Inbox row to
# explain away. Nothing in `core/` names customer_voice to make that happen.
#
# TWO TAPS TO MESSAGES, which is exactly what he described: *"When people click inbox and then
# messages, it launches into the exact inbox that you designed in that mock up."* So the section's
# own href is `/inbox/` — the day — and Messages is a row beneath it rather than the section
# itself. No shipped URL moves.
#
# THE ICONS ARE THE TAB BAR'S, LOOKED UP BY HREF RATHER THAN COPIED. Two menus drawing the same
# destination with two different glyphs is the drift `rail_html` exists to prevent, and a
# duplicated `d` string is how it would start — the reasoning behind these three shapes is in
# `_TABS` above and it should not have to be true in two places.
#
# THE LABELS ARE DELIBERATELY NOT SHARED. The tab bar says "Inbox" because it sits at the bottom
# of the whole app; the rail says "Messages" because it sits INSIDE a section already titled
# Inbox, where a row called Inbox says nothing to the person reading it.
_TAB_ICON = {href: d for href, _label, d in _TABS}

shell.register_section(
    # UNIFIED INBOX, NOT INBOX. Owner, 2026-09-21: *"the name of the machine should be Unified
    # Inbox not Inbox."* It is the product's name and the dashboard card already used it, so the
    # rail was the one surface still calling it something shorter than it is called everywhere
    # else. `Messages` below is the SCREEN inside it and keeps its own name.
    "inbox", order=10, machine="customer_voice", title="Inbox Machine",
    href="/inbox/", icon=_TAB_ICON["/inbox/inbox"],
    items=[
        {"key": "messages", "label": "Messages", "href": "/inbox/inbox",
         "icon": _TAB_ICON["/inbox/inbox"]},
        # REPLIES TO SEND SITS SECOND, NEXT TO MESSAGES, because it is the same queue seen from the
        # other end: Messages is who has written to you; this is the answers waiting for a yes.
        # "Drafts" until 2026-09-29, when the owner chose the page's own words for it (IA D5).
        # The owner asked for it by name on 2026-09-22 and called the drafting behind it "one of
        # the killer features for the unified inbox — the big time-saver".
        #
        # ROUTE `/inbox/waiting`, LABEL "Drafts". `/inbox/drafts` is already the AI-account form
        # (§2.6) — a misnamed route from before this screen existed. Renaming it today would
        # break a link somebody may already hold; it should move in its own change.
        {"key": "waiting", "label": "Replies to send", "href": "/inbox/waiting",
         "icon": _TAB_ICON["/inbox/waiting"]},
        # SEARCH IS LISTED HERE BECAUSE IT IS A SCREEN OF THIS SECTION, and leaving it out was a
        # real defect rather than an omission: `shell.is_current` falls back to the section's own
        # href for a path no item claims, so standing on /inbox/search the rail lit *Today*. A
        # menu that tells you that you are somewhere you are not is worse than a menu with a gap
        # in it. Found by rendering the four screens and reading which row came back marked.
        {"key": "search", "label": "Search", "href": "/inbox/search",
         "icon": _TAB_ICON["/inbox/search"]},
        # SETTINGS STAYS A ROW HERE FOR NOW, and this is the one item with a question over it.
        # `docs/PLAN_SCREENS.md` §4.3 argues inbox settings belong in the box's Settings registry
        # rather than in a second settings screen inside the inbox — but that registry is OSDev4's
        # #1326 and it is still a draft. Dropping the row before the replacement exists would
        # orphan a shipped page from the menu, so it keeps its place and moves when there is
        # somewhere to move it to. The owner's own words leave room for either: *"You also have
        # the settings, so those are probably the settings for the inbox in particular."*
        {"key": "settings", "label": "Settings", "href": "/inbox/settings",
         "icon": _TAB_ICON["/inbox/settings"]},
        # SET UP IS IN THE MENU NOW, and until today it was in no menu at all. Measured on a box
        # exported from .18.3 and signed into with nothing connected: `/dashboard` carried ZERO
        # links to this screen, `/inbox/settings` carried ZERO, and the rail did not list it — the
        # only link in the whole product was one on the inbox page. So the best screen we have,
        # the one that says "Three things only you can do", was reachable by accident.
        #
        # `shell.SETUP_KEY` IS THE POINT OF THE KEY. Core's dashboard asks the registry for an item
        # keyed `setup` rather than learning this machine's URL, so the card it draws works on any
        # box that has a set-up screen and draws nothing on one that does not. This line is what
        # makes this box one of the former.
        {"key": shell.SETUP_KEY, "label": "Set Up Your Inbox", "href": "/inbox/setup",
         "icon": _TAB_ICON.get("/inbox/settings", "")},
    ])

# THE UNIFIED INBOX'S OWN SETTINGS, AS A MENU (owner, 2026-09-24: "The unified inbox settings should
# have sub menu choices"). The same two-level shape System Settings has: tap Settings in the inbox
# and the rail lists one row per setting's ONE HOME (docs/SCOPE_ONE_PLACE_PER_SETTING.md), with the
# back arrow returning to the Unified Inbox. Nested through `shell.register_section(parent=...)`,
# which keeps it out of the top-level menu and lets it claim pages that do not live under its path.
shell.register_section(
    "inbox_settings", order=10, machine="customer_voice", title="Settings",
    href="/inbox/settings", parent="inbox", icon=_TAB_ICON.get("/inbox/settings", ""),
    items=[
        {"key": "overview", "label": "Overview", "href": "/inbox/settings"},
        {"key": "mailbox", "label": "Mailbox", "href": "/inbox/mailbox"},
        {"key": "signature", "label": "Email Signature", "href": "/inbox/signature"},
        {"key": "reply_style", "label": "Reply Style", "href": "/inbox/reply-style"},
        {"key": "sending", "label": "Sending", "href": "/inbox/sending"},
        {"key": "pitch_back", "label": "Cold Pitches", "href": "/inbox/pitch-back"},
        {"key": "snippets", "label": "Saved Replies", "href": "/inbox/snippets"},
        {"key": "channels", "label": "Social Accounts", "href": "/inbox/connect"},
    ])


def _unread() -> int:
    """How many conversations he has not read — 0 on any box that cannot answer.

    THE TAB BAR IS IN `_shell`, so this runs on every page this app draws. It is one indexed
    query and it is wrapped, because a number that cannot be fetched must cost him a dot, never
    a page: a box mid-migration, a Space with no inbox tables, a locked database during a poll.
    Silent by design — `log.info` and not `warning`, since the honest reading of a failure here
    is "nothing to report", which is also what 0 renders as.
    """
    try:
        from marketing.customer_voice.inbox import store as _store
        return int(_store.unread_conversations(_space()))
    except Exception as e:                       # noqa: BLE001 — see above
        log.info("voice.unread_unreadable", extra={"error": type(e).__name__})
        return 0


def _tabbar(here: str) -> str:
    unread = _unread()
    out = []
    # EVERY SETTING'S HOME LIGHTS THE SETTINGS TAB, not only pages under /inbox/settings: the
    # mailbox lives at /inbox/mailbox and is still one of Settings' rows (the nested section).
    try:
        _sec = shell.current(here)
        if _sec is not None and _sec.key == "inbox_settings":
            here = "/inbox/settings"
    except Exception:                                    # noqa: BLE001 — a tab bar never 500s
        pass
    for href, label, d in _TABS:
        on = " on" if (here == href or here.startswith(href + "/")) else ""
        cur = ' aria-current="page"' if on else ""
        # A DOT, NOT A NUMBER — owner, 2026-09-17, choosing between the two rendered side by
        # side. From another screen the only question is whether anything is waiting, and a dot
        # answers exactly that; a number invites a precision the tab then has to defend (two
        # conversations, or two messages? two people, or one who wrote twice?). The icon's own
        # comment above already refuses to encode a count for the same reason.
        #
        # IT IS DRAWN ON THE INBOX TAB EVEN WHILE HE IS STANDING ON IT. Suppressing it there
        # would be a rule to learn — "why did it vanish?" — and the dot is still true: the rows
        # under it are bold. It goes out when he has read them, which is the whole contract.
        #
        # AND THE NUMBER IS THE PART A SCREEN READER GETS. A dot is invisible to one, exactly as
        # the row's bold is, so the count goes in `.vh` where it costs no pixels. `aria-hidden`
        # on the dot itself so the same fact is not announced twice.
        mark = ""
        if unread and href == "/inbox/inbox":
            mark = ('<span class="mark" aria-hidden="true"></span>'
                    f'<span class="vh">{unread} unread</span>')
        out.append(
            f'<a class="tab{on}" href="{href}"{cur}>'
            f'<svg width="23" height="23" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            f'<path d="{d}"/></svg>{mark}<span>{label}</span></a>')
    # ── THE ORB, DEAD CENTRE, DOING NOTHING ─────────────────────────────────────────────────
    #
    # OWNER, 2026-09-18: *"Ship the orb in place and make it look really cool like glass, but
    # don't make it do anything yet."* His call, made after I argued the other way: a control
    # that does nothing is the dead control this app keeps deleting. Recorded because the
    # objection was mine and it lost, and because the next person will want to wire it up.
    #
    # SO IT IS NOT A CONTROL AT ALL. Not a disabled button, which reads as broken, and not a
    # link to somewhere unrelated, which is worse — a decorative `span`, `aria-hidden`, out of
    # the focus order, with `pointer-events: none` so a tap falls through rather than lighting
    # a press state that leads nowhere. Nothing is announced to a screen reader, nothing is
    # reachable by keyboard, and nobody is promised anything.
    #
    # WHAT IT IS FOR is voice, which does not exist yet: speech has to be captured, transcribed
    # by a vendor, metered through `cost_guard` before the call and recorded after, and only
    # then reasoned about through `brain.think()`. The day that lands, this span becomes a
    # button and this comment goes with it.
    #
    # IT NEEDS AN EVEN NUMBER OF TABS EITHER SIDE and there are exactly four, which is why the
    # search screen had to be real before this could be centred. With three it would have sat
    # on top of the middle one.
    # THE SLOT AND THE BEAD ARE TWO ELEMENTS, and the first version made them one: `flex:1`
    # beat `width:54px`, so the glass painted on a 78x54 box with `border-radius:50%` — an
    # ellipse. Measured, not noticed: the render reported width 78. The slot takes the flex
    # share; the bead is the circle inside it.
    orb = ('<span class="orb" aria-hidden="true">'
           '<span class="bead"></span></span>')
    out.insert(len(out) // 2, orb)

    # NOT "Sections" ANY MORE, AND THAT IS NOT COSMETIC. Core's rail is `aria-label="Sections"`,
    # and this page now carries both — two navigations with one accessible name, which a
    # screen reader offers as "Sections navigation" twice with no way to tell them apart.
    # They are different scopes and now say so: the rail lists the box's sections, this bar
    # lists the three screens inside this one.
    return (f'<nav class="tabs" aria-label="Inbox screens">'
            f'<div class="tabs-in">{"".join(out)}</div></nav>')


# THE APP'S ONE NAME. Every inbox screen installs as this and titles its tab with it (owner,
# 2026-09-29: "We want every screen of the unified inbox to bookmark the same way"). The manifest's
# short_name and the iOS title tag both read it, so the two cannot drift.
APP_TITLE = "Inbox Machine"

THEME_COOKIE = "aios_voice_theme"
# THE BAR BEHIND THE CLOCK IS THE PAGE'S GROUND. Light was the grey-blue of the inbox
# before it took box.css; the page is the box's cream now (core/dash/static/box.css --ground,
# the value core's own screens send), so an installed app showed a blue-grey band over cream.
# ONE TABLE, core's: this was a copy, and its dark half went stale the day box.css took the
# homepage's dark ground (PR #1659). tests/test_the_box_wears_one_look.py holds theme.GROUND to
# box.css --ground, so pointing here keeps the bar and the page the same colour in both themes.
from core.dash.theme import GROUND as _THEME_BG  # noqa: E402


def _theme() -> str:
    """'light' | 'dark' | '' — and EMPTY MEANS WHITE, not "ask the OS".

    This said "the OS decides" until 2026-09-18, which stopped being true on 09-16 when the
    prefers-color-scheme block was removed to honour "white screens first and foremost". The
    sentence outliving the behaviour is how the Settings screen came to offer a "System" segment
    that could not follow the system.
    """
    # THE PERSON'S CHOICE FIRST (core/dash/theme.py), so one switch governs every screen; the
    # cookie is only what a phone remembers from before that existed.
    from core.dash import theme as _core_theme
    uid = _core_theme._user_id()
    mine = _core_theme.chosen(uid)
    if mine:
        return mine
    try:
        v = (request.cookies.get(THEME_COOKIE) or "").strip().lower()
    except Exception:                            # noqa: BLE001 — no request, no cookie
        return ""
    v = v if v in ("light", "dark") else ""
    if v and uid:
        # CARRIED OVER ONCE (WebDev2, §14.4): a phone's old choice becomes the person's setting the
        # first time they're seen signed in, and from then on the setting is the answer.
        try:
            _core_theme.put(uid, v)
        except Exception:                        # noqa: BLE001 — the page still renders their choice
            pass
    return v


# ── the box's menu, on the one screen that never had it ──────────────────────────────────────
#
# OWNER, 2026-09-18: *"Why is there no hamburger menu."* There was one — `core.dash.home` has
# shipped the drawer since the rail landed, and every page drawn by `chrome()` wears it. This app
# draws its own page, so it wore none of it: no rail on a desktop, no menu on a phone, and the
# only way to the rest of the box was the back arrow. The screen a buyer opens most was the screen
# with the least navigation in it.
#
# IT IMPORTS THE RENDERER, IT DOES NOT REIMPLEMENT IT. `rail_html` resolves the sections from the
# same registry the dashboard reads, refuses rows this build does not serve, and marks the current
# one — so a machine that registers a section appears here on the day it ships, with no edit to
# this file. A second hand-written menu would have been correct for about a week.
def _rail(path: str) -> str:
    """The box's sections, from core, or nothing at all if the registry cannot answer.

    A FAILURE HERE COSTS THE MENU, NEVER THE PAGE — the same rule `_unread` follows for the dot.
    This runs on every screen this app draws, including on a box mid-migration, and an inbox that
    500s because its navigation could not resolve is a worse product than one with no navigation.
    """
    try:
        from core.dash.home import rail_html
        return rail_html(path)
    except Exception as e:                       # noqa: BLE001 — see above
        log.warning("voice.rail_unrenderable",
                    extra={"error": f"{type(e).__name__}: {e}"[:160]})
        return ""


def _menu_button() -> str:
    """The menu button: the client's icon with the menu badge. CORE'S, not a second drawing.

    THE SAME MARKUP AT EVERY WIDTH, because the drawer is a CSS-only checkbox and a button that
    exists at one width and not another cannot be the label the stylesheet targets. Above 820px the
    rail is on screen, so CSS leaves the icon as the bar's brand mark: no badge, nothing to press.
    """
    # THE CLIENT'S ICON, WITH THE MENU BADGE (owner, 2026-09-29, option B): core's, so it is the
    # same button the Base Machine's screens draw.
    return _look.menu_button()


def _trail(path: str) -> str:
    """The box's way back, on a screen drilled into from a menu (owner, 2026-09-29: "make them
    standard anytime you're drilled down to a sub menu page"). CORE'S, so every machine draws it the
    same (`core.dash.home.trail`). NOT ON A TAB: Messages, Replies, Search and Settings are this
    app's top level, peers in the bar at the bottom, and a tab never carries a way back."""
    if any(path == href for href, _l, _d in _TABS):
        return ""
    try:
        from core.dash.home import link_back, trail
        # A CONVERSATION IS DRILLED INTO FROM MESSAGES, the commonest way down in this app.
        if path.startswith("/inbox/inbox/"):
            return link_back("/inbox/inbox", "Messages", "Conversation")
        return trail(path)
    except Exception:                                    # noqa: BLE001 — a link, never a 500
        return ""


def _shell(body: str, *, day: str = "", here: str = "", wide: bool = False, bar: str = "") -> str:
    brand = dash.brand()
    # HIS CHOICE IS STAMPED ON <html>. No stamp means he has never chosen, and that renders
    # WHITE — the OS is not consulted (owner, 2026-09-16: white screens first and foremost).
    th = _theme()
    from core.dash import theme as _core_theme
    stamp = _core_theme.html_attr(th) if th else ""
    # THE STATUS BAR HAS TO MOVE TOO. Installed, iOS paints the area behind the clock with
    # theme-color; leaving it at the dark value puts a black band above a white app. Unstamped,
    # both are declared with a media attribute and the OS picks.
    if th:
        # System carries two media-matched colours and the script that picks before paint.
        tc = _core_theme.head_tags(th)
    else:
        # UNSTAMPED IS WHITE, not "ask the OS". Declaring the dark variant here would paint a
        # black band above a white app on an installed iOS home-screen icon — the status bar
        # following a preference the page itself no longer follows.
        tc = f'<meta name="theme-color" content="{_THEME_BG["light"]}">' + _core_theme.status_bar("light")
    return f"""<!doctype html><html lang="en"{stamp}><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow">
{tc}
<link rel="manifest" href="/inbox/manifest.webmanifest">
<meta name="apple-mobile-web-app-title" content="{APP_TITLE}">
<!-- APPLE STILL READS ITS OWN META. The manifest's `display` is what MDN says iOS requires before
     `Notification` even exists, and this legacy pair is what older iOS reads for the same thing.
     Both cost one line and the failure they prevent is silent. -->
<meta name="apple-mobile-web-app-capable" content="yes">
<title>{_esc(brand)} · {APP_TITLE}</title>{_look.head_tags()}<style>{CSS}</style></head>
<body{' class="ib"' if wide else ''}>
<input class="navtoggle" type="checkbox" id="navtoggle" aria-controls="railnav">
<div class="bar{' bar-find' if bar else ''}"><div class="bar-in">{_menu_button()}
{bar or f'<span class="brand">{APP_TITLE}</span>'}</div></div>
<label class="scrim" for="navtoggle" aria-label="Close menu"></label>
<div class="lay">{_rail(here or request.path)}
<main class="main"><div class="wrap">{_trail(here or request.path)}{body}</div></main></div>
{_tabbar(here or request.path)}
<script>{JS}</script></body></html>"""


# ── Today ───────────────────────────────────────────────────────────────────────────────────
def _reader_zone():
    """THE CLOCK OF THE PERSON READING, not the box's. -> a tzinfo, or None for the host's clock.

    A SOLD BOX RUNS ON UTC (`cost.timezone`), so the box's clock said "Good afternoon" to the owner
    over his morning coffee (owner, 2026-09-24: "It should be on the local time not UTC").
    `notify.buyer_timezone()` is the one answer the Morning Review and every notice already use:
    the settings override, then the zone the buyer's own browser gave when the box was claimed,
    then the box's. The greeting and every time this app prints read it, so the screen and the 8am
    message agree on when morning is. Never raises: a clock never 500s a page.
    """
    from zoneinfo import ZoneInfo
    try:
        from core import notify
        return ZoneInfo(notify.buyer_timezone())
    except Exception as e:                       # noqa: BLE001 — unknown zone, no tzdata
        log.warning("voice.reader_zone_unavailable",
                    extra={"error": f"{type(e).__name__}: {e}"[:120]})
    try:
        from core.report import tz
        return tz()
    except Exception:                            # noqa: BLE001
        return None


@blueprint.get("/inbox/")
@blueprint.get("/inbox")
def r_today():
    """THE INBOX OPENS ON ITS MESSAGES. Owner, 2026-09-29, IA decision D1 (docs/SCOPE_APP_IA.md):
    three screens summarised the same day, so the day went to the Morning Review and "right now" to
    the Base Machine, and this app's "Today" screen went. Its address still answers — installed
    icons, bookmarks and old notifications open it — and sends them to the messages."""
    return redirect("/inbox/inbox", code=302)


# ── time a person can read ──────────────────────────────────────────────────────────────────
def _when(v) -> str:
    """An ISO instant as a day and a time, IN THE BOX'S OWN TIMEZONE.

    THE HELPER IS `tz`, AND I ONCE ASKED FOR `zone`. #1081: the wrong name raised an ImportError
    that a bare `except` swallowed, so every timestamp on every page of the lead app was UTC for
    days while a worked example in the PR body claimed otherwise. On a phone screen "when did they
    message me" is most of the value, so this is written with the name checked and the swallow
    made audible.
    """
    s = str(v or "").strip()
    if not s:
        return ""
    from datetime import datetime
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s                                 # unparseable passes through, never guessed at
    if d.tzinfo is not None:
        z = _reader_zone()                       # the reader's clock, not the box's UTC
        if z is not None:
            d = d.astimezone(z)
    return d.strftime("%a %-d %b, %-H:%M")


# ── which client's inbox ────────────────────────────────────────────────────────────────────
def _ago(v) -> str:
    """A short relative age, the way Kinso and Mail write it: 3m, 5h, 2d, then 4 Aug.

    THE LONG FORM WAS EATING THE NAME. "Tue 15 Sep, 7:41" is 16 characters sitting in the same
    row as the person who messaged you, and at 390px it truncated "Samantha Reyes" to
    "Samantha R…". The name is the thing being scanned; the timestamp is context. Measured on
    the rendered screen, not guessed.

    The full instant is still available in the thread, where there is room for it and where
    "when exactly did they say this" is an actual question.
    """
    s = str(v or "").strip()
    if not s:
        return ""
    from datetime import datetime, timezone
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s
    if d.tzinfo is None:
        return d.strftime("%-d %b")
    now = datetime.now(timezone.utc)
    secs = (now - d.astimezone(timezone.utc)).total_seconds()
    if secs < 0:
        secs = 0
    if secs < 3600:
        return f"{int(secs // 60) or 1}m"
    if secs < 86400:
        return f"{int(secs // 3600)}h"
    if secs < 7 * 86400:
        return f"{int(secs // 86400)}d"
    z = _reader_zone()                           # the reader's date, not the box's
    if z is not None:
        d = d.astimezone(z)
    return d.strftime("%-d %b")


def _space() -> str:
    """The Space this request belongs to — the tenant boundary, resolved ONCE here.

    ONE READER, for the same reason `core.dash.host_label` exists: two scopes that resolve the
    host differently can disagree about whose data a page is showing, and on this surface that
    means one client reading another client's customer messages. So this goes through
    `core.spaces` like every other scope rather than reading the host itself.

    A LABELLED HOST IS THAT SPACE; a bare host is the box's own single tenant. That is the same
    rule the content board keeps (`space_by_label`: no alias, no default, an unlabelled host is
    the whole box), and it fails the safe way — a label no Space carries resolves to None, so the
    caller gets the default rather than being silently scoped onto some other brand.
    """
    from core import spaces
    try:
        s = spaces.space_by_label(dash.host_label())
    except Exception:                            # noqa: BLE001 — no request, no label
        s = None
    return str((s or {}).get("name") or spaces.DEFAULT)


# ── the inbox ───────────────────────────────────────────────────────────────────────────────
# THE SCREEN THE PRODUCT IS ACTUALLY FOR (spec §6 stage 2). Today is the number; this is the thing
# a person came to read.
#
# NO MASKING HERE, AND THAT IS A DIVERGENCE FROM SPEC §5.5 WORTH SAYING OUT LOUD. That note asks
# for the participant to be masked in the view model. Masking exists in `machine_app` because that
# app has an ANONYMOUS tier — a public page needs a redacted version of a real row. This app has no
# anonymous tier at all (see `_gate`): every request that reaches here already carries the owner's
# dash session or his app token, which is exactly the credential set `unlocked()` treats as fully
# unmasked on the lead app. A mask that hides names from the only people who can open the page is a
# control that protects nobody, and a flag that is always false is the dead control this codebase
# keeps deleting. So the thing protecting these names is the DOOR, and the test that pins it is
# "answers nobody without a credential" — which is what would catch a public tier being added later.
def _thread_href(zcid: str) -> str:
    from urllib.parse import quote as _q
    return "/inbox/inbox/" + _q(str(zcid), safe="")


def _channel(value: str) -> str:
    """A name a person uses, for a value the vendor spells its own way.

    THE TABLE LIVES IN `inbox/channels.py` AND NOT HERE. It used to live in both, and the two
    copies were one edit apart from disagreeing: the worker told the owner "New Messenger
    message" from its own literal while this screen read a map that already knew about six
    other channels. A channel's name is one fact, and the file that decides which channels get
    polled is the file that should hold it.

    "Unknown" IS THIS SCREEN'S WORD FOR EMPTY, not the shared default — a heading over a thread
    has to say something, and Slack's sentence form wants "this channel" instead. `channels.name`
    makes the caller say which, precisely so neither one silently borrows the other's.
    """
    from marketing.customer_voice.inbox import channels as _ch
    return _ch.name(value, fallback="Unknown")


def _drafts_row(*, primary: bool = True) -> str:
    """docs/COPY_INBOX_FIRST_RUN.md §2.6 — the row that turns drafting on.

    THIS IS NOT AN OPTION AT LAUNCH, IT IS THE ONLY SWITCH. A delivered box ships
    `brain.backend: api` with no key from install.sh and none from the provisioner, so
    `drafter/draft.py` returns {"skipped": "unconfigured"} and the box files messages and writes
    nothing. The buyer has no shell and no `.env`. Without this row the product silently does not
    do the thing it was bought for — §2.6's warning, in its own words: "a buyer sees an empty
    reply box, is never told drafting exists, and concludes the box does not do what the card
    said. Today that is exactly what a delivered box does."

    PHRASED AS A CAPABILITY, NOT A MISSING KEY, which is §2.6's call and a good one: "No API key
    configured" is a fault report about our plumbing, and the buyer did nothing wrong and does
    not know what an API key is. It says what the box can do, then what it needs to do it — and
    turns the one real advantage of bring-your-own-key, that nothing they receive passes through
    us, from an apology into the reason.

    WHEN MANAGED INFERENCE LANDS, §2.5's wording replaces this and the row stays where it is.
    """
    from core import box_secrets
    # THE ROW ASKS WHETHER A DRAFT WILL ARRIVE, NOT WHETHER A KEY EXISTS, and those are two
    # different questions the moment a key can be revoked. `anthropic_state()` is the one reader
    # the set-up screen already uses, so the two cannot disagree about the same key.
    _ai = box_secrets.anthropic_state()

    # EVERY DOOR TO THE AI ACCOUNT GOES TO ITS ONE HOME, System Settings → AI account (owner,
    # 2026-09-24: one place per setting). It is the owner's page, so a member reads who to ask
    # instead of meeting a refusal. ONE INK PILL PER SCREEN: where a row above already carries
    # the screen's pill (Settings on a fresh box, channels first), this door is a link instead.
    def _door(label: str) -> str:
        if not _is_owner():
            return ('<p class="quiet" style="margin:10px 0 0">Only the owner of this box can '
                    'change the AI account.</p>')
        if not primary:
            return (f'<p style="margin:10px 0 0"><a href="/settings/ai" style="color:var(--href)">'
                    f'{_esc(label)} &rarr;</a></p>')
        return (f'<p style="margin:10px 0 0"><a class="btn" href="/settings/ai">{_esc(label)}'
                '</a></p>')
    if _ai["status"] == "not_connected":
        return ('<div class="setrow"><b>Writing your drafts</b>'
                '<span>Ownbox can write a reply for every message, ready for you to read and '
                'send. It needs an AI account to write with — yours, on your own bill, so '
                'nothing you receive passes through us.</span>'
                + _door("Connect an AI account") + '</div>')
    # TURNED OFF BY THE OWNER, AND THE AI ACCOUNT UNTOUCHED. "Turn off" used to delete the box's
    # AI key from this machine's screen: every machine on the box lost its account, a box on a
    # Claude subscription kept drafting anyway, and turning it back on meant signing in again.
    # It is a switch now, and the account stays exactly as it was.
    if not _drafting_on():
        return ('<div class="setrow"><b>Writing your drafts</b>'
                '<span>Off. Ownbox is not writing replies for you. Every message still arrives, '
                'and you can answer it by hand.</span>'
                + ('<span style="margin-top:8px">'
                   + _post_button("/inbox/drafts", "drafting", "on", "Turn on") + '</span>'
                   if _is_owner() else _OWNER_ONLY)
                + '</div>')
    # STOPPED, AND WHY, IN THE WORDS OF THE FIX. The key was checked when it was pasted, so a box
    # that lands here has had something CHANGE at the vendor — and the buyer's only clue used to
    # be drafts that stopped appearing. The two reasons take different actions, so they get
    # different sentences and different buttons.
    if _ai["status"] == "payment_required":
        return ('<div class="setrow"><b>Writing your drafts</b>'
                '<span>Paused. Your AI account needs credit before it will write any more '
                'replies — add some there and your box picks up on its own. Every message is '
                'still arriving and you can still answer by hand.</span>'
                '<p style="margin:10px 0 0"><a class="btn" '
                'href="https://console.anthropic.com/settings/billing" target="_blank" '
                'rel="noopener">Add credit</a></p></div>')
    if _ai["status"] == "needs_reauth":
        return ('<div class="setrow"><b>Writing your drafts</b>'
                '<span>Paused. Your AI account stopped accepting the key this box has — that '
                'usually means it was deleted or replaced in the console. Connect it again and '
                'drafting starts again. Every message is still arriving in the meantime.</span>'
                + _door("Fix it in AI account") + '</div>')
    # THE SECOND SENTENCE IS LOAD-BEARING AND DOES NOT GET CUT (§2.6). It is the promise the
    # whole product rests on, and Settings is where a nervous buyer goes to check it.
    return ('<div class="setrow"><b>Writing your drafts</b>'
            '<span>On. Ownbox writes a reply for every message that arrives. You read it and '
            'you send it — nothing goes out on its own.</span>'
            '<span style="margin-top:8px">Writing with your own AI account. '
            + ('<a href="/settings/ai" style="color:var(--href)">Change</a> · '
               + _post_button("/inbox/drafts", "drafting", "off", "Turn off")
               if _is_owner() else '')
            + '</span>'
            + ('' if _is_owner() else _OWNER_ONLY)
            + '</div>')


def _channels_row(*, primary: bool = True) -> str:
    """B1 — the row that connects the accounts the inbox reads FROM.

    THE PRODUCT HAD NO SUCH ROW UNTIL NOW, and that is the whole of why this exists. Measured
    2026-09-16: the only connect link in the system was minted by the provisioner and surfaced on
    the build page while an order was still building. A buyer who closed that tab, or who arrived
    after it, owned a box with no way to connect anything to it — Settings offered an AI key, a
    theme, an install guide and a paragraph about polling, and the inbox stayed empty forever with
    nothing on any screen saying why.

    NO NETWORK CALL FROM SETTINGS. This row reads the stored status only. Settings is the page a
    worried buyer opens, and a page that has to reach a vendor before it can paint is a page that
    hangs when the vendor is slow. The live account list lives one click away, on /inbox/connect,
    where a person has said they want to look."""
    from core import box_secrets

    # ONE INK PILL PER SCREEN: the inbox row above owns it while the mailbox is undone, and this
    # optional step's door is a link until then.
    def _door(label: str) -> str:
        if not _is_owner():
            return _OWNER_ONLY
        if primary:
            return (f'<p style="margin:10px 0 0"><a class="btn" href="/inbox/connect">'
                    f'{_esc(label)}</a></p>')
        return (f'<p style="margin:10px 0 0"><a href="/inbox/connect" style="color:var(--href)">'
                f'{_esc(label)} &rarr;</a></p>')
    st = box_secrets.zernio_state()
    status = st.get("status")
    if status == "not_connected":
        return ('<div class="setrow"><b>Your social accounts</b>'
                '<span>Ownbox reads your Instagram and Messenger for you and keeps every '
                'conversation in one place. Connect them with your own social account — it stays '
                'yours, and you can take it back any day.</span>'
                + _door("Connect your social accounts") + '</div>')
    if status == "payment_required":
        # THE ONE FAILURE A BUYER CAN ACTUALLY FIX, so it gets its own sentence instead of the
        # word the API uses. Told "authentication failed" they re-paste a perfectly good key.
        return ('<div class="setrow"><b>Your social accounts</b>'
                '<span>Your social account needs a payment method before it will connect any '
                'more accounts. Add one there, then come back — nothing here needs changing.'
                '</span>'
                + _door("Check your social accounts") + '</div>')
    if status == "needs_reauth":
        return ('<div class="setrow"><b>Your social accounts</b>'
                '<span>Ownbox can no longer reach your social account, so nothing new is '
                'arriving. Re-connect it and the inbox catches up on its own.</span>'
                + _door("Re-connect") + '</div>')
    return ('<div class="setrow"><b>Your social accounts</b>'
            '<span>Connected. New messages arrive on their own.</span>'
            + ('<span style="margin-top:8px">'
               '<a href="/inbox/connect" style="color:var(--href)">Add or remove a social account'
               '</a></span>' if _is_owner() else _OWNER_ONLY)
            + '</div>')


# ── what a row can tell you before you open it ──────────────────────────────────────────────
# Owner, 2026-09-16: "we're also going to add some different tags on each conversation and an
# action button menu on hover."
#
# EVERY TAG BELOW IS COMPUTED AND NONE IS TYPED. Each one comes from a column already on the
# conversation row plus `window.decide` — the SAME function the send path consults — so a tag can
# never promise a reply the box would then refuse to send. That is the whole point of putting
# them here: the reader learns whether he can still answer someone BEFORE he opens the thread and
# types, rather than after.
#
# THE BEST TAG IS THE ONE THAT IS MISSING, and it is missing honestly. "Waiting on you" — their
# message was the last one — needs the direction of the newest message per conversation, which
# `list_conversations` does not select; asking per row is the N+1 its own docstring refuses by
# name. That is one subquery in `inbox/store.py`, another machine's file, and it is on the wall
# for OSDev4 rather than reached into from here.
_TAG_LIMIT = 3

_WINDOW_TAG = {
    "blocked": ("Window closed", "warn"),
    "tagged":  ("Tagged reply only", "warn"),
    "limited": ("Limited replies", "warn"),
}


@functools.lru_cache(maxsize=64)
def _has_send_rule(platform: str) -> bool:
    """Is a send policy WRITTEN for this channel, on this box?

    ASKED THROUGH `window.decide`, NEVER BY READING ITS `_RULES`. The private table belongs to the
    send path; a screen that imported it would be a second reader free to drift from it, and this
    app already deleted one duplicated channel table for exactly that reason (see `_channel`). The
    public answer is enough: a message that arrived THIS INSTANT is inside any free window that
    exists, so FREEFORM means a rule is written and BLOCKED means there is none.

    ASKED AS "IS ONE WRITTEN", NOT "IS ONE OPEN", and the difference arrived with email. Every
    rule used to carry a window, so "a message that arrived this instant is FREEFORM" was the same
    question — it is not any more. Email's rule is written and cited, and `decide` still refuses
    it because there is no platform window at all and the box does not send UNATTENDED. Reading
    FREEFORM as "a rule exists" would report that written decision as a gap in our work.

    CACHED, because a rule is code and not data — it cannot change between two rows of one page.
    """
    from datetime import datetime, timezone
    from marketing.customer_voice.inbox import window as _w
    now = datetime.now(timezone.utc)
    return not _w.decide(platform, now.isoformat(), now).get("no_rule")


@functools.lru_cache(maxsize=64)
def _no_send_lane(platform: str) -> bool:
    """Is this a channel whose policy IS written, and says the send happens somewhere else?

    THE DIFFERENCE MATTERS TO A PERSON, which is the only reason it is worth a second function.
    "No reply rule" means nobody has read this channel's policy and the box refuses out of
    caution — a gap, and it reads like one. The other state is a channel whose policy IS read and
    says the send happens elsewhere; told the first sentence about the second, a buyer waits for
    us to finish something that is already finished.

    NOTHING IS IN THAT SECOND STATE TODAY. Email was the only one — "this box has no SMTP path
    and the owner has not ruled on an email send policy" — and he ruled on 2026-09-22, so the box
    sends it and this returns False for every channel. Kept for the next channel that needs it.

    ASKED THROUGH `window.decide` LIKE ITS SIBLING, never by reading `_RULES` — the flag rides the
    RESULT for exactly this caller. Cached for the same reason: a rule is code, not data.
    """
    from datetime import datetime, timezone
    from marketing.customer_voice.inbox import window as _w
    now = datetime.now(timezone.utc)
    return bool(_w.decide(platform, now.isoformat(), now).get("no_send_lane"))


def _tag_list(k: dict) -> list:
    """The tags for one conversation, MOST CONSTRAINING FIRST.

    Order is the message, the same rule the Today screen keeps: what stops him replying, then
    whether this person is new, then where they came from. Capped at three — a row that wraps
    into a wall of pills is the row he stops reading.
    """
    plat = str(k.get("platform") or "")
    out = []
    # AN AUTOMATION IS HANDLING IT (customer_voice/claims.py), so it isn't waiting on him: said first,
    # because it is why this row isn't in his count.
    if k.get("held_by") and not k.get("opted_out"):
        out.append((f'Handled by {k["held_by"]}', "draft"))
    if k.get("opted_out"):
        # THE ONE MISTAKE THIS SCREEN CAN HELP HIM MAKE is replying to someone who said STOP.
        out.append(("Opted out", "stop"))
    elif _no_send_lane(plat):
        # NOT A FAULT, SO NOT RED, and checked before the branch below because it is the more
        # specific fact: the rule for this channel IS written and says the send happens somewhere
        # else. "No reply rule" here would report a gap where there is a finished decision.
        #
        # NO CHANNEL IS IN THIS STATE TODAY. Email was the only one, and the owner retired it on
        # 2026-09-22 — the box sends email itself now. Kept because it is the shape the next such
        # channel needs and `decide` still carries the flag for it, but the comment that used to
        # sit here explained the branch by describing email, which is no longer true of email.
        out.append(("Send in your mail app", "warn"))
    elif not _has_send_rule(plat):
        # NOT A WINDOW PROBLEM, AND IT MUST NOT READ AS ONE. Nothing sends on a channel whose
        # policy nobody has written — `window.decide`'s most important branch — and "Window
        # closed" would quietly promise it reopens tomorrow.
        out.append(("No reply rule", "stop"))
    elif not k.get("has_inbound"):
        # A ROW WE KNOW ABOUT AND HAVE NEVER HEARD FROM. Every channel here is reply-only, so
        # there is no permission to answer — a different fact from a window that has shut.
        #
        # ASKS THE MESSAGES, VIA `has_inbound`, FOR THE SAME REASON `_compose` DOES: this read
        # `last_inbound_at` and so labelled "No inbound yet" onto rows whose thread prints the
        # customer's own words. A tag is a claim about the conversation, and that one was false.
        # A row with inbound and no readable clock now falls through to the window branch below,
        # where `decide(plat, None)` refuses and the row says "Window closed" — which is the
        # honest reading of not knowing WHEN they wrote, and matches the note the thread puts
        # above the reply box on the very same row. A warning, on both surfaces; never a block.
        out.append(("No inbound yet", "warn"))
    else:
        from marketing.customer_voice.inbox import window as _w
        _d = _w.decide(plat, k.get("last_inbound_at"))
        # NOT EVERY `BLOCKED` IS A SHUT WINDOW, and the pill must only ever claim the one thing
        # it says. `decide` answers for the UNATTENDED box, so email — which has no window at all
        # — comes back BLOCKED because the box does not mail anyone on its own yet. Read as a
        # decision alone that painted "Window closed" onto every email row: false on a channel
        # with no clock, and it tells a buyer they cannot answer their own customer when they can.
        #
        # THE FLAG IS WHY THIS IS ONE LINE. `no_window` rides the result for exactly this reader,
        # the same way `no_send_lane` and `no_rule` do, so the screen never reads `_RULES`.
        if _d["decision"] in _WINDOW_TAG and not _d.get("no_window"):
            out.append(_WINDOW_TAG[_d["decision"]])
    # THE "NEW" PILL USED TO BE HERE, on `message_count == 1`. Owner, 2026-09-17: "the new label
    # just gets in the way too. New messages should be in Bold text. Read messages in regular."
    #
    # IT WAS ALSO NEVER QUITE TRUE. `message_count == 1` means nobody has replied yet, which stays
    # true on a conversation he has read ten times — so the badge that looked like an unread
    # marker was measuring something else, and the row had no way to say what he had actually
    # seen. `store._UNREAD` is that fact now, and the row spends no horizontal space on it.
    #
    # WHAT IS LEFT ON THIS LINE IS CONSTRAINTS AND ATTRIBUTION — what stops him replying, and
    # what a conversation is worth. Both are things a weight cannot say.
    if k.get("ad_title"):
        # IT USED TO BE PROSE IN THE GREY LINE, where it competed with the message count for the
        # same characters and lost. Attribution is the one thing on this row that says what the
        # conversation is WORTH.
        out.append((f'From {k["ad_title"]}', "ad"))
    return out[:_TAG_LIMIT]


# ONE NAME FOR THE ONE DESTINATION. The channel row's first chip and the row menu's way out both
# land on /inbox/inbox with no channel; two spellings of the same idea is how a reader concludes
# they must do different things. It is "Every channel" and not "All", because the UNANSWERED filter
# row above it already opens with "All" and means something else entirely.


def _tags(k: dict) -> str:
    return "".join(f'<span class="tag {c}">{_esc(t)}</span>' for t, c in _tag_list(k))


def _live(*paths: str) -> str:
    """The first of `paths` THIS BOX ACTUALLY SERVES, or "" when it serves none of them.

    ASKS THE LIVE URL MAP, exactly as `core.dash.landing()` does and for the same reason it gives:
    "WHICH PAGES EXIST IS A PER-BOX FACT, NEVER A CONSTANT. The same binary ships as four
    different products, and any hard-coded landing is right for one of them and a 404 for the
    rest." A link to a route this box does not serve is the dead control this app keeps deleting,
    and on the first screen it would be a 404 handed to somebody in their first five minutes.

    IT IS A HELPER AND NOT A CONSTANT because two different callers now need it: the connect
    button on an empty inbox, and any row the morning report hands up with an `href` on it.
    """
    try:
        from flask import current_app
        have = {str(r) for r in current_app.url_map.iter_rules()}
    except Exception:                            # noqa: BLE001 — no app context, no link
        return ""
    for path in paths:
        if path in have:
            return path
    return ""


def _connect_verb(go: str) -> str:
    """WHAT THE BUTTON SAYS, decided by WHERE IT LANDS — because the two must agree.

    "Connect a channel" promises a choice. On a box that serves the hub (`/inbox/connect`) there
    is one. On a box that does not, this button reaches the MAILBOX screen, which offers exactly
    one thing — and a buyer who pressed "Connect a channel" expecting to pick Instagram and
    arrived at a Gmail form has been told something that was not true of his box.

    A LABEL AND A DESTINATION THAT DISAGREE is the same defect as a link that 404s, only quieter:
    nothing breaks, and he simply believes the product is not what he was shown.
    """
    # "SET UP YOUR INBOX", not "your box" (owner, 2026-09-29, D2 as revised): this guide covers the
    # inbox's own connections; the box's checklist is the Base Machine's set-up card.
    return {"/inbox/setup": "Set up your inbox",
            "/inbox/connect": "Connect your social accounts",
            "/inbox/mailbox": "Connect your inbox"}.get(go, "Finish setting up")


def _connect_href() -> str:
    """Where a buyer with nothing connected should be sent — or "" when this box has nowhere.

    ORDER IS "THE MOST IT CAN DO FOR HIM, ON THIS BOX". `/inbox/connect` is the hub when a box
    serves one; `/inbox/mailbox` connects the one channel that needs no vendor account at all;
    `/inbox/settings` is the last resort, and on a bare box it offers an AI key, a theme and an
    install guide — nothing that connects anything. It was where this button led for a day.
    """
    return _live("/inbox/setup", "/inbox/connect", "/inbox/mailbox", "/inbox/settings")


def _nothing_arrives_yet() -> bool:
    """Is there NO channel that could deliver a message to this box?

    THE EMPTY INBOX SAYS "The first person who messages you appears here." That is a promise, and
    on a box with nothing connected it is one the box cannot keep — nobody is coming, because
    nothing is listening. Measured on a claimed box before any connect screen existed: a buyer
    could sit in front of that sentence indefinitely and conclude the product was broken, which
    is the opposite of what the sentence is for.

    ASKED THE WAY THE POLLER DECIDES WHETHER TO SWEEP AT ALL (`poller._spaces`), so the screen and
    the worker cannot disagree about whether this box is listening. A Zernio key OR a mailbox
    credential is enough — `poller._spaces` was widened for exactly that case, "a buyer who
    connects Gmail and nothing else is a box with no Zernio key at all".
    """
    try:
        from core import box_secrets, spaces as _sp
        if box_secrets.email_credential():
            return False
        return not any(s.get("zernio_key") for s in _sp.all_spaces())
    except Exception as e:                       # noqa: BLE001 — never a 500 on an empty screen
        log.warning("voice.channels_unreadable", extra={"error": type(e).__name__})
        return False                             # say nothing rather than say something wrong


def _logos(space: str, current: str, *, q: str = "", waiting: bool = False,
           from_ad: bool = False) -> str:
    """One round logo per channel THIS BOX ACTUALLY HAS, on a white disc. Never a menu of hopes.

    ONE ROW WITH THE STATUS PILLS (owner, 2026-09-29, choosing his target composition): All ·
    Unanswered · then the logos. So there is no "Every channel" chip any more — All clears every
    filter — and tapping the chosen logo again turns it off.

    ONE CHANNEL IS NOT A CHOICE, so a box with only Messenger draws no logos at all.

    THE LOGOS CARRY THE QUERY, because a filter that silently throws away what he typed is worse
    than no filter. THE COUNT RIDES IN THE SPOKEN NAME ONLY, and not while searching: the count is
    the whole inbox's, not the matches'.

    ON A WHITE DISC IN BOTH THEMES (docs/SCOPE_MOBILE_APP_REDESIGN.md §11): the blue marks fail on
    the dark ground and the green ones on the cream, and none of them is ours to recolour.
    """
    try:
        from marketing.customer_voice.inbox import store as _store
        present = _store.platforms_present(space)
    except Exception as e:                       # noqa: BLE001 — a missing logo row is not a 500
        log.warning("voice.chips_unreadable", extra={"error": f"{type(e).__name__}: {e}"[:160]})
        return ""
    if len(present) < 2:
        return ""
    out = []
    for row in present:
        pid = str(row["platform"] or "")
        on = pid == current
        said = _channel(pid) + ("" if (q or waiting or from_ad) else f", {row['n']}")
        href = _url(q=q, channel="" if on else pid, waiting=waiting, from_ad=from_ad)
        out.append(f'<a class="chip mkchip{" on" if on else ""}" aria-pressed="{"true" if on else "false"}" '
                   f'href="{_esc(href)}"><span class="ui-disc">{_mark(pid, 20)}</span>'
                   f'<span class="vh">{_esc(said)}</span></a>')
    return "".join(out)


def _counts(space: str) -> dict:
    """How many are waiting on a person, and how many came from an ad. -1 when the box cannot say.

    -1 RATHER THAN 0, because those are opposite facts on this screen: zero is "you are caught
    up" and is worth celebrating, and a store that would not answer must say nothing at all.

    ONE CALL, because it is one query — the header and both filter pills are answered by the same
    scan rather than one apiece on every render.
    """
    try:
        from marketing.customer_voice.inbox import store as _store
        got = _store.inbox_counts(space)
        return {"waiting": int(got.get("waiting") or 0), "from_ad": int(got.get("from_ad") or 0)}
    except Exception as e:                       # noqa: BLE001 — a missing number is not a 500
        log.warning("voice.counts_unreadable", extra={"error": f"{type(e).__name__}: {e}"[:160]})
        return {"waiting": -1, "from_ad": -1}


def _drafts_ready(space: str) -> int:
    """Replies the box has written and nobody has sent yet, or -1 when the box cannot say."""
    try:
        from marketing.customer_voice.drafter import store as _drafts
        return _drafts.waiting_count(space)
    except Exception as e:                       # noqa: BLE001 — a missing number is not a 500
        log.warning("voice.drafts_unreadable", extra={"error": f"{type(e).__name__}: {e}"[:160]})
        return -1


def _head(n: int, drafts: int = -1) -> str:
    """The inbox title, carrying the only number this product exists to produce.

    THE WORD "Inbox" IS FOR A SCREEN READER ONLY since 2026-09-29: the owner chose a header that
    reads "Unified Inbox" with the summary straight under it, so a visible "Inbox" repeated it. The
    page keeps its one heading, read aloud, and the sentence under the header does the work.

    THE NUMBER IS A SENTENCE, NOT A BADGE. "3" beside the word Inbox is a notification dot, and a
    notification dot means "something happened". This number means something DIFFERENT and more
    useful: three people are waiting on you personally. Said in words it needs no legend.

    AND IT IS SAID ONCE. The filter below is the control; the header is the fact. Printing "3" in
    both places is the stutter this file removed from the set-up screen an hour ago.
    """
    if n < 0:
        return '<h1 class="vh">Inbox</h1>'
    if n == 0:
        return ('<h1 class="vh">Inbox</h1>'
                '<p class="quiet sum">Nobody is waiting on you.</p>')
    who = "1 person is" if n == 1 else f"{n} people are"
    # AND WHAT THE BOX HAS READY FOR THEM (owner, 2026-09-29, his target composition): "4 people are
    # waiting on a reply · 6 drafts ready to send". Said only when there is at least one.
    ready = ("" if drafts < 1 else
             f' · <b>{"1 draft" if drafts == 1 else f"{drafts} drafts"}</b> ready to send')
    return (f'<h1 class="vh">Inbox</h1><p class="quiet sum">'
            f'<b class="warn">{who}</b> waiting on a reply{ready}.</p>')


def _pills(counts: dict, waiting: bool, from_ad: bool, *, q: str = "", channel: str = "",
           space: str = "") -> str:
    """All / Unanswered / Leads — and each one only when it can change the screen.

    A FILTER THAT CANNOT CHANGE THE SCREEN IS NOT SHIPPED HERE, which is the same rule `_chips`
    applies to a box with one channel. With nothing waiting, Unanswered leads to an empty list a
    person did not need to visit; the header has already told them they are caught up. With no
    conversation from an ad — which is most boxes, most weeks — that pill is a promise about a
    kind of message this box has never received.

    EACH IS STILL DRAWN WHILE ITS OWN FILTER IS ON AND ITS COUNT HAS FALLEN TO ZERO — answering
    the last one must not delete the way back to All under his thumb.

    "LEADS" — OWNER, 2026-09-18, ASKED DIRECTLY AND ANSWERED IN ONE WORD. This read "From an ad"
    and this paragraph used to argue for it: everyone in the inbox is arguably a lead, while the
    thing the filter actually knows is narrower — these people clicked something he PAID for.
    That case is recorded because it is a real one, and it LOST. The decision is his and it is
    made, so the argument is history rather than a live objection sitting next to the code.
    Nothing about the filter changed: it is still `from_ad`, still the poller's `ad_meta_id`, and
    still a fact the box already had. Only the word a buyer reads is different.
    """
    show_wait = counts.get("waiting", 0) > 0 or waiting
    show_ad = counts.get("from_ad", 0) > 0 or from_ad
    logos = _logos(space, channel, q=q, waiting=waiting, from_ad=from_ad) if space else ""
    if not show_wait and not show_ad and not logos:
        return ""

    def pill(label: str, on: bool, **flip) -> str:
        # EACH PILL TOGGLES ITS OWN DIMENSION AND CARRIES THE OTHER. They are not a radio group:
        # the store composes them, and "unanswered AND from an ad" is the most valuable list in
        # the box — somebody who clicked an ad he paid for and has not been answered. A row that
        # dropped the other filter would make that list unreachable by tapping, and a pill that
        # could not turn itself off would make it a one-way door.
        #
        # `aria-pressed`, because two of these can be on at once. A radio group is what this LOOKS
        # like, and a screen reader must not be told that.
        return (f'<a class="chip{" on" if on else ""}" aria-pressed="{"true" if on else "false"}" '
                f'href="{_esc(_url(q=q, channel=channel, **flip))}">{label}</a>')

    # ALL CLEARS EVERY FILTER, the channel included, now that it leads the one row.
    out = [f'<a class="chip{" on" if not (waiting or from_ad or channel) else ""}" '
           f'aria-pressed="{"false" if (waiting or from_ad or channel) else "true"}" '
           f'href="{_esc(_url(q=q))}">All</a>']
    if show_wait:
        out.append(pill("Unanswered", waiting, waiting=not waiting, from_ad=from_ad))
    if show_ad:
        out.append(pill("Leads", from_ad, waiting=waiting, from_ad=not from_ad))
    return f'<div class="chips pills">{"".join(out)}{logos}</div>'


def _find(q: str, channel: str, waiting: bool = False, from_ad: bool = False) -> str:
    """The search field. A plain GET form, so it works before any script has run.

    THE FILTERS RIDE ALONG AS HIDDEN FIELDS. Searching inside a channel, or inside the messages
    still waiting on him, is the obvious thing to want and it is one input each; dropping either
    would quietly widen a search he had narrowed.
    """
    # THE FILTERS RIDE, THE PAGE DOES NOT. A new search is a new set of results and page one
    # is the only page it can be on — carrying the old `page` in a hidden field is how a person
    # searches for "boiler", gets a blank screen, and concludes search is broken.
    chan = (f'<input type="hidden" name="channel" value="{_esc(channel)}">' if channel else "")
    # THE WAITING FILTER RIDES ALONG, exactly as the channel does: searching inside a filter is
    # the obvious thing to want, and dropping it would quietly widen a search he had narrowed.
    chan += '<input type="hidden" name="waiting" value="1">' if waiting else ""
    chan += '<input type="hidden" name="from_ad" value="1">' if from_ad else ""
    # AND THE ACTION IS THE CURRENT URL. These commits were written before the /voice -> /inbox
    # cut, so each carried its own copy of the old path — the staleness that made their suites
    # 404 while every store-side assertion in them passed.
    return ('<form class="find" method="get" action="/inbox/inbox" role="search">'
            '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            'stroke-width="1.9" stroke-linecap="round" aria-hidden="true">'
            '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.2 4.2"/></svg>'
            # THE PLACEHOLDER FITS, AND THE FIRST ONE DID NOT. "Search everything anyone has
            # said" rendered as "Search everything anyone ha…" at 390px — the app's own copy cut
            # off inside the control that exists to invite him to type. This is the card's own
            # phrase, and the longer explanation belongs on the no-match screen, which is the
            # moment he wonders whether it read the messages or only the names.
            f'{chan}<input type="search" name="q" id="q" value="{_esc(q)}" '
            'placeholder="Search everything" autocomplete="off" '
            'autocapitalize="none" spellcheck="false" enterkeyhint="search" '
            'aria-label="Search every conversation">'
            '<button type="submit">Search</button></form>')


def _bar_find(q: str, channel: str, waiting: bool = False, from_ad: bool = False) -> str:
    """THE MESSAGES PAGE'S OWN HEADER: the search, in the bar, the way Gmail does it.

    Owner, 2026-10-05, on the messages page at 390px: "we need to figure out a better header and a way that we can
    hide that terrible looking search box that takes up so much space. You can see that Gmail save a ton of space by
    integrating the search into the upper area and then the first message is way higher on the screen. We should do
    that for just the messages page" and "Our inbox should have a different header than the rest".

    So on a mobile the bar's title gives way to one search pill beside the menu, and the card that sat under the
    summary is gone: the first conversation moves up by the card's height. Every other screen keeps its title. The
    bar is mobile-only, so a desktop keeps `_find` in the page (wrapped in `.find-wide`, which shows only there).

    The same GET form as `_find`, filters riding as hidden fields; no Search button, because the keyboard's own
    Search key submits it (`enterkeyhint`), and Gmail's pill has none either. A screen reader still gets a button.
    """
    keep = (f'<input type="hidden" name="channel" value="{_esc(channel)}">' if channel else "")
    keep += '<input type="hidden" name="waiting" value="1">' if waiting else ""
    keep += '<input type="hidden" name="from_ad" value="1">' if from_ad else ""
    return ('<form class="barfind" method="get" action="/inbox/inbox" role="search">'
            '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            'stroke-width="1.9" stroke-linecap="round" aria-hidden="true">'
            '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.2 4.2"/></svg>'
            f'{keep}<input type="search" name="q" id="qb" value="{_esc(q)}" '
            'placeholder="Search messages" autocomplete="off" '
            'autocapitalize="none" spellcheck="false" enterkeyhint="search" '
            'aria-label="Search every conversation">'
            '<button type="submit" class="vh">Search</button></form>')


PAGE = 50            # what the store is asked for, and what a page holds


def _url(*, q: str = "", channel: str = "", page: int = 1, waiting: bool = False,
         from_ad: bool = False) -> str:
    """Every link on this screen, built in ONE place.

    THE CHIPS ALREADY LOST THE QUERY ONCE. Each control here — a chip, Clear, Older, Newer —
    carries the two the reader is not changing and drops the one they are, and every one of them
    was hand-assembling its own query string. That is three places to forget `page` in, and the
    forgetting is silent: the link still works, it just quietly puts him back on page one.

    A DEFAULT IS NEVER WRITTEN INTO THE URL. `/inbox/inbox` and `/inbox/inbox?page=1` are the
    same screen, and only one of them is worth showing a person.
    """
    from urllib.parse import quote as _qt
    bits = []
    if q:
        bits.append(f'q={_qt(q, safe="")}')
    if channel:
        bits.append(f'channel={_qt(channel, safe="")}')
    if waiting:
        bits.append("waiting=1")
    if from_ad:
        bits.append("from_ad=1")
    if page > 1:
        bits.append(f"page={int(page)}")
    return "/inbox/inbox" + ("?" + "&".join(bits) if bits else "")


def _clear(channel: str, waiting: bool = False, from_ad: bool = False) -> str:
    """Back out of a search WITHOUT backing out of the channel he chose — or the page he is on.

    THE PAGE GOES. That is the point of Clear: page 4 of a search is meaningless once the search
    is gone, and landing on page 4 of everything is not what he asked for.

    THE UNANSWERED FILTER STAYS, for the same reason the channel does. He narrowed to the messages
    waiting on him and then searched inside that; clearing the search is not asking to be shown
    every conversation he has already dealt with.
    """
    return _url(channel=channel, waiting=waiting, from_ad=from_ad)


def _pager(*, q: str, channel: str, page: int, more: bool, waiting: bool = False,
           from_ad: bool = False) -> str:
    """Older and Newer — and NEITHER of them when there is nowhere to go.

    A CONTROL THAT CANNOT SUCCEED IS THE ONE THIS APP KEEPS DELETING, and a greyed-out Older on
    a box with forty conversations is exactly that. `more` is not a guess: the reader asks the
    store for ONE ROW MORE than a page holds, and the presence of that row is the whole answer.
    Counting instead would be a second query to learn something the first one already knew.
    """
    if page <= 1 and not more:
        return ""
    out = []
    if page > 1:
        out.append(f'<a class="pg prev" '
                   f'href="{_esc(_url(q=q, channel=channel, page=page - 1, waiting=waiting, from_ad=from_ad))}" '
                   f'rel="prev">&larr; Newer</a>')
    if more:
        out.append(f'<a class="pg next" '
                   f'href="{_esc(_url(q=q, channel=channel, page=page + 1, waiting=waiting, from_ad=from_ad))}" '
                   f'rel="next">Older &rarr;</a>')
    return f'<div class="pager">{"".join(out)}</div>'


def _hits(q: str, channel: str, n: int, *, page: int, more: bool) -> str:
    """Where he is, and a way out. Nothing at all when he is not searching and is on page one.

    IT STILL NEVER CLAIMS A TOTAL IT DID NOT COUNT, and now it does not have to hedge either.
    Before paging, a full page could only say "the 50 most recent" — true, but a dead end. With
    a next page the honest sentence is a RANGE, which says exactly what is on the screen and
    implies nothing about what is past it:

      one page   →  "3 conversations matching leak"          (a real count; the page IS the set)
      more pages →  "Conversations 51-100 matching leak"     (a position, never a total)

    A RANGE IS ALSO THE ONLY THING THAT ANSWERS "where am I". Two identical-looking pages of
    fifty rows with no marker between them is how a person loses their place and starts again.
    """
    if not q and page <= 1:
        return ""
    lo = (page - 1) * PAGE + 1
    hi = lo + max(n, 1) - 1
    where = f" on {_channel(channel)}" if channel else ""
    if q:
        what = "conversation" if n == 1 else "conversations"
        said = (f"Conversations {lo}-{hi} matching" if (more or page > 1)
                else f"{n} {what} matching")
        body = f'<span>{_esc(said)} <b>{_esc(q)}</b>{_esc(where)}</span>'
        out = f'<a href="{_esc(_clear(channel))}">Show everything</a>'
    else:
        body = f'<span>Conversations {lo}-{hi}{_esc(where)}</span>'
        out = f'<a href="{_esc(_url(channel=channel))}">Back to the top</a>'
    return f'<div class="found">{body}{out}</div>'


def _stopped_note() -> str:
    """One line, on the page a buyer lives on, when they have stopped the box.

    THE GAP THIS CLOSES IS ONE WE MADE TODAY (#1341). `Stop everything` moved into core and onto
    /dashboard, which says plainly that the box is stopped. The INBOX said nothing at all — and
    the inbox is where a buyer actually is. New messages simply stop arriving, the screen looks
    completely normal, and the conclusion available to them is "this thing is broken". Measured on
    an exported customer_voice box: dashboard says stopped, inbox/thread/settings say nothing.

    IT NAMES WHAT STILL WORKS, because half a fact here is worse than none. `core/pause` is read
    by `core/worker` and by nothing else — so the MACHINES stop and a person can still answer by
    hand. Telling someone their box is stopped without that would have them thinking a reply they
    typed went nowhere.
    """
    try:
        from core import pause
        if not pause.is_paused():
            return ""
    except Exception:                    # noqa: BLE001 — a note is never worth a 500
        return ""
    return ('<div class="quiet" style="margin-bottom:12px">Your box is stopped, so no new '
            'messages are arriving. You can still reply to the ones here. '
            '<a href="/settings/access#stop" style="color:var(--href)">Start it again</a>.</div>')


# ── THE ASK: the one thing that was never wired, and without it none of push exists ─────────────
# MEASURED 2026-09-22, AFTER THE OWNER SAID "it still hasn't done what you're saying it will do":
# `ownboxEnableNotifications` is DEFINED and called by nothing, anywhere in the product. Not on a
# screen, not on a timer, not behind a button. So `pushManager.subscribe` never ran, no endpoint
# was ever stored, `push.subscriptions_for()` returns empty on every box we have sold, and every
# notification the poller tries to send is a loop over zero rows.
#
# EVERYTHING ELSE WAS FINISHED. A VAPID identity, RFC 8291 encryption written against the RFCs, a
# service worker, subscription storage, `notify._ring` wired into the poller's pass. A complete
# channel with no front door — which is exactly the shape of the connector bug #1409 fixed, and of
# `/deploy` before #1414: built, correct, and unreachable.
#
# WHEN IT ASKS IS THE OWNER'S RULING, 2026-09-20: "after the buyer has seen their first real
# message, never on first load." So this renders ONLY on an inbox that already has conversations
# in it. An iOS denial can only be undone in Settings, which nobody does, so a prompt fired at an
# empty box spends the whole channel on somebody with nothing to be notified about.
#
# THREE STATES, AND ONLY ONE OF THEM ASKS:
#   default  — offer the button. Pressing it is what fires the prompt; the page never does.
#   granted  — say nothing and re-subscribe quietly. That is not a prompt, it is honouring a
#              decision already made — and it repairs the case `core.push.CLIENT_JS` warns about,
#              where iOS drops a subscription after long disuse and the box keeps pushing at a
#              dead endpoint forever.
#   denied   — say nothing at all. The browser will not ask again and a button that cannot work
#              is the dead control this app keeps deleting.
_NOTIFY_OFFER = ('<div class="card" id="ownbox-notify" hidden>'
               '<div class="setrow"><b>Get these on your mobile</b>'
               '<span>This box can tell you the moment a customer writes, instead of you '
               'checking. You can turn it off again whenever you like.</span></div>'
               '<p style="margin:10px 0 0">'
               '<button class="btn" type="button" id="ownbox-notify-yes">Turn on notifications'
               '</button> '
               '<button type="button" id="ownbox-notify-no" '
               'style="background:none;border:0;color:var(--dim);font:inherit;cursor:pointer">'
               'Not now</button></p>'
               '<p class="quiet" id="ownbox-notify-said" style="margin:8px 0 0"></p></div>')

def _push_client_js() -> str:
    """ONE COPY OF THE CLIENT, loaded by both screens that need it.

    It lives in `core.push` because the two endpoints it fetches are core's. Loaded through a
    function rather than imported at module scope so a box whose release predates it still serves
    every other screen: an inbox that 500s because push is missing is a worse box than one that
    cannot receive a push.
    """
    try:
        from core import push
        return push.CLIENT_JS
    except Exception:                                    # noqa: BLE001 — a screen outranks a feature
        return ""


_NOTIFY_JS = """(function () {
  var box = document.getElementById('ownbox-notify');
  if (!box || !window.ownboxCanNotify || !window.ownboxEnableNotifications) { return; }
  // ON IPHONE ONLY THE INSTALLED APP CAN RECEIVE A PUSH. Offering this in a Safari tab spends
  // the one answer a person ever gets on a surface that could not have delivered anyway.
  if (!window.ownboxCanNotify()) { return; }
  var perm = (window.Notification && Notification.permission) || 'default';
  if (perm === 'denied') { return; }
  if (perm === 'granted') {
    // ALREADY SAID YES. Re-subscribe quietly rather than ask again: this fires no prompt, and it
    // repairs a subscription iOS dropped while the box went on believing it could reach them.
    window.ownboxEnableNotifications();
    return;
  }
  // AND NOT AGAIN TODAY IF THEY SAID "not now". Per viewer, per browser; it is a convenience and
  // the page renders correctly when it cannot be read.
  try { if (localStorage.getItem('ownbox.notify.hidden')) { return; } } catch (e) {}
  box.hidden = false;
  var said = document.getElementById('ownbox-notify-said');
  document.getElementById('ownbox-notify-no').addEventListener('click', function () {
    box.hidden = true;
    try { localStorage.setItem('ownbox.notify.hidden', '1'); } catch (e) {}
  });
  document.getElementById('ownbox-notify-yes').addEventListener('click', function (ev) {
    ev.target.disabled = true;
    said.textContent = 'Asking your device…';
    window.ownboxEnableNotifications().then(function (r) {
      if (r && r.ok) {
        said.textContent = 'Done. This device will be notified when something arrives.';
        document.getElementById('ownbox-notify-yes').style.display = 'none';
        document.getElementById('ownbox-notify-no').textContent = 'Close';
      } else {
        // THE REASON, NOT "something went wrong". Every branch of the client returns one a person
        // can act on, and the commonest is "you said no", which nothing here can undo.
        said.textContent = 'Not turned on — ' + ((r && r.why) || 'your device declined') + '.';
        ev.target.disabled = false;
      }
    });
  });
})();"""


@blueprint.get("/inbox/inbox")
def r_inbox():
    """Who has spoken to this business, most recent first."""
    space = _space()
    # THE CHIP THE READER IS ON. Passed to the store as a bound predicate, never interpolated;
    # an unknown value simply matches no rows, which is the honest answer to a hand-typed URL.
    channel = (request.args.get("channel") or "").strip().lower()[:40]
    # WHAT HE TYPED. Clamped, then handed to the store as a bound parameter like everything else
    # — `search_conversations` escapes LIKE's own wildcards before binding, so a query of `%`
    # asks for a percent sign rather than for every conversation in the box.
    q = (request.args.get("q") or "").strip()[:120]
    # THE ONE FILTER THAT IS A VERB. Anything other than the exact strings this screen's own links
    # produce is off — a filter reached by a hand-typed URL should behave like no filter, not like
    # a third state nobody designed.
    waiting = (request.args.get("waiting") or "") in ("1", "true", "yes", "on")
    from_ad = (request.args.get("from_ad") or "") in ("1", "true", "yes", "on")
    # WHICH PAGE. Anything that is not a page number is page one — a hand-typed `page=banana`
    # or `page=-3` is a person who has not asked for anything in particular, and the answer to
    # that is the top of the list, not a 500 and not a negative OFFSET.
    try:
        page = max(1, int(request.args.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        from marketing.customer_voice.inbox import store as _store
        # ONE ROW MORE THAN A PAGE HOLDS, and that extra row is the entire pagination design.
        # Its presence says "there is a next page" without a second query, and a COUNT(*) here
        # would be a whole extra scan of the messages table to learn something this result set
        # already knows. The row is dropped before anything renders it.
        ask = PAGE + 1
        off = (page - 1) * PAGE
        if q:
            convs = _store.search_conversations(space, q, limit=ask, offset=off,
                                                platform=channel or None, waiting=waiting,
                                                from_ad=from_ad)
        else:
            convs = _store.list_conversations(space, limit=ask, offset=off,
                                              platform=channel or None, waiting=waiting,
                                              from_ad=from_ad)
        more = len(convs) > PAGE
        convs = convs[:PAGE]
    except Exception as e:                       # noqa: BLE001 — a page, never a stack trace
        log.warning("voice.inbox_unreadable", extra={"error": f"{type(e).__name__}: {e}"[:160]})
        return _shell('<h1 class="vh">Inbox</h1><div class="quiet">The inbox could not be read on this box. '
                      'Nothing has been lost.</div>'), 200

    if not convs:
        # QUIET IS NOT BROKEN, and on this screen the difference matters more than anywhere else:
        # a business with no messages yet is the normal first week, not a fault.
        #
        # TWO DIFFERENT EMPTIES, and telling them apart is the whole job of this branch. "Nothing
        # has ever arrived" and "nothing has arrived ON INSTAGRAM" look identical and mean
        # opposite things — the second one still has a working inbox one tap away, and a person
        # shown the first message would reasonably conclude the box is broken.
        #
        # AND A THIRD EMPTY ARRIVED WITH SEARCH, which is the one most easily mistaken for a
        # broken box: "nothing matches `boiler`" is not "you have no messages", and showing the
        # first-week welcome to a person who has 200 conversations and mistyped a word would be
        # the worst reading of all three.
        #
        # AND A FOURTH ARRIVED WITH PAGING, which is the one a person reaches by ACCIDENT — a
        # stale bookmark, a back button, a hand-typed number — and it is the empty most likely
        # to be read as data loss. "You have run off the end of the list" and "you have no
        # messages" are opposite facts, and this box may hold two hundred conversations.
        #
        # AND A FIFTH ARRIVED WITH THE UNANSWERED FILTER, and it is the only empty on this screen
        # that is GOOD NEWS. An inbox with nothing left waiting is the state this product is for,
        # and rendering the neutral "no conversations" over it would be the one moment the app had
        # something to congratulate somebody for and said nothing.
        # THE FILTER ROW IS DRAWN ON THE EMPTY SCREENS TOO. It is how a person gets back out of
        # the filter that emptied the screen, and a control that disappears exactly when it is
        # needed is the same bug as one that was never there.
        _n = _counts(space)
        top = (_head(_n["waiting"]) + f'<div class="find-wide">{_find(q, channel, waiting, from_ad)}</div>'
               + _pills(_n, waiting, from_ad, q=q, channel=channel, space=space))
        rail = ""
        if page > 1:
            body = (f'{top}{rail}'
                    f'<div class="quiet">There is no page {page}'
                    + (f' of conversations matching <b>{_esc(q)}</b>' if q else "")
                    + '. Nothing has been lost — the list simply ends before here. '
                    f'<a href="{_esc(_url(q=q, channel=channel, waiting=waiting, from_ad=from_ad))}" '
                    'style="color:var(--href)">Back to the top</a></div>')
        elif q:
            body = (f'{top}{rail}'
                    f'<div class="quiet">Nothing matches <b>{_esc(q)}</b>'
                    + (' among the messages waiting on you' if waiting else "")
                    + (' among the conversations that came from an ad' if from_ad else "")
                    + (f' on {_esc(_channel(channel))}' if channel else "")
                    + '. This searches what people wrote, not just their names. '
                    f'<a href="{_esc(_clear(channel, waiting, from_ad))}" '
                    'style="color:var(--href)">Show everything</a></div>')
        elif waiting:
            body = (f'{top}{rail}'
                    '<div class="quiet">You have answered everyone'
                    + (f' on {_esc(_channel(channel))}' if channel else "")
                    + '. Nothing here is waiting on you. '
                    f'<a href="{_esc(_url(channel=channel))}" style="color:var(--href)">'
                    'Show every conversation</a></div>')
        elif from_ad:
            # NOT "you have no leads". Nobody has clicked an ad INTO this box, which is a fact
            # about advertising and not about the inbox — and a box whose owner runs no ads is
            # the normal case, not a fault to report.
            body = (f'{top}{rail}'
                    '<div class="quiet">No conversation here started from one of your ads'
                    + (f' on {_esc(_channel(channel))}' if channel else "")
                    + '. When somebody messages you by tapping an ad, they land here with the '
                    'ad they came from on the row. '
                    f'<a href="{_esc(_url(channel=channel))}" style="color:var(--href)">'
                    'Show every conversation</a></div>')
        elif channel:
            body = (f'{top}{rail}'
                    f'<div class="quiet">Nothing on {_esc(_channel(channel))} yet. '
                    f'Other channels may have messages — tap <b>All</b>.</div>')
        elif _nothing_arrives_yet():
            # NOTHING IS LISTENING. "The first person who messages you appears here" is a promise,
            # and here it is one the box cannot keep: nobody is coming. This is the only empty
            # state with something for him to DO, so it is the only one carrying a button — and
            # the button appears only on a box that serves somewhere to send him.
            go = _connect_href()
            body = ('<h1 class="vh">Inbox</h1><div class="quiet">Nothing can reach you yet, because '
                    'nothing is connected. Connect your inbox and everything people send you '
                    'lands here.</div>'
                    # THE TITLE SAYS WHAT HE GETS, THE BUTTON SAYS WHAT HE DOES. Both read
                    # "Connect a channel" until this was rendered — the same three words twice
                    # inside one small card, which reads as a template that was never finished.
                    + (f'<div class="card"><div class="setrow">'
                       '<b>Your inbox fills itself after this</b>'
                       '<span>It takes about a minute, and it is the only step nobody can do '
                       'for you.</span><p style="margin:10px 0 0">'
                       f'<a class="btn" href="{go}">{_connect_verb(go)}</a></p></div></div>'
                       if go else ""))
        else:
            # NO SEARCH BOX ON A BOX THAT HAS NEVER RECEIVED ANYTHING. A field offering to search
            # an empty inbox is the control that cannot succeed this app keeps deleting.
            body = ('<h1 class="vh">Inbox</h1><div class="quiet">No conversations yet. '
                    'The first person who messages you appears here, and you will get a '
                    'notification once this is installed as an app on your mobile.</div>')
        # WIDE ON THE EMPTIES TOO. Every branch above still draws the chip row, so a reader who
        # filtered to Instagram and found nothing must keep the rail that got them there —
        # otherwise the layout moves under them at the exact moment they need to change filter.
        # THE BAR SEARCHES WHERE THE PAGE WOULD HAVE: only the branches above that drew `top`.
        return _shell(_stopped_note() + body + _panels(), wide=True,
                      bar=_bar_find(q, channel, waiting, from_ad) if body.startswith(top) else ""), 200

    # WHICH ROWS HAVE A REPLY READY — one query for the page, not one per row.
    try:
        from marketing.customer_voice.drafter import store as _drafts
        drafted = {str(d.get("zcid")) for d in _drafts.waiting(space, limit=500)}
    except Exception:                            # noqa: BLE001 — a missing chip is not a 500
        drafted = set()
    rows = []
    for k in convs:
        who = (k.get("participant") or "").strip() or "Someone"
        when = _ago(k.get("last_inbound_at"))
        # THE FLAGS LEFT THIS SENTENCE AND BECAME TAGS. "opted out" and "from <ad>" used to be
        # prose here, third and fourth in a line that truncates — so the two facts most worth
        # seeing were the two most likely to be cut off. They are pills now, and pills do not
        # truncate; what is left is the one thing a sentence says better than a badge.
        # THE ROW SAID "1 message" AND THE MESSAGE WAS THE ONE THING IT WOULD NOT SHOW, so every
        # row had to be opened to find out whether it mattered. `preview` is the newest message,
        # trimmed in SQL, selected in the same pass as the count.
        #
        # "You:" WHEN WE SPOKE LAST, which is how every inbox a person already uses reads, and it
        # answers the question the list is for — whether the ball is in their court. `awaiting_reply`
        # is the same subquery ordered the same way, so the prefix and the tag can never disagree.
        # Guarded on there BEING a message: a conversation the poller knows about but has never
        # heard a word on is not one we replied to.
        preview = " ".join(str(k.get("preview") or "").split())
        mine = preview and not k.get("awaiting_reply")
        # AND THE ROW NO LONGER COUNTS THEM. It said "N messages" on a line of its own, which
        # is the line the message now runs onto — owner, 2026-09-17, that label was "totally in
        # the way". Nobody opens an inbox to learn a conversation has four messages in it; they
        # open it to read the newest one. `message_count` is still read, once, by `_tag_list`,
        # where exactly-one earns the New tag — a fact, not a tally.
        tags = _tags(k)
        if str(k.get("zernio_conversation_id")) in drafted:
            tags += '<span class="tag draft">Draft waiting</span>'
        # UNREAD IS A CLASS ON THE ROW, not a badge inside it. One word of markup, and it styles
        # the name, the clock and the message together — which is what makes the row read as one
        # object in two states rather than three elements that happen to agree.
        unread = bool(k.get("unread"))
        # KINSO'S ROW, EXACTLY: avatar, name with the time beside it, one grey line under it, and
        # the channel's own logo far right. The mark is a SHAPE before it is a colour, so it still
        # separates in greyscale — and the channel's word is one tap away in the thread header, so
        # nothing here is carried by colour alone.
        plat = k.get("platform")
        rows.append('<div class="convrow">'
                    f'<a class="conv{" unread" if unread else ""}" '
                    f'href="{_esc(_thread_href(k.get("zernio_conversation_id")))}">'
                    f'<span class="av" aria-hidden="true">{_monogram(who)}</span>'
                    # THE WORD, FOR A READER THAT CANNOT SEE THE WEIGHT. First thing inside the
                    # link, so it is announced before the name rather than after the timestamp.
                    + ('<span class="vh">Unread.</span>' if unread else "")
                    + f'<span class="w"><b>{_esc(who)}</b><span class="t">{_esc(when)}</span></span>'
                    + (f'<span class="p">{"<i>You:</i> " if mine else ""}{_esc(preview)}</span>'
                       if preview else '<span class="p"><i>No message yet</i></span>')
                    # NO EMPTY GREY LINE. With the count gone this span can have nothing in it
                    # at all — an ordinary conversation inside its window wears no tag — and an
                    # empty flex row still spends the grid's row gap. Drawn only when it carries
                    # something, so a calm row is genuinely shorter than a busy one.
                    + (f'<span class="s">{tags}</span>' if tags else "")
                    + f'<span class="mk">{_mark(plat)}'
                    f'<span class="vh">{_esc(_channel(plat))}</span></span></a></div>')
    # UNION, AND BOTH SIDES SHIP SOMETHING. This branch replaced the bare `<h1>Inbox</h1>` with
    # the header sentence and the filter pills; main (#1357) put `_stopped_note()` above
    # everything here, so a buyer whose box is stopped is told BEFORE he reads a list that
    # cannot be growing. The note is about the BOX and the header is about the LIST, so taking
    # either side alone silently deletes a shipped feature.
    counts = _counts(space)
    return _shell(_stopped_note()
                  + f'{_head(counts["waiting"], _drafts_ready(space))}'
                  f'<div class="find-wide">{_find(q, channel, waiting, from_ad)}</div>'
                  f'{_pills(counts, waiting, from_ad, q=q, channel=channel, space=space)}'
                  f'{_hits(q, channel, len(convs), page=page, more=more)}'
                  f'<div class="card">{"".join(rows)}</div>'
                  f'{_pager(q=q, channel=channel, page=page, more=more, waiting=waiting, from_ad=from_ad)}'
                  + _panels()
                  # THIS BRANCH IS THE ONE WITH CONVERSATIONS IN IT, which is the whole condition
                  # the owner set. The four empty states above render none of this.
                  + _NOTIFY_OFFER
                  + f'<script>{_push_client_js()}</script><script>{_NOTIFY_JS}</script>',
                  wide=True, bar=_bar_find(q, channel, waiting, from_ad)), 200


def _panels() -> str:
    """THE INBOX'S SLOT FOR OTHER MACHINES' SECTIONS (`m.panel("inbox", ...)`, core/panels.py).

    Owner, 2026-10-01: "the main way to control it will be within the unified inbox." A machine that runs
    conversations here (the owner's Lead Magnet Machine first) adds its controls as a card in this slot, and
    this screen never names it. BELOW THE CONVERSATIONS, because on a mobile screen the conversations are the
    screen: a control panel above them would push every person waiting on him down past the fold.

    Rendered inside this signed-in route, so a panel is shown to exactly who the Inbox is shown to. A panel
    that raises is a quiet card (core/panels.py); a registry that can't be read is no panels at all."""
    try:
        from core import panels
        return panels.render("inbox")
    except Exception as e:                       # noqa: BLE001 — the Inbox must render without them
        log.warning("voice.panels_unreadable", extra={"error": f"{type(e).__name__}"[:80]})
        return ""


@blueprint.get("/inbox/inbox/<path:zcid>")
def r_thread(zcid):
    """One conversation, oldest first — the way a person reads a thread.

    THE ID ARRIVES FROM A URL, so the Space is applied as a second predicate in the query rather
    than trusted to the vendor's uniqueness (see `store.messages_for`). A 404 rather than a 403 on
    a conversation this Space does not own: a boundary that announces what exists on the other side
    of it is not a boundary.
    """
    space = _space()
    try:
        from marketing.customer_voice.inbox import store as _store
        conv = _store.get_conversation(space, zcid)
        msgs = _store.messages_for(space, zcid) if conv else []
    except Exception as e:                       # noqa: BLE001
        log.warning("voice.thread_unreadable", extra={"error": f"{type(e).__name__}: {e}"[:160]})
        return _shell('<h1>Conversation</h1><div class="quiet">This conversation could not be '
                      'read on this box.</div>'), 200
    if not conv:
        return ("", 404)

    # HE HAS LOOKED AT IT, SO IT STOPS SHOUTING. The act of opening the thread is what clears
    # the bold on the list — the same contract as every inbox, and the reason no "mark as read"
    # control has to exist on a phone screen that has no room for one.
    #
    # NEVER FAILS THE READ. A stamp that cannot be written is a row that stays bold, which is a
    # cosmetic wrong; refusing to show him the conversation over it would be a real one.
    # `_store`, NOT `store`. This module imports the store under a local alias inside each
    # route's try block and has no module-level name for it — so `store.mark_read` here raises
    # NameError INSIDE the except below, logs a warning, and marks nothing read for ever. A bug
    # that passes every screen test because the screen still renders.
    try:
        _store.mark_read(space, zcid)
    except Exception as e:                       # noqa: BLE001 — see above
        log.warning("voice.mark_read_failed", extra={"error": f"{type(e).__name__}: {e}"[:160]})

    who = (conv.get("participant") or "").strip() or "Someone"
    # THE CHANNEL, IN THE HEADER. Answering an Instagram DM as though it were an email is a
    # category of mistake this screen should make impossible, and the only way it does that is by
    # saying which channel this is before he starts typing.
    _p = conv.get("platform")
    head = [f'<h1>{_esc(who)} <span class="chan">{_esc(_channel(_p))}{_mark(_p, 15)}</span></h1>']
    # THE ADDRESS, AND THEN THE REST BEHIND A CARET. Taken from the newest message this thread
    # has headers for — which is the newest one polled since the box started keeping them, and
    # nothing at all on a thread that has not been polled since. Both render: the address line
    # and the caret each disappear rather than printing an empty row.
    # ONE LOOKUP FOR THE WHOLE THREAD, not one per bubble. Asked for here rather than joined
    # into `messages_for`, because this is the only view that renders a sender's markup and an
    # HTML part is tens of kilobytes — see `store.html_for`.
    try:
        _html_by_id = _store.html_for(space, zcid)
    except Exception as e:                       # noqa: BLE001 — a thread must still open
        log.warning("voice.html_for_failed", extra={"error": f"{type(e).__name__}"[:80]})
        _html_by_id = {}
    _hdrs = next((m.get("headers") or {} for m in reversed(msgs) if m.get("headers")), {})
    _addr = _addr_of(_hdrs)
    if _addr and _addr.lower() != who.lower():
        head.append(f'<div class="addr">{_esc(_addr)}</div>')
    head.append(_details(_hdrs))
    if conv.get("opted_out"):
        head.append('<div class="card needs"><div class="row"><span class="t">'
                    'This person has opted out. Nothing is sent to them.</span></div></div>')
    if conv.get("ad_title"):
        head.append(f'<div class="card"><div class="row"><span class="t">Came from '
                    f'<b>{_esc(conv["ad_title"])}</b></span></div></div>')
    # WHO IS HANDLING IT, AND WHY IT CAME BACK (customer_voice/claims.py). While an automation holds the
    # conversation it says so, so he knows the box is answering and he needn't. When it hands one back, its
    # note says what it couldn't answer, above the thread he's about to reply in.
    try:
        from marketing.customer_voice import claims as _claims
        _held = _claims.holder(space, zcid)
        _back = None if _held else _claims.handed_back(space, zcid)
    except Exception as e:                       # noqa: BLE001 — a thread must still open
        log.warning("voice.claim_unreadable", extra={"error": f"{type(e).__name__}"[:80]})
        _held = _back = None
    if _held:
        head.append(f'<div class="card"><div class="row"><span class="t"><b>{_esc(_held["title"])}</b> is '
                    'handling this conversation. Anything it can\'t answer comes back here.</span></div></div>')
    elif _back:
        head.append(f'<div class="card needs"><div class="row"><span class="t">Handed back by '
                    f'<b>{_esc(_back["title"])}</b>: {_esc(_back["note"])}</span></div></div>')

    if not msgs:
        head.append('<div class="quiet">No messages have been mirrored for this conversation '
                    'yet.</div>')
        return _shell("".join(head)), 200

    # WHO SAID IT, ON EVERY LINE. `sent_by` is contact | ai | human, and on a screen where the
    # machine may have answered on his behalf, not saying which is which is the one thing that
    # would make him distrust the whole surface.
    # AND THE CONTACT IS CALLED BY THEIR NAME. This line read "them" under every message the
    # customer sent, on a screen whose header is that customer's name. The file already argues
    # the case for the other side — "once more than one person can answer, 'you' is wrong for
    # everyone except whoever typed it" — and then only half-applied it: we take trouble to name
    # OUR human and call theirs a direction. A first name is what a person would say out loud.
    #
    # FIRST NAME ONLY, because the surname is already in the header two lines up and a thread of
    # "Len Okafor · 20:02" repeated down the page is the header stuttering. "Someone" is the
    # fallback `who` already uses, so an unnamed contact still reads as a person.
    first = who.split()[0] if who and who != "Someone" else "Them"
    said = {"contact": first, "ai": "the machine", "human": "you"}
    bubbles = []
    last_stamp = ""
    for m in msgs:
        inbound = str(m.get("direction") or "") == "in"
        by = said.get(str(m.get("sent_by") or ""), first if inbound else "you")
        # WHICH HUMAN. "Sent by Maria" is the entire point of having employees on the box: once
        # more than one person can answer, "you" is wrong for everyone except whoever typed it.
        # The name comes from the ledger row, so it stays right after that person is revoked —
        # `active = 0` never deletes the user, precisely so history keeps resolving to a name.
        if str(m.get("sent_by") or "") == "human":
            nm = (m.get("sender_name") or "").strip() or (m.get("sender_email") or "").strip()
            # NULL user_id is the owner (a legacy session, and every session on a live box the
            # day this ships), so an unnamed human send reads "you" exactly as it did before.
            if nm:
                by = nm
        # THE CLOCK IS PRINTED WHEN IT CHANGES, NOT ON EVERY LINE. Four messages exchanged inside
        # one minute rendered the same "Wed 16 Sep, 20:02" four times — the reader learns nothing
        # from the repeats and the thread turns into a column of dates. A run at the same stamp
        # keeps it once, on the first of the run, which is also where a person looks for it.
        # WHEN THEY SENT IT, where the channel said; when the box stored it otherwise.
        stamp = _when(m.get("sent_at") or m.get("created_at"))
        show = stamp != last_stamp
        last_stamp = stamp
        # AN EMAIL-LENGTH MESSAGE GETS THE FULL COLUMN. Measured off the owner's own morning
        # review: at 82% and 390px it wrapped into a 44-character ragged column forty lines
        # deep. The threshold is on the text, not the channel — a one-line email still reads
        # as a chat bubble, which is the shape he asked to keep.
        _txt = str(m.get("full_text") or m.get("body") or "")
        _long = " long" if len(_txt) > 400 or _txt.count("\n") > 6 else ""
        # THE SENDER'S OWN HTML WHERE THERE IS ANY, AND THE TEXT WHERE THERE IS NOT. This is the
        # whole point of having kept it: a message written in HTML has been showing the buyer the
        # fallback its mailer generated. A framed message takes the column, because an email is
        # not a chat line — and the frame is never the whole story, so the text stays available
        # under it rather than being replaced by a box the reader cannot search.
        _frame = _mail_frame(_html_by_id.get(str(m.get("id")), ""), who)
        _long = " long" if (_frame or _long) else ""
        bubbles.append(
            f'<div class="msg {"in" if inbound else "out"}{_long}">'
            # TWO SHAPES, AND WHICH ONE IS DECIDED BY WHAT ARRIVED. A message written in HTML
            # gets the sender's own document in a sandboxed frame, with the plain text folded
            # under it — kept, not replaced, because a frame is not searchable, not selectable
            # in one sweep, and not what somebody wants when they are copying an address out of
            # a message. Everything else gets the text it always got.
            #
            # `readable`, NOT `_esc`. It escapes FIRST and then makes the message legible: runs
            # of blank lines collapsed, `<https://…>` turned into a real link. `.msg .b` is
            # `white-space:pre-wrap`, so every throwaway blank line in a mailer's fallback was
            # dead space on the buyer's screen. It emits no sender markup and cannot: the only
            # tags it produces are its own, built from already-escaped characters.
            #
            # `full_text` FIRST, `body` ONLY AS THE FALLBACK. `inbox_messages.body` is a
            # 2000-character PREVIEW — right for the list, and it was the only copy kept, so
            # long mail was cut off mid-sentence here with nothing to say it had been.
            + (f'<div class="b mail">{_frame}'
               '<details class="orig"><summary>Plain text</summary>'
               f'<div class="ot">{_readable(_txt)}</div></details></div>'
               if _frame else f'<div class="b">{_readable(_txt)}</div>')
            + f'<div class="m">{_esc(by)}'
            + (f' · {_esc(stamp)}' if show else "") + '</div></div>')
    back = '<div class="foot"><a href="/inbox/inbox">← Inbox</a></div>'
    return _shell("".join(head) + f'<div class="thread">{"".join(bubbles)}</div>'
                  + _compose(zcid, conv, msgs) + back), 200


# HOW EACH WINDOW STATE LOOKS. A table, not a branch — and keyed on `state`, which OSDev4 added
# to `explain()` precisely so a screen can style a row "without matching on English or reading
# `_RULES` — the second reader of that table this module refuses to have". A state this screen has
# not met yet falls back to the calm tone rather than to no styling at all.
_WINDOW_TONE = {
    "open":    "good",
    "tagged":  "warn",
    "limited": "warn",
    "closed":  "warn",
    "no_lane": "dim",
    "unknown": "dim",
}


def _window_note(conv: dict, msgs: list[dict]) -> str:
    """The send window, in the buyer's words, above the box he is about to type in.

    SILENT WHEN THE WINDOW IS SIMPLY OPEN. "You can reply now" over a reply box is the screen
    narrating itself; the box being there already says it. The sentence earns its space only when
    the answer is something other than yes — which is also the only time a person can act on it.

    A FAILURE HERE COSTS THE NOTE, NEVER THE BOX. `explain()` is documented never to raise, but
    this screen is the one place where being wrong about that would take away a working reply box
    on a live conversation, so it is wrapped anyway.

    IT DOES NOT ASK `explain()` A QUESTION IT HAS NO INPUT FOR. `explain()` reads one column, and
    handed a NULL one it answers, correctly for what it was given, "nothing from them has reached
    the box yet". On a thread that IS printing their messages that sentence is false, and moving
    the reply box back without this branch simply relocates the false sentence one line lower —
    which is exactly what rendering showed on the first pass of this fix.

    AND IT DOES NOT DATE THE MESSAGES INSTEAD, which was the tempting version of this. A message's
    `created_at` is `state._now()` AT MIRROR TIME, not when the customer hit send: a backfill
    stamps three-week-old messages with today. Feeding that to `explain()` would print "you can
    reply now" over a window that shut a fortnight ago — a confident lie in the direction of
    encouraging a send, which is worse than the one it replaces. So we say the true thing, which
    is that we do not know the hour, and leave the decision where the note always leaves it.
    """
    if not str(conv.get("last_inbound_at") or "").strip() \
            and any(str(m.get("direction") or "") == "in" for m in msgs):
        return ('<div class="card win unknown"><div class="row"><span class="t">'
                '<b class="dim">The box cannot tell when they last wrote</b>'
                '<span class="sub" style="display:block;margin-top:3px">Their messages are here, '
                'but not the hour they arrived, so Ownbox cannot work out whether '
                + _esc(_channel(str(conv.get("platform") or ""))) + ' still counts this as a live '
                'conversation. Send it — the channel decides, and it will say so either '
                'way.</span></span></div></div>')
    try:
        from marketing.customer_voice.inbox import window as _w
        got = _w.explain(str(conv.get("platform") or ""), conv.get("last_inbound_at"))
    except Exception as e:                       # noqa: BLE001 — no note is not a broken screen
        log.info("voice.window_unreadable", extra={"error": type(e).__name__})
        return ""
    state = str(got.get("state") or "")
    if state == "open":
        return ""
    head = str(got.get("headline") or "").strip()
    if not head:
        return ""
    tone = _WINDOW_TONE.get(state, "dim")
    detail = str(got.get("detail") or "").strip()
    return (f'<div class="card win {_esc(state)}"><div class="row"><span class="t">'
            f'<b class="{_esc(tone)}">{_esc(head)}</b>'
            + (f'<span class="sub" style="display:block;margin-top:3px">{_esc(detail)}</span>'
               if detail else "")
            + '</span></div></div>')


def _compose(zcid: str, conv: dict, msgs: list[dict]) -> str:
    """The reply box. Absent — not disabled — when this conversation cannot be replied to.

    A CONTROL THAT CANNOT SUCCEED IS WORSE THAN NO CONTROL, which this codebase keeps deleting
    by name. Two cases where there is nothing to render:

    · OPTED OUT. The send refuses it too (`reply.send_reply` checks the row, not this screen) —
      but a person should never be given a box to type into when the words can never leave. The
      banner above already says why.
    · NO `account_id` ON THE ROW. Every conversation polled since account_id began being stored
      has one; a thread mirrored before that does not, and the gateway cannot send without it.
      Saying so is honest; a box that silently 500s on submit is not.
    """
    if conv.get("opted_out"):
        return ""
    if _no_send_lane(str(conv.get("platform") or "")):
        # A WRITTEN RULE THAT SAYS THE SEND HAPPENS ELSEWHERE. Checked before the no-rule branch
        # below because it is the more specific fact: both hide the compose box, and only this one
        # can tell the person where their reply actually goes.
        #
        # UNREACHABLE TODAY, AND THE SENTENCE BELOW IS WHY THAT MATTERS. Email was the only
        # channel with this flag, and this is the copy the owner read back at us on 2026-09-22 —
        # "is this true??", then "if we can't auto draft emails and then actually go ahead and
        # send them, this is a completely worthless app". It is no longer true of email: the box
        # sends it. The branch survives for the next channel that genuinely has no lane, and the
        # WORDING is OSDev5's to write when there is one, rather than this retired paragraph.
        return ('<div class="card"><div class="row"><span class="t quiet">Ownbox reads your '
                + _esc(_channel(str(conv.get("platform") or ""))) + ' and writes the reply, but '
                'the send happens outside the box for this channel.</span></div></div>')
    if not _has_send_rule(str(conv.get("platform") or "")):
        # FOUND BY RENDERING, 2026-09-16. A seeded box on a channel with NO send rule written —
        # `email`, today — drew a full reply box with a Send button under it, and the send would
        # have been refused by `window.decide` after he had typed. That branch is the one
        # window.py calls the most important in the file: adding a platform string to the poller
        # must never be enough, by itself, to authorise a send on it. A compose box IS that
        # authorisation, granted by an oversight, one screen earlier.
        #
        # NARROW ON PURPOSE. This refuses a channel with NO POLICY AT ALL — a static property of
        # the channel, not of the clock. It deliberately does NOT hide the box on a thread whose
        # window has merely shut: that is a live, time-varying decision the send path owns, and
        # taking it here would quietly delete the reply box from every conversation older than a
        # day. Raised with OSDev4, whose machine owns the window.
        return ('<div class="card"><div class="row"><span class="t quiet">Ownbox has no reply '
                'rule for ' + _esc(_channel(str(conv.get("platform") or ""))) + ' yet, so it '
                'will not send on it. You can read everything here; replies go out from the '
                'channel itself for now.</span></div></div>')
    if not any(str(m.get("direction") or "") == "in" for m in msgs):
        # THE SECOND HALF OF THE SAME RULE, and rendering found it the same way. Every channel
        # here is reply-only, so a conversation with no inbound message on record has nothing to
        # reply TO — `window.decide` refuses it outright, on every platform, at every hour.
        #
        # THE LINE THIS FILE DRAWS: the box is absent when the send is refused for a reason THE
        # CLOCK CANNOT CHANGE — no policy for the channel, or nothing ever received on the row.
        # It stays PRESENT when the refusal is the clock itself, a window that shut and reopens
        # the moment they write again; that decision is the send path's, not this screen's, and
        # taking it here would delete the reply box from every conversation older than a day.
        #
        # IT ASKS THE MESSAGES, NOT `last_inbound_at`, AND THAT IS THE WHOLE FIX (OSDev4's call,
        # 2026-09-17: "a thread only renders because messages exist"). `last_inbound_at` is an
        # inference a poller fills and two paths leave it NULL on a thread the customer plainly
        # wrote on: `upsert_conversation` COALESCEs only non-null values in, so one later poll
        # carrying nothing never clears it but one earlier poll carrying nothing never sets it;
        # and `email_channel` passes None outright whenever the message it mirrors is outbound.
        #
        # RENDERED, 2026-09-17, WHICH IS HOW THE SIZE OF IT LANDED. A thread with three messages
        # — two of them from the customer, printed on the screen in her own words — closed with
        # "Nothing has come in on this conversation yet." The page contradicted itself in one
        # scroll, and the reply box was gone, which on a reply-only channel is the whole product.
        # The messages were right there in the route the entire time; this line just asks them.
        #
        # THE SENTENCE IS STILL TRUE WHEN IT PRINTS. The route returns before this on an empty
        # thread, so what reaches here and still has no inbound is a conversation carrying only
        # outbound messages — and on a reply-only channel there is genuinely nothing to reply to.
        return ('<div class="card"><div class="row"><span class="t quiet">Nothing has come in on '
                'this conversation yet. Every channel here is reply-only, so there is nothing to '
                'reply to until they write — and then the box appears.</span></div></div>')
    if not (conv.get("account_id") or "").strip():
        # SAID AS THE BUYER SEES IT. The old sentence ("mirrored before the box started
        # recording which account owns it") described our plumbing, not his thread.
        return ('<div class="card"><div class="row"><span class="t quiet">You cannot reply to '
                'this one from here yet: it arrived before the box knew which of your accounts '
                'it came through. Their next message fixes that.</span></div></div>')
    # WHAT THE CHANNEL WILL PROBABLY DO, SAID BEFORE HE TYPES. OSDev4's `window.explain()`
    # (#1290) turns the decision `decide()` already makes into a sentence for the person about to
    # write. Bound to `state`, NEVER to `reason` or to the English: `reason` is dev-facing on
    # purpose — "nobody has written the send rules for this platform" is an accurate sentence
    # about OUR work and a baffling one to a plumber looking at his own inbox.
    #
    # IT NEVER STOPS A SEND, and this screen must not become the place that does. The window
    # state is an INFERENCE from `last_inbound_at`, a column a poller fills; the vendor's answer
    # is the fact. So a shut clock is a warning above a working box, not a missing box — which is
    # also why this sits here, after the branches that return early for reasons the clock cannot
    # change, rather than adding a new one.
    win = _window_note(conv, msgs)

    # ONE NONCE PER RENDER. It is what "this particular attempt to send" means: a browser that
    # resubmits this same form (double tap, flaky connection, back button) carries the same one
    # and is refused, while a genuinely new reply comes from a new render and sends — even if the
    # words are identical, which "Thanks!" twice in a week very often is.
    from marketing.customer_voice.inbox import reply as _reply

    # WHAT THE MACHINE WOULD SAY, ALREADY IN THE BOX. A draft is a suggestion with the words
    # written, not a message: it is prefilled so he edits and sends in one motion, and it
    # becomes a message only when he taps Send — through the identical path a reply he typed
    # himself takes. If the drafter is off, unconfigured, or has nothing for this thread, the
    # box is simply empty and the screen behaves exactly as it did before.
    drafted, note, off_note = "", "", ""
    try:
        from marketing.customer_voice.drafter import store as _drafts
        row = _drafts.latest_for(_space(), zcid)
        if row:
            drafted = str(row.get("body") or "")
            note = '<div class="drafted">Drafted for you — read it before you send.</div>'
    except Exception as e:                       # noqa: BLE001 — no draft is not a broken page
        log.info("voice.draft_unreadable", extra={"error": type(e).__name__})
    # §2.6, THE THIRD STATE: no banner, no upsell, no empty-draft placeholder. One line, under
    # the box, and ONLY on a thread that would have had a draft — which is why it hangs off the
    # same `row` the drafter would have filled. A buyer with drafting on never sees it; a buyer
    # who has not connected an account sees it once, where the missing thing would have been,
    # instead of being told nothing at all and concluding the box does not work.
    if not drafted:
        from core import box_secrets
        # ONE READER, SO THE THREAD AND SETTINGS CANNOT DISAGREE. This asked `is_set` — "is there
        # a key" — which was the wrong question in the one case that matters most: a key that the
        # vendor has since refused IS set, so this note fell silent and the buyer got an ordinary
        # empty reply box on every thread with no sentence anywhere.
        _ai = box_secrets.anthropic_state()["status"]
        if _ai == "not_connected":
            # STRAIGHT TO WHERE THEY ARE TURNED ON, not to the menu that lists it (#1356): two
            # clicks for a promise worded as one is the bug. That place is now the AI account's
            # one home, System Settings (owner, 2026-09-24: one place per setting), so the owner
            # goes there; a member goes to the page that says whose account it is (`_ai_home`).
            off_note = ('<div class="quiet" style="margin-top:8px">Drafts are off. '
                        f'<a href="{_ai_home()}" style="color:var(--href)">'
                        'Turn them on</a>.</div>')
        elif _ai == "needs_reauth":
            # SAME RULE AS THE LINE ABOVE, APPLIED TO THE OTHER STATE THAT HAS A FIELD BEHIND IT.
            # A key the vendor no longer accepts is fixed by connecting the account again, and
            # that is the same page — so this goes straight there rather than through Settings.
            off_note = ('<div class="quiet" style="margin-top:8px">Drafts are paused — your AI '
                        f'account no longer accepts this key. <a href="{_ai_home()}" '
                        'style="color:var(--href)">Connect it again</a>.</div>')
        elif _ai == "payment_required":
            # AND THIS ONE GOES TO SETTINGS, deliberately, because the fix is NOT a field on this
            # box — it is a card in the Anthropic console. Settings is where that sentence and its
            # link live; sending them to the key form would offer a control that cannot help.
            off_note = ('<div class="quiet" style="margin-top:8px">Drafts are paused — your AI '
                        'account needs credit. <a href="/inbox/settings" '
                        'style="color:var(--href)">See why</a>.</div>')
    # `note` sits ABOVE the box because it introduces the draft inside it. `off_note` sits BELOW,
    # because §2.6 puts it there and the reason is the difference between the two: one labels
    # what is in the box, the other is an aside about what is not. Only one is ever present.
    # `id` IS LOAD-BEARING NOW, not decoration: the inbox row's Reply item links to `#reply`,
    # and an anchor with no target scrolls nowhere and looks like a dead control. The two agree
    # on when it exists because they test the same two fields — see `_acts`.
    # THE BOX FITS THE DRAFT. At three fixed rows a four-line draft was cut off mid-sentence on a
    # mobile, so the owner read half a reply above a Send button. Rows are counted at about 34
    # characters a line (390px, 16px type), capped at ten; `field-sizing` grows it as he edits
    # where the browser supports it.
    picker, picked, picked_id = _snippet_picker(zcid, conv)
    if picked is not None:
        drafted = picked
        note = '<div class="drafted">From a saved reply. Change anything before you send.</div>'
    _tall = min(10, max(3, sum(-(-len(ln) // 34) or 1 for ln in (drafted or "").split("\n"))))
    return (win + picker
            + '<form class="compose" id="reply" method="post" '
            'action="' + _esc(f"/inbox/inbox/{zcid}/reply") + '">'
            f'<input type="hidden" name="n" value="{_esc(_reply.new_nonce())}">'
            f'<input type="hidden" name="snippet" id="snip-used" value="{_esc(picked_id)}">'
            + note +
            f'<textarea name="text" rows="{_tall}" maxlength="1800" required '
            f'placeholder="Write a reply…">{_esc(drafted)}</textarea>'
            + _signed_note(conv) +
            '<button class="btn" type="submit">Send</button>'
            '</form>' + off_note)


def _signed_note(conv: dict) -> str:
    """Under an email reply box: what the signature will add, said before Send, or where to write one.
    The signature itself is added in the send path (inbox/signature.py), never typed into this box."""
    if str((conv or {}).get("platform") or "").strip().lower() != "email":
        return ""
    from marketing.customer_voice.inbox import signature as _sig
    sig = _sig.get(_space())
    if sig:
        return ('<div class="quiet" style="margin:6px 0 8px;white-space:pre-line">Your signature goes at the end:'
                f'\n{_esc(sig)}</div>')
    return ('<div class="quiet" style="margin:6px 0 8px"><a href="/inbox/signature" style="color:var(--href)">'
            'Add an email signature</a> to end every reply with your name and link.</div>')
def _snippet_picker(zcid: str, conv: dict) -> tuple[str, str | None, str]:
    """The saved-replies dropdown above the reply box (inbox/snippets.py). -> (html, picked words or None, its id).

    ITS OWN FORM, ABOVE THE REPLY FORM, and a GET: picking can never send. With scripts on, choosing one fills the
    reply box at once and offers Undo; without them, Use reloads the thread with the words in the box. Either way
    the person reads the words and presses Send, the owner's pick (decision 3). Fill-ins are filled here, so the box
    shows exactly what goes."""
    from marketing.customer_voice.inbox import snippets as _snips
    try:
        rows = _snips.all_for(_space())
    except Exception as e:                       # noqa: BLE001 — a list that can't be read costs the list only
        log.info("voice.snippets_unreadable", extra={"error": type(e).__name__})
        return "", None, ""
    if not rows:
        return ('<div class="quiet" style="margin:0 0 8px"><a href="/inbox/snippets" style="color:var(--href)">'
                'Save replies you send often</a> and pick them here.</div>' if _is_owner() else ""), None, ""
    try:
        me = (dash.session_user(request) or {}).get("name") or ""
    except Exception:                            # noqa: BLE001
        me = ""
    who = conv.get("participant")
    filled = {r["id"]: _snips.fill(r["body"], participant=who, my_name=me) for r in rows}
    want = str(request.args.get("snippet") or "")
    picked = filled.get(want)
    sel = ('font:inherit;font-size:max(16px, calc(16 * var(--px, 1px)));min-height:48px;width:100%;'
           'padding:10px 12px;border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--ink)')
    opts = '<option value="">Saved replies…</option>' + "".join(
        f'<option value="{_esc(r["id"])}" data-body="{_esc(filled[r["id"]])}"'
        f'{" selected" if r["id"] == want else ""}>{_esc(r["title"])}</option>' for r in rows)
    html_ = ('<form class="snip" method="get" action="' + _esc(f"/inbox/inbox/{zcid}") + '#reply" '
             'style="display:flex;gap:8px;align-items:stretch;margin:0 0 8px">'
             f'<select name="snippet" id="snip-pick" aria-label="Saved replies" style="{sel}">{opts}</select>'
             '<button class="btn" type="submit" id="snip-use" style="min-height:48px">Use</button></form>'
             '<div style="margin:-4px 0 8px"><a href="#reply" id="snip-undo" style="display:none;color:var(--href)">'
             'Undo: put back what was in the box</a></div>'
             f'<script>{_SNIP_JS}</script>')
    return html_, picked, (want if picked is not None else "")


# RUN ONCE THE PAGE IS READ: this sits above the reply box, which does not exist yet when it is first seen.
_SNIP_JS = ("document.addEventListener('DOMContentLoaded',function(){var s=document.getElementById('snip-pick');if(!s)return;"
            "var b=document.getElementById('snip-use');if(b)b.style.display='none';"
            "var ta=document.querySelector('#reply textarea[name=text]'),h=document.getElementById('snip-used'),"
            "u=document.getElementById('snip-undo'),prev=null,prevId='';"
            "s.addEventListener('change',function(){var o=s.options[s.selectedIndex];if(!o||!o.value||!ta)return;"
            "if(prev===null){prev=ta.value;prevId=h?h.value:'';}"
            "ta.value=o.getAttribute('data-body');if(h)h.value=o.value;ta.focus();"
            "if(u&&prev.trim())u.style.display='inline';});"
            "if(u)u.addEventListener('click',function(e){e.preventDefault();if(prev===null||!ta)return;"
            "ta.value=prev;if(h)h.value=prevId;s.selectedIndex=0;prev=null;u.style.display='none';});});")


# NOT `@blueprint.post`. `tests/test_customer_voice.py:498-500` scans this department for a CALL
# named `post` and a decorator is a call — `app.py:659` took the same two extra characters for
# the same reason, and weakening the guard to fit a feature is refused by name at `app.py:655`.
@blueprint.route("/inbox/inbox/<path:zcid>/reply", methods=["POST"])
def r_reply(zcid):
    """Take a typed reply and hand it to the one part of this department allowed to send.

    THIS ROUTE DOES NOT SEND, and that is enforced rather than chosen: the department guard above
    means the send lives in `inbox/`, and §8.4 means it has to be a plain function so the future
    auto-responder — which has no request context — can call the identical path. So everything
    here is about the REQUEST: who is asking, what they typed, and what to show them after.
    """
    from marketing.customer_voice.inbox import reply as _reply
    space = _space()
    text = (request.form.get("text") or "").strip()
    here = f"/inbox/inbox/{zcid}"

    # WHICH PERSON — AND NO REPLY WITHOUT ONE. Since migration 47 every session belongs to a real
    # row, so "no user" is not "the owner", it is nobody. The earlier cut defaulted to the owner
    # on any failure here, which meant an unreadable session could put the owner's name on a
    # message he never wrote. `_gate()` has already refused anyone not signed in; this decides
    # whose name goes on the ledger, so it is read and REQUIRED rather than assumed.
    try:
        u = dash.session_user(request)
    except Exception as e:                       # noqa: BLE001 — cannot say who: do not send
        log.error("voice.reply_no_session_user", extra={"error": type(e).__name__})
        u = None
    if not u or not u.get("id"):
        return _thread_notice(zcid, "needs", "Your session could not be read, so nothing was "
                                             "sent. Sign in again and the reply is still here "
                                             "to retype."), 200
    who = u["id"]

    try:
        out = _reply.send_reply(space=space, zcid=zcid, text=text, user_id=who,
                                nonce=request.form.get("n", ""))
    except _reply.ReplyRefused as e:
        log.info("voice.reply_refused", extra={"conversation": zcid, "why": str(e)[:160]})
        return _thread_notice(zcid, "needs", str(e)), 200
    except _reply.ReplyIndeterminate:
        # NEVER "failed" and never a retry button. Invariant 4: a timeout is indeterminate, and
        # the one thing a person must not do is send it again.
        #
        # THE COPY NO LONGER ASSERTS A NETWORK DROP, because since the ledger key is CLAIMED
        # before the vendor call this arm has a second, ordinary cause: a double-tapped send
        # button, where the losing request is told "may have arrived" while its twin is still
        # mid-call. Nothing dropped there, and telling a person it did — then sending them to
        # the platform to check — is a scare over a button they pressed twice. What is true in
        # BOTH cases, and is the only part that matters, is that the box does not know and will
        # not resend.
        log.error("voice.reply_indeterminate", extra={"conversation": zcid})
        return _thread_notice(
            zcid, "needs",
            "This was handed over and the box never got a clear answer back, so it may or "
            "may not have arrived. Open the thread on the platform before sending it again "
            "— the box will not resend it for you."), 200
    except Exception as e:                       # noqa: BLE001 — a reply is never worth a 500
        log.error("voice.reply_failed", extra={"conversation": zcid,
                                               "error": f"{type(e).__name__}: {e}"[:160]})
        return _thread_notice(zcid, "needs", "That did not send. Nothing was charged and "
                                             "nothing was delivered."), 200

    if out.get("duplicate"):
        log.info("voice.reply_duplicate_absorbed", extra={"conversation": zcid})
    elif request.form.get("snippet"):
        from marketing.customer_voice.inbox import snippets as _snips
        _snips.used(space, str(request.form.get("snippet")))
    # PRG: redirect after post, so a refresh cannot re-submit the form at all.
    return redirect(here, code=303)


# ── WHO REALLY SENT IT ───────────────────────────────────────────────────────────────────────
# WHAT THIS SCREEN SHOWED UNTIL 2026-09-23: a display name, and nothing else. "Morning Review"
# tells a reader nothing — a display name is the one part of an email anybody can set to
# anything, and it is precisely what a spoof relies on. Gmail puts the address beside the name
# and hides the rest behind a caret. Owner, 2026-09-23: *"our inbox doesn't even show what the
# email address is of the sender... study Gmail and how they have a little drop-down."*
#
# BUILT FROM `inbox_message_detail`, so it is empty on a Messenger thread, empty on mail stored
# before the headers were kept, and populated the moment that mail is polled again.
_AUTH = re.compile(r"\b(spf|dkim|dmarc)\s*=\s*([a-z]+)", re.I)


def _addr_of(headers: dict) -> str:
    """The sender's bare address, or "" — for the line under the name."""
    import email.utils
    _n, addr = email.utils.parseaddr(str((headers or {}).get("From") or ""))
    return addr.strip()


def _domain_of(value: str) -> str:
    """The domain in an address or a `d=` tag, without the angle brackets or the semicolon."""
    got = str(value or "").strip().strip("<>").rstrip(";")
    return got.rsplit("@", 1)[-1].strip().strip("<>").rstrip(";") if got else ""


def _signed_by(headers: dict) -> str:
    """The `d=` of the first DKIM signature — Gmail calls this "signed-by"."""
    m = re.search(r"\bd\s*=\s*([^;\s]+)", str((headers or {}).get("DKIM-Signature") or ""))
    return m.group(1).strip() if m else ""


def _checks(headers: dict) -> str:
    """SPF / DKIM / DMARC as the receiving server recorded them, or "".

    NOT "STANDARD ENCRYPTION (TLS)", WHICH IS WHAT GMAIL PRINTS HERE. That line describes the hop
    into Google's own servers and Google wrote it. This box did not make that hop and cannot
    honestly report on it, so it answers the question a buyer actually has — is this really from
    who it says — with the verdicts the mail carries.
    """
    seen = {}
    for kind, verdict in _AUTH.findall(str((headers or {}).get("Authentication-Results") or "")):
        seen.setdefault(kind.upper(), verdict.lower())
    return ", ".join(f"{k} {v}" for k, v in seen.items())


def _details(headers: dict) -> str:
    """Gmail's caret, with the rows we can fill honestly. "" when we kept nothing for this one."""
    if not headers:
        return ""
    rows = [("from", headers.get("From")), ("to", headers.get("To")), ("cc", headers.get("Cc")),
            ("date", headers.get("Date")), ("subject", headers.get("Subject")),
            ("mailed-by", _domain_of(headers.get("Return-Path"))),
            ("signed-by", _signed_by(headers)), ("security", _checks(headers))]
    body = "".join(f'<div class="dr"><span class="dk">{_esc(k)}</span>'
                   f'<span class="dv">{_esc(str(v))}</span></div>'
                   for k, v in rows if str(v or "").strip())
    if not body:
        return ""
    return ('<details class="det"><summary>Details</summary>'
            f'<div class="dt">{body}</div></details>')


def _thread_notice(zcid: str, kind: str, message: str) -> str:
    """Say what happened, on the thread, without losing the thread."""
    return _shell(f'<div class="card {_esc(kind)}"><div class="row"><span class="t">'
                  f'{_esc(message)}</span></div></div>'
                  f'<div class="foot"><a href="/inbox/inbox/{_esc(zcid)}">← Back to the '
                  'conversation</a></div>')


# ── the installable part ────────────────────────────────────────────────────────────────────
# STAGE 3 (spec §6). A manifest, two icons, a service worker, and the screen that teaches the
# install — because on an iPhone there IS no prompt we can trigger and a buyer who never installs
# has bought a bookmark that cannot notify him.
#
# THESE FOUR ROUTES ARE THE ONE HOLE IN THE GATE, and it is deliberate, narrow and listed. A
# manifest is fetched by the BROWSER, and a manifest fetch does not send cookies unless the link
# carries crossorigin="use-credentials" — so a gated manifest fails to install with no error a
# person could act on, which is the silent failure this whole file is written against. The same is
# true of the icons it names. None of these four carries a customer's name, a message, or a
# number: the manifest is a colour and a title, the icons are the Ownbox mark (core/dash/look.py),
# and the worker is our own code. `start_url` still points at `/inbox/`, which IS gated — so
# opening the installed app asks for the password exactly as the browser does.
PUBLIC_PATHS = frozenset({
    "/inbox/manifest.webmanifest",
    "/inbox/icon-192.png",
    "/inbox/icon-512.png",
    "/inbox/sw.js",
    "/voice/sw.js",   # THE TOMBSTONE, not an install file — see TOMBSTONE_SW_JS. Remove with it.
})

# THE INSTALLED APP'S SPLASH AND BAR, before any page has loaded. It was near black,
# from before white became the default (owner, 2026-09-16: white first): a cream app opened on
# a black splash under a black status bar. A manifest carries one value, so it is the light
# one, the look every box opens in.
THEME = _THEME_BG["light"]


# ── settings, and the light/dark switch ─────────────────────────────────────────────────────
def _mailbox_row() -> str:
    """The Settings row that makes /inbox/mailbox reachable AFTER it has been set up.

    A DEAD END I BUILT, found by reading the rendered Settings rather than the code: every link
    to the mailbox screen lived on an EMPTY state, so connecting an inbox deleted the only way
    back to it. And the way back is not a nicety — Google revokes an app password whenever the
    account password changes, which is the single most likely reason a buyer needs this screen
    again, and by then the empty states are gone.

    IT SAYS THE STATE, because "is it still reading my mail" is the question this row is for. It
    never carries the password; `email_state()` is documented not to return one.

    ABSENT WHEN THE BOX DOES NOT SERVE THE SCREEN — same rule as every other link in this app.
    """
    go = _live("/inbox/mailbox")
    if not go:
        return ""
    from core import box_secrets
    st = box_secrets.email_state()
    status, who = st.get("status"), st.get("user") or ""
    if status == "needs_reauth":
        said = (f'Google is refusing the app password for <b>{_esc(who)}</b> — nothing from this '
                'inbox is arriving until it is replaced.')
        verb = "Fix it"
    elif status == "admin_disabled":
        said = ('Your Google administrator has switched app passwords off, so this inbox cannot '
                'be read.')
        verb = "What to do"
    elif who:
        said = f'Ownbox is reading <b>{_esc(who)}</b>. It sends only the replies you send, and never marks a message read.'
        verb = "Change or stop it"
    else:
        said = ('Ownbox can read the mail your customers send you and draft replies. Nothing is '
                'connected yet.')
        verb = "Connect your inbox"
    # THE REQUIRED STEP OWNS THE SCREEN'S ONE INK PILL while it is undone or refused: reading the
    # mail is what the box is. It was a small link while the optional social accounts row below
    # carried the pill, so a fresh box pointed its buyer at the step that can wait.
    if not _is_owner():
        return f'<div class="setrow"><b>Your inbox</b><span>{said}</span>{_OWNER_ONLY}</div>'
    if status == "needs_reauth" or not who:
        return (f'<div class="setrow"><b>Your inbox</b><span>{said}</span>'
                f'<p style="margin:10px 0 0"><a class="btn" href="{go}">{verb}</a></p></div>')
    return (f'<div class="setrow"><b>Your inbox</b><span>{said} '
            f'<a href="{go}" style="color:var(--href)">{verb}</a>.</span></div>')


@blueprint.get("/inbox/settings")
def r_settings():
    """The third tab. It exists because the switch needs somewhere to live that is not a thread.

    THE SWITCH IS A LINK, NOT A SCRIPT. Every screen in this app is server-rendered so that a
    person with JavaScript blocked still reads their inbox, and a theme applied by script has a
    second problem beyond that: the page paints the old theme first and then repaints, which is
    the flash every JS theme toggle on the web has to work around. A cookie read during render
    has neither failure.
    """
    # THE FIRST UNFINISHED ROW OWNS THE SCREEN'S ONE INK PILL; the drafts row below it defers.
    _mb = _mailbox_row()
    _above = _mb + _channels_row(primary='class="btn"' not in _mb)
    body = (
      '<h1>Settings</h1>'
      '<div class="card">'
      # INBOX, THEN CHANNELS, THEN DRAFTS — the owner's own order (2026-09-16): "the first item
      # on the screen is the Gmail setup fields and instructions, and the second area should be
      # the zernio key field along with instructions". It is also the order a buyer does them in.
      # Reading the mail is what the box IS; the channels widen what it reads; drafting is what it
      # does with what it read, and a row for the last above the first asks somebody to configure
      # an answer to a question nothing is yet asking.
      + _above + _drafts_row(primary='class="btn"' not in _above) +
      # ONE HOME FOR EACH SHARED THING (owner, 2026-09-29, IA decision D3 in docs/SCOPE_APP_IA.md).
      # The AI account, the assistants, installing the app and Appearance belong to the whole box,
      # so they live once, in System Settings; this screen keeps only what is the inbox's own and
      # says where the rest went. The installed app's scope is the whole box since Phase 4, so the
      # link stays inside the app on an iPhone.
      '<div class="setrow"><b>Everything else</b>'
      '<span>Your AI account, assistants, the mobile app and Appearance are the whole box\'s, '
      'so they are in System Settings. '
      '<a href="/settings" style="color:var(--href)">System Settings</a></span></div>'
      '<div class="setrow"><b>How this stays current</b>'
      '<span>The box checks for new messages on a schedule rather than holding a connection '
      'open. Pull down to check now.</span></div>'
      '</div>')
    body += f'<script>{_push_client_js()}</script>'
    return _shell(body), 200


@blueprint.get("/inbox/theme")
def r_theme():
    """Set, or clear, the appearance cookie — then go back to settings.

    `system` DELETES the cookie rather than storing the word, so "follow the phone" is the absence
    of a preference and not a third value every reader has to know about. A value we did not write
    is ignored by `_theme` and cleared here, so a hand-typed URL cannot wedge the app into a theme
    that has no tokens.
    """
    to = (request.args.get("to") or "").strip().lower()
    resp = redirect("/inbox/settings", code=303)
    # THE ONE HOME IS THE PERSON'S SETTING (core/dash/theme.py), so this switch writes there too.
    # Only Light and Dark, the two this screen offers: System (owner, 2026-09-27) comes with the
    # redesign's control, which posts to /settings/theme; an old `?to=system` bookmark stays white.
    # The cookie below stays for a phone that isn't signed in.
    from core.dash import theme as _core_theme
    uid = _core_theme._user_id()
    if uid and to in ("light", "dark"):
        _core_theme.put(uid, to)
    if to in ("light", "dark"):
        resp.set_cookie(THEME_COOKIE, to, max_age=365 * 86400, samesite="Lax",
                        secure=request.is_secure, httponly=True, path="/inbox")
    else:
        resp.delete_cookie(THEME_COOKIE, path="/inbox")
    return resp


# NOT CACHED HERE ANY MORE: core's answer caches by the icon's version, and a cache here outlived an
# upload — the inbox served the old mark until the box restarted (found by its test, 2026-09-29).
def _png(size: int) -> bytes:
    """The app's icon at `size`: the Ownbox mark, drawn by core/dash/look.py.

    IT WAS A PLACEHOLDER, a sky-blue ring, and said so here from the day it shipped. Owner,
    2026-09-24, with the installed app on a home screen beside the Ownbox icon: *"Right now it
    has just a blue O, but I want the actual."* So the app wears the box's one mark, the same one
    ownbox.io and every box screen use. It is drawn to the edge, as a maskable icon must be: the
    launcher cuts the corners, and the cream square sits inside the safe zone.

    WHY BYTES IN THE MODULE still holds. A manifest's `icons` are URLs the browser fetches, so this
    app keeps its own two addresses; the picture comes from core, which every box has, and never
    from `sites/`, which no box has. The drawing is Pillow-free for the same reason as before.

    THE CLIENT'S ICON ONCE THEY UPLOAD ONE (owner, 2026-09-29: it appears "everywhere"), through
    core's one answer for every home screen, so this app never learns that an upload exists.
    """
    return _look.app_png(size)


@blueprint.get("/inbox/manifest.webmanifest")
def r_manifest():
    """WHAT MAKES IT INSTALLABLE AT ALL. `display: standalone` is not decoration: MDN's
    compatibility data says the `Notification` interface is undefined on iOS unless the page is a
    home-screen app AND the manifest has a non-default `display`. Without this line there is no
    push on an iPhone, ever."""
    import json
    from flask import Response
    # THE WHOLE BOX IS IN SCOPE, AND THE APP IS STILL THIS ONE (owner, 2026-09-29: "keep you inside").
    # The menu reaches System Settings and the Base Machine, and outside its scope an iPhone opened
    # those in a browser sheet with a Done button. `id` pins who the app is — it was its start_url,
    # "/inbox/", and still is — so widening the scope changes where it goes, not what it is.
    m = {
        "id": "/inbox/",
        "name": f"{dash.brand()} · {APP_TITLE}",
        "short_name": APP_TITLE,
        # THE APP OPENS ON ITS MESSAGES (IA D1, 2026-09-29). `id` stays "/inbox/": it is the
        # installed app's identity, and changing it would make every installed icon a stranger.
        "start_url": "/inbox/inbox",
        "scope": "/",
        "display": "standalone",
        "background_color": THEME,
        "theme_color": THEME,
        # BOTH SIZES, because Chrome's installability criteria want a 192 and a 512, and `maskable`
        # so an Android launcher crops our own safe zone rather than a square. At this app's own two
        # addresses, versioned once a client icon is uploaded so a phone fetches the new one.
        "icons": _look.manifest_icons("/inbox/icon-{n}.png"),
    }
    return Response(json.dumps(m), mimetype="application/manifest+json")


@blueprint.get("/inbox/icon-192.png")
def r_icon_192():
    from flask import Response
    return Response(_png(192), mimetype="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


@blueprint.get("/inbox/icon-512.png")
def r_icon_512():
    from flask import Response
    return Response(_png(512), mimetype="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


# ── the two typefaces, served by the box itself ─────────────────────────────────────────────
#
# OWNER, 2026-09-18, choosing between three pairings rendered on the real screen: "A all the
# way." Archivo for headings, Public Sans for body.
#
# THEY ARE NOT ON A CDN, AND THAT IS THE WHOLE POINT. A box is single-tenant, cloned per
# customer and installed to a home screen as a PWA. A `<link>` to fonts.googleapis.com would
# mean a business's own machine cannot draw its own text without reaching a third party — on a
# train, behind a corporate proxy, or on the day that host is slow — and would tell that host
# every time a buyer opened their inbox. 62 KB of latin-subset variable woff2 lives in the
# repository instead, which is less than one channel logo would cost as a PNG.
#
# THE NAME IS A KEY, NEVER A PATH. `name` arrives from the URL and is only ever looked up in
# `_FONTS`; it is not joined to a directory, so `../` and an absolute path are not traversals
# here, they are simply misses. Serving a file whose name a stranger chose is how a settings
# page becomes a file browser.
_FONTS = {"head": "archivo-latin-var.woff2", "body": "public-sans-latin-var.woff2"}


@blueprint.get("/inbox/font/<name>.woff2")
def r_font(name: str):
    """One of exactly two files, or a 404. Both are OFL 1.1; the licences sit beside them."""
    from flask import Response
    fn = _FONTS.get(str(name or "").strip().lower())
    if not fn:
        return ("", 404)
    try:
        blob = (pathlib.Path(__file__).resolve().parent / "fonts" / fn).read_bytes()
    except OSError as e:
        # A MISSING FONT COSTS THE TYPEFACE, NEVER THE PAGE. `font-display: swap` means the
        # fallback stack is already on screen, so a 404 here is a screen that looks ordinary
        # rather than one that fails to render.
        log.warning("voice.font_unreadable", extra={"font": fn, "error": type(e).__name__})
        return ("", 404)
    # THIRTY DAYS, AND DELIBERATELY NOT `immutable`. These URLs carry no content hash, so a
    # clone that takes a new file by `git pull` serves it at the same address — a year-long
    # immutable cache would leave installed home-screen apps drawing last month's font until
    # 2027. A month is long enough that nobody fetches this twice in a session.
    return Response(blob, mimetype="font/woff2",
                    headers={"Cache-Control": "public, max-age=2592000"})


# THE WORKER DOES NOT CACHE ANYTHING YET, and that is the right stage-3 answer. A cache on a page
# of customer messages is a copy of customer messages on the device, and deciding how long that
# lives is a question worth asking on purpose rather than inheriting from a boilerplate. What it
# exists for today is stage 4: a registered worker is the thing a push is delivered TO.
SW_JS = """
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });

// EVERY PUSH SHOWS A NOTIFICATION, with no exception and no silent path. Apple: "Safari doesn't
// support invisible push notifications... If you don't [show one], Safari revokes the push
// notification permission for your site." One silent push costs the whole channel, so the
// fallback below shows something even when the payload cannot be read.
self.addEventListener('push', function (event) {
  var d = {};
  try { d = event.data ? event.data.json() : {}; } catch (err) { d = {}; }
  var title = d.title || 'Inbox Machine';
  var body = d.body || 'Something new came in.';
  event.waitUntil(self.registration.showNotification(title, {
    body: body,
    icon: '/inbox/icon-192.png',
    badge: '/inbox/icon-192.png',
    data: { navigate: d.navigate || '/inbox/inbox' }
  }));
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  var to = (event.notification.data && event.notification.data.navigate) || '/inbox/inbox';
  // ONLY OUR OWN APP. The payload is authored by the box and encrypted to this subscription, so
  // this is defence in depth, not a fix — the same rule safe_next applies on the way in. The
  // morning review (/app/review), Shifts (/shifts/, where a coworker's report opens), Add a
  // Machine (/add-machine, where an update that stopped a machine the owner built is explained,
  // core/machine_breaks.py) and Approvals (/approvals, core/approvals.py: without it, a tap on
  // "waiting for your OK" opened Messages) are the doors outside the inbox a notification may open.
  var door = typeof to === 'string' && (to === '/app/review' || to.indexOf('/app/review/') === 0
      || to.indexOf('/shifts/') === 0 || to === '/add-machine' || to === '/approvals');
  if (typeof to !== 'string' || (to.indexOf('/inbox/') !== 0 && !door)) { to = '/inbox/inbox'; }
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true })
    .then(function (list) {
      for (var i = 0; i < list.length; i++) {
        if (list[i].url.indexOf('/inbox/') !== -1 && 'focus' in list[i]) {
          list[i].navigate(to); return list[i].focus();
        }
      }
      return self.clients.openWindow(to);
    }));
});
"""


@blueprint.get("/inbox/sw.js")
def r_sw():
    """SCOPE IS THE TRAP. MDN: a worker cannot have a scope broader than its own location unless
    the server sends `Service-Worker-Allowed`. Served at `/inbox/sw.js` it controls `/inbox/*` and
    nothing else, which is exactly what we want — so no header is needed, but the registration
    below still passes `{scope: '/inbox/'}` explicitly, because the failure mode is silent: it
    registers, reports success, and never intercepts a thing."""
    from flask import Response
    return Response(SW_JS, mimetype="application/javascript",
                    headers={"Cache-Control": "no-cache"})


# ── the tombstone at the OLD worker address ─────────────────────────────────────────────────────
# /voice/* MOVED TO /inbox/* ON 2026-09-17. The owner: "No redirect, please. Full cut over", because
# /voice is the AI receptionist machine's path — so every other /voice route is simply gone: a 404,
# never a redirect. THIS ONE ADDRESS STAYS, briefly, for a reason no redirect could serve. A phone
# that installed the app before the cut holds a worker registered at scope /voice/, and a worker can
# never claim /inbox/ (the scope rule in r_sw above). On its next update check that device fetches
# THIS file, installs it, and it unregisters itself. Nothing else: no fetch handler, no cache, no
# navigation — it must not answer a single request on a path that is no longer ours.
# REMOVE IT, and its PUBLIC_PATHS entry, once the owner's installed phone has taken it (runbook
# #1311 §5.3). Until then tests/test_voice_path_is_free.py holds it to exactly this.
TOMBSTONE_SW_JS = """// The inbox moved to /inbox/. This worker removes the old one and does nothing else.
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (event) {
  event.waitUntil(self.registration.unregister());
});
"""


@blueprint.get("/voice/sw.js")
def r_sw_tombstone():
    """The old worker's address, serving only the old worker's removal. See TOMBSTONE_SW_JS."""
    from flask import Response
    return Response(TOMBSTONE_SW_JS, mimetype="application/javascript",
                    headers={"Cache-Control": "no-cache"})


# `methods=["POST"]` RATHER THAN `@blueprint.post`, deliberately. test_customer_voice bans a
# call named `post` anywhere in this department — the structural guarantee that Customer
# Voice reads and drafts but cannot publish. A Flask route decorator is the opposite of
# publishing (it registers an INBOUND route) so this is a false positive on the name, but the
# guard is worth more than the two characters it costs me to avoid it. Weakening a "cannot
# publish" rule to fit a beacon would be a bad trade at any price.
# NOT `@blueprint.post`. tests/test_customer_voice.py scans this department for a CALL named
# `post`, and a decorator is a call — the same two extra characters app.py:659 and :909 spend.
@blueprint.route("/inbox/drafts", methods=["GET", "POST"])
def r_drafts():
    """§2.6 — where the buyer connects the AI account that writes their drafts.

    THE KEY IS WRITTEN, NEVER READ BACK. It goes into `box_secrets` (which explains why a table
    rather than `.env`: the drafter runs in the worker, a different process, and the web app
    cannot write its environment). No screen and no route ever returns it — the Settings row
    reports only that one is present, and this form is always empty even when a key is set.
    """
    from core import box_secrets
    gate = _gate()
    if gate is not None:
        return gate
    # WHO SET IT, for the audit line. Best effort and never a blocker: `_gate` has already
    # refused anyone not signed in, so this only decides whose name the row carries.
    try:
        _u = dash.session_user(request) or {}
    except Exception:                            # noqa: BLE001 — an unreadable session is not
        _u = {}                                  # a reason to refuse the buyer their own key
    whoami = _u.get("id")
    # TURN OFF AND TURN ON ARE A SWITCH, POSTED, AND THE OWNER'S. The switch is the box's
    # `inbox.drafts.enabled`, which the drafter already reads; the AI account is not touched, so
    # every other machine keeps drafting and turning it back on needs no sign-in. A GET with
    # ?off=1, the old link, changes nothing now (see `_post_button`).
    choice = request.form.get("drafting") if request.method == "POST" else None
    if choice in ("on", "off"):
        if not _is_owner():
            return _owner_refusal()
        from core import box_settings
        box_settings.put("inbox", "drafts.enabled", choice == "on", set_by=whoami)
        return redirect("/inbox/settings", code=303)
    # THE AI ACCOUNT HAS ONE HOME, and it is System Settings → AI account (owner, 2026-09-24:
    # "there's only one place to add a key or change a setting"; docs/SCOPE_ONE_PLACE_PER_SETTING.md).
    # This page used to carry a second paste form for the same credential. Now it says which account
    # the drafts use and links to that home; a post (an old page left open) stores nothing.
    if request.method == "POST":
        return redirect("/settings/ai" if _is_owner() else "/inbox/settings", code=303)
    # NO SECOND PAGE FOR IT EITHER (owner, 2026-09-29, IA D3 in docs/SCOPE_APP_IA.md). The drafting
    # switch is a row on the inbox's own Settings, beside the door to the account; this address
    # still answers — old links and the switch's form land here — and goes there.
    return redirect("/inbox/settings", code=302)


# ── the mailbox ─────────────────────────────────────────────────────────────────────────────
# NUMBERED, BECAUSE THE BUYER IS IN A DIFFERENT TAB. Every one of these is a place in Google's
# own settings, and the order is not cosmetic: App passwords is HIDDEN until 2-Step Verification
# is on, so a buyer sent looking for it first finds nothing and concludes the product is wrong.
# The wording is OSDev1's, from `core.box_secrets.SETUP_STEPS` in #1250 — kept identical here
# rather than reworded, so that when that contract lands this screen renders it unchanged.
def _step_spec(key: str) -> dict:
    """One entry of `box_secrets.SETUP_STEPS` by key, or {} on a box too old to have it.

    THE COPY HAS ONE SOURCE NOW. This file carried its own tuple of Gmail instructions, written
    to match #1250's word for word, which is the arrangement that stays identical right up until
    somebody edits one of them. The set-up screen and this screen render the SAME entry.
    """
    try:
        from core import box_secrets
        for step in box_secrets.SETUP_STEPS:
            if step["key"] == key:
                return step
    except Exception:                            # noqa: BLE001 — a missing contract is not a 500
        pass
    return {}


_MAILBOX_STEPS = tuple(_step_spec("email").get("steps") or ())
_MAILBOX_ADMIN = ("If App passwords is missing, your Google administrator has switched it off "
                  "for your organisation — ask them to allow it.")


def _mailbox_host(form) -> str:
    """The IMAP host a posted form names: a preset's, or the one typed for "Another provider".

    RAISES `SecretRejected` WITH THE SENTENCE, so both screens show a bad server exactly as they
    show a bad password. A form from before the provider field existed posts no provider, and that
    is Gmail — the only thing it could have meant."""
    from core import box_secrets
    from core.vendors.mailbox import providers
    try:
        return providers.host_for(str(form.get("provider") or providers.DEFAULT),
                                  str(form.get("host") or ""))
    except ValueError as e:
        raise box_secrets.SecretRejected(str(e)) from None


def _mailbox_form(*, user: str = "", note: str = "", verb: str = "Start reading this inbox") -> str:
    """The two fields and the button. THE PASSWORD IS NEVER PRE-FILLED and never echoed back —
    the address is, because retyping it after a rejected password is a punishment for their
    typo in the other field."""
    field = ('font:inherit;font-size:max(16px, calc(17 * var(--px, 1px)));padding:12px 14px;width:100%;'
             'border:1px solid var(--line);border-radius:12px;'
             'background:var(--card);color:var(--ink)')
    return (note +
            '<form class="compose" method="post" action="/inbox/mailbox" '
            'style="display:flex;flex-direction:column;gap:10px;align-items:stretch">'
            + _trip_field() + _mailbox_provider_fields() +
            # LABELLED, NOT ONLY PLACEHOLDERED: a placeholder vanishes the moment the buyer
            # types, and on a mobile that is the moment they switch apps to fetch the password.
            '<label style="display:block;margin-top:10px">'
            '<span class="t" style="display:block;font-size:calc(14.5 * var(--px, 1px));margin-bottom:4px">'
            'Your email address</span>'
            f'<input type="email" name="user" value="{_esc(user)}" autocomplete="email" '
            'spellcheck="false" aria-label="The email address to read" '
            f'placeholder="you@yourcompany.com" style="{field}"></label>'
            '<label style="display:block">'
            '<span class="t" style="display:block;font-size:calc(14.5 * var(--px, 1px));margin-bottom:4px">'
            'App password</span>'
            '<input type="password" name="password" autocomplete="off" spellcheck="false" '
            'aria-label="App password" placeholder="the app password for this mailbox" '
            f'style="{field}"></label>'
            f'<button class="btn" type="submit">{_esc(verb)}</button></form>')


def _mailbox_provider_fields() -> str:
    """Where the mail lives, and the server for "Another provider" — from the same contract fields
    the set-up screen draws, so the two screens cannot offer different lists."""
    spec = {f.get("name"): f for f in _step_spec("email").get("fields") or ()}
    return "".join(_setup_field(spec[n]) for n in ("provider", "host") if n in spec)


def _mailbox_no_password_yet() -> str:
    """The line that joins the form to the instructions under it.

    THE FORM COMES FIRST NOW, and without a sentence the four numbered steps beneath it read as
    an afterthought rather than as the answer to the obvious question. One line, phrased as the
    question a person actually has.
    """
    return ('<p class="quiet" style="margin-top:14px">Do not have one yet? '
            'It takes about a minute:</p>')


def _mailbox_steps() -> str:
    items = "".join(f'<div class="row"><span class="n">{i}</span>'
                    f'<span class="t">{_esc(t)}</span></div>'
                    for i, t in enumerate(_MAILBOX_STEPS, 1))
    return (f'<div class="card">{items}{_step_extras(_step_spec("email"))}</div>'
            f'<p class="quiet">{_esc(_MAILBOX_ADMIN)}</p>')


# ── where the steps come from ───────────────────────────────────────────────────────────────
# #1273 gives core a plug-in point for set-up steps (`core.onboarding.register_step`) so that the
# base box stops knowing which machines exist. It registers NOTHING itself — OSDev4 moves the
# inbox's Gmail and Zernio steps onto it next, in his own machine. Between those two landings
# `onboarding.steps()` is EMPTY, and a screen that rendered it unconditionally would show a paying
# customer a set-up page with nothing on it and no way to connect anything.
#
# So the screen asks the seam first and falls back to the old contract while the seam is still
# empty. DELETE THIS WHOLE SECTION once every step is registered: `_setup_source` collapses to
# `onboarding.steps()` and `_setup_save` to `onboarding.save(...)`. Nothing above or below changes,
# because nothing else knows which source it got — that is the point of the entries having one
# shape.
def _seam_keys() -> set:
    """The step keys the seam actually holds — empty on a box whose machines have not moved yet."""
    try:
        from core import onboarding
        return {str(e.get("key")) for e in onboarding.steps()}
    except Exception:                            # noqa: BLE001 — a box too old for the seam
        return set()


def _setup_source() -> list:
    """The steps to render: BOTH sources, merged, the seam winning a key it has taken over.

    THIS USED TO PREFER THE SEAM, AND THAT WAS A LANDMINE. The line was `live = onboarding.steps()`
    / `if live: return live` — so the FIRST step anybody registered through the seam, about
    anything at all, made every step still living in `box_secrets` disappear from the screen.
    Measured on 2026-09-17 (OSDev4, scope doc #1307): the set-up page goes from
    ['email', 'zernio', 'anthropic'] to ['relay'] on one unrelated registration. Not a crash — a
    page that renders, and is wrong, on the screen a buyer uses to connect his box.

    MERGING IS ALSO WHAT MAKES THE MIGRATION POSSIBLE. `core/onboarding`'s own comment says the
    old section gets deleted "once every step is registered", which under the old behaviour meant
    ALL THREE had to move in one commit or the screen broke in between. Merged, a step moves on
    its own: register `email` in the seam and it REPLACES the old one, in place; register nothing
    and nothing changes.

    IN PLACE, DELIBERATELY. A seam step that takes over an existing key keeps that key's position
    rather than jumping to the end by `order` — the owner set this screen's order (mailbox, then
    channels, then drafts) and migrating a step is not the moment to rearrange his screen.

    A seam that raises is still not allowed to take the page down with it: the old contract renders
    alone and the failure is logged rather than shown. The reverse is not true — if the OLD
    contract raises there is nothing left to draw, and `r_setup` turns that into a page that says
    so, which is why this does not swallow it.
    """
    seam: dict = {}
    try:
        from core import onboarding
        seam = {str(e.get("key")): dict(e) for e in onboarding.steps()}
    except Exception as e:                       # noqa: BLE001 — the page outranks the seam
        log.warning("voice.setup_seam_unreadable", extra={"error": f"{type(e).__name__}: {e}"[:160]})
    from core import box_secrets
    out, taken = [], set()
    for entry in box_secrets.setup_state():
        key = str(entry.get("key"))
        migrated = seam.get(key)
        out.append(dict(migrated) if migrated else dict(entry))
        taken.add(key)
    # Whatever the seam holds that core does not: appended in the order its machines declared,
    # which is what `onboarding.steps()` already sorts by.
    out.extend(dict(e) for k, e in seam.items() if k not in taken)
    # AND THE BOX'S OWN STEPS ARE NOT THIS SCREEN'S, as of 2026-09-22. Owner: "Step four and five
    # should not be in this wizard any longer" — the AI account, the phone and the AI coworkers
    # are the box's settings and are finished in the box's own drawer, on core screens.
    #
    # FILTERED HERE, AT THE SOURCE, RATHER THAN IN THE RENDERER. This function is the answer to
    # "what does this screen show", and every counter, progress bar and assertion downstream
    # derives from it — `_setup_progress` counts what it is handed. A filter applied later would
    # leave the bar counting steps that are not on the page, which is the "3 of 5" on a finished
    # screen that nobody could explain.
    #
    # A STEP THAT DECLARES NOTHING IS STILL THIS SCREEN'S. `surface_of` defaults to the machine,
    # so every machine written before today keeps every step it had.
    return [e for e in out if box_secrets.surface_of(e) == box_secrets.SURFACE_MACHINE]


# EACH STEP'S HOME, and whether it is required. Named here, in the machine, because core may not
# learn an inbox route (tests/test_core_boundary.py). A step a machine registers through core's seam
# declares its own `home`; one with none falls back to this machine's Settings.
_SETUP_HOMES = {
    "email": ("/inbox/mailbox", "Connect your inbox", True),
    "zernio": ("/inbox/connect", "Connect your social accounts", False),
}


def _setup_save(which: str, form, *, user_id: str | None) -> None:
    """Store one step's values. Raises an error whose str() is a sentence for the buyer.

    THROUGH THE SEAM WHEN THE STEP LIVES THERE, and that is worth more than tidiness: core hands
    the machine only the fields the step DECLARED, so a hidden field posted from a crafted form
    cannot reach a store that happens to read it.
    """
    from core import box_secrets
    if which in _seam_keys():
        from core import onboarding
        onboarding.save(which, form, user_id=user_id)
        return
    # NO OTHER STEP IS SAVED HERE. The mailbox, social accounts and the AI account each have one
    # home that saves them (owner, 2026-09-24: one place per setting), and set-up only links to it.
    # The legacy arm that wrote all three from this screen went with that ruling; `r_setup` already
    # refused to reach it, and tests/test_one_place_per_setting.py holds that nothing does again.
    raise box_secrets.SecretRejected("That form is not one this screen knows.")


def _setup_rejections() -> tuple:
    """The two errors that carry a sentence for the buyer. Both subclass ValueError, and catching
    ValueError itself would put a machine's genuine bug on the screen as if it were advice."""
    from core.box_secrets import SecretRejected
    try:
        from core.onboarding import StepRejected
        return (StepRejected, SecretRejected)
    except Exception:                            # noqa: BLE001 — a box too old for the seam
        return (SecretRejected,)


# ── the set-up screen ───────────────────────────────────────────────────────────────────────
# WHAT A BUYER READS, PER STATUS: the sentence, the tone, and WHAT THE BUTTON SAYS. The verb
# belongs in this table and not in a branch, because it is a claim about the world: "Replace it"
# tells someone there is something stored to replace, and only some of these statuses know that.
_SET_STATUS = {
    "connected":        ("Connected", "good", "Replace it"),
    "needs_reauth":     ("Needs a new password", "warn", "Replace it"),
    "admin_disabled":   ("Switched off by your administrator", "warn", "Replace it"),
    "payment_required": ("Needs a payment method on your Zernio account", "warn", "Replace it"),
    "not_connected":    ("Not connected yet", "dim", "Save"),
    # CORE'S OWN VERDICT, not a machine's: `onboarding._live_state` answers `unavailable` when a
    # machine's state() raises or returns a status outside the closed set. It must NOT read as
    # "not connected" — that sends someone to make a new App password they did not need. It is
    # the box that could not look, and the sentence core supplies says so.
    #
    # AND THE BUTTON SAYS "Save", NOT "Replace it". We could not check, so we do not know that
    # anything is stored; offering to replace a thing whose existence we just failed to establish
    # is the same wrong claim in the other direction.
    "unavailable":      ("Could not be checked just now", "warn", "Save"),
}


# WHICH BROWSER HINT GOES WITH WHICH INPUT TYPE — a table, not a branch inside the renderer.
# It is keyed by the HTML type the CONTRACT gives, never by which step is being drawn.
_AUTOCOMPLETE = {"email": "email"}


def _setup_field(f: dict, value: str = "") -> str:
    """One field from the contract. THE CONTRACT DECIDES THE TYPE, never this file — a password
    rendered as a text input is a credential shown over somebody's shoulder."""
    style = ('font:inherit;font-size:max(16px, calc(17 * var(--px, 1px)));padding:12px 14px;width:100%;'
             'border:1px solid var(--line);border-radius:12px;'
             'background:var(--card);color:var(--ink)')
    kind = str(f.get("type") or "text")
    if kind == "select":
        # A CHOICE THE CONTRACT DECLARED, e.g. where the buyer's email lives. The first option is
        # the default unless the buyer already picked one (a refused save keeps their choice).
        chosen = value or (f.get("options") or (("", ""),))[0][0]
        opts = "".join(f'<option value="{_esc(k)}"{" selected" if k == chosen else ""}>{_esc(v)}</option>'
                       for k, v in f.get("options") or ())
        return (f'<label style="display:block;margin-top:10px">'
                f'<span class="t" style="display:block;font-size:calc(14.5 * var(--px, 1px));margin-bottom:4px">'
                f'{_esc(f.get("label"))}</span>'
                f'<select name="{_esc(f.get("name"))}" style="{style};appearance:auto">{opts}</select>'
                f'</label>')
    return (f'<label class="fld-{_esc(f.get("name"))}" style="display:block;margin-top:10px">'
            f'<span class="t" style="display:block;font-size:calc(14.5 * var(--px, 1px));margin-bottom:4px">'
            f'{_esc(f.get("label"))}</span>'
            f'<input type="{_esc(kind)}" name="{_esc(f.get("name"))}" '
            f'value="{_esc(value) if kind != "password" else ""}" '
            f'placeholder="{_esc(f.get("placeholder") or "")}" '
            f'autocomplete="{_AUTOCOMPLETE.get(kind, "off")}" spellcheck="false" '
            f'style="{style}"></label>')


def _setup_consent(e: dict) -> str:
    """Both vendors' terms, one click away, at the moment a person is deciding.

    IT INFORMS; IT DOES NOT GATE. Owner, 2026-09-18: "We are not policing people's usage of their
    own property... they can review their own fucking terms... we're going above and beyond by
    putting a link to Anthropic terms and to OpenAI terms right there on the page." Nothing here
    is `required`, nothing is withheld if the box is left unticked, and the store refuses nothing.
    The buyer owns this machine; what we owe them is the information, not a permission slip.

    RENDERED ONLY FOR A STEP THAT DECLARES ONE, so this stays a contract like every other part of
    the set-up screen rather than a branch on a step's name. A step with no `consent_field` draws
    nothing and is unchanged.
    """
    field = e.get("consent_field")
    if not field:
        return ""
    warn = e.get("terms_warning") or ""
    links = "".join(
        f'<a href="{_esc(u)}" target="_blank" rel="noopener noreferrer" '
        f'style="color:var(--href)">{_esc(t)}</a>'
        + ('<span class="quiet"> · </span>' if i < len(e.get("terms_links") or ()) - 1 else "")
        for i, (t, u) in enumerate(e.get("terms_links") or ()))
    return (
        '<div style="margin-top:14px;padding:12px 14px;border:1px solid var(--line);'
        'border-radius:12px;background:var(--card)">'
        f'<p class="t" style="margin:0 0 8px;font-size:calc(14.5 * var(--px, 1px));line-height:1.5">{_esc(warn)}</p>'
        + (f'<p style="margin:0 0 10px;font-size:calc(14.5 * var(--px, 1px))">{links}</p>' if links else "")
        + f'<label style="display:flex;gap:10px;align-items:flex-start;font-size:calc(14.5 * var(--px, 1px));'
          f'line-height:1.5;cursor:pointer">'
          f'<input type="checkbox" name="{_esc(field)}" value="yes" '
          f'style="margin-top:3px;flex:none;width:18px;height:18px">'
          f'<span>{_esc(e.get("consent_label") or "")}</span></label>'
        '</div>')


def _setup_progress(steps: list[dict]) -> str:
    """WHERE YOU ARE IN A JOB THAT HAS AN END. Owner, 2026-09-21: a progress bar across the top.

    A SEGMENT PER STEP, NOT A PERCENTAGE. A handful of steps is few enough to draw each
    one, and a
    segment answers the question a percentage dodges: how many are left, and is the one I am
    looking at among them. It carries no labels — the steps below are the labels, and a second
    set of names in a 6px bar is noise a person has to read twice.

    THE NUMBER IS THE HEADLINE AND THE BAR IS THE PICTURE. Screen readers get the sentence and
    skip the bar entirely (aria-hidden), because "2 of 5 connected" is the whole content; a row
    of filled-or-not spans read aloud is worse than silence.

    THE TOTAL IS COUNTED, NEVER WRITTEN. `steps` is `_setup_source()`, which is
    `box_secrets.setup_state()` merged with the seam — the only source (OSDev1, 2026-09-22). It
    read 4 before the AI-coworkers step landed and reads 5 after it; nothing here changed.
    """
    total = len(steps)
    done = sum(1 for e in steps if e.get("status") == "connected")
    segs = "".join(
        f'<span class="seg{" on" if e.get("status") == "connected" else ""}"></span>'
        for e in steps)
    left = total - done
    # WHAT IS LEFT, IN WORDS, because "2 of 4" tells you where you are and not how much further.
    tail = ("Everything is connected." if not left else
            f'{left} to go.' if done else "Nothing connected yet.")
    return (f'<div class="wiz">'
            f'<p class="wiz-n"><b>{done} of {total} connected.</b> {_esc(tail)}</p>'
            f'<div class="wiz-bar" aria-hidden="true">{segs}</div>'
            f'</div>')

def _choice_picker(choice: dict) -> str:
    """A dropdown a STEP declared. The renderer knows nothing about which step it belongs to.

    `test_setup_screen_loop` refuses a `_setup_step` that names a particular step, and it is
    right to — the contract exists so a machine can add a step without editing this file. A
    picker is data, exactly like a field is data.

    DISABLED IS THE HONEST STATE for an option we cannot serve. `core/brain.py` speaks to
    Anthropic and nothing else, so a selectable ChatGPT would take a buyer's `sk-...`, store it,
    and never draft a reply — the fake feature the owner named on his own box on 2026-09-21. The
    greyed entries say where this is going without claiming to have arrived, and
    tests/test_setup_asks_for_the_ai_key.py refuses to let one be marked available unless
    brain.py actually carries a client for it.
    """
    options = choice.get("options") or ()
    if not options:
        return ""
    first_live = next((o["id"] for o in options if o.get("available")), "")
    opts = "".join(
        f'<option value="{_esc(o["id"])}"{"" if o.get("available") else " disabled"}'
        f'{" selected" if o["id"] == first_live else ""}>'
        f'{_esc(o["name"])}{"" if o.get("available") else " — coming soon"}</option>'
        for o in options)
    return ('<div class="card"><div class="setrow">'
            f'<b>{_esc(choice.get("label") or "")}</b>'
            f'<span>{_esc(choice.get("note") or "")}</span></div>'
            '<select aria-label="' + _esc(choice.get("label") or "") + '" '
            'style="width:100%;font:inherit;font-size:max(16px, calc(17 * var(--px, 1px)));padding:12px 14px;'
            'border:1px solid var(--line);border-radius:12px;background:var(--card);'
            'color:var(--ink)">' + opts + '</select></div>')


def _step_extras(e: dict) -> str:
    """WHERE THE CREDENTIAL COMES FROM, one tap away and always live (`help`), and every other
    provider's one line (`alternatives`) with any provider that cannot work said plainly
    (`caveat`). All are contract keys; nothing here knows which step it is drawing. Used by the
    set-up screen and by /inbox/mailbox, so the two cannot drift."""
    out = ""
    help_ = e.get("help") or {}
    if help_.get("url"):
        # THE LINK IS THE CARD'S LAST LINE WHEN NOTHING FOLLOWS IT, and the card is padded 2px top
        # and bottom because its rows carry their own. On the social accounts page it sat 2px from
        # the card's rounded edge (measured at 390, 2026-09-24), so it gets the row's own room.
        _end = "0" if e.get("alternatives") else "14px"
        out += (f'<p style="margin:10px 0 {_end}"><a href="{_esc(help_["url"])}" target="_blank" '
                f'rel="noopener noreferrer" style="color:var(--href);font-weight:var(--w-strong)">'
                f'{_esc(help_.get("label") or "")} &rarr;</a></p>')
    if e.get("alternatives"):
        out += ('<details style="margin-top:12px"><summary><b>'
                + _esc(e.get("alternatives_title") or "Other providers") + '</b></summary>'
                + "".join(f'<p class="quiet" style="margin:8px 0 0"><b>{_esc(a)}:</b> {_esc(b)}</p>'
                          for a, b in e["alternatives"])
                + (f'<p class="quiet" style="margin:8px 0 0"><b>{_esc(e.get("caveat_title") or "")}'
                   f':</b> {_esc(e.get("caveat") or "")}</p>' if e.get("caveat") else "")
                + '</details>')
    return out


def _setup_step(n: int, e: dict, *, note: str = "", typed: dict | None = None,
                owner: bool, primary: bool = True) -> str:
    """ONE ENTRY, RENDERED THE SAME WAY WHATEVER IT IS. This is the whole point of the contract:
    two vendors, two kinds of secret, one shape — a number, a title, why it is wanted, what is
    set, the instructions, the fields, and whatever the buyer can press."""
    typed = typed or {}
    # WHATEVER THE ENTRY DECLARED, and nothing about which entry it is.
    picker = _choice_picker(e["choose"]) if e.get("choose") else ""
    label, tone, verb = _SET_STATUS.get(e.get("status"), _SET_STATUS["not_connected"])
    who = e.get("who") or ""
    said = f'{label} — {who}' if (who and e.get("status") != "not_connected") else label
    # NEVER THE SAME THING TWICE IN ONE BREATH. The status line is a label plus the machine's
    # sentence, and sometimes the sentence already IS the label — core's own `unavailable` text
    # reads "This could not be checked just now…" under a label that says "Could not be checked
    # just now". Rendered, that is a stutter. The sentence wins when it contains the label,
    # because the sentence is the half that says what to do about it.
    detail = str(e.get("detail") or "")
    if detail and label.lower().rstrip(".") in detail.lower():
        said, detail = detail, ""
    steps = "".join(f'<div class="row"><span class="n">{i}</span>'
                    f'<span class="t">{_esc(t)}</span></div>'
                    for i, t in enumerate(e.get("steps") or (), 1))
    steps += _step_extras(e)
    fields = "".join(_setup_field(f, typed.get(f.get("name"), "")) for f in e.get("fields") or ())
    # BELOW THE FIELD, ABOVE SUBMIT — where the owner asked for it, and where a person reads it
    # before they press anything rather than after.
    fields += _setup_consent(e)

    link = e.get("link") or {}
    if link and link.get("enabled"):
        # A NEW TAB, because the consent lives on the vendor's screens and the buyer should keep
        # this page. `rel=noopener` is not optional on a target=_blank we did not write.
        out = (f'<p style="margin:12px 0 0"><a class="btn" href="{_esc(link.get("url"))}" '
               f'{"target=_blank rel=noopener" if link.get("new_tab") else ""}>'
               f'{_esc(link.get("label"))}</a></p>'
               f'<p class="quiet" style="margin-top:8px">{_esc(link.get("after") or "")}</p>')
    elif link:
        # DRAWN, AND PLAINLY DEAD. A button that silently does nothing is worse than no button;
        # one that says why it is waiting is an instruction. `aria-disabled` and no href, because
        # a disabled <a> is not a thing the platform has.
        out = (f'<p style="margin:12px 0 0"><span class="btn" aria-disabled="true" '
               f'style="opacity:.45;pointer-events:none;display:inline-block">'
               f'{_esc(link.get("label"))}</span></p>'
               f'<p class="quiet" style="margin-top:8px">'
               f'{_esc(link.get("disabled_because") or "")}</p>')
    else:
        out = ""

    # A FINISHED STEP FOLDS SHUT. Until today every step printed its four Google instructions
    # whether or not the buyer had already followed them, so a box with three of four connected
    # was a wall of things not to do — and the one step still owed was somewhere inside it.
    # Owner, 2026-09-21, asking for a wizard and for readability; OSDev1 named this one directly.
    #
    # `<details>` AND NOT A SCRIPT. This app's screens run without client JavaScript on purpose
    # (the theme is a cookie for the same reason), and open/closed is exactly what the element
    # is for: it keeps the content in the page for search and for a screen reader, and it needs
    # no state of ours. The summary carries the verb, so the way back in is named rather than
    # discovered — "Change" on a step that is done, which is the only thing left to do to it.
    done_ = e.get("status") == "connected"
    head_ = (f'<h1 style="font-size:calc(19 * var(--px, 1px))">{n}. {_esc(e.get("title"))}</h1>'
             f'<p class="quiet" style="margin:2px 0 0">{_esc(e.get("why"))}</p>'
             f'<p class="quiet" style="margin:6px 0 0"><b class="{_esc(tone)}">{_esc(said)}</b>'
             + (f' — {_esc(detail)}' if detail else "") + '</p>')
    if done_:
        # THE TICK IS DECORATION AND THE WORD IS THE CONTENT. "Connected" is already in `said`;
        # a tick that a screen reader also announces would say it twice.
        head_ = (f'<summary class="stepsum">'
                 f'<span class="tick" aria-hidden="true">\u2713</span>'
                 f'<span class="stepsum-t"><b>{n}. {_esc(e.get("title"))}</b>'
                 f'<span class="stepsum-s">{_esc(said)}'
                 + (f' — {_esc(detail)}' if detail else "") + '</span></span>'
                 f'<span class="stepsum-v" aria-hidden="true">Change</span>'
                 f'</summary>')
    tag = "details" if done_ else "section"
    return (f'<{tag} id="{_esc(e.get("key"))}" class="wstep{" done" if done_ else ""}" '
            f'style="margin-top:26px">'
            + head_
            # ABOVE THE INSTRUCTIONS, because it decides which set of them applies.
            # DECLARED BY THE STEP (#1410, OSDev1) and drawn by the renderer, which is
            # why a folded step carries its picker shut with it and this file still
            # names no step key — test_setup_screen_loop refuses one that does.
            + picker
            # THE ONE-PRESS ROUTE FIRST, ABOVE THE INSTRUCTIONS FOR THE LONG WAY ROUND. A step
            # that declares `action_href` has a door a buyer can simply walk through; drawing it
            # under four numbered steps would hide the easy path behind the hard one. A step that
            # declares none renders exactly as it did — this is a contract, not a special case.
            #
            # AND NOT DRAWN AT ALL FOR SOMEBODY WHO WOULD BE REFUSED AT IT. Connecting an AI
            # account bills the whole box, so its door answers 403 to a member —
            # and this card was offering them the button anyway. A door you are shown and then
            # turned away from reads as the product being broken, not as a permission you lack.
            # Caught by test_a_buyer_can_walk_every_screen, which walks as a member on purpose.
            + (f'<div class="card">'
               f'<p style="margin:0 0 10px">{_esc(e.get("action_why") or "")}</p>'
               f'<p style="margin:0"><a class="btn" href="{_esc(e.get("action_href"))}">'
               f'{_esc(e.get("action_label") or "Connect")}</a></p></div>'
               if e.get("action_href") and owner else "")
            + (f'<div class="card">{steps}</div>' if steps else "")
            + (f'<p class="quiet">{_esc(e.get("note"))}</p>' if e.get("note") else "")
            + note
            + (f'<form class="compose" method="post" action="/inbox/setup" '
               'style="display:flex;flex-direction:column;gap:2px;align-items:stretch">'
               f'<input type="hidden" name="step" value="{_esc(e.get("key"))}">'
               f'{fields}<p style="margin:12px 0 0">'
               # ONE INK PILL PER SCREEN: the step to do next carries it, every other step's
               # button is the outline (OSDev0's drift check, 2026-09-24: two ink Saves here).
               f'<button class="btn{"" if primary else " ghost"}" type="submit">{verb}</button>'
               '</p></form>' if fields else "")
            + out + f'</{tag}>')


# ── SET-UP IS A GUIDE, NOT A SETTINGS PAGE ──────────────────────────────────────────────────────
# Owner, 2026-09-24 (relayed by OSDev1): "there's only one place to add a key or change a setting",
# and "I don't think a setup tab should have actual settings on it … set up tab should be more like
# a guide or a wizard." docs/SCOPE_ONE_PLACE_PER_SETTING.md holds the research: a checklist that
# links to each setting's one home and brings the buyer back (Shopify's setup guide, Stripe's
# account checklist, Wix, GOV.UK's task list), with status read from the box, never ticked.
#
def _safe_setup_source() -> list:
    try:
        return _setup_source()
    except Exception:                                    # noqa: BLE001 — a post decides nothing
        return []


def _from_setup() -> bool:
    """Did the buyer arrive from set-up? Carried as `from=setup` on the link and in the form."""
    try:
        return str(request.values.get("from") or "") == "setup"
    except Exception:                                    # noqa: BLE001 — outside a request: no
        return False


def _trip_field() -> str:
    """The hidden field that carries the return trip through a form post."""
    return '<input type="hidden" name="from" value="setup">' if _from_setup() else ""


def _setup_way_back() -> str:
    """On a home page past its first form, the way back to set-up, when that is where they came from."""
    if not _from_setup():
        return ""
    return ('<p style="margin-top:14px"><a href="/inbox/setup" style="color:var(--href)">'
            '&larr; Back to set-up</a></p>')


def _back_link() -> str:
    """Back to set-up when that is where the buyer came from, otherwise back to Settings."""
    if _from_setup():
        return ('<p style="margin-top:14px"><a href="/inbox/setup" style="color:var(--href)">'
                '&larr; Back to set-up</a></p>')
    return ('<p style="margin-top:14px"><a href="/inbox/settings" style="color:var(--href)">'
            '&larr; Settings</a></p>')


def _has_home(e: dict) -> bool:
    """Does this step have a page of its own? A step a machine registered through core's seam
    without declaring a `home` does not, so set-up stays its one home (its form renders there)
    until it gets one: nothing a buyer can set today stops being settable."""
    return str(e.get("key") or "") in _SETUP_HOMES or bool(e.get("home"))


def _setup_home(e: dict) -> tuple:
    """(href, verb, required) for a step: this machine's table, or what a seam step declared."""
    key = str(e.get("key") or "")
    if key in _SETUP_HOMES:
        return _SETUP_HOMES[key]
    return (str(e.get("home") or "/inbox/settings"),
            str(e.get("action_label") or "Set this up"), bool(e.get("required", True)))


def _guide_row(n: int, e: dict) -> str:
    """ONE ROW: the whole row is the link to the step's home (GOV.UK's task list). Status is plain
    text, never a button-shaped chip, because people tap chips (GOV.UK's research finding)."""
    label, tone, _verb = _SET_STATUS.get(e.get("status"), _SET_STATUS["not_connected"])
    who = e.get("who") or ""
    done = e.get("status") == "connected"
    said = f"{label} — {who}" if (who and e.get("status") != "not_connected") else label
    href, _verb2, required = _setup_home(e)
    go = f'{href}{"&" if "?" in href else "?"}from=setup'
    mark = "\u2713" if done else str(n)
    return (f'<a class="grow{" done" if done else ""}" id="{_esc(e.get("key"))}" href="{_esc(go)}">'
            f'<span class="gn" aria-hidden="true">{mark}</span>'
            f'<span class="gt"><b>{_esc(e.get("title"))}</b>'
            f'<span class="gs {_esc(tone)}">{_esc(said)}</span></span>'
            # A DONE ROW NAMES THE WAY BACK INTO IT; an open optional one says it can wait.
            + ('<span class="gtag">Change</span>' if done else
               "" if required else '<span class="gtag">Optional</span>')
            + '<span class="chev" aria-hidden="true">&rsaquo;</span></a>')


@blueprint.route("/inbox/setup", methods=["GET", "POST"])
def r_setup():
    """EVERY CREDENTIAL THE BUYER SUPPLIES, ON ONE SCREEN, IN THE OWNER'S ORDER.

    Assigned by OSDev1 (2026-09-16): render the steps as a LOOP — Gmail first, Zernio second, the
    link out drawn disabled until the key is in — rather than three bespoke flows that drift apart.
    Then (17:20): render them from the seam, `core.onboarding.steps()`, with no step-specific code.

    NOTHING IN THIS FUNCTION KNOWS WHAT A STEP IS — no key is named in it, and no key is named in
    the renderer either. A machine that registers a third credential gets a screen for it with no
    change at all here. The steps with a page of their own are named once, in `_SETUP_HOMES`.

    THE WRITE GOES THROUGH CORE, which hands the machine only the fields its step declared — so a
    hidden field posted from a crafted form cannot reach a store. A rejection carries a sentence
    for the person in front of the screen, and that sentence is shown against the step it came
    from rather than at the top of the page, where a buyer with two forms open cannot tell which
    one it is about.
    """
    gate = _gate()
    if gate is not None:
        return gate
    try:
        _u = dash.session_user(request) or {}
    except Exception:                            # noqa: BLE001 — an unreadable session decides
        _u = {}                                  # only whose name the audit line carries
    whoami = _u.get("id")

    # A POST SAVES ONLY A STEP WHOSE ONE HOME IS THIS PAGE: a seam step with no page of its own
    # (see _has_home). Anything else (an old page left open, a cached form, a crafted request)
    # saves nothing and lands back on the guide. Every other setting is written on its home.
    notes: dict = {}
    typed: dict = {}
    if request.method == "POST":
        which = str(request.form.get("step") or "")
        homeless = {str(e.get("key")) for e in _safe_setup_source() if not _has_home(e)}
        if which not in homeless:
            return redirect("/inbox/setup", code=303)
        typed = {k: str(v) for k, v in request.form.items() if k != "step"}
        try:
            _setup_save(which, request.form, user_id=whoami)
            return redirect(f"/inbox/setup#{which}")
        except _setup_rejections() as e:
            notes[which] = (f'<p class="quiet" style="color:var(--accent);margin-top:10px">'
                            f'{_esc(str(e))}</p>')
        typed = {k: v for k, v in typed.items() if k != "password" and k != "key"}

    try:
        steps = _setup_source()
    except Exception as e:                       # noqa: BLE001 — the set-up page outranks the cause
        log.warning("voice.setup_unreadable", extra={"error": f"{type(e).__name__}: {e}"[:160]})
        return _shell('<h1>Set-up</h1><div class="quiet">This box could not read its own set-up '
                      'list. Nothing you have already connected is affected.</div>',
                      here="/inbox/setup"), 200

    # PROGRESS COUNTS WHAT IS REQUIRED, and says what is optional out loud (Shopify's guidelines;
    # an optional step left undone is not a failure). Read from the box every time, never ticked.
    req = [e for e in steps if _setup_home(e)[2]]
    req_done = sum(1 for e in req if e.get("status") == "connected")
    opt_left = [e for e in steps if not _setup_home(e)[2] and e.get("status") != "connected"]
    finished = req_done == len(req)
    if finished:
        head = ('<h1>You are set up.</h1><p class="quiet">Your box is reading your messages. '
                + ('The rest is optional: add it whenever you like.' if opt_left else
                   'Everything is connected. Change any of it from here or from Settings.')
                + '</p>')
    else:
        # ONE COUNT ON THE PAGE, and it is the owner's progress bar (2026-09-21) just below: the
        # heading says what the page is, not a second number (walk #6, "one honest count").
        head = ('<h1>Set up your inbox.</h1><p class="quiet">Each step opens its own page and '
                'brings you back here.'
                + (' Anything marked Optional can wait.' if opt_left else '') + '</p>')

    # WHAT JUST HAPPENED, confirmed from the box's own state rather than from the link that
    # brought them back (Stripe: returning proves nothing until you check).
    just = str(request.args.get("done") or "")
    back_ok = next((e for e in steps if e.get("key") == just and e.get("status") == "connected"),
                   None)
    note = (f'<div class="card"><p style="margin:0"><b>{_esc(back_ok.get("title"))}</b> '
            'is connected.</p></div>' if back_ok else "")

    # THE ONE INK PILL: the next required step, at the top where a thumb is (the research's
    # "next up"). With everything required done there is no next thing and no pill.
    nxt = next((e for e in req if e.get("status") != "connected"), None)
    cta = ""
    if nxt is not None:
        href, verb, _r = _setup_home(nxt)
        cta = (f'<p style="margin:16px 0 4px"><a class="btn" '
               f'href="{_esc(href)}{"&" if "?" in href else "?"}from=setup">{_esc(verb)}</a></p>')

    body = (head + _setup_progress(steps) + note + cta
            + '<div class="guide">' + "".join(
                _guide_row(i, e) if _has_home(e) else
                _setup_step(i, e, note=notes.get(e.get("key"), ""), typed=typed,
                            owner=(_u.get("role") or "") == "owner", primary=False)
                for i, e in enumerate(steps, 1))
            + '</div>'
            + '<p style="margin-top:22px"><a href="/inbox/settings" '
              'style="color:var(--href)">&larr; Settings</a></p>')
    return _shell(body, here="/inbox/setup"), 200


# NOT `@blueprint.post`. tests/test_customer_voice.py scans this department for a CALL named
# `post`, and a decorator is a call — the same two extra characters r_drafts spends.
def _mailbox_for_member(st: dict) -> str:
    """The mailbox screen as a member sees it: what the box is reading, and whose it is to change."""
    status, who = st.get("status"), st.get("user") or ""
    if status == "needs_reauth":
        said = (f'Google is refusing the app password for <b>{_esc(who)}</b>, so nothing new '
                'from this inbox is arriving until the owner replaces it.')
    elif status == "admin_disabled":
        said = ('App passwords are switched off for this Google organisation, so this inbox '
                'cannot be read.')
    elif who:
        said = (f'Ownbox is reading <b>{_esc(who)}</b>. It sends only the replies you send, and '
                'never marks a message read.')
    else:
        said = 'No inbox is connected to this box yet.'
    return (f'<h1>Your inbox</h1><div class="card"><div class="setrow"><span>{said}</span>'
            + _OWNER_ONLY + '</div></div>' + _back_link())


@blueprint.route("/inbox/mailbox", methods=["GET", "POST"])
def r_mailbox():
    """WHERE A BUYER CONNECTS THEIR INBOX, and until now there was nowhere.

    `box_secrets.put_email()` shipped with EXACTLY ONE OCCURRENCE IN THE REPOSITORY — its own
    definition. Measured across every live branch, 2026-09-16: no screen, no route and no script
    ever called it, so the mailbox credential could not be set by anyone without a shell, and the
    buyer has no shell. Meanwhile a bare box's Settings offered an AI key, a theme and an install
    guide: three things, none of which connect anything.

    THE PASSWORD IS WRITTEN AND NEVER READ BACK, exactly as the AI key is. `email_state()` is what
    a screen may see — a status, an address, and a sentence — and it is documented never to carry
    the password. The form is empty on every render, including when a credential is set.

    "SAVED" IS NOT "CONNECTED", AND THIS SCREEN WILL NOT CONFLATE THEM. `put_email` writes
    `status=connected` after a successful WRITE, not a successful LOGIN — nothing has spoken to
    Google at that point. So a fresh save says the box will try it, and the poller's own verdict
    (`note_email_status`) is what turns this screen into a claim about Google. A screen that said
    "Connected" the instant a password was pasted would be wrong for every typo.
    """
    from core import box_secrets
    gate = _gate()
    if gate is not None:
        return gate
    try:
        _u = dash.session_user(request) or {}
    except Exception:                            # noqa: BLE001 — an unreadable session decides
        _u = {}                                  # only whose name the audit line carries
    whoami = _u.get("id")

    # THE MAILBOX IS THE OWNER'S TO CHANGE (owner, 2026-09-24: "Owner only"). A member reads its
    # state and nothing else; a member's POST changes nothing.
    owner = _is_owner()
    if request.method == "POST" and not owner:
        return _owner_refusal()
    if not owner:
        return _shell(_mailbox_for_member(box_secrets.email_state()), here="/inbox/mailbox"), 200

    if request.method == "POST" and request.form.get("off"):
        # A POST, never the old ?off=1 link (see `_post_button`).
        # STOPPING IS AS REAL A CONTROL AS STARTING. The status rows go with the credential: a
        # left-behind "connected" would have this screen reporting on a mailbox it no longer reads.
        #
        # `clear_email` RATHER THAN THE ROWS BY NAME. This listed them one by one, so every row
        # added to a mailbox connection since has been a row somebody had to remember to add here
        # too — and the store is where that list belongs, beside `clear_zernio`, which has always
        # worked this way.
        box_secrets.clear_email(user_id=whoami)
        return redirect("/inbox/mailbox", code=303)

    note, typed = "", ""
    if request.method == "POST":
        typed = str(request.form.get("user") or "")
        try:
            # VALIDATED IN THE STORE, NOT HERE, so the rule is the same whoever writes one. This
            # screen's job is to show the sentence the store wrote for the person in front of it.
            box_secrets.put_email(host=_mailbox_host(request.form), user=typed,
                                  password=str(request.form.get("password") or ""),
                                  user_id=whoami)
            # THE RETURN TRIP: from set-up, saving goes straight back to the guide, which checks
            # the box's own state and says so (docs/SCOPE_ONE_PLACE_PER_SETTING.md).
            if _from_setup():
                return redirect("/inbox/setup?done=email#email", code=303)
            return redirect("/inbox/mailbox?saved=1")
        except box_secrets.SecretRejected as e:
            note = (f'<p class="quiet" style="color:var(--accent)">{_esc(str(e))}</p>')

    st = box_secrets.email_state()
    status, who = st.get("status"), st.get("user") or ""
    detail = str(st.get("detail") or "").strip()

    if status == "needs_reauth":
        body = ('<h1>Google is refusing that password.</h1>'
                f'<p class="quiet">Ownbox cannot sign in to <b>{_esc(who)}</b> any more. Google '
                'revokes an app password whenever the account password changes, so this is '
                'usually what happened — make a new one and paste it here.</p>'
                + (f'<p class="quiet">Google said: {_esc(detail)}</p>' if detail else "")
                # SAME ORDER, AND THE CASE IS STRONGER HERE: this person HAS had a working app
                # password, so they know the drill and may already have made the replacement.
                # Making them scroll past the tutorial they no longer need, to reach the field
                # they came for, is the fold bug with an extra insult on top.
                + _mailbox_form(user=who, note=note, verb="Use this password instead")
                + _mailbox_no_password_yet()
                + _mailbox_steps()
                + _back_link())
    elif status == "admin_disabled":
        body = ('<h1>Your administrator has switched this off.</h1>'
                '<p class="quiet">App passwords are turned off for your Google organisation, so '
                'nobody in it can make one. An administrator can allow them again in the Google '
                'Admin console; until then Ownbox cannot read this inbox, and nothing else about '
                'your box is affected.</p>'
                + (f'<p class="quiet">Google said: {_esc(detail)}</p>' if detail else "")
                + _back_link())
    elif who:
        saved = request.args.get("saved")
        body = ('<h1>Your inbox is set.</h1>'
                f'<p class="quiet">Ownbox is set to read <b>{_esc(who)}</b>. '
                + ("It will try that password on the next check — if Google refuses it, this "
                   "screen says so and tells you what to do."
                   if saved else
                   "If Google ever refuses the password, this screen says so and tells you what "
                   "to do.")
                + ' It sends only the replies you send, and never marks a message read.'
                  '</p>'
                '<div class="card"><div class="setrow"><b>Change the password</b>'
                '<span>Make a new app password in Google and paste it here. The address stays '
                'the same unless you change it too.</span></div></div>'
                + _mailbox_form(user=who, note=note, verb="Save this password")
                + '<p style="margin-top:16px">'
                + _post_button("/inbox/mailbox", "off", "1", "Stop reading this inbox") + '</p>'
                + _back_link())
    else:
        body = ('<h1>Connect your inbox.</h1>'
                '<p class="quiet">Ownbox reads the mail your customers send you, and drafts '
                'replies. It sends only the replies you send, and never marks a message read.</p>'
                '<p class="quiet">Google will not take your ordinary password for this, and it '
                'should not — an <b>app password</b> is sixteen letters that only Ownbox uses and '
                'that you can revoke on its own, without changing anything else.</p>'
                # THE FORM IS ABOVE THE INSTRUCTIONS. Measured in a real browser at 390x844 by
                # OSDev5 and reproduced here: with the four steps first, the email field sat at
                # y=809 in an 844px viewport — below the fold and behind the tab bar, with 154
                # words above it. A buyer read a wall of Google instructions and never learned
                # there WAS a form until they scrolled, on the screen that is step one of set-up.
                # Same browser, same viewport, after the swap: y=398.
                #
                # IT SERVES BOTH PEOPLE WHO ARRIVE HERE. Somebody who already has an app password
                # — the returning buyer, and anyone who read the instructions on a laptop — can
                # paste it without reading anything. Somebody who does not gets the line below
                # and the steps directly under it, which is the shape `/inbox/connect` already
                # uses and which measures at y=294.
                + _mailbox_form(user=typed, note=note)
                + _mailbox_no_password_yet()
                + _mailbox_steps()
                + _back_link())
    return _shell(body, here="/inbox/mailbox"), 200


# ── B1: where a buyer connects the accounts the inbox reads from ────────────────────────────
# THE PRODUCT SHIPPED WITHOUT THIS. Until today the only connect link in the system was minted by
# the provisioner and shown on the build page while the order was still building; a delivered box
# had no connect surface of any kind. That is the gap this closes.
#
# NOT `@blueprint.post` and not `@blueprint.get` on the POST route, for the same reason r_drafts
# spends the same two characters: tests/test_customer_voice.py bans a CALL named `post` in this
# department, which is the structural guarantee that Customer Voice reads and drafts but cannot
# publish. A decorator is a false positive on that scan and the guard is worth more than the
# characters.

# WHAT A PERSON CAN CONNECT HERE, and it is deliberately the channels the box actually INGESTS
# rather than everything the vendor supports. Offering a person a channel the poller never reads
# is a button that appears to work, takes a real OAuth grant, and delivers silence.
# `channels.POLLED` is the authority; these are its vendor tokens with a human name each.
_CONNECTABLE = (("instagram", "Instagram"), ("facebook", "Messenger"), ("whatsapp", "WhatsApp"))
# WHAT A CHANNEL NEEDS BEFORE ITS BUTTON CAN WORK, said beside the button rather than discovered at the vendor.
_CONNECT_NEEDS = {"whatsapp": "Needs a WhatsApp Business account."}


def _connect_space():
    """The Space this box connects FOR, as a resolved binding — key and profile together."""
    from core import spaces
    return spaces.space_by_name(_space()) or {}


def _connect_return(platform: str) -> str:
    """Where the vendor sends the person back to: THIS box, on this host.

    NOT ownbox.io/building. That is the provisioner's redirect and it is a live 404 (measured
    2026-09-16, OSDev4/OSDev1 on the wall) — but even once it exists it is the wrong destination
    for this flow, because a person who is signed into their own box should come back to their own
    box, not to a build page for an order that finished weeks ago."""
    root = str(request.host_url or "").rstrip("/")
    return f"{root}/inbox/connect?connected={platform}"


def _resolve_profile(z, brand_name: str):
    """The Profile inside the BUYER's account that this box's channels hang under.

    (profile_id, choices) — `choices` is non-empty ONLY when the account holds more than one and
    a person has to say which. We do not guess in that case: a Zernio account with several folders
    is one being used for something else too, and picking the first would file the buyer's own
    Instagram under a folder they keep for a client.

    ONE FOLDER OR NONE IS THE ORDINARY CASE and needs no question — a fresh account has one, and
    an empty account gets one made, named for their brand so it is recognisable when they look at
    it from the vendor's own screen."""
    from core import box_secrets
    found = z.connect.profiles()
    if len(found) == 1:
        pid = found[0]["id"]
        box_secrets.put_zernio_profile(pid)
        return pid, []
    if not found:
        pid = z.connect.create_profile(brand_name or "Ownbox")
        box_secrets.put_zernio_profile(pid)
        return pid, []
    return None, found


@blueprint.route("/inbox/connect", methods=["GET", "POST"])
def r_connect():
    """Connect a social account to this box — or say, in one sentence, why it did not work.

    EVERY VENDOR FAILURE LANDS AS A SENTENCE ON THIS PAGE, never as a 500 and never as silence.
    A person is standing here having just clicked something; the one thing that must not happen
    is a blank page that leaves them unable to tell whether it worked."""
    from core import box_secrets
    from core.vendors import zernio
    gate = _gate()
    if gate is not None:
        return gate
    try:
        _u = dash.session_user(request) or {}
    except Exception:                            # noqa: BLE001 — an unreadable session is not a
        _u = {}                                  # reason to refuse a buyer their own account
    whoami = _u.get("id")

    # THE SOCIAL ACCOUNTS ARE THE OWNER'S TO CHANGE (owner, 2026-09-24: "Owner only"). A member
    # reads the stored state, with no call to Zernio, and a member's POST changes nothing.
    if not _is_owner():
        if request.method == "POST":
            return _owner_refusal()
        return _shell(_connect_for_member(box_secrets.zernio_state()), here="/inbox/connect"), 200

    if request.method == "POST" and request.form.get("do") in ("stop", "read", "pick"):
        return _connect_change(request.form, whoami)

    if request.method == "POST" and request.form.get("off"):
        # A POST, never the old ?off=1 link (see `_post_button`).
        # Disconnect. The key AND the profile resolved with it — `clear_zernio` is one call for
        # exactly that reason: a profile id left behind would be handed to the NEXT key pasted in.
        box_secrets.clear_zernio(user_id=whoami)
        return redirect("/inbox/settings", code=303)

    note = ""
    if request.method == "POST":
        try:
            was = box_secrets.zernio_key()
            box_secrets.put_zernio(str(request.form.get("key") or ""), user_id=whoami)
            if was and was != box_secrets.zernio_key():
                # A NEW KEY OPENS ANOTHER ACCOUNT: the accounts picked in the old one are not in it.
                from .inbox import channels as _ch
                _ch.forget_choices(by=whoami)
            return redirect("/inbox/connect?from=setup" if _from_setup() else "/inbox/connect",
                            code=303)
        except box_secrets.SecretRejected as e:
            # Never an echo of what they pasted. Same discipline as the AI key form.
            note = f'<p class="quiet" style="color:var(--accent)">{_esc(str(e))}</p>'

    if not box_secrets.is_set(box_secrets.ZERNIO) or request.args.get("replace") or note:
        # REPLACE KEY (owner 10-04): the same form, while the old key keeps working until a new one is checked.
        return _shell(_connect_key_form(note, replacing=box_secrets.is_set(box_secrets.ZERNIO)),
                      here="/inbox/connect"), 200

    # ── connected: show what is on, and what can still be added ──────────────────────────────
    sp = _connect_space()
    z = zernio.client(sp)
    try:
        brand = str(dash.brand() or "")          # a str, not a dict — core/dash/__init__.py:247
    except Exception:                            # noqa: BLE001 — the name is a nicety here; it
        brand = ""                               # only labels a folder the buyer will recognise
    chosen = request.args.get("profile")
    if chosen:
        # ONLY A WORKSPACE IN THIS ACCOUNT (owner 10-04, Switch workspace): an id from anywhere else is ignored. A new
        # workspace holds other accounts, so the accounts picked in the old one are forgotten.
        try:
            known = {w.get("id") for w in z.connect.profiles()}
        except zernio.ZernioError:
            known = set()
        if chosen in known:
            if chosen != sp.get("zernio_profile_id"):
                from .inbox import channels as _ch
                _ch.forget_choices(by=whoami)
            box_secrets.put_zernio_profile(chosen, user_id=whoami)
        return redirect("/inbox/connect", code=303)

    try:
        pid, choices = (sp.get("zernio_profile_id"), [])
        if not pid:
            pid, choices = _resolve_profile(z, brand)
        if choices:
            return _shell(_connect_profile_chooser(choices) + _setup_way_back(), here="/inbox/connect"), 200
        if request.args.get("workspace"):
            # SWITCH WORKSPACE (owner 10-04): every workspace in his account, the one in use marked.
            return _shell(_connect_profile_chooser(z.connect.profiles(), current=pid), here="/inbox/connect"), 200
        sp = dict(sp, zernio_profile_id=pid)
        z = zernio.client(sp)
        accounts = z.accounts.all()
        workspace = next((w.get("name") for w in z.connect.profiles() if w.get("id") == pid), "") or ""
    except zernio.ZernioError as e:
        # `payment_required` is recorded so SETTINGS can say it too, without a network call.
        detail = str(e)
        if "402" in detail or "payment" in detail.lower():
            box_secrets.note_zernio_status("payment_required", detail, user_id=whoami)
            return _shell(_connect_trouble(
                "Your social account needs a payment method before it will connect any more "
                "channels. Add one there, then come back — nothing here needs changing."),
                here="/inbox/connect"), 200
        log.warning("connect.discover_failed", extra={"err": detail[:200]})
        return _shell(_connect_trouble(
            "Ownbox could not reach your social account just now. Nothing is lost — try again "
            "in a minute."), here="/inbox/connect"), 200

    just = request.args.get("connected") or ""
    return _shell(_connect_page(accounts, just, workspace=workspace) + _setup_way_back(), here="/inbox/connect"), 200


def _connect_for_member(st: dict) -> str:
    """The social accounts screen as a member sees it: the stored state, and whose it is."""
    said = {"not_connected": "No social accounts are connected to this box yet.",
            "payment_required": "The owner's social account needs a payment method before it "
                                "connects any more accounts.",
            "needs_reauth": "Ownbox can no longer reach the owner's social account, so nothing "
                            "new is arriving until they connect it again."}.get(
        st.get("status"), "Connected. New messages arrive on their own.")
    return (f'<h1>Your social accounts</h1><div class="card"><div class="setrow"><span>{said}'
            '</span>' + _OWNER_ONLY + '</div></div>' + _back_link())


@blueprint.get("/inbox/connect/<platform>")
def r_connect_start(platform: str):
    """Send the person to the vendor's consent screen for ONE platform.

    THE URL IS MINTED SERVER-SIDE AND NEVER RENDERED INTO THE PAGE. A consent URL is scoped to
    this box's profile and lives for a while; rendering a dozen of them into HTML puts them in
    history, in a screenshot, and in whatever the browser syncs. This route makes exactly one,
    for the button that was actually pressed, and redirects straight into it."""
    from core.vendors import zernio
    gate = _gate()
    if gate is not None:
        return gate
    if not _is_owner():                          # the social accounts are the owner's
        return _owner_refusal()
    if platform not in {p for p, _ in _CONNECTABLE}:
        # An unknown platform is never passed through to the vendor. The allow-list is the
        # channels the poller reads; anything else would grant access nothing ever collects.
        return redirect("/inbox/connect", code=303)
    sp = _connect_space()
    if not sp.get("zernio_key") or not sp.get("zernio_profile_id"):
        return redirect("/inbox/connect", code=303)
    try:
        url = zernio.client(sp).connect.url(platform, redirect_url=_connect_return(platform))
    except zernio.ZernioError as e:
        log.warning("connect.url_failed", extra={"platform": platform, "err": str(e)[:200]})
        return _shell(_connect_trouble(
            "That channel would not start just now. Nothing is lost — try again in a minute."),
            here="/inbox/connect"), 200
    return redirect(url, code=303)


def _connect_key_form(note: str, *, replacing: bool = False) -> str:
    """State one: no social account connected yet.

    SAME SHAPE AS THE AI KEY FORM ON PURPOSE. A buyer who has done one of these already should
    recognise the second on sight, and the sentence underneath is the same promise in both places:
    the account is theirs, on their bill, and they can take it back."""
    if replacing:
        # REPLACE KEY (owner 10-04): the old key keeps working until this one is checked and saved.
        return (
          '<h1>Replace your Zernio key.</h1>'
          '<div class="card"><div class="setrow">'
          '<span>Paste the new key from your Zernio account. Ownbox checks it with Zernio before it is saved, and '
          'the key in use now keeps working until then. A key from another Zernio account brings that '
          'account&rsquo;s workspace and channels with it.</span></div></div>'
          + note +
          '<form class="compose" method="post" action="/inbox/connect">'
          '<label style="display:block">'
          '<span class="t" style="display:block;font-size:calc(14.5 * var(--px, 1px));margin-bottom:4px">'
          'Your new Zernio key</span>'
          '<input type="password" name="key" autocomplete="off" spellcheck="false"'
          ' aria-label="Paste your key" placeholder="Paste your key" '
          'style="width:100%;font:inherit;font-size:max(16px, calc(17 * var(--px, 1px)));padding:12px 14px;'
          'border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--ink)">'
          '</label>'
          '<button class="btn" type="submit">Save the new key</button>'
          '</form>'
          '<p style="margin-top:14px"><a href="/inbox/connect" style="color:var(--href)">'
          '&larr; Keep the key I have</a></p>')
    return (
      '<h1>Connect your social accounts.</h1>'
      '<div class="card"><div class="setrow">'
      '<span>Ownbox reads your Instagram, Messenger and WhatsApp through your own social account, so the '
      'connection stays yours and you can take it back any day without asking us.</span>'
      # THE SITE ROOT, NOT A GUESSED DEEP LINK. scripts/doctor.py:154 says "zernio.com → API key"
      # and that is the whole of what we actually know; a made-up /settings/api path that 404s in
      # front of a buyer at the exact moment they are trying to find something is worse than one
      # extra click.
      # ONLY WHEN THE STEP'S CONTRACT CARRIES NO BETTER LINK: once it names the API keys page
      # (owner-confirmed, #1506), _connect_contract draws that one and this would be a second.
      + ('' if (_step_spec("zernio").get("help") or {}).get("url") else
         '<p style="margin:10px 0 0"><a href="https://zernio.com" target="_blank" '
         'rel="noopener" style="color:var(--href)">Where to find your key &rarr;</a></p>')
      + '</div></div>'
      + _connect_contract()
      + note +
      '<form class="compose" method="post" action="/inbox/connect">' + _trip_field() +
      # LABELLED, like the mailbox form: the placeholder is gone the moment they paste.
      '<label style="display:block">'
      '<span class="t" style="display:block;font-size:calc(14.5 * var(--px, 1px));margin-bottom:4px">'
      'Your Zernio API key</span>'
      '<input type="password" name="key" autocomplete="off" spellcheck="false"'
      ' aria-label="Paste your key" placeholder="Paste your key" '
      'style="width:100%;font:inherit;font-size:max(16px, calc(17 * var(--px, 1px)));padding:12px 14px;'
      'border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--ink)">'
      '</label>'
      '<button class="btn" type="submit">Continue</button>'
      '</form>'
      '<p class="quiet" style="margin-top:12px">Checked with your provider before it is saved, '
      'so you find out here if it is wrong — not tomorrow, from an empty inbox.</p>'
      + _back_link())


def _connect_contract() -> str:
    """THE SOCIAL STEP'S OWN INSTRUCTIONS, ON ITS ONE HOME. They used to live only on set-up; now
    set-up is a guide that links here (docs/SCOPE_ONE_PLACE_PER_SETTING.md), so this page draws the
    same contract the guide's row reads: the steps, the help link and the note."""
    e = _step_spec("zernio")
    if not e:
        return ""
    steps = "".join(f'<div class="row"><span class="n">{i}</span>'
                    f'<span class="t">{_esc(t)}</span></div>'
                    for i, t in enumerate(e.get("steps") or (), 1))
    return ((f'<div class="card">{steps}{_step_extras(e)}</div>' if steps else "")
            + (f'<p class="quiet" style="margin-top:10px">{_esc(e.get("note"))}</p>'
               if e.get("note") else ""))


def _connect_profile_chooser(choices: list, *, current: str | None = None) -> str:
    """Their account holds several folders, so they say which one; or they asked to switch (owner 10-04), and the one
    in use is marked."""
    rows = "".join(
        f'<p style="margin:10px 0 0"><a class="btn{" ghost" if c["id"] == current else ""}" '
        f'href="/inbox/connect?profile={_esc(c["id"])}">{_esc(c["name"] or "Untitled")}'
        f'{" (in use)" if c["id"] == current else ""}</a></p>'
        for c in choices)
    return (
      '<h1>Which one is this business?</h1>'
      '<div class="card"><div class="setrow">'
      '<span>Your social account keeps more than one workspace. Pick the one this box is for — '
      'Ownbox will only ever read the channels inside it.</span>'
      f'{rows}</div></div>'
      '<p style="margin-top:14px"><a href="/inbox/settings" style="color:var(--href)">'
      '&larr; Settings</a></p>')


def _connect_names(accounts: list, platform: str) -> list:
    return [a for a in accounts if a.get("platform") == platform]


def _platform_button(action_value: str, label: str, platform: str) -> str:
    """A per-channel switch: `_post_button`, carrying which channel it is for."""
    return (f'<form method="post" action="/inbox/connect" class="inline">'
            f'<input type="hidden" name="platform" value="{_esc(platform)}">'
            f'<button type="submit" class="ghost txt" name="do" value="{_esc(action_value)}">{_esc(label)}</button>'
            '</form>')


def _connect_page(accounts: list, just: str, *, workspace: str = "") -> str:
    """State two: connected. A row per channel naming the account it reads, with Change and Disconnect; the channels
    still to add; iMessage, honestly; and the Zernio account itself (owner 10-04: "I should be able to edit settings,
    similar to how GSC functions").

    IT SAYS WHAT IS ON BEFORE IT OFFERS WHAT IS NOT. A person arriving back from a consent screen
    has one question — did that work — and the answer is the first thing on the page.

    DISCONNECT ON A CHANNEL STOPS OWNBOX READING IT; it never removes the account from Zernio, where other machines
    may post through it (channels.stop). Disconnecting Zernio itself is the last row, and stops everything."""
    from .inbox import channels as _ch
    stopped, picked = _ch.stopped(), _ch.chosen()
    done = ""
    if just:
        label = dict(_CONNECTABLE).get(just, just.title())
        # LIVE, NOT THE QUERY STRING. A `?connected=` in the URL is whatever the browser was
        # handed; the only honest confirmation is the account list the vendor just returned.
        done = ('<p class="quiet" style="color:var(--accent)">'
                + _esc(f"{label} is connected. New messages start arriving on the next check.")
                + '</p>') if _connect_names(accounts, just) else (
                '<p class="quiet">' + _esc(f"{label} did not finish connecting. Try it again.")
                + '</p>')
    rows, adds = [], []
    for vendor_token, label in _CONNECTABLE:
        mine = _connect_names(accounts, vendor_token)
        if not mine:
            need = _CONNECT_NEEDS.get(vendor_token)
            adds.append(f'<div class="setrow"><b>{_esc(label)}</b>'
                        f'<span>Not connected yet.{" " + _esc(need) if need else ""}</span>'
                        '<p style="margin:10px 0 0"><a class="btn" '
                        f'href="/inbox/connect/{_esc(vendor_token)}">Connect {_esc(label)}</a></p></div>')
            continue
        read = [a for a in mine if a["id"] == picked.get(vendor_token)] or mine
        names = ", ".join(a["name"] or "your account" for a in read)
        if vendor_token in stopped:
            rows.append(f'<div class="setrow"><b>{_esc(label)}</b>'
                        f'<span>Disconnected: Ownbox is not reading {_esc(names)}. It stays in your Zernio '
                        'workspace.</span><p class="acts">'
                        + _platform_button("read", "Read it again", vendor_token) + '</p></div>')
            continue
        rows.append(f'<div class="setrow"><b>{_esc(label)}</b>'
                    f'<span>Connected. Reading {_esc(names)}.</span><p class="acts">'
                    f'<a href="/inbox/connect/{_esc(vendor_token)}/change">Change</a>'
                    + _platform_button("stop", "Disconnect", vendor_token) + '</p></div>')
    # iMESSAGE, SAID ONCE AND TRUE: Apple offers no way for any app outside Apple to read or answer iMessages
    # (docs/PLAN_OWNBOX_UNIFIED_INBOX_MACHINE_V3.md §6: "No third-party API. This is how a list becomes a lie").
    adds.append('<div class="setrow"><b>iMessage</b><span>Not available. Apple does not let any app outside Apple '
                'read or answer iMessages, so no inbox can carry them.</span></div>')
    return (
      '<h1>Your social accounts.</h1>'
      + done
      + ('<div class="card">' + "".join(rows) + '</div>' if rows else '')
      + '<h2 class="eyebrow" style="margin-top:18px">Add a channel</h2>'
      '<div class="card">' + "".join(adds) + '</div>'
      '<h2 class="eyebrow" style="margin-top:18px">Your Zernio account</h2>'
      '<div class="card">'
      f'<div class="setrow"><b>Workspace</b><span>{_esc(workspace or "Your workspace")}: the channels above are '
      'the ones in it.</span><p class="acts"><a href="/inbox/connect?workspace=1">Switch workspace</a></p></div>'
      '<div class="setrow"><b>Key</b><span>Saved, and checked with Zernio.</span><p class="acts">'
      '<a href="/inbox/connect?replace=1">Replace key</a></p></div>'
      '<div class="setrow"><b>Disconnect Zernio</b><span>Ownbox stops reading every channel above. Nothing you '
      'have already received is deleted.</span><p class="acts">'
      + _post_button("/inbox/connect", "off", "1", "Disconnect Zernio") + '</p></div>'
      '</div>'
      '<p style="margin-top:14px"><a href="/inbox/settings" style="color:var(--href)">'
      '&larr; Settings</a></p>')


def _connect_change(form, whoami):
    """The owner's per-channel choices, posted from Social Accounts: stop reading one channel, read it again, or read
    only one account where the workspace holds several. Each is checked against what the screen offers; anything else
    changes nothing."""
    from core.vendors import zernio
    from .inbox import channels as _ch
    platform, do = str(form.get("platform") or ""), str(form.get("do") or "")
    if platform not in {p for p, _ in _CONNECTABLE}:
        return redirect("/inbox/connect", code=303)
    if do in ("stop", "read"):
        _ch.stop(platform, do == "stop", by=whoami)
        return redirect("/inbox/connect", code=303)
    account = str(form.get("account") or "")
    if account == "all":
        _ch.choose(platform, None, by=whoami)
        return redirect("/inbox/connect", code=303)
    try:
        known = {a["id"] for a in zernio.client(_connect_space()).accounts.all() if a.get("platform") == platform}
    except zernio.ZernioError:
        known = set()
    if account in known:                               # ONLY AN ACCOUNT IN THIS WORKSPACE, ON THIS PLATFORM
        _ch.choose(platform, account, by=whoami)
        _ch.stop(platform, False, by=whoami)
    return redirect("/inbox/connect", code=303)


@blueprint.get("/inbox/connect/<platform>/change")
def r_connect_change(platform: str):
    """CHANGE, for one channel (owner 10-04): the accounts on this platform in the workspace, the one being read
    marked, and a way to connect another. Like Search Console's "change the website"."""
    from core.vendors import zernio
    from .inbox import channels as _ch
    gate = _gate()
    if gate is not None:
        return gate
    if not _is_owner():
        return _owner_refusal()
    label = dict(_CONNECTABLE).get(platform)
    if not label:
        return redirect("/inbox/connect", code=303)
    try:
        mine = [a for a in zernio.client(_connect_space()).accounts.all() if a.get("platform") == platform]
    except zernio.ZernioError:
        return _shell(_connect_trouble("Ownbox could not reach your social account just now. Nothing is lost — "
                                       "try again in a minute."), here="/inbox/connect"), 200
    want = _ch.chosen().get(platform)
    reading = want or (mine[0]["id"] if len(mine) == 1 else "")

    def pick(value: str, text: str, on: bool) -> str:
        return ('<form method="post" action="/inbox/connect" style="margin:10px 0 0">'
                f'<input type="hidden" name="do" value="pick"><input type="hidden" name="platform" '
                f'value="{_esc(platform)}"><input type="hidden" name="account" value="{_esc(value)}">'
                f'<button class="btn{" ghost" if on else ""}" type="submit">{_esc(text)}'
                f'{" (reading now)" if on else ""}</button></form>')
    rows = "".join(pick(a["id"], a["name"] or f"Your {label} account", reading == a["id"]) for a in mine)
    if len(mine) > 1:
        rows += pick("all", f"Read all {len(mine)}", not want)
    return _shell(
        f'<h1>Change {_esc(label)}.</h1>'
        '<div class="card"><div class="setrow"><span>'
        + (f"The {_esc(label)} accounts in your Zernio workspace. Ownbox reads the one you pick."
           if mine else f"No {_esc(label)} account is in your Zernio workspace yet.")
        + f'</span>{rows}</div></div>'
        f'<p style="margin-top:14px"><a class="btn ghost" href="/inbox/connect/{_esc(platform)}">'
        f'Connect another {_esc(label)} account</a></p>'
        '<p style="margin-top:14px"><a href="/inbox/connect" style="color:var(--href)">&larr; Social accounts</a></p>',
        here="/inbox/connect"), 200


def _connect_trouble(sentence: str) -> str:
    """One sentence a person can act on, and a way back. Never a stack trace, never a code."""
    return ('<h1>Your social accounts.</h1>'
            f'<div class="card"><div class="setrow"><span>{_esc(sentence)}</span></div></div>'
            '<p style="margin-top:14px"><a href="/inbox/connect" style="color:var(--href)">'
            'Try again</a> · <a href="/inbox/settings" style="color:var(--href)">Settings</a>'
            '</p>')


@blueprint.route("/inbox/installed", methods=["POST"])
def r_installed():
    """A device reported that it is running installed.

    GATED LIKE EVERY OTHER WRITE — a beacon carries the session cookie, so this is the owner's own
    device saying so and not an anonymous claim.

    A LOG LINE, NOT A TABLE, and that is a stage boundary rather than laziness. Stage 4 adds
    `voice_push_subs`, one row per DEVICE, which is the honest home for "this phone has the app" —
    tying it to the subscription that actually proves it. Inventing a table here would mean
    migrating it one stage later. The journal is greppable on the box today, which is what
    "measure the completion rate" needs to start meaning something.
    """
    log.info("voice.install_confirmed",
             extra={"space": _space(),
                    "agent": str(request.headers.get("User-Agent", ""))[:120]})
    return ("", 204)


@blueprint.get("/inbox/install")
def r_install():
    """INSTALLING THE APP HAS ONE HOME (owner, 2026-09-29, IA D3): System Settings > Mobile App,
    which teaches every app on the box — Base Machine, Unified Inbox and each add-on machine — with
    the same Share, Add to Home Screen steps. This address still answers for old links."""
    return redirect("/settings/mobile", code=302)


# ── search, as a screen of its own ──────────────────────────────────────────────────────────
@blueprint.get("/inbox/search")
def r_search():
    """The place you go to look for something, rather than a field you find on the way past.

    WHY THIS EXISTS AT ALL, recorded because it looks redundant next to `_find`. Search had no
    address: the field lives on the list, so "search" was a control you could only reach by first
    loading 50 conversations you did not want. That is fine on a laptop and wrong on a phone,
    where the field sits at the top of a screen your thumb cannot reach.

    IT DOES NOT SEARCH. Every result, every empty state and all five of the ways this box can
    legitimately have nothing to show already live on `/inbox/inbox?q=`, which took a long time
    to get right. Reimplementing any of it here would be a second answer to one question — so
    this screen is the DOOR and the list stays the room: `_find` is the same GET form it is
    everywhere else, and submitting it lands on the list with `q` bound exactly as before.

    AND IT SAYS WHAT SEARCH ACTUALLY READS. `search_conversations` matches message BODIES, not
    just the names on the rows — a fact that has been true for weeks and that no screen has ever
    told a buyer. Someone who assumes it only matches names will not try the word they remember.
    """
    space = _space()
    channel = (request.args.get("channel") or "").strip().lower()[:40]
    body = (
        '<h1>Search</h1>'
        + _find("", channel)
        # NARROW BEFORE YOU TYPE, on the boxes where that is a real choice. `_logos` draws
        # nothing at all on a box with one channel, which is the rule this app applies to every
        # filter: a control with one option cannot change anything.
        + ((f'<div class="chips">{_lg}</div>') if (_lg := _logos(space, channel)) else "")
        + '<div class="card"><div class="row"><span class="t">Search reads what people actually '
          'wrote, not just their names — so the word you remember from the message is '
          'usually enough to find it again.</span></div></div>'
        + '<div class="foot"><a href="/inbox/inbox">← All conversations</a></div>')
    return _shell(body, here="/inbox/search"), 200



def _is_owner() -> bool:
    """ONE ANSWER TO THIS QUESTION, used by the row and by the screen it links to.

    Two scopes that resolve it differently would eventually disagree, and the shape of that
    disagreement is a link drawn for somebody the next screen refuses.
    """
    try:
        return (( dash.session_user(request) or {}).get("role") or "") == "owner"
    except Exception:                                    # noqa: BLE001 — an unreadable session is
        return False                                     # not the owner, and never a 500


# THE BOX'S CONNECTIONS ARE THE OWNER'S TO CHANGE. Owner, 2026-09-24, asked who on a team box may
# change or switch off the mailbox, the social accounts and drafting: "Owner only". A member still
# reads, replies and sends, and sees each connection's state; the controls are the owner's, the
# same rule the AI account's page has had since it moved to System Settings.
_OWNER_ONLY = ('<span style="margin-top:8px;color:var(--dim)">Only the owner of this box can '
               'change this.</span>')


def _owner_refusal():
    """A member's POST to a control that is the owner's: a sentence and a 403, never a change."""
    return _shell('<h1>Only the owner can change this.</h1>'
                  '<p class="quiet">This box\'s connections belong to its owner. Ask them, or '
                  'read and answer messages as usual.</p>' + _back_link()), 403


def _post_button(action: str, name: str, value: str, label: str) -> str:
    """A switch that changes the box, drawn as a line of text but sent as a POST.

    NEVER A LINK. Each of these was an `<a href="...?off=1">`, and a GET that changes state fires
    for anything that merely loads the URL: an iPhone long-press preview, a link prefetch, or a
    link on another site, which the Lax session cookie still rides on a top-level GET. A POST
    from another site carries no cookie, and nothing previews a form.
    """
    return (f'<form method="post" action="{_esc(action)}" class="inline">'
            f'<button type="submit" class="ghost txt" name="{_esc(name)}" value="{_esc(value)}">'
            f'{_esc(label)}</button></form>')


def _drafting_on() -> bool:
    """Whether this box writes drafts: the owner's switch, then what the box shipped with.

    `box_settings` under ("inbox", "drafts.enabled") falls back to `inbox.drafts.enabled` in the
    config, the one switch the drafter already reads (`drafter/draft.enabled`).
    """
    try:
        from marketing.customer_voice.drafter import draft as _draft
        return _draft.enabled()
    except Exception:                                    # noqa: BLE001 — a row, never a 500
        return True


def _ai_home() -> str:
    """Where the person reading goes to change the AI account.

    The owner goes to its one home, System Settings. A member would be refused there, so they go
    to the inbox's Settings, whose drafting row says whose account it is and who can change it.
    """
    # A MEMBER GOES TO THE INBOX'S SETTINGS, where the drafting row says whose account it is (the
    # old /inbox/drafts page was folded into it on 2026-09-29, IA D3).
    return "/settings/ai" if _is_owner() else "/inbox/settings"


# ── DRAFTS: the queue of people waiting on an answer ─────────────────────────────────────────
# OWNER, 2026-09-22: "a tab in the dashboard called Drafts where we can select groups of messages
# and then send them off", and, of the drafting itself: "that's one of the killer features for
# the unified inbox. The big time-saver. Even Gmail doesn't have that."
#
# EVERY DRAFT'S FULL TEXT IS ON THE SCREEN, and that is the design, not a layout choice. The time
# this saves is the WRITING, never the reading — a screen that let somebody tick twenty boxes
# against twenty subject lines would be a machine for sending twenty replies nobody read, to
# twenty customers, under the buyer's own name. So the list IS the drafts, in full, oldest first,
# and ticking a box is the act of having read one.
#
# THE ROUTE IS `/inbox/waiting` WHILE THE TAB SAYS DRAFTS. `/inbox/drafts` is already taken by
# the AI-account form (§2.6) — a misnamed route from before this screen existed. Renaming it
# today would break a link somebody may already have; it should move, in its own change.
def _draft_card(d: dict, n: int, pitched: bool = False) -> str:
    asked = str(d.get("asked") or "").strip()
    return (
        f'<div class="card dcard" style="margin-top:12px">'
        f'<label style="display:flex;gap:10px;align-items:flex-start;cursor:pointer">'
        f'<input type="checkbox" name="pick" value="{_esc(str(d["zcid"]))}"'
        f'{" data-pitch=1" if pitched else ""} '
        f'style="margin-top:4px;width:18px;height:18px;flex:0 0 auto">'
        f'<span style="flex:1 1 auto">'
        f'<b>{_esc(str(d.get("participant") or "Someone"))}</b>'
        f'<span class="quiet dm"> · {_esc(_channel(str(d.get("platform") or "")))}'
        f' · asked {_esc(_when(d.get("asked_at") or d.get("created_at")))}</span>'
        # A COLD PITCH, TURNED AROUND (drafter PITCH_BACK): said on the card, so he knows this reply sells back.
        + ('<span class="quiet" style="display:block;margin:4px 0 0;color:var(--accent)">'
           'A cold pitch, turned around: this reply points them to your link.</span>' if pitched else "")
        # WHAT THEY ASKED, ABOVE WHAT WE WOULD SAY. A reply read without the question is a reply
        # nobody can judge, and judging it is the whole point of this screen.
        + (f'<span class="quiet" style="display:block;margin:6px 0 0;padding-left:10px;'
           f'border-left:2px solid var(--line)">{_readable(asked[:400])}</span>' if asked else "")
        + f'<span style="display:block;margin:10px 0 0;white-space:pre-wrap">'
          f'{_readable(str(d.get("body") or ""))}</span>'
        f'<span class="quiet" style="display:block;margin-top:8px">'
        f'<a href="/inbox/inbox/{_esc(str(d["zcid"]))}" style="color:var(--href)">'
        f'Open the conversation to edit it</a></span>'
        f'</span></label></div>')


def _pitch_tick(rows: list, pitched: set) -> str:
    """'Tick every cold pitch': the owner's 'all at once' (2026-10-02), one tap that only ticks; Send still sends."""
    k = sum(1 for d in rows if str(d.get("in_reply_to")) in pitched)
    if not k:
        return ""
    return ('<p style="margin:12px 0 0"><button type="button" class="btn" style="min-height:48px" '
            'onclick="document.querySelectorAll(\'input[data-pitch]\').forEach(function(c){c.checked=true})">'
            f'Tick all {k} cold {"pitch" if k == 1 else "pitches"}</button></p>')


@blueprint.route("/inbox/waiting", methods=["GET", "POST"])
def r_waiting():
    """The drafts waiting on a person, and one button to send the ones they have read.

    NOTHING HERE SENDS. It calls `inbox.reply.send_reply` — the same plain function the thread's
    own reply button uses, which is the only thing in this department allowed to send and which
    carries every rule the single path has: the opted-out refusal, the idempotency claim, the
    hourly window. A second send path would be a second set of those rules to keep in step.

    ONE FAILURE DOES NOT STOP THE REST. Ten ticked drafts are ten independent sends; a vendor
    refusing the third must not silently swallow the seven after it, and the screen says exactly
    which went and which did not.
    """
    from marketing.customer_voice.drafter import store as drafts
    from marketing.customer_voice.inbox import reply as _reply
    gate = _gate()
    if gate is not None:
        return gate
    space = _space()
    note = ""

    if request.method == "POST":
        try:
            u = dash.session_user(request) or {}
        except Exception:                                # noqa: BLE001 — cannot say who: no send
            u = {}
        if not u.get("id"):
            note = ('<div class="card"><p>Your session could not be read, so nothing was sent.'
                    '</p></div>')
        else:
            picked = [p for p in request.form.getlist("pick") if p]
            by_zcid = {d["zcid"]: d for d in drafts.waiting(space, limit=200)}
            sent, failed = [], []
            for zcid in picked:
                d = by_zcid.get(zcid)
                if d is None:                            # answered or dismissed since the page drew
                    continue
                try:
                    out = _reply.send_reply(space=space, zcid=zcid, text=str(d["body"]),
                                            user_id=str(u["id"]),
                                            nonce=f"waiting:{d['id']}")
                except Exception as e:                   # noqa: BLE001 — one bad send, not ten
                    # THE SENTENCE, NOT THE CLASS NAME. This recorded `type(e).__name__`, so a
                    # buyer read "Dr. Mercola did not go — ReplyRefused." and so did we: the one
                    # line that says WHY — "this box is not connected to a mailbox", "there is no
                    # address on this thread" — was thrown away at the only place it was needed.
                    # The thread's own compose box has always shown `str(e)`; this screen did not.
                    log.error("voice.waiting_send_failed",
                              extra={"conversation": zcid, "error": type(e).__name__,
                                     "why": str(e)[:200]})
                    # OUR OWN SENTENCE, NEVER SOMEBODY ELSE'S TEXT. `ReplyRefused` carries a
                    # line written for a buyer and it belongs on the screen. An unexpected raise
                    # carries whatever the failure happened to say — a path, a vendor's internals —
                    # and that has never been fit to show a person, which is what the class name
                    # was protecting against before it started hiding the good sentences too.
                    failed.append((d, str(e) if isinstance(e, _reply.ReplyRefused)
                                   else "it could not be sent just now"))
                    continue
                # `ok` IS WHAT A SEND RETURNS. `send_reply` has only ever returned "ok" — there is
                # no "sent" and no "queued" anywhere in `inbox/reply.py` — so this counted every
                # SUCCESSFUL send as a failure and told the buyer "did not go — ok." about a reply
                # that had left the box. Worse than a wrong word: it invites a re-tick, and only
                # the ledger claim underneath stopped that becoming a second message.
                (sent if str(out.get("status")) == "ok" else failed).append(
                    (d, str(out.get("status"))))
            bits = []
            if sent:
                bits.append(f'<b>Sent {len(sent)}.</b>')
            for d, why in failed:
                bits.append(f'{_esc(str(d.get("participant") or "One reply"))} did not go — '
                            f'{_esc(why)}.')
            if bits:
                note = f'<div class="card"><p>{" ".join(bits)}</p></div>'

    rows = drafts.waiting(space, limit=200)
    if not rows:
        # THE PAGE SAYS WHAT ITS MENU ROW SAYS: "Replies to send" (IA D5, 2026-09-29).
        body = ('<h1>Replies to send</h1>'
                '<div class="card"><div class="row"><span class="t">Nothing to send. When a '
                'customer writes and your box drafts a reply, it appears here for you to read '
                'and send.</span></div></div>'
                '<div class="foot"><a href="/inbox/inbox">← All conversations</a></div>')
        return _shell(note + body, here="/inbox/waiting"), 200

    pitched = drafts.pitch_backs(space)
    n = len(rows)
    body = (f'<h1>{n} {"reply" if n == 1 else "replies"} to send.</h1>'
            '<p class="quiet">Your box wrote these. Read them, tick the ones you are happy with, '
            'and send. Nothing goes out until you press the button — and anything you want to '
            'change, open the conversation and edit it there.</p>'
            + note
            + '<form method="post">'
            + _pitch_tick(rows, pitched)
            + "".join(_draft_card(d, i, str(d.get("in_reply_to")) in pitched) for i, d in enumerate(rows, 1))
            + '<p style="margin:18px 0 0"><button class="btn" type="submit">'
              'Send the ones I ticked</button></p></form>'
            + '<div class="foot"><a href="/inbox/inbox">← All conversations</a></div>')
    return _shell(body, here="/inbox/waiting"), 200


# ── the email signature (inbox/signature.py) ───────────────────────────────────────────────────────────────────
# Owner, 2026-10-02: "I always want to finish with a signature that includes a link to my website", then "Yes, perfect
# add that open field please." One open field, owner-only like the mailbox it signs for; a member reads it.
@blueprint.route("/inbox/signature", methods=["GET", "POST"])
def r_signature():
    from marketing.customer_voice.inbox import signature as _sig
    gate = _gate()
    if gate is not None:
        return gate
    owner = _is_owner()
    if request.method == "POST" and not owner:
        return _owner_refusal()
    space = _space()
    note, typed = "", None
    if request.method == "POST":
        typed = str(request.form.get("signature") or "")
        try:
            u = dash.session_user(request) or {}
        except Exception:                        # noqa: BLE001 — only whose name the audit line carries
            u = {}
        try:
            _sig.put(space, typed, by=u.get("id"))
            return redirect("/inbox/signature?saved=1", code=303)
        except ValueError as e:
            note = f'<p class="quiet" style="color:var(--accent)">{_esc(str(e))}</p>'
    current = _sig.get(space)
    saved = request.args.get("saved") and not note
    head = ('<h1>Email signature</h1>'
            '<p class="quiet">It goes at the end of every email reply your box drafts or sends, including the '
            'drafts it puts in your Gmail Drafts. Gmail doesn\'t add its own there.</p>'
            + ('<p class="quiet">Saved. Your next reply ends with it.</p>' if saved else ""))
    if not owner:
        shown = (f'<pre style="white-space:pre-wrap;font:inherit">{_esc(current)}</pre>' if current
                 else '<p class="quiet">No signature yet. The owner of this box can add one here.</p>')
        return _shell(head + shown + _back_link(), here="/inbox/signature"), 200
    field = ('font:inherit;font-size:max(16px, calc(17 * var(--px, 1px)));padding:12px 14px;width:100%;'
             'border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--ink)')
    form = (note +
            '<form class="compose" method="post" action="/inbox/signature" '
            'style="display:flex;flex-direction:column;gap:10px;align-items:stretch">'
            '<label style="display:block">'
            '<span class="t" style="display:block;font-size:calc(14.5 * var(--px, 1px));margin-bottom:4px">'
            'Your signature</span>'
            f'<textarea name="signature" rows="5" maxlength="{_sig.MAX_CHARS}" '
            'aria-label="Your email signature" '
            'placeholder="Your name&#10;Your title&#10;www.yourwebsite.com" '
            f'style="{field}">{_esc(current if typed is None else typed)}</textarea></label>'
            '<p class="quiet" style="margin:0">Leave it empty to stop adding one.</p>'
            '<button class="btn" type="submit" style="min-height:48px">Save signature</button></form>')
    return _shell(head + form + _back_link(), here="/inbox/signature"), 200


# WHAT EACH LEVEL DOES, said on the page (owner, 2026-10-04: "And that should be explained on the webpage"). One card per
# level, stacked: a table three columns wide does not fit a mobile screen. The words follow the drafter's STYLE_* text.
_LEVELS = (
    ("Customer service", "For an established business with steady work coming in. Pure service, never salesy.",
     ("Prospects: a complete, helpful answer. How to book only if they ask.",
      "Customers: their problem solved, clearly and kindly. No upsell.",
      "Anyone else: nothing about what you sell.")),
    ("Subtle sales", "A light touch. People learn what you do without feeling sold to.",
     ("Prospects: an answer, then a gentle invitation to book or visit your website.",
      "Customers: their problem solved first, then one light sentence about what you do.",
      "Anyone else: one light sentence about what you do, with your website.")),
    ("Strong sales", "Startup mode, for a business still finding its market. Every reply works to win business.",
     ("Prospects: an answer, then a clear next step in every reply, and a plain ask for the booking or the sale.",
      "Customers: problem-solving service first. Once they are happy, an invitation to book again.",
      "Anyone else: one light sentence about what you do, never a hard sell.")),
)


def _style_levels() -> str:
    cards = "".join(
        f'<div class="card" style="margin-top:10px"><div class="t"><b>{_esc(name)}</b></div>'
        f'<p class="quiet" style="margin:4px 0 6px">{_esc(lede)}</p>'
        + "".join(f'<p style="margin:2px 0">{_esc(x)}</p>' for x in rows) + '</div>'
        for name, lede, rows in _LEVELS)
    return f'<div style="margin:6px 0 16px">{cards}</div>'


# ── reply style, per channel (inbox/reply_style.py, drafter STYLE_SALES / STYLE_SERVICE) ─────────────────────────────
# Owner, 2026-10-04: "I wish there was a setting of a style of response that we could select per channel... I just
# always want to be trying to get more business so want to always be closing ABC." Owner edits; members read.
@blueprint.route("/inbox/reply-style", methods=["GET", "POST"])
def r_reply_style():
    from marketing.customer_voice.inbox import reply_style as _rs
    gate = _gate()
    if gate is not None:
        return gate
    owner = _is_owner()
    if request.method == "POST" and not owner:
        return _owner_refusal()
    note = ""
    if request.method == "POST":
        try:
            u = dash.session_user(request) or {}
        except Exception:                        # noqa: BLE001 — only whose name the audit line carries
            u = {}
        try:
            _rs.put(email=request.form.get("email"), dms=request.form.get("dms"), by=u.get("id"))
            return redirect("/inbox/reply-style?saved=1", code=303)
        except ValueError as e:
            note = f'<p class="quiet" style="color:var(--accent)">{_esc(str(e))}</p>'
    cur = _rs.get()
    saved = request.args.get("saved") and not note
    head = ('<h1>Reply style</h1>'
            '<p class="quiet">Your box reads every message before it drafts a reply. It tells a <b>prospect</b> '
            '(not a customer yet) from a <b>customer</b> (already bought or booked) and from <b>anyone else</b> (a '
            'recruiter, a supplier, someone pitching you), and writes to each the way a good owner would. That is '
            'built in, and a customer with a problem always gets it solved first.</p>'
            '<p class="quiet">The style sets how far it leans: further toward service, or further toward the sale. Pick '
            'one for each channel. Every draft still waits for you to send it.</p>'
            + _style_levels()
            + ('<p class="quiet">Saved. New drafts are written this way.</p>' if saved else "") + note)
    if not owner:
        rows = "".join(f'<p>{_rs.CHANNEL_WORDS[ch]}: {_rs.STYLES[cur[ch]]}</p>' for ch in _rs.CHANNELS)
        return _shell(head + rows + _back_link(), here="/inbox/reply-style"), 200
    field = ('font:inherit;font-size:max(16px, calc(17 * var(--px, 1px)));padding:12px 14px;width:100%;'
             'min-height:48px;border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--ink)')

    def pick(ch):
        opts = "".join(f'<option value="{k}"{" selected" if cur[ch] == k else ""}>{_esc(v)}</option>'
                       for k, v in _rs.STYLES.items())
        return ('<label style="display:block"><span class="t" style="display:block;'
                f'font-size:calc(14.5 * var(--px, 1px));margin-bottom:4px">{_rs.CHANNEL_WORDS[ch]}</span>'
                f'<select name="{ch}" aria-label="{_rs.CHANNEL_WORDS[ch]}" style="{field}">{opts}</select></label>')
    form = ('<form class="compose" method="post" action="/inbox/reply-style" '
            'style="display:flex;flex-direction:column;gap:12px;align-items:stretch">'
            + pick("email") + pick("dms")
            + '<button class="btn" type="submit" style="min-height:48px">Save</button></form>')
    return _shell(head + form + _back_link(), here="/inbox/reply-style"), 200


# ── sending, what the box sends on its own (inbox/sending.py) ────────────────────────────────────────────────────
# Owner, 2026-10-04: "Seems like there's some things that are not even built out yet." The first message and the
# hourly cap lived only in the configuration file, which a buyer cannot edit. Owner edits; members read.
@blueprint.route("/inbox/sending", methods=["GET", "POST"])
def r_sending():
    from marketing.customer_voice.inbox import sending as _snd
    gate = _gate()
    if gate is not None:
        return gate
    owner = _is_owner()
    if request.method == "POST" and not owner:
        return _owner_refusal()
    note = ""
    if request.method == "POST":
        try:
            u = dash.session_user(request) or {}
        except Exception:                        # noqa: BLE001 — only whose name the audit line carries
            u = {}
        try:
            _snd.put(first_message=request.form.get("first_message") or "off", text=request.form.get("text"),
                     hourly_cap=request.form.get("hourly_cap"), by=u.get("id"))
            return redirect("/inbox/sending?saved=1", code=303)
        except ValueError as e:
            note = f'<p class="quiet" style="color:var(--accent)">{_esc(str(e))}</p>'
    cur = _snd.get()
    saved = request.args.get("saved") and not note
    head = ('<h1>Sending</h1>'
            '<p class="quiet">Everything your box writes waits for you to send it, except one thing you can turn on '
            'here: a <b>first message</b>. When it is on, a brand-new conversation on Messenger, Instagram or '
            'WhatsApp gets the fixed words below at once, so nobody waits while you are busy. It goes once per '
            'conversation, never by email, and stops the moment the person asks it to.</p>'
            '<p class="quiet">The <b>hourly cap</b> is the most messages your box sends in any hour, counted across '
            'every channel and every send. The default, 40, is safe. Your mailbox provider and Meta have limits of '
            'their own and watch for bursts: set this high and a busy hour can get your mail marked as junk or '
            'your account held back. Raise it only if you know your provider allows it.</p>'
            + ('<p class="quiet">Saved.</p>' if saved else "") + note)
    on = cur["first_message"] == "on"
    if not owner:
        rows = (f'<p>First message on its own: {"On" if on else "Off"}</p>'
                + (f'<p>The first message: {_esc(cur["text"])}</p>' if cur["text"] else "")
                + f'<p>Most messages in an hour: {cur["hourly_cap"]}</p>')
        return _shell(head + rows + _back_link(), here="/inbox/sending"), 200
    field = ('font:inherit;font-size:max(16px, calc(17 * var(--px, 1px)));padding:12px 14px;width:100%;'
             'min-height:48px;border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--ink)')
    label = 'class="t" style="display:block;font-size:calc(14.5 * var(--px, 1px));margin-bottom:4px"'
    form = ('<form class="compose" method="post" action="/inbox/sending" '
            'style="display:flex;flex-direction:column;gap:12px;align-items:stretch">'
            f'<label style="display:block"><span {label}>First message on its own</span>'
            f'<select name="first_message" aria-label="First message on its own" style="{field}">'
            f'<option value="off"{"" if on else " selected"}>Off</option>'
            f'<option value="on"{" selected" if on else ""}>On</option></select></label>'
            f'<label style="display:block"><span {label}>The first message</span>'
            f'<textarea name="text" aria-label="The first message" rows="3" style="{field};min-height:96px" '
            f'placeholder="Thanks for reaching out! I got your message and I\'m on it. What can I help with?"'
            f'>{_esc(cur["text"])}</textarea></label>'
            f'<label style="display:block"><span {label}>Most messages in an hour</span>'
            f'<input name="hourly_cap" type="number" inputmode="numeric" min="{_snd.MIN_CAP}" max="{_snd.MAX_CAP}" '
            f'aria-label="Most messages in an hour" value="{cur["hourly_cap"]}" style="{field}"></label>'
            '<button class="btn" type="submit" style="min-height:48px">Save</button></form>')
    return _shell(head + form + _back_link(), here="/inbox/sending"), 200


# ── cold pitches, turned around (inbox/pitch_back.py, drafter PITCH_BACK) ───────────────────────────────────────────
# Owner, 2026-10-02: "I get tons of cold email and I want to advertise right back to them and turn it right around on
# them." The box drafts; he reads and sends (or ticks them all on Replies to send). Owner-only, like the mailbox.
@blueprint.route("/inbox/pitch-back", methods=["GET", "POST"])
def r_pitch_back():
    from marketing.customer_voice.inbox import pitch_back as _pb
    gate = _gate()
    if gate is not None:
        return gate
    owner = _is_owner()
    if request.method == "POST" and not owner:
        return _owner_refusal()
    note, typed = "", None
    if request.method == "POST":
        typed = str(request.form.get("link") or "")
        try:
            u = dash.session_user(request) or {}
        except Exception:                        # noqa: BLE001 — only whose name the audit line carries
            u = {}
        try:
            _pb.put(bool(request.form.get("on")), typed, by=u.get("id"))
            return redirect("/inbox/pitch-back?saved=1", code=303)
        except ValueError as e:
            note = f'<p class="quiet" style="color:var(--accent)">{_esc(str(e))}</p>'
    cur = _pb.get()
    saved = request.args.get("saved") and not note
    head = ('<h1>Cold pitches</h1>'
            '<p class="quiet">When someone emails you a sales pitch, your box can draft a friendly reply that turns it '
            'around: it thanks them, says what you offer and points them to your website. Each one waits on '
            '<a href="/inbox/waiting" style="color:var(--href)">Replies to send</a>, marked as a cold pitch, and in your '
            'Gmail Drafts. Nothing goes out until you send it.</p>'
            + (('<p class="quiet">Saved. ' + ("New cold pitches get a reply drafted." if cur["on"] else
                                               "Cold pitches are left alone.") + '</p>') if saved else "") + note)
    if not owner:
        state_line = (f'On, pointing to {_esc(cur["link"])}.' if cur["on"] else "Off.")
        return _shell(head + f'<p>{state_line}</p>' + _back_link(), here="/inbox/pitch-back"), 200
    field = ('font:inherit;font-size:max(16px, calc(17 * var(--px, 1px)));padding:12px 14px;width:100%;'
             'border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--ink)')
    link = cur["link"] if typed is None else typed
    form = ('<form class="compose" method="post" action="/inbox/pitch-back" '
            'style="display:flex;flex-direction:column;gap:10px;align-items:stretch">'
            '<label style="display:flex;gap:10px;align-items:center;min-height:48px">'
            f'<input type="checkbox" name="on" value="1"{" checked" if cur["on"] else ""} '
            'style="width:22px;height:22px;flex:0 0 auto">'
            '<span>Turn cold pitches around</span></label>'
            '<label style="display:block"><span class="t" style="display:block;'
            'font-size:calc(14.5 * var(--px, 1px));margin-bottom:4px">Your website</span>'
            f'<input name="link" value="{_esc(link)}" inputmode="url" autocomplete="url" spellcheck="false" '
            f'aria-label="Your website" placeholder="www.yourwebsite.com" style="{field}"></label>'
            '<button class="btn" type="submit" style="min-height:48px">Save</button></form>')
    return _shell(head + form + _back_link(), here="/inbox/pitch-back"), 200


# ── saved replies (inbox/snippets.py, #1821) ─────────────────────────────────────────────────────────────────────
# Owner, 2026-10-02: "a list of snippets where on each message you could pull a drop-down and see all of them and
# select them". He edits the list; every member uses it (his pick 2). Archive, never delete.
@blueprint.route("/inbox/snippets", methods=["GET", "POST"])
def r_snippets():
    from marketing.customer_voice.inbox import snippets as _snips
    gate = _gate()
    if gate is not None:
        return gate
    owner = _is_owner()
    if request.method == "POST" and not owner:
        return _owner_refusal()
    space, note = _space(), ""
    if request.method == "POST":
        act, sid = str(request.form.get("act") or ""), str(request.form.get("id") or "")
        try:
            u = dash.session_user(request) or {}
        except Exception:                        # noqa: BLE001 — only whose name the row carries
            u = {}
        try:
            if act == "archive":
                _snips.archive(space, sid)
                return redirect("/inbox/snippets?done=archived", code=303)
            if act == "save" and sid:
                _snips.edit(space, sid, request.form.get("title"), request.form.get("body"))
            else:
                _snips.add(space, request.form.get("title"), request.form.get("body"), by=u.get("id"))
            return redirect("/inbox/snippets?done=saved", code=303)
        except _snips.SnippetRefused as e:
            note = f'<p class="quiet" style="color:var(--accent)">{_esc(str(e))}</p>'
    rows = _snips.all_for(space)
    done = {"saved": "Saved. It is in the dropdown above every reply.",
            "archived": "Archived. It is gone from the dropdown."}.get(str(request.args.get("done") or ""), "")
    head = ('<h1>Saved replies</h1>'
            '<p class="quiet">Words you send often, one tap away above every reply box. Pick one and it fills the '
            'reply; you read it and press Send. Write {first_name} for the person\'s first name and {my_name} for '
            'yours.</p>' + (f'<p class="quiet">{done}</p>' if done and not note else "") + note)
    if not owner:
        listed = "".join(f'<div class="card"><div class="t"><b>{_esc(r["title"])}</b></div>'
                         f'<pre style="white-space:pre-wrap;font:inherit;margin:6px 0 0">{_esc(r["body"])}</pre></div>'
                         for r in rows) or '<p class="quiet">No saved replies yet. The owner of this box adds them here.</p>'
        return _shell(head + listed + _back_link(), here="/inbox/snippets"), 200
    field = ('font:inherit;font-size:max(16px, calc(17 * var(--px, 1px)));padding:12px 14px;width:100%;'
             'border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--ink)')

    def form(r=None, typed=None):
        r = r or {}
        t = (typed or {}).get("title", r.get("title", ""))
        b = (typed or {}).get("body", r.get("body", ""))
        sid = r.get("id", "")
        out = ('<form class="compose" method="post" action="/inbox/snippets" '
               'style="display:flex;flex-direction:column;gap:8px;align-items:stretch">'
               f'<input type="hidden" name="id" value="{_esc(sid)}">'
               f'<input type="hidden" name="act" value="{"save" if sid else "add"}">'
               f'<input name="title" value="{_esc(t)}" maxlength="{_snips.TITLE_MAX}" required '
               f'aria-label="Name in the dropdown" placeholder="Name in the dropdown, e.g. Pricing" style="{field}">'
               f'<textarea name="body" rows="5" maxlength="{_snips.BODY_MAX}" required aria-label="The words" '
               f'placeholder="Hi {{first_name}}, here is our pricing: https://…" style="{field}">{_esc(b)}</textarea>'
               f'<button class="btn" type="submit" style="min-height:48px">{"Save changes" if sid else "Add saved reply"}'
               '</button></form>')
        if sid:
            # QUIET AND APART: archiving is never the easiest thing on the screen to hit by accident.
            out += ('<form method="post" action="/inbox/snippets" style="margin:6px 0 0;text-align:right">'
                    f'<input type="hidden" name="act" value="archive"><input type="hidden" name="id" value="{_esc(sid)}">'
                    '<button type="submit" class="quiet" style="background:none;border:0;color:var(--href);'
                    'font:inherit;padding:12px 0;min-height:48px">Archive this one</button></form>')
        return out

    typed = None
    if note and request.method == "POST" and request.form.get("act") != "save":
        typed = {"title": str(request.form.get("title") or ""), "body": str(request.form.get("body") or "")}
    add = '<h2 style="margin-top:18px">Add one</h2>' + form(typed=typed)
    listed = "".join(f'<div class="card" style="margin-top:12px"><div class="quiet">Used {int(r["uses"])} '
                     f'time{"" if int(r["uses"]) == 1 else "s"}</div>{form(r)}</div>' for r in rows)
    mine = ('<h2 style="margin-top:22px">Your saved replies</h2>' + listed) if rows else ""
    return _shell(head + add + mine + _back_link(), here="/inbox/snippets"), 200


# THE ROUTES ZERNIO'S INBOX SCREENS TALK TO (#1990 step 1.2), on this blueprint so `_gate` admits them or nobody.
from . import app_api  # noqa: E402,F401
