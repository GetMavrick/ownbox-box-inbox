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
from core.dash import blueprint, brand, look

_BASE = """
:root{--bg:var(--ground);--dim:var(--ink-3);--faint:var(--ink-3);--hover:var(--wash);
--sel:var(--card);--accent:var(--ink);--accent-dark:var(--ink-2);--on-accent:var(--on-ink);
--good:var(--ok);--danger:var(--bad);--rail:var(--ground);--nav-ink:var(--ink-2);
--drawer-flat:none;--drawer-lift:var(--shadow-lift)}
/* the box's tokens come from its one stylesheet; these names are the older ones this page's rules
   still read, each pointed at the token that replaced it */
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:calc(17 * var(--px, 1px));
line-height:1.5;-webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration:none}
svg{flex:none}

"""

# ── THE RAIL AND THE DRAWER, ON ITS OWN SO THERE IS ONE COPY OF IT ──────────────────────────
#
# EXTRACTED 2026-09-18, prompted by a question the owner asked about a screen: *"Why is there no
# hamburger menu"* on the inbox. The answer was that this drawer has always existed and `chrome()`
# was the only thing wearing it — so the screen a buyer opens twenty times a day was the one
# screen without the box's menu. A machine that draws its own page could not reuse this without
# copying it, and a copied menu is two menus by the end of the month.
#
# THE INBOX DRAWS ITS OWN PAGE ON PURPOSE, which is why importing a string is the fix and calling
# `chrome()` is not: that page carries the PWA manifest, the apple-touch icon, a theme-color that
# has to follow his light/dark choice, and the stamp on `<html>` that makes white the default.
# `chrome()` carries none of it, and an installed phone app quietly loses its identity if you swap
# one for the other.
#
# WHAT IS *NOT* IN HERE IS EACH CONSUMER'S OWN TOP BAR. `.topbar` is the dashboard's bar and it
# lives below with the dashboard's own rules, because it is `display:none` until the breakpoint —
# the inbox's bar is visible at every width and would have vanished on a desktop the moment it
# imported this. The shared part is the rail, the scrim, the hamburger's own box and the drawer
# behaviour; where the button SITS is the consumer's business.
#
# EVERY COLOUR IN HERE IS A TOKEN, WHICH IS WHAT MAKES IT SHARED RATHER THAN COPIED. This block
# used to carry five literals — `#333940` on a nav row, the account avatar's gradient, the
# drawer's shadow. The dashboard is light-only so they were invisible there; the inbox is
# theme-stamped, and `#333940` text on a dark rail is exactly the unreadable-app bug its own
# design suite refuses. The values above are unchanged, so this rail looks as it always did.
RAIL_CSS = """/* the checkbox that opens the drawer — focusable, never visible. A drawer built on a hidden
   input cannot be reached by keyboard, so this is offscreen rather than `hidden`. */
.navtoggle{position:absolute;width:1px;height:1px;opacity:0;margin:0;pointer-events:none}

.ham{display:flex;align-items:center;justify-content:center;width:42px;height:42px;
border-radius:10px;cursor:pointer;color:var(--ink)}
.ham:hover{background:var(--hover)}
.ham{position:relative}
.menubadge{position:absolute;right:0;bottom:2px;width:17px;height:17px;border-radius:50%;
background:var(--ink);color:var(--on-ink);display:flex;align-items:center;justify-content:center;
box-shadow:0 0 0 2px var(--ground)}
.menubadge svg{width:11px;height:11px}
.scrim{display:none;position:fixed;inset:0;z-index:35;background:var(--scrim);opacity:0;
pointer-events:none;transition:opacity .2s ease}

.lay{display:flex;min-height:100%;align-items:stretch}

/* THE RAIL SETS ITS OWN TYPEFACE, and until now it did not. This block is imported by every
   machine that draws its own page, so the menu inherited whatever `body` font its HOST had
   chosen — Inter Tight on the dashboard, Public Sans inside the inbox. One component, two
   faces, on two screens a buyer moves between in a single click. The owner saw it immediately
   (2026-09-22): *"The name of the box needs to be just like the dashboard. I don't know why
   this one is different font"*. Core's rail is core's, so it names the stack it wants and stops
   depending on the room it is standing in. A machine's own content keeps its own font. */
.rail{width:272px;flex:0 0 272px;background:var(--rail);border-right:1px solid var(--hairline,var(--line));
padding:0 10px 14px;display:flex;flex-direction:column;
font:calc(17 * var(--px, 1px))/1.5 var(--sans,-apple-system),-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.who{display:flex;align-items:center;gap:11px;padding:15px 8px 13px}
.who .av{width:calc(38 * var(--px, 1px));height:calc(38 * var(--px, 1px));border-radius:11px}
.who .av:not(.fill)>img{width:70%;height:70%}
.who .id{min-width:0;display:flex;flex-direction:column;line-height:1.25}
.who b{font-size:calc(17 * var(--px, 1px));white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.who span{color:var(--dim);font-size:calc(15 * var(--px, 1px));white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

/* A SUB-MENU'S TITLE BAR: the way out on the left, where you are in the middle, in bold. The
   empty third column balances the first so the title is centred on the drawer, not on what is
   left after the chevron. 48px square, because it is the control a thumb reaches for first. */
.subhead{display:grid;grid-template-columns:48px 1fr 48px;align-items:center;min-height:48px;
margin:0 0 6px}
.subhead a{display:flex;align-items:center;justify-content:center;width:48px;height:48px;
border-radius:9px;color:var(--ink)}
.subhead a:hover{background:var(--hover)}
.subhead a .ic{color:inherit}
.subhead b{text-align:center;font-size:calc(18 * var(--px, 1px));font-weight:700;white-space:nowrap;overflow:hidden;
text-overflow:ellipsis}

.nav{display:flex;flex-direction:column;gap:1px}
.nav a{display:flex;align-items:center;gap:11px;min-height:44px;padding:8px 10px;
border-radius:var(--r-sm,9px);color:var(--nav-ink);font-size:calc(17 * var(--px, 1px))}
.nav a:hover{background:var(--hover)}
.nav a[aria-current]{background:var(--sel);color:var(--ink);font-weight:600;
box-shadow:inset 0 0 0 1px var(--hairline,transparent)}
.nav a.danger{color:var(--danger)}
/* a new group starts: a little more room above it, and its name when the menu names its groups */
.nav a.grp{margin-top:16px}
.nav .glabel{display:block;margin:18px 0 2px;padding:0 10px;font-size:calc(12 * var(--px, 1px));font-weight:600;
letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3)}
.nav .glabel+a.grp{margin-top:0}
/* a plan badge beside a row this box's plan lacks: the row still opens, onto the upgrade */
.nav .badge,.row .badge{display:inline-block;margin-left:7px;padding:1px 7px;border:1px solid currentColor;
border-radius:var(--r-pill,999px);font-size:calc(11 * var(--px, 1px));font-weight:600;letter-spacing:.02em;
color:var(--link);vertical-align:1px}
.nav a .lbl{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.nav a .out{color:var(--faint);margin-left:2px}
.nav a .fwd{color:var(--faint);flex:none}
.nav a .ic{color:var(--faint)}
.nav a[aria-current] .ic{color:var(--ink)}

.railfoot{margin-top:auto;padding:12px 10px 2px;border-top:1px solid var(--hairline,var(--line));
color:var(--faint);font-size:calc(14.5 * var(--px, 1px));line-height:1.4}
.railfoot b{display:block;color:var(--dim);font-weight:600;font-size:calc(14.5 * var(--px, 1px))}

/* the way back, on every screen a menu leads into: one rust link for a thumb, the trail for a pointer */
.back{display:inline-flex;align-items:center;min-height:44px;margin:-8px 0 2px;
color:var(--link);font-size:calc(16 * var(--px, 1px));font-weight:600}
.crumb{display:none;color:var(--dim);font-size:calc(14 * var(--px, 1px));margin-bottom:10px}
.crumb b{color:var(--ink);font-weight:600}
.crumb a{color:var(--link);font-weight:600}
@media (min-width:821px){.back{display:none}.crumb{display:block}}
.main{flex:1;min-width:0;padding:22px 26px 56px;max-width:1000px}
@media (prefers-reduced-motion: reduce){.rail,.scrim{transition:none}}

/* A SMALL SCREEN — A DRAWER OVER THE PAGE, NOT A BLOCK ABOVE IT.
   Measured first, in Chromium at 375x780: stacked, the owner's own ten-item Settings menu is
   538px tall, which put the page title at 585px and the first field card at 673px. A buyer who
   tapped Settings -> Profile scrolled a full screen of menu to reach the one thing he opened it
   to change. The drawer is the owner's reference and it is better than the two answers I reached
   on my own: the menu keeps BOTH levels and all its choices, and it costs the page no height at
   all, because it is not in the page's flow until it is asked for.
   No JavaScript: one offscreen checkbox, a label for the hamburger and a label for the scrim, so
   tapping outside closes it exactly as tapping the button does. */
@media (max-width:820px){
 .scrim{display:block}
 .lay{display:block}
 .rail{position:fixed;top:0;bottom:0;left:0;width:86vw;max-width:340px;z-index:40;
  border-right:1px solid var(--line);transform:translateX(-101%);transition:transform .22s ease;
  overflow-y:auto;padding-top:env(safe-area-inset-top,0px);box-shadow:var(--drawer-flat)}
 .navtoggle:checked~.lay .rail{transform:none;box-shadow:var(--drawer-lift)}
 .navtoggle:checked~.scrim{opacity:1;pointer-events:auto}
 .main{padding:18px 16px 48px;max-width:none}
}
"""

# ── THIS PAGE'S OWN CHROME: the bar that holds the hamburger, and the cards under it ─────────
# THE MENU IS ON THE LEFT, then the client's icon and the app's name, on every screen so the button
# never changes sides; the drawer opens from the left to match. Owner, 2026-09-29, reversing the
# right-hand button he had chosen that morning: "I think it was a bad idea to move the hamburger
# menu over to the right side. In fact, I know it was. I think we should move it back to the left.
# It has a better flow and feel."
_PAGE = """.topbar{display:none;position:sticky;top:env(safe-area-inset-top,0px);z-index:30;
align-items:center;gap:12px;min-height:52px;padding:0 6px 0 4px;background:var(--ground);
border-bottom:1px solid var(--hairline)}
.navtoggle:focus-visible~.topbar .ham{outline:2px solid var(--accent);outline-offset:-2px}
.mark{font-weight:600;letter-spacing:-.01em}
.topbar .appmark{width:32px;height:32px}
/* the home's cards: one column on a mobile, a grid once there is room, as the owner's target draws */
.dash-grid{display:grid;gap:16px;margin-bottom:16px}
.dash-grid>.card{margin-bottom:0}
@media (min-width:1000px){.dash-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
.card h2.eyebrow{font-size:calc(13 * var(--px, 1px));font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:var(--ink-3);
margin:0 0 12px}
.state{display:flex;align-items:center;gap:8px;margin:0 0 10px;font-weight:600;font-size:calc(18 * var(--px, 1px))}
.state .dot,.now .dot{width:8px;height:8px;border-radius:50%;background:var(--ink-3);flex:none}
.state.ok .dot,.now .dot{background:var(--ok-dot)}
.state.stale .dot{background:var(--warn)}
.mono{font-family:var(--mono);font-size:calc(15 * var(--px, 1px));overflow-wrap:anywhere}
.now{display:flex;align-items:center;gap:8px;margin:14px 0 0;color:var(--ink-2)}
.card .row .what{color:var(--ink-2)}
.grow{flex:1}

@media (max-width:820px){
 .topbar{display:flex}
}
h1{margin:0 0 6px;font-size:var(--t-title);font-weight:600;letter-spacing:-.02em;line-height:1.1}
.lede{margin:0 0 24px;color:var(--ink-2);font-size:calc(17 * var(--px, 1px))}
.card{background:var(--card);border:1px solid var(--card-edge);border-radius:var(--r-md);
padding:22px 22px;margin-bottom:16px}
.card h2{margin:0 0 4px;font-size:calc(18 * var(--px, 1px));font-weight:600;letter-spacing:-.01em}
.card .sub{color:var(--ink-2);font-size:calc(16 * var(--px, 1px));margin:0 0 12px}
.card p,.card ul,.card ol{margin:0 0 12px}
.card>:last-child,.card p:last-child{margin-bottom:0}
.card ul,.card ol{padding-left:22px}
.card li+li{margin-top:6px}
.card h3{margin:22px 0 6px;font-size:calc(17 * var(--px, 1px));font-weight:600;letter-spacing:-.01em}
.card h2+h3,.card h3:first-child{margin-top:8px}
.card.notice{border-left:3px solid var(--warn)}
.card details{margin:0 0 12px}
.card summary{cursor:pointer;display:block;padding:11px 0;color:var(--link);font-weight:600;
font-size:calc(16 * var(--px, 1px));list-style:none}
.card summary::-webkit-details-marker{display:none}
.card summary::after{content:"+";margin-left:6px;font-weight:400}
.card details[open]>summary::after{content:"−"}
details.fold>summary{color:var(--link);font-weight:600;font-size:calc(16 * var(--px, 1px));list-style:none}
details.fold>summary::-webkit-details-marker{display:none}
details.fold>summary::after{content:"+";margin-left:6px;font-weight:400}
details.fold[open]>summary::after{content:"−"}
@media (min-width:720px){.card{padding:28px 32px}.lede{font-size:var(--t-lede)}}
.row{display:flex;align-items:baseline;gap:10px;padding:8px 0;border-top:1px solid var(--hairline)}
.row:first-of-type{border-top:0}
.card ol.steps{margin:4px 0 12px;padding-left:22px}
.card ol.steps li{margin:8px 0;line-height:1.5}
.card code{font-family:var(--mono);font-variant-ligatures:none;font-size:.9em;
background:var(--wash);padding:1px 5px;border-radius:6px;overflow-wrap:anywhere}
.card a.step{align-items:center;flex-wrap:nowrap;min-height:48px;color:var(--ink)}
.card a.step b{flex:1;min-width:0;font-weight:600}
.card a.step b small{display:block;font-size:calc(14 * var(--px, 1px));font-weight:400;color:var(--ink-3)}
.card a.step .go{color:var(--dim);flex:none;font-size:calc(15 * var(--px, 1px))}
.card a.step .fwd{color:var(--faint);flex:none}
a.row:hover{color:var(--accent)}
.n{font-variant-numeric:tabular-nums;font-weight:600;min-width:2.2em}
.big{font-size:calc(33 * var(--px, 1px));font-weight:600;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.quiet{color:var(--dim)}
.stale{color:var(--warn)}

/* ── CONTROLS ──────────────────────────────────────────────────────────────────────────────
   EVERY FORM IN core/ RAN ON BROWSER DEFAULTS UNTIL NOW, and nobody noticed while core had no
   forms in it. The box's own settings screens arrived with #1418 and brought the first buttons
   and fields this stylesheet has ever had to draw: a grey 1995 submit
   button under a styled card, and a label sitting ON the same line as its input because nothing
   here had ever said `display:block`. Owner, 2026-09-22, looking at his own box: *"it needs to be
   styled out with the buttons"*.
   A CARD IS NOT A DESIGN SYSTEM. The rule that keeps this honest is that a control takes its
   colour from the same tokens the surface behind it does — no literal anywhere below. */
label{display:block;font-size:var(--t-label);font-weight:600;margin:16px 0 8px}
input[type=text],input[type=password],input[type=email],input[type=url],input[type=time],input:not([type]),
select,textarea{width:100%;font:inherit;font-size:max(16px, calc(17 * var(--px, 1px)));font-weight:400;min-height:var(--control);
padding:12px 14px;border:1px solid var(--line);border-radius:var(--r-sm);background:var(--card);
color:var(--ink)}
input::placeholder{color:var(--faint)}
select{appearance:auto;cursor:pointer}
/* A DISABLED OPTION IS THE POINT OF THE PICKER, not a defect in it — see `_choice_picker`. */
select option:disabled{color:var(--faint)}
/* A PILL, NOT A ROUNDED RECTANGLE. Owner, 2026-09-22: *"I like the blue that's cool. But make
   sure you are using 9999 pixel radius, rounded pills"*. 9999px rather than 50%: a percentage
   radius on a box wider than it is tall draws an ellipse that changes shape with the label, and
   these buttons carry labels from one word to four. A radius larger than the box
   always clamps to exactly half the height, so every button on the box is the same pill whatever
   it says. The horizontal padding goes up with it — a pill needs room at the ends or the first
   and last letters sit on the curve.

   MOBILE FIRST, AND THAT IS THE BASE RULE RATHER THAN A BREAKPOINT. The owner's standing ruling
   of 2026-09-22, quoted in full in CLAUDE.md: nine buyers in ten are on a mobile device, so the
   mobile rule is the unqualified one and the desktop gets the media query, not the other way
   round. A full-width pill at a 48px tap target — a thumb's worth of button, comfortably past
   the 44px both platforms ask for — and the `min-width` query below shrinks it to its label once
   there is a pointer to aim with.

   HIS WORDS ARE IN CLAUDE.md AND NOT HERE ON PURPOSE. Every byte of this stylesheet is inlined
   into every page the box serves, so a quotation in it is a quotation a buyer receives — and his
   phrasing that morning used the very noun the naming ruling below now reserves. The doctrine
   file is read by developers and served to nobody, which is where a verbatim quote belongs. */
button{display:block;width:100%;min-height:var(--control);font:inherit;font-size:calc(17 * var(--px, 1px));
font-weight:600;padding:12px 26px;margin-top:16px;border:1px solid var(--accent);
border-radius:var(--r-pill);background:var(--accent);color:var(--on-accent);cursor:pointer}
button:hover{opacity:.88}
/* THE SECOND BUTTON ON A SCREEN IS NEVER THE ONE WE WANT PRESSED — Cancel beside Finish, and the
   pair read identically while both were the browser's default grey. */
button.ghost{background:var(--card);color:var(--ink);border-color:var(--line);font-weight:600}
button.ghost:hover{background:var(--wash);border-color:var(--line);opacity:1}
/* THE HALT IS NOT THE FRIENDLY BLUE ONE. Making every button accent-filled turned the control
   that stops the box into the most inviting thing on the dashboard — worse than the anonymous
   grey default it replaced, because grey at least did not ask to be pressed. Outlined rather
   than filled: a thumb scrolling past a full-width red slab is a thumb that eventually hits it.

   AND NO CONTROL'S LABEL IS QUOTED ANYWHERE IN THIS STYLESHEET, for the same reason no route is
   (see the row rule below): every byte of it is inlined into every page, so a label written in a
   comment here is a label PRESENT in the HTML of a page that must not offer it. That is not
   theoretical — `test_the_box_shows_a_buyer_the_way_in` went red on the first draft of this very
   block, an hour after the same mistake with a route went red one rule further down. */
button.danger{background:var(--card);color:var(--danger);border-color:var(--danger)}
button.danger:hover{background:var(--danger);color:var(--card);border-color:var(--danger);opacity:1}

@media (min-width:560px){
 /* A POINTER CAN HIT A SMALL TARGET, so a desktop button is the width of what it says. */
 button{display:inline-block;width:auto}
}
input:focus-visible,select:focus-visible,textarea:focus-visible,button:focus-visible{
outline:2px solid var(--ink);outline-offset:2px}
/* THE TICK IS NOT A FIELD AND MUST NOT WEAR A FIELD'S LABEL. Bold, block and 16px above a
   checkbox makes a consent line shout; it is a sentence somebody reads, beside a box. */
label.consent{display:flex;gap:10px;align-items:flex-start;margin:16px 0 0;
font-size:calc(15 * var(--px, 1px));font-weight:400;line-height:1.45;cursor:pointer}
label.consent input{width:22px;height:22px;flex:none;margin:0;accent-color:var(--ink)}
/* `a{color:inherit}` IS RIGHT FOR THE RAIL AND WRONG FOR PROSE. Every terms link, console link
   and "Sign in to Claude" on the settings screens rendered as plain text — unfindable unless you
   happened to drag the pointer over it. Scoped to paragraphs so rows and the rail keep theirs. */
.card p a{color:var(--link)}
.card p a:hover{text-decoration:underline}
.foot{margin-top:18px;font-size:calc(15 * var(--px, 1px))}
.foot a{color:var(--link)}
.foot a:hover{color:var(--link)}
/* AN ADDRESS SOMEBODY HAS TO COPY BY HAND. It was unstyled prose; on a narrow screen it ran off
   the card. Monospace, a ground it can sit on, and it wraps rather than overflows.

   THE VOCABULARY IN THIS FILE'S COMMENTS IS PRODUCT VOCABULARY, which is worth writing down
   because nothing about editing a stylesheet suggests it. `CSS` here is inlined into every served
   page, so these comments are shipped text: a word in one reaches a buyer exactly as a word in a
   heading does. The receptionist machine now being built gets to keep the nouns that describe
   what it does, and the thing a buyer installs on a device is the mobile app — the owner's naming
   ruling of 2026-09-22, relayed by OSDev4 in #1426.
   `tests/test_core_css_keeps_the_vocabulary.py` measures this rather than trusting this comment,
   and it caught this very paragraph naming a reserved route while explaining the rule. */
.addr{font:calc(16 * var(--px, 1px))/1.5 var(--mono);font-variant-ligatures:none;
background:var(--bg);border:1px solid var(--hairline);border-radius:var(--r-xs);
padding:10px 12px;margin:10px 0 0;word-break:break-all;user-select:all}
/* A ROW WHOSE TRAILING TEXT IS A SENTENCE, not a number. The coworkers screen puts four of
   these under `Works with` and each one drove its instruction hard against the right edge.
   NO ROUTE IS NAMED IN THIS FILE'S CSS, and that is not fussiness: this stylesheet is inlined
   into EVERY page, so a path mentioned in a comment here is a path present in the HTML served to
   a member who is refused at it — which is exactly what
   `test_the_box_settings_are_the_boxs_own` measures, and exactly how it caught this block. */
.row{flex-wrap:wrap}
.row.setting{flex-wrap:nowrap;align-items:center;padding:12px 0}
.row.setting .what{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px}
.row.setting .what>span{font-size:calc(16 * var(--px, 1px))}
.row.setting .step{flex:none;width:26px;height:26px;border-radius:50%;display:flex;align-items:center;
justify-content:center;font-size:calc(13 * var(--px, 1px));font-weight:600;color:var(--ink-2);
border:1px solid var(--hairline)}
.row.setting .step.done{background:var(--ink);color:var(--on-ink);border-color:var(--ink)}
.row.setting{gap:14px}
.row.setting .act{color:var(--link);font-weight:600;white-space:nowrap;min-height:44px;
display:flex;align-items:center}
.row>.quiet{flex:1 1 320px;min-width:0}
/* THE APPS AS THEY WILL SIT ON A HOME SCREEN: the real icons and names on a warm ground, so a
   person knows what they are adding before they add it. */
.apps{display:flex;flex-wrap:wrap;gap:22px 26px;justify-content:center;padding:26px 16px 22px;
margin:4px 0 14px;border-radius:var(--r-md);
background:linear-gradient(170deg,var(--ground),color-mix(in srgb,var(--link) 22%,var(--ground)))}
.apps a{display:flex;flex-direction:column;align-items:center;gap:8px;width:84px;min-height:48px;
color:var(--ink);font-size:calc(14 * var(--px, 1px));font-weight:600;text-align:center;line-height:1.2}
.apps img,.iconsee .tile{width:64px;height:64px;border-radius:15px;box-shadow:0 1px 3px rgba(17,17,17,.18)}
/* THE DANGER ZONE, last on Server Access, in GitHub's shape (owner, 2026-09-30): the title in the
   colour that means stop, one box edged in it, a row per action with its red button on the right,
   stacked under the words on a mobile. A row opens to a warning and a second button. */
.danger{margin-top:40px}
.danger>h2{margin:0 0 4px;color:var(--danger);font-size:calc(22 * var(--px, 1px));font-weight:700}
.danger>.sub{color:var(--ink-2);font-size:calc(16 * var(--px, 1px));margin:0 0 14px}
.dz{border:1px solid var(--danger);border-radius:var(--r-md);background:var(--card)}
.dzrow{padding:18px 20px}
.dzrow+.dzrow{border-top:1px solid var(--card-edge)}
.dzrow>summary{list-style:none;cursor:pointer;display:flex;flex-wrap:wrap;align-items:center;gap:12px 20px}
.dzrow>summary::-webkit-details-marker{display:none}
.dzrow .what{flex:1 1 240px;min-width:0;display:flex;flex-direction:column;gap:2px}
.dzrow .what b{font-size:calc(17 * var(--px, 1px))}
.dzrow .what>span{color:var(--ink-2);font-size:calc(16 * var(--px, 1px))}
.dzbtn{display:inline-flex;align-items:center;justify-content:center;min-height:44px;padding:0 18px;
border:1px solid var(--card-edge);border-radius:var(--r-pill);background:var(--wash);color:var(--danger);
font-weight:600;white-space:nowrap}
.dzrow[open] .dzbtn .go,.dzrow:not([open]) .dzbtn .no{display:none}
.dzbody{margin-top:16px;display:grid;gap:12px}
.dzbody p,.dzbody h3{margin:0}
.dzrow form{margin-top:4px}
/* THE CLIENT'S ICON WHERE IT IS SEEN: the circle that opens the menu, and its home-screen tile. */
.iconsee{display:flex;gap:28px;align-items:flex-end;margin:4px 0 14px}
.iconsee figure{margin:0;display:flex;flex-direction:column;align-items:center;gap:8px}
.iconsee figcaption{color:var(--dim);font-size:calc(14 * var(--px, 1px))}
.iconsee .ui-disc{width:44px;height:44px}
/* A LINK THAT IS THE ACTION wears the ink pill, full width until a pointer exists. */
.card a.btn{display:flex;align-items:center;justify-content:center;min-height:var(--control);
padding:12px 26px;border-radius:var(--r-pill);background:var(--accent);color:var(--on-accent);
font-weight:600;text-decoration:none}
.card a.btn:hover{opacity:.88;text-decoration:none}
/* A PERSON: who they are on top, their two outlines under it, side by side. */
.row.person{display:block;padding:14px 0}
.row.person .what{display:flex;flex-direction:column;gap:2px;overflow-wrap:anywhere}
.row.person .what .quiet{font-size:calc(15 * var(--px, 1px))}
.row.person .acts{display:flex;gap:10px;margin-top:10px}
.row.person .acts form{flex:1}
.row.person .acts button{margin-top:0}
@media (min-width:560px){
 .card a.btn{display:inline-flex}
 .row.person{display:flex;align-items:center;gap:14px}
 .row.person .what{flex:1;min-width:0}
 .row.person .acts{margin-top:0;flex:none}
 .row.person .acts form{flex:none}
}

"""

# ORDER IS THE ORDER IT ALWAYS WAS — the phone's overrides sit last, after the base rules they
# are written to override. Verified by rendering both and diffing every computed property.
CSS = _BASE + _PAGE + RAIL_CSS


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


# LEAVES THE BOX. Drawn, not typed: a CSS `content:"\2197"` renders as tofu wherever the face
# lacks the glyph, which is exactly what a headless render showed — a missing-character box
# followed by a stray "97" beside the word Billing. Every other mark in this rail is an SVG for
# the same reason, and an icon that depends on the user's installed fonts is not an icon.
_OUT_ARROW = ('<svg class="out" width="13" height="13" viewBox="0 0 24 24" fill="none" '
              'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
              'stroke-linejoin="round" aria-hidden="true"><path d="M7 17 17 7M9 7h8v8"/></svg>')
# CORE'S OWN SECTION IS THE ONLY ICON THIS FILE HOLDS. Every other one arrives with the machine
# that registered the section, the same way its title does.
# A SCREEN WITH A STAND, because this row is where the box reports what it did — the owner asked
# for "a control panel of some sort for a screen" (2026-09-22). The house shape it replaces said
# "home", which is true of the route and says nothing about what is on it.
_HOME_ICON = "M3 4.5h18v11.5H3zM9 20h6M12 16v4"
# TWO CHEVRONS, AND THEY ARE A PAIR. Owner, 2026-09-23, on the reference he has sent more than
# once: *"Notice the forward chevron to show when a menu item has sub menu items."* and, of the
# same reference opened on Settings, *"the first thing at the top you have a back arrow/chevron
# that functions as the back button. And then settings is in bold showing that we are on the
# settings."* The forward one sits at the right edge of a row that opens a sub-menu; the back one
# sits at the left of the sub-menu's own title. Same stroke, mirrored, so the way in and the way
# out read as one gesture. (The shaft-and-head arrow this replaces was a row of its own labelled
# Dashboard; the owner retired it the same day: *"we need to do away with the back arrow and the
# dashboard that we have."*)
_BACK_ARROW = "M15 5l-7 7 7 7"
_FWD_CHEVRON = ('<svg class="fwd" width="16" height="16" viewBox="0 0 24 24" fill="none" '
                'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
                'stroke-linejoin="round" aria-hidden="true"><path d="M9 5l7 7-7 7"/></svg>')
# A PLUS, and nothing else. This row adds a machine to the box; the glyph is the verb.
_ADD_ICON = "M12 5.5v13M5.5 12h13"
# A SUN for the Morning Review, and nothing else (owner, 2026-09-29: "The morning review can just be
# a sun instead of have the arrow"): a disc and eight even rays.
_SUN_ICON = ("M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0ZM12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2"
             "M5.28 5.28l1.42 1.42M17.3 17.3l1.42 1.42M5.28 18.72l1.42-1.42M17.3 6.7l1.42-1.42")
# A GEAR. Two subpaths in one `d` — the cog outline and the hole — because `_svg` draws exactly
# one path and a gear without its centre reads as a flower. REDRAWN 2026-09-29 (owner: "the System
# Settings gear needs to be redrawn. It's kind of mangled"): the old one was hand-traced and its
# teeth were uneven. This one is computed — six identical teeth on a 7.4 root and 9.4 tip radius,
# joined along the root circle, around a 3-unit hole — and six teeth, not eight, because fewer,
# larger teeth stay crisp at the menu's 19px, in the dark theme most of all.
_GEAR_ICON = ("M9.71 4.96L10 2.61L14 2.61L14.29 4.96A7.4 7.4 0 0 1 16.95 6.5L19.13 5.58L21.13 9.03"
              "L19.24 10.46A7.4 7.4 0 0 1 19.24 13.54L21.13 14.97L19.13 18.42L16.95 17.5"
              "A7.4 7.4 0 0 1 14.29 19.04L14 21.39L10 21.39L9.71 19.04A7.4 7.4 0 0 1 7.05 17.5"
              "L4.87 18.42L2.87 14.97L4.76 13.54A7.4 7.4 0 0 1 4.76 10.46L2.87 9.03L4.87 5.58"
              "L7.05 6.5A7.4 7.4 0 0 1 9.71 4.96ZM15 12a3 3 0 1 1-6 0a3 3 0 1 1 6 0Z")


def _svg(d: str, size: int = 19) -> str:
    """One stroke icon. A section with no icon renders a fixed-width blank instead of nothing, so
    the labels stay on one optical column — a list where some rows indent and others do not reads
    as a mistake before it reads as a list."""
    if not d:
        return f'<span class="ic" style="width:{size}px" aria-hidden="true"></span>'
    return (f'<svg class="ic" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
            f'stroke="currentColor" stroke-width="1.7" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true"><path d="{_esc(d)}"/></svg>')


def _account(who: str, email: str) -> str:
    """The block at the top of the rail: which box this is, and who is signed in.

    IT NAMES THE BOX, NOT THE PERSON. `dash.brand()` already answers "whose box is this" and
    answers it correctly on a sold one — the buyer's own company, not ours. A rail headed with an
    individual's name reads as a personal account on a product whose entire pitch is that a
    business owns it.

    AND IT WEARS THE BOX'S ICON, not the name's first letter. Owner, 2026-09-30: the client's icon
    is "used inside the dashboard like in the upper left side of the menu where it's not currently
    being updated properly". The letter tile never changed when an icon was uploaded; the menu
    opens from that icon, and should open onto it.
    """
    return (f'<div class="who"><span aria-hidden="true">{look.header_mark("av")}</span>'
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
    # WHO IS LOOKING, asked once. An owner-only row is dropped for everyone else rather than drawn
    # and refused at — see `shell.Item.owner_only`. Unreadable means "not the owner", because the
    # safe answer to "may I show this person root on the server" is always no.
    try:
        from core.dash import session_user as _who_rail
        _owner = (_who_rail(request) or {}).get("role") == "owner"
    except Exception:                            # noqa: BLE001 — no request, no owner
        _owner = False
    rows = []
    # THE GAP BEFORE A NEW GROUP IS CARRIED, not dropped with its row: if the first row of the
    # add-on group is one this person is not shown, the gap moves to the next row that is.
    gap, label = False, ""
    for it in got.items:
        gap = gap or it.group_start
        # A GROUP'S NAME TRAVELS WITH ITS GAP to the first row this person is shown, and a new group's
        # name replaces one whose rows were all hidden, so a name never sits over another group's rows.
        label = it.label_above or (label if not it.group_start else "")
        if it.owner_only and not _owner:
            continue
        # OFF-BOX IS READ FROM THE HREF, never declared beside it. `away` still works for a row
        # that says so, but a row whose destination is an https:// address IS away whether or not
        # somebody remembered the tone, and the two cannot drift apart.
        off = shell.is_off_box(it.href)
        if have and not off and it.href not in have and it.tone != "away":
            # SILENTLY ABSENT, not greyed out. A disabled row still promises the thing exists.
            #
            # AND AN OFF-BOX ROW IS EXEMPT, which is not a loophole but the same rule: `_serving()`
            # answers "does THIS box route that path", and a website on somebody else's domain is
            # not a path this box routes. Without the exemption the Add a Machine row would be
            # dropped from every rail as unserved — measured, not reasoned.
            continue
        cls = " ".join(c for c in (it.tone, "grp" if gap and rows else "") if c)
        if label:
            rows.append(f'<span class="glabel">{_esc(label)}</span>')
        gap, label = False, ""
        badge = ""
        if it.feature:
            try:
                from core import tiers as _tiers
                badge = _tiers.badge(it.feature)
            except Exception:                    # noqa: BLE001 — a badge is never worth a broken menu
                badge = ""
        cur = ' aria-current="page"' if shell.is_current(it, path) else ""
        # A LINK THAT LEAVES OPENS AWAY FROM THE BOX and carries `noopener`: the destination is
        # outside this box's control, and a menu row is not a reason to hand it this window.
        away = (' target="_blank" rel="noopener noreferrer"' if off else "")
        rows.append(f'<a href="{_esc(it.href)}"{away}'
                    + (f' class="{_esc(cls)}"' if cls else "")
                    # A SUB-MENU'S ROWS ARE WORDS ONLY. In the reference the icons belong to the
                    # first level, where they tell sections apart at a glance; one level down every
                    # row is already inside one section, and the title above them says which.
                    + f'{cur}>{_svg(it.icon) if got.level == 1 else ""}'
                    + f'<span class="lbl">{_esc(it.label)}'
                    + (f'<span class="badge">{_esc(badge)}</span>' if badge else "") + '</span>'
                    + (_OUT_ARROW if (off or it.tone == "away") else "")
                    + (_FWD_CHEVRON if it.submenu else "") + '</a>')

    head = _account(who or brand(), email)
    if got.level == 2:
        # A SUB-MENU OPENS WITH ITS OWN NAME AND THE WAY OUT OF IT, on one line. Owner, 2026-09-23,
        # of the reference opened on Settings: *"the first thing at the top you have a back
        # arrow/chevron that functions as the back button. And then settings is in bold showing
        # that we are on the settings. I think we need to do away with the back arrow and the
        # dashboard that we have."*
        #
        # SO THE DASHBOARD ROW IS GONE, and with it the argument the comments here used to record
        # — a row or chrome, an arrow or the dashboard's own icon (his rulings of 09-21 and 09-22,
        # in git history). The reference settles it by doing neither: the title says where you
        # ARE, in bold, and the chevron beside it is the only control that leaves. Exactly one way
        # back still, and it still goes where `shell` resolved — the words that name it are its
        # accessible label, from `back_label`, so the link and its name cannot come apart.
        head += (f'<div class="subhead"><a class="home" href="{_esc(got.back)}" '
                 f'aria-label="Back to {_esc(got.back_label or "the start")}">'
                 f'{_svg(_BACK_ARROW, 20)}</a><b>{_esc(got.title)}</b></div>')
    return (f'<nav class="rail" id="railnav" aria-label="Sections">{head}'
            f'<div class="nav">{"".join(rows)}</div>{_foot()}</nav>')



def trail(path: str) -> str:
    """The way back, drawn the same on every screen a menu leads into — or "" when there is none.

    OWNER, 2026-09-29: "it's very helpful to have those rust colored breadcrumbs. It's not
    consistent when they appear so we should make them standard anytime you're drilled down to a
    sub menu page." They were drawn by this module's `chrome()` only, so a machine drawing its own
    shell (the Unified Inbox) had none, and the desktop trail was grey text with nothing to press.

    ONE RULE, EVERYWHERE: a screen that is a row of a menu, other than that menu's first screen,
    gets one rust link back to where the menu begins (a thumb's, below 821px) and the trail with
    its parent as a rust link (a pointer's, from 821px). Never on the menu's first screen, never
    back to the page itself — a section whose address forwards to its first row (the AEO Machine
    lands on Articles) would otherwise offer a link back to the screen it is.
    """
    sec, here = shell.current(path), shell.current_item(path)
    if sec is None or here is None or not sec.items:
        return ""
    landing = next((i.href for i in sec.items if i.href == sec.href), sec.items[0].href)
    # BY KEY: the rail hands back a grouped copy of a row (`shell._grouped`), the same row but not
    # an equal object. The Base Machine's section has no rows, so it never gets here.
    if not any(i.key == here.key for i in sec.items):
        return ""
    at_start = here.href == landing or sec.href.rstrip("/") == (path or "").rstrip("/")
    if at_start:
        # A MENU INSIDE A MENU IS ITSELF DRILLED INTO (the AEO Machine's Data Sources): its first
        # screen goes back up one, to the menu it opened from. A top-level menu's first screen has
        # nowhere above it but the main menu, which the header's button already opens.
        up = next((s for s in shell.sections() if s.key == sec.parent), None) if sec.parent else None
        return link_back(up.href, up.title, sec.title) if up is not None else ""
    return link_back(sec.href, sec.title, here.label)


def link_back(href: str, to: str, here: str) -> str:
    """The way back itself: one rust link for a thumb, and the trail with its parent a rust link
    for a pointer. Shared, so a machine's own screens draw it exactly as the box's do."""
    return (f'<a class="back" href="{_esc(href)}">&lsaquo; {_esc(to)}</a>'
            f'<div class="crumb"><a href="{_esc(href)}">{_esc(to)}</a> / <b>{_esc(here)}</b></div>')


def chrome(path: str, *, title: str, lede: str, body: str,
           who: str = "", email: str = "") -> str:
    """The buyer-facing page: rail, breadcrumb, one title, one plain sentence, then cards."""
    parts = shell.crumb(path)
    sec, here = shell.current(path), shell.current_item(path)
    # THE WAY BACK IS ONE FUNCTION, SHARED WITH EVERY MACHINE'S SCREENS (`trail`).
    way_back = trail(path)
    # WHICH APP THIS SCREEN IS. A machine that registered one (core/dash/look.py) names every tab
    # and every home-screen icon for itself; anything else is the Base Machine's.
    app = look.app_for(sec.machine if sec is not None else None)
    # WHICH OF THE THREE SHAPES THIS PAGE IS, decided from the path like everything else here.
    # `crumb()` already answers it: two parts means an item inside a section, one part means the
    # section's own index, none means level 1. The stylesheet needs it and nothing else does.
    shape = "lvl1" if not parts else ("lvl2-item" if len(parts) > 1 else "lvl2-index")
    name = who or brand()
    # THIS PERSON'S APPEARANCE, stamped before the first paint (core/dash/theme.py). White until
    # they choose; System carries the one-line script the server can't do for it.
    from core.dash import theme
    th = theme.for_request()
    return f"""<!doctype html><html lang="en"{theme.html_attr(th)}><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow">
{theme.head_tags(th)}
<title>{_esc(name)} · {_esc(app["name"] if app else title)}</title>{look.head_tags()}{look.app_tags(app)}<style>{CSS}</style></head><body>
<input class="navtoggle" type="checkbox" id="navtoggle" aria-controls="railnav">
<div class="topbar">{look.menu_button()}<span class="mark">{_esc(app["name"] if app else "Base Machine")}</span></div>
<label class="scrim" for="navtoggle" aria-label="Close menu"></label>
<div class="lay {shape}">{rail_html(path, who=who, email=email)}
<main class="main">{way_back}<h1>{_esc(title)}</h1><p class="lede">{_esc(lede)}</p>
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
    # ONE RULE FOR "IS THERE ANYTHING TO SHOW", shared with the Morning Review (owner, 2026-09-29:
    # "If a line doesn't have data, it should not be displayed"). Zero is nothing too.
    from core.dash.review import has_value
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
        if txt and (val is None or has_value(val)):
            rows.append('<div class="row">'
                        + (f'<span class="n">{_esc(val)}</span>' if val is not None else "")
                        + f'<span>{_esc(txt)}</span></div>')
        if seg.get("error"):
            # A SOURCE THAT FAILED MUST NOT LOOK LIKE A SOURCE WITH NOTHING TO SAY (report §1.11).
            rows.append(f'<div class="row"><span class="quiet">{_esc(seg["error"])}</span></div>')
        # THE MACHINE'S OWN FIGURES, up to four, as the owner's target dashboard draws them (2026-09-29):
        # each a number over the words its machine chose. Only figures with a value: an empty one is a
        # promise of data, which this page does not make.
        figs = [f for f in (seg.get("figures") or {}).values()
                if isinstance(f, dict) and has_value(f.get("value")) and f.get("label")][:4]
        if figs:
            rows.append('<div class="ui-metrics">' + "".join(
                f'<div class="ui-metric"><b>{_esc(f["value"])}</b><span>{_esc(f["label"])}</span></div>'
                for f in figs) + '</div>')
        if not rows:
            continue
        out.append(f'<div class="card"><h2 class="eyebrow">{_esc(seg.get("title") or "")}</h2>'
                   + "".join(rows) + '</div>')
    return "".join(out)


# ── THE BOX'S OWN CARDS: the machine, its work, what is connected to it, and its AI ─────────────
# Owner, 2026-09-29, his target dashboard. Every figure here is read from something the box already
# records — core.box_tools.health() (the reading the box's own MCP tool gives), the jobs table, the
# connector's seats, the nightly database copy, the disk — and a figure the box does not record is
# not drawn. Money is left out on purpose: test_a_member_has_a_home holds this page to none,
# because members read it too; spend stays on the owner-only Morning Review.
def _host() -> str:
    from flask import request as _rq
    try:
        from core import claim
        return claim.provisioned_host() or _rq.host
    except Exception:                        # noqa: BLE001 — a card, never a 500
        return _rq.host


def _ago_short(iso: str) -> str:
    from datetime import datetime, timezone
    try:
        then = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        s = max(0, int((datetime.now(timezone.utc) - then).total_seconds()))
    except (TypeError, ValueError):
        return ""
    return f"{s}s" if s < 60 else (f"{s // 60}m" if s < 3600 else f"{s // 3600}h")


def _this_machine_card() -> str:
    import shutil
    from core import box_tools
    try:
        h = box_tools.health()
    except Exception:                        # noqa: BLE001
        return ""
    worker = h.get("worker") or {}
    if pause.is_paused():
        word, tone = "Stopped by you", "stale"
    elif (h.get("alerts") or {}).get("failing"):
        word, tone = "Needs attention", "stale"
    elif worker.get("ok"):
        word, tone = "Running", "ok"
    elif worker.get("state") == "no_beat_yet":
        word, tone = "Starting", "quiet"
    else:
        word, tone = "Not running", "stale"
    rows = [f'<div class="row"><span class="what">Address</span><span class="mono">{_esc(_host())}</span></div>']
    # LAST BACKUP: the newest nightly copy, as scripts/backup_db.py writes it (aios-backup.timer, 03:00
    # owner time, <db dir>/backups/aios-YYYY-MM-DD.db, seven kept). Litestream also carries the database
    # off the box as it changes; the nightly copy is the one with a time a person can read.
    last = _last_backup()
    # A LINE WITHOUT DATA IS NOT DRAWN (owner, 2026-09-29). Before the first nightly copy there is
    # no backup time to show, so there is no row; it appears the morning after the first copy.
    if last:
        rows.append('<div class="row"><span class="what">Last backup</span>'
                    f'<span>{_esc(last)}</span></div>')
    try:
        du = shutil.disk_usage("/")
        rows.append(f'<div class="row"><span class="what">Disk</span>'
                    f'<span>{du.used / 1e9:.1f} GB of {du.total / 1e9:.0f} GB</span></div>')
    except OSError:
        pass
    return ('<div class="card"><h2 class="eyebrow">This machine</h2>'
            f'<p class="state {tone}"><span class="dot"></span>{_esc(word)}</p>' + "".join(rows) + '</div>')


def _last_backup() -> str:
    """When the newest nightly database copy was taken, in the box's own time, or "" when none is."""
    from datetime import datetime
    from pathlib import Path
    try:
        from core.config import settings
        found = sorted(Path(settings.db_path).resolve().parent.joinpath("backups").glob("aios-????-??-??.db"))
        if not found:
            return ""
        when = datetime.fromtimestamp(found[-1].stat().st_mtime, report.tz())
        today = report.today()
        day = ("Today" if when.date() == today else
               "Yesterday" if (today - when.date()).days == 1 else when.strftime("%a %-d %b"))
        return f"{day} {when:%H:%M}"
    except Exception:                        # noqa: BLE001 — a row, never a 500
        return ""


def _queue_card() -> str:
    from core import box_tools, state
    try:
        q = (box_tools.health().get("queue") or {})
        start = report.window(report.today())[0]
        with state.connect() as c:
            done = c.execute("SELECT COUNT(*) FROM jobs WHERE status = 'done' AND updated_at >= ?",
                             (start,)).fetchone()[0]
            now = c.execute("SELECT intent, id, updated_at FROM jobs WHERE status = 'running' "
                            "ORDER BY updated_at DESC LIMIT 1").fetchone()
    except Exception:                        # noqa: BLE001
        return ""
    # ONLY THE FIGURES WITH SOMETHING IN THEM (owner, 2026-09-29: "If nothing exists, we don't
    # wanna have zeros"), and a queue with nothing running, waiting, done or failed is no card.
    figs = [(k, int(v or 0)) for k, v in (("Running", q.get("running", 0)),
                                          ("Waiting", q.get("queued", 0)), ("Done today", done))
            if int(v or 0)]
    body = ('<div class="ui-metrics">' + "".join(
        f'<div class="ui-metric"><b>{v}</b><span>{k}</span></div>' for k, v in figs) + '</div>'
            if figs else "")
    if now:
        # THE WORK IN WORDS, not its code name and id: "draft_reply · 90b7" read like a log line.
        words = (str(now[0] or "").replace("_", " ").strip().capitalize()) or "Working"
        ago = _ago_short(now[2])
        body += (f'<p class="now"><span class="dot"></span>{_esc(words)}'
                 + (f' · started {_esc(ago)} ago' if ago and ago != "0s" else " · just started")
                 + '</p>')
    if q.get("failed_24h"):
        n = int(q["failed_24h"])
        body += f'<p class="stale">{n} {"job" if n == 1 else "jobs"} failed in the last day</p>'
    if not body:
        return ""
    return f'<div class="card"><h2 class="eyebrow">Queue</h2>{body}</div>'


def _mcp_card() -> str:
    """OWNER ONLY: which AI agents hold a key to this box is the owner's business."""
    if not _is_owner():
        return ""
    from flask import request as _rq
    try:
        from core.connector import seats
        live = [s for s in seats.all_seats(include_revoked=False) if s.get("last_seen_at")]
    except Exception:                        # noqa: BLE001
        return ""
    address = f"{_rq.scheme}://{_host()}/mcp"
    if live:
        names = ", ".join(sorted({str(s.get("label") or "a client") for s in live}))
        said = (f'<p class="state ok"><span class="dot"></span>{len(live)} '
                f'{"client" if len(live) == 1 else "clients"} connected · {_esc(names)}</p>')
    else:
        go = "/settings/agent" if "/settings/agent" in _serving() else ""
        said = ('<p class="quiet">Nothing connected yet.'
                + (f' <a href="{go}">Connect an AI agent &rarr;</a>' if go else "") + '</p>')
    # NAMED BY THE MENU'S NAME FOR IT: MCP Server since 2026-10-02 (owner, from the System Settings
    # preview; Coworkers (Agents) from 2026-09-30). The address stays: it is what gets pasted into one.
    return (f'<div class="card"><h2 class="eyebrow">MCP Server</h2>'
            f'<p class="mono">{_esc(address)}</p>{said}</div>')


def _ai_card() -> str:
    """OWNER ONLY, like the setting it reports: the AI account is the owner's to connect."""
    if not _is_owner():
        return ""
    from core import box_secrets
    try:
        e = next((x for x in box_secrets.setup_state() if x.get("key") == "anthropic"), None)
    except Exception:                        # noqa: BLE001
        return ""
    if e is None or e.get("status") == "not_connected":
        # NOT SET UP IS THE SET-UP CARD'S LINE, which leads this page until it is done. Drawing it
        # here too was the same sentence twice, one card apart (2026-09-29).
        return ""
    said, tone = _BOX_SAID.get(str(e.get("status")), _BOX_SAID["not_connected"])
    press = "Change" if e.get("status") == "connected" else "Set up"
    go = "/settings/ai" if "/settings/ai" in _serving() else ""
    return ('<div class="card"><h2 class="eyebrow">Your AI</h2>'
            f'<p class="state {"ok" if e.get("status") == "connected" else tone}"><span class="dot"></span>'
            f'{_esc(said)}</p>'
            + (f'<p><a href="{go}">{press} your AI account &rarr;</a></p>' if go else "") + '</div>')


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
    # IT STANDS DOWN ONLY WHILE NOTHING HAS STARTED. On a buyer's first sign-in this was the most
    # prominent control on the page — a button to stop a box that had not begun, above a line
    # saying there was nothing to report. `_setup_card` draws in its place.
    #
    # THE FIRST VERSION ASKED THE WRONG QUESTION and undid #1341 for a whole class of box: it
    # stood down whenever set-up was UNFINISHED, so a box with the mailbox connected and two
    # steps outstanding — receiving mail, worker sweeping, entirely operational — offered its
    # owner no way to stop it. Not hidden for taste either way: an offer to halt work that has
    # not started is a control that cannot do what it says, and removing the control from a box
    # that IS working is worse than the thing it was meant to fix.
    if _nothing_has_started():
        return ""
    paused = pause.is_paused()
    word = "Start it again" if paused else "Stop everything"
    sub = ("Nothing is running. Every machine on this box is stopped until you start it again."
           if paused else
           "Stops every machine on this box. Your data and your messages stay exactly as they are.")
    return (f'<div class="card"><h2>{"Your box is stopped" if paused else "Stop everything"}</h2>'
            f'<p class="sub">{_esc(sub)}</p>'
            f'<form method="post" action="/dash/{"resume" if paused else "stop"}">'
            # `danger` ONLY FOR THE HALT. "Start it again" is the ordinary control on a stopped
            # box and wearing the warning colour would make restarting look like the risk.
            f'<button class="{"" if paused else "danger"}" type="submit">'
            f'{_esc(word)}</button></form></div>')


def _setup_progress():
    """`(done, total, waiting_titles)` for this box, or None when it cannot say.

    ONE QUESTION, ASKED IN ONE PLACE. `_stop_card` used to decide by calling `_setup_card()` and
    asking whether it drew anything — which tied "should the stop button show" to "does the set-up
    card render", two questions whose answers differ (see `_stop_card`, and the box it broke).

    IT IS STILL TWO READS PER RENDER, not one, and saying otherwise would be the kind of comment
    this file keeps deleting. `_home` asks once through `_setup_card` and once through
    `_stop_card`; that was also true before the split, so nothing got cheaper — what changed is
    that the two callers now ask the questions they actually mean. Both are one indexed read of a
    three-row table and neither is worth threading an argument through two signatures the tests
    call directly; if this page ever gets expensive, that is the change to make.
    """
    try:
        from core import box_secrets
        steps = box_secrets.setup_state()
    except Exception:                            # noqa: BLE001 — a box too old for the seam is
        return None                              # not a broken dashboard
    if not steps:
        return None
    # OPTIONAL STEPS ARE NOT PART OF "FINISHED". "Your phone" is a device the box may never see —
    # email is the floor and many buyers will never install anything — so counting it would hold a
    # working box at "3 of 4" for good, nagging about a choice the buyer already made.
    steps = [r for r in steps if not r.get("optional")]
    if not steps:
        return None
    waiting = [w for w in (str(r.get("title") or "").strip() for r in steps
                           if str(r.get("status") or "") in ("", "not_connected")) if w]
    done = sum(1 for r in steps if str(r.get("status") or "") not in ("", "not_connected"))
    return done, len(steps), waiting


def _nothing_has_started() -> bool:
    """Is there NO connected step at all — nothing listening, nothing to stop?

    THE PREDICATE THE STOP BUTTON ACTUALLY NEEDS, and my first version got it wrong in a way that
    undid #1341. That version hid `Stop everything` while set-up was UNFINISHED, which is not the
    same question: a box with the mailbox connected and two steps outstanding is RECEIVING MAIL —
    the machines are running, the worker is sweeping — and its owner could not stop it, because
    he had not finished connecting things he may never connect at all. A person who cannot stop a
    thing he owns does not really own it, which is the whole argument #1341 was built on.

    So: nothing connected means nothing has started. One connected step means the box is working
    and the button comes back, whatever else is outstanding.

    A BOX THAT CANNOT ANSWER KEEPS ITS BUTTON. `None` here means the set-up seam is unreadable,
    and the safe answer to "may I take away the stop control" is always no.
    """
    got = _setup_progress()
    return bool(got) and got[0] == 0


def _setup_card() -> str:
    """The way in, on the screen a buyer actually lands on.

    THE BEST SCREEN IN THE PRODUCT WAS REACHABLE BY ACCIDENT. Measured on a box exported from
    release/2026.09.18.3 and signed into with nothing connected: this page carried ZERO links to
    the set-up screen, the box's settings page carried ZERO, and the rail did not list it. The
    only link anywhere was one on the inbox page. So the first thing a buyer read after signing
    in was "Nothing to report yet", with **Stop everything** as the loudest control on the screen
    — on a box that had not started. Same shape as the Managed door yesterday: the work was
    already done and the door had no handle.

    IT COUNTS, BECAUSE A NUMBER IS THE DIFFERENCE BETWEEN A CHORE AND A FINISH LINE. "Two of
    three connected" tells a person how much is left and that some of it is already behind them;
    "Set up your box" tells them nothing and reads the same on the third visit as the first.

    IT GOES AWAY WHEN IT IS DONE. A set-up card that outlives set-up is the banner every SaaS
    product trains its users to stop reading, and the one screen that must survive that training
    is this one.

    IT GOES TO THE BOX'S OWN SETTINGS, which every box serves, so it draws on every box with
    something left to do. The channel rows beneath it still take their destination from the
    registry (`_steps_left`), because core never writes a machine's URL.
    """
    # OWNER ONLY, and this was missing in the first version. Measured: a member saw "Finish
    # setting up your box", followed the link, and could not finish it — the set-up screen takes
    # their POST and stores nothing, because these are the box owner's credentials. So the card
    # was a task a member cannot do, pointing at a form that will not accept them, which is the
    # dead-control rule this codebase keeps deleting by name. Same gate as Stop and Managed.
    from core.dash import session_user as _who
    person = _who(request)
    if not person or person.get("role") != "owner":
        return ""
    got = _setup_progress()
    if not got:
        return ""
    done, total, _waiting = got
    if done >= total:
        return ""
    # THE CARD IS THE BOX'S ONE SET-UP CHECKLIST (owner, 2026-09-29, IA decision D2 as revised:
    # "go with the lighter version"). One row per thing still to do, each going straight to the
    # page that finishes it, in the order he named on 2026-09-23 — the AI account first, the mobile
    # app second, then the channels a machine connects ("It should immediately get them their LLM
    # connection and their progressive Web App"). The rows do what the headline link to System
    # Settings was for, so that link went: it opened a settings page, not a checklist.
    return ('<div class="card"><h2>Finish setting up your box</h2>'
            f'<p class="sub">{_esc(_setup_line(done, total))}</p>'
            f'{_steps_left()}</div>')


# THE ORDER THE CARD OFFERS THE STEPS IN — the box's own first, because nothing a machine does
# works without the AI account, and the mobile app is how the box reaches its owner at all.
_SETUP_FIRST = (("anthropic", "/settings/ai"), ("mobile", "/settings/mobile"))
# THE APP IS INSTALLED, NOT CONNECTED. The sentence above the rows counts connections, and the
# mobile app is not one of them (it is optional, so it never holds "finished" back). Its own verb
# keeps "3 things to connect" true over a card that shows four rows.
_SETUP_VERB = {"mobile": "Install"}


def _setup_line(done: int, total: int) -> str:
    """How far along, in one sentence. A number is the difference between a chore and a finish
    line: "two of three connected" says how much is behind them, "set up your box" says nothing.
    The step names are left to the rows beneath, so the sentence does not say them twice."""
    if done:
        return f"{done} of {total} connected."
    return f"{total} things to connect, and only you can do them."


def _steps_left() -> str:
    """One row per step still open, each going straight to the screen that finishes it.

    THE BOX'S STEPS GO TO THE BOX'S SCREENS; A MACHINE'S GO WHERE THE REGISTRY SAYS. Core never
    writes a machine's URL, so the channel rows follow `shell.setup_href()`, and a box whose
    machines serve no set-up screen simply draws no channel rows.

    THE MOBILE APP IS OFFERED EVEN THOUGH IT IS OPTIONAL. It is not counted towards "finished" —
    `_setup_progress` holds that line, and the Stop button depends on it — but the owner asked
    for it to be put in front of a new buyer straight away, and a row is how that is done without
    changing what finished means.
    """
    try:
        from core import box_secrets
        steps = {str(e.get("key")): e for e in box_secrets.setup_state()}
    except Exception:                            # noqa: BLE001 — the card must not 500 the page
        return ""
    open_ = [(k, e) for k, e in steps.items() if str(e.get("status") or "") in ("", "not_connected")]
    where = shell.setup_href()
    rows = []
    for key, href in _SETUP_FIRST:
        e = steps.get(key)
        if e is not None and (key, e) in open_:
            rows.append((str(e.get("title") or ""), href, _SETUP_VERB.get(key, "Set up"),
                         bool(e.get("optional"))))
    for key, e in open_:
        if e.get("surface") == "machine" and not e.get("optional") and where:
            rows.append((str(e.get("title") or ""), f"{where}#{key}", "Set up", False))
    # AN OPTIONAL ROW SAYS SO, because the sentence above counts only what is required. Walk #6
    # (OSDev4, #1483): the card read "3 things to connect" over FOUR rows. The mobile app is shown
    # on purpose and not counted on purpose (see above); the word under it is what makes the
    # number and the rows agree.
    return "".join(
        f'<a class="row step" href="{_esc(h)}"><b>{_esc(t)}'
        + ('<small>Optional</small>' if opt else '') + '</b>'
        f'<span class="go">{v}</span>{_FWD_CHEVRON}</a>' for t, h, v, opt in rows if t)


def _managed_card() -> str:
    """The way to the cancel door, on the screen a buyer actually lands on.

    THE DOOR WAS ALREADY OPEN AND NOTHING POINTED AT IT. #1229 shipped `/dash/managed` with
    Ownbox's Stripe portal as the default, so the page works on every sold box and a buyer can
    cancel from it. Measured on a box exported from main (2026-09-17): across every screen that
    box serves — this one, the people page, and both of its machine's — there were zero
    occurrences of `/dash/managed` or of the word Managed. The only way in was to know the URL.

    THAT IS THE HALF THE LAW IS ABOUT. The owner's ruling (2026-09-15, OSDev2's session, on the
    wall) is that Managed auto-renews at $99/mo after ninety days and needs "self-serve cancel in
    the box, one click to the Stripe portal, as easy as ticking the box (FTC click-to-cancel)". A
    page nobody can find is not one click, and the checkout copy promises cancelling before day 90
    costs nothing — a promise the box could not keep from any screen it actually shows.

    DRAWN ONLY WHEN MANAGED WAS BOUGHT. `provisioned_managed_until()` is written into
    provision.json by the provisioner from the cart's line items, so most boxes have none. A card
    about a subscription you never bought is a support ticket, and worse, it invites somebody to
    go looking for a charge that does not exist.

    OWNER ONLY, the same gate as Stop everything and for a narrower version of the same reason:
    the Stripe portal signs in the person who PAID, so a member following this link gets an email
    they will never receive. Hiding it is the courtesy; `/dash/managed` has its own gate.

    IT NAMES THE DATE HERE RATHER THAN MAKING THEM CLICK TO FIND OUT. What a person wants from a
    dashboard is whether anything is about to happen to their card; the page behind it is where
    the detail and the button live.
    """
    from core import claim as _claim
    from core.dash import session_user as _who
    person = _who(request)
    if not person or person.get("role") != "owner":
        return ""
    until = _claim.provisioned_managed_until()
    if not until:
        return ""
    try:
        from datetime import datetime as _dt
        day = _dt.fromisoformat(until).strftime("%-d %B %Y")
    except ValueError:
        day = until                          # an unparseable date is still better shown than hidden
    from core import box_updates
    title, said = box_updates.managed_words(day)
    if title == "Managed is on":                  # the short form the card always had, while nothing changed
        said = (f"Free until {day}. Unless you cancel, it renews after that. "
                "Cancelling does not touch the box — it keeps running and it stays yours.")
    return ('<div class="card"><h2>Managed</h2>'
            f'<p class="sub">{_esc(said)}</p>'
            '<p><a href="/dash/managed">Manage or cancel &rarr;</a></p></div>')


def _home() -> str:
    view = report.view()
    if not view.get("exists"):
        # NOT ZEROS. `view()` draws this line itself precisely so a page cannot invent a number
        # for a day nothing has been written for yet.
        # ONE SENTENCE, NOT THE HEADING SAID TWICE. `empty_line` opens "No report yet —", which under
        # a heading reading "Nothing to report yet" was the same news twice, and "the machine
        # starting" named the builder's noun. The morning review keeps its own wording; this is
        # the home screen's.
        line = ("Your box writes its first report within fifteen minutes of starting, then one "
                "every day. They are all kept here." if view.get("live") else
                view.get("empty_line") or "")
        body = (f'<div class="card"><h2>Nothing to report yet</h2>'
                f'<p class="sub">{_esc(line)}</p></div>')
    else:
        stale = view.get("stale")
        note = (f'<div class="card"><span class="stale">No report since {_esc(stale)} — '
                'the numbers below are the last ones written, not this minute\'s.</span></div>'
                if stale else "")
        body = note + _needs_card(view) + '<div class="dash-grid">' + _segments(view)
    # THE BOX'S OWN CARDS JOIN THE GRID whether or not a report exists yet: a box minutes old still
    # has a machine, a queue and a connection worth seeing.
    if '<div class="dash-grid">' not in body:
        body += '<div class="dash-grid">'
    body += _this_machine_card() + _queue_card() + _mcp_card() + _ai_card() + '</div>'
    # THE WAY IN GOES FIRST, ABOVE THE NUMBERS, and only while there is set-up left to do. A box
    # with nothing connected has no numbers worth reading — "Nothing to report yet" is the whole
    # of what the section above can say — so the first thing on the page should be the thing that
    # changes that. It removes itself when the last step is connected.
    # WAITING FOR YOU GOES FIRST, while something is (core/approvals.py): a coworker asked to change something in
    # an app, and nothing happens until the owner answers. Its own file draws it; '' when nothing waits.
    from core.dash import approvals as _approvals
    body = _approvals.card() + _setup_card() + body
    # BOTH LAST, AND IN THIS ORDER. Stop everything is the control you want findable and never
    # the one you want your thumb near while reading the morning's numbers on a phone; Managed is
    # a thing you go looking for on a particular day, so it sits below the numbers and above the
    # one button on this page that changes what the box is doing.
    # UPGRADE TO PRO SITS UNDER THE NUMBERS, above Managed: a thing the owner chooses on a
    # particular day, like Managed, and never above what the box did today. Its own file draws it.
    from core.dash import upgrade as _upgrade
    body += _upgrade.dashboard_card()
    body += _managed_card()
    body += _stop_card()
    return chrome("/dashboard", title="Base Machine",
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


# ── THE BOX'S OWN SETTINGS ───────────────────────────────────────────────────────────────────
# OWNER RULED 2026-09-22, choosing option B of two put to him: TWO settings destinations, not one
# merged registry. What belongs to the BOX lives here, in core's drawer; what belongs to a MACHINE
# stays in that machine's own Settings.
#
# HE HAD ALREADY SAID SO ONCE. Settings were meant to be in the system drawer and ended up inside
# a machine's menu instead, and the machine that holds them records why in that row's own comment:
# "Dropping the row before the replacement exists would orphan a shipped page from the menu."
# There was nowhere else to put them. This is the somewhere. (Core does not name that machine
# here, and `test_the_inbox_joins_the_rail` caught the first draft of this comment doing it — the
# menu names nobody, in code OR in prose, or the next machine needs core edited to get a row.)
#
# THE SPLIT IS NOT A JUDGEMENT CALL, IT IS A QUESTION WITH ONE ANSWER: would a box running some
# other machine still need this? The AI account and the phone are the owner's own words — *"The
# LLM and the phone."* (2026-09-22) — and both pass that test: `core.brain` is the one gateway
# EVERY machine reasons through, and a phone belongs to a person rather than to a product. The AI
# coworkers step is on the same side by the same test, and THAT one is my reading, not his word.
#
# WHY NOT THE MERGED REGISTRY (option A): core would have to understand each machine's settings
# shape to draw them, which is precisely what `tests/test_core_boundary.py` exists to refuse. B
# needs no registry, so it is also not blocked on one being designed.
# IN THE ORDER A NEW BUYER SETS A BOX UP (owner, 2026-10-02): "connect to their AI account, figure out how to do the
# inbound MCP server set up and then add outbound MCP data connections. And then set up the mobile app." The data
# sources row and the coworkers row are core's own (`_sources_row`, `_coworkers_row`), placed by `_box_rows`.
BOX_SETTINGS = ("anthropic", "agent", "mobile")
# THE AI ACCOUNT NAMES THE AIs IT CAN BE, on this screen only (owner, 2026-10-02, his words for the Overview).
_BOX_TITLES = {"anthropic": "Your AI account (Claude, ChatGPT, Gemini, Grok)"}


# THE SAME CLOSED SET `box_secrets.setup_state()` DOCUMENTS, plus core's own `unavailable`. Said
# in core's words rather than the inbox's: this screen is about the box, so "Not set up yet"
# rather than "Not connected yet", which reads oddly against an app somebody installs.
_BOX_SAID = {
    "connected":        ("Connected", ""),
    "needs_reauth":     ("Needs signing in again", "stale"),   # an AI account has no password (plan #1857 launch bar 1)
    "admin_disabled":   ("Switched off by your administrator", "stale"),
    "payment_required": ("Needs a payment method", "stale"),
    "not_connected":    ("Not set up yet", "quiet"),
    "unavailable":      ("Cannot be read right now", "stale"),
}


def _is_owner() -> bool:
    """Whether the person reading this page owns the box. False for a member and for nobody."""
    from flask import request as _rq
    from core.dash import session_user as _who
    person = _who(_rq)
    return bool(person and person.get("role") == "owner")


def _box_rows(*, owner: bool) -> str:
    """One row per box-level credential, live, each pointing at the screen that sets it.

    CORE NEVER LEARNS A MACHINE'S URL, and this screen does not become the exception. Every
    destination comes from data core already holds: a step carrying its own same-box `link` says
    where it goes, and anything else goes to whichever machine registered the `setup` item in the
    rail (`shell.setup_href()`). An OFF-BOX link is deliberately not followed here — the AI step's
    own link is a vendor sign-in page, and a Settings row that throws someone at a vendor instead
    of at the setting is the 404-shaped mistake #1410 was opened for.

    A BOX MISSING ONE OF THESE DRAWS NOTHING FOR IT rather than an empty row. `BOX_SETTINGS` names
    what core asks for; `setup_state()` decides what this box actually carries, and an older box
    mid-self-update legitimately carries fewer.
    """
    from core import box_secrets
    try:
        state = {str(e.get("key")): e for e in box_secrets.setup_state()}
    except Exception:                        # noqa: BLE001 — settings must never 500 on a reader
        return ('<p class="quiet">This box could not read its own settings just now. '
                'Nothing you have already set up is affected.</p>')
    where = shell.setup_href()
    rows = []
    for key in BOX_SETTINGS:
        e = state.get(key)
        if e is None:
            continue
        said, tone = _BOX_SAID.get(str(e.get("status")), _BOX_SAID["not_connected"])
        detail = str(e.get("detail") or "").strip()
        url = str((e.get("link") or {}).get("url") or "")
        # A DOOR WE CANNOT VOUCH FOR IS NOT OFFERED TO SOMEBODY WHO MAY BE REFUSED AT IT.
        #
        # THE WALK SUITE CAUGHT THIS AND IT WAS A REAL DEAD END: `_AGENT_STEP` declares an
        # OWNER-ONLY machine door, and this screen admits members on purpose — a phone belongs
        # to a person, not to the box's owner. So a member saw "Set up", pressed it, and met a
        # 403. The owner's standing rule is no dead screens, and a door that refuses you is
        # worse than no door at all: it reads as the box being broken. (The literal path is not
        # named here on purpose: core/dash/home.py must contain no machine URL, and the guard in
        # test_the_box_shows_a_buyer_the_way_in.py greps this file's source to enforce it.)
        #
        # THE SPLIT IS BY WHO KNOWS THE GATE, not by which step it is. Core SYNTHESISES the
        # set-up link, so it knows that screen admits everyone and offers it to everyone. A
        # step's OWN declared link is a machine's door whose gate core cannot read, so it is
        # offered only to the owner, who can open any of them. The row still renders either way
        # — a member sees what is set, which is true and useful, and simply has nothing to press.
        if not url:
            url = str(e.get("action_href") or "")
        mine = url.startswith("/")
        # CORE CAN NOW VOUCH FOR ITS OWN DOORS, and that is what changed on 2026-09-22. Every
        # box-level step is finished on a core screen, so the step declares its gate as data
        # (`owner_only`) and this row honours it — instead of the old blanket rule, which offered
        # a machine's door to the owner alone because core could not read a machine's gate.
        #
        # THE DEAD END THAT RULE EXISTED FOR IS GONE RATHER THAN HIDDEN. A member used to be shown
        # "Set up" against the AI coworkers step and met a 403 at it; now the step says who may
        # open it, this row does not draw a link a member cannot use, and the phone — which is
        # theirs as much as anybody's — is offered to them properly for the first time.
        allowed = owner or not e.get("owner_only")
        href = (url if (mine and allowed) else
                ("" if mine else (f"{where}#{_esc(key)}" if where else "")))
        # THE VERB IS THE STATE. "Change" on something set and "Set up" on something not is the
        # whole difference a person needs, and it saves the row a second sentence explaining it.
        press = ("Change" if e.get("status") == "connected" else "Set up")
        rows.append((e.get("status") == "connected", _BOX_TITLES.get(key) or str(e.get("title") or ""),
                     tone, said + (f' — {detail}' if detail else ''), href, press, ""))
        if key == "agent":
            rows += _sources_row(owner)
    if not rows:
        return '<p class="quiet">This box carries no box-level settings yet.</p>'
    rows += _coworkers_row(owner)
    # NUMBERED IN THE ORDER TO DO THEM, A TICK FOR WHAT IS DONE (owner, 2026-10-02, approved from a preview).
    return "".join(
        '<div class="row setting">'
        f'<span class="step{" done" if done else ""}" aria-hidden="true">{"&#10003;" if done else n}</span>'
        f'<span class="what"><b>{_esc(title)}{badge}</b><span class="{tone}">{_esc(said)}</span></span>'
        + (f'<a class="act" href="{_esc(href)}">{press}</a>' if href else '') + '</div>'
        for n, (done, title, tone, said, href, press, badge) in enumerate(rows, 1))


def _sources_row(owner: bool) -> list:
    """YOUR DATA SOURCES, between the MCP server and the mobile app (owner, 2026-10-02: "Data sources should probably
    be one too"). What the box reads from: the built-in sources a department registered (core/source_cards.py, by
    their own summaries, so core names none) and the apps connected through their own MCP servers. The owner's
    page, so a member is shown the row with nothing to press."""
    from core import source_cards
    names = []
    try:
        names += [spec["title"] for key, spec in source_cards.cards() if source_cards.summary(key)["connected"]]
        from core.connections import store as _apps
        names += [str(r.get("name") or "") for r in (_apps.load().get("items") or {}).values() if isinstance(r, dict)]
    except Exception:                        # noqa: BLE001 — a row is never worth a broken settings page
        return []
    names = [n for n in names if n]
    said = (f"{len(names):,} connected: " + ", ".join(names)) if names else "Not set up yet"
    return [(bool(names), "Your data sources", "" if names else "quiet", said,
             "/settings/sources" if owner else "", "Add" if names else "Set up", "")]


def _coworkers_row(owner: bool) -> list:
    """YOUR COWORKERS, last: the ones that work on the box, on their schedules. A plan feature, asked of by feature
    (core/tiers.py). Without it the row stays, with its badge, and offers the one upgrade (owner, 2026-10-02: "leave
    it and it could be an upsell opportunity", "a little pro badge on the feature and there is an upgrade path")."""
    try:
        from core import tiers
        badge = tiers.badge("coworkers")
    except Exception:                        # noqa: BLE001
        return []
    if badge:
        from core.dash import UPGRADE_URL
        return [(False, "Your coworkers", "quiet", "Coworkers that work on a schedule, on your box, while you are "
                 "away.", UPGRADE_URL if owner else "", "Upgrade", f'<span class="badge">{_esc(badge)}</span>')]
    try:
        from core.coworkers import runner
        # SWITCHED ON ONLY (OSDev1's review of #1828): a coworker that is off is not "working on its schedule".
        n = sum(1 for cw in runner.discover()[0] if getattr(cw, "enabled", True))
    except Exception:                        # noqa: BLE001
        n = 0
    return [(bool(n), "Your coworkers", "" if n else "quiet",
             f"{n:,} working on {'its' if n == 1 else 'their'} schedule{'s' if n != 1 else ''}" if n else "None yet",
             "/settings/shifts", "Change" if n else "Set up", "")]


# TWO KINDS OF COWORKER, ONE WORD. Owner, 2026-09-30: "it's all coworkers. People can use their outside
# coworkers, and they can also create coworkers on the box ... we should try to explain that." Said
# once, here, and drawn on both screens — MCP Server and Coworkers — each naming its own kind
# first and pointing at the other, so the two screens cannot describe the split two ways.
def coworkers_two_kinds(here: str) -> str:
    """`here` is "agents" or "shifts": the screen drawing it, whose kind comes first and links nowhere.

    THE LINK TO THE MCP SERVER IS THE OWNER'S ALONE, like the page: Coworkers is open to a member,
    and a link that refuses the person who taps it is the dead end the buyer walk forbids."""
    outside = ("<p><b>From outside.</b> AI agents you already pay for, like Claude, ChatGPT or Grok, "
               "let into this box. They work when you ask them, wherever you already use them."
               + (' <a href="/settings/agent">MCP Server &rarr;</a>'
                  if here != "agents" and _is_owner() else "") + "</p>")
    onbox = ("<p><b>On this box.</b> Coworkers you create here, each with a job in plain words and its "
             "own times. They work on schedule, on their own, and tell you what they did."
             + (' <a href="/settings/shifts">Coworkers &rarr;</a>' if here != "shifts" else "") + "</p>")
    return ('<div class="card"><h2>Two kinds of coworker</h2>'
            + (outside + onbox if here == "agents" else onbox + outside) + '</div>')


@blueprint.route("/settings")
def settings():
    """THE SYSTEM DRAWER'S SETTINGS — the box's, never a machine's.

    THE SAME GATE AS THE DASHBOARD, and for the same reasoning rather than a copy of it: this page
    publishes no money and no meters, so `owner_only` would lock a signed-in member out of their
    own mobile app setting for nothing. The one genuinely owner-only thing on it — replacing the box's
    AI key — is gated where it is actually done, not by hiding the row that says it is set.
    """
    from core.dash import review as _review
    refuse = _review._admit(owner_only=False)
    if refuse is not None:
        return refuse
    # "YOUR OWNBOX" (owner, 2026-10-02, his words for this card, approved from a preview).
    body = ('<div class="card"><h2>Your Ownbox</h2>'
            '<p class="sub">What every machine on this box shares. Each machine keeps its own '
            'settings in its own menu.</p>'
            + _box_rows(owner=_is_owner()) + '</div>')
    # THE BOX'S ICON, SECOND, beside the box it stands for (owner, 2026-09-30): it is the menu
    # button and the top of the menu on every screen, not a mobile-app setting, so it lives here and
    # Mobile App links to it. The owner's alone; for anyone else the card is not drawn.
    from core.dash import box_settings as _bs
    body += _bs.icon_card()
    # APPEARANCE IS EACH PERSON'S, SO IT IS FOR EVERYBODY and sits in the same place whoever looks.
    # Owner, 2026-09-27: dark mode and "system default", modelled on ownbox.io's dark half.
    from core.dash import theme as _theme
    body += ('<div class="card"><h2>Appearance</h2>'
             '<p class="sub">Light is the box\'s own look. Automatic follows your device, and '
             'changes when it does.</p>' + _theme.control("/settings") + '</div>')
    # THE MACHINE ITSELF, and it is the owner's alone. A key added there is root on the server —
    # strictly more than this dashboard grants anybody — so the row is drawn only for the owner,
    # who is the only person the screen behind it admits. Offering it to a member would be the
    # dead end the walk suite already caught once: a door that refuses you reads as a broken box.
    #
    # IT IS NOT ONE OF THE CREDENTIAL ROWS ABOVE and must not become one. Those come from
    # `setup_state()` — things the box is waiting to be GIVEN before a machine can work. This is
    # not a set-up step with a done state; it is a standing door to the machine the buyer owns,
    # available on day one and on day four hundred.
    # WHAT THIS BOX IS RUNNING, and it is for everybody. Somebody who works in this box daily
    # should be able to see whether it is current; the page behind this publishes a version string
    # and nothing else — no money, no keys, no customer data — so there is nothing to gate on
    # ownership. It sits ABOVE the owner-only card so the order of the page does not change
    # depending on who is looking at it.
    body += ('<div class="card"><h2>Updates</h2>'
             '<p class="sub">This box improves on its own. See what it is running now, when it '
             'last looked for something newer, and how that works.</p>'
             '<div class="foot"><a href="/settings/updates">What this box is running &rarr;</a>'
             '</div></div>')
    if _is_owner():
        # EMAIL IS OPTIONAL, SO IT IS A DOOR AND NOT A SET-UP STEP (owner, 2026-09-23): the review
        # and alerts reach the owner in the mobile app without it. Owner-only, like the page.
        from core import box_mail
        mail = box_mail.describe()
        said = (f"Set up — sending from {_esc(mail['from'])}." if mail["kind"] else
                "Not set up. Your Morning Review and alerts come to the mobile app; add email to "
                "get them in your inbox too.")
        body += ('<div class="card"><h2>Email</h2>'
                 f'<p class="sub">{said}</p>'
                 '<div class="foot"><a href="/settings/email">Email settings &rarr;</a>'
                 '</div></div>')
        body += ('<div class="card"><h2>The machine itself</h2>'
                 '<p class="sub">This box is a server you own outright. Put your own key on it '
                 'and you can sign in to the server itself — no account with us, and it keeps '
                 'working if you move the box somewhere else.</p>'
                 '<div class="foot"><a href="/settings/access">Server access &rarr;</a></div></div>')
        # MOVING THE BOX IS NOT ON THIS SCREEN (owner, 2026-09-30: "delete this", then "We don't want
        # this to be stumbled upon too much"). It is in the danger zone at the foot of Server Access.
    # SYSTEM SETTINGS, THE NAME ON THE MENU ROW THAT OPENED IT. The page said "Settings" under a
    # breadcrumb and a menu that both say System Settings; three names for one place. The lede said
    # what it holds in the builder's words ("belong to the box, not to one machine"); it now names
    # the things a person came here for.
    return chrome("/settings", title="System Settings",
                  lede="Your AI account, MCP server, your icon, the mobile app, email and the server itself.",
                  body=body), 200


# WHAT A BUYER MAY ADD, SAID ON THEIR OWN BOX. Owner, 2026-09-23: *"Please scope out an add machine
# page ... explain that they can build their own machine with their SSH access and customize it to
# their business use cases and workflows. And then on that page you would provide a link to our
# website where we are going to have machines available for purchase."* Scope and decisions:
# docs/SCOPE_ADD_MACHINE_PAGE.md §2. The ask goes to the site's contact form: help@ownbox.io is not
# read (owner chose "form only" for /contact, 2026-09-25).
_MACHINE_SHOP_ASK = "https://www.ownbox.io/contact"


def _custom_machine_rows() -> str:
    """One row per folder in my/machines/, and whether it started. Read-only: the loader owns this."""
    try:
        from core import custom_machines, machine_breaks, machine_jobs
        seen = custom_machines.status()
        by_update = machine_breaks.stopped()
        jobs_failing = machine_jobs.failing()
    except Exception:                            # noqa: BLE001 — the page must not 500 on a reader
        seen, by_update, jobs_failing = [], {}, {}
    if not seen:
        return '<p class="quiet">None yet.</p>'
    rows = []
    for m in seen:
        # R4: A MACHINE AN UPDATE STOPPED SAYS WHICH UPDATE, so the owner (or whoever they ask to
        # fix it) knows it was working before and what changed underneath it.
        hit = by_update.get(m.get("slug")) if not m.get("ok") else None
        said = ("Running" if m.get("ok") else
                ("Stopped by the update to " + str(hit.get("release") or "a new release")
                 .replace("release/", "") + " — " if hit else "Not started — ")
                + str(m.get("reason") or "see the box's log"))
        # WHOSE FIX IT IS (SDK v0, §9.2): a machine built only on what the box promises was broken
        # by us; one that reached past the promise is told what it used and how to stop.
        if hit and hit.get("promised") is True:
            said += ". It uses only what the box promises, so this one is ours to fix: tell us."
        elif hit and hit.get("unpromised"):
            said += ". It used something the box never promised: " + str(hit["unpromised"])
        # A SCHEDULED JOB THAT FAILS IS REPORTED HERE (m.every, owner 2026-10-01): the machine is running,
        # and one of its jobs isn't doing its work. Said once per job, with when it started failing.
        from core.sdk import _key
        broken = [j for j in jobs_failing.values() if j.get("machine") == _key(str(m.get("slug") or "x"))]
        if m.get("ok") and broken:
            said = "Running, but " + "; ".join(
                f'its job {j.get("job")} is failing since {str(j.get("since") or "")[:16].replace("T", " ")} UTC: '
                f'{j.get("error")}' for j in broken)
        tone = "" if m.get("ok") and not broken else "stale"
        rows.append('<div class="row"><b style="flex:1;min-width:0">' + _esc(m.get("slug"))
                    + f'</b><span class="{tone}">{_esc(said)}</span></div>')
    return "".join(rows)


@blueprint.route("/add-machine")
def add_machine():
    """THE ADD A MACHINE PAGE — on the box, never a link that leaves it.

    IT NEVER 404s. The rail row points here on every box, including one with no machine of the
    owner's own: that box shows "None yet", not an error.

    THE SAME GATE AS SETTINGS. Anyone signed in may read what a machine is; only the owner is shown
    how to build one, because building one means signing in to the server, which only the owner can.
    """
    from core.dash import review as _review
    refuse = _review._admit(owner_only=False)
    if refuse is not None:
        return refuse
    # MACHINES FROM US. The shop page on ownbox.io does not exist yet, so this offers the one door
    # that works today — an email to us — rather than a link to a page that 404s.
    body = ('<div class="card"><h2>Get a machine from us</h2>'
            '<p class="sub">A machine is a new job your box does: its own screens, its own work in '
            'the background, the same AI account as everything else. Ready-made machines are added '
            'to your box by us.</p>'
            f'<div class="foot"><a href="{_MACHINE_SHOP_ASK}" target="_blank" rel="noopener">Ask us to add one &rarr;</a></div>'
            '</div>')
    if _is_owner():
        body += ('<div class="card"><h2>Build your own</h2>'
                 '<p class="sub">This box is a server you own. Sign in to it and you can add a '
                 'machine shaped to your own business and the way you work.</p>'
                 '<ol class="steps">'
                 '<li>Set up <b>Server access</b> so you can sign in to the server.</li>'
                 '<li>Make a folder in <code>my/machines/</code> named after your machine, with a '
                 '<code>machine.yaml</code> and an <code>__init__.py</code>. The guide on your box, '
                 '<code>BUILD_A_MACHINE.md</code>, has a working example to copy.</li>'
                 '<li>Restart the box and your machine appears in this menu.</li></ol>'
                 '<div class="foot"><a href="/settings/access">Server access &rarr;</a></div>'
                 '</div>'
                 '<div class="card"><h2>Your machines</h2>'
                 '<p class="sub">Every folder in <code>my/machines/</code>, and whether it started.'
                 '</p>' + _custom_machine_rows() + '</div>'
                 '<div class="card"><h2>The one rule</h2>'
                 '<p class="sub">Build inside <code>my/machines/</code> and no update ever touches '
                 'your machine. Change any of the box\'s own files and updates pause until they are '
                 'put back. Updates come with Managed support.</p></div>')
    else:
        body += ('<div class="card"><h2>Build your own</h2>'
                 '<p class="sub">The box\'s owner can build machines of their own for this box.</p>'
                 '</div>')
    return chrome("/add-machine", title="Add a Machine",
                  lede="Give your box a new job — from us, or one you build yourself.",
                  body=body), 200


# THE BOX'S HOME IS A CORE SECTION. Everything else in the rail past the base group is a machine's
# to declare, which is what keeps this file from becoming the list of every product we sell.
# BASE MACHINE, NOT DASHBOARD. Owner, 2026-09-24: *"Dashboard should be re-named to: Base Machine.
# This way people will know which machine they are working with."* Every other row in the menu is
# a machine by name; this one said what kind of screen it was. The address stays /dashboard,
# because installed apps and bookmarks open it.
shell.register_section("dashboard", order=0, machine="core", title="Base Machine",
                       href="/dashboard", home=True, icon=_HOME_ICON)

# THE MORNING REVIEW HEADS THE ADD-ON MACHINES. Owner, 2026-09-29: "Add the Morning Review to the
# menu" (it had no row; the 8 AM message and the email were its only doors), and then: "move
# morning review down to the top of the list of the add-on machines. That way it's sort of grouped
# with what it is related to." It is the day's report of every machine below it, so it leads them:
# the group's gap opens above it, and a negative order keeps it ahead of the machines, which sort
# by name at 0. OWNER-ONLY because its page publishes what the box spends and refuses anyone else
# (`review._admit`); a member is shown no row rather than a door that sends them to sign in again.
shell.register_section("review", order=-1, machine="core", title="Morning Review",
                       href="/app/review", icon=_SUN_ICON, owner_only=True, group="addons")

# ADD A MACHINE CLOSES THE ADD-ON GROUP. Owner, 2026-09-24: the add-on machines are *"unified inbox
# and then add a machine"* — the machines a box has, then the way to add one more. `order=1000`
# keeps it below any machine a box gains later, including one its owner builds.
# ADD A MACHINE STAYS ON THE BOX. It left for ownbox.io/machines from 2026-09-22 — the owner's
# destination then, before that page existed — and a buyer read it as leaving their own machine.
# Owner, 2026-09-23, asked for a page on the box that explains building your own and links to the
# shop from there; that page is `add_machine()` above, and the shop link rides on it once it exists.
shell.register_section("add_machine", order=1000, machine="core", title="Add a Machine",
                       href="/add-machine", icon=_ADD_ICON, group="addons")

# SYSTEM SETTINGS SITS RIGHT BELOW BASE MACHINE. Owner, 2026-09-24: *"you can put System Settings
# right below it."* Both are the box's own, so they are the base group; the machines follow.
# SYSTEM SETTINGS, NOT SETTINGS. Owner, 2026-09-22. The box now has two settings screens by his
# own ruling — this one for what the box shares, and each machine's own for what only it has —
# and two rows both reading "Settings" is the menu telling somebody they are in the same place
# twice. The qualifier is what makes the pair legible.
# SYSTEM SETTINGS IS A SUB-MENU. Owner, 2026-09-23: *"there are now sub menu items under
# settings"* — so tapping it swaps the rail for these rows, the two-level menu he asked for on
# 2026-09-17 (see `core/shell.py`). The AI account and the mobile app lead because they are the
# two things he wants a new buyer to reach first. The five rows whose screens refuse a member are
# `owner_only`, so a member is shown a shorter menu rather than doors that refuse them.
shell.register_section("settings", order=10, machine="core", title="System Settings",
                       href="/settings", icon=_GEAR_ICON,
                       # FOUR GROUPS, EACH NAMED ABOVE ITS ROWS (owner, 2026-10-02: "rename and re-organize some
                       # things to clean up the user experience", approved from a preview).
                       #
                       # THE AI GROUP IN HIS ORDER (owner, 2026-10-02: "The order of the menu should go AI account,
                       # MCP server, data sources, and then coworkers", approved from a preview): the account that
                       # writes, the box's MCP server your own AI connects to, the apps the box reads from, and the
                       # coworkers that work on the box. It is the order a box is set up in: "connect to their AI
                       # account, figure out how to do the inbound MCP server set up and then add outbound MCP data
                       # connections. And then set up the mobile app."
                       group_labels={"ai": "AI", "general": "General", "server": "Server"},
                       items=[
                           {"key": "overview", "label": "Overview", "href": "/settings"},
                           {"key": "ai", "label": "AI Account", "href": "/settings/ai",
                            "owner_only": True, "group": "ai"},
                           # MCP SERVER, ON EVERY BOX (owner, 2026-10-02: "standard base machines should be able to
                           # have a menu choice called MCP server"). It read "Coworkers (Agents)"; the page and its
                           # address are unchanged.
                           {"key": "agent", "label": "MCP Server", "href": "/settings/agent",
                            "owner_only": True, "group": "ai"},
                           # DATA SOURCES: the apps the box reads from, each through its own MCP server
                           # (docs/SCOPE_CONNECTIONS_MCP_FIRST.md), and ideas for what to connect.
                           {"key": "sources", "label": "Data Sources", "href": "/settings/sources",
                            "owner_only": True, "group": "ai"},
                           # COWORKERS, WHERE IT READ "SHIFTS": coworkers on the box, on their schedules. Pro only, so
                           # on a box without it the row stays, with "a little pro badge", and opens onto the upgrade
                           # (core/dash/shifts.py).
                           {"key": "shifts", "label": "Coworkers", "href": "/settings/shifts",
                            "group": "ai", "feature": "coworkers"},
                           # GENERAL (owner, 2026-10-03: "what is reaching you in the left side bar? That doesn't mean
                           # anything relevant ... Too many categories. AI is settled and server is settled, but the
                           # other three need to be in another category"; he chose General from options). The app on
                           # your mobile; the address the box sends from (Morning Review, alerts), "Sending Email" so
                           # it is not taken for an inbox; and the people on the box.
                           {"key": "mobile", "label": "Mobile App", "href": "/settings/mobile",
                            "group": "general"},
                           {"key": "email", "label": "Sending Email", "href": "/settings/email",
                            "owner_only": True, "group": "general"},
                           # PEOPLE HAD NO DOOR once: the page existed and nothing on a box linked to it, so
                           # inviting a colleague meant knowing the address.
                           {"key": "people", "label": "People", "href": "/settings/people",
                            "owner_only": True, "group": "general"},
                           # THE BOX'S ONE TIME ZONE (plan #1857 H9): the review's 8 AM and every "As of" follow it.
                           {"key": "timezone", "label": "Time Zone", "href": "/settings/timezone",
                            "owner_only": True, "group": "general"},
                           # SERVER: what it runs, and who can sign in to it. MOVE YOUR BOX IS NOT A ROW (owner,
                           # 2026-09-30): it is in the danger zone at the foot of Server Access.
                           {"key": "updates", "label": "Updates", "href": "/settings/updates",
                            "group": "server"},
                           {"key": "access", "label": "Server Access",
                            "href": "/settings/access", "owner_only": True, "group": "server"},
                       ])
