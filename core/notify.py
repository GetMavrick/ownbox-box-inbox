"""Telling the OWNER of a box something happened on it, without becoming a way to mail anyone else.

WHAT THIS IS FOR. A box runs unattended: it polls, it drafts, and the thing it is actually for —
a customer's message waiting for an answer — is invisible until somebody opens a screen. Nobody
watches a screen. So the box reaches out, to the person who bought it, and to nobody else.

PUSH FIRST, EMAIL AS THE FLOOR — AND THAT REVERSED A DECISION RECORDED HERE. This module shipped
saying "NOT BROWSER PUSH", on the owner's call of 2026-09-17: web push needs `cryptography` in a
hash-pinned lock and routes every notification's existence through Google's, Mozilla's or Apple's
service, while email through the box's own Resend account added no dependency and no new
sub-processor. That reasoning was sound and it lost to a better argument.

Owner, 2026-09-20: notifications on a mobile are "a critical business function with the unified
inbox" — and, on Slack as the alternative, "if they get the notification on Slack, then they're
totally outside of our environment on their phone." That is the point email cannot answer either.
A mail is read in Mail. Only a notification from the installed web app OPENS THE INBOX when it is
tapped, and that is the difference between a product people live in and one they get reminded
about. The dependency cost did not change; what changed is what it buys.

BOTH CHANNELS FIRE, AND THAT REVERSED A RULING MADE HOURS EARLIER THE SAME DAY. At 2026-09-20 the
owner agreed email should stop where push lands ("3. Agree no"), on the argument that two
notifications for one event trains people to ignore both. He then changed it, before any of this
shipped: *"Just make sure you're sending both notifications. Don't turn email off at this point.
We just need to have extra notifications and we'll fine tune the settings for them later."*

The later ruling governs, and the reasoning is sound for where the product actually is: nobody has
lived with push yet, push is the channel with the most ways to silently fail — a denied
permission, an uninstalled app, a dropped subscription, a push service having a bad day — and
until it has proven itself on real boxes, a duplicate notification is a far cheaper mistake than a
missed customer. Tuning this is a settings question for later, not a default to guess at now.

PER PERSON, NOT PER BOX — a box seats three, and one of them having a phone installed says nothing
about the others, who must still be told.

THREE PROPERTIES, and each one exists because the obvious version of this module is a spam cannon:

  IT CANNOT NAME AN OUTSIDER. Every address comes from `box_mail.to_box_people`, which reads this
  box's own users table and nothing else. Callers pass a user id or nothing; no caller passes an
  address, so no caller can invent one.

  IT CANNOT RAISE. This is called from the poller. A notifier that throws takes intake down with
  it — the box would stop reading a customer's mail because it could not tell its owner about it,
  which is the tail wagging a very important dog. Every path returns a dict.

  IT CANNOT FLOOD. One notice per key per quiet gap, held in `alert_state.last_alert_ts`, which is
  the column that already exists for spacing out "still down" reminders. Twenty messages arriving
  at once is one email.

CADENCE IS THE OWNER'S WORD (CLAUDE.md), and he has now given it — 2026-09-17, relayed by OSDev1:
"For right now an 8 AM and a 5 PM email will work." Two a day on the BUYER's clock, skipped when
nobody is waiting. The first version of this module mailed on arrival behind a thirty-minute gap,
which was a cadence a developer picked; it is replaced rather than configured, because a schedule
and a throttle are two ways to answer the same question and one of them would be wrong later.
"""
from __future__ import annotations

from datetime import datetime, timezone

from core import box_mail, state
from core.logging import get_logger

log = get_logger(__name__)

def _cfg() -> dict:
    from core.config import get_config          # per call: config bound at import defeats a patch
    return dict(get_config().get("notify") or {})


def is_on() -> bool:
    """Both halves, and a box that is half-configured is OFF rather than nearly on.

    `is_configured` is the honest one: Resend rejects an unverified sender, so a key with no
    from-address sends nothing at all. Reporting that as "notifications are on" is the exact
    class of claim this repo spent a night deleting — enabled, ok and connected are not works.
    """
    return bool(_cfg().get("enabled", True)) and box_mail.is_configured()


# QUIET HOURS ARE GONE, AND THAT IS A SIMPLIFICATION RATHER THAN A LOSS. They existed to stop an
# event-driven notifier mailing at three in the morning. The owner has since ruled the cadence —
# "an 8 AM and a 5 PM email" — so the schedule IS the quiet hours, and there is nothing outside the
# two windows for a second mechanism to suppress. Two ways to decide whether a box may speak is one
# of them being wrong later.


def buyer_timezone() -> str:
    """WHERE THE PERSON READING THIS ACTUALLY IS, in priority order.

    NOT `cost.timezone`, WHICH IS THE BOX'S. OSDev1 measured it on 2026-09-17: a sold box ships
    that as UTC and nothing in the provisioner ever sets it to the buyer's, so a schedule built on
    it mails a Pacific buyer at one in the morning. The owner's own box has it right, which is
    exactly why the bug survives every test run on ours.

      1. `notify.timezone` in config — the override a settings screen will write, and the only
         one a person can correct after the fact.
      2. What the browser told us when he claimed the box. Asked of the browser, never of him.
      3. The box's `cost.timezone`, which is at least a deliberate value on an operator box.
      4. UTC, which is the honest answer when nothing else is known.
    """
    cfg = str(_cfg().get("timezone") or "").strip()
    if cfg:
        return cfg
    try:
        from core import claim
        row = claim.claimed() or {}
        claimed_tz = str(row.get("timezone") or "").strip()
        if claimed_tz:
            return claimed_tz
    except Exception as e:                     # noqa: BLE001 — a box too old for the column
        log.warning("notify.claim_tz_unreadable", error=str(e)[:120])
    try:
        from core.config import get_config
        return str((get_config().get("cost") or {}).get("timezone") or "UTC")
    except Exception:                          # noqa: BLE001
        return "UTC"


def _local(now: datetime) -> datetime:
    """`now` on the BUYER's wall clock. Never raises — a bad zone must not silence a box."""
    try:
        from zoneinfo import ZoneInfo
        return now.astimezone(ZoneInfo(buyer_timezone()))
    except Exception:                          # noqa: BLE001 — unknown zone, no tzdata, anything
        return now


def unsent(marker: str) -> bool:
    """Has this slot NOT gone yet? Unknown or unreadable = YES, send it.

    FAILING TOWARD SENDING IS DELIBERATE, and it is the opposite of what the rest of this repo
    does. Everywhere else an unreadable state means refuse, because the cost of acting is a
    message to a stranger. Here the cost of acting is one duplicate email to the person who bought
    the box; the cost of not acting is his customer waiting all day. The asymmetry runs the other
    way, so the default does too — and the marker carries a date and a slot, so the worst case is
    bounded at one extra email, not a loop.
    """
    try:
        row = state.get_alert_row(_key(marker)) or {}
        # THE TIMESTAMP WE WROTE, NOT THE ONE `set_alert` STAMPED. `set_alert` fills
        # `last_alert_ts` from its own `_now()`, so a caller that passes `now` — the worker under
        # test, a backfill, anything replaying — would compare its clock against a different one
        # and find every notice due. Storing the instant in the state value keeps one clock in
        # play. `last_alert_ts` is still bumped, because that column is what an operator reads.
        return not (row.get("state") or row.get("last_alert_ts"))
    except Exception as e:                     # noqa: BLE001
        log.warning("notify.marker_unreadable", marker=marker, error=str(e)[:120])
        return True


# ── WHEN, RULED BY THE OWNER ─────────────────────────────────────────────────────────────
#
# Owner, 2026-09-17, relayed by OSDev1 on the wall, verbatim: "For right now an 8 AM and a 5 PM
# email will work." Two a day, on the BUYER's clock, and nothing in between.
#
# THIS REPLACED AN EVENT-DRIVEN NOTIFIER, and that was the point of the ruling. The first version
# mailed on arrival behind a thirty-minute gap — which is a cadence, and cadence is his word, not
# a number a developer picks (CLAUDE.md). A busy Monday under the old shape was up to forty-eight
# emails; under this one it is two, and the second one says how the day ended.
#
# SLOTS, NOT A CRON. The worker ticks on its own schedule and a box can be asleep, restarted or an
# hour behind; asking "which slot is this, and has it already gone today" is answerable from one
# stored marker and is correct whenever the tick happens to land.
# EACH SLOT IS A WINDOW, NOT AN INSTANT, AND IT CLOSES. A worker tick is not a cron: the box can
# be asleep, restarted, or an hour behind, so a slot that opened at its hour has to stay open long
# enough to survive that. Four hours is long enough that a restart never costs the email and short
# enough that it is still the email the owner ruled — a box that was off all evening does not mail
# him at eleven at night to tell him about the afternoon. It simply waits for the morning.
SLOTS = {"morning": (8, 12), "evening": (17, 21)}


def due_slot(now: datetime | None = None) -> str | None:
    """Which of the day's two slots is open right now, on the buyer's clock. None outside both."""
    hour = _local(now or datetime.now(timezone.utc)).hour
    for name, (opens, closes) in SLOTS.items():
        if opens <= hour < closes:
            return name
    return None


def slot_key(key: str, now: datetime | None = None) -> str | None:
    """The marker for this key's slot TODAY, or None when no slot is open.

    The buyer's local DATE is in it, not the box's, so a box in UTC serving a Pacific buyer does
    not roll its day over in the middle of his afternoon and send the morning email twice.
    """
    slot = due_slot(now)
    if slot is None:
        return None
    local = _local(now or datetime.now(timezone.utc))
    return f"{key}:{local.date().isoformat()}:{slot}"


def _key(key: str) -> str:
    return f"notify:{key}"


def _notify_devices(person_id: str, waiting: int | None) -> int:
    """Notify every device this person has installed the app on. Returns how many were reached.

    NEVER RAISES, like everything else here — it is called from the poller's pass. A push service
    having a bad minute must leave the email floor intact rather than take intake down.

    THE PAYLOAD CARRIES A NUMBER, NEVER THE NOTICE'S WORDS. `subject` and `text_body` are written
    for a mail client behind a login; a notification renders on a locked screen in front of
    whoever is holding the phone. The count is the most that can be said safely.
    """
    try:
        from core import push
        subs = push.subscriptions_for(str(person_id))
        if not subs:
            return 0
        return sum(1 for sub in subs if push.send(sub, waiting=waiting)[0])
    except Exception as e:                     # noqa: BLE001 — the floor must survive this
        log.warning("notify.push_failed", error=str(e)[:120])
        return 0


def send_notice(key: str, subject: str, text_body: str, html_body: str | None = None, *,
                user_id: str | None = None, now: datetime | None = None,
                waiting: int | None = None) -> dict:
    """Tell this box's people one thing. NEVER RAISES. -> {"sent", "skipped", "detail"}.

    `key` is what the gap is counted against, so two different kinds of notice do not silence each
    other — and the SAME kind, arriving twenty times, is one email.

    The idempotency key is per key, per recipient, per gap window, so a worker that retries after
    an ambiguous timeout collapses to one vendor send rather than two identical emails.
    """
    now = now or datetime.now(timezone.utc)
    try:
        if not is_on():
            return {"sent": 0, "skipped": "off", "detail": "notifications are not configured"}
        marker = slot_key(key, now)
        if marker is None:
            # Between the two windows, or before the first. The night belongs to the buyer.
            return {"sent": 0, "skipped": "outside_slot",
                    "detail": "not one of the buyer's two daily slots"}
        if unsent(marker) is False:
            # ALREADY SENT THIS SLOT. The marker carries the buyer's local DATE and the slot name,
            # so a box that ticks every forty-five seconds sends once and then recognises itself
            # for the rest of the window.
            return {"sent": 0, "skipped": "already_sent", "detail": f"{marker} already went"}
        people = box_mail.to_box_people(user_id)
        if not people:
            return {"sent": 0, "skipped": "nobody", "detail": "this box has no signed-in people"}

        sent, failed, notified = 0, [], 0
        for p in people:
            # THE APP AND THEN THE MAIL, BOTH, EVERY TIME (owner, 2026-09-20, superseding his
            # own earlier ruling the same day). A notification that fails costs nothing here because the
            # mail goes regardless; that is the whole point of sending both while push is new.
            if _notify_devices(p["id"], waiting):
                notified += 1
            try:
                box_mail.send(p["email"], subject, text_body, html_body or _plain_html(text_body),
                              idem_key=f"notify:{marker}:{p['id']}",
                              sender_name=_sender_name(), note="notify")
                sent += 1
            except Exception as e:             # noqa: BLE001 — one bad address is not an outage
                # PER RECIPIENT, ON PURPOSE. A box with three people whose second address bounces
                # must still reach the first and the third.
                failed.append(str(e)[:120])
                log.warning("notify.send_failed", key=key, error=str(e)[:120])
        # A NOTIFICATION COUNTS AS DELIVERY FOR THE MARKER, and that matters now that both channels fire.
        # The marker is what stops the 15-minute tick re-sending inside one slot. Keyed on the mail
        # alone, a box whose push landed and whose mail bounced would mark nothing and notify the
        # phone again every quarter of an hour until the slot closed — the spam cannon this module
        # exists to prevent, arriving through the newer channel.
        delivered = sent + notified
        if delivered:
            # BUMPED ONLY WHEN SOMETHING ACTUALLY WENT. A failed round that marked the gap would
            # buy silence with nothing delivered, and the next real notice would be suppressed.
            state.set_alert(_key(marker), now.isoformat(), bump_alert_ts=True)
            log.info("notify.sent", key=key, people=sent, notified=notified)
        return {"sent": sent, "notified": notified,
                "skipped": "" if delivered else "all_failed",
                "detail": "; ".join(failed)}
    except Exception as e:                     # noqa: BLE001 — see the module docstring
        log.error("notify.unexpected", key=key, error=str(e)[:160])
        return {"sent": 0, "skipped": "error", "detail": str(e)[:160]}


def _sender_name() -> str:
    from core.config import get_config
    return str((get_config().get("brand") or {}).get("name") or "Your box").strip() or "Your box"


def _plain_html(text_body: str) -> str:
    """A readable HTML part built from the text one. Resend wants both; writing two bodies by hand
    is how they drift, and a notice is four lines."""
    import html as _h
    lines = [f"<p style=\"margin:0 0 12px\">{_h.escape(l)}</p>"
             for l in str(text_body).splitlines() if l.strip()]
    return ('<div style="font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;color:#1d2330">'
            + "".join(lines) + "</div>")
