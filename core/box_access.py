"""The buyer's own way into their own box: their SSH key, added by the box itself.

WHY THIS FILE EXISTS. Ownbox sells "full access to the machine you own", and until now a buyer had
none. The only key ever placed on a droplet is OURS — `DO_SSH_KEYS`, which the provisioner refuses
to start without and whose comment must end in ` ownbox-provisioning` — and first boot DELETES it
the moment bootstrap succeeds (provisioner/userdata.py), deliberately, so we hold no standing
access to a customer's machine. Correct, and it leaves the buyer locked out of the server they
bought. This is the other half: the box lets its owner add a key of their own.

DONE ON THE BOX, NOT IN THE PROVISIONER, and that is the design rather than the convenient option:

  * nothing has to be asked for before the sale. An SSH key demanded at checkout costs orders from
    exactly the buyers least likely to have one to hand;
  * the buyer needs no DigitalOcean account, and we register nothing on ours;
  * it works identically on a box they moved to a Mac mini or another VPS, which is a promise the
    product makes and a provisioner-side answer could not keep;
  * first boot's cleanup already spares it. That filter removes only lines ending in our
    provisioning comment, so a key added here survives untouched — and a key added BEFORE
    bootstrap finishes survives too.

WHAT IS REFUSED, AND WHY EACH ONE. An `authorized_keys` line is not just a key; OpenSSH reads an
optional OPTIONS field in front of it (`command=`, `permitopen=`, `from=`, `environment=`). A line
beginning with anything but a key type is therefore a configuration change wearing a key's
clothes, and it is refused rather than parsed. So are multi-line pastes, control characters, and a
private key — people paste `id_ed25519` instead of `id_ed25519.pub` constantly, and a private key
in a world-readable-ish file on a server is a much worse day than a rejected paste. It is refused
by SHAPE and the message says which file to open, because "invalid key" teaches nobody anything.

IDENTITY IS TYPE + BODY, NEVER THE COMMENT. The comment is a human label a laptop chooses
(`brian@macbook`) and it changes when a machine is renamed. Keying on it would let the same key be
added twice and would make removal miss.

NOTHING HERE REASONS, and nothing here reads a private key, a passphrase or an agent.
"""
from __future__ import annotations

import base64
import hashlib
import os
import pathlib
import re

from core.logging import get_logger

log = get_logger(__name__)

# Where OpenSSH looks for root's keys. The dispatch service runs as root with ProtectSystem=full,
# which makes /usr and /boot read-only and leaves /root writable — so this path is reachable from
# the web process without loosening a single hardening directive.
AUTHORIZED_KEYS = pathlib.Path(os.environ.get("AIOS_AUTHORIZED_KEYS") or "/root/.ssh/authorized_keys")

# OURS, and never touched by anything in this file. provisioner/userdata.py places it and first
# boot removes it; a buyer must not be able to delete it before bootstrap has finished, because a
# half-built box would then be unreachable for diagnosis and simply fail.
PROVISIONING_COMMENT = "ownbox-provisioning"

# The key types OpenSSH accepts that anyone actually has. `ssh-dss` is deliberately absent: it is
# disabled by default in OpenSSH 7.0+, so accepting it here would store a key that can never log
# in and report success.
KEY_TYPES = (
    "ssh-ed25519",
    "ssh-rsa",
    "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
    # Hardware security keys — a YubiKey is exactly the kind of key a careful owner uses.
    "sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com",
)

_LINE = re.compile(r"^(?P<type>[A-Za-z0-9@.-]+)[ \t]+(?P<body>[A-Za-z0-9+/]+={0,3})(?:[ \t]+(?P<comment>.*))?$")
_PRIVATE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")


class KeyRefused(ValueError):
    """The paste is not a public key we will store. The message is shown to the owner verbatim."""


def fingerprint(body: str) -> str:
    """OpenSSH's own SHA256 fingerprint, so it matches what `ssh-keygen -lf` prints.

    The owner compares what the screen shows against their own machine. A fingerprint we invented
    would be a number they cannot check anywhere, which is the same as no fingerprint.
    """
    raw = base64.b64decode(body + "=" * (-len(body) % 4))
    return "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")


def parse(pasted: str) -> tuple[str, str, str]:
    """(type, body, comment) for one public key, or raise KeyRefused saying what to do instead."""
    text = (pasted or "").strip()
    if not text:
        raise KeyRefused("nothing was pasted")
    if _PRIVATE.search(text):
        raise KeyRefused(
            "that is your PRIVATE key — it must never leave your computer. Open the file with "
            "the same name ending in .pub instead (id_ed25519.pub, not id_ed25519)")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) != 1:
        raise KeyRefused(f"paste one key, on one line — this is {len(lines)} lines")
    line = lines[0].strip()
    if any(ord(c) < 32 or ord(c) == 127 for c in line):
        raise KeyRefused("that paste contains control characters, so it is not a public key")
    # THE OPTIONS FIELD IS CAUGHT BEFORE THE SHAPE CHECK, because `command="/bin/false" ssh-...`
    # fails the shape check too and would earn the generic message below — which tells somebody
    # holding a perfectly good key that their key is malformed. A line whose first word is not a
    # key type while a key type appears LATER in it is an options field, and saying so is the
    # whole value of the refusal.
    first = line.split(None, 1)[0]
    if first not in KEY_TYPES and any(f" {t} " in f" {line} " for t in KEY_TYPES):
        raise KeyRefused(
            f"the line begins with {first[:40]!r} before the key. That is OpenSSH's options "
            f"field, and this screen carries no options — paste the key line on its own, "
            f"beginning with its type")
    m = _LINE.match(line)
    if not m:
        raise KeyRefused(
            "that is not a public key line. It starts with a type such as ssh-ed25519, then the "
            "key itself, then an optional name")
    ktype, body = m.group("type"), m.group("body")
    if ktype not in KEY_TYPES:
        # A line beginning with anything else is OpenSSH's OPTIONS field — `command=`, `from=`,
        # `permitopen=` — which is a configuration change, not a key, and is not something this
        # screen accepts on the owner's behalf.
        raise KeyRefused(
            f"{ktype!r} is not a key type this box accepts. The line must BEGIN with the type "
            f"({', '.join(KEY_TYPES[:3])}, …) and carry no options in front of it")
    try:
        raw = base64.b64decode(body + "=" * (-len(body) % 4), validate=True)
    except Exception:                                    # noqa: BLE001
        raise KeyRefused("the key itself is not valid base64, so the paste was cut short") from None
    if len(raw) < 32:
        raise KeyRefused("the key itself is too short to be real, so the paste was cut short")
    # THE TYPE IS ALSO WRITTEN INSIDE THE KEY, and OpenSSH reads THAT one. A line saying
    # ssh-ed25519 in front of an RSA body is accepted by this parser and refused at login, which
    # would send the owner hunting through sshd rather than re-pasting.
    declared = raw[4:4 + int.from_bytes(raw[:4], "big")].decode("ascii", "replace")
    if declared != ktype:
        raise KeyRefused(f"the line says {ktype} but the key inside it is {declared} — "
                         f"the two halves are from different keys, so re-copy the whole line")
    return ktype, body, (m.group("comment") or "").strip()


def _is_ours(line: str) -> bool:
    return line.rstrip().endswith(" " + PROVISIONING_COMMENT)


def listed(text: str) -> list[dict]:
    """The buyer's keys in an authorized_keys file. OURS IS NOT LISTED and cannot be removed here.

    Hiding it is not cosmetic: it is removed by first boot on its own, and a screen that offered a
    delete button for it would let an owner lock a half-built box out of its own bootstrap.
    """
    out = []
    for line in (text or "").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or _is_ours(line):
            continue
        try:
            ktype, body, comment = parse(line)
        except KeyRefused:
            continue                                     # a line we did not write and cannot read
        out.append({"type": ktype, "comment": comment, "fingerprint": fingerprint(body)})
    return out


def add(text: str, pasted: str) -> tuple[str, str]:
    """Return (new file text, fingerprint). Idempotent on type+body; never rewrites another line."""
    ktype, body, comment = parse(pasted)
    fp = fingerprint(body)
    for line in (text or "").splitlines():
        try:
            t2, b2, _ = parse(line)
        except KeyRefused:
            continue
        if (t2, b2) == (ktype, body):
            return text, fp                              # already here: adding twice is not an error
    line = " ".join(x for x in (ktype, body, comment) if x)
    body_text = text or ""
    if body_text and not body_text.endswith("\n"):
        body_text += "\n"
    return body_text + line + "\n", fp


def remove(text: str, fp: str) -> tuple[str, bool]:
    """Drop the key with this fingerprint. Returns (new text, whether anything was removed).

    A LINE WE CANNOT PARSE IS KEPT, and so is ours. This file may hold keys a person put there by
    hand over SSH, and a screen that silently discarded them on an unrelated edit would take away
    the access it exists to provide.
    """
    kept, gone = [], False
    for line in (text or "").splitlines():
        if line.strip() and not _is_ours(line):
            try:
                _, b2, _ = parse(line)
            except KeyRefused:
                kept.append(line)
                continue
            if fingerprint(b2) == fp:
                gone = True
                continue
        kept.append(line)
    return ("\n".join(kept) + "\n" if kept else ""), gone


def read() -> str:
    try:
        return AUTHORIZED_KEYS.read_text()
    except FileNotFoundError:
        return ""
    except OSError as e:                                 # noqa: BLE001
        log.error("box_access.unreadable", error=type(e).__name__)
        raise


def write(text: str) -> None:
    """Replace the file atomically, with the permissions OpenSSH insists on.

    NOT AN APPEND, and not an in-place edit: a write interrupted halfway through authorized_keys is
    a box nobody can reach. Written beside the real file and renamed, which is atomic on the same
    filesystem, so the file is always either the old one or the new one.

    OpenSSH IGNORES a key file that is group- or world-writable, and it does so SILENTLY from the
    client's side — the login just fails. So the mode is set explicitly rather than inherited from
    whatever umask the web process happens to be running under.
    """
    AUTHORIZED_KEYS.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(AUTHORIZED_KEYS.parent, 0o700)
    tmp = AUTHORIZED_KEYS.with_suffix(".aios-tmp")
    tmp.write_text(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, AUTHORIZED_KEYS)
