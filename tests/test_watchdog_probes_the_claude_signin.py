"""The watchdog probes the Claude sign-in a buyer connected, not an API key the box doesn't have.

FOUND ON THE OWNER'S OWN BOX, 2026-10-02. His Morning Review said an AEO article "could not be
published", and the box's health answered `brain: no_ai_key — no AI key is set, so nothing on this
box can draft yet`, while OSDev1 had read a real answer from his Claude sign-in that morning and
`brain.can_think()` was True. Two defects in core/watchdog.py, reproduced here before the fix:

  1. `probe_backend` picked the backend from config alone. Every sold box is built with
     `backend: api`; a buyer who connects a Claude subscription thinks on claude_code without that
     line changing (`brain._backend`, step 2). So the probe went looking for an API key, found none,
     and wrote the `unset` heartbeat the health report turns into "no AI key".
  2. `_oauth_token` read settings, the environment and the .env file, but never the token the buyer
     connected with Sign in to Claude, which is where `box_secrets.claude_oauth_token()` keeps it and
     what the brain thinks with. Fixing (1) alone would have turned "no AI key" into "not set".

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED: the probe picking a backend the brain doesn't think on,
or asking for the sign-in somewhere other than the brain's own resolver.

Run: python tests/test_watchdog_probes_the_claude_signin.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "wd_signin.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
for var in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "OPERATOR_SLACK_USER_ID"):
    os.environ.pop(var, None)                      # a delivered box: nothing in its environment

from core import state  # noqa: E402

state.init_db()

from core import box_secrets as bs  # noqa: E402
from core import brain  # noqa: E402
from core import config as _config  # noqa: E402
from core import watchdog  # noqa: E402

FAILS: list[str] = []
SIGN_IN = "sk-ant-oat01-" + "s" * 90


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


# A SOLD BOX'S CONFIG, whatever this repo's own says: `backend: api`, as export_box.sh writes it.
_real = _config.get_config
_sold = {**_real(), "brain": {**(_real().get("brain") or {}), "backend": "api"}}
_config.get_config = lambda: _sold                 # type: ignore[assignment]
brain.get_config = _config.get_config              # type: ignore[assignment]
watchdog.get_config = _config.get_config           # type: ignore[assignment]
watchdog.settings.claude_code_oauth_token = ""     # nothing in settings or the .env either
_real_read = Path.read_text
Path.read_text = lambda self, *a, **k: "" if self.name == ".env" else _real_read(self, *a, **k)  # type: ignore[assignment]

REAL_PROBE = watchdog._probe_claude_code
seen: list = []
watchdog._probe_claude_code = lambda: (seen.append(watchdog._oauth_token()) or (True, "ok"))   # no CLI, no spend
watchdog._probe_anthropic = lambda key=None: (_ for _ in ()).throw(AssertionError("probed an API key"))

# A FAKE `claude` ON PATH, FROM THE START: CI has no real CLI, and `brain.can_think()` needs one to say yes.
# it answers as the mode file says, writes down the token it was handed and
# whether stdin was closed, and counts its runs. No real CLI, no network, no spend.
_BIN = Path(tempfile.mkdtemp())
_MODE, _RUNS, _ENVSEEN = _BIN / "mode", _BIN / "runs", _BIN / "env_seen"
(_BIN / "claude").write_text(f"""#!/bin/sh
echo run >> "{_RUNS}"
printf '%s' "$CLAUDE_CODE_OAUTH_TOKEN" > "{_ENVSEEN}"
if [ -t 0 ]; then echo tty >> "{_ENVSEEN}.stdin"; else cat >/dev/null; echo closed >> "{_ENVSEEN}.stdin"; fi
case "$(cat "{_MODE}")" in
  ok)   echo '{{"type":"result","is_error":false,"result":"ok","total_cost_usd":0.00012}}' ;;
  auth) echo '{{"type":"result","is_error":true,"result":"Failed to authenticate. API Error: 401 {{\\"type\\":\\"error\\",\\"error\\":{{\\"type\\":\\"authentication_error\\",\\"message\\":\\"OAuth token has expired.\\"}}}}"}}'; exit 1 ;;
  net)  echo 'Connection error: getaddrinfo ENOTFOUND api.anthropic.com' >&2; exit 1 ;;
esac
""")
(_BIN / "claude").chmod(0o755)
os.environ["PATH"] = f"{_BIN}{os.pathsep}{os.environ.get('PATH', '')}"
_MODE.write_text("ok")

print("\n— before anyone signs in: still a skip, never a false outage —")
name, up, detail = watchdog.probe_backend()
ok("no sign-in and no key is still the no-AI-key skip", name == "anthropic_api" and detail == watchdog.NO_AI_KEY,
   f"{name} {detail}")

print("\n— a buyer signs in to Claude on a box built with backend: api —")
bs.put_claude_oauth(SIGN_IN)
ok("the brain thinks on the sign-in", brain._backend() == "claude_code" and brain.can_think()[0], brain.can_think())
name, up, detail = watchdog.probe_backend()
ok("the probe tests that sign-in, not an absent API key", name == "claude_code" and up is True, f"{name} {up} {detail}")
ok("...and finds the token where the brain keeps it", seen and seen[-1] == SIGN_IN, str([s[:14] for s in seen]))
ok("so the heartbeat is ok, never 'unset'", watchdog._beat(up, detail) == "ok", watchdog._beat(up, detail))

print("\n— the source cannot drift back to config —")
src = (ROOT / "core" / "watchdog.py").read_text()
body = src[src.index("def probe_backend"):src.index("\ndef ", src.index("def probe_backend") + 10)]
ok("probe_backend picks with brain._backend()", "_brain._backend()" in body and "get_config()" not in body)
tok = src[src.index("def _oauth_token"):src.index("\ndef ", src.index("def _oauth_token") + 10)]
ok("_oauth_token asks box_secrets.claude_oauth_token() first",
   "_bs.claude_oauth_token()" in tok and "tok or (settings.claude_code_oauth_token" in tok
   and tok.index("_bs.claude_oauth_token()") < tok.index("tok or (settings.claude_code_oauth_token"))

Path.read_text = _real_read                        # type: ignore[assignment]

# ── OSDev1's review of #1820: the live probe itself ─────────────────────────────────────────────────
watchdog._probe_claude_code = REAL_PROBE


def _reset(mode: str) -> None:
    _MODE.write_text(mode)
    for f in (_RUNS, _ENVSEEN, Path(f"{_ENVSEEN}.stdin")):
        f.unlink(missing_ok=True)
    with state.connect() as c:
        c.execute("DELETE FROM heartbeats WHERE component = ?", (watchdog._CLAUDE_PROBE_ROW,))
        c.execute("DELETE FROM spend_ledger")


def _runs() -> int:
    return len(_RUNS.read_text().split()) if _RUNS.exists() else 0


print("\n— the token goes to the one child process, never into this one's environment —")
_reset("ok")
os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
_logged: list = []
_real_info = watchdog.log.info
watchdog.log.info = lambda event, **kw: (_logged.append((event, kw)), _real_info(event, **kw))[1]  # type: ignore[assignment]
up, detail = watchdog._probe_claude_code()
watchdog.log.info = _real_info  # type: ignore[assignment]
_cost = [kw.get("total_cost_usd") for ev, kw in _logged if ev == "watchdog.claude_probe_live"]
ok("the live call logs what it cost (total_cost_usd)", _cost == [0.00012], str(_logged)[:200])
ok("a live probe on a signed-in box passes", up is True and detail == "reasoning ok (live call)", f"{up} {detail}")
ok("the CLI was handed the saved sign-in", _ENVSEEN.exists() and _ENVSEEN.read_text() == SIGN_IN)
_probe_src = (ROOT / "core" / "watchdog.py").read_text()
_probe_body = _probe_src[_probe_src.index("def _probe_claude_code"):_probe_src.index("\ndef ", _probe_src.index("def _probe_claude_code") + 10)]
# A SOURCE CHECK, not a runtime one: with no terminal attached (CI), stdin reads as closed either way.
ok("...with stdin closed (stdin=subprocess.DEVNULL on the call), so it can never wait on a prompt",
   "stdin=subprocess.DEVNULL" in _probe_body and "env=env" in _probe_body)
ok("this process's environment is unchanged after the probe", "CLAUDE_CODE_OAUTH_TOKEN" not in os.environ)
src = (ROOT / "core" / "watchdog.py").read_text()
ok("no line writes the token into os.environ", 'os.environ["CLAUDE_CODE_OAUTH_TOKEN"]' not in src)

print("\n— a live call runs at most every 6 hours, and never while real answers prove the sign-in —")
up, detail = watchdog._probe_claude_code()
ok("a second pass inside 6 hours of a passing live call does not call the CLI", up is True and _runs() == 1,
   f"{_runs()} runs, {detail}")
_reset("ok")
state.record_spend(task="draft_reply", model="cc:haiku", cost_usd=0.0, input_tokens=10, output_tokens=5)
up, detail = watchdog._probe_claude_code()
ok("a real answer in the last 6 hours skips the probe", up is True and _runs() == 0 and "real request" in detail,
   f"{_runs()} runs, {detail}")

print("\n— a refused call is not an answer: zero-output cc: rows never stand in for the probe —")
_reset("auth")
for _ in range(3):                                   # shifts kept trying on the expired sign-in
    state.record_spend(task="shift_tick", model="cc:sonnet", cost_usd=0.0, input_tokens=900, output_tokens=0)
up, detail = watchdog._probe_claude_code()
ok("an expired sign-in with only zero-output cc: rows still probes live, and pages the fix",
   _runs() == 1 and up is False and detail == watchdog.CLAUDE_SIGNIN_EXPIRED, f"{_runs()} runs, {up} {detail}")
bs.note_claude_oauth_status("connected")

print("\n— a refused sign-in names its fix —")
_reset("auth")
up, detail = watchdog._probe_claude_code()
ok("a 401 pages 'Claude sign-in expired: sign in again on Settings → AI'",
   up is False and detail == watchdog.CLAUDE_SIGNIN_EXPIRED, f"{up} {detail}")
ok("...and the AI screen learns the sign-in needs redoing", bs.claude_oauth_state()["status"] == "needs_reauth",
   str(bs.claude_oauth_state()))
ok("the missing-sign-in message points at Settings → AI, not a server file",
   "Settings → AI" in watchdog.CLAUDE_SIGNIN_MISSING and "/opt/aios/.env" not in src)
bs.note_claude_oauth_status("connected")

print("\n— anything else pages on the second miss in a row, not the first —")
_reset("net")
up1, d1 = watchdog._probe_claude_code()
ok("the first network miss is a skip: no page, no false recovery", up1 is True and d1.startswith("skip ("), d1)
ok("...recorded as 'unchecked', never a green 'ok' (OSDev4's catch) and never 'unset' ('no AI key')",
   watchdog._beat(up1, d1) == "unchecked", watchdog._beat(up1, d1))
ok("every other probe's skip still beats ok, so a box's check-in never warns about features it lacks",
   watchdog._beat(True, "skip (no inbox or reel tables on this box)") == "ok"
   and watchdog._beat(True, "skip (OperationalError)") == "ok" and watchdog._beat(*(True, watchdog.NO_AI_KEY)) == "unset")
up2, d2 = watchdog._probe_claude_code()
ok("the second miss in a row pages", up2 is False and "ENOTFOUND" in d2, f"{up2} {d2}")
_MODE.write_text("ok")
up3, d3 = watchdog._probe_claude_code()
ok("after a miss the next pass probes again, and a pass clears it", up3 is True and d3 == "reasoning ok (live call)", d3)

print("\n— with an API key and a sign-in both present, the probe follows the brain onto claude_code —")
_reset("ok")
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-api03-" + "k" * 90
name, up, detail = watchdog.probe_backend()
ok("key plus token: the brain and the probe both pick claude_code",
   brain._backend() == "claude_code" and name == "claude_code" and up is True, f"{brain._backend()} {name} {detail}")
os.environ.pop("ANTHROPIC_API_KEY", None)

print("\n— the box's own health, and the AEO Machine's status, read the sign-in as ready —")
_reset("ok")
watchdog.check_backend_now()
from core import box_tools  # noqa: E402
_brain_state = (box_tools.health() or {}).get("brain") or {}
ok("box health's brain reads ok, not no_ai_key", _brain_state.get("state") == "ok", str(_brain_state))
# ...and right after ONE failed check: not green, not "no AI key", and AEO doesn't call the account missing.
state.heartbeat("brain_backend", watchdog._beat(True, watchdog.CLAUDE_FIRST_MISS + "ENOTFOUND)"))
_unchecked = (box_tools.health() or {}).get("brain") or {}
ok("after one failed check, box health's brain is 'unchecked' (ok: None), not ok and not no_ai_key",
   _unchecked.get("state") == "unchecked" and _unchecked.get("ok") is None, str(_unchecked))
from core import health as _health  # noqa: E402
_line = next((l for l in _health.report().splitlines() if "brain:" in l), "")
ok("...and the health report says the check didn't get through, without a green mark",
   "didn't get through" in _line and ":white_check_mark:" not in _line, _line)
state.heartbeat("brain_backend", "ok")
try:
    from marketing.aeo_machine import tools as aeo_tools  # noqa: E402
    _need = (aeo_tools.status() or {}).get("missing") or (aeo_tools.status() or {}).get("need") or []
    _flat = " ".join(map(str, _need)) if isinstance(_need, (list, tuple)) else str(aeo_tools.status())
    ok("AEO status no longer lists the AI account as missing", "signed-in AI account" not in _flat, _flat[:200])
    state.heartbeat("brain_backend", "unchecked")
    _need2 = (aeo_tools.status() or {}).get("missing") or []
    ok("...nor right after one failed check", not any("signed-in AI account" in str(n) for n in _need2), str(_need2)[:200])
except ImportError as e:
    ok("the AEO Machine imports on this checkout", False, str(e))

print("\n— and this file cannot silently fall out of CI —")
if (ROOT / ".github").is_dir():
    ok("test_watchdog_probes_the_claude_signin is in the workflow's suite list",
       "test_watchdog_probes_the_claude_signin" in (ROOT / ".github/workflows/tests.yml").read_text())

print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
