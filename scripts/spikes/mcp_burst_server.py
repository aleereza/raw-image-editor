"""Spike f: a minimal MCP server for Claude's culling pass, built on the official MCP Python SDK (2.x).

Tools: `list_bursts`, `get_burst_sheet` (a numbered sheet of a burst's frames from the camera's embedded JPEGs,
with each frame's faces below it) and `suggest_frame` (records the suggestion as a JSON line). It only reads the
NEFs. Started by Claude Code over stdio; configured through environment variables:

  RIE_SAMPLES   folder with the NEFs
  RIE_BURSTS    JSON object: burst name -> list of NEF file names, in shooting order
  RIE_OUT       folder for suggestions.jsonl and copies of the sheets
  RIE_MODELS    folder with yunet/face_detection_yunet_2023mar.onnx (optional: no face strips without it)
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import cv2
import numpy as np
import rawpy
from mcp.server.mcpserver import Image, MCPServer

SAMPLES = Path(os.environ["RIE_SAMPLES"])
BURSTS: dict[str, list[str]] = json.loads(os.environ["RIE_BURSTS"])
OUT = Path(os.environ["RIE_OUT"])
MODELS = Path(os.environ.get("RIE_MODELS", ""))
FRAME_EDGE, FACE, SHEET_MAX = 760, 180, 1568
FLIP = {3: cv2.ROTATE_180, 5: cv2.ROTATE_90_COUNTERCLOCKWISE, 6: cv2.ROTATE_90_CLOCKWISE}

server = MCPServer(
    "rie",
    instructions="Photo culling helper. Sheets number frames from 1 in shooting order. Suggestions are only advice "
    "to the photographer; nothing is deleted.",
)


def upright_preview(nef: Path) -> np.ndarray:
    with rawpy.imread(str(nef)) as raw:
        data, flip = raw.extract_thumb().data, raw.sizes.flip
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
    assert img is not None
    return cv2.rotate(img, FLIP[flip]) if flip in FLIP else img


def faces(img: np.ndarray) -> list[np.ndarray]:
    model = MODELS / "yunet" / "face_detection_yunet_2023mar.onnx"
    if not model.is_file():
        return []
    s = 1600 / max(img.shape[:2])
    small = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    det = cv2.FaceDetectorYN.create(str(model), "", (small.shape[1], small.shape[0]), 0.7, 0.3, 50)
    _, found = det.detect(small)
    crops = []
    for f in sorted(found if found is not None else [], key=lambda f: f[0])[:5]:
        x, y, w, h = (f[:4] / s).astype(int)
        side = int(max(w, h) * 1.4)
        cx, cy = x + w // 2, y + h // 2
        crop = img[max(cy - side // 2, 0) : cy + side // 2, max(cx - side // 2, 0) : cx + side // 2]
        if crop.size:
            crops.append(cv2.resize(crop, (FACE, FACE), interpolation=cv2.INTER_AREA))
    return crops


def label(img: np.ndarray, text: str) -> None:
    cv2.rectangle(img, (0, 0), (90, 56), (0, 0, 0), -1)
    cv2.putText(img, text, (8, 44), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 3)


def sheet(burst: str) -> bytes:
    tiles = []
    for i, name in enumerate(BURSTS[burst], start=1):
        img = upright_preview(SAMPLES / name)
        s = FRAME_EDGE / max(img.shape[:2])
        frame = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        crops = faces(img)[: frame.shape[1] // (FACE + 4)]
        strip = FACE + 6 if crops else 0
        tile = np.full((frame.shape[0] + strip, frame.shape[1], 3), 255, np.uint8)
        tile[: frame.shape[0]] = frame
        for j, crop in enumerate(crops):
            tile[frame.shape[0] + 6 :, j * (FACE + 4) : j * (FACE + 4) + FACE] = crop
        label(tile, f"#{i}")
        tiles.append(tile)
    h = max(t.shape[0] for t in tiles)
    w = max(t.shape[1] for t in tiles)
    tiles = [
        cv2.copyMakeBorder(t, 0, h - t.shape[0] + 6, 0, w - t.shape[1] + 6, cv2.BORDER_CONSTANT, value=(255,) * 3)
        for t in tiles
    ]
    cols = 2 if len(tiles) <= 4 else 3
    while len(tiles) % cols:
        tiles.append(np.full_like(tiles[0], 255))
    full = np.vstack([np.hstack(tiles[i : i + cols]) for i in range(0, len(tiles), cols)])
    s = min(1.0, SHEET_MAX / max(full.shape[:2]))
    full = cv2.resize(full, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else full
    ok, buf = cv2.imencode(".jpg", full, [cv2.IMWRITE_JPEG_QUALITY, 85])
    assert ok
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"sheet-{burst}.jpg").write_bytes(buf.tobytes())
    return buf.tobytes()


@server.tool()
def list_bursts() -> list[dict]:
    """The bursts waiting for a suggestion, with their number of frames."""
    return [{"burst": name, "frames": len(frames)} for name, frames in BURSTS.items()]


@server.tool()
def get_burst_sheet(burst: str) -> Image:
    """A numbered sheet of the burst's frames (camera previews, about 760 px each) with each frame's faces below it."""
    if burst not in BURSTS:
        raise ValueError(f"unknown burst {burst!r}; call list_bursts")
    t = time.perf_counter()
    data = sheet(burst)
    with (OUT / "server-log.jsonl").open("a") as fh:
        fh.write(
            json.dumps(
                {
                    "tool": "get_burst_sheet",
                    "burst": burst,
                    "bytes": len(data),
                    "seconds": round(time.perf_counter() - t, 3),
                    "at": time.time(),
                }
            )
            + "\n"
        )
    return Image(data=data, format="jpeg")


@server.tool()
def suggest_frame(burst: str, best: int, reason: str, rejects: list[int] | None = None) -> str:
    """Record the best frame of a burst (1-based, as numbered on its sheet), a one-line reason, and the frames
    you would reject. This is a suggestion for the photographer, who makes the decision."""
    if burst not in BURSTS:
        raise ValueError(f"unknown burst {burst!r}")
    n = len(BURSTS[burst])
    if not 1 <= best <= n or any(not 1 <= r <= n for r in rejects or []):
        raise ValueError(f"frame numbers must be 1-{n}")
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "suggestions.jsonl").open("a") as fh:
        fh.write(
            json.dumps(
                {
                    "burst": burst,
                    "best": best,
                    "file": BURSTS[burst][best - 1],
                    "reason": reason,
                    "rejects": rejects or [],
                    "at": time.time(),
                }
            )
            + "\n"
        )
    return f"recorded: burst {burst}, frame {best}"


if __name__ == "__main__":
    server.run("stdio")
