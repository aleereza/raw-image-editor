"""Managed RawTherapee sidecars (plan §3.3, decision 14): the app's edit of a photo written next to its raw file,
so the RawTherapee GUI opens it the way the app renders it, and the owner can finish it by hand there.

All writes go through `rie.fileops`:

- **no sidecar yet:** the app creates one holding the base profile and the photo's recipe;
- **a sidecar the app created**, unchanged since the app wrote it: rewritten with the current recipe; if it changed
  (the owner edited it in RawTherapee), nothing is written until `rie open` has imported the owner's changes;
- **a sidecar the owner made** (Phase 0 found thousands): backed up to the internal disk once, before the app first
  adds to it, and then the app only adds keys the sidecar lacks. It never removes or changes the owner's keys; the
  photo's recipe already starts from them, as the owner's locked values.

What the app wrote is kept in `sidecars.written`, so that importing the sidecar later takes only what the owner
changed in RawTherapee as theirs (`rie.sidecars`).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import catalog, fileops
from .config import Config
from .engine import pp3
from .engine.recipe import base_profile_path, compile_profile
from .engine.recipe import parse as parse_recipe
from .review import load_recipe
from .scan.folder import folder_path


class SidecarError(RuntimeError):
    """A sidecar the app may not write now."""


@dataclass
class Written:
    path: Path
    action: str  # created, updated, added, unchanged
    keys: int  # keys written or added


def profile_for(conn: sqlite3.Connection, cfg: Config, image_id: int) -> tuple[pp3.Profile, int]:
    """The whole profile the app renders the photo with (base profile and recipe), and the recipe's version."""
    version, recipe = load_recipe(conn, image_id)
    parsed = parse_recipe(recipe)
    base = pp3.load(base_profile_path(parsed, cfg.rawtherapee.profiles_dir))
    return pp3.stack(base, compile_profile(parsed)), version


def write_sidecar(conn: sqlite3.Connection, cfg: Config, image_id: int) -> Written:
    row = conn.execute("SELECT folder_id, name FROM images WHERE id = ?", (image_id,)).fetchone()
    if row is None:
        raise SidecarError(f"no photo {image_id}")
    folder_id, name = row["folder_id"], row["name"]
    folder = folder_path(conn, folder_id, cfg)
    sidecar_name = f"{name}.pp3"
    path = folder / sidecar_name
    profile, version = profile_for(conn, cfg, image_id)
    header = [f"written by raw-image-editor from recipe version {version}; RawTherapee may rewrite this file"]
    known = conn.execute(
        "SELECT id, created_by_app, backup_path, sha256 FROM sidecars WHERE folder_id = ? AND name = ?",
        (folder_id, sidecar_name),
    ).fetchone()
    if not path.is_file():
        text = pp3.dump(profile, header=header)
        sha = fileops.create_file(path, text.encode("utf-8"))
        _record(conn, folder_id, image_id, sidecar_name, sha, text, created_by_app=True, backup=None)
        return Written(path, "created", sum(len(v) for v in profile.values()))
    data = path.read_bytes()
    current_sha = fileops.sha256_bytes(data)
    if known is not None and known["created_by_app"]:
        if known["sha256"] != current_sha:
            raise SidecarError(
                f"{sidecar_name} changed in RawTherapee since the app wrote it; open the folder again (`rie open`) "
                "to import those changes first"
            )
        text = pp3.dump(profile, header=header)
        if text.encode("utf-8") == data:
            return Written(path, "unchanged", 0)
        sha = fileops.update_sidecar(
            path, text.encode("utf-8"), expected_sha256=current_sha, backed_up=False, created_by_app=True
        )
        _record(conn, folder_id, image_id, sidecar_name, sha, text, created_by_app=True, backup=None)
        return Written(path, "updated", sum(len(v) for v in profile.values()))
    # The owner's sidecar: back it up once, then only add what it lacks.
    existing = pp3.parse(data.decode("utf-8", errors="replace"))
    missing = {
        section: {k: v for k, v in keys.items() if k not in existing.get(section, {})}
        for section, keys in profile.items()
    }
    missing = {s: k for s, k in missing.items() if k}
    if not missing:
        return Written(path, "unchanged", 0)
    backup = known["backup_path"] if known is not None and known["backup_path"] else None
    if backup is None or not Path(backup).is_file():
        backup = str(fileops.backup_sidecar(path, cfg.paths.data / "sidecar-backups" / str(folder_id)))
    merged = pp3.stack(existing, missing)
    text = pp3.dump(merged)
    sha = fileops.update_sidecar(
        path, text.encode("utf-8"), expected_sha256=current_sha, backed_up=True, created_by_app=False
    )
    _record(conn, folder_id, image_id, sidecar_name, sha, text, created_by_app=False, backup=backup)
    return Written(path, "added", sum(len(v) for v in missing.values()))


def _record(
    conn: sqlite3.Connection,
    folder_id: int,
    image_id: int,
    name: str,
    sha: str,
    text: str,
    *,
    created_by_app: bool,
    backup: str | None,
) -> None:
    with catalog.transaction(conn):
        conn.execute(
            "INSERT INTO sidecars (folder_id, image_id, name, created_by_app, backup_path, sha256, written) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (folder_id, name) DO UPDATE SET image_id = excluded.image_id, "
            "created_by_app = MAX(sidecars.created_by_app, excluded.created_by_app), "
            "backup_path = COALESCE(excluded.backup_path, sidecars.backup_path), sha256 = excluded.sha256, "
            "written = excluded.written",
            (folder_id, image_id, name, int(created_by_app), backup, sha, text),
        )
