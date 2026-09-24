"""Every setting a buyer can change has one place to change it.

Owner, 2026-09-24: "there's only one place to add a key or change a setting." Before this, the AI
account was written from /settings/ai, /inbox/drafts and /inbox/setup; the mailbox from
/inbox/mailbox and /inbox/setup; the social key from /inbox/connect and /inbox/setup. The screens
disagreed on what to say, and a fix to one form never reached the others.
docs/SCOPE_ONE_PLACE_PER_SETTING.md §3 is the plan this suite follows.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  · a second route starts writing a credential that already has a home: a paste form on a set-up
    screen, a machine's settings page, a new wizard. It fails naming both routes;
  · the call map goes blind (a writer renamed, a route registered some new way), so a writer is
    reached from no route at all. That is a failure too, not a pass;
  · a set-up screen grows a field that takes a credential;
  · a home stops being linked from its settings index, so a buyer cannot find the one place.

HOW THE MAP IS BUILT: the source of core/ and marketing/ is parsed, every Flask route is found by
its decorator, and each route's calls are followed through the functions of its own module to the
writers below. It is the same reading the inventory in the scope doc was built from, done by code.

Run: python tests/test_one_place_per_setting.py
"""
import ast
import os
import pathlib
import re
import sys
import tempfile
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "oneplace.db")
os.environ.setdefault("DASH_TOKEN", "pw")

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


INBOX = (ROOT / "marketing" / "customer_voice" / "app.py").is_file()

# EACH WRITER, AND THE ONE ROUTE THAT IS ITS HOME. (module the call is made through, function) ->
# route. A writer that belongs to a machine is checked only on a box that carries that machine.
HOMES = {
    ("box_secrets", "put_ai_credential"): "/settings/ai",
    ("claude_login", "finish"): "/settings/ai",
    ("box_mail", "put_own"): "/settings/email",
}
if INBOX:
    HOMES.update({
        ("box_secrets", "put_email"): "/inbox/mailbox",
        ("box_secrets", "put_zernio"): "/inbox/connect",
        ("box_secrets", "put_zernio_profile"): "/inbox/connect",
    })
# THE LOWER-LEVEL WRITERS no route may call directly. Each one is reached only through a writer
# above (`put_ai_credential` routes to them), so a route calling one is a second door to the AI
# account that skips the first one's rules.
NO_ROUTE_CALLS = {("box_secrets", "put_anthropic"), ("box_secrets", "put_claude_oauth")}


def _route_map():
    """{(module, writer): {route paths that reach it}} over every route in core/ and marketing/."""
    files = [p for d in ("core", "marketing") for p in (ROOT / d).rglob("*.py")
             if "tests" not in p.parts and "__pycache__" not in p.parts]
    routes, calls = {}, defaultdict(set)
    for f in files:
        try:
            tree = ast.parse(f.read_text())
        except SyntaxError:
            continue
        rel = str(f.relative_to(ROOT))
        # A ROUTE'S PATH CAN BE A MODULE CONSTANT (core/dash/box_email.py: `DOOR`).
        consts = {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign)
                  and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)
                  for t in n.targets if isinstance(t, ast.Name)}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            key = (rel, node.name)
            for d in node.decorator_list:
                if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                        and d.func.attr in ("route", "get", "post") and d.args):
                    a = d.args[0]
                    path = a.value if isinstance(a, ast.Constant) else consts.get(getattr(a, "id", ""))
                    if isinstance(path, str):
                        routes.setdefault(key, set()).add(path)
            for c in ast.walk(node):
                if isinstance(c, ast.Call):
                    fn = c.func
                    if isinstance(fn, ast.Attribute):
                        calls[key].add((ast.unparse(fn.value).split(".")[-1], fn.attr))
                    elif isinstance(fn, ast.Name):
                        calls[key].add(("", fn.id))
    reached = defaultdict(set)
    for key, paths in routes.items():
        seen, stack = set(), [key]
        while stack:
            k = stack.pop()
            if k in seen:
                continue
            seen.add(k)
            for mod, name in calls.get(k, ()):
                reached[(mod, name)] |= paths
                if mod == "" and (k[0], name) in calls:
                    stack.append((k[0], name))
    return reached


print("\ntest_every_writer_has_one_route")
reached = _route_map()
for writer, home in sorted(HOMES.items()):
    got = reached.get(writer, set())
    name = ".".join(writer)
    ok(f"{name} is reached from its home, {home}", home in got,
       f"reached from {sorted(got) or 'no route at all: the map went blind'}")
    ok(f"...and {name} from no other route", got <= {home}, f"also from {sorted(got - {home})}")
for writer in sorted(NO_ROUTE_CALLS):
    got = reached.get(writer, set())
    ok(f"{'.'.join(writer)} is reached from no route directly", not got, str(sorted(got)))

# PROOF THE MAP SEES WHAT IT CLAIMS TO: a route known to write, planted in a scratch module, must
# show up. Without this, a parser change that found nothing would pass every check above that
# says "no other route".
_probe = ROOT / "marketing" / "_one_place_probe.py"
try:
    _probe.write_text('@bp.route("/probe/second-door", methods=["POST"])\n'
                      'def second():\n    _helper()\n\n'
                      'def _helper():\n    box_secrets.put_ai_credential("x")\n')
    planted = _route_map().get(("box_secrets", "put_ai_credential"), set())
    ok("a planted second door is caught, through a helper", "/probe/second-door" in planted,
       str(sorted(planted)))
finally:
    _probe.unlink(missing_ok=True)


print("\ntest_set_up_takes_no_credential")
from core import state                                                # noqa: E402

state.init_db()
from core import dash                                                 # noqa: E402
from core.dispatch import app                                         # noqa: E402

c = app.test_client()
c.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
_FIELD = re.compile(r'<input[^>]*\bname="(key|password|code|token)"', re.I)
_setup = ["/inbox/setup"] if INBOX else []
for path in _setup:
    r = c.get(path)
    html = r.get_data(as_text=True)
    ok(f"{path} renders", r.status_code == 200 and len(html) > 200, str(r.status_code))
    ok(f"{path} carries no field that takes a credential", not _FIELD.search(html),
       str(_FIELD.findall(html)))
if not _setup:
    print("  --   no set-up screen ships on this box")


print("\ntest_every_home_is_linked_from_its_index")
# THE HOMES OF THE WRITERS ABOVE, and nothing else. /inbox/drafts is not one: since the AI account
# moved to /settings/ai (#1510) it writes no credential, and /settings/ai is checked here.
_index = {"/settings/ai": "/settings", "/settings/email": "/settings"}
if INBOX:
    _index.update({"/inbox/mailbox": "/inbox/settings", "/inbox/connect": "/inbox/settings"})
_pages = {}
for home, index in sorted(_index.items()):
    html = _pages.setdefault(index, c.get(index).get_data(as_text=True))
    ok(f"{home} is linked from {index}", f'href="{home}' in html)


print("\n— and this file cannot silently fall out of CI —")
_wf = ROOT / ".github/workflows/tests.yml"
if _wf.is_file():
    ok("test_one_place_per_setting is in the workflow's suite list",
       "test_one_place_per_setting" in _wf.read_text())

print("\nALL OK" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
