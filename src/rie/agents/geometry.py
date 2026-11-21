"""Geometry agent v1: straightening (plan §4.5).

Line segments (OpenCV's LSD) on the as-shot render, which RawTherapee has already corrected for lens distortion,
feed length-weighted histograms of how far near-horizontal and near-vertical lines lean. Verticals far from the
middle column and horizontals far from the middle row lean through perspective rather than roll, so they count
less. Each histogram's peak is an estimate of the roll. As the plan's fusion rule asks, horizontals and verticals
that agree within AGREE_AXES give a confident roll; one kind of line alone gives a cautious one (applied halfway and
reviewed); lines that disagree, or too little line, abstain. Photos dominated by a face (portraits and close-ups)
count lines less. Stripes in clothing and other textures make peaks of their own, which the agreement test catches.
GeoCalib and a separate horizon detector (plan §4.5, items 2 and 3) are not used yet.

Policy: a roll under MIN_ROLL is left alone; up to MAX_ROLL it is corrected; above that it is probably deliberate
and only flagged. RawTherapee's AutoFill enlarges the turned picture to hide the corners (ADR 0001), so no crop is
needed, but the enlargement cuts the edges: a correction that would cut a trusted face is reduced until it does
not, or flagged.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from . import colorspace as cs
from .context import Face, ImageContext
from .proposal import Proposal

VERSION = "geometry@1"
MIN_ROLL, MAX_ROLL = 0.2, 8.0  # degrees (plan §4.5)
WINDOW = 10.0  # degrees around horizontal and vertical that count as lines to straighten by
BANDWIDTH = 0.15  # degrees, kernel of the lean histogram
AGREE = 0.5  # degrees from the peak that count as agreeing
MIN_SUPPORT = 0.3  # agreeing line length per kind of line, in frame diagonals, for an estimate
SINGLE_SUPPORT = 1.0  # agreeing length needed when only one kind of line gives an estimate
STRONG_SUPPORT = 3.0  # agreeing length at which the line evidence is full
AGREE_AXES = 0.5  # degrees within which horizontals and verticals agree
FACE_DOMINANT = 0.06  # a trusted face larger than this share of the frame makes the photo a portrait
WORK_EDGE = 1280
MARGIN = 0.01  # a face must stay this far (share of the frame) inside the visible area


def autofill_scale(angle: float, width: float, height: float) -> float:
    """How much RawTherapee's AutoFill enlarges a picture turned by `angle` degrees to hide the corners."""
    a = math.radians(abs(angle))
    return math.cos(a) + max(width / height, height / width) * math.sin(a)


def visible(point: tuple[float, float], angle: float, width: float, height: float) -> bool:
    """Whether a point of the upright frame (normalized) stays in the picture after the turn and AutoFill."""
    s = autofill_scale(angle, width, height)
    a = math.radians(angle)
    # Turn the point with the picture (counter-clockwise on screen for a positive angle; y points down), enlarge
    # it by s, and see whether it is still inside the frame.
    x, y = (point[0] - 0.5) * width, (point[1] - 0.5) * height
    u = x * math.cos(a) + y * math.sin(a)
    v = -x * math.sin(a) + y * math.cos(a)
    return abs(u) <= width / (2 * s) * (1 - MARGIN) and abs(v) <= height / (2 * s) * (1 - MARGIN)


def face_kept(face: Face, angle: float, width: float, height: float) -> bool:
    x, y, w, h = face.box
    corners = ((x, y), (x + w, y), (x, y + h), (x + w, y + h))
    return all(visible(c, angle, width, height) for c in corners)


def leans(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Lean (degrees, the roll that would make each line level or plumb) and weight of near-axis segments.

    Image coordinates point down, so a horizontal line falling to the right leans by a positive angle, and turning
    the picture counter-clockwise by that angle (RawTherapee's positive `[Rotation] Degree`) levels it.
    """
    detector = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD)
    found = detector.detect(gray)[0]
    if found is None:
        return np.zeros(0), np.zeros(0), np.zeros(0, dtype=bool)
    seg = found.reshape(-1, 4).astype(np.float64)
    h, w = gray.shape
    dx, dy = seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1]
    length = np.hypot(dx, dy)
    angle = np.degrees(np.arctan2(dy, dx))
    angle = np.where(angle > 90, angle - 180, np.where(angle <= -90, angle + 180, angle))
    mid_x, mid_y = (seg[:, 0] + seg[:, 2]) / (2 * w), (seg[:, 1] + seg[:, 3]) / (2 * h)
    horizontal = np.abs(angle) <= WINDOW
    vertical = np.abs(np.abs(angle) - 90) <= WINDOW
    lean = np.where(horizontal, angle, np.where(angle > 0, angle - 90, angle + 90))
    # Perspective tilts verticals away from the middle column and horizontals away from the middle row.
    centred = np.where(horizontal, np.exp(-((mid_y - 0.5) ** 2) / 0.08), np.exp(-((mid_x - 0.5) ** 2) / 0.08))
    keep = (horizontal | vertical) & (length >= 0.02 * max(h, w))
    diagonal = math.hypot(h, w)
    return lean[keep], (length * centred)[keep] / diagonal, horizontal[keep]


def _peak(lean: np.ndarray, weight: np.ndarray) -> tuple[float | None, float]:
    """The histogram peak's roll and the line length (in diagonals) that agrees with it."""
    if not len(lean):
        return None, 0.0
    grid = np.arange(-WINDOW, WINDOW + 0.001, 0.02)
    density = (weight[None, :] * np.exp(-0.5 * ((grid[:, None] - lean[None, :]) / BANDWIDTH) ** 2)).sum(axis=1)
    peak = float(grid[int(np.argmax(density))])
    near = np.abs(lean - peak) <= AGREE
    if not near.any():
        return None, 0.0
    return float(np.average(lean[near], weights=weight[near])), float(weight[near].sum())


def estimate(gray: np.ndarray) -> tuple[float | None, float, str]:
    """(roll, agreeing line length in diagonals, which lines: "both", "horizontals", "verticals", "disagree" or
    "none") from a grayscale image."""
    lean, weight, horizontal = leans(gray)
    h_roll, h_support = _peak(lean[horizontal], weight[horizontal])
    v_roll, v_support = _peak(lean[~horizontal], weight[~horizontal])
    has_h = h_roll is not None and h_support >= MIN_SUPPORT
    has_v = v_roll is not None and v_support >= MIN_SUPPORT
    if has_h and has_v:
        assert h_roll is not None and v_roll is not None
        if abs(h_roll - v_roll) > AGREE_AXES:
            return None, h_support + v_support, "disagree"
        roll = (h_roll * h_support + v_roll * v_support) / (h_support + v_support)
        return roll, h_support + v_support, "both"
    for roll, support, kind in ((h_roll, h_support, "horizontals"), (v_roll, v_support, "verticals")):
        if roll is not None and support >= SINGLE_SUPPORT:
            return roll, support, kind
    return None, max(h_support, v_support), "none"


def gray_of(render: np.ndarray) -> np.ndarray:
    """An 8-bit grayscale copy of a linear render at WORK_EDGE, for the line detector."""
    h, w = render.shape[:2]
    scale = min(1.0, WORK_EDGE / max(h, w))
    small = cv2.resize(render, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    return (cs.encode_srgb(cs.luminance(small)) * 255).astype(np.uint8)


def propose(ctx: ImageContext) -> Proposal:
    assert ctx.before is not None, "the editor renders the photo as shot before the agents run"
    h, w = ctx.before.shape[:2]
    roll, support, lines = estimate(gray_of(ctx.before))
    as_shot = {"geometry": {"rotate_deg": 0.0}}
    evidence = {"roll": None if roll is None else round(roll, 3), "support": round(support, 2), "lines": lines}
    if roll is None:
        why = "level and plumb lines disagree" if lines == "disagree" else "too few straight lines to judge"
        return Proposal(
            VERSION,
            {"geometry": {"rotate_deg": 0.0}},
            0.0,
            f"{why}: left as shot",
            as_shot=as_shot,
            evidence=evidence,
            abstain=True,
        )
    confidence = min(1.0, support / STRONG_SUPPORT) ** 0.5 * (0.95 if lines == "both" else 0.7)
    biggest = max((f.box[2] * f.box[3] for f in ctx.trusted_faces), default=0.0)
    if biggest > FACE_DOMINANT:
        confidence *= 0.6  # a portrait's lines are background; its tilt is often deliberate
    flags = []
    if abs(roll) < MIN_ROLL:
        return Proposal(
            VERSION,
            {"geometry": {"rotate_deg": 0.0}},
            confidence,
            f"level: lines lean {roll:+.2f} degrees",
            as_shot=as_shot,
            evidence=evidence,
        )
    if abs(roll) > MAX_ROLL:
        return Proposal(
            VERSION,
            {"geometry": {"rotate_deg": 0.0}},
            confidence,
            f"lines lean {roll:+.1f} degrees: probably deliberate, left as shot",
            as_shot=as_shot,
            evidence=evidence,
            flags=["deliberate tilt?"],
        )
    angle = roll
    while abs(angle) >= MIN_ROLL and not all(face_kept(f, angle, w, h) for f in ctx.trusted_faces):
        angle *= 0.8
    note = ""
    if abs(angle) < MIN_ROLL:
        flags.append("straightening would cut a face")
        return Proposal(
            VERSION,
            {"geometry": {"rotate_deg": 0.0}},
            confidence,
            f"lines lean {roll:+.1f} degrees, but straightening would cut a face",
            as_shot=as_shot,
            evidence=evidence,
            flags=flags,
        )
    if abs(angle - roll) > 0.01:
        note = f" (reduced from {roll:+.2f} to keep faces in the frame)"
        confidence *= 0.8
    direction = "counter-clockwise" if angle > 0 else "clockwise"
    return Proposal(
        VERSION,
        {"geometry": {"rotate_deg": round(angle, 2)}},
        confidence,
        f"turned {abs(angle):.2f} degrees {direction} to level the lines{note}",
        as_shot=as_shot,
        evidence=evidence,
        flags=flags,
    )
