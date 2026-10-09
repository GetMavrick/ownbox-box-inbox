"""The Business Brain's screen: the business's own files, on its own Ownbox (owner, 2026-10-08).

Owner, 2026-10-08: "we should definitely have a basic files manager immediately and Should have it listed as Business
Brain in the menu at the top", and on sending a file from it to a client through the share sheet, "it would be nice".
On 10-09, of this screen: "This is gonna be a very important page that we show on our website ... it's gotta borrow
material design elements from top companies like Google", and "Yes, exactly Mobile first ... material three and Google
Drive as design reference."

SO IT IS DRIVE'S SHAPE IN THE BOX'S OWN LOOK. Material 3's parts, as Drive uses them: a search bar on top, two-line rows
with a type icon (folders first), an extended "New" button in thumb reach, a bottom sheet of actions for a file, a
dialog for a name, and a snackbar with Undo after a delete. Every colour, the face and the corners are the box's tokens
(box.css); the type icons borrow Drive's colour per kind from the box's own palette (red PDF, blue document, green
sheet, amber slides, rust picture).

THE SERVER DRAWS EVERY VIEW (a folder, a search, Recently deleted), so the list is there before any script runs and a
row is a real link: a folder opens, a file opens in the browser. The script only acts: the + menu, the file's sheet,
the uploads with their progress, and the calls to `core/dash/brain_api.py` (OSDev4's door, `{"ok", ...}` answers),
after which it reloads the view. The rules are all `core/business_brain.py`'s; nothing here decides what is allowed.

SEND IS THE PHONE'S OWN SHARE SHEET (#2071's spec): the file is fetched when its sheet opens, and the tap calls
`navigator.share` at once, because Safari opens the sheet only from the tap itself. Where a browser cannot share
files, Send downloads it.
"""
from __future__ import annotations

import html as _html
import json
from datetime import datetime, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from flask import request

from core import business_brain as bb
from core import state
from core.dash import blueprint
from core.dash.box_settings import _admit
from core.dash.home import chrome
from core.logging import get_logger

log = get_logger(__name__)

DOOR = "/brain"
# SEND READIES A FILE ONLY THIS BIG AHEAD OF THE TAP (OSDev1's review of #2077): opening a sheet to rename or move a
# 100 MB video must not spend 100 MB of the person's data. Above it, Send downloads instead.
SHARE_AHEAD_MAX = 25 << 20
TITLE = "Shared Brain"            # owner, 2026-10-09: "Change it TO: Shared Brain. Not business brain"
LEDE = "Your business's files, kept on your Ownbox and nowhere else."


def _esc(s) -> str:
    return _html.escape(str(s if s is not None else ""), quote=True)


# ── what a row says ──────────────────────────────────────────────────────────────────────────────────────────────────
# A FILE'S KIND, by its name's ending, for its icon and the word under its name. Drive's colour per kind, each one a
# colour the box already has (box.css), so nothing new enters the palette.
_KINDS = (
    ("pdf", "PDF", (".pdf",)),
    ("doc", "Document", (".doc", ".docx", ".odt", ".rtf", ".pages", ".txt", ".md", ".markdown")),
    ("sheet", "Spreadsheet", (".xls", ".xlsx", ".csv", ".tsv", ".ods", ".numbers")),
    ("slide", "Presentation", (".ppt", ".pptx", ".key", ".odp")),
    ("image", "Picture", (".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif", ".svg", ".bmp", ".tif", ".tiff")),
    ("video", "Video", (".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv")),
    ("audio", "Audio", (".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac")),
    ("zip", "Archive", (".zip", ".rar", ".7z", ".tar", ".gz")),
)


def kind_of(name: str) -> tuple[str, str]:
    """(icon key, the word a person reads) for a file's name."""
    low = str(name or "").lower()
    for key, word, ends in _KINDS:
        if low.endswith(ends):
            return key, word
    return "file", "File"


def size_words(n) -> str:
    """In 1,024s, as the brain counts its limits (100 MB a file is 100 << 20), so a full box reads "5 GB of 5 GB"."""
    n = int(n or 0)
    if n < 1024:
        return f"{n} bytes" if n != 1 else "1 byte"
    for unit, step in (("KB", 1 << 10), ("MB", 1 << 20), ("GB", 1 << 30)):
        if n < step * 1024 or unit == "GB":
            v = n / step
            return f"{v:.1f} {unit}".replace(".0 ", " ") if v < 10 else f"{v:.0f} {unit}"
    return f"{n} bytes"


def _zone():
    try:
        from core import notify
        return ZoneInfo(notify.buyer_timezone())
    except Exception:                                # noqa: BLE001 — a bad zone shows UTC, never a broken page
        return timezone.utc


def when_words(iso: str, now: datetime | None = None) -> str:
    """Drive's way: the time today, the month and day this year, the full date before that."""
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    z = _zone()
    t = t.astimezone(z)
    now = (now or datetime.now(timezone.utc)).astimezone(z)
    if t.date() == now.date():
        return t.strftime("%-I:%M %p")
    if t.year == now.year:
        return t.strftime("%b %-d")
    return t.strftime("%b %-d, %Y")


# ── the glyphs ───────────────────────────────────────────────────────────────────────────────────────────────────────
# STROKE ICONS IN THE BOX'S OWN HAND (1.8 on a 24 grid), shaped after Material's symbols for the same jobs.
_I = {
    "search": "M10.5 4a6.5 6.5 0 1 1 0 13 6.5 6.5 0 0 1 0-13ZM20 20l-4.8-4.8",
    "close": "M6 6l12 12M18 6L6 18",
    "plus": "M12 5v14M5 12h14",
    "more": "M12 5.5v.01M12 12v.01M12 18.5v.01",
    "folder": "M3.5 7a1.5 1.5 0 0 1 1.5-1.5h4.2l2 2.2H19a1.5 1.5 0 0 1 1.5 1.5v8.3A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5Z",
    "file": "M7 3.5h6.5L18 8v11a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 6 19V5a1.5 1.5 0 0 1 1-1.5ZM13.5 3.5V8H18",
    "upload": "M12 15V4.5M7.5 9 12 4.5 16.5 9M5 15v3.5A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5V15",
    "newfolder": "M3.5 7a1.5 1.5 0 0 1 1.5-1.5h4.2l2 2.2H19a1.5 1.5 0 0 1 1.5 1.5v8.3A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5ZM12 10.5v5M9.5 13h5",
    "send": "M12 15V4M8 8l4-4 4 4M6 11H5.5A1.5 1.5 0 0 0 4 12.5v6A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5v-6a1.5 1.5 0 0 0-1.5-1.5H18",
    "download": "M12 4v11M7.5 10.5 12 15l4.5-4.5M5 19.5h14",
    "rename": "M4 20h4L19 9a2.1 2.1 0 0 0-3-3L5 17v3ZM14 8l2 2",
    "move": "M3.5 7a1.5 1.5 0 0 1 1.5-1.5h4.2l2 2.2H19a1.5 1.5 0 0 1 1.5 1.5v8.3A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5ZM9 13h6M12.5 10.5 15 13l-2.5 2.5",
    "delete": "M5 7h14M10 4h4M7 7l.8 12a1.5 1.5 0 0 0 1.5 1.4h5.4a1.5 1.5 0 0 0 1.5-1.4L17 7M10 11v6M14 11v6",
    "restore": "M4.5 12a7.5 7.5 0 1 0 2.2-5.3M4.5 4.5v4h4",
    "trash": "M5 7h14M10 4h4M7 7l.8 12a1.5 1.5 0 0 0 1.5 1.4h5.4a1.5 1.5 0 0 0 1.5-1.4L17 7",
    "open": "M14 4.5h5.5V10M19.5 4.5 11 13M18 14v4.5A1.5 1.5 0 0 1 16.5 20h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10",
    "chev": "M9 6l6 6-6 6",
    "home": "M4 11.5 12 5l8 6.5V19a1 1 0 0 1-1 1h-4.5v-5h-5v5H5a1 1 0 0 1-1-1Z",
}


def icon(name: str, size: int = 24) -> str:
    return (f'<svg class="bb-g" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            f'<path d="{_I[name]}"/></svg>')


def type_tile(item: dict) -> str:
    """The row's leading icon: a folder, or the file's kind in its colour, on a tint of that colour."""
    if item.get("kind") == "folder":
        return f'<span class="bb-ic t-folder">{icon("folder")}</span>'
    key, _ = kind_of(item.get("name", ""))
    label = {"pdf": "PDF", "doc": "DOC", "sheet": "XLS", "slide": "PPT", "zip": "ZIP"}.get(key)
    inner = (f'<span class="bb-ext">{label}</span>' if label else icon({"image": "file", "video": "file",
                                                                         "audio": "file"}.get(key, "file")))
    if key == "image":
        inner = ('<svg class="bb-g" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                 'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
                 '<path d="M5.5 4.5h13a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-13a1 1 0 0 1-1-1v-13a1 1 0 0 1 1-1Z'
                 'M4.5 16l4.5-4.5 4 4 2.5-2.5 4 4M15 9.5v.01"/></svg>')
    elif key == "video":
        inner = ('<svg class="bb-g" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                 'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
                 '<path d="M4.5 6.5h11a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1Z'
                 'M16.5 10.5l4-2.5v8l-4-2.5"/></svg>')
    elif key == "audio":
        inner = ('<svg class="bb-g" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                 'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
                 '<path d="M9 17.5V6l10-2v11.5M9 17.5a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0Z'
                 'M19 15.5a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0Z"/></svg>')
    return f'<span class="bb-ic t-{key}">{inner}</span>'


def _file_data(item: dict, path: str = "") -> str:
    key, word = kind_of(item.get("name", ""))
    return _esc(json.dumps({"id": item["id"], "kind": item["kind"], "name": item["name"], "size": item.get("size", 0),
                            "mime": item.get("mime", ""), "type": key, "word": word, "parent": item.get("parent", ""),
                            "sizeWords": size_words(item.get("size", 0)), "when": when_words(item.get("changed_at")),
                            "path": path}))


def row(item: dict, *, path: str = "", counts: dict | None = None) -> str:
    """One two-line row: the icon, the name, what it is and when it changed; its ⋮ opens the same sheet a tap does."""
    folder = item.get("kind") == "folder"
    when = when_words(item.get("changed_at"))
    if folder:
        n = (counts or {}).get(item["id"], 0)
        sub = f"{n} item{'s' if n != 1 else ''}" if n else "Empty"
        href = f"{DOOR}?{urlencode({'folder': item['id']})}"
    else:
        sub = f"{kind_of(item['name'])[1]} · {size_words(item.get('size'))}"
        href = f"{DOOR}/file/{_esc(item['id'])}"
    # THE FOLDER IT IS IN, never its own name again: a search's path ends with the item itself, and a name can't hold
    # a "/" (business_brain.clean_name), so everything before the last one is where it lives. At the top it says nothing.
    where = path.rsplit("/", 1)[0] if "/" in path else ""
    if where:
        sub = f"In {where}"
    sub = f"{sub} · {when}" if when else sub
    return (f'<li class="bb-row" data-item="{_file_data(item, path)}">'
            f'<a class="bb-main" href="{_esc(href)}"{"" if folder else " data-open"}>{type_tile(item)}'
            f'<span class="bb-txt"><span class="bb-name">{_esc(item["name"])}</span>'
            f'<span class="bb-sub">{_esc(sub)}</span></span></a>'
            f'<button type="button" class="bb-more" data-more aria-label="More for {_esc(item["name"])}">'
            f'{icon("more")}</button></li>')


def trash_row(item: dict) -> str:
    left = int(item.get("days_left", 0))
    sub = (f"{'Folder' if item['kind'] == 'folder' else kind_of(item['name'])[1]} · deleted "
           f"{when_words(item.get('deleted_at'))} · {left} day{'s' if left != 1 else ''} left")
    return (f'<li class="bb-row" data-item="{_file_data(item)}"><div class="bb-main">{type_tile(item)}'
            f'<span class="bb-txt"><span class="bb-name">{_esc(item["name"])}</span>'
            f'<span class="bb-sub">{_esc(sub)}</span></span></div>'
            f'<button type="button" class="bb-text-btn" data-restore>Restore</button></li>')


# ── what the view needs from the store ───────────────────────────────────────────────────────────────────────────────
def _folders() -> list[dict]:
    """Every live folder with its whole path, for Move: one read, paths built here."""
    with state.connect() as c:
        rows = [dict(r) for r in c.execute("SELECT id, name, parent FROM brain_items WHERE kind = 'folder' AND "
                                           "deleted_at IS NULL").fetchall()]
    by = {r["id"]: r for r in rows}

    def path(r, seen=()):
        up = by.get(r["parent"])
        return (path(up, seen + (r["id"],)) + "/" if up and up["id"] not in seen else "") + r["name"]
    return sorted(({"id": r["id"], "path": path(r)} for r in rows), key=lambda x: x["path"].lower())


def _counts(ids: list[str]) -> dict:
    if not ids:
        return {}
    with state.connect() as c:
        q = ",".join("?" * len(ids))
        return {r["parent"]: int(r["n"]) for r in c.execute(
            f"SELECT parent, COUNT(*) AS n FROM brain_items WHERE parent IN ({q}) AND deleted_at IS NULL "
            "GROUP BY parent", ids).fetchall()}


# ── the page ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def _search_bar(q: str, folder: str) -> str:
    return (f'<form class="bb-search" role="search" method="get" action="{DOOR}">'
            f'<span class="bb-search-ic">{icon("search")}</span>'
            f'<input type="search" name="q" value="{_esc(q)}" placeholder="Search your files" '
            'aria-label="Search your files" autocomplete="off" enterkeyhint="search">'
            + (f'<a class="bb-icon-btn" href="{DOOR}" aria-label="Clear the search">{icon("close")}</a>' if q else "")
            + '</form>')


def _path(crumbs: list[dict], *, extra: str = "") -> str:
    parts = [f'<a href="{DOOR}">{TITLE}</a>']
    for i, c in enumerate(crumbs):
        last = i == len(crumbs) - 1 and not extra
        parts.append(f'<span aria-current="page">{_esc(c["name"])}</span>' if last
                     else f'<a href="{DOOR}?{urlencode({"folder": c["id"]})}">{_esc(c["name"])}</a>')
    if extra:
        parts.append(f'<span aria-current="page">{_esc(extra)}</span>')
    if len(parts) == 1:
        return ""
    return ('<nav class="bb-path" aria-label="Where you are">'
            + f'<span class="bb-sep">{icon("chev", 16)}</span>'.join(parts) + '</nav>')


def _empty(title: str, sub: str) -> str:
    return (f'<div class="bb-empty"><span class="bb-empty-ic">{icon("folder", 40)}</span>'
            f'<p class="bb-empty-t">{_esc(title)}</p><p class="bb-empty-s">{_esc(sub)}</p></div>')


def _usage_line(u: dict) -> str:
    return (f'<p class="bb-usage">{u["files"]} file{"s" if u["files"] != 1 else ""} · '
            f'{_esc(size_words(u["bytes"]))} of {_esc(size_words(u["max"]))} used'
            f'<a class="bb-link" href="{DOOR}?view=trash">{icon("trash", 18)}Recently deleted</a></p>')


def _sheets(folder: str, folders: list[dict]) -> str:
    """The New menu, the file's sheet and the small dialogs, drawn once and filled by the script."""
    max_mb = bb.FILE_MAX >> 20
    opts = "".join(f'<option value="{_esc(f["id"])}">{_esc(f["path"])}</option>' for f in folders)
    act = lambda key, ic, label, extra="": (f'<button type="button" class="bb-act" data-act="{key}"{extra}>'  # noqa: E731
                                            f'{icon(ic)}<span>{label}</span></button>')
    return (
        # NEW: Drive's FAB menu (its 2025 Material 3 redesign): the choices stand above the button, which turns into
        # the way to close them.
        f'<dialog class="bb-fabmenu" id="bb-new" aria-label="Add to {TITLE}"><div class="bb-fm-items">'
        f'<label class="bb-fm-item" for="bb-files">{icon("upload")}<span>Add files<small>Up to {max_mb} MB each'
        f'</small></span></label>'
        f'<input type="file" id="bb-files" class="bb-file-in" multiple tabindex="-1">'
        f'<button type="button" class="bb-fm-item" data-act="newfolder">{icon("newfolder")}<span>New folder</span>'
        f'</button></div><button type="button" class="bb-fm-close" data-cancel aria-label="Close">{icon("close")}'
        f'</button></dialog>'
        # THE FILE'S SHEET: what it is, a look at it, then what can be done with it, Delete last and apart.
        '<dialog class="bb-sheet" id="bb-item" aria-labelledby="bb-item-t"><div class="bb-handle"></div>'
        '<div class="bb-item-head"><span data-tile></span><span class="bb-txt"><span class="bb-name" id="bb-item-t">'
        '</span><span class="bb-sub" data-sub></span></span></div>'
        '<div class="bb-preview" data-preview></div>'
        # THREE PILLS ON TOP, as Drive's sheet has, for what a file is for: send it, keep a copy, read it.
        '<div class="bb-quick" data-file-only>'
        + "".join(f'<button type="button" class="bb-pill" data-act="{k}">{icon(ic)}<span>{label}</span></button>'
                  for k, ic, label in (("send", "send", "Send"), ("download", "download", "Download"),
                                       ("open", "open", "Open")))
        + f'</div><div class="bb-acts">{act("rename", "rename", "Rename")}{act("move", "move", "Move")}'
        f'<hr class="bb-div">{act("delete", "delete", "Delete", " data-danger")}</div></dialog>'
        # A NAME: a new folder, or a rename.
        '<dialog class="bb-dialog" id="bb-name" aria-labelledby="bb-name-t"><form method="dialog" data-name-form>'
        '<h2 class="bb-dialog-t" id="bb-name-t">New folder</h2>'
        '<label class="bb-field"><span>Name</span><input name="name" required maxlength="120" autocomplete="off" '
        'enterkeyhint="done"></label><p class="bb-err" data-err hidden></p>'
        '<div class="bb-dialog-acts"><button type="button" class="bb-text-btn" data-cancel>Cancel</button>'
        '<button type="submit" class="bb-text-btn bb-strong" data-ok>Create</button></div></form></dialog>'
        # MOVE: to the top, or to any folder.
        '<dialog class="bb-dialog" id="bb-move" aria-labelledby="bb-move-t"><form method="dialog" data-move-form>'
        '<h2 class="bb-dialog-t" id="bb-move-t">Move to</h2>'
        f'<label class="bb-field"><span>Folder</span><select name="parent"><option value="">{TITLE} (the top)</option>'
        f'{opts}</select></label><p class="bb-err" data-err hidden></p>'
        '<div class="bb-dialog-acts"><button type="button" class="bb-text-btn" data-cancel>Cancel</button>'
        '<button type="submit" class="bb-text-btn bb-strong">Move</button></div></form></dialog>'
        # DELETE ASKS FIRST, and says it can be put back.
        '<dialog class="bb-dialog" id="bb-del" aria-labelledby="bb-del-t"><form method="dialog" data-del-form>'
        '<h2 class="bb-dialog-t" id="bb-del-t">Move to the trash?</h2>'
        f'<p class="bb-dialog-s" data-del-s>It stays in Recently deleted for {bb.TRASH_DAYS} days, '
        'and can be put back until then.</p><p class="bb-err" data-err hidden></p>'
        '<div class="bb-dialog-acts"><button type="button" class="bb-text-btn" data-cancel>Cancel</button>'
        '<button type="submit" class="bb-text-btn bb-danger">Move to trash</button></div></form></dialog>'
        # UPLOADS: one row per file, its bar, and its refusal in words.
        '<dialog class="bb-sheet" id="bb-up" aria-labelledby="bb-up-t"><div class="bb-handle"></div>'
        '<h2 class="bb-sheet-t" id="bb-up-t">Adding files</h2><div class="bb-bar"><span data-bar></span></div>'
        '<ul class="bb-up-list" data-up-list></ul>'
        '<div class="bb-dialog-acts"><button type="button" class="bb-text-btn bb-strong" data-done hidden>Done'
        '</button></div></dialog>'
        '<div class="bb-snack" role="status" aria-live="polite" hidden><span data-snack-t></span>'
        '<button type="button" class="bb-snack-act" data-snack-act hidden>Undo</button></div>')


@blueprint.route(DOOR)
def brain_screen():
    refuse = _admit(owner_only=False)
    if refuse is not None:
        return refuse
    folder = request.args.get("folder", "")
    q = request.args.get("q", "").strip()
    view = request.args.get("view", "")
    note = ""
    if view == "trash":
        items = bb.trash()
        body = (_path([], extra="Recently deleted")
                + f'<p class="bb-note">Deleted files stay here for {bb.TRASH_DAYS} days, then are removed for good.</p>'
                + (f'<ul class="bb-list" data-view="trash">{"".join(trash_row(i) for i in items)}</ul>' if items
                   else _empty("Nothing deleted", f"What you delete waits here for {bb.TRASH_DAYS} days.")))
        here = ""
    elif q:
        items = bb.search(q)
        counts = _counts([i["id"] for i in items if i["kind"] == "folder"])
        body = (f'<h2 class="bb-h">{len(items)} result{"s" if len(items) != 1 else ""} for “{_esc(q)}”</h2>'
                + (f'<ul class="bb-list">{"".join(row(i, path=i.get("path", ""), counts=counts) for i in items)}</ul>'
                   if items else _empty("Nothing found", "Try another word, or part of a file's name.")))
        here = ""
    else:
        try:
            got = bb.listing(folder)
        except bb.BrainError as e:
            note, folder, got = str(e), "", bb.listing("")
        items = got["items"]
        counts = _counts([i["id"] for i in items if i["kind"] == "folder"])
        body = (_path(got["path"])
                + (f'<p class="bb-note" role="alert">{_esc(note)}</p>' if note else "")
                + (f'<ul class="bb-list">{"".join(row(i, counts=counts) for i in items)}</ul>' if items
                   else _empty("Nothing here yet" if not folder else "This folder is empty",
                               "Tap + to add files or a folder." if not folder else
                               "Tap + to add files here.")))
        here = folder
    page = (f'<style>{_CSS}</style><div class="bb" data-folder="{_esc(here)}" data-max="{bb.FILE_MAX}" '
            f'data-share-max="{SHARE_AHEAD_MAX}">'
            + _search_bar(q, here) + body + _usage_line(bb.usage())
            # A FAB, NOT AN EXTENDED ONE: Material 3 opens a FAB menu only from a FAB (and #2071's spec asks for a +).
            + (f'<button type="button" class="bb-fab" data-new aria-haspopup="dialog" aria-label="New: add files or a '
               f'folder" title="New">{icon("plus", 28)}</button>' if view != "trash" else "")
            + _sheets(here, _folders()) + f'<script>{_JS}</script></div>')
    return chrome(DOOR, title=TITLE, lede=LEDE, body=page)


# ── the look ─────────────────────────────────────────────────────────────────────────────────────────────────────────
# MATERIAL 3'S MEASUREMENTS, THE BOX'S TOKENS. Sizes run through --px so an iPhone's Dynamic Type still scales them.
_CSS = r"""
.bb{--bb-ease:cubic-bezier(.2,0,0,1);--bb-r:28px;position:relative;padding-bottom:112px}
.bb button{margin:0}.bb button:hover{opacity:1}
/* HIDDEN MEANS GONE, whatever display a rule below gives the element (the snackbar, a folder's file-only actions). */
.bb [hidden]{display:none!important}
.bb-g{display:block;flex:none}
.bb-search{display:flex;align-items:center;gap:4px;height:56px;padding:0 4px 0 16px;margin:0 0 12px;
background:var(--card);border-radius:28px;box-shadow:0 0 0 1px var(--card-edge)}
.bb-search:focus-within{box-shadow:0 0 0 2px var(--ink)}
.bb-search-ic{color:var(--ink-3);display:flex}
.bb-search input{flex:1;min-width:0;height:100%;border:0;background:none;color:var(--ink);font:inherit;
font-size:16px;padding:0 8px;outline:none;-webkit-appearance:none;appearance:none}
.bb-search input::-webkit-search-cancel-button{display:none}
.bb-icon-btn{display:flex;align-items:center;justify-content:center;width:48px;height:48px;border-radius:50%;
color:var(--ink-2);position:relative}
.bb-path{display:flex;flex-wrap:wrap;align-items:center;gap:2px;margin:0 0 8px;font-size:calc(15 * var(--px, 1px));
color:var(--ink-3)}
.bb-path a{color:var(--ink-2);padding:6px 4px;border-radius:8px}
.bb-path a:hover{background:var(--wash)}
.bb-path [aria-current]{color:var(--ink);font-weight:600;padding:6px 4px}
.bb-sep{display:flex;color:var(--ink-3)}
.bb-h{font-size:calc(15 * var(--px, 1px));font-weight:600;color:var(--ink-2);margin:4px 4px 8px}
.bb-note{margin:0 4px 12px;color:var(--ink-3);font-size:calc(15 * var(--px, 1px))}
.bb-list{list-style:none;margin:0;padding:4px 0;background:var(--card);border-radius:var(--r-lg, 16px);
box-shadow:0 0 0 1px var(--card-edge)}
.bb-row{display:flex;align-items:center;min-height:72px;padding-right:4px;position:relative}
.bb-row+.bb-row .bb-main::before{content:"";position:absolute;left:72px;right:0;top:0;border-top:1px solid var(--hairline)}
.bb-main{flex:1;min-width:0;display:flex;align-items:center;gap:16px;padding:8px 8px 8px 16px;min-height:72px;
position:relative;color:inherit;text-decoration:none;border-radius:12px;-webkit-tap-highlight-color:transparent}
a.bb-main::after,.bb-more::after,.bb-icon-btn::after,.bb-act::after,.bb-fab::after,.bb-text-btn::after{content:"";
position:absolute;inset:0;border-radius:inherit;background:currentColor;opacity:0;transition:opacity .15s linear;
pointer-events:none}
a.bb-main:hover::after,.bb-more:hover::after,.bb-icon-btn:hover::after,.bb-act:hover::after,.bb-fab:hover::after,
.bb-text-btn:hover::after{opacity:.08}
a.bb-main:active::after,.bb-more:active::after,.bb-icon-btn:active::after,.bb-act:active::after,.bb-fab:active::after,
.bb-text-btn:active::after{opacity:.1}
.bb-ic{position:relative;flex:none;width:40px;height:40px;border-radius:12px;display:flex;align-items:center;
justify-content:center}
.bb-ic::before{content:"";position:absolute;inset:0;border-radius:inherit;background:currentColor;opacity:.12}
.bb-ext{position:relative;font-size:11px;font-weight:800;letter-spacing:.02em}
.t-folder{color:var(--ink-2)}.t-pdf{color:var(--bad)}.t-doc{color:var(--blue)}.t-sheet{color:var(--ok)}
.t-slide{color:var(--warn)}.t-image{color:var(--link)}.t-video{color:var(--bad)}.t-audio{color:var(--blue)}
.t-zip,.t-file{color:var(--ink-3)}
.bb-txt{min-width:0;display:flex;flex-direction:column}
.bb-name{font-size:calc(16 * var(--px, 1px));line-height:1.5;font-weight:400;color:var(--ink);white-space:nowrap;
overflow:hidden;text-overflow:ellipsis}
.bb-sub{font-size:calc(14 * var(--px, 1px));line-height:1.43;color:var(--ink-3);white-space:nowrap;overflow:hidden;
text-overflow:ellipsis}
.bb-more{position:relative;flex:none;width:48px;height:48px;border:0;border-radius:50%;background:none;padding:0;
color:var(--ink-3);display:flex;align-items:center;justify-content:center;min-height:0;cursor:pointer}
.bb-more .bb-g{width:24px;height:24px;stroke-width:2.6}
.bb-text-btn{position:relative;border:0;background:none;color:var(--ink);font:inherit;font-size:calc(15 * var(--px, 1px));
font-weight:600;min-height:40px;height:40px;padding:0 14px;border-radius:20px;cursor:pointer;width:auto;flex:none}
.bb-strong{color:var(--ink)}.bb-danger{color:var(--bad)}
.bb-empty{display:flex;flex-direction:column;align-items:center;text-align:center;padding:48px 24px;
background:var(--card);border-radius:var(--r-lg, 16px);box-shadow:0 0 0 1px var(--card-edge)}
.bb-empty-ic{color:var(--ink-3);margin-bottom:12px}
.bb-empty-t{margin:0;font-size:calc(18 * var(--px, 1px));font-weight:600;color:var(--ink)}
.bb-empty-s{margin:4px 0 0;color:var(--ink-3);font-size:calc(15 * var(--px, 1px))}
.bb-usage{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px;margin:12px 4px 0;
color:var(--ink-3);font-size:calc(14 * var(--px, 1px))}
.bb-link{display:inline-flex;align-items:center;gap:6px;color:var(--ink-2);font-weight:600;padding:8px 4px}
/* THE FAB: Material 3's medium FAB on a mobile (80, corners 20, icon 28), the 56 FAB (corners 16) on a desktop. */
.bb-fab{position:fixed;right:max(16px,env(safe-area-inset-right));bottom:max(16px,env(safe-area-inset-bottom));z-index:30;
display:flex;align-items:center;justify-content:center;width:80px;height:80px;min-height:0;padding:0;border:0;
border-radius:20px;background:var(--ink);color:var(--on-ink);cursor:pointer;
box-shadow:0 1px 3px rgba(0,0,0,.18),0 4px 8px 3px rgba(0,0,0,.12)}
@media (min-width:821px){.bb-fab{right:32px;bottom:32px;width:56px;height:56px;border-radius:16px}}
.bb dialog{color:var(--ink);background:var(--card);border:0;padding:0;max-width:none}
.bb dialog.bb-sheet{max-width:640px}
.bb dialog.bb-fabmenu{position:fixed;inset:auto max(16px,env(safe-area-inset-right)) max(16px,env(safe-area-inset-bottom)) auto;
margin:0;background:none;overflow:visible;display:none;flex-direction:column;align-items:flex-end;gap:8px}
.bb dialog.bb-fabmenu[open]{display:flex}
.bb dialog.bb-fabmenu::backdrop{background:rgba(0,0,0,.32)}
@media (min-width:821px){.bb dialog.bb-fabmenu{inset:auto 32px 32px auto}}
/* FAB MENU (Material 3): items 56 high, fully rounded, 24 in from each side, icon to label 8, 4 apart, no shadow;
   the close is a 56 circle 8 below them, where the FAB was. */
.bb-fm-items{display:flex;flex-direction:column;align-items:flex-end;gap:4px}
.bb-fm-item{position:relative;display:flex;align-items:center;gap:8px;min-height:56px;height:auto;width:auto;
padding:6px 24px;border:0;border-radius:28px;background:var(--card);color:var(--ink);font:inherit;
font-size:calc(16 * var(--px, 1px));line-height:1.5;font-weight:500;text-align:left;cursor:pointer}
.bb-fm-item small{display:block;font-size:calc(13 * var(--px, 1px));font-weight:400;color:var(--ink-3)}
.bb-fabmenu[open] .bb-fm-item{animation:bb-rise .25s var(--bb-ease) both}
.bb-fabmenu[open] .bb-fm-item+.bb-fm-item{animation-delay:.04s}
@keyframes bb-rise{from{opacity:0;transform:translateY(12px) scale(.96)}to{opacity:1;transform:none}}
.bb-fm-close{position:relative;width:56px;height:56px;min-height:56px;border:0;border-radius:50%;padding:0;
display:flex;align-items:center;justify-content:center;background:var(--ink);color:var(--on-ink);cursor:pointer;
box-shadow:0 1px 3px rgba(0,0,0,.18),0 4px 12px rgba(0,0,0,.16)}
.bb-quick{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;padding:4px 16px 12px}
.bb-pill{position:relative;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;
min-height:72px;height:auto;width:100%;padding:12px 8px;border:0;border-radius:20px;background:var(--wash);
color:var(--ink);font:inherit;font-size:calc(14 * var(--px, 1px));font-weight:600;cursor:pointer}
.bb-pill .bb-g{color:var(--ink)}
.bb-pill[disabled]{opacity:.55;cursor:default}
.bb-pill::after,.bb-fm-item::after,.bb-fm-close::after{content:"";position:absolute;inset:0;border-radius:inherit;
background:currentColor;opacity:0;transition:opacity .15s linear;pointer-events:none}
.bb-pill:hover::after,.bb-fm-item:hover::after,.bb-fm-close:hover::after{opacity:.08}
.bb-pill:active::after,.bb-fm-item:active::after,.bb-fm-close:active::after{opacity:.1}
.bb dialog::backdrop{background:rgba(0,0,0,.32)}
.bb-sheet{position:fixed;inset:auto 0 0 0;margin:0 auto;width:100%;max-width:640px;max-height:calc(100vh - 72px);overflow:auto;
border-radius:var(--bb-r) var(--bb-r) 0 0;padding:0 0 max(16px,env(safe-area-inset-bottom))}
.bb-sheet[open]{animation:bb-up .4s cubic-bezier(.05,.7,.1,1)}
@keyframes bb-up{from{transform:translateY(100%)}to{transform:none}}
.bb-handle{width:32px;height:4px;border-radius:2px;background:var(--ink-3);opacity:.4;margin:22px auto 14px}
.bb-sheet-t{font-size:calc(16 * var(--px, 1px));font-weight:600;margin:0;padding:8px 24px 8px}
.bb-act{position:relative;display:flex;align-items:center;gap:16px;width:100%;min-height:56px;padding:0 24px;border:0;
background:none;color:var(--ink);font:inherit;font-size:calc(16 * var(--px, 1px));font-weight:500;text-align:left;
border-radius:0;cursor:pointer;height:auto}
.bb-act{justify-content:flex-start}
/* THE FILE INPUT IS THERE BUT UNSEEN, so its label opens it on every browser, an iPhone's included. */
.bb-file-in{position:absolute!important;width:1px!important;height:1px!important;opacity:0;overflow:hidden;
clip:rect(0 0 0 0);pointer-events:none}
.bb-act small{display:block;font-size:calc(13 * var(--px, 1px));font-weight:400;color:var(--ink-3)}
.bb-act .bb-g{color:var(--ink-2)}
.bb-act[data-danger],.bb-act[data-danger] .bb-g{color:var(--bad)}
.bb-act[disabled]{opacity:.5;cursor:default}
.bb-div{border:0;border-top:1px solid var(--hairline);margin:8px 0}
.bb-item-head{display:flex;align-items:center;gap:16px;padding:4px 24px 12px}
.bb-item-head .bb-name{white-space:normal;overflow-wrap:anywhere}
.bb-preview{margin:0 16px 8px;border-radius:12px;overflow:hidden;background:var(--wash)}
.bb-preview:empty{display:none}
.bb-preview img{display:block;width:100%;max-height:40vh;object-fit:contain;background:var(--wash)}
.bb-text{margin:0;padding:16px;max-height:40vh;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;
font-family:var(--mono);font-size:calc(14 * var(--px, 1px));line-height:1.5;color:var(--ink-2);background:var(--card)}
.bb-card-prev{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;padding:28px 16px;
color:var(--ink-3);font-size:calc(14 * var(--px, 1px))}
.bb-card-prev .bb-ic{width:72px;height:72px;border-radius:20px}
.bb-card-prev .bb-ext{font-size:16px}.bb-card-prev .bb-g{width:36px;height:36px}
.bb-preview video,.bb-preview audio{display:block;width:100%;max-height:40vh}
.bb-dialog{width:min(560px,calc(100% - 48px));border-radius:var(--bb-r);padding:24px!important;margin:auto}
.bb-dialog[open]{animation:bb-in .3s cubic-bezier(.05,.7,.1,1)}
@keyframes bb-in{from{opacity:0;transform:scale(.96)}to{opacity:1;transform:none}}
.bb-dialog-t{font-size:calc(22 * var(--px, 1px));font-weight:600;margin:0 0 16px;line-height:1.3}
.bb-dialog-s{margin:0 0 8px;color:var(--ink-2);font-size:calc(15 * var(--px, 1px))}
.bb-field{display:flex;flex-direction:column;gap:6px;font-size:calc(14 * var(--px, 1px));color:var(--ink-3)}
.bb-field input,.bb-field select{font:inherit;font-size:16px;color:var(--ink);height:52px;padding:0 14px;border-radius:12px;
border:1px solid var(--line);background:var(--card);width:100%}
.bb-field input:focus,.bb-field select:focus{outline:2px solid var(--ink);outline-offset:-1px}
.bb-err{color:var(--bad);margin:8px 0 0;font-size:calc(14 * var(--px, 1px))}
.bb-dialog-acts{display:flex;justify-content:flex-end;gap:8px;margin-top:24px;padding:0 16px}
.bb-dialog .bb-dialog-acts{padding:0}
.bb-bar{height:4px;margin:4px 24px 12px;border-radius:2px;background:var(--wash);overflow:hidden}
.bb-bar span{display:block;height:100%;width:0;background:var(--ink);transition:width .2s linear}
.bb-up-list{list-style:none;margin:0;padding:0 24px;font-size:calc(15 * var(--px, 1px))}
.bb-up-list li{display:flex;flex-direction:column;padding:8px 0;border-top:1px solid var(--hairline)}
.bb-up-list .bb-up-err{color:var(--bad);font-size:calc(14 * var(--px, 1px))}
.bb-up-list .bb-up-ok{color:var(--ok);font-size:calc(14 * var(--px, 1px))}
.bb-snack{position:fixed;left:16px;right:16px;bottom:calc(max(16px,env(safe-area-inset-bottom)) + 96px);z-index:40;
max-width:560px;margin:0 auto;min-height:48px;display:flex;align-items:center;gap:8px;padding:6px 8px 6px 16px;
border-radius:4px;background:var(--ink);color:var(--on-ink);font-size:calc(14 * var(--px, 1px));line-height:1.43;
box-shadow:0 3px 8px rgba(0,0,0,.2)}
.bb-snack span{flex:1}
.bb-snack-act{border:0;background:none;color:var(--on-ink);font:inherit;font-weight:700;min-height:40px;height:40px;
padding:0 12px;border-radius:20px;cursor:pointer;width:auto;font-size:calc(14 * var(--px, 1px));font-weight:700}
.bb a:focus-visible,.bb button:focus-visible,.bb label:focus-visible{outline:3px solid var(--ink);outline-offset:2px}
@media (prefers-reduced-motion:reduce){.bb-sheet[open],.bb-dialog[open],.bb-fabmenu[open] .bb-fm-item{animation:none}}
"""

_JS = r"""(function(){
var root=document.querySelector('.bb');if(!root)return;
var here=root.getAttribute('data-folder')||'',MAX=+root.getAttribute('data-max'),SHAREMAX=+root.getAttribute('data-share-max');
var $=function(s,el){return (el||root).querySelector(s)};
var sheetNew=$('#bb-new'),sheetItem=$('#bb-item'),dlgName=$('#bb-name'),dlgMove=$('#bb-move'),dlgDel=$('#bb-del'),
    sheetUp=$('#bb-up'),snack=$('.bb-snack'),cur=null,shareFile=null,snackTimer=0;

function open(d){if(d.showModal)d.showModal();else d.setAttribute('open','')}
function shut(d){if(d.open){if(d.close)d.close();else d.removeAttribute('open')}}
root.querySelectorAll('dialog').forEach(function(d){
  d.addEventListener('click',function(e){if(e.target===d)shut(d)});            // a tap on the scrim closes it
  d.querySelectorAll('[data-cancel]').forEach(function(b){b.addEventListener('click',function(){shut(d)})});
});
function say(text,undo){
  snack.querySelector('[data-snack-t]').textContent=text;var a=snack.querySelector('[data-snack-act]');
  a.hidden=!undo;a.onclick=undo?function(){snack.hidden=true;undo()}:null;snack.hidden=false;
  clearTimeout(snackTimer);snackTimer=setTimeout(function(){snack.hidden=true},undo?10000:4000);
}
function later(text){try{sessionStorage.setItem('bb-say',text)}catch(e){}}
try{var was=sessionStorage.getItem('bb-say');if(was){sessionStorage.removeItem('bb-say');say(was)}}catch(e){}
function post(path,data){
  var f=new FormData();for(var k in data)f.append(k,data[k]);
  return fetch('/brain/api/'+path,{method:'POST',body:f,credentials:'same-origin'})
    .then(function(r){return r.json().catch(function(){return {ok:false,error:'Something went wrong. Try again.'}})})
    .catch(function(){return {ok:false,error:'Your Ownbox could not be reached. Try again.'}});
}
function err(d,text){var p=d.querySelector('[data-err]');p.textContent=text||'';p.hidden=!text}

// NEW: the bottom sheet with Add files and New folder.
// THE BUTTON BECOMES THE MENU'S CLOSE: hidden while its choices stand above the close in its place.
var fab=$('[data-new]');if(fab)fab.addEventListener('click',function(){fab.style.visibility='hidden';open(sheetNew)});
sheetNew.addEventListener('close',function(){if(fab)fab.style.visibility=''});
var nameMode='folder';
$('[data-act="newfolder"]',sheetNew).addEventListener('click',function(){
  shut(sheetNew);nameMode='folder';$('#bb-name-t').textContent='New folder';$('[data-ok]',dlgName).textContent='Create';
  var i=dlgName.querySelector('input');i.value='';err(dlgName,'');open(dlgName);setTimeout(function(){i.focus()},50);
});
dlgName.querySelector('form').addEventListener('submit',function(e){
  e.preventDefault();var name=dlgName.querySelector('input').value.trim();if(!name)return;
  var go=nameMode==='folder'?post('folder',{parent:here,name:name}):post('rename',{id:cur.id,name:name});
  go.then(function(r){if(!r.ok)return err(dlgName,r.error);
    later(nameMode==='folder'?'Folder created':'Renamed');location.reload()});
});

// ADD FILES: each one sent with the others, one bar for the lot, and every refusal in its own words.
$('#bb-files').addEventListener('change',function(){
  var files=[].slice.call(this.files||[]);this.value='';if(!files.length)return;shut(sheetNew);
  var list=$('[data-up-list]',sheetUp),bar=$('[data-bar]',sheetUp),done=$('[data-done]',sheetUp);
  list.innerHTML='';bar.style.width='0';done.hidden=true;$('#bb-up-t').textContent='Adding '+files.length+' file'+(files.length>1?'s':'');
  var send=[],refusedHere=[];
  files.forEach(function(f){
    var li=document.createElement('li');li.innerHTML='<span></span><span class="bb-up-note"></span>';
    li.firstChild.textContent=f.name;list.appendChild(li);
    if(f.size>MAX){li.lastChild.className='bb-up-err';li.lastChild.textContent='Too big: files can be up to '+Math.round(MAX/1048576)+' MB.';refusedHere.push(f)}
    else send.push({f:f,li:li});
  });
  open(sheetUp);
  if(!send.length){done.hidden=false;return}
  var fd=new FormData();fd.append('folder',here);send.slice(0,10).forEach(function(s){fd.append('file',s.f,s.f.name)});
  send.slice(10).forEach(function(s){s.li.lastChild.className='bb-up-err';s.li.lastChild.textContent='Add at most 10 files at once.'});
  var x=new XMLHttpRequest();x.open('POST','/brain/api/upload');
  x.upload.onprogress=function(e){if(e.lengthComputable)bar.style.width=Math.round(e.loaded/e.total*100)+'%'};
  x.onload=function(){
    var r={};try{r=JSON.parse(x.responseText)}catch(e){r={ok:false,error:'Something went wrong. Try again.'}}
    bar.style.width='100%';var saved=(r.saved||[]).length,bad={};(r.refused||[]).forEach(function(z){bad[z.name]=z.error});
    send.slice(0,10).forEach(function(s){var n=s.li.lastChild;
      if(bad[s.f.name]){n.className='bb-up-err';n.textContent=bad[s.f.name]}
      else if(r.saved){n.className='bb-up-ok';n.textContent='Added'}
      else{n.className='bb-up-err';n.textContent=r.error||'Not added.'}});
    if(saved&&!refusedHere.length&&!(r.refused||[]).length&&send.length<=10){later('Added '+saved+' file'+(saved>1?'s':''));location.reload();return}
    done.hidden=false;done.onclick=function(){location.reload()};
  };
  x.onerror=function(){send.forEach(function(s){s.li.lastChild.className='bb-up-err';s.li.lastChild.textContent='Your Ownbox could not be reached.'});done.hidden=false};
  x.send(fd);
});

// A FILE'S SHEET: a tap on a file (or ⋮ on anything) opens it; Send has the file ready before the tap.
function itemOf(el){var li=el.closest('.bb-row');return li?JSON.parse(li.getAttribute('data-item')):null}
function tileOf(el){var li=el.closest('.bb-row');return li?li.querySelector('.bb-ic').outerHTML:''}
function showItem(it,tile){
  cur=it;shareFile=null;var file=it.kind==='file';
  $('#bb-item-t').textContent=it.name;$('[data-sub]',sheetItem).textContent=file?(it.word+' · '+it.sizeWords+(it.when?' · '+it.when:'')):'Folder';
  $('[data-tile]',sheetItem).innerHTML=tile;
  sheetItem.querySelectorAll('[data-file-only]').forEach(function(b){b.hidden=!file});
  var pv=$('[data-preview]',sheetItem),src='/brain/file/'+encodeURIComponent(it.id);pv.innerHTML='';
  if(file){
    // A LOOK THAT WORKS ON EVERY PHONE: a picture, the words of a text document, a video that plays; a PDF and the
    // rest get a card, and Open reads them in the phone's own viewer (Android's browser draws no PDF inside a page).
    if(it.type==='image'&&/^image\/(png|jpeg|gif|webp)$/.test(it.mime)){var im=new Image();im.alt=it.name;im.src=src;pv.appendChild(im)}
    else if(/\.(txt|md|markdown|csv|tsv|json|log)$/i.test(it.name)&&it.size<=200000){
      var pre=document.createElement('pre');pre.className='bb-text';pv.appendChild(pre);
      fetch(src+'?download=1',{credentials:'same-origin'}).then(function(r){return r.ok?r.text():''}).then(function(t){if(cur===it)pre.textContent=t.slice(0,20000)}).catch(function(){})}
    else if(it.type==='video'&&/^video\//.test(it.mime)){var v=document.createElement('video');v.controls=true;v.preload='metadata';v.src=src+'?download=1';pv.appendChild(v)}
    else{var card=document.createElement('div');card.className='bb-card-prev';card.innerHTML=tile;
      pv.appendChild(card)}
    var sendBtn=$('[data-act="send"]',sheetItem);
    if(navigator.canShare&&window.File&&it.size<=SHAREMAX){       // a big file is downloaded by Send instead
      sendBtn.disabled=true;sendBtn.querySelector('span').textContent='Getting it ready…';
      fetch(src+'?download=1',{credentials:'same-origin'}).then(function(r){if(!r.ok)throw 0;return r.blob()}).then(function(b){
        if(cur!==it)return;var f=new File([b],it.name,{type:it.mime||b.type||'application/octet-stream'});
        shareFile=navigator.canShare({files:[f]})?f:null;
      }).catch(function(){}).then(function(){if(cur!==it)return;sendBtn.disabled=false;sendBtn.querySelector('span').textContent='Send'});
    }
  }
  open(sheetItem);
}
root.addEventListener('click',function(e){
  var more=e.target.closest('[data-more]'),openRow=e.target.closest('[data-open]');
  if(more){e.preventDefault();showItem(itemOf(more),tileOf(more));return}
  if(openRow&&!e.metaKey&&!e.ctrlKey){e.preventDefault();showItem(itemOf(openRow),tileOf(openRow));return}
  var rb=e.target.closest('[data-restore]');
  if(rb){var it=itemOf(rb);rb.disabled=true;post('restore',{id:it.id}).then(function(r){
    if(!r.ok){rb.disabled=false;return say(r.error)}later('Put back: '+it.name);location.reload()})}
});
// THE FILE'S ADDRESS IS BUILT HERE, never written out as a link in the page's source.
function fileUrl(it,dl){return '/brain/file/'+encodeURIComponent(it.id)+(dl?'?download=1':'')}
function download(it){var a=document.createElement('a');a.href=fileUrl(it,true);a.download=it.name;document.body.appendChild(a);a.click();a.remove()}
sheetItem.addEventListener('click',function(e){
  var b=e.target.closest('[data-act]');if(!b||!cur)return;var it=cur,act=b.getAttribute('data-act');
  if(act==='send'){
    // STRAIGHT FROM THE TAP: no await between here and share(), or Safari will not open the sheet.
    if(shareFile){navigator.share({files:[shareFile],title:it.name}).catch(function(){});return}
    download(it);return;
  }
  if(act==='open'){window.open('/brain/file/'+encodeURIComponent(it.id),'_blank','noopener');return}
  if(act==='download'){download(it);return}
  shut(sheetItem);
  if(act==='rename'){nameMode='rename';$('#bb-name-t').textContent='Rename';$('[data-ok]',dlgName).textContent='Rename';
    var i=dlgName.querySelector('input');i.value=it.name;err(dlgName,'');open(dlgName);
    setTimeout(function(){i.focus();var dot=it.kind==='file'?it.name.lastIndexOf('.'):-1;i.setSelectionRange(0,dot>0?dot:it.name.length)},50);return}
  if(act==='move'){var s=dlgMove.querySelector('select');s.value=it.parent||'';
    [].forEach.call(s.options,function(o){o.disabled=o.value===it.id});err(dlgMove,'');open(dlgMove);return}
  if(act==='delete'){$('#bb-del-t').textContent='Move “'+it.name+'” to the trash?';err(dlgDel,'');open(dlgDel)}
});
dlgMove.querySelector('form').addEventListener('submit',function(e){
  e.preventDefault();var to=dlgMove.querySelector('select').value;
  post('move',{id:cur.id,parent:to}).then(function(r){if(!r.ok)return err(dlgMove,r.error);later('Moved');location.reload()});
});
dlgDel.querySelector('form').addEventListener('submit',function(e){
  e.preventDefault();var it=cur;
  post('delete',{id:it.id}).then(function(r){
    if(!r.ok)return err(dlgDel,r.error);shut(dlgDel);
    var li=root.querySelector('.bb-row[data-item*="'+it.id+'"]');if(li)li.remove();
    say('Moved to the trash',function(){post('restore',{id:it.id}).then(function(x){
      if(!x.ok)return say(x.error);later('Put back: '+it.name);location.reload()})});
  });
});
})();"""
