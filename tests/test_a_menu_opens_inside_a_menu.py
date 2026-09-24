"""The Unified Inbox's Settings is a menu of its own, one row per setting's one home.

Owner, 2026-09-24 (relayed by OSDev1): "The unified inbox settings should have sub menu choices."
OSDev1: give the inbox's Settings the same two-level rail System Settings has, one row per setting
home, so the long single page goes away. docs/SCOPE_ONE_PLACE_PER_SETTING.md.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  · a nested section shows up in the top-level menu beside the machines;
  · its back arrow skips the machine and goes all the way home;
  · a setting whose home is not under /inbox/settings (the mailbox, at /inbox/mailbox) lights the
    wrong menu, or the wrong row, or the Settings tab goes dark on it;
  · a section nests under another machine's section, or nests two deep.

Run: python tests/test_a_menu_opens_inside_a_menu.py
"""
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "menu.db")
os.environ.setdefault("DASH_TOKEN", "pw")

from core import state  # noqa: E402

state.init_db()

from core import dash, shell  # noqa: E402
from core.dispatch import app  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


print("\ntest_the_core_rules")
try:
    shell.register_section("tparent", order=900, machine="tmachine", title="Parent", href="/tp/",
                           items=[{"key": "aa", "label": "A", "href": "/tp/a"}])
    shell.register_section("tchild", order=900, machine="tmachine", title="Child",
                           href="/tp/settings", parent="tparent",
                           items=[{"key": "home", "label": "Home", "href": "/tp/settings"},
                                  {"key": "far", "label": "Far away", "href": "/tp/elsewhere"}])
    top = shell.rail("/").items
    ok("a nested section is not in the top-level menu", not any(i.key == "tchild" for i in top))
    ok("...while its parent is", any(i.key == "tparent" for i in top))
    r = shell.rail("/tp/settings")
    ok("standing on it shows its own rows", r.level == 2 and r.title == "Child", str(r))
    ok("...and the back arrow goes up one, to the parent", r.back == "/tp/" and r.back_label == "Parent",
       f"{r.back} {r.back_label}")
    far = shell.rail("/tp/elsewhere")
    ok("a row whose page lives outside the section's path still lights this menu",
       far.title == "Child", far.title)
    ok("...and that row, not another", (shell.current_item("/tp/elsewhere") or shell.Item("", "", "")).key == "far")
    for bad, why in ((dict(parent="nope"), "an unregistered parent"),
                     (dict(parent="tchild"), "a parent that is itself nested"),
                     (dict(parent="inbox"), "another machine's section")):
        raised = False
        try:
            shell.register_section("tbad", order=901, machine="tmachine", title="Bad", href="/tb/",
                                   **bad)
        except ValueError:
            raised = True
        ok(f"refused: {why}", raised)
finally:
    for k in ("tparent", "tchild", "tbad"):
        shell._SECTIONS.pop(k, None)

print("\ntest_the_inbox_settings_menu")
_inbox = ROOT / "marketing" / "customer_voice" / "app.py"
if _inbox.is_file():
    c = app.test_client()
    c.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
    rows = {"/inbox/settings": "Overview", "/inbox/mailbox": "Mailbox",
            "/inbox/connect": "Social accounts", "/inbox/drafts": "AI and drafts",
            "/inbox/install": "Home screen"}
    r = shell.rail("/inbox/mailbox")
    ok("the inbox's Settings is a menu with a row per setting home",
       r.level == 2 and r.title == "Settings" and [i.label for i in r.items] == list(rows.values()),
       str([i.label for i in r.items]))
    ok("...whose back arrow returns to the Unified Inbox", r.back == "/inbox/" and
       r.back_label == "Unified Inbox", f"{r.back} {r.back_label}")
    ok("...and it is not a top-level entry", not any(i.key == "inbox_settings" for i in shell.rail("/").items))
    for path, label in rows.items():
        html_ = c.get(path).get_data(as_text=True)
        ok(f"{path}: its own row is the current one ({label})",
           (shell.current_item(path) or shell.Item("", "", "")).label == label)
        ok(f"{path}: the Settings tab is lit",
           'class="tab on" href="/inbox/settings"' in html_)
else:
    print("  --   no inbox machine ships on this box, so there is no inbox Settings menu to check")

print("\n— and this file cannot silently fall out of CI —")
_wf = ROOT / ".github/workflows/tests.yml"
if _wf.is_file():
    ok("test_a_menu_opens_inside_a_menu is in the workflow's suite list",
       "test_a_menu_opens_inside_a_menu" in _wf.read_text())

print("\nALL OK" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
