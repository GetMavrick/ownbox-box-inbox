#!/usr/bin/env python3
"""THE MANIFEST IS THE CONTRACT — and a contract nobody can break is not a contract.

Every rule in core/packs.py is proven here by planting a pack that violates it and reading
the field the refusal names. A validator that only ever sees valid packs has never been
tested (the plug-contract scan reported PASS on zero machines once; not again).
"""
import os
import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("AIOS_HERMETIC_TEST", "1")
from core import packs  # noqa: E402

_fails: list[str] = []


def ok(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  — {detail}" if (detail and not cond) else ""))
    if not cond:
        _fails.append(name)


GOOD = """manifest: 1
slug: med-spas-california
name: Med Spas — California
host: lead_machine
contract: 1
requires_foundation: "1.0"
kind: places-campaign
prefix: places
industry: med-spas
env: []
vendors: [google_places]
cost: "~$1.90 per 1,000 businesses"
config:
  enabled: false
  dry_run: true
  states: [CA]
suites: [tests/test_it.py]
"""


def plant(root: pathlib.Path, dirname="med_spas_california", text=GOOD, suite=True) -> pathlib.Path:
    d = root / dirname
    (d / "tests").mkdir(parents=True, exist_ok=True)
    (d / "machine.yaml").write_text(text)
    if suite:
        (d / "tests" / "test_it.py").write_text("print('ok')\n")
    return d


def refuses(text, field, dirname="med_spas_california", suite=True):
    """Plant a bad pack, load it, and assert the refusal names `field`."""
    tmp = pathlib.Path(tempfile.mkdtemp())
    d = plant(tmp, dirname, text, suite)
    try:
        packs.load(d)
        return False, "loaded — should have refused"
    except packs.PackError as e:
        return field in str(e), str(e)[:90]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_valid_pack_loads_and_is_discovered_in_every_root():
    tmp = pathlib.Path(tempfile.mkdtemp())
    roots = [tmp / "lead_machine" / "plugins", tmp / "content_machine" / "plugins", tmp / "machines"]
    for i, r in enumerate(roots):
        plant(r, f"pack_{i}", GOOD.replace("med-spas-california", f"pack-{i}"))
    found = packs.discover(roots)
    ok("discovery finds a pack in each of the three roots", [m["slug"] for m in found] == ["pack-0", "pack-1", "pack-2"], str([m["slug"] for m in found]))
    ok("a loaded manifest records where it lives", all(m["_path"].startswith(str(tmp)) for m in found))
    ok("an empty root is simply empty", packs.discover([tmp / "nowhere"]) == [])
    shutil.rmtree(tmp, ignore_errors=True)


def test_every_rule_refuses_and_names_its_field():
    ok("a missing required field is named", *refuses(GOOD.replace("host: lead_machine\n", ""), "host"))
    ok("an unknown host is refused", *refuses(GOOD.replace("host: lead_machine", "host: finance_machine"), "host"))
    ok("a pack that ships ARMED is refused", *refuses(GOOD.replace("enabled: false", "enabled: true"), "config.enabled"))
    ok("a pack with no enabled flag at all is refused", *refuses(GOOD.replace("  enabled: false\n", ""), "config.enabled"))
    ok("kind AND module together are refused", *refuses(GOOD + "module: marketing.lead_machine.plugins.x\n", "exactly one"))
    ok("neither kind nor module is refused", *refuses(GOOD.replace("kind: places-campaign\n", ""), "exactly one"))
    ok("an unknown kind is refused", *refuses(GOOD.replace("places-campaign", "telepathy"), "kind"))
    ok("a wrong contract is refused", *refuses(GOOD.replace("contract: 1", "contract: 2"), "contract"))
    ok("a bad prefix is refused", *refuses(GOOD.replace("prefix: places", "prefix: Bad-Prefix"), "prefix"))
    ok("a slug that does not match its directory is refused", *refuses(GOOD, "does not match directory", dirname="somewhere_else"))
    ok("a declared suite that does not exist is refused", *refuses(GOOD, "suites", suite=False))
    ok("a manifest format we do not know is refused", *refuses(GOOD.replace("manifest: 1", "manifest: 2"), "manifest"))
    ok("YAML that does not parse is refused, not swallowed", *refuses("slug: [unclosed", "YAML"))
    ok("a module outside the host's plugins/ is refused",
       *refuses(GOOD.replace("kind: places-campaign", "module: core.brain"), "module"))


def test_two_packs_with_one_slug_refuse():
    tmp = pathlib.Path(tempfile.mkdtemp())
    plant(tmp / "a", "med_spas_california"); plant(tmp / "b", "med_spas_california")
    try:
        packs.discover([tmp / "a", tmp / "b"]); ok("duplicate slug across roots refuses", False)
    except packs.PackError as e:
        ok("duplicate slug across roots refuses", "two packs claim slug" in str(e))
    shutil.rmtree(tmp, ignore_errors=True)


def test_foundation_version_gates_and_names_both_sides():
    ok("core/FOUNDATION_VERSION is a version", packs._VERSION.match(packs.foundation_version()) is not None, packs.foundation_version())
    good, msg = packs.requires_ok({"requires_foundation": "0.1"})
    ok("an older requirement is satisfied", good, msg)
    bad, msg = packs.requires_ok({"requires_foundation": "99.0"})
    ok("a newer requirement refuses and names both versions", not bad and "99.0" in msg and packs.foundation_version() in msg, msg)
    ok("and tells them how to update", "git pull" in msg)


def test_invalid_lists_the_broken_ones_for_the_doctor():
    tmp = pathlib.Path(tempfile.mkdtemp())
    plant(tmp, "good_one", GOOD.replace("med-spas-california", "good-one"))
    plant(tmp, "bad_one", GOOD.replace("med-spas-california", "bad-one").replace("enabled: false", "enabled: true"))
    bad = packs.invalid([tmp])
    ok("the doctor can see the broken pack and why", len(bad) == 1 and "bad_one" in bad[0][0] and "enabled" in bad[0][1], str(bad))
    shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    print("machine packs")
    test_a_valid_pack_loads_and_is_discovered_in_every_root()
    test_every_rule_refuses_and_names_its_field()
    test_two_packs_with_one_slug_refuse()
    test_foundation_version_gates_and_names_both_sides()
    test_invalid_lists_the_broken_ones_for_the_doctor()
    print(f"\n{'ALL MACHINE-PACK TESTS PASS' if not _fails else str(len(_fails)) + ' FAILED'}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
