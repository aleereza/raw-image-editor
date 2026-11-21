"""Phase 2 spike: what the tone and colour keys of the looks do to a render (ADR 0013).

Renders one NEF with the base profile, then with each candidate key, and compares the renders pixel by pixel:
for every variant it prints the output level reached by pixels of the base render at a few input levels (sRGB,
0-1), the mean change in lightness and in chroma (CIELAB), and how many pixels clip. That shows whether a key
changes the render at all (RawTherapee ignores unknown keys silently) and in which space a tone curve acts.

Usage: uv run python scripts/spikes/rt_tone.py /path/to/DSC_0001.NEF OUTPUT_DIR [--size 1024]
The renders go to OUTPUT_DIR, which must be outside the repository.
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

from rie import config
from rie.agents import colorspace as cs
from rie.engine import pp3
from rie.render import engine, size_overlay

S_CURVE = "1;0;0;0.25;0.15;0.75;0.85;1;1;"
VARIANTS: dict[str, dict[str, dict[str, pp3.Value]]] = {
    "base": {},
    "film S": {"Exposure": {"CurveMode": "FilmLike", "Curve": S_CURVE}},
    "standard S": {"Exposure": {"CurveMode": "Standard", "Curve": S_CURVE}},
    "perceptual S": {"Exposure": {"CurveMode": "Perceptual", "Curve": S_CURVE}},
    "contrast +30": {"Exposure": {"Contrast": 30}},
    "black 500": {"Exposure": {"Black": 500}},
    "camera curve": {"Exposure": {"HistogramMatching": True}},
    "vibrance 40": {"Vibrance": {"Enabled": True, "Pastels": 40, "Saturated": 40, "PastSatTog": True}},
    "vibrance 40 skin": {
        "Vibrance": {"Enabled": True, "Pastels": 40, "Saturated": 40, "PastSatTog": True, "ProtectSkins": True}
    },
    "saturation 30": {"Exposure": {"Saturation": 30}},
    "hl compr 100": {"Exposure": {"Compensation": 1, "HighlightCompr": 100}},
    "ev +1": {"Exposure": {"Compensation": 1}},
    "hl compr 100 th 50": {"Exposure": {"Compensation": 1, "HighlightCompr": 100, "HighlightComprThreshold": 50}},
    "hl compr 100 th 80": {"Exposure": {"Compensation": 1, "HighlightCompr": 100, "HighlightComprThreshold": 80}},
    "hl compr 40": {"Exposure": {"Compensation": 1, "HighlightCompr": 40}},
    "shadows 30": {"Shadows & Highlights": {"Enabled": True, "Shadows": 30}},
    "neutral curve": {
        "Exposure": {
            "CurveMode": "Perceptual",
            "Curve": "1;0;0;0.05;0.045;0.15;0.165;0.3;0.36;0.46;0.56;0.65;0.76;0.85;0.92;1;1;",
        }
    },
}
LEVELS = (0.1, 0.2, 0.3, 0.46, 0.6, 0.75, 0.9)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("nef", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--size", type=int, default=1024)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    if repo in args.out.resolve().parents or args.out.resolve() == repo:
        print("the output folder must be outside the repository", file=sys.stderr)
        return 2
    cfg = config.load()
    rt = engine(cfg)
    args.out.mkdir(parents=True, exist_ok=True)
    base = cfg.rawtherapee.profiles_dir / "d7100-base.pp3"
    size = args.out / "size.pp3"
    size.write_text(pp3.dump(size_overlay(args.size)), encoding="utf-8")

    def render(item: tuple[str, dict[str, dict[str, pp3.Value]]]) -> tuple[str, np.ndarray]:
        label, profile = item
        slug = label.replace(" ", "-").replace("+", "p")
        extra = args.out / f"{slug}.pp3"
        extra.write_text(pp3.dump(pp3.stack(profile)), encoding="utf-8")
        result = rt.render(args.nef, [base, extra, size], args.out / f"{args.nef.stem}-{slug}.jpg")
        return label, np.asarray(Image.open(result.output).convert("RGB"), dtype=np.float32) / 255.0

    with ThreadPoolExecutor(4) as pool:
        renders = dict(pool.map(render, VARIANTS.items()))
    ref = renders["base"]
    ref_y = cs.encode_srgb(cs.luminance(cs.decode_srgb(ref)))
    ref_lab = cs.lab(cs.decode_srgb(ref))
    print(f"{'variant':18s} " + " ".join(f"{v:>5.2f}" for v in LEVELS) + "   dL*    dC*  clipped%")
    for label, img in renders.items():
        y = cs.encode_srgb(cs.luminance(cs.decode_srgb(img)))
        row = []
        for level in LEVELS:
            near = np.abs(ref_y - level) < 0.01
            row.append(f"{float(np.median(y[near])) if near.sum() > 50 else float('nan'):5.2f}")
        lab = cs.lab(cs.decode_srgb(img))
        dl = float(np.mean(lab[..., 0] - ref_lab[..., 0]))
        dc = float(np.mean(np.hypot(lab[..., 1], lab[..., 2]) - np.hypot(ref_lab[..., 1], ref_lab[..., 2])))
        clipped = float(np.mean(img.max(axis=2) >= 0.995)) * 100
        print(f"{label:18s} " + " ".join(row) + f"  {dl:5.1f}  {dc:5.1f}  {clipped:6.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
