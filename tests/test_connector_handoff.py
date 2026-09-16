"""First boot's connector handoff (scripts/connector_handoff.py) — the box half of SPEC_CONNECT_HANDOFF.

The provisioner mints a Zernio profile per box and a key scoped to it, and leaves both in provision.json.
This is what turns that into a box that can actually post. The suite defends the two things that would hurt
a buyer if they went wrong quietly: a key ending up somewhere it can be read, and a key being replaced by
one that posts to somebody else's account.

Run: python tests/test_connector_handoff.py
"""
from __future__ import annotations

import io
import json
import os
import stat
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("connector_handoff", ROOT / "scripts/connector_handoff.py")
ch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ch)                 # scripts/ is not a package: load the file as one

FAILS: list[str] = []
KEY = "zk_live_ABCdef0123456789_xyz"
PROFILE = "0123456789abcdef01234567"


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


def box(connector=..., env_lines=("DASHBOARD_BASE_URL=https://acme.ownbox.app", "DASH_TOKEN=abc"), env_mode=0o600):
    """A box on disk: its provision.json and its .env. `connector=None` means a box built without one."""
    d = tempfile.mkdtemp()
    facts = {"buyer": "Acme", "order": "cs_live_x", "host": "acme.ownbox.app", "box_type": "customer_voice"}
    if connector is ...:
        connector = {"vendor": "zernio", "profile_id": PROFILE, "key": KEY}
    if connector is not None:
        facts["connector"] = connector
    prov = os.path.join(d, "provision.json")
    with open(prov, "w", encoding="utf-8") as fh:
        json.dump(facts, fh)
    env = os.path.join(d, ".env")
    fd = os.open(env, os.O_WRONLY | os.O_CREAT, env_mode)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(env_lines) + "\n")
    return prov, env


def run(prov, env, *extra):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = ch.main(["--provision", prov, "--env", env, *extra])
    return rc, out.getvalue() + err.getvalue()


def env_map(path):
    return dict(line.split("=", 1) for line in open(path, encoding="utf-8").read().splitlines() if "=" in line)


print("\n— a box we built —")
prov, env = box()
rc, said = run(prov, env)
vals = env_map(env)
ok("first boot writes the box's own key and its profile into .env", rc == 0
   and vals.get("ZERNIO_API_KEY") == KEY and vals.get("ZERNIO_PROFILE_ID") == PROFILE, str(vals))
ok("THE KEY IS NEVER PRINTED — not on success, where a boot log keeps it forever", KEY not in said, said)
ok("...though it does say which variables it wrote, by name",
   "ZERNIO_API_KEY" in said and "ZERNIO_PROFILE_ID" in said, said)
ok("everything else in .env is left exactly as it was",
   vals.get("DASHBOARD_BASE_URL") == "https://acme.ownbox.app" and vals.get("DASH_TOKEN") == "abc", str(vals))
mode = stat.S_IMODE(os.stat(env).st_mode)
ok("the file holding the key stays readable by root alone", mode == 0o600, oct(mode))
# ...and when there is no .env yet, the mode is THIS script's to get right, not install.sh's.
fresh_dir = tempfile.mkdtemp()
fresh = os.path.join(fresh_dir, ".env")
rc3, _ = run(prov, fresh)
fresh_mode = stat.S_IMODE(os.stat(fresh).st_mode)
ok("an .env it creates itself is readable by root alone too", rc3 == 0 and fresh_mode == 0o600, oct(fresh_mode))
# ...and the case that actually happens on a box: .env ALREADY exists, made by install.sh, which sets no
# mode of its own. O_CREAT's mode does not apply to an existing file, so without this the key would simply
# inherit whatever that file happened to be. Found by running the shipped script on the live box.
loose_prov, loose_env = box(env_mode=0o644)
os.chmod(loose_env, 0o644)                   # umask can bite the fixture too; state it outright
rc4, said4 = run(loose_prov, loose_env)
loose_after = stat.S_IMODE(os.stat(loose_env).st_mode)
ok("A LOOSE .env IS TIGHTENED before a key goes in it, not inherited",
   rc4 == 0 and loose_after == 0o600, oct(loose_after))
ok("...and it says it did, because a permission changed silently is one nobody can audit",
   "tightened" in said4, said4)
strict_prov, strict_env = box(env_mode=0o400)
os.chmod(strict_env, 0o600)
run(strict_prov, strict_env)
os.chmod(strict_env, 0o400)
before = stat.S_IMODE(os.stat(strict_env).st_mode)
ok("...and a box stricter than 0600 is never LOOSENED to meet it", before == 0o400, oct(before))
rc2, said2 = run(prov, env)
ok("running it again changes nothing and still succeeds (every boot runs it)",
   rc2 == 0 and env_map(env) == vals and "already set" in said2, said2)

print("\n— a box nobody bought, and a box built before the connector existed —")
rc, said = run(os.path.join(tempfile.mkdtemp(), "absent.json"), box(connector=None)[1])
ok("no provision.json at all is an ordinary green outcome, not a failed boot",
   rc == 0 and "nothing to hand over" in said, said)
prov, env = box(connector=None)
rc, said = run(prov, env)
ok("a provision.json with no connector block is green, and writes nothing",
   rc == 0 and "ZERNIO_API_KEY" not in env_map(env), said)

print("\n— a box that already has a key of its own —")
prov, env = box(env_lines=("ZERNIO_API_KEY=the_buyers_own_key", "DASH_TOKEN=abc"))
rc, said = run(prov, env)
ok("a key the buyer pasted is NOT replaced: their posts keep going where they think they go",
   rc == 0 and env_map(env)["ZERNIO_API_KEY"] == "the_buyers_own_key", str(env_map(env)))
ok("...and it says so, rather than leaving a person to wonder", "kept the box's own value" in said, said)
ok("...while the profile id, which it did not have, is still filled in",
   env_map(env).get("ZERNIO_PROFILE_ID") == PROFILE, str(env_map(env)))
rc, said = run(prov, env, "--force")
ok("--force is the deliberate rotation, and only then is it replaced",
   rc == 0 and env_map(env)["ZERNIO_API_KEY"] == KEY, str(env_map(env)))
ok("...and even a rotation prints no key", KEY not in said, said)

print("\n— a handoff that cannot be trusted is refused, loudly —")
for name, bad in {
    "an unknown vendor": {"vendor": "someone_else", "profile_id": PROFILE, "key": KEY},
    "a profile id that is not one": {"vendor": "zernio", "profile_id": "not-an-id", "key": KEY},
    "a key with a shell metacharacter in it": {"vendor": "zernio", "profile_id": PROFILE, "key": "a`id`b"},
    "a key far too short to be one": {"vendor": "zernio", "profile_id": PROFILE, "key": "short"},
}.items():
    prov, env = box(connector=bad)
    rc, said = run(prov, env)
    ok(f"{name}: refused, and nothing is written", rc == 1 and "ZERNIO_API_KEY" not in env_map(env), said)
    ok(f"...and the refusal does not quote the value it rejected", str(bad["key"]) not in said, said)

print("\n— the box is actually wired to use it —")
boot = (ROOT / "scripts/bootstrap.sh").read_text()
ok("bootstrap runs the handoff", "scripts/connector_handoff.py" in boot)
ok("...and a failed handoff does not fail the whole box: it is still a working box, without Instagram",
   "connector handoff failed" in boot and "|| echo" in boot.split("connector_handoff.py")[1][:40], boot[:0])
upd = (ROOT / "scripts/box_update.sh").read_text()
ok("...and the DAILY UPDATE runs it too, so a box built from an older image is healed rather than stranded",
   "scripts/connector_handoff.py" in upd)
ok("...guarded, so an older checkout without the script does not fail its update",
   "[ -f scripts/connector_handoff.py ]" in upd)
ok("the profile id is documented where a buyer would look for it",
   "ZERNIO_PROFILE_ID=" in (ROOT / ".env.example").read_text())

os.environ["ZERNIO_API_KEY"], os.environ["ZERNIO_PROFILE_ID"] = KEY, PROFILE
os.environ.setdefault("AIOS_HERMETIC_TEST", "1")
from core import spaces  # noqa: E402
from core.config import settings  # noqa: E402
ok("the setting reads the variable the handoff writes", settings.zernio_profile_id == PROFILE)
implicit = spaces._default_space()
ok("the single-tenant Space posts as the profile this box was given",
   implicit.get("zernio_profile_id") == PROFILE and implicit.get("zernio_key") == KEY, str(implicit))

# ── the handoff's shape is defined once ──────────────────────────────────────────────
print("\n— one definition of what a handoff block looks like —")
# THREE PLACES CHECK THIS BLOCK and each is right to: they run on different machines, minutes or
# months apart. What they must not do is check against three DIFFERENT patterns — widen one and
# leave the others, and the provisioner mints a key the box silently refuses, surfacing as an
# Instagram that never connects rather than as an error anybody can read.
#
# THE SOURCE CHECK BELOW IS THE ONE THAT ENFORCES THIS, and the identity checks are weaker than
# they look. I first wrote them claiming identity beats equality because "two separately-compiled
# regexes with the same source are different objects". THAT IS FALSE: `re.compile` caches by
# pattern and flags, so two independent `re.compile(r"^[0-9a-f]{24}$")` calls return the SAME
# object. Found by probing — restating the pattern in the box script left all three identity
# assertions green and only the source check went red. They are kept because they do fail the
# moment two patterns differ in text, which is the drift being guarded; they simply do not prove
# the import, so the check that a second copy is not present is doing the real work.
import re  # noqa: E402
from core import handoff_shape  # noqa: E402
ok("the box script validates with the shared profile-id pattern", ch._PROFILE_ID is handoff_shape.PROFILE_ID)
ok("...and the shared key pattern", ch._KEY is handoff_shape.KEY)
ok("...and the shared vendor name", ch._VENDOR == handoff_shape.VENDOR)
_hsrc = (ROOT / "scripts/connector_handoff.py").read_text(encoding="utf-8")
ok("...and carries no second copy of the pattern", 'r"^[0-9a-f]{24}$"' not in _hsrc)
# The provisioner's end is asserted in tests/test_provisioner_connector.py, which is allowed to
# import provisioner/. THIS suite must not: export_box.sh refuses to ship any file that does, and
# a suite that cannot run inside a built box is not guarding the box.
# CHECKED WITH THE EXPORTER'S OWN RULE, not a substring of the file. The first version of this
# line searched for "import provisioner" anywhere and failed on the COMMENT above it — the same
# false positive test_suite_integrity hit when it read a filename out of a comment. A check that
# fires on prose is one somebody eventually deletes.
_imports_provisioner = re.compile(r"^\s*(?:from|import)\s+provisioner\b", re.M)
ok("this suite stays shippable, so it runs where the handoff runs",
   not _imports_provisioner.search((ROOT / "tests/test_connector_handoff.py").read_text(encoding="utf-8")))

# AND THE SHAPE STAYS CHEAP TO IMPORT. bootstrap.sh runs the handoff under the SYSTEM python3,
# before the venv exists, so the shape may not drag in a third-party package. It must also not
# reach core.config, whose Settings captures os.environ AT IMPORT: putting the shape in
# core/claim.py (which imports core.state, which imports core.config) made merely importing the
# handoff script freeze every setting on the box, and broke the two assertions above this block.
_before = set(sys.modules)
import importlib  # noqa: E402
importlib.reload(handoff_shape)
_pulled = {m.split(".")[0] for m in set(sys.modules) - _before}
ok("importing the shape reaches no third-party package",
   not (_pulled - set(sys.stdlib_module_names) - {"core"}), str(sorted(_pulled)))
_shape_src = (ROOT / "core/handoff_shape.py").read_text(encoding="utf-8")
ok("...and imports nothing from core, so it can never reach core.config",
   "from core" not in _shape_src and "import core" not in _shape_src, _shape_src[:0])

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
