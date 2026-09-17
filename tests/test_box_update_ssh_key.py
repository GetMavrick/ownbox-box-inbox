"""A sold box updates with ITS OWN key; the operator's box keeps its own (scripts/box_update.sh).

MEASURED 2026-09-17 on a box built from golden image v8: the daily update failed every time —

    Warning: Identity file /root/.ssh/id_ed25519 not accessible: No such file or directory.
    Host key verification failed.
    DEPLOY ABORTED: the release check could not run (above). Nothing was installed.

box_update.sh exported GIT_SSH_COMMAND unconditionally with /root/.ssh/id_ed25519, a key only the operator's
box has, and GIT_SSH_COMMAND overrides the repository's core.sshCommand — which scripts/box_update_key.sh sets
on every sold box to its own read-only deploy key and GitHub's pinned host key. So no box we sell could ever
install a release: no fix, no security patch, ever. Every clone-check had asserted the update TIMER was
enabled; none had run the update.

This runs the script's own key-selection block — extracted between its markers, not re-typed — against two
real git repositories: one shaped like a sold box (core.sshCommand set) and one like the operator's checkout
(none). A copy of the logic would pass while the script regressed; the block itself cannot.

Run: python tests/test_box_update_ssh_key.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "box_update.sh"
FAILS: list[str] = []
OPERATOR_CMD = "ssh -i /root/.ssh/id_ed25519 -o IdentitiesOnly=yes"


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


src = SCRIPT.read_text()
m = re.search(r"# >>> ssh-key-selection[^\n]*\n(.*?)# <<< ssh-key-selection", src, re.S)
ok("the key-selection block is marked in box_update.sh, so this suite runs the real thing", m is not None)
if m is None:
    print(f"\n{len(FAILS)} FAILED"); sys.exit(1)
block = m.group(1)


def selected_ssh_command(repo: Path) -> str:
    """Run the script's block against `repo` (standing in for /opt/aios) and report what git would use."""
    code = block.replace("/opt/aios", str(repo)) + '\nprintf %s "${GIT_SSH_COMMAND-UNSET}"\n'
    env = {k: v for k, v in os.environ.items() if k != "GIT_SSH_COMMAND"}
    r = subprocess.run(["bash", "-euo", "pipefail", "-c", code], capture_output=True, text=True, env=env, timeout=30)
    return r.stdout if r.returncode == 0 else f"ERROR rc={r.returncode}: {r.stderr[-200:]}"


def repo(with_ssh_command: bool) -> Path:
    d = Path(tempfile.mkdtemp()) / "aios"
    d.mkdir()
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    if with_ssh_command:
        subprocess.run(["git", "-C", str(d), "config", "core.sshCommand",
                        "ssh -i /var/lib/aios/update_key -o IdentitiesOnly=yes "
                        "-o UserKnownHostsFile=/opt/aios/trust/github_known_hosts -o StrictHostKeyChecking=yes"],
                       check=True)
    return d


print("\n— a sold box: its own deploy key and pinned host key must win —")
got = selected_ssh_command(repo(with_ssh_command=True))
ok("GIT_SSH_COMMAND is NOT exported, so the box's own core.sshCommand is what git uses",
   got == "UNSET", got)

print("\n— the operator's box: no core.sshCommand, so it keeps the key it always used —")
got = selected_ssh_command(repo(with_ssh_command=False))
ok("GIT_SSH_COMMAND is exported with the operator key", got == OPERATOR_CMD, got)

print("\n— nothing outside the block can put the override back —")
outside = src[:m.start()] + src[m.end():]
ok("no other line in box_update.sh exports GIT_SSH_COMMAND",
   "GIT_SSH_COMMAND" not in outside,
   "\n".join(ln for ln in outside.splitlines() if "GIT_SSH_COMMAND" in ln)[:200])

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
