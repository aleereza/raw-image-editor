"""Where a folder lives: on the photo drive (by filesystem UUID and a path relative to its mount point) or elsewhere.

The catalog stores folders on the configured photo drive as (drive UUID, relative path), so a different mount point
does not break anything (plan §3.3). Before anything reads a folder on the drive, the drive's state is checked from
the device and the mount table only (ADR 0005): with an automount, touching the mount point of an unplugged drive
would block for the device timeout. Folders elsewhere (another disk, a test folder) are stored by absolute path.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from . import drive
from .config import Config


class DriveUnavailable(RuntimeError):
    """The folder is on the photo drive, and the drive is not connected."""

    def __init__(self, status: drive.DriveStatus) -> None:
        detail = f": {status.detail}" if status.detail else ""
        super().__init__(f"the photo drive is {status.state}{detail}")
        self.status = status


@dataclass(frozen=True)
class Location:
    drive_uuid: str  # "" when not on the configured photo drive
    path: str  # relative to the drive's mount point, or absolute

    @property
    def on_drive(self) -> bool:
        return bool(self.drive_uuid)


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def drive_roots(cfg: Config, status: drive.DriveStatus | None = None) -> list[str]:
    """Paths that mean "on the photo drive": its current mount point, the configured one and the photos folder."""
    roots = []
    if status is not None and status.mountpoint is not None:
        roots.append(os.path.abspath(status.mountpoint))
    if cfg.drive.mountpoint is not None:
        roots.append(os.path.abspath(cfg.drive.mountpoint))
    if cfg.paths.photos is not None and not roots:
        roots.append(os.path.abspath(cfg.paths.photos))
    return list(dict.fromkeys(roots))


def status(cfg: Config) -> drive.DriveStatus:
    return drive.drive_status(cfg.drive.uuid, cfg.drive.mountpoint)


def locate(folder: Path, cfg: Config) -> Location:
    """The catalog location of `folder`; raises DriveUnavailable for a folder on a drive that is not connected.

    The path is made absolute without touching the filesystem, so asking about an unplugged drive never blocks.
    """
    path = os.path.abspath(os.path.expanduser(folder))
    if not cfg.drive.uuid:
        return Location("", path)
    state = status(cfg)
    roots = drive_roots(cfg, state)
    root = next((r for r in roots if _under(path, r)), None)
    if root is None:
        return Location("", path)
    if state.state is drive.DriveState.NOT_MOUNTED:
        state = drive.ensure_mounted(state)
    if not state.connected or state.mountpoint is None:
        raise DriveUnavailable(state)
    mount = os.path.abspath(state.mountpoint)
    if not _under(path, mount):
        # The drive is mounted somewhere else now: keep the part below the configured root.
        path = os.path.join(mount, os.path.relpath(path, root))
    return Location(cfg.drive.uuid, os.path.relpath(path, mount))


def resolve(location: Location, cfg: Config) -> Path:
    """The folder's current absolute path; raises DriveUnavailable when its drive is not connected."""
    if not location.on_drive:
        return Path(location.path)
    if location.drive_uuid != cfg.drive.uuid:
        raise DriveUnavailable(
            drive.DriveStatus(
                drive.DriveState.UNPLUGGED,
                location.drive_uuid,
                detail=f"the folder is on drive {location.drive_uuid}; config.toml names {cfg.drive.uuid or 'none'}",
            )
        )
    state = status(cfg)
    if state.state is drive.DriveState.NOT_MOUNTED:
        state = drive.ensure_mounted(state)
    if not state.connected or state.mountpoint is None:
        raise DriveUnavailable(state)
    return Path(state.mountpoint) / location.path


def available(location: Location, cfg: Config) -> bool:
    """Whether the folder's files can be read now, without touching an unplugged drive."""
    if not location.on_drive:
        return True
    return location.drive_uuid == cfg.drive.uuid and status(cfg).connected
