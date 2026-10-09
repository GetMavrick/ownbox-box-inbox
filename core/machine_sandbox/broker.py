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

THE CALLS. Step 1: hello, setting, save_setting, log, every, next and done, which need no permission. Step 2:
think (AI through the box's account, at most the granted number a month) and fetch (the box makes the request,
fetch.py), each refused with `not_permitted` unless the owner granted it (grants.py, read at every call, so a
revoke takes effect on the next one). Everything else (people, sending, screens) answers `unknown_call` until its
step lands.

EVERY CALL IS AUDITED (D18) in the box's database (audit.py), except the long-poll itself: what, the permission it
needed, whether it went through, and for a fetch the host and the bytes each way.
"""
from __future__ import annotations

import base64
import collections
import json
import os
import re
import socket
import struct
import threading
import time
import uuid

from core.logging import get_logger
from core.machine_sandbox import audit as _audit
from core.machine_sandbox import fetch as _fetch
from core.machine_sandbox import grants as _grants

log = get_logger(__name__)

SDK_VERSION = 1
MAX_LINE = 2 << 20                  # one request, in bytes, newline included (a 1 MB body, base64, fits)
MAX_REPLY = 8 << 20                 # one answer (a 4 MB fetched body, base64, fits)
MAX_VALUE = 64 * 1024               # one setting's value, as JSON
MAX_CONNECTIONS = 4                 # the guest uses one; a few more covers a reconnect racing an old one
MAX_JOBS = 20
MIN_EVERY = 15                      # seconds, as the in-process m.every
MAX_EVERY = 7 * 24 * 3600
MAX_WAIT = 30                       # the longest a `next` long-poll is held, in seconds
MAX_LOG = 4 * 1024
KEEP_CALLS = 500                    # recent calls kept in memory, beside the audit log
FETCHES_PER_MINUTE = 60
# A MACHINE'S SETTINGS LIVE IN THE BOX'S OWN DATABASE, so their total is capped too (OSDev4's review of #2065): one
# value was capped and the number of keys was not, so a machine could fill the box's disk and every backup. More
# belongs in its own data folder, which its unit caps.
MAX_SETTINGS = 200
MAX_SETTINGS_BYTES = 2 << 20
# ITS LOG LINES GO OUT UNDER THE BOX'S OWN SERVICE, so a flood would trip journald's rate limit on the box itself
# and hide the box's own lines (OSDev4's review of #2065). Past this many a minute they are dropped and counted.
LOGS_PER_MINUTE = 60
# AND EVERY CALL IS A ROW IN THE BOX'S AUDIT LOG, so the door itself has a rate: past this many calls a minute (the
# long-poll aside) a call is refused, counted, and written nowhere. Log lines are logs, not audit rows.
CALLS_PER_MINUTE = 120
MAX_PROMPT = 200_000                # characters
MAX_SYSTEM = 50_000
MAX_TOKENS = 4096
_TEXTY = ("text/", "application/json", "application/xml", "application/javascript", "+json", "+xml",
          "application/x-www-form-urlencoded")

_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")
_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_EVENT = re.compile(r"^[a-z][a-z0-9_.]{0,59}$")
_TASK = re.compile(r"^[a-z][a-z0-9_.-]{0,59}$")


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

    def usage(self, key) -> tuple[int, int, int | None]:
        """(how many settings it keeps, their total size, the size of `key`'s value or None when it has none)."""
        from core import state
        with state.connect() as c:
            rows = c.execute("SELECT key, LENGTH(value) AS n FROM box_settings WHERE machine = ?",
                             (self.key,)).fetchall()
        sizes = {r["key"]: int(r["n"] or 0) for r in rows}
        return len(sizes), sum(sizes.values()), sizes.get(key)


class Broker:
    """Serves one machine's socket. `start()` returns at once; the box's calls run on their own threads."""

    def __init__(self, slug: str, *, uid: int, socket_path: str, gid: int | None = None,
                 peer_uid=None, store=None, clock=time.time, version: str = "", grants_fn=None, audit_fn=None,
                 think_fn=None, fetch_fn=None, secret_fn=None, backend_fn=None):
        self.slug = slug
        self.version = str(version)
        self._grants_fn = grants_fn or (lambda: _grants.current(slug)[1])
        self._audit_fn = audit_fn or _audit.record
        self._think_fn = think_fn or _think
        self._fetch_fn = fetch_fn or _fetch.request
        self._secret_fn = secret_fn or (lambda name: _secret(slug, name))
        self._backend_fn = backend_fn or _backend
        self._thinking = threading.Lock()              # one AI call at a time per machine: the budget can't be raced
        self._fetched = collections.deque()             # times of this minute's fetches
        self._logged = collections.deque()              # times of this minute's log lines
        self.logs_dropped = 0
        self._called = collections.deque()              # times of this minute's calls
        self.calls_dropped = 0
        self._ctx = threading.local()                   # what the call in progress touched, for its audit row
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
                       "log": self._log, "every": self._every, "next": self._next, "done": self._done,
                       "think": self._think, "fetch": self._fetch}

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
        if call != "next" and not self._admit():
            self.calls_dropped += 1
            if self.calls_dropped % 1000 == 1:              # one line says so, never one per refused call
                log.warning("machine_sandbox.calls_dropped", machine=self.slug, dropped=self.calls_dropped)
            return {"id": rid, "ok": False, "code": "rate_limited",
                    "error": f"a machine may make {CALLS_PER_MINUTE} calls a minute"}
        self._ctx.info = {}
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

    def _admit(self) -> bool:
        now = time.monotonic()
        with self._lock:
            while self._called and now - self._called[0] > 60:
                self._called.popleft()
            if len(self._called) >= CALLS_PER_MINUTE:
                return False
            self._called.append(now)
            return True

    def _record(self, call: str, reply: dict) -> None:
        with self._lock:
            self.calls.append({"call": call, "ok": reply["ok"], "code": reply.get("code", ""), "at": self._clock()})
            del self.calls[:-KEEP_CALLS]
        if call in ("next", "log"):                        # a long-poll is not news, and a log line is a log
            return
        info = getattr(self._ctx, "info", {}) or {}
        log.info("machine_sandbox.call", machine=self.slug, call=call, ok=reply["ok"], code=reply.get("code", ""),
                 host=info.get("host", ""))
        try:
            self._audit_fn(self.slug, call, ok=reply["ok"], version=self.version, code=reply.get("code", ""),
                           permission=info.get("permission", ""), host=info.get("host", ""),
                           bytes_out=info.get("bytes_out", 0), bytes_in=info.get("bytes_in", 0))
        except Exception as e:                             # noqa: BLE001 — a full disk must not wedge the door
            log.error("machine_sandbox.audit_failed", machine=self.slug, error=type(e).__name__)

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
        size = len(json.dumps(args.get("value"), default=str))
        if size > MAX_VALUE:
            raise CallError("too_large", f"a setting is at most {MAX_VALUE // 1024} KB: keep more in your data "
                                         f"folder")
        usage = getattr(self._store, "usage", None)
        if usage is not None:
            with self._lock:
                keys, total, this = usage(key)
                if this is None and keys >= MAX_SETTINGS:
                    raise CallError("too_many_settings", f"a machine keeps at most {MAX_SETTINGS} settings: keep "
                                                         f"more in your data folder")
                if total - (this or 0) + size > MAX_SETTINGS_BYTES:
                    raise CallError("settings_full", f"a machine's settings hold at most {MAX_SETTINGS_BYTES >> 20} "
                                                     f"MB in all: keep more in your data folder")
                self._store.put(key, args.get("value"), self.slug)
            return True
        self._store.put(key, args.get("value"), self.slug)
        return True

    def _log(self, args: dict) -> bool:
        level = args.get("level") if args.get("level") in ("info", "warning", "error") else "info"
        event = args.get("event")
        if not isinstance(event, str) or not _EVENT.match(event):
            raise CallError("bad_event", "an event name is lowercase letters, digits, dots and _")
        detail = json.dumps(args.get("fields") or {}, default=str)[:MAX_LOG]
        now = time.monotonic()
        with self._lock:
            while self._logged and now - self._logged[0] > 60:
                self._logged.popleft()
            if len(self._logged) >= LOGS_PER_MINUTE:
                self.logs_dropped += 1
                if self.logs_dropped % 1000 == 1:          # one line says so, never one per dropped line
                    log.warning("machine_sandbox.logs_dropped", machine=self.slug, dropped=self.logs_dropped)
                raise CallError("rate_limited", f"a machine may log {LOGS_PER_MINUTE} lines a minute")
            self._logged.append(now)
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

    # ── step 2: calls that need a permission ────────────────────────────────────────────────────
    def _granted(self, permission: str) -> dict:
        self._ctx.info["permission"] = permission
        g = self._grants_fn() or {}
        if not g.get(permission):
            raise CallError("not_permitted", f"the owner has not allowed this machine to {_WORDS[permission]}")
        return g

    def _think(self, args: dict) -> str:
        g = self._granted("think")
        task, prompt, system = args.get("task"), args.get("prompt"), args.get("system")
        if not isinstance(task, str) or not _TASK.match(task):
            raise CallError("bad_task", "a task name is lowercase letters, digits, dots, _ and -")
        if not isinstance(prompt, str) or not prompt or len(prompt) > MAX_PROMPT:
            raise CallError("bad_prompt", f"a prompt is text of at most {MAX_PROMPT:,} characters")
        if system is not None and (not isinstance(system, str) or len(system) > MAX_SYSTEM):
            raise CallError("bad_prompt", f"a system prompt is text of at most {MAX_SYSTEM:,} characters")
        mt = args.get("max_tokens", 1024)
        if isinstance(mt, bool) or not isinstance(mt, int) or not 1 <= mt <= MAX_TOKENS:
            raise CallError("bad_prompt", f"max_tokens is 1 to {MAX_TOKENS}")
        # NOT ON CHATGPT YET (OSDev4's review of #2066). A machine's prompt is entirely its own, and the AI runs outside
        # the sandbox. On the Claude path the CLI gets no tools at all (`--tools ""`); the Codex path's read-only
        # sandbox limits writing, not reading, and nothing yet proves its built-in shell is off. Until a real-CLI
        # measurement shows a prompt cannot read a file through it, a sandboxed machine does not think on it.
        if self._backend_fn() == "codex":
            raise CallError("not_on_this_ai", "a machine of yours cannot use AI on a box signed in with ChatGPT yet; "
                                              "it can on Claude")
        out_chars = len(prompt) + len(system or "")
        since = _audit.month_start()
        with self._thinking:
            cap = int(g["think"].get("per_month") or 0)
            used = _audit.count(self.slug, "think", since=since)
            if used >= cap:
                raise CallError("over_budget", f"this machine has used its {cap} AI calls for the month; the owner "
                                               f"can allow more")
            # SIZE, NOT ONLY CALLS (OSDev4's review of #2066): 300 calls of 200,000 characters each could spend the
            # box's whole AI ceiling and stop the inbox's drafts. The answer's room is counted before the call.
            room = int(g["think"].get("chars_per_month") or _grants.THINK_CHARS_PER_MONTH)
            spent = _audit.size(self.slug, "think", since=since)
            if spent + out_chars + mt * _grants.CHARS_PER_TOKEN > room:
                raise CallError("over_budget", f"this machine has used its {room:,} characters of AI for the month; "
                                               f"the owner can allow more")
            self._ctx.info["bytes_out"] = out_chars
            from core.sdk import _key
            try:
                text = self._think_fn(task, prompt, system=system, max_tokens=mt, machine=_key(self.slug))
            except Exception as e:                     # noqa: BLE001 — the box's AI said no: a reason, never its detail
                raise CallError("ai_unavailable", f"the box's AI could not answer ({type(e).__name__})") from None
            self._ctx.info["bytes_in"] = len(text or "")
            return text

    def _fetch(self, args: dict) -> dict:
        g = self._granted("fetch")
        url = args.get("url")
        try:
            host = _fetch.host_of(url)
        except _fetch.FetchRefused as e:
            raise CallError(e.code, e.error) from None
        self._ctx.info["host"] = host
        if host not in (g.get("fetch") or []):
            raise CallError("host_not_allowed", f"the owner has not allowed this machine to reach {host}")
        try:
            clean = _fetch.machine_headers(args.get("headers") or {})
        except _fetch.FetchRefused as e:
            raise CallError(e.code, e.error) from None
        secret = ""
        name = args.get("key")
        if name is not None:
            spec = (g.get("keys") or {}).get(name) if isinstance(name, str) else None
            if spec is None:
                raise CallError("key_not_allowed", f"the owner has not given this machine a key called {name!r}")
            if spec["host"] != host:
                raise CallError("key_wrong_host", f"the {spec['label']} goes only to {spec['host']}")
            if any(k.lower() == spec["header"].lower() for k in clean):
                raise CallError("bad_headers", f"the box sets the {spec['header']} header from the key")
            secret = self._secret_fn(name) or ""
            if not secret:
                raise CallError("key_missing", f"the owner has not saved the {spec['label']} yet")
            clean[spec["header"]] = spec["prefix"] + secret
        if args.get("body_b64") is not None:
            try:
                body = base64.b64decode(str(args["body_b64"]), validate=True)
            except ValueError:
                raise CallError("bad_body", "body_b64 is not base64") from None
        else:
            body = str(args.get("body") or "").encode()
        now = time.monotonic()
        with self._lock:
            while self._fetched and now - self._fetched[0] > 60:
                self._fetched.popleft()
            if len(self._fetched) >= FETCHES_PER_MINUTE:
                raise CallError("rate_limited", f"a machine may fetch {FETCHES_PER_MINUTE} times a minute")
            self._fetched.append(now)
        self._ctx.info["bytes_out"] = len(body)
        try:
            r = self._fetch_fn(url, method=args.get("method") or "GET", headers=clean, body=body,
                               timeout=args.get("timeout") or 20, secret=secret)
        except _fetch.FetchRefused as e:
            raise CallError(e.code, e.error) from None
        data = r["body"]
        self._ctx.info["bytes_in"] = len(data)
        ctype = str(r["headers"].get("content-type") or "").lower()
        out = {"status": r["status"], "headers": r["headers"]}
        if any(t in ctype for t in _TEXTY):
            try:
                out["body"] = data.decode("utf-8")
                return out
            except UnicodeDecodeError:
                pass
        out["body_b64"] = base64.b64encode(data).decode()
        return out


_WORDS = {"think": "use AI", "fetch": "reach the internet"}


def _think(task: str, prompt: str, *, system, max_tokens: int, machine: str) -> str:
    """ISOLATED, WITH NOTHING BUT THE MACHINE'S OWN WORDS (OSDev4's review of #2066). A call that is not isolated
    carries the box's knowledge (the owner's own words, the website read, what was learned from sent mail) and, on the
    Claude path, the box's own setting sources. A machine granted only `think` could ask the model to repeat them.
    Reading the business's knowledge would be a permission of its own."""
    from core import brain
    return brain.think(task, prompt, system=system, max_tokens=max_tokens, machine=machine, isolated=True)


def _backend() -> str:
    from core import brain
    return brain._backend()


def _secret(slug: str, name: str) -> str:
    """A key the owner saved for this machine, read by the box only to attach it to a request."""
    from core import machine_secrets
    from core.sdk import _key
    return machine_secrets.get(_key(slug), name)
