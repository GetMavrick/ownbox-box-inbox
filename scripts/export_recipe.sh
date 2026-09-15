#!/usr/bin/env bash
# Package one machine pack (a sold recipe) from the tree AS IT IS — prove-from-inside for packs.
#
#   bash scripts/export_recipe.sh marketing/lead_machine/plugins/med_spas_california /tmp/recipes
#
# Produces <out>/<slug>.zip and <out>/<slug>.tar.gz, each holding ONE top folder
# <slug_with_underscores>/ with the manifest, its files, its own suite, an INSTALL.md and a
# PACK.json stamp. A buyer installs it with `python scripts/add_machine.py <the zip>`.
#
# Refuses, naming the reason, when: the manifest does not validate; the pack's prefix collides
# with ANOTHER pack's (plan §9 — first-seen provenance only means something if a prefix is unique);
# the pack ships armed. tests/test_recipe_ships.py is the proof: it builds this archive, installs
# it into a fresh Lead box from a zip, and runs every shipped suite there with the pack in place.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${ROOT}/.venv/bin/python"; [ -x "$PY" ] || PY=python3
SRC="${1:?usage: export_recipe.sh <pack-dir> <out-dir>}"; OUT="${2:?usage: export_recipe.sh <pack-dir> <out-dir>}"
SRC="$(cd "$SRC" && pwd)"
[ -f "$SRC/machine.yaml" ] && { true; } || { echo "$SRC has no machine.yaml — not a pack" >&2; exit 2; }

# ── validate + prefix collision + slug, in one read of the manifest ──────────────────────
# stdout is not a channel here: the structured logger prints module registration to it on
# some hosts (CI did), so the manifest summary goes through a file.
METAF="$(mktemp)"
(cd "$ROOT" && AIOS_HERMETIC_TEST=1 "$PY" - "$SRC" "$METAF" <<'PYE'
import json, pathlib, sys
sys.path.insert(0, ".")
from core import packs
src, metaf = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
try:
    m = packs.load(src)
except packs.PackError as e:
    print(f"REFUSED manifest: {e}", file=sys.stderr); sys.exit(3)
if (m.get("config") or {}).get("enabled") is not False:
    print("REFUSED: config.enabled must be false — a pack ships dark", file=sys.stderr); sys.exit(3)
try:
    # The HOST's kernel prefixes (PW-4): reading the Lead Machine's list while exporting a
    # CONTENT pack compares against the wrong universe — a content prefix that rides a
    # content kernel source would be refused, and one that collides would be let through.
    import importlib
    # Built from the host, never spelled out: the exporter scans strings as well as imports,
    # and a literal module path here would name a lane the box may not carry.
    plug = importlib.import_module(f"marketing.{m['host']}.plug")
    kernel = set(plug._KERNEL_PREFIXES)
except Exception as e:  # noqa: BLE001
    # An unreadable kernel list used to fall back to "no kernel prefixes", which turned every pack
    # riding `places` into a false "prefix claimed by <other pack>" refusal the moment a second
    # Places recipe existed (measured: a worktree without its own .venv, system python3). Stop and
    # say why; a wrong verdict that blames another pack is worse than stopping.
    print(f"REFUSED: cannot read the kernel prefixes ({type(e).__name__}: {e}) — run this from the box's venv", file=sys.stderr); sys.exit(3)
try:
    others = [o for o in packs.discover() if o["slug"] != m["slug"] and o["prefix"] == m["prefix"]
              and m["prefix"] not in kernel]
except packs.PackError as e:
    print(f"REFUSED: another pack in this tree is invalid: {e}", file=sys.stderr); sys.exit(3)
if others:
    print(f"REFUSED: prefix '{m['prefix']}' is also claimed by pack {others[0]['slug']}", file=sys.stderr); sys.exit(3)
metaf.write_text(json.dumps({"slug": m["slug"], "name": m["name"], "host": m["host"], "prefix": m["prefix"],
                  "requires_foundation": m["requires_foundation"], "kind": m.get("kind"), "module": m.get("module")}))
PYE
)
META="$(cat "$METAF")"; rm -f "$METAF"
SLUG="$(printf '%s' "$META" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["slug"])')"
DIR="${SLUG//-/_}"
mkdir -p "$OUT"; STAGE="$(mktemp -d)"; PACK="$STAGE/$DIR"
rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store' --exclude '.pytest_cache' "$SRC/" "$PACK/"

# ── stamp + install note ─────────────────────────────────────────────────────────────────
SHA="$(cd "$ROOT" && git rev-parse --short HEAD 2>/dev/null || echo unversioned)"
"$PY" - "$PACK" "$META" "$SHA" <<'PYE'
import hashlib, json, pathlib, sys, datetime
pack, meta, sha = pathlib.Path(sys.argv[1]), json.loads(sys.argv[2]), sys.argv[3]
digest = hashlib.sha256((pack / "machine.yaml").read_bytes()).hexdigest()
(pack / "PACK.json").write_text(json.dumps({**meta, "built_from": sha,
    "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "manifest_sha256": digest}, indent=2) + "\n")
host = {"lead_machine": "Lead Machine", "content_machine": "Content Machine"}.get(meta["host"], meta["host"])
(pack / "INSTALL.md").write_text(f"""# Installing {meta['name']}

This is a machine pack for the **{host}**. It plugs into an AIOS that already has that
machine installed (foundation {meta['requires_foundation']} or newer — `cat core/FOUNDATION_VERSION`).

## 1. Put the zip anywhere

You do not unzip it. From the top of your AIOS (the folder with `CLAUDE.md` and `my/`):

```bash
.venv/bin/python scripts/add_machine.py /path/to/{meta['slug']}.zip
```

It validates the manifest, refuses if the {host} is not here, records the pack in
`licence.json`, copies it to `marketing/{meta['host']}/plugins/{pack.name}/`, writes ONE line
to `my/settings.yaml` (`enabled: false` — it ships dark), and runs the pack's own checks.
Run it twice and the second run changes nothing.

## 2. See the plan before spending anything

```bash
.venv/bin/python scripts/run_recipe.py {meta['slug']}
```

Tiles, what is already done, the worst-case requests and dollars, and every reason a live
run would be refused (a missing key, no ceiling).

## 3. Turn it on

In `my/settings.yaml`:

```yaml
machines:
  {meta['slug']}:
    enabled: true      # plans and logs; still spends nothing while dry_run is true
    dry_run: false     # spends, up to max_usd — only when the plan looks right
```

The worker sweeps it every 6 hours (`gtm_recipes` in `scripts/doctor.py`). At `max_usd` the
recipe pauses ITSELF and says so; nothing else in your AIOS stops.
""")
PYE

# ── archives: one top folder, both formats ───────────────────────────────────────────────
( cd "$STAGE" && rm -f "$OUT/$SLUG.zip" "$OUT/$SLUG.tar.gz" \
  && zip -qr -X "$OUT/$SLUG.zip" "$DIR" && COPYFILE_DISABLE=1 tar -czf "$OUT/$SLUG.tar.gz" "$DIR" )   # no ._ AppleDouble entries from macOS
rm -rf "$STAGE"
echo "  [$SLUG] $(du -h "$OUT/$SLUG.zip" | cut -f1) zip, $(du -h "$OUT/$SLUG.tar.gz" | cut -f1) tar.gz → $OUT   (built from $SHA)"
