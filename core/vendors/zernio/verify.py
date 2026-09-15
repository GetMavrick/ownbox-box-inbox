"""Pin + signature verification (docs/ZERNIO_DEEP_INTEGRATION.md §2).

The guarantee that was advertised but never fired: assert the SDK is exactly the
pin AND that its real signatures match what the gateway depends on, at boot and as
a launch_check gate. A fake/drifted SDK that doesn't require account_id (the class
that let the inbox ship broken) fails here, not in production.
"""

PINNED_SDK = "1.4.551"


def verify_sdk() -> tuple[bool, str]:
    """Returns (ok, detail). Callers (gateway import / launch_check) treat a
    mismatch as 'stay inert + page the operator', never 'run anyway'."""
    try:
        import zernio
    except Exception as e:  # noqa: BLE001 — not installed is a deploy failure
        return False, f"zernio-sdk not importable: {e}"
    got = getattr(zernio, "__version__", None) or getattr(zernio, "SDK_VERSION", "?")
    if str(got) != PINNED_SDK:
        return False, f"zernio-sdk version drift: installed {got}, pinned {PINNED_SDK}"
    try:
        import inspect

        from zernio import Zernio
        probe = Zernio(api_key="verify-probe")            # construct is lazy — no network
        for m in ("send_inbox_message", "get_inbox_conversation_messages",
                  "mark_conversation_read"):
            if "account_id" not in inspect.signature(getattr(probe.messages, m)).parameters:
                return False, f"zernio-sdk signature drift: {m} lacks account_id"
        send_p = inspect.signature(probe.messages.send_inbox_message).parameters
        if "message_tag" not in send_p or "messaging_type" not in send_p:
            return False, "zernio-sdk signature drift: send_inbox_message message-tag pair missing"
        if "max_retries" not in inspect.signature(Zernio.__init__).parameters:
            return False, "zernio-sdk signature drift: client lacks max_retries"
        # Comment-automation contract (lead-magnet cross-reel capture): create must expose a
        # keyword trigger and keep post_id OPTIONAL (omitting = all-reels scope). A drift here
        # silently changes what the gateway's upsert() means — stay inert instead.
        # W1.4: the private-reply funnel's live path — the audit found verify_sdk blind to
        # exactly the methods the dead funnel depended on. get_inbox_post_comments is the
        # comments-for-a-post primitive (drift here = the funnel dies silently again);
        # send_private_reply_to_comment is message 1 itself.
        for m, req in (("list_inbox_comments", "account_id"),
                       ("get_inbox_post_comments", "account_id"),
                       ("send_private_reply_to_comment", "account_id")):
            if req not in inspect.signature(getattr(probe.comments, m)).parameters:
                return False, f"zernio-sdk signature drift: {m} lacks {req}"
        ca_create = inspect.signature(probe.comment_automations.create_comment_automation).parameters
        if "keywords" not in ca_create or "match_mode" not in ca_create:
            return False, "zernio-sdk signature drift: create_comment_automation lacks keywords/match_mode"
        if "post_id" not in ca_create or ca_create["post_id"].default is not None:
            return False, "zernio-sdk signature drift: create_comment_automation post_id not optional (all-reels scoping)"
    except Exception as e:  # noqa: BLE001
        return False, f"zernio-sdk signature check failed: {e}"
    return True, f"zernio-sdk {got} (pinned, signatures verified)"
