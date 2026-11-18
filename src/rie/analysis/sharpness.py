"""Sharpness maps from the raw green channel (plan §4.3, focus inspector v1).

For each tile of the half-resolution green channel, the map compares gradient energy at the finest scale with the
gradient energy after a Gaussian blur of `SIGMA_C` pixels. A sharp edge or texture loses most of its gradient
energy to that blur; a soft one has little to lose. For a blurred step edge the ratio R of the two energies is
sqrt(s^2 + SIGMA_C^2) / s, so the tile's blur s = SIGMA_C / sqrt(R^2 - 1), in half-resolution pixels, whatever
the edge's contrast.

- The signal is square-rooted first, which makes shot noise about the same everywhere.
- Noise inflates the fine energy, so a noise floor, measured in the flattest tiles of the frame, is subtracted.
  That keeps high-ISO grain from reading as detail.
- Tiles without enough structure above the noise get no value (NaN): skin, sky, out-of-focus background.
- The same ratio in four directions (0, 45, 90 and 135 degrees) shows directional blur: motion blur along a
  direction removes fine detail along it and leaves the perpendicular one.
"""

from __future__ import annotations

import base64
import math
from dataclasses import dataclass

import cv2
import numpy as np

TILE = 32  # half-resolution pixels per tile, 64 sensor pixels
SIGMA_C = 2.0  # the comparison blur, in half-resolution pixels
OFFSET = 0.002  # added before the square root, so read noise in the shadows is not amplified without bound
CLIPPED = 0.97  # green values at or above this share of the white level count as clipped
MIN_STRUCTURE = 0.5  # coarse gradient energy, relative to the noise floor, that a tile needs to get a value
DIRECTIONS = (0, 45, 90, 135)
VERSION = "sharpness@1"


@dataclass
class SharpnessMap:
    blur: np.ndarray  # (rows, cols) blur per tile in half-resolution pixels; NaN without enough structure
    structure: np.ndarray  # (rows, cols) coarse gradient energy above noise, relative to the noise floor
    direction: np.ndarray  # (rows, cols) direction of the strongest blur in degrees, or -1
    anisotropy: np.ndarray  # (rows, cols) blur ratio between the most and least blurred directions (1 = round)
    noise: float  # fine-scale noise floor (gradient energy of the square-rooted signal)

    @property
    def shape(self) -> tuple[int, int]:
        return self.blur.shape  # type: ignore[return-value]

    def to_json(self) -> dict:
        return {
            "version": VERSION,
            "rows": int(self.blur.shape[0]),
            "cols": int(self.blur.shape[1]),
            "blur": encode_map(self.blur),
            "direction": encode_map(np.where(self.direction < 0, np.nan, self.direction / 45.0)),
            "anisotropy": encode_map(self.anisotropy),
            "noise": self.noise,
        }

    @classmethod
    def from_json(cls, data: dict) -> SharpnessMap:
        rows, cols = data["rows"], data["cols"]
        blur = decode_map(data["blur"], rows, cols)
        direction = decode_map(data["direction"], rows, cols)
        return cls(
            blur=blur,
            structure=np.where(np.isnan(blur), 0.0, 1.0),
            direction=np.where(np.isnan(direction), -1.0, np.round(direction) * 45.0),
            anisotropy=decode_map(data["anisotropy"], rows, cols),
            noise=float(data["noise"]),
        )


def encode_map(values: np.ndarray) -> str:
    """A float map as base64 of float16 values (NaN kept), compact enough for the catalog."""
    return base64.b64encode(np.asarray(values, dtype=np.float16).tobytes()).decode("ascii")


def decode_map(text: str, rows: int, cols: int) -> np.ndarray:
    return np.frombuffer(base64.b64decode(text), dtype=np.float16).astype(np.float32).reshape(rows, cols)


def _tile_mean(values: np.ndarray, rows: int, cols: int) -> np.ndarray:
    h, w = rows * TILE, cols * TILE
    return values[:h, :w].reshape(rows, TILE, cols, TILE).mean(axis=(1, 3))


def _gradients(s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """x and y derivatives at the centre of each 2 x 2 block, so every direction has the same footprint."""
    a, b, c, d = s[:-1, :-1], s[:-1, 1:], s[1:, :-1], s[1:, 1:]
    return ((b - a) + (d - c)) * 0.5, ((c - a) + (d - b)) * 0.5


def _directional(gx: np.ndarray, gy: np.ndarray) -> dict[int, np.ndarray]:
    """Squared derivatives along 0, 45, 90 and 135 degrees, steered from the x and y derivatives."""
    half = np.float32(0.5)
    return {
        0: np.square(gx),
        90: np.square(gy),
        45: np.square(gx + gy) * half,
        135: np.square(gx - gy) * half,
    }


def _blur_from_ratio(ratio: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return SIGMA_C / np.sqrt(np.maximum(ratio * ratio - 1.0, 1e-6))


def sharpness_map(green: np.ndarray) -> SharpnessMap:
    """The tile map of a half-resolution linear green channel (values 0-1)."""
    s = np.sqrt(green + OFFSET, dtype=np.float32)
    coarse = cv2.GaussianBlur(s, (0, 0), SIGMA_C)
    rows, cols = (s.shape[0] - 1) // TILE, (s.shape[1] - 1) // TILE
    if rows < 2 or cols < 2:
        raise ValueError("image too small for a sharpness map")
    fine = {d: _tile_mean(v, rows, cols) for d, v in _directional(*_gradients(s)).items()}
    blurred = {d: _tile_mean(v, rows, cols) for d, v in _directional(*_gradients(coarse)).items()}
    fine_all = (fine[0] + fine[90]) * 0.5
    coarse_all = (blurred[0] + blurred[90]) * 0.5
    # Clipped highlights have no noise and hard artificial edges: they neither set the noise floor nor count.
    clipped = _tile_mean((green[:-1, :-1] >= CLIPPED).astype(np.float32), rows, cols) > 0.02

    # Noise floor: the fine energy of the flattest tenth of the unclipped frame, where there is little but noise.
    candidates = coarse_all[~clipped] if np.any(~clipped) else coarse_all
    flat = (coarse_all <= np.quantile(candidates, 0.1)) & ~clipped
    if not np.any(flat):
        flat = ~clipped if np.any(~clipped) else np.ones_like(clipped)
    noise_fine = max(float(np.median(fine_all[flat])), 1e-9)
    noise_coarse = float(np.median(coarse_all[flat]))
    signal_fine = np.maximum(fine_all - noise_fine, 0.0)
    signal_coarse = np.maximum(coarse_all - noise_coarse, 0.0)
    structure = signal_coarse / noise_fine
    valid = (structure >= MIN_STRUCTURE) & ~clipped
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(valid, signal_fine / np.maximum(signal_coarse, 1e-12), np.nan)
    blur = np.where(valid, _blur_from_ratio(np.maximum(ratio, 1.0 + 1e-3)), np.nan).astype(np.float32)

    # Directional blur: each direction's own ratio, with the noise split evenly between directions.
    per_dir = []
    for d in DIRECTIONS:
        f = np.maximum(fine[d] - noise_fine, 0.0)
        c = np.maximum(blurred[d] - noise_coarse, 0.0)
        enough = c >= MIN_STRUCTURE * noise_fine
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.where(enough, f / np.maximum(c, 1e-12), np.nan)
        per_dir.append(np.where(enough, _blur_from_ratio(np.maximum(r, 1.0 + 1e-3)), np.nan))
    stack = np.stack(per_dir)
    usable = np.sum(~np.isnan(stack), axis=0) == len(DIRECTIONS)
    filled = np.where(np.isnan(stack), 0.0, stack)
    most = np.argmax(filled, axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        least = np.min(np.where(np.isnan(stack), np.inf, stack), axis=0)
        aniso = np.where(usable & valid, np.max(filled, axis=0) / np.maximum(least, 1e-6), np.nan)
    direction = np.where(usable & valid, np.array(DIRECTIONS, dtype=np.float32)[most], -1.0)
    return SharpnessMap(
        blur=blur,
        structure=structure.astype(np.float32),
        direction=direction.astype(np.float32),
        anisotropy=aniso.astype(np.float32),
        noise=noise_fine,
    )


def region(values: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray:
    """The tiles of a map inside a normalized box (x, y, w, h), at least one tile."""
    rows, cols = values.shape
    x, y, w, h = box
    c0 = min(max(math.floor(x * cols), 0), cols - 1)
    r0 = min(max(math.floor(y * rows), 0), rows - 1)
    c1 = min(max(math.ceil((x + w) * cols), c0 + 1), cols)
    r1 = min(max(math.ceil((y + h) * rows), r0 + 1), rows)
    return values[r0:r1, c0:c1]


def best_blur(values: np.ndarray, quantile: float = 0.2) -> float | None:
    """A robust "sharpest part" of a set of tiles: the low quantile of the valid blur values."""
    valid = values[~np.isnan(values)]
    if valid.size == 0:
        return None
    return float(np.quantile(valid, quantile))
