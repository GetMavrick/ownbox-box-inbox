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
  * scheduled jobs   m.every(seconds, fn): at most every 15 seconds, in the worker, on its own thread
                     under a time budget; a job that fails is reported, never stops the box
                     (core.machine_jobs). Added in SDK 1, owner 2026-10-01.
  * panels           m.panel(slot, title=, render=): a card on another machine's screen, in a slot
                     that screen offers; one that fails shows a quiet card (core.panels). Added in
                     SDK 1, owner 2026-10-01.
  * the manifest     machine.yaml: name, version, requires_foundation, needs:, sdk
  NOT promised: worker.register / register_periodic (use m.every; a job on the worker's own loop
  holds up every other one), state.register_schema (a company's tables would block our migrations:
  use m.data_dir()), and any other `core.*` import.
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
         "save_setting", "data_dir", "every", "panel", "STYLE_CLASSES", "VERSION")

# The style classes a screen may use. Our markup changes; these names keep their meaning.
STYLE_CLASSES = ("card", "quiet", "addr", "consent")

_SLUG = re.compile(r"^[a-z][a-z0-9-]{1,40}$")


def _key(slug: str) -> str:
    """The registries' key form of a machine's slug: `job-tracker` → `my_job_tracker`.

    YOUR OWN MACHINES LIVE IN THEIR OWN CORNER (owner, 2026-10-01: "ownbox new should have some sort of
    prefix"). Every name a machine of your own takes on the box (its settings, its Morning Review part, its
    tools, its menu row) starts `my_`, and every page lives under `/my/<slug>`. Official add-on machines keep
    the plain names, so a machine of yours can never take a name an add-on needs, and becoming an official
    add-on later is one deliberate move to the plain names, never a collision."""
    return "my_" + slug.replace("-", "_")


def _home(slug: str) -> str:
    """Where every page of this machine lives: `/my/<slug>`."""
    return f"/my/{slug}"


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
        self.home = _home(slug)                  # `m.home`: this machine's own address on the box
        from flask import Blueprint
        # The box mounts a package's `blueprint`; `__init__.py` sets `blueprint = m.blueprint`.
        self.blueprint = Blueprint(self.key, f"my_machines.{slug.replace('-', '_')}")

    def _own(self, path: str, what: str) -> str:
        """`path` if it is this machine's own address or below it; else the one-sentence fix."""
        path = str(path or "")
        if path == self.home or path.startswith(self.home + "/") or path.startswith(self.home + "?"):
            return path
        raise ValueError(f"{what} {path!r} is outside this machine's own address: every page of a machine of "
                         f"your own lives under {self.home} (use m.home), so it never takes a name an "
                         f"official add-on needs")

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
             args: dict | None = None, title: str | None = None) -> None:
        """One question the owner's AI may ask this box. `capability` is `read:<noun>` for a
        question that changes nothing; the box decides who may ask it. `title` is what the owner
        reads when their AI asks permission ("Read your latest notes"); without one, the box makes
        one from the name. Added in SDK 1 without breaking anything built on it: it is optional."""
        from core.connector import tools
        tools.register(name, fn=fn, description=description, machine=self.key,
                       capability=capability, args=args, title=title)

    # ── the menu ──────────────────────────────────────────────────────────────────────────────
    def menu(self, key: str, *, title: str, href: str) -> None:
        """One row in the box's menu, pointing at one of this machine's own pages (under `m.home`). Where it sits
        and how it looks are the box's to decide."""
        from core import shell
        key = key if str(key).startswith("my_") else "my_" + str(key)
        shell.register_section(key, order=90, machine=self.key, title=title, href=self._own(href, "menu link"))

    # ── screens ───────────────────────────────────────────────────────────────────────────────
    def screen(self, path: str, *, owner_only: bool = False, methods=("GET",)):
        """A page of this machine, behind the box's own sign-in, at `m.home` or below it. Return `sdk.page(...)`.

            @m.screen(m.home)                    # /my/job-tracker
            def home():
                return sdk.page(m.home, title="Jobs", lede="...", body='<div class="card">…</div>')
        """
        path = self._own(path, "screen")

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

    # ── scheduled jobs ────────────────────────────────────────────────────────────────────────
    def every(self, seconds, fn=None, *, name: str | None = None):
        """Run `fn()` every `seconds` (15 or more) in the box's worker. Either form works:

            m.every(60, check_orders)

            @m.every(60)
            def check_orders(): ...

        Each run gets its own thread and a time budget; a run still going when the next is due is left to
        finish, never doubled. A run that raises is reported on the Add a Machine page and the next one goes
        ahead. The job is known as `<this machine>.<name>`; `name` defaults to the function's name. Keep
        your own progress in `m.data_dir()`, so a restart picks up where the last run stopped."""
        from core import machine_jobs
        if fn is None:
            def wrap(f):
                machine_jobs.register(self.key, seconds, f, name=name)
                return f
            return wrap
        machine_jobs.register(self.key, seconds, fn, name=name)
        return fn

    # ── panels on another machine's screen ────────────────────────────────────────────────────
    def panel(self, slot: str, *, title: str, render) -> None:
        """A card in `slot` on another machine's screen (that machine's guide names its slots). `render()`
        returns the card's HTML; the box draws the card and its `title`. It is shown to whoever that screen
        shows itself to. A render that raises shows "This section couldn't load", never a broken screen.
        Link to your own pages under `m.home` for anything more than a card."""
        from core import panels
        panels.register(slot, machine=self.key, title=title, render=render)

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
