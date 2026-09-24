"""The Unified Inbox speaks the box's design language: two weights, nothing above the page title,
and one ink pill per screen.

WHY THIS EXISTS. OSDev0's drift check (2026-09-24, against docs/BOX_DESIGN_REFERENCE.md) found the
inbox, freshly moved onto box.css by #1498, still drawing its headline at 34px and weight 680, with
two identical ink "Save" pills on /inbox/setup and a Today button that never went full width on a
phone. The reference is Inter at 400 and 600 only, and nothing larger than the 32px page title.
Every one of those passed review because nothing counted them; this counts them.

Run: python tests/test_the_inbox_speaks_the_language.py
"""
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "marketing/customer_voice/app.py")

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  — {detail}"))
    if not cond:
        _failed += 1


# A BOX SHIPS ONLY ITS OWN MACHINES: test_recipe_ships runs every shipped suite inside an exported
# Lead box, which has no inbox. No inbox is a true absence, said so, not a skip.
if not os.path.isfile(APP):
    print("  --   no inbox machine ships on this box; nothing to check")
    sys.exit(0)

_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "inbox-lang.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
os.environ["AIOS_MY_MACHINES"] = os.path.join(_T, "none")
os.environ.pop("ANTHROPIC_API_KEY", None)

from core import state  # noqa: E402

state.init_db()

from core import dash  # noqa: E402
from core.dispatch import app  # noqa: E402
from marketing.customer_voice import app as _inbox  # noqa: E402,F401  (registers /inbox)

SRC = open(APP, encoding="utf-8").read()

print("\ntest_two_weights_only")
stray = sorted(set(re.findall(r"font-weight:\s*(\d{3})", SRC)) - {"400", "600"})
ok("every numeric font-weight in the inbox is 400 or 600 (the rest use --w-regular/--w-strong)",
   not stray, str(stray))

print("\ntest_nothing_is_larger_than_the_page_title")
big = [px for px in re.findall(r"font-size:\s*([0-9.]+)px", SRC) if float(px) > 32]
ok("no inbox font-size above 32px, the box's --t-title ceiling", not big, str(big))
head_v = re.search(r"\.head \.v\{([^}]*)\}", SRC)
ok("the Today headline is the page title's size and weight",
   bool(head_v) and "var(--t-title)" in head_v.group(1) and "var(--w-strong)" in head_v.group(1),
   head_v.group(1) if head_v else "no .head .v rule")

print("\ntest_a_link_styled_as_the_pill_can_go_full_width")
btn = re.search(r"\n\.btn\{([^}]*)\}", SRC)
ok("every .btn is a flex box, so an <a class=btn> fills the width on a phone like a <button>",
   bool(btn) and "display:inline-flex" in btn.group(1) and "width:100%" in btn.group(1),
   btn.group(1)[:120] if btn else "no .btn rule")

print("\ntest_one_ink_pill_per_inbox_screen")
_QUIET = ("ghost", "ui-ghost", "danger", "ui-danger")


def ink_pills(page: str) -> list[str]:
    """The label of every control after the menu that draws as the ink pill (as in
    test_every_settings_screen_has_one_primary)."""
    main = page.split("</nav>", 1)[-1]
    out = []
    for m in re.finditer(r"<(button|input|a)\b([^>]*)>(.*?)(?=</\1>|<)", main, re.S):
        tag, attrs, text = m.group(1), m.group(2), m.group(3)
        cls = set((re.search(r'class="([^"]*)"', attrs) or [None, ""])[1].split())
        if tag == "input" and 'type="submit"' not in attrs:
            continue
        if tag == "a" and not cls & {"btn", "ui-btn"}:
            continue
        if cls & set(_QUIET):
            continue
        label = text.strip() or (re.search(r'value="([^"]*)"', attrs) or [None, "?"])[1]
        out.append(re.sub(r"\s+", " ", label)[:40])
    return out


# A COUNTER THAT NEVER FAILS PROVES NOTHING.
ok("the counter sees two plain Saves as two", ink_pills(
    '<nav></nav><button class="btn">Save</button><button class="btn">Save</button>') == ["Save", "Save"])
ok("...and the outline as none", ink_pills('<nav></nav><button class="btn ghost">Save</button>') == [])

c = app.test_client()
c.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
for href in ("/inbox/", "/inbox/setup"):
    page = c.get(href, follow_redirects=True).get_data(as_text=True)
    got = ink_pills(page)
    ok(f"{href}: at most one ink pill", len(got) <= 1, str(got))

print("\n" + ("all ok" if not _failed else f"{_failed} FAILED"))
sys.exit(1 if _failed else 0)
