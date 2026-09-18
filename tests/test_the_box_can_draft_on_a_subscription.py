"""A box that is handed an OAuth token must have the binary that token is for.

WHY THIS SUITE EXISTS. The owner ruled on 2026-09-18 that the AI credential is the CLIENT's choice, and
#1380 routes an `sk-ant-oat...` credential to the `claude_code` backend, which shells out to the `claude`
CLI. Nothing installed that CLI on a customer's box: `scripts/install_claude_code.sh` existed and no boot
path ran it. A buyer would paste their token, the screen would accept it, and the box would draft NOTHING
while reporting the CLI missing — the exact shape of every silent failure this product has been fixing all
week: work done, credential accepted, nothing happens.

TWO CALL SITES, ON PURPOSE, and both are asserted here:
  · `image_prepare.sh` bakes the binary into the golden image, so a customer's box never waits on
    claude.ai at the moment they are watching it start.
  · `bootstrap.sh` installs it at first boot if the image did not carry it — the only path that runs
    again for a box built from an image cut before this shipped.

Run: python tests/test_the_box_can_draft_on_a_subscription.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


installer = ROOT / "scripts" / "install_claude_code.sh"
image = (ROOT / "scripts" / "image_prepare.sh").read_text()
boot = (ROOT / "scripts" / "bootstrap.sh").read_text()

print("\n— the installer itself —")
ok("the installer ships", installer.exists())
src = installer.read_text()
ok("...and is idempotent: it checks for the binary before fetching",
   "command -v claude" in src and "curl -fsSL https://claude.ai/install.sh" in src)
ok("...and exposes it on the PATH systemd units use",
   "/usr/local/bin/claude" in src)

print("\n— every box gets it, by both roads —")
ok("the IMAGE bakes it, so a buyer's first boot does not wait on a download",
   re.search(r"^bash scripts/install_claude_code\.sh", image, re.M) is not None)
ok("FIRST BOOT installs it too, for a box built from an older image",
   re.search(r"^bash scripts/install_claude_code\.sh", boot, re.M) is not None)

print("\n— a failure must not cost the buyer their box —")
for name, text in (("image_prepare.sh", image), ("bootstrap.sh", boot)):
    line = next((l for l in text.split("\n") if l.startswith("bash scripts/install_claude_code.sh")), "")
    ok(f"...{name} tolerates the installer failing", line.endswith("|| true")
       or "|| true" in line, line or "line not found")

print("\n— and it is installed BEFORE the box is declared done —")
ok("bootstrap installs it before the final doctor gate",
   boot.index("install_claude_code.sh") < boot.index("scripts/doctor.py"))

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:")
    for f in FAILS:
        print("   -", f)
    sys.exit(1)
print("ALL OK")
