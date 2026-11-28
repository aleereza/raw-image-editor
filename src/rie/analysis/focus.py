"""Focus inspector v1: is the subject sharp? (plan §4.3)

The subject is, in order: the eyes of the face the camera focused on (the face holding the AF point, else the
biggest faces), the AF point the camera used (`PrimaryAFPoint`, ADR 0002), or the sharpest detail in the frame (for
manual focus, such as the Tokina macro). Its blur, from the raw sharpness map, gives the verdict:

- `sharp`: the subject's blur is at most SHARP;
- `missed`: the subject is soft while the background is clearly sharper (front or back focus);
- `motion`: the subject shows directional blur the background does not, or the shutter was slower than the
  handheld limit and the subject is soft;
- `shake`: most of the frame shows blur in one direction;
- `soft`: anything else that is not sharp; `unknown` when nothing in the subject region has enough detail.

A face away from the AF point usually is the subject: focusing with one point and then reframing moves the face away
from it. But when the camera focused on an object with a small face far behind it, the soft eyes are no clear miss.
When a folder is sorted, the AF point is therefore also measured on its own, from the blur map the catalog keeps
(`af_measures`). A missed focus is unclear when the AF point is sharp and no face lies near it (`unclear_miss`,
ADR 0019).

The thresholds are provisional: they were set on the Phase 0 samples, not on the golden set, which the owner has
not shot yet (plan §9's rule). `eval` fitting replaces them. Burst ranking compares frames with each other, which
matters more than these absolute values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .sharpness import SharpnessMap, best_blur, region

VERSION = "focus@1"
SHARP = 1.0  # blur (half-resolution pixels) at or below which the subject counts as sharp; provisional
SOFT = 1.8  # blur above which a soft subject is a technical failure on its own; provisional
MISSED_RATIO = 0.6  # background blur below this share of the subject's blur means focus landed elsewhere
DIRECTIONAL = 1.7  # anisotropy above which a tile's blur counts as directional
SHAKE_SHARE = 0.55  # share of textured tiles blurred in one shared direction that means camera shake
HANDHELD_FACTOR = 1.5  # handheld limit: 1 / (1.5 x focal length) seconds, no VR (plan §4.3)
AWAY = 4.0  # face sizes from the AF point beyond which a face is not where the camera focused; provisional (ADR 0019)

# D7100 51-point AF layout in the landscape sensor frame (normalized). Nikon says the points cover the 1.3x crop
# frame (the central 80 %); columns 1-11 and rows A-E are spread evenly over it. Estimated, not measured.
AF_PITCH_X = 0.067
AF_PITCH_Y = 0.115


def af_point_position(name: str) -> tuple[float, float] | None:
    """Centre of a named AF point (such as "C6") in the landscape sensor frame."""
    if len(name) < 2 or name[0] not in "ABCDE" or not name[1:].isdigit():
        return None
    row = "ABCDE".index(name[0])
    number = int(name[1:])
    column = number + 1 if name[0] in "AE" else number  # A1 and E1 sit above and below B2 and D2
    if not 1 <= column <= 11:
        return None
    return 0.5 + (column - 6) * AF_PITCH_X, 0.5 + (row - 2) * AF_PITCH_Y


def to_upright(u: float, v: float, orientation: int) -> tuple[float, float]:
    """A normalized point of the stored (sensor) frame in the upright frame."""
    if orientation in (3, 4):
        return 1 - u, 1 - v
    if orientation in (5, 6):  # turned 90 degrees clockwise for display
        return 1 - v, u
    if orientation in (7, 8):  # turned 90 degrees counter-clockwise for display
        return v, 1 - u
    return u, v


def af_box(name: str, orientation: int, crop_mode: str | None = None) -> list[float] | None:
    """The upright, normalized box one AF pitch wide and high around an AF point."""
    centre = af_point_position(name)
    if centre is None:
        return None
    u, v = centre
    w, h = AF_PITCH_X, AF_PITCH_Y
    if crop_mode == "1.3x":  # the points then cover the whole recorded frame
        u, v, w, h = (u - 0.1) / 0.8, (v - 0.1) / 0.8, w / 0.8, h / 0.8
    x, y = to_upright(u, v, orientation)
    if orientation in (5, 6, 7, 8):
        w, h = h, w
    return [max(x - w / 2, 0.0), max(y - h / 2, 0.0), w, h]


def _grow(box: list[float], factor: float) -> list[float]:
    x, y, w, h = box
    nw, nh = w * factor, h * factor
    return [x - (nw - w) / 2, y - (nh - h) / 2, nw, nh]


def _inside(point: tuple[float, float], box: list[float]) -> bool:
    return box[0] <= point[0] <= box[0] + box[2] and box[1] <= point[1] <= box[1] + box[3]


def _mask(shape: tuple[int, int], boxes: list[list[float]]) -> np.ndarray:
    rows, cols = shape
    mask = np.zeros(shape, dtype=bool)
    for x, y, w, h in boxes:
        c0, r0 = max(int(x * cols), 0), max(int(y * rows), 0)
        c1, r1 = min(int(np.ceil((x + w) * cols)), cols), min(int(np.ceil((y + h) * rows)), rows)
        mask[r0:r1, c0:c1] = True
    return mask


def af_blur(blur: np.ndarray, af: list[float]) -> float | None:
    """The AF point's own blur: the sharper part of the tiles under its box."""
    return best_blur(region(blur, tuple(af)), 0.3)  # type: ignore[arg-type]


def af_gap(af: list[float], box: list[float]) -> float:
    """How far the AF point's centre lies from a face's centre, in face sizes (each axis divided by the face's extent
    along it)."""
    x, y, w, h = box
    return float(np.hypot((af[0] + af[2] / 2 - x - w / 2) / w, (af[1] + af[3] / 2 - y - h / 2) / h))


def af_measures(blur: np.ndarray, af: list[float] | None, boxes: list[list[float]]) -> dict[str, float | None]:
    """The AF point on its own: its blur, and how far the nearest face box lies from it in face sizes (ADR 0019)."""
    if af is None:
        return {"af_blur": None, "af_gap": None}
    b = af_blur(blur, af)
    gaps = [af_gap(af, box) for box in boxes if box[2] > 0 and box[3] > 0]
    return {"af_blur": None if b is None else round(b, 3), "af_gap": round(min(gaps), 2) if gaps else None}


def unclear_miss(result: dict[str, Any]) -> str | None:
    """Why a missed focus on a face is no clear failure, or None when it is one.

    It is unclear when the AF point itself is sharp and no face lies within AWAY face sizes of it: the camera focused
    on something else, perhaps on purpose, such as an object with someone small behind it (ADR 0019). `result` is a
    focus result with the AF point's own measures (`af_measures`) added.
    """
    if result.get("verdict") != "missed" or result.get("subject") not in ("eyes", "face"):
        return None
    blur, gap = result.get("af_blur"), result.get("af_gap")
    if blur is None or blur > SHARP or gap is None or gap <= AWAY:
        return None
    where = "the eyes away from it are" if result["subject"] == "eyes" else "the face away from it is"
    return f"the AF point is sharp, but {where} soft"


def face_blur(sharp: SharpnessMap, face: dict[str, Any]) -> float | None:
    """Blur at the eyes (the sharper eye), or over the face when the eyes have no measurable detail."""
    x, y, w, h = face["box"]
    values = []
    for ex, ey in face.get("eyes") or []:
        eye = region(sharp.blur, (ex - 0.14 * w, ey - 0.09 * h, 0.28 * w, 0.18 * h))
        b = best_blur(eye, 0.0)
        if b is not None:
            values.append(b)
    if values:
        return round(min(values), 3)
    b = best_blur(region(sharp.blur, (x, y, w, h)), 0.2)
    return None if b is None else round(b, 3)


@dataclass
class FocusResult:
    verdict: str
    subject: str  # eyes, face, af, frame
    subject_blur: float | None
    background_blur: float | None
    subject_boxes: list[list[float]] = field(default_factory=list)
    af_box: list[float] | None = None
    shake: float = 0.0  # share of textured tiles blurred in the most common direction
    motion: float = 0.0  # median anisotropy in the subject region
    handheld_ratio: float | None = None  # exposure time over the handheld limit
    face_blur: list[float | None] = field(default_factory=list)
    reason: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"version": VERSION, **self.__dict__}


def inspect(
    sharp: SharpnessMap,
    faces: list[dict[str, Any]],
    meta: dict[str, Any],
    orientation: int,
) -> FocusResult:
    """The focus verdict for one photo from its sharpness map, faces and metadata."""
    blur = sharp.blur
    blurs = [face_blur(sharp, f) for f in faces]
    af = None
    manual = str(meta.get("focus_mode", "")).lower().startswith("manual")
    if not manual and meta.get("af_primary"):
        af = af_box(meta["af_primary"], orientation, meta.get("crop_mode"))

    subject, boxes, subject_blur = "frame", [], None
    if faces:
        chosen = []
        if af is not None:
            centre = (af[0] + af[2] / 2, af[1] + af[3] / 2)
            chosen = [i for i, f in enumerate(faces) if _inside(centre, _grow(f["box"], 1.4))]
        if not chosen:
            areas = [f["box"][2] * f["box"][3] for f in faces]
            biggest = max(areas)
            chosen = [i for i, a in enumerate(areas) if a >= 0.6 * biggest]
        measured = [b for b in (blurs[i] for i in chosen) if b is not None]
        if measured:
            subject_blur = min(measured)
            subject = "eyes" if any(faces[i].get("eyes") for i in chosen) else "face"
            boxes = [faces[i]["box"] for i in chosen]
    if subject_blur is None and af is not None:
        subject_blur = af_blur(blur, af)
        if subject_blur is not None:
            subject, boxes = "af", [af]
    if subject_blur is None:
        subject_blur = best_blur(blur, 0.05)
        subject, boxes = "frame", []

    outside = ~_mask(blur.shape, [_grow(b, 1.5) for b in boxes]) if boxes else np.ones(blur.shape, bool)
    background = best_blur(np.where(outside, blur, np.nan), 0.1) if boxes else None

    textured = ~np.isnan(sharp.anisotropy)
    directional = textured & (sharp.anisotropy >= DIRECTIONAL)
    shake = 0.0
    if textured.sum() >= 20:
        counts = [int((directional & (sharp.direction == d)).sum()) for d in (0, 45, 90, 135)]
        shake = max(counts) / int(textured.sum())
    inside = _mask(blur.shape, boxes) & textured if boxes else np.zeros(blur.shape, bool)
    motion = float(np.median(sharp.anisotropy[inside])) if inside.sum() >= 2 else 0.0

    handheld = None
    focal, exposure = meta.get("focal_length"), meta.get("exposure_time")
    if focal and exposure:
        handheld = round(float(exposure) * HANDHELD_FACTOR * float(focal), 3)

    result = FocusResult(
        verdict="unknown",
        subject=subject,
        subject_blur=None if subject_blur is None else round(subject_blur, 3),
        background_blur=None if background is None else round(background, 3),
        subject_boxes=boxes,
        af_box=af,
        shake=round(shake, 3),
        motion=round(motion, 3),
        handheld_ratio=handheld,
        face_blur=blurs,
    )
    if subject_blur is None:
        result.reason = "no measurable detail"
        return result
    where = {"eyes": "the eyes", "face": "the face", "af": "the AF point", "frame": "the sharpest detail"}[subject]
    if subject_blur <= SHARP:
        result.verdict, result.reason = "sharp", f"{where} {'are' if subject == 'eyes' else 'is'} sharp"
    elif background is not None and background <= SHARP and background < MISSED_RATIO * subject_blur:
        result.verdict, result.reason = "missed", f"focus missed: the background is sharper than {where}"
    elif shake >= SHAKE_SHARE:
        result.verdict, result.reason = "shake", "camera shake: the whole frame is blurred in one direction"
    elif motion >= DIRECTIONAL or (handheld is not None and handheld > 1.0):
        slow = f" at 1/{1 / float(exposure):.0f} s" if handheld is not None and handheld > 1.0 and exposure else ""
        result.verdict, result.reason = "motion", f"motion blur at {where}{slow}"
    else:
        result.verdict, result.reason = "soft", f"{where} {'are' if subject == 'eyes' else 'is'} soft"
    return result
