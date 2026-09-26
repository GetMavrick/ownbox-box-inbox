"""Where a coworker runs: its own user, in a locked systemd unit, able to reach its workspace and nothing else.

docs/SCOPE_SHIFTS.md §4.1. The coworker is the AI CLI, and the AI reads pages anyone can write. So
it must be unable to reach the box's keys, database or code whatever a page talks it into, and that
is enforced by the kernel here, not by asking the AI nicely.

PROVEN ON A REAL BOX, 2026-09-26: a Pro-size droplet (s-1vcpu-2gb) built from the live golden image
(246870912), with ~/.claude/aios-release/coworker_sandbox_probe.sh. Under these properties a
process running as `aios-shift` could not list /opt/aios, read aios.db or .env, list /root, read
/proc/1/environ, or write /etc or anything outside its workspace. It could write its workspace
(bind-mounted from /opt/aios/my/coworkers/<slug>/workspace while /opt/aios itself is hidden) and
its private /tmp. The pinned CLI (2.1.278) ran there and ended a no-sign-in run as JSON.

THE CLI HAS TO LIVE OUTSIDE /root. The native installer puts the binary in
/root/.local/share/claude/versions/<v> and links /usr/local/bin/claude to it. As `aios-shift`,
behind ProtectHome, that link resolves to nothing and the CLI prints NOTHING and exits: a
coworker that would start on time and silently do no work. scripts/coworker_setup.sh copies the
pinned binary to /usr/local/lib/claude-code/<v>, and `cli_path()` refuses to hand back any path
under /root, so that failure is a FAILED receipt with a reason, never a silent one.

STDIN GOES THROUGH. `systemd-run --pipe` connects the caller's stdin to the unit, which is how the
prompt reaches the CLI (brain.run_agent writes it to stdin). The flip side: a caller whose stdin is
something else must pass DEVNULL, or the unit swallows it (the probe lost the rest of its own script
that way).

THE ENVIRONMENT DOES NOT GO THROUGH. A unit starts with systemd's environment, not the caller's, so
the AI credential reaches the CLI only through `env_file` (read by systemd as root, never a readable
property). The run's other files (the MCP config, the system prompt) are in a private run
directory (`run_dir()`), bound read-only into the unit at RUN_DIR. So the CLI must be given
RUN_DIR paths, not host paths. The host's /tmp is invisible behind PrivateTmp, which is why the
directory lives under /run.

IF THE CALLER GIVES UP, STOP THE UNIT. Killing `systemd-run --wait` leaves the unit running until
RuntimeMaxSec, and a coworker drafting after its receipt says FAILED is worse than one that
stopped. `stop_argv(unit)` is the command to run on a timeout.

Pure functions: nothing here runs anything, so all of it is testable on a laptop.
"""
from __future__ import annotations

import os
import re

USER = "aios-shift"
MOUNT = "/var/lib/aios-shift/work"           # where the workspace appears inside the unit
RUN_DIR = "/var/lib/aios-shift/run"          # where the run's private files appear, read-only
RUNS = "/run/aios-coworker"                  # host side: one private directory per run (tmpfs)
CLI_DIR = "/usr/local/lib/claude-code"
CLI_LINK = "/usr/local/bin/claude"
MEMORY_MAX = "1G"                            # Pro is 2 GB with ~1.5 GB free at rest; piece 7 measures
GRACE_SECONDS = 120                          # RuntimeMaxSec is the backstop behind the runner's own kill

# HIDDEN FROM THE COWORKER. /opt/aios holds the code, .env and the database; /root and /home are
# covered by ProtectHome. The rest hold a box's other secrets or state; '-' means a box without one
# is fine. Adding a path here is always safe. Removing one needs a reason in review.
HIDDEN = ("/opt/aios", "-/var/lib/aios", "-/var/lib/caddy", "-/etc/caddy", "-/etc/litestream.yml")

_SLUG = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
_MEMORY = re.compile(r"^[1-9][0-9]*[KMG]$")


class SandboxError(Exception):
    """The coworker cannot be started safely. The message is the receipt's reason."""


def cli_path(link: str = CLI_LINK) -> str:
    """The AI CLI's real path, which must be outside /root or the coworker cannot run it."""
    real = os.path.realpath(link)
    if not os.path.isfile(real):
        raise SandboxError(f"the AI CLI is not installed ({link} leads nowhere)")
    if real == "/root" or real.startswith("/root/") or real.startswith("/home/"):
        raise SandboxError(f"the AI CLI is at {real}, inside a home folder the coworker cannot "
                           f"read. The next box update moves it (scripts/coworker_setup.sh)")
    return real


def workspace(slug: str, root: str = "/opt/aios") -> str:
    """The coworker's workspace on the box. It appears inside the unit as MOUNT."""
    if not isinstance(slug, str) or not _SLUG.match(slug):
        raise SandboxError(f"not a coworker name: {slug!r}")
    return os.path.join(root, "my", "coworkers", slug, "workspace")


def properties(*, workspace_dir: str, minutes: int, env_file: str | None = None,
               ro_dir: str | None = None, memory_max: str = MEMORY_MAX) -> list:
    """The unit properties, as `-p` values, in a fixed order so a review can diff them."""
    if not isinstance(minutes, int) or isinstance(minutes, bool) or not 1 <= minutes <= 120:
        raise SandboxError(f"minutes must be 1 to 120, got {minutes!r}")
    if not _MEMORY.match(memory_max or ""):
        raise SandboxError(f"memory_max must look like 1G or 900M, got {memory_max!r}")
    for label, path in (("workspace", workspace_dir), ("ro_dir", ro_dir)):
        if path is not None and (not os.path.isabs(path) or ":" in path or " " in path):
            raise SandboxError(f"{label} must be an absolute path without ':' or spaces: {path!r}")
    props = [
        f"User={USER}",
        "ProtectSystem=strict",
        "ProtectHome=yes",
        "PrivateTmp=yes",
        "PrivateDevices=yes",
        "NoNewPrivileges=yes",
        "ProtectKernelTunables=yes",
        "ProtectKernelModules=yes",
        "ProtectControlGroups=yes",
        "RestrictSUIDSGID=yes",
        "CapabilityBoundingSet=",
        f"InaccessiblePaths={' '.join(HIDDEN)}",
        f"BindPaths={workspace_dir}:{MOUNT}",
        f"WorkingDirectory={MOUNT}",
        # HOME is the unit's private /tmp: the CLI keeps its own state there and it is thrown away
        # with the unit, so nothing it writes about itself lands in the owner's workspace.
        "Environment=HOME=/tmp",
        *([f"BindReadOnlyPaths={ro_dir}:{RUN_DIR}"] if ro_dir else []),
        f"MemoryMax={memory_max}",
        f"RuntimeMaxSec={minutes * 60 + GRACE_SECONDS}",
    ]
    # THE AI CREDENTIAL, BY FILE. An Environment= property is readable by any local user over
    # D-Bus (`systemctl show`); an EnvironmentFile is read by systemd as root before the unit drops
    # to aios-shift, and never becomes a property. The runner writes it 0600 and removes it after.
    if env_file:
        if not os.path.isabs(env_file):
            raise SandboxError(f"env_file must be an absolute path: {env_file!r}")
        props.append(f"EnvironmentFile={env_file}")
    return props


def argv(*, unit: str, command: list, workspace_dir: str, minutes: int,
         env_file: str | None = None, ro_dir: str | None = None,
         memory_max: str = MEMORY_MAX) -> list:
    """The full `systemd-run` command. Its stdin is the unit's stdin (see the module doc)."""
    if not re.match(r"^aios-coworker-[a-z0-9-]{1,80}$", unit or ""):
        raise SandboxError(f"unit must be aios-coworker-<name>, got {unit!r}")
    if not command or not isinstance(command, list):
        raise SandboxError("command must be a non-empty list")
    out = ["systemd-run", "--quiet", "--pipe", "--wait", "--collect", f"--unit={unit}"]
    for p in properties(workspace_dir=workspace_dir, minutes=minutes, env_file=env_file,
                        ro_dir=ro_dir, memory_max=memory_max):
        out += ["-p", p]
    return out + ["--"] + [str(c) for c in command]


def unit_name(slug: str, run_id: str) -> str:
    """aios-coworker-<slug>-<run id's safe part>, unique per run and legal as a unit name."""
    tail = re.sub(r"[^a-z0-9-]", "-", str(run_id).lower()).strip("-")[:40]
    if not _SLUG.match(slug or "") or not tail:
        raise SandboxError(f"cannot name a unit for {slug!r} / {run_id!r}")
    return f"aios-coworker-{slug}-{tail}"


def stop_argv(unit: str) -> list:
    """Stop a run's unit. Run this when the caller gives up on `systemd-run --wait`."""
    if not re.match(r"^aios-coworker-[a-z0-9-]{1,80}$", unit or ""):
        raise SandboxError(f"not a coworker unit: {unit!r}")
    return ["systemctl", "stop", unit]


def _group_id():
    try:
        import grp
        return grp.getgrnam(USER).gr_gid
    except (ImportError, KeyError):
        return None                          # a laptop without the user: tests, never a box


def run_dir(run_id: str, base: str = RUNS) -> str:
    """Make this run's private directory: root-owned, readable by the aios-shift group only (0750).

    The caller writes the run's files into it with `write_private()` and `write_env()`, passes it
    as `ro_dir` and `env_file`, and removes it when the run ends.
    """
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$", str(run_id)):
        raise SandboxError(f"not a run id: {run_id!r}")
    os.makedirs(base, mode=0o755, exist_ok=True)
    d = os.path.join(base, str(run_id))
    os.mkdir(d, 0o750)                       # a second run with the same id is a bug: refuse it
    gid = _group_id()
    if gid is not None:
        os.chown(d, 0, gid)
    os.chmod(d, 0o750)
    return d


def write_private(directory: str, name: str, text: str) -> str:
    """A file the coworker may READ, at RUN_DIR/<name> inside the unit. Returns the in-unit path."""
    if not re.match(r"^[a-z][a-z0-9_.-]{0,40}$", name):
        raise SandboxError(f"not a file name: {name!r}")
    path = os.path.join(directory, name)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    gid = _group_id()
    if gid is not None:
        os.chown(path, 0, gid)
    return f"{RUN_DIR}/{name}"


def write_env(directory: str, env: dict) -> str:
    """The unit's EnvironmentFile, readable by root only (systemd reads it before the unit drops to
    aios-shift). Returns the host path to pass as `env_file`."""
    lines = []
    for k, v in env.items():
        # systemd reads each line whole and interprets quotes and backslashes, so a value holding
        # any of them could arrive changed. Credentials never do; anything that does is refused.
        if not re.match(r"^[A-Z_][A-Z0-9_]{0,60}$", k) or any(c in str(v) for c in "\n\x00\"'\\"):
            raise SandboxError(f"cannot put {k!r} in an environment file")
        lines.append(f"{k}={v}\n")
    path = os.path.join(directory, "env")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("".join(lines))
    return path
