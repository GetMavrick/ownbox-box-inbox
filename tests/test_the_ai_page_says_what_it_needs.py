"""The AI account page says, before any choice, what connecting needs.

Walk #3 (docs/JOURNEY_WALK_2026-09-23.md): *"No AI subscription, no way forward."* The page offered a
Claude sign-in, a ChatGPT sign-in and a key field, and a buyer with none of the three learned only
that we could do it for them. OSDev1's line for it, on the wall 2026-09-24: *"Needs a Claude or
ChatGPT subscription, or an API key."*

This suite holds, as the owner, that the line is on /settings/ai in the opening card, above the
model picker, whichever model is picked, including one the box cannot draft with yet.

Run: python tests/test_the_ai_page_says_what_it_needs.py
"""
import html
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "needs.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
os.environ["AIOS_MY_MACHINES"] = os.path.join(_T, "none")
os.environ.pop("ANTHROPIC_API_KEY", None)

from core import state                                                # noqa: E402

state.init_db()

from core import dash                                                 # noqa: E402
from core.dash import box_settings                                    # noqa: E402
from core.dispatch import app                                         # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


c = app.test_client()
c.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
LINE = html.escape("Needs a Claude or ChatGPT subscription, or an API key.")

print("\ntest_the_ai_page_says_what_it_needs")
ok("the words are the ones on the wall", getattr(box_settings, "AI_NEEDS", None) == html.unescape(LINE))
page = c.get("/settings/ai").get_data(as_text=True)
main = page.split("</nav>", 1)[-1]
ok("the line is on the page", LINE in main)
first = re.search(r'<div class="card">(.*?)</div>', main, re.S)
ok("...in the opening card, before any choice", bool(first) and LINE in first.group(1),
   first.group(1)[:200] if first else "no card")
ok("...and above the model picker",
   LINE in main and ("ai-model" not in main or main.index(LINE) < main.index("ai-model")))

opts = re.findall(r'<option value="([^"]+)"', main)
for model in opts:
    p = c.get(f"/settings/ai?model={model}").get_data(as_text=True)
    ok(f"picked {model}: the line is still there", LINE in p)

print("\n" + ("all good" if not _failed else f"{_failed} FAILED"))
sys.exit(1 if _failed else 0)
