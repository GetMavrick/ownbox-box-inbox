"""The demo zone's gate: Caddy issues a certificate only when /tls/ask says yes — and it says yes
only for this box's own address or an industry a pack claims, under the configured zone."""
import os, sys, pathlib, tempfile
os.environ.setdefault("AIOS_HERMETIC_TEST", "1")
os.environ.setdefault("AIOS_DB_PATH", tempfile.mkdtemp() + "/t.db")
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import dash, packs
from core.config import get_config

_failed = 0
def ok(label, cond, detail=""):
    global _failed
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  — {detail}"))
    if not cond: _failed += 1

cfg = get_config(); dsec = cfg.setdefault("dash", {}); old_zone = dsec.get("demo_zone"); old_base = os.environ.get("DASHBOARD_BASE_URL")
claimed = sorted({str(m.get("industry", "")).lower() for m in packs.discover() if m.get("industry")})
ok("at least one pack claims an industry (the gate needs a real label to admit)", bool(claimed), str(claimed))
label = claimed[0] if claimed else "med-spas"
try:
    os.environ["DASHBOARD_BASE_URL"] = "https://aios.example.com"
    dsec["demo_zone"] = ""
    ok("zone unset: the box's own address is admitted", dash.ask_tls("aios.example.com"))
    ok("zone unset: an industry label under any zone is refused", not dash.ask_tls(f"{label}.nlvl.co"))
    dsec["demo_zone"] = "nlvl.co"
    ok(f"zone set: {label}.nlvl.co (an industry a pack claims) is admitted", dash.ask_tls(f"{label}.nlvl.co"))
    ok("upper case and a port are normalised", dash.ask_tls(f"{label.upper()}.NLVL.CO:443"))
    for bad, why in [("acme.nlvl.co", "a client name, not an industry"), ("nlvl.co", "the bare zone"), (f"{label}.example.com", "another zone"),
                     (f"deep.{label}.nlvl.co", "a deeper subdomain"), ("probe-no-such-industry.nlvl.co", "an unclaimed label"), ("203.0.113.7", "an IP"),
                     ("", "empty"), (f"{label}.nlvl.co/../x", "junk"), (f"{label}.nlvl.co evil", "whitespace"), ("*.nlvl.co", "the wildcard itself")]:
        ok(f"refused: {why}", not dash.ask_tls(bad), bad)
    ok("the box's own address is still admitted with the zone set", dash.ask_tls("aios.example.com"))
    # a machine that is code, not a pack, is named in dash.labels (owner 2026-09-05: an app for every machine)
    old_labels = dsec.get("labels"); dsec["labels"] = {"health-and-wellness": ["job:"], "Bad Label": ["job:"]}
    try:
        ok("a dash.labels label is admitted", dash.ask_tls("health-and-wellness.nlvl.co"))
        ok("a malformed dash.labels entry admits nothing", not dash.ask_tls("bad label.nlvl.co") and not dash.ask_tls("bad-label.nlvl.co"))
        ok("an unclaimed label is still refused with the map set", not dash.ask_tls("probe-no-such-industry.nlvl.co"))
    finally:
        if old_labels is None: dsec.pop("labels", None)
        else: dsec["labels"] = old_labels
    # the HTTP surface Caddy actually calls
    from core.dispatch import app
    c = app.test_client()
    r1 = c.get(f"/tls/ask?domain={label}.nlvl.co"); r2 = c.get("/tls/ask?domain=evil.nlvl.co"); r3 = c.get("/tls/ask")
    ok("GET /tls/ask → 200 for a claimed industry", r1.status_code == 200, r1.status_code)
    ok("GET /tls/ask → 403 for an unknown label, body reveals nothing", r2.status_code == 403 and r2.get_data(as_text=True).strip() == "no", (r2.status_code, r2.get_data(as_text=True)[:40]))
    ok("GET /tls/ask without a domain → 403", r3.status_code == 403, r3.status_code)
    r4 = c.get(f"/tls/ask?domain={label}.nlvl.co", headers={"X-Forwarded-For": "203.0.113.9"})
    ok("a request that came through the proxy (X-Forwarded-For) is refused even for a claimed label — only Caddy's own call is answered", r4.status_code == 403, r4.status_code)
    r5 = c.get(f"/tls/ask?domain={label}.nlvl.co", headers={"X-Forwarded-Host": "evil.example"})
    ok("…and one carrying X-Forwarded-Host", r5.status_code == 403, r5.status_code)
finally:
    dsec["demo_zone"] = old_zone
    if old_base is None: os.environ.pop("DASHBOARD_BASE_URL", None)
    else: os.environ["DASHBOARD_BASE_URL"] = old_base
print(f"{_failed} FAILED" if _failed else "all ok"); sys.exit(1 if _failed else 0)
