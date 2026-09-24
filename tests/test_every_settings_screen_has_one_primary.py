"""Every System Settings screen has at most one ink pill: the thing the owner does next.

The design language (docs/SCOPE_DESIGN_LANGUAGE.md, OSDev0's checklist in #1490 §6) allows one
primary action per screen. `box.css` and the core screens' CSS draw EVERY <button> as the ink pill
unless it says otherwise, so each extra button silently becomes an extra primary. OSDev0's drift
check, 2026-09-24, found /settings/ai drawing three (Show, Connect, Save) and /settings/email two
identical "Save and send a test" pills for two alternative ways to send.

So this suite opens, as the owner, every row of the System Settings menu, plus the states a
person actually meets there (a Claude sign-in half done, email set up and not), and counts the
ink pills in what the server sends. A page with no action at all (the overview, Updates) has
none, which is right: a primary is something to do, not a decoration.

It also holds the open fold's minus sign, which the same screenshots found broken.

It counts HTML, not pixels: a <button> or an <input type=submit> with no outline or danger class,
and an <a> styled as a button. It includes a <noscript> fallback and a button folded inside a
<details>, because the person who opens them sees the pill.

Run: python tests/test_every_settings_screen_has_one_primary.py
"""
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "pill.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
os.environ["AIOS_MY_MACHINES"] = os.path.join(_T, "none")
os.environ.pop("ANTHROPIC_API_KEY", None)

from core import state                                                # noqa: E402

state.init_db()

from core import box_mail, claude_login, dash, shell                  # noqa: E402
from core.config import settings                                      # noqa: E402
from core.dispatch import app                                         # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


_QUIET = ("ghost", "ui-ghost", "danger", "ui-danger")


def ink_pills(page: str) -> list[str]:
    """The label of every control on the page that draws as the ink pill."""
    main = page.split("</nav>", 1)[-1]                   # the menu's rows are links, not pills
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


c = app.test_client()
c.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))


def at_most_one(label, href):
    r = c.get(href)
    if r.status_code != 200:
        ok(f"{label} renders", False, str(r.status_code))
        return
    found = ink_pills(r.get_data(as_text=True))
    ok(f"{label}: {len(found)} ink pill{'s' if len(found) != 1 else ''}", len(found) <= 1, str(found))


print("\ntest_every_settings_row_has_at_most_one_primary")
sec = next(s for s in shell.sections() if s.key == "settings")
for it in sec.items:
    at_most_one(it.href, it.href)

print("\ntest_the_states_a_person_meets_there")
_real_pending = claude_login.pending_url
claude_login.pending_url = lambda: "https://claude.com/sign-in?example=1"
try:
    at_most_one("/settings/ai, mid sign-in (Finish is the one)", "/settings/ai")
finally:
    claude_login.pending_url = _real_pending

_key = getattr(settings, "resend_api_key", "")
try:
    object.__setattr__(settings, "resend_api_key", "")
    ok("(email is off for this render)", not box_mail.is_configured())
    at_most_one("/settings/email with no email yet (Resend is the one)", "/settings/email")
    object.__setattr__(settings, "resend_api_key", "re_example")
    at_most_one("/settings/email while the box sends", "/settings/email")
finally:
    object.__setattr__(settings, "resend_api_key", _key)

at_most_one("/settings/chatgpt", "/settings/chatgpt")

print("\ntest_the_shipped_css_carries_no_control_characters")
# A CSS ESCAPE IN A PYTHON STRING IS A PYTHON ESCAPE FIRST. `content:"\2212"` (the minus on an open
# fold) reached the browser as U+0091 then "2", drawn as a broken glyph beside every open fold on
# System Settings. Found by screenshot, 2026-09-24. Write the character itself.
from core.dash import home                                            # noqa: E402

ctl = sorted({hex(ord(ch)) for ch in home.CSS
              if (ord(ch) < 32 and ch not in "\n\t") or 0x7F <= ord(ch) <= 0x9F})
ok("the core screens' CSS has no control characters in it", not ctl, str(ctl))
ok("...and an open fold shows a real minus sign", 'details[open]>summary::after{content:"−"}' in home.CSS)

print("\ntest_the_counter_sees_a_second_pill")
# A COUNTER THAT NEVER FAILS PROVES NOTHING. Two plain buttons count two; the outline, the danger
# and a link that is only a link do not count.
ok("two plain buttons are two pills",
   len(ink_pills('<nav></nav><button type="submit">A</button><button>B</button>')) == 2)
ok("an outline, a danger and a plain link are none",
   ink_pills('<nav></nav><button class="ghost">A</button><button class="danger">B</button>'
             '<a href="/x">C</a><input type="text" value="d">') == [])
ok("a link styled as a button counts", ink_pills('<nav></nav><a class="ui-btn" href="/x">Go</a>') == ["Go"])

print("\n" + ("all good" if not _failed else f"{_failed} FAILED"))
sys.exit(1 if _failed else 0)
