#!/usr/bin/env python3
"""add_machine — plug a pack into THIS box (plan PR 4: D1b, D6, D6b, D7).

  python scripts/add_machine.py <pack-dir | pack.zip>          install (or re-run: no change)
  python scripts/add_machine.py <pack> --dry-run                say what would happen, touch nothing

What it does, in order, refusing before it copies anything:
  1. reads machine.yaml and validates it (core.packs — refusal names the field)
  2. checks `contract` against the plug contract and `requires_foundation` against
     core/FOUNDATION_VERSION (names both versions, tells you to `git pull`)
  3. D6b — the HOST core machine must already be installed here. A recipe never lands in
     bare foundation, and a Content-hosted recipe never lands in a Lead box.
  4. D7 — records the pack in licence.json (a record, not a lock). With --require-licence,
     a pack the licence does not already name is refused instead.
  5. copies the pack under <host>/plugins/<slug>/ (never overwrites files it did not write)
  6. D6 — writes ONLY `machines: <slug>: enabled: false` into my/settings.yaml. Every other
     default lives in the manifest and is merged BELOW your overlay at load. Never a list.
  7. runs the pack's own suites inside this box
  8. prints the env vars the pack needs that are still unset
Idempotent: run it twice and the second run changes nothing.
"""
import argparse
import json
import pathlib
import shutil
import sys
import tempfile
import zipfile
import subprocess
import os

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("AIOS_HERMETIC_TEST", "1")
from core import packs  # noqa: E402

HOST_MARKER = {"lead_machine": "marketing/lead_machine/handler.py", "content_machine": "marketing/content_machine/written"}


def fail(msg: str) -> int:
    print(f"✗ {msg}"); return 1


def env_set(k: str) -> bool:
    if os.environ.get(k):
        return True
    envf = ROOT / ".env"
    return envf.exists() and any(l.startswith(k + "=") and l.split("=", 1)[1].strip() for l in envf.read_text().splitlines())


def overlay_has_block(text: str, slug: str) -> bool:
    return f"\n  {slug}:" in text and "\nmachines:" in text


def add_overlay_block(path: pathlib.Path, slug: str) -> bool:
    """Append `machines: <slug>: enabled: false` textually — comments survive, lists are never
    written, and an existing block for this slug is left exactly as the buyer set it."""
    text = path.read_text() if path.exists() else ""
    if overlay_has_block(text, slug):
        return False
    block = (f"  {slug}:\n    enabled: false   # added by add_machine — set true to arm this pack\n")
    if "\nmachines:" in text or text.startswith("machines:"):
        idx = text.index("machines:") + len("machines:\n") if "machines:\n" in text else None
        text = text[:idx] + block + text[idx:] if idx else text + "\n" + block
    else:
        text = text.rstrip("\n") + "\n\nmachines:\n" + block
    path.write_text(text)
    return True


def main(argv) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("pack"); ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--require-licence", action="store_true")
    a = ap.parse_args(argv)
    src = pathlib.Path(a.pack)
    tmp = None
    if src.is_file() and src.suffix == ".zip":
        tmp = pathlib.Path(tempfile.mkdtemp()); zipfile.ZipFile(src).extractall(tmp)
        inner = [d for d in tmp.iterdir() if d.is_dir() and (d / "machine.yaml").exists()]
        if len(inner) != 1:
            return fail("the zip must contain exactly one pack directory holding machine.yaml")
        src = inner[0]
    if not (src / "machine.yaml").exists():
        return fail(f"{src} has no machine.yaml — not a pack")
    try:
        m = packs.load(src)
    except packs.PackError as e:
        return fail(f"machine.yaml refused: {e}")
    print(f"pack: {m['name']} ({m['slug']})  host: {m['host']}  contract: {m['contract']}  prefix: {m['prefix']}")

    ok, msg = packs.requires_ok(m)
    if not ok:
        return fail(msg)
    if m["contract"] != 1:
        return fail(f"contract {m['contract']} — this foundation speaks plug contract 1")
    marker = ROOT / HOST_MARKER[m["host"]]
    if not marker.exists():
        return fail(f"this pack plugs into the {m['host'].replace('_', ' ')}, which is not installed in this box. "
                    f"A recipe needs a core machine — the Lead Machine or the Content Machine — to plug into.")

    # a prefix is provenance. Riding a kernel source's prefix is fine (two Places recipes both
    # emit `places:` rows); a NEW prefix belongs to exactly one pack, and the door is where that
    # is refused — never at boot.
    # THE HOST'S OWN PLUG, not always the Lead Machine's (PW-4). Each host keeps its own
    # kernel prefixes — the Lead Machine's are Apollo/Places/registers, the Content Machine's
    # are reel/manual/voice — so reading one host's list while installing into the other
    # waves through a prefix that collides with a kernel source it never saw. Harmless while
    # content had no plug; wrong the moment it got one, and silent either way.
    # BUILT FROM THE HOST, never written out. The exporter drops a script naming a lane the
    # box does not carry — and it scans STRINGS as well as imports, so spelling both module
    # paths here made this very script vanish from the Lead box (measured: test_box_boots,
    # "every script a buyer doc names ships in this box"). An f-string names neither.
    try:
        import importlib
        kernel = set(importlib.import_module(f"marketing.{m['host']}.plug")._KERNEL_PREFIXES)
    except Exception:  # noqa: BLE001 — a box without that host has no plug to read
        kernel = set()
    try:
        for o in packs.discover():
            if o["slug"] != m["slug"] and o["prefix"] == m["prefix"] and m["prefix"] not in kernel:
                return fail(f"prefix '{m['prefix']}' is already claimed by the installed pack {o['slug']} — "
                            f"a new prefix belongs to one pack")
    except packs.PackError as e:
        return fail(f"an installed pack is invalid — fix it first: {e}")

    lic = ROOT / "licence.json"
    licence = json.loads(lic.read_text()) if lic.exists() else {}
    named = m["slug"] in (licence.get("packs") or [])
    if a.require_licence and not named:
        return fail(f"licence.json does not name '{m['slug']}' — this box is not licensed for it")

    dest = ROOT / "marketing" / m["host"] / "plugins" / m["slug"].replace("-", "_")
    overlay = ROOT / "my" / "settings.yaml"
    print(f"→ {dest.relative_to(ROOT)}" + ("  (present)" if dest.exists() else ""))
    if a.dry_run:
        print("dry run — nothing written"); return 0

    changed = []
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not (dest.parent / "__init__.py").exists():
        (dest.parent / "__init__.py").write_text("")
    if dest.exists():
        same = all((dest / p.relative_to(src)).exists() and (dest / p.relative_to(src)).read_bytes() == p.read_bytes()
                   for p in src.rglob("*") if p.is_file())
        if not same:
            return fail(f"{dest.relative_to(ROOT)} exists and differs from the pack. Remove it yourself if you mean to replace it — this tool never overwrites.")
    else:
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store")); changed.append("copied")
    if add_overlay_block(overlay, m["slug"]):
        changed.append("overlay")
    if not named:
        licence.setdefault("packs", []).append(m["slug"]); lic.write_text(json.dumps(licence, indent=2) + "\n"); changed.append("licence")

    vpy = ROOT / ".venv/bin/python"; vpy = str(vpy) if vpy.exists() else sys.executable
    for s in m.get("suites", []):
        r = subprocess.run([vpy, str(dest / s)], cwd=ROOT, capture_output=True, text=True, env={**os.environ, "AIOS_HERMETIC_TEST": "1"})
        print(f"  suite {s}: {'ok' if r.returncode == 0 else 'FAILED'}")
        if r.returncode:
            print((r.stdout + r.stderr)[-600:]); return 1
    missing = [k for k in m.get("env", []) if not env_set(k)]
    if missing:
        print(f"  set in .env before arming: {' '.join(missing)}")
    print("installed:" if changed else "already installed — nothing changed:",
          ", ".join(changed) or "no-op", f"| arm it with  machines.{m['slug']}.enabled: true  in my/settings.yaml")
    if tmp: shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
