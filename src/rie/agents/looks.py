"""Looks: named, versioned sets of tone and colour settings on top of the agents' edits (plan §4.12).

A look sets recipe parameters only: a tone curve and its mode, contrast, saturation, extra vibrance, and a warmth
bias that shifts the white balance agent's temperature. White balance and exposure stay with their agents, and
the owner's locked values win. Until the owner builds looks in the look lab, every photo gets the neutral look.

**neutral@1:** a gentle filmic curve in RawTherapee's Perceptual mode, which changes lightness and keeps colours.
RawTherapee's plain render is about 1.7 times darker than the camera's JPEG in the mid-tones (median 0.29 against
0.44 sRGB on the samples, ADR 0013); the curve lifts mid-grey from 0.46 to 0.56, steepens the mid-tones a little,
deepens the darkest shadows slightly and rolls the highlights off towards white, and the exposure agent sets the
rest per photo.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

import numpy as np
from scipy.interpolate import CubicSpline

from ..engine import wbmodel
from .proposal import Params

NEUTRAL_CURVE = (
    (0.0, 0.0),
    (0.05, 0.045),
    (0.15, 0.165),
    (0.30, 0.36),
    (0.46, 0.56),
    (0.65, 0.76),
    (0.85, 0.92),
    (1.0, 1.0),
)


@dataclass(frozen=True)
class Look:
    name: str
    version: int
    curve: tuple[tuple[float, float], ...] = NEUTRAL_CURVE
    curve_mode: str = "perceptual"
    contrast: int = 0
    saturation: int = 0
    vibrance: int = 0  # added to the colour agent's vibrance
    warmth: float = 0.0  # mired taken off the white balance: positive renders warmer
    description: str = ""
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def id(self) -> str:
        return f"{self.name}@{self.version}"

    def params(self) -> Params:
        tone: dict[str, Any] = {"curve": [list(p) for p in self.curve], "curve_mode": self.curve_mode}
        if self.contrast:
            tone["contrast"] = self.contrast
        out: Params = {"tone": tone}
        if self.saturation:
            out["color"] = {"saturation": self.saturation}
        return out

    @cached_property
    def tone(self) -> Callable[[np.ndarray], np.ndarray]:
        """The curve as a function on sRGB-encoded values (RawTherapee's spline is a natural cubic spline)."""
        points = np.array(self.curve, dtype=float)
        spline = CubicSpline(points[:, 0], points[:, 1], bc_type="natural")

        def apply(values: np.ndarray) -> np.ndarray:
            return np.clip(spline(np.clip(values, 0.0, 1.0)), 0.0, 1.0)

        return apply

    def warm(self, temp: float) -> float:
        """A white balance temperature with this look's warmth bias."""
        if not self.warmth:
            return temp
        return wbmodel.from_mired(max(wbmodel.mired(temp) - self.warmth, 15.0))


NEUTRAL = Look("neutral", 1, description="natural and accurate: a gentle filmic curve, colours as measured")
LOOKS = {NEUTRAL.id: NEUTRAL}


def get(look_id: str) -> Look:
    try:
        return LOOKS[look_id]
    except KeyError:
        raise KeyError(f"unknown look {look_id}; known: {', '.join(LOOKS)}") from None
