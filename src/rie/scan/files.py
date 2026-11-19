"""List a shoot folder, pair RAW+JPEG shots and hash files, reading only.

A folder is scanned one level deep: subfolders (such as the `jpg/` exports) are not shots. Dot files and macOS
AppleDouble files (`._DSC_0001.NEF`) are skipped (ADR 0002). A camera JPEG with the same name as a raw file is the
raw's pair and counts as one photo (plan §4.1); a JPEG without a raw is a photo of its own.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

RAW_EXTENSIONS = frozenset({".nef", ".nrw"})
JPEG_EXTENSIONS = frozenset({".jpg", ".jpeg"})
SIDECAR_EXTENSION = ".pp3"
QUICK_HASH_BYTES = 64 * 1024
_QUICK_HASH_TAG = b"rie-quick-1"


@dataclass(frozen=True)
class Shot:
    """One photo: a raw file (with its camera JPEG, if any) or a JPEG on its own."""

    name: str
    kind: str  # "raw" or "jpeg"
    size: int
    mtime_ns: int
    jpeg_name: str | None = None


@dataclass(frozen=True)
class Listing:
    shots: list[Shot] = field(default_factory=list)
    sidecars: list[str] = field(default_factory=list)  # RawTherapee .pp3 files
    other: list[str] = field(default_factory=list)  # videos, other programs' sidecars, unknown raw formats
    subfolders: list[str] = field(default_factory=list)


def list_folder(folder: Path) -> Listing:
    """The shots, sidecars and other files directly inside `folder`, sorted by name."""
    raws: dict[str, os.DirEntry[str]] = {}
    jpegs: dict[str, os.DirEntry[str]] = {}
    sidecars, other, subfolders = [], [], []
    with os.scandir(folder) as entries:
        for entry in entries:
            name = entry.name
            if name.startswith("."):
                continue
            if entry.is_dir(follow_symlinks=False):
                subfolders.append(name)
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext in RAW_EXTENSIONS:
                raws[name] = entry
            elif ext in JPEG_EXTENSIONS:
                jpegs[name] = entry
            elif ext == SIDECAR_EXTENSION:
                sidecars.append(name)
            else:
                other.append(name)

    jpeg_by_stem: dict[str, str] = {}
    for name in sorted(jpegs):
        jpeg_by_stem.setdefault(os.path.splitext(name)[0].lower(), name)
    paired: set[str] = set()
    shots = []
    for name in sorted(raws):
        stat = raws[name].stat(follow_symlinks=False)
        jpeg = jpeg_by_stem.get(os.path.splitext(name)[0].lower())
        if jpeg is not None and jpeg not in paired:
            paired.add(jpeg)
        else:
            jpeg = None
        shots.append(Shot(name, "raw", stat.st_size, stat.st_mtime_ns, jpeg))
    for name in sorted(jpegs):
        if name not in paired:
            stat = jpegs[name].stat(follow_symlinks=False)
            shots.append(Shot(name, "jpeg", stat.st_size, stat.st_mtime_ns))
    shots.sort(key=lambda s: s.name)
    return Listing(shots=shots, sidecars=sorted(sidecars), other=sorted(other), subfolders=sorted(subfolders))


def quick_hash(path: Path, size: int | None = None) -> str:
    """SHA-256 of the file size and its first 64 KiB.

    For a NEF that span holds the EXIF block, the maker notes (shutter count, serial number) and a thumbnail, so it
    identifies a shot after a move or rename without reading 30 MB over USB. The full content hash comes later,
    when analysis reads the whole file anyway.
    """
    with open(path, "rb") as fh:
        head = fh.read(QUICK_HASH_BYTES)
        if size is None:
            size = os.fstat(fh.fileno()).st_size
    digest = hashlib.sha256(_QUICK_HASH_TAG)
    digest.update(size.to_bytes(8, "little"))
    digest.update(head)
    return "q1:" + digest.hexdigest()


def content_hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()
