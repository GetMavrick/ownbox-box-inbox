"""A box with no swap dies rather than slows, and no box we sell had any.

WHAT WAS TRUE UNTIL THIS. The owner's own box carries a 2G swapfile dated 2026-06-10 — he made it
by hand. Nothing in `bootstrap.sh`, `image_prepare.sh` or the image ever created one, so every box
ever delivered to a customer ran with no cushion at all. Measured on a $6 droplet (s-1vcpu-1gb)
built from the current image, 2026-09-19:

    RAM 961MB total, 487MB used at rest, 473MB available, swap 0
    `claude` peaks at 224MB starting up and FAILING AUTH — before it drafts a single word

Roughly 250MB of headroom on the one job the box exists to do, and no swap means the ceiling is
the OOM killer choosing a process, not a slow minute. The same $6 box with 2G of swap added kept
answering /claim with 200 and the peak was unchanged.

WHAT THIS SUITE PINS, and why each would ship a broken box:
  · swap is created at FIRST BOOT, never baked into the image — a 2G file in the snapshot adds 2G
    to every image and to its `min_disk_size`, and that number is exactly what decides whether a
    box can be a $6 droplet at all (50GB floor vs the $6 tier's 25GB disk: measured, refused).
  · it is SKIPPED when the machine already has swap, so the owner's hand-made file survives and a
    re-run never stacks a second one.
  · a machine that cannot make swap still finishes booting. Refusing to build someone's box over a
    cushion is worse than the missing cushion.

Run: python tests/test_every_box_gets_swap.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts/bootstrap.sh"
FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


src = BOOTSTRAP.read_text()

# ── the block, extracted by its own markers so a move fails LOUDLY rather than vacuously ─────────
start = src.find("# ── SWAP, BEFORE ANYTHING ELSE NEEDS IT")
end = src.find('echo "== 1/6 system packages')
ok("the swap block is still in bootstrap.sh, above the first install step",
   start != -1 and end != -1 and start < end, f"start={start} end={end}")
block = src[start:end] if (start != -1 and end > start) else ""


def run_block(*, has_swap: bool) -> tuple[str, bool]:
    """Run the extracted block with stubbed binaries. Returns (output, did it try to make swap)."""
    d = Path(tempfile.mkdtemp())
    bin_ = d / "bin"
    bin_.mkdir()
    (d / "made").write_text("")
    swap_line = "/dev/sda2 partition 2097148 0 -2" if has_swap else ""
    # THE STUB TERMINATES ITS LINE, because the real `swapon` does — and the first version of this
    # test did not, which is how it found a guard that counted lines instead of testing emptiness.
    body = f"printf '%s\\n' \"{swap_line}\"" if has_swap else "true"
    (bin_ / "swapon").write_text(f"#!/bin/sh\n{body}\nexit 0\n")
    (bin_ / "fallocate").write_text(f"#!/bin/sh\necho made >> {d}/made\nexit 0\n")
    (bin_ / "mkswap").write_text(f"#!/bin/sh\necho mkswap >> {d}/made\nexit 0\n")
    (bin_ / "chmod").write_text("#!/bin/sh\nexit 0\n")
    (bin_ / "free").write_text("#!/bin/sh\necho 'Swap: 2047 0 2047'\nexit 0\n")
    for f in bin_.iterdir():
        f.chmod(0o755)
    script = d / "block.sh"
    # /swapfile must not exist for the guard to pass; point it at the sandbox instead of the host's
    script.write_text("set -u\n" + block.replace("/swapfile", str(d / "swapfile"))
                                        .replace("/etc/fstab", str(d / "fstab")))
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}"}
    r = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env, timeout=60)
    return (r.stdout + r.stderr), bool((d / "made").read_text().strip())


print("\ntest_a_box_with_no_swap_gets_some")
out, made = run_block(has_swap=False)
ok("a machine with no swap creates a swapfile", made, out[:200])
ok("...and says so while it boots", "swap" in out.lower(), out[:200])

print("\ntest_a_box_that_already_has_swap_is_left_alone")
out, made = run_block(has_swap=True)
ok("existing swap is never touched — the owner's hand-made file survives", not made, out[:200])
ok("...and nothing is printed about it", "0/6" not in out, out[:200])

print("\ntest_the_image_never_carries_a_swapfile")
# A 2G file in the snapshot raises min_disk_size, which is what decides $6 viability.
prep = (ROOT / "scripts/image_prepare.sh").read_text()
ok("image_prepare.sh does not create swap (it would bloat every snapshot)",
   "mkswap" not in prep and "fallocate" not in prep)

print("\ntest_a_box_that_cannot_make_swap_still_boots")
ok("the failure path continues rather than exiting",
   "continuing without it" in block and "exit 1" not in block, block[-300:])
ok("...and the whole block is guarded, so it never runs twice",
   "swapon --show" in block and "-e /swapfile" in block)

print("\n— and this file cannot silently fall out of CI —")
_wf = ROOT / ".github/workflows/tests.yml"
if _wf.is_file():
    ok("test_every_box_gets_swap is in the workflow's suite list",
       "test_every_box_gets_swap" in _wf.read_text())

print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED: " + "; ".join(FAILS))
sys.exit(1 if FAILS else 0)
