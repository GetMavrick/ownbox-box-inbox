"""Decide whether a fetched release tag may be installed on this box.

WHY THIS EXISTS. A box runs the code at a git ref as root, and that box holds a company's customer
conversations, its keys, and its ability to send mail as the company. So whoever can put a commit on
the ref the box follows owns all of that. Signing is the answer — but git does NOT verify anything on
its own. `git fetch` and `git checkout` accept an unsigned or wrongly-signed tag without a word
(measured by OSDev4, 2026-09-13). Verification is code somebody has to run, and this is that code.

WHAT IT DOES NOT DO. It never fetches. The caller fetches from a remote URL or from a bundle file and
then asks this module about objects that are already local, so an online box and an air-gapped box
take the identical path. Wiring verification to the remote would make the offline path a second,
untested one — which is the failure this design exists to avoid.

HOW IT DECIDES. Every check is a refusal with a reason code; the only way to get `ok=True` is to pass
all of them, in this order:

  bad_tag_name            the ref is not `release/YYYY.MM.DD.N` — a branch or a stray tag is never
                          installable, because a branch moves under anyone who can push
  not_annotated           a lightweight tag carries no signature at all
  unsigned                an annotated tag with no SSH signature block
  name_mismatch           the signed object names a DIFFERENT tag than the ref it was found under.
                          Refs are just pointers: a validly signed old release can be pushed under a
                          newer ref name, and a downgrade check that trusts the ref name would wave
                          it through. The name inside the signature is the only name that counts
  untrusted_signer        no principal in the pinned trust file holds the signing key, or that key
                          is outside its valid-after / valid-before window at the box's own clock
  signature_invalid       a principal matched but the signature does not verify
  downgrade               the release is not newer than the one installed (a human may override)
  trust_change_needs_root the release changes the trust file itself, and it was not signed by a
                          root key. The everyday signing key can ship code; only a root key can
                          change who is trusted, so a stolen everyday key cannot promote itself
  dirty_tree              tracked files on the box were edited by hand; installing over them would
                          either fail halfway or silently discard someone's change

WHY NOT `git verify-tag`. Measured, not assumed (tests/test_release_verify.py prints it): a tag
signed by a key the trust file RETIRED with `valid-before="20260101"`, but backdated to 2025 by the
tagger, passes `git verify-tag` with exit 0 — git verifies at the timestamp the tagger wrote. This
module verifies at the box's own clock instead, calling the same `ssh-keygen -Y` that git calls, and
refuses that tag. A retired key must stay retired, however its tags are dated.

TRUST FILE. `trust/allowed_signers` in the release tree, in OpenSSH allowed-signers format, read from
the release that is CURRENTLY INSTALLED — never from the candidate, which is exactly what is being
judged. Principals named `root-*` are root keys (held offline by the owner); every other principal may
sign code but may not change this file.
"""
from __future__ import annotations

import base64
import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

TRUST_PATH = "trust/allowed_signers"
ROOT_PREFIX = "root-"
NAMESPACE = "git"                      # the namespace git uses when it signs a tag
_TAG = re.compile(r"^release/(\d{4})\.(\d{2})\.(\d{2})\.(\d+)$")
_SIG_BEGIN = b"-----BEGIN SSH SIGNATURE-----"


@dataclass(frozen=True)
class Verdict:
    ok: bool
    tag: str
    reason: str                        # "ok" or a refusal code from the module docstring
    detail: str = ""
    commit: str | None = None
    principal: str | None = None


def version_of(tag: str) -> tuple[int, int, int, int] | None:
    m = _TAG.match(tag)
    return tuple(int(g) for g in m.groups()) if m else None  # type: ignore[return-value]


def _signing_key(signature: bytes) -> str | None:
    """The base64 public key INSIDE an armored SSH signature (OpenSSH PROTOCOL.sshsig: "SSHSIG",
    uint32 version, then the signer's public key as a length-prefixed string). Read from the
    signature itself, so the key is exactly the one that signed — never inferred from a name."""
    try:
        body = b"".join(l for l in signature.splitlines() if l and not l.startswith(b"-----"))
        blob = base64.b64decode(body)
    except (ValueError, TypeError):
        return None
    if not blob.startswith(b"SSHSIG") or len(blob) < 14:
        return None
    n = int.from_bytes(blob[10:14], "big")
    return base64.b64encode(blob[14:14 + n]).decode() if len(blob) >= 14 + n else None


def _principals_by_key(trust_file: Path) -> dict[str, set[str]]:
    """Every principal each public key is listed under, across EVERY line of the trust file.
    `ssh-keygen -Y find-principals` reports only the first matching line (measured 2026-09-15), so it
    cannot tell us a key appears twice — and a key listed as `root-x` above its `ci` line would come
    back as root."""
    keys: dict[str, set[str]] = {}
    for line in trust_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            tokens = shlex.split(line)
        except ValueError:
            continue
        for i, tok in enumerate(tokens[1:], start=1):
            if re.match(r"^(ssh-|ecdsa-|sk-)", tok) and i + 1 < len(tokens):
                keys.setdefault(tokens[i + 1], set()).update(filter(None, tokens[0].split(",")))
                break
    return keys


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True)


def _refuse(tag: str, reason: str, detail: str = "", **kw) -> Verdict:
    return Verdict(ok=False, tag=tag, reason=reason, detail=detail, **kw)


def verify_release(repo: str | Path, tag: str, trust_file: str | Path, *,
                   current_tag: str | None = None, now: datetime | None = None,
                   allow_downgrade: bool = False) -> Verdict:
    """Judge `tag` in `repo` against the pinned `trust_file`. Never raises for a bad release —
    a bad release is a Verdict with ok=False, because the caller must be able to log the reason."""
    repo = Path(repo)
    trust_file = Path(trust_file)
    now = now or datetime.now(timezone.utc)

    new_version = version_of(tag)
    if new_version is None:
        return _refuse(tag, "bad_tag_name", "installable refs are release/YYYY.MM.DD.N")

    if _git(repo, "cat-file", "-t", f"refs/tags/{tag}").stdout.strip() != b"tag":
        return _refuse(tag, "not_annotated", "a lightweight tag or a missing ref carries no signature")

    raw = _git(repo, "cat-file", "tag", f"refs/tags/{tag}").stdout
    if _SIG_BEGIN not in raw:
        return _refuse(tag, "unsigned", "annotated tag with no SSH signature")
    payload, sig_tail = raw.split(_SIG_BEGIN, 1)
    signature = _SIG_BEGIN + sig_tail

    headers = dict(line.split(" ", 1) for line in payload.decode("utf-8", "replace").splitlines()
                   if line and " " in line and not line.startswith(" "))
    if headers.get("tag") != tag:
        return _refuse(tag, "name_mismatch",
                       f"signed object names {headers.get('tag')!r}, found under ref {tag!r}")
    if headers.get("type") != "commit":
        return _refuse(tag, "not_annotated", f"tag points at a {headers.get('type')!r}, not a commit")

    if not trust_file.is_file():
        return _refuse(tag, "untrusted_signer", f"trust file missing: {trust_file}")
    verify_time = now.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")

    with tempfile.TemporaryDirectory() as td:
        sig_path = Path(td) / "tag.sig"
        sig_path.write_bytes(signature)
        found = subprocess.run(
            ["ssh-keygen", "-Y", "find-principals", "-s", str(sig_path), "-f", str(trust_file),
             "-O", f"verify-time={verify_time}"],
            capture_output=True)
        principals = [p for p in found.stdout.decode().split() if p]
        if found.returncode != 0 or not principals:
            return _refuse(tag, "untrusted_signer",
                           "no trusted, currently valid key matches this signature")
        # ONE KEY, ONE ROLE. Read the key out of the signature and count its principals across the
        # whole trust file. A key listed under several principals would make "is this a root key?"
        # depend on line order, and the separation between shipping code and changing trust with it.
        owners = _principals_by_key(trust_file).get(_signing_key(signature) or "", set())
        if len(owners) != 1:
            return _refuse(tag, "untrusted_signer",
                           f"the signing key is listed under {len(owners)} principals "
                           f"{sorted(owners)}; one key, one role")
        principal = next(iter(owners))
        if principal not in principals:
            return _refuse(tag, "untrusted_signer",
                           f"{principal} is not currently valid for this signature")
        checked = subprocess.run(
            ["ssh-keygen", "-Y", "verify", "-f", str(trust_file), "-I", principal,
             "-n", NAMESPACE, "-s", str(sig_path), "-O", f"verify-time={verify_time}"],
            input=payload, capture_output=True)
        if checked.returncode != 0:
            return _refuse(tag, "signature_invalid", checked.stderr.decode().strip()[:200],
                           principal=principal)

    commit = _git(repo, "rev-parse", f"refs/tags/{tag}^{{commit}}").stdout.decode().strip()

    if current_tag:
        current_version = version_of(current_tag)
        if current_version and new_version <= current_version and not allow_downgrade:
            return _refuse(tag, "downgrade", f"{tag} is not newer than installed {current_tag}",
                           commit=commit, principal=principal)
        old_trust = _git(repo, "rev-parse", f"refs/tags/{current_tag}^{{commit}}:{TRUST_PATH}").stdout.strip()
        new_trust = _git(repo, "rev-parse", f"{commit}:{TRUST_PATH}").stdout.strip()
        if old_trust != new_trust and not principal.startswith(ROOT_PREFIX):
            return _refuse(tag, "trust_change_needs_root",
                           f"{TRUST_PATH} changes in this release and {principal} is not a root key",
                           commit=commit, principal=principal)

    if _git(repo, "rev-parse", "--is-inside-work-tree").stdout.strip() == b"true":
        dirty = _git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip()
        if dirty:
            return _refuse(tag, "dirty_tree", dirty.decode()[:200], commit=commit, principal=principal)

    return Verdict(ok=True, tag=tag, reason="ok", commit=commit, principal=principal)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    ap = argparse.ArgumentParser(description="Refuse a release tag unless it is signed and allowed.")
    ap.add_argument("repo")
    ap.add_argument("tag")
    ap.add_argument("--trust", required=True, help="the INSTALLED release's trust/allowed_signers")
    ap.add_argument("--current", help="the installed release tag, for downgrade and trust checks")
    ap.add_argument("--allow-downgrade", action="store_true",
                    help="a human rolling back on purpose; never passed by /deploy")
    a = ap.parse_args(argv)
    v = verify_release(a.repo, a.tag, a.trust, current_tag=a.current,
                       allow_downgrade=a.allow_downgrade)
    print(json.dumps(v.__dict__))
    return 0 if v.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
