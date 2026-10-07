"""The menu reads: the box's own rows, a little space, then the machines added to it.

Owner, 2026-09-24: *"Dashboard should be re-named to: Base Machine. This way people will know which
machine they are working with."* Then: *"Let's rename that to base machine. And then you can put
System Settings right below it. And then almost in another section. We should have the add-on
machines. Which would be unified inbox and then add a machine."* And of that second section: *"very
subtle. Almost just like an extra space."*

So this suite renders the real menu, the way a buyer's box draws it, and holds:
  1. the rows, in order: Base Machine, the Morning Review, Approvals, System Settings, then the add-on machines,
     then Add a Machine — and a member is not shown the Morning Review, which refuses them;
  2. exactly one row opens a new group: the first add-on machine, for the owner and a member alike;
  3. the gap is space and nothing else: no heading, no rule, no extra words;
  4. a machine that registers a low `order` still cannot climb above the box's own rows,
     and Add a Machine stays last, below any machine a box gains later.

Run: python tests/test_the_menu_has_the_box_then_its_machines.py
"""
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "menu.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
os.environ["AIOS_MY_MACHINES"] = os.path.join(_T, "none")
os.environ.pop("ANTHROPIC_API_KEY", None)

from core import state                                                # noqa: E402

state.init_db()

from marketing.customer_voice import app as _machine                  # noqa: E402,F401
from core import dash, shell                                          # noqa: E402
from core.dash import home                                            # noqa: E402
from core.dispatch import app                                         # noqa: E402

state.init_db()
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


c = app.test_client()
c.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))


def menu(path):
    html = c.get(path).get_data(as_text=True)
    nav = html.split('<nav class="rail"', 1)[-1].split("</nav>", 1)[0]
    return html, nav


print("\ntest_the_rows_read_in_the_owners_order")
html, nav = menu("/dashboard")
rows = re.findall(r'<a href="[^"]*"[^>]*>.*?<span class="lbl">([^<]*)</span>', nav, re.S)
# THE ADD-ON MACHINES SIT BETWEEN THE BOX'S OWN ROWS AND ADD A MACHINE, in their registered order.
# THE MORNING REVIEW IS SECOND, RIGHT BELOW BASE MACHINE (owner, 2026-10-05: "move morning review up to the second
# item in the list right below base machine", and "It's part of the base machine, so let's keep it there"). It
# headed the add-on machines from 2026-09-29 until then. APPROVALS FOLLOWS IT (owner, 10-04: "Where is the approvals
# Page? It should be in the dashboard").
ok("Base Machine, the Morning Review, Approvals, System Settings, then the add-on machines, then Add a Machine",
   rows == ["Base Machine", "Morning Review", "Approvals", "System Settings", "AEO Machine", "Inbox Machine",
            "Add a Machine"], str(rows))
ok("...and the Morning Review row opens the review", '<a href="/app/review"' in nav)
_rv = c.get("/app/review")
_rv_nav = _rv.get_data(as_text=True).split('<nav class="rail"', 1)[-1].split("</nav>", 1)[0]
ok("...which marks its own row as the page you are on",
   _rv.status_code == 200
   and re.findall(r'<a href="([^"]*)"[^>]*aria-current="page"', _rv_nav) == ["/app/review"],
   str(_rv.status_code))

# A MEMBER IS SHOWN NO ROW THE PAGE WOULD REFUSE. The review publishes what the box spends and its
# gate sends anyone but the owner to sign in, so a member's menu is shorter, not broken.
_m = app.test_client()
_m.set_cookie(dash.COOKIE, dash.new_session(state.add_user("lena.okafor@acme.co", name="Lena",
                                                           role="member")["id"]))
_m_nav = _m.get("/dashboard").get_data(as_text=True).split('<nav class="rail"', 1)[-1].split("</nav>", 1)[0]
_m_rows = re.findall(r'<a href="[^"]*"[^>]*>.*?<span class="lbl">([^<]*)</span>', _m_nav, re.S)
ok("a member's menu has no Morning Review row", "Morning Review" not in _m_rows
   and "/app/review" not in _m_nav, str(_m_rows))
ok("...and still has the rest, in the same order (no Approvals: approving is the owner's)",
   _m_rows == ["Base Machine", "System Settings", "AEO Machine", "Inbox Machine", "Add a Machine"],
   str(_m_rows))
# THE GAP IS CARRIED, not dropped with the row a member is not shown: it opens above their first machine.
_m_grp = re.findall(r'<a href="[^"]*"[^>]*class="[^"]*\bgrp\b[^"]*"[^>]*>.*?<span class="lbl">([^<]*)</span>',
                    _m_nav, re.S)
ok("...with the group's gap above their first add-on machine", _m_grp == ["AEO Machine"], str(_m_grp))
try:
    shell.register_section("odd_home", order=2, machine="core", title="Odd", href="/odd-home",
                           home=True, owner_only=True)
    ok("an owner-only section cannot also be the home every back arrow lands on", False)
except ValueError:
    ok("an owner-only section cannot also be the home every back arrow lands on", True)
ok("the page itself is titled Base Machine", "<h1>Base Machine</h1>" in html)
ok("...and no row or heading on it still says Dashboard",
   ">Dashboard<" not in html and '"lbl">Dashboard' not in html)
ok("...while its address stays /dashboard, so installed apps still open it",
   '<a href="/dashboard"' in nav)

print("\ntest_the_add_on_machines_are_named_above_them")
# Owner, 2026-10-07: "add a separator in the main base machine menu that says: ADD-ON MACHINES".
grp = re.findall(r'<a href="[^"]*"[^>]*class="[^"]*\bgrp\b[^"]*"[^>]*>.*?<span class="lbl">([^<]*)</span>',
                 nav, re.S)
ok("exactly one row opens a new group, and it is the first add-on machine: the Morning Review is the box's own",
   grp == ["AEO Machine"], str(grp))
css = home.CSS
ok("ADD-ON MACHINES sits once, directly above that row, in the menu's own group label",
   nav.count('<span class="glabel">Add-on machines</span>') == 1
   and re.search(r'<span class="glabel">Add-on machines</span><a href="[^"]*"[^>]*class="[^"]*\bgrp\b[^"]*"[^>]*>'
                 r'.*?<span class="lbl">AEO Machine<', nav, re.S) is not None, nav[:600])
ok("...set in capitals by the label's style, as System Settings' group names are",
   re.search(r"\.nav \.glabel\{[^}]*text-transform:uppercase", css) is not None)
ok("...and still no rule and no heading between the groups",
   "<hr" not in nav and "<h2" not in nav and "<h3" not in nav
   and not re.search(r"\.nav a\.grp\{[^}]*border", css))

print("\ntest_system_settings_is_four_groups_with_plain_names")
# Owner, 2026-09-29, IA decision D4 (docs/SCOPE_APP_IA.md): Your AI · Reaching you · Your team ·
# The server, each opened by the same subtle gap as the main menu's groups, and plain names —
# Email (not Outbound Email). The AI agents you already pay for are Coworkers (Agents), owner,
# 2026-09-30. Move Your Box is not a row since the same day: it is in Server Access's danger zone.
_sn = menu("/settings/people")[1]
# A ROW'S NAME IS THE TEXT BEFORE ANY BADGE INSIDE IT (the plan badge sits in the label, beside the name).
_srows = re.findall(r'<a href="[^"]*"[^>]*>.*?<span class="lbl">([^<]*)', _sn, re.S)
# Data Sources joined Your AI on 2026-10-01: the page the owner approved for connecting apps by MCP
# (docs/SCOPE_CONNECTIONS_MCP_FIRST.md), and what it connects is read by coworkers on their shifts.
# FOUR NAMED GROUPS, IN THE ORDER A BOX IS SET UP (owner, 2026-10-02, approved from a preview): AI (the account, the MCP
# server your own AI connects to, the coworkers on the box), Connections (what it reads from, the mobile app, the
# address it sends from), Team, Server.
# HIS ORDER (owner, 2026-10-02, approved from a preview): "The order of the menu should go AI account, MCP server, data
# sources, and then coworkers"; then General (owner, 2026-10-03: the mobile app, the address the box sends from, the
# people on it: "AI is settled and server is settled, but the other three need to be in another category"), Server.
ok("the rows, in their groups' order",
   _srows == ["Overview", "Your Business", "AI Account", "MCP Server", "Data Sources", "Coworkers", "Mobile App", "Sending Email",
              "People", "Time Zone", "Updates", "Server Access"], str(_srows))
_sgrp = re.findall(r'<a href="[^"]*"[^>]*class="[^"]*\bgrp\b[^"]*"[^>]*>.*?<span class="lbl">([^<]*)', _sn, re.S)
ok("...each group opening with a gap", _sgrp == ["Your Business", "AI Account", "Mobile App", "Updates"], str(_sgrp))
ok("...and its name above its first row", re.findall(r'<span class="glabel">([^<]*)</span>', _sn)
   == ["Business", "AI", "General", "Server"], re.findall(r'<span class="glabel">([^<]*)</span>', _sn))
ok("...and no old name is left", all(x not in _sn for x in ("AI Coworkers", "Outbound Email", "Coworkers (Agents)",
                                                           "Reaching you", '"glabel">Team<',
                                                           '"lbl">Shifts', '"lbl">Email<')))
_mc = app.test_client()
_mc.set_cookie(dash.COOKIE, dash.new_session(state.add_user("tomas.reyes@acme.co", name="Tomas",
                                                            role="member")["id"]))
_mn = _mc.get("/settings").get_data(as_text=True).split('<nav class="rail"', 1)[-1].split("</nav>", 1)[0]
_mgrp = re.findall(r'<a href="[^"]*"[^>]*class="[^"]*\bgrp\b[^"]*"[^>]*>.*?<span class="lbl">([^<]*)', _mn, re.S)
# A GROUP WHOSE FIRST ROWS A MEMBER IS NOT SHOWN still opens with its gap, on the first row they are.
ok("a member's shorter menu keeps its gaps on the rows they are shown",
   _mgrp == ["Your Business", "Coworkers", "Mobile App", "Updates"], str(_mgrp))
# A GROUP'S NAME TRAVELS WITH ITS GAP, and a group a member sees nothing of has no name over another group's rows.
ok("...and its group names over them, never a name for a group they are shown nothing of",
   re.findall(r'<span class="glabel">([^<]*)</span>', _mn) == ["Business", "AI", "General", "Server"],
   re.findall(r'<span class="glabel">([^<]*)</span>', _mn))


print("\ntest_every_settings_page_says_where_you_are")
# EVERY SUB-PAGE MARKS ITS OWN ROW. /settings/email passed "/settings" to chrome() and read
# "System Settings / Overview" over the Email page (found by rendering, 2026-09-24). Each row of
# the System Settings menu is opened here as the owner, and must light itself and name itself.
sec = next(s_ for s_ in shell.sections() if s_.key == "settings")
for it in sec.items:
    r = c.get(it.href)
    if r.status_code != 200:
        ok(f"{it.href} renders", False, str(r.status_code))
        continue
    page_ = r.get_data(as_text=True)
    nav_ = page_.split('<nav class="rail"', 1)[-1].split("</nav>", 1)[0]
    lit = re.findall(r'<a href="([^"]*)"[^>]*aria-current="page"', nav_)
    ok(f"{it.href}: the menu marks its own row", lit == [it.href], str(lit))
    crumb = re.search(r'<div class="crumb">(.*?)</div>', page_, re.S)
    if it.href == sec.href:
        # THE SECTION'S OWN SCREEN DRAWS NO TRAIL (owner, 2026-09-29: the breadcrumb without
        # "Overview"). Its heading already names it; "System Settings / Overview" read it back.
        ok(f"{it.href}: the section's own screen draws no trail; its heading names it",
           not crumb and "<h1>System Settings</h1>" in page_, crumb.group(1) if crumb else "")
        continue
    ok(f"{it.href}: the breadcrumb names it", bool(crumb) and f"<b>{it.label}</b>" in crumb.group(1),
       crumb.group(1) if crumb else "no crumb")

print("\ntest_a_machine_cannot_climb_above_the_box")
shell.register_section("eager", order=-5, machine="eager_machine", title="Eager",
                       href="/eager/")
keys = [s.key for s in shell.sections()]
ok("a machine with the lowest order still sits below System Settings",
   keys.index("eager") > keys.index("settings"), str(keys))
ok("...and Add a Machine is still the last row", keys[-1] == "add_machine", str(keys))
ok("a machine that is not core joins the add-on group without saying so",
   next(s for s in shell.sections() if s.key == "eager").group == "addons")
try:
    shell.register_section("odd", order=1, machine="odd", title="Odd", href="/odd/", group="middle")
    ok("a group that does not exist is refused at boot", False)
except ValueError:
    ok("a group that does not exist is refused at boot", True)

print("\n" + ("all good" if not _failed else f"{_failed} FAILED"))
sys.exit(1 if _failed else 0)
