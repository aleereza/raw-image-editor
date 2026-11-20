"""The rule-based sort (`rules@2`): a suggestion, kind, confidence and reason per photo, from the local checks.

In order of precedence:

1. Technical failures: an almost black or mostly blown frame, a clearly soft, shaken or motion-blurred subject, or
   a clearly missed focus.
2. A near-duplicate of a better frame of its burst (duplicates).
3. Closed eyes where another frame of the burst has fewer (eyes).
4. Any other frame of a burst that is not its best (expression and moment).
5. A face cut by the frame edge (composition), as a suggestion only.
6. Everything else is a keep: the best frame of a burst (expression) or a good single shot (technical).

The thresholds come from ADR 0008 and are provisional; the personal model learns the owner's own weighting.
`rules@2` is `rules@1` without its first rule, which kept photos rated in RawTherapee: the app ignores ratings
(ADR 0010).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ..analysis import focus

MODEL = "rules@2"


@dataclass
class Suggestion:
    decision: str  # keep or reject
    kind: str
    confidence: float  # 0-1: how sure the rule is
    reason: str
    features: dict[str, Any] = field(default_factory=dict)

    @property
    def reject_probability(self) -> float:
        p = 0.5 + self.confidence / 2
        return round(p if self.decision == "reject" else 1 - p, 4)


def _num(value: Any) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else math.nan


VERDICTS = ("sharp", "soft", "missed", "motion", "shake", "unknown")


def features(meta: dict, focus_result: dict, burst: dict, look: dict, faces: list[dict], extra: dict) -> dict[str, Any]:
    """The numbers the rules and the personal model see for one photo (NaN where unknown)."""
    sizes = [f["box"][2] * f["box"][3] for f in faces if f.get("box")]
    out: dict[str, Any] = {
        "subject_blur": _num(focus_result.get("subject_blur")),
        "background_blur": _num(focus_result.get("background_blur")),
        "shake": _num(focus_result.get("shake")),
        "motion": _num(focus_result.get("motion")),
        "handheld_ratio": _num(focus_result.get("handheld_ratio")),
        "faces": float(len(faces)),
        "faces_cut": float(sum(bool(f.get("cut_off")) for f in faces)),
        "face_size": max(sizes) if sizes else math.nan,
        "closed_eyes": _num(burst.get("closed_eyes")),
        "looking_away": _num(burst.get("looking_away")),
        "smile": _num(burst.get("smile")),
        "burst_size": _num(burst.get("size")),
        "rank": _num(burst.get("rank")),
        "gap": _num(burst.get("gap")),
        "sharp_rel": _num(burst.get("sharp_rel")),
        "is_best": float(burst.get("rank") == 1),
        "duplicate": float(bool(burst.get("duplicate_of"))),
        "luma": _num(look.get("luma")),
        "clip_high": _num(look.get("clip_high")),
        "clip_low": _num(look.get("clip_low")),
        "iso": _num(meta.get("iso")),
        "exposure_time": _num(meta.get("exposure_time")),
        "focal_length": _num(meta.get("focal_length")),
        "fnumber": _num(meta.get("fnumber")),
        "flash": float(bool(meta.get("flash_fired"))),
        "rt_trash": float(bool(extra.get("rt_trash"))),
    }
    verdict = focus_result.get("verdict", "unknown")
    for v in VERDICTS:
        out[f"verdict_{v}"] = float(verdict == v)
    return out


def suggest(
    meta: dict,
    focus_result: dict,
    burst: dict,
    look: dict,
    faces: list[dict],
    extra: dict,
    burst_frames: dict[int, tuple[int, int]] | None = None,
) -> Suggestion:
    """The rules' suggestion for one photo. `burst_frames` maps the burst's photos to (rank, position)."""
    f = features(meta, focus_result, burst, look, faces, extra)
    verdict = focus_result.get("verdict", "unknown")
    blur = focus_result.get("subject_blur")
    background = focus_result.get("background_blur")
    luma, clip_high = look.get("luma"), look.get("clip_high", 0.0)
    if luma is not None and luma < 0.03 and look.get("clip_low", 0.0) > 0.8:
        return Suggestion("reject", "technical", 0.95, "almost black frame", f)
    if clip_high is not None and clip_high > 0.6:
        return Suggestion("reject", "technical", 0.9, "mostly blown out", f)
    if blur is not None and verdict in ("soft", "motion", "shake") and blur >= focus.SOFT:
        return Suggestion("reject", "technical", 0.85, focus_result.get("reason") or verdict, f)
    if (
        verdict == "missed"
        and blur is not None
        and blur >= 1.4
        and background is not None
        and background <= focus.SHARP
    ):
        return Suggestion("reject", "technical", 0.85, focus_result.get("reason") or "focus missed", f)
    if extra.get("rt_trash"):
        return Suggestion("reject", "technical", 0.5, "in RawTherapee's trash", f)

    size = int(burst.get("size") or 1)
    rank = int(burst.get("rank") or 1)
    gap = float(burst.get("gap") or 0.0)
    if size > 1 and rank > 1:
        frames = burst_frames or {}
        better_twins = sorted(frames[d] for d in burst.get("duplicate_of", []) if d in frames and frames[d][0] < rank)
        if better_twins and gap >= 0.05:
            issues = burst.get("issues") or []
            twin = better_twins[0][1]
            text = f"almost the same as #{twin}, which is better" + (
                f"; this one: {', '.join(issues)}" if issues else ""
            )
            return Suggestion("reject", "duplicates", min(0.95, 0.8 + gap), text, f)
        if (burst.get("closed_eyes") or 0) > 0:
            return Suggestion("reject", "eyes", 0.75, burst.get("reason") or "eyes closed", f)
        return Suggestion(
            "reject", "expression", round(0.5 + 0.45 * min(gap / 0.3, 1.0), 3), burst.get("reason") or "", f
        )

    if faces and any(face.get("cut_off") for face in faces):
        largest = max(faces, key=lambda face: face["box"][2] * face["box"][3])
        if largest.get("cut_off"):
            return Suggestion("reject", "composition", 0.35, "a face is cut by the frame edge", f)

    if size > 1:
        return Suggestion("keep", "expression", 0.7, burst.get("reason") or f"best of {size}", f)
    if (burst.get("closed_eyes") or 0) > 0:
        return Suggestion("reject", "eyes", 0.6, "eyes closed", f)
    if verdict == "sharp":
        return Suggestion("keep", "technical", 0.8, focus_result.get("reason") or "sharp", f)
    return Suggestion("keep", "technical", 0.4, focus_result.get("reason") or "no clear problem found", f)
