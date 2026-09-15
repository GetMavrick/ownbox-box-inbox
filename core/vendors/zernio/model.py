"""The Zernio data-model contract (docs/ZERNIO_DEEP_INTEGRATION.md §1).

The SDK has two shapes we must reconcile everywhere, and getting them wrong is
the exact class that shipped six defects (F-5/A-4/A-6):

  - curated resources return Pydantic models whose id is exposed as `field_id`
    (the alias of the API's `_id`), and whose platform is an enum (`.value` is the
    wire name, str() is "Platform12.FACEBOOK");
  - generated resources return raw dicts enveloped as {data|messages, pagination}.

Every id/list/cursor/platform read in the gateway goes through here — once — so no
resource facade hand-rolls `_field(…, "_id", "id")` again.
"""


def field(obj, *names):
    """Read a field from a Pydantic model OR a plain dict, trying several names.
    Handles the `field_id` alias transparently (callers list it where an id is
    expected)."""
    for n in names:
        if isinstance(obj, dict) and obj.get(n) is not None:
            return obj[n]
        v = getattr(obj, n, None)
        if v is not None:
            return v
    return None


def obj_id(obj):
    """The canonical id of any Zernio object — `field_id` (Pydantic alias) first,
    then the raw-dict `_id`/`id`/`accountId` (A-4)."""
    return field(obj, "field_id", "_id", "accountId", "id")


def platform_value(obj) -> str | None:
    """The wire platform name. `obj.platform` is an enum whose str() is
    "Platform12.FACEBOOK"; `.value` is "facebook" (A-6)."""
    p = field(obj, "platform")
    if p is None:
        return None
    return str(getattr(p, "value", None) or p).lower()


def items(resp, *keys) -> list:
    """The list out of a response envelope — {data|messages|items: [...]} or a bare
    list. `keys` are the envelope keys to try in order."""
    return (field(resp, *keys) or (resp if isinstance(resp, list) else [])) or []


def cursor(resp):
    """The next-page cursor: `pagination.nextCursor`."""
    return field(field(resp, "pagination") or {}, "nextCursor", "next_cursor")


def is_follower(convo) -> bool | None:
    """Meta's own follow signal for an Instagram conversation participant
    (`instagramProfile.isFollower`), read tolerantly across the Pydantic/dict shapes the SDK
    returns (docs/LEAD_MAGNET_MACHINE_SPEC.md §6.4). Returns True/False, or **None when the
    signal is absent** — the follow-gate caller treats None as NOT following (fail-closed: no
    email without a verified follow). Checks the participant list, then the conversation itself
    and its sender/contact, since different envelope shapes hang the profile in different spots."""
    parts = field(convo, "participants", "members")
    candidates = list(parts) if isinstance(parts, (list, tuple)) else []
    candidates.append(convo)                               # some shapes hang it on the convo
    sender = field(convo, "sender", "contact", "participant")
    if sender is not None:
        candidates.append(sender)
    for cand in candidates:
        prof = field(cand, "instagramProfile", "instagram_profile")
        if prof is not None:
            v = field(prof, "isFollower", "is_follower")
            if isinstance(v, bool):
                return v
    return None


def message_id(resp) -> str | None:
    """The sent-message id across the real send envelope (F-5): `data.messageId`
    first, then looser top-level / nested shapes. Genuine absence → None (the
    caller treats a send with no id as indeterminate — it can't enter the ledger)."""
    data = field(resp, "data")
    if data is not None:
        mid = field(data, "messageId", "message_id", "id", "_id", "field_id")
        if mid:
            return str(mid)
    mid = field(resp, "messageId", "message_id", "id", "_id", "field_id")
    if not mid:
        msg = field(resp, "message")
        if isinstance(msg, dict) or hasattr(msg, "id"):
            mid = field(msg, "id", "_id", "messageId")
    return str(mid) if mid else None
