import os
import tempfile
from pathlib import Path

import pytest

from rie import fileops, safety


@pytest.fixture
def volume(tmp_path):
    """A stand-in for the photo drive: a top directory with a shoot folder in it."""
    top = tmp_path / "drive"
    shoot = top / "Photos" / "2026 - shoot"
    shoot.mkdir(parents=True)
    return top, shoot


def nef(folder: Path, name: str = "DSC_0001.NEF", data: bytes = b"raw data") -> Path:
    path = folder / name
    path.write_bytes(data)
    return path


def test_trash_moves_the_file_and_writes_trashinfo(volume):
    top, shoot = volume
    photo = nef(shoot)
    trashed = fileops.trash(photo, topdir=top)
    uid = os.getuid()
    assert not photo.exists()
    assert trashed.trashed == top / f".Trash-{uid}" / "files" / "DSC_0001.NEF"
    assert trashed.trashed.read_bytes() == b"raw data"
    info = trashed.info.read_text()
    assert info.startswith("[Trash Info]\nPath=Photos/2026%20-%20shoot/DSC_0001.NEF\nDeletionDate=")
    assert trashed.size == len(b"raw data")


def test_trash_never_replaces_an_earlier_trashed_file(volume):
    top, shoot = volume
    first = fileops.trash(nef(shoot, data=b"one"), topdir=top)
    second = fileops.trash(nef(shoot, data=b"two"), topdir=top)
    assert first.trashed.read_bytes() == b"one"
    assert second.trashed.name == "DSC_0001.2.NEF" and second.trashed.read_bytes() == b"two"


def test_trash_uses_the_shared_trash_when_it_has_the_sticky_bit(volume):
    top, shoot = volume
    shared = top / ".Trash"
    shared.mkdir()
    shared.chmod(0o1777)
    trashed = fileops.trash(nef(shoot), topdir=top)
    assert trashed.trashed.parent == shared / str(os.getuid()) / "files"


def test_trash_refuses_folders(volume):
    top, shoot = volume
    with pytest.raises(fileops.FileOpsError, match="only regular files"):
        fileops.trash(shoot, topdir=top)


@pytest.mark.skipif(not Path("/dev/shm").is_dir(), reason="needs a second filesystem")
def test_trash_on_another_device_is_refused(volume):
    _top, shoot = volume
    photo = nef(shoot)
    with tempfile.TemporaryDirectory(dir="/dev/shm") as other:
        if os.stat(other).st_dev == os.stat(shoot).st_dev:
            pytest.skip("/dev/shm is on the same device here")
        with pytest.raises(fileops.NotSameDrive):
            fileops.trash(photo, topdir=Path(other))
    assert photo.read_bytes() == b"raw data"  # untouched


def test_restore_puts_the_file_back_and_removes_the_trashinfo(volume):
    top, shoot = volume
    photo = nef(shoot)
    trashed = fileops.trash(photo, topdir=top)
    fileops.restore(trashed.trashed, photo, trashed.info)
    assert photo.read_bytes() == b"raw data"
    assert not trashed.trashed.exists() and not trashed.info.exists()


def test_restore_never_overwrites(volume):
    top, shoot = volume
    photo = nef(shoot)
    trashed = fileops.trash(photo, topdir=top)
    nef(shoot, data=b"a new file with the same name")
    with pytest.raises(fileops.FileOpsError, match="refusing to restore over it"):
        fileops.restore(trashed.trashed, photo, trashed.info)
    assert photo.read_bytes() == b"a new file with the same name"
    assert trashed.trashed.read_bytes() == b"raw data"


def test_restore_reports_an_emptied_trash(volume):
    top, shoot = volume
    with pytest.raises(fileops.FileOpsError, match="no longer in the trash"):
        fileops.restore(top / ".Trash-1000" / "files" / "gone.NEF", shoot / "gone.NEF")


def test_create_file_never_replaces(volume):
    _top, shoot = volume
    sidecar = shoot / "DSC_0001.NEF.pp3"
    digest = fileops.create_file(sidecar, b"[General]\nRank=3\n")
    assert sidecar.read_bytes() == b"[General]\nRank=3\n" and digest == fileops.sha256_bytes(sidecar.read_bytes())
    with pytest.raises(FileExistsError):
        fileops.create_file(sidecar, b"other")
    assert [p.name for p in shoot.iterdir()] == ["DSC_0001.NEF.pp3"]  # no temporary file left behind


def test_rename_noreplace_refuses_an_existing_target(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    with pytest.raises(FileExistsError):
        fileops._rename_noreplace(a, b)
    assert b.read_bytes() == b"b"


def test_update_sidecar_needs_a_backup_and_the_content_it_read(volume, tmp_path):
    _top, shoot = volume
    sidecar = shoot / "DSC_0001.NEF.pp3"
    sidecar.write_bytes(b"[General]\nRank=2\n")
    current = fileops.sha256_bytes(sidecar.read_bytes())
    with pytest.raises(fileops.FileOpsError, match="no backup"):
        fileops.update_sidecar(sidecar, b"x", expected_sha256=current, backed_up=False, created_by_app=False)
    backup = fileops.backup_sidecar(sidecar, tmp_path / "backups")
    assert backup.read_bytes() == b"[General]\nRank=2\n"
    with pytest.raises(fileops.FileOpsError, match="changed since it was read"):
        fileops.update_sidecar(sidecar, b"x", expected_sha256="0" * 64, backed_up=True, created_by_app=False)
    new = b"[General]\nRank=4\n"
    fileops.update_sidecar(sidecar, new, expected_sha256=current, backed_up=True, created_by_app=False)
    assert sidecar.read_bytes() == b"[General]\nRank=4\n"
    assert sorted(p.name for p in shoot.iterdir()) == ["DSC_0001.NEF.pp3"]


@pytest.mark.parametrize("name", ["DSC_0001.NEF", "DSC_0001.JPG", "notes.txt"])
def test_only_sidecars_are_updated_in_place(volume, name):
    _top, shoot = volume
    path = nef(shoot, name)
    digest = fileops.sha256_bytes(path.read_bytes())
    with pytest.raises(fileops.FileOpsError, match="only RawTherapee sidecars"):
        fileops.update_sidecar(path, b"x", expected_sha256=digest, backed_up=True, created_by_app=True)
    assert path.read_bytes() == b"raw data"


def test_trash_usage_and_space_report(volume):
    top, shoot = volume
    fileops.trash(nef(shoot, "a.NEF", b"12345"), topdir=top)
    fileops.trash(nef(shoot, "b.NEF", b"123"), topdir=top)
    usage = fileops.trash_usage(top)
    assert (usage.files, usage.bytes) == (2, 8)
    report = safety.space_report(top, min_free_gb=1e9)
    assert report.low and "LOW" in report.describe()


def test_autoempty_findings_read_kde_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(safety, "_run", lambda cmd: None)
    monkeypatch.setattr(safety, "_grep", lambda paths, words: [])
    config_dir = tmp_path / ".config"
    config_dir.mkdir()
    (config_dir / "ktrashrc").write_text("[/home/user/.local/share/Trash]\nUseTimeLimit=true\nDays=7\n")
    findings = safety.trash_autoempty_findings(home=tmp_path)
    assert len(findings) == 1 and "time limit" in findings[0]


def test_autoempty_findings_read_gnome_settings(tmp_path, monkeypatch):
    answers = {"remove-old-trash-files": "true\n", "old-files-age": "uint32 30\n"}
    monkeypatch.setattr(safety, "_run", lambda cmd: answers.get(cmd[-1]))
    monkeypatch.setattr(safety, "_grep", lambda paths, words: [])
    assert safety.trash_autoempty_findings(home=tmp_path) == [
        "GNOME deletes trashed files older than 30 days (Settings > Privacy > File History & Trash)"
    ]
