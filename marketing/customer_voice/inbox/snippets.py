"""Saved replies ("snippets"): one list per Space, picked from a dropdown above the reply box.

docs/SCOPE_INBOX_SNIPPETS.md (#1821). Owner, 2026-10-02: *"A killer feature would be to have a list of snippets where
on each message you could pull a drop-down and see all of them and select them and then have a send button where
that snippet could be sent. The way I would use this as I would have sales emails with links to resources and I would
use this multiple times each day."* He chose all four recommendations the same evening:
  1. replies only (a snippet answers someone who wrote; new cold emails are another feature);
  2. the owner edits the list, every member uses it;
  3. picking one FILLS THE REPLY BOX and the person presses Send (one look, no extra tap);
  4. two fill-ins to start: {first_name} and {my_name}.
And on 2026-10-03: *"Yes, proceed and do numbers one and then two. Finish them."*

A SNIPPET IS NEVER A SEND. It only fills the box; Send goes through `reply.send_reply` with every check that has
(opted out, the claim, the signature on email). Nothing here can reach a person.
"""
from __future__ import annotations

import re
import secrets

from core import state
from core.logging import get_logger

log = get_logger(__name__)

TITLE_MAX = 60
BODY_MAX = 1800                 # the reply box's own maxlength (app._compose)
LIST_MAX = 100                  # a dropdown longer than this is not a dropdown anyone can use
FILL_INS = ("{first_name}", "{my_name}")
_COLS = "id, title, body, uses, last_used_at, created_by, created_at, updated_at, archived_at"


class SnippetRefused(ValueError):
    """The words a Settings page shows when a snippet can't be saved."""


def _now() -> str:
    return state._now()


def _clean(title, body) -> tuple[str, str]:
    t = " ".join(str(title or "").split())
    b = str(body or "").replace("\r\n", "\n").strip()
    if not t:
        raise SnippetRefused("Give the snippet a name: it is what the dropdown shows.")
    if len(t) > TITLE_MAX:
        raise SnippetRefused(f"A snippet's name can be up to {TITLE_MAX} characters.")
    if not b:
        raise SnippetRefused("A snippet needs words to put in the reply.")
    if len(b) > BODY_MAX:
        raise SnippetRefused(f"A snippet can be up to {BODY_MAX:,} characters, the reply box's own limit.")
    return t, b


def all_for(space: str, *, archived: bool = False) -> list[dict]:
    """The list, most used first (then most recently used, then by name). Archived ones only when asked."""
    with state.connect() as c:
        rows = c.execute(
            f"SELECT {_COLS} FROM inbox_snippets WHERE space = ? AND archived_at IS {'NOT ' if archived else ''}NULL "
            "ORDER BY uses DESC, COALESCE(last_used_at, '') DESC, title COLLATE NOCASE LIMIT ?",
            (space, LIST_MAX)).fetchall()
    return [dict(r) for r in rows]


def get(space: str, snippet_id: str) -> dict | None:
    """One live snippet of this Space, or None. Another Space's id is not found, never forbidden."""
    with state.connect() as c:
        r = c.execute(f"SELECT {_COLS} FROM inbox_snippets WHERE space = ? AND id = ? AND archived_at IS NULL",
                      (space, str(snippet_id or ""))).fetchone()
    return dict(r) if r else None


def add(space: str, title, body, *, by: str | None = None) -> dict:
    t, b = _clean(title, body)
    if len(all_for(space)) >= LIST_MAX:
        raise SnippetRefused(f"The list holds up to {LIST_MAX} snippets; archive one to add another.")
    sid, now = "snp_" + secrets.token_hex(6), _now()
    with state.connect() as c:
        c.execute("INSERT INTO inbox_snippets (space, id, title, body, created_by, created_at, updated_at) "
                  "VALUES (?,?,?,?,?,?,?)", (space, sid, t, b, by, now, now))
    log.info("inbox.snippet_created", extra={"space": space, "snippet": sid})
    return get(space, sid)


def edit(space: str, snippet_id: str, title, body) -> dict:
    t, b = _clean(title, body)
    with state.connect() as c:
        n = c.execute("UPDATE inbox_snippets SET title = ?, body = ?, updated_at = ? "
                      "WHERE space = ? AND id = ? AND archived_at IS NULL",
                      (t, b, _now(), space, str(snippet_id or ""))).rowcount
    if not n:
        raise SnippetRefused("That snippet isn't on this box any more.")
    return get(space, snippet_id)


def archive(space: str, snippet_id: str) -> bool:
    with state.connect() as c:
        n = c.execute("UPDATE inbox_snippets SET archived_at = ?, updated_at = ? "
                      "WHERE space = ? AND id = ? AND archived_at IS NULL",
                      (_now(), _now(), space, str(snippet_id or ""))).rowcount
    return bool(n)


def used(space: str, snippet_id: str) -> None:
    """Count one send that started from this snippet (the dropdown's order). Never raises: a count never costs a send."""
    try:
        with state.connect() as c:
            c.execute("UPDATE inbox_snippets SET uses = uses + 1, last_used_at = ? WHERE space = ? AND id = ?",
                      (_now(), space, str(snippet_id or "")))
    except Exception as e:                                # noqa: BLE001
        log.warning("inbox.snippet_count_failed", extra={"error": type(e).__name__})


def first_name(participant) -> str:
    """The first word of a conversation's name, or "there" when it has none a person would use."""
    p = " ".join(str(participant or "").split())
    if not p or "@" in p or not re.match(r"^[^\W\d_]", p):
        return "there"
    return p.split(" ")[0][:40]


def fill(body: str, *, participant=None, my_name=None) -> str:
    """The snippet's words with its fill-ins replaced. The reply box shows the result, so nothing surprising goes."""
    mine = " ".join(str(my_name or "").split())
    return (str(body or "").replace("{first_name}", first_name(participant))
            .replace("{my_name}", mine.split(" ")[0] if mine else ""))
