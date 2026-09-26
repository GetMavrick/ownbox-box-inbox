"""The Shifts screen: a buyer makes a coworker on a mobile device and gets its first report without
touching a file (docs/SCOPE_SHIFTS.md §7, piece 4's done-when), and hires a machine's coworker from
its permission sheet (piece 5's sheet).

  · only the owner, and only on Pro; anything else is told why, and nothing is written;
  · the form writes a coworker the contract accepts, keeps what it does not edit (steps, limits,
    from), and says in plain words what is wrong before anything is saved;
  · reading the box and reaching the web together needs the owner's tick (§4.3), and a changed
    grant asks again;
  · Try it now queues a trial run; the runner runs it; the screen shows the outcome and its reason;
  · one ink pill per screen, and none of the reserved nouns in what a buyer reads.

Run: python tests/test_shifts_screen.py
"""
import contextlib
import html
import json
import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = _T + "/shifts.db"
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
MACHINES = pathlib.Path(tempfile.mkdtemp())
MINE = pathlib.Path(tempfile.mkdtemp())
os.environ["AIOS_MY_MACHINES"] = str(MACHINES)
os.environ["AIOS_MY_COWORKERS"] = str(MINE)
PROVISION = pathlib.Path(tempfile.mkdtemp()) / "provision.json"
os.environ["AIOS_PROVISION_JSON"] = str(PROVISION)
os.environ.pop("ANTHROPIC_API_KEY", None)

from core import state  # noqa: E402

state.init_db()

from core import box_mail, box_settings, brain, claim, dash, push, shell  # noqa: E402
from core.connector import seats, tools  # noqa: E402
from core.coworkers import contract, hire, locks, runner, runs  # noqa: E402
from core.dispatch import app  # noqa: E402

claim.PROVISION_JSON = str(PROVISION)
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


# THE PLAN COMES FROM core.tiers (docs/SCOPE_TIERS.md §2.1), set here through a fixture, as §3
# says tests do. core/tiers.py is OSDev4's and lands before this screen; until it exists the
# fixture stands in for it with exactly the two calls the screen makes, current() and allows().
import types  # noqa: E402

import core  # noqa: E402

try:
    from core import tiers  # noqa: E402
except ImportError:
    tiers = types.ModuleType("core.tiers")
    sys.modules["core.tiers"] = core.tiers = tiers
PLAN = {}
TIER_NAMES = {"ownbox": "Base", "pro": "Pro"}
tiers.current = lambda: dict(PLAN)
tiers.allows = lambda feature: feature in PLAN.get("features", ())
# The runner still reads provision.json until OSDev4's revert lands; it is kept in step so a trial
# run behaves the same before and after.
PROVISION.write_text(json.dumps({"tier": "pro"}))


def plan(tier):
    PLAN.clear()
    PLAN.update({"tier": tier, "name": TIER_NAMES[tier], "add": [], "source": "ownbox",
                 "features": ["coworkers"] if tier == "pro" else []})


def main(page: str) -> str:
    return page.split("</nav>", 1)[-1]


def ink_pills(page: str) -> list:
    m = main(page)
    out = []
    for b in re.finditer(r"<button\b([^>]*)>(.*?)</button>", m, re.S):
        cls = set((re.search(r'class="([^"]*)"', b.group(1)) or [None, ""])[1].split())
        if not cls & {"ghost", "danger"}:
            out.append(re.sub(r"\s+", " ", b.group(2)).strip())
    out += [re.sub(r"<[^>]+>", "", a) for a in re.findall(r'<a class="btn"[^>]*>(.*?)</a>', m)]
    return out


# SHIPPED TEXT ONLY: the page minus the box's shared stylesheet, which its own test measures.
_RESERVED = re.compile(r"\b(phones?|rings?|calls?|dial|lines?|voice)\b", re.I)


def reserved(page: str) -> list:
    text = re.sub(r"<style>.*?</style>", "", main(page), flags=re.S)
    return sorted(set(m.lower() for m in _RESERVED.findall(text)))


owner = app.test_client()
owner.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
member = app.test_client()
member.set_cookie(dash.COOKIE, dash.new_session(
    state.add_user("coach@example.com", role="member")["id"]))

# The inbox's read tool and its drafting tool, as a Pro box with the Unified Inbox has them.
tools._reset_for_tests()
tools.register("list_threads", fn=lambda: ["t1"], description="list conversations",
               machine="inbox_demo", capability="read:inbox")
tools.register("draft_reply", fn=lambda **k: {"id": "d_1"}, description="draft a reply",
               machine="inbox_demo", capability="write:proposals", min_role="act",
               args={"text": {"type": "string", "required": True}})

print("\ntest_the_menu_has_a_shifts_row")
sec = next((s for s in shell.sections() if s.key == "settings"), None)
labels = [i.label for i in sec.items] if sec else []
ok("Shifts is in System Settings, right after AI coworkers",
   "Shifts" in labels and labels.index("Shifts") == labels.index("AI coworkers") + 1, str(labels))
ok("...open to a member, who may look", not next(i for i in sec.items if i.label == "Shifts").owner_only)
ok("the top of the menu is unchanged: no row of its own", "shifts" not in [s.key for s in shell.sections()])

print("\ntest_not_in_your_plan")
plan("ownbox")
page = owner.get("/settings/shifts").get_data(as_text=True)
ok("a Base box reads Coworkers come with Pro, and which plan it is on",
   "Coworkers come with Pro" in page and "This box is on Base" in page)
ok("...with the upgrade as the one ink pill", ink_pills(page) == ["See Pro"]
   and 'href="https://www.ownbox.io/#pricing"' in page, str(ink_pills(page)))
ok("...and no way to make one", "New coworker" not in main(page))
r = owner.post("/settings/shifts/new",
               data={"title": "X", "job": "y", "start0": "07:30", "days0": "Mon-Fri"})
ok("...and a post writes nothing", not any(MINE.iterdir()), str(list(MINE.iterdir())))
ok("the screen asks about the feature, never a tier's name",
   "tier ==" not in (ROOT / "core" / "dash" / "shifts.py").read_text()
   and '"pro"' not in (ROOT / "core" / "dash" / "shifts.py").read_text())
PLAN.update({"tier": "ownbox", "add": ["coworkers"], "features": ["coworkers"]})
ok("an add-on that brings coworkers to Base opens it: the feature decides, not the tier",
   "Coworkers come with Pro" not in owner.get("/settings/shifts").get_data(as_text=True))

print("\ntest_in_your_plan_but_needs_setting_up")
plan("pro")
brain.can_think = lambda: (False, "no AI account is connected")
page = owner.get("/settings/shifts").get_data(as_text=True)
ok("it says coworkers are in the plan, and names the AI account as missing",
   "Coworkers are in your plan" in page and "Connect an AI account" in page)
ok("...and connecting it is the one ink pill, with New coworker waiting as a link",
   ink_pills(page) == ["Connect an AI account"] and 'href="/settings/ai"' in page
   and 'href="/settings/shifts/new">New coworker' in page, str(ink_pills(page)))
_health = runner.health
runner.health = lambda now=None: {"tick": "off", "age_s": None}
page = owner.get("/settings/shifts").get_data(as_text=True)
ok("a stopped shift clock is named too", "The shift clock isn" in page)
brain.can_think = lambda: (True, "claude_code")
page = owner.get("/settings/shifts").get_data(as_text=True)
ok("with only the clock missing, there is nothing to tap for it, so New coworker is the pill",
   "shift clock isn" in page and ink_pills(page) == ["New coworker"], str(ink_pills(page)))
runner.health = lambda now=None: {"tick": "ok", "age_s": 30}

print("\ntest_working")
page = owner.get("/settings/shifts").get_data(as_text=True)
ok("in the plan and set up: no setting-up card, no upgrade",
   "Coworkers are in your plan" not in page and "Coworkers come with Pro" not in page)

print("\ntest_only_the_owner_changes")
r = member.get("/settings/shifts")
ok("a member may look", r.status_code == 200)
ok("...and is refused at every page that changes something, told it is the owner's",
   all(member.get(p).status_code == 403 for p in (
       "/settings/shifts/new", "/settings/shifts/anyone/edit",
       "/settings/shifts/hire/acme-social/poster"))
   and "Only the owner" in member.get("/settings/shifts/new").get_data(as_text=True))

print("\ntest_an_empty_box_says_what_a_coworker_is")
r = owner.get("/settings/shifts")
page = r.get_data(as_text=True)
ok("the page opens", r.status_code == 200)
ok("it says what a coworker is", "No coworkers yet" in page)
ok("one ink pill, and it is New coworker", ink_pills(page) == ["New coworker"], str(ink_pills(page)))
r = owner.get("/shifts/")
ok("the address a report's notification opens leads here", r.status_code == 302
   and r.headers["Location"].endswith("/settings/shifts"), r.headers.get("Location"))

print("\ntest_the_form_is_the_studio_example")
page = owner.get("/settings/shifts/new").get_data(as_text=True)
ok("the example is a studio's trial members",
   "Trial follow-up" in page and "free trial" in page and "plumb" not in page.lower())
ok("times are time fields, and the first is weekdays at 07:30 to start",
   page.count('type="time"') >= 2 and 'value="07:30"' in page and 'value="07:45"' in page)
ok("it offers reading conversations and drafting for approval, in plain words",
   "Read your conversations" in page and "Draft things for your approval" in page)
ok("...and the web, off unless ticked",
   "Search the web" in page and 'value="web:search" checked' not in page)
ok("the risk sentence is on the form, with its tick", html.escape(hire.RISK) in page and 'name="risk_ok"' in page)
ok("one ink pill, and it is Save", ink_pills(page) == ["Save"], str(ink_pills(page)))
ok("field text is 16px (no zoom on focus) and times are styled as fields",
   "input[type=time]" in page)
ok("none of the reserved nouns", reserved(page) == [], str(reserved(page)))

print("\ntest_plain_words_before_anything_is_saved")
r = owner.post("/settings/shifts/new", data={"title": "", "job": "", "days0": "Mon-Fri", "start0": ""})
page = r.get_data(as_text=True)
ok("nothing typed: refused, with each thing to fix",
   r.status_code == 400 and "Give it a name." in page and "Say what its job is" in page
   and "at least one time" in page)
r = owner.post("/settings/shifts/new", data={"title": "Trial follow-up", "job": "Do it.", "days0": "Mon-Fri",
                                     "start0": "07:30", "latest0": "07:32"})
ok("a skip time under 5 minutes after the start is refused in words",
   r.status_code == 400 and "at least 5 minutes" in r.get_data(as_text=True))
r = owner.post("/settings/shifts/new", data={"title": "Scout", "job": "Look around.", "days0": "Mon-Fri",
                                     "start0": "09:00", "may": ["read:inbox", "web:search"]})
ok("reading the box and the web together, unticked, is refused",
   r.status_code == 400 and "Tick the sentence" in r.get_data(as_text=True))
ok("...and what was typed comes back", 'value="Scout"' in r.get_data(as_text=True))
ok("nothing was written by any of it", not any(MINE.iterdir()), str(list(MINE.iterdir())))

print("\ntest_the_gym_example_is_made_from_the_screen")
r = owner.post("/settings/shifts/new", data={
    "title": "Trial follow-up", "job": "Every weekday morning, read trial members' messages. "
    "Draft a friendly reply to each.", "days0": "Mon-Fri", "start0": "07:30", "latest0": "07:45",
    "days1": "Sat", "start1": "09:00", "latest1": "",
    "may": ["read:inbox", "write:proposals"], "enabled": "1"})
ok("saved, and it opens the coworker", r.status_code == 303
   and r.headers["Location"].endswith("/settings/shifts/trial-follow-up?said=saved"), r.headers.get("Location"))
cw, why = contract.load(MINE / "trial-follow-up")
ok("the contract accepts the file the screen wrote", cw is not None, str(why))
ok("...with its job, times, grant, and switched on",
   cw and cw.enabled and cw.may == ("read:inbox", "write:proposals") and cw.source == "my"
   and [(s.start, s.latest) for s in cw.shifts] == [("07:30", "07:45"), ("09:00", "09:15")]
   and "trial members" in (MINE / "trial-follow-up" / "job.md").read_text())
ok("a blank skip time is a quarter of an hour after the start", cw and cw.shifts[1].latest == "09:15")
r = owner.post("/settings/shifts/new", data={"title": "Trial follow-up", "job": "Another.", "days0": "Daily",
                                     "start0": "12:00"})
ok("the same name again gets its own folder", (MINE / "trial-follow-up-2").is_dir())
r = owner.post("/settings/shifts/new", data={"title": "New", "job": "x", "days0": "Daily", "start0": "13:00"})
ok("a coworker called New never takes the form's address",
   not (MINE / "new").exists() and (MINE / "new-2").is_dir(), str(sorted(p.name for p in MINE.iterdir())))

print("\ntest_the_coworker_page")
page = owner.get("/settings/shifts/trial-follow-up").get_data(as_text=True)
ok("it says it is on, and when", "<h2>On</h2>" in page and "Weekdays at 07:30" in page)
ok("one ink pill, and it is Try it now", ink_pills(page) == ["Try it now"], str(ink_pills(page)))
ok("switching off is a ghost, removing is a danger folded away",
   'class="ghost">Switch off' in page and "<summary>Remove this coworker</summary>" in page)
ok("what it may do is its permission sheet", "Read your conversations" in page
   and "Send, publish or pay for anything" in page)
ok("no runs yet says what a run will be", "No runs yet" in page)
ok("none of the reserved nouns", reserved(page) == [], str(reserved(page)))
page = owner.get("/settings/shifts").get_data(as_text=True)
ok("the list shows it, on, with its times", "Trial follow-up" in page and ">On<" in page
   and "Weekdays at 07:30; Sat at 09:00" in page
   and "Weekdays at 07:30 and" not in page)

page = member.get("/settings/shifts").get_data(as_text=True)
ok("a member sees the coworker, and no New coworker", "Trial follow-up" in page
   and "New coworker" not in main(page))
page = member.get("/settings/shifts/trial-follow-up").get_data(as_text=True)
ok("a member sees its page with no control on it", "<h2>On</h2>" in page
   and "<button" not in main(page) and "/edit" not in main(page), str(ink_pills(page)))
r = member.post("/settings/shifts/trial-follow-up", data={"do": "off"})
ok("...and a member's post changes nothing", r.status_code == 403
   and contract.load(MINE / "trial-follow-up")[0].enabled)

print("\ntest_switching_off_and_on")
owner.post("/settings/shifts/trial-follow-up", data={"do": "off"})
ok("off writes enabled: false", contract.load(MINE / "trial-follow-up")[0].enabled is False)
page = owner.get("/settings/shifts/trial-follow-up").get_data(as_text=True)
ok("...and its page offers Switch on as the one ink pill", ink_pills(page) == ["Switch on"],
   str(ink_pills(page)))
owner.post("/settings/shifts/trial-follow-up", data={"do": "on"})
ok("on writes enabled: true", contract.load(MINE / "trial-follow-up")[0].enabled is True)

print("\ntest_try_it_now_and_the_first_report")
PT = runner.box_tz()
NEXT, ALIVE, MAIL, PUSHES = [], set(), [], []
locks.write_next = lambda epoch, next_file=None: NEXT.append(epoch)
locks.update_running = lambda deploy_lock=None: False
locks._busy = lambda path: False


@contextlib.contextmanager
def _held(shift_lock=None, deploy_lock=None):
    yield True


locks.shift = _held
runner._load_machines = lambda: None
runner._stop_agent = lambda cmd: None
runner.ROOT = pathlib.Path(tempfile.mkdtemp())
box_mail.is_configured = lambda: True
box_mail.to_box_people = lambda user_id=None: [{"id": user_id, "email": "owner@example.com"}]
box_mail.send = lambda to, subject, text, html, **kw: MAIL.append((subject, text)) or "m1"
push.subscriptions_for = lambda user_id: [{"endpoint": "https://push.example/1"}]
push.send = lambda sub, **kw: PUSHES.append(kw) or (True, "ok")


def fake_agent(prompt, **kw):
    seat = seats.verify(kw["mcp"]["credential"])
    tools.call("aios.inbox_demo.list_threads", None, seat)
    return {"text": "Drafted 2 replies for trial members.", "turns": 6, "minutes": 1.0,
            "cost_usd": 0.0, "api_usd": 0.1, "backend": "claude_code", "model": "cc", "denied": []}


r = owner.post("/settings/shifts/trial-follow-up", data={"do": "run"})
ok("Try it now queues a trial run", r.status_code == 303 and "said=queued" in r.headers["Location"])
row = next((x for x in runs.by_status("queued") if x["coworker"] == "trial-follow-up"), None)
ok("...marked as a dry run", row is not None and row["dry_run"] == 1)
page = owner.get("/settings/shifts/trial-follow-up?said=queued").get_data(as_text=True)
ok("the page says it is queued, and shows it waiting as a trial run",
   "trial run is queued" in page and ">Waiting<" in page and "Trial run" in page)
d = runner.tick(launch=lambda slot, unit, minutes: ALIVE.add(unit),
                unit_active=lambda unit: unit in ALIVE)
runner.run(row["slot"], run_agent=fake_agent, sleep=lambda s: None)
done = runs.get(row["slot"])
ok("the runner runs it to Done", done and done["status"] == "DONE", str(done and done["status"]))
runner.report(done)
ok("its first report goes out, by email and to the mobile app",
   len(MAIL) == 1 and PUSHES and PUSHES[0].get("navigate") == "/shifts/", str((MAIL, PUSHES)))
page = owner.get("/settings/shifts/trial-follow-up").get_data(as_text=True)
ok("the page shows it Done, with what it did", ">Done<" in page and "Drafted 2 replies" in page,
   main(page)[-1500:])
ok("the list shows the last run", "Last: Done" in owner.get("/settings/shifts").get_data(as_text=True))

print("\ntest_web_and_data_need_the_owners_ok")
r = owner.post("/settings/shifts/new", data={"title": "Scout", "job": "Find what trial members ask about.",
                                     "days0": "Mon-Fri", "start0": "10:00",
                                     "may": ["read:inbox", "web:search"], "risk_ok": "1",
                                     "enabled": "1"})
scout = contract.load(MINE / "scout")[0]
ok("ticked, it saves and holds the OK", scout is not None and hire.acknowledged(scout))
r = owner.post("/settings/shifts/scout/edit", data={"title": "Scout", "job": "Find what they ask.",
                                            "days0": "Mon-Fri", "start0": "10:00",
                                            "may": ["read:inbox", "read:reports", "web:search"],
                                            "enabled": "1"})
ok("a wider grant without the tick is refused", r.status_code == 400
   and "Tick the sentence" in r.get_data(as_text=True))
# A GRANT CHANGED BY HAND is not the one the owner said yes to.
raw = json.loads(json.dumps(__import__("yaml").safe_load((MINE / "scout" / "coworker.yaml").read_text())))
raw["may"] = ["read:inbox", "read:reports", "web:read"]
(MINE / "scout" / "coworker.yaml").write_text(__import__("yaml").safe_dump(raw))
page = owner.get("/settings/shifts/scout").get_data(as_text=True)
ok("a grant changed by hand asks again, as the one ink pill",
   "It needs your OK to run" in page and ink_pills(page) == ["OK, let it run"], str(ink_pills(page)))
r = owner.post("/settings/shifts/scout", data={"do": "run"})
ok("...and it can't be tried until then", r.status_code == 400
   and "needs your OK" in r.get_data(as_text=True))
owner.post("/settings/shifts/scout", data={"do": "ok"})
ok("OK, let it run records the OK for that grant", hire.acknowledged(contract.load(MINE / "scout")[0]))

print("\ntest_an_edit_keeps_what_the_screen_does_not_edit")
d = MINE / "night-shift"
d.mkdir()
(d / "coworker.yaml").write_text(json.dumps({
    "coworker": 1, "title": "Night shift", "job": "tasks.md", "from": "my",
    "may": ["read:reports"], "shifts": [{"days": "Mon,Wed,Fri", "start": "22:00", "latest": "22:30"}],
    "steps": {"before": [], "after": ["acme.save_picks"]}, "limits": {"minutes": 12, "turns": 9},
    "enabled": False}))
(d / "tasks.md").write_text("Summarise the day.")
(d / "notes.txt").write_text("mine")
page = owner.get("/settings/shifts/night-shift/edit").get_data(as_text=True)
ok("the edit form shows its own days", 'value="Mon,Wed,Fri" selected' in page)
r = owner.post("/settings/shifts/night-shift/edit", data={
    "title": "Night shift", "job": "Summarise the day, briefly.", "days0": "Mon,Wed,Fri",
    "start0": "21:00", "latest0": "21:30", "may": ["read:reports"]})
cw, why = contract.load(d)
ok("saved", r.status_code == 303 and cw is not None, str(why))
ok("its steps, limits, job file and the file beside them are kept",
   cw and cw.after == ("acme.save_picks",) and cw.minutes == 12 and cw.turns == 9
   and cw.job == "tasks.md" and (d / "tasks.md").read_text().startswith("Summarise the day, briefly")
   and (d / "notes.txt").read_text() == "mine")
ok("...and the new time", cw and cw.shifts[0].start == "21:00")

print("\ntest_a_broken_file_is_named_not_hidden")
(MINE / "broken").mkdir()
(MINE / "broken" / "coworker.yaml").write_text("coworker: 2\n")
page = owner.get("/settings/shifts").get_data(as_text=True)
ok("it is listed as Can't run, with why", "broken" in page and "Can&#x27;t run" in page)

print("\ntest_removing")
scout = contract.load(MINE / "scout")[0]
ok("before removing, its OK is held", hire.acknowledged(scout))
r = owner.post("/settings/shifts/scout", data={"do": "remove"})
ok("remove deletes the folder", r.status_code == 303 and not (MINE / "scout").exists())
ok("...and its OK, so a new Scout is asked again", not hire.acknowledged(scout))

print("\ntest_hiring_from_a_machine")
m = MACHINES / "acme-social"
(m / "coworkers" / "poster").mkdir(parents=True)
(m / "machine.yaml").write_text('name: acme-social\nversion: 1\nrequires_foundation: "1.2"\n')
(m / "__init__.py").write_text("")
(m / "coworkers" / "poster" / "coworker.yaml").write_text(json.dumps({
    "coworker": 1, "title": "Poster", "job": "job.md", "from": "acme-social",
    "may": ["read:inbox", "web:read"], "shifts": [{"days": "Mon-Fri", "start": "10:00",
                                                  "latest": "10:30"}], "enabled": False}))
(m / "coworkers" / "poster" / "job.md").write_text("Draft posts.")
page = owner.get("/settings/shifts").get_data(as_text=True)
ok("a machine's coworker is listed Ready to hire", "Ready to hire" in page and "Poster" in page
   and 'href="/settings/shifts/hire/acme-social/poster"' in page)
page = owner.get("/settings/shifts/hire/acme-social/poster").get_data(as_text=True)
ok("its sheet says who, what it may, what it can't, and when",
   "acme-social&#x27;s Poster" in page and "Read your conversations" in page
   and "See your keys or passwords" in page and "Weekdays, starting between 10:00 and 10:30" in page)
ok("...and the risk, with its tick", html.escape(hire.RISK) in page and 'name="risk_ok"' in page)
ok("one ink pill, and it is Hire", ink_pills(page) == ["Hire"], str(ink_pills(page)))
r = owner.post("/settings/shifts/hire/acme-social/poster",
               data={"fingerprint": re.search(r'name="fingerprint" value="([^"]+)"', page).group(1)})
ok("hiring without the tick is refused, and nothing is copied",
   r.status_code == 400 and not (MINE / "poster").exists())
FP = re.search(r'name="fingerprint" value="([^"]+)"', page).group(1)
r = owner.post("/settings/shifts/hire/acme-social/poster", data={"risk_ok": "1", "fingerprint": "stale"})
ok("an offer that changed since the sheet was read is refused, and nothing is copied",
   r.status_code == 400 and "has changed since you looked at it" in r.get_data(as_text=True)
   and not (MINE / "poster").exists())
r = owner.post("/settings/shifts/hire/acme-social/poster", data={"risk_ok": "1", "fingerprint": FP})
ok("with it, it is hired and switched on", r.status_code == 303
   and contract.load(MINE / "poster")[0].enabled)
ok("...and it is no longer offered", "Ready to hire" not in owner.get("/settings/shifts").get_data(as_text=True))

print("\ntest_a_report_notification_may_open_shifts")
from marketing.customer_voice import app as inbox_app  # noqa: E402
sw = inbox_app.SW_JS if hasattr(inbox_app, "SW_JS") else inbox_app.r_sw().get_data(as_text=True)
ok("the inbox's service worker lets a notification open Shifts", "'/shifts/'" in sw)

print("\n" + ("all good" if not _failed else f"{_failed} FAILED"))
sys.exit(1 if _failed else 0)
