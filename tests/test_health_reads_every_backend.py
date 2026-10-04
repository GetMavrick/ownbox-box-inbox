"""The box's health reads the AI check of every backend the watchdog can probe (OSDev1, 2026-10-04).

THE BUG: `watchdog.probe_backend()` records its result as `probe:<key>` for claude_code, codex or anthropic_api, but
the health report and `core.health` read only `brain_backend`, `probe:claude_code` and `probe:anthropic_api`. On a box
signed in to ChatGPT the every-30-minute `probe:codex` was never read, so once the worker's startup probe was older than
_BACKEND_STALE_S the box said "some of what it checks is unknown" until its next restart. The owner's box, release
2026.10.03.4, 97 minutes after the update: `brain.state = stale_ok`, `ok` withheld.

WHAT IS MEASURED, no network and no database (the heartbeats are handed in):
  * every key `probe_backend()` can return, read from its own source, is a component health reads
  * box_tools and core.health read ONE tuple, so the two can never drift again
  * a ChatGPT box with a fresh `probe:codex` and an old startup probe is ok, not stale_ok
  * a ChatGPT box whose codex probe failed reads fail, and one with only an old startup probe still reads stale_ok
"""
from __future__ import annotations

import os
import pathlib
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
T = pathlib.Path(tempfile.mkdtemp(prefix="health_backends_"))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")

from core import box_tools, health  # noqa: E402

_failed = 0


def ok(label: str, cond: bool, detail=None) -> None:
    global _failed
    print(("  PASS  " if cond else "  FAIL  ") + label + ("" if cond else f"   -> {detail!r}"))
    if not cond:
        _failed += 1


def _at(seconds_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


print("\nEvery backend the watchdog probes is read by the health report\n")
src = (ROOT / "core" / "watchdog.py").read_text()
body = src[src.index("def probe_backend("):src.index("\n\n\n", src.index("def probe_backend("))]
keys = set(re.findall(r'return \(?"([a-z_]+)",', body))
ok("probe_backend() returns the keys claude_code, codex and anthropic_api (read from its source)",
   keys == {"claude_code", "codex", "anthropic_api"}, keys)
ok("every one of them is a component the health report reads", all(f"probe:{k}" in health.BACKEND_COMPONENTS
                                                                    for k in keys), health.BACKEND_COMPONENTS)
ok("box_tools reads the very tuple core.health does", box_tools._BACKEND_COMPONENTS is health.BACKEND_COMPONENTS,
   box_tools._BACKEND_COMPONENTS)

print("\nA box signed in to ChatGPT, 97 minutes after a restart (the owner's box, 2026-10-04)\n")
old_start = {"ts": _at(97 * 60), "status": "ok"}
got = box_tools._brain({"brain_backend": old_start, "probe:codex": {"ts": _at(25 * 60), "status": "ok"}})
ok("a fresh codex probe makes it ok, not stale_ok", got.get("state") == "ok" and got.get("ok") is True, got)
got = box_tools._brain({"brain_backend": old_start, "probe:codex": {"ts": _at(25 * 60), "status": "fail"}})
ok("a failed codex probe reads fail", got.get("state") == "fail" and got.get("ok") is False, got)
got = box_tools._brain({"brain_backend": old_start})
ok("with no watchdog probe at all, an old startup probe is still stale_ok (the threshold is unchanged)",
   got.get("state") == "stale_ok" and got.get("ok") is None, got)

print("\nALL HEALTH BACKEND CHECKS PASS" if not _failed else f"\n{_failed} HEALTH BACKEND CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
