"""Sign in to ChatGPT from the box — a one-time code, no key, no browser on the box.

The second of the four logins the product sells. Owner, 2026-09-21: "We need the Claude login.
And then we're gonna need the ChatGPT login right behind it."

HOW IT WORKS, MEASURED ON codex-cli 0.155.1 (2026-09-22, test box):

    $ CODEX_HOME=/var/lib/aios/codex codex login --device-auth
    Follow these steps to sign in with ChatGPT using device code authorization:
    1. Open this link in your browser and sign in to your account
       https://auth.openai.com/codex/device
    2. Enter this one-time code (expires in 15 minutes)
       XXXX-XXXX
    Continue only if you started this login in Codex. ...

The CLI then POLLS OpenAI until the person has entered the code on any device, and on success
writes `$CODEX_HOME/auth.json` itself. Nothing is pasted back into the box — the flow is the
mirror image of Claude's `setup-token`, where the person brings a code TO the box. So there is
no FIFO, no pty and no token to capture here: the box shows the link and the code, waits for
the CLI to exit, and asks `codex login status` whether it worked.

THE HELPER IS STILL DETACHED, for the reason core/claude_login.py documents at length: the app
serves from two gunicorn workers, and `start()` and the page that polls for "done" are separate
requests with no guarantee of landing on the same one. The CLI must therefore be owned by a
process that outlives any request — this module run as `-m core.codex_login --serve <dir>` — and
both workers read its state from a session directory beside the box's database:

    <dir>/pid      the helper, so any worker can reap it
    <dir>/url      the verification link, written once seen
    <dir>/code     the one-time code, written once seen (the person types it on THEIR device)
    <dir>/status   starting | awaiting_approval | done | error
    <dir>/error    a sentence for the buyer
    <dir>/user     who is signing in

Every file is written to a temporary name and renamed, so a reader sees the old value or the
new one, never half of either.

THE CREDENTIAL IS THE CLI'S FILE, NOT A ROW. auth.json under CODEX_HOME is what `codex exec`
reads; this box records only a status beside it (`box_secrets.note_codex_status`). Disconnect is
`codex logout` plus clearing that status. The file is 0600 under a 0700 directory, root only.

THE REFUSAL THAT IS NOT A BUG. Device-code sign-in is off by default on some ChatGPT accounts
and needs a switch — personal accounts under Settings → Security, workspaces via an admin. The
CLI says "contact your workspace admin to enable device code authentication" and exits; that
sentence is mapped to one that tells the buyer where the switch is, because a box that just says
"failed" here sends them to support for something they can fix in a minute.
"""
from __future__ import annotations

import os
import pathlib
import re
import select
import shutil
import signal
import subprocess
import sys
import time

from core import box_secrets
from core.logging import get_logger

log = get_logger(__name__)

START_TIMEOUT_S = 40.0          # the CLI prints the link within a second or two; forty is generous
# The code itself expires in fifteen minutes (measured text). Read from the environment because the
# helper is its OWN process: a test that shortens it must reach the helper, not this module's copy.
APPROVE_TIMEOUT_S = float(os.environ.get("AIOS_CODEX_APPROVE_TIMEOUT_S") or 15 * 60 + 30)
_DIRNAME = ".codex-login"

_URL = re.compile(r"https://auth\.openai\.com/[^\s\"'\x1b]+")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07")
# The code is on the line after "Enter this one-time code". Its alphabet is not documented, so
# this takes the first token on that line rather than pinning a shape the vendor may change.
_CODE_LINE = re.compile(r"one-time code[^\n]*\n\s*([A-Za-z0-9][A-Za-z0-9\-]{3,})", re.I)
_ADMIN = re.compile(r"device code auth|workspace admin", re.I)


class LoginError(RuntimeError):
    """A sentence for the buyer. Never a traceback, never the CLI's raw output."""


# ── session directory ───────────────────────────────────────────────────────────────────────
def _dir() -> pathlib.Path:
    """Beside the box's database — the one writable place every worker agrees on. Config is read
    INSIDE the function, as claude_login does, so a test that points the box elsewhere is obeyed."""
    from core.config import settings
    return pathlib.Path(settings.db_path).resolve().parent / _DIRNAME


def _read(d: pathlib.Path, name: str) -> str:
    try:
        return (d / name).read_text().strip()
    except OSError:
        return ""


def _write(d: pathlib.Path, name: str, value: str) -> None:
    tmp = d / f".{name}.{os.getpid()}.tmp"
    tmp.write_text(value)
    os.chmod(tmp, 0o600)
    os.replace(tmp, d / name)


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _live_dir() -> pathlib.Path | None:
    d = _dir()
    if not d.is_dir():
        return None
    try:
        pid = int(_read(d, "pid") or 0)
    except ValueError:
        pid = 0
    st = _read(d, "status")
    if st in ("done", "error") or _alive(pid):
        return d
    return None


def _reap(why: str) -> None:
    d = _dir()
    if not d.is_dir():
        return
    try:
        pid = int(_read(d, "pid") or 0)
    except ValueError:
        pid = 0
    if _alive(pid):
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
    shutil.rmtree(d, ignore_errors=True)
    log.info("codex_login.reaped", why=why)


def _clean(raw: bytes) -> str:
    return _ANSI.sub("", raw.decode("utf-8", "replace")).replace("\r", "")


def _sayable(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and "PATH aliases" not in ln]
    return " ".join(lines)[-200:]


def _env() -> dict:
    env = {**os.environ, "TERM": "dumb", "NO_COLOR": "1", "CODEX_HOME": box_secrets.codex_home()}
    # A key in the environment would make the CLI use it and never ask anyone to sign in — the
    # sign-in "succeeds" for a subscription nobody connected. The box drafts on the account the
    # person signed in with, and only that.
    env.pop("OPENAI_API_KEY", None)
    env.pop("CODEX_API_KEY", None)
    return env


# ── public API ──────────────────────────────────────────────────────────────────────────────
def cli_present() -> bool:
    return bool(shutil.which("codex"))


def pending() -> dict:
    """{url, code, status} for a login already in flight, so a reloaded page is not a dead end."""
    d = _live_dir()
    if d is None:
        return {}
    return {"url": _read(d, "url"), "code": _read(d, "code"), "status": _read(d, "status"),
            "error": _read(d, "error")}


def start(*, user_id: str | None = None) -> dict:
    """Begin a sign-in. Returns {url, code} the buyer uses on their own device. Raises LoginError."""
    if not cli_present():
        raise LoginError("This box cannot sign in to ChatGPT yet — the Codex CLI is not installed "
                         "on it. Ask support@ownbox.io and we will put it on.")
    _reap("a new login replaces the old one")
    d = _dir()
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    _write(d, "status", "starting")
    if user_id:
        _write(d, "user", str(user_id))
    root = pathlib.Path(__file__).resolve().parents[1]
    try:
        logf = open(d / "log", "wb")                     # noqa: SIM115 — handed to the child
        proc = subprocess.Popen(
            [sys.executable, "-m", "core.codex_login", "--serve", str(d)],
            cwd=str(root), stdin=subprocess.DEVNULL, stdout=logf, stderr=logf,
            close_fds=True, start_new_session=True,
            env={**os.environ, "PYTHONPATH": str(root)})
    except Exception as e:                               # noqa: BLE001
        _reap("helper would not start")
        raise LoginError(f"This box could not start the ChatGPT sign-in ({type(e).__name__}).") from e
    finally:
        try:
            logf.close()                                 # noqa: F821 — set above or the except ran
        except Exception:                                # noqa: BLE001
            pass
    _write(d, "pid", str(proc.pid))
    deadline = time.time() + START_TIMEOUT_S
    while time.time() < deadline:
        url, code = _read(d, "url"), _read(d, "code")
        if url and code:
            log.info("codex_login.started")
            return {"url": url, "code": code}
        if _read(d, "status") == "error":
            why = _read(d, "error")
            _reap("the login could not start")
            raise LoginError(why or "ChatGPT did not return a sign-in link. Try again in a minute.")
        if proc.poll() is not None:
            break
        time.sleep(0.2)
    _reap("no link")
    raise LoginError("ChatGPT did not return a sign-in link. Try again in a minute.")


def status() -> str:
    d = _live_dir()
    return _read(d, "status") if d is not None else ""


def cancel() -> None:
    _reap("cancelled")


def disconnect(*, user_id: str | None = None) -> None:
    """Sign the box out of ChatGPT: the CLI forgets its file, and the status row goes with it."""
    _reap("disconnecting")
    if cli_present():
        try:
            subprocess.run(["codex", "logout"], env=_env(), capture_output=True, text=True,
                           timeout=20, stdin=subprocess.DEVNULL)
        except Exception as e:                           # noqa: BLE001
            log.warning("codex_login.logout_failed", error=type(e).__name__)
    try:
        (pathlib.Path(box_secrets.codex_home()) / "auth.json").unlink()
    except OSError:
        pass
    box_secrets.clear_codex(user_id=user_id)
    log.info("codex_login.disconnected", user=user_id)


def logged_in() -> bool:
    """Ask the CLI, which is the only party that can say. No spend."""
    if not cli_present():
        return False
    try:
        r = subprocess.run(["codex", "login", "status"], env=_env(), capture_output=True,
                           text=True, timeout=20, stdin=subprocess.DEVNULL)
    except Exception:                                    # noqa: BLE001
        return False
    return r.returncode == 0 and "not logged in" not in (r.stdout + r.stderr).lower() \
        and "logged in" in (r.stdout + r.stderr).lower()


# ── the detached helper ─────────────────────────────────────────────────────────────────────
def _serve(d: pathlib.Path) -> int:
    def fail(sentence: str) -> int:
        _write(d, "error", sentence)
        _write(d, "status", "error")
        return 1

    pathlib.Path(box_secrets.codex_home()).mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        proc = subprocess.Popen(["codex", "login", "--device-auth"], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=_env(),
                                close_fds=True)
    except Exception as e:                               # noqa: BLE001
        return fail(f"This box could not start the ChatGPT sign-in ({type(e).__name__}).")
    assert proc.stdout is not None
    fd = proc.stdout.fileno()
    os.set_blocking(fd, False)
    buf = b""

    def pump() -> bool:
        nonlocal buf
        r, _, _ = select.select([fd], [], [], 1.0)
        if r:
            try:
                chunk = os.read(fd, 65536)
            except BlockingIOError:
                return True
            except OSError:
                return False
            if not chunk:
                return False
            buf += chunk
        return True

    # ── 1. the link and the code ────────────────────────────────────────────────────────────
    deadline = time.time() + START_TIMEOUT_S
    url = code = ""
    while time.time() < deadline and not (url and code):
        open_ = pump()
        text = _clean(buf)
        m = _URL.search(text)
        c = _CODE_LINE.search(text)
        url = m.group(0) if m else url
        code = c.group(1) if c else code
        if not open_ or (proc.poll() is not None and not (url and code)):
            break
    if not (url and code):
        tail = _sayable(_clean(buf))
        if proc.poll() is None:
            proc.kill()
        if _ADMIN.search(tail):
            return fail("ChatGPT refused device-code sign-in for this account. Turn it on first: "
                        "ChatGPT → Settings → Security → device code authorization (on a work "
                        "account, an admin has to allow it). Then press Connect again.")
        return fail("ChatGPT did not return a sign-in link"
                    + (f" — it said: {tail}" if tail else " and said nothing.")
                    + " Try again in a minute.")
    _write(d, "url", url)
    _write(d, "code", code)
    _write(d, "status", "awaiting_approval")

    # ── 2. wait for the person to enter the code on their device ───────────────────────────
    deadline = time.time() + APPROVE_TIMEOUT_S
    closed = False
    while time.time() < deadline:
        if not pump():
            closed = True                            # the pipe closed: the CLI is on its way out
            break
        if proc.poll() is not None:
            break
    # EOF ARRIVES BEFORE THE EXIT STATUS DOES, and reading `poll()` in that instant returns None
    # for a process that has finished perfectly well — so a SUCCESSFUL sign-in was killed here and
    # reported to the buyer as expired. Caught as a flaky suite (2026-09-22) rather than by a
    # buyer, which is the only reason it never reached one.
    #
    # ONLY WHEN THE PIPE CLOSED. Waiting unconditionally makes the genuinely-expired path sit here
    # for the whole grace period with the buyer watching a page that has already given up — which
    # is what the first version of this fix did, and the suite caught that too.
    if closed:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    if proc.poll() is None:
        proc.kill()
        return fail("That code expired before it was entered. Press Connect to get a new one.")
    tail = _sayable(_clean(buf))
    if proc.returncode != 0:
        if _ADMIN.search(tail):
            return fail("ChatGPT refused device-code sign-in for this account. Turn it on first: "
                        "ChatGPT → Settings → Security → device code authorization (on a work "
                        "account, an admin has to allow it). Then press Connect again.")
        return fail("The sign-in did not complete" + (f" — ChatGPT said: {tail}" if tail else "")
                    + ". Press Connect to try again.")

    # ── 3. the CLI says it worked; ask it, do not assume ───────────────────────────────────
    if not logged_in():
        return fail("ChatGPT finished, but this box is still not signed in. Press Connect to try "
                    "again — and if it keeps happening, tell support@ownbox.io.")
    box_secrets.note_codex_status("connected", user_id=_read(d, "user") or None)
    _write(d, "status", "done")
    log.info("codex_login.connected")
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--serve":
        sys.exit(_serve(pathlib.Path(sys.argv[2])))
    sys.exit(2)
