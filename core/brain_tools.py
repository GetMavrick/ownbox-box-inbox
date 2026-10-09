"""The Business Brain, for an AI on the connector: look through it, read a document, and ask to save one.

Owner, 2026-10-08: the Business Brain is "the shared context and knowledge base that the agents inside the machine and
outside the machine will reference, read and write". So any seat the owner minted reads it, as it reads the inbox
(which holds conversations at least as private as a document), and every write is a proposal the owner approves on
Approvals. An agent saves words only: a document it wrote, as text. It never deletes; people do that, and the trash
keeps it.

A PROTOTYPE, KEPT TO ITS OWN FILE (owner, 2026-10-08: "Nothing invasive please"): the rules are core/business_brain.py,
and a version two replaces both without touching the rest of the box.
"""
from __future__ import annotations

import os

from core import business_brain as bb
from core.connector import tools
from core.connector import words as say
from core.logging import get_logger

log = get_logger(__name__)

MACHINE = "core"
SAVE_KIND = "business_brain_save"
SAVE_MAX = 12_000                       # characters an agent may save in one document: the card shows it whole
SCREEN = "/brain"


def _file_link(r: dict) -> str:
    return say.link(f"/brain/file/{r['id']}")


# ── reading ───────────────────────────────────────────────────────────────────────────────────────────────────────
def business_brain(folder: str = "", search: str = ""):
    """What is in a folder of the Shared Brain (the top when none is named), or what a search finds."""
    if str(search or "").strip():
        return {"search": search, "items": bb.search(search, limit=30)}
    f = bb.resolve(folder) if str(folder or "").strip() else None
    if str(folder or "").strip() and (not f or f["kind"] != "folder"):
        return {"error": f"there is no folder called {folder!r}", "items": []}
    got = bb.listing(f["id"] if f else "")
    prefix = bb.path_of(f["id"]) if f else ""
    for i in got["items"]:
        i["path"] = f"{prefix}/{i['name']}" if prefix else i["name"]
    return {"folder": prefix, "items": got["items"], "usage": bb.usage()}


def _render_list(r: dict) -> str:
    if r.get("error"):
        return say.answer(f"Nothing found: {r['error']}. See your Shared Brain: {say.link(SCREEN)}",
                          say.ask_next(("core.business_brain", "What is in my Shared Brain?")))
    items = r.get("items") or []
    where = (f"for {say.quoted(r['search'], 60)}" if r.get("search") else
             f"in {say.quoted(r['folder'], 80)}" if r.get("folder") else "at the top of your Shared Brain")
    if not items:
        head = f"Nothing {where}."
    else:
        lines = [f"- {i['path']}" + ("/" if i["kind"] == "folder" else f" ({_file_link(i)})") for i in items[:30]]
        head = f"{say.plural(len(items), 'item', 'items')} {where}:\n" + "\n".join(lines)
    return say.answer(head + f"\n\nOpen it on your phone: {say.link(SCREEN)}",
                      say.ask_next(("core.business_brain_file", "Read one of these documents"),
                                   ("core.propose_business_brain_save", "Save a document to it")))


def business_brain_file(path: str = ""):
    """One document by its path in the Shared Brain ("Folder/Sub/name.md"): its words when the box can read them."""
    if not str(path or "").strip():
        return {"error": "name the document, by its path like Price sheets/Gyms.md"}
    r = bb.resolve(path)
    if not r or r["kind"] != "file":
        return {"error": f"there is no document at {path!r}"}
    try:
        text = bb.read_text(r["id"])
    except bb.BrainError as e:
        return {"error": str(e)}
    return {"path": bb.path_of(r["id"]), "name": r["name"], "size": r["size"], "mime": r["mime"],
            "changed_at": r["changed_at"], "readable": text is not None, "text": text or "",
            "link": f"/brain/file/{r['id']}"}


def _render_file(r: dict) -> str:
    if r.get("error"):
        return say.answer(f"Nothing found: {r['error']}.", say.ask_next(("core.business_brain",
                                                                         "What is in my Shared Brain?")))
    nxt = say.ask_next(("core.business_brain", "What else is in my Shared Brain?"),
                       ("core.propose_business_brain_save", "Save a document to it"))
    if not r.get("readable"):
        return say.answer(f"{r['path']} is a file the box can't read as words yet (a PDF, an image, a Word file). "
                          f"Open it here: {say.link(r['link'])}", nxt)
    return say.answer(f"{r['path']}:\n\n{r['text']}\n\nOpen it here: {say.link(r['link'])}", nxt)


# ── writing: always a proposal ────────────────────────────────────────────────────────────────────────────────────
def _split(path: str) -> tuple[list[str], str]:
    parts = [p.strip() for p in str(path or "").replace("\\", "/").split("/") if p.strip()]
    if not parts:
        raise bb.BrainError("Say where it goes and what it is called, like Price sheets/Gyms.md.")
    return [bb.clean_name(p) for p in parts[:-1]], bb.clean_name(parts[-1])


def propose_business_brain_save(path: str = "", text: str = "", seat=None):
    """Ask the owner to save a document an agent wrote into the Shared Brain."""
    from core import approvals
    try:
        folders, name = _split(path)
    except bb.BrainError as e:
        return {"asked": False, "note": str(e)}
    if not os.path.splitext(name)[1]:
        name += ".md"
    if not bb.is_text(name):
        return {"asked": False, "note": "an agent saves words only: name it .md or .txt"}
    body = str(text or "")
    if not body.strip():
        return {"asked": False, "note": "there is nothing to save: send the document's words"}
    if len(body) > SAVE_MAX:
        return {"asked": False, "note": f"that is {len(body):,} characters; one document from an agent is at most "
                                        f"{SAVE_MAX:,}"}
    where = "/".join(folders + [name])
    existing = bb.resolve(where)
    shown = {"Change": ("Replace" if existing else "Save") + f" {where} in your Shared Brain",
             "Document": body,
             "Means": ("the document there now goes to the trash, where you can put it back for 30 days" if existing
                       else "a new document" + (f", in a new folder {'/'.join(folders)}" if folders and
                                                not bb.resolve("/".join(folders)) else ""))}
    a = approvals.propose(SAVE_KIND, machine=MACHINE, title=shown["Change"],
                          detail={"app": "Shared Brain", "arguments": shown, "folders": folders, "name": name,
                                  "text": body},
                          seat_id=str((seat or {}).get("label") or (seat or {}).get("id") or ""))
    return {"asked": True, "approval": a["id"], "repeat": bool(a.get("repeat")),
            "note": "waiting for the owner, who approves or declines in the mobile app. Nothing has changed yet."}


def _run_save(detail: dict) -> dict:
    from core import approvals
    by = f"approval:{approvals.decider() or 'owner'}"
    try:
        parent = ""
        for f in (detail or {}).get("folders") or []:
            found = [i for i in bb.listing(parent)["items"] if i["kind"] == "folder" and i["name"].lower() == f.lower()]
            parent = found[0]["id"] if found else bb.make_folder(parent, f, by=by)["id"]
        r = bb.save(parent, (detail or {}).get("name") or "document.md",
                    str((detail or {}).get("text") or "").encode("utf-8"), by=by, replace=True)
    except bb.BrainError as e:
        return {"ok": False, "text": f"Not saved: {e}"}
    return {"ok": True, "text": f"Saved to your Shared Brain: {bb.path_of(r['id'])}."}


from core import approvals as _approvals  # noqa: E402

_approvals.register_kind(SAVE_KIND, run=_run_save)


def _render_save(r: dict) -> str:
    return say.proposal(r, ("core.business_brain", "What is in my Shared Brain?"))


tools.register(
    "business_brain",
    title="Look through your Shared Brain",
    fn=business_brain, machine=MACHINE, min_role="read", render=_render_list, capability="read:reports",
    description="The business's own folders and documents, kept on its box: what is in a folder (the top when none is "
                "named, by path like Price sheets/Gyms), or every item a search finds by name and, for text "
                "documents, by their words. Each item has its path and a link the person can open.",
    args={"folder": {"type": "string", "required": False,
                     "description": "A folder's path, like Price sheets or Clients/2026. Empty for the top."},
          "search": {"type": "string", "required": False,
                     "description": "Words to find in names and in text documents, instead of a folder."}},
)

tools.register(
    "business_brain_file",
    title="Read a document in your Shared Brain",
    fn=business_brain_file, machine=MACHINE, min_role="read", render=_render_file, capability="read:reports",
    description="One document from the Shared Brain by its path. A text document (.md, .txt, .csv and the like) comes "
                "back as its words; a PDF, an image or a Word file comes back as its name and a link, because the box "
                "can't read inside those yet.",
    args={"path": {"type": "string", "required": False,
                   "description": "The document's path, like Price sheets/Gyms.md."}},
)

tools.register(
    "propose_business_brain_save",
    title="Ask before saving a document to your Shared Brain",
    fn=propose_business_brain_save, machine=MACHINE, min_role="act", render=_render_save,
    capability="write:proposals", wants_seat=True,
    description=f"Ask the owner to save a document you wrote into the Shared Brain, at a path like Price "
                f"sheets/Gyms.md (missing folders are made). Words only (.md or .txt), at most {SAVE_MAX:,} characters. "
                "A document already at that path is replaced and its old version goes to the trash for 30 days. "
                "Nothing changes until the owner approves.",
    args={"path": {"type": "string", "required": True,
                   "description": "Where it goes and its name, like Price sheets/Gyms.md."},
          "text": {"type": "string", "required": True, "description": "The document's words."}},
)
