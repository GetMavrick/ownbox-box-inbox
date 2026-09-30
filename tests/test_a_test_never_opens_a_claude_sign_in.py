"""A test never starts the real Claude sign-in (core/claude_login.py).

Owner, 2026-09-30: a Claude authorize page opened in his browser "20 to 30 times a day". Every local test
run that touched the sign-in found the REAL `claude` on his Mac's PATH and ran `claude setup-token`,
which opens the browser. CI has no `claude`, so it never showed there.

  · under a test, the real, installed `claude` is treated as absent, the way CI sees it;
  · a fake `claude` a test writes into a temporary folder still runs, so sign-in tests keep working;
  · outside a test, nothing changes.

Run: python tests/test_a_test_never_opens_a_claude_sign_in.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")

import shutil  # noqa: E402

from core import claude_login  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


real_which = shutil.which
fake_dir = tempfile.mkdtemp()
try:
    shutil.which = lambda name, *a, **k: "/Users/someone/.local/bin/claude" if name == "claude" else None
    ok("under a test, the real installed claude counts as absent", claude_login.cli_present() is False)
    try:
        claude_login.start()
        ok("...so starting a sign-in is refused, as it is in CI", False)
    except claude_login.LoginError as e:
        ok("...so starting a sign-in is refused, as it is in CI", "not installed" in str(e), str(e))
    shutil.which = lambda name, *a, **k: os.path.join(fake_dir, "claude") if name == "claude" else None
    ok("a fake claude a test wrote into a temporary folder still counts", claude_login.cli_present() is True)
    os.environ.pop("AIOS_HERMETIC_TEST")
    shutil.which = lambda name, *a, **k: "/usr/local/bin/claude" if name == "claude" else None
    ok("outside a test, nothing changes: the installed claude is used", claude_login.cli_present() is True)
finally:
    shutil.which = real_which
    os.environ["AIOS_HERMETIC_TEST"] = "1"

print()
if _failed:
    print(f"{_failed} FAILED")
    sys.exit(1)
print("all passed")
