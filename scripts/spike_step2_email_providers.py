"""SPIKE — Step 2: Apollo exec-name + domain → VERIFIED email, via FREE providers.

Proves the two best free Step-2 providers (Tomba, Prospeo) can take what Apollo's
free tier gives us (first name + company + domain) and return a VERIFIED owner email
— so we can run Step 2 on free quotas with a switcher rotating between them, no paid
Hunter until there's ROI.

Both providers: free API (no credit card), domain-search endpoint (domain → emails
with names/titles/verification), free/built-in verification, no charge on no-result.

USAGE — sign up free (no CC) + paste keys, then run:
  Tomba   (https://tomba.io, Dashboard → API):   TOMBA_API_KEY=ta_...  TOMBA_API_SECRET=ts_...
  Prospeo (https://prospeo.io, Settings → API):  PROSPEO_API_KEY=...
  python scripts/spike_step2_email_providers.py

It runs DOMAIN-SEARCH (we have first name + domain, not last name → domain-search,
match the owner by first name / owner-ish title — the same shape as the live ladder),
then reports the verified owner email + confidence for each provider that has a key.
Read-only against the vendors; spends 1 free credit per domain per provider.
"""
import json
import os
import re
import urllib.error
import urllib.request

# What Apollo's free tier hands Step 2 (first name + company + resolved domain).
# Two real SC-HVAC targets so we can eyeball the match.
CASES = [
    {"first": "Mike", "company": "Howell HVAC", "domain": "howellhvacllc.com"},
    {"first": "Gordon", "company": "Berkeley Heating and Air", "domain": "berkeleyheating.com"},
]
OWNERISH = re.compile(r"owner|president|ceo|founder|principal|partner|proprietor|vice president", re.I)


def _http(method, url, headers, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=30)
        return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {"_raw": "unparseable"}
    except Exception as e:  # noqa: BLE001
        return None, {"_err": f"{type(e).__name__}: {e}"}


def _pick_owner(first, emails):
    """Mirror the live verified-only pick: prefer a VALID address whose first name
    matches the Apollo owner, else a valid owner-ish position. Return (email, status, conf)."""
    def valid(e): return str(e.get("status") or e.get("verification") or "").lower() in ("valid", "deliverable", "verified") or e.get("verified") is True
    fn = (first or "").lower()
    # 1) first-name match + valid
    for e in emails:
        if valid(e) and (e.get("first_name") or "").lower() == fn:
            return e.get("email") or e.get("value"), "valid", e.get("confidence") or e.get("score")
    # 2) owner-ish position + valid
    for e in emails:
        if valid(e) and OWNERISH.search(e.get("position") or ""):
            return e.get("email") or e.get("value"), "valid", e.get("confidence") or e.get("score")
    return None, None, None


def spike_tomba(case):
    key, secret = os.environ.get("TOMBA_API_KEY"), os.environ.get("TOMBA_API_SECRET")
    if not (key and secret):
        return "SKIP (set TOMBA_API_KEY + TOMBA_API_SECRET — free signup, no CC, at tomba.io)"
    st, d = _http("GET", f"https://api.tomba.io/v1/domain-search?domain={case['domain']}",
                  {"X-Tomba-Key": key, "X-Tomba-Secret": secret})
    if st != 200:
        return f"HTTP {st}: {str(d)[:160]}"
    emails = (d.get("data") or {}).get("emails") or d.get("emails") or []
    norm = [{"email": e.get("email"), "first_name": e.get("first_name"),
             "position": e.get("position"),
             "status": (e.get("verification") or {}).get("status") if isinstance(e.get("verification"), dict) else e.get("status")}
            for e in emails]
    email, status, conf = _pick_owner(case["first"], norm)
    return f"{len(emails)} emails → owner={email} status={status} conf={conf}"


def spike_prospeo(case):
    key = os.environ.get("PROSPEO_API_KEY")
    if not key:
        return "SKIP (set PROSPEO_API_KEY — free signup, no CC, at prospeo.io)"
    st, d = _http("POST", "https://api.prospeo.io/domain-search",
                  {"Content-Type": "application/json", "X-KEY": key},
                  {"company": case["company"], "domain": case["domain"], "limit": 10})
    if st != 200:
        return f"HTTP {st}: {str(d)[:160]}"
    resp = d.get("response") or d.get("data") or d
    emails = resp.get("email_list") or resp.get("emails") or []
    norm = [{"email": e.get("email") or e.get("value"), "first_name": e.get("first_name"),
             "position": e.get("job_title") or e.get("position"),
             "status": e.get("email_status") or e.get("status")} for e in emails]
    email, status, conf = _pick_owner(case["first"], norm)
    return f"{len(emails)} emails → owner={email} status={status} conf={conf}"


if __name__ == "__main__":
    print("=" * 78)
    print("STEP-2 SPIKE — free providers turning Apollo's (first name + domain) → verified email")
    print("=" * 78)
    for case in CASES:
        print(f"\n▶ {case['company']} ({case['domain']}) — Apollo gave first name '{case['first']}'")
        print(f"   Tomba   : {spike_tomba(case)}")
        print(f"   Prospeo : {spike_prospeo(case)}")
    print("\n(Read-only. ~1 free credit per domain per keyed provider. 0 emails sent.)")
