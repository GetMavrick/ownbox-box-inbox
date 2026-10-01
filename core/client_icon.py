"""The client's own icon: uploaded once by the owner, worn by every header and home-screen icon.

Owner, 2026-09-27: "we are going to keep the name of the client and give them the ability to upload
their icon." Owner, 2026-09-29: it appears everywhere — every screen's header and all three
home-screen icons — PNG or JPEG only (D-C in docs/SCOPE_MOBILE_APP_REDESIGN.md). Owner, 2026-09-30,
moving it to the System Settings main screen because it is not only the mobile app's: "it's used
inside the dashboard like in the upper left side of the menu", and "the icon should fill the whole
space instead of shrinking down and having White". Until one is uploaded, the box's own mark stands
in, and removing it brings the mark back.

TWO KINDS OF ICON, AND EACH IS DRAWN AS WHAT IT IS (`fills()`):
  · one that brings its own ground to its edges — a logo on its own coloured square, the way an app
    icon is made — FILLS every place it is drawn, the header's circle and the home-screen tile alike,
    edge to edge. A white frame around a square that already has a background is a picture of a
    picture;
  · one with a transparent ground — a bare logo — has no ground to fill with, so it sits on white,
    inside the margin a home screen's mask needs. Its own empty margins are trimmed first, so the
    logo is as large as that margin allows, never shrunk by padding it happened to be exported with.

THE FIRST PLACE A CLIENT'S FILE ENTERS THE BOX, so it is treated as hostile until proven otherwise:
  · the format is read from the bytes by Pillow, never from the file name or the browser's word;
  · anything but PNG or JPEG is refused (an SVG can carry script, and this box has no rasteriser);
  · a decompression bomb is refused before it is decoded, not after it has eaten the memory;
  · the image is RE-ENCODED, so nothing of the upload survives but its pixels — no metadata, no
    trailing bytes, nothing a later reader could mistake for something else — and the original is
    never stored and never served.

STORED IN THE DATABASE, in `box_settings`, because the database is what leaves the box: Litestream
replicates it and `scripts/restore_drill.sh` proves it comes back. A file under `my/` would live on
one disk. One normalised master is kept; every size is drawn from it on request and cached.
"""
from __future__ import annotations

import base64
import functools
import hashlib
import io
import warnings
from datetime import datetime, timezone

MACHINE, KEY = "core", "client_icon"
FORMATS = ("PNG", "JPEG")
MAX_BYTES = 5 * 1024 * 1024          # a logo is well under this; a raw phone photo can be over it
MIN_SIDE = 192                       # the smallest size a home screen asks for, drawn without guessing
MAX_PIXELS = 40_000_000              # past this, refuse before decoding: a bomb, not a logo
MASTER = 512                         # the largest size any manifest names
SAFE = 0.70                          # a maskable icon's art must sit in the central circle; 70% of the
                                     # side keeps a square logo's corners inside it on every launcher


class Refused(ValueError):
    """An upload the box will not keep. `code` travels in the redirect; `SAID[code]` is what the
    screen shows, so a crafted address can pick one of these sentences and never write its own."""

    def __init__(self, code: str):
        super().__init__(SAID.get(code, SAID["unreadable"]))
        self.code = code if code in SAID else "unreadable"


def said(code: str) -> str:
    """The sentence for a refusal code; an unknown code gets the general one, never its own text."""
    return SAID.get(str(code or ""), SAID["unreadable"])


SAID = {
    "empty": "That file is empty.",
    "large": "That file is larger than 5 MB. Export the logo smaller and try again.",
    "format": "That file is not a PNG or a JPEG. Save the logo as a PNG and try again.",
    "unreadable": "That file could not be read as an image. Save the logo as a PNG and try again.",
    "small": f"That image is smaller than 192 pixels on a side. Use a larger one so it stays sharp "
             "on a home screen.",
}


def _pil():
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    return Image, ImageOps


def normalise(data: bytes) -> bytes:
    """The upload as a 512px square PNG, or `Refused` saying why not. Fully transparent margins are
    trimmed and the picture is centred, so a non-square logo gets transparent bands, never white."""
    if not data:
        raise Refused("empty")
    if len(data) > MAX_BYTES:
        raise Refused("large")
    Image, ImageOps = _pil()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(data))
            fmt = img.format
            if fmt not in FORMATS:
                raise Refused("format")
            img.load()
    except Refused:
        raise
    except Exception:                                      # noqa: BLE001 — every decode failure says one thing
        raise Refused("unreadable")
    img = ImageOps.exif_transpose(img).convert("RGBA")     # a photo taken sideways stands up first
    if min(img.size) < MIN_SIDE:
        raise Refused("small")
    # EMPTY MARGINS ARE TRIMMED, AFTER the size check: the check is about the file a person chose,
    # and a logo exported on a large transparent canvas is still a large logo. Near-invisible
    # pixels (alpha 8 or under, a soft shadow's tail) do not count as the picture.
    box = img.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
    if box and box != (0, 0, *img.size):
        img = img.crop(box)
    fit = ImageOps.contain(img, (MASTER, MASTER), Image.LANCZOS)
    out = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))
    out.paste(fit, ((MASTER - fit.width) // 2, (MASTER - fit.height) // 2), fit)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def fills(png: bytes) -> bool:
    """Whether a normalised icon brings its own ground to its edges, and so fills where it is drawn.

    THE MIDDLE HALF OF EACH EDGE IS WHAT IS READ, not the whole edge: an icon exported with its own
    rounded corners has transparent corners and is still an icon with a ground, while a round
    logo, or a wordmark with transparent bands above and below, touches the middle of an edge at
    a point at most. 95% opaque, so a stray pixel of anti-aliasing does not decide it."""
    Image, _ = _pil()
    a = Image.open(io.BytesIO(png)).convert("RGBA").getchannel("A")
    n = a.width
    lo, hi = n // 4, n - n // 4
    edge = ([a.getpixel((i, j)) for i in range(lo, hi) for j in (0, n - 1)]
            + [a.getpixel((j, i)) for i in range(lo, hi) for j in (0, n - 1)])
    return sum(v >= 250 for v in edge) >= 0.95 * len(edge)


def save(data: bytes, *, by: str | None) -> str:
    """Keep the upload as this box's icon; returns its version. Raises `Refused` with the reason."""
    from core import box_settings
    png = normalise(data)
    sha = hashlib.sha256(png).hexdigest()[:12]
    box_settings.put(MACHINE, KEY, {"png": base64.b64encode(png).decode(), "sha": sha,
                                    "fills": fills(png),
                                    "at": datetime.now(timezone.utc).isoformat()}, set_by=by)
    return sha


def remove() -> None:
    """Back to the box's own mark."""
    from core import box_settings
    box_settings.clear(MACHINE, KEY)


# AN ICON SAVED BEFORE `fills` WAS RECORDED is measured once, by its version, and remembered: the
# answer cannot change without the picture changing, and a new picture is a new version.
_FILLS: dict[str, bool] = {}


def current() -> dict | None:
    """{"sha", "at", "fills"} for the icon in force, or None when the box wears its own mark."""
    from core import box_settings
    got = box_settings.get(MACHINE, KEY)
    if not isinstance(got, dict) or not got.get("png") or not got.get("sha"):
        return None
    sha = str(got["sha"])
    full = got.get("fills")
    if not isinstance(full, bool):
        if sha not in _FILLS:
            try:
                _FILLS[sha] = fills(base64.b64decode(got["png"]))
            except Exception:                              # noqa: BLE001 — unreadable: drawn on white
                _FILLS[sha] = False
        full = _FILLS[sha]
    return {"sha": sha, "at": str(got.get("at") or ""), "fills": full}


def _master() -> tuple[str, bytes] | None:
    from core import box_settings
    got = box_settings.get(MACHINE, KEY)
    if not isinstance(got, dict) or not got.get("png"):
        return None
    try:
        return str(got["sha"]), base64.b64decode(got["png"])
    except Exception:                                      # noqa: BLE001 — an unreadable row is no icon
        return None


def master_png() -> bytes | None:
    """The normalised upload itself: what a header, the menu and a browser tab draw."""
    got = _master()
    return got[1] if got else None


@functools.lru_cache(maxsize=16)
def _tile(sha: str, size: int, master: bytes, full: bool) -> bytes:
    Image, _ = _pil()
    logo = Image.open(io.BytesIO(master)).convert("RGBA")
    inner = size if full else max(1, round(size * SAFE))
    logo = logo.resize((inner, inner), Image.LANCZOS)
    tile = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    tile.paste(logo, ((size - inner) // 2, (size - inner) // 2), logo)
    buf = io.BytesIO()
    tile.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def tile_png(size: int) -> bytes | None:
    """The icon as a home screen needs it, or None when there is no upload: square and OPAQUE (iOS
    composites transparency onto black). An icon with its own ground fills the tile edge to edge,
    as an app icon does, and the launcher's mask rounds it; a bare logo sits on white inside the
    maskable safe zone, so an Android launcher's circle never clips it. One picture serves `any`
    and `maskable`."""
    got, now = _master(), current()
    if not got or not now:
        return None
    return _tile(got[0], size, got[1], bool(now["fills"]))
