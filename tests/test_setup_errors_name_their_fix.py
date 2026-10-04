"""Every setup error a buyer can hit names its fix and links the exact page (plan #1857, launch bar 1).

Owner, 2026-10-01: setup fixes itself, and its errors name the fix. 2026-10-03: "our customers don't have the ability
to ask you questions." Held here:
  1. every literal error sentence the setup code can raise (AI sign-in, AI keys, mailbox, sending email, Data
     Sources, Search Console) says what to do; a sentence with no fix fails, by file and line;
  2. every refusal the Search Console module raises has a sentence on its page (three showed nothing);
  3. every page core/setup_errors.py links to is a real page the owner can open;
  4. on each setup page, a real failure shows a setup-error card that links the page that fixes it: AI Account,
     MCP Server, Data Sources, Sending Email, Search Console, Server Access.
Run: python tests/test_setup_errors_name_their_fix.py
"""
from __future__ import annotations

import ast
import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "setup.db")
os.environ["DISPATCH_BEARER_TOKEN"], os.environ["DASH_TOKEN"] = "bearer", "pw"
os.environ["AIOS_BOX_ACCESS_KEYS"] = os.path.join(_T, "authorized_keys")
for _k in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
    os.environ.pop(_k, None)

from core import state  # noqa: E402

state.init_db()
from core import dash, setup_errors  # noqa: E402
from core.dash import google_search  # noqa: E402
from core.dispatch import app  # noqa: E402

_failed = 0


def ok(label: str, cond: bool, detail="") -> None:
    global _failed
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  — {str(detail)[:900]}"))
    if not cond:
        _failed += 1


# ── 1. every error sentence says what to do ───────────────────────────────────────────────────────────────────
SETUP_FILES = ["core/claude_login.py", "core/codex_login.py", "core/box_secrets.py", "core/brain.py",
               "core/vendors/mailbox/verify.py", "core/vendors/mailbox/providers.py", "core/box_mail.py",
               "core/connections/client.py", "core/connections/oauth.py", "core/connections/store.py"]
ERRORS = {"LoginError", "SecretRejected", "Refused", "SignInFailed", "ConnectionFailed", "ValueError", "KeyRefused"}
# WHAT A FIX SOUNDS LIKE: an instruction, or where to go.
FIX = re.compile(r"\b(press|paste|try again|copy|sign in|turn on|turn it|add|choose|ask|support@|make|open|check|"
                 r"connect|put|allow|finish|change|start again|use|pick|give|wait|update|remove|switch|set up|"
                 r"enter|type|fix|go to|create|name the|replace)\b", re.I)
# PROGRAMMING ERRORS NO BUYER SEES (a wrong status passed by our own code), named so a new one is a decision.
INTERNAL = ("unknown claude_code status", "unknown anthropic status", "a run's workspace is an existing absolute",
            "unknown codex status")


def _text(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
    if isinstance(node, ast.BinOp):
        return (_text(node.left) or "") + (_text(node.right) or "")
    return None


def sentences():
    for f in SETUP_FILES:
        for n in ast.walk(ast.parse((ROOT / f).read_text())):
            if isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call) and n.exc.args:
                name = getattr(n.exc.func, "id", None) or getattr(n.exc.func, "attr", None)
                if name in ERRORS:
                    for a in n.exc.args:
                        t = _text(a)
                        if t and len(t) > 25 and " " in t and not t.startswith(INTERNAL):
                            yield f, n.lineno, t


print("\n1. every setup error says what to do —")
found = list(sentences())
no_fix = [f"{f}:{ln} {t[:100]}" for f, ln, t in found if not FIX.search(t)]
ok(f"ALL {len(found)} ERROR SENTENCES IN THE SETUP CODE NAME A FIX", len(found) > 60 and not no_fix, no_fix)
bad_said = [k for k, (_h, t, good) in google_search._SAID.items() if not good and not FIX.search(t)]
ok("...and so does every Search Console message", not bad_said, bad_said)

print("\n2. every Search Console refusal has its sentence —")
raised = {n.args[0].value for n in ast.walk(ast.parse((ROOT / "core/vendors/google_search_console.py").read_text()))
          if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "Refused" and n.args
          and isinstance(n.args[0], ast.Constant)}
ok("EVERY KEY THE MODULE RAISES IS ON THE PAGE (not_connected, no_property and google_down showed nothing)",
   raised and raised <= set(google_search._SAID), sorted(raised - set(google_search._SAID)))

print("\n3. every page it links to is a real page —")
owner = app.test_client()
owner.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
for area, (href, name) in setup_errors.PAGES.items():
    r = owner.get(href)
    ok(f"{name} ({href}) opens for the owner", r.status_code == 200, r.status_code)

print("\n4. a real failure on each setup page links the page that fixes it —")


def links(html: str, area: str) -> bool:
    href = setup_errors.PAGES[area][0]
    return 'class="card setup-error"' in html and f'<a href="{href}">Fix it on' in html


cases = {
    "ai": owner.post("/settings/ai", data={"do": "key", "key": "not-a-key"}),
    "mcp": owner.post("/settings/agent", data={"do": "mint", "label": "", "role": "act"}),
    "sources": owner.post("/settings/sources", data={"do": "add", "name": "Notion", "url": "http://mcp.notion.com/mcp"}),
    "email": owner.post("/settings/email", data={"do": "resend", "from": "hello@glowmedspa.com", "key": "abc"}),
    "search_console": owner.get("/settings/aeo/google?said=cancelled"),
    "access": owner.post("/settings/access", data={"key": "this is not a key"}),
}
for area, r in cases.items():
    page = r.get_data(as_text=True)
    ok(f"{setup_errors.PAGES[area][1]}: the error names its fix and links {setup_errors.PAGES[area][0]}",
       links(page, area), page[page.find("setup-error") - 200:][:600] if "setup-error" in page else page[:300])

print()
print("FAILED" if _failed else "ALL PASSED", _failed)
sys.exit(1 if _failed else 0)
