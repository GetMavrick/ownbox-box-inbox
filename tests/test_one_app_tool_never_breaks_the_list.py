"""One connected app's odd tool must never break the owner's whole tool list (the connector strike, 2026-10-02).

A connected app's tools are listed beside the box's own, and a client that can't take one tool refuses the whole
reload: the owner saw "Couldn't reload tools from the server". So each app tool is checked before it is listed;
one that fails is left off and named on its row on Data sources, with why, and the rest of the list stays whole.

Run: python tests/test_one_app_tool_never_breaks_the_list.py
"""
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = tempfile.mkdtemp() + "/apps.db"
os.environ["DISPATCH_BEARER_TOKEN"] = "x"

from core import state  # noqa: E402

state.init_db()
from core import dispatch  # noqa: E402
from core.connections import gateway, store  # noqa: E402
from core.connector import seats  # noqa: E402
from core.dash import sources  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def tool(tid, schema, *, read_only=True):
    return {"id": tid, "name": tid, "title": tid.replace("_", " ").capitalize(), "description": "",
            "input_schema": schema, "read_only": read_only, "changes": not read_only}


GOOD = tool("search_pages", {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]})
NO_SCHEMA = tool("list_all", {})
ARRAY = tool("bulk_lookup", {"type": "array", "items": {"type": "string"}})
BAD_PROPS = tool("odd_props", {"type": "object", "properties": ["q"]})
BAD_REQ = tool("odd_required", {"type": "object", "properties": {"q": {"type": "string"}}, "required": "q"})
LONG = tool("x" * 48, {"type": "object"})
ASK_ARRAY = tool("make_many", {"type": "array"}, read_only=False)
apps = {"fake-notes-and-tasks-for-teams": {
    "name": "Fake Notes", "host": "notes.example", "added_at": "2026-10-02T19:00:00+00:00",
    "tools": [GOOD, NO_SCHEMA, ARRAY, BAD_PROPS, BAD_REQ, LONG, ASK_ARRAY],
    "enabled": [t["id"] for t in (GOOD, NO_SCHEMA, ARRAY, BAD_PROPS, BAD_REQ, LONG)],
    "ask_first": [ASK_ARRAY["id"]]}}
store._save({"items": apps}, by="test")
slug = "fake-notes-and-tasks-for-teams"

print("\nOne app tool never breaks the list\n")
gateway.refresh()
c = dispatch.app.test_client()
lists = {}
for role in ("read", "act"):
    _id, cred = seats.mint(f"{role} seat", role)
    r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
               headers={"Authorization": f"Bearer {cred}"})
    lists[role] = (r.status_code, (r.get_json() or {}).get("result", {}).get("tools", []))
code, listed = lists["act"]
names = {t["name"] for t in listed}
app_names = sorted(n for n in names if n.startswith("app_"))
ok("the list still answers, whole", code == 200 and "core.health" in names, code)
ok("the good app tool and the schema-less one are offered", f"app_{slug}.search_pages" in names
   and f"app_{slug}.list_all" in names, app_names)
ok("an array-typed tool, odd properties, an odd required list, and a name too long are left off",
   not any(x in n for n in app_names for x in ("bulk_lookup", "odd_props", "odd_required", "xxxx")), app_names)
ok("...and so is an ask-first tool whose inputs aren't named fields", not any("make_many" in n for n in app_names),
   app_names)
ok("every tool in the list, the box's and the apps', has an object input schema and a name of at most 64",
   all(t["inputSchema"].get("type") == "object" and len(t["name"]) <= 64 for t in listed),
   [t["name"] for t in listed if t["inputSchema"].get("type") != "object" or len(t["name"]) > 64])
ok("a read seat's list is whole too", lists["read"][0] == 200 and f"app_{slug}.search_pages"
   in {t["name"] for t in lists["read"][1]})
left = gateway.skipped(slug)
ok("the box knows which it left off, and why", set(left) == {"bulk_lookup", "odd_props", "odd_required", "x" * 48,
                                                             "make_many"}, left)

row = sources._app_row(slug, apps[slug], opened=True)
ok("the app's row on Data sources names each one left off, with why", row.count("Your AI isn&#x27;t offered this one")
   == 5 and "too long" in row and "named fields" in row, row[:400])
ok("...and not the ones that are offered", "Search pages" in row and row.count("t offered this one") == 5)

print("\nALL APP-TOOL LIST CHECKS PASS" if not _failed else f"\n{_failed} APP-TOOL LIST CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
