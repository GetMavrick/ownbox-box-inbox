"""A deliberately hostile machine: it tries every way out of its sandbox and writes down what happened.

Used only by tests/test_machine_sandbox_walls.py, which runs it in the real unit and judges the results. It
reports what it SAW, never a verdict, so a wall that quietly opens shows up as "OPEN: <what it got>" and the test
decides. It also does the honest things a machine needs (its data folder, its settings, a scheduled job) so the
walls are proven not to have broken the door.

Every attack except memory runs at import. Memory is last and runs once (a marker in the data folder): the unit is
killed when it passes 256 MB, systemd restarts it, and the restarted machine finds the marker and carries on.
"""
import ctypes
import ctypes.util
import json
import os
import socket
import time

from core import sdk

m = sdk.machine("hostile-walls")
R = {}


def attempt(name, fn):
    try:
        got = fn()
        R[name] = "OPEN: " + str(got)[:120]
    except Exception as e:                                         # noqa: BLE001 — every refusal is the point
        code = getattr(e, "errno", None) or getattr(e, "code", "")
        R[name] = f"blocked: {type(e).__name__} {code}".strip()


def read(p):
    with open(p, "rb") as f:
        return f.read(64)


def write(p):
    with open(p, "w") as f:
        f.write("hostile")
    return "wrote " + p


def seen(name, fn):
    try:
        R[name] = "SEEN: " + json.dumps(fn())
    except Exception as e:                                         # noqa: BLE001
        R[name] = f"blocked: {type(e).__name__}"


# ── the box's secrets, and decoys the test planted with a token in them ────────────────────────────────────────
for p in ("/opt/aios/.env", "/opt/aios/aios.db", "/opt/aios/config/settings.yaml", "/opt/aios/core/sdk.py",
          "/etc/litestream.yml", "/etc/caddy/Caddyfile", "/etc/shadow", "/etc/ssh/ssh_host_ed25519_key",
          "/root/.claude/.credentials.json", "/root/.codex/auth.json", "/root/.ssh/authorized_keys",
          "/var/lib/caddy/.local/share/caddy", "/var/log/syslog",
          "/opt/aios-walls-decoy/.env", "/var/lib/aios-walls-decoy/secret", "/etc/aios-walls-decoy.yml",
          "/root/aios-walls-decoy", "/home/aios-walls-decoy/secret", "/srv/aios-walls-decoy",
          "/run/aios-walls-decoy", "/tmp/aios-walls-decoy", "/var/tmp/aios-walls-decoy",
          "/opt/aios-walls-decoy/other-machine/data/secret",
          "/aios-walls-decoy-top/secret", "/aios-walls-decoy-top.txt", "/swapfile",
          "/proc/1/environ", "/proc/1/cmdline", "/proc/1/root/etc/hostname", "/dev/mem", "/dev/sda", "/dev/vda"):
    attempt("read:" + p, lambda p=p: read(p))

for d in ("/opt", "/var", "/run", "/etc", "/home", "/root", "/srv", "/mnt", "/media", "/tmp", "/dev"):
    seen("list:" + d, lambda d=d: sorted(os.listdir(d)))
seen("walk:/run files", lambda: sorted(os.path.join(d, f) for d, _, fs in os.walk("/run") for f in fs))
seen("list:/proc pids", lambda: sorted(int(x) for x in os.listdir("/proc") if x.isdigit()))
seen("my pid", lambda: os.getpid())
seen("whoami", lambda: [os.getuid(), os.geteuid(), os.getgid()])

for p in ("/opt/machine/code/x", "/opt/machine/sdk/x", "/opt/machine/sdk/core/sdk.py", "/etc/x", "/usr/x",
          "/opt/x", "/var/x", "/run/x"):
    attempt("write:" + p, lambda p=p: write(p))
attempt("chmod:my socket folder", lambda: os.chmod("/opt/machine/run", 0o777))

# ── the network ────────────────────────────────────────────────────────────────────────────────────────────────
attempt("socket:inet tcp", lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM))
attempt("socket:inet6 udp", lambda: socket.socket(socket.AF_INET6, socket.SOCK_DGRAM))
attempt("socket:netlink", lambda: socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, 0))
attempt("socket:packet", lambda: socket.socket(socket.AF_PACKET, socket.SOCK_RAW))
attempt("dns:example.com", lambda: socket.getaddrinfo("example.com", 443))
attempt("dns:metadata name", lambda: socket.getaddrinfo("metadata.google.internal", 80))


def unix(path):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(2)
    s.connect(path)
    return "connected " + repr(path)


for p in ("/run/dbus/system_bus_socket", "/var/run/dbus/system_bus_socket", "/run/systemd/resolve/io.systemd.Resolve",
          "/run/systemd/private", "/run/aios-machines/honest-twin/sdk.sock", "\0aios-walls-decoy"):
    attempt("unix:" + p.replace("\0", "@"), lambda p=p: unix(p))

# ── the kernel ─────────────────────────────────────────────────────────────────────────────────────────────────
libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)


def checked(rc):
    if rc != 0:
        raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
    return rc


attempt("setuid root", lambda: os.setuid(0))
attempt("unshare user namespace", lambda: os.unshare(os.CLONE_NEWUSER))
attempt("unshare net namespace", lambda: os.unshare(os.CLONE_NEWNET))
attempt("mount tmpfs", lambda: checked(libc.mount(b"tmpfs", b"/opt/machine/data", b"tmpfs", 0, None)))
attempt("ptrace pid 1", lambda: checked(libc.ptrace(16, 1, None, None)))
attempt("kill pid 1", lambda: os.kill(1, 0))
attempt("raise priority", lambda: os.nice(-5))

# ── the box's door, used wrongly ───────────────────────────────────────────────────────────────────────────────
door = sdk._door()
attempt("box:think without a grant", lambda: door.call("think", task="x", prompt="leak"))
attempt("box:approve its own proposal", lambda: door.call("decide", approval="1", yes=True))
attempt("box:another machine's setting key", lambda: door.call("save_setting", key="../my_other", value=1))
attempt("box:setting over 64 KB", lambda: door.call("save_setting", key="big", value="x" * 70000))
attempt("box:sdk 2 hello", lambda: door.call("hello", sdk=2, slug="hostile-walls"))
attempt("box:hello as another machine", lambda: door.call("hello", sdk=1, slug="honest-twin"))

# ── the box's fetch (step 2): the test grants fetch to crm-good.com and metadata-trick.com, and the key "crm" for
# crm-good.com only. Its fake server echoes every header it receives, the worst case for a key. ──────────────────
attempt("fetch:a host not granted", lambda: m.fetch("https://evil.com/"))
attempt("fetch:a granted name that points at the metadata address", lambda: m.fetch("https://metadata-trick.com/"))
attempt("fetch:http", lambda: m.fetch("http://crm-good.com/"))
attempt("fetch:the key to another host", lambda: m.fetch("https://metadata-trick.com/", key="crm"))
attempt("fetch:setting the key's header itself", lambda: m.fetch("https://crm-good.com/", key="crm",
                                                                    headers={"Authorization": "x"}))
attempt("key:read it", lambda: m.secret("crm"))
try:
    echo = m.fetch("https://crm-good.com/contacts", key="crm")
    R["honest:fetch with its key"] = f"SEEN: {echo.status} {echo.text[:300]}"
except Exception as e:                                             # noqa: BLE001
    R["honest:fetch with its key"] = f"FAILED: {type(e).__name__} {e}"

# ── the box's resources ────────────────────────────────────────────────────────────────────────────────────────
kids = []
for _ in range(120):
    try:
        pid = os.fork()
    except OSError:
        break
    if pid == 0:
        time.sleep(3)
        os._exit(0)
    kids.append(pid)
R["fork:children made of 120"] = f"SEEN: {len(kids)}"
for pid in kids:
    try:
        os.waitpid(pid, 0)
    except OSError:
        pass


def big_file():
    p = os.path.join(m.data_dir(), "big")
    chunk = b"x" * (1 << 20)
    try:
        with open(p, "wb") as f:
            for _ in range(250):
                f.write(chunk)
                f.flush()
        return "wrote 250 MB"
    finally:
        os.unlink(p)


attempt("disk:one 250 MB file", big_file)

# The keys this machine reports through, made first: the settings flood below fills the rest.
for k in ("honest", "walls", "job_ran", "memory", "flood"):
    if m.setting(k) is None:
        m.save_setting(k, False if k == "job_ran" else "")

# ── the honest half: the walls must not have broken the door ──────────────────────────────────────────────────
honest = os.path.join(m.data_dir(), "honest.txt")
with open(honest, "w") as f:
    f.write("kept")
R["honest:data folder"] = "ok" if open(honest).read() == "kept" else "FAILED"
m.save_setting("honest", {"n": 1})
R["honest:setting round trip"] = "ok" if m.setting("honest") == {"n": 1} else "FAILED"

m.save_setting("walls", R)


@m.every(15)
def tick():
    with open(os.path.join(m.data_dir(), "ticks"), "a") as f:
        f.write("t\n")
    m.save_setting("job_ran", True)
    if not m.setting("flood"):
        flood()


def flood():
    """The box's own database and logs, flooded through the door (OSDev4's review of #2065). Run from the job, after
    the machine has reported, and the minute waited out after, because a flood spends the door's whole rate."""
    kept = logged = 0
    for i in range(300):
        try:
            m.save_setting(f"flood{i}", i)
            kept += 1
        except Exception:                                          # noqa: BLE001 — a refusal is the point
            pass
    for i in range(500):
        try:
            door.call("log", level="info", event="flood", fields={"i": i})
            logged += 1
        except Exception:                                          # noqa: BLE001
            pass
    time.sleep(62)
    m.save_setting("flood", {"settings kept of 300": kept, "log lines taken of 500": logged})


# ── memory, last and once ─────────────────────────────────────────────────────────────────────────────────────
marker = os.path.join(m.data_dir(), "memory_tried")
if not os.path.exists(marker):
    open(marker, "w").close()
    hog = b"\x01" * (2 << 30)       # 2 GB, every page written (a zeroed bytearray would never be touched)
    m.save_setting("memory", "OPEN: allocated 2 GB")
else:
    m.save_setting("memory", "restarted after the memory attack")
