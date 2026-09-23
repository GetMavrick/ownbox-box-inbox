"""The buyer's own SSH key, added by the box, and everything it refuses.

Ownbox sells full access to a machine the buyer owns. Until this shipped they had NONE: the only
key ever placed on a droplet is ours, and first boot deletes it on purpose so we keep no standing
access. This suite is the proof of the half that gives them theirs.

WHAT IT REFUSES TO LET THROUGH, each because it is a real way to lose a box or a secret:

  1. A PRIVATE KEY. People paste `id_ed25519` instead of `id_ed25519.pub` constantly. Storing one
     on a server is a much worse day than a rejected paste, so it is refused by shape and the
     message names the file to open instead.
  2. AN OPTIONS LINE. OpenSSH reads an optional options field in front of a key — `command=`,
     `from=`, `permitopen=`. A line starting with anything but a key type is a configuration
     change wearing a key's clothes, and this screen does not make configuration changes.
  3. OUR PROVISIONING KEY BEING TOUCHED. It is not listed and cannot be removed here. A buyer who
     deleted it before bootstrap finished would lock a half-built box out of its own build.
  4. A KEY WHOSE TWO HALVES DISAGREE. The type is written twice, once in the line and once inside
     the key, and OpenSSH reads the second. A mismatch is accepted by a naive parser and refused
     at login, which sends the owner into sshd logs instead of back to their clipboard.
  5. LINES THIS BOX DID NOT WRITE BEING DISCARDED. The file may hold keys a person added by hand
     over SSH. An edit here keeps them, or the screen takes away the access it exists to provide.

Run: python tests/test_the_buyer_can_get_in.py
"""
import base64
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = tempfile.mkdtemp() + "/access.db"
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
# NEVER READ THE MACHINE YOU RUN ON for a fact the product branches on: our dev boxes carry these
# and CI carries none, so a suite that passes on only one of the two is worse than no suite.
for _k in [k for k in list(os.environ) if k.startswith("ZERNIO_API_KEY")] + ["ANTHROPIC_API_KEY"]:
    os.environ.pop(_k, None)
_KEYS = pathlib.Path(tempfile.mkdtemp()) / "root-ssh" / "authorized_keys"
os.environ["AIOS_AUTHORIZED_KEYS"] = str(_KEYS)

from core import box_access as A  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def blob(ktype: str, payload: bytes) -> str:
    """A real SSH wire-format public key: the type is written INSIDE it as well as in front."""
    t = ktype.encode()
    return base64.b64encode(len(t).to_bytes(4, "big") + t
                            + len(payload).to_bytes(4, "big") + payload).decode()


ED = blob("ssh-ed25519", bytes(range(32)))
ED2 = blob("ssh-ed25519", bytes(range(1, 33)))
RSA = blob("ssh-rsa", bytes(range(64)))
KEY = f"ssh-ed25519 {ED} brian@macbook"
KEY2 = f"ssh-ed25519 {ED2} brian@phone"
OURS = f"ssh-ed25519 {blob('ssh-ed25519', bytes(32))} ownbox-provisioning"


# ── it takes a real key ───────────────────────────────────────────────────────────────────
print("\n— a buyer pastes the key from their laptop —")

t, b, c = A.parse(KEY)
ok("a normal ed25519 key parses into its three parts", (t, b, c) == ("ssh-ed25519", ED, "brian@macbook"))
ok("a key with no comment is fine — the comment is a label, not part of the key",
   A.parse(f"ssh-ed25519 {ED}")[2] == "")
ok("surrounding whitespace and a trailing newline are forgiven",
   A.parse(f"  \n ssh-ed25519 {ED} me  \n ")[1] == ED)
ok("an RSA key is accepted too", A.parse(f"ssh-rsa {RSA}")[0] == "ssh-rsa")

fp = A.fingerprint(ED)
ok("the fingerprint is OpenSSH's own SHA256 form, so it matches ssh-keygen -lf",
   fp.startswith("SHA256:") and "=" not in fp, fp)


# ── and refuses the five things that cost you a box or a secret ───────────────────────────
print("\n— what it will not store —")


def refused(pasted, needle, label):
    try:
        A.parse(pasted)
    except A.KeyRefused as e:
        ok(label, needle in str(e), f"said: {e}")
    else:
        ok(label, False, "it was ACCEPTED")


refused("-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----",
        "id_ed25519.pub", "a PRIVATE key is refused, and the message names the .pub file to open")
refused(f'command="/bin/false" ssh-ed25519 {ED}', "options field",
        "an options field in front of the key is refused — that is a config change, not a key")
refused(f'from="10.0.0.0/8" ssh-ed25519 {ED}', "options field",
        "...and so is a from= restriction, however harmless it looks")
# THE MESSAGE IS THE POINT. Both of those fail the shape check as well, so a refusal that did not
# look for the options field FIRST would tell somebody holding a perfectly good key that their key
# is malformed, and they would go and generate another one.
try:
    A.parse(f'command="/bin/false" ssh-ed25519 {ED}')
except A.KeyRefused as e:
    ok("...and the message names the options field rather than blaming the key",
       "options" in str(e) and "not a public key line" not in str(e), str(e))
refused(f"ssh-ed25519 {ED}\nssh-ed25519 {ED2}", "one line",
        "two keys at once are refused rather than half-stored")
refused(f"ssh-ed25519 {RSA}", "different keys",
        "A LINE WHOSE TYPE DISAGREES WITH THE KEY INSIDE IT is refused HERE, not at login")
refused("ssh-dss AAAAB3NzaC1kc3MAAACBANTL", "not a key type this box accepts",
        "ssh-dss is refused: OpenSSH disables it, so storing one would report a success that cannot log in")
refused("ssh-ed25519 not-base64-!!!", "not a public key line",
        "a mangled paste is refused")
refused("ssh-ed25519 AAAA", "cut short", "a truncated key is refused")
refused("", "nothing was pasted", "an empty box says so plainly")
refused(f"ssh-ed25519 {ED}\x00evil", "control characters", "a control character is refused")


# ── adding, twice, and removing ───────────────────────────────────────────────────────────
print("\n— the file the box writes —")

text, fp1 = A.add("", KEY)
ok("the first key lands in an empty file", text.strip() == f"ssh-ed25519 {ED} brian@macbook")
again, fp_again = A.add(text, KEY)
ok("ADDING THE SAME KEY TWICE IS NOT AN ERROR AND NOT A DUPLICATE",
   again == text and fp_again == fp1, repr(again))
ok("...and a re-paste with a DIFFERENT comment is still the same key",
   A.add(text, f"ssh-ed25519 {ED} renamed-my-laptop")[0] == text)
two, _ = A.add(text, KEY2)
ok("a second, different key is appended", len(A.listed(two)) == 2)

gone, removed = A.remove(two, fp1)
ok("removing by fingerprint takes exactly that one", removed and len(A.listed(gone)) == 1)
ok("...and leaves the other untouched", A.listed(gone)[0]["comment"] == "brian@phone")
_, missing = A.remove(gone, "SHA256:nothing")
ok("removing a fingerprint that is not there reports so rather than raising", not missing)


# ── our key is not theirs to see or delete ────────────────────────────────────────────────
print("\n— the provisioning key is ours, and first boot's to remove —")

mixed = OURS + "\n" + KEY + "\n"
ok("OUR PROVISIONING KEY IS NOT LISTED on the buyer's screen", len(A.listed(mixed)) == 1)
ok("...and the one listed is theirs", A.listed(mixed)[0]["comment"] == "brian@macbook")
after, _ = A.remove(mixed, A.fingerprint(blob("ssh-ed25519", bytes(32))))
ok("IT CANNOT BE REMOVED HERE — a half-built box must not be locked out of its own bootstrap",
   OURS in after, after)
added, _ = A.add(mixed, KEY2)
ok("...and adding a key never rewrites it either", OURS in added)

# A line this box cannot read is a key somebody put there by hand over SSH. Discarding it on an
# unrelated edit would take away the very access this screen exists to provide.
hand = "# added over ssh by me\nsomething-we-cannot-parse\n" + KEY + "\n"
kept, _ = A.remove(hand, "SHA256:nothing")
ok("A LINE THE BOX DID NOT WRITE SURVIVES AN EDIT", "something-we-cannot-parse" in kept)
ok("...and a comment line survives too", "# added over ssh by me" in kept)


# ── on disk, with the permissions OpenSSH insists on ──────────────────────────────────────
print("\n— written to disk —")

ok("nothing is there to begin with", A.read() == "")
A.write(two)
ok("the file round-trips", A.read() == two)
ok("THE FILE IS 0600 — OpenSSH silently ignores a key file anyone else can write",
   oct(_KEYS.stat().st_mode)[-3:] == "600", oct(_KEYS.stat().st_mode))
ok("...and its directory is 0700, for the same reason",
   oct(_KEYS.parent.stat().st_mode)[-3:] == "700", oct(_KEYS.parent.stat().st_mode))
A.write(gone)
ok("a replace leaves no temporary file behind — a half-written file is an unreachable box",
   sorted(p.name for p in _KEYS.parent.iterdir()) == ["authorized_keys"],
   str([p.name for p in _KEYS.parent.iterdir()]))

# ── and the screen a buyer actually uses ──────────────────────────────────────────────────
print("\n— the screen, driven over HTTP —")

from core import state  # noqa: E402

state.init_db()
from core import dash  # noqa: E402
from core.dispatch import app  # noqa: E402


def client_for(user_id):
    c = app.test_client()
    c.set_cookie(dash.COOKIE, dash.new_session(user_id))
    return c


A.write("")                                              # a box nobody has added a key to yet
owner = client_for(state.owner_user()["id"])
member = client_for(state.add_user("sam@acme.co", name="Sam", role="member")["id"])

# A SCREEN NOBODY CAN FIND IS NOT A FEATURE. The owner reaches it from System Settings; a member
# is not offered a door that would refuse them, which is the dead end the walk suite caught before.
idx = owner.get("/settings").get_data(as_text=True)
ok("the owner is offered the way in from System Settings", "/settings/access" in idx)
ok("...and a member is NOT, because that door would refuse them",
   "/settings/access" not in member.get("/settings").get_data(as_text=True))

page = owner.get("/settings/access").get_data(as_text=True)
ok("a box with no buyer key SAYS SO — silence would read as 'you are already in'",
   "SSH will refuse you" in page)
ok("...and tells them the exact command to run on their own computer",
   "ssh-keygen -t ed25519" in page and "id_ed25519.pub" in page)
ok("...and warns which file must never leave it",
   "never leave" in page)

r = owner.post("/settings/access", data={"key": KEY})
body = r.get_data(as_text=True)
ok("the owner's key is stored through the real POST", r.status_code == 200 and ED in A.read(), A.read())
ok("...and the page shows the fingerprint to check, not just a tick",
   A.fingerprint(ED) in body, body[:200])

# THE REFUSAL HAS TO REACH THE SCREEN. A message that teaches nothing is the same as no message,
# and this is the paste people actually get wrong.
bad = owner.post("/settings/access",
                 data={"key": "-----BEGIN OPENSSH PRIVATE KEY-----\nx\n-----END OPENSSH PRIVATE KEY-----"})
ok("A PASTED PRIVATE KEY IS REFUSED ON THE SCREEN, naming the .pub file instead",
   "id_ed25519.pub" in bad.get_data(as_text=True) and "PRIVATE KEY" not in A.read())

listed = owner.get("/settings/access").get_data(as_text=True)
ok("the stored key is listed with its fingerprint", A.fingerprint(ED) in listed)
owner.get(f"/settings/access?remove={A.fingerprint(ED)}")
ok("and removing it through the screen takes it off the box", ED not in A.read(), A.read())

# A KEY HERE IS ROOT, which is strictly more than this dashboard grants anyone. It is the one
# screen in the drawer where the gate matters most, so it is checked on the POST as well as the page.
ok("a member is refused the page outright", member.get("/settings/access").status_code == 403)
ok("...and on the POST, not merely the page",
   member.post("/settings/access", data={"key": KEY}).status_code == 403)
ok("...and nothing they sent was written", ED not in A.read())
anon = app.test_client()
r = anon.post("/settings/access", data={"key": KEY})
ok("a stranger is sent to the login and stores nothing",
   r.status_code in (302, 303) and "/dash/login" in (r.headers.get("Location") or "")
   and ED not in A.read(), str(r.status_code))

print("\nALL ACCESS CHECKS PASS" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
