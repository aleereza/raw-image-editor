"""Folder-level analysis: group the photos into scenes and bursts, then rank each burst.

Runs after the per-photo steps, on what the catalog holds, and replaces the folder's earlier groups. It reads no
photo file, so it also works while the drive is away.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

import numpy as np

from .. import catalog
from ..scan.exif import timestamp
from . import burst as burst_mod
from . import grouping

VERSION = f"{grouping.VERSION}+{burst_mod.VERSION}"


@dataclass
class GroupStats:
    scenes: int = 0
    bursts: int = 0
    in_bursts: int = 0  # photos in bursts of two or more
    seconds: float = 0.0


def _frames(conn: sqlite3.Connection, folder_id: int) -> list[grouping.Frame]:
    rows = conn.execute(
        "SELECT i.id, i.name, i.capture_time, i.shutter_count, i.meta, a.result AS look FROM images i "
        "LEFT JOIN analyses a ON a.image_id = i.id AND a.kind = 'look' "
        "WHERE i.folder_id = ? AND i.state = 'present'",
        (folder_id,),
    ).fetchall()
    frames = []
    for row in rows:
        meta = catalog.loads(row["meta"], {})
        look = catalog.loads(row["look"], {}) or {}
        layout = np.array(look["layout"], dtype=np.float32) if look.get("layout") else None
        frames.append(
            grouping.Frame(
                image_id=row["id"],
                time=timestamp(row["capture_time"]),
                shutter=row["shutter_count"],
                name=row["name"],
                focal=meta.get("focal_length"),
                fnumber=meta.get("fnumber"),
                ev=meta.get("light_value"),
                flash=meta.get("flash_fired"),
                layout=layout,
                dhash=int(look["dhash"], 16) if look.get("dhash") else None,
            )
        )
    return frames


def _inputs(conn: sqlite3.Connection, ids: list[int]) -> dict[int, burst_mod.FrameInput]:
    marks = ",".join("?" * len(ids))
    focus = {
        r["image_id"]: catalog.loads(r["result"], {})
        for r in conn.execute(
            f"SELECT image_id, result FROM analyses WHERE kind = 'focus' AND image_id IN ({marks})", ids
        )
    }
    looks = {
        r["image_id"]: catalog.loads(r["result"], {})
        for r in conn.execute(
            f"SELECT image_id, result FROM analyses WHERE kind = 'look' AND image_id IN ({marks})", ids
        )
    }
    faces: dict[int, list[dict]] = {i: [] for i in ids}
    for r in conn.execute(f"SELECT * FROM faces WHERE image_id IN ({marks}) ORDER BY image_id, idx", ids):
        faces[r["image_id"]].append(
            {
                "box": catalog.loads(r["box"]),
                "blink_l": r["blink_l"],
                "blink_r": r["blink_r"],
                "smile": r["smile"],
                "yaw": r["yaw"],
                "pitch": r["pitch"],
                "sharpness": r["sharpness"],
                "cut_off": bool(r["cut_off"]),
            }
        )
    out = {}
    for i in ids:
        f, look = focus.get(i, {}), looks.get(i, {})
        out[i] = burst_mod.FrameInput(
            image_id=i,
            faces=faces[i],
            subject_blur=f.get("subject_blur"),
            verdict=f.get("verdict", "unknown"),
            clip_high=float(look.get("clip_high", 0.0)),
            clip_low=float(look.get("clip_low", 0.0)),
        )
    return out


def run_grouping(conn: sqlite3.Connection, folder_id: int) -> GroupStats:
    """Replace the folder's scenes and bursts, and rank every burst."""
    start = time.perf_counter()
    stats = GroupStats()
    frames = _frames(conn, folder_id)
    scenes = grouping.group(frames)
    inputs = _inputs(conn, [f.image_id for f in frames]) if frames else {}
    now = catalog.now()
    with catalog.transaction(conn):
        conn.execute(
            "DELETE FROM group_members WHERE group_id IN (SELECT id FROM groups WHERE folder_id = ?)", (folder_id,)
        )
        conn.execute("UPDATE groups SET parent_id = NULL WHERE folder_id = ?", (folder_id,))
        conn.execute("DELETE FROM groups WHERE folder_id = ?", (folder_id,))
        conn.execute(
            "DELETE FROM analyses WHERE kind = 'burst' AND image_id IN (SELECT id FROM images WHERE folder_id = ?)",
            (folder_id,),
        )
        position = 0
        for scene_index, scene in enumerate(scenes):
            cur = conn.execute(
                "INSERT INTO groups (folder_id, kind, parent_id, position, info) VALUES (?, 'scene', NULL, ?, ?)",
                (folder_id, scene_index, catalog.dumps({"size": len(scene.frames), "bursts": len(scene.bursts)})),
            )
            scene_id = int(cur.lastrowid or 0)
            stats.scenes += 1
            for frame_index, frame in enumerate(scene.frames):
                conn.execute(
                    "INSERT INTO group_members (group_id, image_id, position) VALUES (?, ?, ?)",
                    (scene_id, frame.image_id, frame_index),
                )
            for b in scene.bursts:
                ranks = burst_mod.rank_burst([inputs[f.image_id] for f in b.frames])
                best = min(ranks, key=lambda r: r.rank)
                info = {
                    "size": len(b.frames),
                    "best": best.image_id,
                    "reason": best.reason,
                    "duplicates": b.duplicates,
                    "people": max((r.people for r in ranks), default=0),
                }
                cur = conn.execute(
                    "INSERT INTO groups (folder_id, kind, parent_id, position, info) VALUES (?, 'burst', ?, ?, ?)",
                    (folder_id, scene_id, position, catalog.dumps(info)),
                )
                burst_id = int(cur.lastrowid or 0)
                position += 1
                stats.bursts += 1
                stats.in_bursts += len(b.frames) if len(b.frames) > 1 else 0
                duplicates_of = {}
                for a, c in b.duplicates:
                    duplicates_of.setdefault(a, []).append(c)
                    duplicates_of.setdefault(c, []).append(a)
                for r in ranks:
                    conn.execute(
                        "INSERT INTO group_members (group_id, image_id, position, rank, score) VALUES (?, ?, ?, ?, ?)",
                        (burst_id, r.image_id, r.position - 1, r.rank, r.score),
                    )
                    for idx, track in enumerate(r.tracks):
                        conn.execute(
                            "UPDATE faces SET track = ? WHERE image_id = ? AND idx = ?", (track, r.image_id, idx)
                        )
                    result = {
                        "burst_id": burst_id,
                        "size": len(b.frames),
                        "position": r.position,
                        "rank": r.rank,
                        "score": r.score,
                        "gap": round(burst_mod.rank_gap(ranks, r.image_id), 4),
                        "best": best.image_id,
                        "best_position": best.position,
                        "sharp_rel": r.sharp_rel,
                        "people": r.people,
                        "closed_eyes": r.closed_eyes,
                        "looking_away": r.looking_away,
                        "smile": r.smile,
                        "issues": r.issues,
                        "reason": r.reason,
                        "duplicate_of": duplicates_of.get(r.image_id, []),
                    }
                    conn.execute(
                        "INSERT INTO analyses (image_id, kind, version, input_hash, result, seconds, created_at) "
                        "VALUES (?, 'burst', ?, '', ?, NULL, ?)",
                        (r.image_id, VERSION, catalog.dumps(result), now),
                    )
    stats.seconds = time.perf_counter() - start
    return stats
