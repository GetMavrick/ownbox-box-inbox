"""A machine's own AI account, beside the Base Machine's (docs/SCOPE_ONE_PLACE_PER_SETTING.md §4).

Owner, 2026-09-24: "The base machine should have an LLM account. And each machine should have the
choice to use the base machine or another account." The box's account stays where it is, in
`box_secrets`, with its one home at System Settings → AI account. This module holds only the
machines that chose their own.

NO ROW IS THE DEFAULT. A machine with no row here uses the Base Machine's account, exactly as every
machine does today; `core.brain.think(machine=...)` with nothing stored behaves byte for byte like
`think()` with no machine at all. Choosing "Use the Base Machine's account" again deletes the row,
so choosing a different account later means signing in again (owner, 2026-09-24, §7 question 3).

CORE NAMES NO MACHINE. The key is whatever the caller passes, checked only for shape; the machine
that owns a page decides its own key. A NEW MODULE rather than names in `box_secrets.py`, because
`tests/test_core_boundary.py` freezes the names there (the reason `box_mail.MAIL_OWN` lives apart).

THE CREDENTIAL IS WRITTEN, NEVER RETURNED TO A SCREEN. `account()` is what `core.brain` reads;
`state()` is what a page may show, and it carries the kind and the status, never the value.
"""
from __future__ import annotations

import os
import re

from core import state
from core.logging import get_logger

log = get_logger(__name__)

# WHAT A MACHINE'S OWN ACCOUNT CAN BE: the same three the box can think on, and no fourth.
KINDS = ("claude_oauth", "anthropic_key", "codex")
STATUSES = ("connected", "needs_reauth", "payment_required")
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")


def _key(machine: str) -> str:
    m = str(machine or "").strip()
    if not _NAME.match(m):
        raise ValueError(f"not a machine key: {machine!r}")
    return m


def _row(machine: str) -> dict | None:
    try:
        with state.connect() as c:
            r = c.execute("SELECT * FROM machine_accounts WHERE machine = ?",
                          (_key(machine),)).fetchone()
    except ValueError:
        raise
    except Exception as e:                       # noqa: BLE001 — a box too old for the table
        log.warning("machine_accounts.unreadable", error=f"{type(e).__name__}: {e}"[:160])
        return None
    return dict(r) if r else None


def choice(machine: str) -> str:
    """"own" when this machine chose its own account, "box" (the Base Machine's) otherwise."""
    return "own" if _row(machine) else "box"


def account(machine: str) -> dict | None:
    """The credential `core.brain` should use for this machine, or None for the box's.

    NONE UNLESS IT CAN BE USED. A row whose last verdict is not "connected" is the machine's
    choice, still, but it cannot draft; returning it would stop drafting, and the owner ruled that
    it must not stop (§7 question 1). The caller falls back to the box's account and says so.
    """
    r = _row(machine)
    if not r or r.get("status") != "connected":
        return None
    return {"machine": r["machine"], "kind": r["kind"], "value": r.get("value") or "",
            "home": codex_home(r["machine"]) if r["kind"] == "codex" else ""}


def state_of(machine: str) -> dict:
    """What a page may show: the choice, the kind, the status, and when drafts last fell back.

    Never the credential. Named `state_of` so it does not shadow `core.state` inside this module.
    """
    r = _row(machine)
    if not r:
        return {"choice": "box", "kind": "", "status": "", "detail": "", "fell_back_at": ""}
    return {"choice": "own", "kind": r["kind"], "status": r["status"],
            "detail": r.get("detail") or "", "fell_back_at": r.get("fell_back_at") or ""}


def put(machine: str, kind: str, value: str = "", *, user_id: str | None = None) -> None:
    """This machine uses its own account from now on. Replaces any it had.

    `value` is the token or key; a ChatGPT sign-in is a directory (`codex_home`), so its value is
    empty. The value is never logged.
    """
    m = _key(machine)
    if kind not in KINDS:
        raise ValueError(f"unknown account kind {kind!r}")
    value = str(value or "").strip()
    if kind != "codex" and not value:
        raise ValueError("an account needs its token or key")
    with state.connect() as c:
        c.execute("INSERT INTO machine_accounts (machine, kind, value, status, detail, "
                  "fell_back_at, set_at, set_by) VALUES (?,?,?,'connected',NULL,NULL,?,?) "
                  "ON CONFLICT(machine) DO UPDATE SET kind = excluded.kind, "
                  "value = excluded.value, status = 'connected', detail = NULL, "
                  "fell_back_at = NULL, set_at = excluded.set_at, set_by = excluded.set_by",
                  (m, kind, value, state._now(), user_id))
    log.info("machine_accounts.set", machine=m, kind=kind, user=user_id)


def forget(machine: str, *, user_id: str | None = None) -> bool:
    """Back to the Base Machine's account. Returns whether the machine had its own.

    The row goes, and with it the token or key. A ChatGPT sign-in's directory is left where it is:
    nothing on this box deletes files, and the next sign-in for this machine writes over it.
    """
    m = _key(machine)
    with state.connect() as c:
        gone = c.execute("DELETE FROM machine_accounts WHERE machine = ?", (m,)).rowcount > 0
    if gone:
        log.info("machine_accounts.forgotten", machine=m, user=user_id)
    return gone


def note_status(machine: str, status: str, detail: str = "") -> None:
    """What the vendor last said about this machine's own account. Same verdicts as the box's."""
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    with state.connect() as c:
        c.execute("UPDATE machine_accounts SET status = ?, detail = ? WHERE machine = ?",
                  (status, (detail or "")[:300] or None, _key(machine)))


def note_fallback(machine: str) -> None:
    """Drafts for this machine were just written on the Base Machine's account instead of its own.

    Recorded so the machine's AI page can say so in one line (§4.2: "records that it did").
    """
    try:
        with state.connect() as c:
            c.execute("UPDATE machine_accounts SET fell_back_at = ? WHERE machine = ?",
                      (state._now(), _key(machine)))
    except Exception as e:                       # noqa: BLE001 — bookkeeping never stops a draft
        log.warning("machine_accounts.fallback_unrecorded", machine=machine,
                    error=f"{type(e).__name__}: {e}"[:160])


def codex_home(machine: str) -> str:
    """Where this machine's ChatGPT sign-in lives: its own directory beside the box's.

    ONE DIRECTORY PER MACHINE, because the Codex CLI keeps exactly one sign-in per CODEX_HOME. A
    machine sharing the box's directory would sign the box out of its own account.
    """
    base = (os.environ.get("AIOS_CODEX_HOME") or "/var/lib/aios/codex").strip().rstrip("/")
    return f"{base}-machines/{_key(machine)}"


def overview() -> list[dict]:
    """Every machine that chose its own account: key, kind and status. For the one overview (§4.1)."""
    try:
        with state.connect() as c:
            rows = c.execute("SELECT machine, kind, status, fell_back_at FROM machine_accounts "
                             "ORDER BY machine").fetchall()
    except Exception:                            # noqa: BLE001
        return []
    return [dict(r) for r in rows]
