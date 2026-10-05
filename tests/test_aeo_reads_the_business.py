"""The AEO Machine reads the business's website and competitors from core (#1957 C5; OSDev1, 2026-10-04).

Owner, 2026-10-04: "The basic business context should be part of the base machine. Add-on machines should draw from
it instead of gathering their own." The website and competitors live once, in core/business_context.py; the AEO
Machine reads them there and saves them there. Its old keys stay where they are (storage keeps its name: a copy to a
new key splits the data when an update rolls back) and are read through only while the context has nothing.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * a box that never saved to the business context reading anything but what it read before;
  * the website typed on Your Business not reaching the AEO writer, the publisher's article address or its one site;
  * AEO Settings, or an approved change, writing the website or competitors anywhere but the business context;
  * an emptied website coming back from the old key;
  * a website the context refuses being half saved, or reaching Approvals;
  * any AEO file writing site_url or competitors into its own namespace again.

Run: python tests/test_aeo_reads_the_business.py
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
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "aeo_reads_business.db")

from core import state  # noqa: E402

state.init_db()
from core import box_settings  # noqa: E402
from core import business_context as bc  # noqa: E402
from marketing.aeo_machine import app, proposals, publisher, settings, site  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -- {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


class Form(dict):
    pass


def form(**kw):
    f = Form({"site_url": "", "indexnow_key": "", "weekly_cap": "", "facts": "", "allowed_numbers": "",
              "never_words": "", "never_phrases": "", "competitors": ""})
    f.update(kw)
    return f


print("test_a_box_that_never_saved_there_reads_what_it_read_before")
box_settings.put("seo", "site_url", "https://www.glow-medspa.example", set_by="test")
box_settings.put("seo", "competitors", ["Acme Spa", "Rival Aesthetics"], set_by="test")
s = settings.get()
ok("the website is the machine's old one", s["site_url"] == "https://www.glow-medspa.example", s["site_url"])
ok("...and so are the competitors", list(s["competitors"]) == ["Acme Spa", "Rival Aesthetics"], s["competitors"])
ok("articles keep their address", publisher.article_url("botox-cost") ==
   "https://www.glow-medspa.example/articles/botox-cost")

print("\ntest_your_business_reaches_the_aeo_machine")
bc.put("website", "glow-austin.example", by="owner")
bc.put("competitors", ["Acme Spa", "Rival Aesthetics", "Smooth Skin Co"], by="owner")
s = settings.get()
ok("the website typed on Your Business is the one the AEO Machine writes for",
   s["site_url"] == "https://glow-austin.example" and site.site() == "glow-austin.example", (s["site_url"], site.site()))
ok("...and where its articles are published", publisher.article_url("x") == "https://glow-austin.example/articles/x")
ok("its competitors are the business's", settings.lists()["competitors"] == ("Acme Spa", "Rival Aesthetics",
                                                                            "Smooth Skin Co"))
ok("the machine's old keys are never copied over or deleted",
   box_settings.get("seo", "site_url") == "https://www.glow-medspa.example")

print("\ntest_aeo_settings_saves_to_the_business")
app._save(form(site_url="www.Glow-Downtown.example", competitors="Acme Spa\nNew Rival", weekly_cap="3"),
          user_id="owner-1")
ok("the website saved on AEO Settings is the business's", bc.website() == "https://www.glow-downtown.example")
ok("...the competitors too", bc.get()["competitors"] == ["Acme Spa", "New Rival"], bc.get()["competitors"])
ok("...and nothing new is written under the machine's own name",
   box_settings.get("seo", "site_url") == "https://www.glow-medspa.example"
   and box_settings.get("seo", "competitors") == ["Acme Spa", "Rival Aesthetics"])
ok("its own settings are still its own", settings.get()["weekly_cap"] == 3)
try:
    app._save(form(site_url="https://under_score.example", weekly_cap="5"), user_id="owner-1")
    refused = ""
except app._Refused as e:
    refused = str(e)
ok("a website the business context refuses is refused in words, and nothing is saved",
   "doesn't look like a website" in refused and settings.get()["weekly_cap"] == 3, refused)
app._save(form(site_url="", weekly_cap="3"), user_id="owner-1")
ok("an emptied website stays empty: the old key doesn't bring it back",
   bc.website() == "" and settings.get()["site_url"] == "" and not box_settings.get("seo", "site_url"),
   (bc.website(), box_settings.get("seo", "site_url")))
ok("...and emptied competitors stay empty", settings.get()["competitors"] == [], settings.get()["competitors"])

print("\ntest_an_approved_change_goes_to_the_business")
asked = proposals.propose_setting(name="site_url", value="glow-medspa.example")
ok("a change to the website is asked, in the business's form", asked.get("asked") is not False, asked)
proposals._run({"do": "setting", "name": "site_url", "arguments": {"change": "set to",
                                                                    "value": "https://glow-medspa.example"}})
ok("approved, it is the business's website", bc.website() == "https://glow-medspa.example", bc.website())
proposals._run({"do": "setting", "name": "competitors", "arguments": {"change": "add", "value": "Lux Lasers"}})
ok("an approved competitor is the business's", bc.get()["competitors"] == ["Lux Lasers"], bc.get()["competitors"])
ok("a website the context refuses never reaches Approvals",
   "error" in proposals.propose_setting(name="site_url", value="https://under_score.example"))

print("\ntest_one_home")
offenders = []
pat = re.compile(r"box_settings\.(put|clear)\([^)]*[\"'](site_url|competitors)[\"']")
for p in (ROOT / "marketing/aeo_machine").glob("*.py"):
    if pat.search(p.read_text()):
        offenders.append(p.name)
ok("no AEO file writes the website or competitors under its own name", not offenders, offenders)
ok("...and that scan sees one when there is one (self-proof)",
   pat.search('box_settings.put(settings.MACHINE, "site_url", site, set_by=SET_BY)') is not None)
real = bc.get
bc.get = lambda: (_ for _ in ()).throw(RuntimeError("store unreadable"))
box_settings.put("seo", "site_url", "https://www.glow-medspa.example", set_by="test")
ok("a business context that can't be read leaves the machine's old website, never an empty one",
   settings.get()["site_url"] == "https://www.glow-medspa.example")
bc.get = real

print("\ntest_the_suite_runs_in_ci")
if (ROOT / ".github").is_dir():                         # a box has no repository
    ok("test_aeo_reads_the_business is in the workflow's suite list",
       "test_aeo_reads_the_business \\" in (ROOT / ".github/workflows/tests.yml").read_text())

print("\nALL AEO-READS-THE-BUSINESS CHECKS PASS" if not _failed else f"\n{_failed} AEO-READS-THE-BUSINESS CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
