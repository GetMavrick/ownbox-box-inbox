"""Every table reaches every box. No network, no model.

THE BUG THIS EXISTS TO PREVENT, in full, because it cost a night and looked like four other
things first:

`_migration_28` shipped creating `written_posts`. The live box ran it. Later, #459 appended
`written_metrics` to the SAME step. A migration runs ONCE per box and is then skipped forever,
so that addition reached every fresh clone and no existing box, ever.

What that looks like from outside is the nasty part. CI is green — CI is a fresh clone. Local is
green — also fresh. The box has `written_posts` and not `written_metrics`, an asymmetry that
looks impossible until you notice the two were added to one step at different times. The
performance loop raises `no such table` into an hourly warning nobody reads, `user_version`
says 32 and the code says 32, so every version check agrees the box is current. It is current.
It is also missing a table.

The governing rule at the top of the migrations block already said new tables belong in SCHEMA,
which `init_db` executescripts on EVERY boot and which therefore self-heals. Eight steps broke
that rule. The rule was a comment; this makes it a test.
"""
import os
import pathlib
import re
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AIOS_HERMETIC_TEST", "1")
_DB = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ["AIOS_DB_PATH"] = _DB

from core.config import settings  # noqa: E402
settings.db_path = _DB
from core import state  # noqa: E402

_fails = []
SRC = pathlib.Path(state.__file__).read_text()


def ok(label, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        _fails.append(label)


def _migration_tables() -> dict:
    """Tables created inside a migration step — for steps this box actually runs.

    THE SCHEMA SPLIT (2026-09-13): a step tagged with a machine runs only where that machine is
    registered, so a table it creates is not an orphan on a box that does not ship the machine —
    the step never executes there. Steps for machines this tree carries are held to the rule."""
    present = _machines_present()
    out = {}
    for m in re.finditer(r"def (_migration_(\d+))\(c\).*?(?=\ndef |\nMIGRATIONS)", SRC, re.S):
        owner = state._MIGRATION_OWNER.get(int(m.group(2)))
        if owner is not None and owner not in present:
            continue
        for t in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", m.group(0)):
            out.setdefault(t, m.group(1))
    return out


_PKG_KEY = {"lead_machine": "lead", "content_machine": "content", "customer_voice": "customer_voice"}


def _machines_present() -> set:
    """The machines this tree ships, by their schema.py — an exported box carries only its own."""
    root = pathlib.Path(state.__file__).resolve().parents[1]
    return {key for pkg, key in _PKG_KEY.items() if (root / "marketing" / pkg / "schema.py").exists()}


def _schema_tables() -> set:
    """Every table with a real definition: the kernel's SCHEMA plus each shipped machine's DDL.

    A machine declares its tables in <package>/schema.py and registers them at import (the schema
    split); a definition there is as real as one in the kernel, and `init_db` + registration
    re-create both on every boot."""
    m = re.search(r'SCHEMA\s*=\s*"""', SRC)
    body = SRC[m.end():SRC.index('"""', m.end())]
    tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", body))
    root = pathlib.Path(state.__file__).resolve().parents[1]
    for pkg in _PKG_KEY:
        f = root / "marketing" / pkg / "schema.py"
        if f.exists():
            tables |= set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", f.read_text()))
    return tables


def test_no_table_exists_only_inside_a_migration():
    """THE ONE THAT WOULD HAVE CAUGHT #459.

    A migration is allowed to create a table — that is how it reaches boxes mid-life. It is NOT
    allowed to be the table's only definition, because the step is skipped forever afterwards
    and any later edit to it is a no-op on every box that already ran it."""
    schema, mig = _schema_tables(), _migration_tables()
    orphans = {t: s for t, s in mig.items() if t not in schema}
    for t, s in sorted(orphans.items()):
        ok(f"{t} (created in {s}) is ALSO in SCHEMA, so a restart heals it", False)
    ok("no table is defined only inside a migration", not orphans)
    # #459's two tables are the Content Machine's; they are expected only where that machine ships.
    if "content" in _machines_present():
        ok("and the ones that bit us (#459) are covered", {"written_posts", "written_metrics"} <= schema)
    else:
        ok("this image does not carry the Content Machine, so #459's tables are rightly absent",
           not ({"written_posts", "written_metrics"} & schema))


def test_init_db_reruns_SCHEMA_on_every_boot():
    """The whole self-healing property rests on this one line. If `init_db` ever gates the
    executescript behind a version check, every guarantee above quietly evaporates."""
    body = SRC[SRC.index("def init_db()"):][:900]
    ok("init_db executescripts SCHEMA unconditionally", "executescript(SCHEMA)" in body)
    ok("…and every registered machine's DDL with it", "executescript(_SCHEMAS[m])" in body)
    ok("…before running migrations, so a new table exists for a step to ALTER",
       body.index("executescript(SCHEMA)") < body.index("_run_migrations("))
    ok("a machine registered AFTER init_db is applied at once, not at the next boot",
       "if ready:" in SRC[SRC.index("def register_schema("):][:800] and "_apply_machine(machine)" in SRC)


def test_the_live_box_state_heals_on_a_plain_restart():
    """Reproduces the box exactly — `written_posts` present, `written_metrics` absent,
    user_version pinned at 32 — and asserts a restart is enough. No version bump, no new
    migration, no hand-run SQL on a production database."""
    # THE SCHEMA SPLIT: written_metrics is the Content Machine's, declared in its schema.py and
    # registered at import — so on a box that ships that machine we register it here the way the
    # worker's module load would, and the heal is the same. On an image without it, the same
    # property is proven on a kernel table; a healed table is a healed table.
    if "content" in _machines_present():
        # Loaded from the file, not imported: this suite ships to every image, and the exporter
        # drops a suite whose static imports name a machine the image does not carry.
        root = pathlib.Path(state.__file__).resolve().parents[1]
        ns = {}
        exec(compile((root / "marketing" / "content_machine" / "schema.py").read_text(),
                     "content_machine/schema.py", "exec"), ns)
        state.register_schema("content", ns["DDL"])
        present, missing = "written_posts", "written_metrics"
    else:
        present, missing = "heartbeats", "alert_state"
    state.init_db()
    with sqlite3.connect(_DB) as c:
        c.execute(f"DROP TABLE IF EXISTS {missing}")
        c.execute("PRAGMA user_version = 32")

    def has(t):
        with sqlite3.connect(_DB) as c:
            return bool(c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone())

    ok(f"reproduced: the box has {present} and not {missing}",
       has(present) and not has(missing))
    state.init_db()
    ok("a plain restart creates the missing table", has(missing))
    with sqlite3.connect(_DB) as c:
        ok("…without needing a version bump — the fix is not a migration",
           c.execute("PRAGMA user_version").fetchone()[0] == state.SCHEMA_VERSION)


def test_every_table_the_code_queries_actually_exists():
    """The general case. A table named in a query but absent from the schema is the same defect
    wearing a different hat, and it fails at runtime inside a warning rather than at CI.

    THE SCHEMA SPLIT MADE THIS OWNERSHIP-AWARE (2026-09-13), and in doing so it found two real
    leaks: core/compliance.py wrote the Lead Machine's suppression table (now kernel — an opt-out
    is every channel's), and marketing/content_machine/report.py still counted the inbox table the
    Stage B move left behind. The rules, so the next leak fails here rather than on a buyer's box:

      · a table queried by a machine's own package must exist where that package ships;
      · a table queried from core/ must exist on EVERY image — unless the file is one of the two
        that reach into machine tables BY DESIGN, named below. A third file doing it is a finding.
    """
    # Register every machine this tree ships, the way the worker's module load would, so the check
    # measures what a box's database actually holds after boot.
    present = _machines_present()
    root = pathlib.Path(state.__file__).parent.parent
    for pkg, key in _PKG_KEY.items():
        if key in present:
            ns = {}
            exec(compile((root / "marketing" / pkg / "schema.py").read_text(), str(pkg), "exec"), ns)
            state.register_schema(key, ns["DDL"])
    state.init_db()
    with sqlite3.connect(_DB) as c:
        live = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    kernel = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)",
                            SRC[re.search(r'SCHEMA\s*=\s*"""', SRC).end():]
                            .split('"""', 1)[0]))
    # core/ files allowed to name a machine's table, and why. Both degrade correctly where the
    # machine is absent: the watchdog's probes are try/except by design and report the machine as
    # not present; compliance guards its one lead-row update on the table existing.
    core_by_design = {"core/watchdog.py", "core/compliance.py"}
    prefixes = ("written_", "gtm_", "newsletter_", "reel_", "leadmagnet_", "inbox_", "prospect",
                "voice_", "waalaxy_", "mev_", "heygen_", "airtable_", "watch_", "investor", "company_", "email_format")
    missing, leaks = [], []
    for f in list(root.glob("marketing/**/*.py")) + list(root.glob("core/*.py")):
        rel = str(f.relative_to(root))
        if rel == "core/state.py":
            continue
        for m in re.finditer(r"(?:FROM|INTO|UPDATE)\s+([a-z_][a-z0-9_]*)", f.read_text()):
            t = m.group(1)
            if not t.startswith(prefixes) or t in live:
                continue
            if rel.startswith("core/"):
                if t in kernel:
                    missing.append(f"{rel}: {t}")          # a kernel table that init_db did not create
                elif rel not in core_by_design:
                    leaks.append(f"{rel}: {t}")            # core reaching into a machine it may not have
                continue
            missing.append(f"{rel}: {t}")                  # a machine querying a table it does not create
    for x in sorted(set(missing)):
        ok(f"queried table exists after init_db — {x}", False)
    for x in sorted(set(leaks)):
        ok(f"core/ does not reach into a machine's table outside the two files that do so by design — {x}", False)
    ok("every table the shipped code queries exists after a plain init_db", not missing)
    ok("no core/ file outside watchdog and compliance names a machine table", not leaks)


if __name__ == "__main__":
    print("SCHEMA INTEGRITY — every table reaches every box")
    test_no_table_exists_only_inside_a_migration()
    test_init_db_reruns_SCHEMA_on_every_boot()
    test_the_live_box_state_heals_on_a_plain_restart()
    test_every_table_the_code_queries_actually_exists()
    if _fails:
        print(f"\n{len(_fails)} FAILED: " + ", ".join(_fails))
        sys.exit(1)
    print("\nALL SCHEMA INTEGRITY TESTS PASS")
