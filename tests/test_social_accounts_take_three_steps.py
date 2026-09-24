"""Connecting social accounts takes three steps, and asks for a card only where Zernio does (walk #11).

docs/JOURNEY_WALK_2026-09-23.md, finding 11: the social accounts step was "a second business
account" — create an account, add a payment method, create an API key — three things a
non-technical buyer has never done. Zernio's docs (docs.zernio.com, read 2026-09-24) say "The first
2 connected accounts are free without a card, except X", so the card step was friction most buyers
never needed. It now lives in the note, said once, where it applies.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  · a fourth step comes back, or any step asks for a payment method up front;
  · the note stops saying when a card IS needed (after two accounts, and for X), so a buyer who
    hits Zernio's limit is surprised by it;
  · a buyer reads a reserved noun (CLAUDE.md, mobile first).

Run: python tests/test_social_accounts_take_three_steps.py
"""
import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "social.db")

from core import box_secrets  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


step = next(s for s in box_secrets.SETUP_STEPS if s["key"] == "zernio")
steps = tuple(step.get("steps") or ())
print("\ntest_three_steps")
ok("the social accounts step has three steps", len(steps) == 3, str(len(steps)))
ok("no step asks for a payment method up front",
   not any(re.search(r"payment|card", s, re.I) for s in steps), str(steps))
ok("the API keys page is one tap away, on the verified link",
   (step.get("help") or {}).get("url") == "https://zernio.com/dashboard/api-keys")
ok("the API key and the connecting are both still there",
   any("API key" in s for s in steps) and any("connect" in s for s in steps))

print("\ntest_the_card_is_said_where_it_applies")
note = str(step.get("note") or "")
ok("the note says the key and the first two accounts need no card",
   "No card is needed for the key or for your first two connected accounts" in note, note)
ok("...and that X needs one from the start", "X from the start" in note, note)

print("\ntest_the_card_rule_is_cited")
_src = (ROOT / "core" / "box_secrets.py").read_text()
ok("the card rule cites Zernio's own page, with its words", "https://docs.zernio.com/" in _src
   and "The first 2 connected accounts are free without a card" in _src)

print("\ntest_the_vocabulary")
said = " ".join(steps) + " " + note + " " + str(step.get("why") or "")
banned = re.findall(r"\b(phone|phones|ring|call|calls|dial|line|voice|answer)\b", said, re.I)
ok("no reserved noun in the step", not banned, str(banned))

print("\ntest_the_key_field_is_labelled")
# THE PLACEHOLDER IS GONE THE MOMENT THEY PASTE, so the field says what it holds in a label, like
# the mailbox form. Only where this box carries the inbox, which draws the page.
if (ROOT / "marketing" / "customer_voice" / "app.py").is_file():
    os.environ.setdefault("DASH_TOKEN", "pw")
    from core import state, dash                                      # noqa: E402
    state.init_db()
    from core.dispatch import app                                     # noqa: E402
    _c = app.test_client()
    _c.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
    box_secrets.clear(box_secrets.ZERNIO)
    _page = _c.get("/inbox/connect").get_data(as_text=True)
    ok("the key field on /inbox/connect carries a label", "Your Zernio API key" in _page
       and 'name="key"' in _page, _page[-300:])
else:
    print("  --   no inbox machine ships on this box, so there is no connect page to check")

print("\n— and this file cannot silently fall out of CI —")
_wf = ROOT / ".github/workflows/tests.yml"
if _wf.is_file():
    ok("test_social_accounts_take_three_steps is in the workflow's suite list",
       "test_social_accounts_take_three_steps" in _wf.read_text())

print("\nALL OK" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
