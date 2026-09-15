"""The compliance spine — what may be sent, on which platform, right now.

CODE DECIDES, NEVER THE MODEL AND NEVER A CALLER'S JUDGMENT. That was true of the first version
and it is the only thing about it that survives unchanged.

WHY THIS IS NO LONGER ONE NUMBER. The first version hardcoded `FREEFORM_HOURS = 24` — Meta's
Standard Messaging Window — and applied it to everything. Correct for Messenger, and wrong or
absent on every other platform the machine is growing into. A single global constant applied to
TikTok either blocks a legal reply or permits an illegal one, and there is no way to tell which
from the outside.

EVERY RULE BELOW CARRIES ITS CITATION, and that is a hard requirement rather than courtesy
(docs/SPEC_CUSTOMER_VOICE_BUILD.md §4.3, researched in docs/SPEC_CUSTOMER_VOICE_SEND_WINDOWS.md).
The claim that started this — that TikTok accounts had to be reconnected — came from a screenshot
and turned out to have no primary source anywhere. A compliance rule taken from a screenshot is
the dead-vendor-client failure again with a legal tail.

FAIL CLOSED ON AN UNKNOWN PLATFORM. A channel nobody wrote a rule for must REFUSE, never inherit
Messenger's. That is the single most important line in this file: it means adding a platform
string to the poller cannot silently authorise sending on it.

WHAT THIS MODULE DOES NOT DECIDE. Whether the one allowed message has already been sent. That is
a claim, not a clock — see `store.claim_opener`, and §2 of the research doc on why an Instagram
comment reply cannot be modelled as a window at all.
"""
from datetime import datetime, timedelta, timezone

# ── decisions ────────────────────────────────────────────────────────────────────────────
FREEFORM = "freeform"          # send it, no tag needed
TAGGED = "tagged"              # outside the free window, but a tag lane is open — NON-PROMOTIONAL
LIMITED = "limited"            # allowed, but a bounded number remain (TikTok's degraded state)
BLOCKED = "blocked"            # do not send

# ── the rules, one per channel, each with its source ─────────────────────────────────────
#
# `free_hours`  hours from the person's last inbound message during which a free-form reply is
#               allowed. None = no window is documented for this channel.
# `tag_hours`   hours during which a tagged, NON-PROMOTIONAL reply is allowed. None = no tag lane
#               we have confirmed; the module will never invent one.
# `after_tag`   what is left once even the tag window closes.
#
# NOTE ON PROMOTIONAL CONTENT, which is easy to miss and is a real legal difference: Meta allows
# promotional content INSIDE the free window and forbids it in a tagged message OUTSIDE it. Same
# text, different legality, decided by the clock. A drafter must be told which it is, which is why
# TAGGED is a distinct decision and not a flavour of FREEFORM.
_RULES = {
    # "Businesses have up to 24 hours to respond to a user."
    #   developers.facebook.com/documentation/business-messaging/messenger-platform/policy
    # "The Human Agent feature allows your app to have a human agent respond to user messages
    #  using the human_agent tag within 7 days of a user's message."
    #   developers.facebook.com/docs/features-reference/human-agent
    # The tag is a GATED FEATURE: App Review plus business verification. Held by the connected
    # vendor's Meta app, not ours — so `tag_hours` here says what the PLATFORM permits, and a
    # caller still has to hold the feature.
    "messenger": {
        "free_hours": 24, "tag_hours": 24 * 7,
        "cite": "https://developers.facebook.com/documentation/business-messaging/"
                "messenger-platform/policy",
        "cite_tag": "https://developers.facebook.com/docs/features-reference/human-agent",
    },

    # "Your app has 24 hours to respond to any message sent from an Instagram user to your app
    #  user."
    #   developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/messaging-api/
    #
    # NO TAG LANE HERE, DELIBERATELY. Instagram's own page confirms a human-agent tag exists but
    # DOES NOT STATE ITS DURATION, and a second Meta source claims the 7-day tag is unavailable on
    # the Instagram Messaging API at all. Two Meta-primary sources that disagree is not a rule.
    # 24 hours is the half both agree on, so 24 hours is what ships. Raising this needs a citation,
    # not a guess.
    "instagram": {
        "free_hours": 24, "tag_hours": None,
        "cite": "https://developers.facebook.com/docs/instagram-platform/"
                "instagram-api-with-instagram-login/messaging-api/",
        # WHY tag_hours IS None, carried as data so it survives a refactor that drops comments.
        "tag_unconfirmed": "Meta's Instagram page confirms a human-agent tag exists but does not "
                           "state its duration, and a second Meta source says the 7-day tag is "
                           "unavailable on the Instagram Messaging API. Two primary sources that "
                           "disagree is not a rule. Raising this needs a citation, not a guess.",
    },

    # "Initial 48-hour window: After receiving the first message from a TikTok user, Business
    #  Account can send up to 10 messages within the next 48 hours."
    # "If more than 48 hours have passed since the TikTok user's last reply, the Business Account
    #  is limited to sending a maximum of three additional messages."
    #   business-api.tiktok.com/portal/docs/messaging-limits-for-business-messaging-api/v1.3
    #
    # TIKTOK DOES NOT CLOSE, IT DEGRADES — which is why a boolean could never have expressed it.
    # Also reply-only: "You are prohibited from initiating a conversation or messaging any TikTok
    # user who has not started a conversation with you."
    #   business-api.tiktok.com/portal/docs/manage-direct-messages-for-a-business-account/v1.3
    #
    # THE RULE IS HERE; THE LANE IS NOT WIRED. The API is unavailable in the EEA, Switzerland and
    # the UK outright, and the US requires a passed security review. See the research doc §3.
    "tiktok": {
        "free_hours": 48, "tag_hours": None, "after_free_allowance": 3,
        "cite": "https://business-api.tiktok.com/portal/docs/"
                "messaging-limits-for-business-messaging-api/v1.3",
        "cite_reply_only": "https://business-api.tiktok.com/portal/docs/"
                           "manage-direct-messages-for-a-business-account/v1.3",
        # Not a window fact, but it governs whether this lane may exist for a given buyer at all.
        "region_blocked": ("EEA", "Switzerland", "UK"),
    },
}

# Channels that are deliberately absent, so nobody reads their absence as an oversight:
#
#   instagram_comment  NOT A WINDOW. One private reply per comment, ever, within 7 days OF THE
#                      COMMENT — not of an inbound message. A one-shot claim, not a clock.
#                      developers.facebook.com/docs/messenger-platform/instagram/features/private-replies/
#   reddit             Blocked above the code: Reddit's developer terms require a separate written
#                      agreement for commercial use, and explicit consent before any private
#                      message. A licensing question, not a window.
#   google_review      No time limit, and a reply can be updated or deleted. Nothing to gate.


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def decide(platform: str, last_inbound_at: str | None, now: datetime | None = None) -> dict:
    """What may be sent on `platform` right now. Always a dict, never an exception.

    Returns {"decision", "reason", "remaining"} — `remaining` is a bounded message allowance where
    the platform defines one, and None where it does not. The REASON is not decoration: it is what
    a human reads in the queue when a draft cannot go, and "blocked" with no reason is how a person
    concludes the machine is broken and sends it by hand.
    """
    rule = _RULES.get((platform or "").strip().lower())
    if rule is None:
        # THE MOST IMPORTANT BRANCH IN THIS FILE. An unrecognised platform refuses. Adding a
        # platform string to the poller must never be enough, by itself, to authorise a send on it.
        return {"decision": BLOCKED,
                "reason": f"no send rule is written for {platform!r}; nothing sends on a platform "
                          f"whose policy nobody has read",
                "remaining": None}

    last = _parse(last_inbound_at)
    if last is None:
        # USER-INITIATED ONLY, on every platform here. No recorded inbound means there is no
        # permission to reply to.
        return {"decision": BLOCKED,
                "reason": "no inbound message on record — every channel here is reply-only",
                "remaining": None}

    now = now or datetime.now(timezone.utc)
    if now < last:
        # A future timestamp is clock skew or a bad row, and either way it is not evidence that a
        # window is open. Refuse rather than compute a negative age.
        return {"decision": BLOCKED,
                "reason": "the last inbound message is timestamped in the future; refusing rather "
                          "than trusting a clock that disagrees with itself",
                "remaining": None}

    age = now - last
    if age <= timedelta(hours=rule["free_hours"]):
        return {"decision": FREEFORM,
                "reason": f"within {rule['free_hours']}h of their last message",
                "remaining": None, "cite": rule.get("cite")}

    if rule.get("tag_hours") and age <= timedelta(hours=rule["tag_hours"]):
        return {"decision": TAGGED,
                "reason": f"past {rule['free_hours']}h but within {rule['tag_hours']}h — a tagged "
                          f"reply is allowed and MUST NOT be promotional",
                "remaining": None, "cite": rule.get("cite_tag") or rule.get("cite")}

    allowance = rule.get("after_free_allowance")
    if allowance:
        return {"decision": LIMITED,
                "reason": f"past {rule['free_hours']}h — at most {allowance} more messages until "
                          f"they reply again",
                "remaining": allowance, "cite": rule.get("cite")}

    return {"decision": BLOCKED,
            "reason": f"more than {rule['free_hours']}h since their last message",
            "remaining": None, "cite": rule.get("cite")}


def allowed_send(last_inbound_at: str | None, now: datetime | None = None,
                 platform: str = "messenger") -> str:
    """Phase-1 compatible answer: 'freeform' or 'blocked'. Messenger unless told otherwise.

    KEPT, AND KEPT NARROW. `handler.py` has exactly one send tier — the fixed template opener,
    which is always a direct response to a fresh inbound — so 'freeform' is the only decision that
    may open that door. TAGGED and LIMITED are real permissions on their platforms and they are
    deliberately NOT translated to 'freeform' here: a tagged message must not be promotional, and a
    limited one is spending a bounded allowance. Both are decisions for the draft queue, where a
    human can see the reason. Collapsing them here would hand the opener a permission it was never
    designed to reason about.
    """
    return FREEFORM if decide(platform, last_inbound_at, now)["decision"] == FREEFORM else BLOCKED
