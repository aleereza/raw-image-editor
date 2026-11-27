"""Scene harmonization (plan §4.6, §4.7 and principle 5: consistency beats per-frame perfection).

Phase 1's scenes are frames a few minutes apart under similar light (a flash change or a jump in light splits a
scene), so their white balance and exposure should match, or a sequence flickers.

- **White balance:** each frame gets the scene's confidence-weighted median, in mired and in log green, with the
  scene's confidence. A frame keeps its own only on strong evidence: its own confidence is at least STRONG, it
  differs from the scene by more than WB_DEVIATE mired, and the camera's own white balance saw that frame's light
  differently too (by LIGHT_CHANGE mired, the same way), so the light itself changed.
- **Exposure:** what has to match is how bright the same light comes out, so the median is taken of each frame's
  rendering gain, its EV plus the light the camera captured (log2 of shutter time x ISO / f-number^2). Frames
  shot with the same settings then get the same EV, and a frame the camera exposed differently is evened out. A
  frame keeps its own EV only on strong evidence (confidence at least STRONG, more than EV_DEVIATE apart). Frames
  with another exposure compensation dialled in are left alone: that difference is the owner's.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..engine import wbmodel
from .proposal import Proposal

VERSION = "harmonize@1"
STRONG = 0.9
WB_DEVIATE = 25.0  # mired
LIGHT_CHANGE = 15.0  # mired between a frame's camera white balance and the scene's
EV_DEVIATE = 0.5  # EV
SAME_MIRED, SAME_GREEN = 6.0, 0.012  # as the white balance agent: closer than this to the camera's is the camera's
SMALL_MIRED, SMALL_GREEN = 12.0, 0.03  # as the white balance agent: changes up to this need no review


@dataclass
class Frame:
    name: str
    scene: int | None
    wb: Proposal | None
    exposure: Proposal | None
    meta: dict


def _wmedian(values: list[float], weights: list[float]) -> float:
    order = np.argsort(values)
    v, w = np.array(values)[order], np.array(weights)[order]
    cumulative = np.cumsum(w)
    return float(v[int(np.searchsorted(cumulative, cumulative[-1] / 2))])


def _wb_values(p: Proposal) -> tuple[float, float] | None:
    final = p.evidence.get("final") or p.evidence.get("camera")
    if not final:
        return None
    return float(final["temp"]), float(final["green"])


def captured(meta: dict) -> float | None:
    """log2 of the light the camera let in: shutter time x ISO / f-number squared."""
    t, iso, n = meta.get("exposure_time"), meta.get("iso"), meta.get("fnumber")
    if not t or not iso or not n:
        return None
    return math.log2(float(t) * float(iso) / float(n) ** 2)


def _harmonize_wb(frames: list[Frame]) -> None:
    usable = [(f, _wb_values(f.wb)) for f in frames if f.wb is not None and not f.wb.abstain]
    usable = [(f, v) for f, v in usable if v is not None]
    if len(usable) < 2:
        return
    weights = [f.wb.confidence + 0.05 for f, _ in usable if f.wb is not None]
    mireds = [wbmodel.mired(v[0]) for _, v in usable]
    greens = [math.log(v[1]) for _, v in usable]
    scene_mired, scene_green = _wmedian(mireds, weights), _wmedian(greens, weights)
    scene_conf = sum(f.wb.confidence * w for (f, _), w in zip(usable, weights, strict=True) if f.wb) / sum(weights)
    temp, green = wbmodel.from_mired(scene_mired), math.exp(scene_green)
    cameras = [wbmodel.mired(f.wb.evidence["camera"]["temp"]) for f, _ in usable if f.wb is not None]
    scene_camera = _wmedian(cameras, [1.0] * len(cameras))
    for (frame, (own_temp, _own_green)), own_mired, camera_mired in zip(usable, mireds, cameras, strict=True):
        p = frame.wb
        assert p is not None
        apart = abs(own_mired - scene_mired)
        light_changed = (
            abs(camera_mired - scene_camera) > LIGHT_CHANGE
            and (camera_mired - scene_camera) * (own_mired - scene_mired) > 0
        )
        if p.confidence >= STRONG and apart > WB_DEVIATE and light_changed:
            p.evidence["harmonized"] = f"kept its own: {apart:.0f} mired from the scene's {temp:.0f} K"
            continue
        camera = p.evidence["camera"]
        same = (
            abs(scene_mired - wbmodel.mired(camera["temp"])) < SAME_MIRED
            and abs(scene_green - math.log(camera["green"])) < SAME_GREEN
        )
        p.params = (
            {"white_balance": {"as_shot": True}}
            if same
            else {"white_balance": {"temperature_k": round(temp), "tint": round(green, 4)}}
        )
        if not same and "temperature_k" not in p.as_shot.get("white_balance", {}):
            p.as_shot = {"white_balance": {"temperature_k": camera["temp"], "tint": camera["green"]}}
        p.confidence = max(p.confidence, scene_conf)
        if same or (
            abs(scene_mired - wbmodel.mired(camera["temp"])) <= SMALL_MIRED
            and abs(scene_green - math.log(camera["green"])) <= SMALL_GREEN
        ):
            p.confidence = max(p.confidence, 0.8)  # close to the camera's: little at stake
        p.evidence["harmonized"] = f"scene median of {len(usable)} frames (this frame alone: {own_temp:.0f} K)"
        p.rationale = f"{temp:.0f} K for the whole scene ({len(usable)} frames); this frame alone: {own_temp:.0f} K"


def _harmonize_exposure(frames: list[Frame]) -> None:
    usable = []
    for f in frames:
        x = captured(f.meta)
        if f.exposure is None or f.exposure.abstain or x is None:
            continue
        usable.append((f, x, float(f.exposure.params.get("exposure", {}).get("ev", 0.0))))
    if len(usable) < 2:
        return
    compensation = _wmedian([float(f.meta.get("exposure_comp") or 0.0) for f, _, _ in usable], [1.0] * len(usable))
    usable = [(f, x, ev) for f, x, ev in usable if float(f.meta.get("exposure_comp") or 0.0) == compensation]
    if len(usable) < 2:
        return
    gains = [x + ev for _, x, ev in usable]
    weights = [f.exposure.confidence + 0.05 for f, _, _ in usable if f.exposure is not None]
    scene_gain = _wmedian(gains, weights)
    for frame, x, ev in usable:
        p = frame.exposure
        assert p is not None
        target = round((scene_gain - x) / 0.05) * 0.05
        if p.confidence >= STRONG and abs(target - ev) > EV_DEVIATE:
            p.evidence["harmonized"] = f"kept its own: {abs(target - ev):.1f} EV from the scene"
            continue
        if abs(target - ev) < 0.05:
            p.evidence["harmonized"] = f"matches the scene ({len(usable)} frames)"
            continue
        params = dict(p.params.get("exposure", {}))
        params["ev"] = target
        p.params = {"exposure": params}
        p.as_shot = {"exposure": {k: p.as_shot.get("exposure", {}).get(k, 0) for k in params}}
        p.evidence["harmonized"] = f"scene exposure of {len(usable)} frames (this frame alone: {ev:+.2f} EV)"
        p.rationale = f"{target:+.2f} EV to match its scene ({len(usable)} frames); this frame alone: {ev:+.2f} EV"


def harmonize(frames: list[Frame]) -> int:
    """Harmonize the proposals of each scene in place; returns how many scenes had two frames or more."""
    scenes: dict[int, list[Frame]] = {}
    for frame in frames:
        if frame.scene is not None:
            scenes.setdefault(frame.scene, []).append(frame)
    count = 0
    for members in scenes.values():
        if len(members) < 2:
            continue
        count += 1
        _harmonize_wb(members)
        _harmonize_exposure(members)
    return count
