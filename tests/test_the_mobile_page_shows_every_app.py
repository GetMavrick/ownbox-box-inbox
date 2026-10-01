"""The Mobile app page shows every app this box puts on a home screen, and each one is installable.

Owner, 2026-09-24, with a screenshot of two home-screen icons side by side, Ownbox and Unified Inbox:
*"put this on the Mobile app screen showing the PWA add to homescreen for the base machine and add
on machines"*. Until then only the inbox declared a web manifest, so adding the Base Machine offered
its page title as the name and opened it in the browser.

This suite holds:
  1. the Base Machine serves a manifest of its own: public, standalone, named Ownbox, with icons
     that exist, and only the sizes it names;
  2. every core screen links it, and a machine's own screens do not (a page carries one manifest);
  3. /settings/mobile draws one tile per manifest the box serves, the Base Machine first, each with
     its icon, its name, and a link that opens it, read from the routes rather than a list;
  4. a box without the inbox still shows the Base Machine, and only what it has.

Run: python tests/test_the_mobile_page_shows_every_app.py
"""
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "apps.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
os.environ["AIOS_MY_MACHINES"] = os.path.join(_T, "none")
os.environ.pop("ANTHROPIC_API_KEY", None)

from core import state                                                # noqa: E402

state.init_db()

# A BOX SHIPS ONLY ITS OWN MACHINES. On a Lead box there is no inbox, and that is a true absence,
# checked below as such rather than skipped.
try:
    from marketing.customer_voice import app as _inbox                # noqa: E402,F401
    HAS_INBOX = True
except ImportError:
    HAS_INBOX = False

from core import dash                                                 # noqa: E402
from core.dispatch import app                                         # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


anon = app.test_client()
owner = app.test_client()
owner.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))

print("\ntest_the_base_machine_is_an_app")
r = anon.get("/ui/manifest.webmanifest")
ok("its manifest answers without a session, as a home screen asks for it",
   r.status_code == 200 and r.mimetype == "application/manifest+json", f"{r.status_code} {r.mimetype}")
try:
    m = json.loads(r.get_data(as_text=True) or "{}")
except ValueError:
    m = {}
# THE CLIENT'S NAME ON THEIR OWN HOME SCREEN (owner, 2026-09-29), not ours: `dash.brand()` is the
# buyer's on a sold box, and the product's own name only on a box nobody has bought.
BASE = dash.brand()
ok("it is named for the client on the icon", m.get("short_name") == BASE and m.get("name") == BASE, str(m))
ok("it opens standalone, like the inbox, starting at the box's front door",
   m.get("display") == "standalone" and m.get("start_url") == "/" and m.get("scope") == "/")
for icon in m.get("icons") or []:
    got = anon.get(icon["src"])
    ok(f"{icon['src']} is a real PNG, public", got.status_code == 200 and got.data[:4] == b"\x89PNG",
       str(got.status_code))
ok("...and it names both sizes an install needs",
   sorted(i["sizes"] for i in m.get("icons") or []) == ["192x192", "512x512"])
ok("a size it does not name is a 404, never drawn on demand", anon.get("/ui/icon-4096.png").status_code == 404)

print("\ntest_core_screens_link_it_and_machines_do_not")
page = owner.get("/settings").get_data(as_text=True)
ok("a core screen links the Base Machine's manifest",
   '<link rel="manifest" href="/ui/manifest.webmanifest">' in page)
ok("...and names it for the client for iOS", f'<meta name="apple-mobile-web-app-title" content="{BASE}">' in page)
ok("...exactly once", page.count('rel="manifest"') == 1)
if HAS_INBOX:
    inbox = owner.get("/inbox/inbox").get_data(as_text=True)
    ok("the inbox links its own manifest and not the Base Machine's",
       "/inbox/manifest.webmanifest" in inbox and "/ui/manifest.webmanifest" not in inbox)
else:
    print("  --   no inbox on this box; nothing of its to check")

print("\ntest_the_mobile_page_shows_every_app")
page = owner.get("/settings/mobile").get_data(as_text=True)
strip = re.search(r'<div class="apps">(.*?)</div>', page, re.S)
ok("the page draws the apps as they will sit on a home screen", bool(strip))
tiles = re.findall(r'<a href="([^"]+)"><img src="([^"]+)"[^>]*><span>([^<]+)</span></a>',
                   strip.group(1) if strip else "")
names = [t[2] for t in tiles]
ok("the Base Machine is first, named for the client, opening the box", bool(tiles) and tiles[0][:1] == ("/",)
   and names[0] == BASE, str(tiles[:1]))
# THE AEO MACHINE IS ITS OWN APP TOO (owner, 2026-09-29: "All the screens on AEO machine need to
# reflect AEO machine"), so a box that ships it offers its tile beside the inbox's.
try:
    import marketing.aeo_machine.app  # noqa: F401
    HAS_AEO = True
except ImportError:
    HAS_AEO = False
want = ([BASE] + (["AEO Machine"] if HAS_AEO else [])
        + (["Unified Inbox"] if HAS_INBOX else []))
ok(f"one tile per app the box serves: {want}", names == want, str(names))
for href, src, name in tiles:
    ok(f"{name}: its icon is a real image", owner.get(src).data[:4] == b"\x89PNG", src)
    ok(f"{name}: its link opens something", owner.get(href).status_code in (200, 302, 303), href)
ok("the tiles are read from the box's routes, not written down in core",
   "Unified Inbox" not in open(os.path.join(os.path.dirname(os.path.dirname(
       os.path.abspath(__file__))), "core", "dash", "box_settings.py"), encoding="utf-8").read())


print("\ntest_the_owner_can_give_the_box_its_own_icon")
# Owner, 2026-09-27 and 2026-09-29: the client uploads their icon, PNG or JPEG, and it appears in
# every header and on every home-screen app. Owner, 2026-09-30: uploaded on the System Settings main
# screen, since the menu wears it too, with a link left on Mobile App; and it fills its space. The
# first place a client's file enters the box, so every refusal is exercised, not just the happy path.
import io as _io  # noqa: E402
import json as _json  # noqa: E402
import re as _re  # noqa: E402
from PIL import Image as _Img  # noqa: E402
from PIL import ImageDraw as _ImgDraw  # noqa: E402
from core import client_icon  # noqa: E402
from core.dash import look as _look  # noqa: E402


def _img(w, h, fmt="PNG", mode="RGBA", colour=(20, 90, 200, 255)):
    b = _io.BytesIO()
    _Img.new(mode, (w, h), colour if mode != "1" else 1).save(b, format=fmt)
    return b.getvalue()


def _post(client, data, name="logo.png"):
    return client.post("/settings/icon", data={"icon": (_io.BytesIO(data), name)},
                       content_type="multipart/form-data")


mark192 = _look.mark_png(192)
page = owner.get("/settings").get_data(as_text=True)
ok("the owner is offered the upload on the System Settings main screen",
   'id="icon"' in page and 'action="/settings/icon"' in page and 'accept="image/png,image/jpeg"' in page)
mob = owner.get("/settings/mobile").get_data(as_text=True)
ok("...and Mobile App links to it, in the place it left, with no second form",
   'href="/settings#icon"' in mob and 'enctype="multipart/form-data"' not in mob)
member_id = state.add_user("ines@brightline.example", role="member")["id"]
mc = app.test_client()
mc.set_cookie(dash.COOKIE, dash.new_session(member_id))
ok("a member is not offered it, nor the link to it",
   'id="icon"' not in mc.get("/settings").get_data(as_text=True)
   and 'href="/settings#icon"' not in mc.get("/settings/mobile").get_data(as_text=True))
_post(mc, _img(512, 512))
ok("...and a member's upload is refused, changing nothing", client_icon.current() is None)

for label, data, name, code in (
        ("a GIF", _img(300, 300, "GIF", "RGB", (0, 0, 0)), "logo.gif", "format"),
        ("an SVG, which can carry script", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)'
         b'</script></svg>', "logo.svg", "unreadable"),
        ("a text file named .png", b"not an image at all", "logo.png", "unreadable"),
        ("an image too small for a home screen", _img(100, 100), "logo.png", "small"),
        ("an empty file", b"", "logo.png", "empty"),
        ("a decompression bomb", _img(7000, 6000, "PNG", "1"), "bomb.png", "unreadable"),
        ("a file over 5 MB", b"\x89PNG" + b"0" * (client_icon.MAX_BYTES + 10), "big.png", "large")):
    r = _post(owner, data, name)
    where = r.headers.get("Location", "")
    ok(f"{label} is refused, saying why", r.status_code == 303 and f"icon_error={code}" in where, where)
ok("...and nothing refused was kept", client_icon.current() is None)
said = owner.get("/settings?icon_error=format").get_data(as_text=True)
ok("the refusal reads as a sentence, chosen by its code", client_icon.SAID["format"] in said)
forged = owner.get("/settings?icon_error=%3Cb%3Epwned-by-url%3C%2Fb%3E").get_data(as_text=True)
ok("...and a forged code cannot write its own: the address picks a sentence, never supplies one",
   "pwned-by-url" not in forged and client_icon.SAID["unreadable"] in forged)

wide = _io.BytesIO()
_Img.effect_noise((700, 700), 90).convert("RGB").save(wide, format="PNG")
ok("(a real-world-size logo: over 256 KB)", len(wide.getvalue()) > 256 * 1024, str(len(wide.getvalue())))
r = _post(owner, wide.getvalue())
ok("this route takes it, though every other route stops at 256 KB",
   r.status_code == 303 and "icon=saved" in r.headers.get("Location", ""), r.headers.get("Location", ""))
got = client_icon.current()
ok("the icon is kept, with a version", bool(got and got.get("sha")), str(got))

m192 = owner.get("/ui/icon-192.png").data
t = _Img.open(_io.BytesIO(m192))
ok("the home-screen icon is now the client's, not the mark", m192 != mark192 and t.size == (192, 192))
ok("...opaque, since an iPhone paints transparency black", t.mode == "RGB")
ok("the inbox's own icon address serves it too", owner.get("/inbox/icon-192.png").data == m192
   if HAS_INBOX else True)
a180 = _Img.open(_io.BytesIO(owner.get("/apple-touch-icon.png").data))
ok("...and the iPhone's touch icon", a180.size == (180, 180) and a180.mode == "RGB")
man = _json.loads(owner.get("/ui/manifest.webmanifest").get_data(as_text=True))
ok("the manifest names the new icon by its version, so a phone fetches it",
   all(i["src"].endswith(f"?v={got['sha']}") for i in man["icons"]), str(man["icons"]))
head = owner.get("/dashboard").get_data(as_text=True)
ok("every header draws it", f'src="/ui/client-icon.png?v={got["sha"]}"' in head)
master = _Img.open(_io.BytesIO(owner.get("/ui/client-icon.png").data))
ok("the header's picture is the re-encoded upload, square, never the file as sent",
   master.format == "PNG" and master.size == (512, 512))
ok("the top of the menu wears it too, where a letter tile used to be",
   _re.search(r'<div class="who"><span aria-hidden="true"><span class="[^"]*\bav\b[^"]*"><img src="/ui/client-icon\.png\?v=', head)
   is not None)
ok("...and so does the browser tab", f'rel="icon" href="/ui/client-icon.png?v={got["sha"]}"' in head
   and 'href="/ui/icon.svg" type="image/svg+xml"' not in head)

# IT FILLS ITS SPACE (owner, 2026-09-30: "the icon should fill the whole space instead of shrinking
# down and having White"). An icon with its own ground runs edge to edge; a bare logo has no ground,
# so it sits on white, but as large as the mask allows: its own empty margins are trimmed.
def _corner(png):
    return _Img.open(_io.BytesIO(png)).convert("RGB").getpixel((2, 2))


_post(owner, _img(400, 400, colour=(22, 58, 44, 255)))
full = client_icon.current()
ok("an icon with its own ground is recognised as one", bool(full and full["fills"]), str(full))
ok("...its home-screen tile is the icon edge to edge, no white frame",
   _corner(owner.get("/ui/icon-192.png").data) == (22, 58, 44)
   and _corner(owner.get("/apple-touch-icon.png").data) == (22, 58, 44))
ok("...and it fills the circle that opens the menu", 'class="ui-disc appmark fill"' in owner.get("/dashboard").get_data(as_text=True))
bare = _Img.new("RGBA", (600, 600), (0, 0, 0, 0))     # a round 120px mark lost in a 600px canvas:
_ImgDraw.Draw(bare).ellipse((240, 240, 360, 360), fill=(196, 150, 72, 255))   # a SQUARE mark, trimmed,
b = _io.BytesIO(); bare.save(b, format="PNG")
_post(owner, b.getvalue())
lone = client_icon.current()
ok("a bare logo is recognised as one", bool(lone) and lone["fills"] is False, str(lone))
t192 = _Img.open(_io.BytesIO(owner.get("/ui/icon-192.png").data)).convert("RGB")
ok("...it sits on white, inside the margin a home screen's mask needs",
   _corner(owner.get("/ui/icon-192.png").data) == (255, 255, 255) and t192.getpixel((96, 96)) == (196, 150, 72))
ok("...and as large as that margin allows: the canvas it came on is trimmed away",
   t192.getpixel((38, 96)) == (196, 150, 72), str(t192.getpixel((38, 96))))
ok("...and it stays on the white disc in a header, which keeps a dark logo readable on dark",
   'class="ui-disc appmark"' in owner.get("/dashboard").get_data(as_text=True))

r = owner.post("/settings/icon", data={"do": "remove"})
ok("removing it brings the mark back everywhere", r.status_code == 303 and client_icon.current() is None
   and owner.get("/ui/icon-192.png").data == mark192
   and 'src="/ui/icon.svg"' in owner.get("/dashboard").get_data(as_text=True)
   and 'href="/ui/icon.svg" type="image/svg+xml"' in owner.get("/dashboard").get_data(as_text=True))

print("\n" + ("all good" if not _failed else f"{_failed} FAILED"))
sys.exit(1 if _failed else 0)
