"""Sort a folder into the three piles (plan §4.4): auto-reject, auto-keep and your call.

The rules give each photo a suggestion, a kind and a reason. When a personal model is active, its probability that
the owner rejects the photo replaces the rules' confidence; where model and rules point different ways, the photo
is the owner's call. A photo goes to an automatic pile only when its kind is set to automatic on the trust dial and
the probability is at least 0.8 (reject) or at most 0.2 (keep).
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field

from .. import catalog
from . import KINDS, rules
from . import model as model_mod

AUTO_REJECT = 0.8
AUTO_KEEP = 0.2


@dataclass
class SortStats:
    photos: int = 0
    piles: dict[str, int] = field(default_factory=lambda: {"your_call": 0, "auto_reject": 0, "auto_keep": 0})
    model: str = rules.MODEL
    seconds: float = 0.0


def trust_modes(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["kind"]: r["mode"] for r in conn.execute("SELECT kind, mode FROM trust")}


def set_trust(conn: sqlite3.Connection, kind: str, mode: str) -> None:
    if kind not in KINDS or mode not in ("suggest", "automatic"):
        raise ValueError(f"unknown kind or mode: {kind} {mode}")
    conn.execute("UPDATE trust SET mode = ?, changed_at = ? WHERE kind = ?", (mode, catalog.now(), kind))


def pile_for(decision: str, kind: str, probability: float, modes: dict[str, str]) -> str:
    if modes.get(kind) != "automatic":
        return "your_call"
    if decision == "reject" and probability >= AUTO_REJECT:
        return "auto_reject"
    if decision == "keep" and probability <= AUTO_KEEP:
        return "auto_keep"
    return "your_call"


def folder_inputs(conn: sqlite3.Connection, folder_id: int) -> list[dict]:
    """Everything the rules look at, per photo of the folder."""
    images = conn.execute(
        "SELECT i.id, i.name, i.meta FROM images i "
        "WHERE i.folder_id = ? AND i.state = 'present' ORDER BY i.capture_time, i.shutter_count, i.name",
        (folder_id,),
    ).fetchall()
    ids = [r["id"] for r in images]
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    analyses: dict[tuple[int, str], dict] = {}
    for r in conn.execute(
        f"SELECT image_id, kind, result FROM analyses "
        f"WHERE image_id IN ({marks}) AND kind IN ('focus', 'burst', 'look')",
        ids,
    ):
        analyses[(r["image_id"], r["kind"])] = catalog.loads(r["result"], {})
    faces: dict[int, list[dict]] = {i: [] for i in ids}
    for r in conn.execute(f"SELECT image_id, box, cut_off FROM faces WHERE image_id IN ({marks})", ids):
        faces[r["image_id"]].append({"box": catalog.loads(r["box"]), "cut_off": bool(r["cut_off"])})
    trash_flags = {
        r["image_id"]
        for r in conn.execute(
            f"SELECT image_id FROM sidecars WHERE in_trash = 1 AND image_id IN ({marks})",
            ids,
        )
    }
    out = []
    for r in images:
        i = r["id"]
        out.append(
            {
                "image_id": i,
                "meta": catalog.loads(r["meta"], {}),
                "focus": analyses.get((i, "focus"), {}),
                "burst": analyses.get((i, "burst"), {}),
                "look": analyses.get((i, "look"), {}),
                "faces": faces[i],
                "extra": {"rt_trash": i in trash_flags},
            }
        )
    return out


def sort_folder(conn: sqlite3.Connection, folder_id: int) -> SortStats:
    """Replace the folder's suggestions and piles."""
    start = time.perf_counter()
    stats = SortStats()
    inputs = folder_inputs(conn, folder_id)
    frames_by_burst: dict[int, dict[int, tuple[int, int]]] = {}
    for item in inputs:
        b = item["burst"]
        if b.get("burst_id") is not None:
            frames_by_burst.setdefault(b["burst_id"], {})[item["image_id"]] = (b.get("rank", 1), b.get("position", 1))
    suggestions = [
        rules.suggest(
            item["meta"],
            item["focus"],
            item["burst"],
            item["look"],
            item["faces"],
            item["extra"],
            frames_by_burst.get(item["burst"].get("burst_id"), {}),
        )
        for item in inputs
    ]
    probabilities = [s.reject_probability for s in suggestions]
    active = model_mod.active_model(conn)
    if active is not None:
        stats.model, clf = active
        probabilities = model_mod.predict(clf, [s.features for s in suggestions])
    modes = trust_modes(conn)
    now = catalog.now()
    with catalog.transaction(conn):
        conn.execute(
            "DELETE FROM suggestions WHERE image_id IN (SELECT id FROM images WHERE folder_id = ?)", (folder_id,)
        )
        for item, s, p in zip(inputs, suggestions, probabilities, strict=True):
            decision, kind, reason = s.decision, s.kind, s.reason
            if active is not None and (p >= 0.5) != (decision == "reject"):
                # The model disagrees with the rules: the owner decides, told what each thinks.
                decision = "reject" if p >= 0.5 else "keep"
                reason = f"your model would {decision} this ({p:.0%} reject); the checks say {s.decision}: {s.reason}"
                pile = "your_call"
            else:
                pile = pile_for(decision, kind, p, modes)
            stats.piles[pile] += 1
            stats.photos += 1
            conn.execute(
                "INSERT INTO suggestions (image_id, pile, decision, kind, probability, reason, model, features, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item["image_id"],
                    pile,
                    decision,
                    kind,
                    round(p, 4),
                    reason,
                    stats.model,
                    catalog.dumps({k: (None if v != v else v) for k, v in s.features.items()}),
                    now,
                ),
            )
    stats.seconds = time.perf_counter() - start
    return stats
