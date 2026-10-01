"""Permissions a person understands: every tool on a box has a plain title, and nothing says AIOS.

Owner, 2026-10-01, looking at Claude's connector screen for his own box: *"human readable permissions
that a human understands what they are granting. I don't think all of them should start with AIOS."*
Assigned by OSDev1 the same day. What is measured, on the box as it boots, over the real MCP endpoint:
  * every tool Ownbox ships has a title it was GIVEN (not one made from its name), in plain words:
    no dots, no underscores, never AIOS
  * no two tools read the same, so a person can tell them apart on the permission screen
  * tools/list sends the title in both places a client looks: `title` and `annotations.title`
  * no tool id starts with `aios.`, and the server calls itself Ownbox in every handshake
  * the manifest and health answers never say aios, including the box type of a box with several machines
  * a custom machine's tool with no title still loads, with one made from its name (SDK 1 holds)
  * a title that is not plain words, or one already taken, is refused at registration

Run: python tests/test_permissions_read_plainly.py
"""
import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = tempfile.mkdtemp() + "/perm.db"
os.environ["DISPATCH_BEARER_TOKEN"] = "the-wide-token"

from core import state  # noqa: E402

state.init_db()
from core.connector import manifest, seats, tools  # noqa: E402
from core import dispatch  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


client = dispatch.app.test_client()
_, act_cred = seats.mint("the owner's agent", "act")
H = {"Authorization": f"Bearer {act_cred}", "Content-Type": "application/json",
     "Accept": "application/json, text/event-stream"}


MCP_PATH = "/api/v1/mcp"


def rpc(method, params=None):
    r = client.post(MCP_PATH, headers=H, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                                                          "params": params or {}}))
    return r.get_json()


registry = dict(tools._REGISTRY)
print("the titles Ownbox ships")
ok("the box boots with tools to check", len(registry) >= 8, str(sorted(registry)))
ok("EVERY TOOL OWNBOX SHIPS WAS GIVEN A TITLE, not one made from its name",
   all(s["title_given"] for s in registry.values()),
   str(sorted(n for n, s in registry.items() if not s["title_given"])))
ok("...in plain words: no dots, no underscores, never AIOS",
   all(tools.plain_title(s["title"]) for s in registry.values()),
   str([s["title"] for s in registry.values() if not tools.plain_title(s["title"])]))
titles = [s["title"] for s in registry.values()]
ok("NO TWO PERMISSIONS READ THE SAME", len(titles) == len(set(titles)), str(sorted(titles)))
ok("NO TOOL ID STARTS WITH aios.", not any(n.lower().startswith("aios.") for n in registry), str(sorted(registry)))
for name, want in (("core.health", "See whether your box is running"), ("inbox.search", "Search your inbox"),
                   ("morning_review.report_day", "Read a day's Morning Review")):
    ok(f"{name} reads as '{want}'", registry.get(name, {}).get("title") == want,
       str(registry.get(name, {}).get("title")))

print("over MCP, as the owner's AI sees it")
listed = rpc("tools/list")["result"]["tools"]
ok("tools/list carries every tool's title, and the same title under annotations",
   listed and all(t.get("title") and t["annotations"].get("title") == t["title"] for t in listed),
   str([(t["name"], t.get("title")) for t in listed]))
init = rpc("initialize", {"protocolVersion": "2025-06-18"})["result"]
disc = rpc("server/discover")["result"]
ok("the server calls itself ownbox in the handshake", init["serverInfo"]["name"] == "ownbox"
   and disc["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "ownbox", str(init["serverInfo"]))
ok("its instructions say Ownbox", "Ownbox" in disc["instructions"], disc["instructions"])
seen = json.dumps([listed, init, disc]).lower()
ok("NOTHING THE OWNER'S AI IS SHOWN SAYS aios: not an id, a title, the name or the instructions",
   "aios" not in seen, [w for w in ("aios",) if w in seen])
man = rpc("tools/call", {"name": "core.manifest", "arguments": {}})["result"]["structuredContent"]
ok("the manifest gives each tool its title too", all(t.get("title") for t in man["tools"]), str(man["tools"][:1]))
health = rpc("tools/call", {"name": "core.health", "arguments": {}})["result"]["structuredContent"]
_real_mods = manifest._modules
manifest._modules = lambda: ["marketing.lead_machine", "marketing.customer_voice.inbox"]
several = manifest.box_type()
manifest._modules = _real_mods
ok("a box with several machines is not called aios either", several == "multi_machine", several)
ok("...and NEITHER THE MANIFEST NOR HEALTH SAYS aios in what it answers",
   "aios" not in json.dumps([man, health]).lower(),
   [k for k, v in {**man, **health}.items() if "aios" in json.dumps(v).lower()])

print("registration")
tools.register("latest_notes", fn=lambda: {}, description="d", machine="job_tracker", capability="read:notes")
ok("A CUSTOM MACHINE'S TOOL WITH NO TITLE STILL LOADS (SDK 1 holds), titled from its name",
   tools._REGISTRY["job_tracker.latest_notes"]["title"] == "Latest notes")
for bad, why in (("inbox.search", "a dotted id"), ("Read_notes", "an underscore"), ("Ask AIOS", "AIOS"),
                 ("search your inbox", "no capital"), ("Search your inbox", "a title already taken")):
    try:
        tools.register(f"t_{abs(hash(bad)) % 10**6}", fn=lambda: {}, description="d", machine="job_tracker",
                       capability="read:notes", title=bad)
        ok(f"a title with {why} is refused", False, bad)
    except ValueError:
        ok(f"a title with {why} is refused", True)

print("\n" + ("ALL OK" if not _failed else f"{_failed} FAILED"))
sys.exit(1 if _failed else 0)
