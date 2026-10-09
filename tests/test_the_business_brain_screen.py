"""The Business Brain's screen: the business's files on its own Ownbox, drawn Drive's way (owner, 2026-10-09).

Owner, 2026-10-08: "a basic files manager immediately ... listed as Business Brain in the menu at the top"; 10-09: "it's
gotta borrow material design elements from top companies like Google", "Mobile first ... material three and Google
Drive as design reference." The rules are core/business_brain.py's (#2071, tests/test_business_brain.py); this holds
the screen on core/dash/brain.py.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * the menu's Shared Brain row stops opening the screen, or someone signed out can open it;
  * a view stops drawing on the server (a folder, a search, Recently deleted), or folders stop coming first;
  * a row loses its kind's icon, its size and date, its link, or its ⋮;
  * a folder that is gone breaks the page instead of saying so;
  * Add files stops taking several files, or stops saying how big a file can be;
  * Delete stops asking first, or stops saying the file waits 30 days;
  * Move stops offering every folder by its path;
  * the script stops being valid JavaScript.

No network, no model.

Run: python tests/test_the_business_brain_screen.py
"""
from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "brain-screen.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "dash-pw"

from core import business_brain as bb  # noqa: E402
from core import dash, state  # noqa: E402

state.init_db()
from core.dash import brain  # noqa: E402
from core.dispatch import app  # noqa: E402

FAILS: list[str] = []


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:400]}"))
    if not cond:
        FAILS.append(label)


def rows(page: str) -> list[str]:
    return [html.unescape(n) for n in re.findall(r'<span class="bb-name">([^<]+)</span>', page)]


o = app.test_client()
OWNER = state.owner_user()["id"]
o.set_cookie(dash.COOKIE, dash.new_session(OWNER))

print("test_the_menu_opens_it_and_a_stranger_cannot")
dash_page = o.get("/dashboard").get_data(as_text=True)
ok("the menu's Shared Brain row opens /brain", re.search(r'<a href="/brain"[^>]*>(?:(?!</a>).)*Shared Brain', dash_page,
                                                           re.S) is not None)
out = app.test_client().get(brain.DOOR)
ok("someone signed out is sent to sign in, and shown no files", out.status_code in (302, 303, 401, 403)
   and "bb-list" not in out.get_data(as_text=True), out.status_code)
member = app.test_client()
member.set_cookie(dash.COOKIE, dash.new_session(state.add_user("sam@glowmedspa.example", name="Sam",
                                                               role="member")["id"]))
ok("a member of the team opens it too (the business's files are the team's)", member.get(brain.DOOR).status_code == 200)

print("\ntest_an_empty_brain_says_what_to_do")
page = o.get(brain.DOOR).get_data(as_text=True)
ok("nothing yet: the empty state, the search bar with 16px text, and the New button",
   "Nothing here yet" in page and "Tap + to add files or a folder." in page and 'class="bb-search"' in page and "font-size:16px" in page
   and 'class="bb-fab" data-new' in page and 'aria-label="New: add files or a folder"' in page)
ok("...and Add files takes several at once and says how big each may be",
   '<input type="file" id="bb-files" class="bb-file-in" multiple' in page and f"Up to {bb.FILE_MAX >> 20} MB each" in page)

print("\ntest_the_list_reads_like_drive")
contracts = bb.make_folder("", "Contracts", by=OWNER)
bb.make_folder("", "Marketing", by=OWNER)
bb.save("", "Price list 2026.pdf", b"%PDF-1.4 price list", by=OWNER)
bb.save("", "Botox consent form.docx", b"PK" + b"\0" * 2000, by=OWNER)
bb.save(contracts["id"], "Supplier agreement.pdf", b"%PDF-1.4 agreement", by=OWNER)
bb.save("", "Welcome script.txt", b"Your first consultation is free, and we offer HydraFacials.", by=OWNER)
page = o.get(brain.DOOR).get_data(as_text=True)
names = rows(page)
ok("folders first, then files, by name", names[:5] == ["Contracts", "Marketing", "Botox consent form.docx",
                                                        "Price list 2026.pdf", "Welcome script.txt"], names)
pdf = re.search(r'<li class="bb-row"(?:(?!</li>).)*Price list 2026\.pdf(?:(?!</li>).)*</li>', page, re.S).group(0)
ok("a file's row: its kind's icon, its name, what it is and its size, a link to the file, and its ⋮",
   'class="bb-ic t-pdf"' in pdf and "PDF · 19 bytes" in pdf and 'href="/brain/file/' in pdf and " data-open" in pdf
   and 'aria-label="More for Price list 2026.pdf"' in pdf, pdf[:400])
folder = re.search(r'<li class="bb-row"(?:(?!</li>).)*>Contracts<(?:(?!</li>).)*</li>', page, re.S).group(0)
ok("a folder's row opens the folder and says how much is in it",
   f'href="/brain?folder={contracts["id"]}"' in folder and "1 item" in folder and "t-folder" in folder, folder[:400])
ok("...and the page says how much room is used", "4 files · " in page and "of 5 GB used" in page)

print("\ntest_a_folder_and_the_way_back")
page = o.get(f"{brain.DOOR}?folder={contracts['id']}").get_data(as_text=True)
ok("inside a folder: its files, and the path back to the top",
   rows(page)[:1] == ["Supplier agreement.pdf"] and '<nav class="bb-path"' in page
   and f'<a href="{brain.DOOR}">Shared Brain</a>' in page and '<span aria-current="page">Contracts</span>' in page)
gone = o.get(f"{brain.DOOR}?folder=not-a-folder")
ok("a folder that is gone says so and shows the top, never a broken page",
   gone.status_code == 200 and "That folder isn't there any more." in html.unescape(gone.get_data(as_text=True))
   and "Contracts" in rows(gone.get_data(as_text=True)))

print("\ntest_search_finds_names_and_words")
page = o.get(f"{brain.DOOR}?q=agreement").get_data(as_text=True)
ok("by name, wherever it is, saying which folder", rows(page)[:1] == ["Supplier agreement.pdf"]
   and "In Contracts · " in page and "1 result for" in page, rows(page))
ok("...and only the folder: never the file's own name again",
   re.search(r'<span class="bb-sub">([^<]*)</span>', page).group(1).startswith("In Contracts · "))
bb.save(bb.make_folder(contracts["id"], "2026", by=OWNER)["id"], "Sarah Lee consent form.pdf", b"%PDF-1.4", by=OWNER)
page = o.get(f"{brain.DOOR}?q=sarah").get_data(as_text=True)
_sub = re.search(r'<span class="bb-sub">([^<]*)</span>', page).group(1)
ok("...however deep it is", _sub.startswith("In Contracts/2026 · ") and "Sarah" not in _sub, _sub)
page = o.get(f"{brain.DOOR}?q=price").get_data(as_text=True)
ok("...and a file at the top says what it is, not where", "PDF · 19 bytes" in page and "In " not in
   re.search(r'<span class="bb-sub">([^<]*)</span>', page).group(1), rows(page))
page = o.get(f"{brain.DOOR}?q=hydrafacials").get_data(as_text=True)
ok("by the words inside a text document", rows(page)[:1] == ["Welcome script.txt"], rows(page))
ok("...and nothing found says so", "Nothing found" in o.get(f"{brain.DOOR}?q=zzqx").get_data(as_text=True))

print("\ntest_the_sheets_ask_and_say")
ok("a file's sheet: Send, Open, Download, Rename, Move, then Delete apart",
   all(f'data-act="{a}"' in page for a in ("send", "open", "download", "rename", "move", "delete"))
   and page.index('data-act="move"') < page.index('class="bb-div"') < page.index('data-act="delete"'))
ok("Delete asks first, and says it waits in Recently deleted for 30 days",
   'id="bb-del"' in page and f"It stays in Recently deleted for {bb.TRASH_DAYS} days" in page)
ok("Move offers the top and every folder by its path",
   f'<option value="">Shared Brain (the top)</option>' in page
   and f'<option value="{contracts["id"]}">Contracts</option>' in page)

print("\ntest_send_readies_only_a_small_file")
# OSDev1's review: opening a sheet to rename a 100 MB video must not spend 100 MB of the person's data.
ok("Send readies a file ahead of the tap only up to 25 MB, and the script checks the size before it fetches",
   brain.SHARE_AHEAD_MAX == 25 << 20 and f'data-share-max="{25 << 20}"' in page
   and "navigator.canShare&&window.File&&it.size<=SHAREMAX" in brain._JS)

print("\ntest_recently_deleted")
bb.delete(bb.resolve("Botox consent form.docx")["id"], by=OWNER)
page = o.get(f"{brain.DOOR}?view=trash").get_data(as_text=True)
ok("what was deleted, with the days it has left and Restore, and no New button",
   rows(page)[:1] == ["Botox consent form.docx"] and f"{bb.TRASH_DAYS} days left" in page and "data-restore" in page
   and 'class="bb-fab"' not in page, rows(page))

print("\ntest_the_words_on_a_row")
ok("sizes count in 1,024s like the brain's limits", brain.size_words(100 << 20) == "100 MB"
   and brain.size_words(5 << 30) == "5 GB" and brain.size_words(1536) == "1.5 KB" and brain.size_words(1) == "1 byte")
ok("each kind gets Drive's word", [brain.kind_of(n)[1] for n in ("a.PDF", "b.xlsx", "c.key", "d.heic", "e.mov", "f.bin")]
   == ["PDF", "Spreadsheet", "Presentation", "Picture", "Video", "File"])
now = datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc)
ok("today shows the time, this year the day, before that the year",
   re.match(r"\d{1,2}:\d\d [AP]M$", brain.when_words((now - timedelta(minutes=5)).isoformat(), now) or "")
   and brain.when_words("2026-03-04T12:00:00+00:00", now) == "Mar 4"
   and brain.when_words("2025-03-04T12:00:00+00:00", now) == "Mar 4, 2025")

print("\ntest_the_script_parses")
_node = shutil.which("node")
if _node:
    f = Path(_T) / "brain.js"
    f.write_text(brain._JS)
    r = subprocess.run([_node, "--check", str(f)], capture_output=True, text=True)
    ok("the screen's script is valid JavaScript", r.returncode == 0, r.stderr[-300:])
else:
    print("  --   no node on this machine, so the syntax check is skipped here")

print("\nALL BUSINESS BRAIN SCREEN CHECKS PASS" if not FAILS else f"\n{len(FAILS)} BUSINESS BRAIN SCREEN CHECK(S) FAILED")
sys.exit(1 if FAILS else 0)
