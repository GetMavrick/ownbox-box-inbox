"""The locked unit a custom machine runs in: pure functions, so every wall is testable on a laptop.

Decisions D1 (the sandbox) and D2 (no network) of docs/REVIEW_CUSTOM_MACHINES_DECISION_LOG.md. Proven on a box
built from the golden image by tests/test_machine_sandbox_walls.py, which attacks every wall below with a hostile
machine and also proves an honest machine still works behind them.

WHAT THE MACHINE SEES. Inside the unit, the filesystem is the system's read-only /usr plus four folders of its own:

    /opt/machine/code   its code, read-only (the pinned commit; a machine never edits itself)
    /opt/machine/sdk    the guest SDK, read-only (guest/), so `from core import sdk` works unchanged
    /opt/machine/data   its data folder, read-write: its own SQLite file and anything else it keeps
    /opt/machine/run    the folder holding its one socket to the box

Everything else a box keeps is covered by an empty, read-only tmpfs: /opt (the box's code, .env and database),
/var (backups, Caddy, logs), /run (D-Bus, systemd-resolved and every other machine's socket), /srv, /mnt, /media
and, through ProtectHome, /root and /home (the AI sign-in files). /etc is the same empty tmpfs with a short list of
files Python needs bound back in (ETC_KEEP), so a secret file a future feature drops into /etc is hidden by default
rather than by someone remembering to list it.

HIDING BY NAMESPACE, NOT BY FILE MODE. Every box service runs as root (no unit in deploy/ sets User=, found by
OSDev4's review), so files the box writes are root-owned and, under the default umask, readable by anyone. A
non-root user alone would read them; a mount namespace that does not contain them cannot.

NO NETWORK AT ALL (D2). `RestrictAddressFamilies=AF_UNIX` makes every other socket fail at creation, so there is
no raw socket and no DNS. `PrivateNetwork=yes` gives the unit a network namespace with only loopback, which also
privatises abstract Unix sockets. `IPAddressDeny=any` is the third lock. An outbound call is something the box
makes on the machine's behalf (`m.fetch`, step 2), against the hosts the owner approved.

ON A $12 BOX (D15). 256 MB of memory with no swap, half a CPU, 64 tasks and a low priority, so a machine that
loops or leaks hurts only itself. A single file is capped at 200 MB (LimitFSIZE; CPython ignores SIGXFSZ, so the
write fails with EFBIG instead of killing it). The data folder's total is checked by the runner (step 3).
"""
from __future__ import annotations

import hashlib
import os
import re

INSIDE = "/opt/machine"
CODE = f"{INSIDE}/code"
SDK = f"{INSIDE}/sdk"
DATA = f"{INSIDE}/data"
RUN = f"{INSIDE}/run"
SOCKET = f"{RUN}/sdk.sock"                    # the machine's end of its one door to the box

PYTHON = "/usr/bin/python3"                   # the system's: no site-packages, so stdlib only (D14)
BOOT = f"{SDK}/boot.py"

MEMORY_MAX = "256M"
CPU_QUOTA = "50%"
TASKS_MAX = 64
FILE_MAX = "200M"
NICE = 10
RESTART_SECONDS = 5
CRASHES_PER_HOUR = 5                          # then systemd stops restarting it and the box says why

# Covered by an empty read-only tmpfs. /home and /root are covered by ProtectHome=tmpfs.
HIDDEN_TREES = ("/opt", "/var", "/run", "/srv", "/mnt", "/media", "/etc")
# Gone entirely where present ('-' = a box without one is fine).
GONE = ("-/boot", "-/snap", "-/lost+found")
# The files from /etc that Python needs, bound back in read-only. Adding one needs a reason in review: each is a
# file a machine can read.
ETC_KEEP = ("/etc/ld.so.cache", "/etc/localtime", "/etc/passwd", "/etc/group", "/etc/nsswitch.conf")

_SLUG = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
_SAFE_PATH = re.compile(r"^/[A-Za-z0-9._/@+-]+$")


class UnitError(Exception):
    """The machine cannot be started safely. The message says why, in words for the owner."""


def check_slug(slug: str) -> str:
    if not isinstance(slug, str) or not _SLUG.match(slug):
        raise UnitError(f"not a machine name: {slug!r}")
    return slug


def user_name(slug: str) -> str:
    """The machine's own Linux user: `aiosm-<slug>`, or a shortened name with a hash when the slug is long.

    Linux user names stop at 32 characters and slugs may be 41, so a long slug keeps 17 characters and adds 8 of
    its hash. Two slugs never share a user, and the same slug always gets the same one."""
    check_slug(slug)
    name = f"aiosm-{slug}"
    if len(name) <= 32:
        return name
    return f"aiosm-{slug[:17]}-{hashlib.sha256(slug.encode()).hexdigest()[:8]}"


def unit_name(slug: str) -> str:
    return f"aios-machine-{check_slug(slug)}.service"


def _path(label: str, p: str) -> str:
    # A bind is written `src:dst`, and systemd splits on ':' and spaces: a path holding either would bind
    # something else. Refused here rather than escaped.
    if not isinstance(p, str) or not _SAFE_PATH.match(p) or ".." in p.split("/"):
        raise UnitError(f"{label} must be an absolute path of plain characters: {p!r}")
    return p


def properties(*, slug: str, code_dir: str, sdk_dir: str, data_dir: str, socket_dir: str) -> list[str]:
    """The unit's properties, as `-p` values, in a fixed order so a review can diff them."""
    user = user_name(slug)
    code_dir = _path("code_dir", code_dir)
    sdk_dir = _path("sdk_dir", sdk_dir)
    data_dir = _path("data_dir", data_dir)
    socket_dir = _path("socket_dir", socket_dir)
    return [
        # who it runs as
        f"User={user}",
        f"Group={user}",
        "UMask=0077",
        "NoNewPrivileges=yes",
        "CapabilityBoundingSet=",
        "AmbientCapabilities=",
        "RestrictSUIDSGID=yes",
        # what it can see (D1)
        "ProtectSystem=strict",
        "ProtectHome=tmpfs",
        *[f"TemporaryFileSystem={t}:ro" for t in HIDDEN_TREES],
        f"InaccessiblePaths={' '.join(GONE)}",
        f"BindReadOnlyPaths={' '.join('-' + f for f in ETC_KEEP)}",
        f"BindReadOnlyPaths={code_dir}:{CODE} {sdk_dir}:{SDK}",
        f"BindPaths={data_dir}:{DATA} {socket_dir}:{RUN}",
        "PrivateTmp=yes",
        "PrivateDevices=yes",
        "DevicePolicy=closed",
        "PrivateIPC=yes",
        "ProtectProc=invisible",
        "ProcSubset=pid",
        "ProtectKernelTunables=yes",
        "ProtectKernelModules=yes",
        "ProtectKernelLogs=yes",
        "ProtectControlGroups=yes",
        "ProtectClock=yes",
        "ProtectHostname=yes",
        "KeyringMode=private",
        "RemoveIPC=yes",
        # what it can reach (D2)
        "PrivateNetwork=yes",
        "RestrictAddressFamilies=AF_UNIX",
        "IPAddressDeny=any",
        # what it can ask the kernel for
        "RestrictNamespaces=yes",
        "RestrictRealtime=yes",
        "LockPersonality=yes",
        "MemoryDenyWriteExecute=yes",
        "SystemCallArchitectures=native",
        "SystemCallFilter=@system-service",
        "SystemCallFilter=~@privileged @resources @mount @debug @cpu-emulation @obsolete",
        "SystemCallErrorNumber=EPERM",
        # how much of the box it can use (D15)
        f"MemoryMax={MEMORY_MAX}",
        "MemorySwapMax=0",
        f"CPUQuota={CPU_QUOTA}",
        f"TasksMax={TASKS_MAX}",
        f"Nice={NICE}",
        f"LimitFSIZE={FILE_MAX}",
        "LimitNOFILE=256",
        "LimitCORE=0",
        # how it starts and when it stops coming back
        f"WorkingDirectory={DATA}",
        f"Environment=HOME={DATA} PYTHONDONTWRITEBYTECODE=1 OWNBOX_MACHINE={slug}",
        "Restart=on-failure",
        f"RestartSec={RESTART_SECONDS}",
        "StartLimitIntervalSec=3600",
        f"StartLimitBurst={CRASHES_PER_HOUR}",
    ]


def argv(*, slug: str, code_dir: str, sdk_dir: str, data_dir: str, socket_dir: str) -> list[str]:
    """The `systemd-run` command that starts the machine. It returns once the unit has started."""
    out = ["systemd-run", "--quiet", "--collect", "--service-type=exec", f"--unit={unit_name(slug)}"]
    for p in properties(slug=slug, code_dir=code_dir, sdk_dir=sdk_dir, data_dir=data_dir, socket_dir=socket_dir):
        out += ["-p", p]
    # -I: ignore PYTHON* variables and the user's site folder; -S: no site-packages at all, so a machine has the
    # standard library and the SDK and nothing the box happens to have installed (D14).
    return out + ["--", PYTHON, "-I", "-S", BOOT]


def stop_argv(slug: str) -> list[str]:
    return ["systemctl", "stop", unit_name(slug)]


def show_argv(slug: str) -> list[str]:
    return ["systemctl", "show", unit_name(slug), "-p",
            "ActiveState,SubState,Result,NRestarts,MainPID,CPUQuotaPerSecUSec,MemoryMax,TasksMax"]


def host_paths(slug: str, *, root: str = "/opt/aios") -> dict:
    """Where a machine's folders live on the box, outside the unit."""
    check_slug(slug)
    base = os.path.join(root, "my", "sandboxed", slug)
    return {
        "code": os.path.join(base, "code"),
        "data": os.path.join(base, "data"),
        "socket_dir": os.path.join("/run/aios-machines", slug),
        "sdk": os.path.join(os.path.dirname(os.path.abspath(__file__)), "guest"),
    }
