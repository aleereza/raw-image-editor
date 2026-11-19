"""Per-photo analysis (sharpness, faces, focus verdicts) and per-folder analysis (grouping, burst ranking).

Coordinates are normalized to the upright frame (0-1 on each axis), so results from the raw decode, the embedded
JPEGs and the cached copies line up without caring about their pixel sizes. Nothing about who people are is
computed: faces are boxes, landmarks and expression scores, followed through a burst by position only.
"""
