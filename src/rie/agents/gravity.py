"""Which way is up, by GeoCalib (ECCV 2024; ADR 0017).

A network reads where "up" points all over the picture, from people, trees and horizons as well as lines, and a
geometric optimization turns that into the camera's roll and pitch, each with an uncertainty. The focal length from
the photo's metadata goes in as a prior. The weights are GeoCalib's pinhole release: RawTherapee has already corrected
the lens distortion of the renders it reads. It runs on the CPU in about 1.5 s per photo, one photo at a time,
since it uses every core.
"""

from __future__ import annotations

import math
import threading
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..config import REPO_ROOT
from . import colorspace as cs

MODELS = REPO_ROOT / "models"
WEIGHTS = Path("geocalib/geocalib-pinhole.tar")
SENSOR_LONG_MM = 23.5  # the D7100's sensor is 23.5 x 15.6 mm

_lock = threading.Lock()
_model: Any = None


class GeoCalibMissing(RuntimeError):
    """The GeoCalib weights are not in models/; `uv run python scripts/fetch_models.py` downloads them."""


@dataclass(frozen=True)
class Gravity:
    turn: float  # degrees that level the picture, positive counter-clockwise like `geometry.rotate_deg`
    uncertainty: float  # degrees, one standard deviation of the roll
    pitch: float  # degrees, positive when the camera points up
    focal_prior: float | None  # pixels, from the metadata

    def to_json(self) -> dict[str, float | None]:
        return {
            "turn": round(self.turn, 3),
            "uncertainty": round(self.uncertainty, 3),
            "pitch": round(self.pitch, 2),
            "focal_prior": None if self.focal_prior is None else round(self.focal_prior, 1),
        }


def available() -> bool:
    return (MODELS / WEIGHTS).is_file()


def _load() -> Any:
    global _model
    if _model is None:
        path = MODELS / WEIGHTS
        if not path.is_file():
            raise GeoCalibMissing(f"{path} is missing; `uv run python scripts/fetch_models.py` downloads it")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)  # its TorchScript helpers
            from ..vendor.geocalib import GeoCalib

            _model = GeoCalib(weights=str(path))
    return _model


def focal_pixels(focal_mm: float | None, long_edge: int) -> float | None:
    """The focal length in pixels of an image `long_edge` pixels across the sensor's long side."""
    if not focal_mm or focal_mm <= 0:
        return None
    return float(focal_mm) / SENSOR_LONG_MM * long_edge


def estimate(render: np.ndarray, focal_mm: float | None = None) -> Gravity:
    """The roll and pitch of an upright, lens-corrected render (linear sRGB, (h, w, 3))."""
    import torch

    h, w = render.shape[:2]
    srgb = np.clip(cs.encode_srgb(np.clip(render, 0.0, 1.0)), 0.0, 1.0)
    image = torch.from_numpy(np.ascontiguousarray(srgb.transpose(2, 0, 1))).float()
    focal = focal_pixels(focal_mm, max(h, w))
    priors = {"focal": torch.tensor(focal)} if focal else None
    with _lock:
        model = _load()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            result = model.calibrate(image, priors=priors)
    gravity = result["gravity"]
    # GeoCalib's roll is the camera's; the picture levels when it turns the other way (ADR 0017).
    return Gravity(
        turn=-math.degrees(float(gravity.roll.item())),
        uncertainty=math.degrees(float(result["roll_uncertainty"].item())),
        pitch=math.degrees(float(gravity.pitch.item())),
        focal_prior=focal,
    )
