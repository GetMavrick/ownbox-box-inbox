"""One record per person (docs/SCOPE_ONE_PERSON_RECORD.md, phase 1; owner-approved 2026-10-01).

Owner, 2026-10-01: "all these machines are going to be closely interrelated like a marketing operating system
that is unified", and phase 1 of this, "the best next thing that we can do."

A PERSON IS EVERYONE A BUSINESS MEETS THROUGH ANY MACHINE: the article reader, the commenter, the DM sender,
the prospect. Every machine writes the ways it knows someone (ids) and what happened (events) through this one
seam, so the box sees one person where it used to see four strangers, and can answer "where did this client
come from?"

LINKS ARE EXACT, NEVER A GUESS. Two ids join only on evidence a machine actually saw: the same email, an email
typed inside a DM conversation, a link we sent a known person that a web visitor then opened. Names never join
people. Every join keeps its evidence and the ids it moved, so `undo_link` puts a wrong one back exactly.

SHOWN, NOT MASKED (owner, 2026-10-01: "I would just show them. No need to mask. Clients don't want it
masked."). The record is the business's own and never leaves its box; that is the privacy. Callers show it.

THE SEAM, the only way machines touch these tables:
    identify(space, kind, value, machine=…)  -> the person id, created on first sight (None for a bad id)
    find(space, kind, value)                 -> the person id, or None
    link(space, (kind, value), (kind, value), machine=…, evidence=…) -> the person both are now
    undo_link(link_id, by=…)                 -> True when it put a join back
    touch(space, person_id, machine=…, kind=…, ref=…) -> True when the event is new
    person(person_id)                        -> {id, space, name, ids, first_seen_at, first_seen_by}
    timeline(person_id)                      -> events, newest first, across every join

Never raises for a bad value: a machine's job never fails because a person id was malformed.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

from core import state

KINDS = ("email", "phone", "instagram", "messenger", "facebook", "web", "prospect")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,253}\.[^@\s]{2,63}$")
_PLATFORM = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_WEB = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_:.-]{0,63}$")
_EVENT = re.compile(r"^[a-z][a-z_]{0,39}$")
_MAX_HOPS = 32                                   # merged_into chains this long mean a bug, not a business


def _now(at: str | None = None) -> str:
    return at or datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize(kind: str, value) -> str | None:
    """The one spelling of an id, or None when it isn't one. Emails and handles are case-blind; a phone keeps
    its digits and a leading +. Never raises."""
    if kind not in KINDS or value is None:
        return None
    v = str(value).strip()
    if kind == "email":
        v = v.lower()
        return v if len(v) <= 254 and _EMAIL.match(v) else None
    if kind == "phone":
        digits = re.sub(r"\D", "", v)
        return ("+" if v.startswith("+") else "") + digits if 7 <= len(digits) <= 15 else None
    if kind in ("instagram", "messenger", "facebook"):
        v = v.lstrip("@").lower() if kind == "instagram" else v
        return v if _PLATFORM.match(v) else None
    if kind == "web":
        return v if _WEB.match(v) else None
    return v[:200] if v and v.isprintable() else None           # prospect: a lead source's own reference


def _ok_space(space) -> bool:
    return isinstance(space, str) and 0 < len(space) <= 64


def _canonical(c, person_id: str | None) -> str | None:
    seen = 0
    while person_id and seen < _MAX_HOPS:
        row = c.execute("SELECT merged_into FROM people WHERE id=?", (person_id,)).fetchone()
        if row is None:
            return None
        if not row["merged_into"]:
            return person_id
        person_id, seen = row["merged_into"], seen + 1
    return person_id


def _find(c, space: str, kind: str, value: str) -> str | None:
    row = c.execute("SELECT person_id FROM person_ids WHERE space=? AND kind=? AND value=?",
                    (space, kind, value)).fetchone()
    return _canonical(c, row["person_id"]) if row else None


def find(space: str, kind: str, value) -> str | None:
    norm = normalize(kind, value)
    if not norm or not _ok_space(space):
        return None
    with state.connect() as c:
        return _find(c, space, kind, norm)


def _identify(c, space: str, kind: str, norm: str, machine: str, name: str, at: str) -> str:
    found = _find(c, space, kind, norm)
    if found:
        if name:
            c.execute("UPDATE people SET name=?, updated_at=? WHERE id=? AND name=''", (name[:120], at, found))
        return found
    pid = "p_" + uuid.uuid4().hex
    c.execute("INSERT INTO people (id, space, name, first_seen_at, first_seen_by, updated_at) VALUES (?,?,?,?,?,?)",
              (pid, space, (name or "")[:120], at, machine, at))
    c.execute("INSERT OR IGNORE INTO person_ids (space, kind, value, person_id, added_by, added_at) "
              "VALUES (?,?,?,?,?,?)", (space, kind, norm, pid, machine, at))
    # TWO PROCESSES CAN MEET THE SAME PERSON AT ONCE: whoever's id row won is the person; a loser's new
    # row is left with no ids and folded into the winner, never a second person.
    winner = _find(c, space, kind, norm)
    if winner != pid:
        c.execute("UPDATE people SET merged_into=?, updated_at=? WHERE id=?", (winner, at, pid))
    return winner


def identify(space: str, kind: str, value, *, machine: str, name: str = "", at: str | None = None) -> str | None:
    """The person behind this id, created the first time any machine meets them. None for a bad id."""
    norm = normalize(kind, value)
    if not norm or not _ok_space(space) or not _SLUG.match(machine or ""):
        return None
    with state.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        return _identify(c, space, kind, norm, machine, name, _now(at))


def link(space: str, a: tuple, b: tuple, *, machine: str, evidence: str, at: str | None = None) -> str | None:
    """Join the people behind two ids, on evidence a machine actually saw. Returns the person both now are.
    The earlier-met person is kept; the other's ids move to it, and the move is recorded so it can be undone.
    None when either id is bad or there is no evidence: a join is never made on nothing."""
    na, nb = normalize(a[0], a[1]), normalize(b[0], b[1])
    evidence = (evidence or "").strip()
    if not (na and nb and evidence and _ok_space(space) and _SLUG.match(machine or "")):
        return None
    when = _now(at)
    with state.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        pa = _identify(c, space, a[0], na, machine, "", when)
        pb = _identify(c, space, b[0], nb, machine, "", when)
        if pa == pb:
            return pa
        first = c.execute("SELECT id FROM people WHERE id IN (?, ?) ORDER BY first_seen_at, id LIMIT 1",
                          (pa, pb)).fetchone()["id"]
        kept, merged = (pa, pb) if first == pa else (pb, pa)
        moved = [[r["kind"], r["value"]] for r in
                 c.execute("SELECT kind, value FROM person_ids WHERE person_id=? AND space=?", (merged, space))]
        c.execute("UPDATE person_ids SET person_id=? WHERE person_id=? AND space=?", (kept, merged, space))
        c.execute("UPDATE people SET merged_into=?, updated_at=? WHERE id=?", (kept, when, merged))
        c.execute("UPDATE people SET name=(SELECT name FROM people WHERE id=?), updated_at=? "
                  "WHERE id=? AND name=''", (merged, when, kept))
        c.execute("INSERT INTO person_links (space, kept, merged, moved, machine, evidence, at) VALUES (?,?,?,?,?,?,?)",
                  (space, kept, merged, json.dumps(moved), machine, evidence[:300], when))
        return kept


def undo_link(link_id: int, *, by: str, at: str | None = None) -> bool:
    """Put a wrong join back: the merged person stands alone again with the ids it brought. False when the link
    doesn't exist, was already undone, or the person it kept has since been joined elsewhere (undo that first)."""
    when = _now(at)
    with state.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM person_links WHERE id=?", (int(link_id),)).fetchone()
        if row is None or row["undone_at"]:
            return False
        kept = c.execute("SELECT merged_into FROM people WHERE id=?", (row["kept"],)).fetchone()
        if kept is None or kept["merged_into"]:
            return False
        for kind, value in json.loads(row["moved"]):
            c.execute("UPDATE person_ids SET person_id=? WHERE space=? AND kind=? AND value=? AND person_id=?",
                      (row["merged"], row["space"], kind, value, row["kept"]))
        c.execute("UPDATE people SET merged_into=NULL, updated_at=? WHERE id=? AND merged_into=?",
                  (when, row["merged"], row["kept"]))
        c.execute("UPDATE person_links SET undone_at=?, undone_by=? WHERE id=?", (when, (by or "")[:80], row["id"]))
        return True


def touch(space: str, person_id: str | None, *, machine: str, kind: str, ref: str = "",
          at: str | None = None) -> bool:
    """Record what happened to this person. A machine telling the same thing twice records it once. Never
    raises; False for a bad call or a repeat."""
    if not (person_id and _ok_space(space) and _SLUG.match(machine or "") and _EVENT.match(kind or "")):
        return False
    with state.connect() as c:
        pid = _canonical(c, person_id)
        if not pid:
            return False
        cur = c.execute("INSERT OR IGNORE INTO person_events (person_id, space, at, machine, kind, ref) "
                        "VALUES (?,?,?,?,?,?)", (pid, space, _now(at), machine, kind, str(ref or "")[:200]))
        return cur.rowcount == 1


def _family(c, person_id: str) -> list[str]:
    rows = c.execute("WITH RECURSIVE fam(id) AS (SELECT ? UNION SELECT p.id FROM people p JOIN fam "
                     "ON p.merged_into = fam.id) SELECT id FROM fam", (person_id,)).fetchall()
    return [r["id"] for r in rows]


def person(person_id: str) -> dict | None:
    """The person as one record: every way we know them, shown plainly."""
    with state.connect() as c:
        pid = _canonical(c, person_id)
        row = c.execute("SELECT * FROM people WHERE id=?", (pid,)).fetchone() if pid else None
        if row is None:
            return None
        ids = [{"kind": r["kind"], "value": r["value"], "added_by": r["added_by"]} for r in
               c.execute("SELECT kind, value, added_by FROM person_ids WHERE person_id=? ORDER BY added_at, kind",
                         (pid,))]
        return {"id": pid, "space": row["space"], "name": row["name"], "ids": ids,
                "first_seen_at": row["first_seen_at"], "first_seen_by": row["first_seen_by"]}


def timeline(person_id: str, *, limit: int = 200) -> list[dict]:
    """Everything that happened to this person, newest first, across every join."""
    with state.connect() as c:
        pid = _canonical(c, person_id)
        if not pid:
            return []
        fam = _family(c, pid)
        marks = ",".join("?" * len(fam))
        return [{"at": r["at"], "machine": r["machine"], "kind": r["kind"], "ref": r["ref"]} for r in
                c.execute(f"SELECT at, machine, kind, ref FROM person_events WHERE person_id IN ({marks}) "
                          "ORDER BY at DESC, id DESC LIMIT ?", (*fam, int(limit)))]
