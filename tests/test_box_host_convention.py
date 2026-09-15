"""Every machine app answers at <name>.nlvl.co — the owner's rule, asserted, not remembered.

Owner, 2026-09-05 (OSDev5's session): "When we automatically create these mini applications
that have the two tabs, I would like for them to be on a subdomain of this domain: nlvl.co.
such as: health-wellness.nlvl.co". Asked which name: the INDUSTRY for one of our own demo apps,
the CLIENT for a sold box. Asked who makes the record: him, from his machine, before bootstrap —
the Cloudflare token never sits on a client's VPS.

What this proves, by running the tools rather than reading them:
  1. dns_add.py defaults to zone nlvl.co and, in --dry-run, produces exactly <name>.nlvl.co
     for both shapes of name — with no token in the environment, so the test is hermetic.
  2. dns_add.py refuses a label that is not a DNS-safe slug (the industry / client name is
     typed by a person; "Health Wellness" must fail before it reaches Cloudflare).
  3. No operator-facing example in the three tools names a host outside the rule — the
     placeholders that used to be there (nlvl.co, acme.example.com, aios.yourdomain.com)
     taught the wrong convention on the one screen a person reads before typing.
  4. bootstrap's own header says the record is made from the owner's machine first.

Box-aware: a buyer's box does not ship dns_add.py (it is the owner's tool); where the file
is absent there is nothing to enforce and the suite says so.
"""
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"

DNS_ADD = ROOT / "scripts" / "dns_add.py"
DNS_CHECK = ROOT / "scripts" / "dns_check.py"
BOOTSTRAP = ROOT / "scripts" / "bootstrap.sh"
STALE = re.compile(r"runmav\.com|acme\.example\.com|aios\.yourdomain\.com")
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def dns_add(*args):
    env = {k: v for k, v in os.environ.items() if k not in ("CLOUDFLARE_API_TOKEN", "AIOS_DNS_ZONE")}
    return subprocess.run([sys.executable, str(DNS_ADD), *args], cwd=ROOT, capture_output=True, text=True, env=env)


def test_dns_add_speaks_the_rule():
    if not DNS_ADD.exists():
        ok("dns_add.py not shipped here (a box) — nothing to enforce", True)
        return
    r = dns_add("health-wellness", "203.0.113.7", "--dry-run")
    ok("a demo app: health-wellness -> health-wellness.nlvl.co (default zone, no token needed)",
       r.returncode == 0 and "health-wellness.nlvl.co" in r.stdout, r.stdout + r.stderr)
    r = dns_add("acme", "203.0.113.7", "--dry-run")
    ok("a sold box: acme -> acme.nlvl.co", r.returncode == 0 and "acme.nlvl.co" in r.stdout)
    r = dns_add("Health Wellness", "203.0.113.7", "--dry-run")
    ok("a label with a space or capitals is refused before any API call", r.returncode != 0 and "not a usable label" in r.stdout + r.stderr)
    r = dns_add("health-wellness", "203.0.113.999", "--dry-run")
    ok("a bad IPv4 is refused", r.returncode != 0 and "IPv4" in r.stdout + r.stderr)
    src = DNS_ADD.read_text()
    ok("the default zone is nlvl.co in the source, not only in the environment", '"nlvl.co"' in src)
    ok("dns_add's help names both shapes of name", "industry" in src and "client" in src)


def test_no_tool_teaches_another_host():
    present = [p for p in (DNS_ADD, DNS_CHECK, BOOTSTRAP) if p.exists()]
    if not present:
        ok("none of the operator tools ship here — nothing to enforce", True)
        return
    for p in present:
        hits = sorted(set(STALE.findall(p.read_text())))
        ok(f"{p.name}: no stale example host", not hits, str(hits))
    if BOOTSTRAP.exists():
        head = BOOTSTRAP.read_text()[:4000]
        ok("bootstrap's header example uses <name>.nlvl.co", ".nlvl.co" in head)
        ok("bootstrap's header says the record is made from the owner's machine, never this box",
           "dns_add.py" in head and "never" in head)
    if DNS_CHECK.exists():
        head = DNS_CHECK.read_text()[:3000]
        ok("dns_check's examples show a demo app and a sold box on nlvl.co",
           "health-wellness.nlvl.co" in head and "acme.nlvl.co" in head)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(f"\n{name}")
            fn()
    print(f"\n{'ALL OK' if not _failed else f'{_failed} FAILED'}")
    sys.exit(1 if _failed else 0)
