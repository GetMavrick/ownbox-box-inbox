"""The release verifier refuses every bad release — the refusals ARE the test.

A verifier that accepts a good release proves almost nothing: `git fetch && git checkout "$TAG"`
accepts a good release too, and verifies nothing (OSDev4, 2026-09-13). What makes the verifier real is
that each way of getting a bad release onto a box is attempted here and refused with its own reason.

Builds everything in a temp directory with throwaway keys: a source repository, a trust file with a
root key, an everyday CI key and a retired key, a rogue key the trust file has never heard of, and two
boxes — one that fetched from a remote and one that fetched from a bundle file — so the air-gapped path
is proven to reach the same decisions as the online one. Never touches global git or ssh config.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.release.verify import TRUST_PATH, verify_release, version_of  # noqa: E402

FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


missing = [t for t in ("git", "ssh-keygen") if not shutil.which(t)]
if missing:
    # A SKIP THAT EXITS 0 IS A SUITE THAT PASSED HAVING RUN NOTHING, and CI's loop cannot tell the two
    # apart (OSDev5 hit exactly that reproducing a red on a container without ssh-keygen). Outside CI a
    # missing tool is a skip; in CI the tools are part of the contract, so it is a failure.
    print(("FAIL" if os.environ.get("CI") else "SKIP") + f": {', '.join(missing)} not found; nothing here was tested")
    raise SystemExit(1 if os.environ.get("CI") else 0)

T = Path(tempfile.mkdtemp())
SRC = T / "src"
ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}


def run(*args, cwd=None, env=None, check=True) -> subprocess.CompletedProcess:
    r = subprocess.run(list(args), cwd=cwd, env=env or ENV, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{args}: {r.stderr.strip()}")
    return r


def key(name: str) -> tuple[Path, str]:
    p = T / name
    run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(p))
    return p, " ".join((T / f"{name}.pub").read_text().split()[:2])


ROOT_KEY, ROOT_PUB = key("root_active")
CI_KEY, CI_PUB = key("ci")
RETIRED_KEY, RETIRED_PUB = key("retired")
ROGUE_KEY, _ = key("rogue")

TRUST = (f'root-active@ownbox namespaces="git" {ROOT_PUB}\n'
         f'ci@ownbox namespaces="git" {CI_PUB}\n'
         f'retired@ownbox namespaces="git",valid-before="20260101" {RETIRED_PUB}\n')

GIT = ["git", "-c", "user.name=release", "-c", "user.email=release@ownbox.test", "-c", "gpg.format=ssh"]


def git(*args, cwd=SRC, env=None, check=True):
    return run(*GIT, *args, cwd=cwd, env=env, check=check)


def commit(message: str, files: dict[str, str]) -> str:
    for rel, text in files.items():
        (SRC / rel).parent.mkdir(parents=True, exist_ok=True)
        (SRC / rel).write_text(text)
    git("add", "-A")
    git("commit", "-q", "-m", message)
    return git("rev-parse", "HEAD").stdout.strip()


def signed_tag(name: str, keyfile: Path, target: str = "HEAD", env=None) -> None:
    git("-c", f"user.signingkey={keyfile}", "tag", "-s", name, "-m", name, target, env=env)


def trust_file_at(repo: Path, tag: str) -> Path:
    """The pinned trust file a box would hold: the one from its INSTALLED release."""
    out = T / f"trust-{tag.replace('/', '_')}"
    out.write_text(run("git", "-C", str(repo), "show", f"refs/tags/{tag}^{{commit}}:{TRUST_PATH}").stdout)
    return out


SRC.mkdir()
git("init", "-q")
c1 = commit("first release", {TRUST_PATH: TRUST, "app.py": "print('v1')\n"})
signed_tag("release/2026.09.15.1", CI_KEY)
c2 = commit("second release", {"app.py": "print('v2')\n"})
signed_tag("release/2026.09.16.1", CI_KEY)
INSTALLED = "release/2026.09.16.1"
PINNED = trust_file_at(SRC, INSTALLED)

print("\n— the good release, and the naming rule —")
ok("version_of parses a release tag", version_of("release/2026.09.16.1") == (2026, 9, 16, 1))
ok("version_of refuses anything else", version_of("v1.0") is None and version_of("main") is None)
c3 = commit("third release", {"app.py": "print('v3')\n"})
signed_tag("release/2026.09.17.1", CI_KEY)
v = verify_release(SRC, "release/2026.09.17.1", PINNED, current_tag=INSTALLED)
ok("a newer release signed by a trusted key is accepted", v.ok and v.reason == "ok", str(v))
ok("...and names who signed it and the commit it installs",
   v.principal == "ci@ownbox" and v.commit == c3, str(v))

print("\n— every way a bad release reaches a box, refused —")
signed_tag("v9.9.9", CI_KEY)
v = verify_release(SRC, "v9.9.9", PINNED, current_tag=INSTALLED)
ok("a signed tag outside release/* is refused (a branch or stray tag is never installable)",
   not v.ok and v.reason == "bad_tag_name", str(v))

git("tag", "release/2026.09.18.1")
v = verify_release(SRC, "release/2026.09.18.1", PINNED, current_tag=INSTALLED)
ok("a lightweight tag is refused", not v.ok and v.reason == "not_annotated", str(v))

git("tag", "-a", "release/2026.09.18.2", "-m", "no signature")
v = verify_release(SRC, "release/2026.09.18.2", PINNED, current_tag=INSTALLED)
ok("an annotated tag with no signature is refused", not v.ok and v.reason == "unsigned", str(v))

signed_tag("release/2026.09.18.3", ROGUE_KEY)
v = verify_release(SRC, "release/2026.09.18.3", PINNED, current_tag=INSTALLED)
ok("a tag signed by a key the trust file has never heard of is refused",
   not v.ok and v.reason == "untrusted_signer", str(v))

old_object = git("rev-parse", "refs/tags/release/2026.09.15.1").stdout.strip()
git("update-ref", "refs/tags/release/2026.12.31.9", old_object)
v = verify_release(SRC, "release/2026.12.31.9", PINNED, current_tag=INSTALLED)
ok("an OLD validly-signed release replayed under a NEWER ref name is refused by the name inside "
   "the signature", not v.ok and v.reason == "name_mismatch", str(v))

raw = run("git", "-C", str(SRC), "cat-file", "tag", "refs/tags/release/2026.09.17.1").stdout
tampered = raw.replace(f"object {c3}", f"object {c1}", 1)
assert tampered != raw, "the tamper must actually change the object line"
evil = subprocess.run(["git", "-C", str(SRC), "hash-object", "-t", "tag", "-w", "--stdin"],
                      input=tampered, capture_output=True, text=True, env=ENV).stdout.strip()
git("update-ref", "refs/tags/release/2026.09.17.1", evil)
v = verify_release(SRC, "release/2026.09.17.1", PINNED, current_tag=INSTALLED)
ok("a validly signed tag REWRITTEN afterwards to point at different code is refused — the name and the "
   "signer still match, only the signature can catch it", not v.ok and v.reason == "signature_invalid", str(v))
git("tag", "-d", "release/2026.09.17.1")
signed_tag("release/2026.09.17.1", CI_KEY, target=c3)

v = verify_release(SRC, "release/2026.09.15.1", PINNED, current_tag=INSTALLED)
ok("a genuinely older signed release is refused as a downgrade", not v.ok and v.reason == "downgrade", str(v))
v = verify_release(SRC, "release/2026.09.15.1", PINNED, current_tag=INSTALLED, allow_downgrade=True)
ok("...and allowed only when a human passes allow_downgrade", v.ok, str(v))

backdated = {**ENV, "GIT_COMMITTER_DATE": "2025-06-01T12:00:00Z"}
signed_tag("release/2026.09.18.4", RETIRED_KEY, env=backdated)
v = verify_release(SRC, "release/2026.09.18.4", PINNED, current_tag=INSTALLED)
ok("a RETIRED key (valid-before 2026-01-01) is refused even when the tag is backdated into its "
   "window — verification runs at the box's clock", not v.ok and v.reason == "untrusted_signer", str(v))
g = run(*GIT, "-c", f"gpg.ssh.allowedSignersFile={PINNED}", "verify-tag", "release/2026.09.18.4",
        cwd=SRC, check=False)
print(f"  info plain `git verify-tag` on that backdated tag: exit {g.returncode}")

v = verify_release(SRC, "release/2026.09.17.1", PINNED, current_tag=INSTALLED,
                   now=datetime(2025, 6, 1, tzinfo=timezone.utc))
ok("control: the same clock rule does not break a key that has no window", v.ok, str(v))

print("\n— the trust file can only be changed by a root key —")
c4 = commit("rotate: add a second everyday key", {TRUST_PATH: TRUST + f'ci2@ownbox namespaces="git" {ROGUE_KEY.with_suffix(".pub").read_text().split()[0]} {ROGUE_KEY.with_suffix(".pub").read_text().split()[1]}\n'})
signed_tag("release/2026.09.19.1", CI_KEY)
v = verify_release(SRC, "release/2026.09.19.1", PINNED, current_tag=INSTALLED)
ok("a release that changes the trust file, signed by the everyday key, is refused — a stolen CI key "
   "cannot promote itself or a friend", not v.ok and v.reason == "trust_change_needs_root", str(v))
signed_tag("release/2026.09.19.2", ROOT_KEY)
v = verify_release(SRC, "release/2026.09.19.2", PINNED, current_tag=INSTALLED)
ok("the same change signed by a root key is accepted", v.ok and v.principal == "root-active@ownbox", str(v))

shadow = f'root-shadow@ownbox namespaces="git" {CI_PUB}\n'
for label, text in (
        ("root line FIRST — the order ssh-keygen would have resolved as root", shadow + PINNED.read_text()),
        ("root line last", PINNED.read_text() + shadow),
        ("both names on one line", PINNED.read_text().replace("ci@ownbox ", "root-shadow@ownbox,ci@ownbox ", 1))):
    amb = T / "trust-ambiguous"
    amb.write_text(text)
    v = verify_release(SRC, "release/2026.09.19.1", amb, current_tag=INSTALLED)
    ok(f"the everyday key ALSO listed as a root principal is refused ({label})",
       not v.ok and v.reason == "untrusted_signer" and "one key, one role" in v.detail, str(v))

print("\n— a box never installs over hand edits —")
(SRC / "app.py").write_text("print('edited on the box')\n")
v = verify_release(SRC, "release/2026.09.19.2", PINNED, current_tag=INSTALLED)
ok("a tracked file edited by hand refuses the install", not v.ok and v.reason == "dirty_tree", str(v))
git("checkout", "--", "app.py")

# A BUYER'S OWN MACHINE NEVER STOPS AN UPDATE (#1472; OSDev1 asked for this on 2026-09-23). It is
# untracked, under my/machines/, and neither the dirty-tree check nor the collision check may count
# it. Measured through the real verify_release on a signed tag, not a helper.
mine = SRC / "my" / "machines" / "acme"
mine.mkdir(parents=True)
(mine / "__init__.py").write_text("# the buyer's own machine\n")
(mine / "machine.yaml").write_text("name: acme\n")
v = verify_release(SRC, "release/2026.09.19.2", PINNED, current_tag=INSTALLED)
ok("A MACHINE THE BUYER BUILT IN my/machines/ DOES NOT REFUSE THE UPDATE", v.ok, str(v))
shutil.move(str(SRC / "my"), str(T / "my-machines-kept"))   # stepped aside, not deleted

print("\n— online and air-gapped take the identical path —")
bundle = T / "releases.bundle"
git("bundle", "create", str(bundle), "--all")
boxes = {}
for how, source in (("remote", str(SRC)), ("bundle", str(bundle))):
    box = T / f"box-{how}"
    run("git", "init", "-q", str(box))
    run("git", "-C", str(box), "fetch", "-q", source, "refs/tags/*:refs/tags/*")
    boxes[how] = box
cases = ["release/2026.09.17.1", "release/2026.09.18.2", "release/2026.09.18.3",
         "release/2026.12.31.9", "release/2026.09.19.1"]
for tag in cases:
    via_remote = verify_release(boxes["remote"], tag, PINNED, current_tag=INSTALLED)
    via_bundle = verify_release(boxes["bundle"], tag, PINNED, current_tag=INSTALLED)
    ok(f"{tag}: the bundle box reaches the same verdict as the remote box ({via_remote.reason})",
       (via_remote.ok, via_remote.reason) == (via_bundle.ok, via_bundle.reason),
       f"remote={via_remote.reason} bundle={via_bundle.reason}")

print()
shutil.rmtree(T, ignore_errors=True)
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
