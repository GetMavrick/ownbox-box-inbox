"""The inbox app's design rules, as things that can fail.

A DUAL TOKEN SET IS THE THING THAT ROTS QUIETLY. Light is the base and dark overrides it, so a
token added to one and forgotten in the other does not crash, does not warn, and does not look
wrong to whoever added it — it looks wrong to the half of the customers using the other theme,
who will not file a bug about it. That is the failure this suite exists for, and the rest of it
is the floor the app already stood on.

WHAT IT CANNOT HOLD, and does not pretend to: hierarchy, tone, whether a control should be
absent rather than disabled, whether an empty state reads as calm or as broken. Those are review.

Run: python tests/test_inbox_design.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "marketing", "customer_voice")
APP = os.path.join(PKG, "app.py")

# A GUARD MUST KNOW WHERE IT IS STANDING, and the first cut of this one did not. This suite ships
# into every box, but only a Customer Voice box carries the app it reads — so inside a Lead box
# `open(APP)` raised FileNotFoundError at import and took the whole run down. That is the exact
# defect class `test_customer_voice.py` already records having made once, and I made it again.
#
# THE DEGRADE IS NARROW ON PURPOSE, because "skip when the file is missing" is how a suite quietly
# stops testing anything. The question asked is whether this box carries the MACHINE at all:
#   · no marketing/customer_voice directory  → this box does not have the inbox, nothing to hold
#   · the directory exists but app.py is gone → that is a real failure and it is reported as one
if not os.path.isdir(PKG):
    print("test_inbox_design")
    print("  --   this box does not carry the Unified Inbox — no app to hold to its design")
    print("all ok")
    sys.exit(0)

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


# THE DIRECTORY IS HERE, SO THE APP MUST BE. Reaching this line with no app.py means the machine
# shipped broken, which is worth a loud failure rather than a quiet skip.
if not os.path.isfile(APP):
    print("test_inbox_design")
    print(f"  FAIL the machine ships but its app is missing — {APP}")
    sys.exit(1)

SRC = open(APP).read()


def _block(name):
    return SRC.split(f'{name} = """', 1)[1].split('"""', 1)[0]


CSS = _block("CSS")
JS = _block("JS")


def _tokens(decls):
    return {m.group(1): m.group(2).strip()
            for m in re.finditer(r"--([a-z][a-z0-9-]*)\s*:\s*([^;}]+)", decls)}


# The two declaration sites, matched by their own selectors so a renamed block fails loudly
# rather than silently scoring zero tokens.
LIGHT_M = re.search(r"(?<!\])\n:root\{([^}]*)\}", CSS)
STAMP_M = re.search(r':root\[data-theme="dark"\]\{([^}]*)\}', CSS)

print("test_white_is_the_default_and_dark_is_only_ever_chosen")
# OWNER, 2026-09-16: "we want white screens first and foremost... and then we'll probably have a
# dark toggle later." THIS SUITE USED TO ASSERT THE OPPOSITE — it required a
# `prefers-color-scheme: dark` block and checked it agreed with the stamped one. That rule came
# from this file's own comment ("light is the target, dark as the toggle") being implemented as
# "follow the OS", so a buyer whose laptop is in dark mode opened a dark product having never
# asked for one. The assertions are inverted rather than deleted: the property is still checked,
# it is just the property he actually asked for.
ok("the LIGHT base is declared on a bare :root — the default, whatever the OS says",
   LIGHT_M is not None)
# READ AS A RULE, NOT AS A SUBSTRING. The first version searched the raw CSS for the phrase and
# failed on the COMMENT that explains this very change — the same false positive this repo has
# now hit in a suite guard, an export guard and a shippability check. Comments are stripped and
# the actual at-rule is what is looked for.
_CSS_NO_COMMENTS = re.sub(r"/\*.*?\*/", " ", CSS, flags=re.S)
ok("the product NEVER darkens itself from the operating system",
   not re.search(r"@media[^{]*prefers-color-scheme", _CSS_NO_COMMENTS),
   "a prefers-color-scheme at-rule is back in the app's CSS")
ok("dark is still reachable, by the switch and only by the switch", STAMP_M is not None)

LIGHT = _tokens(LIGHT_M.group(1)) if LIGHT_M else {}
STAMP = _tokens(STAMP_M.group(1)) if STAMP_M else {}
ok(f"the light palette has tokens to check ({len(LIGHT)})", len(LIGHT) >= 10, str(sorted(LIGHT)))


# ── the rot guard ───────────────────────────────────────────────────────────────────────────
print("\ntest_no_token_exists_in_only_one_theme")
# A TOKEN THE OTHER THEME NEVER REDEFINES INHERITS THE LIGHT VALUE. On a dark ground that is a
# light-theme colour on a dark surface — unreadable, and invisible to whoever added it, because
# they were looking at the theme they wrote.
missing_stamp = sorted(set(LIGHT) - set(STAMP))
ok("every light token is redefined for the explicit dark stamp"
   + (f" — MISSING: {missing_stamp}" if missing_stamp else ""), not missing_stamp)
stray = sorted(set(STAMP) - set(LIGHT))
ok("dark introduces no token light has never heard of"
   + (f" — ONLY IN DARK: {stray}" if stray else ""), not stray)

print("\ntest_every_token_used_is_actually_defined")
used = set(re.findall(r"var\(\s*--([a-z][a-z0-9-]*)", CSS))
undefined = sorted(used - set(LIGHT))
ok("every var(--x) resolves to a token in the light base"
   + (f" — UNDEFINED: {undefined}" if undefined else ""), not undefined)


# ── the palette is closed ───────────────────────────────────────────────────────────────────
print("\ntest_no_colour_enters_outside_the_palette")
def _rgb(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


decl = "".join(m.group(1) for m in (LIGHT_M, STAMP_M) if m)
palette = {_rgb(h) for h in re.findall(r"#[0-9a-fA-F]{6}\b", decl)}
palette |= {tuple(int(x) for x in t.replace(" ", "").split(","))
            for t in re.findall(r"rgba?\(\s*(\d+\s*,\s*\d+\s*,\s*\d+)", decl)}
body = CSS
for m in (LIGHT_M, STAMP_M):
    if m:
        body = body.replace(m.group(1), "")
ok(f"the palette's own colours were read ({len(palette)})", len(palette) >= 8)

# BRAND MARKS ARE THE ONE EXEMPTION, and it is narrow: a channel's logo is its owner's colour,
# not ours, and forcing it through our palette would be drawing the wrong logo. Those live in
# Python, not in the stylesheet, so the stylesheet rule stays absolute.
foreign = sorted({tuple(int(x) for x in t.replace(" ", "").split(","))
                  for t in re.findall(r"rgba?\(\s*(\d+\s*,\s*\d+\s*,\s*\d+)", body)} - palette)
ok("no colour in the stylesheet outside the declared palette"
   + (f" — FOREIGN: {foreign}" if foreign else ""), not foreign)
stray_hex = sorted(set(re.findall(r"#[0-9a-fA-F]{3,8}", body)))
ok("no raw hex outside the palette blocks — a colour with no name is one nobody can theme"
   + (f" — FOUND: {stray_hex}" if stray_hex else ""), not stray_hex)


# ── the PWA floor ───────────────────────────────────────────────────────────────────────────
print("\ntest_it_behaves_like_an_installed_app")
# INSTALLED THERE IS NO BROWSER CHROME, so the app's own furniture has to clear the hardware.
# SCOPED TO THE RULE THAT HAS TO CARRY IT, not to the stylesheet as a whole. The first cut of
# these two asked whether the string appeared ANYWHERE, and `body` also uses the bottom inset to
# reserve room for the bar — so deleting it from `.tabs` left the check green. Measured: the probe
# ran, the edit applied, and the suite still exited 0. A check that cannot fail is not a check,
# which is the second time that exact shape has turned up in this app's guards in one day.
# ANCHORED AT THE START OF A LINE, because `.tabs{` is a substring of `nav.tabs{` and the
# stylesheet gained one of those (the desktop query hides the bar now that a rail replaces
# it). Unanchored, this regex matched `nav.tabs{display:none}` — which appears EARLIER in
# the file — and then checked that rule for the safe-area inset, so a green run said nothing
# about the bar it was written to guard. The check itself was the thing that broke, not the
# padding: `.tabs` still carries the inset, which is what the re-anchored search now reads.
_tabs = re.search(r"^\.tabs\{([^}]*)\}", CSS, re.M)
_bar = re.search(r"\.bar\{([^}]*)\}", CSS)
ok("the tab bar rule exists to check", _tabs is not None)
ok("the tab bar itself clears the home indicator",
   _tabs is not None and "env(safe-area-inset-bottom" in _tabs.group(1))
ok("the title bar itself clears the notch",
   _bar is not None and "env(safe-area-inset-top" in _bar.group(1))
ok("...and the page reserves room for the fixed bar, so the last row is never under it",
   re.search(r"body\{[^}]*padding-bottom:calc\([^)]*safe-area-inset-bottom", CSS) is not None)
ok("theme-color follows the theme rather than being pinned to one",
   SRC.count("theme-color") >= 2 and '_THEME_BG["light"]' in SRC)
ok("the switch is a link that sets a cookie, not a script",
   "THEME_COOKIE" in SRC and "set_cookie(THEME_COOKIE" in SRC)
ok("...and 'system' CLEARS the preference instead of storing a third value",
   "delete_cookie(THEME_COOKIE" in SRC)

print("\ntest_the_accessibility_floor_holds")
# READ AS RULES, NOT AS TEXT — the same reason the prefers-color-scheme check above strips
# comments. This one matched a COMMENT that said "NOT `outline:none`", i.e. it failed the CSS for
# writing down the rule it obeys. That is not a weaker guard: a declaration removes focus and a
# comment removes nothing, so stripping comments is the difference between measuring the rule and
# measuring the prose around it. The theme guard had this exact fix for this exact reason.
ok("focus is never removed — outline:none appears in no declaration",
   not re.search(r"outline\s*:\s*none", _CSS_NO_COMMENTS))
ok("...and focus is drawn with an offset so it reads against the surface",
   "outline:2px" in CSS.replace(" ", "") and "outline-offset" in CSS)
ok("a conversation row is a 44px tap target",
   re.search(r"\.conv\{[^}]*min-height:44px", CSS) is not None)
# READS THE SOURCE, so it sees the f-string that appends the read-state class rather than the
# rendered attribute. The claim is unchanged: the anchor opens with the row's own class.
ok("...and the whole row is the anchor, not a word inside it", '<a class="conv' in SRC)
ok("a tab is a 44px+ target too", re.search(r"\.tab\{[^}]*min-height:5\dpx", CSS) is not None)
# THE CHANNEL IS NAMED, NOT ONLY COLOURED. The mark is a shape, and the name travels with it in
# text a screen reader reads — a coloured dot with a title attribute would not pass this.
ok("the channel mark carries the channel's NAME in readable text",
   'class="vh">' in SRC and "_channel(plat)" in SRC)
ok("...and that text is visually hidden rather than merely tiny", re.search(r"\.vh\{[^}]*clip-path", CSS) is not None)

print("\ntest_no_text_input_is_small_enough_to_zoom_ios")
small = []
for sel, b in re.findall(r"([^{}]*(?:textarea|input|select)[^{}]*)\{([^}]*)\}", CSS):
    for size in re.findall(r"font-size\s*:\s*(\d+(?:\.\d+)?)px", b):
        if float(size) < 16:
            small.append(f"{sel.strip()} -> {size}px")
ok("no text input sets a font-size under 16px" + (f" — {small}" if small else ""), not small)
ok("...and the compose box states 16px rather than inheriting it",
   re.search(r"\.compose textarea\{[^}]*font-size:16px", CSS) is not None)

print("\ntest_the_app_never_implies_it_is_live")
for banned in ("setInterval", "setTimeout", "fetch(", "XMLHttpRequest", "EventSource",
               "WebSocket", "location.reload"):
    ok(f"the client script does not use {banned}", banned not in JS)
ok("no meta refresh anywhere", 'http-equiv="refresh"' not in SRC.lower())

print(("FAILED " + str(_failed)) if _failed else "all ok")
sys.exit(1 if _failed else 0)
