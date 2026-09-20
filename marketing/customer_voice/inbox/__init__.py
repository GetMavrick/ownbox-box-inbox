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

from . import handler, notices, poller

log = get_logger(__name__)

# A-3: actually CALL verify_sdk at module load (i.e. worker boot) AND honor it.
# The contract is fail-closed: a drifted/signature-blind SDK must leave this
# department INERT — 'log one line then register anyway' shipped the broken-inbox
# class once already. The watchdog's zernio_sdk probe pages the operator on drift,
# and launch_check is a NO-GO (docs/ZERNIO_DEEP_INTEGRATION.md §2).
_sdk_ok, _sdk_detail = zernio.verify_sdk()
(log.info if _sdk_ok else log.error)("inbox.verify_sdk", ok=_sdk_ok, detail=_sdk_detail)

# THE SEND PATH STAYS FAIL-CLOSED. `handler.handle` is what SENDS — the instant opener, the reply
# — and sending on an SDK nobody can verify is the exact failure this gate was built for.
if _sdk_ok:
    register("inbox", handler.handle)
else:
    log.error("inbox.disabled_sdk_drift", detail=_sdk_detail)

# INTAKE IS REGISTERED EITHER WAY, AND THAT IS THE DECISION — the same one this file already made
# for `tools` below, applied to the channel with the stronger claim to it.
#
# WHAT WAS TRUE BEFORE (OSDev4, measured 2026-09-17). This registration sat inside the `if` above,
# and `poll_sweep` is where the MAILBOX is swept too (`_sweep_email`). Email is IMAP: it touches no
# Zernio SDK, no Zernio key and no Zernio account. So a drifted vendor SDK stopped a buyer's Gmail
# from being read — and that is step ONE of set-up, the step with the longest instructions on the
# screen and the only one that needs no new account and no card. A buyer who did it perfectly got
# silence, for a reason that had nothing to do with them, on the first day they owned the box.
#
# ZERNIO ITSELF IS NOT LOOSENED. `poller._vendor_intake_allowed` asks the same `verify_sdk` and
# leaves the Zernio client unbuilt when it says no, so every Zernio channel takes the skip an
# email-only box has always taken. Intake for the vendor stops; intake for the mailbox does not.
#
# 45s default (config inbox.poll_interval_s) — an ad DM gets its opener inside ~a minute, and an
# unchanged inbox costs one list call per Space per sweep.
register_periodic(poller.poll_sweep,
                  interval_s=int((get_config().get("inbox", {}) or {})
                                 .get("poll_interval_s", 45)),
                  name="inbox_poll")
# THE NOTIFIER REGISTERS EITHER WAY, AND THAT PLACEMENT IS THE DECISION — the same one the read
# tools are about, one line down. Everything above is fail-closed on the Zernio SDK, correctly:
# polling and sending on an SDK nobody can verify is what that gate is for. `notices.tick` touches
# no vendor SDK. It counts rows already on this box and mails the box's own owner, so an
# EMAIL-ONLY BOX — no Zernio key at all, which is a shape we sell — must still tell him somebody
# is waiting. Registering it inside the gate would have made the notification a feature you only
# get if you also connected Instagram.
#
# FIFTEEN MINUTES, not the slot length. The slot is four hours wide and the marker makes the send
# once-per-slot, so this interval only decides how soon after 08:00 the mail goes and how much a
# restart costs. Quarter-hourly is inside the noise on a $12 box: sixteen hours a day it is
# arithmetic on the clock that returns before touching the database.
register_periodic(notices.tick, interval_s=900, name="inbox_notify")

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
