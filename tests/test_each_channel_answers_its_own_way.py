"""Each channel answers its own way: Leave to me, Draft or Auto-reply, and a style (owner, 2026-10-07).

Owner, 2026-10-07: "We also need to build different settings per channel. For example, DM's get an auto reply whereas
email just gets an auto draft." Shown a one-screen mockup: "Please just get everything ready for Webdev2 our app
developer." This holds the part under the page: inbox/answering.py (the one read and the one write the Channels page
calls) and the drafter obeying it. The page itself is WebDev2's (docs/SCOPE_CHANNELS_SCREEN.md).

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * an untouched box changes behaviour (every channel must read as it ran before: Draft, Customer service, no
    Auto-reply);
  * email can be set to answer on its own;
  * a channel set to Leave to me is still drafted, or the box-wide switch off stops a channel switched on alone;
  * Auto-reply on a channel doesn't switch on the sender's own gate (sending.auto_reply_since) or drafting under it;
  * a DM channel's own style is ignored, or the all-DMs style stops being the fallback;
  * a refused choice writes anything.

No network, no model.

Run: python tests/test_each_channel_answers_its_own_way.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "channels.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"

from core import box_settings, state  # noqa: E402

state.init_db()
from core.dispatch import app  # noqa: E402,F401
from marketing.customer_voice.drafter import draft, store  # noqa: E402
from marketing.customer_voice.inbox import answering, sending  # noqa: E402
from marketing.customer_voice.inbox import store as inbox_store  # noqa: E402

FAILS: list[str] = []


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:400]}"))
    if not cond:
        FAILS.append(label)


def row(ch: str) -> dict:
    return next(r for r in answering.get() if r["channel"] == ch)


def mode(ch: str) -> str:
    return row(ch)["mode"]


ASKED = []
_real_needs = store.needs_a_draft


def _spy(space, **kw):
    ASKED.append(kw)
    return []


def sweep_asks() -> dict | None:
    """What the drafter's sweep asks the queue for, or None when it didn't ask at all."""
    ASKED.clear()
    store.needs_a_draft = _spy
    try:
        draft.sweep("acme")
    finally:
        store.needs_a_draft = _real_needs
    return ASKED[0] if ASKED else None


print("test_an_untouched_box_runs_as_it_did")
ok("three channels, in the page's order", [r["channel"] for r in answering.get()] == ["email", "instagram", "messenger"])
ok("every channel drafts, in Customer service, and none answers on its own",
   all(r["mode"] == "draft" and r["style"] == "service" and not r["since"] for r in answering.get()), answering.get())
ok("email offers Leave to me and Draft only; the DMs add Auto-reply",
   [m["value"] for m in row("email")["modes"]] == ["off", "draft"]
   and [m["value"] for m in row("instagram")["modes"]] == ["off", "draft", "auto"])
ok("the sweep skips nothing", (sweep_asks() or {}).get("skip") == (), ASKED)

print("\ntest_email_never_sends_itself")
try:
    answering.put("email", mode="auto", by="owner")
    ok("email refuses Auto-reply", False)
except ValueError as e:
    ok("email refuses Auto-reply, in a sentence the page can show", "Email always waits for you" in str(e), e)
ok("...and nothing was written", mode("email") == "draft" and box_settings.get("inbox", "drafts.email") is None)

print("\ntest_auto_reply_is_the_senders_own_switch")
answering.put("instagram", mode="auto", by="owner")
ok("Instagram on Auto-reply turns on the sender's gate, from now", bool(sending.auto_reply_since("instagram"))
   and row("instagram")["since"] == sending.auto_reply_since("instagram"))
ok("...and drafting under it, since what sends is the draft", draft.drafts_for("instagram")
   and box_settings.get("inbox", "drafts.instagram") == "on")
answering.put("instagram", mode="draft", by="owner")
ok("back to Draft, nothing sends on its own", mode("instagram") == "draft" and not sending.auto_reply_since("instagram"))
answering.put("instagram", mode="auto", by="owner")

print("\ntest_leave_to_me_is_not_drafted")
inbox_store.upsert_conversation(space="acme", zcid="z-m", participant="Ana", account_id="a1", platform="messenger")
inbox_store.record_message(space="acme", zcid="z-m", zmid="m-m", direction="in", sent_by="contact", body="Open Sunday?")
inbox_store.upsert_conversation(space="acme", zcid="z-i", participant="Bo", account_id="a1", platform="instagram")
inbox_store.record_message(space="acme", zcid="z-i", zmid="m-i", direction="in", sent_by="contact", body="Price?")
answering.put("messenger", mode="off", by="owner")
ok("Messenger reads Leave to me", mode("messenger") == "off" and not draft.drafts_for("messenger"))
ok("the sweep asks the queue to skip it", (sweep_asks() or {}).get("skip") == ("messenger",), ASKED)
got = {r["zcid"] for r in store.needs_a_draft("acme", limit=10, skip=("messenger",))}
ok("...and the queue leaves its messages out, keeping the rest", got == {"z-i"}, got)

print("\ntest_the_box_wide_switch_still_stands_under_it")
box_settings.put("inbox", "drafts.enabled", False, set_by="owner")
ok("box-wide off: a channel set on its own still drafts, alone",
   (sweep_asks() or {}).get("platforms") == ("instagram",), ASKED)
ok("...email, never set here, follows the box-wide switch", mode("email") == "off" and not draft.drafts_for("email"))
answering.put("instagram", mode="off", by="owner")
ok("...and with every channel off, the sweep asks nothing at all", sweep_asks() is None, ASKED)
box_settings.put("inbox", "drafts.enabled", True, set_by="owner")

print("\ntest_each_dm_channel_keeps_its_own_style")
box_settings.put("inbox", "reply_style.dms", "subtle", set_by="owner")
ok("a DM channel nobody styled alone follows the all-DMs style", draft.reply_style("messenger") == "subtle"
   and row("messenger")["style"] == "subtle")
answering.put("instagram", style="sales", by="owner")
ok("Instagram styled on its own wins over it", draft.reply_style("instagram") == "sales"
   and row("instagram")["style"] == "sales")
ok("...email keeps its own", draft.reply_style("email") == "" and row("email")["style"] == "service")
ok("...and the drafter's instructions differ by channel, so the old drafts are rewritten in the new style",
   draft.rules("instagram") != draft.rules("messenger"))

answering.put("messenger", mode="draft", style="subtle", by="owner")
store.put(space="acme", zcid="z-m", in_reply_to="m-m", body="Yes, open 10 to 4 Sunday!", rules=draft.rules("messenger"))
store.put(space="acme", zcid="z-i", in_reply_to="m-i", body="From $120. Want a time?", rules=draft.rules("instagram"))
stale = {r["zcid"] for r in store.stale_waiting("acme", email_rules=draft.rules("email"), dms_rules=draft.rules("dm"),
                                                by_channel=draft._rules_by_channel(), limit=10)}
ok("a Messenger draft written under Messenger's own style is not stale because Instagram's differs (it would be "
   "rewritten, and paid for, every sweep)", stale == set(), stale)
box_settings.put("inbox", "reply_style.instagram", "service", set_by="owner")
stale = {r["zcid"] for r in store.stale_waiting("acme", email_rules=draft.rules("email"), dms_rules=draft.rules("dm"),
                                                by_channel=draft._rules_by_channel(), limit=10)}
ok("...while restyling Instagram makes Instagram's draft, and only it, stale", stale == {"z-i"}, stale)
answering.put("instagram", style="sales", by="owner")
answering.put("messenger", mode="off", by="owner")

print("\ntest_a_refused_choice_writes_nothing")
before = answering.get()
for args in (("messenger", {"mode": "auto", "style": "loud"}), ("whatsapp", {"mode": "draft"}),
             ("messenger", {"mode": "sometimes"})):
    try:
        answering.put(args[0], by="owner", **args[1])
        ok(f"refused: {args}", False)
    except ValueError:
        pass
ok("three bad choices refused, and every channel reads as before", answering.get() == before)
ok("one line in words for the overview and the tools",
   answering.in_words() == "Email: Draft · Instagram: Leave to me · Messenger: Leave to me", answering.in_words())

print("\nALL EACH-CHANNEL CHECKS PASS" if not FAILS else f"\n{len(FAILS)} EACH-CHANNEL CHECK(S) FAILED")
sys.exit(1 if FAILS else 0)
