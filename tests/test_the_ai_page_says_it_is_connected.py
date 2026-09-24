"""The AI account page says it is connected, and which account, before offering to change it.

Found at 390px on 2026-09-24 (OSDev4): System Settings reads "Your AI account: Connected", and
/settings/ai, one tap later, drew a fresh Connect screen with the terms tick and a paste field, as if
nothing were set up. The page never asked. On the owner's own box, which is connected, that is the
screen he opens in the demo.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  · a connected box's page stops opening on "Connected" and the account it drafts with;
  · the ways to connect stop folding under "Use a different account" there, or the fold loses them;
  · the fold closes over a buyer who is mid-change: a sign-in running, a refusal just shown, or a
    model picked from the list;
  · a box with nothing connected gets the fold, hiding the only thing it needs;
  · a buyer reads a reserved noun (CLAUDE.md, mobile first).

Run: python tests/test_the_ai_page_says_it_is_connected.py
"""
import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "aipage.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
os.environ.pop("ANTHROPIC_API_KEY", None)

from core import state                                                # noqa: E402

state.init_db()

from core import box_secrets, claude_login, dash                      # noqa: E402
from core.dispatch import app                                         # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def text(html):
    html = re.sub(r"(?is)<(style|script)\b.*?</\1>", " ", html)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


owner = state.owner_user()["id"]
c = app.test_client()
c.set_cookie(dash.COOKIE, dash.new_session(owner))
_real_url = claude_login.pending_url
claude_login.pending_url = lambda: ""


def clear():
    box_secrets.clear(box_secrets.ANTHROPIC)
    box_secrets.clear(box_secrets.ANTHROPIC_STATUS)
    box_secrets.clear(box_secrets.CLAUDE_OAUTH)
    box_secrets.clear(box_secrets.CLAUDE_OAUTH_STATUS)


def page(path="/settings/ai"):
    r = c.get(path)
    return r.status_code, r.get_data(as_text=True)


try:
    print("\ntest_nothing_connected_is_the_connect_page_as_before")
    clear()
    code, html = page()
    ok("it renders", code == 200, str(code))
    ok("it does not claim a connection", "<h2>Connected</h2>" not in html)
    ok("...and folds nothing away: the Connect button is on the page",
       'class="fold ai-other"' not in html and 'value="start"' in html)

    print("\ntest_an_api_key_opens_on_connected")
    clear()
    box_secrets.put(box_secrets.ANTHROPIC, "sk-ant-api03-" + "a" * 60)
    box_secrets.put(box_secrets.ANTHROPIC_STATUS, "connected")
    code, html = page()
    ok("it opens on Connected, first", "<h2>Connected</h2>" in html
       and html.find("<h2>Connected</h2>") < html.find('value="start"'), html[:200])
    ok("...naming the account the drafts are written with", "Anthropic API key" in text(html))
    fold = html[html.find('<details class="fold ai-other">'):]
    ok("the ways to connect sit inside 'Use a different account'",
       "Use a different account" in fold and 'value="start"' in fold and 'name="key"' in fold)
    ok("the key itself is never on the page", "a" * 60 not in html)

    print("\ntest_a_claude_subscription_is_named_as_one")
    clear()
    box_secrets.put(box_secrets.CLAUDE_OAUTH, "sk-ant-oat01-" + "b" * 60)
    code, html = page()
    ok("it says Claude subscription", "<h2>Connected</h2>" in html
       and "Claude subscription" in text(html).split("Use a different account")[0])

    print("\ntest_a_buyer_mid_change_sees_the_cards")
    ok("a model picked from the list unfolds the page",
       'class="fold ai-other"' not in page("/settings/ai?model=claude")[1])
    claude_login.pending_url = lambda: "https://claude.com/cai/oauth/authorize?x=1"
    try:
        _, html = page()
    finally:
        claude_login.pending_url = lambda: ""
    ok("a sign-in in progress unfolds it, so the code field is in view",
       'class="fold ai-other"' not in html and 'name="code"' in html)

    print("\ntest_a_member_is_still_refused")
    member = state.add_user("sam@example.com", name="Sam")["id"]
    m = app.test_client()
    m.set_cookie(dash.COOKIE, dash.new_session(member))
    ok("a member gets the owner's page refused, as before", m.get("/settings/ai").status_code == 403)

    print("\ntest_the_vocabulary")
    clear()
    box_secrets.put(box_secrets.CLAUDE_OAUTH, "sk-ant-oat01-" + "b" * 60)
    said = text(page()[1])
    banned = re.findall(r"\b(phone|phones|ring|call|calls|dial|line|voice)\b", said, re.I)
    ok("no reserved noun on the connected page", not banned, str(sorted(set(banned))))
finally:
    claude_login.pending_url = _real_url
    clear()

print("\n— and this file cannot silently fall out of CI —")
_wf = ROOT / ".github/workflows/tests.yml"
if _wf.is_file():
    ok("test_the_ai_page_says_it_is_connected is in the workflow's suite list",
       "test_the_ai_page_says_it_is_connected" in _wf.read_text())

print("\nALL OK" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
