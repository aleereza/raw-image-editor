"""Opening a folder end to end: scan, cached previews, and (in later steps) analysis, grouping and the sort.

Every step is idempotent and skips work that is already done, so opening a folder again, or resuming after a
crash, only does what is missing. Steps report progress through a callback; the CLI prints it, and the web server
turns it into server-sent events.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from . import catalog, sidecars
from .analysis.folder import GroupStats, run_grouping
from .analysis.run import AnalysisStats, run_analysis
from .config import Config
from .cull.piles import SortStats, sort_folder
from .scan import files, previews
from .scan.folder import ScanSummary, folder_path, scan_folder

type Progress = Callable[[str, int, int], None]

LOOK_VERSION = "look@1"


def _nothing(_step: str, _done: int, _total: int) -> None:
    pass


@dataclass
class StepStats:
    done: int = 0
    skipped: int = 0
    failed: int = 0
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)
    per_image: list[float] = field(default_factory=list)


def run_previews(
    conn: sqlite3.Connection, cfg: Config, folder_id: int, *, progress: Progress = _nothing, path: Path | None = None
) -> StepStats:
    """Make the cached grid and loupe copies of every photo that lacks them, several at a time."""
    start = time.perf_counter()
    stats = StepStats()
    path = path or folder_path(conn, folder_id, cfg)
    rows = conn.execute(
        "SELECT i.id, i.name, i.kind, i.quick_hash, i.orientation, i.previews, i.width, a.input_hash AS look_hash "
        "FROM images i LEFT JOIN analyses a ON a.image_id = i.id AND a.kind = 'look' "
        "WHERE i.folder_id = ? AND i.state = 'present' ORDER BY i.capture_time, i.shutter_count, i.name",
        (folder_id,),
    ).fetchall()
    todo = []
    for row in rows:
        cached = (
            previews.thumb_path(cfg, row["quick_hash"]).exists()
            and previews.screen_path(cfg, row["quick_hash"]).exists()
        )
        if cached and row["width"] and row["look_hash"] == row["quick_hash"]:
            stats.skipped += 1
        else:
            todo.append(row)
    progress("previews", 0, len(todo))
    if not todo:
        stats.seconds = time.perf_counter() - start
        return stats

    def work(row: sqlite3.Row) -> previews.PreviewResult:
        return previews.make_previews(
            row["id"],
            path / row["name"],
            row["kind"],
            catalog.loads(row["previews"], {}),
            row["orientation"],
            row["quick_hash"],
            cfg,
        )

    by_id = {row["id"]: row for row in todo}
    pending: list[previews.PreviewResult] = []
    retry: list[sqlite3.Row] = []
    with ThreadPoolExecutor(max_workers=cfg.previews.workers) as pool:
        futures = {pool.submit(work, row): row for row in todo}
        for future in as_completed(futures):
            result = future.result()
            if result.error:
                retry.append(futures[future])  # once more at the end, alone
                continue
            pending.append(result)
            if len(pending) >= 16:
                _store_previews(conn, pending, by_id, stats)
                pending = []
                progress("previews", stats.done + stats.failed, len(todo))
    pending += [work(row) for row in retry]
    _store_previews(conn, pending, by_id, stats)
    progress("previews", stats.done + stats.failed, len(todo))
    stats.seconds = time.perf_counter() - start
    return stats


def _store_previews(
    conn: sqlite3.Connection,
    results: list[previews.PreviewResult],
    by_id: dict[int, sqlite3.Row],
    stats: StepStats,
) -> None:
    if not results:
        return
    now = catalog.now()
    with catalog.transaction(conn):
        for result in results:
            row = by_id[result.image_id]
            if result.error:
                stats.failed += 1
                stats.errors.append(f"{row['name']}: {result.error}")
                continue
            stats.done += 1
            stats.per_image.append(result.seconds)
            conn.execute(
                "UPDATE images SET width = ?, height = ? WHERE id = ?", (result.width, result.height, result.image_id)
            )
            conn.execute(
                "INSERT OR REPLACE INTO analyses (image_id, kind, version, input_hash, result, seconds, created_at) "
                "VALUES (?, 'look', ?, ?, ?, ?, ?)",
                (result.image_id, LOOK_VERSION, row["quick_hash"], catalog.dumps(result.look), result.seconds, now),
            )


@dataclass
class OpenResult:
    scan: ScanSummary
    previews: StepStats
    sidecars: sidecars.ImportStats | None = None
    analysis: AnalysisStats | None = None
    groups: GroupStats | None = None
    sort: SortStats | None = None


def open_folder(
    conn: sqlite3.Connection,
    cfg: Config,
    folder: Path,
    *,
    progress: Progress = _nothing,
    analyze: bool = True,
    workers: int | None = None,
    on_folder: Callable[[int], None] | None = None,
) -> OpenResult:
    """Scan a folder, make its previews, analyse its photos and group them.

    The folder is grouped and sorted after each step: right after the scan, so the grid can be browsed within
    seconds (thumbnails are made on demand meanwhile), after the previews (which add the look of each frame), and
    once the analysis is done. `on_folder` hears the folder's id after the scan.
    """
    summary = scan_folder(conn, cfg, folder, progress=progress)
    if on_folder is not None:
        on_folder(summary.folder_id)
    names = files.list_folder(summary.path).sidecars
    sidecar_stats = sidecars.import_sidecars(conn, summary.folder_id, summary.path, names)
    run_grouping(conn, summary.folder_id)
    sort_folder(conn, summary.folder_id)
    progress("sorted", 0, 0)
    result = OpenResult(
        scan=summary,
        previews=run_previews(conn, cfg, summary.folder_id, progress=progress, path=summary.path),
        sidecars=sidecar_stats,
    )
    if analyze:
        run_grouping(conn, summary.folder_id)
        sort_folder(conn, summary.folder_id)
        result.analysis = run_analysis(conn, cfg, summary.folder_id, progress=progress, workers=workers)
    result.groups = run_grouping(conn, summary.folder_id)
    result.sort = sort_folder(conn, summary.folder_id)
    return result
