"""Instagram is the second channel — and a second channel is not a second string.

Owner, 2026-09-15: "Instagram is the first channel, start building ingestion."

INGESTION IS THE EASY HALF AND IT IS NOT WHAT THIS SUITE IS ABOUT. Zernio returns IG DMs
only under platform="instagram" and Messenger DMs only under "facebook", so the intake fix
is one more list call. The half that can hurt a real person is the SEND: this box already
auto-sends an opener, and every guard that decides whether it may — the 24h window most of
all — is keyed by a platform string that, until this change, no code ever supplied. A
handler that inherited `allowed_send`'s Messenger default would answer an Instagram thread
under Messenger's rulebook, and `window.py` says in its own docstring that this is the one
thing it exists to prevent: "adding a platform string to the poller must never be enough,
by itself, to authorise a send on it." That sentence is only true if something checks it.
This suite is that something.

What it holds to the wall:
  · the vocabulary      — vendor token vs stored key vs label, in one place, each distinct
  · the pairing guard   — no channel is polled that has no send rule written for it
  · ingestion           — an IG thread lands, stamped `instagram`, with ITS OWN account
  · channel isolation   — the IG call failing forever never costs Messenger its intake,
                          its heartbeat, or its throttle slot
  · the send            — the window is asked about THIS thread's channel, and a channel
                          with no rule sends nothing at all
  · the lake            — an IG contact is NOT laked under a Messenger match key

Run: python tests/test_inbox_instagram.py
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "ig.db")
os.environ.pop("ZERNIO_API_KEY", None)

from core.vendors import zernio                                       # noqa: E402


class FakeInbox:
    """A gateway that answers PER CHANNEL, because the real one does.

    The single most important line in this class is that `list` consults `platform`. A
    fake that ignored it would return the same conversation to every channel in the sweep
    — and a sweep that never passed `platform` at all would then look like it worked.
    """

    def __init__(self):
        self.pages: dict = {}          # vendor token -> page
        self.raises: dict = {}         # vendor token -> exception
        self.messages_by_conv: dict = {}
        self.sent: list = []
        self.calls: list = []          # every (platform) asked for, in order

    def list(self, *, limit=50, platform="facebook"):
        self.calls.append(platform)
        if platform in self.raises:
            raise self.raises[platform]
        return self.pages.get(platform, {"conversations": [], "next_cursor": None})

    def messages(self, cid, account_id, *, limit=100):
        return {"messages": self.messages_by_conv.get(cid, []), "next_cursor": None}

    def send(self, cid, account_id, text, *, tag=None):
        self.sent.append({"cid": cid, "account_id": account_id, "text": text})
        return {"message_id": f"out-{len(self.sent)}"}


class FakeScoped:
    def __init__(self, inbox):
        self.inbox = inbox


INBOX = FakeInbox()
zernio.client = lambda space: FakeScoped(INBOX)

from core import slack, state                                         # noqa: E402
from core.config import get_config                                    # noqa: E402
from marketing.customer_voice.inbox import (                          # noqa: E402
    channels, handler, poller, store, window)

_failed = 0
SPACE = "meet-mavrick"


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def _conv(cid, acct, activity, **extra):
    return dict({"id": cid, "accountId": acct, "updatedTime": activity}, **extra)


def _msg(mid, text, when):
    """THE SHAPE THE VENDOR ACTUALLY SENDS, not the one we assumed.

    This fixture used to say `direction: "in"` and `createdAt`. Zernio sends `direction:
    "incoming"` and `sentAt`, and carries no `fromMe` at all (OSDev1's live probe, 2026-09-16) —
    so every suite built on this helper passed while the poller kept 0 of 22 real messages and the
    live box showed zero conversations. A fixture written from an assumption can only ever prove
    the code agrees with the assumption.

    BOTH CLOCKS RIDE, BECAUSE THE VENDOR SENDS BOTH. OSDev1 measured the live account on
    2026-09-17: `createdAt` is on all 58 messages, ISO-8601 Z, equal to `sentAt` on every one. A
    fixture carrying only `sentAt` implies reading `createdAt` finds nothing, which is what I
    wrongly published as the reason the clock was empty. It never was empty."""
    return {"_id": mid, "accountId": "acc-x", "conversationId": "conv-x",
            "direction": "incoming", "senderId": "sender-x", "message": text,
            "sentAt": when, "createdAt": when}


def _reset_sweep():
    """A sweep talks to exactly one Space and starts from no throttle state."""
    poller._poll_fail.clear()
    poller._spaces = lambda: [{"name": SPACE, "zernio_key": "sk_mm",
                               "zernio_profile_id": None, "slack_channel": "C_VOICE"}]
    INBOX.pages, INBOX.raises, INBOX.calls = {}, {}, []


# ── 1. the vocabulary ────────────────────────────────────────────────────────────────
def test_the_three_names_of_a_channel_are_kept_apart():
    print("test_the_three_names_of_a_channel_are_kept_apart")
    by_key = {c.key: c for c in channels.POLLED}
    ok("Messenger is polled under the vendor's token, not its own name",
       by_key["messenger"].vendor == "facebook")
    ok("...and is STORED as 'messenger', which is what the screen and window.py read",
       by_key["messenger"].key == "messenger")
    ok("Instagram is polled and stored under 'instagram'",
       by_key["instagram"].vendor == "instagram" and by_key["instagram"].key == "instagram")
    ok("no two channels share a vendor token",
       len({c.vendor for c in channels.POLLED}) == len(channels.POLLED))
    ok("no two channels share a stored key",
       len({c.key for c in channels.POLLED}) == len(channels.POLLED))
    # Messenger first is a deliberate ordering, not an accident of typing: a box with no IG
    # account errors on the IG call every single sweep, and the channel carrying live leads
    # must not queue behind the one that is guaranteed to fail.
    ok("Messenger is swept first — the channel with live leads never waits on a new one",
       channels.POLLED[0].key == "messenger")
    # EVERY POLLED CHANNEL HAS A WRITTEN NAME. The fallback title-cases the stored key, which
    # is fine for a channel nobody has met yet and wrong for one we ingest on every sweep:
    # "Sms" and "Whatsapp" are what a product looks like when nobody read its own screen.
    unnamed = [c.key for c in channels.POLLED if c.key not in channels.NAMES]
    ok("every polled channel has a name written for it", not unnamed, str(unnamed))
    # The fallback is the bug this table replaced: a default that names a channel is how an
    # Instagram notification ends up telling the owner it was a Messenger one.
    ok("an unknown channel's label NEVER borrows a known channel's name",
       channels.label("whatsapp") not in ("Messenger", "Instagram")
       and channels.label("") not in ("Messenger", "Instagram")
       and channels.label(None) not in ("Messenger", "Instagram"))
    ok("...and the known ones still read like themselves",
       channels.label("instagram") == "Instagram" and channels.label("messenger") == "Messenger")
    # ONE TABLE, TWO WORDS FOR EMPTY, neither inherited by accident: `name` has no default
    # fallback, so a caller cannot forget to say which sentence it is writing.
    ok("the empty fallback is the CALLER's word, not a shared default",
       channels.name("", fallback="Unknown") == "Unknown"
       and channels.label("") == "this channel")
    # ASSERTED ON THE SIGNATURE, because "the caller must choose" is a property of the
    # function and not of any one call: checking a call that passes `fallback` still passes
    # the day someone gives the parameter a default, and from then on the caller that forgot
    # silently inherits the other one's word for empty. That is the whole design claim here,
    # so it is held where it can actually break.
    import inspect
    ok("`name` REFUSES to pick a fallback for you — it has no default",
       inspect.signature(channels.name).parameters["fallback"].default
       is inspect.Parameter.empty)
    try:
        channels.name("")
        ok("...and calling it without one is an error, not a guess", False, "no TypeError")
    except TypeError:
        ok("...and calling it without one is an error, not a guess", True)
    ok("...and a named channel reads the same whichever caller asks",
       channels.name("sms", fallback="Unknown") == channels.label("sms") == "SMS")


# ── 2. the pairing guard ─────────────────────────────────────────────────────────────
def test_no_channel_is_polled_that_nobody_wrote_a_send_rule_for():
    print("test_no_channel_is_polled_that_nobody_wrote_a_send_rule_for")
    # THE GUARD THAT MAKES window.py's PROMISE CHECKABLE. Ingesting a channel is harmless;
    # ingesting it and then auto-replying on it under another channel's clock is not. The
    # two lists are written in different files by different hands, so pair them here: a
    # channel added to the poller with no rule beside it fails this line, in CI, before a
    # single DM goes out under a rulebook nobody read.
    missing = [c.key for c in channels.POLLED if c.key not in window._RULES]
    ok("every polled channel has a send rule written for it", not missing, str(missing))
    fresh = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    ok("...and a channel with no rule BLOCKS rather than inheriting one",
       window.decide("whatsapp", fresh)["decision"] == window.BLOCKED)
    ok("...as does an empty channel, which is what a row with no platform would give",
       window.decide("", fresh)["decision"] == window.BLOCKED)


# ── 3. ingestion ─────────────────────────────────────────────────────────────────────
def test_an_instagram_dm_reaches_the_box_stamped_as_instagram():
    print("test_an_instagram_dm_reaches_the_box_stamped_as_instagram")
    _reset_sweep()
    now = datetime.now(timezone.utc).isoformat()
    # Disjoint pages — exactly as the vendor answers. The IG thread is INVISIBLE to the
    # facebook call, so a sweep that only ever asked for facebook would score 0 here.
    INBOX.pages["facebook"] = {"conversations": [
        _conv("zc-fb", "acc-page", "a1", participantId="psid-fb", participantName="Dana R.")]}
    INBOX.pages["instagram"] = {"conversations": [
        _conv("zc-ig", "acc-ig", "a1", participantId="igid-9", participantName="Ravi P.")]}
    INBOX.messages_by_conv = {"zc-fb": [_msg("m-fb", "do you take walk-ins?", now)],
                              "zc-ig": [_msg("m-ig", "how much for a cleaning?", now)]}

    res = poller.poll_sweep()
    # THE VENDOR CHANNELS, NOT ALL OF POLLED. Email is polled now and arrives over IMAP with a
    # credential of its own, so it makes no call to this vendor at all — comparing against every
    # polled channel would assert that the Zernio client is asked for a mailbox. The property this
    # line was written for is unchanged: every channel the VENDOR serves is asked for by name,
    # rather than one of them riding the client's Messenger default.
    # FROM `channels.NOT_INBOX_LIST`, NOT A HAND-KEPT EXCEPTION. This read `!= channels.IMAP`,
    # written when email arrived; comments then needed the identical edit here, which is how the
    # third such channel gets forgotten and this assertion starts demanding a `inbox.list` call
    # for something that never makes one. The exclusions are named beside the channels now.
    _vendor_polled = [c.vendor for c in channels.POLLED
                      if c.vendor not in channels.NOT_INBOX_LIST]
    ok("the sweep asks the vendor for BOTH channels",
       INBOX.calls == _vendor_polled, f"{INBOX.calls} != {_vendor_polled}")
    ok("both threads are ingested in one sweep", res["enqueued"] == 2, str(res))

    ig = store.get_conversation(SPACE, "zc-ig")
    fb = store.get_conversation(SPACE, "zc-fb")
    ok("the IG thread is stored", ig is not None)
    ok("...stamped `instagram`", ig and ig["platform"] == "instagram")
    # The vendor calls it "facebook"; the store has said "messenger" since the first row and
    # the screen, the chip row and window._RULES all read that value. Leaking the vendor's
    # token into the column would split one channel into two everywhere downstream.
    ok("the Messenger thread is stored as `messenger`, never the vendor's `facebook`",
       fb and fb["platform"] == "messenger")
    # THE SEND SAFETY PROPERTY, AT ITS ROOT. `inbox.send` takes a conversation and the
    # account that owns it — there is no platform argument to get wrong. An IG thread
    # answers from the IG account for exactly one reason: this row.
    ok("each thread carries ITS OWN account — an IG reply can never leave the page account",
       ig["account_id"] == "acc-ig" and fb["account_id"] == "acc-page")

    with state.connect() as c:
        row = c.execute("SELECT raw_text FROM jobs WHERE idempotency_key=?",
                        (f"inbox:{SPACE}:zc-ig:m-ig",)).fetchone()
    payload = json.loads(row["raw_text"]) if row else {}
    ok("the job payload names the channel too (the handler's row-less fallback needs it)",
       payload.get("platform") == "instagram", str(payload)[:120])
    ok("...and the account rides with it", payload.get("account_id") == "acc-ig")

    ok("a re-poll with unchanged activity ingests nothing on either channel",
       poller.poll_sweep()["enqueued"] == 0)


# ── 4. channel isolation ─────────────────────────────────────────────────────────────
def test_a_channel_that_fails_forever_never_costs_the_other_one_anything():
    print("test_a_channel_that_fails_forever_never_costs_the_other_one_anything")
    _reset_sweep()
    now = datetime.now(timezone.utc).isoformat()
    # The ordinary shape of a real box: a Facebook page connected, no Instagram account. The
    # IG call errors on every 45s sweep, forever. That is a configuration fact, not an outage.
    INBOX.raises["instagram"] = zernio.ZernioError("PLATFORM_NOT_SUPPORTED")
    INBOX.pages["facebook"] = {"conversations": [_conv("zc-fb2", "acc-page", "b1")]}
    INBOX.messages_by_conv = {"zc-fb2": [_msg("m-fb2", "are you open sunday?", now)]}

    warns: list = []
    infos: list = []
    _svw, _svi = poller.log.warning, poller.log.info
    poller.log.warning = lambda msg, **k: warns.append((msg, k))
    poller.log.info = lambda msg, **k: infos.append((msg, k))
    try:
        res = poller.poll_sweep()
        ok("Messenger intake is untouched by the Instagram failure", res["enqueued"] == 1)
        with state.connect() as c:
            hb = c.execute("SELECT status FROM heartbeats WHERE component='inbox_poll'"
                           ).fetchone()
        # A pager that fires every 45 minutes about a channel the client never connected is
        # a pager nobody reads on the day Messenger actually dies.
        ok("...and the box does not page: one channel listing is 'ok'",
           hb and hb["status"] == "ok", str(dict(hb) if hb else None))
        ok("the failing channel is warned about BY NAME", warns and
           [k.get("channel") for m, k in warns if m == "inbox.poll_list_failed"] == ["instagram"])

        # THE THROTTLE HAS TO SURVIVE THE OTHER CHANNEL SUCCEEDING, and that is the whole
        # reason the failure state is keyed per (Space, channel) rather than per Space. On
        # this box Messenger succeeds on every single sweep — and under a per-Space key that
        # success CLEARS the Instagram failure state, so the permanently-broken channel warns
        # again on the next sweep, and the next, forever, each one preceded by a "recovered"
        # line for a channel that never recovered. That is the ~2.9k-lines-a-day spam this
        # throttle was written to stop, returning with a false all-clear on top of it.
        for _ in range(4):
            poller.poll_sweep()
        ok("...ONCE across five sweeps, though Messenger succeeded between every one",
           [k.get("channel") for m, k in warns if m == "inbox.poll_list_failed"] == ["instagram"],
           str([k.get("channel") for m, k in warns if m == "inbox.poll_list_failed"]))
        ok("...and nothing is ever logged 'recovered' while it is still failing",
           not [k for m, k in infos if m == "inbox.poll_recovered"],
           str([k for m, k in infos if m == "inbox.poll_recovered"]))

        # And the channel that is always broken must not be able to hide the channel that
        # just broke: live Messenger intake dying is the alarm that matters most on this box.
        warns.clear()
        INBOX.raises["facebook"] = zernio.ZernioError("revoked key")
        poller.poll_sweep()
        ok("Messenger's FIRST failure still warns, though Instagram is mid-throttle",
           "messenger" in [k.get("channel") for m, k in warns
                           if m == "inbox.poll_list_failed"])
        with state.connect() as c:
            hb = c.execute("SELECT status FROM heartbeats WHERE component='inbox_poll'"
                           ).fetchone()
        ok("...and with NOTHING listing anywhere, the heartbeat finally says 'fail'",
           hb and hb["status"] == "fail", str(dict(hb) if hb else None))
    finally:
        poller.log.warning, poller.log.info = _svw, _svi


# ── 5. the send ──────────────────────────────────────────────────────────────────────
def test_the_send_window_is_asked_about_this_threads_own_channel():
    print("test_the_send_window_is_asked_about_this_threads_own_channel")
    now = datetime.now(timezone.utc)
    get_config()["inbox"] = {"autonomy": "opener", "hourly_send_cap": 40}
    handler._space = lambda name: {"name": name, "zernio_key": "sk_mm",
                                   "zernio_profile_id": None, "opener_template": "Hi!"}
    slack.post = lambda ch, text, blocks=None: SLACK.append(text) or True

    asked: list = []
    _sv = window.allowed_send
    window.allowed_send = lambda last, now=None, platform="messenger": (
        asked.append(platform) or _sv(last, now, platform))
    try:
        store.upsert_conversation(space=SPACE, zcid="zc-ig-w", platform="instagram",
                                  account_id="acc-ig", last_inbound_at=now.isoformat())
        job = {"id": "jw", "slack_channel_id": "C_VOICE", "raw_text": json.dumps(
            {"space": SPACE, "zcid": "zc-ig-w", "account_id": "acc-ig",
             "platform": "instagram", "inbound_msg_id": "m-w",
             "inbound_text": "hey!", "inbound_at": now.isoformat()})}
        before = len(INBOX.sent)
        res = handler.handle(job)
        ok("the window is asked about INSTAGRAM, not about the default",
           asked == ["instagram"], str(asked))
        ok("a fresh IG inbound is inside its own 24h window and the opener goes",
           res["status"] == "opened" and len(INBOX.sent) == before + 1, str(res))
        ok("...sent from the IG account on the row", INBOX.sent[-1]["account_id"] == "acc-ig")
        ok("...and the owner is told which channel it was",
           any("Instagram" in m for m in SLACK) and not any(
               "New Messenger ad lead" in m for m in SLACK[-3:]))

        # THE ROW OUTRANKS THE PAYLOAD. A requeue of a job written by an older poller must
        # not be able to re-label a thread the human is looking at.
        asked.clear()
        job_lying = dict(job, id="jw2", raw_text=json.dumps(
            {"space": SPACE, "zcid": "zc-ig-w", "account_id": "acc-ig",
             "platform": "messenger", "inbound_msg_id": "m-w",
             "inbound_text": "hey!", "inbound_at": now.isoformat()}))
        handler.handle(job_lying)
        ok("a payload that disagrees with the row is judged by the ROW's channel",
           asked == ["instagram"], str(asked))

        # FAIL CLOSED. A channel somebody ingested without writing a rule for sends NOTHING.
        # Before the handler passed a platform through, this thread would have been judged
        # by Messenger's clock and auto-replied — which is the whole point of the change.
        asked.clear()
        store.upsert_conversation(space=SPACE, zcid="zc-unknown", platform="whatsapp",
                                  account_id="acc-wa", last_inbound_at=now.isoformat())
        job_u = {"id": "ju", "slack_channel_id": "C_VOICE", "raw_text": json.dumps(
            {"space": SPACE, "zcid": "zc-unknown", "account_id": "acc-wa",
             "platform": "whatsapp", "inbound_msg_id": "m-u",
             "inbound_text": "hello?", "inbound_at": now.isoformat()})}
        before = len(INBOX.sent)
        res = handler.handle(job_u)
        ok("a channel with no send rule is BLOCKED, however fresh the message",
           res["status"] == "window_blocked" and len(INBOX.sent) == before, str(res))
    finally:
        window.allowed_send = _sv


# ── 6. the lake ──────────────────────────────────────────────────────────────────────
def test_an_instagram_contact_is_not_laked_under_a_messenger_match_key():
    print("test_an_instagram_contact_is_not_laked_under_a_messenger_match_key")
    # `messenger_psid` IS A MATCH KEY (docs/PROSPECTS_CONTRACT.md). Writing an Instagram id
    # under it would merge two strangers into one prospect, and there is no clean way back
    # from a bad merge. Instagram's own identity (`instagram_user_id`, channel `ig_dm`) and
    # its touch.utm vocabulary belong to WS2/WS4 — ingestion does not need the lake to work,
    # and inventing a token inside someone else's contract does need their say-so.
    with state.connect() as c:
        rows = c.execute("SELECT idempotency_key FROM prospects_outbox").fetchall()
    keys = [r["idempotency_key"] for r in rows]
    ok("the Messenger contact IS laked (that path is untouched)",
       "outbox:messenger-create:psid-fb" in keys, str(keys))
    ok("the Instagram contact is NOT laked under a Messenger key",
       not any("igid-9" in k for k in keys), str(keys))


SLACK: list = []

if __name__ == "__main__":
    state.init_db()
    slack.post = lambda ch, text, blocks=None: SLACK.append(text) or True
    test_the_three_names_of_a_channel_are_kept_apart()
    test_no_channel_is_polled_that_nobody_wrote_a_send_rule_for()
    test_an_instagram_dm_reaches_the_box_stamped_as_instagram()
    test_a_channel_that_fails_forever_never_costs_the_other_one_anything()
    test_the_send_window_is_asked_about_this_threads_own_channel()
    test_an_instagram_contact_is_not_laked_under_a_messenger_match_key()
    print("\nall ok" if not _failed else f"\n{_failed} FAILED")
    sys.exit(1 if _failed else 0)
