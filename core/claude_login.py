"""Sign in to Claude from the box — a login, not a key to find and paste.

WHY THIS EXISTS. Owner, 2026-09-18: *"What am I going to click on to authorize Claude
subscription? There's no key. It's a login."* He was right, and the set-up screen was wrong. It
asked for an `sk-ant-oat…` token, which is the OUTPUT of a login somebody has to perform somewhere
else, on a laptop, in a terminal, having first installed a CLI. For the people we sell to that is
not onboarding; it is a wall.

WHAT THE CLI ACTUALLY DOES WITH NO BROWSER, measured on 2026-09-18:

    $ claude setup-token
    This will guide you through long-lived (1-year) auth token setup...
    Browser didn't open? Use the url below to sign in
    https://claude.com/cai/oauth/authorize?code=true&client_id=…&redirect_uri=…
    Paste code here if prompted >

That printed URL is the whole product. On a laptop the browser opens and the URL is invisible; on
a server it is printed and the process WAITS. So a box can run the login on the buyer's behalf,
hand them the link, and take back the short code Claude shows them. They never see a credential.

THE FLOW, AND WHO HOLDS WHAT:
  1. `start()`  — the box runs the CLI, captures the authorize URL, and keeps the process waiting.
  2. the buyer  — clicks the link, signs in AT claude.com, approves. Their password never comes
                  near this box; we see a short code and nothing else.
  3. `finish()` — feeds that code to the waiting login, which mints a 1-year token. Stored through
                  `box_secrets.put_claude_oauth`, the same door a pasted token uses.

WHOSE AUTHORIZATION THIS IS. The client is Claude Code's, exactly as it is when the buyer runs the
command on their own laptop — the box is hosting the terminal, not impersonating anybody. Nothing
here depends on Ownbox being registered as an OAuth client; that question only decides whose name
appears on the consent screen.

NO REASONING HAPPENS HERE (spec §11-6). This is authentication: a subprocess, a URL and a code.
Nothing routes through `brain.think()`, nothing is metered, and there is nothing for `cost_guard`
to meter — the same argument `brain.verify_key` makes for the API key's probe.

────────────────────────────────────────────────────────────────────────────────────────────────
WHY THE SESSION IS NOT A VARIABLE (OSDev1, 2026-09-18, before this could reach a buyer)

The first cut kept the waiting process in a module global. That is correct in one process, and a
box serves this app with TWO:

    ExecStart=…/gunicorn --worker-class gthread --workers 2 --threads 8 …
    pgrep -fa gunicorn  ->  3 processes (master + 2 workers)      [measured on image 246025588]

`start()` and `finish()` are separate HTTP requests with a trip to claude.com in between, and
nothing pins them to the same worker. About half the time the code came back to the worker that
had never heard of the login, and the buyer was told *"that sign-in has expired"* — on a box that
was still waiting for them. Pressing the button again re-rolled the same coin. It is the trap in
`reference_aios_two_processes_module_globals` wearing new clothes.

A pty and a live child cannot be shared between processes, so they do not live in a request
handler at all. `start()` spawns a DETACHED HELPER — this same module, run as `-m core.claude_login
--serve <dir>` — which owns the pty and the CLI for the life of the login. Both workers reach it
through a session directory beside the box's database:

    <dir>/pid      the helper, so any worker can reap it
    <dir>/url      the authorize URL, written once captured
    <dir>/status   starting | awaiting_code | done | error   (renamed into place, never half-read)
    <dir>/error    a sentence for the buyer
    <dir>/code     a FIFO: whichever worker takes the code writes it here
    <dir>/user     who is signing in, so the helper stores the token against them

Every file is written to a temporary name and renamed, so a worker reading while the helper writes
sees the old value or the new one and never half of either.

THE RELOAD IS NOT A DEAD END EITHER. The buyer leaves this tab to authorize and may well come back
to a reloaded page; the URL now survives in the session, so `pending_url()` can draw the link again
instead of offering a Connect button for a login already running.
"""
from __future__ import annotations

import errno
import os
import pathlib
import pty
import re
import select
import signal
import subprocess
import sys
import time

from core import box_secrets
from core.logging import get_logger

log = get_logger(__name__)

# WHAT THE CLI PRINTS. The URL is matched on claude.com's authorize path rather than on "https://"
# so a different link in the same output (docs, a status page) can never be handed to a buyer as
# their login. The terminator class excludes the BEL and ESC that hyperlink escapes wrap it in.
_URL = re.compile(r"https://claude\.com/[^\s\x07\x1b\"']+/authorize\?[^\s\x07\x1b\"']+")
# AND THE HYPERLINK ESCAPE, WHICH IS THE ONLY COMPLETE COPY. The CLI prints the URL twice: once
# inside an OSC-8 hyperlink, BEL-terminated and exact, and once as visible text the terminal WRAPS
# across lines and positions with cursor-moves instead of spaces. Reading the visible copy means
# joining those lines — and whatever follows has no whitespace to stop the match either. Measured
# on this machine 2026-09-18, and reproduced independently by OSDev1 on his own cut:
#
#   ...&state=BpZywyWl-…-DsHoldShiftwhileselectingtouseyourterminal
#   ...&state=…L9UhOVYPastecodehereifprompted>
#
# Claude refuses both. The buyer clicks, it fails, and the box looks broken on the one screen it
# cannot afford to — while every assertion of the form "a claude.com URL came back" stays green.
_URL_LINK = re.compile(r"\x1b\]8;[^;]*;(https://claude\.com/[^\x07\x1b]+/authorize\?[^\x07\x1b]+)\x07")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[=>78]|\x1b\][^\x1b]*\x1b\\")
# THE TOKEN, WHICH IS THE ONLY THING WORTH READING OUT OF THE SUCCESS OUTPUT.
_TOKEN = re.compile(r"sk-ant-oat[A-Za-z0-9_\-]{20,}")
_TOKEN_START = re.compile(r"sk-ant-oat[A-Za-z0-9_\-]*")
_TOKEN_LINE = re.compile(r"[A-Za-z0-9_\-]+")


def find_token(text: str) -> str:
    """The token the CLI printed, whole, and NOTHING that came after it.

    MEASURED 2026-09-22 ON THE OWNER'S BOX: the stored token was 130 characters and ended in
    "Storethistokensecurely". The CLI prints the 108-character token on an 80-column pty, so it
    wraps once, and then prints a blank line and the sentence "Store this token securely." The
    old capture squashed ALL whitespace first and then matched greedily, so the wrap was healed —
    and the sentence was glued on. Every draft on that box then failed with "401 OAuth access
    token is invalid", from a sign-in the screen had called done.

    A WRAP IS A LINE BREAK WITH TOKEN CHARACTERS ON BOTH SIDES AND NOTHING ELSE ON THE NEXT LINE.
    So: take the run that starts at the prefix; join the next line ONLY if it is entirely token
    characters (a wrapped tail never contains a space; a sentence always does), and stop at a
    blank line, a space, or any other character. The length is not hard-coded — that is the
    vendor's to change — but a sane ceiling stops a runaway join.
    """
    text = text.replace("\r\n", "\n").replace("\r", "")
    m = _TOKEN_START.search(text)
    if not m:
        return ""
    tok, pos = m.group(0), m.end()
    while pos < len(text) and text[pos] == "\n":
        rest = text[pos + 1:]
        n = _TOKEN_LINE.match(rest)
        if not n:
            break                                    # blank line or a non-token character
        end = n.end()
        if end < len(rest) and rest[end] not in ("\n",):
            break                                    # the line goes on with a space or more: prose
        if len(tok) + end > 256:
            break                                    # no token is this long; stop a runaway join
        tok += n.group(0)
        pos += 1 + end
    return tok if len(tok) >= 30 else ""


def redact(text: str) -> str:
    """Everything secret-shaped, replaced — before a transcript is written or read.

    THE DIAGNOSTIC NEARLY BECAME THE BREACH. The transcript keeper added hours earlier existed to
    explain FAILURES, and a SUCCESS transcript ends with the CLI printing the minted token in
    full: "Your OAuth token (valid for 1 year): sk-ant-oat01-...". On 2026-09-21 that put a live
    one-year credential on disk in plaintext, and then on a screen. The owner's token had to be
    revoked. A support artefact must never be able to carry the thing it is helping debug.

    REDACTED AT THE BOUNDARY, not at the print: anything written is already clean, so there is no
    way to read the raw form back out of a file later. Both the oat and the api-key shapes go,
    because `setup-token` is not the only thing whose output lands here.
    """
    out = _TOKEN.sub("sk-ant-oat[REDACTED]", text or "")
    return re.sub(r"sk-ant-api[A-Za-z0-9_\-]{10,}", "sk-ant-api[REDACTED]", out)


START_TIMEOUT_S = 60.0     # the CLI fetches its OAuth parameters before it can print a URL
FINISH_TIMEOUT_S = 90.0    # minting the token is a round trip to Anthropic
_STALE_AFTER_S = 900.0     # a login nobody finished is reaped rather than left holding a pty

_DIRNAME = ".claude-login"


def _clean(raw: bytes) -> str:
    """Terminal output as readable text.

    THE CLI POSITIONS EVERY WORD WITH A CURSOR-MOVE rather than spaces — `\\x1b[2GPaste\\x1b[8Gcode`
    — so stripping escapes yields `Pastecodehere`, with no spaces at all. Every match on this text
    must therefore be space-insensitive; a probe of mine looked for "Paste code here" and reported
    the prompt missing while it was on screen. Cost: one wrong answer to the owner, nearly two.
    """
    return _ANSI.sub("", raw.decode("utf-8", "replace")).replace("\r", "")


def find_url(raw: bytes, stripped: str) -> str:
    """The sign-in link, read from the hyperlink escape where there is one.

    NOTHING IS RETURNED UNLESS IT CARRIES THE PARAMETERS THAT MAKE IT AN AUTHORISATION. A
    truncated link, or a docs page, handed over as a sign-in is a dead end that looks like a
    working feature — and that is precisely the shape the corruption above produced.
    """
    text = raw.decode("utf-8", "replace")
    hit = _URL_LINK.search(text)
    url = hit.group(1) if hit else ""
    if not url:
        # NO HYPERLINK ESCAPE EXISTS ON A BOX, so this is the path that actually runs. Measured
        # from the raw pty on 2026-09-18, with the box's own TERM=dumb and NO_COLOR=1:
        #
        #   authorize?code=true&client_id=9d1c250a-e61b-44d9-88\r\r\ned-5944d1962f5e&...
        #   ...&state=FjmDPKkGaWmEgwcyxWSlshro49DDIjV1_SRAEHUi7dY\r\r\n\r\r\n\r\r\n
        #   \x1b[2GPaste\x1b[8Gcode\x1b[13Ghere\x1b[18Gif\x1b[21Gprompted\x1b[30G>
        #
        # `\x1b]8;` never appears — the OSC-8 read is inert here and only helps a terminal that
        # emits hyperlinks. Two true facts are in tension on this path: the CLI HARD-WRAPS the
        # URL at the terminal width, so the newlines inside it must be joined; and it prints its
        # prompt after a BLANK LINE, so joining every newline welds `Paste code here if
        # prompted >` onto `state` and the buyer's link is refused. Both of us shipped the second
        # half of that and asserted `client_id=` was present, which is true of the broken link.
        #
        # The blank line is the boundary the CLI actually gives us. Join wraps within a block;
        # never across one.
        for block in re.split(r"\n[ \t]*\n", text.replace("\r", "")):
            hit2 = _URL.search(_ANSI.sub("", block).replace("\n", ""))
            if hit2:
                url = hit2.group(0)
                break
    if not url or "client_id=" not in url or "code_challenge=" not in url or "state=" not in url:
        return ""
    # WHAT THE CLI SAYS NEXT IS NEVER PART OF THE LINK. Belt and braces over the structural fix
    # above, because this is the failure that survives every plausible-looking assertion.
    if "pastecode" in _squash(url) or ">" in url:
        return ""
    return url


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _sayable(text: str) -> str:
    """The tail of the CLI's output, fit to show a person.

    NEVER RAW. The first cut handed the buyer the end of the transcript, which is the wrapped
    authorize URL — `claude.co m%2Foauth%2Fcode%2Fcallback&scope=user%3Ainference&code_challenge=…`
    — as the explanation for their failed sign-in. That is worse than saying nothing: it looks like
    the box broke, and it puts a code_challenge on screen. URLs, percent-encoded runs and long
    opaque strings come out; if nothing readable is left, the caller says nothing instead.
    """
    t = re.sub(r"https?://\S+", " ", text)
    t = re.sub(r"[A-Za-z0-9_%\-]{24,}", " ", t)          # challenges, states, wrapped URL pieces
    t = re.sub(r"[^A-Za-z0-9 .,:;!?'()\-]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    # A fragment with no sentence in it is noise. Two words is the floor for something worth
    # quoting back to somebody who is already stuck.
    return t[-160:] if len(t.split()) >= 2 else ""


class LoginError(RuntimeError):
    """Something a person in front of the screen can act on. The message is for them."""


# ── the session on disk, which is what makes this work across workers ────────────────────────────

def _dir() -> pathlib.Path:
    """Beside the box's database, which is the one writable place every worker agrees on.

    IMPORTED INSIDE THE FUNCTION on purpose: a module-scope read of config freezes it at import and
    defeats every test that points the box somewhere else (`reference_config_bound_at_import`).
    """
    from core.config import settings
    return pathlib.Path(settings.db_path).resolve().parent / _DIRNAME


def _read(d: pathlib.Path, name: str) -> str:
    try:
        return (d / name).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _write(d: pathlib.Path, name: str, value: str) -> None:
    """Rename into place. A worker polling `status` must never read a half-written word."""
    tmp = d / f".{name}.{os.getpid()}"
    tmp.write_text(value, encoding="utf-8")
    os.replace(tmp, d / name)


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError as e:
        return e.errno == errno.EPERM      # running, owned by somebody else
    return True


_LAST_DIRNAME = ".claude-login-last"


def _last_dir() -> pathlib.Path:
    return _dir().parent / _LAST_DIRNAME


def _keep_last_failure(d: pathlib.Path, why: str) -> None:
    """Copy what a failed sign-in knew into a directory the next one will not overwrite.

    NEVER RAISES. This runs inside `_reap`, whose whole contract is that cleanup does not fail.
    """
    try:
        keep = _last_dir()
        keep.mkdir(parents=True, exist_ok=True)
        (keep / "when").write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                   encoding="utf-8")
        (keep / "why").write_text(str(why)[:400], encoding="utf-8")
        for f in ("transcript", "error", "status", "log"):
            src = d / f
            if src.exists():
                # BELT AND BRACES. `transcript` is redacted where it is written; `log` is the
                # helper's own stdout and has never been through that path at all.
                text = redact(src.read_text(encoding="utf-8", errors="replace"))
                (keep / f).write_text(text[-20000:], encoding="utf-8")
    except Exception as e:                               # noqa: BLE001 — see the docstring
        log.warning("claude_login.keep_failed", error=f"{type(e).__name__}: {e}")


def last_failure() -> dict:
    """What the last failed sign-in left behind, for an operator — never for a buyer's screen."""
    keep = _last_dir()
    out: dict = {}
    for f in ("when", "why", "error", "status", "transcript"):
        try:
            out[f] = redact((keep / f).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            out[f] = ""
    return out


def _reap(why: str) -> None:
    """End any live login and remove its session. Never raises: cleanup is not a place to fail."""
    d = _dir()
    if not d.exists():
        return
    try:
        pid = int(_read(d, "pid") or 0)
    except ValueError:
        pid = 0
    if _alive(pid):
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pid, sig)          # the helper is a session leader, so kill the group
            except OSError:
                try:
                    os.kill(pid, sig)
                except OSError:
                    pass
            for _ in range(20):
                if not _alive(pid):
                    break
                time.sleep(0.05)
            if not _alive(pid):
                break
    # THE EVIDENCE OUTLIVES THE SESSION. Until 2026-09-21 this loop deleted the transcript along
    # with everything else, so the ONE artefact that says why a sign-in failed was destroyed at
    # the exact moment it became worth reading. The owner hit a silent failure on his own box and
    # there was nothing to look at — not on his box, not on a clone, because the clone has to
    # reproduce a failure it cannot reproduce without his account.
    #
    # KEPT OUT OF THE SESSION DIRECTORY, because the next `start()` recreates that. This is a
    # support artefact, never shown to a buyer: it is the CLI's own words, and `_sayable` exists
    # precisely because those words are not fit to put in front of somebody.
    _keep_last_failure(d, why)
    for f in ("pid", "url", "status", "error", "code", "user", "log", "transcript"):
        try:
            (d / f).unlink()
        except OSError:
            pass
    try:
        d.rmdir()
    except OSError:
        pass
    log.info("claude_login.reaped", why=why)


def cli_present() -> bool:
    import shutil
    return bool(shutil.which("claude"))


def _live_dir() -> pathlib.Path | None:
    """The session directory IF a login is genuinely still running in it."""
    d = _dir()
    if not d.is_dir():
        return None
    try:
        pid = int(_read(d, "pid") or 0)
    except ValueError:
        return None
    if not _alive(pid):
        return None
    try:
        age = time.time() - (d / "pid").stat().st_mtime
    except OSError:
        return None
    if age > _STALE_AFTER_S:
        return None
    return d if _read(d, "status") in ("starting", "awaiting_code") else None


def in_progress() -> bool:
    return _live_dir() is not None


def pending_url() -> str:
    """The link a login already in flight is waiting on, so a reloaded page is not a dead end."""
    d = _live_dir()
    return _read(d, "url") if d is not None else ""


def start(*, consented: bool = False) -> str:
    """Begin a login and return the URL the buyer must open. Raises LoginError with a sentence.

    `consented` IS CARRIED, NOT ASSUMED. This path used to store the token with `consented=True`
    hard-coded, which recorded a tick nobody had been shown: the only consent box on the screen
    sat in the "Or paste a key" card and was never submitted by this flow. So the box wrote down
    that its owner had reviewed their provider's terms on the strength of nothing.

    The tick now sits beside Connect, where the subscription actually gets connected (owner,
    2026-09-22: *"I have reviewed tick box needs to be moved up to the use your claude
    subscription"*), and what it says travels with the login that is about to run. It is still
    never REQUIRED — `put_claude_oauth` refuses nobody, on the owner's 2026-09-18 ruling.
    """
    if not cli_present():
        raise LoginError("This box cannot sign in to Claude yet — the Claude Code CLI is not "
                         "installed on it. Ask support@ownbox.io and we will put it on.")
    # STARTING A SECOND ONE ENDS THE FIRST rather than refusing — the common case is a person who
    # closed the tab and pressed the button again, and telling them "a login is already in
    # progress" when they cannot see it is a dead end.
    _reap("a new login replaces the old one")

    d = _dir()
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    fifo = d / "code"
    try:
        os.mkfifo(fifo, 0o600)
    except FileExistsError:
        pass
    _write(d, "status", "starting")
    # BEFORE THE CHILD IS SPAWNED, because the child reads it at the moment the token appears and
    # there is no second chance to tell it. Same file-in-the-live-dir shape as `user`.
    _write(d, "consent", "1" if consented else "")

    root = pathlib.Path(__file__).resolve().parents[1]
    try:
        logf = open(d / "log", "wb")                     # noqa: SIM115 — handed to the child
    except OSError as e:
        raise LoginError(f"This box could not start the Claude sign-in ({type(e).__name__}).") from e
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "core.claude_login", "--serve", str(d)],
            cwd=str(root), stdin=subprocess.DEVNULL, stdout=logf, stderr=logf,
            close_fds=True, start_new_session=True,
            env={**os.environ, "PYTHONPATH": str(root)})
    except Exception as e:                               # noqa: BLE001
        logf.close()
        _reap("helper would not start")
        raise LoginError(f"This box could not start the Claude sign-in ({type(e).__name__}).") from e
    finally:
        try:
            logf.close()
        except OSError:
            pass
    _write(d, "pid", str(proc.pid))

    deadline = time.time() + START_TIMEOUT_S
    while time.time() < deadline:
        url = _read(d, "url")
        if url:
            log.info("claude_login.started")
            return url
        if _read(d, "status") == "error":
            why = _read(d, "error")
            _reap("the login could not start")
            raise LoginError(why or "Claude did not return a sign-in link. Try again, or paste a "
                                    "token instead.")
        if not _alive(proc.pid):
            break
        time.sleep(0.4)

    tail = _sayable(_clean(_read(d, "log").encode()))
    _reap("no url")
    raise LoginError("Claude did not return a sign-in link"
                     + (f" — it said: {tail}" if tail else " and said nothing.")
                     + " Try again, or paste a token instead.")


def finish(code: str, *, user_id: str | None = None) -> None:
    """Hand Claude's code to the waiting login. Stores the token, or raises LoginError."""
    code = (code or "").strip()
    if not code:
        raise LoginError("Paste the code Claude showed you after you signed in.")
    d = _live_dir()
    if d is None or _read(d, "status") != "awaiting_code":
        raise LoginError("That sign-in has expired. Press Connect again to start a new one.")

    # WHO, BEFORE WHAT. The helper stores the token against this person, so the name has to be in
    # place before the code that mints it goes down the pipe.
    if user_id:
        _write(d, "user", str(user_id))
    try:
        fd = os.open(str(d / "code"), os.O_WRONLY | os.O_NONBLOCK)
    except OSError as e:
        _reap("the login was not listening")
        raise LoginError("The sign-in stopped before the code reached it. Please try again.") from e
    try:
        os.write(fd, (code + "\n").encode())
    except OSError as e:
        _reap("write failed")
        raise LoginError("The sign-in stopped before the code reached it. Please try again.") from e
    finally:
        try:
            os.close(fd)
        except OSError:
            pass

    deadline = time.time() + FINISH_TIMEOUT_S
    while time.time() < deadline:
        status = _read(d, "status")
        if status == "done":
            _reap("signed in")
            log.info("claude_login.connected", user=user_id)
            return
        if status == "error":
            why = _read(d, "error")
            _reap("the cli refused it")
            raise LoginError(why or "Claude did not accept that code. Press Connect to start "
                                    "again, and paste the new code as soon as Claude shows it.")
        try:
            pid = int(_read(d, "pid") or 0)
        except ValueError:
            pid = 0
        if not _alive(pid):
            break
        time.sleep(0.4)

    # NOTHING FROM THE TRANSCRIPT ON THIS PATH. What sits at the end of the buffer here is the
    # wrapped authorize URL, and terminal wrapping breaks it into pieces too short for any
    # sanitiser to recognise — two attempts at quoting it put `scope user 3Ainference
    # code_challenge …` in front of the buyer as the reason their sign-in failed. A person cannot
    # act on a CLI transcript. They can act on a sentence that tells them what to do next.
    _reap("no token")
    raise LoginError("The sign-in did not complete — Claude did not return a token. This usually "
                     "means the code was wrong or had already expired. Press Connect to start "
                     "again, and paste the new code as soon as Claude shows it.")


def cancel() -> None:
    _reap("cancelled by the buyer")


# ── the helper: one process, one pty, one login, for as long as the buyer needs ──────────────────

SUBMIT_CHUNK = 8           # bytes per write, for the chunked fallback
SUBMIT_PAUSE_S = 0.02      # between chunks, so the CLI's reader keeps up
PASTE_START = b"\x1b[200~"  # bracketed paste, xterm's atomic-paste protocol
PASTE_END = b"\x1b[201~"


def submit_bytes(code: str | bytes) -> bytes:
    """The code as it must arrive: trimmed, CARRIAGE-RETURN terminated. See `submit_code`."""
    raw = code.encode() if isinstance(code, str) else bytes(code)
    return raw.strip() + b"\r"


def paste_bytes(code: str | bytes) -> bytes:
    """The same code wrapped in BRACKETED PASTE, which is how a terminal delivers a long string.

    `ESC[200~ … ESC[201~` tells the reader "everything between these is pasted text, take it as a
    unit". It is the mechanism this problem has, rather than a timing trick that happens to win.
    """
    raw = code.encode() if isinstance(code, str) else bytes(code)
    return PASTE_START + raw.strip() + PASTE_END


def submit_code(fd: int, code: str | bytes, *, sleep=time.sleep) -> None:
    """Deliver the code to the CLI's prompt, atomically, then press Enter.

    THE BUG THIS EXISTS FOR (2026-09-21/22, measured on claude 2.1.278 through the real finish()
    path): `claude setup-token` reads the code at a RAW-MODE masked prompt. Its Enter is `\r`,
    not `\n`, and its reader cannot take a long burst. A real ~92-character code written in ONE
    call is echoed in full as asterisks — so it plainly "arrived" — and then never acted on:

        one write, 92 chars   0/3 reacted, 20s+ each   (before: 92.1s then a misleading error)
        chunked 8B/20ms       3/3 reacted, 0.5s worst
        bracketed paste       3/3 reacted, 0.2s worst

    Every test code anybody had used was SHORT, so this passed every test and failed every real
    sign-in. The owner lost a day of demo recording to it.

    WHY BRACKETED PASTE AND NOT THE CHUNKS. Both work today. Chunking assumes 20ms is enough for
    the CLI's renderer on whatever box this is — and the renderer was observed being OUTRUN even
    when it worked (the echo came back as mixed asterisks and plaintext), so that margin is thin
    on a loaded $6 droplet. Bracketed paste is ONE write with no race in it at all. Its cost is a
    protocol assumption, and that assumption is now fixed: the CLI is pinned
    (scripts/install_claude_code.sh) and every image cut runs the sign-in mechanism check.

    THE CHUNKED PATH IS KEPT AS A FALLBACK, not as dead code: if a pinned-version bump ever meets
    a CLI that ignores bracketed paste, the escape bytes would land in the buffer as garbage, and
    `chunked_fallback` is what that upgrade should try before anybody concludes the feature is
    broken. Do not delete it because nothing calls it today.
    """
    os.write(fd, paste_bytes(code))
    sleep(SUBMIT_PAUSE_S)
    os.write(fd, b"\r")


def chunked_fallback(fd: int, code: str | bytes, *, sleep=time.sleep) -> None:
    """Type it in slowly — for a CLI that does not honour bracketed paste. See `submit_code`."""
    payload = submit_bytes(code)
    for i in range(0, len(payload), SUBMIT_CHUNK):
        os.write(fd, payload[i:i + SUBMIT_CHUNK])
        sleep(SUBMIT_PAUSE_S)


def _serve(d: pathlib.Path) -> int:
    """Own the CLI for the life of one login. Runs detached; both workers talk to it through `d`.

    It writes what it learns into the session directory and takes the buyer's code off the FIFO.
    Every exit path leaves a terminal status behind, because a worker polling `status` must never
    be left reading `awaiting_code` at a corpse.
    """
    buf = b""

    def keep_transcript() -> None:
        """The CLI's own words, on disk, before anything can kill this process.

        WRITTEN ON EVERY EXIT PATH AND WHILE WAITING, not only at the end: the helper can be
        SIGKILLed by a reap, and a transcript that only lands on a clean exit is missing for
        exactly the failures worth reading."""
        try:
            _write(d, "transcript", redact(_clean(buf))[-20000:])
        except Exception:                                # noqa: BLE001 — never break the login
            pass

    def fail(sentence: str) -> int:
        keep_transcript()
        _write(d, "error", sentence)
        _write(d, "status", "error")
        return 1

    master, slave = pty.openpty()
    # TERM=dumb KEEPS THE OUTPUT PARSEABLE and, more importantly, keeps the CLI from drawing a
    # full-screen interface we would have to reverse-engineer. The URL and the prompt still print.
    env = {**os.environ, "TERM": "dumb", "NO_COLOR": "1"}
    # The box's own token must not be inherited into a process whose whole job is to mint a new
    # one for somebody else — that is how a login "succeeds" without the person ever signing in.
    env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    try:
        proc = subprocess.Popen(["claude", "setup-token"], stdin=slave, stdout=slave, stderr=slave,
                                env=env, close_fds=True)
    except Exception as e:                               # noqa: BLE001
        os.close(master)
        os.close(slave)
        return fail(f"This box could not start the Claude sign-in ({type(e).__name__}).")
    os.close(slave)

    # ── 1. wait for the authorize URL ────────────────────────────────────────────────────────
    deadline = time.time() + START_TIMEOUT_S
    url = ""
    while time.time() < deadline and not url:
        r, _, _ = select.select([master], [], [], 1.0)
        if r:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
        found = find_url(buf, _clean(buf))
        if found:
            url = found
            break
        if proc.poll() is not None:
            break
    if not url:
        tail = _sayable(_clean(buf))
        if proc.poll() is None:
            proc.kill()
        os.close(master)
        return fail("Claude did not return a sign-in link"
                    + (f" — it said: {tail}" if tail else " and said nothing.")
                    + " Try again, or paste a token instead.")
    _write(d, "url", url)
    _write(d, "status", "awaiting_code")

    # ── 2. wait for the buyer's code, without holding the FIFO open against them ─────────────
    # O_RDONLY|O_NONBLOCK returns immediately with no writer, which is what lets this wait be
    # interruptible and bounded rather than a block with no way out.
    try:
        cfd = os.open(str(d / "code"), os.O_RDONLY | os.O_NONBLOCK)
    except OSError as e:
        proc.kill()
        os.close(master)
        return fail(f"This box could not wait for your code ({type(e).__name__}).")
    code = b""
    deadline = time.time() + _STALE_AFTER_S
    while time.time() < deadline and not code.strip():
        r, _, _ = select.select([cfd, master], [], [], 1.0)
        if cfd in r:
            try:
                code += os.read(cfd, 4096)
            except OSError:
                pass
        if master in r:
            try:
                buf += os.read(master, 65536) or b""
            except OSError:
                pass
        if proc.poll() is not None and not code.strip():
            os.close(cfd)
            os.close(master)
            return fail("The sign-in ended before your code arrived. Press Connect to start again.")
    os.close(cfd)
    if not code.strip():
        if proc.poll() is None:
            proc.kill()
        os.close(master)
        return fail("That sign-in expired while it waited. Press Connect to start a new one.")

    # HOW MANY TIMES IT HAS ASKED, BEFORE WE ANSWER. If the CLI asks AGAIN after our code, the
    # code was refused — that is the fastest and most reliable signal it gives, because it does
    # not always print the word "invalid". Without this a wrong code sat until the timeout and
    # then reported as if the box had failed rather than the code.
    asked_before = _squash(_clean(buf)).count("pastecodehere")
    try:
        submit_code(master, code)
    except OSError:
        proc.kill()
        os.close(master)
        return fail("The sign-in stopped before the code reached it. Please try again.")

    # ── 3. the token, or the reason there is not one ─────────────────────────────────────────
    deadline = time.time() + FINISH_TIMEOUT_S
    while time.time() < deadline:
        r, _, _ = select.select([master], [], [], 1.0)
        if r:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
        text = _clean(buf)
        # THE TOKEN IS SEARCHED FOR IN THE WHOLE TRANSCRIPT, not only the newest chunk: it is
        # printed across a wrapped line, and a chunk boundary can land in the middle of it.
        # WHOLE, AND NOTHING MORE — see find_token for the night this line cost.
        found = find_token(text)
        if found:
            user = _read(d, "user") or None
            # BOTH HALVES OF THIS LINE WERE FIXED TONIGHT, BY TWO PEOPLE, AN HOUR APART, and a
            # merge that kept either one alone would have quietly undone the other. `found` is
            # #1422's — the token captured whole and nothing that came after it. The consent is
            # this branch's — read from the login's own directory rather than asserted, because
            # the tick a buyer is shown now sits beside Connect and has to survive the round trip.
            box_secrets.put_claude_oauth(found, consented=_read(d, "consent") == "1",
                                         user_id=user)
            keep_transcript()
            _write(d, "status", "done")
            if proc.poll() is None:
                proc.kill()
            os.close(master)
            return 0
        flat = _squash(text)
        if flat.count("pastecodehere") > asked_before:
            proc.kill()
            os.close(master)
            return fail("Claude did not accept that code. Press Connect to start again, and paste "
                        "the new code as soon as Claude shows it to you.")
        # A REFUSED CODE IS THE LIKELY FAILURE and it must not read as a timeout. The CLI says so
        # in words; the words are matched space-insensitively for the reason `_clean` explains.
        for bad, said in (("invalid", "Claude did not accept that code."),
                          ("expired", "That code had expired."),
                          ("failed", "Claude refused the sign-in.")):
            if bad in flat:
                proc.kill()
                os.close(master)
                return fail(said + " Start again and paste the new code promptly.")
        keep_transcript()
        if proc.poll() is not None:
            # THE CLI FINISHED AND WE FOUND NO TOKEN — a DIFFERENT failure from a refusal, and it
            # is named separately because it points somewhere else entirely: either the CLI
            # stopped PRINTING the token (its output format is not a contract we control) or it
            # stored the credential itself. Reported as a timeout, that reads as our bug when it
            # is a shape change, and the transcript beside it is what settles which.
            keep_transcript()
            os.close(master)
            return fail("Claude finished the sign-in but this box did not recognise a token in "
                        "what it printed. Nothing is wrong with your account. Paste a token "
                        "instead, and tell support the sign-in ended without one.")
    if proc.poll() is None:
        proc.kill()
    os.close(master)
    return fail("The sign-in did not complete — Claude did not return a token. This usually means "
                "the code was wrong or had already expired. Press Connect to start again, and "
                "paste the new code as soon as Claude shows it.")


if __name__ == "__main__":                               # pragma: no cover — the detached helper
    if len(sys.argv) == 3 and sys.argv[1] == "--serve":
        raise SystemExit(_serve(pathlib.Path(sys.argv[2])))
    print("usage: python -m core.claude_login --serve <session-dir>", file=sys.stderr)
    raise SystemExit(2)
