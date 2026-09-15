"""The legal layer — suppression + CAN-SPAM enforcement. Built FIRST, fails CLOSED.

WHY THIS IS IN `core/` AND NOT IN A MACHINE (OSDev1, 2026-09-06, ruling option B). An
opt-out is honoured by EVERY machine, whichever one asked. It lived in the Lead Machine, and
the Content Machine's keyword funnel — a LISTED, SOLD feature of the $99 Content Machine —
reached across to it, which is why a Content box had to ship a two-file slice of a product
its buyer did not purchase. Two separate suppression lists (the other option) is an opt-out
one machine honours and another breaks, and that is a bad day nobody recovers from.

WHAT STAYED BEHIND, AND WHY IT MATTERS. `assert_listable` / `assert_pushable` are NOT here.
They carry the INVESTOR WALL, which reads the Lead Machine's own `investors_store` — a lead
concept that has no business in core. Lifting them would have done one of two bad things:
dragged an investor table into the foundation, or turned the wall into something registered
at import, which fails OPEN the moment nobody registers it. A compliance check that silently
stops checking is worse than one that lives in the wrong file. So the machine-neutral half —
suppression, the footer, the hosted unsubscribe — is here, and the Lead Machine keeps the
gate that needs its own data.

Three primitives, in dependency order:

  suppress() / is_suppressed()   append-only opt-out registry. An address that
                                 opted out stays out forever: rows are never
                                 deleted, re-suppression never weakens a row,
                                 and suppressing also freezes any un-pushed
                                 lead with that address.
  enforce_footer()               every outbound body must carry a working
                                 unsubscribe mechanism AND the sender's
                                 physical postal address (CAN-SPAM §5(a)(5)).
                                 No address configured → nothing sends.
  assert_pushable()              THE gate. Every push path calls this with the
                                 recipient and the final rendered body; it
                                 raises ComplianceError (terminal — retrying
                                 won't make it legal) unless both checks pass.

Suppression matches the address as used (lowercased, trimmed). Dot/plus-alias
equivalence games are out of scope on purpose: we suppress what the recipient
gave the vendor, which is what the vendor sends to.
"""
import re

from core import state
from core.config import settings
from core.exceptions import AIOSError
from core.logging import get_logger

log = get_logger(__name__)

VALID_REASONS = ("unsubscribe", "bounce", "complaint", "manual",
                 # Cross-channel reply-back (WAALAXY_INTEGRATION_PLAN §5.2): the owner
                 # marks a LinkedIn reply by hand and the email drip halts the same
                 # moment. Not an opt-out semantically, but the same wall enforces it.
                 "replied_linkedin")

# An unsubscribe mechanism = the literal word (rendered footer) or a vendor
# merge tag that becomes one ({{unsubscribe}} et al). Case-insensitive.
_UNSUB_RE = re.compile(r"unsubscribe", re.I)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ComplianceError(AIOSError):
    """Terminal by contract: the worker never retries it, because a retry
    cannot make an illegal send legal. Fix the input, not the schedule."""


def normalize_email(raw: str) -> str:
    e = (raw or "").strip().lower().strip("<>")
    if not _EMAIL_RE.match(e):
        raise ComplianceError(f"not an email address: {raw!r}")
    return e


def suppress(email: str, reason: str = "manual", *, note: str = "") -> dict:
    """Add to the do-not-contact registry (idempotent, append-only).

    Re-suppressing keeps the ORIGINAL row (first opt-out wins; nothing ever
    downgrades it) and also flips any of this address's leads that haven't
    been pushed yet, so nothing already in the pipeline slips out.
    """
    e = normalize_email(email)
    r = reason if reason in VALID_REASONS else "manual"
    with state.connect() as c:
        cur = c.execute(
            "INSERT INTO gtm_suppression (email, reason, added_at, note) "
            "VALUES (?,?,?,?) ON CONFLICT(email) DO NOTHING",
            (e, r, state._now(), note[:300]))
        new = cur.rowcount > 0
        # THE LEAD MACHINE'S ROW IS MARKED ONLY WHERE THAT MACHINE LIVES. gtm_suppression is kernel
        # (every channel honours an opt-out); gtm_leads ships only on a Lead image. A Unified Inbox
        # box records the suppression and has no lead row to mark — correct, not an error.
        frozen = 0
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='gtm_leads'").fetchone():
            frozen = c.execute(
                "UPDATE gtm_leads SET status = 'suppressed', updated_at = ? "
                "WHERE email = ? AND status != 'pushed'",
                (state._now(), e)).rowcount
    log.info("gtm.suppress", email=e, reason=r, new=new, leads_frozen=frozen)
    return {"email": e, "reason": r, "new": new, "leads_frozen": frozen}


def _plus_stripped(e: str) -> str:
    """'tom+news@x.com' -> 'tom@x.com'. Empty when there is no tag to strip."""
    local, _, domain = e.partition("@")
    base, plus, _tag = local.partition("+")
    return f"{base}@{domain}" if (plus and base and domain) else ""


def is_suppressed_identifier(value: str) -> bool:
    """True if this EXACT identifier has opted out. For things that are not email addresses.

    `is_suppressed` normalises its argument as an address and fails closed on anything it cannot
    parse — right for mail, and wrong for a LinkedIn profile URL, which it would read as an
    unparseable address and refuse forever. That is not caution, it is a false positive that
    closes a channel entirely (measured 2026-09-07: every register lead refused).

    So this matches the literal string, with no normalisation and no plus-stripping, which is
    what an opt-out against a non-address would have stored. An empty value is suppressed:
    nothing is a valid identifier, and we do not contact a row we cannot name.
    """
    v = (value or "").strip().lower()
    if not v:
        return True
    with state.connect() as c:
        return c.execute("SELECT 1 FROM gtm_suppression WHERE lower(email) = ?", (v,)).fetchone() is not None


def is_suppressed(email: str) -> bool:
    """True if this address must not be mailed. Deliberately over-inclusive.

    Checks the literal address AND its plus-stripped base, because sub-addressing
    routes to ONE mailbox at the providers that support it. The dangerous direction is
    the realistic one: an owner replies "unsubscribe" from tom+contact@, we suppress
    exactly that string, and then keep mailing tom@ — the same human, who has already
    told us to stop. Checking both closes it symmetrically.

    The cost of being wrong each way is what decides this. A provider that treats '+'
    literally means we may suppress a genuinely different mailbox and lose a lead. A
    missed opt-out is a CAN-SPAM violation. Those are not close, and the house rule is
    that compliance fails closed.

    STORAGE is unchanged — still append-only, still first-opt-out-wins, still the exact
    string that opted out. Only the MATCH is widened, so nothing about the audit trail
    of who asked to stop becomes fuzzy.
    """
    try:
        e = normalize_email(email)
    except ComplianceError:
        return True          # unparseable address: fail closed, never send
    candidates = [e]
    base = _plus_stripped(e)
    if base:
        candidates.append(base)
    marks = ", ".join("?" * len(candidates))
    with state.connect() as c:
        hit = c.execute(
            f"SELECT 1 FROM gtm_suppression WHERE email IN ({marks}) "
            # ...and the reverse: a tagged address suppressed under its own string must
            # also block the base address we may hold separately in the list.
            f"   OR (instr(email, '+') > 0 AND substr(email, 1, instr(email, '+') - 1)"
            f"       || substr(email, instr(email, '@')) IN ({marks}))",
            (*candidates, *candidates)).fetchone()
    return hit is not None


def suppression_count() -> int:
    with state.connect() as c:
        return c.execute("SELECT COUNT(*) AS n FROM gtm_suppression").fetchone()["n"]


def enforce_footer(body: str) -> None:
    """Raise unless the rendered body carries an unsubscribe mechanism AND the
    configured physical postal address. No AIOS_PHYSICAL_ADDRESS → fail closed."""
    addr = (settings.physical_address or "").strip()
    if not addr:
        raise ComplianceError(
            "AIOS_PHYSICAL_ADDRESS is not set — outbound email legally requires "
            "a physical postal address in the footer (CAN-SPAM). Set it in .env.")
    text = body or ""
    if not _UNSUB_RE.search(text):
        raise ComplianceError(
            "outbound body has no unsubscribe mechanism — add an unsubscribe "
            "link/line (or the vendor's {{unsubscribe}} merge tag) to the footer")
    if addr.lower() not in text.lower():
        raise ComplianceError(
            "outbound body is missing the physical postal address footer")


def ensure_footer(body: str) -> str:
    """Deterministic REPAIR (charter standard: 're-appended in code if the model
    ever omits them'): return the body with a compliant footer guaranteed —
    appending the unsubscribe merge tag and/or the postal address when missing.
    Pairs with enforce_footer(): repair at compose time, verify at the gate,
    so a model regression can never ship a non-compliant email. Still fails
    closed when no postal address is configured — a footer can't be invented."""
    addr = (settings.physical_address or "").strip()
    if not addr:
        raise ComplianceError(
            "AIOS_PHYSICAL_ADDRESS is not set — outbound email legally requires "
            "a physical postal address in the footer (CAN-SPAM). Set it in .env.")
    out = (body or "").rstrip()
    footer_lines = []
    if not _UNSUB_RE.search(out):
        footer_lines.append("Unsubscribe: {{unsubscribe}}")
    if addr.lower() not in out.lower():
        footer_lines.append(addr)
    if footer_lines:
        out = out + "\n\n" + "\n".join(footer_lines)
    return out


# ── hosted one-click unsubscribe (for the Resend send backend, which — unlike
#    Instantly — does not process the {{unsubscribe}} merge tag, so WE host the
#    opt-out and own the suppression). HMAC-signed so a link is tamper-evident;
#    over-suppression would be fail-safe anyway (we err toward not-contacting).
def _unsub_sig(email: str) -> str:
    import hashlib
    import hmac as _hmac
    # Dedicated signing key: opt-out links must SURVIVE a bearer-token rotation
    # (CAN-SPAM requires the opt-out to keep working ≥30 days after the send;
    # the bearer is rotatable by design). install_services.sh generates
    # UNSUB_SIGNING_KEY into .env; the bearer-derived fallback keeps old boxes
    # working but logs loudly so it gets set before real sends.
    key = (getattr(settings, "unsub_signing_key", "") or "").encode()
    if not key:
        log.warning("gtm.unsub_key_fallback_set_UNSUB_SIGNING_KEY_in_env")
        key = (getattr(settings, "dispatch_bearer_token", "") or "aios-unsub").encode()
    return _hmac.new(key, email.encode(), hashlib.sha256).hexdigest()[:40]


def unsubscribe_url(email: str) -> str:
    """The public one-click unsubscribe link to embed in an outbound email."""
    import urllib.parse
    e = normalize_email(email)
    base = (getattr(settings, "dashboard_base_url", "") or "").rstrip("/")
    return f"{base}/gtm/unsubscribe?e={urllib.parse.quote(e)}&s={_unsub_sig(e)}"


def verify_unsubscribe(email: str, sig: str) -> bool:
    """Constant-time check that this unsubscribe link is authentic."""
    import hmac as _hmac
    try:
        e = normalize_email(email)
    except ComplianceError:
        return False
    return _hmac.compare_digest(_unsub_sig(e), sig or "")


def assert_unsub_reachable(email: str) -> None:
    """Raise ComplianceError unless the unsubscribe URL for this address is
    absolute and https — i.e. DASHBOARD_BASE_URL is set and starts with https://.
    A relative link (DASHBOARD_BASE_URL unset) is a broken opt-out and violates
    CAN-SPAM's ≥30-day working-unsubscribe requirement. Terminal: retrying won't
    fix a misconfigured env."""
    url = unsubscribe_url(email)
    if not url.startswith("https://"):
        raise ComplianceError(
            "DASHBOARD_BASE_URL is not set (or not https) — the unsubscribe link "
            "would be a relative path, which is a broken opt-out. "
            "Set DASHBOARD_BASE_URL=https://your-domain in .env before sending.")

    # A BOX MUST SIGN WITH ITS OWN KEY. Unset, _unsub_sig falls back to the bearer and then to
    # a literal shipped in this source file — so every clone that never set one would sign with
    # the SAME publicly-readable constant, and anyone could forge an opt-out for any address on
    # any box. The VPS path generates a key (scripts/install_services.sh); a laptop install runs
    # neither bootstrap nor install_services, which is exactly how a box reaches a real send
    # without one. Verification of ALREADY-SENT links is untouched — this gates new sends only.
    if not (getattr(settings, "unsub_signing_key", "") or "").strip():
        raise ComplianceError(
            "UNSUB_SIGNING_KEY is not set — unsubscribe links would be signed with a shared "
            "default that ships in the source, so anyone could forge an opt-out. Generate one:\n"
            "  python -c \"import secrets; print('UNSUB_SIGNING_KEY=' + secrets.token_hex(32))\" >> .env\n"
            "Then restart. Keep it forever: CAN-SPAM requires opt-out links to keep working.")
