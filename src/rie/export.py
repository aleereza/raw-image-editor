"""Export (plan §6.1, §3.5): one full-size JPEG per kept, edited photo, into `jpg/` inside the shoot folder.

Each photo is rendered at full size with the full profile (capture sharpening, raw CA correction, its noise
reduction) into a temporary folder in the cache; exiftool copies the raw file's metadata into that copy
(`rie.scan.exif.copy_metadata`, which writes only inside the cache); then `rie.fileops` creates `jpg/` if needed
and the JPEG under a temporary name renamed into place, never over anything. A JPEG already at that name is moved
to the drive's trash first, and recorded so it can be restored. A photo whose recipe has not changed since its
last export, and whose JPEG is still the one written, is skipped. Each exported photo's managed RawTherapee sidecar
is written too (`rie.managed`, plan §3.3), so the folder opens in RawTherapee the way the app rendered it.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import catalog, fileops, locations, managed
from .config import Config
from .cull.actions import final_calls, topdir_for
from .engine.recipe import parse as parse_recipe
from .render import render
from .review import load_recipe
from .scan import exif
from .scan.folder import folder_path

type Progress = Callable[[str, int, int], None]

EXPORT_DIR = "jpg"


class ExportError(RuntimeError):
    """A folder that cannot be exported (drive away, nothing edited)."""


@dataclass
class ExportResult:
    exported: int = 0
    skipped: int = 0
    replaced: int = 0  # older JPEGs moved to the trash first
    sidecars: int = 0  # managed sidecars created, updated or added to
    failed: int = 0
    bytes: int = 0
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def target_name(raw_name: str) -> str:
    return f"{EXPORT_DIR}/{Path(raw_name).stem}.jpg"


def export_folder(
    conn: sqlite3.Connection,
    cfg: Config,
    folder_id: int,
    *,
    image_ids: list[int] | None = None,
    workers: int = 3,
    progress: Progress | None = None,
) -> ExportResult:
    """Export the folder's kept, edited photos (or `image_ids`) as full-size JPEGs into its `jpg/`."""
    start = time.perf_counter()
    result = ExportResult()
    row = conn.execute("SELECT drive_uuid, path FROM folders WHERE id = ?", (folder_id,)).fetchone()
    if row is None:
        raise ExportError(f"no folder {folder_id} in the catalog")
    if not locations.available(locations.Location(row["drive_uuid"], row["path"]), cfg):
        raise ExportError("the photo drive is not connected; exporting needs the raw files")
    folder = folder_path(conn, folder_id, cfg)
    kept = {c.image_id for c in final_calls(conn, folder_id) if c.final == "keep"}
    edited = {
        r[0]
        for r in conn.execute(
            "SELECT e.image_id FROM edits e JOIN images i ON i.id = e.image_id WHERE i.folder_id = ?", (folder_id,)
        )
    }
    ids = [i for i in (image_ids if image_ids is not None else sorted(kept)) if i in edited]
    if not ids:
        raise ExportError("nothing to export: edit the folder's kept photos first (`rie edit`)")
    photos = conn.execute(
        f"SELECT id, name, kind FROM images WHERE id IN ({','.join('?' * len(ids))}) ORDER BY capture_time, name", ids
    ).fetchall()
    topdir = topdir_for(conn, cfg, folder_id, folder)
    drive_uuid = row["drive_uuid"]
    tmp_root = cfg.paths.cache / "tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)

    plans = []
    for photo in photos:
        version, recipe = load_recipe(conn, photo["id"])
        recipe_sha = hashlib.sha256(catalog.dumps(recipe).encode("utf-8")).hexdigest()
        name = target_name(photo["name"])
        last = conn.execute(
            "SELECT sha256, recipe_sha256 FROM exports WHERE image_id = ? AND trash_entry IS NULL "
            "ORDER BY id DESC LIMIT 1",
            (photo["id"],),
        ).fetchone()
        target = folder / name
        if last and last["recipe_sha256"] == recipe_sha and target.is_file() and _sha256(target) == last["sha256"]:
            result.skipped += 1
            continue
        plans.append((photo, version, recipe, recipe_sha, name))

    if progress:
        progress("export", 0, len(plans))
    lenses = {
        r["id"]: (catalog.loads(r["meta"], {}) or {}).get("lens")
        for r in conn.execute("SELECT id, meta FROM images WHERE folder_id = ?", (folder_id,))
    }
    with tempfile.TemporaryDirectory(dir=tmp_root, prefix="export-") as tmp:

        def render_one(item: tuple) -> tuple[Path | None, str | None]:
            photo, _version, recipe, _sha, _name = item
            raw = folder / photo["name"]
            out = Path(tmp) / f"{photo['id']}.jpg"
            try:
                render(raw, parse_recipe(recipe), out, cfg)
                lens = lenses.get(photo["id"])
                exif.copy_metadata(
                    raw, out, cfg.paths.cache, lens=lens, artist=cfg.export.artist, copyright=cfg.export.copyright
                )
            except Exception as exc:  # one photo must not stop the export
                return None, f"{photo['name']}: {type(exc).__name__}: {exc}"
            return out, None

        done = 0
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for item, (rendered, error) in zip(plans, pool.map(render_one, plans), strict=True):
                photo, version, _recipe, recipe_sha, name = item
                done += 1
                if error or rendered is None:
                    result.failed += 1
                    result.errors.append(error or f"{photo['name']}: no render")
                    if progress:
                        progress("export", done, len(plans))
                    continue
                try:
                    _write(
                        conn,
                        folder,
                        topdir,
                        drive_uuid,
                        folder_id,
                        photo["id"],
                        name,
                        rendered,
                        version,
                        recipe_sha,
                        result,
                    )
                except (OSError, fileops.FileOpsError) as exc:
                    result.failed += 1
                    result.errors.append(f"{photo['name']}: {exc}")
                else:
                    try:
                        written = managed.write_sidecar(conn, cfg, photo["id"])
                        result.sidecars += written.action in ("created", "updated", "added")
                    except (managed.SidecarError, OSError, fileops.FileOpsError) as exc:
                        result.errors.append(f"{photo['name']} sidecar: {exc}")
                if progress:
                    progress("export", done, len(plans))
    result.seconds = time.perf_counter() - start
    return result


def _write(
    conn: sqlite3.Connection,
    folder: Path,
    topdir: Path | None,
    drive_uuid: str,
    folder_id: int,
    image_id: int,
    name: str,
    rendered: Path,
    version: int,
    recipe_sha: str,
    result: ExportResult,
) -> None:
    """Put one rendered JPEG into the folder: the old one to the trash first, then the new one, never over it."""
    target = folder / name
    data = rendered.read_bytes()
    if os.path.lexists(target):
        trashed = fileops.trash(target, topdir=topdir)
        with catalog.transaction(conn):  # recorded at once, so a failure below loses no record
            cur = conn.execute(
                "INSERT INTO trash_entries (folder_id, image_id, drive_uuid, topdir, original, trashed, info, size, "
                "trashed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    folder_id,
                    image_id,
                    drive_uuid,
                    str(trashed.topdir),
                    os.path.relpath(trashed.original, trashed.topdir),
                    os.path.relpath(trashed.trashed, trashed.topdir),
                    os.path.relpath(trashed.info, trashed.topdir),
                    trashed.size,
                    catalog.now(),
                ),
            )
            conn.execute(
                "UPDATE exports SET trash_entry = ? WHERE image_id = ? AND name = ? AND trash_entry IS NULL",
                (cur.lastrowid, image_id, name),
            )
        result.replaced += 1
    fileops.make_folder(target.parent)
    sha = fileops.create_file(target, data)
    with catalog.transaction(conn):
        conn.execute(
            "INSERT INTO exports (image_id, name, recipe_version, recipe_sha256, sha256, size, exported_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (image_id, name, version, recipe_sha, sha, len(data), catalog.now()),
        )
    result.exported += 1
    result.bytes += len(data)
