"""An old tool name still answers, through the same gates (core/connector/tools.py `OLD_PREFIX`).

Measured 2026-10-02: #1743 dropped the `aios.` prefix from every public tool name, and a client that listed the
tools before then (OSDev1's own connector to the owner's box) got "this box does not serve 'aios.core.health'" on
EVERY call, the evening before the owner films his investor demo.

  · `aios.<machine>.<tool>` reaches `<machine>.<tool>` and answers the same (HTTP and MCP both call tools.call);
  · the capability and role gates still apply to the old name: it is a spelling, never a way round a gate;
  · the audit row keeps the name the client sent, so we can see when old names stop arriving;
  · tools/list and the manifest show only the new names;
  · a truly unknown name, with or without the prefix, is still refused in the same words.

Run: python tests/test_old_tool_names_still_answer.py
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = tempfile.mkdtemp() + "/old_names.db"
os.environ["DISPATCH_BEARER_TOKEN"] = "the-wide-token"

from core import state  # noqa: E402

state.init_db()

from core.connector import manifest, seats, tools  # noqa: E402
from core import box_tools  # noqa: E402,F401 — registers core.health, as the box does at boot

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -- {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


tools.register("echo", fn=lambda who: {"said": who}, description="Repeat a word.", machine="old_names",
               min_role="read", capability="read:reports", args={"who": {"type": "string", "required": True}})
tools.register("act_only", fn=lambda: {"done": True}, description="Needs an act seat.", machine="old_names",
               min_role="act", capability="read:reports")
tools.register("proposes", fn=lambda: {"queued": True}, description="Needs write:proposals.", machine="old_names",
               min_role="act", capability="write:proposals")

read_id, _ = seats.mint("reader", "read")
act_id, _ = seats.mint("operator", "act")
with state.connect() as c:
    READ = dict(c.execute("SELECT * FROM seats WHERE id = ?", (read_id,)).fetchone())
    ACT = dict(c.execute("SELECT * FROM seats WHERE id = ?", (act_id,)).fetchone())


def audit(seat_id):
    with state.connect() as c:
        return [(r["tool"], r["outcome"]) for r in c.execute(
            "SELECT tool, outcome FROM seat_actions WHERE seat_id = ? ORDER BY rowid", (seat_id,))]


print("\nan old name answers the same as the new one —")
new, s_new = tools.call("old_names.echo", {"who": "hi"}, READ)
old, s_old = tools.call("aios.old_names.echo", {"who": "hi"}, READ)
ok("the new name answers", s_new == 200 and new.get("result") == {"said": "hi"}, str(new))
ok("...and the old name answers the same", s_old == 200 and old.get("result") == {"said": "hi"}, str(old))
ok("...and says the name it was called by", old.get("tool") == "aios.old_names.echo", str(old))
ok("the box's own health answers by its old name", tools.call("aios.core.health", {}, READ)[1] == 200)
ok("...and so does the manifest", tools.call("aios.core.manifest", {}, READ)[1] == 200)

print("\nthe gates still apply to the old name —")
body, status = tools.call("aios.old_names.act_only", {}, READ)
ok("a read seat can't reach an act tool through the old name", status == 403 and body.get("error") == "forbidden",
   str(body))
body, status = tools.call("aios.old_names.proposes", {}, READ)
ok("...nor a capability it doesn't hold", status == 403 and "write:proposals" in body.get("message", ""), str(body))
ok("an act seat reaches it by the old name", tools.call("aios.old_names.act_only", {}, ACT)[1] == 200)
body, status = tools.call("aios.old_names.echo", {"who": "hi", "nope": 1}, READ)
ok("arguments are still checked", status != 200 and body.get("error") == "unknown_args", str(body))

print("\nthe audit keeps the name the client sent —")
rows = audit(read_id)
ok("the old-name call is recorded under the old name", ("aios.old_names.echo", "ok") in rows, str(rows))
ok("...and the refused one too", ("aios.old_names.act_only", "denied") in rows, str(rows))

print("\nonly the new names are shown —")
listed = [t["name"] for t in tools.visible_to(READ)]
shown = [t.get("name") for t in manifest.build(seat=READ).get("tools", [])]
ok("nothing listed carries the old prefix", listed and not any(n.startswith("aios.") for n in listed), str(listed[:5]))
ok("...and the new name is listed", "old_names.echo" in listed, str(listed[:8]))
ok("the manifest shows no old name either", shown and not any(str(n).startswith("aios.") for n in shown), str(shown[:5]))

print("\na truly unknown name is still refused, in the same words —")
for n in ("old_names.nope", "aios.old_names.nope", "aios.aios.old_names.echo"):
    body, status = tools.call(n, {}, READ)
    ok(f"{n} is refused", status == 404 and body.get("error") == "unknown_tool"
       and f"this box does not serve {n!r}" in body.get("message", ""), str(body))

print()
if _failed:
    print(f"{_failed} FAILED")
    sys.exit(1)
print("all passed")
