"""The Business Brain's files: the folders and documents a business keeps on its own box, for people and agents alike.

Owner, 2026-10-08: "we should definitely have a basic files manager immediately and Should have it listed as Business
Brain in the menu at the top", and, on tapping a file to send it to a client through the phone's share sheet, "it
would be nice". Same day, on Google Drive and the like: he decided "against anything that goes against our ethos".
So a business's documents live on its own box, in its own folders, and leave it only when a person sends one.

WHERE THE BYTES LIVE. On the box's disk, beside its database, each file named by the sha256 of its content
(`root()/blobs/ab/abcdef...`). A name never reaches the disk, so no name can climb out of the folder, and the same
document saved twice is stored once. The rows in `brain_items` (core/state.py) are its name, its folder and who
added or changed it; `brain_history` keeps every change and who made it.

NOTHING IS REMOVED AT ONCE. Delete moves an item, and everything inside a folder, to the trash for TRASH_DAYS, and
restore puts it back. Saving over a file keeps the old version in the trash too. `purge()`, run now and then when the
brain is opened (no job of its own), removes what has been in the trash longer, and a file's bytes only once no row anywhere still needs them.

SERVED SO A FILE CAN NEVER RUN AS THE BOX. A document is someone's bytes on the box's own address: an HTML or SVG file
opened there would run with the signed-in person's session. So only images, PDFs and plain text are ever shown in the
browser (`serve_headers`); everything else is downloaded, every answer says nosniff, and a shown image or text also
carries a sandbox policy. A person who wants to send it still can: the screen hands the bytes to the phone's share
sheet.

AGENTS read it through the connector (core/brain_tools.py) and may only ask to save: the owner approves on Approvals.
"""
from __future__ import annotations

import hashlib
import io
import mimetypes
import os
import re
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from core import state
from core.config import settings
from core.logging import get_logger

log = get_logger(__name__)

NAME_MAX = 120
FILE_MAX = 100 << 20                      # one file (owner, 2026-10-08: "It should probably be 100 MB per file")
TOTAL_MAX = 5 << 30                       # every file together, unless the box's config says more (total_max)
CHUNK = 1 << 20                           # a file is written and read a megabyte at a time, never held whole
DISK_FLOOR = 1 << 30                      # never fill the box: this much stays free for everything else
TRASH_DAYS = 30
TEXT_MAX = 200_000                        # the most of one document an agent reads, in characters
PURGE_EVERY = 6 * 3600                    # seconds between two looks at the trash
SEARCH_TEXT_MAX = 1 << 20                 # a text file larger than this is searched by its name only

# What a browser is ever allowed to SHOW from the brain. Everything else is a download.
INLINE = {"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf", "text/plain"}
# What an agent can read as words today. PDFs and Word files are listed by name until the box can read inside them.
TEXT_TYPES = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml", ".html", ".htm", ".xml", ".log"}

_BAD = re.compile(r"[\x00-\x1f\x7f/\\]")


class BrainError(ValueError):
    """A request the brain refuses. The message is a sentence for the person who asked."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def root() -> Path:
    return Path(settings.db_path).resolve().parent / "brain"


def _blob(sha: str) -> Path:
    return root() / "blobs" / sha[:2] / sha


# ── names ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def clean_name(name) -> str:
    """A name as a person would read it: no slashes or control characters, spaces tidied, at most NAME_MAX."""
    n = " ".join(_BAD.sub(" ", str(name or "")).split()).strip()
    if n in ("", ".", ".."):
        raise BrainError("Give it a name.")
    if len(n) > NAME_MAX:
        stem, ext = os.path.splitext(n)
        n = stem[:NAME_MAX - len(ext[:12])].rstrip() + ext[:12]
    return n


def _taken(c, parent: str, name: str, exclude: str = "") -> bool:
    return c.execute("SELECT 1 FROM brain_items WHERE parent = ? AND lower(name) = lower(?) AND deleted_at IS NULL "
                     "AND id != ?", (parent, name, exclude)).fetchone() is not None


def _free_name(c, parent: str, name: str, exclude: str = "") -> str:
    """`name`, or `name (2).ext`, `name (3).ext` ... whichever is free in the folder."""
    if not _taken(c, parent, name, exclude):
        return name
    stem, ext = os.path.splitext(name)
    for i in range(2, 1000):
        candidate = f"{stem} ({i}){ext}"
        if not _taken(c, parent, candidate, exclude):
            return candidate
    raise BrainError("That folder already has too many items with that name.")


# ── reading ───────────────────────────────────────────────────────────────────────────────────────────────────────
def _row(c, item_id: str, *, deleted: bool = False) -> dict | None:
    r = c.execute("SELECT * FROM brain_items WHERE id = ?" + ("" if deleted else " AND deleted_at IS NULL"),
                  (str(item_id or ""),)).fetchone()
    return dict(r) if r else None


def get(item_id: str) -> dict | None:
    with state.connect() as c:
        return _row(c, item_id)


def _folder(c, folder_id: str) -> str:
    """The id of a live folder, or '' for the top. Raises when it is not one."""
    fid = str(folder_id or "")
    if not fid:
        return ""
    r = _row(c, fid)
    if not r or r["kind"] != "folder":
        raise BrainError("That folder isn't there any more.")
    return fid


def _crumbs(c, item_id: str) -> list[dict]:
    out, seen, cur = [], set(), str(item_id or "")
    while cur and cur not in seen:
        seen.add(cur)
        r = _row(c, cur, deleted=True)
        if not r:
            break
        out.append({"id": r["id"], "name": r["name"]})
        cur = r["parent"]
    return list(reversed(out))


def _public(r: dict) -> dict:
    return {"id": r["id"], "kind": r["kind"], "name": r["name"], "parent": r["parent"], "size": r["size"],
            "mime": r["mime"], "added_by": r["added_by"], "added_at": r["added_at"], "changed_by": r["changed_by"],
            "changed_at": r["changed_at"]}


def listing(folder_id: str = "") -> dict:
    """{"folder": {...} | None, "path": [{"id", "name"}], "items": [...]}: folders first, then files, by name."""
    _purge_now_and_then()
    with state.connect() as c:
        fid = _folder(c, folder_id)
        rows = c.execute("SELECT * FROM brain_items WHERE parent = ? AND deleted_at IS NULL "
                         "ORDER BY kind = 'file', lower(name)", (fid,)).fetchall()
        here = _row(c, fid) if fid else None
        return {"folder": _public(here) if here else None, "path": _crumbs(c, fid),
                "items": [_public(dict(r)) for r in rows]}


def path_of(item_id: str) -> str:
    with state.connect() as c:
        return "/".join(x["name"] for x in _crumbs(c, item_id))


def resolve(path: str) -> dict | None:
    """The live item at "Folder/Sub/name.pdf", names matched without regard to case, or None."""
    parts = [p for p in str(path or "").replace("\\", "/").split("/") if p.strip()]
    parent, r = "", None
    with state.connect() as c:
        for p in parts:
            r = c.execute("SELECT * FROM brain_items WHERE parent = ? AND lower(name) = lower(?) AND deleted_at IS "
                          "NULL", (parent, p.strip())).fetchone()
            if r is None:
                return None
            parent = r["id"]
    return dict(r) if r else None


def blob_path(item_id: str) -> tuple[Path, dict]:
    """Where a live file's bytes are on the disk, and its row, for streaming it. Raises BrainError when it is gone."""
    r = get(item_id)
    if not r or r["kind"] != "file":
        raise BrainError("That file isn't there any more.")
    p = _blob(r["sha256"])
    if not p.is_file():
        log.error("brain.blob_missing", item=r["id"])
        raise BrainError("That file's contents are missing from the box.")
    return p, r


def content(item_id: str) -> tuple[bytes, dict]:
    """A file's bytes and its row. Raises BrainError when it is not a live file or its bytes are missing."""
    r = get(item_id)
    if not r or r["kind"] != "file":
        raise BrainError("That file isn't there any more.")
    try:
        return _blob(r["sha256"]).read_bytes(), r
    except OSError:
        log.error("brain.blob_missing", item=r["id"])
        raise BrainError("That file's contents are missing from the box.") from None


def is_text(name: str) -> bool:
    return os.path.splitext(str(name or ""))[1].lower() in TEXT_TYPES


def read_text(item_id: str) -> str | None:
    """A text document's words, at most TEXT_MAX characters; None for a file the box can't read as words yet."""
    path, r = blob_path(item_id)
    if not is_text(r["name"]):
        return None
    with open(path, "rb") as f:                       # never a 100 MB text file into memory: its start is enough
        return f.read(TEXT_MAX * 4).decode("utf-8", "replace")[:TEXT_MAX]


def search(q: str, limit: int = 50) -> list[dict]:
    """Live items whose name holds every word of `q`, then text documents whose words do. Each with its path."""
    words = [w for w in str(q or "").lower().split() if w][:8]
    if not words:
        return []
    out, seen = [], set()
    with state.connect() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM brain_items WHERE deleted_at IS NULL ORDER BY changed_at "
                                           "DESC").fetchall()]
    for r in rows:
        if all(w in r["name"].lower() for w in words):
            out.append(r)
            seen.add(r["id"])
    for r in rows:
        if len(out) >= limit:
            break
        if r["id"] in seen or r["kind"] != "file" or not is_text(r["name"]) or r["size"] > SEARCH_TEXT_MAX:
            continue
        try:
            text = _blob(r["sha256"]).read_bytes().decode("utf-8", "replace").lower()
        except OSError:
            continue
        if all(w in text for w in words):
            out.append(r)
    return [{**_public(r), "path": path_of(r["id"])} for r in out[:limit]]


def trash() -> list[dict]:
    """What was deleted and can still be put back: each deletion once (not every file inside a deleted folder)."""
    _purge_now_and_then()
    cut = datetime.now(timezone.utc)
    with state.connect() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT i.* FROM brain_items i WHERE i.deleted_at IS NOT NULL AND NOT EXISTS (SELECT 1 FROM brain_items p "
            "WHERE p.id = i.parent AND p.deleted_at = i.deleted_at) ORDER BY i.deleted_at DESC").fetchall()]
    out = []
    for r in rows:
        try:
            gone = datetime.fromisoformat(r["deleted_at"])
        except ValueError:
            gone = cut
        out.append({**_public(r), "deleted_at": r["deleted_at"], "deleted_by": r["deleted_by"],
                    "days_left": max(0, TRASH_DAYS - (cut - gone).days)})
    return out


def usage() -> dict:
    with state.connect() as c:
        r = c.execute("SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS b FROM brain_items WHERE kind = 'file' AND "
                      "deleted_at IS NULL").fetchone()
    return {"files": int(r["n"]), "bytes": int(r["b"]), "max": total_max()}


def history(item_id: str, limit: int = 50) -> list[dict]:
    with state.connect() as c:
        return [dict(r) for r in c.execute("SELECT at, action, by, detail FROM brain_history WHERE item = ? ORDER BY "
                                           "id DESC LIMIT ?", (str(item_id), int(limit))).fetchall()]


# ── writing ───────────────────────────────────────────────────────────────────────────────────────────────────────
def _log(c, item: str, action: str, by: str, detail: str = "") -> None:
    c.execute("INSERT INTO brain_history (at, item, action, by, detail) VALUES (?,?,?,?,?)",
              (_now(), item, action, str(by)[:80], str(detail)[:300]))


def _by(by) -> str:
    b = str(by or "").strip()
    if not b:
        raise BrainError("Every change says who made it.")
    return b[:80]


def make_folder(parent: str, name: str, *, by: str) -> dict:
    by, name = _by(by), clean_name(name)
    with state.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        pid = _folder(c, parent)
        name = _free_name(c, pid, name)
        iid, now = uuid.uuid4().hex, _now()
        c.execute("INSERT INTO brain_items (id, parent, kind, name, added_by, added_at, changed_by, changed_at) "
                  "VALUES (?,?,?,?,?,?,?,?)", (iid, pid, "folder", name, by, now, by, now))
        _log(c, iid, "added", by, name)
        return _public(_row(c, iid))


def total_max() -> int:
    """How much the brain may hold. MORE ROOM IS A SETTING, NEVER A CODE CHANGE (owner, 2026-10-08: "then we could
    upgrade their storage somehow"): `business_brain: {max_gb: N}` in the box's config, else TOTAL_MAX."""
    try:
        from core.config import get_config
        gb = float((get_config().get("business_brain") or {}).get("max_gb") or 0)
    except Exception:                                # noqa: BLE001 — an unreadable config keeps the default
        gb = 0
    return int(gb * (1 << 30)) if gb > 0 else TOTAL_MAX


def _store(stream) -> tuple[str, int]:
    """Copy a stream to the disk a megabyte at a time, under its sha256, atomically, refusing it past FILE_MAX or
    the disk's floor before it is kept. -> (sha256, size). Nothing is left behind when it is refused."""
    r = root()
    (r / "tmp").mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(r).free < DISK_FLOOR + CHUNK:
        raise BrainError("Your Ownbox is nearly out of space, so nothing more can be saved.")
    tmp = r / "tmp" / uuid.uuid4().hex
    h, size = hashlib.sha256(), 0
    try:
        with open(tmp, "wb") as out:
            while True:
                chunk = stream.read(CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > FILE_MAX:
                    raise BrainError(f"Files must be under {FILE_MAX >> 20} MB.")
                h.update(chunk)
                out.write(chunk)
        if shutil.disk_usage(r).free < DISK_FLOOR:
            raise BrainError("Your Ownbox is nearly out of space, so nothing more can be saved.")
        if usage()["bytes"] + size > total_max():
            raise BrainError(f"The Business Brain holds {total_max() / (1 << 30):g} GB, and this would go past it. "
                             "Delete something first.")
        sha = h.hexdigest()
        p = _blob(sha)
        if p.exists():
            tmp.unlink()
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, p)
        return sha, size
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def save(parent: str, name: str, data: bytes, *, by: str, replace: bool = False) -> dict:
    """Keep a file in a folder, from bytes in hand (an agent's document). See save_stream. -> the file's row."""
    return save_stream(parent, name, io.BytesIO(bytes(data or b"")), by=by, replace=replace)


def save_stream(parent: str, name: str, stream, *, by: str, replace: bool = False) -> dict:
    """Keep a file in a folder, read from a stream (an upload) a megabyte at a time. A name already there gets a
    number added, unless `replace`: then the file there is updated and its old version goes to the trash, where it
    can be put back. -> the file's row."""
    by, name = _by(by), clean_name(name)
    sha, size = _store(stream)
    try:
        return _save_row(parent, name, size, sha, by=by, replace=replace)
    except Exception:
        _unlink_unused({sha})                       # a save that did not land leaves no bytes behind
        raise


def _save_row(parent: str, name: str, size: int, sha: str, *, by: str, replace: bool) -> dict:
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    with state.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        pid = _folder(c, parent)
        now = _now()
        old = c.execute("SELECT * FROM brain_items WHERE parent = ? AND lower(name) = lower(?) AND deleted_at IS NULL",
                        (pid, name)).fetchone() if replace else None
        if old is not None and old["kind"] == "file":
            keep = uuid.uuid4().hex
            stem, ext = os.path.splitext(old["name"])
            c.execute("INSERT INTO brain_items (id, parent, kind, name, sha256, size, mime, added_by, added_at, "
                      "changed_by, changed_at, deleted_at, deleted_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (keep, pid, "file", f"{stem} (before {now[:10]}){ext}", old["sha256"], old["size"], old["mime"],
                       old["added_by"], old["added_at"], old["changed_by"], old["changed_at"], now, by))
            c.execute("UPDATE brain_items SET sha256 = ?, size = ?, mime = ?, changed_by = ?, changed_at = ? "
                      "WHERE id = ?", (sha, size, mime, by, now, old["id"]))
            _log(c, old["id"], "replaced", by, f"the old version is in the trash ({keep})")
            return _public(_row(c, old["id"]))
        name = _free_name(c, pid, name)
        iid = uuid.uuid4().hex
        c.execute("INSERT INTO brain_items (id, parent, kind, name, sha256, size, mime, added_by, added_at, changed_by, "
                  "changed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (iid, pid, "file", name, sha, size, mime, by, now, by, now))
        _log(c, iid, "added", by, name)
        return _public(_row(c, iid))


def rename(item_id: str, name: str, *, by: str) -> dict:
    by, name = _by(by), clean_name(name)
    with state.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        r = _row(c, item_id)
        if not r:
            raise BrainError("That isn't there any more.")
        name = _free_name(c, r["parent"], name, exclude=r["id"])
        c.execute("UPDATE brain_items SET name = ?, changed_by = ?, changed_at = ? WHERE id = ?",
                  (name, by, _now(), r["id"]))
        _log(c, r["id"], "renamed", by, f"{r['name']} -> {name}")
        return _public(_row(c, r["id"]))


def move(item_id: str, parent: str, *, by: str) -> dict:
    by = _by(by)
    with state.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        r = _row(c, item_id)
        if not r:
            raise BrainError("That isn't there any more.")
        pid = _folder(c, parent)
        if r["kind"] == "folder" and any(x["id"] == r["id"] for x in _crumbs(c, pid)):
            raise BrainError("A folder can't go inside itself.")
        name = _free_name(c, pid, r["name"], exclude=r["id"])
        c.execute("UPDATE brain_items SET parent = ?, name = ?, changed_by = ?, changed_at = ? WHERE id = ?",
                  (pid, name, by, _now(), r["id"]))
        _log(c, r["id"], "moved", by, "/".join(x["name"] for x in _crumbs(c, pid)) or "the top")
        return _public(_row(c, r["id"]))


def _descendants(c, item_id: str, *, deleted_at=None) -> list[str]:
    out, todo = [], [item_id]
    while todo:
        cur = todo.pop()
        q = ("SELECT id FROM brain_items WHERE parent = ? AND deleted_at IS NULL" if deleted_at is None else
             "SELECT id FROM brain_items WHERE parent = ? AND deleted_at = ?")
        kids = [r["id"] for r in c.execute(q, (cur,) if deleted_at is None else (cur, deleted_at)).fetchall()]
        out += kids
        todo += kids
    return out


def delete(item_id: str, *, by: str) -> dict:
    """To the trash, with everything inside it, for TRASH_DAYS. -> {"deleted": how many, "days": TRASH_DAYS}."""
    by = _by(by)
    with state.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        r = _row(c, item_id)
        if not r:
            raise BrainError("That isn't there any more.")
        now = _now()
        ids = [r["id"]] + _descendants(c, r["id"])
        c.executemany("UPDATE brain_items SET deleted_at = ?, deleted_by = ? WHERE id = ?", [(now, by, i) for i in ids])
        _log(c, r["id"], "deleted", by, f"{len(ids)} item(s)")
        return {"deleted": len(ids), "days": TRASH_DAYS}


def restore(item_id: str, *, by: str) -> dict:
    """Out of the trash, with what went with it. Back where it was, or at the top when that folder is gone."""
    by = _by(by)
    with state.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        r = _row(c, item_id, deleted=True)
        if not r or not r["deleted_at"]:
            raise BrainError("That isn't in the trash.")
        parent = r["parent"] if (not r["parent"] or _row(c, r["parent"])) else ""
        name = _free_name(c, parent, r["name"])
        ids = [r["id"]] + _descendants(c, r["id"], deleted_at=r["deleted_at"])
        c.executemany("UPDATE brain_items SET deleted_at = NULL, deleted_by = NULL WHERE id = ?", [(i,) for i in ids])
        c.execute("UPDATE brain_items SET parent = ?, name = ?, changed_by = ?, changed_at = ? WHERE id = ?",
                  (parent, name, by, _now(), r["id"]))
        _log(c, r["id"], "restored", by, f"{len(ids)} item(s)")
        return _public(_row(c, r["id"]))


def purge(days: int = TRASH_DAYS) -> int:
    """Remove what has been in the trash longer than `days`, and the bytes no row needs any more. -> rows removed."""
    cut = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    with state.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        gone = [dict(r) for r in c.execute("SELECT id, sha256 FROM brain_items WHERE deleted_at IS NOT NULL AND "
                                           "deleted_at < ?", (cut,)).fetchall()]
        for g in gone:
            c.execute("DELETE FROM brain_items WHERE id = ?", (g["id"],))
            _log(c, g["id"], "purged", "the trash", f"after {days} days")
    removed = _unlink_unused({g["sha256"] for g in gone if g["sha256"]})
    if gone:
        log.info("brain.purged", rows=len(gone), blobs=removed)
    return len(gone)


def _unlink_unused(shas: set) -> int:
    """Remove the bytes of each sha256 no row needs any more. -> how many went."""
    if not shas:
        return 0
    with state.connect() as c:
        still = {r[0] for r in c.execute("SELECT DISTINCT sha256 FROM brain_items WHERE sha256 != ''").fetchall()}
    n = 0
    for sha in shas - still:
        try:
            _blob(sha).unlink()
            n += 1
        except OSError:
            pass
    return n


# ── the way out ───────────────────────────────────────────────────────────────────────────────────────────────────
def export_tree(dest: str) -> int:
    """Write every live folder and file as an ordinary folder tree under `dest`. -> files written.

    A PROTOTYPE THAT IS EASY TO REPLACE (owner, 2026-10-08: "easily replaced by version two or easily upgraded ...
    nothing invasive to our architecture"). This is the one step from this storage to any other: a version two
    imports the tree, or works on it as it is. Names are the ones people gave; clean_name already keeps them to one
    path segment each, and the destination is checked anyway."""
    base = Path(dest).resolve()
    base.mkdir(parents=True, exist_ok=True)
    with state.connect() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM brain_items WHERE deleted_at IS NULL").fetchall()]
    by_id = {r["id"]: r for r in rows}

    def where(r) -> Path:
        parts, cur, seen = [], r, set()
        while cur and cur["id"] not in seen:
            seen.add(cur["id"])
            parts.append(cur["name"])
            cur = by_id.get(cur["parent"])
        p = base.joinpath(*reversed(parts)).resolve()
        if base not in p.parents and p != base:
            raise BrainError(f"{r['name']!r} would leave the export folder")
        return p

    n = 0
    for r in rows:
        p = where(r)
        if r["kind"] == "folder":
            p.mkdir(parents=True, exist_ok=True)
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(_blob(r["sha256"]), p)
            n += 1
        except OSError:
            log.error("brain.export_missing", item=r["id"])
    return n


# ── serving ───────────────────────────────────────────────────────────────────────────────────────────────────────
def serve_headers(r: dict, *, download: bool = False) -> dict:
    """The headers a file is answered with. Shown in the browser only when it is an image, a PDF or plain text, and
    asked to be; everything else is a download. Never sniffed; a shown image or text is sandboxed as well."""
    mime = r.get("mime") or "application/octet-stream"
    show = not download and mime in INLINE
    name = r.get("name") or "file"
    ascii_name = re.sub(r'[^A-Za-z0-9._ -]', "_", name)[:NAME_MAX] or "file"
    h = {"Content-Type": mime if show else "application/octet-stream",
         "Content-Disposition": f"{'inline' if show else 'attachment'}; filename=\"{ascii_name}\"; "
                                f"filename*=UTF-8''{quote(name)}",
         "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
         "Content-Security-Policy": "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"}
    if show and mime == "application/pdf":
        # A browser's own PDF viewer does not run in the box's page, and a sandbox policy stops some from opening it.
        h["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; object-src 'self'"
    return h



def _purge_now_and_then() -> None:
    """THE TRASH EMPTIES ITSELF WITHOUT A JOB OF ITS OWN: at most every PURGE_EVERY, when the brain is opened, so the
    prototype adds nothing to the worker (owner, 2026-10-08: "Nothing invasive please"). Never raises."""
    try:
        from core import box_settings
        last = box_settings.get("brain", "_purged_at", default="") or ""
        if last and (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() < PURGE_EVERY:
            return
        box_settings.put("brain", "_purged_at", _now(), set_by="the trash")
        purge()
    except Exception as e:                           # noqa: BLE001 — a late purge is never a broken screen
        log.warning("brain.purge_failed", error=type(e).__name__)
