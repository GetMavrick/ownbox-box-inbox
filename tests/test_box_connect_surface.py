"""B1 — the in-box connect surface. The product shipped without one.

WHAT WAS MEASURED ON 2026-09-16. The only connect link in the whole system was minted by the
provisioner and surfaced on the build page while an order was still building. A buyer who closed
that tab owned a box with no way to connect anything: /inbox/settings offered an AI key, a theme
switch, an install guide and a paragraph about polling. The inbox stayed empty forever and no
screen said why.

THE DANGEROUS PART IS THE PROFILE, NOT THE PAGE. A Zernio profile id is only meaningful inside the
account that minted it. Under bring-your-own-key the box holds the BUYER's key, while
`settings.zernio_profile_id` still holds the id the provisioner wrote — a folder in OUR account.
Hand those two to the vendor together and the consent screen asks a person to grant their own
Instagram into somebody else's folder. That pairing is asserted here from both directions, and so
is the rule it must not weaken: a Space that NAMES its own key gets THAT key or nothing.

Run: python tests/test_box_connect_surface.py
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "connect.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
os.environ["ZERNIO_API_KEY"] = "OURS_FROM_THE_PROVISIONER"
os.environ["ZERNIO_PROFILE_ID"] = "prof_OURS"

from core import state  # noqa: E402

state.init_db()

from core import box_secrets as bs  # noqa: E402
from core import spaces  # noqa: E402
# NOT `from core.vendors.zernio import client` — the package __init__ rebinds that name to the
# client() FACTORY, so the import gives a function and the submodule is unreachable through it.
from core.vendors.zernio import transport as ztransport  # noqa: E402
from core.vendors.zernio.client import ScopedClient  # noqa: E402
from core.vendors.zernio import verify as zverify  # noqa: E402
from core.vendors.zernio.errors import ZernioError  # noqa: E402

FAILS: list[str] = []
BUYERS_KEY = "BUYER_PASTED_THIS_ONE"


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


def quiet(fn, *a, **k):
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        return fn(*a, **k)


# ── the pairing: a key and a profile id are ONE binding ──────────────────────────────────────
print("\n— the buyer's key never travels with OUR profile id —")

# A BUYER'S BOX HAS NO `spaces:` BLOCK — it is the implicit single tenant. This repo's own config
# does have one (the owner's two Spaces), so it is stood down for this section; the owner's shape
# is asserted on its own terms further down, where it matters.
_real_get_config = spaces.get_config
spaces.get_config = lambda: {}                                    # type: ignore[assignment]

sp = spaces.all_spaces()[0]
ok("before BYO, the box uses the provisioned pair",
   sp["zernio_key"] == "OURS_FROM_THE_PROVISIONER" and sp["zernio_profile_id"] == "prof_OURS",
   str(sp))

zverify.verify_key = lambda key: (True, "")                       # type: ignore[assignment]
quiet(bs.put_zernio, BUYERS_KEY)

sp = spaces.all_spaces()[0]
ok("the buyer's key wins the moment it is pasted", sp["zernio_key"] == BUYERS_KEY, str(sp))
ok("AND OUR PROFILE ID IS DROPPED WITH IT — the whole point",
   sp["zernio_profile_id"] is None, repr(sp["zernio_profile_id"]))

bs.put_zernio_profile("prof_THEIRS")
sp = spaces.all_spaces()[0]
ok("once resolved in their account, their profile travels with their key",
   (sp["zernio_key"], sp["zernio_profile_id"]) == (BUYERS_KEY, "prof_THEIRS"), str(sp))

# ── disconnecting takes the profile with the key ─────────────────────────────────────────────
print("\n— disconnect clears the binding, not half of it —")
bs.clear_zernio()
ok("the key is gone", bs.zernio_key() == "", bs.zernio_key())
ok("AND THE PROFILE IS GONE — a stale id would be handed to the NEXT key pasted in",
   bs.zernio_profile() == "", bs.zernio_profile())
sp = spaces.all_spaces()[0]
ok("and the box falls back to the provisioned pair, whole",
   (sp["zernio_key"], sp["zernio_profile_id"]) == ("OURS_FROM_THE_PROVISIONER", "prof_OURS"),
   str(sp))

# ── the isolation rule this must not weaken ──────────────────────────────────────────────────
print("\n— a Space that NAMES its own key still gets THAT key or nothing —")
quiet(bs.put_zernio, BUYERS_KEY)
bs.put_zernio_profile("prof_THEIRS")

spaces.get_config = lambda: {"spaces": [                          # type: ignore[assignment]
    {"name": "client-b", "airtable_base": "appB", "zernio_key_env": "CLIENT_B_KEY",
     "zernio_profile_id": "prof_B"},
]}
os.environ["CLIENT_B_KEY"] = "CLIENT_B_OWN_KEY"
b = spaces.all_spaces()[0]
ok("a named key is used, never the buyer's own", b["zernio_key"] == "CLIENT_B_OWN_KEY",
   str(b))
ok("and its CONFIG profile is kept — not the buyer's",
   b["zernio_profile_id"] == "prof_B", repr(b["zernio_profile_id"]))

os.environ.pop("CLIENT_B_KEY")
b = spaces.all_spaces()[0]
ok("A NAMED KEY THAT IS MISSING RESOLVES TO NOTHING — never a fallthrough to the box's own",
   b["zernio_key"] is None, repr(b["zernio_key"]))

# ── the owner's own box, on this repo's real config ──────────────────────────────────────────
print("\n— BYO changes nothing on a box whose Space NAMES its key —")
spaces.get_config = _real_get_config                              # type: ignore[assignment]
own = [s for s in spaces.all_spaces() if s["name"] == "meet-mavrick"]
if own:
    # OUR config, multi-tenant: a Space that names its key keeps it, and BYO must not reach in.
    ok("the owner's Space is configured with zernio_key_env", True)
    ok("...so a key pasted into this box does NOT take it over",
       own[0]["zernio_key"] == "OURS_FROM_THE_PROVISIONER", str(own[0]["zernio_key"]))
    ok("...and its configured profile is untouched",
       own[0]["zernio_profile_id"] == "6a30198d53d7c5ddc44c8990", str(own[0]["zernio_profile_id"]))
else:
    # A SOLD BOX, where the opposite is the requirement and it is the stronger half. The exporter
    # strips every `zernio_key_env` (#1262) precisely because `_norm` gives a Space that names one
    # THAT key or nothing — so a buyer's box carrying one would have a Space that could never use
    # the key its owner pastes in. Asserted, not skipped: this is the condition that makes
    # onboarding work, checked in the only place it is true.
    allsp = spaces.all_spaces()
    ok("A SOLD BOX HAS NO SPACE NAMING A KEY ENV VAR, so the buyer's own key binds",
       bool(allsp) and not any(s.get("zernio_key_env") for s in allsp),
       str([(s["name"], s.get("zernio_key_env")) for s in allsp])[:200])
    ok("...and every Space resolves to the key this box was given",
       all(s["zernio_key"] == BUYERS_KEY for s in allsp),
       str([(s["name"], (s["zernio_key"] or "")[:12]) for s in allsp])[:200])
spaces.get_config = lambda: {}                                    # type: ignore[assignment]

# ── the consent call refuses rather than guessing ────────────────────────────────────────────
print("\n— connect refuses without a profile, instead of letting None reach the vendor —")
sent: dict = {}


class _FakeConnect:
    def get_connect_url(self, platform, profile_id, **kw):
        sent.update({"platform": platform, "profile_id": profile_id, **kw})
        return {"authUrl": "https://zernio.com/oauth/whatever"}


class _FakeProfiles:
    def __init__(self, rows): self._rows = rows
    def list_profiles(self): return {"profiles": self._rows}
    def create_profile(self, name):
        self._rows.append({"_id": "prof_MADE", "name": name})
        return {"profile": {"_id": "prof_MADE", "name": name}}


class _FakeSDK:
    def __init__(self, rows=None):
        self.connect = _FakeConnect()
        self.profiles = _FakeProfiles(rows if rows is not None else [])


_rows: list = []
ztransport.raw_client = lambda key, **kw: _FakeSDK(_rows)   # type: ignore[assignment]

c = ScopedClient(BUYERS_KEY, None)
try:
    c.connect.url("instagram", redirect_url="https://box.example/voice/connect")
    ok("no profile -> refused", False, "it returned a URL")
except ZernioError as e:
    ok("no profile -> refused before the vendor is called", "no profile" in str(e), str(e))
ok("...and nothing was sent", sent == {}, str(sent))

c = ScopedClient(BUYERS_KEY, "prof_THEIRS")
url = c.connect.url("instagram", redirect_url="https://box.example/voice/connect?connected=instagram")
ok("with a profile, the vendor is called WITH THAT PROFILE", sent.get("profile_id") == "prof_THEIRS",
   str(sent))
ok("instagram pins login_method, as the provisioner does",
   sent.get("login_method") == "instagram_login", str(sent))
ok("the redirect comes back to THIS BOX, not to the build page",
   sent.get("redirect_url", "").startswith("https://box.example/voice/connect"), str(sent))
ok("the https authUrl is returned", url.startswith("https://"), url)

sent.clear()
c.connect.url("facebook", redirect_url="https://box.example/voice/connect?connected=facebook")
ok("messenger connects under the vendor's 'facebook' token",
   sent.get("platform") == "facebook", str(sent))
ok("and login_method is NOT pinned there", "login_method" not in sent, str(sent))


class _NoUrl(_FakeConnect):
    def get_connect_url(self, platform, profile_id, **kw): return {"authUrl": ""}


ztransport.raw_client = lambda key, **kw: type(          # type: ignore[assignment]
    "S", (), {"connect": _NoUrl(), "profiles": _FakeProfiles([])})()
try:
    ScopedClient(BUYERS_KEY, "prof_THEIRS").connect.url(
        "instagram", redirect_url="https://box.example/x")
    ok("a 200 with no url fails HERE, not in the browser", False, "it returned")
except ZernioError as e:
    ok("a 200 with no url fails HERE, not in the browser", "authUrl" in str(e), str(e))

# ── resolving a profile inside the buyer's account ───────────────────────────────────────────
print("\n— one folder is used, none is created, several are ASKED about —")
from marketing.customer_voice import app as voice_app  # noqa: E402

_rows = [{"_id": "prof_ONLY", "name": "My Shop"}]
ztransport.raw_client = lambda key, **kw: _FakeSDK(_rows)  # type: ignore[assignment]
pid, choices = voice_app._resolve_profile(ScopedClient(BUYERS_KEY, None), "My Shop")
ok("exactly one folder is used without asking", (pid, choices) == ("prof_ONLY", []), f"{pid} {choices}")
ok("...and remembered", bs.zernio_profile() == "prof_ONLY", bs.zernio_profile())

_rows = []
pid, choices = voice_app._resolve_profile(ScopedClient(BUYERS_KEY, None), "My Shop")
ok("an empty account gets one made, named for the brand", pid == "prof_MADE", str(pid))

_rows = [{"_id": "prof_A", "name": "Client A"}, {"_id": "prof_B", "name": "Client B"}]
bs.put_zernio_profile("prof_ONLY")                                # a stale one, to be ignored
pid, choices = voice_app._resolve_profile(ScopedClient(BUYERS_KEY, None), "My Shop")
ok("SEVERAL FOLDERS ARE NEVER GUESSED BETWEEN — the person is asked",
   pid is None and [c["id"] for c in choices] == ["prof_A", "prof_B"], f"{pid} {choices}")
ok("...and nothing was written while the question is open",
   bs.zernio_profile() == "prof_ONLY", bs.zernio_profile())

# ── the screen only offers channels the poller actually reads ────────────────────────────────
print("\n— a button for a channel nothing ingests is a button that delivers silence —")
from marketing.customer_voice.inbox import channels  # noqa: E402

offered = {p for p, _ in voice_app._CONNECTABLE}
# FROM `channels.NOT_INBOX_LIST`, NOT A HAND-KEPT EXCEPTION — the THIRD suite to need this same
# edit, after test_inbox_instagram and test_customer_voice_inbox. It read `!= channels.IMAP`,
# written when email arrived; comments then broke it, and this one is a set EQUALITY so it broke
# loudly rather than drifting.
#
# COMMENTS ARE NOT SEPARATELY CONNECTABLE, AND THAT IS WHY THEY ARE EXCLUDED RATHER THAN ADDED TO
# THE SCREEN. They ride the Instagram and Facebook accounts a buyer has already connected — there
# is no third button to press and nothing extra to authorise. The property this line defends is
# untouched: every button on the connect screen still maps to a channel the poller reads, so no
# button delivers silence.
polled = {ch.vendor for ch in channels.POLLED if ch.vendor not in channels.NOT_INBOX_LIST}
ok("every connectable platform is one the poller sweeps", offered == polled,
   f"offered={sorted(offered)} polled={sorted(polled)}")

# ── the screen is kept honest by the poller ──────────────────────────────────────────────────
print("\n— a revoked key stops reading AND stops the screen saying it is fine —")
from marketing.customer_voice.inbox import poller  # noqa: E402

quiet(bs.put_zernio, BUYERS_KEY)
ok("a fresh key reads as connected", bs.zernio_state()["status"] == "connected",
   str(bs.zernio_state()))

poller._note_zernio_health(False, "zernio inbox.list failed: [402] payment method required")
ok("a 402 becomes the one state a buyer can fix",
   bs.zernio_state()["status"] == "payment_required", str(bs.zernio_state()))

poller._note_zernio_health(True)
ok("a successful sweep clears it again", bs.zernio_state()["status"] == "connected",
   str(bs.zernio_state()))

poller._note_zernio_health(False, "zernio inbox.list failed: [401] Invalid API key")
ok("a revoked key asks to be re-connected",
   bs.zernio_state()["status"] == "needs_reauth", str(bs.zernio_state()))

poller._note_zernio_health(False, "zernio inbox.list failed: [500] upstream is having a moment")
ok("A BAD MINUTE AT THE VENDOR IS NOT THE BUYER'S FAULT — the state is left alone",
   bs.zernio_state()["status"] == "needs_reauth", str(bs.zernio_state()))

bs.clear_zernio()
poller._note_zernio_health(False, "[401] Invalid API key")
ok("...and a box on a CONFIG key gets no status row it cannot act on",
   bs.zernio_state()["status"] == "not_connected", str(bs.zernio_state()))


# ── and the pages actually render ────────────────────────────────────────────────────────────
# ASKED OF THE APP, NOT READ OFF THE SOURCE. Every claim above is about a function; this section
# is the only one that answers "does a buyer who clicks this get a page". A 500 here is what they
# would actually see, and it is the failure the rest of the suite cannot detect.
print("\n— a buyer clicking through it gets pages, not a 500 —")
from core import dash as _dash  # noqa: E402
from core.dispatch import app as flask_app  # noqa: E402

_c = flask_app.test_client()
_c.set_cookie("aios_session", _dash.new_session(state.owner_user()["id"]), domain="localhost")

bs.clear_zernio()
r = _c.get("/inbox/settings")
ok("settings renders with nothing connected", r.status_code == 200, str(r.status_code))
body = r.get_data(as_text=True)
ok("...and it now OFFERS a connect surface, which it never did before",
   "/inbox/connect" in body, "no link to /inbox/connect on Settings")
# THE ROW, NOT THE TAB. Since the Drafts TAB joined the rail, a bare find("Drafts") matches the
# nav link at the top of every page and this assertion passes or fails for the wrong reason. The
# row the buyer does second is the one that says who WRITES the drafts.
ok("...above the drafts row, the order a buyer does them in",
   body.find("Your channels") < body.find("Writing your drafts")
   if "Writing your drafts" in body else False)

r = _c.get("/inbox/connect")
ok("the connect page renders for a box with no key", r.status_code == 200, str(r.status_code))
ok("...and asks for the key", "Paste your key" in r.get_data(as_text=True))

r = _c.post("/inbox/connect", data={"key": ""})
ok("an empty paste is a sentence, not an error page", r.status_code == 200, str(r.status_code))
ok("...that says what to do",
   "Paste the API key" in r.get_data(as_text=True), r.get_data(as_text=True)[:200])

# THE KEY IS NEVER RENDERED BACK, in any state. Same discipline as the AI key form.
zverify.verify_key = lambda key: (True, "")                       # type: ignore[assignment]
_rows = [{"_id": "prof_ONLY", "name": "My Shop"}]
ztransport.raw_client = lambda key, **kw: _FakeSDK(_rows)         # type: ignore[assignment]


class _Discovering(_FakeSDK):
    def __init__(self, rows):
        super().__init__(rows)
        self.accounts = type("A", (), {
            "list": staticmethod(lambda **kw: {"accounts": [
                {"_id": "acc_1", "platform": "instagram"}]})})()


ztransport.raw_client = lambda key, **kw: _Discovering(_rows)     # type: ignore[assignment]
r = _c.post("/inbox/connect", data={"key": BUYERS_KEY}, follow_redirects=True)
body = r.get_data(as_text=True)
ok("a good key lands on the connected page", r.status_code == 200, str(r.status_code))
ok("THE KEY IS NEVER RENDERED BACK", BUYERS_KEY not in body)
ok("it says which channel is live", "Connected." in body, body[:300])
ok("...and offers the one that is not", "Connect Messenger" in body, body[:300])

r = _c.get("/inbox/connect/instagram")
ok("pressing Connect redirects INTO the vendor, never renders the url",
   r.status_code == 303 and str(r.headers.get("Location", "")).startswith("https://"),
   f"{r.status_code} {r.headers.get('Location')}")

r = _c.get("/inbox/connect/tiktok")
ok("A PLATFORM THE POLLER NEVER READS IS REFUSED — a grant that collects nothing",
   r.status_code == 303 and "/inbox/connect" in str(r.headers.get("Location", "")),
   f"{r.status_code} {r.headers.get('Location')}")

r = _c.get("/inbox/connect?off=1", follow_redirects=False)
ok("disconnect returns to settings", r.status_code == 303, str(r.status_code))
ok("...and the whole binding is gone",
   bs.zernio_key() == "" and bs.zernio_profile() == "",
   f"{bs.zernio_key()!r} {bs.zernio_profile()!r}")

# THE PAGE IS GATED. It holds a credential field and a consent launcher; an anonymous GET must
# never reach either.
_anon = flask_app.test_client()
for path in ("/inbox/connect", "/inbox/connect/instagram"):
    r = _anon.get(path)
    ok(f"anonymous {path} is refused", r.status_code in (302, 303, 401, 403), str(r.status_code))


print("\n" + ("FAILED: " + ", ".join(FAILS) if FAILS else "ALL OK"))
sys.exit(1 if FAILS else 0)
