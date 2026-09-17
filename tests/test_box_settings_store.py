"""Where a buyer's settings live, and why it is not the settings file.

docs/PLAN_BOX_SETTINGS_AND_CONNECTIONS.md §5. A box already had two answers for "what is this
value": the tracked config, which is what it SHIPS with, and the untracked overlay, which is what an
OPERATOR pins. Neither is what a BUYER edits from a screen.

WHY NOT THE FILE — measured on 2026-09-17, and any one of the three settles it:

  1. `core.config.get_config` is `@functools.lru_cache(maxsize=1)`, so the file is read ONCE per
     process. A screen that writes it changes nothing until the box restarts: the buyer watches his
     change save, and it does not happen. This suite pins that fact directly, because it is the one
     a future "just write the yaml" patch would not think to check.
  2. The overlay's `_deep_merge` recurses into dicts and REPLACES everything else, so writing one
     key of a list drops the rest — `send_days: [mon]` wipes the other six days.
  3. The file has no author, and a box must answer who changed a setting.

THE READ ORDER IS THE CONTRACT: this person → this box → what it shipped with → the caller's
default. A key nobody has touched must read exactly as it did before this module existed, which is
what makes adopting it one key at a time safe rather than a flag day.

Run: python tests/test_box_settings_store.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "settings.db")
os.environ["DASH_TOKEN"] = "pw"
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"

from core import state  # noqa: E402

state.init_db()

from core import box_settings as S  # noqa: E402

FAILS = []
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ok(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        FAILS.append(label)


# ── 1. the read order ─────────────────────────────────────────────────────────────────────
print("\ntest_the_most_specific_answer_wins")

ok("an untouched key reads what the box SHIPPED with — nothing changes by adopting this",
   S.get("review", "hour_local") == 8, str(S.get("review", "hour_local")))
ok("...and a key nobody ships falls to the caller's default",
   S.get("nosuch", "thing", default="fallback") == "fallback")

S.put("review", "hour_local", 9, set_by="u-owner")
ok("the box's own answer beats what it shipped with", S.get("review", "hour_local") == 9)

S.put("review", "hour_local", 6, user_id="u-dana", set_by="u-dana")
ok("A PERSON'S ANSWER BEATS THE BOX'S, for that person",
   S.get("review", "hour_local", user_id="u-dana") == 6)
ok("...and leaves the box's answer alone for everyone else",
   S.get("review", "hour_local") == 9 and S.get("review", "hour_local", user_id="u-sam") == 9)


# ── 2. clearing falls back rather than pinning ────────────────────────────────────────────
print("\ntest_reset_to_default_means_fall_through_not_write_the_default_back")

ok("clearing a person's row returns them to the box's answer",
   S.clear("review", "hour_local", user_id="u-dana")
   and S.get("review", "hour_local", user_id="u-dana") == 9)
ok("clearing the box's row returns it to what it shipped with",
   S.clear("review", "hour_local") and S.get("review", "hour_local") == 8)
ok("...and clearing something already absent says so rather than pretending",
   S.clear("review", "hour_local") is False)
ok("THE SHIPPED VALUE WAS NEVER COPIED IN — that is what makes a later default change apply",
   S.describe("review", "hour_local")["source"] == "shipped",
   S.describe("review", "hour_local")["source"])


# ── 3. a value keeps its type ─────────────────────────────────────────────────────────────
print("\ntest_a_toggle_that_is_off_reads_as_off")

for value, name in ((False, "False"), (True, "True"), (0, "0"), ([], "an empty list"),
                    (["mon", "tue"], "a list"), ({"a": 1}, "a dict"), ("", "an empty string")):
    S.put("t", "v", value)
    got = S.get("t", "v")
    ok(f"{name} survives the round trip as itself", got == value and type(got) is type(value),
       f"{got!r} ({type(got).__name__})")

# THE BUG THIS PREVENTS, NAMED: `str(False)` is "False", which is truthy. A store that flattened
# values would give a screen a toggle that can never be switched off.
S.put("t", "off", False)
ok("...and the flattened-string bug cannot come back: a stored False is falsy",
   not S.get("t", "off"), repr(S.get("t", "off")))


# ── 4. who changed it ─────────────────────────────────────────────────────────────────────
print("\ntest_the_box_can_say_who_changed_a_setting")

S.put("review", "hour_local", 7, set_by="u-owner")
d = S.describe("review", "hour_local")
ok("the audit answer is there", d["set_by"] == "u-owner" and bool(d["set_at"]), str(d))
ok("...and the source says where the value in force came from", d["source"] == "box", str(d))

S.put("review", "hour_local", 5, user_id="u-dana", set_by="u-dana")
d = S.describe("review", "hour_local", user_id="u-dana")
ok("a person's own row reports as theirs, not the box's", d["source"] == "person", str(d))

# SET_BY AND USER_ID ARE DIFFERENT PEOPLE, and that is the case worth pinning: an owner setting a
# box-wide value writes user_id=None, set_by=<him>.
S.put("review", "hour_local", 4, user_id=None, set_by="u-owner")
d = S.describe("review", "hour_local")
ok("an owner changing a BOX value is recorded as him, on a row about nobody",
   d["source"] == "box" and d["set_by"] == "u-owner", str(d))


# ── 5. it never takes down the thing it configures ───────────────────────────────────────
print("\ntest_a_settings_store_that_throws_is_worse_than_no_settings")

_real = S.state.connect
S.state.connect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk gone"))
ok("an unreadable store falls through to the shipped value rather than raising",
   S.get("review", "hour_local") == 8, str(S.get("review", "hour_local")))
ok("...and describe answers too", isinstance(S.describe("review", "hour_local"), dict))
S.state.connect = _real

with state.connect() as c:
    # '' IS THE BOX-WIDE SCOPE, not NULL — see the schema comment. SQLite treats NULLs as distinct
    # inside a primary key, so a NULL scope made ON CONFLICT a no-op and every write appended a row
    # the reader never saw. This suite is what found that, by writing seven values to one key.
    c.execute("INSERT INTO box_settings (machine, key, user_id, value, set_at, set_by) "
              "VALUES ('bad','row','','not json',?,NULL)", (state._now(),))
ok("a row that is not JSON reads as its raw text, never as a crash",
   S.get("bad", "row") == "not json", repr(S.get("bad", "row")))


# ── 6. THE MEASUREMENT THAT DECIDES THE WHOLE DESIGN ─────────────────────────────────────
print("\ntest_writing_the_settings_file_would_not_have_worked")

# A FRESH INTERPRETER, because `get_config` is lru_cache(maxsize=1) and this suite has already
# called it — checking in-process would prove nothing, which is exactly how this trap survives.
_probe = textwrap.dedent('''
    import os, tempfile, yaml
    d = tempfile.mkdtemp(); p = os.path.join(d, "settings.yaml")
    os.environ["AIOS_CONFIGLOCAL_CONFIG_PATH"] = p
    open(p, "w").write("review:\\n  hour_local: 8\\n")
    from core.config import get_config
    before = (get_config().get("review") or {}).get("hour_local")
    open(p, "w").write("review:\\n  hour_local: 21\\n")          # a "settings screen" writes
    after = (get_config().get("review") or {}).get("hour_local")
    print(f"{before},{after}")
''')
out = subprocess.run([sys.executable, "-c", _probe], capture_output=True, text=True, cwd=ROOT)
line = (out.stdout or "").strip().splitlines()[-1] if out.stdout.strip() else ""
before, _, after = line.partition(",")
ok("the file is read once per process, so a write after boot changes NOTHING",
   before == "8" and after == "8", f"before={before!r} after={after!r} {out.stderr[-200:]}")

# And the list hazard, in the same fresh-interpreter way.
_probe2 = textwrap.dedent('''
    import os, tempfile
    d = tempfile.mkdtemp(); p = os.path.join(d, "s.yaml")
    os.environ["AIOS_CONFIGLOCAL_CONFIG_PATH"] = p
    open(p, "w").write("review:\\n  send_days: [mon]\\n")
    from core.config import get_config
    print(len((get_config().get("review") or {}).get("send_days") or []))
''')
out2 = subprocess.run([sys.executable, "-c", _probe2], capture_output=True, text=True, cwd=ROOT)
n = (out2.stdout or "").strip().splitlines()[-1] if out2.stdout.strip() else ""
ok("...and overlaying ONE day of a seven-day list leaves one day, not seven",
   n == "1", f"{n!r} {out2.stderr[-200:]}")

# The store has neither problem.
S.put("review", "send_days", ["mon"])
ok("THE STORE TAKES EFFECT IMMEDIATELY and keeps the list it was given",
   S.get("review", "send_days") == ["mon"])


# ── 7. it is core's, and it stays core's ─────────────────────────────────────────────────
print("\ntest_it_knows_no_machine_by_name")

import inspect  # noqa: E402

src = inspect.getsource(S)
for name in ("inbox", "customer_voice", "zernio", "resend", "notify"):
    ok(f"the store never names {name!r} — machines pass their own key",
       f'"{name}"' not in src and f"'{name}'" not in src)

# ── 8. the trap that produced the schema comment ─────────────────────────────────────────
print("\ntest_writing_the_same_key_twice_changes_it")

# THE BUG THIS SUITE CAUGHT, PINNED SO IT CANNOT RETURN. With NULL as the box-wide scope, SQLite's
# primary key treated every write as a new row (NULLs are distinct), the reader's fetchone() handed
# back whichever landed first, and a setting could be changed and would simply not change.
for n in (1, 2, 3):
    S.put("repeat", "k", n)
ok("the third write is what reads back", S.get("repeat", "k") == 3, str(S.get("repeat", "k")))
with state.connect() as c:
    rows = c.execute("SELECT COUNT(*) n FROM box_settings WHERE machine='repeat' AND key='k'"
                     ).fetchone()["n"]
ok("...and there is ONE row, not three — the upsert really upserts", rows == 1, str(rows))

for n in (1, 2):
    S.put("repeat", "k", n, user_id="u-dana")
with state.connect() as c:
    rows = c.execute("SELECT COUNT(*) n FROM box_settings WHERE machine='repeat' AND key='k'"
                     ).fetchone()["n"]
ok("a person's row is a SECOND row, not a replacement of the box's", rows == 2, str(rows))
ok("...and the two do not shadow each other",
   S.get("repeat", "k") == 3 and S.get("repeat", "k", user_id="u-dana") == 2)

print("\n— and this file cannot silently fall out of CI —")
import pathlib  # noqa: E402

_wf = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml"
if _wf.is_file():
    ok("test_box_settings_store is in the workflow's suite list",
       "test_box_settings_store" in _wf.read_text())
else:
    print("  --   no workflow file here (a box, not the repo) — nothing to check")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
