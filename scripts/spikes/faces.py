"""Spike c: YuNet face detection plus MediaPipe Face Landmarker blendshapes (eyes, smile) and head pose.

Usage: uv run --group spikes python scripts/spikes/faces.py MODELS_DIR SAMPLES_DIR OUT_DIR [NEF ...]

Works on the full-size embedded JPEG (JpgFromRaw, 6000 x 4000), turned upright with the NEF's orientation.
YuNet runs three ways: on the whole frame at 1600 px and 2560 px (long edge), and on overlapping 1280 px tiles of a
4000 px copy, merged with non-maximum suppression. The Face Landmarker then runs on a square crop around every
face found by the tiled pass. Writes annotated previews and a sheet of face crops to OUT_DIR for a visual check,
and prints JSON lines. Nothing about who people are is computed.
"""

from __future__ import annotations

import json
import resource
import statistics
import sys
import time
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
import rawpy
from mediapipe.tasks import python as mp_tasks
from mediapipe.tasks.python import vision

SCORE, NMS = 0.6, 0.3
TILE, OVERLAP, TILED_EDGE = 1280, 320, 4000
FLIP = {0: None, 3: cv2.ROTATE_180, 5: cv2.ROTATE_90_COUNTERCLOCKWISE, 6: cv2.ROTATE_90_CLOCKWISE}


def embedded_jpeg(path: Path) -> np.ndarray:
    """The camera's full-size JPEG, upright (BGR).

    The embedded JPEG carries its own EXIF orientation, which OpenCV applies by default; ignore it and apply the
    NEF's orientation instead, so the result does not depend on the JPEG's own tags.
    """
    with rawpy.imread(str(path)) as raw:
        thumb = raw.extract_thumb()
        flip = raw.sizes.flip
    img = cv2.imdecode(np.frombuffer(thumb.data, np.uint8), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
    assert img is not None
    rotation = FLIP.get(flip)
    return img if rotation is None else cv2.rotate(img, rotation)


def scaled(img: np.ndarray, long_edge: int) -> tuple[np.ndarray, float]:
    s = long_edge / max(img.shape[:2])
    return cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA), s


def detect(detector: cv2.FaceDetectorYN, img: np.ndarray) -> np.ndarray:
    detector.setInputSize((img.shape[1], img.shape[0]))
    _, faces = detector.detect(img)
    return faces if faces is not None else np.zeros((0, 15), np.float32)


def detect_whole(detector: cv2.FaceDetectorYN, img: np.ndarray, long_edge: int) -> np.ndarray:
    small, s = scaled(img, long_edge)
    faces = detect(detector, small)
    faces[:, :14] /= s
    return faces


def detect_tiled(detector: cv2.FaceDetectorYN, img: np.ndarray) -> np.ndarray:
    big, s = scaled(img, TILED_EDGE)
    h, w = big.shape[:2]
    step = TILE - OVERLAP
    found = []
    for y in range(0, max(h - OVERLAP, 1), step):
        for x in range(0, max(w - OVERLAP, 1), step):
            tile = big[y : y + TILE, x : x + TILE]
            faces = detect(detector, tile)
            faces[:, [0, 4, 6, 8, 10, 12]] += x  # box corner and landmark x; columns 2-3 are width and height
            faces[:, [1, 5, 7, 9, 11, 13]] += y
            found.append(faces)
    # Faces bigger than a tile are only seen whole on a downscaled frame.
    found.append(detect_whole(detector, big, 1600))
    faces = np.concatenate(found) if found else np.zeros((0, 15), np.float32)
    if len(faces) == 0:
        return faces
    keep = cv2.dnn.NMSBoxes(faces[:, :4].tolist(), faces[:, 14].tolist(), SCORE, NMS)
    faces = faces[np.array(keep).ravel()]
    faces[:, :14] /= s
    return faces


def square_crop(img: np.ndarray, box: np.ndarray, grow: float = 1.8, size: int = 384) -> np.ndarray:
    x, y, w, h = box[:4]
    cx, cy, side = x + w / 2, y + h / 2, max(w, h) * grow
    x0, y0 = round(cx - side / 2), round(cy - side / 2)
    x1, y1 = round(cx + side / 2), round(cy + side / 2)
    pad = max(0, -x0, -y0, x1 - img.shape[1], y1 - img.shape[0])
    padded = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_CONSTANT) if pad else img
    crop = padded[y0 + pad : y1 + pad, x0 + pad : x1 + pad]
    return cv2.resize(crop, (size, size), interpolation=cv2.INTER_AREA if side > size else cv2.INTER_CUBIC)


def euler_degrees(m: np.ndarray) -> tuple[float, float, float]:
    """Yaw, pitch, roll from the landmarker's facial transformation matrix."""
    r = m[:3, :3]
    yaw = np.degrees(np.arctan2(-r[2, 0], np.hypot(r[2, 1], r[2, 2])))
    pitch = np.degrees(np.arctan2(r[2, 1], r[2, 2]))
    roll = np.degrees(np.arctan2(r[1, 0], r[0, 0]))
    return float(yaw), float(pitch), float(roll)


def maxrss_mb() -> int:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024


def main() -> None:
    models, samples, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    nefs = sys.argv[4:] or sorted(p.name for p in samples.glob("*.NEF"))
    out.mkdir(parents=True, exist_ok=True)
    detector = cv2.FaceDetectorYN.create(
        str(models / "yunet" / "face_detection_yunet_2023mar.onnx"), "", (320, 320), SCORE, NMS, 5000
    )
    landmarker = vision.FaceLandmarker.create_from_options(
        vision.FaceLandmarkerOptions(
            base_options=mp_tasks.BaseOptions(model_asset_path=str(models / "mediapipe" / "face_landmarker.task")),
            running_mode=vision.RunningMode.IMAGE,
            num_faces=1,
            min_face_detection_confidence=0.3,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=True,
        )
    )

    crops, labels, all_faces = [], [], []
    timing: dict[str, list[float]] = {
        "decode": [],
        "yunet_1600": [],
        "yunet_2560": [],
        "yunet_tiled": [],
        "landmarker": [],
    }
    for nef in nefs:
        t = time.perf_counter()
        img = embedded_jpeg(samples / nef)
        timing["decode"].append(time.perf_counter() - t)
        counts = {}
        for name in ("yunet_1600", "yunet_2560", "yunet_tiled"):
            t = time.perf_counter()
            if name == "yunet_tiled":
                faces = detect_tiled(detector, img)
            else:
                faces = detect_whole(detector, img, int(name.split("_")[1]))
            timing[name].append(time.perf_counter() - t)
            counts[name] = len(faces)
        tiled = faces  # the last pass
        preview, s = scaled(img, 1600)
        rows = []
        for i, face in enumerate(tiled):
            crop = square_crop(img, face)
            t = time.perf_counter()
            result = landmarker.detect(
                mp.Image(
                    image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
                )
            )
            timing["landmarker"].append(time.perf_counter() - t)
            row: dict[str, object] = {"face": i, "width_px": int(face[2]), "score": round(float(face[14]), 2)}
            if result.face_blendshapes:
                bs = {c.category_name: c.score for c in result.face_blendshapes[0]}
                yaw, pitch, roll = euler_degrees(np.asarray(result.facial_transformation_matrixes[0]))
                row.update(
                    blink_l=round(bs["eyeBlinkLeft"], 2),
                    blink_r=round(bs["eyeBlinkRight"], 2),
                    smile=round((bs["mouthSmileLeft"] + bs["mouthSmileRight"]) / 2, 2),
                    yaw=round(yaw),
                    pitch=round(pitch),
                    roll=round(roll),
                )
                scores = f"B{row['blink_l']:.2f}/{row['blink_r']:.2f} S{row['smile']:.2f} Y{yaw:+.0f}"
                text = f"{nef[4:8]}#{i} w{int(face[2])} {scores}"
            else:
                text = f"{nef[4:8]}#{i} w{int(face[2])} no landmarks"
            rows.append(row)
            crops.append(cv2.resize(crop, (160, 160)))
            labels.append(text)
            x, y, w, h = (face[:4] * s).astype(int)
            colour = (0, 200, 0) if "blink_l" in row else (0, 0, 255)
            cv2.rectangle(preview, (x, y), (x + w, y + h), colour, 2)
            cv2.putText(preview, str(i), (x, max(12, y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1)
        cv2.imwrite(str(out / f"faces-{Path(nef).stem}.jpg"), preview, [cv2.IMWRITE_JPEG_QUALITY, 85])
        all_faces += rows
        print(json.dumps({"nef": nef, **counts, "faces": rows, "maxrss_mb": maxrss_mb()}), flush=True)

    # A sheet of every face crop with its scores, eight per row.
    tiles = []
    for crop, text in zip(crops, labels, strict=True):
        tile = cv2.copyMakeBorder(crop, 0, 34, 0, 0, cv2.BORDER_CONSTANT)
        for j, part in enumerate((text[: len(text) // 2], text[len(text) // 2 :])):
            cv2.putText(tile, part, (2, 172 + 14 * j), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (0, 255, 255), 1)
        tiles.append(tile)
    while len(tiles) % 8:
        tiles.append(np.zeros_like(tiles[0]))
    sheet = np.vstack([np.hstack(tiles[i : i + 8]) for i in range(0, len(tiles), 8)])
    cv2.imwrite(str(out / "face-sheet.jpg"), sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])

    with_landmarks = [f for f in all_faces if "blink_l" in f]
    widths = sorted(int(str(f["width_px"])) for f in all_faces)
    small = [f for f in all_faces if int(str(f["width_px"])) < 120]
    print(
        json.dumps(
            {
                "faces_total": len(all_faces),
                "faces_with_landmarks": len(with_landmarks),
                "small_faces_under_120px": len(small),
                "small_faces_with_landmarks": sum("blink_l" in f for f in small),
                "face_width_px_quartiles": [
                    widths[len(widths) // 4],
                    widths[len(widths) // 2],
                    widths[3 * len(widths) // 4],
                ]
                if widths
                else [],
                "median_seconds": {k: round(statistics.median(v), 3) for k, v in timing.items() if v},
            }
        )
    )


if __name__ == "__main__":
    main()
