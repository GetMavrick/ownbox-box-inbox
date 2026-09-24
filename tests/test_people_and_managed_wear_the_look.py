"""People and Managed are buyer screens, so they wear the box's look and sit where an owner looks.

OSDev1's assignment, 2026-09-24: the screens still on the old operator shell, Managed and Team, move
to the design language, with the done-when of one ink pill per screen at 390px and desktop. And
People had no way in at all: nothing on a box linked to it.

This suite holds, as the owner:
  1. People renders in the box's shell, at /settings/people and at its older /dash/people;
  2. it is a row of System Settings, lit when you stand on it and named in the breadcrumb;
  3. it has one ink pill (Create invite link); a person's own actions are outlines, and Remove is
     the danger one;
  4. a new invite link is shown once, in a notice, as an address a thumb can select;
  5. Managed renders in the box's shell, and when it was bought its cancel door is the one ink
     pill on the screen;
  6. a member who reaches either gets the box's shell and a sentence, not the operator console.

Run: python tests/test_people_and_managed_wear_the_look.py
"""
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "people.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
os.environ["AIOS_MY_MACHINES"] = os.path.join(_T, "none")
os.environ.pop("ANTHROPIC_API_KEY", None)

from core import state                                                # noqa: E402

state.init_db()

from core import claim as _claim, dash                                # noqa: E402
from core.config import settings                                      # noqa: E402
from core.dash import look                                            # noqa: E402
from core.dispatch import app                                         # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


_QUIET = {"ghost", "danger", "ui-ghost", "ui-danger"}


def ink_pills(page: str) -> list[str]:
    main = page.split("</nav>", 1)[-1]
    out = []
    for m in re.finditer(r"<(button|a)\b([^>]*)>(.*?)</\1>", main, re.S):
        tag, attrs, text = m.groups()
        cls = set((re.search(r'class="([^"]*)"', attrs) or [None, ""])[1].split())
        if tag == "a" and not cls & {"btn", "ui-btn"}:
            continue
        if not cls & _QUIET:
            out.append(re.sub(r"<[^>]+>|\s+", " ", text).strip())
    return out


def in_the_box_shell(page: str) -> bool:
    return look.head_tags() in page and '<nav class="rail"' in page and "<main" in page


owner = app.test_client()
owner.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
sam = state.add_user("sam@example.com", role="member")
gone = state.add_user("lee@example.com", role="member")
state.set_user_active(gone["id"], False)

print("\ntest_people_wears_the_look_and_has_a_door")
for href in ("/settings/people", "/dash/people"):
    r = owner.get(href)
    page = r.get_data(as_text=True)
    ok(f"{href} answers the owner", r.status_code == 200, str(r.status_code))
    ok(f"{href} is in the box's shell, with its stylesheet", in_the_box_shell(page))
page = owner.get("/settings/people").get_data(as_text=True)
nav = page.split('<nav class="rail"', 1)[-1].split("</nav>", 1)[0]
ok("People is a row of System Settings, lit while you stand on it",
   re.findall(r'<a href="([^"]*)"[^>]*aria-current="page"', nav) == ["/settings/people"])
ok("...and the breadcrumb says where you are", "System Settings / <b>People</b>" in page)
ok("one ink pill, and it is Create invite link", ink_pills(page) == ["Create invite link"],
   str(ink_pills(page)))
ok("a person's own actions are outlines, and Remove is the danger one",
   '<button class="ghost" type="submit">New link</button>' in page
   and '<button class="danger" type="submit">Remove</button>' in page
   and '<button class="ghost" type="submit">Restore</button>' in page)
ok("the invite field has a label a screen reader can find",
   '<label for="invite-email">' in page and 'id="invite-email"' in page)
ok("no operator-console classes are left on it",
   not re.search(r'class="(val|lbl mb|btn-primary|btn)"', page.split("</nav>", 1)[-1]))

print("\ntest_a_new_invite_link_is_shown_once_in_a_notice")
r = owner.post("/dash/people/invite", data={"email": "new@example.com"})
page = r.get_data(as_text=True)
ok("the invite is made", r.status_code == 200, str(r.status_code))
ok("the link sits in a notice card at the top, as a selectable address",
   re.search(r'<div class="card notice"><h2>Invite link for new@example\.com</h2>.*?'
             r'<p class="addr">http[^<]*/join\?t=', page, re.S) is not None)
ok("...still with exactly one ink pill on the screen", len(ink_pills(page)) == 1, str(ink_pills(page)))
r = owner.post("/dash/people/invite", data={"email": ""})
ok("a refusal is a notice in plain words, not a console line",
   r.status_code == 400 and 'class="card notice"><p>Enter the email address' in r.get_data(as_text=True))

print("\ntest_managed_wears_the_look")
page = owner.get("/dash/managed").get_data(as_text=True)
ok("Managed is in the box's shell", in_the_box_shell(page))
ok("with nothing bought it says so and offers nothing to press",
   "does not have the Managed service" in page and ink_pills(page) == [])
_real_until, _real_portal = _claim.provisioned_managed_until, settings.managed_portal_url
try:
    _claim.provisioned_managed_until = lambda: "2026-12-24"
    object.__setattr__(settings, "managed_portal_url", "https://billing.stripe.com/p/login/example")
    page = owner.get("/dash/managed").get_data(as_text=True)
    ok("bought, it names the date", "24 December 2026" in page)
    ok("...and the cancel door is the one ink pill", ink_pills(page) == ["Manage or cancel Managed"],
       str(ink_pills(page)))
finally:
    _claim.provisioned_managed_until = _real_until
    object.__setattr__(settings, "managed_portal_url", _real_portal)

print("\ntest_a_member_meets_the_box_not_the_console")
member = app.test_client()
member.set_cookie(dash.COOKIE, dash.new_session(sam["id"]))
for href, said in (("/settings/people", "Only the box owner manages who can sign in."),
                   ("/dash/managed", "Only the box owner can see or change the Managed service.")):
    r = member.get(href)
    page = r.get_data(as_text=True)
    ok(f"{href}: a member is refused in the box's shell, with a sentence",
       r.status_code == 403 and in_the_box_shell(page) and said in page, str(r.status_code))
r = member.post("/dash/stop")
ok("a member who presses Stop is refused in the box's shell",
   r.status_code == 403 and in_the_box_shell(r.get_data(as_text=True)), str(r.status_code))

print("\n" + ("all good" if not _failed else f"{_failed} FAILED"))
sys.exit(1 if _failed else 0)
