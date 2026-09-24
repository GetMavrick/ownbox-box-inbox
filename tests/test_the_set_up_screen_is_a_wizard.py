"""THE SET-UP SCREEN SAYS HOW FAR IN YOU ARE, AND GETS FINISHED STEPS OUT OF THE WAY.

Owner, 2026-09-21, asking for a wizard: a progress bar across the top, per-step feedback, and
readability. OSDev1 named the sharpest part of it — a connected step still printed all four of
its Google instructions, so a buyer three-quarters done read a wall of errands he had already
run, with the one he still owed somewhere inside it.

WHAT THIS SUITE WILL NOT ASSERT, DELIBERATELY: what a step is connected TO. `zernio_state()`
returns the key and its status and makes no network call, so step 2 can truthfully say
"Connected" and cannot say connected to which account. A test that expected an account name here
would be asking the screen to invent one. When `accounts.discover` lands, that assertion belongs
with it.
"""
import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB = pathlib.Path(tempfile.mkdtemp(prefix="setup-wizard-")) / "box.db"
# BEFORE ANY core IMPORT — core.config.Settings reads os.environ at class-body time.
os.environ["AIOS_DB_PATH"] = str(_DB)
os.environ.setdefault("DASH_TOKEN", "test-dash-pw")

from core import state                               # noqa: E402

state.init_db()

from core.config import settings                     # noqa: E402
from core.dispatch import app                        # noqa: E402
from marketing.customer_voice import app as _app     # noqa: E402

_failed = 0


def ok(what: str, cond: bool, got: str = "") -> None:
    global _failed
    if cond:
        print(f"  ok   {what}")
    else:
        _failed += 1
        print(f"  FAIL {what}" + (f"  — {got}" if got else ""))


def _client():
    c = app.test_client()
    c.post("/dash/login", data={"token": settings.dash_token})
    return c


_REAL = _app._setup_source
_TOTAL = len(_REAL())


def _with_connected(n: int) -> list[dict]:
    """The real step list, with the first `n` reported connected and nothing invented."""
    out = []
    for i, e in enumerate(_REAL()):
        e = dict(e)
        e["status"] = "connected" if i < n else "not_connected"
        e["who"] = ""
        out.append(e)
    return out


def _page(n: int) -> str:
    _app._setup_source = (lambda k=n: _with_connected(k))
    try:
        return _client().get("/inbox/setup").get_data(as_text=True)
    finally:
        _app._setup_source = _REAL


# ── the bar ───────────────────────────────────────────────────────────────────────────────────
print("test_the_screen_says_how_far_in_you_are")
for _n in (0, 2, _TOTAL):
    _h = _page(_n)
    _segs = len(re.findall(r'<span class="seg[ "]', _h))
    _on = len(re.findall(r'class="seg on"', _h))
    ok(f"{_n} connected: one segment per step", _segs == _TOTAL, str(_segs))
    ok(f"{_n} connected: filled exactly as many as are connected", _on == _n, str(_on))
    # THE SENTENCE IS THE CONTENT AND THE BAR IS THE PICTURE. A screen reader gets this line and
    # is spared the four spans, which is why the bar is aria-hidden.
    ok(f"{_n} connected: and says it in words", f"{_n} of {_TOTAL} connected." in _h)

ok("the bar is hidden from a screen reader, because the sentence already carries it",
   re.search(r'<div class="wiz-bar" aria-hidden="true">', _page(0)) is not None)


# ── a finished step is a done row that still opens its home ────────────────────────────────────
print("\ntest_a_connected_step_reads_done_and_still_opens")
# SET-UP IS A GUIDE NOW (owner, 2026-09-24; docs/SCOPE_ONE_PLACE_PER_SETTING.md): no step recites
# instructions here at all, so nothing needs folding. A connected step is a row marked done; it
# still links to its one home, and names the way back in.
for _n in (0, 2, _TOTAL):
    _body = _page(_n).split("</style>", 1)[-1]      # past the inlined stylesheet
    ok(f"{_n} connected: {_n} rows marked done", _body.count('class="grow done"') == _n,
       str(_body.count('class="grow done"')))
    ok(f"{_n} connected: {_TOTAL - _n} still open",
       _body.count('class="grow"') == _TOTAL - _n, str(_body.count('class="grow"')))
_done_page = _page(_TOTAL)
ok("a done row still opens its home", "from=setup" in _done_page)
ok("...and names the way back into it", "Change" in _done_page)


# ── the class collision that cost a render ────────────────────────────────────────────────────
print("\ntest_the_wizard_does_not_wear_the_instruction_grids_class")
# `.step` was already taken: a two-column `auto 1fr` grid for the numbered instruction rows. The
# first version of this screen styled its sections as `.step`, which laid every OPEN step out in
# that grid and wrapped its copy into a column seven words wide with a dead gutter beside it.
# Valid CSS, correct grid, wrong element — invisible in a diff and obvious in a render.
ok("the instruction grid still owns .step", ".step{display:grid" in _app.CSS)
ok("...and the wizard uses its own name", ".wstep" in _app.CSS)
ok("...so no section is handed the grid", 'class="step"' not in _page(0))
ok("...and the guide rows use their own name too", ".grow{" in _app.CSS)


# ── CI runs this file ─────────────────────────────────────────────────────────────────────────
print("\ntest_ci_actually_runs_this_file")
# GUARDED: this suite ships with the machine, and a buyer's box is not a repository.
_here = pathlib.Path(__file__).resolve().parents[1]
if not (_here / ".github").is_dir():
    print("  --   not the repo — a buyer's box has no CI manifest to be named in")
else:
    _wf = _here / ".github/workflows/tests.yml"
    ok("registered in the suite list",
       "test_the_set_up_screen_is_a_wizard" in _wf.read_text())
    # AND THE FILE STILL PARSES. Registering this suite is how I learned that nothing checked:
    # I inserted the name with two spaces of indent where the list uses twelve, which broke the
    # YAML block outright. GitHub could not start the workflow at all -- two runs "failed" in
    # zero seconds, which reads like a test failure and is not one -- and test_suite_integrity
    # went green throughout, because it checks the list's SHAPE and never asks whether the
    # document is loadable. A malformed line here silently stops every suite in the repo from
    # running, which is worse than any single red one.
    #
    # Imported inside the guard, not at the top: PyYAML is a developer dependency and a buyer's
    # box has neither this file nor a reason to carry the library.
    import yaml                                     # noqa: PLC0415 — repo-only, see above
    try:
        yaml.safe_load(_wf.read_text())
        _loads = True
    except Exception as _e:                         # noqa: BLE001 — the reason is the message
        _loads, _why = False, f"{type(_e).__name__}: {_e}"
    ok("...and the workflow file is still valid YAML", _loads,
       "" if _loads else _why[:160])


print(f"\n{'FAILED — ' + str(_failed) + ' failure(s)' if _failed else 'all checks passed'}")
sys.exit(1 if _failed else 0)
