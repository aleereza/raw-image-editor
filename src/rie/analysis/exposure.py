"""Exposure verdicts for culling: is a frame too dark to keep (ADR 0018)?

The measures come from the camera's own JPEG as the grid thumbnail shows it (`look@2` in `rie.scan.previews`): the
median and the brightest 1 % of the frame's luminance, sRGB-encoded (0-1). A failed exposure, such as a flash that
did not fire or had not recharged, leaves nothing in the frame bright, while a low-key scene keeps its lamp or its
window. The brightest 1 % tells the two apart where the median alone does not.

- **very dark:** the brightest 1 % below 0.30 and the median below 0.12, a technical failure;
- **dark:** the brightest 1 % below 0.40 and the median below 0.15, which an edit may or may not rescue.

The thresholds come from one shoot (376 photos, where five failed flash frames lie 2 EV below every other photo)
and the 29 samples, and are provisional. Over-exposure keeps its old rule (more than 60 % blown out) until there
are examples to set one on.
"""

from __future__ import annotations

import math
from typing import Any

VERY_DARK = (0.30, 0.12)  # brightest 1 %, median
DARK = (0.40, 0.15)
# The camera's tone curve lifts the darkest tones, so a dark frame's JPEG median reads brighter than its raw file.
# log2(0.18 / linear median) minus this offset matched the raw files of six dark frames within 0.5 EV.
EV_OFFSET = 2.1


def verdict(median: float | None, bright: float | None) -> str | None:
    """The verdict ("very dark", "dark" or None) from the median and the brightest 1 % of a frame's luminance."""
    if median is None or bright is None:
        return None
    if bright < VERY_DARK[0] and median < VERY_DARK[1]:
        return "very dark"
    if bright < DARK[0] and median < DARK[1]:
        return "dark"
    return None


def look_verdict(look: dict[str, Any]) -> str | None:
    """The verdict for a photo's look measures; None when they predate `look@2`."""
    median, bright = look.get("lum_p50"), look.get("lum_p99")
    if not isinstance(median, int | float) or not isinstance(bright, int | float):
        return None
    return verdict(float(median), float(bright))


def ev_under(median: float) -> float:
    """Roughly how far a dark frame is under a normal exposure, in EV, from its JPEG median (only meaningful for
    frames already judged dark)."""
    linear = median / 12.92 if median <= 0.04045 else ((median + 0.055) / 1.055) ** 2.4
    return max(0.0, math.log2(0.18 / max(linear, 1e-4)) - EV_OFFSET)


def reason(look: dict[str, Any]) -> str | None:
    """A one-line reason such as "very dark: about 4 EV under", or None for a frame that isn't dark."""
    found = look_verdict(look)
    if found is None:
        return None
    return f"{found}: about {ev_under(float(look['lum_p50'])):.0f} EV under"
