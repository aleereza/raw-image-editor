"""Faces, eyes and smiles with YuNet and MediaPipe Face Landmarker, as ADR 0003 decided.

- YuNet runs on the whole upright frame at 1600 px for every shot.
- Group shots (two or more faces) get a tiled pass on a 3000 px copy; a face found only there counts when the
  landmarker confirms it and it does not lie mostly inside a bigger face.
- Close-ups (a 100 mm lens, a face wider than a third of the frame, or no face at all) get a whole-frame pass at
  640 px; when YuNet still finds nothing on a long lens, the landmarker's own detector gets a try.
- The landmarker runs once per face on a square crop (1.8 times the face, 384 px) and gives the blink and smile
  blendshapes, the head pose, and the iris positions used to measure sharpness at the eyes.
- That crop can show someone else's face first, so a face counts for a box only when its irises lie inside the box
  (ADR 0012). A box beside another face, such as hands or a toy under a child's chin, gets nothing. When the face
  found first belongs to a neighbour, a second look that may find up to three faces takes the one inside the box,
  so an adult cheek to cheek with a child keeps their own eyes. A box where the first look finds no face gets no
  second look: allowed several answers, the landmarker also finds faces in feet and pumpkins.
- Two boxes that read the same face (the same irises): the one whose own eye points sit farther from it looks again
  for a face of its own, and is dropped when it has none (a close-up boxed twice).

Only geometry and expression scores are kept. Nothing about who a person is is computed.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..config import REPO_ROOT

MODELS = REPO_ROOT / "models"
YUNET = Path("yunet/face_detection_yunet_2023mar.onnx")
LANDMARKER = Path("mediapipe/face_landmarker.task")
SCORE, NMS = 0.6, 0.3
WHOLE_EDGE, TILED_EDGE, CLOSEUP_EDGE = 1600, 3000, 640
TILE, OVERLAP = 1280, 320
CROP_GROW, CROP_SIZE = 1.8, 384
LONG_LENS_MM = 85.0
CROP_FACES = 3  # faces the second look may find in one crop (ADR 0012)
EYES_INSIDE = 1.15  # a found face belongs to a box when both irises lie inside the box grown by this factor
SAME_FACE = 0.25  # two boxes read one face when their irises are closer than this share of the eye distance
VERSION = "faces@2"
# MediaPipe face mesh: iris centres (refined landmarks), one per eye.
IRIS = (468, 473)


class FaceModelsMissing(RuntimeError):
    """The model files are not in models/; `uv run python scripts/fetch_models.py` downloads them."""


@dataclass
class Face:
    box: list[float]  # x, y, w, h normalized to the upright frame
    score: float
    source: str  # whole, tiled, closeup, landmarker
    landmarks: list[float] = field(default_factory=list)  # YuNet's five points, normalized x, y pairs
    confirmed: bool = False  # the landmarker found a face in the crop
    blink_l: float | None = None
    blink_r: float | None = None
    smile: float | None = None
    yaw: float | None = None
    pitch: float | None = None
    roll: float | None = None
    eyes: list[list[float]] = field(default_factory=list)  # iris centres, normalized

    @property
    def cut_off(self) -> bool:
        x, y, w, h = self.box
        return x < 0.003 or y < 0.003 or x + w > 0.997 or y + h > 0.997

    def to_json(self) -> dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items() if v is not None}
        out["cut_off"] = self.cut_off
        return out


def models_available(models: Path = MODELS) -> bool:
    return (models / YUNET).is_file() and (models / LANDMARKER).is_file()


class FaceAnalyzer:
    """Holds the models; create one per process and reuse it (MediaPipe takes about 250 MB, the second look 40 MB)."""

    def __init__(self, models: Path = MODELS) -> None:
        if not models_available(models):
            raise FaceModelsMissing(f"face models missing in {models}; run `uv run python scripts/fetch_models.py`")
        from mediapipe.tasks import python as mp_tasks
        from mediapipe.tasks.python import vision

        self._detector = cv2.FaceDetectorYN.create(str(models / YUNET), "", (320, 320), SCORE, NMS, 5000)

        def landmarker(faces: int) -> Any:
            options = vision.FaceLandmarkerOptions(
                base_options=mp_tasks.BaseOptions(model_asset_path=str(models / LANDMARKER)),
                running_mode=vision.RunningMode.IMAGE,
                num_faces=faces,
                min_face_detection_confidence=0.3,
                output_face_blendshapes=True,
                output_facial_transformation_matrixes=True,
            )
            return vision.FaceLandmarker.create_from_options(options)

        self._landmarker = landmarker(1)
        self._second_look = landmarker(CROP_FACES)

    # -- detection --------------------------------------------------------------------------------------------

    def _detect(self, img: np.ndarray) -> np.ndarray:
        self._detector.setInputSize((img.shape[1], img.shape[0]))
        _, faces = self._detector.detect(img)
        return faces if faces is not None else np.zeros((0, 15), np.float32)

    def _whole(self, img: np.ndarray, edge: int) -> np.ndarray:
        scale = edge / max(img.shape[:2])
        small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else img
        faces = self._detect(small)
        if scale < 1:
            faces[:, :14] /= scale
        return faces

    def _tiled(self, img: np.ndarray) -> np.ndarray:
        scale = min(1.0, TILED_EDGE / max(img.shape[:2]))
        big = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else img
        h, w = big.shape[:2]
        step = TILE - OVERLAP
        found = []
        for y in range(0, max(h - OVERLAP, 1), step):
            for x in range(0, max(w - OVERLAP, 1), step):
                faces = self._detect(big[y : y + TILE, x : x + TILE])
                faces[:, [0, 4, 6, 8, 10, 12]] += x
                faces[:, [1, 5, 7, 9, 11, 13]] += y
                found.append(faces)
        faces = np.concatenate(found) if found else np.zeros((0, 15), np.float32)
        faces[:, :14] /= scale
        return faces

    # -- landmarks ----------------------------------------------------------------------------------------------

    def _landmarks(self, img: np.ndarray, face: Face, taken: Face | None = None) -> bool:
        """Read the face in a box (blink, smile, head pose, irises); whether the box got a face of its own.

        The first look finds one face in the crop, as ever. Only when that face lies outside the box (a neighbour's)
        does a second look find up to three and take the one inside the box. `taken` is a face another box has
        already read: then only the second look runs, and it skips that face.
        """
        import mediapipe as mp

        h, w = img.shape[:2]
        x, y, bw, bh = face.box[0] * w, face.box[1] * h, face.box[2] * w, face.box[3] * h
        cx, cy, side = x + bw / 2, y + bh / 2, max(bw, bh) * CROP_GROW
        x0, y0 = round(cx - side / 2), round(cy - side / 2)
        x1, y1 = round(cx + side / 2), round(cy + side / 2)
        pad = max(0, -x0, -y0, x1 - w, y1 - h)
        padded = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_CONSTANT) if pad else img
        crop = padded[y0 + pad : y1 + pad, x0 + pad : x1 + pad]
        if crop.size == 0:
            return False
        interpolation = cv2.INTER_AREA if side > CROP_SIZE else cv2.INTER_CUBIC
        crop = cv2.resize(crop, (CROP_SIZE, CROP_SIZE), interpolation=interpolation)
        rgb = np.ascontiguousarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        def irises(result: Any) -> list[list[tuple[float, float]]]:
            return [
                [((x0 + points[i].x * side) / w, (y0 + points[i].y * side) / h) for i in IRIS if i < len(points)]
                for points in result.face_landmarks
            ]

        result, found, k = None, [], None
        if taken is None:
            result = self._landmarker.detect(image)
            if not result.face_landmarks:
                return False
            found = irises(result)
            k = matching_face(face, found, (w, h))
        if k is None:
            result = self._second_look.detect(image)
            found = irises(result)
            if taken is not None:
                found = [[] if same_eyes(eyes, taken.eyes, (w, h)) else eyes for eyes in found]
            k = matching_face(face, found, (w, h))
        if result is None or k is None or k >= len(result.face_blendshapes):
            return False
        scores = {c.category_name: c.score for c in result.face_blendshapes[k]}
        face.confirmed = True
        face.blink_l = round(float(scores.get("eyeBlinkLeft", 0.0)), 3)
        face.blink_r = round(float(scores.get("eyeBlinkRight", 0.0)), 3)
        face.smile = round(
            (float(scores.get("mouthSmileLeft", 0.0)) + float(scores.get("mouthSmileRight", 0.0))) / 2, 3
        )
        face.yaw = face.pitch = face.roll = None
        if k < len(result.facial_transformation_matrixes):
            yaw, pitch, roll = euler_degrees(np.asarray(result.facial_transformation_matrixes[k]))
            face.yaw, face.pitch, face.roll = round(yaw, 1), round(pitch, 1), round(roll, 1)
        face.eyes = [[round(px, 4), round(py, 4)] for px, py in found[k]]
        return True

    def _one_per_face(self, img: np.ndarray, faces: list[Face]) -> list[Face]:
        """One box per face: of two boxes that read the same face, the one whose own eye points sit farther from it
        looks again for a face of its own (an adult beside the child they hold); with none, it is a second box on the
        same face (a close-up boxed twice) and is dropped."""
        size = (img.shape[1], img.shape[0])
        dropped: set[int] = set()
        confirmed = [f for f in faces if f.confirmed]
        for i, a in enumerate(confirmed):
            for b in confirmed[i + 1 :]:
                if id(a) in dropped or id(b) in dropped or not same_face(a, b, size):
                    continue
                second = second_reader(a, b, size)
                if not self._landmarks(img, second, taken=b if second is a else a):
                    dropped.add(id(second))
        return [f for f in faces if id(f) not in dropped]

    def _landmarker_detect(self, img: np.ndarray) -> list[Face]:
        """The landmarker's own detector on the whole frame, for close-ups YuNet misses."""
        import mediapipe as mp

        scale = CLOSEUP_EDGE / max(img.shape[:2])
        small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        rgb = np.ascontiguousarray(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
        result = self._landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
        faces = []
        for points in result.face_landmarks:
            xs, ys = [p.x for p in points], [p.y for p in points]
            x0, y0, x1, y1 = max(min(xs), 0.0), max(min(ys), 0.0), min(max(xs), 1.0), min(max(ys), 1.0)
            if x1 > x0 and y1 > y0:
                faces.append(Face(box=[x0, y0, x1 - x0, y1 - y0], score=0.5, source="landmarker"))
        return faces

    # -- the whole procedure ------------------------------------------------------------------------------------

    def analyze(self, img: np.ndarray, focal_length: float | None = None) -> list[Face]:
        """Faces in an upright BGR image (about 3000 px on the long edge works best)."""
        h, w = img.shape[:2]

        def to_faces(raw: np.ndarray, source: str) -> list[Face]:
            faces = []
            for f in raw:
                box = [float(f[0]) / w, float(f[1]) / h, float(f[2]) / w, float(f[3]) / h]
                landmarks = [round(float(f[4 + i]) / (w if i % 2 == 0 else h), 4) for i in range(10)]
                faces.append(Face(box=_clip_box(box), score=round(float(f[14]), 3), source=source, landmarks=landmarks))
            return faces

        faces = to_faces(self._whole(img, WHOLE_EDGE), "whole")
        long_lens = focal_length is not None and focal_length >= LONG_LENS_MM
        if not faces or long_lens or any(f.box[2] > 1 / 3 for f in faces):
            closeup = to_faces(self._whole(img, CLOSEUP_EDGE), "closeup")
            faces = _merge(faces, closeup)
        for face in faces:
            self._landmarks(img, face)
        faces = self._one_per_face(img, faces)
        if len(faces) >= 2:
            tiled = [f for f in to_faces(self._tiled(img), "tiled") if not _overlaps_any(f, faces, 0.3)]
            for face in tiled:
                self._landmarks(img, face)
            faces = self._one_per_face(img, faces + [f for f in tiled if f.confirmed and not _contained(f, faces)])
        if not faces and long_lens:
            faces = self._landmarker_detect(img)
            for face in faces:
                self._landmarks(img, face)
            faces = [f for f in faces if f.confirmed]
        faces.sort(key=lambda f: (f.box[0], f.box[1]))
        return faces


def euler_degrees(m: np.ndarray) -> tuple[float, float, float]:
    """Yaw, pitch and roll from the landmarker's facial transformation matrix."""
    r = m[:3, :3]
    yaw = math.degrees(math.atan2(-r[2, 0], math.hypot(r[2, 1], r[2, 2])))
    pitch = math.degrees(math.atan2(r[2, 1], r[2, 2]))
    roll = math.degrees(math.atan2(r[1, 0], r[0, 0]))
    return yaw, pitch, roll


def _clip_box(box: list[float]) -> list[float]:
    x, y, w, h = box
    x0, y0 = max(x, 0.0), max(y, 0.0)
    x1, y1 = min(x + w, 1.0), min(y + h, 1.0)
    return [round(x0, 4), round(y0, 4), round(max(x1 - x0, 0.0), 4), round(max(y1 - y0, 0.0), 4)]


def iou(a: list[float], b: list[float]) -> float:
    ax1, ay1, bx1, by1 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    iw = max(0.0, min(ax1, bx1) - max(a[0], b[0]))
    ih = max(0.0, min(ay1, by1) - max(a[1], b[1]))
    inter = iw * ih
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def _containment(small: list[float], big: list[float]) -> float:
    iw = max(0.0, min(small[0] + small[2], big[0] + big[2]) - max(small[0], big[0]))
    ih = max(0.0, min(small[1] + small[3], big[1] + big[3]) - max(small[1], big[1]))
    area = small[2] * small[3]
    return iw * ih / area if area > 0 else 0.0


def _overlaps_any(face: Face, faces: list[Face], threshold: float) -> bool:
    return any(iou(face.box, other.box) >= threshold for other in faces)


def _contained(face: Face, faces: list[Face]) -> bool:
    """A partial-face box lying mostly inside a bigger face (containment suppression, ADR 0003)."""
    area = face.box[2] * face.box[3]
    return any(
        other is not face and other.box[2] * other.box[3] > area and _containment(face.box, other.box) > 0.6
        for other in faces
    )


def _merge(first: list[Face], second: list[Face]) -> list[Face]:
    merged = list(first)
    for face in second:
        if not _overlaps_any(face, merged, 0.3) and not _contained(face, merged):
            merged.append(face)
    return merged


def _eye_target(face: Face) -> tuple[float, float]:
    """Where the detector put the eyes (between its two eye points), else the centre of the box."""
    marks = face.landmarks
    if len(marks) >= 4:
        return (marks[0] + marks[2]) / 2, (marks[1] + marks[3]) / 2
    x, y, w, h = face.box
    return x + w / 2, y + h / 2


def matching_face(face: Face, found: list[list[tuple[float, float]]], size: tuple[int, int]) -> int | None:
    """Which of the faces the landmarker found in a box's crop belongs to that box, if any (ADR 0012).

    `found` holds each face's two iris centres, normalized to the upright frame of `size` (width, height) pixels.
    The crop is 1.8 times the box, so it can hold a neighbour's face: a box on a child's hands sees the child's face
    above it, and an adult cheek to cheek with a child sees both faces. A face belongs to the box when both irises
    lie inside the box grown by EYES_INSIDE; of several, the one nearest the detector's own eye points wins.
    """
    w, h = size
    x, y, bw, bh = face.box
    cx, cy = x + bw / 2, y + bh / 2
    tx, ty = _eye_target(face)
    best, best_distance = None, math.inf
    for k, eyes in enumerate(found):
        if len(eyes) != 2 or not all(
            abs(ex - cx) <= bw * EYES_INSIDE / 2 and abs(ey - cy) <= bh * EYES_INSIDE / 2 for ex, ey in eyes
        ):
            continue
        distance = math.hypot(((eyes[0][0] + eyes[1][0]) / 2 - tx) * w, ((eyes[0][1] + eyes[1][1]) / 2 - ty) * h)
        if distance < best_distance:
            best, best_distance = k, distance
    return best


def same_eyes(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]], size: tuple[int, int]) -> bool:
    """Whether two pairs of iris centres are one face's: each lies within SAME_FACE of the eye distance of the other."""
    if len(a) != 2 or len(b) != 2:
        return False
    w, h = size

    def apart(p: Sequence[float], q: Sequence[float]) -> float:
        return math.hypot((p[0] - q[0]) * w, (p[1] - q[1]) * h)

    span = max(apart(a[0], a[1]), apart(b[0], b[1]))
    return span > 0 and max(apart(p, q) for p, q in zip(a, b, strict=True)) <= SAME_FACE * span


def same_face(a: Face, b: Face, size: tuple[int, int]) -> bool:
    """Whether two boxes read one face (the same irises)."""
    return same_eyes(a.eyes, b.eyes, size)


def _eye_offset(face: Face, size: tuple[int, int]) -> float:
    """Pixels between the middle of a face's irises and where its detector put the eyes."""
    w, h = size
    tx, ty = _eye_target(face)
    (lx, ly), (rx, ry) = face.eyes
    return math.hypot(((lx + rx) / 2 - tx) * w, ((ly + ry) / 2 - ty) * h)


def second_reader(a: Face, b: Face, size: tuple[int, int]) -> Face:
    """Of two boxes that read the same face, the one whose own eye points sit farther from it (then the lower score)."""
    return max((a, b), key=lambda f: (_eye_offset(f, size), -f.score))


def decode_upright(data: bytes, orientation: int, *, reduce: int = 2, apply_exif: bool = False) -> np.ndarray:
    """Decode a JPEG to BGR at 1/`reduce` size, turned upright with the NEF's orientation."""
    flags = {1: cv2.IMREAD_COLOR, 2: cv2.IMREAD_REDUCED_COLOR_2, 4: cv2.IMREAD_REDUCED_COLOR_4}[reduce]
    if not apply_exif:
        flags |= cv2.IMREAD_IGNORE_ORIENTATION
    img = cv2.imdecode(np.frombuffer(data, np.uint8), flags)
    if img is None:
        raise ValueError("could not decode the JPEG")
    rotation = {3: cv2.ROTATE_180, 6: cv2.ROTATE_90_CLOCKWISE, 8: cv2.ROTATE_90_COUNTERCLOCKWISE}.get(orientation)
    if rotation is not None and not apply_exif:
        img = cv2.rotate(img, rotation)
    return img
