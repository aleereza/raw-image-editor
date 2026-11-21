"""QA on the actual render (plan §4.10): deterministic checks, each with one adjustment it may make.

| Check | Fails when | Adjustment |
|---|---|---|
| faces clipped | more than FACE_CLIP of a trusted face's middle is clipped | -0.3 EV and highlight compression |
| shadows crushed | more than SHADOW_CRUSH of the frame is black in a frame that is not dark overall | shadow lift |
| colours out of gamut | more than GAMUT of the frame has a channel at 0 next to a bright one | half the vibrance |
| neutrals not neutral | neutral surfaces miss their intended colour by NEUTRAL_DELTA | white balance |
| horizon not level | a turned photo still leans more than HORIZON degrees | the rest of the turn |

Faces cut by straightening are prevented before the render (`rie.agents.geometry`). The editor adjusts a photo
once and renders it again; whatever still fails goes to review with the reason (plan §4.10).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..engine import wbmodel
from . import colorspace as cs
from . import geometry
from .context import Face
from .linear import LinearRaw
from .proposal import Params

VERSION = "qa@1"
FACE_CLIP = 0.03
SHADOW_CRUSH, SHADOW_LEVEL = 0.08, 0.002  # share of the frame below this luminance
GAMUT = 0.02
NEUTRAL_DELTA = 4.0  # CIELAB a*b* distance
NEUTRAL_RATIO = 0.05  # |log| of R/G and B/G within which a raw cell counts as a neutral surface
HORIZON = 0.3  # degrees
GRID = (32, 48)


@dataclass
class Check:
    name: str
    ok: bool
    value: float
    limit: float
    message: str
    fix: dict[str, dict[str, Any]] = field(default_factory=dict)  # parameter changes, applied over the recipe

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "value": round(self.value, 4),
            "limit": self.limit,
            "message": self.message,
            "fix": self.fix,
        }


@dataclass
class NeutralProbe:
    """The raw's mean camera RGB on a coarse grid, kept after the raw is released so QA can find neutral surfaces."""

    cells: np.ndarray  # (rows, cols, 3); NaN where a cell has too few usable blocks
    camera: wbmodel.CameraModel
    as_shot: np.ndarray

    @classmethod
    def from_linear(cls, lin: LinearRaw) -> NeutralProbe:
        rows, cols = GRID
        h, w = lin.clipped.shape
        cells = np.full((rows, cols, 3), np.nan, dtype=np.float32)
        valid = lin.valid
        for r in range(rows):
            for c in range(cols):
                block = (slice(r * h // rows, (r + 1) * h // rows), slice(c * w // cols, (c + 1) * w // cols))
                ok = valid[block]
                if ok.mean() >= 0.9:
                    cells[r, c] = lin.rgb[block][ok].mean(axis=0)
        return cls(cells, lin.camera, lin.as_shot)


def _wb(params: Params, probe: NeutralProbe) -> tuple[float, float]:
    wb = params.get("white_balance", {})
    if "temperature_k" in wb:
        return float(wb["temperature_k"]), float(wb["tint"])
    return probe.camera.temperature(probe.as_shot)


def _render_cells(render: np.ndarray, angle: float) -> np.ndarray:
    """Mean linear sRGB of the render at each grid cell of the upright frame, following the turn and AutoFill."""
    rows, cols = GRID
    h, w = render.shape[:2]
    s = geometry.autofill_scale(angle, w, h)
    a = math.radians(angle)
    out = np.full((rows, cols, 3), np.nan, dtype=np.float32)
    half_w, half_h = 0.25 * s * w / cols, 0.25 * s * h / rows
    for r in range(rows):
        for c in range(cols):
            x, y = ((c + 0.5) / cols - 0.5) * w, ((r + 0.5) / rows - 0.5) * h
            u = s * (x * math.cos(a) + y * math.sin(a)) + w / 2
            v = s * (-x * math.sin(a) + y * math.cos(a)) + h / 2
            c0, c1 = int(u - half_w), math.ceil(u + half_w)
            r0, r1 = int(v - half_h), math.ceil(v + half_h)
            if c0 < 0 or r0 < 0 or c1 > w or r1 > h or c1 <= c0 or r1 <= r0:
                continue
            out[r, c] = render[r0:r1, c0:c1].reshape(-1, 3).mean(axis=0)
    return out


def _expected(probe: NeutralProbe, neutral: tuple[float, float], final: tuple[float, float]) -> np.ndarray:
    """Linear sRGB direction a neutral surface should render as: neutral, or the warmth the agent kept."""
    light = probe.camera.illuminant(*neutral)
    balanced = light * probe.camera.multipliers(*final)
    srgb = probe.camera.rgb_cam @ balanced
    return srgb / cs.luminance(srgb)


def _ab(rgb: np.ndarray) -> np.ndarray:
    return cs.lab(np.clip(rgb, 1e-6, None))[..., 1:]


def neutral_check(
    render: np.ndarray, probe: NeutralProbe, neutral: tuple[float, float], params: Params, angle: float
) -> Check:
    final = _wb(params, probe)
    multipliers = probe.camera.multipliers(*neutral)
    with np.errstate(invalid="ignore", divide="ignore"):
        balanced = probe.cells * multipliers
        ratio_r = np.abs(np.log(balanced[..., 0] / balanced[..., 1]))
        ratio_b = np.abs(np.log(balanced[..., 2] / balanced[..., 1]))
    reference = (ratio_r < NEUTRAL_RATIO) & (ratio_b < NEUTRAL_RATIO)
    reference[[0, -1], :] = False
    reference[:, [0, -1]] = False
    measured = _render_cells(render, angle)
    y = cs.luminance(np.nan_to_num(measured))
    reference &= ~np.isnan(measured).any(axis=2) & (y > 0.03) & (y < 0.8)
    if reference.sum() < 6:
        return Check("neutrals", True, 0.0, NEUTRAL_DELTA, "no neutral surfaces to check")
    direction = _expected(probe, neutral, final)
    expected = direction[None, :] * y[reference][:, None]
    delta = (_ab(measured[reference]) - _ab(expected)).mean(axis=0)
    distance = float(np.hypot(*delta))
    if distance <= NEUTRAL_DELTA:
        return Check("neutrals", True, distance, NEUTRAL_DELTA, f"neutral surfaces within {distance:.1f} of intended")
    # One Newton step in (mired, log green): how the intended colour of a neutral moves with the white balance.
    mired, log_green = wbmodel.mired(final[0]), math.log(final[1])

    def ab_at(m: float, lg: float) -> np.ndarray:
        return _ab(_expected(probe, neutral, (wbmodel.from_mired(m), math.exp(lg))) * 0.2)

    base = ab_at(mired, log_green)
    jac = np.column_stack([ab_at(mired + 5, log_green) - base, ab_at(mired, log_green + 0.02) - base])
    jac /= np.array([5.0, 0.02])
    try:
        step = np.linalg.solve(jac, -delta)
    except np.linalg.LinAlgError:
        return Check("neutrals", False, distance, NEUTRAL_DELTA, f"neutral surfaces off by {distance:.1f}")
    new_mired = mired + float(np.clip(step[0], -30, 30))
    new_green = math.exp(log_green + float(np.clip(step[1], -0.1, 0.1)))
    fix = {"white_balance": {"temperature_k": round(wbmodel.from_mired(new_mired)), "tint": round(new_green, 4)}}
    return Check("neutrals", False, distance, NEUTRAL_DELTA, f"neutral surfaces off by {distance:.1f}", fix)


def face_check(render: np.ndarray, faces: list[Face], params: Params) -> Check:
    h, w = render.shape[:2]
    worst = 0.0
    for face in faces:
        x, y, bw, bh = face.box
        patch = render[
            int((y + bh * 0.25) * h) : int((y + bh * 0.75) * h), int((x + bw * 0.25) * w) : int((x + bw * 0.75) * w)
        ]
        if patch.size:
            worst = max(worst, float(np.mean(patch.max(axis=2) >= 0.985)))
    if worst <= FACE_CLIP:
        return Check("faces clipped", True, worst, FACE_CLIP, "faces keep their highlights")
    exposure = params.get("exposure", {})
    fix = {
        "exposure": {
            "ev": round(float(exposure.get("ev", 0.0)) - 0.3, 2),
            "highlight_compression": max(60, int(exposure.get("highlight_compression", 0))),
            "highlight_threshold": int(exposure.get("highlight_threshold", 80)),
        }
    }
    return Check("faces clipped", False, worst, FACE_CLIP, f"{worst:.0%} of a face is clipped", fix)


def shadow_check(render: np.ndarray, params: Params) -> Check:
    y = cs.luminance(render)
    crushed = float(np.mean(y < SHADOW_LEVEL))
    if crushed <= SHADOW_CRUSH or float(np.median(y)) < 0.05:  # a dark frame may be meant to be dark
        return Check("shadows crushed", True, crushed, SHADOW_CRUSH, "shadows keep detail")
    shadows = int(params.get("exposure", {}).get("shadows", 0))
    fix = {"exposure": {"shadows": min(100, shadows + 15)}}
    return Check("shadows crushed", False, crushed, SHADOW_CRUSH, f"{crushed:.0%} of the frame is black", fix)


def gamut_check(render: np.ndarray, params: Params) -> Check:
    share = float(np.mean((render.min(axis=2) <= 0.001) & (render.max(axis=2) >= 0.25)))
    if share <= GAMUT:
        return Check("colours out of gamut", True, share, GAMUT, "colours within the gamut")
    vibrance = int(params.get("color", {}).get("vibrance", 0))
    fix = {"color": {"vibrance": vibrance // 2}} if vibrance > 0 else {}
    return Check("colours out of gamut", False, share, GAMUT, f"{share:.1%} of the frame is out of gamut", fix)


def horizon_check(render: np.ndarray, params: Params) -> Check | None:
    angle = float(params.get("geometry", {}).get("rotate_deg", 0.0))
    if not angle:
        return None
    roll, _support, lines = geometry.estimate(geometry.gray_of(render))
    if roll is None or lines != "both" or abs(roll) <= HORIZON:
        return Check("horizon", True, abs(roll or 0.0), HORIZON, "level after the turn")
    fix = {"geometry": {"rotate_deg": round(angle + roll, 2)}}
    return Check("horizon", False, abs(roll), HORIZON, f"still leans {roll:+.1f} degrees", fix)


def check(
    render: np.ndarray,
    params: Params,
    faces: list[Face],
    probe: NeutralProbe | None,
    neutral: tuple[float, float] | None,
) -> list[Check]:
    """Every check on a render (linear sRGB, upright) of a photo with these recipe parameters."""
    checks = [face_check(render, faces, params), shadow_check(render, params), gamut_check(render, params)]
    if probe is not None and neutral is not None:
        angle = float(params.get("geometry", {}).get("rotate_deg", 0.0))
        checks.append(neutral_check(render, probe, neutral, params, angle))
    horizon = horizon_check(render, params)
    if horizon is not None:
        checks.append(horizon)
    return checks
