"""`from core import sdk` inside a machine's sandbox: the same names as the box's own core/sdk.py, over a socket.

THIS FILE RUNS INSIDE THE MACHINE'S UNIT, so it uses the standard library only and imports nothing of the box's.
It is bound read-only into the unit at /opt/machine/sdk (core/machine_sandbox/unit.py), so a machine can neither
change it nor reach past it: every call becomes one JSON line on the machine's own socket, and the box decides
(core/machine_sandbox/broker.py).

THE SAME API AS IN-PROCESS (decision D3). A machine written against core/sdk.py runs unchanged here, as far as
the calls this step of the build carries: m.setting, m.save_setting, m.data_dir and m.every. Every other promised
call names itself and the build step that brings it (step 2: think, fetch, keys, people, sending; step 4: screens),
rather than half-working. tests/test_machine_sandbox.py keeps this file's names in step with core/sdk.py's SEAMS.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import socket
import threading

VERSION = 1
SOCKET = "/opt/machine/run/sdk.sock"
DATA = "/opt/machine/data"
STYLE_CLASSES = ("card", "quiet", "addr", "consent")

_SLUG = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
_JOBS: dict = {}                    # name -> fn, run by boot.py when the box says a job is due
_MAX_LINE = 1 << 20

# What a sandboxed machine cannot call yet, and the build step that brings it.
_LATER = {
    "think": 2, "tool": 2, "link": 2, "secret": 2, "person": 2, "touch": 2, "claim": 2, "release": 2,
    "messages": 2, "send_dm": 2, "comments": 2, "conversation_for": 2, "follows_you": 2, "reply_to_comment": 2,
    "reporter": 4, "day_window": 4, "menu": 4, "screen": 4, "panel": 4,
}


class BoxRefused(Exception):
    """The box answered a call with a refusal. `code` never changes with a rewording; the message is a sentence."""

    def __init__(self, code: str, error: str):
        super().__init__(error)
        self.code = code


class NotYet(RuntimeError):
    """A promised call a sandboxed machine cannot make yet."""


class _Door:
    """The machine's one connection to the box. One call at a time; reconnects once if the box restarted."""

    def __init__(self, path: str = SOCKET):
        self._path = path
        self._sock = None
        self._file = None
        self._id = 0
        self._lock = threading.Lock()

    def _connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self._path)
        self._sock, self._file = s, s.makefile("rb")

    def _close(self):
        for x in (self._file, self._sock):
            try:
                if x is not None:
                    x.close()
            except OSError:
                pass
        self._sock = self._file = None

    def call(self, call: str, /, **args):
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._sock is None:
                        self._connect()
                    self._id += 1
                    line = json.dumps({"id": self._id, "call": call, "args": args}, default=str).encode() + b"\n"
                    if len(line) > _MAX_LINE:
                        raise BoxRefused("too_large", "one call is at most 1 MB")
                    self._sock.sendall(line)
                    raw = self._file.readline(_MAX_LINE + 1)
                    if not raw:
                        raise ConnectionError("the box closed the connection")
                    reply = json.loads(raw)
                    break
                except (OSError, ValueError):
                    self._close()
                    if attempt == 2:
                        raise
        if not reply.get("ok"):
            raise BoxRefused(str(reply.get("code") or "refused"), str(reply.get("error") or "the box refused"))
        return reply.get("result")


_DOOR = None


def _door() -> _Door:
    global _DOOR
    if _DOOR is None:
        _DOOR = _Door()
    return _DOOR


def _later(name: str, step: int):
    def refuse(self, *a, **k):
        raise NotYet(f"m.{name} is not available to a sandboxed machine yet: it arrives in step {step} of the "
                     f"sandbox build (docs/SCOPE_CUSTOM_MACHINES_FROM_A_REPO.md)")
    refuse.__name__ = name
    return refuse


def page(path: str, *, title: str, lede: str, body: str) -> str:
    raise NotYet("sdk.page is not available to a sandboxed machine: its screens are declarative (step 4)")


class Machine:
    def __init__(self, slug: str):
        if not isinstance(slug, str) or not _SLUG.match(slug):
            raise ValueError(f"machine name {slug!r} must be lowercase letters, digits and hyphens, "
                             f"and the same as its folder's name")
        mine = os.environ.get("OWNBOX_MACHINE")
        if mine and slug != mine:
            raise ValueError(f"this sandbox runs {mine}; sdk.machine({slug!r}) must name it")
        self.slug = slug
        self.key = "my_" + slug.replace("-", "_")
        self.home = f"/my/{slug}"

    # ── small state ───────────────────────────────────────────────────────────────────────────
    def setting(self, key: str, default=None):
        return _door().call("setting", key=key, default=default)

    def save_setting(self, key: str, value, *, by: str = "") -> None:
        _door().call("save_setting", key=key, value=value)

    def data_dir(self) -> pathlib.Path:
        """This machine's own folder: its SQLite file and anything larger than a setting. The only place it can
        write that outlives a restart."""
        return pathlib.Path(DATA)

    # ── scheduled jobs ────────────────────────────────────────────────────────────────────────
    def every(self, seconds, fn=None, *, name: str | None = None):
        """Run `fn()` every `seconds` (15 or more). The box keeps the clock and says when a run is due; the first is
        due as soon as the machine starts. A run that raises is reported and the next one goes ahead."""
        def register(f):
            job = name or f.__name__
            _door().call("every", name=job, seconds=seconds)
            _JOBS[job] = f
            return f
        return register if fn is None else register(fn)

    @property
    def keys_page(self) -> str:
        return f"/settings/machines/{self.slug}/keys"


for _name, _step in _LATER.items():
    setattr(Machine, _name, _later(_name, _step))


def machine(slug: str) -> Machine:
    return Machine(slug)
