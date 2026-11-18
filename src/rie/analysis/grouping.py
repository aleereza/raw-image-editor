"""Grouping v1: scenes, and bursts nested in them (plan §4.2).

Frames are ordered by capture time, then shutter count (the D7100 records tenths of a second, ADR 0002).

- **Burst:** consecutive frames at most 0.7 s apart with the same lens settings and light, or look-alike frames
  (the same shot taken again, by hand or with the self-timer) up to 10 s apart. Single frames are bursts of one.
- **Scene:** frames within a couple of minutes of each other under the same light. A gap of more than 120 s, the
  flash switching on or off, or a jump of more than 2.5 EV starts a new scene, and so does a clearly different look
  after more than 30 s. A burst never spans two scenes.
- **Near-duplicates:** frames of one burst that look almost the same (an 8 x 8 colour layout within 0.02 and a
  difference hash within 4 bits).

The look is the cheap descriptor made with the previews (an 8 x 8 CIELAB layout and a difference hash). The plan's
embeddings (DINOv2 or SigLIP) would recognise the same scene across bigger changes; they are not used yet. The
thresholds come from the Phase 0 samples (consecutive frames of one burst differ by at most 0.03 in layout, other
shots by 0.06 or more) and are provisional until the golden set's groupings exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

VERSION = "grouping@1"
BURST_GAP = 0.7
SIMILAR_GAP = 10.0
SCENE_GAP = 120.0
SCENE_EV_JUMP = 2.5
BURST_LAYOUT = 0.06
SIMILAR_LAYOUT = 0.04
SIMILAR_HASH = 12
DUPLICATE_LAYOUT = 0.02
DUPLICATE_HASH = 4
SCENE_LAYOUT = 0.12
SCENE_LOOK_GAP = 30.0


@dataclass
class Frame:
    image_id: int
    time: float | None
    shutter: int | None = None
    name: str = ""
    focal: float | None = None
    fnumber: float | None = None
    ev: float | None = None
    flash: bool | None = None
    layout: np.ndarray | None = None
    dhash: int | None = None


@dataclass
class Burst:
    frames: list[Frame]
    duplicates: list[tuple[int, int]] = field(default_factory=list)  # image id pairs that look almost the same


@dataclass
class Scene:
    bursts: list[Burst]

    @property
    def frames(self) -> list[Frame]:
        return [f for b in self.bursts for f in b.frames]


def layout_distance(a: Frame, b: Frame) -> float | None:
    if a.layout is None or b.layout is None or a.layout.shape != b.layout.shape:
        return None
    return float(np.sqrt(np.mean(np.square(a.layout - b.layout))))


def hash_distance(a: Frame, b: Frame) -> int | None:
    if a.dhash is None or b.dhash is None:
        return None
    return (a.dhash ^ b.dhash).bit_count()


def same_settings(a: Frame, b: Frame) -> bool:
    if a.focal and b.focal and abs(a.focal - b.focal) > 2:
        return False
    if a.fnumber and b.fnumber and abs(a.fnumber - b.fnumber) > 0.15 * max(a.fnumber, b.fnumber):
        return False
    if a.ev is not None and b.ev is not None and abs(a.ev - b.ev) > 1.0:
        return False
    return not (a.flash is not None and b.flash is not None and a.flash != b.flash)


def _gap(a: Frame, b: Frame) -> float | None:
    if a.time is None or b.time is None:
        return None
    return b.time - a.time


def same_burst(a: Frame, b: Frame) -> bool:
    gap = _gap(a, b)
    if gap is None or gap < 0 or not same_settings(a, b):
        return False
    look = layout_distance(a, b)
    bits = hash_distance(a, b)
    if gap <= BURST_GAP:
        return look is None or look <= BURST_LAYOUT
    if gap <= SIMILAR_GAP and look is not None and bits is not None:
        return look <= SIMILAR_LAYOUT and bits <= SIMILAR_HASH
    return False


def new_scene(a: Frame, b: Frame) -> bool:
    gap = _gap(a, b)
    if gap is None or gap > SCENE_GAP:
        return True
    if a.flash is not None and b.flash is not None and a.flash != b.flash:
        return True
    if a.ev is not None and b.ev is not None and abs(a.ev - b.ev) > SCENE_EV_JUMP:
        return True
    look = layout_distance(a, b)
    return look is not None and look > SCENE_LAYOUT and gap > SCENE_LOOK_GAP


def near_duplicate(a: Frame, b: Frame) -> bool:
    look, bits = layout_distance(a, b), hash_distance(a, b)
    return look is not None and bits is not None and look <= DUPLICATE_LAYOUT and bits <= DUPLICATE_HASH


def order(frames: list[Frame]) -> list[Frame]:
    timed = sorted((f for f in frames if f.time is not None), key=lambda f: (f.time, f.shutter or 0, f.name))
    untimed = sorted((f for f in frames if f.time is None), key=lambda f: f.name)
    return timed + untimed


def group(frames: list[Frame]) -> list[Scene]:
    """Scenes of bursts, in shooting order."""
    ordered = order(frames)
    bursts: list[Burst] = []
    for frame in ordered:
        if bursts and same_burst(bursts[-1].frames[-1], frame):
            bursts[-1].frames.append(frame)
        else:
            bursts.append(Burst([frame]))
    for burst in bursts:
        fs = burst.frames
        burst.duplicates = [
            (fs[i].image_id, fs[j].image_id)
            for i in range(len(fs))
            for j in range(i + 1, len(fs))
            if near_duplicate(fs[i], fs[j])
        ]
    scenes: list[Scene] = []
    for burst in bursts:
        if scenes and not new_scene(scenes[-1].bursts[-1].frames[-1], burst.frames[0]):
            scenes[-1].bursts.append(burst)
        else:
            scenes.append(Scene([burst]))
    return scenes
