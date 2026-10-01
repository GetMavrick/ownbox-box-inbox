#!/usr/bin/env python3
"""ownbox — build your own machine on this box (docs/SCOPE_MACHINE_MARKETPLACE.md §9, SDK v0).

    python scripts/ownbox.py new job-tracker        # a working starter in my/machines/job-tracker/
    python scripts/ownbox.py check job-tracker      # is it built only on what the box promises?
    python scripts/ownbox.py check                  # every machine in my/machines/

`new` writes a machine that runs as it is: a menu row, a signed-in screen, a line on the Morning
Review and a question the owner's AI can ask, all through `from core import sdk` and nothing else.
`check` reads a machine's code without running it and names, with its fix in one sentence:
  * AI used any way but `m.think` (the box's one metered account, under its monthly ceiling)
  * anything imported from the box that `core.sdk` does not promise (it can change in any update)
  * a machine.yaml missing `requires_foundation`, `needs:` or `sdk:`, or naming another folder
It does NOT flag a machine acting on the world (sending, fetching, paying): on your own box that is
yours to decide (CLAUDE.md, non-negotiable 3). Exit 0 when clean, 1 when anything was found.

Written first for the AI that will do the building: every finding is a sentence it can act on.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_SLUG = re.compile(r"^[a-z][a-z0-9-]{1,40}$")

from core.sdk_check import check  # noqa: E402  (one list of rules, shared with the break notice)


def machines_dir() -> pathlib.Path:
    return pathlib.Path(os.environ.get("AIOS_MY_MACHINES") or (ROOT / "my" / "machines"))


def _resolve(target: str) -> pathlib.Path:
    p = pathlib.Path(target)
    return p if p.is_dir() else machines_dir() / target


def cmd_check(targets: list[str]) -> int:
    folders = [_resolve(t) for t in targets] if targets else sorted(
        d for d in machines_dir().iterdir() if d.is_dir() and not d.name.startswith((".", "_"))) \
        if machines_dir().is_dir() else []
    if not folders:
        print(f"No machines to check in {machines_dir()}.")
        return 0
    found = 0
    for folder in folders:
        if not folder.is_dir():
            print(f"✗ {folder}: no such machine folder")
            found += 1
            continue
        rows = check(folder)
        found += len(rows)
        if not rows:
            print(f"✓ {folder.name}: built only on what the box promises (sdk {_sdk_version()})")
        for r in rows:
            where = f"{r['path']}:{r['line']}" if r["line"] else r["path"]
            print(f"✗ {where}: {r['problem']}. Fix: {r['fix']}")
    return 1 if found else 0


def _sdk_version() -> int:
    from core import sdk
    return sdk.VERSION


# ── new: the starter ──────────────────────────────────────────────────────────────────────────────

def _title(slug: str) -> str:
    return " ".join(w.capitalize() for w in slug.split("-"))


STARTER_INIT = '''"""{title}: a machine of your own, built on the box's promise (`from core import sdk`, sdk: 1).

Everything it does goes through `m`, the handle below. After each change run
`python scripts/ownbox.py check {slug}`; the guide is BUILD_A_MACHINE.md.
"""
import html
from datetime import date, datetime, timezone

from flask import redirect, request

from core import sdk

m = sdk.machine("{slug}")
blueprint = m.blueprint                      # the box mounts this when it starts
m.menu("{key}", title="{title}", href=m.home)   # /my/{slug}


def notes() -> list:
    """The saved notes, newest last: [{{"text": str, "day": "YYYY-MM-DD"}}]."""
    got = m.setting("notes", default=[]) or []
    return [n for n in got if isinstance(n, dict) and isinstance(n.get("text"), str)]


def add(text: str) -> None:
    text = (text or "").strip()[:200]
    if text:
        today = datetime.now(timezone.utc).date().isoformat()
        m.save_setting("notes", (notes() + [{{"text": text, "day": today}}])[-50:])


@m.screen(m.home, methods=("GET", "POST"))
def home():
    if request.method == "POST":
        add(request.form.get("note", ""))
        return redirect(m.home, code=303)
    rows = "".join(f"<p>{{html.escape(n['text'])}}</p>" for n in reversed(notes()))
    body = ('<div class="card"><h2>Add a note</h2><form method="post">'
            '<input name="note" maxlength="200" aria-label="Note" '
            'style="font-size:16px;width:100%;min-height:48px;box-sizing:border-box">'
            '<button type="submit" style="width:100%;min-height:48px;margin-top:12px">Save</button>'
            '</form></div>'
            '<div class="card"><h2>Notes</h2>'
            + (rows or '<p class="quiet">Nothing saved yet.</p>') + '</div>')
    return sdk.page(m.home, title="{title}", lede="Your own machine, running on your box.", body=body)


def morning(day: date) -> dict:
    """This machine's part of the Morning Review: the notes saved that day, or nothing at all."""
    saved = [n for n in notes() if n.get("day") == day.isoformat()]
    if not saved:
        return {{}}
    return {{"title": "{title}",
            "headline": {{"value": len(saved), "label": "note saved" if len(saved) == 1 else "notes saved"}},
            "happened": [{{"text": n["text"]}} for n in saved]}}


m.reporter("{title}", morning)


def latest(limit: int = 5) -> dict:
    """The newest notes, for the owner's AI to read."""
    return {{"notes": [n["text"] for n in notes()[-max(1, min(int(limit), 20)):]]}}


m.tool("latest_notes", fn=latest, title="Read the newest notes in {title}",
       description="The newest notes saved in {title}.",
       capability="read:{key}_notes", args={{"limit": {{"type": "integer", "description": "how many, 1 to 20"}}}})
'''

STARTER_YAML = '''name: {slug}
version: 0.1.0
requires_foundation: "{foundation}"  # the box version it was built on
needs: []                           # plan features it needs, like [coworkers]
sdk: {sdk}                              # the promise it is built on: `from core import sdk`
'''

STARTER_TEST = '''"""{title}'s own test. Run it with: python my/machines/{slug}/test_{key}.py"""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))

import yaml  # noqa: E402

m = yaml.safe_load((HERE / "machine.yaml").read_text())
assert m["name"] == HERE.name, "machine.yaml names its own folder"
assert m.get("sdk") == 1, "built on sdk 1"
print("{slug} test ran: ALL OK")
'''


def new(slug: str, *, into: pathlib.Path | None = None) -> pathlib.Path:
    """Write the starter machine. Refuses a bad name or a folder that already exists."""
    from core import packs, sdk
    if not _SLUG.match(slug or ""):
        raise ValueError(f"{slug!r}: use lowercase letters, digits and hyphens, starting with a letter")
    folder = (into or machines_dir()) / slug
    if folder.exists():
        raise ValueError(f"{folder} already exists; pick another name or remove it first")
    folder.mkdir(parents=True)
    values = {"slug": slug, "key": slug.replace("-", "_"), "title": _title(slug),
              "foundation": packs.foundation_version(), "sdk": sdk.VERSION}
    (folder / "machine.yaml").write_text(STARTER_YAML.format(**values), encoding="utf-8")
    (folder / "__init__.py").write_text(STARTER_INIT.format(**values), encoding="utf-8")
    (folder / f"test_{values['key']}.py").write_text(STARTER_TEST.format(**values), encoding="utf-8")
    return folder


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ownbox", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new", help="write a working starter machine in my/machines/")
    n.add_argument("name")
    c = sub.add_parser("check", help="is a machine built only on what the box promises?")
    c.add_argument("names", nargs="*")
    a = ap.parse_args(argv)
    if a.cmd == "new":
        try:
            folder = new(a.name)
        except ValueError as e:
            print(f"✗ {e}")
            return 1
        print(f"✓ {folder} written. Restart the box (sudo systemctl restart aios-dispatch aios-worker) "
              f"and it appears in the menu. Then: python scripts/ownbox.py check {a.name}")
        return 0
    return cmd_check(a.names)


if __name__ == "__main__":
    sys.exit(main())
