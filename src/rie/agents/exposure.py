"""Exposure and tone agent v1 (plan §4.7).

It measures the as-shot render (RawTherapee's own output, linear, lens-corrected) and predicts the edited one: the
render's light times 2^EV, through the look's tone curve. That keeps the prediction in the space the owner sees,
and QA checks it on the actual render afterwards.

- **Faces first:** the weighted median of the trusted faces' lightness (CIELAB L*, the middle of each face) should
  land in FACE_RANGE. A face inside the range is left where it is, so a person with darker skin is never brightened
  to someone else's tone; exposure moves every face together, which keeps the people of a group as they relate.
- **Scenes without faces** move a share of the way to a median brightness of KEY_TARGET.
- **Highlights:** clipping that the push would add in large areas (not specular glints) is limited to CLIP_LIMIT
  of the frame, first with highlight compression above a threshold, which keeps the mid-tones, then by giving up
  some of the push.
- **Backlight:** dark faces against a bright background get Shadows & Highlights' shadow lift and a smaller push.
- **Intent:** a push against the exposure compensation dialled in the camera is halved, and Active D-Lighting's
  deliberate underexposure is given back.

Confidence is highest with confirmed faces and lower without faces and for backlight. A small change (under
SMALL_EV, with no highlight or shadow tool) is applied without review whatever the confidence, since little is at
stake; a push of more than BIG_PUSH is applied but flagged, because it shows noise.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from . import colorspace as cs
from .context import ImageContext
from .looks import NEUTRAL, Look
from .proposal import Proposal

VERSION = "exposure@1"
FACE_RANGE = (48.0, 72.0)  # L* of the weighted median face after the look's curve; provisional
KEY_TARGET = 0.46  # median sRGB luminance after the curve, for scenes without faces; provisional
KEY_SHARE = 0.75  # share of the key correction applied without faces
MAX_UP, MAX_DOWN = 2.5, -2.0  # EV
SAME_EV = 0.1  # smaller changes are left out
SMALL_EV = 0.35  # changes up to this are applied without review
BIG_PUSH = 2.0  # pushes above this are flagged for a look at the noise
CLIP_LIMIT = 0.003  # share of the frame that may newly clip in large areas (plan §4.7: 0.3 %)
SPECULAR = 0.0015  # clipped blobs smaller than this share of the frame count as specular
HIGHLIGHT_THRESHOLD = 80  # RawTherapee's HighlightComprThreshold: compress only the top of the range (ADR 0012)
BACKLIT_FACE, BACKLIT_BRIGHT = 38.0, 0.8  # face L* below, and the frame's 90th percentile above (sRGB)
BACKLIT_SHADOWS = 25
# Active D-Lighting levels (exiftool's numbers) and the light the D7100 holds back for each; provisional.
ADL_EV = {1: 0.3, 3: 0.5, 5: 0.7, 7: 1.0, 8: 1.0, 9: 1.3, 10: 1.3, 11: 1.3, 0xFFFF: 0.5}
WORK_EDGE = 640  # the render is measured at this long edge


def _small(image: np.ndarray) -> np.ndarray:
    h, w = image.shape[:2]
    scale = WORK_EDGE / max(h, w)
    if scale >= 1:
        return image
    return cv2.resize(image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)


def _face_luminance(y: np.ndarray, ctx: ImageContext) -> tuple[float, float] | None:
    """Weighted median luminance of the middle of the trusted faces, and their total weight."""
    h, w = y.shape
    values, weights = [], []
    for face in ctx.trusted_faces:
        x, yy, bw, bh = face.box
        r0, r1 = int((yy + bh * 0.25) * h), math.ceil((yy + bh * 0.75) * h)
        c0, c1 = int((x + bw * 0.25) * w), math.ceil((x + bw * 0.75) * w)
        patch = y[max(r0, 0) : min(r1, h), max(c0, 0) : min(c1, w)]
        if patch.size < 4:
            continue
        values.append(float(np.median(patch)))
        weights.append(face.weight)
    if not values:
        return None
    order = np.argsort(values)
    cumulative = np.cumsum(np.array(weights)[order])
    median = float(np.array(values)[order][int(np.searchsorted(cumulative, cumulative[-1] / 2))])
    return median, float(sum(weights))


class Predictor:
    """The edited render's light for an exposure change, through a look's curve (luminance only)."""

    def __init__(self, before: np.ndarray, look: Look) -> None:
        self.rgb = before
        self.y = cs.luminance(before)
        self.peak = before.max(axis=2)
        self.look = look

    def display(self, linear: np.ndarray | float, ev: float) -> np.ndarray:
        """sRGB-encoded values after the push and the look's curve."""
        pushed = np.minimum(np.asarray(linear, dtype=np.float32) * 2.0**ev, 1.0)
        return self.look.tone(cs.encode_srgb(pushed))

    def lightness(self, linear: float, ev: float) -> float:
        return float(cs.lightness(cs.decode_srgb(self.display(linear, ev))))

    def median(self, ev: float) -> float:
        return float(np.median(self.display(self.y, ev)))

    def ev_for_lightness(self, linear: float, target: float) -> float:
        low, high = MAX_DOWN - 1, MAX_UP + 1
        for _ in range(40):
            mid = (low + high) / 2
            if self.lightness(linear, mid) < target:
                low = mid
            else:
                high = mid
        return (low + high) / 2

    def ev_for_median(self, target: float) -> float:
        low, high = MAX_DOWN - 1, MAX_UP + 1
        for _ in range(30):
            mid = (low + high) / 2
            if self.median(mid) < target:
                low = mid
            else:
                high = mid
        return (low + high) / 2

    def large_clipping(self, ev: float) -> float:
        """Share of the frame that would newly clip in blobs larger than SPECULAR."""
        newly = (self.peak * 2.0**ev >= 0.98) & (self.peak < 0.98)
        if not newly.any():
            return 0.0
        _count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(newly.astype(np.uint8), connectivity=8)
        area = stats[1:, cv2.CC_STAT_AREA]
        large = area[area >= SPECULAR * newly.size].sum()
        return float(large) / newly.size


def propose(ctx: ImageContext, look: Look = NEUTRAL) -> Proposal:
    assert ctx.before is not None, "the editor renders the photo as shot before the agents run"
    before = _small(ctx.before)
    predictor = Predictor(before, look)
    y = predictor.y
    meta = ctx.meta
    faces = _face_luminance(y, ctx)
    p90 = float(np.quantile(cs.encode_srgb(y), 0.9))
    evidence: dict[str, float | str | None] = {"p90": round(p90, 3), "median_before": round(predictor.median(0), 3)}
    confidence = 0.9
    reasons = []
    shadows = 0
    if faces is not None and faces[1] >= 0.5:
        face_y, face_weight = faces
        face_l = predictor.lightness(face_y, 0.0)
        evidence["face_lightness"] = round(face_l, 1)
        target = min(max(face_l, FACE_RANGE[0]), FACE_RANGE[1])
        ev = predictor.ev_for_lightness(face_y, target) if target != face_l else 0.0
        if face_l < BACKLIT_FACE and p90 > BACKLIT_BRIGHT:
            shadows = BACKLIT_SHADOWS
            ev -= 0.3  # the shadow lift does part of the work
            confidence *= 0.9
            reasons.append("backlit: shadows lifted")
        if ev == 0.0:
            # Faces are fine; let the frame's brightness move it a little.
            ev = max(-0.3, min(0.3, 0.5 * KEY_SHARE * predictor.ev_for_median(KEY_TARGET)))
            if abs(ev) < SAME_EV:
                ev = 0.0
        confidence *= min(1.0, 0.85 + 0.15 * face_weight)
        subject = f"faces at L* {face_l:.0f}"
    else:
        key_ev = predictor.ev_for_median(KEY_TARGET)
        ev = KEY_SHARE * key_ev
        confidence = 0.75
        subject = f"median brightness {predictor.median(0):.2f}"

    compensation = meta.get("exposure_comp") or 0.0
    if compensation and ev * compensation < 0:
        ev *= 0.5
        reasons.append(f"the camera's {compensation:+.1f} EV compensation respected")
    adl = meta.get("adl")
    if isinstance(adl, int) and adl in ADL_EV:
        ev += ADL_EV[adl]
        reasons.append(f"Active D-Lighting's {ADL_EV[adl]:+.1f} EV given back")
    ev = min(MAX_UP, max(MAX_DOWN, ev))

    highlight = 0
    clipping = predictor.large_clipping(ev)
    if clipping > CLIP_LIMIT:
        highlight = 60 if clipping < 0.02 else 100 if clipping < 0.06 else 150
        reasons.append("highlights compressed")
        if clipping > 0.06 and ev > 0:
            give_up = min(0.5, ev)
            ev -= give_up
            reasons.append(f"{give_up:.1f} EV less to keep large highlights")
            confidence *= 0.85
    evidence["large_clipping"] = round(clipping, 4)
    ev = round(ev / 0.05) * 0.05
    if abs(ev) < SAME_EV:
        ev = 0.0

    if abs(ev) <= SMALL_EV and not highlight and not shadows:
        confidence = max(confidence, 0.8)
    flags = [f"pushed {ev:+.1f} EV: check the noise"] if ev > BIG_PUSH else []
    params: dict[str, float | int] = {"ev": ev}
    if highlight:
        params["highlight_compression"] = highlight
        params["highlight_threshold"] = HIGHLIGHT_THRESHOLD
    if shadows:
        params["shadows"] = shadows
    after = predictor.median(ev)
    evidence["median_after"] = round(after, 3)
    if faces is not None and faces[1] >= 0.5:
        evidence["face_lightness_after"] = round(predictor.lightness(faces[0], ev), 1)
    extra = f"; {', '.join(reasons)}" if reasons else ""
    if ev == 0 and not highlight and not shadows:
        return Proposal(
            VERSION,
            {"exposure": {"ev": 0.0}},
            confidence,
            f"exposure as shot: {subject}{extra}",
            as_shot={"exposure": {"ev": 0.0}},
            evidence=evidence,
        )
    rationale = f"{ev:+.2f} EV: {subject}"
    if "face_lightness_after" in evidence:
        rationale += f", now about {evidence['face_lightness_after']:.0f}"
    return Proposal(
        VERSION,
        {"exposure": params},
        confidence,
        rationale + extra,
        flags=flags,
        # The threshold is how compression works, not how much: it is never blended.
        as_shot={"exposure": {k: params[k] if k == "highlight_threshold" else 0 for k in params}},
        evidence=evidence,
    )
