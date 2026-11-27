"""Technical baseline v1: rules, not AI (plan §4.9).

The base profile `d7100-base@1` already holds most of it: lensfun's automatic lens corrections (distortion and
vignetting; lateral chromatic aberration is left to raw CA correction), AMaZE demosaicing (ADR 0001 kept it over
RCD) and capture sharpening with an automatic radius. This agent adds noise reduction by **effective ISO**, the
camera's ISO times 2 to the power of the exposure push, from a provisional table, and says what the lens
corrections can do for the lens: lensfun has no vignetting data for some lenses (the Tokina 100 mm macro among
them, ADR 0001), and a lens lensfun does not know gets no correction at all.

Output sharpening is not added: the export is full size, where capture sharpening is the sharpening that matters.
Hot-pixel filtering for long exposures waits for a long exposure to measure it on.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from functools import cache
from pathlib import Path

from .context import ImageContext
from .proposal import Proposal

VERSION = "baseline@1"
# Effective ISO up to which each step applies: (luma, chroma) noise reduction. Provisional until the golden set.
NOISE_TABLE = ((1000, 0, 0), (2000, 0, 10), (4000, 10, 15), (8000, 20, 25), (float("inf"), 30, 35))
LENSFUN_DIRS = (Path("/usr/share/lensfun/version_1"), Path.home() / ".local/share/lensfun/updates/version_1")
# Lenses exiftool does not name ("Unknown (<lens ID>)"), by Nikon lens ID. RawTherapee matches the Tokina to
# lensfun's entry calibrated on a D7100, which has no vignetting data (ADR 0001).
KNOWN_LENS_IDS = {"1F 54 68 68 24 24 14 00": "Tokina AT-X M100 100mm f/2.8 Pro D Macro AF"}


def noise_reduction(effective_iso: float) -> tuple[int, int]:
    """(luma, chroma) noise reduction for an effective ISO."""
    for limit, luma, chroma in NOISE_TABLE:
        if effective_iso <= limit:
            return luma, chroma
    return NOISE_TABLE[-1][1], NOISE_TABLE[-1][2]


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9.]+", text.lower()) if w and w not in {"af-s", "dx", "nikkor", "nikon"}}


@cache
def _lensfun_lenses() -> tuple[tuple[str, frozenset[str]], ...]:
    """(model, calibration kinds) of every lens in the lensfun database on this machine."""
    lenses = []
    for folder in LENSFUN_DIRS:
        for path in sorted(folder.glob("*.xml")) if folder.is_dir() else []:
            try:
                root = ET.parse(path).getroot()
            except (ET.ParseError, OSError):
                continue
            for lens in root.iter("lens"):
                model = lens.findtext("model") or ""
                calibration = lens.find("calibration")
                kinds = frozenset(child.tag for child in calibration) if calibration is not None else frozenset()
                lenses.append((model, kinds))
    return tuple(lenses)


def lens_corrections(lens: str | None) -> frozenset[str] | None:
    """What lensfun can correct for a lens name from the metadata (distortion, tca, vignetting), or None."""
    if not lens:
        return None
    if lens.startswith("Unknown"):
        lens = KNOWN_LENS_IDS.get(lens[lens.find("(") + 1 : lens.rfind(")")].strip())
        if lens is None:
            return None
    wanted = _words(lens)
    best, score = None, 0.0
    for model, kinds in _lensfun_lenses():
        words = _words(model)
        if not words:
            continue
        overlap = len(wanted & words) / len(words | wanted)
        if overlap > score:
            best, score = kinds, overlap
    return best if score >= 0.5 else None


def propose(ctx: ImageContext, ev: float) -> Proposal:
    """`ev` is the exposure the photo will get; pushing it up makes noise more visible."""
    iso = float(ctx.meta.get("iso") or 100)
    effective = iso * 2 ** max(ev, 0.0)
    luma, chroma = noise_reduction(effective)
    lens = ctx.meta.get("lens")
    corrections = lens_corrections(lens)
    if corrections is None:
        lens_note = "lens not identified: lens corrections as RawTherapee finds them" if lens else "lens unknown"
    elif "vignetting" not in corrections:
        lens_note = "lens corrected for distortion; lensfun has no vignetting data for it"
    else:
        lens_note = "lens corrected for distortion and vignetting"
    push = f" pushed {ev:+.1f} EV" if ev > 0.05 else ""
    evidence = {"iso": iso, "effective_iso": round(effective), "lens": lens, "lensfun": sorted(corrections or [])}
    params: dict[str, dict[str, int]] = {}
    if luma or chroma:
        params["detail"] = {"nr_luma": luma, "nr_chroma": chroma}
        what = f"noise reduction (luma {luma}, chroma {chroma})"
    else:
        what = "no noise reduction needed"
    return Proposal(
        VERSION,
        params,
        1.0,
        f"ISO {iso:.0f}{push}, effective {effective:.0f}: {what}; {lens_note}",
        as_shot={"detail": {"nr_luma": 0, "nr_chroma": 0}} if params else {},
        evidence=evidence,
    )
