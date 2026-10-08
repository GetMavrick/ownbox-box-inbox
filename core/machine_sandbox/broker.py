"""The box's end of a sandboxed machine's one door: a Unix socket, JSON lines, a short list of calls.

Decision D3 of docs/REVIEW_CUSTOM_MACHINES_DECISION_LOG.md. The machine never listens on anything; the box is
always the server, and work reaches the machine as the answer to its own long-poll (`next`).

WHO IS CALLING IS DECIDED BY THE KERNEL. Each machine has its own socket, in a folder only its user can enter, and
on every connection the box asks the kernel for the peer's user id (SO_PEERCRED). A connection from any other user
(another machine, a box process, root) is closed before a byte is read. Nothing the caller sends can change which
machine it is: there is no token to steal and no `machine` argument to forge. Every call acts as the socket's own
machine.

ONE LINE, ONE CALL. A request is one JSON object on one line, at most 1 MB:

    {"id": 7, "call": "setting", "args": {"key": "last_run"}}

and the answer is one line with the same id:

    {"id": 7, "ok": true, "result": ...}    or    {"id": 7, "ok": false, "code": "...", "error": "..."}

`code` never changes with a rewording; `error` is a sentence for a person. A line too long, or not JSON, ends the
connection: a peer that sends one is broken or hostile, and the guest SDK reconnects.

STEP 1's CALLS. hello, setting, save_setting, log, every, next and done. Everything else (think, fetch, people,
sending, screens) arrives in later steps, each behind a permission the owner approved, and answers `unknown_call`
until then.
"""
from __future__ import annotations

import json
import os
import re
import socket
import struct
import threading
import time
import uuid

from core.logging import get_logger

log = get_logger(__name__)

SDK_VERSION = 1
MAX_LINE = 1 << 20                  # one request, in bytes, newline included
MAX_VALUE = 64 * 1024               # one setting's value, as JSON
MAX_CONNECTIONS = 4                 # the guest uses one; a few more covers a reconnect racing an old one
MAX_JOBS = 20
MIN_EVERY = 15                      # seconds, as the in-process m.every
MAX_EVERY = 7 * 24 * 3600
MAX_WAIT = 30                       # the longest a `next` long-poll is held, in seconds
MAX_LOG = 4 * 1024
KEEP_CALLS = 500                    # recent calls kept in memory for the machine's page (the audit log is step 2)

_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")
_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_EVENT = re.compile(r"^[a-z][a-z0-9_.]{0,59}$")


class CallError(Exception):
    def __init__(self, code: str, error: str):
        super().__init__(error)
        self.code, self.error = code, error


def _linux_peer_uid(conn: socket.socket) -> int:
    """The connecting process's user id, from the kernel. Linux only: a box is always Linux."""
    pid, uid, gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                                         struct.calcsize("3i")))
    return uid


class BoxSettings:
    """A machine's small state, in the box's own settings under the same key the in-process SDK uses."""

    def __init__(self, slug: str):
        from core.sdk import _key
        self.key = _key(slug)

    def get(self, key, default):
        from core import box_settings
        return box_settings.get(self.key, key, default=default)

    def put(self, key, value, by):
        from core import box_settings
        box_settings.put(self.key, key, value, set_by=by)


class Broker:
    """Serves one machine's socket. `start()` returns at once; the box's calls run on their own threads."""

    def __init__(self, slug: str, *, uid: int, socket_path: str, gid: int | None = None,
                 peer_uid=None, store=None, clock=time.time):
        self.slug = slug
        self.uid = int(uid)
        self.gid = gid
        self.socket_path = socket_path
        self._peer_uid = peer_uid or _linux_peer_uid
        self._store = store or BoxSettings(slug)
        self._clock = clock
        self._lock = threading.Condition()
        self._jobs: dict[str, dict] = {}         # name -> {"seconds", "due", "running": run id or None}
        self._runs: dict[str, dict] = {}         # run id -> {"name", "started"}
        self.calls: list[dict] = []              # recent calls: {"call", "ok", "code", "at"}
        self.results: list[dict] = []            # finished job runs: {"name", "ok", "error", "seconds"}
        self.refused_peers: list[int] = []
        self._open = 0
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self._calls = {"hello": self._hello, "setting": self._setting, "save_setting": self._save_setting,
                       "log": self._log, "every": self._every, "next": self._next, "done": self._done}

    # ── the socket ──────────────────────────────────────────────────────────────────────────────
    def start(self) -> None:
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)                    # a socket left by a box that stopped
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(self.socket_path)
        # Root and the machine's group only. The folder around it is the first lock and SO_PEERCRED the second;
        # this mode is the third.
        os.chmod(self.socket_path, 0o660)
        if self.gid is not None and os.geteuid() == 0:
            os.chown(self.socket_path, 0, self.gid)
        s.listen(8)
        self._sock = s
        threading.Thread(target=self._accept, name=f"machine-broker-{self.slug}", daemon=True).start()
        log.info("machine_sandbox.broker_started", machine=self.slug)

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            self._lock.notify_all()
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass

    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return                                     # closed by stop()
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        counted = False
        try:
            try:
                uid = self._peer_uid(conn)
            except OSError:
                uid = -1
            if uid != self.uid:
                self.refused_peers.append(uid)
                log.warning("machine_sandbox.wrong_peer", machine=self.slug, uid=uid)
                return
            with self._lock:
                if self._open >= MAX_CONNECTIONS:
                    self._send(conn, {"id": None, "ok": False, "code": "too_many_connections",
                                      "error": f"a machine may hold {MAX_CONNECTIONS} connections to the box"})
                    return
                self._open += 1
                counted = True
            f = conn.makefile("rb")
            while not self._stop.is_set():
                line = f.readline(MAX_LINE + 1)
                if not line:
                    return
                if len(line) > MAX_LINE or not line.endswith(b"\n"):
                    self._send(conn, {"id": None, "ok": False, "code": "too_large",
                                      "error": f"one request is at most {MAX_LINE} bytes, on one line"})
                    return
                try:
                    msg = json.loads(line)
                except ValueError:
                    msg = None
                if not isinstance(msg, dict):
                    self._send(conn, {"id": None, "ok": False, "code": "bad_request",
                                      "error": "a request is one JSON object on one line"})
                    return
                self._send(conn, self.handle(msg))
        except OSError:
            return
        finally:
            if counted:
                with self._lock:
                    self._open -= 1
            try:
                conn.close()
            except OSError:
                pass

    @staticmethod
    def _send(conn: socket.socket, reply: dict) -> None:
        conn.sendall(json.dumps(reply, separators=(",", ":"), default=str).encode() + b"\n")

    # ── one call ────────────────────────────────────────────────────────────────────────────────
    def handle(self, msg: dict) -> dict:
        """Answer one request. Never raises: a bad call is an answer with a code."""
        rid = msg.get("id")
        call = msg.get("call")
        args = msg.get("args") if isinstance(msg.get("args"), dict) else {}
        fn = self._calls.get(call) if isinstance(call, str) else None
        try:
            if fn is None:
                raise CallError("unknown_call", f"{str(call)[:40]!r} is not a call this box offers on sdk "
                                                f"{SDK_VERSION} yet")
            result = fn(args)
            reply = {"id": rid, "ok": True, "result": result}
        except CallError as e:
            reply = {"id": rid, "ok": False, "code": e.code, "error": e.error}
        except Exception as e:                             # noqa: BLE001 — the box's bug is never the machine's crash
            log.error("machine_sandbox.call_failed", machine=self.slug, call=str(call)[:40],
                      error=type(e).__name__)
            reply = {"id": rid, "ok": False, "code": "box_error", "error": "the box could not answer that call"}
        self._record(str(call)[:40], reply)
        return reply

    def _record(self, call: str, reply: dict) -> None:
        with self._lock:
            self.calls.append({"call": call, "ok": reply["ok"], "code": reply.get("code", ""), "at": self._clock()})
            del self.calls[:-KEEP_CALLS]
        if call not in ("next",):                          # a long-poll every 30 seconds is not news
            log.info("machine_sandbox.call", machine=self.slug, call=call, ok=reply["ok"],
                     code=reply.get("code", ""))

    # ── the calls ───────────────────────────────────────────────────────────────────────────────
    def _hello(self, args: dict) -> dict:
        wanted = args.get("sdk")
        if not isinstance(wanted, int) or isinstance(wanted, bool) or wanted > SDK_VERSION:
            raise CallError("sdk_too_new", f"built for sdk {wanted}; this box has sdk {SDK_VERSION}")
        if args.get("slug") != self.slug:
            raise CallError("wrong_machine", f"this is {self.slug}'s door")
        from core.machine_sandbox import unit
        return {"sdk": SDK_VERSION, "slug": self.slug, "data": unit.DATA}

    def _setting(self, args: dict):
        key = args.get("key")
        if not isinstance(key, str) or not _KEY.match(key):
            raise CallError("bad_key", "a setting's key is 1 to 80 letters, digits or _ . : -")
        return self._store.get(key, args.get("default"))

    def _save_setting(self, args: dict) -> bool:
        key = args.get("key")
        if not isinstance(key, str) or not _KEY.match(key):
            raise CallError("bad_key", "a setting's key is 1 to 80 letters, digits or _ . : -")
        if len(json.dumps(args.get("value"), default=str)) > MAX_VALUE:
            raise CallError("too_large", f"a setting is at most {MAX_VALUE // 1024} KB: keep more in your data "
                                         f"folder")
        self._store.put(key, args.get("value"), self.slug)
        return True

    def _log(self, args: dict) -> bool:
        level = args.get("level") if args.get("level") in ("info", "warning", "error") else "info"
        event = args.get("event")
        if not isinstance(event, str) or not _EVENT.match(event):
            raise CallError("bad_event", "an event name is lowercase letters, digits, dots and _")
        detail = json.dumps(args.get("fields") or {}, default=str)[:MAX_LOG]
        getattr(log, level)("machine." + event, machine=self.slug, detail=detail)
        return True

    def _every(self, args: dict) -> bool:
        name, seconds = args.get("name"), args.get("seconds")
        if not isinstance(name, str) or not _NAME.match(name):
            raise CallError("bad_name", "a job's name is lowercase letters, digits and _")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or \
                not MIN_EVERY <= seconds <= MAX_EVERY:
            raise CallError("bad_every", f"a job runs at most every {MIN_EVERY} seconds and at least weekly")
        with self._lock:
            if name not in self._jobs and len(self._jobs) >= MAX_JOBS:
                raise CallError("too_many_jobs", f"a machine has at most {MAX_JOBS} jobs")
            old = self._jobs.get(name)
            # The first run is due at once, so a machine that just started catches up rather than waiting a period.
            self._jobs[name] = {"seconds": float(seconds), "due": old["due"] if old else self._clock(),
                                "running": old["running"] if old else None}
            self._lock.notify_all()
        return True

    def _due(self):
        now = self._clock()
        ready = [(j["due"], n) for n, j in self._jobs.items() if j["running"] is None and j["due"] <= now]
        return min(ready)[1] if ready else None

    def _next(self, args: dict) -> dict:
        wait = args.get("wait", MAX_WAIT)
        wait = MAX_WAIT if isinstance(wait, bool) or not isinstance(wait, (int, float)) else \
            max(0.0, min(float(wait), MAX_WAIT))
        deadline = time.monotonic() + wait
        with self._lock:
            while True:
                name = self._due()
                if name is not None:
                    run = uuid.uuid4().hex[:12]
                    self._jobs[name]["running"] = run
                    self._runs[run] = {"name": name, "started": self._clock()}
                    return {"job": {"run": run, "kind": "every", "name": name}}
                left = deadline - time.monotonic()
                if left <= 0 or self._stop.is_set():
                    return {"job": None}
                soonest = min((j["due"] for j in self._jobs.values() if j["running"] is None), default=None)
                self._lock.wait(timeout=min(left, max(0.05, soonest - self._clock())) if soonest else left)

    def _done(self, args: dict) -> bool:
        run = args.get("run")
        with self._lock:
            r = self._runs.pop(run, None) if isinstance(run, str) else None
            if r is None:
                raise CallError("unknown_run", "no job run by that id is waiting for an answer")
            job = self._jobs.get(r["name"])
            if job is not None:
                job["running"] = None
                job["due"] = r["started"] + job["seconds"]
            ok = bool(args.get("ok"))
            self.results.append({"name": r["name"], "ok": ok, "error": str(args.get("error") or "")[:300],
                                 "seconds": round(self._clock() - r["started"], 3)})
            del self.results[:-KEEP_CALLS]
            self._lock.notify_all()
        if not ok:
            log.warning("machine_sandbox.job_failed", machine=self.slug, job=r["name"],
                        error=str(args.get("error") or "")[:200])
        return True
