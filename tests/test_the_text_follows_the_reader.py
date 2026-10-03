"""The box's text follows the reader's own text size — and never falls back to fixed pixels.

Owner, 2026-09-29: "Every phone can adjust text size so how does ours relate or interact?", then
"scope it and then build it" (docs/SCOPE_SYSTEM_TEXT_SIZE.md). Every text size on a box is
N * --px: 1px at the default setting, the device's own text size on iPhone and iPad, the browser's
base size elsewhere, capped at 150%. Fields keep a 16px floor, because iOS zooms the whole page when
a field under 16px is tapped.

What this holds, so a later edit cannot quietly undo it:
  1. --px is declared, capped at 150%, and the iOS opt-in is iOS-only (a Mac's body style is 13px);
  2. no stylesheet a client's screens draw sets a text size in bare pixels again;
  3. every field rule has the 16px floor;
  4. the screens actually carry the unit.

Run: python tests/test_the_text_follows_the_reader.py
"""
import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("AIOS_HERMETIC_TEST", "1")
os.environ.setdefault("AIOS_DB_PATH", os.path.join(tempfile.mkdtemp(), "text.db"))
os.environ.setdefault("DASH_TOKEN", "pw")
os.environ.setdefault("DISPATCH_BEARER_TOKEN", "bearer")

_failed = 0


def ok(what: str, cond, got: str = "") -> None:
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {what}" + (f"  — {got}" if not cond and got else ""))
    if not cond:
        _failed += 1


BOX = (ROOT / "core/dash/static/box.css").read_text()

print("test_the_unit_is_declared_capped_and_ios_only")
# A TOUCH SMALLER since 2026-10-02 (owner, about 6%, from a before and after): over 17, not 16; and one more step on
# 2026-10-03 (owner chose about 6% again): over 18. The ceiling stays.
ok("--px is the browser's base size over 18, under the same 1.5px ceiling",
   "--px: min(calc(1rem / 18), 1.5px);" in BOX)
_ios = re.search(r"@supports \(font: -apple-system-body\) and \(-webkit-touch-callout: none\) \{(.*?)\n\}",
                 BOX, re.S)
ok("iPhone and iPad opt in, and only they (the touch-callout test keeps a Mac out)", _ios is not None)
ok("...handing the root the device's own text size",
   _ios is not None and ":where(html) { font: -apple-system-body; }" in _ios.group(1))
ok("...measured from its default of 17px, the same two steps smaller, under the same ceiling",
   _ios is not None and "--px: min(calc(1rem / 19), 1.5px);" in _ios.group(1))
ok("...and the body takes the box's own face back", re.search(r":where\(body\) \{[^}]*font-family: var\(--sans\)", BOX) is not None)

# ── 2 & 3: every stylesheet a client's screens draw ─────────────────────────────────────────────
print("\ntest_no_text_size_is_fixed_pixels_again")
_BLOCKS = {"core/dash/home.py": ("_BASE", "RAIL_CSS", "_PAGE"), "core/dash/review.py": ("_CSS_SRC",),
           "core/dash/shifts.py": ("_CSS",), "marketing/customer_voice/app.py": ("CSS",),
           "marketing/aeo_machine/app.py": ("DENSE_CSS", "_PH_CSS")}
_BARE = re.compile(r"font-size:\s*\d+(?:\.\d+)?px|font:\s*(?:[a-z0-9]+\s+)*\d+(?:\.\d+)?px")
_FIELD = re.compile(r"(?<![\w-])(input|select|textarea)(?![\w-])")
sheets = {"core/dash/static/box.css": BOX}
for rel, names in _BLOCKS.items():
    p = ROOT / rel
    if not p.is_file():
        continue
    src = p.read_text()
    for name in names:
        m = re.search(r"^" + re.escape(name) + r' = """(.*?)"""', src, re.M | re.S)
        ok(f"{rel}: its stylesheet {name} is where this test expects it", m is not None)
        if m:
            sheets[f"{rel}:{name}"] = m.group(1)
for where, css in sheets.items():
    bare = _BARE.findall(re.sub(r"/\*.*?\*/", "", css, flags=re.S))
    ok(f"{where}: no text size in bare pixels", not bare, str(bare[:4]))
    floorless = []
    for sel, decls in re.findall(r"([^{}]*)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", css, flags=re.S)):
        if _FIELD.search(sel) and re.search(r"font(-size)?:", decls) and "var(--px" in decls \
                and "max(16px" not in decls:
            floorless.append(sel.strip()[-50:])
    ok(f"{where}: every field keeps its 16px floor", not floorless, str(floorless[:3]))
# INLINE STYLES TOO: a size typed into a template is a size nobody's setting reaches.
for rel in ("core/dash/home.py", "core/dash/review.py", "core/dash/box_settings.py",
            "marketing/customer_voice/app.py", "marketing/aeo_machine/app.py"):
    p = ROOT / rel
    if not p.is_file():
        continue
    inline = re.findall(r'style=\\?"[^"]*?font-size:\d+(?:\.\d+)?px', p.read_text())
    ok(f"{rel}: no inline text size in bare pixels", not inline, str(inline[:2]))

# ── 4: the screens carry it ─────────────────────────────────────────────────────────────────────
print("\ntest_the_screens_carry_the_unit")
from core import state          # noqa: E402

state.init_db()
from core import dash           # noqa: E402
from core.dispatch import app   # noqa: E402

c = app.test_client()
c.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
for path in ("/dashboard", "/settings/people", "/inbox/inbox", "/inbox/mailbox"):
    r = c.get(path)
    if r.status_code == 404:
        print(f"  --   {path} is not served on this box")
        continue
    h = r.get_data(as_text=True)
    ok(f"{path} links the stylesheet that declares the unit, and sizes its own text in it",
       "/ui/box.css" in h and "var(--px, 1px)" in h, str(r.status_code))

print("\n— and this file cannot silently fall out of CI —")
if (ROOT / ".github").is_dir():
    ok("registered in the suite list",
       "test_the_text_follows_the_reader" in (ROOT / ".github/workflows/tests.yml").read_text())

print("\n" + ("all good" if not _failed else f"{_failed} FAILED"))
sys.exit(1 if _failed else 0)
