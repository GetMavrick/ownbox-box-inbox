"""The Claude sign-in reads the token the way the real CLI DRAWS it (owner's box, 2026-10-02: every sign-in ended
"Claude's token arrived incomplete, so nothing was saved").

The fixture is the real `claude setup-token` 2.1.278 output, byte for byte, captured on a pty the way the box's
helper opens one, with a local stand-in for Anthropic's token server answering a fake 108-character token (the token
is kept out of the file as two placeholders and put back here). The CLI draws the token as 79 characters, then
`\\r\\x1b[1B` and an indent before the last 29, then `\\r\\x1b[1C\\x1b[2B` before "Store this token securely".

  · the screen shows the token whole, 108 characters, and nothing glued to it;
  · reading the same output as text (the old way) gets 79: this is the bug, so this file fails if the screen goes;
  · a read cut anywhere never shows a token with a wrong character, only a shorter one, though the old frame
    still beside it is token-shaped (the sign-in link's tail);
  · the helper, replaying the real output with a pause in the middle of the token, saves all 108 once Claude ends;
    and when Claude dies mid-token, saves nothing and says so.

Run: python tests/test_the_real_cli_draws_the_token.py
"""
from __future__ import annotations

import os
import pathlib
import stat
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
T = pathlib.Path(tempfile.mkdtemp())
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")
os.environ["DISPATCH_BEARER_TOKEN"], os.environ["DASH_TOKEN"] = "bearer", "pw"
for _k in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
    os.environ.pop(_k, None)

from core import box_secrets, claude_login, state  # noqa: E402

state.init_db()
FAILS: list[str] = []


def ok(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


TOKEN = "sk-ant-oat01-" + ("Qw3_eR7-tY9uI0oP2aS4dF6gH8jK1lZ5xC7vB9nM" * 3)[:93] + "AA"
assert len(TOKEN) == 108
RAW = ((ROOT / "tests" / "fixtures" / "claude_setup_token_2.1.278.bin").read_bytes()
       .replace(b"%%TOKEN-FIRST-ROW%%", TOKEN[:79].encode())
       .replace(b"%%TOKEN-SECOND-ROW%%", TOKEN[79:].encode()))

print("\nthe screen, from the real CLI's output —")
rows = claude_login.screen(RAW)
ok("the token is on the screen whole: all 108 characters", claude_login.token_on_screen(RAW) == TOKEN,
   repr(claude_login.token_on_screen(RAW)))
ok("...with the sentence under it on its own row, not glued on",
   any(r.strip() == "Store this token securely. You won't be able to see it again." for r in rows))
old = claude_login.find_token(claude_login._clean(RAW))
ok("reading the same output as TEXT gets a short token: the bug this file guards", old != TOKEN and len(old) < 90,
   f"{len(old)}")
cuts = [claude_login.token_on_screen(RAW[:k]) for k in range(len(RAW) + 1)]
bad = [k for k, got in enumerate(cuts) if not TOKEN.startswith(got)]
ok("a read cut at any of the bytes never gives a wrong character, only fewer (the old frame is never read)",
   not bad, f"cuts {bad[:5]}: {cuts[bad[0]] if bad else ''}")
ok("...and only the whole output gives the whole token", [k for k, got in enumerate(cuts) if got == TOKEN][0]
   > RAW.index(TOKEN[79:].encode()))
mid = RAW.index(TOKEN[:60].encode()) + 60                  # drawn up to here, the old frame beside it
ok("a token cut mid-row stops where the drawing stopped, though the old frame beside it is token-shaped",
   claude_login.token_on_screen(RAW[:mid]) == TOKEN[:60], repr(claude_login.token_on_screen(RAW[:mid])))
ok("the CLI's default width is what the helper assumes when the pty reports none",
   claude_login.DEFAULT_COLUMNS == 80)

print("\nthe helper, replaying the real CLI —")
BIN = T / "bin"
BIN.mkdir()
(T / "raw.bin").write_bytes(RAW)
_CLAUDE = f'''#!{sys.executable}
import os, sys, time
raw = open({str(T / "raw.bin")!r}, "rb").read()
prompt = raw.index(b">\\r\\r\\n", raw.index(b"prompted")) + 4
cut = raw.index({TOKEN[:43].encode()!r}) + 43
out = sys.stdout.buffer
out.write(raw[:prompt]); out.flush()
sys.stdin.buffer.readline()
out.write(raw[prompt:cut]); out.flush()
time.sleep(float(os.environ.get("FAKE_PAUSE", "2")))
if os.environ.get("FAKE_DIE"):
    sys.exit(1)
out.write(raw[cut:]); out.flush()
time.sleep(0.2)
'''
(BIN / "claude").write_text(_CLAUDE)
(BIN / "claude").chmod((BIN / "claude").stat().st_mode | stat.S_IEXEC)
os.environ["PATH"] = str(BIN) + os.pathsep + os.environ.get("PATH", "")


def sign_in() -> tuple[str, str]:
    box_secrets.clear(box_secrets.CLAUDE_OAUTH)
    claude_login.start(consented=True)
    t0 = time.time()
    while not claude_login.pending_url() and time.time() - t0 < 20:
        time.sleep(0.1)
    said = ""
    try:
        claude_login.finish("the-code#" + "x" * 43, user_id=state.owner_user()["id"])
    except Exception as e:                               # noqa: BLE001 — the sentence the page shows
        said = str(e)
    t1 = time.time()
    while claude_login.in_progress() and time.time() - t1 < 30:
        time.sleep(0.1)
    return box_secrets.get(box_secrets.CLAUDE_OAUTH) or "", said


got, said = sign_in()
ok("a pause in the middle of the token: all 108 characters saved once Claude ends", got == TOKEN,
   f"saved {len(got)}; said {said!r}")
os.environ["FAKE_DIE"] = "1"
got, said = sign_in()
ok("Claude dying mid-token: nothing saved", got == "", f"saved {len(got)}")
ok("...and the page says to start again", "arrived incomplete" in said, said)
os.environ.pop("FAKE_DIE")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    sys.exit(1)
print("all passed")
