#!/usr/bin/env python3
"""Is this box's DNS ready? Ask, do not guess — and get told exactly what to add.

  python scripts/dns_check.py --dash health-wellness.ownbox.app            # one of our demo apps
  python scripts/dns_check.py --dash acme.ownbox.app --send acme-roofing.com  # a sold box

TWO DIFFERENT DOMAINS, AND ONLY ONE OF THEM COSTS ANYTHING:

  --dash   where this box answers. Sending needs a public https address because every email
           carries an unsubscribe link built from it, and the login rides the same host.
           This does NOT have to be the client's domain and does NOT have to be bought: one
           A record on ownbox.app — the owner's rule (2026-09-21, superseding the 2026-09-05
           nlvl.co ruling): <industry>.ownbox.app for one of our demo apps,
           <client>.ownbox.app for a sold box — points at this droplet and Caddy
           issues the certificate by itself. Thirty seconds, no client involvement, no purchase,
           ten times over. scripts/dns_add.py makes the record from the owner's machine.

  --send   the domain the mail comes FROM. This one must be the client's own — a cold email
           from your domain on their behalf either lands in spam or misrepresents who is
           writing — but they already have one, because they are a business with a website.
           Nothing is bought here either: three records added to a domain they own.

  AND THE SEND SIDE IS NOT NEEDED ON DAY ONE. A box that finds businesses, scores them and
  drafts emails is a complete demonstration; sending is the step you arm later, once their
  DNS is in place. Handing over a working box does not have to wait for their registrar.

Checks over DNS-over-HTTPS so it works on a bare droplet with no `dig` installed.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DOH = "https://cloudflare-dns.com/dns-query"
OK, BAD, DIM, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def lookup(name: str, rtype: str, *, fetch=None) -> list[str]:
    """Answers for name/type, or [] — never raises, because a checker that dies on a typo is
    worse than one that says 'nothing there'."""
    if fetch is not None:                       # tests inject; nothing here touches the network
        return fetch(name, rtype)
    q = f"{DOH}?{urllib.parse.urlencode({'name': name, 'type': rtype})}"
    req = urllib.request.Request(q, headers={"Accept": "application/dns-json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
    except Exception:
        return []
    return [a.get("data", "").strip('"') for a in (data.get("Answer") or [])]


def check(label: str, got: list[str], want: str | None, fix: str) -> bool:
    """`want` is a substring that must appear in one of the answers; None means 'anything'."""
    hit = bool(got) and (want is None or any(want.lower() in g.lower() for g in got))
    print(f"  {(OK + 'ready ' + OFF) if hit else (BAD + 'MISSING' + OFF)} {label}")
    if hit:
        print(f"          {DIM}{got[0][:96]}{OFF}")
    else:
        for line in fix.splitlines():
            print(f"          {line}")
    return hit


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dash", help="where this box answers, e.g. health-wellness.ownbox.app (demo) or acme.ownbox.app (sold box)")
    ap.add_argument("--send", help="the client's own domain that mail comes FROM")
    ap.add_argument("--ip", help="this droplet's public IP, to confirm the A record points here")
    a = ap.parse_args(argv)
    if not a.dash and not a.send:
        ap.print_help()
        return 2
    ready = True

    if a.dash:
        print(f"\nTHE BOX'S ADDRESS — {a.dash}")
        got = lookup(a.dash, "A")
        ok_a = check("A record exists", got, None,
                     f"add an A record:  {a.dash}  →  <this droplet's IP>\n"
                     f"on a domain YOU own. Nothing is bought and the client does nothing.")
        if ok_a and a.ip:
            here = a.ip in got
            print(f"  {(OK + 'ready ' + OFF) if here else (BAD + 'WRONG  ' + OFF)} it points at this box"
                  + ("" if here else f"\n          it resolves to {got[0]}, this box is {a.ip}"))
            ready &= here
        elif ok_a:
            print(f"          {DIM}pass --ip to confirm it points at THIS droplet{OFF}")
        ready &= ok_a
        print(f"  {DIM}Caddy issues the certificate itself once this resolves — nothing to buy,"
              f"\n  nothing to install, and you own the domain so no client is in the loop.{OFF}")

    if a.send:
        d = a.send
        print(f"\nTHE SENDING DOMAIN — {d}  {DIM}(the client's own; three records on a domain they already have){OFF}")
        ready &= check("SPF — says this sender may send as them", lookup(d, "TXT"), "v=spf1",
                       f'add a TXT record on {d}:\n'
                       f'  v=spf1 include:amazonses.com ~all      (Resend sends via SES)')
        ready &= check("DMARC — tells inboxes what to do with fakes", lookup(f"_dmarc.{d}", "TXT"), "v=DMARC1",
                       f'add a TXT record on _dmarc.{d}:\n'
                       f'  v=DMARC1; p=none; rua=mailto:postmaster@{d}\n'
                       f'  p=none first — watch, then tighten. Starting at p=reject blocks your own mail.')
        ready &= check("DKIM — signs each message as really theirs", lookup(f"resend._domainkey.{d}", "TXT"), "p=",
                       f'Resend dashboard → Domains → add {d}. It gives you the exact CNAME/TXT;\n'
                       f'  paste it at their registrar. This is the only one we cannot write for you,\n'
                       f'  because the key is generated per domain.')
        print(f"  {DIM}None of this is needed to hand the box over. Discovery and drafting work today;"
              f"\n  arm sending after these three go green.{OFF}")

    print(f"\n{'READY' if ready else 'NOT READY'} — "
          + ("this box can be handed over and can send." if ready else
             "the box can still be handed over; the missing lines above only gate SENDING."))
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
