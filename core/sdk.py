"""The box's promise to anyone building their own machine: `from core import sdk` (sdk: 1).

docs/SCOPE_MACHINE_MARKETPLACE.md §9 (owner-approved 2026-09-29, SDK v0). A company that builds a
machine for itself, in `my/machines/`, imports THIS and nothing else from the box. Everything here is
promised: it keeps working, with the same names and arguments, for every box on `sdk: 1`, and if an
update breaks it that is our bug. Everything else in `core/` is ours to change in any update, and
`python scripts/ownbox.py check` says so the moment a machine reaches for it.

VERSIONED ON ITS OWN. `VERSION` moves only when a promise here changes, never with the foundation
(core/FOUNDATION_VERSION), which moves whenever the box grows. A machine says `sdk: 1` in its
machine.yaml; a box whose facade is older than that refuses the machine by name instead of letting it
half-start (core/custom_machines.py).

WHY THIN WRAPPERS, NOT RE-EXPORTS. A re-export promises the whole signature of the thing behind it,
including every keyword we add next month. Each function here takes exactly the arguments we promise
and passes the rest itself, so the seam behind it can grow without a company's machine noticing.

WHAT IS PROMISED (§9.2), AND WHAT IS NOT:
  * reasoning        m.think(task, prompt): the only way to use AI, on this box's account and under
                     its monthly spend ceiling (core.brain.think)
  * the morning page m.reporter(title, fn)
  * tools            m.tool(name, fn=, description=, capability=, args=): a question this box can
                     answer for the owner's AI (core.connector.tools). Coworkers need no code: a
                     machine ships them as files, `coworkers/<name>/coworker.yaml` (coworker: 1).
  * the menu         m.menu(key, title=, href=): only those three; the menu's look is ours
  * screens          m.screen(path): a signed-in page on the box's own chrome, and STYLE_CLASSES.
                     Signed-in is the default: a screen nobody gates is a page the internet can read.
  * small state      m.setting / m.save_setting (core.box_settings), and m.data_dir() for the
                     machine's own SQLite file inside its own folder
  * the manifest     machine.yaml: name, version, requires_foundation, needs:, sdk
  NOT promised: worker.register / register_periodic (schedules go through Coworkers and Shifts until
  the worker model settles), state.register_schema (a company's tables would block our migrations),
  and any other `core.*` import.
"""
from __future__ import annotations

import functools
import pathlib
import re

VERSION = 1

# The manifest fields a machine may rely on, and the seams above by name. `scripts/ownbox.py check`
# and core/machine_breaks.py read these, so there is one list of what we promised.
MANIFEST_FIELDS = ("name", "version", "requires_foundation", "needs", "sdk")
SEAMS = ("machine", "think", "reporter", "tool", "menu", "screen", "page", "setting",
         "save_setting", "data_dir", "STYLE_CLASSES", "VERSION")

# The style classes a screen may use. Our markup changes; these names keep their meaning.
STYLE_CLASSES = ("card", "quiet", "addr", "consent")

_SLUG = re.compile(r"^[a-z][a-z0-9-]{1,40}$")


def _key(slug: str) -> str:
    """The registries' key form of a machine's slug: `job-tracker` → `job_tracker`."""
    return slug.replace("-", "_")


def page(path: str, *, title: str, lede: str, body: str) -> str:
    """The box's own page around `body`: its menu, a title, one plain sentence, then your cards."""
    from core.dash.home import chrome
    return chrome(path, title=title, lede=lede, body=body)


class Machine:
    """One machine's handle on the box. Get it once, at the top of `__init__.py`:

        from core import sdk
        m = sdk.machine("job-tracker")      # the folder's own name
    """

    def __init__(self, slug: str):
        if not _SLUG.match(str(slug or "")):
            raise ValueError(f"machine name {slug!r} must be lowercase letters, digits and hyphens, "
                             f"and the same as its folder's name")
        self.slug = slug
        self.key = _key(slug)
        from flask import Blueprint
        # The box mounts a package's `blueprint`; `__init__.py` sets `blueprint = m.blueprint`.
        self.blueprint = Blueprint(self.key, f"my_machines.{self.key}")

    # ── reasoning ─────────────────────────────────────────────────────────────────────────────
    def think(self, task: str, prompt: str, *, system: str | None = None,
              max_tokens: int = 1024) -> str:
        """Ask the box's AI. Metered on this box's account and refused past its monthly ceiling, so
        it can raise when the box is out of budget: catch it where a page must still answer."""
        from core import brain
        return brain.think(task, prompt, system=system, max_tokens=max_tokens, machine=self.key)

    # ── the morning page ──────────────────────────────────────────────────────────────────────
    def reporter(self, title: str, fn) -> None:
        """`fn(day)` returns this machine's part of the owner's Morning Review for that day."""
        from core import report
        report.register_reporter(self.key, title, fn)

    # ── tools for the owner's AI ──────────────────────────────────────────────────────────────
    def tool(self, name: str, *, fn, description: str, capability: str,
             args: dict | None = None) -> None:
        """One question the owner's AI may ask this box. `capability` is `read:<noun>` for a
        question that changes nothing; the box decides who may ask it."""
        from core.connector import tools
        tools.register(name, fn=fn, description=description, machine=self.key,
                       capability=capability, args=args)

    # ── the menu ──────────────────────────────────────────────────────────────────────────────
    def menu(self, key: str, *, title: str, href: str) -> None:
        """One row in the box's menu. Where it sits and how it looks are the box's to decide."""
        from core import shell
        shell.register_section(key, order=90, machine=self.key, title=title, href=href)

    # ── screens ───────────────────────────────────────────────────────────────────────────────
    def screen(self, path: str, *, owner_only: bool = False, methods=("GET",)):
        """A page of this machine, behind the box's own sign-in. Return `sdk.page(...)` from it.

            @m.screen("/job-tracker")
            def home():
                return sdk.page("/job-tracker", title="Jobs", lede="...", body='<div class="card">…</div>')
        """
        def wrap(fn):
            @functools.wraps(fn)
            def gated(*a, **kw):
                from core.dash import review
                refuse = review._admit(owner_only=owner_only)
                return refuse if refuse is not None else fn(*a, **kw)
            self.blueprint.add_url_rule(path, endpoint=fn.__name__, view_func=gated,
                                        methods=list(methods))
            return gated
        return wrap

    # ── small state ───────────────────────────────────────────────────────────────────────────
    def setting(self, key: str, default=None):
        """A small value this machine saved (a choice, a list, a date). Never raises."""
        from core import box_settings
        return box_settings.get(self.key, key, default=default)

    def save_setting(self, key: str, value, *, by: str = "") -> None:
        from core import box_settings
        box_settings.put(self.key, key, value, set_by=by or self.slug)

    def data_dir(self) -> pathlib.Path:
        """A folder of this machine's own, for its own SQLite file or anything larger than a setting.
        Inside its folder, so the machine stays one folder that can be copied to another box."""
        from core import custom_machines
        d = custom_machines.machines_dir() / self.slug / "data"
        d.mkdir(parents=True, exist_ok=True)
        return d


def machine(slug: str) -> Machine:
    """This machine's handle: `m = sdk.machine("<the folder's name>")`."""
    return Machine(slug)
