"""`scripts/demo.py` is a buyer's first look at the machine, so it must never lie and never
touch their data. It runs the REAL create / dedupe / compliance / sequence code, so the risk
is not that it crashes — it is that it drifts from the contract it narrates."""
import os
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
fails = 0


def ok(name, cond, detail=""):
    global fails
    print(("  ok   " if cond else "  FAIL ") + name + (f"  — {detail}" if detail and not cond else ""))
    fails += 0 if cond else 1


real_db = pathlib.Path(tempfile.mkdtemp()) / "the-buyers.db"
env = {k: v for k, v in os.environ.items() if not k.startswith("AIOS_")}
env.update({"AIOS_DB_PATH": str(real_db), "AIOS_HERMETIC_TEST": "1"})
r = subprocess.run([sys.executable, str(ROOT / "scripts/demo.py")], capture_output=True, text=True,
                   env=env, cwd=ROOT, timeout=180)
out = re.sub(r"\x1b\[[0-9;]*m", "", r.stdout)

ok("it runs on a box with no keys at all", r.returncode == 0, (r.stderr or out)[-300:])
# THE POINT: a demo that writes into the buyer's database is not a demo.
ok("it never touches the buyer's database", not real_db.exists(), str(real_db))
ok("it says so on screen", "Your data is untouched" in out)

ok("dedupe is shown as identity by source row", "already here" in out and "5 leads from 6 rows" in out, out[:200])
# The contract it narrates: a suppressed person is KEPT and stamped, not refused. This assertion
# is why the file exists — the first draft claimed 'REFUSED' and the code has never done that.
ok("a re-imported opt-out is stored with status suppressed, not refused",
   "stored with status suppressed" in out, [l for l in out.splitlines() if "stored with status" in l])
ok("… and it explains WHY keeping the row is right", "KEPT, not dropped" in out)
ok("the follow-up is scheduled by the real sequencer", "touch 2 scheduled" in out)
ok("every unset key is named with what it buys",
   all(k in out for k in ("ANTHROPIC_API_KEY", "GTM_SENDER_NAME", "DASHBOARD_BASE_URL", "daily_drip")))
ok("it points at the doctor for the real box", "scripts/doctor.py" in out)
# Fabricated data only — example.com is reserved by RFC 2606 and can never be a real business.
addrs = re.findall(r"[\w.+-]+@[\w.-]+", out)
ok(f"every address shown is unroutable by RFC ({len(addrs)} checked)",
   addrs and all(a.endswith(".example.com") for a in addrs), str([a for a in addrs if not a.endswith(".example.com")][:3]))
# It cannot send even by accident: the sender is never imported, and the promise is on screen.
# (A text search for "sent" is the wrong test — the demo legitimately says "Nothing is sent".)
src = (ROOT / "scripts/demo.py").read_text()
ok("the demo never imports the sender or any vendor client",
   not re.search(r"import .*\b(sending|resend_client|instantly_client|places_client|apollo_client)\b", src),
   [l for l in src.splitlines() if "import" in l and "client" in l][:3])
ok("the no-send promise is on screen", "Nothing is sent" in out)
print(f"{fails} FAILED" if fails else "all ok")
sys.exit(1 if fails else 0)
