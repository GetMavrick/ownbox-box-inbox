"""The image preparer's REFUSAL is the product — so the refusal is what this tests.

docs/PLAN_GOLDEN_IMAGE_CUT.md §2: an image is a bootstrap stopped after step 2, and everything
bootstrap writes after that is identity. A snapshot taken one step too late clones ONE box's
identity onto every customer: the same UNSUB_SIGNING_KEY signing every unsubscribe link, the same
bearer opening every /dispatch, one box_id for a fleet, one SSH host fingerprint.

`scripts/image_prepare.sh --verify <root>` is the gate that stops that, and a gate nobody has
watched fail is not a gate. Each scenario here plants ONE identity file in an otherwise clean tree
and asserts the verify REFUSES, then removes it and asserts it passes — so the check is known to
discriminate rather than to refuse everything or pass everything.

Runs anywhere: --verify of a plain directory does the tree half only (the system half needs the
droplet's real paths). Does not need root, systemd, or a droplet.
"""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "image_prepare.sh"
FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


missing = [t for t in ("git", "ssh-keygen") if not shutil.which(t)]
if missing:
    # Outside CI a missing tool is a skip; in CI it is a failure, never a green suite that ran nothing.
    print(("FAIL" if os.environ.get("CI") else "SKIP") + f": {', '.join(missing)} not found; nothing here was tested")
    raise SystemExit(1 if os.environ.get("CI") else 0)

T = pathlib.Path(tempfile.mkdtemp())
GENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}


def _key(name: str) -> tuple[pathlib.Path, str]:
    k = T / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(k)], check=True)
    return k, " ".join((T / f"{name}.pub").read_text().split()[:2])


CI_KEY, CI_PUB = _key("ci")
ROGUE_KEY, _ = _key("rogue")
TRUST = f'ci@ownbox namespaces="git" {CI_PUB}\n'
RELEASE = "release/2026.09.20.1"
BOX_ORIGIN = "git@github.com:GetMavrick/ownbox-box-inbox.git"


def g(d: pathlib.Path, *args: str, key: pathlib.Path = CI_KEY) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "gpg.format=ssh",
                           "-c", f"user.signingkey={key}", "-c", "advice.detachedHead=false", "-C", str(d), *args],
                          env=GENV, capture_output=True, text=True, check=True)


def verify(root: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT), "--verify", str(root)], capture_output=True, text=True,
                          env={**os.environ, "IMAGE_PYTHON": sys.executable})


def clean_tree() -> pathlib.Path:
    """The smallest thing --verify calls an image: a signed checkout of a box repository at a release
    tag, with its trust file and verifier committed, plus what preparing adds untracked (a venv, a stamp)."""
    d = pathlib.Path(tempfile.mkdtemp()) / "aios"
    (d / "scripts").mkdir(parents=True)
    (d / "scripts" / "install.sh").write_text("#!/usr/bin/env bash\n")
    (d / "trust").mkdir()
    (d / "trust" / "allowed_signers").write_text(TRUST)
    for rel in ("core/__init__.py", "core/release/__init__.py", "core/release/verify.py"):
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, d / rel)
    subprocess.run(["git", "init", "-q", str(d)], env=GENV, check=True)
    g(d, "add", "-A")
    g(d, "commit", "-qm", "a release")
    g(d, "tag", "-s", RELEASE, "-m", RELEASE)
    g(d, "remote", "add", "origin", BOX_ORIGIN)
    (d / ".venv" / "bin").mkdir(parents=True)
    py = d / ".venv" / "bin" / "python"
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    (d / "image.json").write_text('{"box_type": "customer_voice", "release": "%s"}' % RELEASE)
    return d


print("\n— a clean tree is an image —")
base = clean_tree()
r = verify(base)
ok("a tree with a venv and a stamp passes", r.returncode == 0, r.stdout + r.stderr)

print("\n— each identity file, alone, is refused —")
# One file at a time, because a check that only fires on the whole set is a check that misses the
# one file somebody forgot. The message must NAME the path: a refusal you cannot act on is noise.
for rel, make in (
    (".env", lambda p: p.write_text("DISPATCH_BEARER_TOKEN=abc\n")),          # the minted secrets
    ("licence.json", lambda p: p.write_text('{"buyer": "Acme"}')),            # one buyer
    ("aios.db", lambda p: p.write_bytes(b"SQLite format 3\x00")),             # one box's life
    ("data/seeds/x.json", lambda p: p.write_text("{}")),                      # tenant data
    ("backups/b.db", lambda p: p.write_bytes(b"x")),                          # one box's backups
    ("my/autopilot_armed.json", lambda p: p.write_text("{}")),                # AN ARMED IMAGE
    ("wall", lambda p: p.write_text("notes")),
    (".x_articles_token.json", lambda p: p.write_text("{}")),
):
    d = clean_tree()
    target = d / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    make(target)
    r = verify(d)
    named = rel.split("/")[0] in r.stdout
    ok(f"{rel} alone → refused, and the refusal names it", r.returncode != 0 and named,
       f"rc={r.returncode} out={r.stdout.strip()[:160]}")

print("\n— a database anywhere under the tree, not just at the top —")
d = clean_tree()
(d / "data").mkdir()
(d / "data" / "aios.db").write_bytes(b"SQLite format 3\x00")
ok("a database in a subdirectory is found", verify(d).returncode != 0)

print("\n— the release trust root is part of an image —")
d = clean_tree()
(d / "trust" / "allowed_signers").unlink()
r = verify(d)
ok("an image without trust/allowed_signers is refused — a clone could never verify an update",
   r.returncode != 0 and "trust/allowed_signers" in r.stdout, r.stdout.strip()[:160])

print("\n— macOS metadata from a Mac-built tarball —")
d = clean_tree()
(d / "core").mkdir(exist_ok=True)
(d / "core" / "._state.py").write_bytes(bytes.fromhex("0005160700020000") + b"\xa3" * 40)
r = verify(d)
ok("an AppleDouble ._*.py file under the tree is refused, and the refusal names it",
   r.returncode != 0 and "._state.py" in r.stdout, r.stdout.strip()[:200])
(d / "core" / "._state.py").unlink()
ok("...and the same tree passes once it is gone", verify(d).returncode == 0)
bootstrap_src = (SCRIPT.parent / "bootstrap.sh").read_text()
ok("the scrub deletes ._* files, so an image never carries them",
   "-name '._*' -type f -delete" in SCRIPT.read_text())
ok("bootstrap removes ._* files before install.sh runs, so a Mac-built handover archive still boots",
   "-name '._*' -type f -delete" in bootstrap_src
   and bootstrap_src.index("-name '._*' -type f -delete") < bootstrap_src.index("bash scripts/install.sh"))

print("\n— an image is a signed checkout of its box repository (v2) —")
d = clean_tree()
shutil.rmtree(d / ".git")
r = verify(d)
ok("a tree that is not a checkout is refused: a clone of it could never take an update",
   r.returncode != 0 and "not a checkout" in r.stdout, r.stdout.strip()[:200])
d = clean_tree()
g(d, "remote", "set-url", "origin", "https://github.com/GetMavrick/AIOS.git")
r = verify(d)
ok("an image following the monorepo is refused", r.returncode != 0 and "not a box repository" in r.stdout, r.stdout.strip()[:200])
d = clean_tree()
g(d, "remote", "set-url", "origin", "https://x-access-token:ghs_SECRET@github.com/GetMavrick/ownbox-box-inbox.git")
r = verify(d)
ok("a credential inside a remote URL is refused, since every clone would hold it", r.returncode != 0 and "credential" in r.stdout, r.stdout.strip()[:200])
ok("...and the refusal never prints the credential", "ghs_SECRET" not in r.stdout + r.stderr)
d = clean_tree()
(d / "scripts" / "install.sh").write_text("#!/usr/bin/env bash\necho later\n")
g(d, "commit", "-qam", "not a release")
r = verify(d)
ok("HEAD past its release tag is refused: an image is cut from a release, never a branch",
   r.returncode != 0 and "not on a release tag" in r.stdout, r.stdout.strip()[:200])
d = clean_tree()
g(d, "tag", "-d", RELEASE)
g(d, "tag", RELEASE)                                    # lightweight: no signature at all
r = verify(d)
ok("an unsigned release tag is refused", r.returncode != 0 and "does not verify" in r.stdout, r.stdout.strip()[:200])
d = clean_tree()
g(d, "tag", "-d", RELEASE)
g(d, "tag", "-s", RELEASE, "-m", RELEASE, key=ROGUE_KEY)
r = verify(d)
ok("a release tag signed by a key the image does not trust is refused", r.returncode != 0 and "does not verify" in r.stdout,
   r.stdout.strip()[:200])
d = clean_tree()
(d / "scripts" / "install.sh").write_text("#!/usr/bin/env bash\necho edited\n")
r = verify(d)
ok("a tracked file edited after checkout is refused", r.returncode != 0 and "edited after checkout" in r.stdout, r.stdout.strip()[:200])
prep = SCRIPT.read_text()
ok("prepare refuses a tree that is not at a release tag, before touching the droplet",
   prep.index("is not a checkout at a release tag") < prep.index("== 1/5 system packages"))
ok("the stamp and the install marker both name the release the image is",
   '"release": "$RELEASE"' in prep and "> /var/lib/aios/release" in prep)

print("\n— the halves of an image that must be PRESENT —")
for rel, why in ((".venv/bin/python", "no venv means the clone pays for it on first boot"),
                 ("image.json", "a clone must be able to say which image it came from"),
                 ("scripts/install.sh", "an image without its box is not an image")):
    d = clean_tree()
    p = d / rel
    p.unlink()
    r = verify(d)
    ok(f"missing {rel} → refused ({why})", r.returncode != 0, r.stdout.strip()[:160])

print("\n— the scrub list and the delete cannot drift apart —")
src = SCRIPT.read_text()
tree_paths = re.search(r"TREE_PATHS=\(([^)]*)\)", src)
ok("TREE_PATHS is declared once and used by both the delete and the check",
   tree_paths is not None
   and src.count("TREE_PATHS[@]") >= 2                      # verify() and the scrub loop
   and "my" in tree_paths.group(1) and ".env" in tree_paths.group(1))
ok("the scrub empties machine-id and removes the host keys",
   ": > /etc/machine-id" in src and "ssh_host_*_key" in src)
ok("cloud-init is cleaned, so the clone gets its own key and hostname",
   "cloud-init clean" in src)
ok("it refuses to run against a live box (a licence or a database present)",
   re.search(r"for guard in licence\.json aios\.db", src) is not None)
ok("it never enables a unit — an image boots inert",
   "systemctl enable" not in src)

print("\n— every shipped unit file reaches the image —")
# install_services.sh installs a named list and ENABLES it; the preparer installs the same files
# and enables none. A unit added there and forgotten here is missing from every image we ever cut.
services = ROOT / "scripts" / "install_services.sh"
if services.exists():
    named = set(re.findall(r"deploy/(aios-[\w.-]+\.(?:service|timer))", services.read_text()))
    on_disk = {p.name for p in (ROOT / "deploy").glob("aios-*.service")} | \
              {p.name for p in (ROOT / "deploy").glob("aios-*.timer")}
    ok("the preparer installs by glob, so it cannot miss one install_services.sh lists",
       "deploy/aios-*.service" in src and "deploy/aios-*.timer" in src, src[:0])
    ok(f"the glob covers everything install_services.sh names ({len(named)} units)",
       named <= on_disk, str(sorted(named - on_disk)))

print("\n— Caddy's home: emptied, never deleted —")
# Measured 2026-09-15 on image v2, which deleted /var/lib/caddy: every clone logged "failed storage check: mkdir
# /var/lib/caddy: permission denied" and no box built from it ever got a certificate.
src = SCRIPT.read_text()
fn = re.search(r"^caddy_home_problem\(\) \{.*?^\}", src, re.S | re.M).group(0)
me = subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()


def caddy_home(where, user):
    return subprocess.run(["bash", "-c", fn + '\ncaddy_home_problem "$1" "$2"', "_", str(where), user],
                          capture_output=True, text=True).stdout.strip()


home = pathlib.Path(tempfile.mkdtemp()) / "caddy"
r = caddy_home(home, me)
ok("a missing Caddy home is refused, and the refusal says a clone would never get a certificate",
   "missing" in r and "certificate" in r, r)
home.mkdir()
ok("an empty Caddy home owned by Caddy's user passes", caddy_home(home, me) == "", caddy_home(home, me))
(home / ".local/share/caddy/certificates").mkdir(parents=True)
(home / ".local/share/caddy/certificates/box.crt").write_text("not for a clone")
r = caddy_home(home, me)
ok("a certificate left inside is refused, and the refusal names a path", "Caddy state remains" in r and str(home) in r, r)
shutil.rmtree(home / ".local")
r = caddy_home(home, "not-" + me)
ok("a Caddy home owned by anyone but Caddy's user is refused", "belongs to" in r, r)
ok("the scrub list no longer deletes /var/lib/caddy",
   "/var/lib/caddy" not in re.search(r"^SYS_PATHS=\((.*)\)$", src, re.M).group(1))
ok("the scrub empties it and keeps it, owned by Caddy's user",
   'find "$CADDY_HOME" -mindepth 1 -delete' in src
   and 'install -d -o "$CADDY_USER" -g "$CADDY_USER" -m 0700 "$CADDY_HOME"' in src)
ok("the droplet half of --verify checks it",
   'caddy_home_problem "$CADDY_HOME" "$CADDY_USER"' in src[src.index("verify() {"):src.index("# MACOS METADATA")])
ex = (ROOT / "scripts" / "expose.sh").read_text()
ok("expose.sh makes Caddy's home, if missing, before it starts Caddy",
   "install -d -o caddy -g caddy -m 0700 /var/lib/caddy" in ex
   and ex.index("install -d -o caddy") < ex.index("systemctl enable --now caddy"))

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
