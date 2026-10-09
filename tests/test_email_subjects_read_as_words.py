"""An email's subject, and the names in its details, read as words: never as the =?UTF-8?Q?...?= they travel in.

Owner, 2026-10-09, of a LinkedIn job alert whose subject read "=?UTF-8?Q?=E2=80=9Cdirector_or_manager_or_sen?=
=?UTF-8?Q?ior_AI=E2=80=A6=E2=80=9D:_CyberCoders_-_AI_S?= ...": "what?". Mail carries a header with any character
outside plain ASCII as RFC 2047 encoded-words, and the sweep keeps headers exactly as they came, on purpose
(email_channel._kept_headers). The subject reached the screen that way. render.readable_header turns them into words for
reading; the stored headers stay as they came.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * an encoded subject reaches the screen (app_api) or the details panel (app._details) undecoded;
  * a folded, multi-part subject keeps its seams, or loses the space between real words;
  * a name in From loses its address, or a plain subject changes at all;
  * a header that won't decode raises instead of showing as it came;
  * the stored headers are rewritten.

No network, no model.

Run: python tests/test_email_subjects_read_as_words.py
"""
from __future__ import annotations

import html
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "subjects.db")

from core import state  # noqa: E402

state.init_db()
try:
    import marketing.customer_voice.app as inbox_app  # noqa: E402
except ImportError:
    print("  --   no customer_voice on this box")
    sys.exit(0)
from marketing.customer_voice import app_api  # noqa: E402
from marketing.customer_voice.inbox import email_channel  # noqa: E402
from marketing.customer_voice.inbox.render import readable_header  # noqa: E402

FAILS: list[str] = []


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:400]}"))
    if not cond:
        FAILS.append(label)


# THE ONE THE OWNER SAW, as the mail server folds it: three encoded-words on three lines.
LINKEDIN = ("=?UTF-8?Q?=E2=80=9Cdirector_or_manager_or_sen?=\r\n =?UTF-8?Q?ior_AI=E2=80=A6=E2=80=9D:_CyberCoders_-_AI_S?="
            "\r\n =?UTF-8?Q?olutions_Architect_-_6-month_contract!_posted_on_10/8/26?=")
WORDS = "“director or manager or senior AI…”: CyberCoders - AI Solutions Architect - 6-month contract! posted on 10/8/26"

print("test_the_words_come_back")
ok("the folded LinkedIn subject reads as its words, one line, no seams", readable_header(LINKEDIN) == WORDS,
   readable_header(LINKEDIN))
ok("base64 words too", readable_header("=?UTF-8?B?Q2Fmw6kgbWVudSDimJU=?=") == "Café menu ☕")
ok("another charset, beside plain words", readable_header("Re: =?ISO-8859-1?Q?R=E9servation?= confirmed")
   == "Re: Réservation confirmed")
ok("a name in From reads as a name, and keeps its address",
   readable_header("=?UTF-8?Q?Jos=C3=A9_Garc=C3=ADa?= <jose@glowmedspa.example>")
   == "José García <jose@glowmedspa.example>")
ok("a plain subject comes back exactly", readable_header("Your consultation on Thursday at 4")
   == "Your consultation on Thursday at 4")
ok("...and one folded over two lines comes back on one", readable_header("Your consultation\r\n on Thursday at 4")
   == "Your consultation on Thursday at 4")
ok("one that won't decode is shown as it came, never an error",
   readable_header("=?bogus-charset?Q?abc?=") == "=?bogus-charset?Q?abc?=" and readable_header(None) == "")

print("\ntest_the_screen_and_the_details_read_it")
import email as email_mod  # noqa: E402

msg = email_mod.message_from_bytes(("Subject: " + LINKEDIN + "\r\nFrom: =?UTF-8?Q?LinkedIn_Job_Alerts?= "
                                     "<jobalerts-noreply@linkedin.example>\r\nTo: owner@glowmedspa.example\r\n\r\nhi")
                                    .encode())
kept = email_channel._kept_headers(msg)
ok("the sweep still keeps the header exactly as it came", kept["Subject"].startswith("=?UTF-8?Q?"), kept["Subject"])
ok("the new Inbox screens get the words", app_api._subject(kept) == WORDS, app_api._subject(kept))
details = html.unescape(inbox_app._details(kept))
ok("...and so does the details panel, its subject and its From", WORDS in details
   and "LinkedIn Job Alerts <jobalerts-noreply@linkedin.example>" in details and "=?UTF-8?" not in details,
   details[:400])

print("\nALL SUBJECT CHECKS PASS" if not FAILS else f"\n{len(FAILS)} SUBJECT CHECK(S) FAILED")
sys.exit(1 if FAILS else 0)
