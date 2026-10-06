"""The MCP Server page guides the connect, step by step, so nobody has to ask (owner, 2026-10-03: "our customers don't
have the ability to ask you questions. So if they can't figure that shit out they're gonna fucking be pissed";
OSDev1's assignment, a .13 blocker).

And on 2026-10-02: "nobody wants to enter an API key because that's really scary because it could get really
expensive." So signing in leads, and the key is the labelled fallback, called a connection key, said to cost nothing.

Held here:
  1. Connect ChatGPT: chatgpt.com in a browser, Settings, Plugins, New custom plugin, the exact field values, a Copy
     button beside the address, Authentication OAuth, the risk box, Create, Allow;
  2. its errors, each with its fix: the desktop or mobile app's "OAuth setup is unavailable in this environment", a
     Business workspace's admin, and a sign-in that won't work;
  3. the connection key comes after signing in, quotes ChatGPT's own "Access token / API key" so the field can be
     found, says in plain words what the key is and costs, and is made right there, named ChatGPT;
  4. Connect Claude the same way, with its own fixes;
  5. the key page: shown once, Copy key and Copy address, where it goes in ChatGPT, and the same plain words;
  6. our own words never say "API key", and never name OpenAI or Anthropic.
Run: python tests/test_the_mcp_page_guides_the_connect.py
"""
from __future__ import annotations

import html
import os
import re
import sys
import tempfile

_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "guide.db")
os.environ["DISPATCH_BEARER_TOKEN"], os.environ["DASH_TOKEN"] = "bearer", "pw"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import state  # noqa: E402

state.init_db()
from core import dash  # noqa: E402
from core.dash import box_settings  # noqa: E402
from core.dispatch import app  # noqa: E402

_failed = 0


def ok(label: str, cond: bool, detail="") -> None:
    global _failed
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  — {str(detail)[:600]}"))
    if not cond:
        _failed += 1


def text(h: str) -> str:
    h = re.sub(r"<(style|script)\b.*?</\1>", " ", h, flags=re.S)
    t = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h)))
    return re.sub(r" ([.,:])", r"\1", re.sub(r"\s+", " ", t)).strip()


def section(h: str, sid: str) -> str:
    m = re.search(rf'<div class="card guide" id="{sid}">(.*?)</div><div class="card', h, re.S)
    return m.group(1) if m else ""


def ours(t: str) -> str:
    """Our own words: ChatGPT's field label is quoted so the buyer can find it, and is not ours."""
    return t.replace("Access token / API key", "")


owner = app.test_client()
owner.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
ADDR = "http://localhost/mcp"
page = owner.get("/settings/agent").get_data(as_text=True)
gpt, cl = section(page, "chatgpt"), section(page, "claude")

print("\ntest_connect_chatgpt_step_by_step")
steps = re.findall(r"<li>(.*?)</li>", gpt.split("<h3>")[0], re.S)
_want = ("On chatgpt.com, in a web browser", "Name: Ownbox", f"Server URL: {ADDR}", "Authentication: OAuth",
         "Tick the box that says you understand the risk", "ChatGPT sends you to this box to sign in")
ok("SIX STEPS, IN THE ORDER A PERSON DOES THEM", len(steps) == 6
   and all(text(x).startswith(w) for x, w in zip(steps, _want)), [text(x) for x in steps])
ok("...on chatgpt.com in a browser: Settings, Plugins, New custom plugin",
   all(f"<b>{w}</b>" in steps[0] for w in ("chatgpt.com", "Settings", "Plugins", "New custom plugin")), steps[:1])
ok("...the address beside a Copy button that copies exactly it", f'data-copy="{ADDR}"' in steps[2]
   and f'<div class="addr">{ADDR}</div>' in steps[2], steps[2:3])
ok("...the risk box, Create, then Allow on this box", "<b>Create</b>" in steps[4] and "<b>Allow</b>" in steps[5])

print("\ntest_every_chatgpt_error_names_its_fix")
fixes = {text(b): text(f) for b, f in re.findall(r"<li><b>(.*?)</b>:(.*?)</li>", gpt.split("<h3>", 1)[-1], re.S)}
_un = fixes.get("“OAuth setup is unavailable in this environment”", "")
ok("CREATE GREYED OUT UNDER 'OAUTH SETUP IS UNAVAILABLE': click Continue in ChatGPT, where Create works (owner 10-03)",
   "Create is greyed out" in _un and "Continue in ChatGPT" in _un and "Create works" in _un, fixes)
ok("...or chatgpt.com in a web browser", "on chatgpt.com in a web browser" in _un, fixes)
ok("NO CUSTOM PLUGINS: the workspace admin turns on Developer mode, in Settings, Apps, Advanced",
   "Developer mode, in Settings, Apps, Advanced" in fixes.get("No Plugins, or no New custom plugin", ""), fixes)
ok("IT WON'T SIGN IN: a connection key, on every plan", "connection key" in fixes.get("It won't sign in", "")
   and "every plan" in fixes.get("It won't sign in", ""), fixes)
ok("...and every one of them says what to do", len(fixes) == 3 and all(len(f) > 30 for f in fixes.values()), fixes)

print("\ntest_the_connection_key_is_the_fallback")
ok("SIGNING IN LEADS: the key comes after the OAuth step and the fixes",
   gpt.index("<b>Authentication:</b> OAuth") < gpt.index("If it doesn") < gpt.index("Use a connection key instead"))
key = gpt.split("Use a connection key instead", 1)[-1]
ok("...quoting ChatGPT's own field, so it can be found", "<b>Access token / API key</b> (ChatGPT's words)"
   in html.unescape(key), key[:400])
ok("...saying what the key is and costs, in plain words", box_settings.KEY_WORDS in html.unescape(key)
   and all(w in box_settings.KEY_WORDS for w in ("made by your box", "costs nothing", "isn't a key to any AI account",
                                                 "delete it here any time")))
ok("...made right there, already named ChatGPT", 'value="ChatGPT"' in key and 'name="do" value="mint"' in key)

print("\ntest_connect_claude_the_same_way")
csteps = [text(s) for s in re.findall(r"<li>(.*?)</li>", cl.split("<h3>")[0], re.S)]
ok("FOUR STEPS: Settings, Connectors, Add custom connector; Ownbox; the address; Add, Connect, Allow",
   len(csteps) == 4 and "Settings, then Connectors, and choose Add custom connector" in csteps[0]
   and csteps[1] == "Name: Ownbox" and csteps[2].startswith(f"Remote MCP server URL: {ADDR}")
   and all(w in csteps[3] for w in ("Add", "Connect", "Allow")), csteps)
ok("...the address beside its Copy button", f'data-copy="{ADDR}"' in cl)
cfix = {text(b): text(f) for b, f in re.findall(r"<li><b>(.*?)</b>:(.*?)</li>", cl.split("<h3>", 1)[-1], re.S)}
ok("...and its errors with their fixes: a Team or Enterprise owner, and the whole address",
   "an owner of the organization adds it first" in cfix.get("No Add custom connector", "")
   and "ending in /mcp" in cfix.get("It couldn't connect", ""), cfix)

print("\ntest_the_key_page")
kp = owner.post("/settings/agent", data={"do": "mint", "label": "ChatGPT", "role": "act"})
kh = kp.get_data(as_text=True)
cred = re.search(r'<p class="addr">([^<]+)</p><button type="button" class="ghost" data-copy="([^"]+)"', kh)
ok("THE KEY, SHOWN ONCE, BESIDE A COPY BUTTON THAT COPIES EXACTLY IT", kp.status_code == 200 and cred is not None
   and cred.group(1) == cred.group(2) and len(cred.group(1)) > 20, kh[:600])
ok("...and the address with its own", f'data-copy="{ADDR}"' in kh and "Copy address" in kh)
ok("...where it goes in ChatGPT, in its words", "<b>Access token / API key</b> (ChatGPT's words)" in html.unescape(kh))
ok("...and the same plain words about the key", box_settings.KEY_WORDS in html.unescape(kh))

print("\ntest_our_own_words")
for name, h in (("the page", page), ("the key page", kh)):
    t = text(h)
    ok(f"{name}: never 'API key' in our own words", "api key" not in ours(t).lower(), re.findall(r".{40}API key.{20}", t))
    ok(f"{name}: no OpenAI or Anthropic", not re.search(r"OpenAI|Anthropic", t), re.findall(r".{30}(?:OpenAI|Anthropic)", t))
ok("the name field's example names no AI (owner 10-03)", 'placeholder="My AI app"' in page
   and not re.search(r'placeholder="[^"]*(?:Grok|Claude|ChatGPT|Gemini)', page))
ids = re.findall(r'id="(seat-label[^"]*)"', page)
ok("two key forms, two different field ids", len(ids) == 2 and len(set(ids)) == 2, ids)
err = owner.post("/settings/agent", data={"do": "mint", "label": "", "role": "act"}).get_data(as_text=True)
ok("A KEY THAT CAN'T BE MADE SAYS WHY, with its fold open", '<details id="key" open>' in err, err[:300])

print()
print("FAILED" if _failed else "ALL PASSED", _failed)
sys.exit(1 if _failed else 0)
