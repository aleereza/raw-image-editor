"""The only code that changes anything in a photo folder (plan §3.5, AGENTS.md).

What it can do, and nothing else:

- **Move a file to its volume's trash** (the freedesktop.org trash: `<topdir>/.Trash-<uid>/` on the photo drive). It
  refuses when the trash is not on the same device as the file, so a move is always a rename on that drive and
  never a copy and delete. It records where the file went, writing the `.trashinfo` file first, as the trash
  specification asks, so file managers can restore it too.
- **Restore** a trashed file to where it was, never over an existing file. After the file is back, its `.trashinfo`
  is removed, as the specification asks; that metadata file is the one thing this module ever removes.
- **Create a new file** (a sidecar) under a temporary name, then rename it into place without replacing anything.
- **Update a RawTherapee sidecar** in place through a temporary name: only `.pp3` files, only when the content on
  disk is still what the caller read, and only when the app created the sidecar or has backed it up first.
- **Back up a sidecar** to the internal disk before the app first adds to it (reading the photo folder only).

It never empties the trash, never deletes a photo, and never writes into a raw file. Renames use `renameat2` with
`RENAME_NOREPLACE` where the kernel and filesystem support it, so nothing is replaced even in a race. Every
operation raises the audit event `rie.fileops`, which the safety tests watch.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import errno
import hashlib
import os
import secrets
import shutil
import stat
import sys
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SIDECAR_SUFFIX = ".pp3"
_TMP_MARK = ".rie-tmp-"


class FileOpsError(RuntimeError):
    """Raised when an operation is refused or cannot be completed safely."""


class NotSameDrive(FileOpsError):
    """The trash for a file is not on the file's device; moving there would mean copying and deleting."""


def _audit(op: str, *paths: Path | str) -> None:
    sys.audit("rie.fileops", op, *(str(p) for p in paths))


# --- renaming without replacing ------------------------------------------------------------------------------------

_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
_libc_name = ctypes.util.find_library("c")
_libc = ctypes.CDLL(_libc_name, use_errno=True) if _libc_name else None
_renameat2 = getattr(_libc, "renameat2", None) if _libc is not None else None


def _rename_noreplace(src: Path, dst: Path) -> None:
    """Rename `src` to `dst` on the same filesystem, failing with FileExistsError if `dst` exists."""
    if _renameat2 is not None:
        result = _renameat2(_AT_FDCWD, os.fsencode(src), _AT_FDCWD, os.fsencode(dst), _RENAME_NOREPLACE)
        if result == 0:
            return
        err = ctypes.get_errno()
        if err == errno.EEXIST:
            raise FileExistsError(errno.EEXIST, "refusing to replace an existing file", str(dst))
        if err not in (errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP):
            raise OSError(err, os.strerror(err), str(src), None, str(dst))
    # Without RENAME_NOREPLACE: check first. The app is the only writer of these names, so the window is harmless.
    if os.path.lexists(dst):
        raise FileExistsError(errno.EEXIST, "refusing to replace an existing file", str(dst))
    os.rename(src, dst)


# --- the trash -----------------------------------------------------------------------------------------------------


def volume_topdir(path: Path) -> Path:
    """The mount point of the filesystem that holds `path` (the highest ancestor on the same device)."""
    path = Path(os.path.abspath(path))
    device = os.lstat(path).st_dev
    current = path
    while current.parent != current:
        try:
            if os.lstat(current.parent).st_dev != device:
                break
        except OSError:
            break
        current = current.parent
    return current


def home_trash() -> Path:
    data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(data_home) / "Trash"


@dataclass(frozen=True)
class TrashDir:
    topdir: Path  # the volume's top directory; trashinfo paths are relative to it
    path: Path  # <topdir>/.Trash/<uid> or <topdir>/.Trash-<uid>, or the home trash
    relative_paths: bool  # trashinfo Path= is relative to topdir (volume trash) or absolute (home trash)

    @property
    def files(self) -> Path:
        return self.path / "files"

    @property
    def info(self) -> Path:
        return self.path / "info"


def volume_trash(topdir: Path) -> TrashDir:
    """The trash of the volume whose top directory is `topdir`: the shared `.Trash/<uid>` when `.Trash` is a real
    directory with the sticky bit, else `.Trash-<uid>` (freedesktop.org trash specification)."""
    top = Path(os.path.abspath(topdir))
    uid = os.getuid()
    shared = top / ".Trash"
    try:
        info = os.lstat(shared)
        if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode) and info.st_mode & stat.S_ISVTX:
            return TrashDir(topdir=top, path=shared / str(uid), relative_paths=True)
    except FileNotFoundError:
        pass
    return TrashDir(topdir=top, path=top / f".Trash-{uid}", relative_paths=True)


def trash_dir(path: Path, topdir: Path | None = None) -> TrashDir:
    """The trash folder for `path`: the home trash for a file on the home partition, else its volume's trash."""
    path = Path(os.path.abspath(path))
    device = os.lstat(path).st_dev
    home = home_trash()
    try:
        home_device = os.stat(home if home.exists() else home.parent).st_dev
    except OSError:
        home_device = None
    if topdir is None and home_device == device:
        return TrashDir(topdir=home.parent, path=home, relative_paths=False)
    return volume_trash(topdir if topdir is not None else volume_topdir(path))


@dataclass(frozen=True)
class Trashed:
    original: Path
    trashed: Path  # in <trash>/files
    info: Path  # in <trash>/info
    topdir: Path
    size: int


def _ensure_trash(trash: TrashDir, device: int) -> None:
    for folder in (trash.path, trash.files, trash.info):
        if not folder.exists():
            _audit("mkdir", folder)
            folder.mkdir(mode=0o700, parents=folder is trash.path)
    for folder in (trash.files, trash.info):
        info = os.lstat(folder)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise FileOpsError(f"{folder} is not a real folder; refusing to trash into it")
        if info.st_dev != device:
            raise NotSameDrive(
                f"the trash {trash.path} is on another device than the file; refusing to copy and delete"
            )


def _trashinfo(original: Path, trash: TrashDir) -> bytes:
    if trash.relative_paths:
        try:
            rel = original.relative_to(trash.topdir)
        except ValueError as exc:
            raise FileOpsError(f"{original} is not under {trash.topdir}") from exc
        recorded = rel.as_posix()
    else:
        recorded = str(original)
    when = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")  # local time, as the specification says
    text = f"[Trash Info]\nPath={urllib.parse.quote(recorded, safe='/')}\nDeletionDate={when}\n"
    return text.encode("utf-8")


def trash(path: Path, *, topdir: Path | None = None) -> Trashed:
    """Move the file `path` to its volume's trash and say where it went. Refuses folders and other devices."""
    path = Path(os.path.abspath(path))
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode):
        raise FileOpsError(f"only regular files are trashed: {path}")
    trash_folder = trash_dir(path, topdir)
    if os.lstat(path.parent).st_dev != info.st_dev:
        raise NotSameDrive(f"{path} is a mount point")
    _ensure_trash(trash_folder, info.st_dev)
    content = _trashinfo(path, trash_folder)

    stem, suffix = path.stem, path.suffix
    for n in range(1, 10_000):
        name = path.name if n == 1 else f"{stem}.{n}{suffix}"
        info_path = trash_folder.info / f"{name}.trashinfo"
        target = trash_folder.files / name
        try:
            fd = os.open(info_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        if os.path.lexists(target):  # an entry without trashinfo: leave it alone and use another name
            os.close(fd)
            _remove_trashinfo(info_path)
            continue
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            _audit("trash", path, target)
            _rename_noreplace(path, target)
        except BaseException:
            _remove_trashinfo(info_path)
            raise
        return Trashed(original=path, trashed=target, info=info_path, topdir=trash_folder.topdir, size=info.st_size)
    raise FileOpsError(f"no free name in {trash_folder.files} for {path.name}")


def _remove_trashinfo(info_path: Path) -> None:
    """Remove a .trashinfo this module wrote: the one removal it makes, and only inside a trash's info folder."""
    if info_path.suffix != ".trashinfo" or info_path.parent.name != "info":
        raise FileOpsError(f"refusing to remove {info_path}")
    _audit("remove-trashinfo", info_path)
    info_path.unlink(missing_ok=True)


def restore(trashed: Path, original: Path, info: Path | None = None) -> Path:
    """Move a trashed file back to `original`; refuses when something is already there."""
    trashed, original = Path(os.path.abspath(trashed)), Path(os.path.abspath(original))
    if not os.path.lexists(trashed):
        raise FileOpsError(f"{trashed.name} is no longer in the trash (it may have been emptied or restored)")
    if os.path.lexists(original):
        raise FileOpsError(f"{original} exists; refusing to restore over it")
    if not original.parent.is_dir():
        raise FileOpsError(f"the folder {original.parent} no longer exists; restore it from the file manager")
    if os.lstat(original.parent).st_dev != os.lstat(trashed).st_dev:
        raise NotSameDrive(f"{original.parent} is on another device than the trash")
    _audit("restore", trashed, original)
    _rename_noreplace(trashed, original)
    if info is not None:
        _remove_trashinfo(Path(os.path.abspath(info)))
    return original


@dataclass(frozen=True)
class TrashUsage:
    path: Path
    files: int
    bytes: int
    free_bytes: int
    total_bytes: int


def trash_usage(topdir: Path) -> TrashUsage:
    """How much the volume's trash holds, and how much space is left on the volume (reading only)."""
    trash_folder = volume_trash(topdir)
    count = size = 0
    if trash_folder.files.is_dir():
        for root, dirs, names in os.walk(trash_folder.files):
            for name in names + dirs:
                try:
                    info = os.lstat(os.path.join(root, name))
                except OSError:
                    continue
                if stat.S_ISREG(info.st_mode):
                    count += 1
                    size += info.st_size
    disk = shutil.disk_usage(topdir)
    return TrashUsage(path=trash_folder.path, files=count, bytes=size, free_bytes=disk.free, total_bytes=disk.total)


# --- new files and sidecars ----------------------------------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _temp_name(path: Path) -> Path:
    return path.with_name(f".{path.name}{_TMP_MARK}{secrets.token_hex(4)}")


def _write_temp(path: Path, data: bytes) -> Path:
    tmp = _temp_name(path)
    _audit("create-temp", tmp)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        _discard_temp(tmp)
        raise
    return tmp


def _discard_temp(tmp: Path) -> None:
    """A temporary file the app wrote and could not rename into place goes to the trash, like any removal."""
    if _TMP_MARK not in tmp.name or not os.path.lexists(tmp):
        return
    with contextlib.suppress(OSError, FileOpsError):  # if trashing fails, the dot file stays for the owner
        trash(tmp)


def create_file(path: Path, data: bytes) -> str:
    """Create a new file the app owns (a sidecar or an export), never replacing anything; returns its SHA-256."""
    path = Path(os.path.abspath(path))
    if os.path.lexists(path):
        raise FileExistsError(errno.EEXIST, "refusing to replace an existing file", str(path))
    tmp = _write_temp(path, data)
    try:
        _audit("create", path)
        _rename_noreplace(tmp, path)
    except BaseException:
        _discard_temp(tmp)
        raise
    return sha256_bytes(data)


def backup_sidecar(path: Path, backup_dir: Path) -> Path:
    """Copy a sidecar to the internal disk before the app first adds to it; reads the photo folder only."""
    path = Path(os.path.abspath(path))
    if path.suffix.lower() != SIDECAR_SUFFIX:
        raise FileOpsError(f"only RawTherapee sidecars are backed up: {path.name}")
    data = path.read_bytes()
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"{path.name}.{stamp}.{sha256_bytes(data)[:12]}.bak"
    if not target.exists():
        target.write_bytes(data)  # in the app's data folder, on the internal disk
    return target


def update_sidecar(path: Path, data: bytes, *, expected_sha256: str, backed_up: bool, created_by_app: bool) -> str:
    """Replace a RawTherapee sidecar's content through a temporary name; returns the new SHA-256.

    Refuses anything but a `.pp3`, a sidecar that changed since the caller read it (RawTherapee may have just
    written it), and a sidecar the app did not create unless it has been backed up.
    """
    path = Path(os.path.abspath(path))
    if path.suffix.lower() != SIDECAR_SUFFIX:
        raise FileOpsError(f"only RawTherapee sidecars are updated in place: {path.name}")
    if not (created_by_app or backed_up):
        raise FileOpsError(f"{path.name} was not created by the app and has no backup; refusing to change it")
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode):
        raise FileOpsError(f"{path} is not a regular file")
    if sha256_bytes(path.read_bytes()) != expected_sha256:
        raise FileOpsError(f"{path.name} changed since it was read; read it again before changing it")
    tmp = _write_temp(path, data)
    try:
        _audit("replace-sidecar", tmp, path)
        os.replace(tmp, path)
    except BaseException:
        _discard_temp(tmp)
        raise
    return sha256_bytes(data)
