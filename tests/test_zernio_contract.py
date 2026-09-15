"""Zernio SDK signature-contract test — runs against the REAL installed SDK.

This is the net that was missing (docs/ZERNIO_SDK_INTEGRATION.md §3 / A-2): every
other inbox/auto_poster test fakes the SDK, so nothing ever asserted that our call
sites match the vendor's ACTUAL signatures. Six defects (F-1/F-3/A-4…) shipped green
because of exactly that. This test imports the pinned, installed `zernio` package and
asserts the signatures our code depends on — so the whole bug class fails CI
mechanically, and any SDK bump that drifts them is caught the moment the pin moves.

CI installs the SDK via `pip install -e .` (it is a hard dependency), so this runs
for real there. Run: python tests/test_zernio_contract.py
"""
import inspect
import pathlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = 0


def ok(name, cond):
    global PASS
    if cond:
        PASS += 1
        print(f"PASS — {name}")
    else:
        print(f"FAIL — {name}")
        sys.exit(1)


def main():
    # The pinned SDK MUST be importable — it is a hard pyproject dependency. A failure
    # here means the deploy/install is broken, which is exactly what we want to catch.
    try:
        import zernio
        from zernio import Zernio
    except Exception as e:  # noqa: BLE001
        print(f"FAIL — real zernio-sdk is not importable (it is a pinned dependency): {e}")
        sys.exit(1)

    from core.vendors import zernio as gw

    # 1. version pin matches the code's expectation
    ok("installed SDK is exactly the pin",
       str(getattr(zernio, "__version__", "?")) == gw.PINNED_SDK)

    # 2. the gateway targets the platform the API actually accepts (F-2)
    ok("gateway inbox platform is 'facebook' (messenger is a live 400)",
       gw.ScopedClient.platform == "facebook")

    probe = Zernio(api_key="contract-probe")     # construct is lazy — no network

    # 3. the inbox mutating/read methods require account_id (F-1)
    for m in ("send_inbox_message", "get_inbox_conversation_messages",
              "mark_conversation_read"):
        params = inspect.signature(getattr(probe.messages, m)).parameters
        ok(f"real {m} requires account_id (F-1)", "account_id" in params)

    # 4. send takes the messaging_type + message_tag PAIR, and NO bare `tag` (F-3)
    send_params = inspect.signature(probe.messages.send_inbox_message).parameters
    ok("send_inbox_message exposes messaging_type", "messaging_type" in send_params)
    ok("send_inbox_message exposes message_tag", "message_tag" in send_params)
    ok("send_inbox_message has NO bare 'tag' param (F-3)", "tag" not in send_params)

    # 5. the transport default that forces our max_retries=1 on mutations (F-4)
    client_params = inspect.signature(Zernio.__init__).parameters
    ok("client __init__ exposes max_retries (F-4 relies on overriding it)",
       "max_retries" in client_params)
    mr = client_params["max_retries"].default
    ok("client default max_retries > 1 (why mutating calls must pin it to 1)",
       isinstance(mr, int) and mr > 1)

    # 6. posts.create shape the auto_poster depends on, still present
    post_params = inspect.signature(probe.posts.create).parameters
    ok("posts.create takes content + platforms (auto_poster contract)",
       "content" in post_params and "platforms" in post_params)

    # 6a-bis. carousel contract (docs/CAROUSEL_MACHINE_SPEC.md §7): the gateway forwards
    # media_items VERBATIM to the SDK, and a carousel is N image items in ONE create
    # call. If a bump renames/drops media_items, every carousel publish breaks — here.
    ok("posts.create exposes media_items (carousel = N image items in one call)",
       "media_items" in post_params)

    # 6b. account-health isolation: get_all_accounts_health must expose profile_id, or
    # the gateway's health() can't scope to one Space and would mix tenants (the same
    # leak the poll/discover profile scoping closes). If a bump drops it, catch it here.
    health_params = inspect.signature(probe.accounts.get_all_accounts_health).parameters
    ok("get_all_accounts_health exposes profile_id (health isolation scoping)",
       "profile_id" in health_params)

    # 6c. comment-automation contract (lead-magnet machine, docs/LEAD_MAGNET_MACHINE_SPEC.md §6).
    # The whole cross-reel capture premise rests on these: create takes a `keywords` list +
    # `match_mode`, and `post_id` is OPTIONAL (omitting it scopes the automation to ALL of the
    # account's posts — "watch every reel for this keyword"). If a bump makes post_id required
    # or drops keywords/match_mode, the gateway's upsert() silently changes meaning — catch it here.
    ca_create = inspect.signature(probe.comment_automations.create_comment_automation).parameters
    ok("create_comment_automation exposes keywords (keyword trigger)", "keywords" in ca_create)
    ok("create_comment_automation exposes match_mode", "match_mode" in ca_create)
    ok("create_comment_automation post_id is OPTIONAL (all-reels scoping premise)",
       "post_id" in ca_create and ca_create["post_id"].default is None)
    ca_update = inspect.signature(probe.comment_automations.update_comment_automation).parameters
    ok("update_comment_automation exposes is_active (pause/deactivate)", "is_active" in ca_update)
    # THE ACCOUNT CANNOT BE MOVED, WHICH IS WHY upsert MUST RECONCILE ON IT (2026-08-12).
    # `update_comment_automation` takes NO account_id — only create does. So an automation found
    # by keyword and updated in place keeps whatever account it was born against, for ever, and
    # the failure is silent: the update returns an id, the caller stores it, the log says
    # activated, and it never fires because that account is disconnected. Measured live —
    # leadmagnet:VOICE/:BRAIN/:ENGINE/:SCALE were all stranded on a dead Instagram account while
    # :AIOS worked only because no automation of that name existed.
    ok("update_comment_automation still takes NO account_id — the premise of the reconcile",
       not any(k in ca_update for k in ("account_id", "accountId")))
    ok("create_comment_automation DOES take the account", "account_id" in ca_create)
    _up = pathlib.Path("core/vendors/zernio/client.py").read_text()
    _up = _up[_up.index("def upsert("):_up.index("def logs(")]
    ok("upsert refuses to update an automation that lives on another account",
       "account_moved" in _up and "automation_id = None" in _up)
    ok("…and it fails SAFE — an unreadable automation is not reused blindly",
       "account_check_failed" in _up)

    ca_logs = inspect.signature(probe.comment_automations.list_comment_automation_logs).parameters
    ok("list_comment_automation_logs takes automation_id (poll surface)",
       "automation_id" in ca_logs)

    # 7. the gateway's verify_sdk() agrees against the REAL SDK (version + signatures)
    ok_, detail = gw.verify_sdk()
    ok(f"gateway.verify_sdk() passes against the real SDK ({detail})", ok_ is True)

    # 8. DRIFT GUARD for the indeterminate classifier: errors.is_indeterminate treats
    # LateConnectionError as DETERMINATE (retry freely) on the source-proven premise
    # that the SDK raises it ONLY from `except httpx.ConnectError` (pre-send: nothing
    # left the box). If an SDK upgrade starts raising it post-send, this trips — and
    # the classifier must be re-audited BEFORE the pin is bumped.
    import late.client.base as _lcb
    _src_lines = inspect.getsource(_lcb).splitlines()
    _sites = [i for i, l in enumerate(_src_lines)
              if "LateConnectionError(" in l and "import" not in l]
    _guarded = all(
        any("except httpx.ConnectError" in _src_lines[j]
            for j in range(max(0, i - 3), i))
        for i in _sites)
    ok("SDK raises LateConnectionError only from `except httpx.ConnectError` "
       f"({len(_sites)} sites — pre-send/determinate premise holds)",
       bool(_sites) and _guarded)

    print(f"\nALL {PASS} CONTRACT CHECKS PASS — the gateway's call sites match the REAL "
          f"zernio-sdk {gw.PINNED_SDK} signatures (F-1/F-2/F-3/F-4).")


if __name__ == "__main__":
    main()
