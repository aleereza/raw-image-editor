"""Spike a: AMaZE vs RCD demosaicing on moiré-prone shots (fine stripes in clothing).

Usage: uv run --group spikes python scripts/spikes/rt_demosaic.py SAMPLES_DIR OUT_DIR NEF [NEF ...]

Renders each NEF at full size with both methods (base profile, so capture sharpening is on), measures false colour
as high-frequency chroma energy, and writes a side-by-side 100% crop of the region where the two differ most
(`compare-<NEF>.jpg`, left AMaZE, right RCD) for a visual check. Results are printed as JSON lines.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

from rie.engine import pp3
from rie.engine.rawtherapee import RawTherapee

REPO = Path(__file__).resolve().parents[2]
BASE = REPO / "profiles" / "rawtherapee" / "d7100-base.pp3"
CROP = 384


def false_colour(img_bgr: np.ndarray) -> np.ndarray:
    """Per-pixel high-frequency chroma: |a* - blur(a*)| + |b* - blur(b*)| in CIELAB."""
    lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    hf = 0.0
    for c in (1, 2):
        hf = hf + np.abs(lab[..., c] - cv2.GaussianBlur(lab[..., c], (0, 0), 3))
    return np.asarray(hf)


def main() -> None:
    samples, out, nefs = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:]
    out.mkdir(parents=True, exist_ok=True)
    rt = RawTherapee(settings_dir=out / "rt-settings")
    imgs = {}
    for method in ("amaze", "rcd"):
        prof = out / f"{method}.pp3"
        prof.write_text(pp3.dump({"RAW Bayer": {"Method": method}}), encoding="utf-8")
    for nef in nefs:
        row: dict[str, object] = {"nef": nef}
        for method in ("amaze", "rcd"):
            result = rt.render(samples / nef, [BASE, out / f"{method}.pp3"], out / f"{Path(nef).stem}-{method}.jpg")
            img = cv2.imread(str(result.output))
            assert img is not None
            imgs[method] = img
            row[f"{method}_s"] = round(result.seconds, 2)
            row[f"{method}_false_colour_mean"] = round(float(false_colour(img).mean()), 3)
        a, r = imgs["amaze"], imgs["rcd"]
        fa, fr = false_colour(a), false_colour(r)
        # Where do the methods disagree on false colour the most? Average over CROP-sized windows.
        diff = cv2.blur(np.abs(fa - fr), (CROP, CROP))
        y, x = np.unravel_index(
            int(np.argmax(diff[CROP // 2 : -CROP // 2, CROP // 2 : -CROP // 2])),
            (diff.shape[0] - CROP, diff.shape[1] - CROP),
        )
        win = (slice(y, y + CROP), slice(x, x + CROP))
        row["window_xy"] = [int(x), int(y)]
        row["window_false_colour"] = {"amaze": round(float(fa[win].mean()), 2), "rcd": round(float(fr[win].mean()), 2)}
        side = np.concatenate([a[win], np.full((CROP, 8, 3), 255, np.uint8), r[win]], axis=1)
        cv2.imwrite(str(out / f"compare-{Path(nef).stem}.jpg"), side, [cv2.IMWRITE_JPEG_QUALITY, 92])
        print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
