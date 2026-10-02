"""The Claude token a box uses is whole, and it is the newest sign-in (owner's box, 2026-10-01: every request refused
with "OAuth access token is invalid", and signing in again changed nothing).

  · the capture waits: a token whose wrapped second line hasn't arrived is NOT complete; it is complete once a blank
    line or a sentence follows it; the whole 108 characters are joined;
  · the newest sign-in wins over a token in the box's .env; the .env is used only when nobody signed in;
  · the AI Account page says which token is in use.

Run: python tests/test_claude_token_whole_and_newest.py
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
T = pathlib.Path(tempfile.mkdtemp())
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")
os.environ["DISPATCH_BEARER_TOKEN"], os.environ["DASH_TOKEN"] = "bearer", "pw"
os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)

from core import box_secrets, claude_login, state  # noqa: E402

state.init_db()
FAILS: list[str] = []


def ok(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


TOKEN = "sk-ant-oat01-" + "A1b2C3d4E5" * 9 + "xyzAA"          # 108 characters, the real shape
assert len(TOKEN) == 108
head, tail = TOKEN[:67], TOKEN[67:]                            # wrapped after "Your OAuth token: " on 80 columns
said = "Your OAuth token (valid for 1 year): " + head + "\n"

print("\nthe capture waits for the whole token —")
tok, complete = claude_login.scan_token(said)
ok("only the first line has arrived: not complete, so nothing is saved yet", not complete, f"{len(tok)} {complete}")
tok, complete = claude_login.scan_token(said + tail)
ok("the second line arriving with nothing after it: still not complete", not complete)
tok, complete = claude_login.scan_token(said + tail + "\n\nStore this token securely.\n")
ok("a blank line after it: complete, and all 108 characters", complete and tok == TOKEN, f"{len(tok)} {complete}")
tok, complete = claude_login.scan_token("token: " + TOKEN + " Store this token securely.")
ok("a sentence after it on the same line: complete, the sentence left out", complete and tok == TOKEN)
ok("find_token, as every caller uses it, still answers whole", claude_login.find_token(said + tail + "\n\nStore") == TOKEN)

print("\nthe newest sign-in wins —")
stale = "sk-ant-oat01-" + "S" * 95
os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = stale
ok("with only a token in the box's .env, that one is used, and named", box_secrets.claude_oauth_token() == stale
   and box_secrets.claude_oauth_source() == "settings file")
box_secrets.put_claude_oauth(TOKEN, consented=True)
ok("a sign-in wins over the .env token", box_secrets.claude_oauth_token() == TOKEN
   and box_secrets.claude_oauth_source() == "sign-in")

print("\nthe page says which token —")
from core import dash  # noqa: E402
from core.dispatch import app  # noqa: E402

owner = app.test_client()
owner.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
html = owner.get("/settings/ai").get_data(as_text=True)
ok("AI Account says the box uses the token from the sign-in", "Using the token from your sign-in" in html,
   html[html.find("Connected"):][:400])
os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    sys.exit(1)
print("all passed")
