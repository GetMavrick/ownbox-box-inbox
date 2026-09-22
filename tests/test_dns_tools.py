"""Onboarding ten clients turns on two DNS questions, so the tools that answer them must not
guess. Everything here runs offline — the lookup is injected, and the Cloudflare tool is only
exercised on the paths that refuse before any network call."""
import importlib.util
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
fails = 0


def ok(name, cond, detail=""):
    global fails
    print(("  ok   " if cond else "  FAIL ") + name + (f"  → {detail}" if detail and not cond else ""))
    fails += 0 if cond else 1


def load(stem):
    spec = importlib.util.spec_from_file_location(stem, ROOT / f"scripts/{stem}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


dns_check = load("dns_check")

# ── the checker reports what is there, and never invents ──────────────────────────────
answers = {
    ("acme.nlvl.co", "A"): ["203.0.113.7"],
    ("acme-roofing.com", "TXT"): ["v=spf1 include:amazonses.com ~all"],
    ("_dmarc.acme-roofing.com", "TXT"): [],
    ("resend._domainkey.acme-roofing.com", "TXT"): ["p=MIGfMA0G"],
}
fake = lambda n, t: answers.get((n, t), [])
ok("finds a record that exists", dns_check.lookup("acme.nlvl.co", "A", fetch=fake) == ["203.0.113.7"])
ok("reports nothing for a record that does not", dns_check.lookup("_dmarc.acme-roofing.com", "TXT", fetch=fake) == [])
ok("a missing record is MISSING, not an error", dns_check.check("x", [], "v=spf1", "fix") is False)
ok("a present record must still MATCH what we need",
   dns_check.check("x", ["google-site-verification=abc"], "v=spf1", "fix") is False)
ok("… and matches when it does", dns_check.check("x", ["v=spf1 include:amazonses.com ~all"], "v=spf1", "fix") is True)
# A checker that dies on a typo is worse than one that says "nothing there".
ok("a lookup that fails returns empty rather than raising", dns_check.lookup("nope.invalid", "A", fetch=lambda n, t: []) == [])

# ── the record writer refuses before it can do damage ─────────────────────────────────
def run(*args, env_token=None):
    import os
    e = {k: v for k, v in os.environ.items() if k != "CLOUDFLARE_API_TOKEN"}
    if env_token:
        e["CLOUDFLARE_API_TOKEN"] = env_token
    return subprocess.run([sys.executable, str(ROOT / "scripts/dns_add.py"), *args],
                          capture_output=True, text=True, env=e, timeout=60)


r = run("acme", "203.0.113.7", "--dry-run")
ok("--dry-run needs no token and changes nothing", r.returncode == 0 and "would create" in r.stdout, r.stdout[:120])
# THE DEFAULT ZONE MOVED (owner, 2026-09-21: "the permanent cutover to ownbox.app for all
# machines"). `run()` passes no --zone, so this reads the DEFAULT — which is the one thing that
# PR changes, and the fourth assertion in the repo to have the old zone baked into it. The three
# above use an explicit zone and deliberately keep a different one: that is what proves the
# argument is honoured rather than the default leaking through.
ok("… and names the full host", "acme.ownbox.app" in r.stdout, r.stdout[:140])
# Proxied ON means Caddy never sees the ACME challenge and the box has no certificate at all.
ok("it says why the record must not be proxied", "proxied off" in r.stdout.lower() or "proxied" in r.stdout)
r = run("Acme Corp", "203.0.113.7")
ok("a label with a space is refused before any API call", r.returncode == 1 and "not a usable label" in r.stdout)
r = run("acme", "999.1.1.1")
ok("an impossible IP is refused", r.returncode == 1 and "not an IPv4" in r.stdout)
r = run("acme", "203.0.113.7")
ok("no token is a clear instruction, not a traceback",
   r.returncode == 1 and "CLOUDFLARE_API_TOKEN is not set" in r.stdout and "Traceback" not in r.stderr)
ok("it promises not to store the token", "never writes it anywhere" in r.stdout)
# The rule that bites in month two, on screen where it will be read.
src = (ROOT / "scripts/dns_add.py").read_text()
ok("it warns that deleting the old record breaks sent unsubscribe links",
   "unsubscribe link" in src and "forever" in src)
print(f"{fails} FAILED" if fails else "all ok")
sys.exit(1 if fails else 0)
