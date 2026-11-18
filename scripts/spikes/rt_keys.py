"""Spike a: confirm which .pp3 keys RawTherapee 5.13 honours, by rendering and measuring the pixels.

Usage: uv run --group spikes python scripts/spikes/rt_keys.py SAMPLES_DIR OUT_DIR

Every render stacks the repository's base profile and one small test profile, so the checks also exercise
`-p base.pp3 -p image.pp3`. Results are printed as JSON lines; renders stay in OUT_DIR, outside the repository.
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
DCP = "/usr/share/rawtherapee/dcpprofiles/NIKON D7100.dcp"
SMALL = {
    "Resize": {
        "Enabled": True,
        "AppliesTo": "Cropped area",
        "Method": "Lanczos",
        "DataSpecified": 4,
        "LongEdge": 1200,
        "AllowUpscaling": False,
    }
}


def srgb_to_linear(img: np.ndarray) -> np.ndarray:
    x = img.astype(np.float64) / 255.0
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


class Spike:
    def __init__(self, samples: Path, out: Path) -> None:
        self.samples, self.out = samples, out
        self.rt = RawTherapee(settings_dir=out / "rt-settings")
        out.mkdir(parents=True, exist_ok=True)

    def render(self, nef: str, name: str, profile: pp3.Profile | dict, *, small: bool = True) -> np.ndarray:
        test = self.out / f"{name}.pp3"
        test.write_text(pp3.dump(pp3.stack(profile, SMALL) if small else pp3.stack(profile)), encoding="utf-8")
        result = self.rt.render(self.samples / nef, [BASE, test], self.out / f"{name}.jpg", quality=95)
        img = cv2.imread(str(result.output), cv2.IMREAD_COLOR_RGB)
        assert img is not None
        return img

    @staticmethod
    def report(check: str, **values: object) -> None:
        print(json.dumps({"check": check, **values}, default=str))


def rotation_sign(ref: np.ndarray, rotated: np.ndarray, degrees: float) -> dict:
    """Which rotation of the reference (about the centre, scaled to fill) best matches RawTherapee's output."""
    h, w = ref.shape[:2]
    a = np.radians(abs(degrees))
    fill = np.cos(a) + max(w / h, h / w) * np.sin(a)  # scale that hides the corners of a rotated w x h frame
    scores = {}
    for sign in (+1, -1):
        m = cv2.getRotationMatrix2D((w / 2, h / 2), sign * abs(degrees), fill)  # OpenCV: positive = counter-clockwise
        warped = cv2.warpAffine(ref, m, (w, h), flags=cv2.INTER_LINEAR)
        g1 = cv2.cvtColor(warped, cv2.COLOR_RGB2GRAY).astype(np.float64)
        g2 = cv2.cvtColor(rotated, cv2.COLOR_RGB2GRAY).astype(np.float64)
        scores["ccw" if sign > 0 else "cw"] = float(np.corrcoef(g1.ravel(), g2.ravel())[0, 1])
    return scores


def main() -> None:
    samples, out = Path(sys.argv[1]), Path(sys.argv[2])
    s = Spike(samples, out)
    landscape, portrait, macro = "DSC_3324.NEF", "DSC_3361.NEF", "DSC_2970.NEF"

    # 1. Stacking basics: comments, unknown sections and misspelt keys.
    base = s.render(landscape, "base", {})
    typo = s.render(landscape, "typo", {"Exposure": {"Compensatoin": 2}, "Bogus Section": {"Foo": 1}})
    s.report("unknown keys and sections are ignored silently", identical=bool(np.array_equal(base, typo)))
    plus1 = s.render(landscape, "ev+1", {"Exposure": {"Compensation": 1}})
    ratio = srgb_to_linear(plus1).mean() / srgb_to_linear(base).mean()
    s.report("[Exposure] Compensation=1", linear_mean_ratio=round(float(ratio), 3))

    # 2. Input profile: the bundled DCP, auto-matched or named, against the camera's standard matrix.
    by_name = s.render(landscape, "dcp-file", {"Color Management": {"InputProfile": f"file:{DCP}"}})
    camera = s.render(landscape, "camera-matrix", {"Color Management": {"InputProfile": "(camera)"}})
    s.report(
        "[Color Management] InputProfile",
        cameraICC_vs_file_identical=bool(np.array_equal(base, by_name)),
        camera_matrix_mean_abs_diff=round(float(np.abs(base.astype(int) - camera.astype(int)).mean()), 2),
    )

    # 3. White balance: custom temperature and green against the camera's as-shot values.
    rows = []
    for temp in (3500, 5000, 6500):
        for green in (0.9, 1.0, 1.1):
            img = srgb_to_linear(
                s.render(
                    landscape,
                    f"wb-{temp}-{green}",
                    {"White Balance": {"Setting": "Custom", "Temperature": temp, "Green": green}},
                )
            )
            r, g, b = (img[..., c].mean() for c in range(3))
            rows.append({"K": temp, "green": green, "R/G": round(r / g, 3), "B/G": round(b / g, 3)})
    s.report("[White Balance] Setting=Custom Temperature Green", grid=rows)

    # 4. Highlight compression, shadows, vibrance, saturation: each must change the render.
    for name, prof in {
        "hlcompr": {"Exposure": {"Compensation": 1, "HighlightCompr": 100}},
        "shadows": {"Shadows & Highlights": {"Enabled": True, "Shadows": 40}},
        "vibrance": {"Vibrance": {"Enabled": True, "Pastels": 40, "Saturated": 40}},
        "saturation": {"Exposure": {"Saturation": 30}},
    }.items():
        img = s.render(landscape, name, prof)
        ref = plus1 if name == "hlcompr" else base
        s.report(f"{name}: {prof}", mean_abs_diff=round(float(np.abs(img.astype(int) - ref.astype(int)).mean()), 2))

    # 5. Fine rotation: direction of a positive Degree, and whether the frame keeps its size (AutoFill).
    rotated = s.render(landscape, "rot+3", {"Rotation": {"Degree": 3}})
    s.report(
        "[Rotation] Degree=3", size=rotated.shape[:2], base_size=base.shape[:2], match=rotation_sign(base, rotated, 3)
    )

    # 6. Crop: output size, and the frame its coordinates refer to (portrait shots are stored landscape).
    full = s.render(portrait, "portrait-full", {}, small=False)
    crop = {"Crop": {"Enabled": True, "X": 100, "Y": 200, "W": 1000, "H": 1500, "FixedRatio": False}}
    cropped = s.render(portrait, "portrait-crop", crop, small=False)
    patch = full[200:1700, 100:1100]
    same = patch.shape == cropped.shape and float(np.abs(patch.astype(int) - cropped.astype(int)).mean()) < 2.0
    s.report(
        "[Crop] X Y W H on a portrait shot",
        full_size=full.shape[:2],
        crop_size=cropped.shape[:2],
        crop_matches_displayed_frame=same,
    )
    both = s.render(portrait, "portrait-rot-crop", {**crop, "Rotation": {"Degree": 2}}, small=False)
    s.report("[Crop] with [Rotation]", size=both.shape[:2])

    # 7. Lens correction from lensfun: auto-matched on both lenses, compared with none.
    for nef in (landscape, macro):
        none = s.render(nef, f"lens-none-{nef}", {"LensProfile": {"LcMode": "none"}})
        auto = s.render(nef, f"lens-auto-{nef}", {"LensProfile": {"LcMode": "lfauto"}})
        h, w = none.shape[:2]
        corner = (slice(0, h // 8), slice(0, w // 8))
        s.report(
            f"[LensProfile] LcMode=lfauto on {nef}",
            mean_abs_diff=round(float(np.abs(auto.astype(int) - none.astype(int)).mean()), 2),
            corner_brightness_ratio=round(
                float(srgb_to_linear(auto[corner]).mean() / srgb_to_linear(none[corner]).mean()), 3
            ),
        )
    # lensfun 0.3.4 has two entries for the Tokina: one calibrated on a D7100 (distortion and TCA only) and one
    # calibrated on a full-frame body (with vignetting). Which one does lfauto pick?
    auto_macro = cv2.imread(str(out / f"lens-auto-{macro}.jpg"), cv2.IMREAD_COLOR_RGB)
    none_macro = cv2.imread(str(out / f"lens-none-{macro}.jpg"), cv2.IMREAD_COLOR_RGB)
    assert auto_macro is not None and none_macro is not None
    for i, lens in enumerate(
        ("Tokina AT-X M100 100mm f/2.8 Pro D Macro AF", "Tokina AF 100mm f/2.8 AT-X Pro D M100 Macro")
    ):
        manual = s.render(
            macro,
            f"lens-manual-{i}",
            {
                "LensProfile": {
                    "LcMode": "lfmanual",
                    "LFCameraMake": "Nikon Corporation",
                    "LFCameraModel": "Nikon D7100",
                    "LFLens": lens,
                }
            },
        )
        h, w = manual.shape[:2]
        corner = (slice(0, h // 8), slice(0, w // 8))
        s.report(
            f"[LensProfile] lfmanual {lens!r}",
            identical_to_lfauto=bool(np.array_equal(manual, auto_macro)),
            diff_vs_lfauto=round(float(np.abs(manual.astype(int) - auto_macro.astype(int)).mean()), 3),
            diff_vs_none=round(float(np.abs(manual.astype(int) - none_macro.astype(int)).mean()), 3),
            corner_brightness_ratio=round(
                float(srgb_to_linear(manual[corner]).mean() / srgb_to_linear(none_macro[corner]).mean()), 3
            ),
        )

    # 8. Resize by long edge on landscape and portrait shots.
    for nef in (landscape, portrait):
        img = s.render(nef, f"resize-{nef}", {"Resize": {**SMALL["Resize"], "LongEdge": 2048}}, small=False)
        s.report(f"[Resize] DataSpecified=4 LongEdge=2048 on {nef}", size=img.shape[:2])


if __name__ == "__main__":
    main()
