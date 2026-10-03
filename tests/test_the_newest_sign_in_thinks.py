"""A box signed in to both Claude and ChatGPT thinks on the NEWEST sign-in, and the AI Account page names no AI.

OSDev1's review of #1848, 2026-10-03: the owner signed in to Claude on 10-02 and to ChatGPT that night for his demo,
and the box kept thinking on Claude, because a Claude token always won. "Connecting one IS choosing it" (brain's own
rule), so between the two the newest sign-in wins, by the times the box already stores.

The owner, the same night, on the AI Account page reading "your Claude subscription": "Please adjust to not mention
which LLM. We're also going to add Gemini and Grok very soon."

Held here:
  1. his box (Claude, then ChatGPT, both before this release) thinks on ChatGPT with no new sign-in;
  2. Claude signed in after ChatGPT thinks on Claude, and a tie or a Claude token only in .env goes to Claude;
  3. disconnecting the newer falls back to the other, in both directions;
  4. `brain.backend` in config still wins outright;
  5. the AI Account page says Connected, "your AI subscription", and names no AI; a lapsed ChatGPT sign-in on a box
     that also holds a Claude token reads as needing a sign-in, never Connected off the token it isn't using.
Run: python tests/test_the_newest_sign_in_thinks.py
"""
from __future__ import annotations

import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
T = pathlib.Path(tempfile.mkdtemp())
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")
os.environ["AIOS_CODEX_HOME"] = str(T / "codex-home")
os.environ["DISPATCH_BEARER_TOKEN"], os.environ["DASH_TOKEN"] = "bearer", "pw"
for _k in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(_k, None)

from core import state  # noqa: E402

state.init_db()
from core import box_secrets as bs, brain, dash  # noqa: E402
from core.dispatch import app  # noqa: E402
import core.config as _cfgmod  # noqa: E402

FAILS: list[str] = []


def ok(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:400]}"))
    if not cond:
        FAILS.append(label)


_real_cfg = _cfgmod.get_config
PIN = {"backend": "api"}                      # a sold box ships `backend: api`; this repo's own config pins Claude


def _as_sold():
    c = _real_cfg()
    return {**c, "brain": {**(c.get("brain") or {}), **PIN}}


_cfgmod.get_config = brain.get_config = _as_sold
TOKEN = "sk-ant-oat01-" + "x" * 95


def at(name: str, when: str) -> None:
    with state.connect() as c:
        c.execute("UPDATE box_secrets SET set_at = ? WHERE name = ?", (when, name))


def claude(when: str) -> None:
    bs.put(bs.CLAUDE_OAUTH, TOKEN)
    at(bs.CLAUDE_OAUTH, when)


def chatgpt(when: str, status: str = "connected") -> None:
    home = pathlib.Path(os.environ["AIOS_CODEX_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    (home / "auth.json").write_text("{}")
    bs.note_codex_status(status)
    at(bs.CODEX_STATUS, when)


def off() -> None:
    bs.clear(bs.CLAUDE_OAUTH)
    bs.clear_codex()


owner = app.test_client()
owner.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))


def card() -> str:
    h = owner.get("/settings/ai").get_data(as_text=True)
    m = re.search(r'<div class="card"><h2>Connected</h2>.*?</div>', h, re.S)
    return m.group(0) if m else h[:600]


print("\n1. his box: Claude on 10-02, ChatGPT that night, both before this release —")
claude("2026-10-02T15:10:00+00:00")
chatgpt("2026-10-03T00:30:00+00:00")
ok("IT THINKS ON CHATGPT, with no new sign-in", brain._backend() == "codex", brain._backend())

print("\n2. Claude signed in after ChatGPT, a tie, and a Claude token only in .env —")
at(bs.CLAUDE_OAUTH, "2026-10-03T01:00:00+00:00")
ok("Claude newer: it thinks on Claude", brain._backend() == "claude_code", brain._backend())
at(bs.CLAUDE_OAUTH, "2026-10-03T00:30:00+00:00")
ok("a tie goes to Claude, the owner's default", brain._backend() == "claude_code", brain._backend())
bs.clear(bs.CLAUDE_OAUTH)
os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = TOKEN
ok("a Claude token only in .env has no sign-in time: the ChatGPT sign-in is newer", brain._backend() == "codex",
   brain._backend())
os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN")

print("\n3. disconnecting the newer falls back to the other —")
off()
claude("2026-10-02T15:10:00+00:00")
chatgpt("2026-10-03T00:30:00+00:00")
bs.clear_codex()
ok("ChatGPT newer, disconnected: back to Claude", brain._backend() == "claude_code", brain._backend())
chatgpt("2026-10-02T09:00:00+00:00")
claude("2026-10-03T01:00:00+00:00")
bs.clear(bs.CLAUDE_OAUTH)
ok("Claude newer, disconnected: back to ChatGPT", brain._backend() == "codex", brain._backend())

print("\n4. config still wins outright —")
claude("2026-10-02T15:10:00+00:00")
chatgpt("2026-10-03T00:30:00+00:00")
PIN["backend"] = "claude_code"
ok("brain.backend: claude_code thinks on Claude, newer ChatGPT or not", brain._backend() == "claude_code")
PIN["backend"] = "api"

print("\n5. the AI Account page names no AI —")
c = card()
ok("CONNECTED, ON 'YOUR AI SUBSCRIPTION'", "Connected" in c and "your AI subscription" in c, c)
ok("...and no AI is named (owner: Gemini and Grok are coming)",
   not re.search(r"Claude|ChatGPT|Anthropic|OpenAI|Gemini|Grok", c), c)
ok("...nor the Claude token's source, while the box thinks on ChatGPT", "token from your sign-in" not in c, c)
at(bs.CLAUDE_OAUTH, "2026-10-03T01:00:00+00:00")
c = card()
ok("on Claude: still no AI named, and the token's source is said",
   not re.search(r"Claude|ChatGPT|Anthropic", c) and "token from your sign-in" in c, c)
at(bs.CLAUDE_OAUTH, "2026-10-02T15:10:00+00:00")
bs.note_codex_status("needs_reauth", "401 Unauthorized")
at(bs.CODEX_STATUS, "2026-10-03T00:40:00+00:00")
st = bs.anthropic_state()
ok("A LAPSED CHATGPT SIGN-IN BESIDE A CLAUDE TOKEN reads as needing a sign-in, never Connected off Claude's",
   brain._backend() == "codex" and st["status"] == "needs_reauth" and st.get("provider") == "chatgpt", st)
ok("...and the page does not say Connected", "<h2>Connected</h2>" not in owner.get("/settings/ai").get_data(as_text=True))

print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    sys.exit(1)
print("all passed")
