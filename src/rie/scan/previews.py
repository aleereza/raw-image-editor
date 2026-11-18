"""The camera's embedded JPEGs: cached screen-size copies for the grid and loupe, and the full size for 100% zoom.

A D7100 NEF carries a 1620 x 1080 `OtherImage` and a 6000 x 4000 `JpgFromRaw` near the start of the file (ADR
0002). Both are read with one sequential read of the byte span that holds them, which suits a USB hard disk. The
grid thumbnail comes from the `OtherImage`, the loupe copy from the `JpgFromRaw`; both are turned upright with the
NEF's orientation (the embedded JPEGs have no EXIF of their own) and cached on the internal disk under the photo's
quick hash, so culling never waits on the drive or on RawTherapee (plan §6, decision 17).

100% zoom serves the `JpgFromRaw` bytes as they are, with an EXIF orientation tag inserted in front of the image
data so the browser turns it upright without re-encoding 24 megapixels.
"""

from __future__ import annotations

import os
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyvips

from ..config import Config

# EXIF orientation -> the libvips rotation that turns the stored pixels upright (mirrored values never occur on
# cameras and are treated as their unmirrored rotation).
_ROTATE = {1: None, 2: None, 3: "rot180", 4: "rot180", 5: "rot90", 6: "rot90", 7: "rot270", 8: "rot270"}


class PreviewError(RuntimeError):
    """Raised when a photo has no usable embedded JPEG."""


def cache_dir(cfg: Config, quick_hash: str) -> Path:
    digest = quick_hash.split(":", 1)[-1]
    return cfg.paths.cache / "previews" / digest[:2] / digest


def thumb_path(cfg: Config, quick_hash: str) -> Path:
    return cache_dir(cfg, quick_hash) / f"thumb-{cfg.previews.thumb_size}.jpg"


def screen_path(cfg: Config, quick_hash: str) -> Path:
    return cache_dir(cfg, quick_hash) / f"screen-{cfg.previews.screen_size}.jpg"


def jpeg_size(data: bytes) -> tuple[int, int]:
    """(width, height) from a JPEG's SOF marker, as stored (before any orientation)."""
    if data[:2] != b"\xff\xd8":
        raise PreviewError("not a JPEG")
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
            i += 1 if marker == 0xFF else 2
            continue
        length = struct.unpack(">H", data[i + 2 : i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return width, height
        if marker == 0xDA:
            break
        i += 2 + length
    raise PreviewError("no frame header in the JPEG")


def with_orientation(data: bytes, orientation: int) -> bytes:
    """The JPEG with a minimal EXIF block (just the orientation) inserted after its start marker.

    The embedded JPEGs carry no EXIF; browsers apply this tag (`image-orientation: from-image`) without any
    re-encoding. A JPEG that already has an APP1 segment is returned unchanged.
    """
    if data[:2] != b"\xff\xd8" or orientation == 1 or b"\xff\xe1" in data[2:64]:
        return data
    tiff = b"MM\x00\x2a" + struct.pack(">I", 8) + struct.pack(">H", 1)
    tiff += struct.pack(">HHI", 0x0112, 3, 1) + struct.pack(">HH", orientation, 0) + struct.pack(">I", 0)
    payload = b"Exif\x00\x00" + tiff
    return data[:2] + b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload + data[2:]


def read_ranges(path: Path, ranges: dict[str, list[int]], names: tuple[str, ...]) -> dict[str, bytes]:
    """The requested embedded JPEGs, read with one sequential read of the span that holds them all."""
    wanted = [(name, ranges[name]) for name in names if name in ranges]
    if not wanted:
        return {}
    first = min(start for _, (start, _) in wanted)
    last = max(start + length for _, (start, length) in wanted)
    with open(path, "rb") as fh:
        fh.seek(first)
        span = fh.read(last - first)
    out = {}
    for name, (start, length) in wanted:
        data = span[start - first : start - first + length]
        if len(data) == length and data[:2] == b"\xff\xd8":
            out[name] = data
    return out


def _upright(image: pyvips.Image, orientation: int) -> pyvips.Image:
    rotation = _ROTATE.get(orientation)
    if rotation is None:
        return image
    return getattr(image.copy_memory(), rotation)()


def _encode(image: pyvips.Image, quality: int) -> bytes:
    return image.jpegsave_buffer(Q=quality, keep=pyvips.enums.ForeignKeep.NONE, optimize_coding=True)


def _write_cache(path: Path, data: bytes, cache: Path) -> None:
    """Write a cached copy under a temporary name, then rename it; cache files belong to the app."""
    if Path(os.path.abspath(cache)) not in Path(os.path.abspath(path)).parents:
        raise PreviewError(f"refusing to write a preview outside the cache: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


@dataclass(frozen=True)
class PreviewResult:
    image_id: int
    width: int  # upright size of the source the loupe copy was made from (the full-size JPEG)
    height: int
    look: dict[str, Any]
    seconds: float
    error: str | None = None


def look_features(thumb: pyvips.Image) -> dict[str, Any]:
    """Cheap descriptors of the frame for grouping and exposure checks, from the upright grid thumbnail.

    - `layout`: an 8 x 8 grid of CIELAB means (L/100, a/128, b/128), flattened: what the frame looks like.
    - `dhash`: a 64-bit difference hash of a 9 x 8 grey thumbnail, for near-duplicates.
    - `luma`: mean luminance (0-1); `clip_high` / `clip_low`: shares of pixels at the ends of the range.
    """
    rgb = thumb.colourspace("srgb") if thumb.interpretation != "srgb" else thumb
    if rgb.bands > 3:
        rgb = rgb.extract_band(0, n=3)
    pixels = np.ndarray(buffer=rgb.write_to_memory(), dtype=np.uint8, shape=(rgb.height, rgb.width, rgb.bands))
    grey = pixels.astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    lab = rgb.colourspace("lab").resize(8 / rgb.width, vscale=8 / rgb.height, kernel="linear")
    lab_values = np.ndarray(buffer=lab.write_to_memory(), dtype=np.float32, shape=(lab.height, lab.width, 3))
    layout = (lab_values[:8, :8] / np.array([100.0, 128.0, 128.0], np.float32)).round(3).flatten().tolist()
    small = rgb.colourspace("b-w").resize(9 / rgb.width, vscale=8 / rgb.height, kernel="linear")
    g = np.ndarray(buffer=small.write_to_memory(), dtype=np.uint8, shape=(small.height, small.width, small.bands))
    g = g[:8, :9, 0].astype(np.int16)
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    dhash = int("".join("1" if b else "0" for b in bits), 2)
    return {
        "layout": layout,
        "dhash": f"{dhash:016x}",
        "luma": round(float(grey.mean()) / 255.0, 4),
        "clip_high": round(float((pixels.max(axis=2) >= 250).mean()), 5),
        "clip_low": round(float((pixels.max(axis=2) <= 5).mean()), 5),
    }


def make_previews(
    image_id: int, path: Path, kind: str, ranges: dict[str, list[int]], orientation: int, quick_hash: str, cfg: Config
) -> PreviewResult:
    """Make (or reuse) the cached grid and loupe copies of one photo and measure its look."""
    start = time.perf_counter()
    thumb_file, screen_file = thumb_path(cfg, quick_hash), screen_path(cfg, quick_hash)
    quality = cfg.previews.jpeg_quality
    try:
        if kind == "raw":
            if "full" not in ranges:
                raise PreviewError("no full-size embedded JPEG")
            need_thumb, need_screen = not thumb_file.exists(), not screen_file.exists()
            if need_thumb or need_screen:
                data = read_ranges(path, ranges, ("other", "full") if need_thumb else ("full",))
                full = data.get("full")
                if full is None:
                    raise PreviewError("could not read the full-size embedded JPEG")
                header = full
            else:
                start_byte, _length = ranges["full"]
                with open(path, "rb") as fh:  # only the frame header, for the size
                    fh.seek(start_byte)
                    header = fh.read(4096)
                full = data = None
            w, h = jpeg_size(header)
            width, height = (h, w) if orientation in (5, 6, 7, 8) else (w, h)
            small_source = data.get("other", full) if data else None
            rotate = orientation
        else:
            full = path.read_bytes()
            probe = pyvips.Image.new_from_buffer(full, "")
            width, height = probe.width, probe.height
            if probe.get_typeof("orientation") and probe.get("orientation") in (5, 6, 7, 8):
                width, height = height, width
            small_source = full
            rotate = 1  # libvips applies the JPEG's own EXIF orientation when it makes a thumbnail

        if thumb_file.exists():
            thumb = pyvips.Image.new_from_file(str(thumb_file), access="sequential")
        else:
            assert small_source is not None
            thumb = _upright(pyvips.Image.thumbnail_buffer(small_source, cfg.previews.thumb_size), rotate)
            thumb = thumb.copy_memory()
            _write_cache(thumb_file, _encode(thumb, quality), cfg.paths.cache)
        if not screen_file.exists():
            assert full is not None
            screen = _upright(pyvips.Image.thumbnail_buffer(full, cfg.previews.screen_size), rotate)
            _write_cache(screen_file, _encode(screen, quality), cfg.paths.cache)
        look = look_features(thumb)
    except (OSError, PreviewError, pyvips.Error) as exc:
        return PreviewResult(image_id, 0, 0, {}, time.perf_counter() - start, error=str(exc).splitlines()[0])
    return PreviewResult(image_id, width, height, look, time.perf_counter() - start)


def full_size(path: Path, kind: str, ranges: dict[str, list[int]], orientation: int) -> bytes:
    """The full-size camera JPEG for 100% zoom, upright in the browser through its EXIF orientation."""
    if kind != "raw":
        return path.read_bytes()
    data = read_ranges(path, ranges, ("full",)).get("full")
    if data is None:
        raise PreviewError(f"{path.name} has no readable full-size embedded JPEG")
    return with_orientation(data, orientation)
