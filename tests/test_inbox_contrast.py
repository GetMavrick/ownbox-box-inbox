"""Every colour pair on the phone app, computed against WCAG AA — owner, 2026-09-16.

"Please improve the contrast. Make sure that the messages are readable."

WHY THIS IS ARITHMETIC AND NOT AN OPINION. The palette that prompted it looked fine to me on a
desktop display, and `--dimmer` was carrying 2.61:1 on white — a bit over half the required
ratio — on the timestamp of every conversation, the sender line inside every thread, the counts on
the channel chips, and the LABELS ON THE TAB BAR. Contrast is the one design property that can be
measured exactly, so it is measured exactly, and nobody has to win an argument about it again.

BOTH GROUNDS, EVERY TIME. This app puts white cards on a grey page, so every text token appears on
two backgrounds. A token checked against one of them is a token that fails on the other the first
time somebody moves an element — `--dim` passed on the card at 4.74:1 and failed on the page at
4.21:1, and both of those were live at once.

AA, NOT AAA, and deliberately: 4.5:1 for body text and 3:1 for large text is the line the WCAG
sets and the line a court reads. Where a value clears it comfortably that is a margin, not an
invitation to spend it.

Run: python tests/test_inbox_contrast.py
"""
import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DASH_TOKEN", "test-dash-pw")

_failed = 0
AA, AA_LARGE = 4.5, 3.0


def ok(what: str, cond: bool, got: str = "") -> None:
    global _failed
    if cond:
        print(f"  ok   {what}")
    else:
        _failed += 1
        print(f"  FAIL {what}" + (f"  — {got}" if got else ""))


def _lum(hexs: str) -> float:
    """Relative luminance, WCAG 2.1 §relative-luminance — the sRGB transfer curve, not an average.
    A naive (r+g+b)/3 would call #0000ff and #ffff00 similarly bright; they are 2.44 and 19.56."""
    h = hexs.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4   # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(fg: str, bg: str) -> float:
    a, b = _lum(fg), _lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _tokens(selector: str) -> dict:
    from marketing.customer_voice.app import CSS
    m = re.search(re.escape(selector) + r"\s*\{(.*?)\n\}", CSS, re.S)
    if not m:
        return {}
    return dict(re.findall(r"--([\w-]+)\s*:\s*(#[0-9a-fA-F]{3,8})", m.group(1)))


# WHAT SITS ON WHAT. Read off the rules that use them, not imagined — the comment on each line is
# the thing a person is actually looking at when this pair is on screen.
PAIRS = (
    ("ink",             "surface", AA,       "a name on a conversation row"),
    ("ink",             "bg",      AA,       "a heading on the page ground"),
    ("dim",             "surface", AA,       "THE MESSAGE PREVIEW — the content of this screen"),
    ("dim",             "bg",      AA,       "the same preview, on the page ground"),
    ("dimmer",          "surface", AA,       "the timestamp, and the sender line in a thread"),
    ("dimmer",          "bg",      AA,       "the tab-bar labels and the channel-chip counts"),
    ("bad",             "surface", AA,       "a failure sentence on a card"),
    ("bad",             "bad_soft", AA,      "the 'window closed' tag"),
    ("accent",          "accent_soft", AA,   "the 'New' tag"),
    ("accent_ink",      "accent",  AA,       "the label on every primary button"),
    ("bubble_out_ink",  "bubble_out", AA,    "a reply this box sent, in the thread"),
)


def _check(theme: str, selector: str) -> None:
    t = _tokens(selector)
    ok(f"{theme}: the palette is readable at all", bool(t), selector)
    if not t:
        return
    for fg, bg, need, what in PAIRS:
        f, b = t.get(fg.replace("_", "-")), t.get(bg.replace("_", "-"))
        if not f or not b:
            ok(f"{theme}: --{fg} and --{bg} both exist", False, f"{fg}={f} {bg}={b}")
            continue
        r = ratio(f, b)
        ok(f"{theme}: {what} — {f} on {b} is {r:.2f}:1", r >= need, f"needs {need}:1")


def test_light_passes_aa():
    _check("light", ":root")


def test_dark_passes_aa():
    _check("dark", ':root[data-theme="dark"]')


def test_the_two_themes_have_the_same_tokens():
    """A token that exists in one theme and not the other renders as `inherit` or as nothing at
    all in the theme that lacks it — which is the classic unreadable-in-dark-mode bug, and it is
    invisible to anyone developing in the other one."""
    light, dark = set(_tokens(":root")), set(_tokens(':root[data-theme="dark"]'))
    ok("dark defines every token light does", not (light - dark), str(sorted(light - dark)))
    ok("...and light defines every token dark does", not (dark - light), str(sorted(dark - light)))


def test_no_colour_is_written_straight_into_a_rule():
    """A hex typed into a rule cannot be re-checked by the two tests above and cannot follow the
    theme. The exemptions are the vendors' OWN marks — Gmail's red, Instagram's gradient — which
    are their brand colours and are wrong if we adjust them."""
    from marketing.customer_voice.app import CSS
    body = re.sub(r"(?s)/\*.*?\*/", " ", CSS)
    body = re.sub(r"(?s):root\s*\{.*?\n\}", " ", body)                 # the token blocks
    body = re.sub(r'(?s):root\[data-theme="dark"\]\s*\{.*?\n\}', " ", body)
    loose = sorted(set(re.findall(r"#[0-9a-fA-F]{6}\b", body)))
    # White and black are allowed as structural values (a scrim, a shadow), not as text colour.
    loose = [c for c in loose if c.lower() not in ("#ffffff", "#000000")]
    ok("no stray hex colour outside the token blocks", not loose, str(loose))


def test_ci_actually_runs_this_file():
    """A GUARD MUST KNOW WHERE IT IS STANDING — and in a BUYER'S BOX this one did not.

    `.github/` is ours and never ships. This suite does ship, because a Customer Voice box
    carries the screen it measures, so in that box the read raised FileNotFoundError and took
    the run down. CI never saw it: `test_recipe_ships` proves every shipped suite inside a fresh
    LEAD box, and this suite is not in a Lead box — it is dropped there for importing a machine
    that box lacks. The box type that DOES carry it is the one nothing exercises.

    Found by exporting a Customer Voice box and running all 71 of its suites by hand.

    THE DEGRADE IS NARROW, because "skip when a file is missing" is how a suite quietly stops
    testing anything. The question asked is whether this is the REPO at all:
      · no .github directory  → a buyer's box, which has no CI manifest to be listed in
      · the directory is there and the workflow is gone → a real failure, reported as one
    """
    here = pathlib.Path(__file__).resolve().parents[1]
    if not (here / ".github").is_dir():
        print("  --   not the repo — a buyer's box has no CI manifest to be named in")
        return
    wf = here / ".github/workflows/tests.yml"
    ok("test_inbox_contrast is in the workflow's suite list",
       "test_inbox_contrast" in wf.read_text())


if __name__ == "__main__":
    for fn in (test_light_passes_aa,
               test_dark_passes_aa,
               test_the_two_themes_have_the_same_tokens,
               test_no_colour_is_written_straight_into_a_rule,
               test_ci_actually_runs_this_file):
        print(fn.__name__)
        fn()
    print(f"{_failed} FAILED" if _failed else "all ok")
    sys.exit(1 if _failed else 0)
