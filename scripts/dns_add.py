#!/usr/bin/env python3
"""Point a client's subdomain at their box — one command, no registrar visit.

  export CLOUDFLARE_API_TOKEN=...            # a token scoped to Zone:DNS:Edit on nlvl.co
  python scripts/dns_add.py acme 203.0.113.7                 # a sold box: <client>.nlvl.co
  python scripts/dns_add.py health-wellness 203.0.113.7      # one of our demo apps: <industry>.nlvl.co
  python scripts/dns_add.py acme 203.0.113.7 --zone nlvl.co --dry-run

Creates (or updates) an A record  acme.nlvl.co → 203.0.113.7. THE NAME IS THE RULE (owner, 2026-09-05):
a sold box is named for the client, one of our own demo apps for its industry, so two roofers never
collide and a demo reads as what it is. Run this from YOUR machine, before bootstrap; the token never
sits on a client's VPS. Caddy on that droplet then
issues its own certificate, so the client's box is reachable over https with nobody at the
registrar and nothing bought. Ten clients is ten runs of this.

WHY A SUBDOMAIN OF A DOMAIN YOU OWN, and not theirs: the box needs a public https address
before it can send at all — every email carries an unsubscribe link built from it — and
waiting on ten different people's registrars is what turns a one-day onboarding into a
three-week one. Their own domain is for the FROM address, which is a separate, later step.

WHEN THEY MOVE TO THEIR OWN DOMAIN — AND THE ONE RULE THAT MATTERS:
  Point their host at the same box, change DASHBOARD_BASE_URL, restart. Then **leave this
  record in place forever.** Every email already sent carries an unsubscribe link on the OLD
  host; deleting the record breaks one-click opt-out for every message in every inbox, which
  is both the law and the thing that keeps a domain out of spam folders. The record costs
  nothing to keep. Keep it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

API = "https://api.cloudflare.com/client/v4"


def call(method: str, path: str, token: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"{API}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"success": False, "errors": [{"message": f"HTTP {e.code}"}]}
    except Exception as e:
        return {"success": False, "errors": [{"message": str(e)}]}


def fail(msg: str) -> int:
    print(f"✗ {msg}")
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", nargs="?", default=None, help="the label — a client ('acme' → acme.nlvl.co) for a sold box, an industry ('health-wellness') for a demo app")
    ap.add_argument("ip", help="the droplet's public IPv4")
    ap.add_argument("--wildcard", action="store_true",
                    help="the demo box's ONE record: *.<zone> → ip (owner, once; every <industry>.<zone> then resolves)")
    ap.add_argument("--zone", default=os.environ.get("AIOS_DNS_ZONE") or "nlvl.co")   # an EMPTY env var must not make "*." records
    ap.add_argument("--dry-run", action="store_true", help="say what it would do, change nothing")
    a = ap.parse_args(argv)
    if a.name == "*" and not a.wildcard:
        return fail("'*' is the demo box's wildcard record — say so: --wildcard <ip>")
    if a.wildcard:
        if a.name not in (None, "*"):
            return fail("--wildcard takes no name (it IS the name: *)")
        a.name = "*"
    elif not a.name:
        return fail("a name is required (a client for a sold box, an industry for a demo app) — or --wildcard for the demo box's one record")

    if a.name != "*" and not re.fullmatch(r"[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?", a.name):
        return fail(f"'{a.name}' is not a usable label — lowercase letters, digits and hyphens")
    if not re.fullmatch(r"(\d{1,3}\.){3}\d{1,3}", a.ip) or any(int(o) > 255 for o in a.ip.split(".")):
        return fail(f"'{a.ip}' is not an IPv4 address")
    fqdn = f"{a.name}.{a.zone}"

    if a.dry_run:
        print(f"would create  A  {fqdn}  →  {a.ip}  (proxied off, so Caddy can answer the ACME challenge)")
        return 0

    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not token:
        return fail("CLOUDFLARE_API_TOKEN is not set.\n"
                    "  Cloudflare → My Profile → API Tokens → Create → Edit zone DNS, scoped to "
                    f"{a.zone}.\n  export CLOUDFLARE_API_TOKEN=... then run this again. "
                    "The token stays in your shell; this script never writes it anywhere.")

    z = call("GET", f"/zones?name={a.zone}", token)
    if not z.get("success") or not z.get("result"):
        return fail(f"cannot see zone {a.zone}: "
                    f"{'; '.join(e.get('message', '?') for e in z.get('errors') or []) or 'no such zone for this token'}")
    zone_id = z["result"][0]["id"]

    # PROXIED MUST BE OFF. Behind Cloudflare's proxy the droplet never sees the ACME challenge,
    # so Caddy cannot get a certificate and the box is unreachable over https — which is the one
    # thing this record exists to provide.
    rec = {"type": "A", "name": fqdn, "content": a.ip, "ttl": 300, "proxied": False}
    existing = call("GET", f"/zones/{zone_id}/dns_records?type=A&name={fqdn}", token)
    hits = existing.get("result") or []
    if hits:
        cur = hits[0]
        if cur.get("content") == a.ip and cur.get("proxied") is False:
            print(f"✓ {fqdn} already points at {a.ip} — nothing to do")
            return 0
        r = call("PUT", f"/zones/{zone_id}/dns_records/{cur['id']}", token, rec)
        verb = f"updated (was {cur.get('content')})"
    else:
        r = call("POST", f"/zones/{zone_id}/dns_records", token, rec)
        verb = "created"
    if not r.get("success"):
        return fail("; ".join(e.get("message", "?") for e in r.get("errors") or []) or "unknown error")

    print(f"✓ {verb}:  A  {fqdn}  →  {a.ip}")
    print(f"  next:  bash /opt/aios/scripts/bootstrap.sh --buyer \"<client>\" --order <ref> --host {fqdn}")
    print(f"  then:  python scripts/dns_check.py --dash {fqdn} --ip {a.ip}")
    print("  keep this record forever, even if they move to their own domain — every email already")
    print("  sent carries an unsubscribe link on this host.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
