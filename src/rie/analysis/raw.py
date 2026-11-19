"""The raw green channel at half resolution, upright, for measuring sharpness (plan §4.3).

The green Bayer samples are linear, have no demosaicing artifacts and no in-camera sharpening. Averaging the two
greens of each 2 x 2 block gives a half-resolution image (3018 x 2010 for the D7100) that is turned upright with
LibRaw's orientation, so it lines up with the camera's JPEG.
"""

from __future__ import annotations

import io

import numpy as np
import rawpy

# LibRaw's `flip` -> number of 90-degree counter-clockwise turns (numpy.rot90) that make the frame upright.
_TURNS = {0: 0, 3: 2, 5: 1, 6: -1}


class RawError(RuntimeError):
    """Raised for a raw file LibRaw cannot read or a sensor layout this code does not handle."""


def green_half(data: bytes) -> tuple[np.ndarray, dict[str, float]]:
    """Upright half-resolution green channel scaled to 0-1 (black to white level), and decode facts."""
    try:
        with rawpy.imread(io.BytesIO(data)) as raw:
            bayer = raw.raw_image_visible
            colors = raw.raw_colors_visible
            desc = raw.color_desc.decode("ascii", "replace")
            black_levels = list(raw.black_level_per_channel)
            white = float(raw.white_level)
            flip = int(raw.sizes.flip)
            greens = [(y, x) for y in (0, 1) for x in (0, 1) if desc[colors[y, x]] == "G"]
            if len(greens) != 2:
                raise RawError(f"not a Bayer sensor with two greens per block (pattern {desc})")
            h = (bayer.shape[0] // 2) * 2
            w = (bayer.shape[1] // 2) * 2
            (y1, x1), (y2, x2) = greens
            g = bayer[y1:h:2, x1:w:2].astype(np.float32)
            g += bayer[y2:h:2, x2:w:2]
            black = float(np.mean([black_levels[colors[y, x]] for y, x in greens]))
    except (rawpy.LibRawError, ValueError) as exc:  # type: ignore[attr-defined]
        raise RawError(str(exc)) from exc
    g = (g * 0.5 - black) / max(white - black, 1.0)
    np.clip(g, 0.0, 1.0, out=g)
    turns = _TURNS.get(flip, 0)
    if turns:
        g = np.ascontiguousarray(np.rot90(g, turns))
    return g, {"white": white, "black": black, "flip": float(flip)}
