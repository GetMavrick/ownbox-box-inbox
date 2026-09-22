"""A sold box can follow its release repository over https — no key, no prompt, same signature check.

MEASURED 2026-09-22 on ownbox-oauth: the image mints /var/lib/aios/update_key for every box, and
nothing registers its public half on the box repository, so every fetch on every sold box was
"Permission denied (publickey)" and the twice-daily updater failed silently fleet-wide. A per-box
key is a per-box registration somebody has to do; a PUBLIC release mirror over https needs neither.
The trust never lived in the transport — `core.release.verify` checks the signed tag against
trust/allowed_signers whichever way the bytes arrived — so the gates that decided "is this a box
repository" only needed to stop insisting on ssh.

WHAT THIS MEASURES. The three scripts that gate on the origin form each carry the pattern inline
in bash, so this runs each script's OWN pattern (lifted from the file, not retyped here) through
`grep -E` against the forms that must pass and the ones that must not. And a box has no terminal:
over https a private or missing repository makes git ask for a username, so box_update.sh must
turn prompts off or the timer hangs to its unit timeout with nothing in the log.

Run: python tests/test_a_box_can_follow_an_https_origin.py
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


GATES = ("scripts/install_services.sh", "scripts/image_prepare.sh", "scripts/box_update_key.sh")
MUST_PASS = (
    "git@github.com:GetMavrick/ownbox-box-inbox.git",          # today's per-box deploy key
    "https://github.com/GetMavrick/ownbox-box-inbox.git",      # a public mirror
    "https://github.com/GetMavrick/ownbox-box-inbox",          # ...as GitHub prints it
    "https://github.com/GetMavrick/ownbox-box-lead-2.git",
)
MUST_FAIL = (
    "https://x-access-token:ghp_abc@github.com/GetMavrick/ownbox-box-inbox.git",  # a baked credential
    "https://github.com/GetMavrick/AIOS.git",                  # the monorepo: a sold box never follows it
    "git@github.com:GetMavrick/AIOS.git",
    "https://github.com/Someone/ownbox-box-inbox.git",         # somebody else's mirror
    "http://github.com/GetMavrick/ownbox-box-inbox.git",       # cleartext
    "https://github.com/GetMavrick/ownbox-box-Inbox.git",      # not the slug alphabet
    "",
)


def pattern_in(path: str) -> str:
    """The origin pattern exactly as the script feeds it to grep -E."""
    src = open(os.path.join(ROOT, path)).read()
    m = re.search(r"grep -Eq '(\^\(?(?:git@github|https://github)[^']*)'", src)
    assert m, f"{path}: no origin gate found"
    return m.group(1)


def grep(pattern: str, value: str) -> bool:
    r = subprocess.run(["grep", "-Eq", pattern], input=value + "\n", text=True)
    return r.returncode == 0


print("test_every_gate_admits_ssh_and_https_and_nothing_else")
for gate in GATES:
    pat = pattern_in(gate)
    for v in MUST_PASS:
        ok(f"{gate}: admits {v}", grep(pat, v), pat)
    for v in MUST_FAIL:
        ok(f"{gate}: refuses {v or '<empty>'}", not grep(pat, v), pat)

print("test_the_three_gates_agree")
pats = {pattern_in(g) for g in GATES}
ok("one pattern, three files — they cannot drift apart silently", len(pats) == 1, str(pats))

print("test_the_updater_never_waits_on_a_prompt")
upd = open(os.path.join(ROOT, "scripts/box_update.sh")).read()
ok("box_update.sh exports GIT_TERMINAL_PROMPT=0",
   re.search(r"^export GIT_TERMINAL_PROMPT=0\s*$", upd, re.M) is not None)
ok("...before the first git command that could reach the network",
   upd.find("GIT_TERMINAL_PROMPT=0") < upd.find("core.release.update"), "guard is after the fetch")

print("test_image_prepare_still_refuses_a_credential_in_any_remote")
prep = open(os.path.join(ROOT, "scripts/image_prepare.sh")).read()
ok("the credential check is still there, independent of the origin gate",
   "a remote URL carries a credential" in prep)

print("\nFAILED" if _failed else "\nALL PASS")
sys.exit(1 if _failed else 0)
