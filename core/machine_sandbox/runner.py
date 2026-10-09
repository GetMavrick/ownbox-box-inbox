"""Start and stop one sandboxed machine: its user, its folders, its door and its unit. Root only, Linux only.

Step 1 of docs/SCOPE_CUSTOM_MACHINES_FROM_A_REPO.md. Nothing in the box calls this yet: installing a machine (the
repository fetch, the checks and the owner's review card) is step 4. tests/test_machine_sandbox_walls.py drives it.

THE FOLDERS AND WHO OWNS THEM.
  code        root-owned. Bound read-only into the unit, so a machine never edits its own code.
  data        the machine's user, mode 0700. The only place it writes that outlives a restart.
  socket_dir  root:<machine's group>, mode 0750, under /run. Only the machine's user (and root) can enter it, and
              the socket inside is 0660. The kernel's peer check (broker.py) is the lock behind those two.
"""
from __future__ import annotations

import grp
import os
import pwd
import subprocess

from core.logging import get_logger
from core.machine_sandbox import unit
from core.machine_sandbox.broker import Broker

log = get_logger(__name__)


def ensure_user(slug: str) -> tuple[int, int]:
    """The machine's own system user and group, made the first time. -> (uid, gid)."""
    name = unit.user_name(slug)
    try:
        p = pwd.getpwnam(name)
    except KeyError:
        subprocess.run(["useradd", "--system", "--user-group", "--no-create-home", "--home-dir", "/nonexistent",
                        "--shell", "/usr/sbin/nologin", name], check=True, capture_output=True)
        p = pwd.getpwnam(name)
    if p.pw_uid == 0:
        raise unit.UnitError(f"{name} must not be root")
    return p.pw_uid, grp.getgrnam(name).gr_gid


def prepare(slug: str, *, code_dir: str, data_dir: str, socket_dir: str, uid: int, gid: int) -> None:
    if not os.path.isfile(os.path.join(code_dir, "__init__.py")):
        raise unit.UnitError(f"no machine at {code_dir}: a machine is a folder with __init__.py")
    os.makedirs(os.path.dirname(socket_dir), mode=0o755, exist_ok=True)
    os.makedirs(socket_dir, mode=0o750, exist_ok=True)
    os.chown(socket_dir, 0, gid)
    os.chmod(socket_dir, 0o750)
    os.makedirs(data_dir, mode=0o700, exist_ok=True)
    os.chown(data_dir, uid, gid)
    os.chmod(data_dir, 0o700)


class Running:
    """One machine, started. `stop()` stops the unit, then the door."""

    def __init__(self, slug: str, *, code_dir: str, data_dir: str, socket_dir: str | None = None,
                 sdk_dir: str | None = None, store=None, version: str = "", **door):
        paths = unit.host_paths(slug)
        self.slug = slug
        self.code_dir, self.data_dir = code_dir, data_dir
        self.socket_dir = socket_dir or paths["socket_dir"]
        self.sdk_dir = sdk_dir or paths["sdk"]
        self.uid, self.gid = ensure_user(slug)
        prepare(slug, code_dir=code_dir, data_dir=data_dir, socket_dir=self.socket_dir, uid=self.uid, gid=self.gid)
        self.broker = Broker(slug, uid=self.uid, gid=self.gid,
                             socket_path=os.path.join(self.socket_dir, "sdk.sock"), store=store, version=version,
                             **door)

    def start(self) -> None:
        self.broker.start()
        cmd = unit.argv(slug=self.slug, code_dir=self.code_dir, sdk_dir=self.sdk_dir, data_dir=self.data_dir,
                        socket_dir=self.socket_dir)
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            self.broker.stop()
            raise unit.UnitError(f"the machine's unit did not start: {(r.stderr or r.stdout).strip()[:300]}")
        log.info("machine_sandbox.started", machine=self.slug, unit=unit.unit_name(self.slug))

    def show(self) -> dict:
        r = subprocess.run(unit.show_argv(self.slug), capture_output=True, text=True)
        return dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)

    def stop(self) -> None:
        subprocess.run(unit.stop_argv(self.slug), capture_output=True)
        subprocess.run(["systemctl", "reset-failed", unit.unit_name(self.slug)], capture_output=True)
        self.broker.stop()
        log.info("machine_sandbox.stopped", machine=self.slug)
