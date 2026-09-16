"""The ScopedClient facade (docs/ZERNIO_DEEP_INTEGRATION.md §1).

A department never constructs a raw `Zernio(...)`. It asks the gateway for a client
scoped to a Space — `zernio.client(space)` — and every call inherits, in one place:
per-Space key + profile scoping, the data-model contract (field_id/envelope/enum),
the determinate/indeterminate error contract, the mutating-retries-off policy, and
the profile-membership isolation backstop.

Resource facades are added here as departments need them (inbox, accounts, posts
today; analytics/ads/contacts next — each on the same contract).
"""
from core.logging import get_logger

from . import isolation, model, transport
from .errors import ZernioError, is_not_found

log = get_logger(__name__)

# Facebook Messenger DMs live under the "facebook" platform (LIVE-VERIFIED:
# "messenger" → [400] PLATFORM_NOT_SUPPORTED — F-2). Do not change back.
_INBOX_PLATFORM = "facebook"
# Meta's out-of-window path needs the messagingType + messageTag PAIR (F-3).
_MESSAGING_TYPE_TAGGED = "MESSAGE_TAG"


class _InboxResource:
    def __init__(self, scoped: "ScopedClient"):
        self._s = scoped

    def list(self, *, status: str = "active", cursor: str | None = None,
             limit: int = 50, platform: str | None = None) -> dict:
        """One page of conversations for THIS Space (profile-scoped) →
        {"conversations": [...], "next_cursor"}. `platform` overrides the default
        (Messenger/facebook) — the lead-magnet sweep passes "instagram", since IG DMs
        are LIVE-VERIFIED to return only under platform='instagram' (facebook → 0)."""
        s = self._s

        def c():
            kw = {"platform": platform or s.platform, "status": status, "limit": limit}
            if cursor:
                kw["cursor"] = cursor
            if s.profile_id:                       # scope the poll to the Space's Profile
                kw["profile_id"] = s.profile_id
            return transport.raw_client(s.key).messages.list_inbox_conversations(**kw)
        r = transport.call("inbox.list", c)
        return {"conversations": model.items(r, "conversations", "data", "items"),
                "next_cursor": model.cursor(r)}

    def messages(self, conversation_id: str, account_id: str, *,
                 cursor: str | None = None, limit: int = 100) -> dict:
        """One page of a conversation's messages. `account_id` is REQUIRED (F-1)."""
        if not account_id:
            raise ZernioError("inbox.messages refused: no account_id (F-1 fail-closed)")
        s = self._s

        def c():
            kw = {"limit": limit}
            if cursor:
                kw["cursor"] = cursor
            return transport.raw_client(s.key).messages.get_inbox_conversation_messages(
                conversation_id, account_id, **kw)
        r = transport.call("inbox.messages", c)
        return {"messages": model.items(r, "messages", "items", "data"),
                "next_cursor": model.cursor(r)}

    def send(self, conversation_id: str, account_id: str, text: str, *,
             tag: str | None = None, quick_replies=None, buttons=None) -> dict:
        """Send ONE message → {"message_id"}. MUTATING + irreversible. Fail-closed
        on a missing key or account_id; the account must be a member of the Space's
        profile (isolation backstop); the tag becomes the messaging_type+message_tag
        pair (F-3); retries off (F-4); the id is parsed from the real envelope (F-5).
        An indeterminate failure is surfaced, never blind-retried.

        `quick_replies` (tap → sends the title back as an inbound message) and `buttons`
        (e.g. a URL button) are passed straight through to send_inbox_message — the funnel's
        DM buttons. Callers build the wire shape via marketing.content_machine.leadmagnet.buttons."""
        s = self._s
        if not s.key:
            raise ZernioError("send refused: no per-Space Zernio key resolved "
                              "(isolation fail-closed — will not send via the global key)")
        if not account_id:
            raise ZernioError("send refused: no account_id resolved (F-1 fail-closed)")
        isolation.assert_member(account_id, profile_id=s.profile_id, api_key=s.key)

        def c():
            kw = {"message": text}
            if tag:
                kw["messaging_type"] = _MESSAGING_TYPE_TAGGED
                kw["message_tag"] = tag
            if quick_replies:
                kw["quick_replies"] = quick_replies
            if buttons:
                kw["buttons"] = buttons
            return transport.raw_client(s.key, max_retries=1).messages.send_inbox_message(
                conversation_id, account_id, **kw)
        r = transport.call("inbox.send", c, mutating=True)
        mid = model.message_id(r)
        if not mid:
            raise ZernioError(f"send returned no message id: {str(r)[:160]}", indeterminate=True)
        log.info("zernio.inbox_sent", conversation_id=conversation_id, message_id=mid)
        return {"message_id": mid}

    def mark_read(self, conversation_id: str, account_id: str) -> bool:
        """Best-effort read receipt (a failure never blocks the pipeline)."""
        if not account_id:
            return False
        try:
            transport.raw_client(self._s.key).messages.mark_conversation_read(
                conversation_id, account_id)
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("zernio.mark_read_failed", error=str(e)[:120])
            return False


class _AccountsResource:
    def __init__(self, scoped: "ScopedClient"):
        self._s = scoped

    def discover(self) -> dict:
        """Connected accounts for THIS Space (profile-scoped) → {platform: account_id}.
        Under a shared key, profile scoping means it can only ever return THIS
        Space's accounts (the posting/isolation half of the invariant)."""
        s = self._s
        r = transport.call(
            "accounts.list",
            lambda: transport.raw_client(s.key).accounts.list(profile_id=s.profile_id))
        out = {}
        for a in model.items(r, "accounts", "data", "items"):
            plat, aid = model.platform_value(a), model.obj_id(a)
            if plat and aid:
                out[plat] = aid
        return out

    def health(self) -> dict:
        """Connected-account health for THIS Space (watchdog/diagnostic feed).
        PROFILE-SCOPED like discover(): under the target one-key model a bare
        get_all_accounts_health() returns EVERY profile's accounts, so a Space's
        health view would mix in other tenants' accounts — the same isolation leak
        the poll/discover scoping closes. Pass profile_id so a Space only ever sees
        its own accounts' health (no-op in two-key mode)."""
        s = self._s
        return transport.call(
            "accounts.health",
            lambda: transport.raw_client(s.key).accounts.get_all_accounts_health(
                profile_id=s.profile_id))


class _PostsResource:
    def __init__(self, scoped: "ScopedClient"):
        self._s = scoped

    def create(self, *, content: str, media_items: list, platforms: list,
               title: str | None = None, mode: str = "draft",
               scheduled_for: str | None = None, timezone: str = "UTC",
               tiktok_privacy: str = "PUBLIC_TO_EVERYONE") -> str:
        """Publish ONE post → the Zernio post id. MUTATING + irreversible → retries
        off (A-1). mode: 'now' | 'schedule' (needs scheduled_for) | 'draft'."""
        s = self._s
        if not platforms:
            raise ZernioError("posts.create: no platform targets resolved")
        # SYMMETRY with inbox.send — the gateway's TWO mutating paths carry the same
        # two guards: fail-closed on an unresolved per-Space key (never the global
        # key), and the profile-membership isolation backstop per target account
        # (no-op in two-key mode; cached 5 min). A public, irreversible post to
        # another tenant's page is the exact failure these exist to prevent.
        if not s.key:
            raise ZernioError("post refused: no per-Space Zernio key resolved "
                              "(isolation fail-closed — will not post via the global key)")
        for t in platforms:
            isolation.assert_member(str(t.get("accountId")),
                                    profile_id=s.profile_id, api_key=s.key)
        kw = dict(content=content, media_items=media_items, platforms=platforms, timezone=timezone)
        if title:
            kw["title"] = title
        if mode == "now":
            kw["publish_now"] = True
        elif mode == "schedule" and scheduled_for:
            kw["scheduled_for"] = scheduled_for
        else:
            kw["is_draft"] = True
        if any((p.get("platform") or "").lower() == "tiktok" for p in platforms):
            kw["tiktok_settings"] = {"privacyLevel": tiktok_privacy}
        r = transport.call("posts.create",
                           lambda: transport.raw_client(s.key, max_retries=1).posts.create(**kw),
                           mutating=True)
        post = model.field(r, "post") or {}
        post_id = model.obj_id(post)
        if not post_id:
            # Envelope drift must be LOUD: the post may have landed but we cannot
            # reconcile it without an id — indeterminate, never a silent sentinel
            # (the old `or "posted"` fallback destroyed reconciliation quietly).
            raise ZernioError(f"posts.create returned no post id: {str(r)[:160]}",
                              indeterminate=True)
        # SUBMITTED, not published. Zernio accepting a post says nothing about whether any
        # network published it — on 2026-08-19 two posts logged here came back `partial`.
        # `posts.get` is what answers the second question; this line no longer pretends to.
        log.info("zernio.submitted", post_id=str(post_id), mode=mode,
                 platforms=[p.get("platform") for p in platforms])
        return str(post_id)

    def recent(self, *, limit: int = 10) -> list:
        """The Space's most recent posts — the reconciliation read for an INDETERMINATE create.

        A timeout on a mutating call means the request MAY have landed, and blind-retrying an
        irreversible public post is the one thing that must never happen. This is how the
        question gets answered instead of escalated: list what exists and look for it."""
        s = self._s
        kw = {"limit": limit}
        if s.profile_id:
            kw["profile_id"] = s.profile_id
        r = transport.call("posts.list",
                           lambda: transport.raw_client(s.key).posts.list_posts(**kw))
        return model.items(r, "posts", "data", "items")

    def get(self, post_id: str) -> dict:
        """One post's record, including PER-PLATFORM outcome — the only place that truth
        exists. A READ, so never indeterminate: a failure here means we do not know YET, and
        the caller must treat it as "ask again", never as "the platform failed"."""
        if not post_id:
            raise ZernioError("posts.get refused: no post_id")
        s = self._s
        r = transport.call("posts.get",
                           lambda: transport.raw_client(s.key).posts.get_post(post_id))
        return r if isinstance(r, dict) else {}


# The SDK exposes ~20 per-platform analytics methods; the gateway hides that sprawl
# behind account_insights(account_id, platform). Add platforms here as we run them.
_INSIGHTS_BY_PLATFORM = {
    "facebook": "get_facebook_page_insights",
    "instagram": "get_instagram_account_insights",
    "tiktok": "get_tik_tok_account_insights",
    "youtube": "get_you_tube_channel_insights",
    "linkedin": "get_linked_in_aggregate_analytics",
}


class _AnalyticsResource:
    """Read-only performance telemetry (docs/ZERNIO_DEEP_INTEGRATION.md §4 Phase C).
    Consumers: Finance (unit economics + quota) and reel iteration (post decay).
    Deterministic reads — no cost, no mutation."""

    def __init__(self, scoped: "ScopedClient"):
        self._s = scoped

    def post_timeline(self, post_id: str) -> dict:
        """A published post's performance over time (reel iteration signal)."""
        return transport.call(
            "analytics.post_timeline",
            lambda: transport.raw_client(self._s.key).analytics.get_post_timeline(post_id))

    def usage(self) -> dict:
        """Zernio plan/quota usage — mirrored next to the in-app $90 ledger (Finance)."""
        return transport.call(
            "analytics.usage",
            lambda: transport.raw_client(self._s.key).analytics.get_usage())

    def all_posts(self, *, limit: int = 100, page: int = 1) -> dict:
        """Every tracked post with its metrics: `{overview, posts, pagination, accounts,
        hasAnalyticsAccess}`.

        THIS IS THE ONE THAT JOINS. Each entry carries `latePostId`, which is exactly the id
        the cascade writes into the board's `<Channel> URL` receipt — so performance can be
        matched back to the row that produced it with a dict lookup and no bookkeeping.

        `post_timeline` looked like the right call and is not: it answers "how did this one
        post decay" and returned an empty timeline for a post an hour old. This answers "what
        happened to everything", in one request, which is what a sweep wants.
        """
        return transport.call(
            "analytics.all_posts",
            lambda: transport.raw_client(self._s.key).analytics.get_analytics(
                limit=limit, page=page))

    def account_insights(self, account_id: str, platform: str) -> dict:
        """Per-account performance, dispatched to the platform's SDK method — one
        clean call over the SDK's ~20 per-platform variants."""
        method = _INSIGHTS_BY_PLATFORM.get((platform or "").lower())
        if not method:
            raise ZernioError(f"analytics: no insights method for platform {platform!r}")
        return transport.call(
            f"analytics.{method}",
            lambda: getattr(transport.raw_client(self._s.key).analytics, method)(account_id))


class _CommentAutomationsResource:
    """Comment→DM automations (docs/LEAD_MAGNET_MACHINE_SPEC.md §6). The sub-60s
    lead-capture hop is a SERVER-SIDE Zernio automation: Meta fires comment→DM
    in-platform, so AIOS never sends the first DM — it CONFIGURES the automation
    (upsert) and READS its logs (poll-only, §11-4). One automation per keyword,
    scoped to ALL of the account's posts (no post_id) — "watch every reel for this
    keyword", the owner's hard requirement.

    Same contract as inbox/posts: per-Space key + profile scoping, fail-closed on a
    falsy key, the profile-membership isolation backstop on the target account,
    mutating-retries-off, and an indeterminate mutation surfaced (never blind-retried).
    """

    def __init__(self, scoped: "ScopedClient"):
        self._s = scoped

    def upsert(self, *, account_id: str, name: str, dm_message: str,
               keywords: list[str], comment_reply: str | None = None,
               buttons: list | None = None, match_mode: str = "contains",
               automation_id: str | None = None) -> str:
        """Create — or, if `automation_id` is known, UPDATE in place — ONE keyword
        automation scoped to ALL of the account's posts. Returns the Zernio automation
        id (stored by the caller so a re-run updates, never duplicates — idempotent).

        MUTATING + irreversible-ish (a live automation DMs real people): retries off
        (F-4), fail-closed on an unresolved per-Space key or account, isolation
        backstop per target account, indeterminate surfaced. `post_id` is INTENTIONALLY
        omitted so the automation is account-wide — do not add it back."""
        s = self._s
        if not s.key:
            raise ZernioError("comment_automation upsert refused: no per-Space Zernio "
                              "key resolved (isolation fail-closed — never the global key)")
        if not account_id:
            raise ZernioError("comment_automation upsert refused: no account_id "
                              "(fail-closed — can't scope the automation to an account)")
        if not s.profile_id:
            # LIVE-VERIFIED: the API returns [400] profileId is required. Refuse with a clear
            # message pointing at the fix (set the Space's zernio_profile_id) instead of a raw 400.
            raise ZernioError("comment_automation upsert refused: this Space has no "
                              "zernio_profile_id (the API requires profileId — set it in config)")
        if not keywords:
            raise ZernioError("comment_automation upsert refused: no keywords "
                              "(an unkeyworded automation would DM on every comment)")
        # Backstop: the target IG account must belong to THIS Space's profile — a live
        # comment automation on another tenant's account would auto-DM their audience
        # (unrecoverable). No-op in two-key mode; cached 5 min.
        isolation.assert_member(account_id, profile_id=s.profile_id, api_key=s.key)

        # AN AUTOMATION CANNOT BE MOVED BETWEEN ACCOUNTS, SO A STALE ONE MUST NOT BE REUSED
        # (2026-08-12). `update_comment_automation` takes no account_id — only create does. So an
        # automation found by keyword and updated in place keeps whatever account it was BORN
        # against, for ever.
        #
        # That is silent and total. The update returns an id, the caller stores it, the log says
        # `activated`, and the automation never fires again because the account it points at is
        # no longer connected. Measured on the live profile: `leadmagnet:VOICE`, `:BRAIN`,
        # `:ENGINE` and `:SCALE` were all still bound to `6a483c...`, an Instagram account that
        # had been disconnected, while `:AIOS` worked purely because no automation of that name
        # existed and it took the create branch.
        #
        # So reconcile on ACCOUNT as well as on keyword: if the one we found lives somewhere
        # else, it is not ours to update. Drop the id and create a fresh one against the account
        # we were actually given.
        if automation_id:
            try:
                # THE RECORD ARRIVES WRAPPED — `{"automation": {...}}` — and reading `accountId`
                # off the envelope silently returns None, which reads as "no mismatch" and
                # updates the stale automation anyway. That is exactly the bug this guard exists
                # to prevent, reintroduced by the guard itself: the three keywords WITHOUT a
                # prior automation healed (create path) while the three WITH one stayed stranded,
                # which is the same asymmetry that exposed the original defect. Unwrap first, and
                # use `model.field` so a rename of the envelope key or the id is handled where
                # every other Zernio shape is handled.
                raw = self.get(automation_id) or {}
                existing = model.field(raw, "automation", "data") or raw
                had = str(model.field(existing, "accountId", "account_id") or "")
                if had and had != str(account_id):
                    log.warning("zernio.comment_automation_account_moved",
                                automation_id=str(automation_id), was=had, now=str(account_id),
                                hint="the account it was created against is not the one we were "
                                     "given; creating a fresh automation instead of updating a "
                                     "stale one that can never fire")
                    automation_id = None
            except Exception as e:                # noqa: BLE001 — unsure means take the safe path
                if is_not_found(e):
                    # PROVABLY GONE, not merely unreadable. Someone deleted the automation in
                    # the Zernio UI; our row still names it. Keeping the id here is what made
                    # `SCALE` permanently dead: the update branch below would 404 on every
                    # single re-arm, so the keyword read as armed in our DB and could never
                    # fire. Drop the id and let the create branch mint a fresh one.
                    log.warning("zernio.comment_automation_vanished",
                                automation_id=str(automation_id), keywords=keywords,
                                hint="stored automation no longer exists on the platform "
                                     "(deleted in the UI?) — creating a fresh one")
                    automation_id = None
                else:
                    log.warning("zernio.comment_automation_account_check_failed",
                                automation_id=str(automation_id),
                                error=f"{type(e).__name__}: {e}"[:160])

        if automation_id:
            def c_update():
                return transport.raw_client(s.key, max_retries=1).comment_automations \
                    .update_comment_automation(
                        automation_id, name=name, keywords=keywords, match_mode=match_mode,
                        dm_message=dm_message, buttons=buttons, comment_reply=comment_reply)
            r = transport.call("comment_automations.update", c_update, mutating=True)
            aid = _automation_id(r) or automation_id   # update keeps the same id
        else:
            def c_create():
                return transport.raw_client(s.key, max_retries=1).comment_automations \
                    .create_comment_automation(
                        s.profile_id, account_id, name, dm_message,
                        keywords=keywords, match_mode=match_mode,
                        buttons=buttons, comment_reply=comment_reply)
            r = transport.call("comment_automations.create", c_create, mutating=True)
            aid = _automation_id(r)
        if not aid:
            # Envelope drift must be LOUD: the automation MAY have landed but we can't
            # reconcile it without an id — indeterminate, never a silent sentinel (the
            # caller reconciles by list()+keyword before ever creating again).
            raise ZernioError(f"comment_automation upsert returned no id: {str(r)[:160]}",
                              indeterminate=True)
        log.info("zernio.comment_automation_upserted", automation_id=str(aid),
                 keywords=keywords, updated=bool(automation_id))
        return str(aid)

    def logs(self, automation_id: str, *, status: str | None = None,
             limit: int = 50, skip: int = 0) -> dict:
        """One page of trigger logs for an automation (who commented, DM sent,
        engagement) → {"items": [...], "raw": <envelope>}. Read-only poll surface —
        the capture sweep reads this to find new DM threads to harvest an email from."""
        if not automation_id:
            raise ZernioError("comment_automation logs refused: no automation_id")
        s = self._s

        def c():
            kw = {"limit": limit, "skip": skip}
            if status:
                kw["status"] = status
            return transport.raw_client(s.key).comment_automations \
                .list_comment_automation_logs(automation_id, **kw)
        r = transport.call("comment_automations.logs", c)
        return {"items": model.items(r, "logs", "data", "items"), "raw": r}

    def list(self) -> list:
        """This Space's comment automations (profile-scoped) — the reconcile read:
        find an existing automation by keyword before ever creating a second one."""
        s = self._s
        r = transport.call(
            "comment_automations.list",
            lambda: transport.raw_client(s.key).comment_automations
            .list_comment_automations(profile_id=s.profile_id))
        return model.items(r, "automations", "data", "items")

    def get(self, automation_id: str) -> dict:
        """One automation's full record."""
        s = self._s
        return transport.call(
            "comment_automations.get",
            lambda: transport.raw_client(s.key).comment_automations
            .get_comment_automation(automation_id))

    def deactivate(self, automation_id: str) -> None:
        """Pause an automation (is_active=False) — the owner's "pause fable5". MUTATING
        but reversible (re-upsert reactivates); retries off, indeterminate surfaced."""
        if not automation_id:
            raise ZernioError("comment_automation deactivate refused: no automation_id")
        s = self._s
        transport.call(
            "comment_automations.deactivate",
            lambda: transport.raw_client(s.key, max_retries=1).comment_automations
            .update_comment_automation(automation_id, is_active=False),
            mutating=True)
        log.info("zernio.comment_automation_deactivated", automation_id=automation_id)


def automation_id(obj) -> str | None:
    """An automation's OWN id — read explicitly as field_id/_id/id and NEVER accountId.
    LIVE-VERIFIED: list/get automation objects carry BOTH `id` (the automation) and `accountId`
    (the IG account), and model.obj_id() returns accountId first (A-4, correct for accounts) —
    which would make reconcile-by-list match the wrong id and create duplicate automations. So
    automation-id reads must bypass obj_id and take the id fields only."""
    return model.field(obj, "field_id", "_id", "id")


def _automation_id(resp) -> str | None:
    """The automation id out of the create/update envelope. LIVE-VERIFIED shape:
    {"success": true, "automation": {"id": ...}} — read `automation` first, then `data`, then
    top-level. Uses automation_id() (id-only, never accountId). Absence → None (indeterminate)."""
    for key in ("automation", "data"):
        obj = model.field(resp, key)
        if obj is not None:
            aid = automation_id(obj)
            if aid:
                return str(aid)
    aid = automation_id(resp)
    return str(aid) if aid else None


class _CommentsResource:
    """Instagram comments — the DIY comment→DM lane that makes message 1's button WORK on a
    poll-only box. The managed comment_automation can only put Meta *template* buttons on its
    auto-DM, whose tap is a `messaging_postbacks` WEBHOOK we never receive (§11-4). A PRIVATE REPLY
    to the comment instead supports Meta QUICK REPLIES, whose tap ECHOES as an inbound message the
    sweep reads — so "Send me the link" drives the funnel with no webhook. We poll new keyword
    comments and private-reply each once."""

    def __init__(self, scoped: "ScopedClient"):
        self._s = scoped

    def list(self, account_id: str, *, since=None, limit: int = 50,
             cursor: str | None = None) -> dict:
        """One page of the account's recent Instagram comments → {"items", "raw"}. Read-only poll
        (never indeterminate). `since` narrows to comments after a timestamp."""
        if not account_id:
            raise ZernioError("comments.list refused: no account_id (fail-closed)")
        s = self._s

        def c():
            kw = {"account_id": account_id, "platform": "instagram", "limit": limit}
            if since:
                kw["since"] = since
            if cursor:
                kw["cursor"] = cursor
            return transport.raw_client(s.key).comments.list_inbox_comments(**kw)
        r = transport.call("comments.list", c)
        return {"items": model.items(r, "comments", "data", "items"), "raw": r}

    def list_post_comments(self, post_id: str, account_id: str, *, limit: int = 50,
                           cursor: str | None = None) -> dict:
        """The ACTUAL comments on one post → {"items", "raw"}. W1.4: list() above wraps
        `list_inbox_comments`, whose own SDK docstring is "List POSTS with comments" — the
        dead-funnel audit found the sweep iterating its post objects as if they were
        comments (0 DMs ever sent). THIS wraps `get_inbox_post_comments` ("Get comments
        for a post"), the method with previously ZERO call sites. Items are SDK `Comment`s:
        id (platform comment id), platformPostId, text, author{id,username,name}, isReply."""
        if not (post_id and account_id):
            raise ZernioError("comments.list_post_comments refused: post_id + account_id "
                              "required (fail-closed)")
        s = self._s

        def c():
            kw = {"limit": limit}
            if cursor:
                kw["cursor"] = cursor
            return transport.raw_client(s.key).comments.get_inbox_post_comments(
                post_id, account_id, **kw)
        r = transport.call("comments.list_post_comments", c)
        return {"items": model.items(r, "comments", "data", "items"), "raw": r}

    def send_private_reply(self, post_id: str, comment_id: str, account_id: str, message: str,
                           *, quick_replies=None) -> dict:
        """Private-reply to a comment with the funnel's message 1 (DM) + a quick-reply button.
        MUTATING + irreversible-ish (a real DM to a real person): fail-closed on key/account,
        isolation backstop, retries OFF (F-4), the id parsed from the envelope, an indeterminate
        failure surfaced (never blind-retried — Meta allows ONE private reply per comment)."""
        s = self._s
        if not s.key:
            raise ZernioError("private reply refused: no per-Space key resolved "
                              "(isolation fail-closed — never the SDK env-var fallback)")
        if not account_id:
            raise ZernioError("private reply refused: no account_id (F-1 fail-closed)")
        if not (post_id and comment_id):
            raise ZernioError("private reply refused: post_id + comment_id required")
        isolation.assert_member(account_id, profile_id=s.profile_id, api_key=s.key)

        def c():
            kw = {}
            if quick_replies:
                kw["quick_replies"] = quick_replies
            return transport.raw_client(s.key, max_retries=1).comments \
                .send_private_reply_to_comment(post_id, comment_id, account_id, message, **kw)
        r = transport.call("comments.send_private_reply", c, mutating=True)
        mid = model.message_id(r)
        if not mid:
            raise ZernioError(f"private reply returned no id: {str(r)[:160]}", indeterminate=True)
        log.info("zernio.private_reply_sent", comment_id=comment_id, message_id=mid)
        return {"message_id": mid, "raw": r}


class ScopedClient:
    """A Zernio client bound to one Space's key + Zernio Profile. The unit of
    tenant isolation — every call it makes is scoped to this Space."""

    platform = _INBOX_PLATFORM

    def __init__(self, key: str | None, profile_id: str | None = None):
        self.key = key
        self.profile_id = profile_id

    @property
    def inbox(self) -> _InboxResource:
        return _InboxResource(self)

    @property
    def accounts(self) -> _AccountsResource:
        return _AccountsResource(self)

    @property
    def posts(self) -> _PostsResource:
        return _PostsResource(self)

    @property
    def analytics(self) -> _AnalyticsResource:
        return _AnalyticsResource(self)

    @property
    def comment_automations(self) -> _CommentAutomationsResource:
        return _CommentAutomationsResource(self)

    @property
    def comments(self) -> _CommentsResource:
        return _CommentsResource(self)

    @property
    def connect(self) -> "_ConnectResource":
        return _ConnectResource(self)


class _ConnectResource:
    """Where a person hands one of their own social accounts to their own box (B1).

    THE ONLY CONSENT SURFACE IN THE PRODUCT. Everything else this gateway does with a connected
    account is a read or a draft; this is the one call that asks a human being to grant access to
    something of theirs. So it is the one place where a wrong profile id is not a failed request
    but a misdirected grant, and the two calls below exist to make that impossible rather than
    unlikely.

    BRING-YOUR-OWN-KEY (owner, 2026-09-16): under BYO the key opens the BUYER's Zernio account, so
    the profile this resource works in is discovered there — never the id the provisioner wrote at
    first boot, which names a folder in ours (`core.spaces._own_binding`).
    """

    def __init__(self, scoped: "ScopedClient"):
        self._s = scoped

    def profiles(self) -> list:
        """Every Profile in the account this key opens → [{"id":…, "name":…}].

        NOT profile-scoped, unlike every other read in this gateway, and deliberately: the caller
        is choosing WHICH profile to work in, so scoping the question to a profile would beg it.
        Safe because a BYO key opens only the buyer's own account — there is no other tenant in it
        to leak. On a shared key this returns OUR folders, which is why the only caller (the box's
        own connect screen) runs when the buyer's own key is set."""
        s = self._s
        r = transport.call(
            "profiles.list",
            lambda: transport.raw_client(s.key).profiles.list_profiles())
        out = []
        for p in model.items(r, "profiles", "data", "items"):
            pid = model.obj_id(p)
            if pid:
                out.append({"id": pid, "name": str(model.field(p, "name") or "")})
        return out

    def create_profile(self, name: str) -> str:
        """A Profile in the buyer's own account, for this box's accounts to hang under.

        MUTATING, SO RETRIES ARE OFF (transport.py): the SDK retries POSTs on timeout by default,
        and under that a single call can leave the buyer with two identically named folders and no
        way to tell which one their Instagram landed in."""
        s = self._s
        r = transport.call(
            "profiles.create",
            lambda: transport.raw_client(s.key, max_retries=1).profiles.create_profile(
                str(name or "Ownbox")[:100]),
            mutating=True)
        pid = model.obj_id(model.field(r, "profile") or r)
        if not pid:
            raise ZernioError("zernio profiles.create: no profile id in the response")
        return str(pid)

    def url(self, platform: str, *, redirect_url: str) -> str:
        """The vendor's consent URL for one platform, to send a person to.

        REFUSES WITHOUT A PROFILE rather than letting the SDK send a None through. `profile_id` is
        a positional argument there, so an unresolved profile would be serialised into the request
        and the grant would attach wherever the vendor decided — which, on an account with folders
        already in it, is a customer's Instagram in the wrong one.

        `login_method` is pinned for Instagram exactly as the provisioner pins it: a Business or
        Creator account with no Facebook Page. Passed explicitly so a future SDK default cannot
        move it underneath us."""
        s = self._s
        if not s.profile_id:
            raise ZernioError("zernio connect: refused — no profile resolved for this Space")
        kw = {"redirect_url": redirect_url}
        if platform == "instagram":
            kw["login_method"] = "instagram_login"
        r = transport.call(
            f"connect.{platform}",
            lambda: transport.raw_client(s.key).connect.get_connect_url(
                platform, s.profile_id, **kw))
        url = str(model.field(r, "authUrl", "auth_url", "url") or "")
        if not url.startswith("https://"):
            # A non-https answer is never a URL worth sending a person to, and the vendor has
            # answered 200-with-no-url before. Fail loudly here, not in the browser.
            raise ZernioError(f"zernio connect.{platform}: no https authUrl in the response")
        return url
