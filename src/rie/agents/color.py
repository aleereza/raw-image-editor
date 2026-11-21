"""Colour agent v1: modest automatic vibrance (plan §4.8).

Colours are measured in the linear raw, balanced with the white balance the photo will get and converted to
CIELAB: muted photos (overcast, dim rooms) get some vibrance, colourful ones none. RawTherapee's vibrance protects
skin tones (`[Vibrance] ProtectSkins`, which ADR 0012 measured), and caps keep foliage from turning neon and
saturated colours inside the gamut. The look adds its own saturation and vibrance on top.
"""

from __future__ import annotations

import numpy as np

from . import colorspace as cs
from .context import ImageContext
from .proposal import Proposal

VERSION = "color@1"
TARGET_CHROMA = 24.0  # median CIELAB chroma below which vibrance is added; provisional
GAIN = 0.8  # vibrance per unit of chroma missing
MAX_VIBRANCE = 25  # the natural look's ceiling
FOLIAGE_HUE = (95.0, 150.0)
FOLIAGE_SHARE, FOLIAGE_CAP = 0.25, 8  # much saturated foliage: at most this much vibrance
VIVID_CHROMA, VIVID_SHARE, VIVID_CAP = 60.0, 0.05, 5  # many near-gamut colours: at most this much


def propose(ctx: ImageContext, multipliers: np.ndarray) -> Proposal:
    """`multipliers` are the camera multipliers of the white balance the photo will get (G = 1)."""
    lin = ctx.linear
    assert lin is not None
    pixels = lin.rgb[lin.valid]
    as_shot = {"color": {"vibrance": 0}}
    if len(pixels) < 500:
        return Proposal(
            VERSION, {"color": {"vibrance": 0}}, 0.0, "too little to measure", as_shot=as_shot, abstain=True
        )
    srgb = (pixels * np.asarray(multipliers, dtype=np.float32)) @ lin.camera.rgb_cam.T.astype(np.float32)
    y = cs.luminance(np.clip(srgb, 0, None))
    srgb = srgb * (0.18 / max(float(np.median(y)), 1e-6))  # judge colours at a normal exposure
    lab = cs.lab(np.clip(srgb, 0, 1))
    usable = (lab[:, 0] > 20) & (lab[:, 0] < 92)
    if usable.sum() < 300:
        return Proposal(
            VERSION, {"color": {"vibrance": 0}}, 0.0, "too little to measure", as_shot=as_shot, abstain=True
        )
    hue, chroma = cs.hue_chroma(lab[usable])
    median = float(np.median(chroma))
    foliage = float(np.mean((hue >= FOLIAGE_HUE[0]) & (hue <= FOLIAGE_HUE[1]) & (chroma > 25)))
    vivid = float(np.mean(chroma > VIVID_CHROMA))
    vibrance = round(min(MAX_VIBRANCE, max(0.0, (TARGET_CHROMA - median) * GAIN)))
    caps = []
    if foliage > FOLIAGE_SHARE and vibrance > FOLIAGE_CAP:
        vibrance, caps = FOLIAGE_CAP, [*caps, "capped for foliage"]
    if vivid > VIVID_SHARE and vibrance > VIVID_CAP:
        vibrance, caps = VIVID_CAP, [*caps, "capped for vivid colours"]
    evidence = {"median_chroma": round(median, 1), "foliage": round(foliage, 3), "vivid": round(vivid, 3)}
    note = f"; {', '.join(caps)}" if caps else ""
    if vibrance < 3:
        return Proposal(
            VERSION,
            {"color": {"vibrance": 0}},
            0.85,
            f"colours left as they are (median chroma {median:.0f}){note}",
            as_shot=as_shot,
            evidence=evidence,
        )
    return Proposal(
        VERSION,
        {"color": {"vibrance": vibrance, "protect_skin": True}},
        0.85,
        f"vibrance +{vibrance}: muted colours (median chroma {median:.0f}), skin protected{note}",
        as_shot={"color": {"vibrance": 0, "protect_skin": False}},
        evidence=evidence,
    )
