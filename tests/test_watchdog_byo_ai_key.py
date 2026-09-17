"""The watchdog probes the AI key the brain ACTUALLY uses (core/watchdog.probe_backend).

FOUND BY THE IMAGE v8 CLONE-CHECK (2026-09-16). A freshly booted clone logged one ERROR:

    watchdog.probe_failed_operator_unset  key=anthropic_api  "no ANTHROPIC_API_KEY configured"

On a box nobody has onboarded yet that sentence is true. The defect is that it stayed true FOREVER.
`core.brain` drafts with `box_secrets.anthropic_key()` — the environment first, then the key a buyer
pastes on the set-up screen — while the probe read `settings.anthropic_api_key` alone. So on every
bring-your-own-key box the watchdog reported the AI down on every pass, at ERROR, while the brain was
drafting perfectly well. Harmless with no operator contact; a permanent false page on any box that
has one, which is what the Managed tier sells.

Every existing watchdog suite stubs `probe_backend` out whole, so none of them could see which key it
resolved. This one stubs only the network call and asserts on the key that reached it.

FOUND AGAIN BY THE IMAGE v9 CLONE-CHECK (2026-09-17), the other half: before anyone adds a key, the
probe still returned a FAILURE, so every new box logged that ERROR from its first pass — and a box
with an operator contact would page for every customer who had not finished set-up. No key is now a
SKIP (no page, no ERROR) with an `unset` heartbeat; a key the vendor refuses still fails loudly.

Run: python tests/test_watchdog_byo_ai_key.py
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
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "wd_byo.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
os.environ.pop("ANTHROPIC_API_KEY", None)          # a delivered box: no key in its environment

from core import state  # noqa: E402

state.init_db()

from core import box_secrets as bs  # noqa: E402
from core import config as _config  # noqa: E402
from core import watchdog  # noqa: E402

FAILS: list[str] = []
BUYERS_KEY = "sk-ant-api03-" + "b" * 40
OPERATORS_KEY = "sk-ant-api03-" + "o" * 40


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


# The API backend is the one a delivered box runs (`brain.backend: api`). This repo's own config may
# name the owner's subscription instead, and that path never reaches the key at all — so it is pinned.
_real_get_config = _config.get_config
_config.get_config = lambda: {**_real_get_config(), "brain": {"backend": "api"}}   # type: ignore[assignment]

probed: list = []
watchdog._probe_anthropic = lambda key=None: (probed.append(key) or (True, "auth ok"))  # no network


def run():
    probed.clear()
    return watchdog.probe_backend()


print("\n— a box nobody has set up yet is NOT an outage (image v9 clone-check, 2026-09-17) —")
name, up, detail = run()
ok("with no key anywhere the probe reports a SKIP, not a failure",
   name == "anthropic_api" and up is True and detail.startswith("skip ("), f"{up} {detail}")
ok("...without a network call it could not make", probed == [], str(probed))
ok("...and it is the exact sentence the heartbeat recognises", detail == watchdog.NO_AI_KEY, detail)

errors: list = []
dms: list = []
_real_error, _real_dm = watchdog.log.error, watchdog.slack.send_dm
watchdog.log.error = lambda event, **kw: errors.append(event)                       # type: ignore[assignment]
watchdog.slack.send_dm = lambda dm, text: (dms.append(text) or True)                # type: ignore[assignment]
try:
    watchdog._alert(name, up, detail)
    watchdog._alert(name, up, detail)                                                # the next pass, too
    watchdog.check_backend_now()
finally:
    watchdog.log.error, watchdog.slack.send_dm = _real_error, _real_dm              # type: ignore[assignment]
ok("NO ERROR is logged on a box that has not been set up — the v9 clone logged one on its first pass",
   errors == [], str(errors))
ok("...nobody is paged", dms == [], str(dms))
row = state.get_alert_row(name)
ok("...and no FAIL is on record", row is None or row["state"] != "FAIL", str(dict(row) if row else None))
beats = {h["component"]: h["status"] for h in state.get_heartbeats()}
ok("the heartbeat says `unset` — never an `ok` it has not earned", beats.get("brain_backend") == "unset",
   str(beats))
from core import health  # noqa: E402
brain = next((ln for ln in health.report().splitlines() if ln.startswith("• brain:")), "")
ok("the health report says there is no AI key yet, neither green nor an outage",
   "no AI key yet" in brain and ":white_check_mark:" not in brain and ":red_circle:" not in brain, brain)

print("\n— THE DEFECT: the buyer has pasted their key —")
bs.put(bs.ANTHROPIC, BUYERS_KEY)
name, up, detail = run()
ok("the AI is reported UP once the buyer's key is stored", up is True, detail)
ok("...because the probe used the key the brain drafts with", probed == [BUYERS_KEY], str(probed))
watchdog.check_backend_now()
beats = {h["component"]: h["status"] for h in state.get_heartbeats()}
ok("...and the heartbeat is a real `ok` now", beats.get("brain_backend") == "ok", str(beats))

print("\n— going quiet on NO key must not mute a BAD key —")
watchdog._probe_anthropic = lambda key=None: (probed.append(key) or (False, "HTTP 401"))  # refused
errors.clear()
watchdog.log.error = lambda event, **kw: errors.append(event)                       # type: ignore[assignment]
try:
    name, up, detail = run()
    watchdog._alert(name, up, detail)
finally:
    watchdog.log.error = _real_error                                                 # type: ignore[assignment]
ok("a key the vendor REFUSES is still a failure, logged at ERROR on its first pass",
   up is False and "watchdog.probe_failed_operator_unset" in errors, f"{up} {detail} {errors}")
ok("...and its heartbeat is `fail`", watchdog._beat(up, detail) == "fail", watchdog._beat(up, detail))
watchdog._probe_anthropic = lambda key=None: (probed.append(key) or (True, "auth ok"))  # no network

print("\n— the environment still wins, as it does for the brain —")
os.environ["ANTHROPIC_API_KEY"] = OPERATORS_KEY
name, up, detail = run()
ok("a key in the environment is the one probed, not the table's", probed == [OPERATORS_KEY], str(probed))
ok("...which is exactly what box_secrets.anthropic_key() — the brain's resolver — returns",
   bs.anthropic_key() == OPERATORS_KEY, bs.anthropic_key())
os.environ.pop("ANTHROPIC_API_KEY", None)

print("\n— one resolution rule, not two —")
src = (ROOT / "core" / "watchdog.py").read_text()
body = src[src.index("def probe_backend"):src.index("def ", src.index("def probe_backend") + 10)]
ok("probe_backend asks box_secrets.anthropic_key(), the brain's own resolver",
   "box_secrets.anthropic_key()" in body, body[-400:])
ok("...and no longer decides on settings.anthropic_api_key alone",
   "if settings.anthropic_api_key:" not in body, body[-400:])

_config.get_config = _real_get_config                                               # type: ignore[assignment]
print()
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
