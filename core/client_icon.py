"""The client's own icon: uploaded once by the owner, worn by every header and home-screen icon.

Owner, 2026-09-27: "we are going to keep the name of the client and give them the ability to upload
their icon." Owner, 2026-09-29: it appears everywhere — every screen's header and all three
home-screen icons — uploaded on System Settings > Mobile App, PNG or JPEG only (D-C in
docs/SCOPE_MOBILE_APP_REDESIGN.md). Until one is uploaded, the box's own mark stands in, and
removing it brings the mark back.

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
    """The upload as a 512px square PNG with transparent margins, or `Refused` saying why not."""
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
    fit = ImageOps.contain(img, (MASTER, MASTER), Image.LANCZOS)
    out = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))
    out.paste(fit, ((MASTER - fit.width) // 2, (MASTER - fit.height) // 2), fit)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def save(data: bytes, *, by: str | None) -> str:
    """Keep the upload as this box's icon; returns its version. Raises `Refused` with the reason."""
    from core import box_settings
    png = normalise(data)
    sha = hashlib.sha256(png).hexdigest()[:12]
    box_settings.put(MACHINE, KEY, {"png": base64.b64encode(png).decode(), "sha": sha,
                                    "at": datetime.now(timezone.utc).isoformat()}, set_by=by)
    return sha


def remove() -> None:
    """Back to the box's own mark."""
    from core import box_settings
    box_settings.clear(MACHINE, KEY)


def current() -> dict | None:
    """{"sha", "at"} for the icon in force, or None when the box wears its own mark."""
    from core import box_settings
    got = box_settings.get(MACHINE, KEY)
    if not isinstance(got, dict) or not got.get("png") or not got.get("sha"):
        return None
    return {"sha": str(got["sha"]), "at": str(got.get("at") or "")}


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
    """The normalised upload itself, transparent margins and all: what a header draws on its disc."""
    got = _master()
    return got[1] if got else None


@functools.lru_cache(maxsize=16)
def _tile(sha: str, size: int, master: bytes) -> bytes:
    Image, _ = _pil()
    logo = Image.open(io.BytesIO(master)).convert("RGBA")
    inner = max(1, round(size * SAFE))
    logo = logo.resize((inner, inner), Image.LANCZOS)
    tile = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    tile.paste(logo, ((size - inner) // 2, (size - inner) // 2), logo)
    buf = io.BytesIO()
    tile.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def tile_png(size: int) -> bytes | None:
    """The icon as a home screen needs it, or None when there is no upload: square, OPAQUE white
    ground (iOS composites transparency onto black), the logo inside the maskable safe zone so an
    Android launcher's circle or squircle never clips it. One picture serves `any` and `maskable`."""
    got = _master()
    if not got:
        return None
    return _tile(got[0], size, got[1])
