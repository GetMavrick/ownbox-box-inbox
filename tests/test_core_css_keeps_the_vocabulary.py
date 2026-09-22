"""Core's stylesheet is SHIPPED TEXT, and it keeps the product's vocabulary.

WHY THIS FILE EXISTS. `core/dash/home.py` builds one `CSS` string and `chrome()` inlines it into
every page the box serves. That makes its COMMENTS shipped text: a word written in a CSS comment
reaches a buyer exactly as a word in a heading does. Nothing about editing a stylesheet makes that
obvious, and in one session on 2026-09-22 it went wrong three times:

  · a comment naming `/settings/ai` put that path into the HTML served to a MEMBER, who is
    refused at it — caught by `test_the_box_settings_are_the_boxs_own`
  · a comment quoting the halt button's label made a box that had not started appear to offer a
    stop button — caught by `test_the_box_shows_a_buyer_the_way_in`
  · a comment quoting the owner on mobile layout shipped the reserved noun onto every page,
    including `/dashboard`, which has nothing to do with the mobile app — caught by the owner

Two of those were caught by guards written for something else, which is luck rather than cover.
This one is aimed at the channel itself.

THE RULING IT ENFORCES. Owner, 2026-09-22, relayed by OSDev4 in #1426: *"It's a mobile app with
notifications"* and *"don't use any terms that will collide with a voice/phone product"*. The
receptionist machine takes `/voice` and will answer real calls; once it ships, a box that says
"your phone" for the thing you install and sells a product that literally answers phones is one
word doing two jobs in one menu. So those nouns are reserved for the product that earns them, and
the thing a buyer installs on a device is the **mobile app**.

WHAT THIS DOES *NOT* CLAIM. It reads core's stylesheet, not the whole page: the step's own title
and route are `box_secrets`' to name and #1426 renames them, and asserting them here would be a
second copy of somebody else's test that goes red on their schedule rather than on a real defect.
And it holds only the unambiguous collisions. "line" is a colour token in this very file and
"call" is ordinary English about a function; a guard that fired on those would be deleted within
a week, and a deleted guard protects nothing.

Run: python tests/test_core_css_keeps_the_vocabulary.py
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AIOS_HERMETIC_TEST", "1")
os.environ.setdefault("DASH_TOKEN", "test-dash-pw")

from core.dash.home import CSS                                           # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


# The nouns the receptionist machine gets to keep. `iPhone`/`Android` are device names in install
# steps and are allowed everywhere — they are what a person must be told and cannot be said
# another way — so the pattern refuses a bare "phone" without swallowing "iPhone".
RESERVED = (r"(?<!i)phone", r"\bphones\b", r"\bring\b", r"\bringing\b", r"\brung\b",
            r"\bdial\b", r"\bdialling\b", r"\bvoice\b")


def hits(pattern: str) -> list:
    return [" ".join(CSS[max(0, m.start() - 60):m.end() + 40].split())
            for m in re.finditer(pattern, CSS, re.IGNORECASE)]


print("\ntest_the_served_stylesheet_reserves_the_receptionists_nouns")
for pat in RESERVED:
    found = hits(pat)
    ok(f"no {pat!r} anywhere in the CSS the box serves", not found, str(found[:2]))


print("\ntest_the_thing_a_buyer_installs_has_a_name_and_it_is_used")
# NOT MERELY THE ABSENCE OF A WORD. A stylesheet that simply stopped mentioning mobile at all
# would pass the block above and teach the next editor nothing, which is how a vocabulary ruling
# decays into a lint rule nobody understands.
ok("the CSS talks about mobile in the words we settled on",
   re.search(r"\bmobile\b", CSS, re.IGNORECASE) is not None)


print("\ntest_no_route_and_no_control_label_is_quoted_in_it")
# THE OTHER TWO THIRDS OF THE SAME LESSON, held here so they are measured on purpose rather than
# by a guard that happens to overlap.
paths = sorted(set(re.findall(r"/(?:settings|dash|inbox|voice)[a-z/_-]*", CSS)))
ok("the stylesheet names no route of any kind", not paths, str(paths))


print("\ntest_the_guard_can_actually_fail")
# A GUARD NOBODY HAS SEEN GO RED IS A GUESS. This one has been wrong before: an earlier cut of the
# pattern used a bare `phone` and would have failed on the word `iPhone` inside an install step.
_probe = "/* a phone, a ring, /settings/ai */"
ok("a reserved noun in a comment would be caught",
   any(re.search(p, _probe, re.IGNORECASE) for p in RESERVED))
ok("...and a route in a comment would be caught",
   bool(re.findall(r"/(?:settings|dash|inbox|voice)[a-z/_-]*", _probe)))
ok("...while a device name in an install step is left alone",
   not any(re.search(p, "Open it on your iPhone", re.IGNORECASE) for p in RESERVED))


print()
if _failed:
    print(f"{_failed} FAILED")
    sys.exit(1)
print("all good")
