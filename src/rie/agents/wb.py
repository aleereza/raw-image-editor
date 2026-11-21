"""White balance agent v1 (plan §4.6).

It works in the linear camera RGB of the raw file, with clipped and near-black blocks left out:

1. **Candidates** for the light's colour: the camera's own (as shot, weighed more when the flash fired, since
   the camera then balances for the flash), gray-world, shades-of-gray (p = 6), gray-edge (p = 6, on a lightly
   blurred image) and the brightest unclipped blocks (white patch). Each becomes a RawTherapee temperature and
   green (`rie.engine.wbmodel`).
2. **Priors:** each candidate is weighed by how natural it makes the skin of the confirmed faces look (a hue range
   in CIELAB, wide enough for every skin tone, so no tone is forced), and fused as a weighted median in mired and
   in log green. The statistics read green poorly (foliage, coloured walls), so the fused green moves only halfway
   from the camera's. Disagreement between the candidates, a frame whose parts point to different lights (mixed
   light), implausible skin, a green far from the daylight and blackbody locus, and few usable pixels lower the
   confidence.
3. **Intent:** the default is neutral. Clearly warm light (below 4200 K) keeps some of its warmth: the white
   balance is set so CAT16 with a degree of adaptation down to WARM_DEGREE at 3200 K would see it, which leaves a
   small warm cast. `keep_warm=False` neutralizes fully.

When the result is within a few mired of the camera's, the photo keeps the camera's white balance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from ..engine import wbmodel
from . import colorspace as cs
from .context import ImageContext
from .proposal import Proposal

VERSION = "wb@1"
WEIGHTS = {
    "as_shot": 2.0,
    "gray_edge": 1.5,
    "shades_of_gray": 1.0,
    "white_patch": 0.8,
    "gray_world": 0.5,
}
FLASH_TRUST = 1.5  # the as-shot weight is multiplied by this when the flash fired
MINKOWSKI = 6.0
EDGE_SIGMA = 1.5  # blocks; the quarter-resolution image is about 1500 px wide
SKIN_HUE = (20.0, 75.0)  # degrees in CIELAB: plausible skin under a neutral light, any skin tone; provisional
SKIN_SOFTNESS = 15.0  # degrees outside the range at which a candidate's weight falls to 1/e
SKIN_FLOOR = 0.3
WARM_FULL, WARM_NONE = 3200.0, 4200.0  # K: full warmth kept below the first, none above the second
WARM_DEGREE = 0.8  # CAT16 degree of adaptation for clearly warm light (1 = neutral); provisional
SAME_MIRED, SAME_GREEN = 6.0, 0.012  # closer than this to the camera's: keep the camera's
SMALL_MIRED, SMALL_GREEN = 12.0, 0.03  # changes up to this are applied without review
SPREAD_MIRED, SPREAD_GREEN = 30.0, 0.15  # candidate spreads at which agreement confidence falls to 1/e
GREEN_SHARE = 0.5  # how far the fused green moves from the camera's
MIXED_MIRED = 80.0  # spread of the frame's parts above which the light counts as mixed


@dataclass
class Candidate:
    name: str
    illuminant: np.ndarray  # camera RGB of a white surface, G = 1
    temp: float
    green: float
    weight: float
    skin: float = 1.0  # how natural skin looks under it, 0-1

    def to_json(self) -> dict[str, float | str]:
        return {
            "name": self.name,
            "temp": round(self.temp),
            "green": round(self.green, 4),
            "weight": round(self.weight, 3),
            "skin": round(self.skin, 3),
        }


def _minkowski(values: np.ndarray, p: float) -> np.ndarray:
    return np.power(np.mean(np.power(values, p), axis=0), 1.0 / p)


def _normalize(rgb: np.ndarray) -> np.ndarray | None:
    rgb = np.asarray(rgb, dtype=float)
    if not np.all(np.isfinite(rgb)) or rgb[1] <= 0 or np.any(rgb <= 0):
        return None
    return rgb / rgb[1]


def _edges(rgb: np.ndarray, valid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Gradient magnitude per channel of the lightly blurred image, and where it can be trusted."""
    blurred = cv2.GaussianBlur(rgb, (0, 0), EDGE_SIGMA)
    gx = cv2.Sobel(blurred, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blurred, cv2.CV_32F, 0, 1, ksize=3)
    magnitude = np.sqrt(gx * gx + gy * gy)
    # Edges next to clipped or black blocks mix in the clipping; keep a margin.
    keep = cv2.erode(valid.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    return magnitude, keep


def _skin_samples(ctx: ImageContext) -> list[tuple[np.ndarray, float]]:
    """Median camera RGB of the skin in the middle of each confirmed face, with the face's weight.

    Eyes, brows and hair are darker than skin and lips are redder, so blocks well below the face's median
    brightness or with much more red than its median are left out.
    """
    lin = ctx.linear
    assert lin is not None
    samples = []
    for face in ctx.trusted_faces:
        if not face.confirmed:
            continue
        rows, cols = lin.region(face.box, shrink=0.5)
        patch = lin.rgb[rows, cols].reshape(-1, 3)
        ok = (~lin.clipped[rows, cols]).reshape(-1) & (patch.max(axis=1) > 0.01)
        if ok.sum() < 6:
            continue
        patch = patch[ok]
        level = patch.sum(axis=1)
        redness = patch[:, 0] / np.maximum(patch[:, 1], 1e-6)
        skin = (level >= 0.6 * np.median(level)) & (redness <= 1.25 * np.median(redness))
        if skin.sum() < 6:
            continue
        samples.append((np.median(patch[skin], axis=0), face.weight))
    return samples


def skin_hue(camera: wbmodel.CameraModel, sample: np.ndarray, multipliers: np.ndarray) -> float:
    """CIELAB hue of a camera-RGB skin sample under these multipliers."""
    srgb = camera.rgb_cam @ (sample * multipliers)
    hue, _chroma = cs.hue_chroma(cs.lab(np.clip(srgb, 1e-6, None) / max(float(srgb.max()), 1e-6) * 0.5))
    return float(hue)


def _skin_factor(hue: float) -> float:
    low, high = SKIN_HUE
    distance = max(low - hue, hue - high, 0.0)
    if hue > 300:  # magenta side, past 0 degrees
        distance = low + 360 - hue
    return max(SKIN_FLOOR, math.exp(-((distance / SKIN_SOFTNESS) ** 2)))


def _wmedian(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values)
    v, w = values[order], weights[order]
    cumulative = np.cumsum(w)
    return float(v[int(np.searchsorted(cumulative, cumulative[-1] / 2))])


def _wmad(values: np.ndarray, weights: np.ndarray, centre: float) -> float:
    return _wmedian(np.abs(values - centre), weights)


def candidates(ctx: ImageContext) -> tuple[list[Candidate], dict[str, float]]:
    """Every illuminant estimate for the photo, before priors."""
    lin = ctx.linear
    assert lin is not None
    cam = lin.camera
    valid = lin.valid
    pixels = lin.rgb[valid]
    stats = {"valid_share": float(valid.mean()), "median_level": float(np.median(pixels[:, 1])) if len(pixels) else 0}
    found: list[tuple[str, np.ndarray | None]] = [("as_shot", 1.0 / lin.as_shot)]
    if len(pixels) >= 500:
        found.append(("gray_world", pixels.mean(axis=0)))
        found.append(("shades_of_gray", _minkowski(pixels, MINKOWSKI)))
        brightness = pixels.max(axis=1)
        top = pixels[brightness >= np.quantile(brightness, 0.99)]
        found.append(("white_patch", top.mean(axis=0)))
        magnitude, keep = _edges(lin.rgb, valid)
        edge_pixels = magnitude[keep]
        if len(edge_pixels) >= 500:
            found.append(("gray_edge", _minkowski(edge_pixels, MINKOWSKI)))
    result = []
    for name, rgb in found:
        illuminant = _normalize(rgb) if rgb is not None else None
        if illuminant is None:
            continue
        temp, green = cam.temperature(1.0 / illuminant)
        if not (wbmodel.MIN_TEMP < temp < wbmodel.MAX_TEMP and 0.2 < green < 5):
            continue
        weight = WEIGHTS[name] * (FLASH_TRUST if name == "as_shot" and ctx.meta.get("flash_fired") else 1.0)
        result.append(Candidate(name, illuminant, temp, green, weight))
    return result, stats


def mixed_light(ctx: ImageContext) -> float:
    """Spread (mired) of gray-edge estimates over a 3 x 3 grid of the frame: large when parts see other lights."""
    lin = ctx.linear
    assert lin is not None
    magnitude, keep = _edges(lin.rgb, lin.valid)
    h, w = keep.shape
    temps, weights = [], []
    for i in range(3):
        for j in range(3):
            block = (slice(i * h // 3, (i + 1) * h // 3), slice(j * w // 3, (j + 1) * w // 3))
            m = magnitude[block][keep[block]]
            if len(m) < 300:
                continue
            illuminant = _normalize(_minkowski(m, MINKOWSKI))
            if illuminant is None:
                continue
            temp, _ = lin.camera.temperature(1.0 / illuminant)
            temps.append(wbmodel.mired(temp))
            weights.append(float(np.mean(m)))
    if len(temps) < 4:
        return 0.0
    values, wts = np.array(temps), np.array(weights)
    return 2.0 * _wmad(values, wts, _wmedian(values, wts))


def warm_degree(temp: float) -> float:
    """CAT16 degree of adaptation for light of this temperature: WARM_DEGREE for warm light, 1 for the rest."""
    if temp >= WARM_NONE:
        return 1.0
    if temp <= WARM_FULL:
        return WARM_DEGREE
    return WARM_DEGREE + (1 - WARM_DEGREE) * (temp - WARM_FULL) / (WARM_NONE - WARM_FULL)


def keep_some_warmth(temp: float, green: float, degree: float) -> tuple[float, float]:
    """The white balance that leaves `1 - degree` of the light's cast (CAT16), as RawTherapee temperature and green."""
    light = wbmodel.XYZ_FROM_SRGB @ wbmodel.temp_to_srgb(temp, green)
    target = cs.adapted_white(light, cs.D65_WHITE, degree)
    return wbmodel.srgb_to_temp(wbmodel.SRGB_D65_FROM_XYZ @ target)


def propose(ctx: ImageContext, *, keep_warm: bool = True) -> Proposal:
    lin = ctx.linear
    assert lin is not None, "the editor reads the raw file before the agents run"
    cam = lin.camera
    t0, g0 = cam.temperature(lin.as_shot)
    as_shot = {"white_balance": {"temperature_k": round(t0), "tint": round(g0, 4)}}
    found, stats = candidates(ctx)
    skin = _skin_samples(ctx)
    for c in found:
        if skin:
            mul = 1.0 / c.illuminant
            factors = [(_skin_factor(skin_hue(cam, s, mul)), w) for s, w in skin]
            c.skin = sum(f * w for f, w in factors) / sum(w for _, w in factors)
    weights = np.array([c.weight * c.skin for c in found])
    mireds = np.array([wbmodel.mired(c.temp) for c in found])
    log_greens = np.array([math.log(c.green) for c in found])
    centre_mired, centre_green = _wmedian(mireds, weights), _wmedian(log_greens, weights)
    spread_mired = _wmad(mireds, weights, centre_mired)
    spread_green = _wmad(log_greens, weights, centre_green)
    temp = wbmodel.from_mired(centre_mired)
    green = math.exp(math.log(g0) + GREEN_SHARE * (centre_green - math.log(g0)))

    confidence = math.exp(-((spread_mired / SPREAD_MIRED) ** 2)) * math.exp(-((spread_green / SPREAD_GREEN) ** 2))
    notes = []
    flags = []
    mixed = mixed_light(ctx)
    if mixed > MIXED_MIRED:
        confidence *= 0.85
        notes.append("mixed light")
    if skin:
        mul = cam.multipliers(temp, green)
        hues = [skin_hue(cam, s, mul) for s, _ in skin]
        skin_ok = sum(_skin_factor(h) * w for h, (_, w) in zip(hues, skin, strict=True)) / sum(w for _, w in skin)
        if skin_ok < 0.6:
            confidence *= 0.75
            notes.append("skin looks off")
    else:
        hues, skin_ok = [], None
    if stats["valid_share"] < 0.3:
        confidence *= 0.7
        notes.append("little usable light")
    if abs(centre_green) > 0.15:
        confidence *= 0.8
        notes.append("unusually green or magenta light")
    if len(found) <= 2:
        confidence *= 0.6
    confidence = min(0.97, max(0.05, confidence))

    warmth = ""
    final_temp, final_green = temp, green
    if keep_warm:
        degree = warm_degree(temp)
        if degree < 1.0:
            final_temp, final_green = keep_some_warmth(temp, green, degree)
            if abs(wbmodel.mired(final_temp) - wbmodel.mired(temp)) >= 3:
                warmth = f"; some of the light's warmth kept (neutral would be {temp:.0f} K)"

    evidence = {
        "candidates": [c.to_json() for c in found],
        "camera": {"temp": round(t0), "green": round(g0, 4)},
        "estimate": {"temp": round(temp), "green": round(green, 4)},
        "final": {"temp": round(final_temp), "green": round(final_green, 4)},
        "spread_mired": round(spread_mired, 1),
        "spread_green": round(spread_green, 4),
        "mixed_mired": round(mixed, 1),
        "skin_hues": [round(h, 1) for h in hues],
        "skin_ok": None if skin_ok is None else round(skin_ok, 3),
        **{k: round(v, 4) for k, v in stats.items()},
    }
    agreeing = sorted((c for c in found if abs(wbmodel.mired(c.temp) - centre_mired) <= 8), key=lambda c: -c.weight)
    basis = " and ".join(c.name.replace("_", "-") for c in agreeing[:2]) or "the candidates"
    note = f"; {', '.join(notes)}" if notes else ""
    same = (
        abs(wbmodel.mired(final_temp) - wbmodel.mired(t0)) < SAME_MIRED and abs(math.log(final_green / g0)) < SAME_GREEN
    )
    if same:
        return Proposal(
            VERSION,
            {"white_balance": {"as_shot": True}},
            confidence,
            f"the camera's white balance ({t0:.0f} K) agrees with {basis}{note}",
            as_shot={"white_balance": {"as_shot": True}},
            evidence=evidence,
            flags=flags,
        )
    if (
        abs(wbmodel.mired(final_temp) - wbmodel.mired(t0)) <= SMALL_MIRED
        and abs(math.log(final_green / g0)) <= SMALL_GREEN
    ):
        confidence = max(confidence, 0.8)  # close to the camera's: little at stake
    if abs(wbmodel.mired(final_temp) - wbmodel.mired(t0)) >= SAME_MIRED / 2:
        direction = f"{'warmer' if final_temp > t0 else 'cooler'} than the camera's {t0:.0f} K"
    else:
        direction = f"{'greener' if final_green > g0 else 'more magenta'} than the camera's tint {g0:.2f}"
    return Proposal(
        VERSION,
        {"white_balance": {"temperature_k": round(final_temp), "tint": round(final_green, 4)}},
        confidence,
        f"{final_temp:.0f} K, {direction}: {basis}{warmth}{note}",
        as_shot=as_shot,
        evidence=evidence,
        flags=flags,
    )
