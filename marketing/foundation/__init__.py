"""The marketing foundation (docs/PLAN_ANALYTICS_FOUNDATION_PHASE1.md): one connection, one set of queries, one
store, one seam. Department code, never the Base Machine (owner, 2026-10-01: keep the Base lean).

A machine that reads website numbers says `needs: [foundation:marketing]` in its machine.yaml; the box imports
this package before that machine, in both processes (core/foundations.py), and ships it with any box that
carries such a machine (scripts/export_box.sh). Nothing else imports it.

What it registers at import: its tables, the Morning Review's line per site, the Website analytics card on Data
Sources, and the worker's hourly pass that syncs yesterday once a day. What machines call: `sites()`,
`day(site, day)`, `week(site, end_day)`.

INSTANTLY IS OFF ON EVERY BOX, his included (owner, 2026-10-02: "I never approved instantly as a standard connection
under data sources. That's an obscure piece of software that not many companies use", "99.5% of customers don't want
instantly", then "Instantly is not going to be a section. I can just connect that with the fucking MCP. I don't need
a section displaying instantly."). A business that uses it connects it through Connect an app, by its MCP server,
like any other app. Its read-only adapter, store and card stay in this package, dormant, never deleted: nothing on a
box calls `wire_outreach()`, so there is no card, row or page, no hourly pull, no worker job and no meter. OSDev2's
Lead Machine plan may move it into the owner's own custom machine later, with his approval.
"""
from core import state as _state
from core.worker import register_periodic as _register_periodic

from . import report as _report  # noqa: F401 — registers the reporter
from . import card as _card  # noqa: F401 — registers the Data Sources card (core/source_cards.py)
from . import jobs as _jobs
from . import sync as _sync
from .schema import DDL as _DDL
from .seam import day, sites, week

__all__ = ["sites", "day", "week"]

_state.register_schema("marketing_foundation", _DDL)
_register_periodic(_sync.tick, interval_s=3600, name="website_sync")
_jobs.register()                    # Sync now and Check and save, run by the worker (jobs.py)


def wire_outreach() -> None:
    """The dormant Instantly source, wired in: its Data Sources card, its hourly pull and its worker jobs. NOTHING ON A
    BOX CALLS THIS (see above); tests/test_the_outreach_source.py does, to keep the dormant code proven."""
    from . import outreach_card  # noqa: F401 — registers the Outreach card (core/source_cards.py)
    from . import outreach_sync
    _register_periodic(outreach_sync.tick, interval_s=3600, name="outreach_pull")
    outreach_sync.register()


def _drain_outbound_mail():
    # Imported per tick so a test can stub the transport first; the module is the foundation's own.
    from . import outbound_mail
    return outbound_mail.drain()


# An email waiting for the next hour, or for a busy email service, goes by itself (OSDev1's review of #1810).
_register_periodic(_drain_outbound_mail, interval_s=60, name="outbound_mail_drain")
