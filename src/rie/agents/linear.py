"""Linear camera RGB from a raw file, for measuring white balance and exposure (plan §4.6, §4.7).

Each 2 x 2 Bayer block gives one RGB value (red, the mean of the two greens, blue), and 2 x 2 of those blocks are
averaged again: a quarter-resolution image (1509 x 1005 for the D7100) with no demosaicing, scaled from the black
level (0) to the white level (1) and turned upright with LibRaw's orientation, so it lines up with the faces found
on the camera's JPEG. A block counts as clipped when any of its sensor values reaches CLIP of the white level.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
import rawpy

from ..analysis.raw import RawError
from ..engine.wbmodel import CameraModel

CLIP = 0.97  # share of the white level from which a sensor value counts as clipped
DARK = 0.004  # blocks darker than this in every channel are left out of colour statistics
_TURNS = {0: 0, 3: 2, 5: 1, 6: -1}  # LibRaw flip -> numpy.rot90 turns, as in rie.analysis.raw


@dataclass
class LinearRaw:
    rgb: np.ndarray  # (h, w, 3) float32 camera RGB, 0 = black level, 1 = white level, upright
    clipped: np.ndarray  # (h, w) bool: a sensor value in the block reached CLIP
    camera: CameraModel  # the camera's dcraw matrix (XYZ to camera)
    as_shot: np.ndarray  # the camera's multipliers (R, G, B with G = 1)
    white: float
    black: float

    @property
    def valid(self) -> np.ndarray:
        """Blocks usable for colour statistics: not clipped and not near black."""
        return ~self.clipped & (self.rgb.max(axis=2) > DARK)

    def region(self, box: list[float] | tuple[float, ...], shrink: float = 0.0) -> tuple[slice, slice]:
        """Row and column slices of a normalized upright box (x, y, w, h), shrunk by `shrink` of its size."""
        h, w = self.clipped.shape
        x, y, bw, bh = box
        x, y = x + bw * shrink / 2, y + bh * shrink / 2
        bw, bh = bw * (1 - shrink), bh * (1 - shrink)
        c0, c1 = max(int(x * w), 0), min(int(np.ceil((x + bw) * w)), w)
        r0, r1 = max(int(y * h), 0), min(int(np.ceil((y + bh) * h)), h)
        return slice(r0, max(r1, r0 + 1)), slice(c0, max(c1, c0 + 1))


def _bin2(a: np.ndarray) -> np.ndarray:
    h, w = (a.shape[0] // 2) * 2, (a.shape[1] // 2) * 2
    a = a[:h, :w]
    return (a[0::2, 0::2] + a[0::2, 1::2] + a[1::2, 0::2] + a[1::2, 1::2]) * 0.25


def read_linear(data: bytes) -> LinearRaw:
    """Quarter-resolution linear camera RGB of a raw file held in memory."""
    try:
        with rawpy.imread(io.BytesIO(data)) as raw:
            bayer = raw.raw_image_visible
            colors = raw.raw_colors_visible
            desc = raw.color_desc.decode("ascii", "replace")
            black_levels = [float(v) for v in raw.black_level_per_channel]
            white = float(raw.white_level)
            flip = int(raw.sizes.flip)
            matrix = np.array(raw.rgb_xyz_matrix, dtype=float)[:3]
            as_shot = np.array(raw.camera_whitebalance[:3], dtype=float)
            positions: dict[str, list[tuple[int, int]]] = {}
            for y in (0, 1):
                for x in (0, 1):
                    positions.setdefault(desc[colors[y, x]], []).append((y, x))
            if sorted(positions) != ["B", "G", "R"] or len(positions["G"]) != 2:
                raise RawError(f"not an RGGB-type Bayer sensor (pattern {desc})")
            h, w = (bayer.shape[0] // 2) * 2, (bayer.shape[1] // 2) * 2
            planes = {}
            peak = None
            for name, spots in positions.items():
                total = None
                for y, x in spots:
                    plane = bayer[y:h:2, x:w:2]
                    peak = plane.copy() if peak is None else np.maximum(peak, plane)
                    level = black_levels[colors[y, x]]
                    value = (plane.astype(np.float32) - level) / max(white - level, 1.0)
                    total = value if total is None else total + value
                assert total is not None
                planes[name] = total / len(spots)
            assert peak is not None
    except (rawpy.LibRawError, ValueError) as exc:  # type: ignore[attr-defined]
        raise RawError(str(exc)) from exc
    black = float(np.mean(black_levels[:3]))
    clipped_half = peak >= black + CLIP * (white - black)
    rgb = np.stack([_bin2(planes["R"]), _bin2(planes["G"]), _bin2(planes["B"])], axis=2)
    np.clip(rgb, 0.0, None, out=rgb)
    clipped = _bin2(clipped_half.astype(np.float32)) > 0
    turns = _TURNS.get(flip, 0)
    if turns:
        rgb = np.ascontiguousarray(np.rot90(rgb, turns))
        clipped = np.ascontiguousarray(np.rot90(clipped, turns))
    if not np.all(as_shot > 0):
        as_shot = np.array([2.0, 1.0, 1.5])  # no recorded white balance: a daylight-like guess
    return LinearRaw(
        rgb=rgb.astype(np.float32),
        clipped=clipped,
        camera=CameraModel.from_matrix(matrix),
        as_shot=as_shot / as_shot[1],
        white=white,
        black=black,
    )
