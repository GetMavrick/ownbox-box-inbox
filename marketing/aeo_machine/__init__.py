"""THE AEO MACHINE — writes articles that answer what this business's customers search for, checks
each one, and publishes it to the business's own website on its own.

OWNER, 2026-09-25: the machine will *"write and publish on its own"*. So nobody reads an article
before it is live, and `guard.py` is the only reviewer an article gets (docs/OWNBOX_ARTICLES.md).

WHAT LIVES WHERE:
  · `schema.py`   the `seo_plan` table: every topic, and what became of it.
  · `plan.py`     the only code that reads or writes that table.
  · `settings.py` every setting, read from the box's own settings store (the screen), never YAML.
  · `guard.py`    the pre-publish check. Refuses; never warns.
  · `app.py`      the two screens, Topics and Settings, mounted through config `web_modules:`.
  · `job.py`      the writing job: the next topic, written, checked, published, recorded on its row.

DARK UNTIL SET UP. A box that has not been given a website, a Sanity project and a token publishes
nothing, and the Topics screen says what is missing rather than offering a button that cannot work.

THIS FILE DECLARES THE TABLE AND REGISTERS THE WRITING JOB'S TICK, nothing more. Importing the
package must stay cheap and side-effect free apart from those two, because the guard and the publisher import it too, and the worker imports it on every
box that carries the machine.
"""
# THIS MACHINE DECLARES ITS TABLES (docs/SPEC_GOLDEN_DROPLET_IMPACT.md §5). First, before anything
# can touch them; idempotent; correct whether init_db() ran already or not.
from core import state as _state  # noqa: E402
from .schema import DDL as _DDL  # noqa: E402

_state.register_schema("aeo_machine", _DDL)

# THE WRITING JOB, ON ITS OWN MINUTE. Registered here because the worker imports this package from
# config `modules:`, and nothing else would. The import of `job` waits for the first tick, so
# importing the package stays cheap for the guard, the publisher and the web process, which never
# run it. A tick with nothing planned is one query; "Write and publish now" is honoured within a
# minute. No `beat=`: a box with no topics is idle, not down, and must not page anyone.
from core.worker import register_periodic as _register_periodic  # noqa: E402


def _aeo_publish_tick():
    from . import job
    return job.periodic()


_register_periodic(_aeo_publish_tick, interval_s=60, name="aeo_publish")
