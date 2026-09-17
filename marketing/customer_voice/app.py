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
  --bg:#eff2f4; --surface:#ffffff; --raised:#ffffff;
  /* MEASURED, NOT CHOSEN BY EYE. Every one of these clears WCAG AA (4.5:1) against BOTH grounds
     this app uses — the white card AND the grey page behind it — because a token that passes on
     one and fails on the other is a token that fails wherever you forgot to check.
     --dimmer was #9ba1a6: 2.61:1 on white, 2.32:1 on grey. It carried the timestamp on every
     conversation, the sender line inside every thread, the counts on the channel chips and the
     LABELS ON THE TAB BAR. Not a subtle failure — a bit over half the required contrast, on the
     furniture a person navigates by. tests/test_inbox_contrast.py computes these now. */
  --ink:#1a1d1f; --dim:#4e5155; --dimmer:#6a6d71;
  --line:#e5eaed; --hair:rgba(26,29,31,.07);
  /* THE ACCENT IS DEEPER THAN THE BRAND ORANGE, and this is the one change here a person will
     SEE rather than merely read more easily. #e05d38 carried white at 3.63:1 — which is the
     label on every primary button and, worse, every reply this box has sent, because an outgoing
     bubble is white on the accent. It is also used as link text throughout, at the same 3.63:1.
     #b03f1c is the same hue, deepened until ONE value fixes all three: white on it 5.88:1, it as
     text on white 5.88:1, and as the 'New' tag on its own soft ground 5.23:1.
     THE ALTERNATIVE WAS TO KEEP #e05d38 AND PUT DARK INK ON IT (4.88:1), as dark mode already
     does. That preserves the exact brand orange for fills but leaves it failing everywhere it is
     used as text, which is most places. Flagged to the owner; one line to switch back. */
  --accent:#b03f1c; --accent-ink:#ffffff; --accent-soft:#fdefe9; --accent-line:rgba(176,63,28,.45);
  /* THE OUTGOING BUBBLE IS ITS OWN TOKEN and so it kept the old orange after the accent moved —
     which is exactly the drift these tests exist to catch. It is the reply this box sent, i.e.
     the message the owner most wants to be able to read back. */
  --bubble-in:#f2f4f6; --bubble-out:#b03f1c; --bubble-out-ink:#ffffff;
  --bad:#c0392b; --bad-soft:#fdecea;
  --lift:0 1px 2px rgba(16,24,32,.05), 0 8px 24px -12px rgba(16,24,32,.18);
  --tab-bg:rgba(255,255,255,.88);
}
:root[data-theme="dark"]{
  --bg:#0d0d0d; --surface:#171717; --raised:#1f1f1f;
  /* Dark had the same hole, smaller: --dimmer was 3.35:1. Same rule, same test. */
  --ink:#f5f3f1; --dim:#a6a2a0; --dimmer:#85817f;
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
.conv .s{grid-row:2;grid-column:2;display:flex;align-items:center;gap:6px;min-width:0;
  flex-wrap:wrap;row-gap:5px;color:var(--dim);font-size:15px}
/* A TAG NEVER TRUNCATES AND NEVER OVERLAPS — IT WRAPS. Written first as one non-wrapping line,
   and a 390px render showed both failures at once: "2 messages" cut to "2 mess…" for no reason,
   and a second tag sliding straight under the channel logo, because a `flex:none` pill cannot
   shrink and simply overflowed its grid column. A half-shown tag is a half-shown fact, and
   "Opted ou" is worse than not saying it. So a busy row grows a line instead of hiding one. */
.conv .s .sub{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.conv .mk{grid-row:1/3;grid-column:3;flex:none;display:flex;align-items:center}
.conv.out .av{background:var(--bubble-in);color:var(--dimmer)}

/* ── tags, and the menu that appears on hover ──────────────────────────────────────────────
   Owner, 2026-09-16: "we're also going to add some different tags on each conversation and an
   action button menu on hover."

   THE ROW IS STILL ONE LINK AND THE MENU IS ITS SIBLING, never its child. An <a> inside an <a>
   is invalid, and browsers recover from it by SPLITTING the outer one — which is how a "Reply"
   button silently starts opening the thread instead. The wrapper carries the hairline so the
   two of them read as a single row. */
.convrow{position:relative;display:flex;align-items:center;gap:4px;
  border-bottom:1px solid var(--hair)}
.convrow:last-child{border-bottom:0}
.convrow .conv{flex:1;min-width:0;border-bottom:0}

/* FOUR MEANINGS, FOUR TREATMENTS, AND NOT ONE OF THEM CARRIED BY COLOUR ALONE — the words
   differ too, because a red pill and a grey pill are the same pill to a colourblind reader
   and this app already refuses colour-only meaning on the channel marks. */
.tag{flex:none;display:inline-flex;align-items:center;border:1px solid transparent;
  border-radius:6px;padding:1px 7px;font-size:11.5px;font-weight:620;letter-spacing:.015em;
  line-height:1.6;white-space:nowrap}
.tag.stop{color:var(--bad);background:var(--bad-soft);border-color:var(--bad-soft)}
.tag.warn{color:var(--dim);background:var(--bg);border-color:var(--accent-line)}
.tag.new{color:var(--accent);background:var(--accent-soft);border-color:var(--accent-soft)}
.tag.ad{color:var(--dim);background:var(--bg);border-color:var(--line)}

/* A <details> IS THE MENU. No library, no state in JS, keyboard-operable and open-able before
   any script has run — the same reason the rest of this app is links and forms. */
.acts{flex:none;position:relative}
.acts>summary{list-style:none;display:flex;align-items:center;justify-content:center;
  width:34px;height:34px;border-radius:9px;color:var(--dimmer);cursor:pointer}
.acts>summary::-webkit-details-marker{display:none}
.acts[open]>summary{background:var(--bg);color:var(--ink)}
/* HOVER IS THE ENHANCEMENT, NOT THE DOOR. A phone has no hover at all and this is a phone app
   first, so the button is permanently there on touch and only fades in on a real pointer. */
@media (hover:hover) and (pointer:fine){
  .acts>summary{opacity:0;transition:opacity .12s ease}
  .convrow:hover .acts>summary,.acts[open]>summary,.acts>summary:focus-visible{opacity:1}
}
@media (prefers-reduced-motion:reduce){.acts>summary{transition:none}}
.acts .menu{position:absolute;right:0;top:calc(100% + 4px);z-index:30;min-width:186px;
  background:var(--raised);border:1px solid var(--line);border-radius:12px;padding:5px;
  box-shadow:var(--lift);display:flex;flex-direction:column}
.acts .menu a{padding:9px 11px;border-radius:8px;font-size:14.5px;font-weight:540;
  white-space:nowrap}
.acts .menu a:hover,.acts .menu a:focus-visible{background:var(--bg)}

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

/* ── the search field ──────────────────────────────────────────────────────────────────────
   THE CARD SOLD THIS AND THE SCREEN DID NOT HAVE IT. `store.search_conversations` has been
   finished for weeks — space-bound, LIKE-escaped, searching message BODIES and not just the
   name — and its only caller was the connector tool. The assistant could search the buyer's
   inbox and the buyer could not.

   16px ON THE INPUT IS NOT A STYLE CHOICE. Mobile Safari zooms the whole page when a field
   smaller than 16px takes focus, and it does not zoom back out — so a 15px search box leaves a
   person on a phone looking at a magnified inbox they have to pinch their way out of. */
.find{display:flex;align-items:center;gap:9px;margin:12px 0 2px;background:var(--surface);
  border-radius:13px;padding:0 13px;box-shadow:var(--lift)}
.find svg{flex:none;display:block;color:var(--dimmer)}
.find input{flex:1;min-width:0;border:0;background:transparent;color:var(--ink);font:inherit;
  font-size:16px;padding:13px 0;-webkit-appearance:none}
/* NOT `outline:none`. Written that way first, and test_inbox_design refused it by name — this
   app's accessibility floor is that focus is never removed, only redrawn. Same two lines the
   reply box already uses, so the two fields focus identically. */
.find input:focus{outline:2px solid var(--accent-line);outline-offset:1px;border-radius:4px}
.find input::placeholder{color:var(--dimmer)}
.find input::-webkit-search-decoration,.find input::-webkit-search-cancel-button{
  -webkit-appearance:none}
.find button{flex:none;border:0;background:transparent;color:var(--accent);font:inherit;
  font-size:14.5px;font-weight:600;padding:8px 0 8px 4px;cursor:pointer}
/* QUIETER THAN THE RESULTS IT COUNTS. Set at 14.5px first and the "Show everything" link wrapped
   onto its own line reading like a call to action — the loudest thing on a screen whose job is
   the rows underneath it. */
.found{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;margin:11px 2px 0;
  color:var(--dim);font-size:13.5px;font-variant-numeric:tabular-nums}
.found a{color:var(--accent);font-weight:540}

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
  background:var(--surface);box-shadow:var(--lift);color:var(--accent);font-size:15px;
  font-weight:580}
.pg:focus-visible{outline:2px solid var(--accent-line);outline-offset:2px}

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
# Three destinations because the app has three: the briefing, the conversations, and the settings
# that hold the light/dark switch. A tab bar with a dead tab in it is the dead control this
# codebase keeps deleting.
_TABS = (
  ("/voice/", "Today",
   "M3 10.5 12 3l9 7.5M5.5 9.5V20h13V9.5"),
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
  ("/voice/inbox", "Inbox",
   "M3.5 12.5v5.6a1.4 1.4 0 0 0 1.4 1.4h14.2a1.4 1.4 0 0 0 1.4-1.4v-5.6h-4.3l-1.3 2.2H9.1"
   "l-1.3-2.2zM7.6 4.3 9.9 9.6M16.4 4.3 14.1 9.6"),
  # A COG IS A MACHINE'S ICON AND THIS IS NOT A MACHINE HE OPERATES. The old one was the
  # standard 12-tooth gear — the single most-drawn glyph in software, and the exact "basic as
  # hell" the owner named. Two sliders say what this screen is: a small number of his own
  # choices, set where he wants them. It also reads at 23px, which the gear barely did.
  ("/voice/settings", "Settings",
   "M4 8h9M17 8h3M4 16h1M9 16h11"
   "M17 8a2 2 0 1 0-4 0 2 2 0 1 0 4 0M9 16a2 2 0 1 0-4 0 2 2 0 1 0 4 0"),
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
    # HIS CHOICE IS STAMPED ON <html>. No stamp means he has never chosen, and that renders
    # WHITE — the OS is not consulted (owner, 2026-09-16: white screens first and foremost).
    th = _theme()
    stamp = f' data-theme="{th}"' if th else ""
    # THE STATUS BAR HAS TO MOVE TOO. Installed, iOS paints the area behind the clock with
    # theme-color; leaving it at the dark value puts a black band above a white app. Unstamped,
    # both are declared with a media attribute and the OS picks.
    if th:
        tc = f'<meta name="theme-color" content="{_THEME_BG[th]}">'
    else:
        # UNSTAMPED IS WHITE, not "ask the OS". Declaring the dark variant here would paint a
        # black band above a white app on an installed iOS home-screen icon — the status bar
        # following a preference the page itself no longer follows.
        tc = f'<meta name="theme-color" content="{_THEME_BG["light"]}">'
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
    """A list of {text, value?, href?} as rows. NO INVENTED NUMBER: an item with no `value` renders
    as a sentence, not as a zero — the same rule the morning review keeps, because "0" and "nothing
    to report" are different claims and a dashboard that conflates them starts lying quietly.

    AN `href` IS A LINK, and this is the half that was missing. The report writes "3 conversations
    are waiting on your reply" into `needs_you` WITH `href: /voice/inbox` — and this function
    rendered `text` and `value` only, so the one instruction on the first screen a buyer opens
    landed as dead text beside a number. The row he most needs to act on was the row he could not
    tap.

    RESOLVED AGAINST THE LIVE URL MAP, never trusted. The href arrives from a REPORTER, which is a
    different module with no idea which pages this box serves — and a reporter is the kind of
    producer that outlives the screen it was written for. An unserved path renders as the plain
    row it was before, which is exactly what it should degrade to.
    """
    out = []
    for it in items:
        n = it.get("value")
        cell = f'<span class="n">{_esc(n)}</span>' if n is not None else ""
        inner = f'{cell}<span class="t">{_esc(it.get("text"))}</span>'
        go = _live(str(it.get("href") or "")) if it.get("href") else ""
        out.append(f'<a class="row go" href="{_esc(go)}">{inner}</a>' if go
                   else f'<div class="row">{inner}</div>')
    return f'<div class="card{" needs" if needs else ""}">{"".join(out)}</div>'


def _first_run() -> str:
    """TODAY, ON A BOX NOTHING CAN REACH — the set-up, not a report.

    Measured by rendering it on a bare box (2026-09-16): a customer who had just bought a unified
    inbox was greeted with a website uptime report — a dash for the headline, "— of checks answered
    today", "0 checks on your site", and two rails asking for a web address. Not one word about
    messages. The report is not wrong; it is not what this person came for, and this is the screen
    the app opens on.

    THE SAME TEST AND THE SAME BUTTON AS THE EMPTY INBOX, deliberately: two screens that disagree
    about whether the box is listening, or about where to send him, is worse than either being
    wrong on its own. The button is absent when this box serves nowhere to put it.
    """
    go = _connect_href()
    return ('<h1>Today</h1><div class="head">'
            '<div class="v">Your box is running.</div>'
            '<div class="l">Nothing is connected to it yet, so there is nothing here to report. '
            'Connect one and this screen fills itself.</div></div>'
            + (f'<div class="card"><div class="setrow">'
               '<b>What lands here once you do</b>'
               '<span>Every message anyone sends you, in one list — and each morning, what came '
               'in overnight and who is still waiting on you.</span>'
               f'<p style="margin:10px 0 0"><a class="btn" href="{go}">{_connect_verb(go)}</a>'
               '</p></div></div>' if go else ""))


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
        # A BOX NOTHING CAN REACH LEADS WITH THE SET-UP HERE TOO — and STILL SAYS THE REPORT
        # COULD NOT BE READ. My first version replaced the sentence, and CI was right to refuse
        # it: an unreadable report is a real fault, and a screen that swallows one because the
        # buyer has nothing connected yet is how a broken box looks fine to everybody. What was
        # wrong was the ORDER, not the sentence. On a bare box the fault is not his first screen
        # and not the headline; it sits under the set-up, where it belongs — true for him, still
        # there for whoever is debugging the box.
        fault = ('<div class="quiet">Today\'s report could not be read on this box. '
                 'Nothing has been lost; the next poll writes it again.</div>')
        body = (_first_run() + fault if _nothing_arrives_yet() else
                '<h1>Today</h1><div class="head"><div class="v">—</div>'
                '<div class="l">Today\'s report could not be read on this box. '
                'Nothing has been lost; the next poll writes it again.</div></div>')
        return _shell(body, day=label), 200

    # ── THE FIRST SCREEN A BUYER EVER SEES ──────────────────────────────────────────────
    # MEASURED ON A BARE BOX, 2026-09-16, by rendering it: Today greeted a customer who had just
    # bought a unified inbox with a WEBSITE UPTIME REPORT — a dash for the headline, "— of checks
    # answered today", "0 checks on your site", and two rails asking for a web address. Not one
    # word about messages, which is the product. The report is not wrong; it is simply not what
    # this person came for, and it is the screen the app opens on.
    #
    # SO ON A BOX NOTHING CAN REACH, TODAY IS THE SET-UP. `_nothing_arrives_yet()` is the same
    # test the empty inbox uses, and the same button — resolved from the live url_map — so the two
    # screens can never disagree about whether this box is listening or about where to send him.
    #
    # NOTHING IS DELETED, ONLY RE-ORDERED. Everything the report produced still renders below
    # this: a buyer who HAS named a website still sees it. What changes is what is at the top on
    # the day he arrives, and a headline of "0 rails set up" is not it.
    first_run = _nothing_arrives_yet()
    if first_run:
        parts = [_first_run()]
    else:
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
    if (not any(v.get(k) for k in ("needs_you", "happened", "watch")) and not figs
            and not first_run):
        # NOT ON A FIRST RUN, because there it is the wrong of the two true sentences. "The rails
        # you own are being polled" is reassurance for a box that is listening and has heard
        # nothing; said to a box with nothing connected it promises a poll that will never find
        # anything — the same lie the empty inbox screen was just fixed for telling.
        parts.append('<div class="quiet">Nothing has come through yet today. '
                     'The rails you own are being polled; the first thing they find appears here.'
                     '</div>')

    # THE FOOTER EXPLAINS FIGURES, so it only belongs under some. "Every figure here is read from
    # your own rails" under a screen carrying no figure is the same small untruth this app keeps
    # deleting — and on a first run that is exactly what it was.
    if figs or v.get("happened"):
        parts.append('<div class="foot">Every figure here is read from your own rails. '
                     'This is the same report that goes out at 8am, on the screen instead of in '
                     'an inbox.</div>')
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


def _drafts_row() -> str:
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
    if not box_secrets.is_set(box_secrets.ANTHROPIC):
        return ('<div class="setrow"><b>Drafts</b>'
                '<span>Ownbox can write a reply for every message, ready for you to read and '
                'send. It needs an AI account to write with — yours, on your own bill, so '
                'nothing you receive passes through us.</span>'
                '<p style="margin:10px 0 0"><a class="btn" href="/voice/drafts">'
                'Connect an AI account</a></p></div>')
    # THE SECOND SENTENCE IS LOAD-BEARING AND DOES NOT GET CUT (§2.6). It is the promise the
    # whole product rests on, and Settings is where a nervous buyer goes to check it.
    return ('<div class="setrow"><b>Drafts</b>'
            '<span>On. Ownbox writes a reply for every message that arrives. You read it and '
            'you send it — nothing goes out on its own.</span>'
            '<span style="margin-top:8px">Writing with your own AI account. '
            '<a href="/voice/drafts" style="color:var(--accent)">Change</a> · '
            '<a href="/voice/drafts?off=1" style="color:var(--accent)">Turn off</a></span>'
            '</div>')


def _channels_row() -> str:
    """B1 — the row that connects the accounts the inbox reads FROM.

    THE PRODUCT HAD NO SUCH ROW UNTIL NOW, and that is the whole of why this exists. Measured
    2026-09-16: the only connect link in the system was minted by the provisioner and surfaced on
    the build page while an order was still building. A buyer who closed that tab, or who arrived
    after it, owned a box with no way to connect anything to it — Settings offered an AI key, a
    theme, an install guide and a paragraph about polling, and the inbox stayed empty forever with
    nothing on any screen saying why.

    NO NETWORK CALL FROM SETTINGS. This row reads the stored status only. Settings is the page a
    worried buyer opens, and a page that has to reach a vendor before it can paint is a page that
    hangs when the vendor is slow. The live account list lives one click away, on /voice/connect,
    where a person has said they want to look."""
    from core import box_secrets
    st = box_secrets.zernio_state()
    status = st.get("status")
    if status == "not_connected":
        return ('<div class="setrow"><b>Your channels</b>'
                '<span>Ownbox reads your Instagram and Messenger for you and keeps every '
                'conversation in one place. Connect them with your own social account — it stays '
                'yours, and you can take it back any day.</span>'
                '<p style="margin:10px 0 0"><a class="btn" href="/voice/connect">'
                'Connect your channels</a></p></div>')
    if status == "payment_required":
        # THE ONE FAILURE A BUYER CAN ACTUALLY FIX, so it gets its own sentence instead of the
        # word the API uses. Told "authentication failed" they re-paste a perfectly good key.
        return ('<div class="setrow"><b>Your channels</b>'
                '<span>Your social account needs a payment method before it will connect any '
                'more channels. Add one there, then come back — nothing here needs changing.'
                '</span>'
                '<p style="margin:10px 0 0"><a class="btn" href="/voice/connect">'
                'Check your channels</a></p></div>')
    if status == "needs_reauth":
        return ('<div class="setrow"><b>Your channels</b>'
                '<span>Ownbox can no longer reach your social account, so nothing new is '
                'arriving. Re-connect it and the inbox catches up on its own.</span>'
                '<p style="margin:10px 0 0"><a class="btn" href="/voice/connect">'
                'Re-connect</a></p></div>')
    return ('<div class="setrow"><b>Your channels</b>'
            '<span>Connected. New messages arrive on their own.</span>'
            '<span style="margin-top:8px">'
            '<a href="/voice/connect" style="color:var(--accent)">Add or remove a channel</a>'
            '</span></div>')


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
    question — it is not any more. Email's rule is written, cited, and refuses: there is no
    platform window at all, and this box has no SMTP path. Reading FREEFORM as "a rule exists"
    would report that written decision as a gap in our work.

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
    caution — a gap, and it reads like one. Email is not that: it has no platform window at all,
    the rule is written with its citation, and the reason nothing goes out from here is that this
    box has no SMTP path and the owner has not ruled on an email send policy. Told the first
    sentence about the second situation, a buyer waits for us to finish something that is already
    finished.

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
    if k.get("opted_out"):
        # THE ONE MISTAKE THIS SCREEN CAN HELP HIM MAKE is replying to someone who said STOP.
        out.append(("Opted out", "stop"))
    elif _no_send_lane(plat):
        # NOT A FAULT, SO NOT RED, and checked before the branch below because it is the more
        # specific fact. The rule for this channel IS written and says the send happens in the
        # person's own mail app — email has no platform window, and this box has no SMTP path.
        # "No reply rule" here would report a gap where there is a finished decision.
        out.append(("Send in your mail app", "warn"))
    elif not _has_send_rule(plat):
        # NOT A WINDOW PROBLEM, AND IT MUST NOT READ AS ONE. Nothing sends on a channel whose
        # policy nobody has written — `window.decide`'s most important branch — and "Window
        # closed" would quietly promise it reopens tomorrow.
        out.append(("No reply rule", "stop"))
    elif not k.get("last_inbound_at"):
        # A ROW WE KNOW ABOUT AND HAVE NEVER HEARD FROM. Every channel here is reply-only, so
        # there is no permission to answer — a different fact from a window that has shut.
        out.append(("No inbound yet", "warn"))
    else:
        from marketing.customer_voice.inbox import window as _w
        d = _w.decide(plat, k.get("last_inbound_at"))["decision"]
        if d in _WINDOW_TAG:
            out.append(_WINDOW_TAG[d])
    if (k.get("message_count") or 0) == 1:
        # EXACTLY ONE, NOT "AT MOST ONE". Written as `<= 1` first, and rendering a seeded box
        # caught it immediately: a conversation with ZERO messages — a row the poller knows about
        # and has never heard a word on — announced itself as New, right beside a tag saying
        # nothing had ever come in. "New" means one message has arrived and nobody has answered
        # it; it is not the empty state wearing a badge.
        out.append(("New", "new"))
    if k.get("ad_title"):
        # IT USED TO BE PROSE IN THE GREY LINE, where it competed with the message count for the
        # same characters and lost. Attribution is the one thing on this row that says what the
        # conversation is WORTH.
        out.append((f'From {k["ad_title"]}', "ad"))
    return out[:_TAG_LIMIT]


def _tags(k: dict) -> str:
    return "".join(f'<span class="tag {c}">{_esc(t)}</span>' for t, c in _tag_list(k))


def _acts(k: dict, *, who: str, channel: str) -> str:
    """The hover menu on a row. EVERY ITEM GOES SOMEWHERE THIS BOX ALREADY SERVES.

    Reply is the thread's own compose box; the channel item is the chip row the reader may never
    have scrolled to. Nothing in here is a control that cannot succeed, which is the thing this
    codebase keeps deleting by name — so the two obvious extras are NOT drawn in grey:

    · "Mark as done" has no column to write to. Inventing one from the screen would make the
      inbox's idea of finished disagree with the poller's the first time a message arrived on a
      thread he had closed.
    · An owner-side "Do not contact" would write the SAME `opted_out` flag a CONTACT sets by
      saying STOP — quietly turning his mute into their refusal, in a field the send path trusts
      and nothing can undo. Both are on the wall for OSDev4, whose machine owns that column.

    REPLY APPEARS ON EXACTLY THE ROWS WHERE THE THREAD WILL SHOW A BOX, because it asks the two
    fields `_compose` asks and no others. A menu item that lands on a thread with nowhere to type
    is the same broken promise as a greyed-out one, just further away.
    """
    href = _thread_href(k.get("zernio_conversation_id"))
    plat = str(k.get("platform") or "")
    items = []
    # THE SAME FOUR QUESTIONS `_compose` ASKS, IN THE SAME ORDER. `_no_send_lane` is the newest
    # of them and it arrived with email: a channel whose rule is written and says the send happens
    # in the person's own mail app shows no box, so it must offer no Reply either. Adding a gate
    # to `_compose` and not to this list is precisely the drift the suite beside this catches.
    if (not k.get("opted_out") and not _no_send_lane(plat) and _has_send_rule(plat)
            and (k.get("last_inbound_at") or "")
            and (k.get("account_id") or "").strip()):
        items.append((f"{href}#reply", "Reply"))
    if plat and channel:
        items.append(("/voice/inbox", "All channels"))
    elif plat:
        from urllib.parse import quote as _q
        items.append((f'/voice/inbox?channel={_esc(_q(plat, safe=""))}',
                      f"Only {_channel(plat)}"))
    if not items:
        # NO BUTTON AT ALL rather than a button that opens an empty card.
        return ""
    links = "".join(f'<a href="{h}">{_esc(t)}</a>' for h, t in items)
    return ('<details class="acts"><summary role="button" '
            f'aria-label="Actions for {_esc(who)}">'
            '<svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" '
            'aria-hidden="true"><circle cx="5" cy="12" r="1.7"/><circle cx="12" cy="12" r="1.7"/>'
            '<circle cx="19" cy="12" r="1.7"/></svg></summary>'
            f'<div class="menu">{links}</div></details>')


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

    "Connect a channel" promises a choice. On a box that serves the hub (`/voice/connect`) there
    is one. On a box that does not, this button reaches the MAILBOX screen, which offers exactly
    one thing — and a buyer who pressed "Connect a channel" expecting to pick Instagram and
    arrived at a Gmail form has been told something that was not true of his box.

    A LABEL AND A DESTINATION THAT DISAGREE is the same defect as a link that 404s, only quieter:
    nothing breaks, and he simply believes the product is not what he was shown.
    """
    return {"/voice/setup": "Set up your box",
            "/voice/connect": "Connect a channel",
            "/voice/mailbox": "Connect your inbox"}.get(go, "Finish setting up")


def _connect_href() -> str:
    """Where a buyer with nothing connected should be sent — or "" when this box has nowhere.

    ORDER IS "THE MOST IT CAN DO FOR HIM, ON THIS BOX". `/voice/connect` is the hub when a box
    serves one; `/voice/mailbox` connects the one channel that needs no vendor account at all;
    `/voice/settings` is the last resort, and on a bare box it offers an AI key, a theme and an
    install guide — nothing that connects anything. It was where this button led for a day.
    """
    return _live("/voice/setup", "/voice/connect", "/voice/mailbox", "/voice/settings")


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


def _chips(space: str, current: str, *, q: str = "") -> str:
    """All, then one chip per channel THIS BOX ACTUALLY HAS. Never a menu of hopes.

    ONE CHANNEL IS NOT A CHOICE, so a box with only Messenger renders no chip row at all — a
    filter offering a single option is a control that cannot change anything, and this app keeps
    deleting those rather than shipping them greyed out.

    THE CHIPS CARRY THE QUERY, because a filter that silently throws away what he typed is worse
    than no filter: he taps Instagram to narrow a search and gets the whole Instagram inbox back.

    AND THE COUNT COMES OFF WHILE HE IS SEARCHING. `platforms_present` counts the WHOLE inbox,
    not the matches, so "Messenger 4" beside a search that found one is simply a wrong number on
    the screen. Scoping it needs a store this screen does not have, and the honest move is to
    stop answering a question nobody asked mid-search rather than answer it incorrectly.
    """
    try:
        from marketing.customer_voice.inbox import store as _store
        present = _store.platforms_present(space)
    except Exception as e:                       # noqa: BLE001 — a missing chip row is not a 500
        log.warning("voice.chips_unreadable", extra={"error": f"{type(e).__name__}: {e}"[:160]})
        return ""
    if len(present) < 2:
        return ""
    # NO `page` ON A CHIP, and that is the whole reason these go through `_url`. Changing the
    # channel changes WHICH conversations there are, so page 7 of the old filter is not page 7
    # of the new one — it is a page that may not exist. Every chip lands on page one.
    out = [f'<a class="chip{"" if current else " on"}" href="{_esc(_url(q=q))}">All</a>']
    for row in present:
        pid = str(row["platform"] or "")
        on = " on" if pid == current else ""
        n = "" if q else f' <span class="n">{row["n"]}</span>'
        out.append(f'<a class="chip{on}" href="{_esc(_url(q=q, channel=pid))}">'
                   f'{_esc(_channel(pid))}{n}</a>')
    return f'<div class="chips">{"".join(out)}</div>'


def _find(q: str, channel: str) -> str:
    """The search field. A plain GET form, so it works before any script has run.

    THE CHANNEL RIDES ALONG AS A HIDDEN FIELD. Searching inside a channel filter is the obvious
    thing to want and it is one input; dropping it would quietly widen a search he had narrowed.
    """
    # THE CHANNEL RIDES, THE PAGE DOES NOT. A new search is a new set of results and page one
    # is the only page it can be on — carrying the old `page` in a hidden field is how a person
    # searches for "boiler", gets a blank screen, and concludes search is broken.
    chan = (f'<input type="hidden" name="channel" value="{_esc(channel)}">' if channel else "")
    return ('<form class="find" method="get" action="/voice/inbox" role="search">'
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


PAGE = 50            # what the store is asked for, and what a page holds


def _url(*, q: str = "", channel: str = "", page: int = 1) -> str:
    """Every link on this screen, built in ONE place.

    THE CHIPS ALREADY LOST THE QUERY ONCE. Each control here — a chip, Clear, Older, Newer —
    carries the two the reader is not changing and drops the one they are, and every one of them
    was hand-assembling its own query string. That is three places to forget `page` in, and the
    forgetting is silent: the link still works, it just quietly puts him back on page one.

    A DEFAULT IS NEVER WRITTEN INTO THE URL. `/voice/inbox` and `/voice/inbox?page=1` are the
    same screen, and only one of them is worth showing a person.
    """
    from urllib.parse import quote as _qt
    bits = []
    if q:
        bits.append(f'q={_qt(q, safe="")}')
    if channel:
        bits.append(f'channel={_qt(channel, safe="")}')
    if page > 1:
        bits.append(f"page={int(page)}")
    return "/voice/inbox" + ("?" + "&".join(bits) if bits else "")


def _clear(channel: str) -> str:
    """Back out of a search WITHOUT backing out of the channel he chose — or the page he is on.

    THE PAGE GOES. That is the point of Clear: page 4 of a search is meaningless once the search
    is gone, and landing on page 4 of everything is not what he asked for.
    """
    return _url(channel=channel)


def _pager(*, q: str, channel: str, page: int, more: bool) -> str:
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
        out.append(f'<a class="pg prev" href="{_esc(_url(q=q, channel=channel, page=page - 1))}" '
                   f'rel="prev">&larr; Newer</a>')
    if more:
        out.append(f'<a class="pg next" href="{_esc(_url(q=q, channel=channel, page=page + 1))}" '
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


@blueprint.get("/voice/inbox")
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
                                                platform=channel or None)
        else:
            convs = _store.list_conversations(space, limit=ask, offset=off,
                                              platform=channel or None)
        more = len(convs) > PAGE
        convs = convs[:PAGE]
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
        if page > 1:
            body = (f'<h1>Inbox</h1>{_find(q, channel)}{_chips(space, channel, q=q)}'
                    f'<div class="quiet">There is no page {page}'
                    + (f' of conversations matching <b>{_esc(q)}</b>' if q else "")
                    + '. Nothing has been lost — the list simply ends before here. '
                    f'<a href="{_esc(_url(q=q, channel=channel))}" style="color:var(--accent)">'
                    'Back to the top</a></div>')
        elif q:
            body = (f'<h1>Inbox</h1>{_find(q, channel)}{_chips(space, channel, q=q)}'
                    f'<div class="quiet">Nothing matches <b>{_esc(q)}</b>'
                    + (f' on {_esc(_channel(channel))}' if channel else "")
                    + '. This searches what people wrote, not just their names. '
                    f'<a href="{_esc(_clear(channel))}" style="color:var(--accent)">'
                    'Show everything</a></div>')
        elif channel:
            body = (f'<h1>Inbox</h1>{_find(q, channel)}{_chips(space, channel)}'
                    f'<div class="quiet">Nothing on {_esc(_channel(channel))} yet. '
                    'Other channels may have messages — tap <b>All</b>.</div>')
        elif _nothing_arrives_yet():
            # NOTHING IS LISTENING. "The first person who messages you appears here" is a promise,
            # and here it is one the box cannot keep: nobody is coming. This is the only empty
            # state with something for him to DO, so it is the only one carrying a button — and
            # the button appears only on a box that serves somewhere to send him.
            go = _connect_href()
            body = ('<h1>Inbox</h1><div class="quiet">Nothing can reach you yet, because no '
                    'channel is connected. Connect one and everything people send you lands '
                    'here.</div>'
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
            body = ('<h1>Inbox</h1><div class="quiet">No conversations yet. '
                    'The first person who messages you appears here, and you will get a '
                    'notification once this is installed on your phone.</div>')
        return _shell(body), 200

    rows = []
    for k in convs:
        who = (k.get("participant") or "").strip() or "Someone"
        n = k.get("message_count") or 0
        when = _ago(k.get("last_inbound_at"))
        # THE FLAGS LEFT THIS SENTENCE AND BECAME TAGS. "opted out" and "from <ad>" used to be
        # prose here, third and fourth in a line that truncates — so the two facts most worth
        # seeing were the two most likely to be cut off. They are pills now, and pills do not
        # truncate; what is left is the one thing a sentence says better than a badge.
        sub = f'{n} message' + ("" if n == 1 else "s")
        # KINSO'S ROW, EXACTLY: avatar, name with the time beside it, one grey line under it, and
        # the channel's own logo far right. The mark is a SHAPE before it is a colour, so it still
        # separates in greyscale — and the channel's word is one tap away in the thread header, so
        # nothing here is carried by colour alone.
        plat = k.get("platform")
        rows.append('<div class="convrow">'
                    f'<a class="conv" href="{_esc(_thread_href(k.get("zernio_conversation_id")))}">'
                    f'<span class="av" aria-hidden="true">{_monogram(who)}</span>'
                    f'<span class="w"><b>{_esc(who)}</b><span class="t">{_esc(when)}</span></span>'
                    f'<span class="s"><span class="sub">{_esc(sub)}</span>{_tags(k)}</span>'
                    f'<span class="mk">{_mark(plat)}'
                    f'<span class="vh">{_esc(_channel(plat))}</span></span></a>'
                    f'{_acts(k, who=who, channel=channel)}</div>')
    return _shell(f'<h1>Inbox</h1>{_find(q, channel)}{_chips(space, channel, q=q)}'
                  f'{_hits(q, channel, len(convs), page=page, more=more)}'
                  f'<div class="card">{"".join(rows)}</div>'
                  f'{_pager(q=q, channel=channel, page=page, more=more)}'), 200


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


def _window_note(conv: dict) -> str:
    """The send window, in the buyer's words, above the box he is about to type in.

    SILENT WHEN THE WINDOW IS SIMPLY OPEN. "You can reply now" over a reply box is the screen
    narrating itself; the box being there already says it. The sentence earns its space only when
    the answer is something other than yes — which is also the only time a person can act on it.

    A FAILURE HERE COSTS THE NOTE, NEVER THE BOX. `explain()` is documented never to raise, but
    this screen is the one place where being wrong about that would take away a working reply box
    on a live conversation, so it is wrapped anyway.
    """
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
    if _no_send_lane(str(conv.get("platform") or "")):
        # A WRITTEN RULE THAT SAYS THE SEND HAPPENS ELSEWHERE. Checked before the no-rule branch
        # below because it is the more specific fact: both hide the compose box, and only this one
        # can tell the person where their reply actually goes.
        return ('<div class="card"><div class="row"><span class="t quiet">Ownbox reads your '
                + _esc(_channel(str(conv.get("platform") or ""))) + ' and writes the reply, but '
                'it does not send mail — you send it from your own mail app, from your own '
                'address. Copy the draft across.</span></div></div>')
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
    if not (conv.get("last_inbound_at") or "").strip():
        # THE SECOND HALF OF THE SAME RULE, and rendering found it the same way. Every channel
        # here is reply-only, so a conversation with no inbound message on record has nothing to
        # reply TO — `window.decide` refuses it outright, on every platform, at every hour.
        #
        # THE LINE THIS FILE DRAWS: the box is absent when the send is refused for a reason THE
        # CLOCK CANNOT CHANGE — no policy for the channel, or nothing ever received on the row.
        # It stays PRESENT when the refusal is the clock itself, a window that shut and reopens
        # the moment they write again; that decision is the send path's, not this screen's, and
        # taking it here would delete the reply box from every conversation older than a day.
        return ('<div class="card"><div class="row"><span class="t quiet">Nothing has come in on '
                'this conversation yet. Every channel here is reply-only, so there is nothing to '
                'reply to until they write — and then the box appears.</span></div></div>')
    if not (conv.get("account_id") or "").strip():
        return ('<div class="card"><div class="row"><span class="t quiet">This thread was '
                'mirrored before the box started recording which account owns it, so it cannot '
                'be replied to from here yet. The next message on it fixes that.</span></div>'
                '</div>')
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
    win = _window_note(conv)

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
        if not box_secrets.is_set(box_secrets.ANTHROPIC):
            off_note = ('<div class="quiet" style="margin-top:8px">Drafts are off. '
                        '<a href="/voice/settings" style="color:var(--accent)">Turn them on in '
                        'Settings</a>.</div>')
    # `note` sits ABOVE the box because it introduces the draft inside it. `off_note` sits BELOW,
    # because §2.6 puts it there and the reason is the difference between the two: one labels
    # what is in the box, the other is an aside about what is not. Only one is ever present.
    # `id` IS LOAD-BEARING NOW, not decoration: the inbox row's Reply item links to `#reply`,
    # and an anchor with no target scrolls nowhere and looks like a dead control. The two agree
    # on when it exists because they test the same two fields — see `_acts`.
    return (win
            + '<form class="compose" id="reply" method="post" '
            'action="' + _esc(f"/voice/inbox/{zcid}/reply") + '">'
            f'<input type="hidden" name="n" value="{_esc(_reply.new_nonce())}">'
            + note +
            '<textarea name="text" rows="3" maxlength="1800" required '
            f'placeholder="Write a reply…">{_esc(drafted)}</textarea>'
            '<button class="btn" type="submit">Send</button>'
            '</form>' + off_note)


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
def _mailbox_row() -> str:
    """The Settings row that makes /voice/mailbox reachable AFTER it has been set up.

    A DEAD END I BUILT, found by reading the rendered Settings rather than the code: every link
    to the mailbox screen lived on an EMPTY state, so connecting an inbox deleted the only way
    back to it. And the way back is not a nicety — Google revokes an app password whenever the
    account password changes, which is the single most likely reason a buyer needs this screen
    again, and by then the empty states are gone.

    IT SAYS THE STATE, because "is it still reading my mail" is the question this row is for. It
    never carries the password; `email_state()` is documented not to return one.

    ABSENT WHEN THE BOX DOES NOT SERVE THE SCREEN — same rule as every other link in this app.
    """
    go = _live("/voice/mailbox")
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
        said = f'Ownbox is reading <b>{_esc(who)}</b>. It never sends and never marks a message read.'
        verb = "Change or stop it"
    else:
        said = ('Ownbox can read the mail your customers send you and draft replies. Nothing is '
                'connected yet.')
        verb = "Connect your inbox"
    return (f'<div class="setrow"><b>Your inbox</b><span>{said} '
            f'<a href="{go}" style="color:var(--accent)">{verb}</a>.</span></div>')


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
      # INBOX, THEN CHANNELS, THEN DRAFTS — the owner's own order (2026-09-16): "the first item
      # on the screen is the Gmail setup fields and instructions, and the second area should be
      # the zernio key field along with instructions". It is also the order a buyer does them in.
      # Reading the mail is what the box IS; the channels widen what it reads; drafting is what it
      # does with what it read, and a row for the last above the first asks somebody to configure
      # an answer to a question nothing is yet asking.
      + _mailbox_row() + _channels_row() + _drafts_row() +
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
# NOT `@blueprint.post`. tests/test_customer_voice.py scans this department for a CALL named
# `post`, and a decorator is a call — the same two extra characters app.py:659 and :909 spend.
@blueprint.route("/voice/drafts", methods=["GET", "POST"])
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
    if request.args.get("off"):
        # "Turn off" — §2.6's second state offers it, so it has to actually work. Removing the
        # key is the whole of turning drafting off: the drafter skips with no key, and every
        # message still arrives and is still answerable by hand.
        box_secrets.clear(box_secrets.ANTHROPIC, user_id=whoami)
        return redirect("/voice/settings")
    note = ""
    if request.method == "POST":
        try:
            # VALIDATED IN THE STORE, NOT HERE, so the rule is the same whoever writes a key —
            # this screen, a future one, or a script. The screen's job is to show the sentence.
            box_secrets.put(box_secrets.ANTHROPIC,
                            str(request.form.get("key") or ""), user_id=whoami)
            return redirect("/voice/settings")
        except box_secrets.SecretRejected as e:
            # Never a lecture and never an echo — the same discipline as the claim form. The
            # field comes back empty: a key is not something to re-display for correction.
            note = f'<p class="quiet" style="color:var(--accent)">{_esc(str(e))}</p>'
    body = (
      '<h1>Add your AI key.</h1>'
      '<div class="card">'
      '<div class="setrow">'
      '<span>Ownbox writes replies in your voice using your own AI account. The words never '
      'pass through us, and you pay the provider directly instead of a markup on our bill.</span>'
      '<p style="margin:10px 0 0"><a href="https://console.anthropic.com/settings/keys" '
      'target="_blank" rel="noopener" style="color:var(--accent)">How to get a key →</a></p>'
      '</div></div>'
      + note +
      '<form class="compose" method="post" action="/voice/drafts">'
      '<input type="password" name="key" autocomplete="off" spellcheck="false"'
      ' aria-label="Paste your key" placeholder="Paste your key" '
      'style="width:100%;font:inherit;font-size:16px;padding:12px 14px;'
      'border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--ink)">'
      '<button class="btn" type="submit">Turn drafts on</button>'
      '</form>'
      '<p class="quiet" style="margin-top:12px">You can change or remove this key any day. '
      'Nothing about it reaches us.</p>'
      '<p style="margin-top:14px"><a href="/voice/settings" style="color:var(--accent)">'
      '← Settings</a></p>')
    return _shell(body, here="/voice/settings"), 200


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


def _mailbox_form(*, user: str = "", note: str = "", verb: str = "Start reading this inbox") -> str:
    """The two fields and the button. THE PASSWORD IS NEVER PRE-FILLED and never echoed back —
    the address is, because retyping it after a rejected password is a punishment for their
    typo in the other field."""
    field = ('font:inherit;font-size:16px;padding:12px 14px;width:100%;'
             'border:1px solid var(--line);border-radius:12px;'
             'background:var(--card);color:var(--ink)')
    return (note +
            '<form class="compose" method="post" action="/voice/mailbox" '
            'style="display:flex;flex-direction:column;gap:10px;align-items:stretch">'
            f'<input type="email" name="user" value="{_esc(user)}" autocomplete="email" '
            'spellcheck="false" aria-label="The email address to read" '
            f'placeholder="you@yourcompany.com" style="{field}">'
            '<input type="password" name="password" autocomplete="off" spellcheck="false" '
            'aria-label="App password" placeholder="sixteen letters from Google" '
            f'style="{field}">'
            f'<button class="btn" type="submit">{_esc(verb)}</button></form>')


def _mailbox_steps() -> str:
    items = "".join(f'<div class="row"><span class="n">{i}</span>'
                    f'<span class="t">{_esc(t)}</span></div>'
                    for i, t in enumerate(_MAILBOX_STEPS, 1))
    return (f'<div class="card">{items}</div>'
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
    return out


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
    # THE OLD WRITE — the only place in this file that knows a step by name, and it is dated.
    if which == "email":
        box_secrets.put_email(host="imap.gmail.com", user=str(form.get("user") or ""),
                              password=str(form.get("password") or ""), user_id=user_id)
    elif which == "zernio":
        box_secrets.put_zernio(str(form.get("key") or ""), user_id=user_id)
    elif which == "anthropic":
        # `put` CARRIES THE VALIDATION, unlike the two above which have their own front doors. It
        # checks the shape and refuses a blank, a pasted newline or the wrong thing entirely with
        # a sentence for the buyer — and deliberately does NOT call Anthropic, because only they
        # can say whether a key works and refusing a valid one on a stale regex is the worse bug.
        box_secrets.put(box_secrets.ANTHROPIC, str(form.get("key") or ""), user_id=user_id)
    else:
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
    style = ('font:inherit;font-size:16px;padding:12px 14px;width:100%;'
             'border:1px solid var(--line);border-radius:12px;'
             'background:var(--card);color:var(--ink)')
    kind = str(f.get("type") or "text")
    return (f'<label style="display:block;margin-top:10px">'
            f'<span class="t" style="display:block;font-size:13.5px;margin-bottom:4px">'
            f'{_esc(f.get("label"))}</span>'
            f'<input type="{_esc(kind)}" name="{_esc(f.get("name"))}" '
            f'value="{_esc(value) if kind != "password" else ""}" '
            f'placeholder="{_esc(f.get("placeholder") or "")}" '
            f'autocomplete="{_AUTOCOMPLETE.get(kind, "off")}" spellcheck="false" '
            f'style="{style}"></label>')


def _setup_step(n: int, e: dict, *, note: str = "", typed: dict | None = None) -> str:
    """ONE ENTRY, RENDERED THE SAME WAY WHATEVER IT IS. This is the whole point of the contract:
    two vendors, two kinds of secret, one shape — a number, a title, why it is wanted, what is
    set, the instructions, the fields, and whatever the buyer can press."""
    typed = typed or {}
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
    fields = "".join(_setup_field(f, typed.get(f.get("name"), "")) for f in e.get("fields") or ())

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

    return (f'<section id="{_esc(e.get("key"))}" style="margin-top:26px">'
            f'<h1 style="font-size:19px">{n}. {_esc(e.get("title"))}</h1>'
            f'<p class="quiet" style="margin:2px 0 0">{_esc(e.get("why"))}</p>'
            f'<p class="quiet" style="margin:6px 0 0"><b class="{_esc(tone)}">{_esc(said)}</b>'
            + (f' — {_esc(detail)}' if detail else "") + '</p>'
            + (f'<div class="card">{steps}</div>' if steps else "")
            + (f'<p class="quiet">{_esc(e.get("note"))}</p>' if e.get("note") else "")
            + note
            + (f'<form class="compose" method="post" action="/voice/setup" '
               'style="display:flex;flex-direction:column;gap:2px;align-items:stretch">'
               f'<input type="hidden" name="step" value="{_esc(e.get("key"))}">'
               f'{fields}<p style="margin:12px 0 0">'
               f'<button class="btn" type="submit">{verb}</button></p></form>' if fields else "")
            + out + '</section>')


# NOT `@blueprint.post`. tests/test_customer_voice.py scans this department for a CALL named
# `post`, and a decorator is a call — the same two extra characters r_drafts spends.
@blueprint.route("/voice/setup", methods=["GET", "POST"])
def r_setup():
    """EVERY CREDENTIAL THE BUYER SUPPLIES, ON ONE SCREEN, IN THE OWNER'S ORDER.

    Assigned by OSDev1 (2026-09-16): render the steps as a LOOP — Gmail first, Zernio second, the
    link out drawn disabled until the key is in — rather than three bespoke flows that drift apart.
    Then (17:20): render them from the seam, `core.onboarding.steps()`, with no step-specific code.

    NOTHING IN THIS FUNCTION KNOWS WHAT A STEP IS — no key is named in it, and no key is named in
    the renderer either. A machine that registers a third credential gets a screen for it with no
    change at all here. The one remaining mention of a key by name lives in `_setup_save`'s legacy
    arm, which is dated and exists only until every machine has moved onto the seam.

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

    notes: dict = {}
    typed: dict = {}
    if request.method == "POST":
        which = str(request.form.get("step") or "")
        typed = {k: str(v) for k, v in request.form.items() if k != "step"}
        try:
            _setup_save(which, request.form, user_id=whoami)
            return redirect(f"/voice/setup#{which}")
        except _setup_rejections() as e:
            notes[which] = (f'<p class="quiet" style="color:var(--accent);margin-top:10px">'
                            f'{_esc(str(e))}</p>')
        # A PASSWORD IS NEVER PUT BACK IN THE PAGE, whatever else is. The address they typed is,
        # because retyping it after the other field was rejected is a punishment for their typo.
        typed = {k: v for k, v in typed.items() if k != "password" and k != "key"}

    try:
        steps = _setup_source()
    except Exception as e:                       # noqa: BLE001 — the set-up page outranks the cause
        log.warning("voice.setup_unreadable", extra={"error": f"{type(e).__name__}: {e}"[:160]})
        return _shell('<h1>Set-up</h1><div class="quiet">This box could not read its own set-up '
                      'list. Nothing you have already connected is affected.</div>',
                      here="/voice/settings"), 200

    done = sum(1 for e in steps if e.get("status") == "connected")
    # COUNTED, NOT WRITTEN. This read "Two things only you can do" while the list had two entries,
    # and went false the moment the AI key became the third (OSDev4, #1287-era audit: a buyer
    # connected Gmail and Zernio, read "You are set up", and had a box that could not draft a
    # single reply because nothing had ever asked him for a key). Deriving it means the next step
    # added cannot make this sentence lie either.
    _n = {1: "One thing", 2: "Two things", 3: "Three things"}.get(len(steps),
                                                                 f"{len(steps)} things")
    head = (f'<h1>Set up your box.</h1><p class="quiet">{_n} only you can do. Your box is '
            'already running — this is what tells it where to listen.</p>'
            if done < len(steps) else
            '<h1>You are set up.</h1><p class="quiet">Everything below is connected. Change any '
            'of it whenever you like.</p>')
    body = head + "".join(_setup_step(i, e, note=notes.get(e.get("key"), ""), typed=typed)
                          for i, e in enumerate(steps, 1))
    body += ('<p style="margin-top:22px"><a href="/voice/settings" '
             'style="color:var(--accent)">← Settings</a></p>')
    return _shell(body, here="/voice/settings"), 200


# NOT `@blueprint.post`. tests/test_customer_voice.py scans this department for a CALL named
# `post`, and a decorator is a call — the same two extra characters r_drafts spends.
@blueprint.route("/voice/mailbox", methods=["GET", "POST"])
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

    if request.args.get("off"):
        # STOPPING IS AS REAL A CONTROL AS STARTING. The status row goes with the credential: a
        # left-behind "connected" would have this screen reporting on a mailbox it no longer reads.
        box_secrets.clear(box_secrets.EMAIL, user_id=whoami)
        box_secrets.clear(box_secrets.EMAIL_STATUS, user_id=whoami)
        box_secrets.clear(box_secrets.EMAIL_DETAIL, user_id=whoami)
        return redirect("/voice/mailbox")

    note, typed = "", ""
    if request.method == "POST":
        typed = str(request.form.get("user") or "")
        try:
            # VALIDATED IN THE STORE, NOT HERE, so the rule is the same whoever writes one. This
            # screen's job is to show the sentence the store wrote for the person in front of it.
            box_secrets.put_email(host="imap.gmail.com", user=typed,
                                  password=str(request.form.get("password") or ""),
                                  user_id=whoami)
            return redirect("/voice/mailbox?saved=1")
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
                + _mailbox_steps()
                + _mailbox_form(user=who, note=note, verb="Use this password instead")
                + '<p style="margin-top:14px"><a href="/voice/settings" '
                  'style="color:var(--accent)">← Settings</a></p>')
    elif status == "admin_disabled":
        body = ('<h1>Your administrator has switched this off.</h1>'
                '<p class="quiet">App passwords are turned off for your Google organisation, so '
                'nobody in it can make one. An administrator can allow them again in the Google '
                'Admin console; until then Ownbox cannot read this inbox, and nothing else about '
                'your box is affected.</p>'
                + (f'<p class="quiet">Google said: {_esc(detail)}</p>' if detail else "")
                + '<p style="margin-top:14px"><a href="/voice/settings" '
                  'style="color:var(--accent)">← Settings</a></p>')
    elif who:
        saved = request.args.get("saved")
        body = ('<h1>Your inbox is set.</h1>'
                f'<p class="quiet">Ownbox is set to read <b>{_esc(who)}</b>. '
                + ("It will try that password on the next check — if Google refuses it, this "
                   "screen says so and tells you what to do."
                   if saved else
                   "If Google ever refuses the password, this screen says so and tells you what "
                   "to do.")
                + ' Ownbox only reads: it never sends anything and never marks a message read.'
                  '</p>'
                '<div class="card"><div class="setrow"><b>Change the password</b>'
                '<span>Make a new app password in Google and paste it here. The address stays '
                'the same unless you change it too.</span></div></div>'
                + _mailbox_form(user=who, note=note, verb="Save this password")
                + '<p style="margin-top:16px"><a href="/voice/mailbox?off=1" '
                  'style="color:var(--accent)">Stop reading this inbox</a></p>'
                '<p style="margin-top:14px"><a href="/voice/settings" '
                'style="color:var(--accent)">← Settings</a></p>')
    else:
        body = ('<h1>Connect your inbox.</h1>'
                '<p class="quiet">Ownbox reads the mail your customers send you, and drafts '
                'replies. It never sends anything and it never marks a message as read.</p>'
                '<p class="quiet">Google will not take your ordinary password for this, and it '
                'should not — an <b>app password</b> is sixteen letters that only Ownbox uses and '
                'that you can revoke on its own, without changing anything else.</p>'
                + _mailbox_steps()
                + _mailbox_form(user=typed, note=note)
                + '<p style="margin-top:14px"><a href="/voice/settings" '
                  'style="color:var(--accent)">← Settings</a></p>')
    return _shell(body, here="/voice/settings"), 200


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
_CONNECTABLE = (("instagram", "Instagram"), ("facebook", "Messenger"))


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
    return f"{root}/voice/connect?connected={platform}"


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


@blueprint.route("/voice/connect", methods=["GET", "POST"])
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

    if request.args.get("off"):
        # Disconnect. The key AND the profile resolved with it — `clear_zernio` is one call for
        # exactly that reason: a profile id left behind would be handed to the NEXT key pasted in.
        box_secrets.clear_zernio(user_id=whoami)
        return redirect("/voice/settings", code=303)

    note = ""
    if request.method == "POST":
        try:
            box_secrets.put_zernio(str(request.form.get("key") or ""), user_id=whoami)
            return redirect("/voice/connect", code=303)
        except box_secrets.SecretRejected as e:
            # Never an echo of what they pasted. Same discipline as the AI key form.
            note = f'<p class="quiet" style="color:var(--accent)">{_esc(str(e))}</p>'

    if not box_secrets.is_set(box_secrets.ZERNIO):
        return _shell(_connect_key_form(note), here="/voice/settings"), 200

    # ── connected: show what is on, and what can still be added ──────────────────────────────
    sp = _connect_space()
    z = zernio.client(sp)
    try:
        brand = str(dash.brand() or "")          # a str, not a dict — core/dash/__init__.py:247
    except Exception:                            # noqa: BLE001 — the name is a nicety here; it
        brand = ""                               # only labels a folder the buyer will recognise
    chosen = request.args.get("profile")
    if chosen:
        box_secrets.put_zernio_profile(chosen, user_id=whoami)
        return redirect("/voice/connect", code=303)

    try:
        pid, choices = (sp.get("zernio_profile_id"), [])
        if not pid:
            pid, choices = _resolve_profile(z, brand)
        if choices:
            return _shell(_connect_profile_chooser(choices), here="/voice/settings"), 200
        sp = dict(sp, zernio_profile_id=pid)
        z = zernio.client(sp)
        live = z.accounts.discover()
    except zernio.ZernioError as e:
        # `payment_required` is recorded so SETTINGS can say it too, without a network call.
        detail = str(e)
        if "402" in detail or "payment" in detail.lower():
            box_secrets.note_zernio_status("payment_required", detail, user_id=whoami)
            return _shell(_connect_trouble(
                "Your social account needs a payment method before it will connect any more "
                "channels. Add one there, then come back — nothing here needs changing."),
                here="/voice/settings"), 200
        log.warning("connect.discover_failed", extra={"err": detail[:200]})
        return _shell(_connect_trouble(
            "Ownbox could not reach your social account just now. Nothing is lost — try again "
            "in a minute."), here="/voice/settings"), 200

    just = request.args.get("connected") or ""
    return _shell(_connect_page(live, just), here="/voice/settings"), 200


@blueprint.get("/voice/connect/<platform>")
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
    if platform not in {p for p, _ in _CONNECTABLE}:
        # An unknown platform is never passed through to the vendor. The allow-list is the
        # channels the poller reads; anything else would grant access nothing ever collects.
        return redirect("/voice/connect", code=303)
    sp = _connect_space()
    if not sp.get("zernio_key") or not sp.get("zernio_profile_id"):
        return redirect("/voice/connect", code=303)
    try:
        url = zernio.client(sp).connect.url(platform, redirect_url=_connect_return(platform))
    except zernio.ZernioError as e:
        log.warning("connect.url_failed", extra={"platform": platform, "err": str(e)[:200]})
        return _shell(_connect_trouble(
            "That channel would not start just now. Nothing is lost — try again in a minute."),
            here="/voice/settings"), 200
    return redirect(url, code=303)


def _connect_key_form(note: str) -> str:
    """State one: no social account connected yet.

    SAME SHAPE AS THE AI KEY FORM ON PURPOSE. A buyer who has done one of these already should
    recognise the second on sight, and the sentence underneath is the same promise in both places:
    the account is theirs, on their bill, and they can take it back."""
    return (
      '<h1>Connect your channels.</h1>'
      '<div class="card"><div class="setrow">'
      '<span>Ownbox reads your Instagram and Messenger through your own social account, so the '
      'connection stays yours and you can take it back any day without asking us.</span>'
      # THE SITE ROOT, NOT A GUESSED DEEP LINK. scripts/doctor.py:154 says "zernio.com → API key"
      # and that is the whole of what we actually know; a made-up /settings/api path that 404s in
      # front of a buyer at the exact moment they are trying to find something is worse than one
      # extra click.
      '<p style="margin:10px 0 0"><a href="https://zernio.com" target="_blank" '
      'rel="noopener" style="color:var(--accent)">Where to find your key &rarr;</a></p>'
      '</div></div>'
      + note +
      '<form class="compose" method="post" action="/voice/connect">'
      '<input type="password" name="key" autocomplete="off" spellcheck="false"'
      ' aria-label="Paste your key" placeholder="Paste your key" '
      'style="width:100%;font:inherit;font-size:16px;padding:12px 14px;'
      'border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--ink)">'
      '<button class="btn" type="submit">Continue</button>'
      '</form>'
      '<p class="quiet" style="margin-top:12px">Checked with your provider before it is saved, '
      'so you find out here if it is wrong — not tomorrow, from an empty inbox.</p>'
      '<p style="margin-top:14px"><a href="/voice/settings" style="color:var(--accent)">'
      '&larr; Settings</a></p>')


def _connect_profile_chooser(choices: list) -> str:
    """The rare state: their account already holds several folders, so they say which one."""
    rows = "".join(
        f'<p style="margin:10px 0 0"><a class="btn" '
        f'href="/voice/connect?profile={_esc(c["id"])}">{_esc(c["name"] or "Untitled")}</a></p>'
        for c in choices)
    return (
      '<h1>Which one is this business?</h1>'
      '<div class="card"><div class="setrow">'
      '<span>Your social account keeps more than one workspace. Pick the one this box is for — '
      'Ownbox will only ever read the channels inside it.</span>'
      f'{rows}</div></div>'
      '<p style="margin-top:14px"><a href="/voice/settings" style="color:var(--accent)">'
      '&larr; Settings</a></p>')


def _connect_page(live: dict, just: str) -> str:
    """State two: connected, with a row per channel saying on or off.

    IT SAYS WHAT IS ON BEFORE IT OFFERS WHAT IS NOT. A person arriving back from a consent screen
    has one question — did that work — and the answer is the first thing on the page."""
    done = ""
    if just:
        label = dict(_CONNECTABLE).get(just, just.title())
        # LIVE, NOT THE QUERY STRING. A `?connected=` in the URL is whatever the browser was
        # handed; the only honest confirmation is the account list the vendor just returned.
        done = ('<p class="quiet" style="color:var(--accent)">'
                + _esc(f"{label} is connected. New messages start arriving on the next check.")
                + '</p>') if live.get(just) else (
                '<p class="quiet">' + _esc(f"{label} did not finish connecting. Try it again.")
                + '</p>')
    rows = []
    for vendor_token, label in _CONNECTABLE:
        if live.get(vendor_token):
            rows.append(f'<div class="setrow"><b>{_esc(label)}</b>'
                        '<span>Connected. Messages arrive on their own.</span></div>')
        else:
            rows.append(f'<div class="setrow"><b>{_esc(label)}</b>'
                        '<span>Not connected yet.</span>'
                        '<p style="margin:10px 0 0"><a class="btn" '
                        f'href="/voice/connect/{_esc(vendor_token)}">Connect {_esc(label)}</a>'
                        '</p></div>')
    return (
      '<h1>Your channels.</h1>'
      + done +
      '<div class="card">' + "".join(rows) + '</div>'
      '<p class="quiet" style="margin-top:12px">Connected with your own social account. '
      '<a href="/voice/connect?off=1" style="color:var(--accent)">Disconnect it</a> and Ownbox '
      'stops reading immediately — nothing you have already received is deleted.</p>'
      '<p style="margin-top:14px"><a href="/voice/settings" style="color:var(--accent)">'
      '&larr; Settings</a></p>')


def _connect_trouble(sentence: str) -> str:
    """One sentence a person can act on, and a way back. Never a stack trace, never a code."""
    return ('<h1>Your channels.</h1>'
            f'<div class="card"><div class="setrow"><span>{_esc(sentence)}</span></div></div>'
            '<p style="margin-top:14px"><a href="/voice/connect" style="color:var(--accent)">'
            'Try again</a> · <a href="/voice/settings" style="color:var(--accent)">Settings</a>'
            '</p>')


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
        # THE DETAIL IS ESCAPED AND THE TITLE IS NOT (see `block` below), so markup in a detail
        # renders as visible &lt;b&gt; tags. It did: the install page read "or <b>Add to Home
        # screen</b> on older versions", angle brackets and all, on the screen that tells a buyer
        # how to keep their notifications working.
        ("Tap <b>Install app</b>", "or Add to Home screen on older versions"),
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
