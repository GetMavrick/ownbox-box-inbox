#!/usr/bin/env python3
"""THE UNITS ARE REAL FILES THAT REACH A BOX.

The installed worker unit on production was three months stale (2026-09-04): editing
`deploy/*.service` changed nothing, because install_services.sh copies them once and deploy
only restarted. This suite is the part CI can answer — the units parse, they carry the
directives we rely on, and deploy.sh still contains the sync that makes an edit take effect.
"""
import configparser
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
UNITS = sorted((ROOT / "deploy").glob("aios-*.service"))
_fails: list[str] = []


def ok(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        _fails.append(name)


def test_every_unit_parses_and_names_its_program():
    ok(f"found the units ({len(UNITS)})", len(UNITS) >= 5)
    for u in UNITS:
        c = configparser.ConfigParser(strict=False)
        c.optionxform = str
        try:
            c.read_text = None
            c.read_string(u.read_text())
            parsed = True
        except configparser.Error:
            parsed = False
        ok(f"{u.name} parses", parsed)
        if not parsed:
            continue
        svc = dict(c["Service"]) if c.has_section("Service") else {}
        ok(f"{u.name} names an ExecStart", bool(svc.get("ExecStart")))
        ok(f"{u.name} reads the env file", svc.get("EnvironmentFile", "").endswith(".env"))


def test_every_long_running_unit_is_sandboxed():
    """The worker ran for three months without the directives its siblings had."""
    for u in UNITS:
        c = configparser.ConfigParser(strict=False)
        c.optionxform = str
        c.read_string(u.read_text())
        if not c.has_section("Service"):
            continue
        svc = dict(c["Service"])
        if svc.get("Type", "simple") == "oneshot":
            continue                       # backup/morning run and exit
        for d in ("NoNewPrivileges", "PrivateTmp", "ProtectSystem"):
            ok(f"{u.name} sets {d}", d in svc)


def test_deploy_still_syncs_units():
    # The unit sync moved out of deploy.sh on 2026-09-07: the box-side half is now one file,
    # scripts/box_update.sh, which deploy.sh pipes over ssh and the /deploy endpoint runs
    # locally. Same logic, one copy. Read it where it lives, and assert deploy.sh delegates
    # rather than keeping a second copy that would drift.
    d = (ROOT / "scripts" / "box_update.sh").read_text()
    ok("deploy.sh delegates to box_update.sh instead of carrying its own copy",
        "box_update.sh" in (ROOT / "scripts" / "deploy.sh").read_text())
    ok("deploy copies a changed unit into /etc/systemd/system",
       "/etc/systemd/system/$n" in d and "cp \"$f\"" in d)
    ok("and reloads systemd when it did", "daemon-reload" in d)
    ok("but only for units already installed", '[ -f "/etc/systemd/system/$n" ] || continue' in d)
    ok("verifying before it overwrites", "systemd-analyze verify" in d)
    ok("and keeping the previous copy", "UNIT_BACKUP" in d)


def main() -> int:
    print("deploy units")
    test_every_unit_parses_and_names_its_program()
    test_every_long_running_unit_is_sandboxed()
    test_deploy_still_syncs_units()
    print(f"\n{'ALL DEPLOY-UNIT TESTS PASS' if not _fails else str(len(_fails)) + ' FAILED'}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
