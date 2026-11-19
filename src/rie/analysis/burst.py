"""Burst ranking v1: which frame of a burst is best, and why (plan §4.4).

Faces are followed through the burst by position (box overlap), never by who they are. For each frame:

- each person scores eyes open x facing the camera x face sharpness relative to that person's sharpest frame;
- blinks are judged against the same person's other frames, because laughing or looking down scores like
  half-closed eyes (ADR 0003); a single frame only counts eyes as closed when the score is very high;
- people combine through a soft minimum, so one blink spoils a group shot; a person missing from a frame counts
  as half;
- smiles add a small bonus, and blur, motion and clipped highlights cost points.

Frames without people score on the subject's sharpness relative to the burst's sharpest frame. Every weight here
is a first guess (plan §9: no threshold without a golden-set number); the personal model learns the owner's own
weighting from their picks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .faces import iou

VERSION = "burst@1"
CLOSED_ABSOLUTE = 0.75  # blink score that counts as closed eyes in a frame with nothing to compare against
CLOSED_RELATIVE = 0.3  # how much worse than the person's best frame, with at least CLOSED_FLOOR, means closed
CLOSED_FLOOR = 0.5
FACING_DEGREES = 35.0
SOFTMIN_POWER = -3.0
MISSING_FACE = 0.5


@dataclass
class FrameInput:
    image_id: int
    faces: list[dict[str, Any]]
    subject_blur: float | None
    verdict: str = "unknown"
    clip_high: float = 0.0
    clip_low: float = 0.0


@dataclass
class FrameRank:
    image_id: int
    position: int  # 1-based, in shooting order
    score: float = 0.0
    rank: int = 0
    sharp_rel: float | None = None
    people: int = 0
    closed_eyes: int = 0
    looking_away: int = 0
    smile: float | None = None
    tracks: list[int] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    reason: str = ""


def sharpness_value(blur: float | None) -> float | None:
    """A sharpness that grows as blur shrinks (blur in half-resolution pixels)."""
    return None if blur is None else 1.0 / (blur + 0.25)


def track_faces(frames: list[FrameInput]) -> list[list[int]]:
    """A track number for each face of each frame, matching boxes between consecutive frames by overlap."""
    tracks: list[list[int]] = []
    last_box: dict[int, list[float]] = {}
    next_id = 0
    for frame in frames:
        assigned: list[int] = [-1] * len(frame.faces)
        pairs = sorted(
            ((iou(face["box"], box), i, t) for i, face in enumerate(frame.faces) for t, box in last_box.items()),
            reverse=True,
        )
        used_faces, used_tracks = set(), set()
        for overlap, i, t in pairs:
            if overlap < 0.25 or i in used_faces or t in used_tracks:
                continue
            assigned[i] = t
            used_faces.add(i)
            used_tracks.add(t)
        for i, t in enumerate(assigned):
            if t < 0:
                assigned[i] = next_id
                next_id += 1
        for i, t in enumerate(assigned):
            last_box[t] = frame.faces[i]["box"]
        tracks.append(assigned)
    return tracks


def _blink(face: dict[str, Any]) -> float | None:
    left, right = face.get("blink_l"), face.get("blink_r")
    if left is None or right is None:
        return None
    return max(float(left), float(right))


def _softmin(values: list[float]) -> float:
    if not values:
        return 1.0
    if any(v <= 0 for v in values):
        return 0.0
    return (sum(v**SOFTMIN_POWER for v in values) / len(values)) ** (1 / SOFTMIN_POWER)


def rank_burst(frames: list[FrameInput]) -> list[FrameRank]:
    """Scores, ranks and reasons for the frames of one burst (in shooting order)."""
    tracks = track_faces(frames)
    all_tracks = sorted({t for ts in tracks for t in ts})
    best_blink: dict[int, float] = {}
    best_face_sharp: dict[int, float] = {}
    for frame, ts in zip(frames, tracks, strict=True):
        for face, t in zip(frame.faces, ts, strict=True):
            b = _blink(face)
            if b is not None:
                best_blink[t] = min(best_blink.get(t, 1.0), b)
            s = sharpness_value(face.get("sharpness"))
            if s is not None:
                best_face_sharp[t] = max(best_face_sharp.get(t, 0.0), s)
    subject_values = [sharpness_value(f.subject_blur) for f in frames]
    best_subject = max((v for v in subject_values if v is not None), default=None)
    single = len(frames) == 1
    # People are faces the landmarker confirmed; in a burst, only those seen in at least two frames (a face found
    # once is a false detection, a passer-by, or someone the detector mostly misses, such as a baby in profile).
    seen: dict[int, int] = {}
    for frame, ts in zip(frames, tracks, strict=True):
        for face, t in zip(frame.faces, ts, strict=True):
            if face.get("blink_l") is not None:
                seen[t] = seen.get(t, 0) + 1
    needed = 1 if single else 2
    people_tracks = [t for t in all_tracks if seen.get(t, 0) >= needed]

    ranks: list[FrameRank] = []
    for position, (frame, ts, subject) in enumerate(zip(frames, tracks, subject_values, strict=True), start=1):
        out = FrameRank(image_id=frame.image_id, position=position, tracks=ts)
        quality: dict[int, float] = {}
        smiles = []
        for face, t in zip(frame.faces, ts, strict=True):
            if t not in people_tracks:
                continue
            b = _blink(face)
            closed = False
            if b is not None:
                if single:
                    closed = b >= CLOSED_ABSOLUTE
                else:
                    closed = b >= CLOSED_FLOOR and b - best_blink.get(t, b) >= CLOSED_RELATIVE
            yaw, pitch = face.get("yaw"), face.get("pitch")
            away = (yaw is not None and abs(yaw) > FACING_DEGREES) or (
                pitch is not None and abs(pitch) > FACING_DEGREES
            )
            s = sharpness_value(face.get("sharpness"))
            rel = s / best_face_sharp[t] if s is not None and best_face_sharp.get(t) else 0.8
            quality[t] = (0.25 if closed else 1.0) * (0.75 if away else 1.0) * (0.5 + 0.5 * rel)
            out.closed_eyes += closed
            out.looking_away += away
            if face.get("smile") is not None:
                smiles.append(float(face["smile"]))
        people = [quality.get(t, MISSING_FACE) for t in people_tracks]
        out.people = len(quality)
        out.smile = round(sum(smiles) / len(smiles), 3) if smiles else None
        out.sharp_rel = round(subject / best_subject, 3) if subject is not None and best_subject else None
        technical = 0.4 + 0.6 * (out.sharp_rel if out.sharp_rel is not None else 0.7)
        if frame.verdict in ("motion", "shake"):
            technical *= 0.6
        if frame.clip_high > 0.03:
            technical *= 0.9
        score = technical * _softmin(people) if people_tracks else technical
        if out.smile is not None:
            score += 0.1 * out.smile
        out.score = round(score, 4)
        ranks.append(out)

    order = sorted(ranks, key=lambda r: (-r.score, r.position))
    for rank, r in enumerate(order, start=1):
        r.rank = rank
    best = order[0]
    total_people = len(people_tracks)
    for r in ranks:
        issues = []
        if r.closed_eyes:
            issues.append(f"{r.closed_eyes} of {total_people} blinking" if total_people > 1 else "eyes closed")
        if r.looking_away:
            issues.append(f"{r.looking_away} looking away" if total_people > 1 else "looking away")
        if total_people and r.people < total_people:
            issues.append(f"{total_people - r.people} of {total_people} faces hidden")
        if r.sharp_rel is not None and r.sharp_rel < 0.8:
            issues.append(f"{round(r.sharp_rel * 100)}% as sharp as the sharpest frame")
        if frames[r.position - 1].verdict in ("motion", "shake"):
            issues.append(
                frames[r.position - 1].verdict.replace("shake", "camera shake").replace("motion", "motion blur")
            )
        r.issues = issues
        if len(frames) == 1:
            r.reason = "; ".join(issues)
        elif r is best:
            r.reason = f"best: #{r.position} of {len(frames)}" + (f", but {', '.join(issues)}" if issues else "")
        else:
            r.reason = f"#{best.position} is better" + (f": {', '.join(issues)}" if issues else "")
    return ranks


def rank_gap(ranks: list[FrameRank], image_id: int) -> float:
    """How far a frame's score is below the burst's best (0 for the best frame)."""
    best = max(r.score for r in ranks)
    mine = next(r.score for r in ranks if r.image_id == image_id)
    return 0.0 if best <= 0 else max(0.0, (best - mine) / best)
