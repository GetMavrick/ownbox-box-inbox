"""Add a Machine opens a page ON the box, and it never 404s.

Owner, 2026-09-23: *"Please scope out an add machine page: /add-machine ... explain that they can
build their own machine with their SSH access ... And then on that page you would provide a link to
our website where we are going to have machines available for purchase."* OSDev1's done-when for the
build: clicking Add a Machine on a delivered box opens a page on the box that tells the owner how to
start one under my/machines/, and it never 404s on a box with none.

Run: python tests/test_the_add_machine_page.py
"""
import os
import pathlib
import sys
import tempfile
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_T = pathlib.Path(tempfile.mkdtemp())
MINE = _T / "machines"
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(_T / "addm.db")
os.environ["AIOS_MY_MACHINES"] = str(MINE)
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

# One machine that starts and one that does not, so the page has both states to show.
for name, init in (("job-tracker", "X = 1\n"), ("quote-builder", "raise RuntimeError('typo')\n")):
    d = MINE / name
    d.mkdir(parents=True)
    (d / "machine.yaml").write_text(f"name: {name}\nversion: 0.1.0\nrequires_foundation: '1.0'\n")
    (d / "__init__.py").write_text(textwrap.dedent(init))

from core import state                                                # noqa: E402

state.init_db()

from core import custom_machines, dash, shell                         # noqa: E402
from core.dispatch import app                                         # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def client(user_id):
    c = app.test_client()
    c.set_cookie(dash.COOKIE, dash.new_session(user_id))
    return c


owner = client(state.owner_user()["id"])
member = client(state.add_user("sam@example.com", name="Sam")["id"])

print("\ntest_the_row_stays_on_the_box")
sec = next((s for s in shell.sections() if s.key == "add_machine"), None)
ok("the Add a Machine row exists", sec is not None)
ok("...and points at a page on this box, not a website", bool(sec) and sec.href == "/add-machine",
   sec.href if sec else "")
ok("...so it carries no leave-the-box arrow", bool(sec) and not shell.is_off_box(sec.href))

print("\ntest_the_page_answers_everyone_signed_in")
r = owner.get("/add-machine")
html = r.get_data(as_text=True)
ok("the owner gets the page", r.status_code == 200, str(r.status_code))
m = member.get("/add-machine")
mhtml = m.get_data(as_text=True)
ok("a member gets the page too", m.status_code == 200, str(m.status_code))
ok("nobody signed in is sent to sign in, not shown the page",
   app.test_client().get("/add-machine").status_code in (302, 303, 401))

print("\ntest_the_owner_is_shown_how_to_build_one")
ok("it says where to build: my/machines/", "my/machines/" in html)
ok("...names both files a machine needs", "machine.yaml" in html and "__init__.py" in html)
ok("...points at the guide that ships on the box", "BUILD_A_MACHINE.md" in html)
ok("...and at Server access, the way onto the server", 'href="/settings/access"' in html)
ok("the machine that started reads Running", "job-tracker" in html and "Running" in html)
ok("the one that did not says why", "quote-builder" in html and "typo" in html)

print("\ntest_a_member_is_not_shown_what_only_the_owner_can_do")
ok("no build steps for a member", "my/machines/" not in mhtml and "/settings/access" not in mhtml)
ok("...but told who can", "owner" in mhtml)

print("\ntest_no_dead_link_to_a_shop_that_does_not_exist_yet")
ok("the page does not link to ownbox.io/machines", "ownbox.io/machines" not in html)
ok("...and still offers a way to get one from us", "mailto:help@ownbox.io" in html)

print("\ntest_a_box_with_no_machines_of_its_own_never_404s")
custom_machines._STATUS.clear()
empty = owner.get("/add-machine")
ok("the page still answers", empty.status_code == 200, str(empty.status_code))
ok("...and says there are none yet", "None yet" in empty.get_data(as_text=True))

print("\n" + ("all good" if not _failed else f"{_failed} FAILED"))
sys.exit(1 if _failed else 0)
