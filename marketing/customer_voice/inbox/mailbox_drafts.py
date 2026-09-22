"""Put the box's drafted replies into the buyer's OWN Gmail Drafts, inside the customer's thread.

WHY THIS FILE EXISTS AT ALL, RATHER THAN A CALL IN THE DRAFTER. `tests/test_customer_voice.py`
refuses `drafter/` any import with `inbox` in its path — the directory that may ask a model
anything may not import the directory that can reach a mailbox, and that separation is the whole
reason drafting is allowed in this machine. So the drafter writes its row and stops, exactly as
before, and this rail carries the row to the mailbox. The part that thinks still only thinks.

WHY IT IS NOT A STEP INSIDE THE POLLER either: a mail server having a bad minute must never delay
a customer's message being mirrored. Its own timer, its own failures, nothing downstream of it.

SCOPE: email conversations only. This is a trick the mailbox makes possible — an Instagram DM has
no Drafts folder to put anything in, and §2.3 is how the buyer hears about those.
"""
from __future__ import annotations

from core import state
from core.logging import get_logger

from . import email_channel, store  # noqa: F401 — store re-exported for the suite's fakes

log = get_logger(__name__)

_PER_SWEEP = 5      # a bound, not a page size: each row is one APPEND on the buyer's mailbox


def _cfg() -> dict:
    # IMPORTED INSIDE THE FUNCTION, like `drafter/draft.py:_cfg` and for the same measured
    # reason: binding the name at module import moves a swapped `get_config` for every file
    # except this one, and that is how a test passes vacuously against the shipped config.
    from core.config import get_config
    return (get_config().get("inbox") or {}).get("mailbox_drafts") or {}


def enabled() -> bool:
    """ON by default — OSDev1's ruling, 2026-09-22, "Gmail Drafts default ON".

    The failure mode of it being on is a suggestion in a folder whose whole purpose is holding
    suggestions. Nothing is sent, nothing is marked read, and the buyer deletes it in one swipe
    if they do not want it — and a deleted draft stays deleted (`mailbox_at` is written once).
    """
    return bool(_cfg().get("enabled", True))


def waiting(space: str, *, limit: int = _PER_SWEEP) -> list[dict]:
    """Email drafts that are still current and have never reached the mailbox. Oldest first.

    THE SQL IS DUPLICATED FROM `drafter/store.waiting` RATHER THAN IMPORTED, deliberately and for
    the same reason that file gives for duplicating `history_for`: importing across this boundary
    is the dependency the guard exists to refuse. A repeated SELECT is the cheaper price.

    STILL CURRENT MEANS NO NEWER OUTBOUND. If somebody has already answered — from the box's own
    screen, from their phone, by hand — the drafted reply is stale, and putting a stale answer in
    their Drafts folder is worse than putting nothing there. Same test the Drafts tab uses.

    OPTED-OUT IS EXCLUDED, like everywhere else. Writing a reply to somebody who asked us to stop
    is not a send, but leaving it one tap from being one is not a thing to build.
    """
    with state.connect() as c:
        rows = c.execute(
            "SELECT d.id, d.zernio_conversation_id AS zcid, d.in_reply_to, d.body "
            "  FROM inbox_drafts d "
            "  JOIN inbox_conversations k ON k.space = d.space "
            "   AND k.zernio_conversation_id = d.zernio_conversation_id "
            "  LEFT JOIN inbox_messages m ON m.space = d.space "
            "   AND m.zernio_message_id = d.in_reply_to "
            " WHERE d.space = ? AND d.dismissed_at IS NULL AND d.mailbox_at IS NULL "
            "   AND k.platform = 'email' AND k.opted_out = 0 "
            "   AND NOT EXISTS (SELECT 1 FROM inbox_messages o "
            "                    WHERE o.space = d.space "
            "                      AND o.zernio_conversation_id = d.zernio_conversation_id "
            "                      AND o.direction = 'out' "
            "                      AND o.created_at > COALESCE(m.created_at, d.created_at)) "
            " ORDER BY d.created_at ASC LIMIT ?", (space, int(limit))).fetchall()
    return [dict(r) for r in rows]


def _claim(space: str, draft_id: str) -> bool:
    """Stamp `mailbox_at` BEFORE the append, and only if it is still NULL. Returns False if
    somebody else got there first.

    CLAIM FIRST, NOT RECORD AFTER. Two processes on a box boot together (worker and dispatch both
    call init_db, and a future second worker is a config change away). Appending and then
    recording leaves a window in which both append, and the buyer opens Gmail to two identical
    drafts under one message — which reads as the box being broken. The claim is a single
    conditional UPDATE, so exactly one caller can win it.
    """
    with state.connect() as c:
        cur = c.execute(
            "UPDATE inbox_drafts SET mailbox_at = ? "
            " WHERE space = ? AND id = ? AND mailbox_at IS NULL",
            (state._now(), space, str(draft_id)))
        return cur.rowcount > 0


def _release(space: str, draft_id: str) -> None:
    """The append did not land, so un-claim it and let the next sweep try again.

    THE ONLY PATH THAT EVER CLEARS THIS COLUMN, and it clears it only for a row this process
    claimed moments ago and failed on. A draft the buyer deleted in Gmail keeps its stamp
    forever, which is the point: software that puts back what you threw away is software people
    uninstall.
    """
    with state.connect() as c:
        c.execute("UPDATE inbox_drafts SET mailbox_at = NULL WHERE space = ? AND id = ?",
                  (space, str(draft_id)))


def sweep(space: str) -> dict:
    """Carry this Space's current email drafts into the buyer's Drafts folder. Never raises."""
    if not enabled():
        return {"status": "off", "appended": 0}
    try:
        rows = waiting(space)
    except Exception as e:                               # noqa: BLE001 — a box without the column
        log.warning("mailbox_drafts.unreadable",
                    extra={"space": space, "error": f"{type(e).__name__}: {e}"[:120]})
        return {"status": "unreadable", "appended": 0}
    if not rows:
        return {"status": "ok", "appended": 0, "considered": 0}

    # ONE LOGIN FOR THE WHOLE SWEEP. Gmail throttles logins far harder than it throttles APPENDs,
    # and a box with five waiting drafts should not knock on the door five times.
    conn = None
    appended = 0
    try:
        from core import box_secrets
        cred = box_secrets.email_credential()
        if not cred:
            return {"status": "not_connected", "appended": 0}
        conn = email_channel._connect(cred)
        for row in rows:
            if not _claim(space, row["id"]):
                continue
            landed = email_channel.append_draft(space=space, zcid=row["zcid"],
                                                in_reply_to=row["in_reply_to"],
                                                body=row["body"], conn=conn)
            if landed:
                appended += 1
            elif landed is False:
                # NOT THIS TIME. Un-claim it, and the next sweep tries again.
                _release(space, row["id"])
            else:
                # NEVER, for this row: `append_draft` has told us no attempt can work. The claim
                # STAYS, which is the only thing that takes the row out of `waiting()` — and if
                # it did not, a handful of these would fill every sweep's limit and starve the
                # drafts that would have landed. It is still on the box's own Drafts tab.
                log.info("mailbox_drafts.unmailable",
                         extra={"space": space, "conversation": row["zcid"]})
    except Exception as e:                               # noqa: BLE001 — a mailbox is not an outage
        log.warning("mailbox_drafts.failed",
                    extra={"space": space, "error": f"{type(e).__name__}: {e}"[:160]})
    finally:
        if conn is not None:
            try:
                conn.logout()
            except Exception:                            # noqa: BLE001
                pass
    return {"status": "ok", "appended": appended, "considered": len(rows)}


def periodic() -> dict:
    """Worker entry. Every Space on this box, then stop. Never raises.

    NO `beat=` where this registers, for the reason the drafter has none: a draft that did not
    reach a mailbox is not an outage. The box's own Drafts tab still has it, and paging an owner
    over a convenience is how a pager gets ignored.
    """
    try:
        from core import spaces as _spaces
        rows = _spaces.all_spaces()
    except Exception as e:                               # noqa: BLE001 — unconfigured is not broken
        log.info("mailbox_drafts.no_spaces", extra={"error": type(e).__name__})
        return {"skipped": "unconfigured"}
    appended = 0
    for sp in rows or []:
        name = (sp or {}).get("name") if isinstance(sp, dict) else str(sp)
        if not name:
            continue
        try:
            appended += int(sweep(name).get("appended") or 0)
        except Exception as e:                           # noqa: BLE001 — one Space never stops the rest
            log.warning("mailbox_drafts.space_failed",
                        extra={"space": name, "error": type(e).__name__})
    return {"appended": appended}
