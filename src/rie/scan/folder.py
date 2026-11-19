"""Scan a folder into the catalog: list and pair files, hash new ones, read metadata, re-link moved files.

Opening a folder again is cheap: a file whose name, size and modification time are unchanged is not read at all.
A file the catalog has not seen at that name is hashed (64 KiB); if the hash matches a photo whose old file is gone,
that photo is re-linked, keeping its decisions and analysis. A whole folder that was moved or renamed is recognised
the same way. Nothing in the folder is written.
"""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import catalog, locations
from ..config import Config
from . import exif, files

type Progress = Callable[[str, int, int], None]


class ScanError(RuntimeError):
    """Raised when a folder cannot be scanned (not a folder, unreadable)."""


@dataclass
class ScanSummary:
    folder_id: int
    path: Path
    shots: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    relinked: int = 0
    missing: int = 0
    sidecars: int = 0
    other: int = 0
    moved_from: str | None = None  # the folder's previous location, when the whole folder was re-linked
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)


def folder_path(conn: sqlite3.Connection, folder_id: int, cfg: Config) -> Path:
    row = conn.execute("SELECT drive_uuid, path FROM folders WHERE id = ?", (folder_id,)).fetchone()
    if row is None:
        raise ScanError(f"no folder {folder_id} in the catalog")
    return locations.resolve(locations.Location(row["drive_uuid"], row["path"]), cfg)


def _folder_gone(conn: sqlite3.Connection, folder_id: int, cfg: Config) -> bool:
    """True when the folder's recorded location is readable and no longer exists."""
    row = conn.execute("SELECT drive_uuid, path FROM folders WHERE id = ?", (folder_id,)).fetchone()
    location = locations.Location(row["drive_uuid"], row["path"])
    if not locations.available(location, cfg):
        return False
    return not locations.resolve(location, cfg).is_dir()


def _find_moved_folder(conn: sqlite3.Connection, cfg: Config, hashes: dict[str, str]) -> int | None:
    """A catalog folder that holds most of these files and whose own location has gone."""
    if not hashes:
        return None
    marks = ",".join("?" * len(hashes))
    rows = conn.execute(
        f"SELECT folder_id, COUNT(*) AS n FROM images WHERE quick_hash IN ({marks}) "
        "GROUP BY folder_id ORDER BY n DESC LIMIT 3",
        list(hashes.values()),
    ).fetchall()
    for row in rows:
        if row["n"] * 2 >= len(hashes) and _folder_gone(conn, row["folder_id"], cfg):
            return int(row["folder_id"])
    return None


def _relink_candidate(
    conn: sqlite3.Connection, cfg: Config, quick_hash: str, size: int, folder_id: int, listed: set[str]
) -> sqlite3.Row | None:
    """A photo with this hash whose file is gone: missing, left out of this folder's listing, or deleted elsewhere."""
    rows = conn.execute(
        "SELECT id, folder_id, name, state FROM images WHERE quick_hash = ? AND size = ? AND state != 'trashed'",
        (quick_hash, size),
    ).fetchall()
    for row in rows:
        if row["folder_id"] == folder_id:
            if row["name"] not in listed:
                return row
            continue
        if row["state"] == "missing":
            return row
        other = conn.execute("SELECT drive_uuid, path FROM folders WHERE id = ?", (row["folder_id"],)).fetchone()
        location = locations.Location(other["drive_uuid"], other["path"])
        if locations.available(location, cfg) and not (locations.resolve(location, cfg) / row["name"]).exists():
            return row
    return None


def _metadata_row(record: dict[str, Any]) -> dict[str, Any]:
    iso = exif.capture_time(record)
    return {
        "capture_time": iso,
        "shutter_count": exif.shutter_count(record),
        "orientation": exif.orientation(record),
        "meta": catalog.dumps(exif.normalize(record)),
        "exif": catalog.dumps({k: v for k, v in record.items() if k != "SourceFile"}),
        "previews": catalog.dumps(exif.preview_ranges(record)),
    }


def scan_folder(
    conn: sqlite3.Connection,
    cfg: Config,
    folder: Path,
    *,
    progress: Progress | None = None,
    exiftool: str = "exiftool",
) -> ScanSummary:
    """Scan `folder` into the catalog and return what changed."""
    start = time.perf_counter()
    location = locations.locate(folder, cfg)
    path = locations.resolve(location, cfg)
    if not path.is_dir():
        raise ScanError(f"not a folder: {path}")
    listing = files.list_folder(path)
    listed = {shot.name for shot in listing.shots}

    row = conn.execute(
        "SELECT id FROM folders WHERE drive_uuid = ? AND path = ?", (location.drive_uuid, location.path)
    ).fetchone()
    folder_id = int(row["id"]) if row else None
    existing: dict[str, sqlite3.Row] = {}
    if folder_id is not None:
        existing = {
            r["name"]: r
            for r in conn.execute(
                "SELECT id, name, size, mtime_ns, quick_hash, state FROM images WHERE folder_id = ?", (folder_id,)
            )
        }

    to_hash = [
        s
        for s in listing.shots
        if s.name not in existing or (existing[s.name]["size"], existing[s.name]["mtime_ns"]) != (s.size, s.mtime_ns)
    ]
    hashes: dict[str, str] = {}
    for i, shot in enumerate(to_hash):
        hashes[shot.name] = files.quick_hash(path / shot.name, shot.size)
        if progress and i % 50 == 0:
            progress("hash", i, len(to_hash))

    summary = ScanSummary(folder_id=-1, path=path, shots=len(listing.shots))
    with catalog.transaction(conn):
        if folder_id is None:
            moved = _find_moved_folder(conn, cfg, hashes)
            if moved is not None:
                old = conn.execute("SELECT drive_uuid, path FROM folders WHERE id = ?", (moved,)).fetchone()
                summary.moved_from = old["path"]
                conn.execute(
                    "UPDATE folders SET drive_uuid = ?, path = ?, name = ? WHERE id = ?",
                    (location.drive_uuid, location.path, path.name, moved),
                )
                folder_id = moved
                existing = {
                    r["name"]: r
                    for r in conn.execute(
                        "SELECT id, name, size, mtime_ns, quick_hash, state FROM images WHERE folder_id = ?",
                        (folder_id,),
                    )
                }
            else:
                cur = conn.execute(
                    "INSERT INTO folders (drive_uuid, path, name, opened_at) VALUES (?, ?, ?, ?)",
                    (location.drive_uuid, location.path, path.name, catalog.now()),
                )
                folder_id = int(cur.lastrowid or 0)
        else:
            conn.execute("UPDATE folders SET opened_at = ? WHERE id = ?", (catalog.now(), folder_id))
    assert folder_id is not None
    summary.folder_id = folder_id

    # Decide what each listed shot is: unchanged, re-linked, changed or new.
    need_metadata: list[files.Shot] = []
    relinks: list[tuple[int, files.Shot]] = []
    for shot in listing.shots:
        prev = existing.get(shot.name)
        if prev is not None and (prev["size"], prev["mtime_ns"]) == (shot.size, shot.mtime_ns):
            summary.unchanged += 1
            continue
        h = hashes[shot.name]
        if prev is not None and prev["quick_hash"] == h and prev["size"] == shot.size:
            summary.unchanged += 1  # only the modification time changed
            conn.execute("UPDATE images SET mtime_ns = ? WHERE id = ?", (shot.mtime_ns, prev["id"]))
            continue
        if prev is None:
            candidate = _relink_candidate(conn, cfg, h, shot.size, folder_id, listed)
            if candidate is not None:
                relinks.append((int(candidate["id"]), shot))
                continue
        need_metadata.append(shot)

    with catalog.transaction(conn):
        listed_now = set(listed)
        for image_id, shot in relinks:
            conn.execute(
                "UPDATE images SET folder_id = ?, name = ?, jpeg_name = ?, mtime_ns = ?, state = 'present' "
                "WHERE id = ?",
                (folder_id, shot.name, shot.jpeg_name, shot.mtime_ns, image_id),
            )
            summary.relinked += 1
        # Photos of this folder that are no longer listed are missing (trashed ones stay trashed).
        for name, prev in existing.items():
            if name not in listed_now and prev["state"] == "present" and prev["id"] not in {i for i, _ in relinks}:
                conn.execute("UPDATE images SET state = 'missing' WHERE id = ?", (prev["id"],))
                summary.missing += 1
        for shot in listing.shots:
            prev = existing.get(shot.name)
            if (
                prev is not None
                and prev["state"] == "missing"
                and (prev["size"], prev["mtime_ns"]) == (shot.size, shot.mtime_ns)
            ):
                conn.execute("UPDATE images SET state = 'present' WHERE id = ?", (prev["id"],))

    # Metadata for new and changed shots: one exiftool process, in chunks.
    if need_metadata:
        paths = [path / shot.name for shot in need_metadata]
        by_name = {shot.name: shot for shot in need_metadata}
        done = 0
        chunk: list[tuple[files.Shot, dict[str, Any]]] = []
        for record in exif.read_folder(paths, executable=exiftool):
            name = os.path.basename(str(record.get("SourceFile", "")))
            shot = by_name.get(name)
            if shot is None:
                continue
            chunk.append((shot, record))
            if len(chunk) >= exif.CHUNK:
                _store(conn, folder_id, chunk, hashes, existing, summary)
                done += len(chunk)
                chunk = []
                if progress:
                    progress("metadata", done, len(need_metadata))
        if chunk:
            _store(conn, folder_id, chunk, hashes, existing, summary)
            done += len(chunk)
        if progress:
            progress("metadata", done, len(need_metadata))

    summary.sidecars = len(listing.sidecars)
    summary.other = len(listing.other)
    conn.execute("UPDATE folders SET scanned_at = ? WHERE id = ?", (catalog.now(), folder_id))
    summary.seconds = time.perf_counter() - start
    return summary


def _store(
    conn: sqlite3.Connection,
    folder_id: int,
    chunk: list[tuple[files.Shot, dict[str, Any]]],
    hashes: dict[str, str],
    existing: dict[str, sqlite3.Row],
    summary: ScanSummary,
) -> None:
    now = catalog.now()
    with catalog.transaction(conn):
        for shot, record in chunk:
            values = _metadata_row(record)
            if "ExifTool:Error" in record or "File:Error" in record:
                summary.errors.append(f"{shot.name}: {record.get('ExifTool:Error') or record.get('File:Error')}")
            prev = existing.get(shot.name)
            if prev is not None:
                # The file at this name changed: new content, so earlier analysis no longer applies.
                conn.execute(
                    "UPDATE images SET kind = ?, jpeg_name = ?, size = ?, mtime_ns = ?, quick_hash = ?, "
                    "content_hash = NULL, state = 'present', capture_time = ?, shutter_count = ?, orientation = ?, "
                    "meta = ?, exif = ?, previews = ?, width = NULL, height = NULL, scanned_at = ? WHERE id = ?",
                    (
                        shot.kind,
                        shot.jpeg_name,
                        shot.size,
                        shot.mtime_ns,
                        hashes[shot.name],
                        values["capture_time"],
                        values["shutter_count"],
                        values["orientation"],
                        values["meta"],
                        values["exif"],
                        values["previews"],
                        now,
                        prev["id"],
                    ),
                )
                conn.execute("DELETE FROM analyses WHERE image_id = ?", (prev["id"],))
                conn.execute("DELETE FROM faces WHERE image_id = ?", (prev["id"],))
                summary.changed += 1
            else:
                conn.execute(
                    "INSERT INTO images (folder_id, name, kind, jpeg_name, size, mtime_ns, quick_hash, capture_time, "
                    "shutter_count, orientation, meta, exif, previews, scanned_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        folder_id,
                        shot.name,
                        shot.kind,
                        shot.jpeg_name,
                        shot.size,
                        shot.mtime_ns,
                        hashes[shot.name],
                        values["capture_time"],
                        values["shutter_count"],
                        values["orientation"],
                        values["meta"],
                        values["exif"],
                        values["previews"],
                        now,
                    ),
                )
                summary.new += 1
