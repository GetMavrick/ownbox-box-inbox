"""A sandboxed machine's unit, door and guest SDK, checked on any laptop (no systemd, no root).

docs/SCOPE_CUSTOM_MACHINES_FROM_A_REPO.md (version 2), build step 1. The walls themselves are proven by attacking
them in a real unit (tests/test_machine_sandbox_walls.py, root and systemd only). This suite makes sure they cannot
quietly drift between those runs, and proves the door's protocol end to end through the real guest SDK.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * any wall leaving the unit: a hidden tree, the network locks, the namespace, syscall or privilege locks, or a
    resource limit; or the unit gaining root, a capability, an environment file or a writable bind;
  * a long or odd machine name making a bad Linux user or a path that binds something else;
  * the door answering a connection from the wrong user, a line over 1 MB, a line that isn't JSON, a call it
    doesn't offer, a forged key, an oversized value, a newer SDK or another machine's name; holding more than
    four connections; or running a job twice at once or early;
  * the guest SDK importing anything but the standard library, missing a name core/sdk.py promises, or
    disagreeing with the box about the socket, the data folder or the size of a line;
  * the walls suite falling out of CI.

Run: python tests/test_machine_sandbox.py
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import pathlib
import socket
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "sandbox.db")

from core import sdk as box_sdk  # noqa: E402
from core.machine_sandbox import broker, unit  # noqa: E402

FAILS: list[str] = []


def ok(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:400]}"))
    if not cond:
        FAILS.append(label)


def refused(fn, *a, **k) -> str:
    try:
        fn(*a, **k)
    except unit.UnitError as e:
        return str(e)
    return ""


GUEST = ROOT / "core" / "machine_sandbox" / "guest"
SLUG = "lead-machine"
KW = dict(slug=SLUG, code_dir="/opt/aios/my/sandboxed/lead-machine/code", sdk_dir=str(GUEST),
          data_dir="/opt/aios/my/sandboxed/lead-machine/data", socket_dir="/run/aios-machines/lead-machine")


def test_the_unit_carries_every_wall():
    print("test_the_unit_carries_every_wall")
    props = unit.properties(**KW)
    required = [f"User={unit.user_name(SLUG)}", "UMask=0077", "NoNewPrivileges=yes", "CapabilityBoundingSet=",
                "AmbientCapabilities=", "RestrictSUIDSGID=yes", "ProtectSystem=strict", "ProtectHome=tmpfs",
                *[f"TemporaryFileSystem={t}:ro" for t in ("/opt", "/var", "/run", "/srv", "/mnt", "/media", "/etc")],
                "PrivateTmp=yes", "PrivateDevices=yes", "DevicePolicy=closed", "PrivateIPC=yes",
                "ProtectProc=invisible", "ProcSubset=pid", "ProtectKernelTunables=yes", "ProtectKernelModules=yes",
                "ProtectKernelLogs=yes", "ProtectControlGroups=yes", "KeyringMode=private",
                "PrivateNetwork=yes", "RestrictAddressFamilies=AF_UNIX", "IPAddressDeny=any",
                "RestrictNamespaces=yes", "RestrictRealtime=yes", "LockPersonality=yes", "MemoryDenyWriteExecute=yes",
                "SystemCallArchitectures=native", "SystemCallFilter=@system-service", "SystemCallErrorNumber=EPERM",
                "MemoryMax=256M", "MemorySwapMax=0", "CPUQuota=50%", "TasksMax=64", "LimitFSIZE=200M",
                "LimitCORE=0", "Restart=on-failure", "StartLimitIntervalSec=3600", "StartLimitBurst=5"]
    missing = [p for p in required if p not in props]
    ok("every wall the real-unit proof relied on is in the unit", not missing, missing)
    ok("the syscall filter also refuses privileged, mount and debug calls",
       any(p.startswith("SystemCallFilter=~") and all(g in p for g in ("@privileged", "@mount", "@debug"))
           for p in props), [p for p in props if p.startswith("SystemCallFilter")])
    ok("it never runs as root, and gains no capability",
       not any(p in ("User=root", "User=0") or (p.startswith("AmbientCapabilities=") and p != "AmbientCapabilities=")
               or (p.startswith("CapabilityBoundingSet=") and p != "CapabilityBoundingSet=") for p in props))
    ok("no environment file and no secret in its environment",
       not any(p.startswith("EnvironmentFile") for p in props)
       and [p for p in props if p.startswith("Environment=")] ==
       [f"Environment=HOME={unit.DATA} PYTHONDONTWRITEBYTECODE=1 OWNBOX_MACHINE={SLUG}"])
    rw = [p for p in props if p.startswith("BindPaths=")]
    ro = [p for p in props if p.startswith("BindReadOnlyPaths=")]
    ok("only its data folder and its socket's folder are writable binds",
       rw == [f"BindPaths={KW['data_dir']}:{unit.DATA} {KW['socket_dir']}:{unit.RUN}"], rw)
    ok("its code and the SDK are bound read-only",
       f"BindReadOnlyPaths={KW['code_dir']}:{unit.CODE} {KW['sdk_dir']}:{unit.SDK}" in ro, ro)
    ok("/etc comes back as five named files, each optional, and nothing else",
       f"BindReadOnlyPaths={' '.join('-' + f for f in unit.ETC_KEEP)}" in ro and len(unit.ETC_KEEP) == 5
       and all(f.startswith("/etc/") and f.count("/") == 2 for f in unit.ETC_KEEP), unit.ETC_KEEP)
    ok("the properties come in a fixed order, so a review can diff them", props == unit.properties(**KW))
    cmd = unit.argv(**KW)
    ok("it starts the system Python with -I -S on the guest's boot file",
       cmd[-4:] == ["/usr/bin/python3", "-I", "-S", f"{unit.SDK}/boot.py"] and cmd[0] == "systemd-run"
       and f"--unit=aios-machine-{SLUG}.service" in cmd, cmd[:6] + cmd[-4:])
    ok("every property is passed as its own -p", cmd.count("-p") == len(props))


def test_names_and_paths():
    print("\ntest_names_and_paths")
    long_a, long_b = "a" + "b" * 40, "a" + "b" * 39 + "c"
    ok("a short slug's user reads plainly", unit.user_name("lead-machine") == "aiosm-lead-machine")
    ok("a 41-character slug still makes a legal user (32 characters at most)",
       len(unit.user_name(long_a)) <= 32 and len(unit.user_name(long_b)) <= 32)
    ok("two long slugs sharing a start get different users", unit.user_name(long_a) != unit.user_name(long_b))
    ok("the same slug always gets the same user", unit.user_name(long_a) == unit.user_name(long_a))
    for bad in ("Root", "../x", "a", "x" * 42, "lead machine", "", None):
        ok(f"{bad!r} is refused as a machine name", bool(refused(unit.user_name, bad)))
    for label, path in (("a colon", "/opt/aios/my/x:/etc"), ("a space", "/opt/aios/my x"),
                        ("a parent step", "/opt/aios/my/../../etc"), ("a relative path", "opt/aios")):
        ok(f"a path with {label} is refused", bool(refused(unit.properties, **{**KW, "data_dir": path})))


class Store:
    def __init__(self):
        self.d, self.by = {}, []

    def get(self, key, default):
        return self.d.get(key, default)

    def put(self, key, value, by):
        self.d[key] = value
        self.by.append(by)


class Peer:
    uid = 4242


def _door(store=None, clock=time.time):
    d = tempfile.mkdtemp(dir="/tmp")
    b = broker.Broker(SLUG, uid=4242, socket_path=os.path.join(d, "s.sock"), peer_uid=lambda c: Peer.uid,
                      store=store or Store(), clock=clock)
    b.start()
    return b


def _raw(path, data: bytes):
    """Send `data`, read one line. -> (that line, whether the box then closed the connection)."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect(path)
    try:
        s.sendall(data)
    except OSError:
        pass
    f = s.makefile("rb")
    try:
        first = f.readline()
    except OSError:
        first = b""
    s.settimeout(0.5)
    try:
        closed = f.readline() == b""
    except OSError:                                        # still open: a timeout, not a hang-up
        closed = False
    s.close()
    return first, closed


def test_the_door():
    print("\ntest_the_door")
    store = Store()
    b = _door(store)
    try:
        call = lambda c, **a: b.handle({"id": 1, "call": c, "args": a})      # noqa: E731
        ok("hello answers sdk 1 and the data folder", call("hello", sdk=1, slug=SLUG)["result"] ==
           {"sdk": 1, "slug": SLUG, "data": unit.DATA})
        ok("a newer SDK is refused by code", call("hello", sdk=2, slug=SLUG)["code"] == "sdk_too_new")
        ok("another machine's name is refused", call("hello", sdk=1, slug="other")["code"] == "wrong_machine")
        ok("a setting round-trips", call("save_setting", key="last", value={"n": 1})["ok"]
           and call("setting", key="last")["result"] == {"n": 1} and store.by == [SLUG])
        ok("the store is the machine's own: a forged key is refused",
           call("save_setting", key="../my_other", value=1)["code"] == "bad_key")
        ok("a value over 64 KB is refused", call("save_setting", key="big", value="x" * 70000)["code"] == "too_large")
        ok("a call the box does not offer is refused by code",
           call("people", q="x")["code"] == "unknown_call" and call("decide", yes=True)["code"] == "unknown_call")
        ok("the reply never echoes what a refused call carried", "leak" not in json.dumps(call("decide", p="leak")))

        Peer.uid = 4242
        first, closed = _raw(b.socket_path, b'{"id":5,"call":"hello","args":{"sdk":1,"slug":"lead-machine"}}\n')
        ok("over the socket, its own user gets an answer with the same id, and the line stays open",
           json.loads(first or b"{}").get("id") == 5 and not closed, first)
        Peer.uid = 999
        first, closed = _raw(b.socket_path, b'{"id":1,"call":"hello","args":{"sdk":1,"slug":"lead-machine"}}\n')
        ok("any other user is closed on without a byte", first == b"" and closed and 999 in b.refused_peers, first)
        Peer.uid = 4242
        first, closed = _raw(b.socket_path, b"x" * (broker.MAX_LINE + 10) + b"\n")
        ok("a line over 1 MB is refused and the connection closed",
           json.loads(first or b"{}").get("code") == "too_large" and closed, first[:80])
        first, closed = _raw(b.socket_path, b"not json\n")
        ok("a line that is not JSON is refused and the connection closed",
           json.loads(first or b"{}").get("code") == "bad_request" and closed, first[:80])

        held = []
        for _ in range(broker.MAX_CONNECTIONS):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(b.socket_path)
            s.sendall(b'{"id":1,"call":"hello","args":{"sdk":1,"slug":"lead-machine"}}\n')
            s.makefile("rb").readline()
            held.append(s)
        first, _ = _raw(b.socket_path, b'{"id":1,"call":"hello","args":{"sdk":1,"slug":"lead-machine"}}\n')
        ok(f"a {broker.MAX_CONNECTIONS + 1}th connection is refused",
           json.loads(first or b"{}").get("code") == "too_many_connections", first)
        for s in held:
            s.close()
    finally:
        b.stop()


def test_jobs_run_once_and_on_time():
    print("\ntest_jobs_run_once_and_on_time")
    now = [1000.0]
    b = _door(clock=lambda: now[0])
    try:
        call = lambda c, **a: b.handle({"id": 1, "call": c, "args": a})      # noqa: E731
        ok("a job more often than every 15 seconds is refused", call("every", name="tick", seconds=5)["code"] ==
           "bad_every")
        ok("a job is registered", call("every", name="tick", seconds=60)["ok"])
        job = call("next", wait=0)["result"]["job"]
        ok("its first run is due at once", job and job["name"] == "tick", job)
        ok("a run still going is never handed out twice", call("next", wait=0)["result"]["job"] is None)
        ok("done is accepted for the run it handed out", call("done", run=job["run"], ok=True)["ok"])
        ok("...and only once", call("done", run=job["run"], ok=True)["code"] == "unknown_run")
        ok("the next run is not due before its period", call("next", wait=0)["result"]["job"] is None)
        now[0] += 60
        again = call("next", wait=0)["result"]["job"]
        ok("it is due after its period", again and again["name"] == "tick" and again["run"] != job["run"], again)
        call("done", run=again["run"], ok=False, error="ValueError: boom")
        ok("a failed run is recorded with its reason", b.results[-1]["ok"] is False and "boom" in
           b.results[-1]["error"])
        for i in range(broker.MAX_JOBS):
            call("every", name=f"j{i}", seconds=60)
        ok(f"a machine has at most {broker.MAX_JOBS} jobs", call("every", name="one_more", seconds=60)["code"] ==
           "too_many_jobs")
        t0 = time.monotonic()
        b2 = _door()
        r = b2.handle({"id": 1, "call": "next", "args": {"wait": 0.3}})
        b2.stop()
        ok("a long-poll with nothing due waits, then answers no job", r["result"]["job"] is None
           and 0.25 <= time.monotonic() - t0 < 3)
    finally:
        b.stop()


def _load_guest():
    spec = importlib.util.spec_from_file_location("guest_sdk_under_test", GUEST / "core" / "sdk.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_guest_sdk():
    print("\ntest_the_guest_sdk")
    for f in (GUEST / "core" / "sdk.py", GUEST / "boot.py", GUEST / "core" / "__init__.py"):
        mods = set()
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Import):
                mods |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                mods.add((node.module or "").split(".")[0])
        outside = sorted(m for m in mods if m not in sys.stdlib_module_names and m not in ("core", "__future__"))
        ok(f"{f.relative_to(ROOT)} imports the standard library only", not outside, outside)
    g = _load_guest()
    missing = [n for n in box_sdk.SEAMS if not (hasattr(g, n) or hasattr(g.Machine, n))]
    ok("every name core/sdk.py promises exists in the guest SDK", not missing, missing)
    ok("the guest and the box agree on the socket, the data folder and a line's size",
       g.SOCKET == unit.SOCKET and g.DATA == unit.DATA and g._MAX_LINE == broker.MAX_LINE
       and g.VERSION == box_sdk.VERSION == broker.SDK_VERSION and g.STYLE_CLASSES == box_sdk.STYLE_CLASSES)
    m = g.Machine(SLUG)
    try:
        m.person("email", "ava@glow.example")
        said = ""
    except g.NotYet as e:
        said = str(e)
    ok("a call that arrives later says so and names its step", "m.person" in said and "step 3" in said, said)

    store = Store()
    b = _door(store)
    try:
        g._DOOR = g._Door(b.socket_path)
        m.save_setting("seen", [1, 2])
        ok("through the real guest code: a setting round-trips", m.setting("seen") == [1, 2] and
           store.d["seen"] == [1, 2])
        ran = []
        m.every(30, lambda: ran.append(1), name="sync")
        job = g._door().call("next", wait=0)["job"]
        ok("through the real guest code: m.every registers a job the box hands back", job["name"] == "sync"
           and "sync" in g._JOBS, job)
        try:
            g._door().call("save_setting", key="../x", value=1)
            code = ""
        except g.BoxRefused as e:
            code = e.code
        ok("a refusal reaches the machine as BoxRefused with the box's code", code == "bad_key", code)
        b.stop()
        b = _door(store)                                   # the box restarted: a new socket at a new path
        g._DOOR._path = b.socket_path
        ok("the guest reconnects once when the box restarted", m.setting("seen") == [1, 2])
    finally:
        b.stop()


def test_the_review_fixes():
    """OSDev4's review of #2065: the top of / is an allowlist, a machine's settings have a total, and its log lines a
    rate, so neither the box's disk nor its own logs are the machine's to fill."""
    print("\ntest_the_review_fixes")
    top = [("/bin", False), ("/usr", True), ("/opt", True), ("/swapfile", False), ("/backups", True),
           ("/lost+found", True), ("/tmp", True)]
    props = unit.properties(**KW, top=top)
    ok("a file at / that is not the system is made inaccessible (the swap file holds the box's memory)",
       "InaccessiblePaths=-/swapfile" in props, [p for p in props if "swapfile" in p])
    ok("a folder at / that is not the system is covered", "TemporaryFileSystem=/backups:ro" in props)
    ok("the system itself, and what the unit already covers, gets no second cover",
       not any(x in p for p in props for x in ("=/usr", "=/bin", "=-/bin", "=/tmp:", "=/opt:ro /", "-/lost+found:"))
       and props.count("TemporaryFileSystem=/opt:ro") == 1, [p for p in props if p.startswith(("Temporary", "Inacc"))])
    ok("an entry at / whose name the unit can't write stops the start, never shows",
       bool(refused(unit.properties, **KW, top=[("/odd name", True)])))
    live = unit.properties(**KW)
    missed = [p for p, _ in unit.top_entries() if os.path.basename(p) not in unit.TOP_KEEP
              and f"TemporaryFileSystem={p}:ro" not in live and f"InaccessiblePaths=-{p}" not in live]
    ok("on this computer, every entry at / that is not the system is covered", not missed, missed)

    class Counted(Store):
        def usage(self, key):
            sizes = {k: len(json.dumps(v)) for k, v in self.d.items()}
            return len(sizes), sum(sizes.values()), sizes.get(key)

    rate = broker.CALLS_PER_MINUTE
    broker.CALLS_PER_MINUTE = 10 ** 6                # each cap on its own; the door's own rate is checked below
    b = broker.Broker(SLUG, uid=1, socket_path="/nonexistent/s.sock", peer_uid=lambda c: 1, store=Counted())
    call = lambda c, **a: b.handle({"id": 1, "call": c, "args": a})          # noqa: E731
    codes = [call("save_setting", key=f"k{i}", value=i).get("code") for i in range(broker.MAX_SETTINGS + 5)]
    ok(f"a machine keeps at most {broker.MAX_SETTINGS} settings", codes[:broker.MAX_SETTINGS] ==
       [None] * broker.MAX_SETTINGS and set(codes[broker.MAX_SETTINGS:]) == {"too_many_settings"}, codes[-6:])
    ok("...and can still change one it has", call("save_setting", key="k1", value="changed").get("ok"))
    b = broker.Broker(SLUG, uid=1, socket_path="/nonexistent/s.sock", peer_uid=lambda c: 1, store=Counted())
    call = lambda c, **a: b.handle({"id": 1, "call": c, "args": a})          # noqa: E731
    big = "x" * (broker.MAX_VALUE - 100)
    codes = [call("save_setting", key=f"b{i}", value=big).get("code") for i in range(40)]
    ok(f"...and {broker.MAX_SETTINGS_BYTES >> 20} MB of them in all", "settings_full" in codes
       and sum(len(json.dumps(v)) for v in b._store.d.values()) <= broker.MAX_SETTINGS_BYTES, codes[-3:])
    sent = [call("log", event="flood", fields={"i": i}) for i in range(broker.LOGS_PER_MINUTE * 3)]
    ok(f"a machine logs at most {broker.LOGS_PER_MINUTE} lines a minute; the rest are refused and counted",
       sum(1 for r in sent if r.get("ok")) == broker.LOGS_PER_MINUTE
       and {r.get("code") for r in sent if not r.get("ok")} == {"rate_limited"}
       and b.logs_dropped == broker.LOGS_PER_MINUTE * 2, b.logs_dropped)
    broker.CALLS_PER_MINUTE = rate
    rows = []
    b = broker.Broker(SLUG, uid=1, socket_path="/nonexistent/s.sock", peer_uid=lambda c: 1, store=Counted(),
                      audit_fn=lambda *a, **k: rows.append(a))
    got = [b.handle({"id": 1, "call": "setting", "args": {"key": "a"}}) for _ in range(broker.CALLS_PER_MINUTE * 4)]
    ok(f"the door takes {broker.CALLS_PER_MINUTE} calls a minute; the rest are refused and written nowhere, so a "
       f"flood can't fill the box's audit log", sum(1 for r in got if r.get("ok")) == broker.CALLS_PER_MINUTE
       and len(rows) == broker.CALLS_PER_MINUTE and b.calls_dropped == broker.CALLS_PER_MINUTE * 3,
       [len(rows), b.calls_dropped])
    ok("...while the long-poll is never counted against it",
       b.handle({"id": 1, "call": "next", "args": {"wait": 0}}).get("ok"))


def test_the_walls_suite_runs_in_ci():
    print("\ntest_the_walls_suite_runs_in_ci")
    wf = ROOT / ".github" / "workflows" / "tests.yml"
    if not wf.exists():                                    # a box has no repository
        return
    src = wf.read_text()
    ok("this suite is in the workflow's suite list", "test_machine_sandbox \\" in src)
    ok("the walls suite runs under sudo in its own step", "sudo" in src and
       "tests/test_machine_sandbox_walls.py" in src)


def main():
    test_the_unit_carries_every_wall()
    test_names_and_paths()
    test_the_door()
    test_jobs_run_once_and_on_time()
    test_the_guest_sdk()
    test_the_review_fixes()
    test_the_walls_suite_runs_in_ci()
    print("\nALL SANDBOX CHECKS PASS" if not FAILS else f"\n{len(FAILS)} SANDBOX CHECK(S) FAILED")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
