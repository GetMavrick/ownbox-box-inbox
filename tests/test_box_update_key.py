"""A sold box mints its own update key once, pins GitHub's host keys, and publishes only the public half.

Runs the real scripts/box_update_key.sh against a temporary checkout (AIOS_DIR / AIOS_STATE_DIR point it
away from /opt/aios), and reads the public half back through core.version.update_key, the function
/health calls.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SCRIPT = ROOT / "scripts" / "box_update_key.sh"
FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


missing = [t for t in ("git", "ssh-keygen") if not shutil.which(t)]
if missing:
    # A SKIP THAT EXITS 0 IS A SUITE THAT PASSED HAVING RUN NOTHING (OSDev5, 2026-09-15). Outside CI a
    # missing tool is a skip; in CI the tools are part of the contract, so it is a failure.
    print(("FAIL" if os.environ.get("CI") else "SKIP") + f": {', '.join(missing)} not found; nothing here was tested")
    raise SystemExit(1 if os.environ.get("CI") else 0)

T = Path(tempfile.mkdtemp())
BOX_ORIGIN = "git@github.com:GetMavrick/ownbox-box-inbox.git"


def box(origin: str | None, *, hosts: bool = True) -> tuple[Path, Path]:
    aios, state = Path(tempfile.mkdtemp(dir=T)) / "aios", Path(tempfile.mkdtemp(dir=T)) / "state"
    (aios / "trust").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(aios)], check=True)
    if origin:
        subprocess.run(["git", "-C", str(aios), "remote", "add", "origin", origin], check=True)
    if hosts:
        shutil.copy(ROOT / "trust" / "github_known_hosts", aios / "trust" / "github_known_hosts")
    return aios, state


def run(aios: Path, state: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "AIOS_DIR": str(aios), "AIOS_STATE_DIR": str(state)}
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)


def ssh_command(aios: Path) -> str:
    return subprocess.run(["git", "-C", str(aios), "config", "--get", "core.sshCommand"],
                          capture_output=True, text=True).stdout.strip()


print("\n— the pinned hosts ship in the tree —")
hosts = (ROOT / "trust" / "github_known_hosts").read_text()
ok("trust/github_known_hosts pins GitHub's ed25519 host key", any(l.startswith("github.com ssh-ed25519 ") for l in hosts.splitlines()))
ok("...and names no other host", all(l.startswith("github.com ") for l in hosts.splitlines() if l and not l.startswith("#")))

print("\n— a box that fetches from its box repository —")
aios, state = box(BOX_ORIGIN)
r = run(aios, state)
key, pub = state / "update_key", state / "update_key.pub"
ok("the first run mints the key", r.returncode == 0 and key.is_file() and pub.is_file(), r.stdout + r.stderr)
ok("the private half is readable by its owner only (0600)", stat.S_IMODE(key.stat().st_mode) == 0o600, oct(key.stat().st_mode))
cmd = ssh_command(aios)
ok("git fetches with that key and no other identity", f"-i {key}" in cmd and "IdentitiesOnly=yes" in cmd, cmd)
ok("...only to hosts pinned in the shipped file, never trusting a first answer",
   f"UserKnownHostsFile={aios}/trust/github_known_hosts" in cmd and "StrictHostKeyChecking=yes" in cmd, cmd)
first = pub.read_text()
r = run(aios, state)
ok("a second run NEVER replaces the key: the repository trusts the first public half", r.returncode == 0 and pub.read_text() == first, r.stdout)

print("\n— what /health publishes —")
os.environ["AIOS_UPDATE_KEY_PUB"] = str(pub)
from core import version  # noqa: E402

published = version.update_key()
ok("the public half, type and key only", published == " ".join(first.split()[:2]) and published.startswith("ssh-ed25519 "), str(published))
ok("...never the private half", "PRIVATE" not in (published or "") and key.read_text().strip() not in (published or ""))
ok("status() carries it for /health", version.status().get("update_key") == published)
os.environ["AIOS_UPDATE_KEY_PUB"] = str(T / "absent.pub")
ok("a box that never minted one reports None, not an error", version.update_key() is None)
junk = T / "junk.pub"; junk.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n")
os.environ["AIOS_UPDATE_KEY_PUB"] = str(junk)
ok("a file that is not an ed25519 public key is never published", version.update_key() is None)

print("\n— boxes this must not touch —")
for origin, why in (("https://github.com/GetMavrick/AIOS.git", "a monorepo checkout (our own box)"),
                    ("git@github.com:SomeoneElse/ownbox-box-inbox.git", "a repository outside GetMavrick"),
                    (None, "an unpacked archive with no origin")):
    a, s = box(origin)
    r = run(a, s)
    ok(f"{why}: nothing minted, nothing configured", r.returncode == 0 and not (s / "update_key").exists() and not ssh_command(a),
       r.stdout + r.stderr)
a, s = box(BOX_ORIGIN, hosts=False)
r = run(a, s)
ok("a box repository checkout WITHOUT the pinned hosts refuses, and mints nothing",
   r.returncode != 0 and not (s / "update_key").exists(), r.stdout + r.stderr)

print("\n— an image never carries one —")
prep = (ROOT / "scripts" / "image_prepare.sh").read_text()
sys_paths = next(l for l in prep.splitlines() if l.startswith("SYS_PATHS="))
ok("image_prepare scrubs, and --verify refuses, both halves of the update key",
   "/var/lib/aios/update_key " in sys_paths + " " and "/var/lib/aios/update_key.pub" in sys_paths, sys_paths)
ok("bootstrap mints it right after install.sh", "bash scripts/install.sh" in (boot := (ROOT / "scripts" / "bootstrap.sh").read_text())
   and boot.index("bash scripts/box_update_key.sh") > boot.index("bash scripts/install.sh"))

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
