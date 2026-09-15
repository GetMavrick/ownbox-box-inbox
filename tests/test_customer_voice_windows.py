"""Per-platform send windows, and the rule that no lane ships without a citation.

docs/SPEC_CUSTOMER_VOICE_BUILD.md §4.3 made one thing the condition of every send lane in this
machine: somebody reads the platform's own policy and writes the citation into the code. This file
is what turns that from a good intention into something that fails the build.

WHAT IT GUARDS, and every one of these has a specific way of going wrong quietly:

  · A NEW PLATFORM INHERITING MESSENGER'S WINDOW. Adding a platform string to the poller must not,
    by itself, authorise sending on it. An unknown platform REFUSES.
  · A RULE WITH NO SOURCE. The claim that started this whole exercise came from a screenshot and
    had no primary source anywhere. So the test reads the module and asserts every platform named
    in the rules is also cited in the file.
  · INSTAGRAM QUIETLY GAINING A 7-DAY TAG LANE. Meta's own pages disagree about whether the
    human-agent tag works on Instagram. 24 hours is the half both agree on. A future edit that
    raises it needs a citation, and this test is what makes that edit visible.
  · TAGGED OR LIMITED BEING TREATED AS FREEFORM. A tagged message must not be promotional and a
    limited one spends a bounded allowance. Neither may open the auto-opener's door.

Run: python tests/test_customer_voice_windows.py
"""
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ.setdefault("AIOS_DB_PATH", tempfile.mkdtemp() + "/w.db")

from marketing.customer_voice.inbox import window  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


def ago(**kw):
    return (NOW - timedelta(**kw)).isoformat()


def d(platform, when):
    return window.decide(platform, when, now=NOW)


# ── Messenger: 24h free, then a 7-day tag lane ───────────────────────────────────────────
ok("messenger, 1h after their message, is freeform", d("messenger", ago(hours=1))["decision"] == window.FREEFORM)
ok("messenger at 23h59m is still freeform", d("messenger", ago(hours=23, minutes=59))["decision"] == window.FREEFORM)
ok("messenger at 25h is TAGGED, not blocked — the human-agent lane is real",
   d("messenger", ago(hours=25))["decision"] == window.TAGGED)
ok("and the reason says the tagged message must not be promotional — same text, different "
   "legality, decided by the clock",
   "promotional" in d("messenger", ago(hours=25))["reason"].lower())
ok("messenger at 6 days is still tagged", d("messenger", ago(days=6))["decision"] == window.TAGGED)
ok("messenger at 8 days is blocked", d("messenger", ago(days=8))["decision"] == window.BLOCKED)


# ── Instagram: 24h, and NO tag lane, because Meta's own pages disagree ────────────────────
ok("instagram at 1h is freeform", d("instagram", ago(hours=1))["decision"] == window.FREEFORM)
ok("instagram at 25h is BLOCKED — the 7-day tag is not confirmed for Instagram, so it is not "
   "granted", d("instagram", ago(hours=25))["decision"] == window.BLOCKED)
ok("instagram carries no tag_hours in its rule at all",
   window._RULES["instagram"]["tag_hours"] is None)


# ── TikTok: 48h, and it DEGRADES rather than closing ─────────────────────────────────────
ok("tiktok at 1h is freeform", d("tiktok", ago(hours=1))["decision"] == window.FREEFORM)
ok("tiktok at 47h is still freeform", d("tiktok", ago(hours=47))["decision"] == window.FREEFORM)
ok("tiktok at 49h is LIMITED, not blocked — the window narrows, it does not shut",
   d("tiktok", ago(hours=49))["decision"] == window.LIMITED)
ok("and it says how many are left, because 'limited' with no number is not actionable",
   d("tiktok", ago(hours=49))["remaining"] == 3)
ok("tiktok is still limited a month later — only their reply reopens it",
   d("tiktok", ago(days=30))["decision"] == window.LIMITED)


# ── fail closed ──────────────────────────────────────────────────────────────────────────
for p in ("reddit", "whatsapp", "instagram_comment", "", "MESSENGER_TYPO", "google_review"):
    r = d(p, ago(hours=1))
    ok(f"an unwritten platform {p!r} REFUSES — adding a string to the poller cannot authorise it",
       r["decision"] == window.BLOCKED, str(r))
ok("and the refusal explains itself rather than just saying no",
   "nobody has read" in d("reddit", ago(hours=1))["reason"])

ok("no inbound on record is blocked — every channel here is reply-only",
   d("messenger", None)["decision"] == window.BLOCKED)
ok("an unparseable timestamp is blocked, never treated as fresh",
   d("messenger", "not-a-date")["decision"] == window.BLOCKED)
ok("a FUTURE timestamp is blocked — clock skew is not evidence a window is open",
   d("messenger", (NOW + timedelta(hours=2)).isoformat())["decision"] == window.BLOCKED)
ok("a naive timestamp is read as UTC rather than crashing",
   d("messenger", (NOW - timedelta(hours=1)).replace(tzinfo=None).isoformat())["decision"]
   == window.FREEFORM)
ok("every answer carries a reason a human can read in the queue",
   all(d(p, t)["reason"] for p in ("messenger", "instagram", "tiktok", "nope")
       for t in (ago(hours=1), ago(days=9), None)))


# ── the Phase-1 door stays exactly as narrow as it was ───────────────────────────────────
ok("allowed_send still answers 'freeform' inside the window",
   window.allowed_send(ago(hours=1), now=NOW) == "freeform")
ok("allowed_send still answers 'blocked' at 25h", window.allowed_send(ago(hours=25), now=NOW) == "blocked")
ok("allowed_send still blocks with no inbound", window.allowed_send(None, now=NOW) == "blocked")
ok("it defaults to messenger, so the existing caller is unchanged",
   window.allowed_send(ago(hours=1), now=NOW) == window.allowed_send(ago(hours=1), now=NOW, platform="messenger"))

# THE ONE THAT MATTERS MOST HERE: a tagged or limited permission must NOT open the auto-opener.
ok("TAGGED does not become 'freeform' — a tagged message must not be promotional, and the opener "
   "cannot reason about that", window.allowed_send(ago(hours=25), now=NOW, platform="messenger") == "blocked")
ok("LIMITED does not become 'freeform' — it spends a bounded allowance a human should see",
   window.allowed_send(ago(hours=49), now=NOW, platform="tiktok") == "blocked")


# ── §4.3's rule, enforced rather than hoped for ──────────────────────────────────────────
# THE CITATION IS A FIELD ON EACH RULE, not prose in the file (OSDev1's ask on #1104). A
# file-level check passes as long as SOME url is somewhere in the module, so a fourth platform
# added with no source would have sailed through on the strength of Messenger's link. Per-rule is
# the only version that actually gates a new platform.
for platform, rule in window._RULES.items():
    cite = rule.get("cite")
    ok(f"the {platform} rule carries its own citation field", bool(cite), str(rule))
    ok(f"and {platform}'s citation is a URL, not a name",
       isinstance(cite, str) and cite.startswith("https://"), str(cite))

ok("messenger's tag lane cites the human-agent feature page SEPARATELY from the window",
   window._RULES["messenger"]["cite_tag"] != window._RULES["messenger"]["cite"])
ok("instagram carries, AS DATA, why it has no tag lane — a comment would not survive a refactor",
   "disagree" in window._RULES["instagram"]["tag_unconfirmed"].lower())
ok("tiktok records its reply-only source too", window._RULES["tiktok"]["cite_reply_only"].startswith("https://"))
ok("and the regions where that lane cannot exist at all",
   set(window._RULES["tiktok"]["region_blocked"]) == {"EEA", "Switzerland", "UK"})

# The citation travels WITH the answer, so whoever reads a refusal in the queue can check it.
for p_, when in (("messenger", ago(hours=1)), ("messenger", ago(hours=25)),
                 ("tiktok", ago(hours=49)), ("instagram", ago(days=9))):
    ok(f"{p_} hands its citation back with the decision", d(p_, when).get("cite", "").startswith("https://"),
       str(d(p_, when)))

_src = (ROOT / "marketing" / "customer_voice" / "inbox" / "window.py").read_text()
ok("the module still names the channels deliberately absent, so absence is not oversight",
   "instagram_comment" in _src and "reddit" in _src and "google_review" in _src)


print(("FAILED " + str(_failed)) if _failed else "all ok")
sys.exit(1 if _failed else 0)
