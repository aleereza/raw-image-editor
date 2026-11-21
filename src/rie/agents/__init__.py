"""The edit agents of Phase 2 (plan §4.5-§4.10): deterministic, versioned functions `propose(ctx) -> Proposal`.

- `baseline`: lens corrections, demosaicing, capture sharpening and noise reduction by effective ISO (rules).
- `geometry`: straightening from line segments on the lens-corrected render, with a face-aware crop check.
- `wb`: white balance from the linear raw, with locus, skin and flash priors, and warm ambience kept partway.
- `exposure`: exposure, highlight compression and shadows from the render, faces first.
- `color`: modest vibrance from chroma statistics, with skin protection and caps.
- `looks`: named tone and colour settings; the neutral look is the default.
- `harmonize`: white balance and exposure made consistent across a scene.
- `qa`: checks on the actual render, which may adjust a photo once.

`rie.edit` runs them over a folder. Every threshold here is provisional until the golden set exists (plan §9);
ADR 0012 lists them.
"""
