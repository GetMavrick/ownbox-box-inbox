"""A box type's release lands in its own repository as a signed tag a box on the previous release accepts.

Real git, real ssh-keygen signatures, the real core/release/verify.py. The export is a small stand-in tree
(the exporter itself is proven by test_box_boots); what is proven here is the publishing: the repository
becomes exactly the export, the tag verifies before anything could be pushed, and every refusal leaves
nothing a caller could push by mistake.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "publish_box.sh"
FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


missing = [t for t in ("git", "ssh-keygen", "rsync") if not shutil.which(t)]
if missing:
    # A SKIP THAT EXITS 0 IS A SUITE THAT PASSED HAVING RUN NOTHING, and CI's loop cannot tell the two
    # apart (OSDev5 hit exactly that reproducing a red on a container without ssh-keygen). Outside CI a
    # missing tool is a skip; in CI the tools are part of the contract, so it is a failure.
    print(("FAIL" if os.environ.get("CI") else "SKIP") + f": {', '.join(missing)} not found; nothing here was tested")
    raise SystemExit(1 if os.environ.get("CI") else 0)

T = Path(tempfile.mkdtemp())
ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null", "PYTHON": sys.executable}


def run(*args, cwd=None, check=True):
    r = subprocess.run([str(a) for a in args], cwd=cwd, env=ENV, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{args}: {r.stderr.strip()}")
    return r


def key(name):
    p = T / name
    run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", p)
    return p, " ".join((T / f"{name}.pub").read_text().split()[:2])


CI_KEY, CI_PUB = key("ci")
ROOT_KEY, ROOT_PUB = key("root")
ROGUE_KEY, _ = key("rogue")
TRUST = f'root-active@ownbox namespaces="git" {ROOT_PUB}\nci@ownbox namespaces="git" {CI_PUB}\n'


def export(files: dict[str, str], *, trust=True) -> Path:
    d = Path(tempfile.mkdtemp(dir=T)) / "box"
    for rel, text in {**({"trust/allowed_signers": TRUST} if trust else {}), **files}.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(text)
    d.mkdir(parents=True, exist_ok=True)
    return d


REPO = T / "box-repo"
run("git", "init", "-q", REPO)
for k, v in (("user.name", "Ownbox release"), ("user.email", "release@ownbox.io"), ("gpg.format", "ssh"),
             ("user.signingkey", CI_KEY)):
    run("git", "-C", REPO, "config", k, v)


def publish(tag, exp, repo=REPO, typ="customer_voice"):
    r = run("bash", SCRIPT, "--type", typ, "--tag", tag, "--repo", repo, "--export-dir", exp, check=False)
    r.why = (r.stdout.strip()[-300:] + " | " + r.stderr.strip()[-300:])   # the verifier's verdict is on stdout
    return r


def content(tag, rel, repo=REPO):
    return run("git", "-C", repo, "show", f"{tag}^{{commit}}:{rel}").stdout


def tree(tag, repo=REPO):
    return sorted(run("git", "-C", repo, "ls-tree", "-r", "--name-only", f"{tag}^{{commit}}").stdout.split())


print("\n— the first release of a box type —")
r = publish("release/2026.09.15.3", export({"app.py": "print('v1')\n", "old.py": "x = 1\n"}))
ok("publishes into an empty repository and says it is ready to push", r.returncode == 0 and "ready to push: release/2026.09.15.3" in r.stdout,
   r.stderr[-300:])
ok("the tag holds exactly the export", tree("release/2026.09.15.3") == ["app.py", "old.py", "trust/allowed_signers"],
   str(tree("release/2026.09.15.3")))
(T / "trust-copy").write_text(TRUST)
v = run(sys.executable, "-m", "core.release.verify", REPO, "release/2026.09.15.3", "--trust", T / "trust-copy", cwd=ROOT, check=False)
ok("a box holding the trust file accepts the tag, independently of the script", v.returncode == 0, v.stdout[-200:] + v.stderr[-200:])

print("\n— the next release —")
r = publish("release/2026.09.16.1", export({"app.py": "print('v2')\n"}))
ok("verified against the PREVIOUS release's trust file, and published", r.returncode == 0 and "previous: release/2026.09.15.3" in r.stdout,
   r.why)
ok("the release holds the NEW content of a file whose size did not change", content("release/2026.09.16.1", "app.py") == "print('v2')\n",
   repr(content("release/2026.09.16.1", "app.py")))
ok("a file the box type dropped is gone from the release (the repository becomes the export)",
   tree("release/2026.09.16.1") == ["app.py", "trust/allowed_signers"], str(tree("release/2026.09.16.1")))
ok("...and the earlier release still has it: history is untouched",
   "old.py" in tree("release/2026.09.15.3"))
r = publish("release/2026.09.16.2", export({"app.py": "print('v2')\n"}))
ok("a release with no change for this type still gets its tag, on the same commit",
   r.returncode == 0 and run("git", "-C", REPO, "rev-parse", "release/2026.09.16.2^{commit}").stdout == run("git", "-C", REPO, "rev-parse", "release/2026.09.16.1^{commit}").stdout,
   r.why)

# THE STALE-FILE RELEASE, forced rather than left to timing. Same size, same modification time, different
# content: rsync's quick check skips exactly this file, and the release would carry the old bytes under
# the new name. Measured on Ubuntu before --checksum: 11 of 25 runs published stale content.
exp = export({"app.py": "print('v3')\n"})
repo_app = REPO / "app.py"
os.utime(exp / "app.py", ns=(repo_app.stat().st_atime_ns, repo_app.stat().st_mtime_ns))
ok("(the trap is set: same size and same mtime as the file already in the repository)",
   (exp / "app.py").stat().st_size == repo_app.stat().st_size and (exp / "app.py").stat().st_mtime_ns == repo_app.stat().st_mtime_ns)
r = publish("release/2026.09.16.3", exp)
ok("a same-size, same-mtime change is STILL published: the tag carries the new bytes",
   r.returncode == 0 and content("release/2026.09.16.3", "app.py") == "print('v3')\n", r.why + " content=" + repr(content("release/2026.09.16.3", "app.py")))

print("\n— refusals leave nothing to push —")
tags_before = run("git", "-C", REPO, "tag", "-l").stdout
r = publish("release/2026.09.16.2", export({"app.py": "print('v3')\n"}))
ok("the same tag again is refused: a published release is never re-cut", r.returncode != 0 and "never re-cut" in r.stderr, r.why)
r = publish("release/2026.09.17.1", export({"app.py": "print('v3')\n"}, trust=False))
ok("an export without trust/allowed_signers is refused", r.returncode != 0 and "trust/allowed_signers" in r.stderr, r.stderr)
ok("...before anything was committed or tagged", run("git", "-C", REPO, "tag", "-l").stdout == tags_before
   and not run("git", "-C", REPO, "status", "--porcelain").stdout)
(REPO / "stray.txt").write_text("hand edit\n")
run("git", "-C", REPO, "add", "stray.txt")
r = publish("release/2026.09.17.1", export({"app.py": "print('v3')\n"}))
ok("a box repository with uncommitted changes is refused", r.returncode != 0 and "uncommitted" in r.stderr, r.stderr)
run("git", "-C", REPO, "rm", "-q", "--cached", "stray.txt"); (REPO / "stray.txt").unlink()

ROGUE = T / "rogue-repo"
shutil.copytree(REPO, ROGUE)
run("git", "-C", ROGUE, "config", "user.signingkey", ROGUE_KEY)
r = publish("release/2026.09.17.1", export({"app.py": "print('evil')\n"}), repo=ROGUE)
ok("a tag signed by a key the box does not trust fails verification, so the script exits non-zero",
   r.returncode != 0 and "untrusted_signer" in (r.stdout + r.stderr), (r.stdout + r.stderr)[-300:])
ok("...and it never printed 'ready to push'", "ready to push" not in r.stdout)

print("\n— arguments —")
for args, why in ((["--type", "voice", "--tag", "release/2026.09.18.1"], "an unknown box type"),
                  (["--type", "customer_voice", "--tag", "v1.0"], "a tag that is not a release name"),
                  (["--type", "customer_voice", "--tag", "release/2026.09.18.1", "--repo", str(T / "nope")], "a repo that is not a git work tree")):
    r = run("bash", SCRIPT, *args, check=False)
    ok(f"{why} is refused", r.returncode == 2, r.stderr)
r = run("bash", SCRIPT, "--type", "customer_voice", "--tag", "release/2099.01.01.1", "--repo", REPO, check=False)
ok("exporting from this checkout refuses a release tag that does not exist here", r.returncode == 1 and "is not a tag" in r.stderr, r.stderr)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
