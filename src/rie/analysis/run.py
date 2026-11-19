"""Analyse the photos of a folder: one read of each file, then sharpness, faces and the focus verdict.

Each photo is read once (the whole file, which also gives its SHA-256 content hash), decoded twice from memory (the
raw green channel and the embedded JPEG) and analysed in a worker process that keeps the face models loaded.
Results land in the catalog keyed by the photo's quick hash and the analysis version, so a photo is analysed again
only when its file or the code changes. Before each batch the photo drive's state is checked; when the drive goes
away the step stops and reports itself paused, and opening the folder again resumes it.
"""

from __future__ import annotations

import hashlib
import multiprocessing
import os
import sqlite3
import time
import traceback
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .. import catalog, locations
from ..config import Config
from . import faces as faces_mod
from . import focus, sharpness
from .raw import RawError, green_half

VERSION = f"{sharpness.VERSION}+{faces_mod.VERSION}+{focus.VERSION}"
BATCH = 12

type Progress = Callable[[str, int, int], None]

_analyzer: faces_mod.FaceAnalyzer | None = None
_analyzer_error: str | None = None


def _load_analyzer() -> faces_mod.FaceAnalyzer | None:
    global _analyzer, _analyzer_error
    if _analyzer is None and _analyzer_error is None:
        try:
            _analyzer = faces_mod.FaceAnalyzer()
        except faces_mod.FaceModelsMissing as exc:
            _analyzer_error = str(exc)
    return _analyzer


def quiet_worker(log_path: str) -> None:
    """Send a worker process's output to a log file in the cache.

    OpenCV and MediaPipe's native code print start-up chatter (EGL, XNNPACK, feedback-manager warnings) straight to
    the terminal, which would fill the window running `rie serve`. Problems still reach the server: every photo's
    errors come back in its result.
    """
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)  # the app's own log, in its cache
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.close(fd)


def _jpeg_sharpness_source(img: np.ndarray) -> np.ndarray:
    """For a JPEG-only shot: its green channel, roughly linearized, as a stand-in for the raw green channel."""
    green = img[:, :, 1].astype(np.float32) / 255.0
    return np.power(green, 2.2)


def analyze_image(task: dict[str, Any]) -> dict[str, Any]:
    """Analyse one photo; runs in a worker process. Never raises: errors come back in the result."""
    start = time.perf_counter()
    out: dict[str, Any] = {"image_id": task["image_id"], "quick_hash": task["quick_hash"]}
    try:
        with open(task["path"], "rb") as fh:
            data = fh.read()
        out["content_hash"] = "sha256:" + hashlib.sha256(data).hexdigest()
        orientation = task["orientation"]
        meta = task["meta"]
        if task["kind"] == "raw":
            green, _info = green_half(data)
            start_byte, length = task["ranges"]["full"]
            img = faces_mod.decode_upright(data[start_byte : start_byte + length], orientation, reduce=2)
        else:
            img = faces_mod.decode_upright(data, 1, reduce=2, apply_exif=True)
            green = _jpeg_sharpness_source(img)
        del data
        smap = sharpness.sharpness_map(green)
        del green
        analyzer = _load_analyzer()
        found = [f.to_json() for f in analyzer.analyze(img, meta.get("focal_length"))] if analyzer else []
        result = focus.inspect(smap, found, meta, orientation if task["kind"] == "raw" else 1)
        out["sharpness"] = {
            "version": sharpness.VERSION,
            "rows": int(smap.blur.shape[0]),
            "cols": int(smap.blur.shape[1]),
            "blur": sharpness.encode_map(smap.blur),
            "noise": smap.noise,
        }
        out["faces"] = found
        out["faces_note"] = _analyzer_error
        out["focus"] = result.to_json()
    except (OSError, RawError, ValueError, KeyError) as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # a worker must report, not die
        out["error"] = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}"
    out["seconds"] = round(time.perf_counter() - start, 3)
    return out


@dataclass
class AnalysisStats:
    done: int = 0
    skipped: int = 0
    failed: int = 0
    paused: bool = False
    seconds: float = 0.0
    per_image: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    note: str | None = None


def _todo(conn: sqlite3.Connection, folder_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT i.id, i.name, i.kind, i.quick_hash, i.orientation, i.previews, i.meta, a.version, a.input_hash "
        "FROM images i LEFT JOIN analyses a ON a.image_id = i.id AND a.kind = 'focus' "
        "WHERE i.folder_id = ? AND i.state = 'present' ORDER BY i.capture_time, i.shutter_count, i.name",
        (folder_id,),
    ).fetchall()


def _store(
    conn: sqlite3.Connection, results: list[dict[str, Any]], names: dict[int, str], stats: AnalysisStats
) -> None:
    now = catalog.now()
    with catalog.transaction(conn):
        for r in results:
            image_id = r["image_id"]
            if "error" in r:
                stats.failed += 1
                stats.errors.append(f"{names.get(image_id, image_id)}: {r['error'].splitlines()[0]}")
                continue
            stats.done += 1
            stats.per_image.append(r["seconds"])
            if r.get("faces_note"):
                stats.note = r["faces_note"]
            conn.execute("UPDATE images SET content_hash = ? WHERE id = ?", (r["content_hash"], image_id))
            conn.execute("DELETE FROM faces WHERE image_id = ?", (image_id,))
            for idx, face in enumerate(r["faces"]):
                blur = r["focus"]["face_blur"][idx] if idx < len(r["focus"]["face_blur"]) else None
                conn.execute(
                    "INSERT INTO faces (image_id, idx, box, landmarks, score, source, blink_l, blink_r, smile, yaw, "
                    "pitch, roll, sharpness, cut_off) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        image_id,
                        idx,
                        catalog.dumps(face["box"]),
                        catalog.dumps({"points": face.get("landmarks", []), "eyes": face.get("eyes", [])}),
                        face["score"],
                        face["source"],
                        face.get("blink_l"),
                        face.get("blink_r"),
                        face.get("smile"),
                        face.get("yaw"),
                        face.get("pitch"),
                        face.get("roll"),
                        blur,
                        int(face.get("cut_off", False)),
                    ),
                )
            for kind, result in (("sharpness", r["sharpness"]), ("focus", r["focus"])):
                conn.execute(
                    "INSERT OR REPLACE INTO analyses "
                    "(image_id, kind, version, input_hash, result, seconds, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (image_id, kind, VERSION, r["quick_hash"], catalog.dumps(result), r["seconds"], now),
                )


def run_analysis(
    conn: sqlite3.Connection,
    cfg: Config,
    folder_id: int,
    *,
    progress: Progress | None = None,
    workers: int | None = None,
) -> AnalysisStats:
    """Analyse every photo of the folder that has no current analysis."""
    start = time.perf_counter()
    stats = AnalysisStats()
    folder = conn.execute("SELECT drive_uuid, path FROM folders WHERE id = ?", (folder_id,)).fetchone()
    location = locations.Location(folder["drive_uuid"], folder["path"])
    rows = _todo(conn, folder_id)
    todo = [r for r in rows if not (r["version"] == VERSION and r["input_hash"] == r["quick_hash"])]
    stats.skipped = len(rows) - len(todo)
    total = len(todo)
    if progress:
        progress("analysis", 0, total)
    if not todo:
        stats.seconds = time.perf_counter() - start
        return stats
    if not locations.available(location, cfg):
        stats.paused = True
        stats.seconds = time.perf_counter() - start
        return stats
    path = locations.resolve(location, cfg)
    names = {r["id"]: r["name"] for r in todo}

    def task(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "image_id": row["id"],
            "path": str(path / row["name"]),
            "kind": row["kind"],
            "quick_hash": row["quick_hash"],
            "orientation": row["orientation"],
            "ranges": catalog.loads(row["previews"], {}),
            "meta": catalog.loads(row["meta"], {}),
        }

    count = workers if workers is not None else cfg.analysis.workers
    batches = [todo[i : i + BATCH] for i in range(0, len(todo), BATCH)]
    if count <= 1:
        for batch in batches:
            if not locations.available(location, cfg):
                stats.paused = True
                break
            _store(conn, [analyze_image(task(r)) for r in batch], names, stats)
            if progress:
                progress("analysis", stats.done + stats.failed, total)
    else:
        context = multiprocessing.get_context("spawn")
        queue = iter(todo)
        done: list[dict[str, Any]] = []
        log = cfg.paths.cache / "logs" / "analysis.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with ProcessPoolExecutor(
            max_workers=count, mp_context=context, initializer=quiet_worker, initargs=(str(log),)
        ) as pool:
            in_flight: set[Future[dict[str, Any]]] = set()

            def refill() -> None:
                while len(in_flight) < 2 * count and not stats.paused:
                    row = next(queue, None)
                    if row is None:
                        return
                    if not locations.available(location, cfg):
                        stats.paused = True
                        return
                    in_flight.add(pool.submit(analyze_image, task(row)))

            refill()
            while in_flight:
                finished = next(as_completed(in_flight))
                in_flight.discard(finished)
                done.append(finished.result())
                if len(done) >= BATCH // 2 or not in_flight:
                    _store(conn, done, names, stats)
                    done = []
                    if progress:
                        progress("analysis", stats.done + stats.failed, total)
                refill()
            if done:
                _store(conn, done, names, stats)
    if not stats.paused:
        conn.execute("UPDATE folders SET analyzed_at = ? WHERE id = ?", (catalog.now(), folder_id))
    stats.seconds = time.perf_counter() - start
    return stats


def models_note(models: Path = faces_mod.MODELS) -> str | None:
    if faces_mod.models_available(models):
        return None
    return f"face models missing in {models}: faces are skipped; run `uv run python scripts/fetch_models.py`"
