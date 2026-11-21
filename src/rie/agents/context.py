"""What the edit agents know about one photo: metadata, faces, its scene and burst, the owner's locked edits, and
the measurements read from the raw file and the as-shot render.

Faces come from Phase 1's analysis, which can be wrong: a tiled pass may find a face in foliage, and some real faces
are missed. Agents therefore weigh each face by its detection score and by whether the landmarker confirmed it,
and work without faces when none is trusted.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .. import catalog
from .linear import LinearRaw
from .proposal import Params

UNCONFIRMED_WEIGHT = 0.35  # a face the landmarker did not confirm counts this much


@dataclass(frozen=True)
class Face:
    box: tuple[float, float, float, float]  # x, y, w, h normalized to the upright frame
    score: float
    confirmed: bool  # the landmarker found a face in the crop
    cut_off: bool

    @property
    def weight(self) -> float:
        """How much an agent should trust this face: detection score, landmark confirmation and size."""
        size = min(1.0, (self.box[2] * self.box[3]) ** 0.5 / 0.04)  # faces under 4 % of the frame count less
        return self.score * (1.0 if self.confirmed else UNCONFIRMED_WEIGHT) * size


@dataclass
class ImageContext:
    name: str
    path: Path  # the raw file, read only
    meta: dict[str, Any]
    orientation: int = 1
    image_id: int | None = None
    quick_hash: str = ""
    faces: list[Face] = field(default_factory=list)
    scene: int | None = None
    burst: int | None = None
    locked: Params = field(default_factory=dict)  # the owner's locked values (sidecars, review)
    locked_provenance: dict[str, dict[str, Any]] = field(default_factory=dict)  # their provenance, by group.name
    focus: dict[str, Any] = field(default_factory=dict)
    linear: LinearRaw | None = None  # filled by the editor before the agents run
    before: np.ndarray | None = None  # the as-shot render, linear sRGB (h, w, 3), upright and lens-corrected

    def is_locked(self, group: str, name: str | None = None) -> bool:
        values = self.locked.get(group, {})
        return bool(values) if name is None else name in values

    @property
    def trusted_faces(self) -> list[Face]:
        return [f for f in self.faces if f.weight >= 0.25]

    @property
    def face_weight(self) -> float:
        return sum(f.weight for f in self.faces)


def locked_values(recipe: dict[str, Any]) -> tuple[Params, dict[str, dict[str, Any]]]:
    """The values a recipe's provenance marks as locked, and that provenance."""
    params = recipe.get("params", {})
    out: Params = {}
    provenance: dict[str, dict[str, Any]] = {}
    for key, prov in recipe.get("provenance", {}).items():
        if not isinstance(prov, dict) or not prov.get("locked"):
            continue
        group, _, name = key.partition(".")
        if name in params.get(group, {}):
            out.setdefault(group, {})[name] = params[group][name]
            provenance[key] = prov
    return out, provenance


def load_contexts(conn: sqlite3.Connection, folder_id: int, folder: Path, image_ids: list[int]) -> list[ImageContext]:
    """Contexts for these photos of a folder, from the catalog (it reads no photo file)."""
    if not image_ids:
        return []
    marks = ",".join("?" * len(image_ids))
    rows = conn.execute(
        f"SELECT i.id, i.name, i.meta, i.orientation, i.quick_hash, r.recipe, a.result AS focus FROM images i "
        f"LEFT JOIN recipes r ON r.image_id = i.id "
        f"LEFT JOIN analyses a ON a.image_id = i.id AND a.kind = 'focus' "
        f"WHERE i.folder_id = ? AND i.id IN ({marks}) ORDER BY i.capture_time, i.shutter_count, i.name",
        (folder_id, *image_ids),
    ).fetchall()
    faces: dict[int, list[Face]] = {}
    for f in conn.execute(
        f"SELECT image_id, box, score, blink_l, landmarks, cut_off FROM faces WHERE image_id IN ({marks}) "
        "ORDER BY image_id, idx",
        image_ids,
    ):
        landmarks = catalog.loads(f["landmarks"], {}) or {}
        confirmed = f["blink_l"] is not None or bool(landmarks.get("eyes"))
        box = tuple(float(v) for v in catalog.loads(f["box"], [0, 0, 0, 0]))
        faces.setdefault(f["image_id"], []).append(
            Face(box=box, score=float(f["score"]), confirmed=confirmed, cut_off=bool(f["cut_off"]))  # type: ignore[arg-type]
        )
    groups: dict[int, dict[str, int]] = {}
    for g in conn.execute(
        f"SELECT m.image_id, g.id, g.kind, g.parent_id FROM group_members m JOIN groups g ON g.id = m.group_id "
        f"WHERE g.folder_id = ? AND m.image_id IN ({marks})",
        (folder_id, *image_ids),
    ):
        entry = groups.setdefault(g["image_id"], {})
        entry[g["kind"]] = g["id"]
        if g["kind"] == "burst" and g["parent_id"] is not None:
            entry.setdefault("scene", g["parent_id"])
    contexts = []
    for row in rows:
        membership = groups.get(row["id"], {})
        locked, locked_provenance = locked_values(catalog.loads(row["recipe"], {}) or {})
        contexts.append(
            ImageContext(
                name=row["name"],
                path=folder / row["name"],
                meta=catalog.loads(row["meta"], {}),
                orientation=row["orientation"],
                image_id=row["id"],
                quick_hash=row["quick_hash"],
                faces=faces.get(row["id"], []),
                scene=membership.get("scene"),
                burst=membership.get("burst"),
                locked=locked,
                locked_provenance=locked_provenance,
                focus=catalog.loads(row["focus"], {}) or {},
            )
        )
    return contexts
