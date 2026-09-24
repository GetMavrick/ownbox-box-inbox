"""The box's icon is the Ownbox mark — on a tab, on a home screen, and in the installed inbox.

Owner, 2026-09-24, with the installed Unified Inbox on a home screen beside the Ownbox icon:
*"Right now it has just a blue O, but I want the actual."* The blue O was the inbox's placeholder
ring. The box now draws ownbox.io's mark in one place (core/dash/look.py) and every icon it serves
comes from there.

WHAT IS HELD HERE, and why each one is a real failure rather than a nicety:
  - the drawing is the SITE'S mark: its numbers are read out of the SVG, and where the site's own
    files are present the pixels are compared with them, so the two cannot drift apart;
  - the home-screen icon is opaque and runs to the edge (iOS paints transparency black and cuts its
    own corners) and keeps the mark inside the circle an Android launcher never crops;
  - the old blue is gone from every size, so the placeholder cannot come back by a revert;
  - the files answer a stranger with no credential, because a browser fetches them before sign-in;
  - every screen names exactly one home-screen icon. Two links with no sizes leave iOS to choose.

The PNGs are decoded here by hand, like they are drawn: Pillow is not on a box.

Run: python tests/test_the_box_wears_the_ownbox_mark.py
"""
import os
import pathlib
import re
import struct
import sys
import tempfile
import zlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_DB = pathlib.Path(tempfile.mkdtemp(prefix="ownbox-mark-")) / "box.db"
os.environ["AIOS_DB_PATH"] = str(_DB)
os.environ["DASH_TOKEN"] = "the-owners-own-password"

from core import state                                      # noqa: E402

state.init_db()

from core.dash import look                                  # noqa: E402

try:
    from marketing.customer_voice import app as _inbox      # noqa: E402
except ImportError:                                         # a box that ships no inbox
    _inbox = None

from core.dispatch import app                               # noqa: E402

_failed = 0
_OLD_BLUE = (125, 211, 252)                                 # the placeholder ring's colour


def ok(what: str, cond: bool, got: str = "") -> None:
    global _failed
    if cond:
        print(f"  ok   {what}")
    else:
        _failed += 1
        print(f"  FAIL {what}" + (f"  — {got}" if got else ""))


def decode(png: bytes):
    """(width, height, channels, rows) for an 8-bit RGB or RGBA PNG, every chunk's CRC checked."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "no PNG signature"
    i, idat, ihdr = 8, b"", None
    while i < len(png):
        n = struct.unpack(">I", png[i:i + 4])[0]
        tag, data = png[i + 4:i + 8], png[i + 8:i + 8 + n]
        crc = struct.unpack(">I", png[i + 8 + n:i + 12 + n])[0]
        assert zlib.crc32(tag + data) & 0xFFFFFFFF == crc, f"bad CRC on {tag!r}"
        if tag == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", data)
        elif tag == b"IDAT":
            idat += data
        i += 12 + n
    w, h, depth, ctype = ihdr[:4]
    assert depth == 8 and ctype in (2, 6), f"depth {depth} colour type {ctype}"
    ch = 3 if ctype == 2 else 4
    raw, stride, rows, prev = zlib.decompress(idat), w * ch, [], bytearray(w * ch)
    for y in range(h):
        f, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for x in range(stride):                             # the five PNG filters, in full
            a = line[x - ch] if x >= ch else 0
            b, c = prev[x], prev[x - ch] if x >= ch else 0
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(line)
        prev = line
    return w, h, ch, rows


def px(img, x, y):
    w, h, ch, rows = img
    return tuple(rows[y][x * ch:(x + 1) * ch])


def test_the_mark_is_the_sites_mark():
    print("test_the_mark_is_the_sites_mark")
    svg = (ROOT / "core/dash/static/icon.svg").read_text()
    rects = re.findall(r"<rect ([^>]*)/>", svg)
    ok("the box's icon.svg is a tile and a square", len(rects) == 2, str(len(rects)))
    attrs = [dict(re.findall(r'(\w+)="([^"]*)"', r)) for r in rects]
    tile, sq = attrs[0], attrs[1]
    side = float(tile["width"])
    rgb = lambda h: tuple(int(h.lstrip("#")[k:k + 2], 16) for k in (0, 2, 4))    # noqa: E731
    ok("the drawing's ink is the SVG's tile fill", look._INK == rgb(tile["fill"]), tile["fill"])
    ok("...its cream is the SVG's square fill", look._GROUND == rgb(sq["fill"]), sq["fill"])
    ok("...the tile's corner is the SVG's", look._TILE_R == float(tile["rx"]) / side)
    ok("...the square is the SVG's size", look._SQUARE == float(sq["width"]) / side)
    ok("...and centred", float(sq["x"]) * 2 + float(sq["width"]) == side, str(sq))
    ok("...with the SVG's corner", look._SQUARE_R == float(sq["rx"]) / side)

    # THE SITE'S OWN FILES, where this tree has them. A box has no sites/, which is a true absence.
    site = ROOT / "sites/ownbox/app"
    if not site.is_dir():
        print("  --   no sites/ on this box; the SVG checks above are the whole comparison")
        return
    ok("icon.svg is ownbox.io's, byte for byte",
       (site / "icon.svg").read_bytes() == (ROOT / "core/dash/static/icon.svg").read_bytes())
    theirs, ours = decode((site / "apple-icon.png").read_bytes()), decode(look.mark_png(180))
    ok("ownbox.io's touch icon is 180 across, as ours is", theirs[:2] == ours[:2] == (180, 180))
    worst, off = 0, 0
    for y in range(180):
        for x in range(180):
            t, o = px(theirs, x, y), px(ours, x, y)
            if len(t) == 4:                                 # over black, as iOS composites it
                t = tuple(round(v * t[3] / 255) for v in t[:3])
            d = max(abs(a - b) for a, b in zip(t, o))
            worst, off = max(worst, d), off + (d > 24)
    # Only anti-aliased edge pixels may differ, where two renderers round a curve differently. Measured
    # 2026-09-24: 28 of 32,400. The wrong geometry puts thousands off.
    ok(f"...and ours matches it pixel for pixel but the edges ({off} edge pixels off by >24)",
       off <= 64, f"worst {worst}")


def test_the_home_screen_icon():
    print("test_the_home_screen_icon")
    for size in (180, 192, 512):
        img = decode(look.mark_png(size))
        w, h, ch, rows = img
        ok(f"{size}: {w}x{h}, opaque (iOS paints transparency black)", (w, h, ch) == (size, size, 3))
        ok(f"{size}: the corner is ink, to the edge", px(img, 0, 0) == look._INK, str(px(img, 0, 0)))
        ok(f"{size}: the centre is the cream square", px(img, size // 2, size // 2) == look._GROUND)
        # MASKABLE: the launcher may crop to any shape that contains the centred circle of 80%.
        far, blue = 0.0, False
        c = size / 2
        for y in range(h):
            for x in range(w):
                p = px(img, x, y)
                blue = blue or p == _OLD_BLUE
                if p != look._INK:
                    far = max(far, ((x + 0.5 - c) ** 2 + (y + 0.5 - c) ** 2) ** 0.5 / size)
        ok(f"{size}: the mark stays inside the maskable safe circle ({far:.3f} of 0.400)", far < 0.40)
        ok(f"{size}: the placeholder blue appears nowhere", not blue)
    ok("drawn once per size, not per request", look.mark_png(512) is look.mark_png(512))


def test_the_tab_icon():
    print("test_the_tab_icon")
    ico = look.favicon_ico()
    reserved, kind, count = struct.unpack("<HHH", ico[:6])
    ok("favicon.ico is an icon file with three images", (reserved, kind, count) == (0, 1, 3))
    sizes = []
    for k in range(count):
        w, h, _, _, planes, bpp, n, off = struct.unpack("<BBBBHHII", ico[6 + 16 * k:22 + 16 * k])
        img = decode(ico[off:off + n])
        sizes.append(w)
        ok(f"{w}px: the entry and the PNG agree", (w, h) == img[:2] and img[2] == 4 and bpp == 32)
        ok(f"{w}px: the tile's corner is clear, as on the site", px(img, 0, 0)[3] == 0)
        ok(f"{w}px: the centre is the cream square, opaque",
           px(img, w // 2, w // 2) == look._GROUND + (255,), str(px(img, w // 2, w // 2)))
    ok("16, 32 and 48", sizes == [16, 32, 48], str(sizes))


def test_the_files_answer_a_stranger():
    print("test_the_files_answer_a_stranger")
    c = app.test_client()
    for path, kind, body in (("/favicon.ico", "image/x-icon", look.favicon_ico()),
                             ("/apple-touch-icon.png", "image/png", look.mark_png(180)),
                             ("/apple-touch-icon-precomposed.png", "image/png", look.mark_png(180)),
                             ("/ui/icon.svg", "image/svg+xml", None)):
        r = c.get(path)
        ok(f"{path} -> 200 {kind} with no credential",
           r.status_code == 200 and kind in r.headers.get("Content-Type", ""),
           f"{r.status_code} {r.headers.get('Content-Type')}")
        if body is not None:
            ok(f"...and it is the mark", r.get_data() == body)
    if _inbox is None:
        print("  --   no inbox on this box; its two icons do not exist to check")
        return
    for size in (192, 512):
        r = c.get(f"/inbox/icon-{size}.png")
        ok(f"/inbox/icon-{size}.png is the mark, to a stranger too",
           r.status_code == 200 and r.get_data() == look.mark_png(size), str(r.status_code))


def test_every_screen_names_one_home_screen_icon():
    print("test_every_screen_names_one_home_screen_icon")
    tags = look.head_tags()
    ok("head_tags() links the tab icon, both forms",
       '<link rel="icon" href="/favicon.ico"' in tags and 'href="/ui/icon.svg" type="image/svg+xml"' in tags)
    ok("...and the home-screen icon", '<link rel="apple-touch-icon" href="/apple-touch-icon.png">' in tags)
    if _inbox is None:
        return
    from core.config import settings
    c = app.test_client()
    c.post("/dash/login", data={"token": settings.dash_token})
    for path in ("/inbox/", "/inbox/inbox", "/inbox/settings"):
        head = c.get(path).get_data(as_text=True).split("</head>", 1)[0]
        n = len(re.findall(r'rel="apple-touch-icon"', head))
        ok(f"{path} names exactly one home-screen icon, the mark", n == 1
           and 'href="/apple-touch-icon.png"' in head, f"{n} found")


def test_the_suite_is_named_in_ci():
    print("test_the_suite_is_named_in_ci")
    wf = ROOT / ".github/workflows/tests.yml"
    if not wf.is_file():
        print("  --   not the repo; a box has no CI manifest to be named in")
        return
    ok("tests.yml runs this suite", re.search(r"^\s+test_the_box_wears_the_ownbox_mark \\$",
                                             wf.read_text(), re.M) is not None)


if __name__ == "__main__":
    for fn in (test_the_mark_is_the_sites_mark, test_the_home_screen_icon, test_the_tab_icon,
               test_the_files_answer_a_stranger, test_every_screen_names_one_home_screen_icon,
               test_the_suite_is_named_in_ci):
        fn()
    print("\n" + ("all good" if not _failed else f"{_failed} FAILED"))
    sys.exit(1 if _failed else 0)
