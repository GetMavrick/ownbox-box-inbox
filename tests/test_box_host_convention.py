"""Every machine app answers at <name>.ownbox.app — the owner's rule, asserted, not remembered.

SUPERSEDED, AND THE OLD RULING IS KEPT BECAUSE IT EXPLAINS THE SHAPE. Owner, 2026-09-05
(OSDev5's session): "When we automatically create these mini applications that have the two tabs,
I would like for them to be on a subdomain of this domain: nlvl.co. such as:
health-wellness.nlvl.co". Owner, 2026-09-21, twice and unprompted: "I thought we were using
ownbox.app for client machines!!" and then "Yes make the permanent cutover to ownbox.app for all
machines". The LATEST ruling governs, so the zone is ownbox.app and nlvl.co is now the wrong
answer everywhere — including on the hand-built machines this file is about, which were the last
place still teaching the old one.

Everything else from 2026-09-05 still stands. Which name: the INDUSTRY for one of our own demo
apps, the CLIENT for a sold box. Who makes the record: him, from his machine, before bootstrap —
the Cloudflare token never sits on a client's VPS. The provisioner already builds every SOLD box
on ownbox.app (provisioner/userdata.py BOXES_DOMAIN); this is the hand-built path catching up.

What this proves, by running the tools rather than reading them:
  1. dns_add.py defaults to zone ownbox.app and, in --dry-run, produces exactly <name>.ownbox.app
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
    ok("a demo app: health-wellness -> health-wellness.ownbox.app (default zone, no token needed)",
       r.returncode == 0 and "health-wellness.ownbox.app" in r.stdout, r.stdout + r.stderr)
    r = dns_add("acme", "203.0.113.7", "--dry-run")
    ok("a sold box: acme -> acme.ownbox.app", r.returncode == 0 and "acme.ownbox.app" in r.stdout)
    r = dns_add("Health Wellness", "203.0.113.7", "--dry-run")
    ok("a label with a space or capitals is refused before any API call", r.returncode != 0 and "not a usable label" in r.stdout + r.stderr)
    r = dns_add("health-wellness", "203.0.113.999", "--dry-run")
    ok("a bad IPv4 is refused", r.returncode != 0 and "IPv4" in r.stdout + r.stderr)
    src = DNS_ADD.read_text()
    ok("the default zone is ownbox.app in the source, not only in the environment",
       '"ownbox.app"' in src and '"nlvl.co"' not in src, "nlvl.co still appears as a default")
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
        ok("bootstrap's header example uses <name>.ownbox.app",
           ".ownbox.app" in head and ".nlvl.co" not in head, head[:200])
        ok("bootstrap's header says the record is made from the owner's machine, never this box",
           "dns_add.py" in head and "never" in head)
    if DNS_CHECK.exists():
        head = DNS_CHECK.read_text()[:3000]
        ok("dns_check's examples show a demo app and a sold box on ownbox.app",
           "health-wellness.ownbox.app" in head and "acme.ownbox.app" in head)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(f"\n{name}")
            fn()
    print(f"\n{'ALL OK' if not _failed else f'{_failed} FAILED'}")
    sys.exit(1 if _failed else 0)
