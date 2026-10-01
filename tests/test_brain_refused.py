"""A refused sign-in reads as refused, in the account's own words (core/brain.py, the subscription path).

Measured on the owner's box, 2026-10-01: the Claude CLI exits 1 and still prints its whole JSON answer when the
account refuses the request ("is_error": true, "api_error_status": 401, the reason in "result"). The old handling
read only the output's tail, matched a word in the stats as transient, and cut the reason off, so "Test it now"
said "claude_code transient" about a sign-in that no retry could fix.

  · rc 1 with a JSON 401: RuntimeError "refused (401: <the account's words>)", never RetryableError; secrets scrubbed;
  · a real transient failure with no JSON still retries; a usage limit still pauses;
  · a good answer is unchanged.

Run: python tests/test_brain_refused.py
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
T = pathlib.Path(tempfile.mkdtemp())
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")

from core import brain, state  # noqa: E402

state.init_db()
FAILS: list[str] = []


def ok(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


brain.shutil.which = lambda name: "/usr/local/bin/claude"


def answer(rc, out, err=""):
    brain._run_claude = lambda cmd, timeout: (rc, out, err)


def call():
    return brain._think_claude_code("router", "claude-haiku-4-5-20251001", "Reply with exactly one word: ready",
                                    system=None, cached_context=None, max_tokens=8, job_id=None, timeout=30,
                                    isolated=True)


stats = {"type": "result", "subtype": "success", "is_error": True, "api_error_status": 401, "num_turns": 1,
         "result": "OAuth access token is invalid sk-ant-oat01-" + "Q" * 40,
         "usage": {"input_tokens": 0}, "permission_denials": [], "stats": {"failed": 0, "killed": {"parent": 0}}}
print("\na refused sign-in —")
answer(1, json.dumps(stats))
try:
    call()
    ok("a 401 raises", False)
except brain.RetryableError as e:
    ok("a 401 is never called transient", False, str(e))
except RuntimeError as e:
    ok("rc 1 with a JSON 401 says refused, in the account's own words", "refused the request (401: OAuth access token "
       "is invalid" in str(e) and "needs attention in Set up" in str(e), str(e))
    ok("...with the token scrubbed", "QQQQ" not in str(e))

stats["stats"]["duration_ms"] = 5123            # the number that read as "a 5xx" in the old tail-matching
answer(1, json.dumps(stats))
try:
    call()
except brain.RetryableError as e:
    ok("a timing figure like 5123 in the stats is not a server error", False, str(e))
except RuntimeError:
    ok("a timing figure like 5123 in the stats is not a server error", True)

print("\neverything else as before —")
answer(1, "", "Error: Connection reset by peer")
try:
    call()
    ok("a network hiccup with no JSON still retries", False)
except brain.RetryableError:
    ok("a network hiccup with no JSON still retries", True)
except Exception as e:  # noqa: BLE001
    ok("a network hiccup with no JSON still retries", False, f"{type(e).__name__}: {e}")
answer(0, json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "ready",
                      "usage": {"input_tokens": 3, "output_tokens": 1}}))
ok("a good answer is unchanged", call().strip() == "ready")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    sys.exit(1)
print("all passed")
