"""A shift's email comes from "Your Ownbox" and reads as plain words (owner, 2026-10-06).

The owner's shift email arrived from "Your box", showed `[October 5 report](https://…)` and `**…**` exactly as the
coworker typed them, and stopped mid-word at "is miss". He: "It's always your Ownbox. We want to brand that term."

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * the From name of a box without a brand name of its own going back to "Your box";
  * a box's own brand name no longer winning over the default;
  * a coworker's Markdown reaching the email as typed;
  * a long account being cut inside a word, or without saying it was cut;
  * the runner putting the coworker's raw text in the receipt instead of the plain words.

Run: python tests/test_the_shift_email_says_your_ownbox.py
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "shift_email.db")

from core import notify  # noqa: E402
from core.coworkers import runner  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -- {str(detail)[:400]}" if not cond and detail else ""))
    if not cond:
        _failed += 1


print("test_the_email_comes_from_your_ownbox")
import core.config as _cfg  # noqa: E402
_orig = _cfg.get_config
try:
    _cfg.get_config = lambda: {}
    ok("a box with no brand name of its own sends as \"Your Ownbox\"", notify._sender_name() == "Your Ownbox",
       notify._sender_name())
    _cfg.get_config = lambda: {"brand": {"name": "Glow Med Spa"}}
    ok("...and a box with one sends as its own name", notify._sender_name() == "Glow Med Spa", notify._sender_name())
finally:
    _cfg.get_config = _orig
ok("the test email's sender is \"Your Ownbox\" too",
   'sender_name="Your Ownbox"' in (ROOT / "core/box_mail.py").read_text()
   and 'sender_name="Your box"' not in (ROOT / "core/box_mail.py").read_text())

print("\ntest_a_shift_reads_as_plain_words")
said = ("I checked the box's [October 5 report](https://glow-medspa.ownbox.app/app/review/2026-10-05): "
        "**yesterday's visitor counts are missing for both sites**. It provides weekly human visits only.")
got = runner.plain_summary(said)
ok("a Markdown link reads as its words with the address after",
   "October 5 report (https://glow-medspa.ownbox.app/app/review/2026-10-05)" in got and "](" not in got, got)
ok("emphasis marks are gone, the words stay", "**" not in got and "yesterday's visitor counts are missing" in got, got)
ok("a short account is unchanged otherwise", not got.endswith("…"), got)
long = "September 28's report is missing its numbers " * 20
cut = runner.plain_summary(long)
ok("a long account ends at a whole word and says it was cut",
   len(cut) <= runner.SUMMARY_MAX and cut.endswith("…") and long.startswith(cut[:-1])
   and long[len(cut) - 1] == " ", (len(cut), cut[-30:]))
ok("the runner keeps the plain words in the receipt, not the coworker's raw text",
   "said = plain_summary(" in (ROOT / "core/coworkers/runner.py").read_text())

print("\ntest_the_suite_runs_in_ci")
if (ROOT / ".github").is_dir():                         # a box has no repository
    ok("test_the_shift_email_says_your_ownbox is in the workflow's suite list",
       "test_the_shift_email_says_your_ownbox \\" in (ROOT / ".github/workflows/tests.yml").read_text())

print("\nALL SHIFT-EMAIL CHECKS PASS" if not _failed else f"\n{_failed} SHIFT-EMAIL CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
