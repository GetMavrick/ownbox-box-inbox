"""The marketing foundation (docs/PLAN_ANALYTICS_FOUNDATION_PHASE1.md): one connection, one set of queries, one
store, one seam. Department code, never the Base Machine (owner, 2026-10-01: keep the Base lean).

A machine that reads website numbers says `needs: [foundation:marketing]` in its machine.yaml; the box imports
this package before that machine, in both processes (core/foundations.py), and ships it with any box that
carries such a machine (scripts/export_box.sh). Nothing else imports it.

What it registers at import: its tables, the Morning Review's line per site, the Website analytics card on
Data Sources, and the worker's hourly pass that syncs yesterday once a day. What machines call: `sites()`,
`day(site, day)`, `week(site, end_day)`.
"""
from core import state as _state
from core.worker import register_periodic as _register_periodic

from . import report as _report  # noqa: F401 — registers the reporter
from . import card as _card  # noqa: F401 — registers the Data Sources card (core/source_cards.py)
from . import sync as _sync
from .schema import DDL as _DDL
from .seam import day, sites, week

__all__ = ["sites", "day", "week"]

_state.register_schema("marketing_foundation", _DDL)
_register_periodic(_sync.tick, interval_s=3600, name="website_sync")
