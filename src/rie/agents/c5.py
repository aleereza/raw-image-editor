"""The colour of the light by C5, Cross-Camera Convolutional Color Constancy (ICCV 2021; ADR 0017).

C5 reads two log-chroma histograms of the linear camera RGB, 64 x 64 bins each: one of the pixels and one of their
local differences. Six more photos from the same camera go in beside the photo; they tell the network how this
camera records colour, which is why the released model (trained on other cameras, with a learned gain) needs no
training for the D7100. The editor gives it other photos of the same folder; with fewer, the photo itself fills
the places, which measured almost as well (ADR 0017).

The histograms reproduce C5's reference code (`src/ops.py`: soft binning into every bin centre within one bin
width, weighted by brightness, then the square root of the normalized counts) in a few milliseconds instead of a
second. The network outputs a probability map over the light's chroma: its mean is the estimate, and its spread,
turned into degrees, says how sure the network is.
"""

from __future__ import annotations

import threading
import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..analysis.raw import RawError
from ..config import REPO_ROOT
from .linear import LinearRaw, read_linear

MODELS = REPO_ROOT / "models"
WEIGHTS = Path("c5/C5_m_7_h_64_w_G.pth")
BINS = 64
BOUNDARY = 2.85  # the histogram spans log chroma -2.85 to 2.85 (C5's get_hist_boundary)
STEP = 2 * BOUNDARY / (BINS - 1)  # distance between bin centres
SIZE = (384, 256)  # width and height C5 works at
INPUTS = 7  # the photo and six others
EPS = 1e-9

_CENTRES = np.arange(-BOUNDARY, BOUNDARY + STEP / 2, STEP)  # exactly as C5 computes them
_lock = threading.Lock()
_network: Any = None


class C5Missing(RuntimeError):
    """The C5 weights are not in models/; `uv run python scripts/fetch_models.py` downloads them."""


@dataclass(frozen=True)
class Estimate:
    illuminant: np.ndarray  # camera RGB of a white surface under the light, G = 1
    spread: float  # degrees: how far the network's probability map spreads around its mean
    others: int  # other photos that went in beside this one


def image_of(lin: LinearRaw) -> np.ndarray:
    """The linear camera RGB C5 reads: clipped and near-black blocks set to 0, resized to SIZE."""
    rgb = np.where(lin.valid[..., None], lin.rgb, 0.0).astype(np.float32)
    return cv2.resize(rgb, SIZE, interpolation=cv2.INTER_AREA)


def _edges(image: np.ndarray) -> np.ndarray:
    """C5's local differences: the mean absolute difference to the eight neighbours (mirrored at the border)."""
    padded = cv2.copyMakeBorder(image, 1, 1, 1, 1, cv2.BORDER_REFLECT)
    h, w = image.shape[:2]
    total = np.zeros_like(image, dtype=np.float64)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx or dy:
                total += np.abs(image - padded[1 + dy : h + 1 + dy, 1 + dx : w + 1 + dx])
    return total / 8


def _bins(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For each value, the four candidate bins around it and which of them C5 counts (0 < distance <= STEP)."""
    first = np.floor((values + BOUNDARY) / STEP).astype(np.int64) - 1
    index = first[:, None] + np.arange(4)[None, :]
    inside = (index >= 0) & (index < BINS)
    distance = np.abs(values[:, None] - _CENTRES[np.clip(index, 0, BINS - 1)])
    return index, inside & (distance <= STEP) & (distance != 0)


def _histogram(rgb: np.ndarray) -> np.ndarray:
    pixels = rgb.reshape(-1, 3).astype(np.float64)
    pixels = pixels[pixels.sum(axis=1) > EPS]
    out = np.zeros(BINS * BINS)
    if len(pixels):
        logs = np.log(pixels + EPS)
        u, v = logs[:, 1] - logs[:, 0], logs[:, 1] - logs[:, 2]
        weight = np.sqrt((pixels**2).sum(axis=1))
        ui, uok = _bins(u)
        vi, vok = _bins(v)
        for a in range(4):
            for b in range(4):
                keep = uok[:, a] & vok[:, b]
                # Rows run from the largest v down (C5 flips the v axis), columns from the smallest u up.
                cells = (BINS - 1 - vi[keep, b]) * BINS + ui[keep, a]
                out += np.bincount(cells, weights=weight[keep], minlength=BINS * BINS)
    return np.sqrt(out / (out.sum() + EPS)).reshape(BINS, BINS)


def histogram(image: np.ndarray) -> np.ndarray:
    """The (64, 64, 2) input C5 reads from an image made by `image_of`: pixels, then local differences."""
    return np.stack([_histogram(image), _histogram(_edges(image))], axis=-1)


def spread[T](items: list[T], n: int = INPUTS) -> list[T]:
    """Up to `n` items spread evenly through a list: the folder's photos C5 reads beside each photo."""
    if len(items) <= n:
        return list(items)
    return [items[int((i + 0.5) * len(items) / n)] for i in range(n)]


def reference(path: Path) -> np.ndarray | None:
    """The C5 histograms of a raw file (read only), or None when it cannot be read."""
    try:
        return histogram(image_of(read_linear(path.read_bytes())))
    except (RawError, OSError):
        return None


def references(paths: list[Path], workers: int = 4) -> list[tuple[str, np.ndarray]]:
    """The C5 histograms of up to seven raw files spread through `paths` (in capture order), by name."""
    chosen = spread(paths)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        found = list(pool.map(reference, chosen))
    return [(p.name, h) for p, h in zip(chosen, found, strict=True) if h is not None]


def _coords() -> tuple[np.ndarray, np.ndarray]:
    """C5's normalized u and v coordinate planes (0 to 1), added to every histogram."""
    half = (BINS - 1) / 2
    u, v = np.meshgrid(np.arange(-half, half + 1), np.arange(half, -half - 1, -1))
    return (u + half) / (BINS - 1), (v + half) / (BINS - 1)


def available() -> bool:
    return (MODELS / WEIGHTS).is_file()


def _load() -> Any:
    global _network
    if _network is None:
        path = MODELS / WEIGHTS
        if not path.is_file():
            raise C5Missing(f"{path} is missing; `uv run python scripts/fetch_models.py` downloads it")
        import torch

        from ..vendor.c5.network import network

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            net = network(input_size=BINS, learn_g=True, data_num=INPUTS, device="cpu")
        net.load_state_dict(torch.load(path, map_location="cpu", weights_only=True))
        _network = net.eval()
    return _network


def _rgb(u: float, v: float) -> np.ndarray:
    """Camera RGB (G = 1) of log chroma u = log(G/R), v = log(G/B)."""
    return np.array([np.exp(-u), 1.0, np.exp(-v)])


def _angle(a: np.ndarray, b: np.ndarray) -> float:
    cos = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
    return float(np.degrees(np.arccos(min(1.0, max(-1.0, cos)))))


def estimate(photo: np.ndarray, others: list[np.ndarray]) -> Estimate:
    """C5's estimate for a photo's histogram, with up to six histograms of other photos from the same camera."""
    import torch

    extra = list(others[: INPUTS - 1])
    used = len(extra)
    extra += [photo] * (INPUTS - 1 - used)
    u, v = _coords()
    stack = np.stack([np.concatenate([h, u[..., None], v[..., None]], axis=-1) for h in [photo, *extra]], axis=-1)
    model_in = torch.from_numpy(stack.transpose(3, 2, 0, 1).copy()).float()[None]
    query = torch.from_numpy(photo.transpose(2, 0, 1).copy()).float()[None]
    with _lock:
        net = _load()
        with torch.no_grad():
            rgb, probability, *_ = net(query, model_in_N=model_in)
    illuminant = rgb[0].numpy().astype(float)
    illuminant = illuminant / illuminant[1]
    # The spread: the probability map's covariance in log chroma, along its widest direction.
    p = probability[0].numpy().astype(float)
    p = p / p.sum()
    scale = 2 * BOUNDARY / BINS  # C5's from_coord_to_uv
    cu, cv = np.meshgrid(np.arange(BINS) - (BINS - 1) / 2, (BINS - 1) / 2 - np.arange(BINS))
    mu, mv = float((p * cu).sum()), float((p * cv).sum())
    du, dv = (cu - mu) * scale, (cv - mv) * scale
    cov = np.array([[(p * du * du).sum(), (p * du * dv).sum()], [(p * du * dv).sum(), (p * dv * dv).sum()]])
    values, vectors = np.linalg.eigh(cov)
    sigma = float(np.sqrt(max(values[-1], 0.0)))
    centre = np.array([mu * scale, mv * scale])
    edge = centre + sigma * vectors[:, -1]
    spread = _angle(_rgb(*centre), _rgb(*edge))
    return Estimate(illuminant=illuminant, spread=spread, others=used)
