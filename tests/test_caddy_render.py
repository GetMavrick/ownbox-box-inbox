"""The Caddyfile the box serves — with and without the demo zone — and the owner's one DNS line."""
import os, sys, pathlib, subprocess, re
os.environ.setdefault("AIOS_HERMETIC_TEST", "1")
ROOT = pathlib.Path(__file__).resolve().parent.parent
_failed = 0
def ok(label, cond, detail=""):
    global _failed
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  — {detail}"))
    if not cond: _failed += 1
R = ROOT / "scripts/render_caddyfile.sh"
one = subprocess.run(["bash", str(R), "aios.example.com"], capture_output=True, text=True)
ok("one argument renders today's file: the box's address, proxy to loopback, no placeholders", one.returncode == 0 and "aios.example.com {" in one.stdout and "reverse_proxy 127.0.0.1:8000" in one.stdout and "{$" not in one.stdout and "on_demand" not in one.stdout, one.stdout[:200] + one.stderr[:200])
two = subprocess.run(["bash", str(R), "aios.example.com", "nlvl.co"], capture_output=True, text=True)
t = two.stdout
ok("with the zone: the global on_demand_tls block comes FIRST and asks this box", two.returncode == 0 and t.lstrip().startswith("#") and t.index("on_demand_tls") < t.index("aios.example.com {") and "ask http://127.0.0.1:8000/tls/ask" in t, t[:300])
ok("with the zone: *.nlvl.co block with tls on_demand and the same loopback proxy", "*.nlvl.co {" in t and re.search(r"\*\.nlvl\.co \{[^}]*tls \{\s*on_demand\s*\}", t, re.S) and t.count("reverse_proxy 127.0.0.1:8000") == 2, t[-300:])
ok("with the zone: the box's own block is unchanged", "aios.example.com {" in t and "{$" not in t)
ok("no zone argument → no wildcard block, no global block (a sold box)", "*." not in one.stdout and "on_demand_tls" not in one.stdout)
ok("expose.sh renders through the renderer and validates before moving the live file", all(x in (ROOT / "scripts/expose.sh").read_text() for x in ("render_caddyfile.sh", "caddy validate", "Caddyfile.candidate", "untouched")))
env = {**os.environ, "CLOUDFLARE_API_TOKEN": "", "AIOS_DNS_ZONE": ""}
w = subprocess.run([sys.executable, str(ROOT / "scripts/dns_add.py"), "--wildcard", "203.0.113.7", "--dry-run"], capture_output=True, text=True, env=env)
ok("dns_add --wildcard --dry-run: the owner's one record, *.nlvl.co → ip", w.returncode == 0 and "*.nlvl.co" in w.stdout and "203.0.113.7" in w.stdout, w.stdout[-200:] + w.stderr[-200:])
s = subprocess.run([sys.executable, str(ROOT / "scripts/dns_add.py"), "*", "203.0.113.7", "--dry-run"], capture_output=True, text=True, env=env)
ok("a bare '*' name without --wildcard is refused", s.returncode != 0, s.stdout[-120:])
n = subprocess.run([sys.executable, str(ROOT / "scripts/dns_add.py"), "203.0.113.7", "--dry-run"], capture_output=True, text=True, env=env)
ok("no name and no --wildcard is refused, naming both options", n.returncode != 0 and "wildcard" in (n.stdout + n.stderr), (n.stdout + n.stderr)[-160:])
print(f"{_failed} FAILED" if _failed else "all ok"); sys.exit(1 if _failed else 0)
