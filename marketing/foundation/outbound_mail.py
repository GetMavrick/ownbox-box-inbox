"""One email to one person who asked for it: the marketing foundation's outbound mail.

OSDev1, 2026-10-02 (the Lead Magnet's welcome email): "YES, but as a marketing foundation, declared via needs:, not
core/. It needs consent evidence (the DM), suppression, an unsubscribe link, an exactly-once ledger, and the
owner's transport. Default cap 20/hour." A machine that delivers what a person asked for (a guide they requested in
a DM) says `needs: [foundation:marketing]` and calls `send()`.

WHAT IT IS NOT. Not cold email, not a newsletter, not a campaign. Every send names the conversation and the
message in which the person asked, the box checks that evidence against the conversation itself, and the
evidence is stored on the row, so "why did this address get mail?" always has an answer a person can read.

THE RULES, AND EACH ONE IS SAID IN A SENTENCE, NEVER SENT AROUND (OSDev1's review of #1810 is in brackets):
  1. consent, VERIFIED [4]: the named message is in the named conversation, it came from the person (inbound),
     and the address being mailed was written by them in that conversation
  2. the person did not say STOP there [3], the address is not on the box's suppression list, the box is
     running, and the box has a way to send (the owner's own transport, /settings/email)
  3. a working opt-out: the box's signing key and https address are set
  4. the hourly cap (20 by default, the owner signs it off), counted in the same transaction as the claim, so
     two workers can't both take the twentieth slot; over the cap the email WAITS and goes next hour [5]
  5. exactly once. Each key moves through queued -> sending -> sent, or failed / unknown:
       * a transient error (a Resend 429/503, an SMTP connection that never logged in) waits and retries with
         the SAME transport key, which Resend deduplicates and SMTP had not claimed yet [2]
       * a definite refusal is `failed`; a later send() of the same key is a NEW attempt with a new transport
         key, because SMTP keeps its claim on a refusal and would otherwise report "sent" for nothing [1]
       * a connection that dropped mid-send is `unknown` and is never sent again (invariant 4) [1]
       * the box's own email allowance used up (cost_guard: BudgetExceeded, RateCapped) waits for room, never
         counted as a try, never given up [re-review]
`drain()` runs in the worker every minute and sends whatever waits, re-checking rule 2 before each one. A STOP or
an unsubscribe drops the waiting email; a stopped box or a brief hiccup only holds it for another look.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from core import state
from core.logging import get_logger

log = get_logger(__name__)

HOURLY_CAP = 20                      # OSDev1's default (2026-10-02); the owner signs off the number
MAX_TRIES = 8                        # transient retries before a waiting email is given up as failed
_NS, _CAP_KEY = "website", "outbound_mail.hourly_cap"   # box_settings: the owner's own number, when set
_KEY = re.compile(r"^[A-Za-z0-9:_.-]{1,160}$")
_MACHINE = re.compile(r"^[a-z][a-z0-9_-]{1,40}$")
_COUNTED = "('sending','sent','unknown')"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%S")


def hourly_cap() -> int:
    try:
        from core import box_settings
        v = int(box_settings.get(_NS, _CAP_KEY, default=HOURLY_CAP))
        return v if v > 0 else HOURLY_CAP
    except Exception:                                    # noqa: BLE001 — an unreadable setting is the default
        return HOURLY_CAP


def set_hourly_cap(n: int, *, set_by: str) -> int:
    """The one place the number is changed (a machine's settings page calls this). -> the number now in force."""
    from core import box_settings
    n = int(n)
    if not 1 <= n <= 500:
        raise ValueError("the hourly limit is a number from 1 to 500")
    box_settings.put(_NS, _CAP_KEY, n, set_by=set_by)
    return n


def sent_last_hour(c=None) -> int:
    q = (f"SELECT COUNT(*) n FROM outbound_mail WHERE status IN {_COUNTED} AND last_try_at > ?",
         (_iso(_now() - timedelta(hours=1)),))
    if c is not None:
        row = c.execute(*q).fetchone()
    else:
        with state.connect() as cc:
            row = cc.execute(*q).fetchone()
    return int(row["n"]) if row else 0


def _out(status: str, reason: str = "", message_id: str | None = None) -> dict:
    return {"status": status, "reason": reason, "message_id": message_id}


def _refused(reason: str) -> dict:
    return _out("refused", reason)


# ── the checks every send makes, again before every retry ─────────────────────────────────────────

def _consent_problem(addr: str, consent: dict) -> str:
    """"" when the evidence holds, else the sentence. Read through core.conversations, never the Inbox."""
    from core import conversations
    try:
        prov = conversations.provider()
    except conversations.NoProvider:
        return "this box has no inbox to check the consent against, so nothing is sent"
    try:
        msgs = prov.messages(conversation=consent["conversation"], limit=500) or []
    except Exception:                                    # noqa: BLE001 — unreadable evidence is no evidence
        return "the conversation couldn't be read to check the consent, so nothing is sent"
    asked = [m for m in msgs if str(m.get("id") or "") == consent["message"]]
    if not asked:
        return "no consent: that message isn't in that conversation"
    if str(asked[0].get("direction") or "") != "in":
        return "no consent: that message was sent by the box, not by the person"
    # The whole address, not a piece of one: "a@example.com" is not in "dana@example.com" (a trailing full stop is fine).
    written = re.compile(r"(?<![\w.+-])" + re.escape(addr) + r"(?![\w-]|\.\w)")
    if not any(str(m.get("direction") or "") == "in" and written.search(str(m.get("body") or "").lower())
               for m in msgs):
        return "no consent: the person never wrote this address in that conversation"
    return ""


def _stop_problem(addr: str, consent: dict) -> tuple[str, bool]:
    """("", False) when nothing says stop, else (the sentence, final). FINAL is the person's own word (a STOP in
    the DM, an unsubscribe): a waiting email is dropped for it. A stopped box, or an inbox that can't be read right
    now, only holds the email: it waits and is checked again (OSDev1's re-review of #1810)."""
    from core import compliance, conversations, pause
    if compliance.is_suppressed(addr):
        return "this address unsubscribed, so nothing is sent to it", True
    try:
        if conversations.provider().opted_out(conversation=consent["conversation"]):
            return "this person said STOP in the conversation, so nothing is sent", True
    except Exception:                                    # noqa: BLE001 — can't tell whether they said stop: don't
        return "the box couldn't check whether this person said STOP, so nothing is sent", False
    if pause.is_paused():
        return "the box is stopped, so no email is sent", False
    return "", False


def _transport_problem(addr: str) -> str:
    from core import box_mail, compliance
    from core.config import settings
    if not box_mail.is_configured():
        return "this box has no way to send email yet: set it up on Settings, Email"
    try:
        # ONE RULE FOR A WORKING OPT-OUT, the one cold email uses: an https address and the box's own signing key.
        compliance.assert_unsub_reachable(addr)
    except compliance.ComplianceError:
        if not (getattr(settings, "unsub_signing_key", "") or "").strip():
            return "the box's unsubscribe signing key isn't set, so a working opt-out can't be made; nothing is sent"
        return "the box's web address isn't set, so an unsubscribe link wouldn't work; nothing is sent"
    return ""


# ── the ledger ───────────────────────────────────────────────────────────────────────────────────

def _row(c, key: str) -> dict | None:
    r = c.execute("SELECT * FROM outbound_mail WHERE idem_key = ?", (key,)).fetchone()
    return dict(r) if r else None


def _take(key: str, *, machine: str, to: str, subject: str, text: str, html: str, sender: str,
          consent: dict) -> tuple[str, dict | None]:
    """One transaction: the cap, then the claim. -> ("send" | "queued" | <the existing status>, row)."""
    import json
    now = _now()
    with state.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = _row(c, key)
        if row and row["status"] in ("sent", "sending", "unknown", "queued"):
            return row["status"], row
        attempt = int(row["attempt"]) + 1 if row else 1          # a failed key gets a NEW transport key
        tkey = f"outbound:{key}:{attempt}"
        over = sent_last_hour(c) >= hourly_cap()
        status = "queued" if over else "sending"
        c.execute("INSERT OR REPLACE INTO outbound_mail (idem_key, machine, to_addr, subject, body_text, body_html, "
                  "sender_name, consent, status, attempt, transport_key, tries, next_at, last_try_at, message_id, "
                  "error, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,0,?,?,NULL,NULL,?)",
                  (key, machine, to, subject[:200], text, html, sender, json.dumps(consent, sort_keys=True)[:2000],
                   status, attempt, tkey, _iso(now), _iso(now) if not over else None,
                   row["created_at"] if row else _iso(now)))
        return ("queued" if over else "send"), _row(c, key)


def _set(key: str, **fields) -> None:
    cols = ", ".join(f"{k} = ?" for k in fields)
    with state.connect() as c:
        c.execute(f"UPDATE outbound_mail SET {cols} WHERE idem_key = ?", (*fields.values(), key))


def _attempt(row: dict) -> dict:
    """Hand one row to the owner's transport. The row is already `sending` and counted."""
    from core import box_mail, compliance
    from core.config import settings
    from core.exceptions import BudgetExceeded, RateCapped, RetryableError, VendorError
    key, addr = row["idem_key"], row["to_addr"]
    unsub = compliance.box_unsubscribe_url(addr)
    postal = (getattr(settings, "physical_address", "") or "").strip()
    footer_text = f"\n\n--\nYou asked for this in a message to us. Unsubscribe: {unsub}"
    footer_html = (f'<p style="font-size:13px;color:#666">You asked for this in a message to us. '
                   f'<a href="{unsub}">Unsubscribe</a></p>')
    if postal:
        footer_text += f"\n{postal}"
        footer_html += f'<p style="font-size:13px;color:#666">{postal}</p>'
    html = row.get("body_html") or ""
    try:
        mid = box_mail.send(addr, row["subject"], row["body_text"] + footer_text,
                            (html + footer_html) if html else "", idem_key=row["transport_key"],
                            sender_name=row.get("sender_name") or "Ownbox", note=f"outbound_mail:{row['machine']}",
                            headers={"List-Unsubscribe": f"<{unsub}>",
                                     "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"})
    except (BudgetExceeded, RateCapped) as e:
        # THE BOX'S OWN LIMIT, CHECKED BEFORE ANY SEND OR CLAIM (cost_guard.check_vendor): nothing went. The email
        # waits for the budget or the cap, and it isn't a failed try, so it is never given up for this.
        # Resend's month is shared with the box's other mail (OSDev1's re-review of #1810).
        at = _iso(_now() + timedelta(hours=6 if getattr(e, "hard", False) else 1))
        if isinstance(e, RateCapped) and getattr(e, "not_before", ""):
            at = str(e.not_before)[:19]
        _set(key, status="queued", next_at=at, error=f"{type(e).__name__}: {e}"[:300])
        log.info("outbound_mail.held_by_budget", key=key, kind=type(e).__name__, until=at)
        return _out("queued", "the box's email allowance is used up for now; it goes when there is room again")
    except RetryableError as e:
        # NOTHING WAS CLAIMED OR RESEND WILL DEDUPLICATE: wait and retry with the SAME transport key.
        tries = int(row.get("tries") or 0) + 1
        if tries >= MAX_TRIES:
            _set(key, status="failed", tries=tries, error=f"gave up after {tries} tries: {e}"[:300])
            log.warning("outbound_mail.gave_up", key=key, tries=tries)
            return _refused("the email service kept failing, so it was given up; nothing was sent")
        wait = min(60, 2 ** tries)
        _set(key, status="queued", tries=tries, next_at=_iso(_now() + timedelta(minutes=wait)), error=str(e)[:300])
        log.info("outbound_mail.retry_later", key=key, tries=tries, minutes=wait)
        return _out("queued", f"the email service was busy; it goes again in {wait} minutes")
    except VendorError as e:
        if str(getattr(e, "status", "")) == "indeterminate":
            # THE CONNECTION DROPPED MID-SEND: it may have gone, so it is never sent again (invariant 4).
            _set(key, status="unknown", error=str(e)[:300])
            log.warning("outbound_mail.indeterminate", key=key)
            return _out("unknown", "it may have gone, so it isn't sent again")
        _set(key, status="failed", error=f"{e}"[:300])
        log.warning("outbound_mail.failed", key=key, error=str(getattr(e, "status", "")))
        return _refused("the email service refused it, so it wasn't sent")
    except Exception as e:                               # noqa: BLE001 — unknown failure: may or may not have gone
        _set(key, status="unknown", error=f"{type(e).__name__}: {e}"[:300])
        log.error("outbound_mail.unexpected", key=key, error=type(e).__name__)
        return _out("unknown", "something unexpected happened while sending, so it isn't sent again")
    _set(key, status="sent", message_id=str(mid), error=None)
    log.info("outbound_mail.sent", machine=row["machine"], key=key)
    return _out("sent", "", str(mid))


# ── the two ways in ──────────────────────────────────────────────────────────────────────────────

def send(*, machine: str, to: str, subject: str, text: str, key: str, consent: dict,
         html: str = "", sender_name: str = "") -> dict:
    """One email to a person who asked for it. Never raises for an outcome.

    -> {"status": "sent" | "queued" | "duplicate" | "refused" | "unknown", "reason", "message_id"}
    `queued` will go by itself (over the hourly cap, or the email service was busy). `consent` =
    {"conversation": <id>, "message": <id of the message where they asked>}.
    """
    from core import compliance
    machine, key = str(machine or "").strip(), str(key or "").strip()
    if not _MACHINE.match(machine):
        return _refused("say which machine is sending")
    if not _KEY.match(key):
        return _refused("a send needs a key of letters, digits and : _ . - so it goes once")
    consent = {"conversation": str((consent or {}).get("conversation") or "").strip(),
               "message": str((consent or {}).get("message") or "").strip()}
    if not consent["conversation"] or not consent["message"]:
        return _refused("no consent: name the conversation and the message where this person asked for it")
    try:
        addr = compliance.normalize_email(to)
    except Exception:                                    # noqa: BLE001 — a bad address is a refusal
        return _refused("that isn't an email address")
    text, subject = str(text or "").strip(), str(subject or "").strip()
    if not text or not subject:
        return _refused("an email needs a subject and words")
    for problem in (_consent_problem(addr, consent), _stop_problem(addr, consent)[0], _transport_problem(addr)):
        if problem:
            return _refused(problem)
    try:
        from core import dash
        sender = sender_name or dash.brand()
    except Exception:                                    # noqa: BLE001
        sender = sender_name or "Ownbox"
    took, row = _take(key, machine=machine, to=addr, subject=subject, text=text, html=html or "",
                      sender=sender, consent=consent)
    if took == "sent":
        return _out("duplicate")
    if took in ("sending", "unknown"):
        return _out("unknown", "an earlier attempt may have gone, so it isn't sent again")
    if took == "queued":
        return _out("queued", "the box has sent its emails for this hour; this one goes as soon as the hour allows")
    return _attempt(row)


def drain(limit: int = 25) -> dict:
    """The worker's minute: send whatever waits and is due, under the cap, re-checking STOP and suppression."""
    import json
    sent = waited = dropped = held = 0
    with state.connect() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM outbound_mail WHERE status = 'queued' AND (next_at IS NULL OR next_at <= ?) "
            "ORDER BY created_at LIMIT ?", (_iso(_now()), int(limit))).fetchall()]
    for row in rows:
        consent = json.loads(row.get("consent") or "{}")
        why, final = _stop_problem(row["to_addr"], consent)
        if why and final:                                # the person's own word: the email is dropped
            _set(row["idem_key"], status="failed", error=why[:300])
            dropped += 1
            continue
        why = why or _transport_problem(row["to_addr"])
        if why:                                          # a hiccup or a stopped box: it waits, checked again later
            _set(row["idem_key"], next_at=_iso(_now() + timedelta(minutes=15)), error=why[:300])
            held += 1
            continue
        with state.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            if sent_last_hour(c) >= hourly_cap():
                waited += 1
                break
            moved = c.execute("UPDATE outbound_mail SET status = 'sending', last_try_at = ? "
                              "WHERE idem_key = ? AND status = 'queued'", (_iso(_now()), row["idem_key"])).rowcount
        if not moved:
            continue
        out = _attempt(dict(row, status="sending"))
        sent += out["status"] == "sent"
    if sent or dropped or held:
        log.info("outbound_mail.drained", sent=sent, dropped=dropped, waiting=waited, held=held)
    return {"sent": sent, "dropped": dropped, "waiting": waited, "held": held}
