import os
from pathlib import Path

import pytest

from rie.drive import DriveState, drive_status, ensure_mounted, parse_mountinfo

UUID = "0123456789ABCDEF"

# An automount point with the NTFS drive mounted on top, as systemd sets up `x-systemd.automount` entries.
AUTOFS = "40 32 0:39 / /mnt/photo\\040drive rw,relatime shared:15 - autofs systemd-1 rw,fd=85,direct\n"
MOUNTED = "809 40 {major}:{minor} / /mnt/photo\\040drive rw,noatime shared:895 - ntfs3 {dev} rw,uid=959\n"
ROOT = "26 1 0:25 / / rw,noatime shared:1 - btrfs /dev/mapper/root rw\n"


@pytest.fixture
def device(tmp_path):
    """A by-uuid folder whose link points at a real device node (/dev/null stands in for the disk)."""
    by_uuid = tmp_path / "by-uuid"
    by_uuid.mkdir()
    (by_uuid / UUID).symlink_to("/dev/null")
    rdev = os.stat("/dev/null").st_rdev
    return by_uuid, os.major(rdev), os.minor(rdev)


def mountinfo(tmp_path, text):
    path = tmp_path / "mountinfo"
    path.write_text(text, encoding="utf-8")
    return path


def test_parse_mountinfo_unescapes_paths():
    (autofs, mounted) = parse_mountinfo(AUTOFS + MOUNTED.format(major=8, minor=1, dev="/dev/sda1"))
    assert autofs.fstype == "autofs" and autofs.mountpoint == Path("/mnt/photo drive")
    assert (mounted.major, mounted.minor, mounted.fstype, mounted.source) == (8, 1, "ntfs3", "/dev/sda1")


def test_connected_when_the_device_is_mounted_at_the_mountpoint(tmp_path, device):
    by_uuid, major, minor = device
    info = mountinfo(tmp_path, ROOT + AUTOFS + MOUNTED.format(major=major, minor=minor, dev="/dev/sdz1"))
    status = drive_status(UUID, Path("/mnt/photo drive"), by_uuid=by_uuid, mountinfo=info)
    assert status.state is DriveState.CONNECTED and status.connected
    assert status.device == Path("/dev/null")


def test_unplugged_when_no_device_has_the_uuid_even_if_the_mountpoint_exists(tmp_path, device):
    by_uuid, *_ = device
    info = mountinfo(tmp_path, ROOT + AUTOFS)
    status = drive_status("FEDCBA9876543210", Path("/mnt/photo drive"), by_uuid=by_uuid, mountinfo=info)
    assert status.state is DriveState.UNPLUGGED


def test_stale_mount_without_its_device_counts_as_unplugged(tmp_path, device):
    """A filesystem left in the mount table after the disk vanished must not count as connected."""
    by_uuid, *_ = device
    info = mountinfo(tmp_path, ROOT + AUTOFS + MOUNTED.format(major=8, minor=1, dev="/dev/sda1"))
    status = drive_status("FEDCBA9876543210", Path("/mnt/photo drive"), by_uuid=by_uuid, mountinfo=info)
    assert status.state is DriveState.UNPLUGGED


def test_not_mounted_when_only_the_automount_is_there(tmp_path, device):
    by_uuid, *_ = device
    status = drive_status(UUID, Path("/mnt/photo drive"), by_uuid=by_uuid, mountinfo=mountinfo(tmp_path, ROOT + AUTOFS))
    assert status.state is DriveState.NOT_MOUNTED


def test_wrong_device_at_the_mountpoint(tmp_path, device):
    by_uuid, major, minor = device
    info = mountinfo(tmp_path, ROOT + AUTOFS + MOUNTED.format(major=major + 1, minor=minor, dev="/dev/sdy1"))
    status = drive_status(UUID, Path("/mnt/photo drive"), by_uuid=by_uuid, mountinfo=info)
    assert status.state is DriveState.WRONG_DEVICE
    assert "/dev/sdy1" in status.detail


def test_drive_mounted_elsewhere_is_found_by_device(tmp_path, device):
    by_uuid, major, minor = device
    info = mountinfo(tmp_path, ROOT + MOUNTED.format(major=major, minor=minor, dev="/dev/sdz1"))
    status = drive_status(UUID, Path("/media/elsewhere"), by_uuid=by_uuid, mountinfo=info)
    assert status.state is DriveState.CONNECTED
    assert status.mountpoint == Path("/mnt/photo drive")
    assert "not /media/elsewhere" in status.detail


def test_unconfigured_without_uuid():
    assert drive_status(None).state is DriveState.UNCONFIGURED


def test_ensure_mounted_leaves_an_unplugged_drive_alone(tmp_path, device):
    by_uuid, *_ = device
    gone = tmp_path / "gone"  # would raise if listed
    status = drive_status("FEDCBA9876543210", gone, by_uuid=by_uuid, mountinfo=mountinfo(tmp_path, ROOT))
    assert ensure_mounted(status, by_uuid=by_uuid).state is DriveState.UNPLUGGED
