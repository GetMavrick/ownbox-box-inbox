"""The owner's request to move this box to their OWN DigitalOcean account, and how far it has got.

OWNER, 2026-09-23: "We have to instantly give someone a button in the dashboard to be able to take
their machine onto their own account." This is the box's half of that button.

WHAT DIGITALOCEAN ALLOWS, verified against its documentation the same night, and it decides the
whole shape: a Droplet cannot be transferred between accounts, and neither can its IP address. The
one thing that moves is a SNAPSHOT — a full image of the machine — sent to the email address of the
receiving DigitalOcean account, where it is moved, not copied. So "take my box with me" means: we
image it, the image lands in their account, they build a server from it there, and the address
they use is pointed at the new server. The numeric IP changes; nothing they type does.

THE BOX NEVER HOLDS OUR DIGITALOCEAN KEY. It cannot image itself and it cannot transfer anything.
It only RECORDS that its owner asked, and to which email. The provisioner — which does hold the key
— reads the request with the narrow per-box deploy token it already has, does the work, and writes
back how far it has got. That is why this module is a store and nothing more: every step that
touches DigitalOcean happens on the other side.

STORED IN box_settings under machine "core", so it survives a restart and a self-update, and needs
no new table (a schema change on launch night is risk with no buyer benefit).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from core import box_settings
from core.logging import get_logger

log = get_logger(__name__)

_MACHINE = "core"
_REQUEST = "move_request"
_STATUS = "move_status"

# A deliberately plain check. The address is DigitalOcean's to validate — a transfer to an address
# with no account on it simply waits there. This only refuses what cannot be an address at all, so
# a typo is caught on the screen rather than discovered a day later by nobody.
_EMAIL = re.compile(r"^[^@\s<>\"',;]{1,64}@[^@\s<>\"',;]{1,190}\.[A-Za-z]{2,24}$")

# How far a move can get. The provisioner writes these; the screen reads them. Anything else it
# sends is refused, so a box never renders a state nobody wrote a sentence for.
STATES = ("requested", "imaging", "sent", "failed")


class MoveRefused(ValueError):
    """The request cannot be recorded. The message is shown to the owner verbatim."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def current() -> dict:
    """The request and its progress, as one dict. Empty when nothing has been asked. Never raises."""
    req = box_settings.get(_MACHINE, _REQUEST) or {}
    status = box_settings.get(_MACHINE, _STATUS) or {}
    if not isinstance(req, dict) or not req.get("email"):
        return {}
    return {**req, "state": (status.get("state") if isinstance(status, dict) else None) or "requested",
            "detail": (status.get("detail") if isinstance(status, dict) else "") or "",
            "updated_at": (status.get("at") if isinstance(status, dict) else None) or req.get("requested_at")}


def request(email: str, *, by: str | None) -> dict:
    """Record the owner's request. Refuses a second request while one is already under way."""
    email = (email or "").strip()
    if not _EMAIL.match(email):
        raise MoveRefused("that is not an email address. Use the one you sign in to DigitalOcean "
                          "with — the copy of your box is sent there.")
    now = current()
    # ONE MOVE AT A TIME. A second request while the first is imaging would send a second copy of
    # the box — to a possibly different address — and the owner would have two images and no idea
    # which is the newer. A finished or failed move can be asked for again.
    if now and now.get("state") in ("requested", "imaging"):
        raise MoveRefused("a move is already under way. Wait for it to finish, or cancel it first "
                          "if it has not started imaging yet.")
    box_settings.put(_MACHINE, _REQUEST, {"email": email, "requested_at": _now(), "by": by}, set_by=by)
    box_settings.put(_MACHINE, _STATUS, {"state": "requested", "detail": "", "at": _now()}, set_by=by)
    log.info("box_move.requested", by=by)      # the address is never logged — it is theirs
    return current()


def cancel(*, by: str | None) -> bool:
    """Withdraw a request the provisioner has NOT started on. Returns whether anything was withdrawn.

    ONLY BEFORE IMAGING. Once the provisioner has begun the image exists, and "cancel" would be a
    promise this box cannot keep — it cannot delete an image in an account it has no key to.
    """
    now = current()
    if not now or now.get("state") != "requested":
        return False
    box_settings.clear(_MACHINE, _REQUEST)
    box_settings.clear(_MACHINE, _STATUS)
    log.info("box_move.cancelled", by=by)
    return True


def pending() -> dict | None:
    """What the provisioner reads: the address, but only while there is something for it to do."""
    now = current()
    if now and now.get("state") in ("requested", "imaging"):
        return {"email": now["email"], "requested_at": now.get("requested_at"), "state": now["state"]}
    return None


_PAUSE_REASON = "moving to your own DigitalOcean account"


def set_status(state: str, detail: str = "") -> None:
    """The provisioner reporting progress. Refuses a state this box has no sentence for.

    THE BOX PAUSES ITSELF WHILE IT IS IMAGED, and this is the reason the move is safe at all. The
    image is taken while the box runs, so the copy boots with the SAME inbox credentials. Two boxes
    working one Gmail inbox would draft twice and could send twice. So on "imaging" the box writes
    its own pause marker BEFORE the provisioner asks DigitalOcean for the image; the marker is a
    file on this disk, so it is IN the image, and the copy boots paused. On "sent" or "failed" the
    original resumes. The new owner starts the copy from the dashboard when they are ready.

    IT ONLY UNDOES WHAT IT DID. If the owner had already paused this box for reasons of their own,
    the move does not pause it again and — the part that matters — does not resume it afterwards.
    Whether the move did the pausing is recorded with the status, not guessed later.
    """
    if state not in STATES:
        raise ValueError(f"unknown move state {state!r}")
    from core import pause
    prior = box_settings.get(_MACHINE, _STATUS) or {}
    paused_by_move = bool(isinstance(prior, dict) and prior.get("paused_by_move"))
    if state == "imaging" and not paused_by_move and not pause.is_paused():
        pause.halt(_PAUSE_REASON)
        paused_by_move = True
    if state in ("sent", "failed") and paused_by_move:
        pause.resume()
        paused_by_move = False
    box_settings.put(_MACHINE, _STATUS, {"state": state, "detail": str(detail or "")[:300],
                                         "at": _now(), "paused_by_move": paused_by_move},
                     set_by="provisioner")
    log.info("box_move.status", state=state, paused_by_move=paused_by_move)
