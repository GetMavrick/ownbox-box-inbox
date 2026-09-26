"""The AEO menu shows only what works today, and Articles is as dense as the inbox.

OSDev1's assignment, 2026-09-25, on the owner's delegation (*"a basic working system by today where
I can start using it to promote Ownbox content"*):
  (a) the AEO menu has only rows that work today. No empty Today, Search or AI rows;
  (b) Articles is the Topics screen made inbox-dense: title, two lines, one detail line, one ink
      pill (Add a topic), and Write and publish now kept.
Data sources and its nested menu are OSDev6's (#1572) and are checked by test_aeo_sources.
And the owner, 2026-09-25: the list needs *"twice as much information on the page… Similar to the
inbox."*

Run: python tests/test_aeo_menu_shows_what_works.py
"""
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "aeomenu.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
os.environ["AIOS_MY_MACHINES"] = os.path.join(_T, "none")
os.environ.pop("ANTHROPIC_API_KEY", None)

from core import state                                                # noqa: E402

state.init_db()

# A BOX SHIPS ONLY ITS OWN MACHINES: a box without the AEO machine has nothing here to check.
try:
    from marketing.aeo_machine import app as aeo_app                  # noqa: E402
    from marketing.aeo_machine import plan                            # noqa: E402
except ImportError:
    print("  --   the AEO machine is not on this box; nothing to check here")
    print("\nall good")
    raise SystemExit(0)

from core import dash, shell                                          # noqa: E402
from core.dispatch import app                                         # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def ink_pills(page: str) -> list[str]:
    main = page.split("</nav>", 1)[-1]
    out = []
    for m in re.finditer(r"<button\b([^>]*)>(.*?)</button>", main, re.S):
        cls = set((re.search(r'class="([^"]*)"', m.group(1)) or [None, ""])[1].split())
        if not cls & {"ghost", "danger", "ui-ghost", "ui-danger"}:
            out.append(re.sub(r"\s+", " ", m.group(2)).strip())
    return out


owner = app.test_client()
owner.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))

print("\ntest_the_menu_has_only_rows_that_work_today")
aeo = next((s for s in shell.sections() if s.key == "aeo"), None)
labels = [i.label for i in aeo.items] if aeo else []
ok("AEO opens on Articles and ends on Settings",
   labels[:1] == ["Articles"] and labels[-1:] == ["Settings"], str(labels))
ok("...with no row for a page that has nothing to show yet",
   not {"Today", "Search", "AI answers", "Questions", "Analytics"} & set(labels), str(labels))
for item in (aeo.items if aeo else ()):
    r = owner.get(item.href)
    ok(f"{item.label} ({item.href}) opens a page", r.status_code == 200, str(r.status_code))

print("\ntest_articles_is_as_dense_as_the_inbox")
for i, (t, q) in enumerate((("Microneedling aftercare", "What should I do after microneedling?"),
                            ("Hydrafacial vs chemical peel", "Is a Hydrafacial better than a peel?"),
                            ("First visit to a med spa", ""),
                            ("Morpheus8 recovery time", "How long does Morpheus8 take to heal?"),
                            ("How often to get Botox", "How often should I get Botox?"),
                            ("Lip filler: what to expect", "How long does lip filler last?"))):
    plan.add(t, q)
page = owner.get("/aeo/topics").get_data(as_text=True)
main = page.split("</nav>", 1)[-1]
ok("the page is called Articles", "<h1>Articles</h1>" in page)
rows = re.findall(r'<div class="ar">', main)
ok("every article is a row in one list, not a card of its own",
   len(rows) == 6 and main.count('class="card ar-list"') == 1, f"{len(rows)} rows")
ok("each row has a title and a state word",
   main.count('class="ar-t"') == 6 and main.count('class="ar-w"') == 6)
ok("...and no detail row that only repeats the state word",
   'class="ar-s"' not in main and "Planned." not in main)
ok("a question shows as the row's two lines", 'class="ar-p">What should I do after microneedling?' in main)
ok("one ink pill, and it is Add topic", ink_pills(page) == ["Add topic"], str(ink_pills(page)))
ok("the example topic is a med spa's, never a trade's",
   "Microneedling aftercare" in page and "boiler" not in page.lower())
_real = aeo_app.writer_installed, aeo_app.missing
aeo_app.writer_installed, aeo_app.missing = (lambda: True), (lambda: [])
try:
    page = owner.get("/aeo/topics").get_data(as_text=True)
    ok("Write and publish now is still offered on a planned row, in its detail row",
       page.count("Write and publish now") == 6 and 'class="ar-s"' in page)
    ok("the list comes before the add form, as in the inbox",
       page.index('class="card ar-list"') < page.index('class="ar-add"'))
    ok("...as a quiet action inside the row, not a second ink pill",
       ink_pills(page) == ["Add topic"], str(ink_pills(page)))
finally:
    aeo_app.writer_installed, aeo_app.missing = _real

print("\n" + ("all good" if not _failed else f"{_failed} FAILED"))
sys.exit(1 if _failed else 0)
