"""The Business Brain's door for its screen: list, search, add, rename, move, delete, restore, and the file itself.

Owner, 2026-10-08: "a basic files manager immediately ... listed as Business Brain in the menu at the top". The
screen (WebDev2's, at /brain) draws the folders and calls these; the rules live in core/business_brain.py, so the
screen, the connector and anything later share one set. Everyone signed in to the box may use it; every change
records who made it, and a delete is a move to the trash for 30 days.

EVERY ANSWER IS JSON, `{"ok": true, ...}` or `{"ok": false, "error": "<a sentence>"}`, except the file itself. A POST
takes a form or JSON; the box's one same-origin gate (core/dispatch.py) already refuses a POST from another site.

THE FILE ITSELF (`/brain/file/<id>`) is answered with `business_brain.serve_headers`: an image, a PDF or plain text
may be shown, everything else is downloaded, and nothing is sniffed. `?download=1` always downloads. The screen's
Send fetches it and hands the bytes to the phone's share sheet (navigator.share), so sending never needs a link.
"""
from __future__ import annotations

from flask import jsonify, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge

from core import business_brain as bb
from core.dash import blueprint
from core.dash.box_settings import _admit, _who
from core.logging import get_logger

log = get_logger(__name__)

BASE = "/brain"
MAX_FILES = 10                                       # one add, at most this many files


def _who_id() -> str:
    return str(_who().get("id") or "")


def _arg(name: str) -> str:
    body = request.get_json(silent=True) if request.is_json else None
    return str((body or {}).get(name) if body is not None else request.values.get(name) or "")


def _gate():
    refuse = _admit(owner_only=False)
    if refuse is not None:
        return refuse
    if not _who_id():
        return jsonify({"ok": False, "error": "Sign in again."}), 401
    return None


def _do(fn):
    try:
        return jsonify({"ok": True, **fn()})
    except bb.BrainError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@blueprint.route(f"{BASE}/api/list")
def brain_list():
    return _gate() or _do(lambda: {**bb.listing(request.args.get("folder", "")), "usage": bb.usage()})


@blueprint.route(f"{BASE}/api/search")
def brain_search():
    return _gate() or _do(lambda: {"items": bb.search(request.args.get("q", ""))})


@blueprint.route(f"{BASE}/api/trash")
def brain_trash():
    return _gate() or _do(lambda: {"items": bb.trash(), "days": bb.TRASH_DAYS})


@blueprint.route(f"{BASE}/api/history/<item_id>")
def brain_history(item_id: str):
    return _gate() or _do(lambda: {"items": bb.history(item_id)})


@blueprint.route(f"{BASE}/api/upload", methods=["POST"])
def brain_upload():
    """One or more files into a folder (`folder`, `file` repeated). Each is kept or refused on its own."""
    gate = _gate()
    if gate:
        return gate
    # THIS ROUTE ALONE TAKES FILES, so it alone raises the app's 256 KB body cap, before anything reads the body (the
    # same move as the inbox's attachment and the box's icon).
    request.max_content_length = MAX_FILES * bb.FILE_MAX + 256 * 1024
    try:
        files = request.files.getlist("file")[:MAX_FILES]
        folder = request.form.get("folder", "")
        saved, refused = [], []
        for f in files:
            try:                                     # streamed to the disk, never held whole (100 MB a file)
                saved.append(bb.save_stream(folder, f.filename or "file", f.stream, by=_who_id()))
            except bb.BrainError as e:
                refused.append({"name": f.filename or "file", "error": str(e)})
    except RequestEntityTooLarge:
        return jsonify({"ok": False, "error": f"Files must be under {bb.FILE_MAX >> 20} MB each, and at most "
                                             f"{MAX_FILES} at once."}), 413
    if not files:
        return jsonify({"ok": False, "error": "Choose a file to add."}), 400
    return jsonify({"ok": bool(saved), "saved": saved, "refused": refused}), (200 if saved else 400)


@blueprint.route(f"{BASE}/api/folder", methods=["POST"])
def brain_folder():
    return _gate() or _do(lambda: {"item": bb.make_folder(_arg("parent"), _arg("name"), by=_who_id())})


@blueprint.route(f"{BASE}/api/rename", methods=["POST"])
def brain_rename():
    return _gate() or _do(lambda: {"item": bb.rename(_arg("id"), _arg("name"), by=_who_id())})


@blueprint.route(f"{BASE}/api/move", methods=["POST"])
def brain_move():
    return _gate() or _do(lambda: {"item": bb.move(_arg("id"), _arg("parent"), by=_who_id())})


@blueprint.route(f"{BASE}/api/delete", methods=["POST"])
def brain_delete():
    return _gate() or _do(lambda: bb.delete(_arg("id"), by=_who_id()))


@blueprint.route(f"{BASE}/api/restore", methods=["POST"])
def brain_restore():
    return _gate() or _do(lambda: {"item": bb.restore(_arg("id"), by=_who_id())})


@blueprint.route(f"{BASE}/file/<item_id>")
@blueprint.route(f"{BASE}/file/<item_id>/<path:name>")
def brain_file(item_id: str, name: str = ""):
    """The file. The name after the id is only for the person's eyes (a saved file keeps it); the id decides."""
    gate = _gate()
    if gate:
        return gate
    try:
        path, r = bb.blob_path(item_id)
    except bb.BrainError as e:
        return jsonify({"ok": False, "error": str(e)}), 404
    # STREAMED FROM THE DISK, and a video or a long PDF can be read a range at a time; the headers are the brain's own.
    h = bb.serve_headers(r, download=request.args.get("download") == "1")
    resp = send_file(path, mimetype=h["Content-Type"], conditional=True, etag=r["sha256"], max_age=0)
    for k, v in h.items():
        resp.headers[k] = v
    return resp
