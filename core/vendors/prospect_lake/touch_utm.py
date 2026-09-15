"""Shared `touch.utm` builders — ONE source of truth so every channel (GTM email,
Messenger, IG/Boosend) logs `touch.utm` with the SAME key vocabulary + value
conventions (contract §3 `touch.utm`, §5 UTM keys). WS4 (OSDev6).

The invariant every channel satisfies: a `touch.utm` ALWAYS carries a non-empty
`utm_source` + `utm_content` (the two keys the contract guarantees on every channel).
`utm_medium` / `utm_campaign` are populated where the channel HAS one; IG is
source+content only, by contract design (organic automation — no campaign/medium).

Value map (so `prospect_touches.utm` is queryable with one mental model):
  utm_source   gtm_email | messenger | ig          (the channel token — exact strings)
  utm_content  created|touch1|touch2|reply | <meta_ad_id>|organic | <automation>
  utm_medium   email (GTM) · paid_social (Messenger) · —omit— (IG)
  utm_campaign gtm_cold_outbound (GTM) · <meta_campaign> (Messenger, when captured) · —omit—

This module imports NOTHING from the package (leaf) — safe to import anywhere, no cycle.
"""

# ── GTM email (WS3) ───────────────────────────────────────────────────────────
UTM_SOURCE_GTM = "gtm_email"
UTM_MEDIUM_EMAIL = "email"
UTM_CAMPAIGN_GTM = "gtm_cold_outbound"   # the two-touch cold-email sequence

# per-touch `utm_content` vocabulary — one stable token per touch kind
CREATED = "created"
TOUCH1 = "touch1"
TOUCH2 = "touch2"
REPLY = "reply"


def gtm_utm(content: str) -> dict:
    """UTM blob for a GTM touch. `content` ∈ {CREATED,TOUCH1,TOUCH2,REPLY} (or `touch{N}`
    from a link) — it's what distinguishes the touch on the inbound click."""
    return {"utm_source": UTM_SOURCE_GTM, "utm_medium": UTM_MEDIUM_EMAIL,
            "utm_campaign": UTM_CAMPAIGN_GTM, "utm_content": content}


# ── Messenger (WS2) ───────────────────────────────────────────────────────────
UTM_SOURCE_MESSENGER = "messenger"
UTM_MEDIUM_PAID_SOCIAL = "paid_social"   # click-to-Messenger ads are paid social
MESSENGER_ORGANIC = "organic"            # a non-ad (direct) Messenger DM — stable token


def messenger_utm(meta_ad_id: str | None, *, campaign: str | None = None) -> dict:
    """UTM blob for a Messenger touch. `utm_content` = the meta_ad_id for a click-to-DM
    ad lead, or `organic` for a direct DM (never an empty string — that fails the shared
    invariant and isn't queryable). `campaign` optional — the Meta campaign id/name once
    the poller captures it (today it records meta_ad_id, not the campaign)."""
    utm = {"utm_source": UTM_SOURCE_MESSENGER, "utm_medium": UTM_MEDIUM_PAID_SOCIAL,
           "utm_content": meta_ad_id or MESSENGER_ORGANIC}
    if campaign:
        utm["utm_campaign"] = campaign
    return utm


# ── Instagram / Boosend (WS1) ─────────────────────────────────────────────────
UTM_SOURCE_IG = "ig"


def ig_utm(automation: str) -> dict:
    """UTM blob for an IG/Boosend touch. Source + content ONLY, by contract design
    (organic automation — no per-person pid, no campaign/medium). `utm_content` = the
    Boosend automation name (one stable token per automation)."""
    return {"utm_source": UTM_SOURCE_IG, "utm_content": automation}


# ── the shared invariant ──────────────────────────────────────────────────────
def assert_touch_utm(utm: dict) -> None:
    """Fail-closed shape guard: every `touch.utm` MUST carry a non-empty `utm_source`
    + `utm_content` (contract §5 — the two keys guaranteed on every channel). Called at
    enqueue time so a malformed shape can't leave the box — mirrors the `stage_hint`
    guard in `outbox.enqueue`."""
    if not (isinstance(utm, dict) and utm.get("utm_source") and utm.get("utm_content")):
        raise ValueError(
            f"touch.utm must carry non-empty utm_source + utm_content, got {utm!r}")
