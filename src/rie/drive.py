"""Is the photo drive connected? Decided from the device and the mount table, never by touching the photo folders.

An empty mount point looks like an empty folder, and with a systemd automount (`x-systemd.automount`) any access to
the mount point while the drive is unplugged blocks until the device timeout before failing. So the check reads
only `/dev/disk/by-uuid` and `/proc/self/mountinfo`: the drive is connected when a device with the configured
filesystem UUID exists and a real (non-autofs) filesystem from that device is mounted.
See docs/adr/0005-external-drive.md.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

BY_UUID = Path("/dev/disk/by-uuid")
MOUNTINFO = Path("/proc/self/mountinfo")


class DriveState(StrEnum):
    CONNECTED = "connected"  # device present and mounted; safe to read
    NOT_MOUNTED = "not mounted"  # device present, filesystem not mounted (an automount mounts it on first access)
    UNPLUGGED = "unplugged"  # no device with this UUID; do not touch the mount point
    WRONG_DEVICE = "wrong device"  # another filesystem is mounted at the configured mount point
    UNCONFIGURED = "unconfigured"  # no drive.uuid in config.toml


@dataclass(frozen=True)
class MountEntry:
    major: int
    minor: int
    mountpoint: Path
    fstype: str
    source: str


@dataclass(frozen=True)
class DriveStatus:
    state: DriveState
    uuid: str | None = None
    device: Path | None = None
    mountpoint: Path | None = None
    detail: str = ""

    @property
    def connected(self) -> bool:
        return self.state is DriveState.CONNECTED


_ESCAPE = re.compile(r"\\([0-7]{3})")


def _unescape(field: str) -> str:
    """mountinfo escapes space, tab, newline and backslash as octal (`\\040`)."""
    return _ESCAPE.sub(lambda m: chr(int(m.group(1), 8)), field)


def parse_mountinfo(text: str) -> list[MountEntry]:
    """Parse /proc/self/mountinfo.

    Each line reads `id parent major:minor root mountpoint options [optional...] - fstype source superoptions`.
    """
    entries = []
    for line in text.splitlines():
        before, sep, after = line.partition(" - ")
        if not sep:
            continue
        fields, tail = before.split(), after.split()
        if len(fields) < 5 or len(tail) < 2:
            continue
        major, _, minor = fields[2].partition(":")
        entries.append(
            MountEntry(
                major=int(major),
                minor=int(minor),
                mountpoint=Path(_unescape(fields[4])),
                fstype=tail[0],
                source=_unescape(tail[1]),
            )
        )
    return entries


def drive_status(
    uuid: str | None,
    mountpoint: Path | None = None,
    *,
    by_uuid: Path = BY_UUID,
    mountinfo: Path = MOUNTINFO,
) -> DriveStatus:
    """Where the drive with filesystem `uuid` is mounted, if it is; `mountpoint` is where it is expected."""
    if not uuid:
        return DriveStatus(DriveState.UNCONFIGURED, detail="set drive.uuid in config.toml (see /dev/disk/by-uuid)")
    link = by_uuid / uuid
    if not link.exists():
        return DriveStatus(
            DriveState.UNPLUGGED, uuid, mountpoint=mountpoint, detail=f"no device has filesystem UUID {uuid}"
        )
    device = link.resolve()
    rdev = os.stat(device).st_rdev
    ids = (os.major(rdev), os.minor(rdev))
    entries = [e for e in parse_mountinfo(mountinfo.read_text(encoding="utf-8")) if e.fstype != "autofs"]
    mine = [e for e in entries if (e.major, e.minor) == ids or e.source == str(device)]

    if mountpoint is not None:
        if any(e.mountpoint == mountpoint for e in mine):
            return DriveStatus(DriveState.CONNECTED, uuid, device, mountpoint)
        others = [e for e in entries if e.mountpoint == mountpoint]
        if others:
            return DriveStatus(
                DriveState.WRONG_DEVICE, uuid, device, mountpoint, f"{others[-1].source} is mounted at {mountpoint}"
            )
    if mine:
        where = mine[-1].mountpoint
        moved = f"mounted at {where}, not {mountpoint}" if mountpoint is not None else ""
        return DriveStatus(DriveState.CONNECTED, uuid, device, where, moved)
    return DriveStatus(DriveState.NOT_MOUNTED, uuid, device, mountpoint, f"{device} is present but not mounted")


def ensure_mounted(status: DriveStatus, *, by_uuid: Path = BY_UUID, mountinfo: Path = MOUNTINFO) -> DriveStatus:
    """Trigger an automount by listing the mount point, but only when the device is present."""
    if status.state is not DriveState.NOT_MOUNTED or status.mountpoint is None:
        return status
    try:
        with os.scandir(status.mountpoint) as entries:
            next(entries, None)
    except OSError as exc:
        return DriveStatus(
            DriveState.NOT_MOUNTED, status.uuid, status.device, status.mountpoint, f"mount failed: {exc}"
        )
    return drive_status(status.uuid, status.mountpoint, by_uuid=by_uuid, mountinfo=mountinfo)
