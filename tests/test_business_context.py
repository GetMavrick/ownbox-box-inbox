"""The business context: one home in core, and the light scan (#1957 C0 and C1; OSDev1, 2026-10-04).

Owner, 2026-10-04: "These people need to have a valuable machine on day one. Not a dumb box." and "The basic business
context should be part of the base machine. Add-on machines should draw from it instead of gathering their own."

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * a field accepts what the screen can't offer (a fourth goal, an industry not on the list, a plan with no month);
  * a website is stored as anything but https://host;
  * the AEO Machine's old website or competitors stop reading through, or get copied or deleted;
  * the light scan reads a personal mail domain, or turns a home page into anything but a SUGGESTION;
  * a suggestion overwrites what a person typed, or fills a field without a person's yes;
  * the scan gives up before the buyer has a business address, or never stops trying;
  * anything outside core writes the business settings (one home);
  * the owner's four example industries leave the list.

NO NETWORK: the scan's fetch is handed in.

Run: python tests/test_business_context.py
"""
from __future__ import annotations

import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "business_context.db")

from core import state  # noqa: E402

state.init_db()
from core import box_settings  # noqa: E402
from core import business_context as bc  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -- {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def refused(fn) -> str:
    try:
        fn()
    except ValueError as e:
        return str(e)
    return ""


print("test_one_home")
ok("the owner's four example industries lead the list",
   bc.INDUSTRIES[:4] == ("med spa", "gym and fitness", "e-commerce", "consulting"), bc.INDUSTRIES[:4])
g = bc.get()
ok("a fresh box knows nothing, and says so with empty fields, never a guess",
   g["website"] == "" and g["goals"] == [] and g["suggested"] == {} and set(g) == set(bc.FIELDS), g)
ok("a website is stored as https://host", bc.put("website", "www.Glow-MedSpa.example/about", by="owner")
   == "https://www.glow-medspa.example")
ok("...and a non-address is refused in words",
   "doesn't look like a website" in refused(lambda: bc.put("website", "not a site", by="owner")))
ok("up to three goals", bc.put("goals", ["more bookings", "better reviews"], by="owner") == ["more bookings",
                                                                                            "better reviews"])
ok("...a fourth is refused in words",
   "Pick up to 3" in refused(lambda: bc.put("goals", list(bc.GOALS[:4]), by="owner")))
ok("a goal not on the list is refused", "from the list" in refused(lambda: bc.put("goals", ["world peace"], by="owner")))
ok("an industry not on the list is refused", "on the list" in refused(lambda: bc.put("industry", "roofing", by="o")))
ok("what you sell keeps names and prices",
   bc.put("sells", [{"name": "Botox", "price": "$12 a unit"}, "Hydrafacial"], by="owner")
   == [{"name": "Botox", "price": "$12 a unit"}, {"name": "Hydrafacial", "price": ""}])
ok("what to push must be something you sell", bc.put("push", ["botox"], by="owner") == ["botox"]
   and "from what you sell" in refused(lambda: bc.put("push", ["Teeth whitening"], by="owner")))
ok("a plan needs a month", bc.put("coming", [{"what": "Spring skin package", "month": "2027-03"}], by="owner")
   == [{"what": "Spring skin package", "month": "2027-03"}]
   and "month" in refused(lambda: bc.put("coming", [{"what": "Gift cards", "month": "soon"}], by="owner")))
ok("a profile line with no source is dropped: nothing invented",
   bc.put("profile", [{"line": "Open Saturdays.", "source": "https://glow.example/hours"}, {"line": "Best in town."}],
          by="full scan") == [{"line": "Open Saturdays.", "source": "https://glow.example/hours", "field": ""}])
ok("a field the box doesn't keep is refused", "no business field" in refused(lambda: bc.put("phone", "1", by="o")))
ok("every change says who made it", "who made it" in refused(lambda: bc.put("name", "Glow", by="")))

print("\ntest_the_aeo_machines_old_keys_read_through")
box_settings.put(bc.NS, "website", "", set_by="test")
box_settings.put("seo", "site_url", "https://www.ownbox.io/articles", set_by="test")
box_settings.put("seo", "competitors", "Acme Spa\nRival Aesthetics", set_by="test")
g = bc.get()
ok("with no website of its own, the context reads the AEO Machine's", g["website"] == "https://www.ownbox.io", g["website"])
ok("...and its competitors", g["competitors"] == ["Acme Spa", "Rival Aesthetics"], g["competitors"])
bc.put("website", "glow-medspa.example", by="owner")
ok("once a website is put, the context's own wins", bc.website() == "https://glow-medspa.example")
ok("...and the AEO Machine's key is never copied over or deleted",
   box_settings.get("seo", "site_url") == "https://www.ownbox.io/articles")

print("\ntest_the_light_scan")
HOME = """<!doctype html><html><head><title>Glow Med Spa | Botox & Facials in Austin</title>
<meta name="description" content="Botox, fillers and facials in South Austin. Saturday appointments book a week ahead.">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"MedicalSpa","name":"Glow Med Spa",
"address":{"@type":"PostalAddress","streetAddress":"12 Main St","addressLocality":"Austin","addressRegion":"TX"},
"openingHoursSpecification":[{"dayOfWeek":["Monday","Tuesday"],"opens":"09:00","closes":"18:00"}],
"sameAs":["https://www.instagram.com/glowmedspa/","https://www.facebook.com/sharer/sharer.php?u=x"]}</script>
</head><body><a href="https://www.tiktok.com/@glowmedspa">TikTok</a><a href="/book">Book</a></body></html>"""
asked = []


def fetch(url):
    asked.append(url)
    return HOME if url.startswith("https://glow-medspa.example") else ""


ok("a personal mail address is never scanned", bc.light_scan("ava@gmail.com", fetch=fetch)["ok"] is False
   and asked == [])
got = bc.light_scan("ava@glow-medspa.example", fetch=fetch)
s = got.get("suggested") or {}
ok("the sign-in's domain is read, home page only", asked == ["https://glow-medspa.example/"], asked)
ok("its name, description, address and hours come from what the page says",
   s.get("name") == "Glow Med Spa" and s.get("description", "").startswith("Botox, fillers")
   and s.get("area") == "12 Main St, Austin, TX" and s.get("hours") == "Monday, Tuesday 09:00-18:00", s)
ok("its own social profiles, never a share link", s.get("socials") == ["https://www.instagram.com/glowmedspa",
                                                                       "https://www.tiktok.com/@glowmedspa"], s.get("socials"))
ok("it is kept as a suggestion only: no field filled without a yes",
   bc.get()["suggested"].get("name") == "Glow Med Spa" and bc.get()["name"] == "")
bc.put("hours", "By appointment", by="owner")
filled = bc.confirm_suggested(by="owner")
g = bc.get()
ok("a person's yes fills the empty fields", g["name"] == "Glow Med Spa" and g["area"] == "12 Main St, Austin, TX"
   and "name" in filled, filled)
ok("...and never overwrites what someone typed", g["hours"] == "By appointment")
ok("a page that says nothing about a business suggests nothing",
   bc.read_home_page("<html><body>hello</body></html>", "https://x.example") == {})
ok("a broken page never raises", bc.read_home_page("<script type='application/ld+json'>{bad", "https://x.example") == {})
asked.clear()
ok("www. is tried when the bare domain doesn't answer",
   bc.light_scan("lee@other.example", fetch=lambda u: asked.append(u) or "")["ok"] is False
   and asked == ["https://other.example/", "https://www.other.example/"], asked)

box_settings.put(bc.NS, "website", "", set_by="test")
bc.put("suggested", {"website": "https://glow-medspa.example", "name": "Glow Med Spa"}, by="light scan")
bc.reject_suggested(by="owner")
asked.clear()
again = bc.light_scan("ava@glow-medspa.example", fetch=fetch)
ok("'No, it isn't' clears the suggestion, and the scan never offers that site again",
   bc.get()["suggested"] == {} and again["ok"] is False and asked == [] and "isn't their website" in again["why"],
   (again, asked))
box_settings.put(bc.NS, "_rejected", [], set_by="test")

print("\ntest_the_scan_runs_once_and_waits_for_a_business_address")
box_settings.put(bc.NS, "website", "", set_by="test")
box_settings.put(bc.NS, "suggested", {}, set_by="test")
box_settings.put("seo", "site_url", "", set_by="test")
owner = state.owner_user()
with state.connect() as c:
    c.execute("UPDATE users SET email = ? WHERE id = ?", ("owner@gmail.com", owner["id"]))
ok("no business address yet: the scan waits and spends no try",
   bc.scan_if_needed()["status"] == "no_business_email" and not box_settings.get(bc.NS, "_scan_tries"))
with state.connect() as c:
    c.execute("UPDATE users SET email = ? WHERE id = ?", ("ava@unreachable-spa.example", owner["id"]))
real = bc.light_scan
bc.light_scan = lambda email=None, fetch=None: {"ok": False, "why": "no answer"}
runs = [bc.scan_if_needed()["status"] for _ in range(5)]
bc.light_scan = real
ok("a site that never answers is tried three times, then left alone",
   runs == ["none", "none", "none", "gave_up", "gave_up"], runs)
bc.put("website", "glow-medspa.example", by="owner")
ok("a known website stops it for good", bc.scan_if_needed()["status"] == "known")

print("\ntest_one_home_one_writer")
offenders = []
for p in list(ROOT.glob("core/**/*.py")) + list(ROOT.glob("marketing/**/*.py")) + list(ROOT.glob("my/**/*.py")):
    if p.name == "business_context.py":
        continue
    src = p.read_text(errors="ignore")
    if re.search(r"box_settings\.(put|get)\(\s*[\"']business[\"']", src):
        offenders.append(str(p.relative_to(ROOT)))
ok("nothing outside core/business_context.py reads or writes the business settings directly", not offenders, offenders)
self_check = 'box_settings.put("business", "website", "x")'
ok("...and that scan sees a direct write when there is one (self-proof)",
   re.search(r"box_settings\.(put|get)\(\s*[\"']business[\"']", self_check) is not None)

print("\nALL BUSINESS-CONTEXT CHECKS PASS" if not _failed else f"\n{_failed} BUSINESS-CONTEXT CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
