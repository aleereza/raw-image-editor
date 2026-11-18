"""Spike a: time rawtherapee-cli renders at 2048 px and full size, full pipeline and `-f` fast export.

Usage: uv run --group spikes python scripts/spikes/rt_timing.py SAMPLES_DIR OUT_DIR [NEF ...]

The fast-export pipeline reads its settings from RawTherapee's preferences, so this spike gives it an app-owned
settings folder whose fast-export keys match the base profile (auto-matched DCP, Rec2020, 2048 px), and compares its
output with the full pipeline's. Results are printed as JSON lines.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

from rie.engine import pp3
from rie.engine.rawtherapee import RawTherapee

REPO = Path(__file__).resolve().parents[2]
BASE = REPO / "profiles" / "rawtherapee" / "d7100-base.pp3"
DEFAULT_NEFS = ["DSC_3324.NEF", "DSC_3361.NEF", "DSC_2970.NEF", "DSC_2702.NEF"]

FAST_OPTIONS = {
    "fastexport_icm_input": "(cameraICC)",
    "fastexport_icm_working": "Rec2020",
    "fastexport_icm_output": "RTv4_sRGB",
    "fastexport_resize_enabled": "true",
    "fastexport_resize_appliesTo": "Cropped area",
    "fastexport_resize_dataspec": "3",
    "fastexport_resize_width": "2048",
    "fastexport_resize_height": "2048",
}


def configure_fast_export(rt: RawTherapee, demosaic: str) -> None:
    """Rewrite the fast-export keys in the app-owned `options` file (RawTherapee creates it on first run)."""
    options = rt.settings_dir / "options"
    if not options.exists():
        rt.version()  # any run creates the file
    text = options.read_text(encoding="utf-8")
    for key, value in {**FAST_OPTIONS, "fastexport_raw_bayer_method": demosaic}.items():
        text, n = re.subn(rf"^{key}=.*$", f"{key}={value}", text, flags=re.M)
        if n != 1:
            raise SystemExit(f"{key} not found in {options}")
    options.write_text(text, encoding="utf-8")


def timed(rt: RawTherapee, nef: Path, profiles: list[Path], out: Path, *, fast: bool = False, runs: int = 2) -> float:
    times = []
    for _ in range(runs):
        times.append(rt.render(nef, profiles, out, quality=95, subsampling=3, fast=fast).seconds)
    return statistics.median(times)


def main() -> None:
    samples, out = Path(sys.argv[1]), Path(sys.argv[2])
    nefs = sys.argv[3:] or DEFAULT_NEFS
    out.mkdir(parents=True, exist_ok=True)
    rt = RawTherapee(settings_dir=out / "rt-settings")
    overlays = {
        "2048": {
            "Resize": {
                "Enabled": True,
                "AppliesTo": "Cropped area",
                "Method": "Lanczos",
                "DataSpecified": 4,
                "LongEdge": 2048,
                "AllowUpscaling": False,
            }
        },
        "rcd": {"RAW Bayer": {"Method": "rcd"}},
    }
    paths = {}
    for name, prof in overlays.items():
        paths[name] = out / f"{name}.pp3"
        paths[name].write_text(pp3.dump(prof), encoding="utf-8")
    rt.version()  # warm up: creates the settings folder

    for nef_name in nefs:
        nef = samples / nef_name
        row: dict[str, object] = {"nef": nef_name}
        row["full_amaze"] = timed(rt, nef, [BASE], out / "full.jpg")
        row["full_rcd"] = timed(rt, nef, [BASE, paths["rcd"]], out / "full-rcd.jpg")
        row["2048_amaze"] = timed(rt, nef, [BASE, paths["2048"]], out / "p2048.jpg")
        row["2048_rcd"] = timed(rt, nef, [BASE, paths["rcd"], paths["2048"]], out / "p2048-rcd.jpg")
        for demosaic in ("fast", "rcd", "amaze"):
            configure_fast_export(rt, demosaic)
            row[f"f_2048_{demosaic}"] = timed(rt, nef, [BASE], out / f"f-{demosaic}.jpg", fast=True)
        ref = cv2.imread(str(out / "p2048.jpg"), cv2.IMREAD_COLOR_RGB)
        assert ref is not None
        for demosaic in ("fast", "rcd", "amaze"):
            img = cv2.imread(str(out / f"f-{demosaic}.jpg"), cv2.IMREAD_COLOR_RGB)
            assert img is not None
            if img.shape != ref.shape:
                img = cv2.resize(img, (ref.shape[1], ref.shape[0]), interpolation=cv2.INTER_AREA)
            row[f"f_{demosaic}_vs_full_mean_abs_diff"] = round(
                float(np.abs(img.astype(int) - ref.astype(int)).mean()), 2
            )
            row[f"f_{demosaic}_size"] = img.shape[:2]
        row = {k: round(v, 2) if isinstance(v, float) else v for k, v in row.items()}
        print(json.dumps(row), flush=True)

    # Throughput with several renders at once (the plan sizes the pool at 2-3 processes).
    for workers in (1, 2, 3, 4):
        jobs = [(samples / n, i) for i, n in enumerate(nefs * 2)]
        start = time.perf_counter()
        with ThreadPoolExecutor(workers) as pool:
            list(pool.map(lambda j: rt.render(j[0], [BASE, paths["2048"]], out / f"par-{j[1]}.jpg"), jobs))
        wall = time.perf_counter() - start
        print(
            json.dumps(
                {
                    "parallel_2048_amaze": workers,
                    "renders": len(jobs),
                    "wall_s": round(wall, 2),
                    "s_per_image": round(wall / len(jobs), 2),
                }
            ),
            flush=True,
        )
        start = time.perf_counter()
        with ThreadPoolExecutor(workers) as pool:
            list(pool.map(lambda j: rt.render(j[0], [BASE], out / f"parfull-{j[1]}.jpg"), jobs))
        wall = time.perf_counter() - start
        print(
            json.dumps(
                {
                    "parallel_full_amaze": workers,
                    "renders": len(jobs),
                    "wall_s": round(wall, 2),
                    "s_per_image": round(wall / len(jobs), 2),
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
