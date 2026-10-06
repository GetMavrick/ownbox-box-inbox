"""Labs: the one switch for every feature built ahead of the bar it ships behind.

Owner, 10-04: step 3 (the AEO Machine's "what should we write next" and what each article brought, #1793 Phase 2)
is built now and reaches buyers only once H2's score bar is met: 6 of the last 7 daily runs at 5 of 5. Until then
each such feature reads `on("<name>")`, and with it off the box shows exactly what it showed before. HQ turns a
feature on for one box (default first, ring 0) with the box setting `labs / <name>`, and off again the same way.

HOW HQ FLIPS ONE (OSDev1, 10-04): nobody can shell into a sold box and the dashboard needs the owner's login, so the
provisioner tells the box with that box's own deploy token: `python -m provisioner.run --labs <order> <name> on|off`
posts to `/deploy/labs` (core/dash/box_settings.py), which calls `switch` below. Only a name in KNOWN is accepted, so a
typo can never write a setting nobody reads; the setting's row keeps who (`HQ`) and when, and the journal says so.
"""
from __future__ import annotations

from core.logging import get_logger

log = get_logger(__name__)

NS = "labs"
HQ = "ownbox-hq"                 # set_by on a switch HQ flipped through /deploy/labs (no person on the box did)
# EVERY LABS FEATURE THERE IS. A new one is added here in the PR that builds it.
KNOWN = (
    "article_results",           # #1793 Phase 2.4: what each article brought (marketing/aeo_machine/brought.py)
    "aeo_topic_sync",            # #1793 Phase 3: topics read from the buyer's Airtable (marketing/aeo_machine/topic_sync.py)
    "aeo_questions",             # #1793 Phase 2: the common customer questions (marketing/aeo_machine/questions.py)
    "inbox_screens",             # #1990 Phase 1: Zernio's inbox screens are the Inbox Machine's (marketing/customer_voice/app_ui.py)
)


def on(name: str) -> bool:
    """Is the labs feature `name` switched on for this box? Off unless the box setting `labs / <name>` says on.
    Never raises: a settings store that cannot be read leaves every labs feature off."""
    try:
        from core import box_settings
        v = box_settings.get(NS, str(name or ""))
    except Exception:                                     # noqa: BLE001 — off is the safe answer, always
        return False
    if isinstance(v, bool):
        return v
    return str(v or "").strip().lower() in ("1", "true", "on", "yes")


def switch(name: str, on: bool, *, by: str) -> None:
    """Switch the labs feature `name` on or off for this box, recording who. Raises ValueError for a name not in
    KNOWN, or an `on` that is not a bool, and then nothing is written."""
    name = str(name or "").strip()
    if name not in KNOWN:
        raise ValueError(f"{name or 'that'} is not a labs feature this box knows: {', '.join(KNOWN)}")
    if not isinstance(on, bool):
        raise ValueError("on must be true or false")
    from core import box_settings
    box_settings.put(NS, name, on, set_by=str(by or "")[:60] or None)
    log.info("labs.set", name=name, on=on, by=by)
