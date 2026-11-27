"""Which way is up, by GeoCalib (ECCV 2024; ADR 0017).

A network reads where "up" points all over the picture, from people, trees and horizons as well as lines, and a
geometric optimization turns that into the camera's roll and pitch, each with an uncertainty. The focal length from
the photo's metadata goes in as a prior. The weights are GeoCalib's pinhole release: RawTherapee has already corrected
the lens distortion of the renders it reads.

It runs one photo at a time: on the GPU when PyTorch sees one with GPU_FREE_MIB free (about 0.4 s per photo on a
GTX 1650, using about 500 MiB), otherwise on the CPU (about 2 s, using every core). A GPU that runs out of memory
sends it back to the CPU. `release()` frees the model and its GPU memory when an edit is done, so the GPU holds one
model at a time.
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
GPU_FREE_MIB = 1024  # GeoCalib needs about 500 MiB on the GPU; with less free than this, it runs on the CPU

_lock = threading.Lock()
_model: Any = None
_device = "cpu"


class GeoCalibMissing(RuntimeError):
    """The GeoCalib weights are not in models/; `uv run python scripts/fetch_models.py` downloads them."""


@dataclass(frozen=True)
class Gravity:
    turn: float  # degrees that level the picture, positive counter-clockwise like `geometry.rotate_deg`
    uncertainty: float  # degrees, one standard deviation of the roll
    pitch: float  # degrees, positive when the camera points up
    focal_prior: float | None  # pixels, from the metadata
    device: str = "cpu"  # where GeoCalib ran

    def to_json(self) -> dict[str, float | str | None]:
        return {
            "turn": round(self.turn, 3),
            "uncertainty": round(self.uncertainty, 3),
            "pitch": round(self.pitch, 2),
            "focal_prior": None if self.focal_prior is None else round(self.focal_prior, 1),
            "device": self.device,
        }


def available() -> bool:
    return (MODELS / WEIGHTS).is_file()


def device() -> str:
    """The device GeoCalib loads on: "cuda" when PyTorch sees a GPU with GPU_FREE_MIB free, else "cpu"."""
    import torch

    if not torch.cuda.is_available():
        return "cpu"
    try:
        free, _total = torch.cuda.mem_get_info()
    except RuntimeError:
        return "cpu"
    return "cuda" if free >= GPU_FREE_MIB * 2**20 else "cpu"


def _load() -> Any:
    global _model, _device
    if _model is None:
        path = MODELS / WEIGHTS
        if not path.is_file():
            raise GeoCalibMissing(f"{path} is missing; `uv run python scripts/fetch_models.py` downloads it")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)  # its TorchScript helpers
            from ..vendor.geocalib import GeoCalib

            _device = device()
            _model = GeoCalib(weights=str(path)).to(_device)
    return _model


def release() -> None:
    """Free the model, and the GPU memory it holds; the next estimate loads it again."""
    global _model, _device
    with _lock:
        if _model is None:
            return
        _model = None
        if _device == "cuda":
            import torch

            torch.cuda.empty_cache()
        _device = "cpu"


def focal_pixels(focal_mm: float | None, long_edge: int) -> float | None:
    """The focal length in pixels of an image `long_edge` pixels across the sensor's long side."""
    if not focal_mm or focal_mm <= 0:
        return None
    return float(focal_mm) / SENSOR_LONG_MM * long_edge


def estimate(render: np.ndarray, focal_mm: float | None = None) -> Gravity:
    """The roll and pitch of an upright, lens-corrected render (linear sRGB, (h, w, 3))."""
    global _device
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
            try:
                result = model.calibrate(
                    image.to(_device), priors={k: v.to(_device) for k, v in priors.items()} if priors else None
                )
            except torch.cuda.OutOfMemoryError:
                model.to("cpu")  # the GPU filled up: go on, and stay, on the CPU
                torch.cuda.empty_cache()
                _device = "cpu"
                result = model.calibrate(image, priors=priors)
        ran_on = _device
    gravity = result["gravity"]
    # GeoCalib's roll is the camera's; the picture levels when it turns the other way (ADR 0017).
    return Gravity(
        turn=-math.degrees(float(gravity.roll.item())),
        uncertainty=math.degrees(float(result["roll_uncertainty"].item())),
        pitch=math.degrees(float(gravity.pitch.item())),
        focal_prior=focal,
        device=ran_on,
    )
