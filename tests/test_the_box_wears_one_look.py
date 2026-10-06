"""The box wears one look: one stylesheet, the ownbox.io tokens, and nobody else's colours.

Owner, 2026-09-24: *"We need a lightweight design system that will be appealing to every single
client who gets one of these boxes"*, with no Tailwind and no build step. The owner's order the
same night, relayed by OSDev1 on the wall: the box wears the ownbox.io design language, served *"as
ONE plain stylesheet, no build step"*. The walk that started it found six stylesheets sharing no
value: 91 colours, 26 font sizes (docs/SCOPE_DESIGN_LANGUAGE.md §1).

This suite holds what makes `core/dash/static/box.css` the one look rather than a seventh:
  1. every colour in it is a token, declared once, in the tokens layer;
  2. the token values are the site's, as OSDev0 measured them (docs/BOX_DESIGN_REFERENCE.md);
  3. every text colour it pairs with a ground passes WCAG AA (4.5:1);
  4. it ships the fonts it names, from the box, with their licences, and reaches no other host;
  5. it stays light: under 20 KB, and plain CSS a browser reads as-is;
  6. THE RATCHET: raw colours in the older stylesheets may only go down. Each screen that moves
     onto the tokens lowers its count here; at zero the rule is strict.

Run: python tests/test_the_box_wears_one_look.py
"""
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "look.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
os.environ.pop("ANTHROPIC_API_KEY", None)

from core import state                                                # noqa: E402

state.init_db()

from core import dash                                                 # noqa: E402
from core.dash import look                                            # noqa: E402
from core.dispatch import app                                         # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


CSS = open(os.path.join(ROOT, "core", "dash", "static", "box.css"), encoding="utf-8").read()
_NO_COMMENTS = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
# THE TOKENS ARE TWO BLOCKS, ONE PER THEME: `:root` is light, `html[data-theme="dark"]` is dark and
# declares only what differs. There is no @layer to find them by any more (see
# test_it_fails_gracefully_on_an_old_browser), so each is found by its selector.
_LIGHT = re.search(r"^:root\s*\{(.*?)^\}", _NO_COMMENTS, re.S | re.M)
_DARK = re.search(r'^html\[data-theme="dark"\]\s*\{(.*?)^\}', _NO_COMMENTS, re.S | re.M)
LIGHT = _LIGHT.group(1) if _LIGHT else ""
DARK = _DARK.group(1) if _DARK else ""
TOKENS = LIGHT + DARK
REST = _NO_COMMENTS
for _m in (_LIGHT, _DARK):
    if _m:
        REST = REST.replace(_m.group(0), "")
_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|oklch|oklab|lab|lch)\(")


def token(name, block=None):
    m = re.search(rf"--{name}:\s*([^;]+);", LIGHT if block is None else block)
    return m.group(1).strip().lower() if m else None


def dark(name):
    """A token's dark value: its own if the dark block declares it, else light's, as the cascade
    resolves it on the page."""
    return token(name, DARK) or token(name)


print("\ntest_every_colour_is_a_token")
ok("the file declares its tokens, light and dark", bool(LIGHT) and bool(DARK))
stray = _COLOUR.findall(REST)
ok("no colour appears outside the two token blocks", not stray, str(stray[:5]))
ok("...and no font size is a bare pixel value outside it, except the mono copy box and the chip",
   # ONE PIXEL UP ACROSS THE APP (owner, 2026-09-29: "move all the text in the app up one point").
   sorted(set(re.findall(r"font-size:\s*(\d+px)", REST))) in ([], ["12px", "16px"], ["12px", "16px", "17px"]),
   str(sorted(set(re.findall(r"font-size:\s*(\d+px)", REST)))))

print("\ntest_the_tokens_are_the_sites")
# THE GREYS ARE DARKER THAN THE SITE'S (owner, 2026-09-29: "The gray text is a little bit
# unreadable"): --ink-3 is 6.7:1 on the cream where the site's #6b6b6b was 4.85, and --ink-2 12:1.
for name, want in (("ground", "#f6f4ef"), ("ink", "#111111"), ("ink-2", "#2e2e2e"),
                   ("ink-3", "#565656"), ("line", "rgba(17, 17, 17, 0.46)"),  # NOT the site's .32: a control edge needs 3:1 (below)
                   ("hairline", "rgba(17, 17, 17, 0.08)"), ("card", "#ffffff"),
                   ("r-sm", "16px"), ("r-md", "22px"), ("r-pill", "999px"),
                   # THE TITLE FOLLOWS THE READER'S TEXT SIZE TOO (2026-09-29): the same 24px to
                   # 32px at the default setting, measured in the box's --px.
                   ("t-title", "clamp(calc(24 * var(--px, 1px)), 2.4vw, calc(32 * var(--px, 1px)))"),
                   ("control", "50px")):
    ok(f"--{name} is {want}", token(name) == want, str(token(name)))
ok("the text face is Inter, with the system stack behind it",
   (token("sans") or "").startswith('"inter"') and "-apple-system" in (token("sans") or ""))
ok("nothing on a box is larger than the 32px page title",
   all(int(n) <= 32 for n in re.findall(r"(\d+)px", token("t-title") or "")))
ok("no blur, the site's marketing material, is carried onto the box", "backdrop-filter" not in CSS)
ok("#767676, which fails AA on the cream, is not used", "#767676" not in CSS.lower())


def _lum(hexv):
    h = hexv.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    rgb = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in rgb]
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def ratio(a, b):
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


print("\ntest_every_text_colour_reads")
for fg in ("ink", "ink-2", "ink-3", "link", "blue", "ok", "warn", "bad"):
    for bg in ("ground", "card"):
        r = ratio(token(fg), token(bg))
        ok(f"--{fg} on --{bg}: {r:.2f}:1 (AA needs 4.5)", r >= 4.5)
r = ratio(token("on-ink"), token("ink"))
ok(f"button text on the ink pill: {r:.2f}:1", r >= 4.5)

print("\ntest_every_text_colour_reads_in_dark")
# Owner, 2026-09-27: dark mode, modelled on the homepage's dark half. Same rule, same pairs.
for fg in ("ink", "ink-2", "ink-3", "link", "blue", "ok", "warn", "bad"):
    for bg in ("ground", "card"):
        r = ratio(dark(fg), dark(bg))
        ok(f"dark --{fg} on --{bg}: {r:.2f}:1 (AA needs 4.5)", r >= 4.5)
r = ratio(dark("on-ink"), dark("ink"))
ok(f"dark: button text on the cream pill: {r:.2f}:1", r >= 4.5)


def _over(value, ground):
    """An rgba() token painted over a solid ground, as the eye sees the edge."""
    n = [float(x) for x in re.findall(r"[\d.]+", value)]
    g = [int(ground.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(n[3] * n[i] + (1 - n[3]) * g[i]):02x}" for i in range(3))


print("\ntest_a_controls_edge_can_be_seen")
# WCAG 1.4.11: the boundary of a control needs 3:1 against what it sits on. --line is the edge of
# every field and every ghost button. Measured 2026-09-27 before this: 2.10 light, 1.50 dark, so a
# field had no edge you could find in daylight.
for label, get in (("light", token), ("dark", dark)):
    for bg in ("ground", "card"):
        r = ratio(_over(get("line"), get(bg)), get(bg))
        ok(f"{label} --line on --{bg}: {r:.2f}:1 (needs 3.0)", r >= 3.0)

print("\ntest_the_bar_behind_the_clock_matches_the_page")
# An installed app paints its status bar from theme-color, which core/dash/theme.py writes from
# its own GROUND table. If that table and --ground disagree, the bar is a band of another colour.
from core.dash import theme as _theme                                # noqa: E402
ok("theme.GROUND is --ground in each theme",
   _theme.GROUND == {"light": token("ground"), "dark": dark("ground")},
   f"{_theme.GROUND} vs light {token('ground')} dark {dark('ground')}")

print("\ntest_it_fails_gracefully_on_an_old_browser")
# A box goes home with its client and is opened on whatever they own. A browser that does not know
# @layer drops the WHOLE block it wraps, every token included, and one that does not know
# color-mix() drops the declaration: an unstyled screen, and nothing errors. Owner, 2026-09-27:
# "something ultra durable". Modern CSS stays where failing costs one nicety.
ok("no @layer: an old browser would drop every token inside it", "@layer" not in _NO_COMMENTS)
ok("no color-mix(): an old browser would drop the colour", "color-mix(" not in _NO_COMMENTS)
# AND NO BASE RULE MAY OUTRANK A SCREEN. @layer used to make this section lose to anything a screen
# wrote; without it, specificity decides, and `input:not(..):not(..):not(..)` (0,3,1) beat the
# inbox's `.find input` (0,1,1) and folded its search field (2026-09-29). Every base selector is
# therefore `:where(...)`, which weighs nothing. The only exceptions cannot be wrapped or need not
# be: the universal box-sizing reset, a pseudo-element, and the reduced-motion !important.
_raw_base = CSS[CSS.index("/* ── base"):CSS.index("/* ── components")]
_base = re.sub(r"/\*.*?\*/", "", _raw_base, flags=re.S)
_base = re.sub(r"@media[^{]*\(prefers-reduced-motion[^{]*\{[^{}]*\{[^{}]*\}\s*\}", "", _base)
_base = re.sub(r"@media[^{]*\{", "{", _base)          # a media query is a wrapper, not a selector
_sels = [x.strip() for x in re.findall(r"([^{}]+)\{", _base) if x.strip()]
_heavy = [x for x in _sels if not x.startswith(":where(")
          and x not in ("*, *::before, *::after", "::placeholder")]
ok(f"every base rule weighs nothing, so a screen always wins ({len(_sels)} rules)", not _heavy, str(_heavy))
ok("color-scheme follows the theme, so fields and scrollbars do too",
   "color-scheme: light" in LIGHT and "color-scheme: dark" in DARK)

print("\ntest_the_box_serves_its_own_fonts_and_nothing_else")
c = app.test_client()
for key, fn, lic in (("sans", "inter-latin-var.woff2", "OFL-Inter.txt"),
                     ("mono", "geist-mono-latin-var.woff2", "OFL-GeistMono.txt")):
    r = c.get(f"/ui/font/{key}.woff2")
    ok(f"/ui/font/{key}.woff2 answers with the font", r.status_code == 200
       and r.mimetype == "font/woff2" and r.data[:4] == b"wOF2", f"{r.status_code} {r.mimetype}")
    here = os.path.join(ROOT, "core", "dash", "static", "fonts")
    ok(f"...and its licence ships beside it ({lic})",
       os.path.isfile(os.path.join(here, lic))
       and "SIL Open Font License" in open(os.path.join(here, lic), encoding="utf-8").read())
ok("a font name that is not a key is a 404, never a path",
   c.get("/ui/font/..%2F..%2Fdash%2Flook.woff2").status_code == 404
   and c.get("/ui/font/other.woff2").status_code == 404)
ok("the stylesheet reaches no other host", not re.search(r"url\(\s*['\"]?https?:", CSS)
   and "@import" not in CSS)

print("\ntest_the_stylesheet_is_served_and_cached_by_its_content")
r = c.get("/ui/box.css")
ok("/ui/box.css answers to anyone, since the sign-in screen needs it before a session exists",
   r.status_code == 200 and r.mimetype == "text/css", str(r.status_code))
ok("...with a short cache when asked without a version", "immutable" not in r.headers.get("Cache-Control", ""))
v = look.version()
r = c.get(f"/ui/box.css?v={v}")
ok("...and a year, immutable, when asked by its own hash",
   "immutable" in r.headers.get("Cache-Control", ""), r.headers.get("Cache-Control"))
ok("a stale hash is not promised forever",
   "immutable" not in c.get("/ui/box.css?v=000000000000").headers.get("Cache-Control", ""))
ok("head_tags() links it by that hash and preloads the text face",
   f"/ui/box.css?v={v}" in look.head_tags() and 'rel="preload"' in look.head_tags())

print("\ntest_the_specimen_is_for_people_signed_in")
ok("a stranger is sent to sign in", c.get("/ui").status_code in (302, 303))
c.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
page = c.get("/ui").get_data(as_text=True)
ok("signed in, every building block is on the page",
   all(k in page for k in ('class="ui-card"', 'class="ui-row"', 'class="ui-chip"', "<button",
                           'class="ui-ghost"', 'class="ui-steps"', 'class="ui-copy"', "<input")))

print("\ntest_no_older_class_on_a_screen_is_restyled_by_accident")
# EVERY COMPONENT IS NAMED ui-. The first draft used .card, .row and ol.steps, and linking it to
# Base Machine drew a second divider under every set-up row and broke Add a Machine's steps into
# one-letter columns: those names were already in use on 118, 35 and 9 older elements. Found by
# rendering, 2026-09-24.
comp = REST
bare = sorted({m for m in re.findall(r"(?<![\w-])\.([a-z][\w-]*)", comp)
               if not m.startswith("ui-") and m not in ("ok", "warn", "bad", "new")})
ok("every class the stylesheet styles starts with ui- (only compound modifiers do not)", not bare, str(bare))
mods = re.findall(r"(?<![\w-])(\.(?:ok|warn|bad|new))\b", comp)
ok("...and a modifier never stands alone", all(
    re.search(r"\.ui-[\w-]+" + re.escape(m) + r"\b", comp) for m in set(mods)), str(set(mods)))

print("\ntest_it_stays_light")
# THE CAP COUNTS RULES, NOT COMMENTS (owner, 2026-09-29, D-B in docs/SCOPE_MOBILE_APP_REDESIGN.md
# §15.4). The comments are what let a client's own developer maintain this file after it leaves
# us, and a cap that counted them taxed exactly that. Rules stay under 20 KB; the whole file,
# comments included, stays under a generous ceiling so nobody pastes a library into it.
size = len(re.sub(r"\n\s*\n", "\n", re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)).encode())
ok(f"rules under 20 KB before compression ({size} bytes, comments not counted)", size < 20_000)
ok(f"...and the whole file under 40 KB ({len(CSS.encode())} bytes)", len(CSS.encode()) < 40_000)
ok("plain CSS: no preprocessor syntax a browser would not read",
   not re.search(r"^\s*(\$|@include|@mixin|@apply|@tailwind)", CSS, re.M))

print("\ntest_raw_colours_only_go_down")
# THE RATCHET. Counted 2026-09-24, before any screen moved onto the tokens. A PR that moves a
# screen lowers its number here; a PR that adds a raw colour to one of these files fails. Issue
# numbers in comments (#879) are not colours and are not counted.
BASELINE = {
    "core/dash/home.py": 3,
    "core/dash/__init__.py": 3,         # the operator console on the tokens too (owner, 2026-09-29)
    "core/dash/review.py": 19,
    "core/dash/box_settings.py": 4,
    "marketing/customer_voice/app.py": 14,   # step 6, then one dark (PR #1659 §14)
    "marketing/customer_voice/inbox/render.py": 3,
    "marketing/lead_machine/machine_app.py": 1,   # the Lead app on the tokens (owner, 2026-09-29)
}
_HEX = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})(?![0-9a-zA-Z])")


def raw_colours(path):
    s = open(os.path.join(ROOT, path), encoding="utf-8").read()
    return sum(1 for m in _HEX.findall(s)
               if len(m) == 7 or re.search(r"[a-fA-F]", m) or len(set(m[1:])) == 1)


for path, most in BASELINE.items():
    # A BOX SHIPS ONLY ITS OWN MACHINES. test_recipe_ships runs every shipped suite inside an
    # exported Lead box, which has no marketing/customer_voice at all — there is no file to count,
    # which is a true absence, said so, not a skip. The repo suite, which has every file, counts.
    if not os.path.isfile(os.path.join(ROOT, path)):
        print(f"  --   {path} does not ship on this box; nothing to count")
        continue
    n = raw_colours(path)
    ok(f"{path}: {n} raw colours, at most {most}", n <= most)
    if n < most:
        print(f"       lower its BASELINE to {n} in this file — it moved onto the tokens")

print("\ntest_translucent_colours_only_go_down_too")
# THE HEX RATCHET ABOVE COULD NOT SEE rgba() OR hsla(), so 109 translucent literals in these files were
# invisible to it (measured 2026-09-29, docs/SCOPE_MOBILE_APP_REDESIGN.md §8). Same rule, same files:
# the count may only fall. A token declaration counts too, which is deliberate — a translucent value
# that belongs in box.css should move there, and this is what notices when it does.
RGBA_BASELINE = {
    "core/dash/home.py": 1,
    "core/dash/__init__.py": 0,
    "core/dash/review.py": 6,
    "core/dash/box_settings.py": 0,
    "marketing/customer_voice/app.py": 39,
    "marketing/customer_voice/inbox/render.py": 0,
    "marketing/lead_machine/machine_app.py": 1,
}
_FN = re.compile(r"\b(?:rgba?|hsla?)\(")
for path, most in RGBA_BASELINE.items():
    if not os.path.isfile(os.path.join(ROOT, path)):
        print(f"  --   {path} does not ship on this box; nothing to count")
        continue
    n = len(_FN.findall(open(os.path.join(ROOT, path), encoding="utf-8").read()))
    ok(f"{path}: {n} translucent colours, at most {most}", n <= most)
    if n < most:
        print(f"       lower its RGBA_BASELINE to {n} in this file")

print("\n" + ("all good" if not _failed else f"{_failed} FAILED"))
sys.exit(1 if _failed else 0)
