"""A test that a test suite actually RUNS its tests.

WHY THIS EXISTS (2026-08-24). Eleven tests across three suites were dead: written, reviewed,
committed, and never once executed. Two of them could not have run at all — one called a
`_reset()` helper that does not exist in its file, the other imported `core.vendors`, a module
that has never existed. They sat green for weeks because a suite here is a plain script with a
hand-maintained call list at the bottom, and a function nobody adds to that list is invisible.
It costs nothing to define a test and forget to register it, and the file still exits 0.

That is the same defect as the bug being fixed the day this was written — a check that reads
correctly and never runs — and it is worse in a test file, because a dead test is indistinguish-
able from a passing one. The whole value of the suite is the claim "these properties hold", and
an unregistered test quietly withdraws one of those claims while still looking like it makes it.

One of the eleven, once switched on, immediately found a real defect: `businesses_store.upsert`
replaces the whole record, so a partial write blanks every omitted column.

The check is intentionally dumb — a name defined and never mentioned again in the same file.
It cannot know whether a registered test is MEANINGFUL, only that it is reachable.
"""
import ast
import io
import pathlib
import re
import sys
import tokenize

ROOT = pathlib.Path(__file__).resolve().parent
_fails = []


def ok(name, cond):
    if cond:
        print(f"  ok   {name}")
    else:
        _fails.append(name)
        print(f"  FAIL {name}")


def orphans_in(path: pathlib.Path) -> list:
    """test_* functions defined in this file and never referenced anywhere in it."""
    src = path.read_text()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []                      # a syntax error is the other suites' problem, not ours
    defined = {n.name for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}
    if not defined:
        return []
    # A suite that sweeps its own namespace runs everything by construction; exempt it rather
    # than pretend to model reflection.
    if "globals()" in src or "getmembers" in src:
        return []
    # ANY reference counts, not just a call: several suites pass function OBJECTS in a tuple
    # and loop over them (`for fn in (test_a, test_b): fn()`). Counting only ast.Call reported
    # six false positives on the first run of this scan, which is exactly the kind of noisy
    # check that gets muted along with its real findings.
    referenced = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    referenced |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    return sorted(defined - referenced)


def first_real_import(src: str):
    """Line of this file's OWN first core/marketing import, or None.

    AST, not regex, and the reason is a false positive that reddened main on Sep 3.
    test_box_boots.py builds a subprocess program as a STRING containing
    `from core import state`; a line scan counted that string's line as an import by this
    file and demanded a redirect before it. The import runs in a different process with a
    scrubbed env, so there was nothing to redirect. A string literal is not an import, and
    only the parser can tell the difference.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [node.module or ""]
        if any(m == "core" or m == "marketing" or m.startswith(("core.", "marketing.")) for m in mods):
            return node.lineno
    return None


def db_guard_defect(path: pathlib.Path):
    """A suite that touches the DB must point AIOS_DB_PATH away from the live box BEFORE
    its first core/marketing import, or the guard is theater.

    WHY (2026-09-01). settings.db_path is frozen the moment core.config imports - an env
    var set on a later line changes nothing. test_gtm_reply_ingest set its path six lines
    too late, and when the full battery ran locally its DELETE FROM gtm_leads executed
    against the LIVE aios.db and erased the entire 12,469-row ore body. In CI the DB is
    empty so nothing ever looked wrong. Line order is the whole defect, so line order is
    what this checks.
    """
    src = path.read_text()
    if "state.connect" not in src and "init_db" not in src:
        return None                    # never touches the DB; nothing to guard
    lines = src.splitlines()
    imp = env = None
    for i, l in enumerate(lines, 1):
        if env is None and "AIOS_DB_PATH" in l and "environ" in l:
            env = i
    imp = first_real_import(src)
    if imp is None:
        return None
    if env is None:
        return "touches the DB but never sets AIOS_DB_PATH"
    if env > imp:
        return f"sets AIOS_DB_PATH on line {env}, after core/marketing import on line {imp} (too late: db_path froze at import)"
    return None


def test_no_suite_can_open_the_live_db():
    suites = sorted(ROOT.glob("test_*.py")) + [ROOT / "smoke_test.py"]
    bad = {p.name: d for p in suites if p.exists() for d in [db_guard_defect(p)] if d}
    for name, d in sorted(bad.items()):
        print(f"       {name}: {d}")
    ok("every DB-touching suite redirects AIOS_DB_PATH before importing core", not bad)


def test_the_db_guard_can_actually_SEE_the_defect():
    import tempfile
    d = pathlib.Path(tempfile.mkdtemp())
    good = d / "test_good.py"
    good.write_text("import os, tempfile\nos.environ['AIOS_DB_PATH'] = tempfile.mkdtemp() + '/t.db'\n"
                    "from core import state\nstate.init_db()\n")
    late = d / "test_late.py"
    late.write_text("import os\nfrom core import state\n"
                    "os.environ.setdefault('AIOS_DB_PATH', ':memory:')\nstate.init_db()\n")
    never = d / "test_never.py"
    never.write_text("from core import state\nstate.connect()\n")
    clean = d / "test_clean.py"
    clean.write_text("from core import config\nprint('no db')\n")
    ok("env before import passes", db_guard_defect(good) is None)
    ok("env after import is flagged", db_guard_defect(late) is not None)
    ok("no env at all is flagged", db_guard_defect(never) is not None)
    ok("a suite that never touches the DB is left alone", db_guard_defect(clean) is None)
    instr = d / "test_instr.py"
    instr.write_text("import subprocess, sys\n"
                     "prog = 'from core import state\\nstate.init_db()\\n'\n"
                     "env = {'AIOS_DB_PATH': '/tmp/box.db'}\n"
                     "subprocess.run([sys.executable, '-c', prog], env=env)\n")
    ok("an import inside a string is not this file's import", db_guard_defect(instr) is None)
    ok("and the AST finds a real import that a string precedes",
       first_real_import("x = 'from core import state'\nfrom core import config\n") == 2)


def test_every_test_in_every_suite_is_actually_reachable():
    suites = sorted(ROOT.glob("test_*.py"))
    # a floor, not the repo's count: the same file ships in every box (the Lead box carries 49)
    ok(f"found the suites to scan ({len(suites)})", len(suites) >= 10)
    dead = {p.name: o for p in suites for o in [orphans_in(p)] if o}
    for name, o in sorted(dead.items()):
        print(f"       {name}: {', '.join(o)}")
    ok("no suite defines a test it never runs", not dead)


def test_the_scan_can_actually_SEE_an_orphan():
    """A guard that cannot fail is not a guard. Prove it catches one before trusting it."""
    import tempfile
    d = pathlib.Path(tempfile.mkdtemp())
    good = d / "test_good.py"
    good.write_text("def test_a():\n    pass\n\nif __name__ == '__main__':\n    test_a()\n")
    bad = d / "test_bad.py"
    bad.write_text("def test_a():\n    pass\n\n\ndef test_orphan():\n    pass\n\n"
                   "if __name__ == '__main__':\n    test_a()\n")
    byref = d / "test_byref.py"
    byref.write_text("def test_a():\n    pass\n\nfor fn in (test_a,):\n    fn()\n")
    ok("a registered test is not flagged", orphans_in(good) == [])
    ok("an UNregistered test IS flagged", orphans_in(bad) == ["test_orphan"])
    ok("a test passed by reference is not a false positive", orphans_in(byref) == [])


def _drives_exporter(path: pathlib.Path) -> bool:
    """True when this suite RUNS scripts/export_box.sh, not when it merely mentions it.

    WHY THIS IS NOT A SUBSTRING MATCH ANY MORE. It was, and on 2026-09-15 it failed
    test_demo_first_run.py in CI over a `#` comment explaining why the exporter leaves a script
    out of an inbox box. The remedy the message offered — add it to the skip set — would have
    been the wrong one: that suite SHOULD ship to buyer boxes (it is a real box check, and the
    same PR is what makes it pass inside one), so obeying the guard would have deleted it from
    every sold box to satisfy a comment.

    A guard that fires on prose teaches people to stop writing prose. Comments AND DOCSTRINGS are
    stripped here; every string the code actually uses — literals, f-strings, any code path — still
    counts. A real driver names the script inside a string it hands to a subprocess, and that is
    untouched by this.

    DOCSTRINGS USED TO COUNT, "to stay conservative", and they false-positived twice on 2026-09-16:
    test_core_boundary and test_setup_screen_on_the_seam each EXPLAINED the exporter in a docstring,
    and each was told to join the skip set — which would have removed a suite that must ship from
    every box. A docstring cannot run a subprocess, so dropping it loses no true positive; it only
    stops punishing the files that document why they exist.
    """
    try:
        src = path.read_text()
    except OSError:
        return False
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return "export_box.sh" in src   # unparseable: fall back to the old, stricter behaviour
    # Blank every docstring — module, class and function — then read back the code alone:
    # ast.unparse never emits comments, so what remains is exactly what can run.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                first.value.value = ""
    return "export_box.sh" in ast.unparse(tree)


# A file whose ONLY reference to the exporter is a comment, written out so the guard above can
# prove it does not flag one. It is not a suite and never runs; it exists to be read.
_TOKENIZE_PROBE = pathlib.Path(__file__).resolve().parent / "fixtures" / "mentions_exporter.py"
# ...and one whose only reference is a DOCSTRING, the shape that false-positived twice in one day.
_DOCSTRING_PROBE = pathlib.Path(__file__).resolve().parent / "fixtures" / "mentions_exporter_in_docstring.py"
# ...and one that genuinely RUNS it, so stripping prose is proven not to blind the guard.
_DRIVER_PROBE = pathlib.Path(__file__).resolve().parent / "fixtures" / "drives_exporter.py"


def test_every_export_driving_suite_is_kept_home():
    """A suite that runs scripts/export_box.sh must be in the exporter's skip set, or it ships
    into every buyer box and fails there on a script that never ships. This happened THREE
    times in one night (test_add_machine, test_recipe_ships, test_bootstrap_delegates) — each
    caught by test_recipe_ships in CI, each fixed with one more skip-list line, each written by
    someone who knew the rule. A rule people know and still miss three times is not a rule; it
    is a guard that has not been written yet."""
    exporter = ROOT.parent / "scripts" / "export_box.sh"
    if not exporter.exists():
        # INSIDE A BUYER BOX. This suite ships (it is a real box check) but the exporter never
        # does — so there is nothing here to enforce, and the first version of this guard
        # crashed every box's own test run with FileNotFoundError, which is the exact defect
        # class it exists to prevent. A guard must know where it is standing.
        ok("export-driving guard: not in a repo checkout, nothing to enforce here", True)
        return
    skip_src = exporter.read_text()
    drivers = sorted(p.name for p in ROOT.glob("test_*.py")
                     if _drives_exporter(p) and p.name != "test_suite_integrity.py")
    ok("found the export-driving suites (none = the glob is wrong)", bool(drivers))
    missing = [d for d in drivers if f'"{d}"' not in skip_src]
    ok(f"every suite that drives export_box.sh is in the exporter's skip set"
       + (f" — ADD TO skip IN scripts/export_box.sh: {missing}" if missing else ""), not missing)
    # the guard must be able to see: a driver that IS skip-listed must not be reported
    ok("the check can see a skip-listed driver", "test_recipe_ships.py" in drivers and
       '"test_recipe_ships.py"' in skip_src)
    # …and must NOT see a suite that only talks about the exporter. See _drives_exporter.
    ok("a suite that only mentions the exporter in a comment is not a driver",
       _TOKENIZE_PROBE.exists() and not _drives_exporter(_TOKENIZE_PROBE))
    ok("...nor one that only EXPLAINS it in a docstring — the shape that false-positived twice",
       _DOCSTRING_PROBE.exists() and not _drives_exporter(_DOCSTRING_PROBE))
    ok("...while a suite that RUNS it is still a driver, so stripping prose did not blind the guard",
       _DRIVER_PROBE.exists() and _drives_exporter(_DRIVER_PROBE))


def test_every_suite_is_actually_run_by_ci():
    """A suite file that CI never invokes is the same defect as an unregistered test function,
    one level up — and this file's own guard could not see it, because that one reads a suite's
    `main()` and this one is about whether anything reads the suite at all.

    Found the honest way (2026-09-05): two suites were added in a PR, CI went green on all four
    required checks, and neither suite had run once. `.github/workflows/tests.yml` names every
    suite by hand in one `for t in ...` loop, so a file nobody adds to that line is invisible in
    exactly the way eleven dead tests were invisible in August.

    KNOWN_UNRUN is a list that may shrink and must not grow. Adding a name to it is a decision
    somebody writes down; the default for a new suite is that CI runs it."""
    wf = ROOT.parent / ".github" / "workflows" / "tests.yml"
    if not wf.exists():
        # INSIDE A BUYER BOX: this suite ships, our workflow never does. Same rule as the
        # export-driving guard above — a guard must know where it is standing.
        ok("CI-coverage guard: not in a repo checkout, nothing to enforce here", True)
        return
    # Named, with the reason, because "it is not in CI" is a claim that rots silently.
    KNOWN_UNRUN = {
        "test_dialogue_pipeline": "asserts the CODE default photo engine while reading the "
                                  "configured one, so it passes in the repo only by test order "
                                  "(already named in the exporter's skip set for the same reason)",
    }
    src = wf.read_text()
    names = sorted([p.stem for p in ROOT.glob("test_*.py")] + ["smoke_test"])
    ok("found the suites (none = the glob is wrong)", len(names) > 50)
    missing = [n for n in names if not re.search(rf"\b{re.escape(n)}\b", src)
               and n not in KNOWN_UNRUN]
    ok("every suite is named in .github/workflows/tests.yml"
       + (f" — ADD TO THE `for t in ...` LOOP: {missing}" if missing else ""), not missing)
    # The guard must be able to see one. A name that cannot possibly be in the workflow must
    # be reported, or this check is a passing check that cannot fail.
    fake = "test_a_suite_that_does_not_exist_anywhere"
    ok("the check can see an unrun suite", not re.search(rf"\b{fake}\b", src))
    stale = [n for n in KNOWN_UNRUN if n not in names]
    ok("KNOWN_UNRUN names no suite that has since been deleted"
       + (f" — REMOVE: {stale}" if stale else ""), not stale)
    now_run = [n for n in KNOWN_UNRUN if re.search(rf"\b{re.escape(n)}\b", src)]
    ok("a suite added to CI is removed from KNOWN_UNRUN"
       + (f" — REMOVE: {now_run}" if now_run else ""), not now_run)


# ─── a shipped suite may not read .github/ unguarded ─────────────────────────────────────────
def _unguarded_github_reads(src: str) -> list[str]:
    """Scopes (function names, or <module>) that name a `.github` path in CODE — never a docstring —
    with no existence check and no FileNotFoundError/OSError handler anywhere in that same scope.

    Deliberately narrow. A broad rule over docs/, sites/ and machine paths was measured on
    2026-09-16 and flagged seven shipped suites, three of which pass inside the very box that ships
    them — a guard that pages on noise is a guard people learn to skip. `.github` is the one that
    RECURRED: eight suites in one day read .github/workflows/tests.yml to check they were listed in
    CI, and every one crashed with FileNotFoundError inside a buyer's box, because a box has no
    repository. Found by hand each time, and once only after a PR had merged."""
    tree = ast.parse(src)
    docs = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.body:
            f = n.body[0]
            if isinstance(f, ast.Expr) and isinstance(f.value, ast.Constant) and isinstance(f.value.value, str):
                docs.add(id(f.value))

    def guarded(scope) -> bool:
        for n in ast.walk(scope):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in (
                    "exists", "is_dir", "is_file"):
                return True
            if isinstance(n, ast.Try) and any(
                    h.type is None or re.search(r"FileNotFoundError|OSError|Exception", ast.unparse(h.type))
                    for h in n.handlers):
                return True
        return False

    owner = {}
    for scope in [tree] + [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for n in ast.walk(scope):
            if (isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs
                    and re.search(r"(^|/)\.github(/|$)", n.value)):
                owner[id(n)] = scope                      # an inner function overwrites the module
    return sorted({getattr(sc, "name", "<module>") for sc in owner.values() if not guarded(sc)})


_GITHUB_BAD = pathlib.Path(__file__).resolve().parent / "fixtures" / "reads_github_unguarded.py"
_GITHUB_OK = pathlib.Path(__file__).resolve().parent / "fixtures" / "reads_github_guarded.py"


def test_no_shipped_suite_reads_github_unguarded():
    """EIGHT IN ONE DAY. A suite that ships into a buyer's box and reads .github/ crashes there,
    and CI cannot see it — test_recipe_ships runs the shipped suites in a LEAD box only, while
    every one of the eight shipped to a Customer Voice box. Caught here at write time instead."""
    print("\n— a shipped suite may not read .github/ without checking it is the repo —")
    ok("the check SEES an unguarded read (so a pass below means something)",
       _GITHUB_BAD.exists() and _unguarded_github_reads(_GITHUB_BAD.read_text()) == ["test_listed_in_ci"])
    ok("...and accepts one guarded on the directory existing",
       _GITHUB_OK.exists() and _unguarded_github_reads(_GITHUB_OK.read_text()) == [])
    exporter = ROOT.parent / "scripts" / "export_box.sh"
    if not exporter.exists():
        ok("not in a repo checkout, nothing to enforce here", True)
        return
    skip_src = exporter.read_text()
    found = {}
    for path in sorted(ROOT.glob("test_*.py")):
        if f'"{path.name}"' in skip_src or path.name == "test_suite_integrity.py":
            continue                                       # kept home: never reaches a box
        try:
            bad = _unguarded_github_reads(path.read_text())
        except SyntaxError:
            continue
        if bad:
            found[path.name] = bad
    ok("no shipped suite reads .github/ unguarded — a box has no repository"
       + (f" — GUARD ON (ROOT / '.github').is_dir(): {found}" if found else ""), not found)


def main():
    test_the_scan_can_actually_SEE_an_orphan()
    test_every_test_in_every_suite_is_actually_reachable()
    test_every_export_driving_suite_is_kept_home()
    test_no_shipped_suite_reads_github_unguarded()
    test_every_suite_is_actually_run_by_ci()
    test_the_db_guard_can_actually_SEE_the_defect()
    test_no_suite_can_open_the_live_db()
    print(f"\n{'ALL SUITE-INTEGRITY TESTS PASS' if not _fails else str(len(_fails)) + ' FAILED'}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
