"""CUSTOMER VOICE — what the outside world says about this business, and whether its front door
works.

Stage 1 (docs/PLAN_CUSTOMER_VOICE.md §4) is the half that needs NO account and NO approval:
uptime and PageSpeed. Reviews wait on Google, comments wait on Meta, search waits on OAuth —
every one of those waits on somebody else's queue, and a machine that cannot prove itself until
a queue clears is a machine nobody can build against. These two answer on any box, from the
first poll, with no key to find.

THE SHAPE IS `ads_machine`'s, which is the right small precedent: a package that registers
periodics at import and holds no job handler, because nothing here is dispatched — it observes
on a clock. `marketing/customer_voice/plugins/` exists and is empty, the same industry-pack axis
`content_machine` carries, so a pack can attach later without this package changing.

WORKING BY DEFAULT (owner, 2026-09-09: *ship things working, never dark*). There is no "enable
customer_voice" switch to find. A rail that needs a credential is inert because the key is
absent — which is not the same thing as a flag someone set to false.
"""
# THIS MACHINE DECLARES ITS TABLES (docs/SPEC_GOLDEN_DROPLET_IMPACT.md §5). First, before
# anything below can touch them; idempotent; correct whether init_db() ran already or not.
from core import state as _state  # noqa: E402
from .schema import DDL as _DDL  # noqa: E402
_state.register_schema("customer_voice", _DDL)

from core.logging import get_logger
from core.worker import register_periodic

from . import rails  # noqa: F401 — the four-state register, imported for its side-effect-free API
from .seo import health

log = get_logger(__name__)


def _interval(section: str, default: int) -> int:
    from core.config import get_config
    try:
        return int(((get_config().get("customer_voice") or {}).get(section) or {}).get("interval_s", default))
    except (TypeError, ValueError):
        return default


# TWO PERIODICS, BOTH WITH `beat=`. A periodic that raises writes no beat, so its heartbeat AGES
# and the watchdog pages on it — without that, a rail that has been failing for a week is one
# throttled WARNING in a journal nobody reads, which is how gtm_unsub_sweep failed unseen for
# seven days.
register_periodic(health.periodic_uptime, interval_s=_interval("uptime", 900),
                  name="voice_uptime", beat="voice_uptime")
register_periodic(health.periodic_pagespeed, interval_s=_interval("pagespeed", 86400),
                  name="voice_pagespeed", beat="voice_pagespeed")

# THE DRAFTER, ON ITS OWN TIMER AND NOT INSIDE THE POLLER. The poller lives in `inbox/`, which
# holds the send path, and no file in this machine may both think and send — so the part that
# asks a model anything runs on a separate periodic that imports none of it. It also means a
# model being slow, capped or unconfigured can never delay a customer's message being mirrored.
#
# NO `beat=`. A missing draft is not an outage: the inbox reads and answers perfectly well
# without one, and paging the owner because a suggestion did not appear would train him to
# ignore the pager. The two rails above are health checks; this is a convenience.
from .drafter import draft as _drafter                                   # noqa: E402
register_periodic(_drafter.periodic, interval_s=_interval("drafts", 120), name="voice_drafts")

# AND THE DRAFT GOES WHERE THE BUYER ALREADY IS. A buyer who reads their mail in the Gmail app
# never opens our Drafts tab, so the drafted reply is appended to their own Drafts folder, inside
# the customer's thread (§1.4 of docs/SCOPE_EMAIL_SEND_AND_MOBILE_NOTIFICATIONS.md).
#
# A SECOND TIMER, NOT A LINE AT THE END OF THE DRAFTER. `drafter/` may not import anything under
# `inbox/` — the guard in tests/test_customer_voice.py refuses it, because the directory that may
# ask a model anything must not be able to reach a mailbox. It also means a slow IMAP server can
# never delay the next draft being written.
#
# SLOWER THAN THE DRAFTER (180s to its 120s) so a draft is normally written and settled before
# this looks: a draft that arrives in the mailbox and is then superseded is worse than one that
# arrives a minute later. NO `beat=`, like the drafter — a missing draft is not an outage.
# ONCE A DAY IS PLENTY. A business's own sent mail does not change hour to hour, and this is one
# model call over a hundred and fifty messages — cheap daily, wasteful hourly. It writes nothing
# when it learns nothing, so a quiet mailbox costs one read and stops.
from .drafter import learn_business as _learn                            # noqa: E402
from .inbox import sent_mail as _sent                                    # noqa: E402


def _learn_the_business() -> dict:
    """Read the owner's sent mail, learn what the business is, write it down.

    THE WIRING LIVES HERE BECAUSE NEITHER HALF MAY DO THE OTHER'S JOB: `inbox/` may open a
    mailbox and may not think, `drafter/` may think and may not touch imaplib or a file. This
    function only passes values between them, which is why it is allowed to see both.
    """
    try:
        sent = _sent.read_sent()
    except Exception as e:                                               # noqa: BLE001
        return {"status": "unreadable", "error": type(e).__name__}
    out = _learn.summarise(sent)
    if out.get("status") == "ok" and out.get("text"):
        from core import brain as _brain
        out["written"] = _brain.write_knowledge(_learn.OUT_NAME, out["text"])
        out.pop("text", None)
    return out


register_periodic(_learn_the_business, interval_s=_interval("learn_business", 86400),
                  name="voice_learn_business")

from .inbox import mailbox_drafts as _mailbox_drafts                     # noqa: E402
register_periodic(_mailbox_drafts.periodic, interval_s=_interval("mailbox_drafts", 180),
                  name="voice_drafts_to_mailbox")

# MONTHLY, AND THAT IS THE POINT. This is the only rail on the machine that spends money —
# one metered Places call per competitor — and a rating moves over months. Polling it hourly
# would buy the same number seven hundred times.
from .competitors import roster as _competitors  # noqa: E402

register_periodic(_competitors.periodic, interval_s=_interval("competitors", 2592000),
                  name="voice_competitors", beat="voice_competitors")

# THE MORNING REVIEW SEGMENT registers here, so it exists wherever this package is imported —
# and the page reads the stored row rather than this module, which is why the segment renders in
# the web process too (docs/PLAN_MORNING_REVIEW.md §1.1).
from . import report  # noqa: E402,F401
