"""Signed plans, the box's half (docs/PLAN_TIER_INTEGRITY.md, step 3 contract, phase B).

WHAT IT PROVES. Ownbox signs every plan it sends a box ({"v", "order_id", "box_key", "seq", "tier",
"add", "until", "issued_at", "host"}) with a key only the provisioner holds, `ssh-keygen -Y sign` in
the namespace `ownbox-plan`. The box verifies it against the public half every release ships
(`config/ownbox_plan_signers`, principal `ownbox-plans`), and against itself: the plan must name this
box's order and the fingerprint of this box's own check-in key. So a plan edited on the box's disk, or
copied from another box, is worth nothing. `host` is information only, never checked: a box that moves
(take-home) keeps its order and its key, and must still accept its next plan, upgrades included.

INERT UNTIL ARMED. With no signers file there is nothing to verify against, and `armed()` is False:
every caller then behaves exactly as it did before signing existed. That is what lets this merge before
Ownbox signs anything (phase A), with no flag day.

THE EXACT BYTES, THEN PARSE. `verify` checks the signature over `text` exactly as it arrived, and only
then parses it. Nothing here re-serializes a plan before verifying it.

TWO WAYS TO FAIL, AND THEY ARE NOT THE SAME (rule 2: never downgrade a paying box because a check
failed). `ssh-keygen` saying the signature is wrong is FORGED: the plan is not Ownbox's. `ssh-keygen`
missing, hanging past its timeout, or the box's own key unreadable is UNVERIFIABLE: nothing is known
about the plan either way, so a caller keeps what it had. `verify` returns which one it was.

NEVER RAISES, and never reaches the network. A verification is cached per process by a hash of its
bytes, because the plan is read wherever a feature is asked about, including while the config loads.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

from core.logging import get_logger

log = get_logger(__name__)

ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = "ownbox-plan"
PRINCIPAL = "ownbox-plans"
FORMAT = 1
TIMEOUT_S = 10

OK, FORGED, UNVERIFIABLE = "ok", "forged", "unverifiable"

_cache: dict = {}


def signers_path() -> Path:
    return Path(os.environ.get("AIOS_PLAN_SIGNERS") or (ROOT / "config" / "ownbox_plan_signers"))


def armed() -> bool:
    """Does this box verify plans? Only once a release has shipped Ownbox's public key."""
    try:
        p = signers_path()
        return p.is_file() and any(ln.strip() and not ln.lstrip().startswith("#")
                                   for ln in p.read_text(encoding="utf-8").splitlines())
    except OSError:
        return False


def box_key() -> str | None:
    """The `SHA256:` fingerprint of this box's own check-in key, or None when it has none."""
    from core import version
    pub = os.environ.get("AIOS_UPDATE_KEY_PUB", version.UPDATE_KEY_PUB)
    if ("fp", pub) in _cache:
        return _cache[("fp", pub)]
    try:
        r = subprocess.run(["ssh-keygen", "-l", "-E", "sha256", "-f", pub],
                           capture_output=True, text=True, timeout=TIMEOUT_S)
        parts = r.stdout.split()
        fp = parts[1] if r.returncode == 0 and len(parts) > 1 and parts[1].startswith("SHA256:") else None
    except Exception:                                   # noqa: BLE001 — no key, no ssh-keygen
        fp = None
    if fp:
        _cache[("fp", pub)] = fp
    return fp


def _signature_ok(text: str, sig: str) -> str:
    """OK, FORGED or UNVERIFIABLE for `sig` over exactly `text`, against the shipped signers."""
    d = tempfile.mkdtemp(prefix="plan.")
    sig_path = Path(d) / "plan.sig"
    try:
        sig_path.write_text(sig, encoding="utf-8")
        r = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(signers_path()), "-I", PRINCIPAL,
                            "-n", NAMESPACE, "-s", str(sig_path)],
                           input=text.encode("utf-8"), capture_output=True, timeout=TIMEOUT_S)
        return OK if r.returncode == 0 else FORGED
    except Exception:                                   # noqa: BLE001 — missing binary, timeout
        return UNVERIFIABLE
    finally:
        sig_path.unlink(missing_ok=True)
        os.rmdir(d)


def verify(signed) -> tuple[str, dict | None, str]:
    """(result, plan, why) for a {"text", "sig"} Ownbox sent. `plan` is the parsed text, only on OK.

    OK needs all of: a good signature from a shipped key, format 1, and this box's own order and key
    fingerprint named in it. Anything that proves the plan is not for this box is FORGED; anything that
    stops the question being answered is UNVERIFIABLE."""
    if not isinstance(signed, dict) or not isinstance(signed.get("text"), str) \
            or not isinstance(signed.get("sig"), str):
        return FORGED, None, "not a signed plan: {text, sig} expected"
    text, sig = signed["text"], signed["sig"]
    key = hashlib.sha256((text + "\0" + sig).encode("utf-8")).hexdigest()
    if key in _cache:
        return _cache[key]
    got = _verify(text, sig)
    if got[0] != UNVERIFIABLE:                          # an unanswered question is asked again
        _cache[key] = got
    return got


def _verify(text: str, sig: str) -> tuple[str, dict | None, str]:
    if not armed():
        return UNVERIFIABLE, None, "no plan signers shipped on this box"
    sig_result = _signature_ok(text, sig)
    if sig_result != OK:
        return sig_result, None, ("the signature is not Ownbox's" if sig_result == FORGED
                                  else "the signature could not be checked")
    try:
        plan = json.loads(text)
    except ValueError:
        return FORGED, None, "signed text is not JSON"
    if not isinstance(plan, dict) or plan.get("v") != FORMAT:
        return FORGED, None, "not a format-1 plan"
    from core import claim
    order = claim.provisioned_order()
    if not order:
        return UNVERIFIABLE, None, "this box has no order of its own to compare"
    if str(plan.get("order_id") or "") != order:
        return FORGED, None, "the plan is for another order"
    mine = box_key()
    if not mine:
        return UNVERIFIABLE, None, "this box's own key could not be read"
    if str(plan.get("box_key") or "") != mine:
        return FORGED, None, "the plan is for another box's key"
    return OK, plan, ""


def reset() -> None:
    """Forget every cached answer (tests, and a release that ships a new signers file)."""
    _cache.clear()
