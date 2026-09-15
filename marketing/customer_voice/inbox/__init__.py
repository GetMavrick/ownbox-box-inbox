"""Unified Inbox — Facebook Messenger first (docs/UNIFIED_INBOX_SPEC.md).

Phase 1: poll-only intake (NO webhook receiver, §11-4) + the instant template
opener. Importing this module registers the `inbox` handler and the poll sweep
with the worker (config `modules:` → core/worker.load_modules()) — but ONLY when
the pinned zernio-sdk verifies; a drifted/fake SDK leaves the department INERT.

Fully deterministic — no Claude anywhere in Phase 1 (the opener is a fixed
per-Space template; Phase 2 adds the AI conversation through brain.think).
Inert until a Space has a Zernio key; the kill switch is `inbox.autonomy: off`.
The inbox tables are NEW tables and live in state.SCHEMA (no migration — the
state.py governing rule), so they reach every box on deploy.
"""
from core.config import get_config
from core.logging import get_logger
from core.worker import register, register_periodic

from core.vendors import zernio

from . import handler, poller

log = get_logger(__name__)

# A-3: actually CALL verify_sdk at module load (i.e. worker boot) AND honor it.
# The contract is fail-closed: a drifted/signature-blind SDK must leave this
# department INERT — 'log one line then register anyway' shipped the broken-inbox
# class once already. The watchdog's zernio_sdk probe pages the operator on drift,
# and launch_check is a NO-GO (docs/ZERNIO_DEEP_INTEGRATION.md §2).
_sdk_ok, _sdk_detail = zernio.verify_sdk()
(log.info if _sdk_ok else log.error)("inbox.verify_sdk", ok=_sdk_ok, detail=_sdk_detail)

if _sdk_ok:
    register("inbox", handler.handle)
    # 45s default (config inbox.poll_interval_s) — an ad DM gets its opener inside
    # ~a minute, and an unchanged inbox costs one list call per Space per sweep.
    register_periodic(poller.poll_sweep,
                      interval_s=int((get_config().get("inbox", {}) or {})
                                     .get("poll_interval_s", 45)),
                      name="inbox_poll")
else:
    log.error("inbox.disabled_sdk_drift", detail=_sdk_detail)

# THE READ TOOLS REGISTER EITHER WAY, and that placement is the decision. Everything above is
# fail-closed on the vendor SDK, correctly: polling and sending on an SDK nobody can verify is the
# failure that gate exists for. `tools` touches no vendor — it reads rows already on this box's own
# disk — so sharing the poller's fate would mean a customer whose SDK drifted also loses the
# ability to read their own conversations through the connector. Intake stops; reading does not.
#
# IT MUST NEVER TAKE THE DEPARTMENT DOWN. A duplicate tool name raises from `tools.register`, and
# an unguarded import here would turn that into a worker that cannot load the inbox at all. The
# connector already has a word for this: the tools are absent, the reason is recorded, and the box
# keeps answering (core/dispatch.py does the same for core.report_tools).
try:
    from . import tools as _inbox_tools  # noqa: F401
except Exception as _tools_err:          # noqa: BLE001
    log.error("inbox.tools_import_failed", error=f"{type(_tools_err).__name__}: {_tools_err}")
    try:
        from core.connector import tools as _ctools
        _ctools.note_absent("marketing.customer_voice.inbox.tools",
                            f"{type(_tools_err).__name__}: {_tools_err}")
    except Exception:                    # noqa: BLE001 — bookkeeping never breaks the loader
        pass
