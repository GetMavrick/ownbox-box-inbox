"""Social Accounts you can manage (owner 10-04: "If I want to add additional accounts, such as WhatsApp or iMessage,
there is no way to do this. I should be able to edit settings, similar to how GSC functions"). His go: all four.

  1. each connected channel names its account, with Change and Disconnect;
  2. Add a channel: WhatsApp, which the box now reads, under Meta's 24-hour window;
  3. iMessage, said honestly: Apple lets no app outside Apple read or answer it;
  4. the Zernio account: Switch workspace, Replace key.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  · a connected channel does not say which account it reads, or offers no Change or Disconnect;
  · Disconnect on one channel stops another, removes the account from Zernio, or does not stop the poller reading it;
  · Change reads an account outside this workspace or platform, or the poller still reads the others on that platform;
  · WhatsApp is not offered, not read, or may be answered outside Meta's 24 hours;
  · iMessage is offered as if it could connect;
  · Switch workspace accepts a workspace from outside the account; Replace key keeps the old workspace or picks;
  · a member can change any of it;
  · a buyer sentence says "API key".
Run: python tests/test_social_accounts_you_can_manage.py
"""
from __future__ import annotations

import os
import pathlib
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "social.db")
os.environ["DISPATCH_BEARER_TOKEN"], os.environ["DASH_TOKEN"] = "bearer", "pw"
for k in ("ZERNIO_API_KEY", "ZERNIO_PROFILE_ID"):
    os.environ.pop(k, None)

from core import state  # noqa: E402

state.init_db()
from core import box_secrets as bs, dash, spaces  # noqa: E402
from core.dispatch import app  # noqa: E402
from core.vendors.zernio import transport as ztransport, verify as zverify  # noqa: E402
from marketing.customer_voice import app as voice  # noqa: E402
from marketing.customer_voice.inbox import channels, poller, window  # noqa: E402

FAILS: list[str] = []


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:900]}"))
    if not cond:
        FAILS.append(label)


spaces.get_config = lambda: {}                                    # type: ignore[assignment]
WORKSPACES = [{"_id": "ws_glow", "name": "Glow Med Spa"}, {"_id": "ws_side", "name": "Maria's side project"}]
ACCOUNTS = {"ws_glow": [{"_id": "acc_ig", "platform": "instagram", "username": "glowmedspa"},
                        {"_id": "acc_ig2", "platform": "instagram", "username": "glowmedspa_austin"},
                        {"_id": "acc_fb", "platform": "facebook", "displayName": "Glow Med Spa"}],
            "ws_side": [{"_id": "acc_side", "platform": "instagram", "username": "mariaskincare"}]}
CONNECTS: list = []
LISTED: list = []


class _SDK:
    def __init__(self, key):
        self.key = key
        sdk = self

        class Profiles:
            def list_profiles(self):
                return {"profiles": WORKSPACES}

        class Accounts:
            def list(self, profile_id=None):
                return {"accounts": ACCOUNTS.get(profile_id, [])}

        class Connect:
            def get_connect_url(self, platform, profile_id, **kw):
                CONNECTS.append((platform, profile_id))
                return {"authUrl": f"https://zernio.com/connect/{platform}"}

        class Inbox:
            def list_inbox_conversations(self, **kw):
                LISTED.append(kw.get("platform"))
                plat = kw.get("platform")
                return {"data": [{"id": f"zc-{plat}-{a['_id']}", "accountId": a["_id"], "platform": plat,
                                  "updatedTime": "2026-10-04T10:00:00Z"}
                                 for a in ACCOUNTS["ws_glow"] if a["platform"] == plat]}
        self.profiles, self.accounts, self.connect, self.messages = Profiles(), Accounts(), Connect(), Inbox()
        _ = sdk


ztransport.raw_client = lambda key, **kw: _SDK(key)               # type: ignore[assignment]
zverify.verify_key = lambda key: (True, "")                       # type: ignore[assignment]
bs.put_zernio("GLOW_KEY_ONE")
bs.put_zernio_profile("ws_glow")

owner = app.test_client()
owner.set_cookie("aios_session", dash.new_session(state.owner_user()["id"]), domain="localhost")
member = app.test_client()
member.set_cookie("aios_session", dash.new_session(state.add_user("sam@glowmedspa.com", name="Sam")["id"]),
                  domain="localhost")


def page(c=owner, url="/inbox/connect") -> str:
    return c.get(url).get_data(as_text=True)


print("\n1. each connected channel names its account, with Change and Disconnect")
body = page()
ok("Instagram says which accounts it reads", "Connected. Reading @glowmedspa, @glowmedspa_austin." in body, body)
ok("Messenger names its page", "Connected. Reading Glow Med Spa." in body, body)
ok("...each with Change and Disconnect", body.count(">Change</a>") == 2 and body.count(">Disconnect</button>") == 2)
ch = page(url="/inbox/connect/instagram/change")
ok("Change lists the Instagram accounts in this workspace, and a way to connect another",
   "@glowmedspa" in ch and "@glowmedspa_austin" in ch and "Read all 2" in ch
   and "Connect another Instagram account" in ch and "@mariaskincare" not in ch, ch)
owner.post("/inbox/connect", data={"do": "pick", "platform": "instagram", "account": "acc_ig2"})
ok("picking one reads only that one", channels.chosen() == {"instagram": "acc_ig2"}
   and "Connected. Reading @glowmedspa_austin." in page(), channels.chosen())
owner.post("/inbox/connect", data={"do": "pick", "platform": "instagram", "account": "acc_side"})
owner.post("/inbox/connect", data={"do": "pick", "platform": "facebook", "account": "acc_ig"})
ok("an account from another workspace, or another platform, is never picked",
   channels.chosen() == {"instagram": "acc_ig2"}, channels.chosen())

LISTED.clear()
from marketing.customer_voice.inbox import store  # noqa: E402

# WHAT GETS PAST THE POLLER'S ACCOUNT FILTER: the watermark is the first thing it asks of a conversation it will read,
# answered "unchanged" so the sweep stops there without fetching messages.
PAST: list = []
store.get_watermark = lambda space, zcid: (PAST.append(zcid), {"last_activity": "2026-10-04T10:00:00Z"})[1]
poller._vendor_intake_allowed = lambda: True                      # type: ignore[assignment]
poller.poll_sweep()
ok("THE POLLER READS ONLY THE PICKED INSTAGRAM ACCOUNT", "zc-instagram-acc_ig2" in PAST
   and "zc-instagram-acc_ig" not in PAST and "zc-facebook-acc_fb" in PAST, PAST)

owner.post("/inbox/connect", data={"do": "stop", "platform": "facebook"})
body = page()
ok("Disconnect on Messenger stops it, says so, and offers it back",
   channels.stopped() == {"facebook"} and "Disconnected: Ownbox is not reading Glow Med Spa" in body
   and "Read it again" in body, body)
ok("...Instagram is untouched", "Connected. Reading @glowmedspa_austin." in body)
LISTED.clear()
poller.poll_sweep()
ok("...and the poller no longer asks for Messenger at all", "facebook" not in LISTED and "instagram" in LISTED,
   LISTED)
owner.post("/inbox/connect", data={"do": "read", "platform": "facebook"})
ok("Read it again reads it again", channels.stopped() == set() and "Connected. Reading Glow Med Spa." in page())

print("\n2. Add a channel: WhatsApp, read by the box under Meta's 24 hours")
body = page()
ok("WhatsApp is offered with what it needs", "Connect WhatsApp" in body
   and "Needs a WhatsApp Business account." in body, body)
r = owner.get("/inbox/connect/whatsapp")
ok("...and its button goes into the vendor's own consent for WhatsApp", r.status_code == 303
   and r.headers["Location"] == "https://zernio.com/connect/whatsapp" and CONNECTS[-1] == ("whatsapp", "ws_glow"),
   (r.status_code, r.headers.get("Location"), CONNECTS[-1:]))
ok("the poller reads WhatsApp", ("whatsapp", "whatsapp") in [(c.vendor, c.key) for c in channels.POLLED]
   and "whatsapp" in LISTED, LISTED)
fresh = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
stale = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
ok("a WhatsApp reply inside 24 hours may go", window.decide("whatsapp", fresh)["decision"] == window.FREEFORM)
ok("...and outside them it BLOCKS: the box holds no approved templates",
   window.decide("whatsapp", stale)["decision"] == window.BLOCKED, window.decide("whatsapp", stale))
ok("...with Meta's own page cited", "developers.facebook.com/docs/whatsapp" in window._RULES["whatsapp"]["cite"])
ok("WhatsApp is named as a person writes it", window.pretty_platform("whatsapp") == "WhatsApp"
   and channels.NAMES["whatsapp"] == "WhatsApp")

print("\n3. iMessage, honestly")
ok("iMessage is listed, not connectable, and says why",
   "Apple does not let any app outside Apple read or answer iMessages" in body
   and "/inbox/connect/imessage" not in body and "Connect iMessage" not in body, body)
r = owner.get("/inbox/connect/imessage")
ok("...and its address goes nowhere", r.status_code == 303 and r.headers["Location"].endswith("/inbox/connect"))

print("\n4. the Zernio account: Switch workspace, Replace key")
ok("the workspace is named, with Switch workspace and Replace key",
   "Glow Med Spa: the channels above are the ones in it." in body and "Switch workspace" in body
   and "Replace key" in body and "Disconnect Zernio" in body, body)
sw = page(url="/inbox/connect?workspace=1")
ok("Switch workspace lists every workspace in the account, the one in use marked",
   "Glow Med Spa (in use)" in sw and "side project" in sw, sw)
owner.get("/inbox/connect?profile=ws_someone_else")
ok("a workspace from outside the account is ignored", bs.zernio_profile() == "ws_glow")
owner.get("/inbox/connect?profile=ws_side")
ok("switching takes the new workspace and forgets the picks made in the old one",
   bs.zernio_profile() == "ws_side" and channels.chosen() == {}, (bs.zernio_profile(), channels.chosen()))
ok("...and the page now reads the new workspace's accounts", "Connected. Reading @mariaskincare." in page())
owner.get("/inbox/connect?profile=ws_glow")
owner.post("/inbox/connect", data={"do": "pick", "platform": "instagram", "account": "acc_ig"})
rk = page(url="/inbox/connect?replace=1")
ok("Replace key shows the key form, and the old key keeps working meanwhile",
   "Replace your Zernio key." in rk and 'name="key"' in rk and "Keep the key I have" in rk
   and bs.zernio_key() == "GLOW_KEY_ONE", rk)
owner.post("/inbox/connect", data={"key": "GLOW_KEY_TWO"})
ok("a new key forgets the old workspace and the old picks",
   bs.zernio_key() == "GLOW_KEY_TWO" and bs.zernio_profile() == "" and channels.chosen() == {},
   (bs.zernio_profile(), channels.chosen()))
owner.post("/inbox/connect", data={"key": "GLOW_KEY_TWO"})
bs.put_zernio_profile("ws_glow")
bs.put_zernio("GLOW_KEY_TWO")
ok("...while the same key pasted again keeps its workspace", bs.zernio_profile() == "ws_glow")

print("\nthe owner's, and in the buyer's words")
for data in ({"do": "stop", "platform": "instagram"}, {"do": "pick", "platform": "instagram", "account": "acc_ig2"}):
    r = member.post("/inbox/connect", data=data)
    ok(f"a member cannot {data['do']}", r.status_code in (403, 200) and channels.stopped() == set()
       and channels.chosen().get("instagram") != "acc_ig2", (r.status_code, channels.stopped(), channels.chosen()))
r = member.get("/inbox/connect/instagram/change")
ok("...nor open Change", r.status_code in (403, 200) and "Read all" not in r.get_data(as_text=True))
def words(html: str) -> str:
    """What a person reads: the page's main column, without its styles, scripts or tags."""
    html = html.split("</nav>", 1)[-1]
    html = re.sub(r"<(style|script)\b.*?</\1>", " ", html, flags=re.S | re.I)
    return re.sub(r"<[^>]+>", " ", html)


said = words(page()) + words(page(url="/inbox/connect/instagram/change")) + words(rk)
ok("never 'API key'", "API key" not in said)

print()
print(f"{len(FAILS)} FAILED" if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
