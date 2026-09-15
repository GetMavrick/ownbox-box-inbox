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
from datetime import date

from flask import Blueprint, redirect, request

from core import dash
from core.logging import get_logger

log = get_logger(__name__)

blueprint = Blueprint("customer_voice_app", __name__)

TOKEN_COOKIE = "aios_app_k"          # the same cookie machine_app banks; one box, one unlock


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

    FOUR PATHS ARE EXEMPT, by an explicit allow-list rather than a prefix or a pattern. A browser
    fetches a manifest WITHOUT cookies unless the link says otherwise, so a gated manifest fails
    to install and says nothing a person could act on. The exemption is safe because those four
    carry no customer data at all — a colour and a title, two icons drawn from constants in this
    module, and our own worker — and `start_url` still points at `/voice/`, which is gated, so
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
*{box-sizing:border-box;margin:0;padding:0}

/* ── DUAL TOKEN SET, LIGHT FIRST ────────────────────────────────────────────────────────────
   Owner, 2026-09-15: "Light product UI is the target, dark as the toggle." So the LIGHT palette
   is the base on bare :root and dark only ever overrides it. Three states, not two, because the
   viewer may also have expressed no preference at all:
     :root                      → light, and the un-stamped default
     prefers-color-scheme:dark  → the OS asked for dark and nobody overrode it
     html[data-theme=dark|light]→ he used the switch, and the switch beats the OS both ways
   Every colour on this page resolves through a token. A literal that only works in one theme is
   the classic unreadable-app bug, and tests/test_inbox_design.py refuses one. */
:root{
  --bg:#eff2f4; --surface:#ffffff; --raised:#ffffff;
  --ink:#1a1d1f; --dim:#6e7478; --dimmer:#9ba1a6;
  --line:#e5eaed; --hair:rgba(26,29,31,.07);
  --accent:#e05d38; --accent-ink:#ffffff; --accent-soft:#fdefe9; --accent-line:rgba(224,93,56,.45);
  --bubble-in:#f2f4f6; --bubble-out:#e05d38; --bubble-out-ink:#ffffff;
  --bad:#c0392b; --bad-soft:#fdecea;
  --lift:0 1px 2px rgba(16,24,32,.05), 0 8px 24px -12px rgba(16,24,32,.18);
  --tab-bg:rgba(255,255,255,.88);
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --bg:#0d0d0d; --surface:#171717; --raised:#1f1f1f;
    --ink:#f5f3f1; --dim:#a8a29c; --dimmer:#6f6a66;
    --line:#262523; --hair:rgba(255,255,255,.08);
    --accent:#f08a5d; --accent-ink:#1a1008; --accent-soft:#2a1a12; --accent-line:rgba(240,138,93,.5);
    --bubble-in:#1f1f1f; --bubble-out:#f08a5d; --bubble-out-ink:#1a1008;
    --bad:#f0857a; --bad-soft:#2a1613;
    --lift:0 1px 2px rgba(0,0,0,.4), 0 8px 24px -12px rgba(0,0,0,.7);
    --tab-bg:rgba(13,13,13,.88);
  }
}
:root[data-theme="dark"]{
  --bg:#0d0d0d; --surface:#171717; --raised:#1f1f1f;
  --ink:#f5f3f1; --dim:#a8a29c; --dimmer:#6f6a66;
  --line:#262523; --hair:rgba(255,255,255,.08);
  --accent:#f08a5d; --accent-ink:#1a1008; --accent-soft:#2a1a12; --accent-line:rgba(240,138,93,.5);
  --bubble-in:#1f1f1f; --bubble-out:#f08a5d; --bubble-out-ink:#1a1008;
  --bad:#f0857a; --bad-soft:#2a1613;
  --lift:0 1px 2px rgba(0,0,0,.4), 0 8px 24px -12px rgba(0,0,0,.7);
  --tab-bg:rgba(13,13,13,.88);
}

/* THE SYSTEM FACE IS THE APPLE COPY. Installed to a home screen this renders in SF on iOS and
   Roboto on Android — the same face as every native app beside it, which no webfont can buy. */
body{background:var(--bg);color:var(--ink);
  font:16px/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",system-ui,sans-serif;
  -webkit-font-smoothing:antialiased;
  /* THE TAB BAR IS FIXED, so the last row of every screen would sit under it without this. */
  padding-bottom:calc(64px + env(safe-area-inset-bottom,0px));}
a{color:inherit;text-decoration:none}
.wrap{max-width:620px;margin:0 auto;padding:0 16px}

/* ── the title bar ─────────────────────────────────────────────────────────────────────────
   STICKY, NOT FIXED, and it clears the notch itself: installed as a PWA there is no browser
   chrome above it, so this bar IS the status area and must not sit under the system clock. */
.bar{position:sticky;top:env(safe-area-inset-top,0px);z-index:10;background:var(--tab-bg);
  backdrop-filter:saturate(180%) blur(20px);border-bottom:1px solid var(--hair)}
.bar-in{display:flex;align-items:baseline;gap:10px;padding:13px 16px;max-width:620px;margin:0 auto}
.brand{font-weight:650;letter-spacing:-.015em}
.day{margin-left:auto;font-size:13px;color:var(--dimmer)}
h1{font-size:22px;font-weight:670;letter-spacing:-.022em;margin:18px 0 2px;color:var(--ink)}
h1 .chan{vertical-align:middle}
.sub{color:var(--dim);font-size:15px;margin:0 0 14px}

/* ── the briefing ──────────────────────────────────────────────────────────────────────────
   KINSO'S PHONE SCREEN IS A BRIEFING — "Good morning, Sarah. You've got 4 new and 9 active
   conversations." Ours already was one; this gives it their shape: a sentence, not a dashboard,
   with the numbers carried in the accent inside running text rather than parked in tiles. */
.head{padding:8px 2px 18px}
.head .v{font-size:34px;line-height:1.15;font-weight:680;letter-spacing:-.03em}
.head .v em{font-style:normal;color:var(--accent)}
.head .l{margin-top:6px;color:var(--dim);font-size:15px}

/* ── grouped lists (Apple) on a tinted ground (Kinso) ──────────────────────────────────────
   Rows live in ONE rounded white card on a grey ground, the iOS inset-grouped table. Kinso
   separates rows with whitespace rather than rules, so the hairline is barely there. */
.card{background:var(--surface);border-radius:16px;padding:2px 14px;margin-top:10px;
  box-shadow:var(--lift)}
.row{display:flex;gap:12px;align-items:baseline;padding:13px 0;border-bottom:1px solid var(--hair)}
.row:last-child{border-bottom:0}
.row .n{font-variant-numeric:tabular-nums;font-weight:650;min-width:2.2em}
.row .t{color:var(--dim);font-size:15px}
.needs{background:var(--bad-soft);box-shadow:none;border:1px solid var(--bad)}
.needs .t{color:var(--ink)}
.figs{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px}
.fig{background:var(--surface);border-radius:16px;padding:15px;box-shadow:var(--lift)}
.fig .v{font-size:23px;font-weight:660;font-variant-numeric:tabular-nums}
.fig .l{margin-top:3px;color:var(--dim);font-size:13px;line-height:1.35}
.quiet{color:var(--dim);font-size:15px;padding:18px 2px;line-height:1.55}
.foot{margin-top:26px;color:var(--dimmer);font-size:13px}
.foot a{color:var(--accent)}

/* ── a conversation row, copied from Kinso ─────────────────────────────────────────────────
   Their structure exactly: avatar, name with the time right beside it, one grey line of preview
   under it, and the channel's own logo far right. 44px minimum and the whole row is the link. */
.conv{display:grid;grid-template-columns:auto 1fr auto;grid-template-rows:auto auto;
  gap:2px 12px;padding:11px 0;min-height:44px;border-bottom:1px solid var(--hair);align-items:center}
.conv:last-child{border-bottom:0}
.conv .av{grid-row:1/3;width:42px;height:42px;border-radius:50%;display:flex;align-items:center;
  justify-content:center;font-size:15px;font-weight:600;letter-spacing:.01em;
  background:var(--accent-soft);color:var(--accent);flex:none;
  /* NO PHOTO EXISTS. The vendor sends us a display name and nothing else, so initials are not a
     placeholder for an avatar we failed to load — they are the avatar, the way Apple's Messages
     draws a contact with no picture. */}
.conv .w{grid-row:1;grid-column:2;font-weight:570;font-size:15.5px;letter-spacing:-.01em;
  display:flex;align-items:baseline;gap:7px;min-width:0}
.conv .w b{font-weight:590;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.conv .t{font-weight:400;font-size:13px;color:var(--dimmer);flex:none}
.conv .s{grid-row:2;grid-column:2;color:var(--dim);font-size:14px;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap;min-width:0}
.conv .mk{grid-row:1/3;grid-column:3;flex:none;display:flex;align-items:center}
.conv.out .av{background:var(--bubble-in);color:var(--dimmer)}

/* The channel mark. Why it is a logo and not a word: see `_mark`. */
.mk svg{display:block}
/* VISUALLY HIDDEN, STILL READ ALOUD. The mark is a shape, so it separates without colour — but a
   shape is not a name, and a screen reader announcing "image" is not an answer to "which channel
   is this". The word travels with every mark and costs no pixels. */
.vh{position:absolute;width:1px;height:1px;margin:-1px;padding:0;overflow:hidden;
  clip:rect(0 0 0 0);clip-path:inset(50%);white-space:nowrap;border:0}
.chan{display:inline-flex;align-items:center;gap:5px;vertical-align:middle;margin-left:7px;
  color:var(--dim);background:var(--bg);border-radius:7px;padding:3px 8px;font-size:12px;
  font-weight:560;letter-spacing:.01em;white-space:nowrap}

/* ── channel chips ─────────────────────────────────────────────────────────────────────────
   Kinso's LEFT RAIL is a desktop idiom; on a phone the same job is a scrolling row. */
.chips{display:flex;gap:8px;margin:12px 0 2px;overflow-x:auto;padding:2px 0 6px;
  -webkit-overflow-scrolling:touch;scrollbar-width:none}
.chips::-webkit-scrollbar{display:none}
.chip{flex:0 0 auto;display:inline-flex;align-items:center;gap:6px;color:var(--dim);
  background:var(--surface);border-radius:999px;padding:7px 13px;font-size:14px;font-weight:540;
  white-space:nowrap;box-shadow:var(--lift)}
.chip.on{color:var(--accent-ink);background:var(--accent)}
.chip.on .n{color:var(--accent-ink);opacity:.75}
.chip .n{color:var(--dimmer);font-size:12px;font-variant-numeric:tabular-nums}

/* ── the thread ────────────────────────────────────────────────────────────────────────────
   Theirs left, ours right — the iMessage shape, which is the one everybody already reads. */
.thread{margin-top:14px;display:flex;flex-direction:column;gap:3px}
.msg{max-width:82%}
.msg.in{align-self:flex-start}
.msg.out{align-self:flex-end;text-align:right}
.msg .b{background:var(--bubble-in);border-radius:19px;padding:9px 14px;white-space:pre-wrap;
  overflow-wrap:anywhere;font-size:16px;line-height:1.35;text-align:left}
.msg.out .b{background:var(--bubble-out);color:var(--bubble-out-ink)}
.msg .m{margin-top:3px;margin-bottom:9px;color:var(--dimmer);font-size:12px}

/* ── the reply box ─────────────────────────────────────────────────────────────────────────
   16px IS NOT A STYLE CHOICE: iOS Safari zooms the page when a focused input is under 16px, and
   on a thread that shunts the conversation off screen the moment he taps to answer. */
.compose{margin-top:18px;display:flex;flex-direction:column;gap:9px}
.compose textarea{width:100%;font:inherit;font-size:16px;line-height:1.45;color:var(--ink);
  background:var(--surface);border:1px solid var(--line);border-radius:18px;padding:12px 15px;
  resize:vertical;min-height:78px;box-shadow:var(--lift)}
.compose textarea:focus{outline:2px solid var(--accent-line);outline-offset:1px;
  border-color:transparent}
.compose .btn{align-self:flex-end}
.btn{font:inherit;font-size:15px;font-weight:570;border:0;border-radius:999px;padding:11px 22px;
  background:var(--accent);color:var(--accent-ink);cursor:pointer;min-height:44px}
.drafted{color:var(--dim);font-size:13px;display:flex;align-items:center;gap:7px}
.drafted::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--accent);
  flex:none}

/* ── the tab bar ───────────────────────────────────────────────────────────────────────────
   THE PWA DECISION. Installed to a home screen there is no browser chrome, so the app supplies
   its own furniture, and on a phone that furniture is a bottom bar — thumb-reachable, the idiom
   every native app on the device already uses. It clears the home indicator with the safe-area
   inset rather than a guessed margin. */
.tabs{position:fixed;left:0;right:0;bottom:0;z-index:20;background:var(--tab-bg);
  backdrop-filter:saturate(180%) blur(20px);border-top:1px solid var(--hair);
  padding-bottom:env(safe-area-inset-bottom,0px)}
.tabs-in{max-width:620px;margin:0 auto;display:flex}
.tab{flex:1;display:flex;flex-direction:column;align-items:center;gap:3px;padding:9px 0 7px;
  min-height:52px;color:var(--dimmer);font-size:10.5px;font-weight:540;letter-spacing:.01em}
.tab svg{display:block}
.tab.on{color:var(--accent)}

/* ── settings ──────────────────────────────────────────────────────────────────────────────
   Where the light/dark switch lives. Server-rendered like everything else: the switch is a LINK
   that sets a cookie, because this app runs its screens without client JavaScript and a theme
   that needs a script is a theme that flashes the wrong one first. */
.seg{display:flex;gap:4px;background:var(--bg);border-radius:12px;padding:4px;margin-top:4px}
.seg a{flex:1;text-align:center;padding:9px 0;border-radius:9px;font-size:14.5px;
  font-weight:540;color:var(--dim);min-height:44px;display:flex;align-items:center;
  justify-content:center}
.seg a.on{background:var(--surface);color:var(--ink);box-shadow:var(--lift)}
.setrow{display:flex;flex-direction:column;gap:2px;padding:14px 0;border-bottom:1px solid var(--hair)}
.setrow:last-child{border-bottom:0}
.setrow b{font-weight:570;font-size:15.5px}
.setrow span{color:var(--dim);font-size:13.5px;line-height:1.45}

/* ── the install steps ─────────────────────────────────────────────────────────────────────*/
.flow{margin-top:16px}
.step{display:grid;grid-template-columns:auto 1fr;gap:12px;align-items:start}
.stepn{width:29px;height:29px;border-radius:999px;display:flex;align-items:center;
  justify-content:center;font-weight:650;font-size:14px;background:var(--accent-soft);
  color:var(--accent);flex:none}
.stepb{background:var(--surface);border-radius:14px;padding:13px 15px;min-width:0;
  box-shadow:var(--lift)}
.steph b{font-size:15px}
.stepb p{color:var(--dim);font-size:14px;margin:5px 0 0}
.stepjoin{width:1px;height:14px;margin:4px 0 4px 14px;background:var(--line)}

@media (min-width:560px){ .figs{grid-template-columns:1fr 1fr 1fr} }
@media (prefers-reduced-motion:reduce){ *{animation:none!important;transition:none!important} }
"""


# THE ONLY SCRIPT ON THIS APP, and it does two things: register the worker, and report whether
# this is running installed. Nothing renders through it — every screen here is server-rendered, so
# a person with script blocked still reads their inbox.
JS = """
(function () {
  // EXPLICIT SCOPE, though the file's own location already implies it. MDN: a worker cannot claim
  // a scope broader than where it is served, so /voice/sw.js controls /voice/ and nothing else —
  // which is what we want. It is passed anyway because the failure mode is SILENT: registration
  // succeeds, reports success, and the worker never intercepts a thing.
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/voice/sw.js', { scope: '/voice/' }).catch(function () {});
  }
  // DID THE INSTALL ACTUALLY HAPPEN. Spec section 4: a buyer who never completes it has bought a
  // bookmark, so the completion rate is the number that says whether this feature worked at all.
  // standalone is how the page knows: iOS sets navigator.standalone, everyone else answers the
  // display-mode query. Reported once per load with sendBeacon so it cannot delay a paint.
  try {
    var installed = (window.navigator.standalone === true) ||
      (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches);
    if (installed && navigator.sendBeacon) { navigator.sendBeacon('/voice/installed'); }
  } catch (e) {}
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
# Three destinations because the app has three: the briefing, the conversations, and the settings
# that hold the light/dark switch. A tab bar with a dead tab in it is the dead control this
# codebase keeps deleting.
_TABS = (
  ("/voice/", "Today",
   "M3 10.5 12 3l9 7.5M5.5 9.5V20h13V9.5"),
  ("/voice/inbox", "Inbox",
   "M3 13.5h5l1.5 2.5h5L16 13.5h5M3 13.5 5.5 5h13L21 13.5V19H3z"),
  ("/voice/settings", "Settings",
   "M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-2.7 1.1v.3a2 2 0 1 1-4 0v-.2a1.6 1.6 0 0 0-2.8-1.1l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0-1.1-2.7H3a2 2 0 1 1 0-4h.2A1.6 1.6 0 0 0 4.3 6l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 2.7-1.1V2a2 2 0 1 1 4 0v.2a1.6 1.6 0 0 0 2.7 1.1l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0 1.1 2.7h.3a2 2 0 1 1 0 4h-.2a1.6 1.6 0 0 0-1.1 1.1z"),
)


def _tabbar(here: str) -> str:
    out = []
    for href, label, d in _TABS:
        on = " on" if (here == href or (href != "/voice/" and here.startswith(href))) else ""
        cur = ' aria-current="page"' if on else ""
        out.append(
            f'<a class="tab{on}" href="{href}"{cur}>'
            f'<svg width="23" height="23" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            f'<path d="{d}"/></svg><span>{label}</span></a>')
    return f'<nav class="tabs" aria-label="Sections"><div class="tabs-in">{"".join(out)}</div></nav>'


THEME_COOKIE = "aios_voice_theme"
_THEME_BG = {"light": "#eff2f4", "dark": "#0d0d0d"}


def _theme() -> str:
    """'light' | 'dark' | '' — empty means he has never chosen and the OS decides."""
    try:
        v = (request.cookies.get(THEME_COOKIE) or "").strip().lower()
    except Exception:                            # noqa: BLE001 — no request, no cookie
        return ""
    return v if v in ("light", "dark") else ""


def _shell(body: str, *, day: str = "", here: str = "") -> str:
    brand = dash.brand()
    # HIS CHOICE IS STAMPED ON <html>, so the switch beats the OS in BOTH directions. No stamp at
    # all is the honest third state: he has never chosen, so prefers-color-scheme decides.
    th = _theme()
    stamp = f' data-theme="{th}"' if th else ""
    # THE STATUS BAR HAS TO MOVE TOO. Installed, iOS paints the area behind the clock with
    # theme-color; leaving it at the dark value puts a black band above a white app. Unstamped,
    # both are declared with a media attribute and the OS picks.
    if th:
        tc = f'<meta name="theme-color" content="{_THEME_BG[th]}">'
    else:
        tc = (f'<meta name="theme-color" media="(prefers-color-scheme: light)" content="{_THEME_BG["light"]}">'
              f'<meta name="theme-color" media="(prefers-color-scheme: dark)" content="{_THEME_BG["dark"]}">')
    return f"""<!doctype html><html lang="en"{stamp}><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow">
{tc}
<link rel="manifest" href="/voice/manifest.webmanifest">
<link rel="apple-touch-icon" href="/voice/icon-192.png">
<!-- APPLE STILL READS ITS OWN META. The manifest's `display` is what MDN says iOS requires before
     `Notification` even exists, and this legacy pair is what older iOS reads for the same thing.
     Both cost one line and the failure they prevent is silent. -->
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<title>{_esc(brand)} · Unified Inbox</title><style>{CSS}</style></head><body>
<div class="bar"><div class="bar-in"><span class="brand">{_esc(brand)}</span>
<span class="day">{_esc(day)}</span></div></div>
<div class="wrap">{body}</div>
{_tabbar(here or request.path)}
<script>{JS}</script></body></html>"""


# ── Today ───────────────────────────────────────────────────────────────────────────────────
def _rows(items: list, *, needs: bool = False) -> str:
    """A list of {text, value?} as rows. NO INVENTED NUMBER: an item with no `value` renders as a
    sentence, not as a zero — the same rule the morning review keeps, because "0" and "nothing to
    report" are different claims and a dashboard that conflates them starts lying quietly."""
    out = []
    for it in items:
        n = it.get("value")
        cell = f'<span class="n">{_esc(n)}</span>' if n is not None else ""
        out.append(f'<div class="row">{cell}<span class="t">{_esc(it.get("text"))}</span></div>')
    return f'<div class="card{" needs" if needs else ""}">{"".join(out)}</div>'


@blueprint.get("/voice/")
@blueprint.get("/voice")
def r_today():
    """WHAT HAPPENED TODAY, off the report this machine already writes.

    `marketing/customer_voice/report.report(day)` is the source and it is unchanged — this screen
    adds no figure of its own and computes nothing. That is deliberate: the 8am message and this
    page must never be able to disagree, and the only way to guarantee that is one producer.

    A 500 HERE IS WORSE THAN A THIN PAGE. The report reads a dozen tables and a box mid-migration
    can be missing one; the lead app's front page 500'd exactly that way on `daily_reports`. So the
    read is guarded and the screen says it could not read rather than showing a stack trace.
    """
    from core.report import today as _today
    day = _today()
    label = day.strftime("%a %-d %b") if isinstance(day, date) else str(day)
    try:
        from marketing.customer_voice.report import report as _report
        v = _report(day)
    except Exception as e:                       # noqa: BLE001 — the front page outranks the cause
        log.warning("voice.report_unreadable", extra={"error": f"{type(e).__name__}: {e}"[:160]})
        body = ('<h1>Today</h1><div class="head"><div class="v">—</div>'
                '<div class="l">Today\'s report could not be read on this box. '
                'Nothing has been lost; the next poll writes it again.</div></div>')
        return _shell(body, day=label), 200

    head = v.get("headline") or {}
    parts = [f'<h1>Today</h1><div class="head">'
             f'<div class="v">{_esc(head.get("value") or "—")}</div>'
             f'<div class="l">{_esc(head.get("label") or "")}</div></div>']

    # ORDER IS THE MESSAGE: what needs him, then what happened, then what to keep an eye on. The
    # report already ranks them that way for the 8am send and this screen does not re-sort them.
    if v.get("needs_you"):
        parts.append('<h1>Needs you</h1>' + _rows(v["needs_you"], needs=True))

    figs = v.get("figures") or {}
    if figs:
        cells = "".join(f'<div class="fig"><div class="v">{_esc(f.get("value"))}</div>'
                        f'<div class="l">{_esc(f.get("label"))}</div></div>'
                        for f in figs.values())
        parts.append(f'<div class="figs">{cells}</div>')

    if v.get("happened"):
        parts.append('<h1>What happened</h1>' + _rows(v["happened"]))
    if v.get("watch"):
        parts.append('<h1>Worth watching</h1>' + _rows(v["watch"]))

    # A QUIET DAY SAYS SO, ONCE. An empty screen reads as a broken app, which is the single most
    # expensive thing a page like this can do — it is the defect the lead app spent two days
    # removing from every one of its branches.
    if not any(v.get(k) for k in ("needs_you", "happened", "watch")) and not figs:
        parts.append('<div class="quiet">Nothing has come through yet today. '
                     'The rails you own are being polled; the first thing they find appears here.'
                     '</div>')

    parts.append('<div class="foot">Every figure here is read from your own rails. '
                 'This is the same report that goes out at 8am, on the screen instead of in an '
                 'inbox.</div>')
    return _shell("".join(parts), day=label), 200

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
        try:
            from core.report import tz
            d = d.astimezone(tz())
        except Exception as e:                   # noqa: BLE001 — a clock never 500s a page
            log.warning("voice.local_time_unavailable",
                        extra={"error": f"{type(e).__name__}: {e}"[:120]})
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
    try:
        from core.report import tz
        d = d.astimezone(tz())
    except Exception:                            # noqa: BLE001 — a clock never 500s a page
        pass
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
    return "/voice/inbox/" + _q(str(zcid), safe="")


# A NAME A PERSON USES, for a value the vendor spells its own way. Unknown platforms fall through
# to the stored string rather than to "Other": a channel this box is receiving and cannot name is
# a thing to SHOW and fix, not to hide behind a bucket.
_CHANNEL_NAMES = {"messenger": "Messenger", "instagram": "Instagram", "email": "Email",
                  "sms": "SMS", "whatsapp": "WhatsApp", "review": "Reviews",
                  "comment": "Comments"}


def _channel(value: str) -> str:
    v = str(value or "").strip().lower()
    return _CHANNEL_NAMES.get(v, v.title() if v else "Unknown")


def _chips(space: str, current: str) -> str:
    """All, then one chip per channel THIS BOX ACTUALLY HAS. Never a menu of hopes.

    ONE CHANNEL IS NOT A CHOICE, so a box with only Messenger renders no chip row at all — a
    filter offering a single option is a control that cannot change anything, and this app keeps
    deleting those rather than shipping them greyed out.
    """
    try:
        from marketing.customer_voice.inbox import store as _store
        present = _store.platforms_present(space)
    except Exception as e:                       # noqa: BLE001 — a missing chip row is not a 500
        log.warning("voice.chips_unreadable", extra={"error": f"{type(e).__name__}: {e}"[:160]})
        return ""
    if len(present) < 2:
        return ""
    from urllib.parse import quote as _q
    out = [f'<a class="chip{"" if current else " on"}" href="/voice/inbox">All</a>']
    for row in present:
        pid = str(row["platform"] or "")
        on = " on" if pid == current else ""
        out.append(f'<a class="chip{on}" href="/voice/inbox?channel={_esc(_q(pid, safe=""))}">'
                   f'{_esc(_channel(pid))} <span class="n">{row["n"]}</span></a>')
    return f'<div class="chips">{"".join(out)}</div>'


@blueprint.get("/voice/inbox")
def r_inbox():
    """Who has spoken to this business, most recent first."""
    space = _space()
    # THE CHIP THE READER IS ON. Passed to the store as a bound predicate, never interpolated;
    # an unknown value simply matches no rows, which is the honest answer to a hand-typed URL.
    channel = (request.args.get("channel") or "").strip().lower()[:40]
    try:
        from marketing.customer_voice.inbox import store as _store
        convs = _store.list_conversations(space, limit=50, platform=channel or None)
    except Exception as e:                       # noqa: BLE001 — a page, never a stack trace
        log.warning("voice.inbox_unreadable", extra={"error": f"{type(e).__name__}: {e}"[:160]})
        return _shell('<h1>Inbox</h1><div class="quiet">The inbox could not be read on this box. '
                      'Nothing has been lost.</div>'), 200

    if not convs:
        # QUIET IS NOT BROKEN, and on this screen the difference matters more than anywhere else:
        # a business with no messages yet is the normal first week, not a fault.
        #
        # TWO DIFFERENT EMPTIES, and telling them apart is the whole job of this branch. "Nothing
        # has ever arrived" and "nothing has arrived ON INSTAGRAM" look identical and mean
        # opposite things — the second one still has a working inbox one tap away, and a person
        # shown the first message would reasonably conclude the box is broken.
        if channel:
            body = (f'<h1>Inbox</h1>{_chips(space, channel)}'
                    f'<div class="quiet">Nothing on {_esc(_channel(channel))} yet. '
                    'Other channels may have messages — tap <b>All</b>.</div>')
        else:
            body = ('<h1>Inbox</h1><div class="quiet">No conversations yet. '
                    'The first person who messages you appears here, and you will get a '
                    'notification once this is installed on your phone.</div>')
        return _shell(body), 200

    rows = []
    for k in convs:
        who = (k.get("participant") or "").strip() or "Someone"
        n = k.get("message_count") or 0
        when = _ago(k.get("last_inbound_at"))
        flags = []
        if k.get("opted_out"):
            # SAID SO ON THE ROW, because replying to someone who opted out is the one mistake
            # this screen can help him make.
            flags.append("opted out")
        if k.get("ad_title"):
            flags.append(f'from {k["ad_title"]}')
        sub = " · ".join([f'{n} message' + ("" if n == 1 else "s")] + flags)
        # KINSO'S ROW, EXACTLY: avatar, name with the time beside it, one grey line under it, and
        # the channel's own logo far right. The mark is a SHAPE before it is a colour, so it still
        # separates in greyscale — and the channel's word is one tap away in the thread header, so
        # nothing here is carried by colour alone.
        plat = k.get("platform")
        rows.append(f'<a class="conv" href="{_esc(_thread_href(k.get("zernio_conversation_id")))}">'
                    f'<span class="av" aria-hidden="true">{_monogram(who)}</span>'
                    f'<span class="w"><b>{_esc(who)}</b><span class="t">{_esc(when)}</span></span>'
                    f'<span class="s">{_esc(sub)}</span>'
                    f'<span class="mk">{_mark(plat)}'
                    f'<span class="vh">{_esc(_channel(plat))}</span></span></a>')
    return _shell(f'<h1>Inbox</h1>{_chips(space, channel)}'
                  f'<div class="card">{"".join(rows)}</div>'), 200


@blueprint.get("/voice/inbox/<path:zcid>")
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

    who = (conv.get("participant") or "").strip() or "Someone"
    # THE CHANNEL, IN THE HEADER. Answering an Instagram DM as though it were an email is a
    # category of mistake this screen should make impossible, and the only way it does that is by
    # saying which channel this is before he starts typing.
    _p = conv.get("platform")
    head = [f'<h1>{_esc(who)} <span class="chan">{_esc(_channel(_p))}{_mark(_p, 15)}</span></h1>']
    if conv.get("opted_out"):
        head.append('<div class="card needs"><div class="row"><span class="t">'
                    'This person has opted out. Nothing is sent to them.</span></div></div>')
    if conv.get("ad_title"):
        head.append(f'<div class="card"><div class="row"><span class="t">Came from '
                    f'<b>{_esc(conv["ad_title"])}</b></span></div></div>')

    if not msgs:
        head.append('<div class="quiet">No messages have been mirrored for this conversation '
                    'yet.</div>')
        return _shell("".join(head)), 200

    # WHO SAID IT, ON EVERY LINE. `sent_by` is contact | ai | human, and on a screen where the
    # machine may have answered on his behalf, not saying which is which is the one thing that
    # would make him distrust the whole surface.
    said = {"contact": "them", "ai": "the machine", "human": "you"}
    bubbles = []
    for m in msgs:
        inbound = str(m.get("direction") or "") == "in"
        by = said.get(str(m.get("sent_by") or ""), "them" if inbound else "you")
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
        bubbles.append(
            f'<div class="msg {"in" if inbound else "out"}">'
            f'<div class="b">{_esc(m.get("body") or "")}</div>'
            f'<div class="m">{_esc(by)} · {_esc(_when(m.get("created_at")))}</div></div>')
    back = '<div class="foot"><a href="/voice/inbox">← Inbox</a></div>'
    return _shell("".join(head) + f'<div class="thread">{"".join(bubbles)}</div>'
                  + _compose(zcid, conv) + back), 200


def _compose(zcid: str, conv: dict) -> str:
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
    if not (conv.get("account_id") or "").strip():
        return ('<div class="card"><div class="row"><span class="t quiet">This thread was '
                'mirrored before the box started recording which account owns it, so it cannot '
                'be replied to from here yet. The next message on it fixes that.</span></div>'
                '</div>')
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
    drafted, note = "", ""
    try:
        from marketing.customer_voice.drafter import store as _drafts
        row = _drafts.latest_for(_space(), zcid)
        if row:
            drafted = str(row.get("body") or "")
            note = '<div class="drafted">Drafted for you — read it before you send.</div>'
    except Exception as e:                       # noqa: BLE001 — no draft is not a broken page
        log.info("voice.draft_unreadable", extra={"error": type(e).__name__})
    return ('<form class="compose" method="post" action="' + _esc(f"/voice/inbox/{zcid}/reply")
            + '">'
            f'<input type="hidden" name="n" value="{_esc(_reply.new_nonce())}">'
            + note +
            '<textarea name="text" rows="3" maxlength="1800" required '
            f'placeholder="Write a reply…">{_esc(drafted)}</textarea>'
            '<button class="btn" type="submit">Send</button>'
            '</form>')


# NOT `@blueprint.post`. `tests/test_customer_voice.py:498-500` scans this department for a CALL
# named `post` and a decorator is a call — `app.py:659` took the same two extra characters for
# the same reason, and weakening the guard to fit a feature is refused by name at `app.py:655`.
@blueprint.route("/voice/inbox/<path:zcid>/reply", methods=["POST"])
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
    here = f"/voice/inbox/{zcid}"

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
    # PRG: redirect after post, so a refresh cannot re-submit the form at all.
    return redirect(here, code=303)


def _thread_notice(zcid: str, kind: str, message: str) -> str:
    """Say what happened, on the thread, without losing the thread."""
    return _shell(f'<div class="card {_esc(kind)}"><div class="row"><span class="t">'
                  f'{_esc(message)}</span></div></div>'
                  f'<div class="foot"><a href="/voice/inbox/{_esc(zcid)}">← Back to the '
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
# number: the manifest is a colour and a title, the icons are drawn from constants in this module,
# and the worker is our own code. `start_url` still points at `/voice/`, which IS gated — so
# opening the installed app asks for the password exactly as the browser does.
PUBLIC_PATHS = frozenset({
    "/voice/manifest.webmanifest",
    "/voice/icon-192.png",
    "/voice/icon-512.png",
    "/voice/sw.js",
})

THEME = "#0b0d10"
ICON_BG = (11, 13, 16)
ICON_FG = (125, 211, 252)


# COMPUTED ONCE PER PROCESS. The 512px icon is ~262k pixels of pure-Python loop (~80 ms on a
# laptop, more on the one-vCPU box) on a route that is deliberately unauthenticated; a browser
# caches it for a day, but a stranger fetching it in a loop should not be able to buy CPU.
# ── settings, and the light/dark switch ─────────────────────────────────────────────────────
@blueprint.get("/voice/settings")
def r_settings():
    """The third tab. It exists because the switch needs somewhere to live that is not a thread.

    THE SWITCH IS A LINK, NOT A SCRIPT. Every screen in this app is server-rendered so that a
    person with JavaScript blocked still reads their inbox, and a theme applied by script has a
    second problem beyond that: the page paints the old theme first and then repaints, which is
    the flash every JS theme toggle on the web has to work around. A cookie read during render
    has neither failure.
    """
    cur = _theme()
    def seg(value, label):
        on = " on" if cur == value else ""
        return f'<a class="seg-a{on}" href="/voice/theme?to={value}">{label}</a>'
    switch = ('<div class="seg">'
              + seg("light", "Light") + seg("dark", "Dark")
              + f'<a class="seg-a{"" if cur else " on"}" href="/voice/theme?to=system">System</a>'
              + '</div>').replace("seg-a", "")
    body = (
      '<h1>Settings</h1>'
      '<div class="card">'
      '<div class="setrow"><b>Appearance</b>'
      '<span>System follows your phone, including its own light and dark schedule.</span>'
      f'{switch}</div>'
      '<div class="setrow"><b>On your home screen</b>'
      '<span>Installed, this opens without a browser around it and can notify you. '
      '<a href="/voice/install" style="color:var(--accent)">Show me how</a>.</span></div>'
      '<div class="setrow"><b>How this stays current</b>'
      '<span>The box checks for new messages on a schedule rather than holding a connection '
      'open. Pull down to check now.</span></div>'
      '</div>')
    return _shell(body), 200


@blueprint.get("/voice/theme")
def r_theme():
    """Set, or clear, the appearance cookie — then go back to settings.

    `system` DELETES the cookie rather than storing the word, so "follow the phone" is the absence
    of a preference and not a third value every reader has to know about. A value we did not write
    is ignored by `_theme` and cleared here, so a hand-typed URL cannot wedge the app into a theme
    that has no tokens.
    """
    to = (request.args.get("to") or "").strip().lower()
    resp = redirect("/voice/settings", code=303)
    if to in ("light", "dark"):
        resp.set_cookie(THEME_COOKIE, to, max_age=365 * 86400, samesite="Lax",
                        secure=request.is_secure, httponly=True, path="/voice")
    else:
        resp.delete_cookie(THEME_COOKIE, path="/voice")
    return resp


@functools.lru_cache(maxsize=4)
def _png(size: int) -> bytes:
    """A square PNG, drawn here rather than shipped as a file.

    WHY BYTES IN THE MODULE. Invariant 7 and spec §1.2: a clone installs this department and gets
    a working app with no dependency on `sites/`, which is a different deploy entirely. The crest
    precedent inlines an image as a data URI to avoid adding a route; that cannot work here,
    because a manifest's `icons` entries are URLs the browser fetches.

    WRITTEN BY HAND because Pillow is not a dependency of this box — `test_box_boots` fails on a
    missing PIL today, so importing it here would take the whole app down on exactly the machine
    this ships to. A PNG is a signature, an IHDR, an IDAT of zlib-compressed filtered rows and an
    IEND; that is little enough code to be worth not adding a dependency for.

    IT IS A PLACEHOLDER AND SAYS SO. A flat mark on the brand ground, safe inside the maskable
    circle so Android's mask cannot crop it. It should be replaced by a real drawn icon before
    this is put in front of a buyer, and that is named in the PR rather than left to be noticed.
    """
    import struct
    import zlib

    cx = cy = size / 2
    # The mark sits inside 40% of the width, which keeps it within the maskable safe zone (the
    # inner 80% circle) on every Android launcher shape.
    r_out, r_in = size * 0.30, size * 0.17
    rows = bytearray()
    for y in range(size):
        rows.append(0)                           # filter type 0 (None) for each scanline
        for x in range(size):
            dx, dy = x + 0.5 - cx, y + 0.5 - cy
            d = (dx * dx + dy * dy) ** 0.5
            rows.extend(ICON_FG if r_in <= d <= r_out else ICON_BG)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)   # 8-bit truecolour RGB
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


@blueprint.get("/voice/manifest.webmanifest")
def r_manifest():
    """WHAT MAKES IT INSTALLABLE AT ALL. `display: standalone` is not decoration: MDN's
    compatibility data says the `Notification` interface is undefined on iOS unless the page is a
    home-screen app AND the manifest has a non-default `display`. Without this line there is no
    push on an iPhone, ever."""
    import json
    from flask import Response
    m = {
        "name": f"{dash.brand()} · Unified Inbox",
        "short_name": "Unified Inbox",
        "start_url": "/voice/",
        "scope": "/voice/",
        "display": "standalone",
        "background_color": THEME,
        "theme_color": THEME,
        "icons": [
            # BOTH SIZES, because Chrome's installability criteria want a 192 and a 512, and
            # `maskable` so an Android launcher crops our own safe zone rather than a square.
            {"src": "/voice/icon-192.png", "sizes": "192x192", "type": "image/png",
             "purpose": "any maskable"},
            {"src": "/voice/icon-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "any maskable"},
        ],
    }
    return Response(json.dumps(m), mimetype="application/manifest+json")


@blueprint.get("/voice/icon-192.png")
def r_icon_192():
    from flask import Response
    return Response(_png(192), mimetype="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


@blueprint.get("/voice/icon-512.png")
def r_icon_512():
    from flask import Response
    return Response(_png(512), mimetype="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


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
  var title = d.title || 'Unified Inbox';
  var body = d.body || 'Something new came in.';
  event.waitUntil(self.registration.showNotification(title, {
    body: body,
    icon: '/voice/icon-192.png',
    badge: '/voice/icon-192.png',
    data: { navigate: d.navigate || '/voice/inbox' }
  }));
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  var to = (event.notification.data && event.notification.data.navigate) || '/voice/inbox';
  // ONLY OUR OWN APP. The payload is authored by the box and encrypted to this subscription, so
  // this is defence in depth, not a fix — the same rule safe_next applies on the way in.
  if (typeof to !== 'string' || to.indexOf('/voice/') !== 0) { to = '/voice/inbox'; }
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true })
    .then(function (list) {
      for (var i = 0; i < list.length; i++) {
        if (list[i].url.indexOf('/voice/') !== -1 && 'focus' in list[i]) {
          list[i].navigate(to); return list[i].focus();
        }
      }
      return self.clients.openWindow(to);
    }));
});
"""


@blueprint.get("/voice/sw.js")
def r_sw():
    """SCOPE IS THE TRAP. MDN: a worker cannot have a scope broader than its own location unless
    the server sends `Service-Worker-Allowed`. Served at `/voice/sw.js` it controls `/voice/*` and
    nothing else, which is exactly what we want — so no header is needed, but the registration
    below still passes `{scope: '/voice/'}` explicitly, because the failure mode is silent: it
    registers, reports success, and never intercepts a thing."""
    from flask import Response
    return Response(SW_JS, mimetype="application/javascript",
                    headers={"Cache-Control": "no-cache"})


# `methods=["POST"]` RATHER THAN `@blueprint.post`, deliberately. test_customer_voice bans a
# call named `post` anywhere in this department — the structural guarantee that Customer
# Voice reads and drafts but cannot publish. A Flask route decorator is the opposite of
# publishing (it registers an INBOUND route) so this is a false positive on the name, but the
# guard is worth more than the two characters it costs me to avoid it. Weakening a "cannot
# publish" rule to fit a beacon would be a bad trade at any price.
@blueprint.route("/voice/installed", methods=["POST"])
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


@blueprint.get("/voice/install")
def r_install():
    """TEACH THE INSTALL, ON THE PHONE, IN THE FLOW — not in a support article.

    THIS SCREEN EXISTS BECAUSE APPLE GIVES US NO PROMPT. `beforeinstallprompt` is Chrome-only
    (MDN: safari NO, safari_ios NO) and web.dev states it plainly: "A browser prompt to install
    your PWA doesn't exist On iOS and iPadOS." So on an iPhone the only path is Share, scroll, Add
    to Home Screen — and every person who does not finish that gets a page that CANNOT notify
    them, which is the exact outcome the owner predicted when he asked for this.

    AND IT IS WORSE FOR A HALF-ADOPTER, which is why the copy says so rather than selling. WebKit
    deletes script-writable storage — service worker registrations included — after seven days of
    Safari use without interaction, and home-screen apps are explicitly exempt from that counter.
    In a tab the notifications die silently after a week. Installed, they do not.
    """
    steps_ios = [
        ("Tap <b>Share</b>", "the square with an arrow, at the bottom of Safari"),
        ("Scroll and tap <b>Add to Home Screen</b>", "it is below the row of apps"),
        ("Tap <b>Add</b>", "it appears on your home screen like any other app"),
    ]
    steps_android = [
        ("Tap the <b>⋮</b> menu", "top right of Chrome"),
        ("Tap <b>Install app</b>", "or <b>Add to Home screen</b> on older versions"),
        ("Tap <b>Install</b>", "it appears in your app drawer"),
    ]

    def block(title, steps, note):
        rows = "".join(
            f'<div class="step"><div class="stepn">{i + 1}</div>'
            f'<div class="stepb"><div class="steph"><b>{t}</b></div>'
            f'<p>{_esc(d)}</p></div></div>'
            + ('<div class="stepjoin" aria-hidden="true"></div>' if i < len(steps) - 1 else "")
            for i, (t, d) in enumerate(steps))
        return (f'<h1>{_esc(title)}</h1><div class="flow">{rows}</div>'
                f'<p class="quiet">{_esc(note)}</p>')

    body = (
        '<h1>Put this on your home screen</h1>'
        '<div class="head"><div class="v">Why it matters</div>'
        '<div class="l">Notifications only work once this is installed. In a browser tab they '
        'stop arriving after about a week of not being opened — that is the phone\\u2019s rule, '
        'not ours. Installed, they keep coming.</div></div>'
        + block("On an iPhone or iPad", steps_ios,
                "Safari has no button we can show you for this — Apple does not provide one, "
                "so these three taps are the whole path.")
        + block("On Android", steps_android,
                "Chrome may also offer this by itself when you have used the app a few times.")
        + '<div class="card"><div class="row"><span class="t">Already done it? Open the app from '
        'your home screen rather than this tab, and you are set. Nothing else to switch on.'
        '</span></div></div>'
        '<div class="foot"><a href="/voice/">← Today</a></div>')
    return _shell(body), 200
