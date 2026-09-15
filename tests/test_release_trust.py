"""The release trust root and the workflow that signs against it — checked as files, before any box reads them.

trust/allowed_signers decides who may put code on every box we ship, and .github/workflows/release.yml is
the only automated thing that signs. A typo in the first or a reordered step in the second fails in the
field, on a box we cannot reach. So both are held here: the file parses the way ssh-keygen and the
verifier read it, and the workflow cannot publish a tag it has not first verified the way a box would.
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.release.verify import ROOT_PREFIX, _principals_by_key  # noqa: E402

FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


TRUST = ROOT / "trust" / "allowed_signers"
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"

print("\n— trust/allowed_signers —")
ok("the trust file exists", TRUST.is_file())
text = TRUST.read_text() if TRUST.is_file() else ""
entries = [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
ok("it lists at least one key", len(entries) >= 1)
ok("it carries no private key material", "PRIVATE KEY" not in text)
for line in entries:
    ok(f"{line.split()[0]}: scoped to the git namespace, so the key signs releases and nothing else",
       'namespaces="git"' in line, line[:80])
keys = _principals_by_key(TRUST)
ok("every key belongs to exactly one principal — one key, one role",
   keys and all(len(p) == 1 for p in keys.values()), str({k[:12]: sorted(v) for k, v in keys.items()}))
principals = {p for ps in keys.values() for p in ps}
roots = {p for p in principals if p.startswith(ROOT_PREFIX)}
ok("two root keys exist, so losing one never locks every box out of the next trust change",
   {"root-active@ownbox", "root-recovery@ownbox"} <= roots, str(sorted(roots)))
ok("the everyday CI key is present and is NOT a root key, so it can ship code but never change trust",
   "ci@ownbox" in principals and not "ci@ownbox".startswith(ROOT_PREFIX), str(sorted(principals)))
if shutil.which("ssh-keygen"):
    for line in entries:
        m = re.search(r"(ssh-ed25519|ssh-rsa|ecdsa-\S+|sk-\S+)\s+(\S+)", line)
        with tempfile.NamedTemporaryFile("w", suffix=".pub", delete=False) as f:
            f.write(f"{m.group(1)} {m.group(2)}\n" if m else "junk\n")
        r = subprocess.run(["ssh-keygen", "-l", "-f", f.name], capture_output=True, text=True)
        ok(f"{line.split()[0]}: ssh-keygen reads the key", r.returncode == 0, r.stderr.strip()[:120])
        pathlib.Path(f.name).unlink()

print("\n— .github/workflows/release.yml —")
# THIS SUITE SHIPS INSIDE EVERY BOX, AND A BOX HAS NO .github. The trust-file half above is meaningful on a
# box — it checks the very file that box verifies updates against — so the suite ships; the workflow half
# is about the repository. It is skipped ONLY when the whole .github directory is absent. If .github
# exists and release.yml does not, that is a real failure, so in the repository — the one place the
# workflow lives — these checks can never quietly degrade into a skip.
if not (ROOT / ".github").is_dir():
    print("  skip no .github in this tree (a box has no repository); the trust file was checked above")
    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
    print("ALL OK")
    sys.exit(0)
wf = WORKFLOW.read_text() if WORKFLOW.is_file() else ""
ok("the release workflow exists", bool(wf))
ok("it is started by hand only — never by a push or a pull request",
   "workflow_dispatch" in wf and not re.search(r"^\s*(push|pull_request|pull_request_target|schedule):", wf, re.M))
ok("the key is an ENVIRONMENT secret (environment: release), not a repository secret",
   re.search(r"^\s*environment:\s*release\s*$", wf, re.M) is not None)
ok("and the job refuses to run off main as a second lock", "github.ref == 'refs/heads/main'" in wf)
ok("the secret is written to a file and never echoed", not re.search(r"echo[^\n]*\$\{?KEY", wf))
ok("the signing key file is removed after signing", 'rm -f "$keyfile"' in wf)
verify_at, push_at = wf.find("core.release.verify"), wf.find("git push origin")
ok("the tag is verified BEFORE it is pushed — a tag a box would refuse is never published",
   0 < verify_at < push_at, f"verify at {verify_at}, push at {push_at}")
ok("it verifies against the PREVIOUS release's trust file, the way a box running it would",
   "$PREV^{commit}:trust/allowed_signers" in wf and "--current \"$PREV\"" in wf)

print("\n— publishing each box type's own repository —")
job = wf.split("  publish-boxes:", 1)[1] if "  publish-boxes:" in wf else ""
ok("a publish-boxes job exists and runs only after the AIOS tag is signed and pushed", "needs: sign" in job, job[:120])
ok("it uses the main-only release environment too, with the same off-main refusal",
   "environment: release" in job and "github.ref == 'refs/heads/main'" in job)
ok("it publishes the exact release tag the sign job produced", "ref: ${{ needs.sign.outputs.tag }}" in job
   and "tag=$tag" in wf.split("  publish-boxes:", 1)[0])
pub_at, push_at2 = job.find("scripts/publish_box.sh"), job.find("push -q origin HEAD:refs/heads/main")
ok("the box repository is pushed only AFTER publish_box.sh (which verifies) succeeded under set -e",
   0 < pub_at < push_at2 and "set -euo pipefail" in job, f"publish at {pub_at}, push at {push_at2}")
ok("GitHub's host keys come from its API over TLS, and the push refuses an unknown host",
   "api.github.com/meta" in job and "StrictHostKeyChecking=yes" in job)
ok("a box type whose publish key is not set is SKIPPED with a notice, so an AIOS release never fails for it",
   "::notice::" in job and "ready=no" in job)
ok("no secret is echoed in the publish job", not re.search(r"echo[^\n]*\$\{?(PUBLISH_KEY|SIGNING_KEY)", job))

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
