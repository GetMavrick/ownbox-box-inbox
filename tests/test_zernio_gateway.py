"""core.vendors.zernio gateway — the SDK contract, tested against a FAITHFUL fake SDK.

The gateway is the ONE place the SDK's real shape is handled, so this is where the
F-1..F-6 + isolation guarantees live. The fake mirrors the real signatures
(account_id required, the messaging_type+message_tag pair, the data.messageId send
envelope, field_id ids, the platform enum). The real-SDK signature check lives in
tests/test_zernio_contract.py so the fake can't drift back into fiction.

Run: python tests/test_zernio_gateway.py
"""
import os
import pathlib
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"  # skip the box .env — scrubs must stick (core/config.py)
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "gw.db")
os.environ.pop("ZERNIO_API_KEY", None)

CALLS: list = []


class FakeMessages:
    def __init__(self, log):
        self.log = log
        self.send_response = {"data": {"messageId": "m-1"}}      # real send envelope (F-5)
        self.send_raises: Exception | None = None

    def list_inbox_conversations(self, *, platform=None, status=None, limit=50,
                                 cursor=None, profile_id=None, **kw):
        self.log.append(("list", {"platform": platform, "status": status,
                                  "profile_id": profile_id}))
        return {"data": [{"id": "c-1", "accountId": "acc-1", "platform": platform}],
                "pagination": {"nextCursor": "cur-2"}}

    def get_inbox_conversation_messages(self, cid, account_id, *, limit=100,
                                        cursor=None, **kw):
        self.log.append(("messages", cid, account_id))
        return {"messages": [{"id": "m-0", "message": "hi"}], "pagination": {}}

    def send_inbox_message(self, cid, account_id, *, message=None,
                           messaging_type=None, message_tag=None, **kw):
        self.log.append(("send", cid, account_id,
                         {"messaging_type": messaging_type, "message_tag": message_tag}))
        if self.send_raises:
            raise self.send_raises
        return self.send_response

    def mark_conversation_read(self, cid, account_id):
        self.log.append(("read", cid, account_id))
        return {}


def _enum(val):                       # mimic the SDK Platform enum (str()="Platform12.X", .value=x)
    return types.SimpleNamespace(value=val)


class FakeAccounts:
    registry: dict = {}               # profile_id -> [account dict]
    default = [{"field_id": "acc-1", "platform": _enum("facebook")},
               {"field_id": "acc-2", "platform": _enum("metaads")}]

    def list(self, *, profile_id=None):
        accts = FakeAccounts.registry.get(profile_id, FakeAccounts.default) \
            if (profile_id or FakeAccounts.registry) else FakeAccounts.default
        return {"accounts": accts}

    last_health_profile: object = "unset"

    def get_all_accounts_health(self, *, profile_id=None, platform=None, status=None):
        FakeAccounts.last_health_profile = profile_id   # assert isolation forwarding
        return {"accounts": [{"field_id": "acc-1", "status": "connected"},
                             {"field_id": "acc-2", "status": "expired"}]}

    def get_account_health(self, account_id):
        return {"data": {"accountId": account_id, "status": "connected"}}


class FakeAnalytics:
    def get_post_timeline(self, post_id):
        return {"data": {"postId": post_id, "views": [1, 2, 3]}}

    def get_usage(self):
        return {"data": {"used": 42, "limit": 100}}

    def get_facebook_page_insights(self, account_id):
        return {"data": {"accountId": account_id, "reach": 999}}

    def get_instagram_account_insights(self, account_id):
        return {"data": {"accountId": account_id}}


class FakePosts:
    created: list = []

    def create(self, **kw):
        FakePosts.created.append(kw)
        return {"post": {"field_id": "zp-1"}}


class FakeCommentAutomations:
    """Faithful to the real signatures verify_sdk() inspects — keywords + match_mode present,
    post_id OPTIONAL (the all-reels scoping premise). Keep in lockstep with the pinned SDK."""

    def create_comment_automation(self, profile_id, account_id, name, dm_message, *,
                                  trigger="comment", platform_post_id=None, post_id=None,
                                  post_title=None, keywords=None, match_mode="contains",
                                  buttons=None, comment_reply=None, link_tracking=True,
                                  click_tag=None):
        return {"data": {"_id": "ca-1"}}

    def update_comment_automation(self, automation_id, *, name=None, keywords=None,
                                  match_mode=None, dm_message=None, buttons=None,
                                  comment_reply=None, link_tracking=None, click_tag=None,
                                  is_active=None):
        return {"data": {"_id": automation_id}}

    def list_comment_automation_logs(self, automation_id, *, status=None, limit=50, skip=0):
        return {"data": []}

    def list_comment_automations(self, *, profile_id=None):
        return {"data": []}

    def get_comment_automation(self, automation_id):
        return {"data": {"_id": automation_id}}


class FakeComments:
    """The faithful comments resource (W1.4): verify_sdk now asserts the private-reply
    funnel's live-path signatures, so the fake must carry them — account_id keyword and all."""
    def list_inbox_comments(self, *, account_id=None, platform=None, min_comments=None,
                            sort_by=None, sort_order=None, limit=50, cursor=None, since=None):
        return {"data": []}

    def get_inbox_post_comments(self, post_id, account_id, *, subreddit=None, limit=25,
                                cursor=None, comment_id=None):
        return {"data": []}

    def send_private_reply_to_comment(self, post_id, comment_id, account_id, message, *,
                                      quick_replies=None, buttons=None):
        return {"data": {"messageId": "pm1"}}


class FakeZernio:
    created: list = []

    def __init__(self, api_key=None, timeout=None, max_retries=None):
        FakeZernio.created.append({"api_key": api_key, "max_retries": max_retries})
        self.messages = FakeMessages(CALLS)
        self.accounts = FakeAccounts()
        self.posts = FakePosts()
        self.analytics = FakeAnalytics()
        self.comment_automations = FakeCommentAutomations()
        self.comments = FakeComments()


fake_mod = types.ModuleType("zernio")
fake_mod.Zernio = FakeZernio
# The fixture tracks the PIN rather than hardcoding it, so a future bump can never
# leave this fake behind (that is exactly what broke on the 1.4.189 -> 1.4.386 bump).
from core.vendors.zernio.verify import PINNED_SDK   # noqa: E402 — no top-level SDK import
fake_mod.__version__ = PINNED_SDK
sys.modules["zernio"] = fake_mod

from core.vendors import zernio                          # noqa: E402
from core.vendors.zernio import isolation                # noqa: E402

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
    # 1. verify_sdk: version + signature contract (against the faithful fake)
    ok("verify_sdk passes on pin + signatures",
       zernio.verify_sdk() == (True, f"zernio-sdk {PINNED_SDK} (pinned, signatures verified)"))
    fake_mod.__version__ = "1.5.0"
    d_ok, d_msg = zernio.verify_sdk()
    ok("verify_sdk fails loud on version drift", d_ok is False and "drift" in d_msg)
    fake_mod.__version__ = PINNED_SDK

    space = {"zernio_key": "sk_A", "zernio_profile_id": None}
    z = zernio.client(space)

    # 2. inbox.list — facebook platform (F-2), envelope unwrapped, cursor surfaced
    FakeZernio.created.clear()
    page = z.inbox.list()
    ok("client built with the Space key", FakeZernio.created[-1]["api_key"] == "sk_A")
    ok("inbox.list uses platform=facebook (F-2)", CALLS[-1][1]["platform"] == "facebook")
    ok("inbox.list unwraps the data envelope + cursor",
       page["conversations"][0]["id"] == "c-1" and page["next_cursor"] == "cur-2")

    # profile scoping is forwarded when set
    zernio.client({"zernio_key": "sk_A", "zernio_profile_id": "prof-A"}).inbox.list()
    ok("inbox.list forwards profile_id when set", CALLS[-1][1]["profile_id"] == "prof-A")

    # 3. inbox.messages — account_id required (F-1)
    msgs = z.inbox.messages("c-1", "acc-1")
    ok("inbox.messages returns the page + passes account_id",
       msgs["messages"][0]["id"] == "m-0" and CALLS[-1][2] == "acc-1")
    try:
        z.inbox.messages("c-1", "")
        ok("inbox.messages refuses missing account_id (F-1)", False)
    except zernio.ZernioError:
        ok("inbox.messages refuses missing account_id (F-1)", True)

    # 4. inbox.send — data.messageId (F-5), tag PAIR (F-3), max_retries=1 (F-4)
    FakeZernio.created.clear()
    sent = z.inbox.send("c-1", "acc-1", "hello", tag="HUMAN_AGENT")
    ok("send returns message_id from data.messageId (F-5)", sent == {"message_id": "m-1"})
    ok("send passed account_id", CALLS[-1][2] == "acc-1")
    ok("tag becomes messaging_type+message_tag pair (F-3)",
       CALLS[-1][3] == {"messaging_type": "MESSAGE_TAG", "message_tag": "HUMAN_AGENT"})
    ok("mutating send built the client with max_retries=1 (F-4)",
       FakeZernio.created[-1]["max_retries"] == 1)

    # 5. send fail-closed: no key, no account_id
    for sp, aid, label in (({"zernio_key": None}, "acc-1", "no key"),
                           ({"zernio_key": "sk_A"}, "", "no account_id")):
        try:
            zernio.client(sp).inbox.send("c-1", aid, "hi")
            ok(f"send fail-closed on {label}", False)
        except zernio.ZernioError as e:
            ok(f"send fail-closed on {label}", e.indeterminate is False)

    # 6. send error contract: timeout=indeterminate, 400=determinate, no-id=indeterminate
    probe = FakeZernio().messages
    z._probe = probe  # not used; keep ref
    import core.vendors.zernio.transport as _t
    _real_raw = _t.raw_client
    _t.raw_client = lambda key, *, max_retries=None: type("Z", (), {"messages": probe})()
    try:
        probe.send_raises = TimeoutError("read timed out")
        try:
            z.inbox.send("c-1", "acc-1", "hi")
            ok("timeout send is indeterminate", False)
        except zernio.ZernioError as e:
            ok("timeout send is indeterminate", e.indeterminate is True)
        probe.send_raises = ValueError("400 bad request")
        try:
            z.inbox.send("c-1", "acc-1", "hi")
            ok("400 send is determinate", False)
        except zernio.ZernioError as e:
            ok("400 send is determinate", e.indeterminate is False)
        probe.send_raises = None
        probe.send_response = {"status": "ok"}       # no data.messageId
        try:
            z.inbox.send("c-1", "acc-1", "hi")
            ok("send without an id fails loud (indeterminate)", False)
        except zernio.ZernioError as e:
            ok("send without an id fails loud (indeterminate)", e.indeterminate is True)
    finally:
        _t.raw_client = _real_raw

    # 7. accounts.discover — field_id ids (A-4) + enum .value platforms (A-6)
    got = z.accounts.discover()
    ok("accounts.discover keys by platform .value + field_id id (A-4/A-6)",
       got == {"facebook": "acc-1", "metaads": "acc-2"})

    # 8. posts.create — payload shape + post id from field_id + max_retries=1
    FakeZernio.created.clear()
    FakePosts.created.clear()
    pid = z.posts.create(content="cap", media_items=[{"type": "video", "url": "u"}],
                         platforms=[{"platform": "tiktok", "accountId": "a"}], mode="now")
    ok("posts.create returns the post id (field_id)", pid == "zp-1")
    ok("posts.create is mutating → max_retries=1 (A-1)",
       FakeZernio.created[-1]["max_retries"] == 1)
    ok("posts.create sets publish_now for mode=now + tiktok_settings",
       FakePosts.created[-1].get("publish_now") is True
       and "tiktok_settings" in FakePosts.created[-1])

    # 9a. analytics — the gateway hides the SDK's ~20 per-platform variants (Phase C)
    ok("analytics.post_timeline returns the timeline",
       z.analytics.post_timeline("zp-1")["data"]["postId"] == "zp-1")
    ok("analytics.usage returns quota", z.analytics.usage()["data"]["limit"] == 100)
    ok("account_insights dispatches to the platform method",
       z.analytics.account_insights("acc-1", "facebook")["data"]["reach"] == 999)
    try:
        z.analytics.account_insights("acc-1", "myspace")
        ok("account_insights refuses an unknown platform", False)
    except zernio.ZernioError:
        ok("account_insights refuses an unknown platform", True)

    # 9b. accounts.health — the watchdog/diagnostic feed, PROFILE-SCOPED like discover
    h = z.accounts.health()
    ok("accounts.health returns all-accounts health",
       isinstance(h, dict) and len(h.get("accounts", [])) == 2)
    zernio.client({"zernio_key": "sk_A", "zernio_profile_id": "prof-A"}).accounts.health()
    ok("accounts.health forwards profile_id (isolation symmetry with discover)",
       FakeAccounts.last_health_profile == "prof-A")

    # 9. isolation membership backstop (docs §5)
    FakeAccounts.registry = {"prof-A": [{"field_id": "acc-A"}],
                             "prof-B": [{"field_id": "acc-B"}]}
    isolation._cache.clear()
    isolation.assert_member("acc-anything", profile_id=None, api_key="k")       # no-op
    ok("membership: transitional (no profile) is a no-op", True)
    isolation.assert_member("acc-A", profile_id="prof-A", api_key="k")
    ok("membership: member account passes", True)
    for aid, pid_, label in (("acc-B", "prof-A", "cross-profile"),
                             ("acc-A", "prof-UNKNOWN", "unknown profile (fail-closed)")):
        isolation._cache.clear()
        try:
            isolation.assert_member(aid, profile_id=pid_, api_key="k")
            ok(f"membership: {label} refused", False)
        except zernio.ZernioError as e:
            ok(f"membership: {label} refused", e.indeterminate is False)

    # 10. FAIL-CLOSED construction: a falsy key refuses at raw_client — the SDK's
    # env-var fallback (how a mis-keyed Space would operate as ANOTHER tenant) is dead.
    from core.vendors.zernio import transport
    FakeZernio.created.clear()
    try:
        transport.raw_client(None)
        ok("raw_client(None) refuses (kills the env fallback)", False)
    except zernio.ZernioError as e:
        ok("raw_client(None) refuses (kills the env fallback)",
           e.indeterminate is False and not FakeZernio.created)

    # 11. posts.create — same two guards as inbox.send (the OTHER mutating path)
    FakeZernio.created.clear()
    try:
        zernio.client({"zernio_key": None}).posts.create(
            content="x", media_items=[], platforms=[{"platform": "facebook",
                                                     "accountId": "acc-A"}])
        ok("posts.create refuses with no per-Space key", False)
    except zernio.ZernioError:
        ok("posts.create refuses with no per-Space key", not FakeZernio.created)
    FakeAccounts.registry = {"prof-A": [{"field_id": "acc-A"}]}
    isolation._cache.clear()
    FakePosts.created.clear()
    try:
        zernio.client({"zernio_key": "sk_A", "zernio_profile_id": "prof-A"}).posts.create(
            content="x", media_items=[], platforms=[{"platform": "facebook",
                                                     "accountId": "acc-B"}])
        ok("posts.create refuses a cross-profile target (isolation symmetry)", False)
    except zernio.ZernioError as e:
        ok("posts.create refuses a cross-profile target (isolation symmetry)",
           e.indeterminate is False and not FakePosts.created)
    pid = zernio.client({"zernio_key": "sk_A", "zernio_profile_id": "prof-A"}).posts.create(
        content="x", media_items=[], platforms=[{"platform": "facebook",
                                                 "accountId": "acc-A"}])
    ok("posts.create still posts for a member account", pid == "zp-1")

    # 12. envelope drift on posts.create is LOUD (no silent 'posted' sentinel)
    _orig_create = FakePosts.create
    FakePosts.create = lambda self, **kw: {"post": {}}
    try:
        zernio.client({"zernio_key": "sk_A"}).posts.create(
            content="x", media_items=[], platforms=[{"platform": "facebook",
                                                     "accountId": "acc-A"}])
        ok("posts.create with no id in the envelope raises indeterminate", False)
    except zernio.ZernioError as e:
        ok("posts.create with no id in the envelope raises indeterminate",
           e.indeterminate is True)
    FakePosts.create = _orig_create

    # 13. indeterminate classification by TYPE (double-post / park-forever guards)
    from core.vendors.zernio.errors import is_indeterminate
    RPE = type("RemoteProtocolError", (Exception,), {})
    RdE = type("ReadError", (Exception,), {})
    LCE = type("LateConnectionError", (Exception,), {})
    API = type("LateAPIError", (Exception,), {})
    e504 = API("[504] HTTP 504")
    e504.status_code = 504
    ok("RemoteProtocolError (request left, socket died) → indeterminate",
       is_indeterminate(RPE("Server disconnected without sending a response.")) is True)
    ok("empty-message ReadError → indeterminate (keywords can't catch '')",
       is_indeterminate(RdE("")) is True)
    ok("proxy 504 → indeterminate (LB may have forwarded)",
       is_indeterminate(e504) is True)
    ok("LateConnectionError (nothing left the box) → DETERMINATE, retry freely",
       is_indeterminate(LCE("Connection failed: [Errno 61] connection refused")) is False)
    # and through transport.call exactly the way a mutating send raises them:
    for exc, want, label in ((RPE("Server disconnected"), True, "RemoteProtocolError"),
                             (LCE("Connection failed: dns"), False, "LateConnectionError")):
        try:
            transport.call("inbox.send", lambda: (_ for _ in ()).throw(exc), mutating=True)
            ok(f"mutating {label} surfaces as ZernioError", False)
        except zernio.ZernioError as e:
            ok(f"mutating {label} → indeterminate={want}", e.indeterminate is want)

    # 14. membership cache is keyed on the (profile, key) PAIR — key rotation safe
    isolation._cache.clear()
    FakeAccounts.registry = {"prof-P": [{"field_id": "acc-P"}]}
    a1 = isolation.accounts_for_profile("prof-P", "key-1")
    FakeAccounts.registry = {"prof-P": [{"field_id": "acc-Q"}]}
    a2 = isolation.accounts_for_profile("prof-P", "key-2")   # new key must REFETCH
    ok("membership cache keyed on (profile, key) pair",
       a1 == {"acc-P"} and a2 == {"acc-Q"})

    test_a_stale_automation_is_not_reused_even_when_the_record_is_WRAPPED()
    test_a_VANISHED_automation_heals_instead_of_updating_a_dead_id()
    test_the_armed_but_dead_probe_refuses_to_page_on_an_UNREADABLE_vendor()

    print(f"\nALL {PASS} GATEWAY CHECKS PASS — the core.vendors.zernio contract "
          "(F-1..F-6 + isolation) holds against the faithful fake SDK.")



def test_a_stale_automation_is_not_reused_even_when_the_record_is_WRAPPED():
    """The guard, and the bug I put INSIDE the guard (2026-08-12).

    `get_comment_automation` returns the record wrapped — `{"automation": {...}}` — so reading
    `accountId` off the envelope returns None, which reads as "no mismatch" and updates the
    stale automation anyway. The tell was an asymmetry identical to the original defect: the
    keywords with NO prior automation healed via the create path, while every keyword that
    already had one stayed stranded on the disconnected account.

    So this asserts the unwrap, not just the comparison — a mismatch that cannot be SEEN is the
    same as no guard at all."""
    from core.vendors.zernio import model
    wrapped = {"automation": {"id": "a1", "accountId": "OLD", "name": "leadmagnet:X"}}
    inner = model.field(wrapped, "automation", "data") or wrapped
    ok("the record is unwrapped before the account is read",
       str(model.field(inner, "accountId", "account_id")) == "OLD")
    bare = {"id": "a1", "accountId": "OLD"}
    ok("…and a BARE record still works, so the unwrap is tolerant",
       str(model.field(model.field(bare, "automation", "data") or bare,
                       "accountId", "account_id")) == "OLD")

    src = pathlib.Path("core/vendors/zernio/client.py").read_text()
    up = src[src.index("def upsert("):src.index("def logs(")]
    ok("upsert unwraps rather than reading the envelope",
       'model.field(raw, "automation"' in up)
    ok("and it still refuses to update an automation on another account",
       "account_moved" in up and "automation_id = None" in up)


def test_a_VANISHED_automation_heals_instead_of_updating_a_dead_id():
    """A 404 is PROVABLY GONE, and the guard used to treat it as merely "unsure".

    `SCALE` was deleted in the Zernio UI. Our row kept its id, so every re-arm ran:
    get -> 404 -> bare `except` that logged "unsure means take the safe path" and then
    took NO path -> update(dead_id) -> 404. The keyword read as armed on the board for
    weeks while every viewer who commented it got silence.

    "Unsure" and "provably gone" call for OPPOSITE recoveries — leave it alone vs. rebuild
    it — so conflating them cannot be papered over with a retry. This asserts the split."""
    from core.vendors.zernio.errors import ZernioError, is_not_found

    class LateNotFoundError(Exception):
        pass

    wrapped = ZernioError("zernio comment_automations.get failed: [404] Automation not found")
    try:
        raise wrapped from LateNotFoundError("[404] Automation not found")
    except ZernioError as e:
        ok("a wrapped 404 is recognised through __cause__", is_not_found(e))
    ok("a bare [404] message is recognised too",
       is_not_found(RuntimeError("boom: [404] Automation not found")))
    ok("a TIMEOUT is NOT a 404 — unsure must never trigger a rebuild",
       not is_not_found(RuntimeError("zernio ... failed: read timed out")))
    ok("nor is a connection failure",
       not is_not_found(RuntimeError("Connection failed: dns")))

    src = pathlib.Path("core/vendors/zernio/client.py").read_text()
    up = src[src.index("def upsert("):src.index("def logs(")]
    ok("upsert asks whether the automation is provably gone", "is_not_found(e)" in up)
    ok("…and drops the dead id so the create branch mints a fresh one",
       "comment_automation_vanished" in up)


def test_the_armed_but_dead_probe_refuses_to_page_on_an_UNREADABLE_vendor():
    """The probe's whole value is that it separates 'no traffic yet' from 'no listener'.

    That only holds if a vendor blip cannot masquerade as an absence: an exception, or an
    EMPTY automation list (a scope or permission change), must stay green. Otherwise one bad
    API response pages naming every keyword we own, and the operator learns to ignore it —
    which is exactly how a real dead keyword would then slip past."""
    src = pathlib.Path("core/watchdog.py").read_text()
    fn = src[src.index("def _probe_leadmagnet_armed("):src.index("def _probe_leadmagnet_owed(")]
    ok("an unreadable space is skipped, never reported dead", "unreadable != absent" in fn)
    ok("an EMPTY list is treated as ambiguous, not as total death",
       "if not live:" in fn and "continue" in fn.split("if not live:")[1][:400])
    ok("a definite absence names the keyword and the space",
       "NO automation on Instagram" in fn)
    ok("the probe is registered", '"leadmagnet_armed":      _probe_leadmagnet_armed()' in src)
    ok("and it has an owner-facing consequence line",
       '"leadmagnet_armed":      "A keyword is advertised' in src)


if __name__ == "__main__":
    main()
