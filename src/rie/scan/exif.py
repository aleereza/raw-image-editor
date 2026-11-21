"""Metadata from exiftool: one process per folder, fed in chunks, reading only.

exiftool runs with `-stay_open`, so a folder costs one process however many chunks it is read in; chunks let the
grid fill in while the rest of the folder is read. Each chunk is read twice: once with `-n -G1 -a` for numeric
values (stored whole in the catalog), and once for the few tags whose printed form matters (the lens name, AF point
names, the shooting mode), because `-n` cannot be undone per tag (ADR 0002).

The arguments are built here, from a fixed list of read-only options plus absolute file paths (which cannot be
mistaken for options), so no write option can reach exiftool.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Iterator, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

NUMERIC_ARGS = ("-json", "-n", "-G1", "-a")
PRINTED_TAGS = ("LensID", "AFAreaMode", "PrimaryAFPoint", "AFPointsUsed", "ShootingMode", "FocusMode")
PRINTED_ARGS = ("-json", "-G1", *(f"-{tag}" for tag in PRINTED_TAGS))
CHUNK = 25

# Options that make exiftool write files or change how commands are read; they must never reach it.
_WRITE_OPTIONS = re.compile(
    r"^-(o|out|overwrite_original.*|tagsfromfile|delete_original.*|restore_original|geotag|srcfile|w!?\+?|"
    r"execute.*|stay_open|@|[A-Za-z0-9_:#-]+[-+<]?=.*)$",
    re.I,
)


class ExifError(RuntimeError):
    """Raised when exiftool is missing or stops answering."""


def check_read_only(args: Sequence[str]) -> None:
    for arg in args:
        if arg.startswith("-") and _WRITE_OPTIONS.match(arg):
            raise ExifError(f"refusing to pass a write option to exiftool: {arg}")


class ExifTool:
    """A running `exiftool -stay_open` process; use as a context manager."""

    def __init__(self, executable: str = "exiftool") -> None:
        path = shutil.which(executable)
        if path is None:
            raise ExifError(f"{executable} not found on PATH")
        self._proc = subprocess.Popen(
            [path, "-stay_open", "True", "-@", "-", "-common_args", "-charset", "filename=utf8"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._count = 0

    def __enter__(self) -> ExifTool:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def execute(self, args: Sequence[str]) -> bytes:
        """Run one command (exiftool arguments, without `-execute`) and return its standard output."""
        check_read_only(args)
        if any("\n" in arg for arg in args):
            raise ExifError("exiftool arguments cannot contain line breaks")
        proc = self._proc
        assert proc.stdin is not None and proc.stdout is not None
        self._count += 1
        marker = f"{{ready{self._count}}}".encode()
        payload = "".join(f"{arg}\n" for arg in args) + f"-execute{self._count}\n"
        try:
            proc.stdin.write(payload.encode("utf-8"))
            proc.stdin.flush()
        except BrokenPipeError as exc:
            raise ExifError("exiftool exited unexpectedly") from exc
        out = bytearray()
        while True:
            line = proc.stdout.readline()
            if not line:
                raise ExifError("exiftool exited unexpectedly")
            if line.rstrip(b"\r\n") == marker:
                return bytes(out)
            out += line

    def read(self, paths: Sequence[Path]) -> list[dict[str, Any]]:
        """Numeric metadata (`-n -G1 -a`) merged with the printed tags, one dict per path, in order."""
        if not paths:
            return []
        files = [os.path.abspath(p) for p in paths]
        numeric = _records(self.execute([*NUMERIC_ARGS, *files]))
        printed = _records(self.execute([*PRINTED_ARGS, *files]))
        by_file = {r.get("SourceFile"): r for r in numeric}
        printed_by_file = {r.get("SourceFile"): r for r in printed}
        result = []
        for path in files:
            record = by_file.get(path, {"SourceFile": path, "ExifTool:Error": "no metadata"})
            extra = printed_by_file.get(path, {})
            record["rie:Printed"] = {k.split(":", 1)[-1]: v for k, v in extra.items() if k != "SourceFile"}
            result.append(record)
        return result

    def close(self) -> None:
        proc = self._proc
        if proc.poll() is None:
            try:
                assert proc.stdin is not None
                proc.stdin.write(b"-stay_open\nFalse\n")
                proc.stdin.flush()
                proc.wait(timeout=10)
            except (BrokenPipeError, subprocess.TimeoutExpired):
                proc.kill()
                proc.wait()


def _records(output: bytes) -> list[dict[str, Any]]:
    text = output.decode("utf-8", errors="replace").strip()
    if not text:
        return []
    data = json.loads(text)
    return data if isinstance(data, list) else []


def read_folder(paths: Sequence[Path], *, chunk: int = CHUNK, executable: str = "exiftool") -> Iterator[dict[str, Any]]:
    """Metadata for `paths`, read by one exiftool process in chunks."""
    with ExifTool(executable) as tool:
        for start in range(0, len(paths), chunk):
            yield from tool.read(paths[start : start + chunk])


# --- normalization -----------------------------------------------------------------------------------------------

# AF point names for 51-point bodies such as the D7100, by index (exiftool's Nikon.pm, %afPoints51):
#        A1  A2  A3  A4  A5  A6  A7  A8  A9
#    B1  B2  B3  B4  B5  B6  B7  B8  B9  B10  B11
#    C1  C2  C3  C4  C5  C6  C7  C8  C9  C10  C11
#    D1  D2  D3  D4  D5  D6  D7  D8  D9  D10  D11
#        E1  E2  E3  E4  E5  E6  E7  E8  E9
AF_POINTS_51 = {
    1: "C6", 2: "B6", 3: "A5", 4: "D6", 5: "E5", 6: "C7", 7: "B7", 8: "A6", 9: "D7", 10: "E6",
    11: "C5", 12: "B5", 13: "A4", 14: "D5", 15: "E4", 16: "C8", 17: "B8", 18: "A7", 19: "D8", 20: "E7",
    21: "C9", 22: "B9", 23: "A8", 24: "D9", 25: "E8", 26: "C10", 27: "B10", 28: "A9", 29: "D10", 30: "E9",
    31: "C11", 32: "B11", 33: "D11", 34: "C4", 35: "B4", 36: "A3", 37: "D4", 38: "E3", 39: "C3", 40: "B3",
    41: "A2", 42: "D3", 43: "E2", 44: "C2", 45: "B2", 46: "A1", 47: "D2", 48: "E1", 49: "C1", 50: "B1",
    51: "D1",
}  # fmt: skip

PREVIEW_TAGS = {"full": "JpgFromRaw", "other": "OtherImage", "small": "PreviewImage"}


def _flat(record: dict[str, Any]) -> dict[str, Any]:
    """Tags without their group, preferring the first occurrence (IFD0 and ExifIFD come before maker notes)."""
    flat: dict[str, Any] = {}
    for key, value in record.items():
        flat.setdefault(key.split(":", 1)[-1], value)
    return flat


def _group(record: dict[str, Any], group: str, tag: str) -> Any:
    return record.get(f"{group}:{tag}")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value).split()[0])
    except (ValueError, IndexError):
        return None


def af_points_from_mask(mask: Any) -> list[str]:
    """Names of the AF points set in a 51-point `AFPointsUsed` bit mask such as "01 00 00 00 00 00 00"."""
    if not isinstance(mask, str):
        return []
    try:
        data = bytes(int(part, 16) for part in mask.split())
    except ValueError:
        return []
    names = []
    for byte_index, byte in enumerate(data):
        for bit in range(8):
            if byte >> bit & 1:
                name = AF_POINTS_51.get(byte_index * 8 + bit + 1)
                if name:
                    names.append(name)
    return names


def capture_time(record: dict[str, Any]) -> str | None:
    """ISO 8601 capture time with the camera's sub-seconds (tenths on the D7100), in the camera's local time."""
    flat = _flat(record)
    value = flat.get("SubSecDateTimeOriginal") or flat.get("DateTimeOriginal") or flat.get("CreateDate")
    if not isinstance(value, str):
        return None
    match = re.match(r"(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})(\.\d+)?", value)
    if not match:
        return None
    y, mo, d, h, mi, s, frac = match.groups()
    if frac is None:
        sub = flat.get("SubSecTimeOriginal")
        frac = f".{sub}" if isinstance(sub, str | int) and str(sub).isdigit() else ""
    try:
        datetime(int(y), int(mo), int(d), int(h), int(mi), int(s))
    except ValueError:
        return None
    return f"{y}-{mo}-{d}T{h}:{mi}:{s}{frac}"


def timestamp(iso: str | None) -> float | None:
    """Seconds for ordering and time gaps; the camera clock has no time zone, so it is read as UTC."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso + "+00:00").timestamp()
    except ValueError:
        return None


def preview_ranges(record: dict[str, Any]) -> dict[str, list[int]]:
    """Byte ranges of the embedded JPEGs: {"full": [start, length], "other": [...], "small": [...]}."""
    ranges = {}
    for key, tag in PREVIEW_TAGS.items():
        start = length = None
        for name, value in record.items():
            if name.endswith(f":{tag}Start"):
                start = value
            elif name.endswith(f":{tag}Length"):
                length = value
        if isinstance(start, int) and isinstance(length, int) and start > 0 and length > 0:
            ranges[key] = [start, length]
    return ranges


def normalize(record: dict[str, Any]) -> dict[str, Any]:
    """The fields later steps use, from one exiftool record (numeric values plus `rie:Printed`)."""
    flat = _flat(record)
    printed = record.get("rie:Printed", {})
    flash = flat.get("Flash")
    lens_id = _group(record, "Composite", "LensID")
    af_primary_index = _group(record, "Nikon", "PrimaryAFPoint")
    primary = AF_POINTS_51.get(af_primary_index) if isinstance(af_primary_index, int) else None
    shooting_mode = _group(record, "Nikon", "ShootingMode")
    crop = _group(record, "Nikon", "CropHiSpeed")
    crop_mode = None
    if isinstance(crop, str) and crop.split():
        crop_mode = "DX" if crop.split()[0] == "0" else "1.3x"
    wb_levels = _group(record, "Nikon", "WB_RBLevels")
    wb_rb = None
    if isinstance(wb_levels, str) and len(wb_levels.split()) >= 2:
        wb_rb = [float(x) for x in wb_levels.split()[:2]]
    raw_size = flat.get("ImageSize")
    meta: dict[str, Any] = {
        "make": flat.get("Make"),
        "model": flat.get("Model"),
        "lens": printed.get("LensID") if isinstance(printed.get("LensID"), str) else None,
        "lens_id": lens_id if isinstance(lens_id, str) else None,
        "focal_length": _number(_group(record, "ExifIFD", "FocalLength") or flat.get("FocalLength")),
        "fnumber": _number(_group(record, "ExifIFD", "FNumber") or flat.get("FNumber")),
        "exposure_time": _number(_group(record, "ExifIFD", "ExposureTime") or flat.get("ExposureTime")),
        "iso": _number(_group(record, "ExifIFD", "ISO") or flat.get("ISO")),
        "exposure_comp": _number(_group(record, "ExifIFD", "ExposureCompensation")),
        "flash_fired": bool(int(flash) & 1) if isinstance(flash, int) else None,
        "adl": _group(record, "Nikon", "ActiveD-Lighting"),
        "vr": _group(record, "Nikon", "VibrationReduction"),
        "focus_mode": printed.get("FocusMode"),
        "af_area_mode": printed.get("AFAreaMode"),
        "af_primary": primary,
        "af_points": af_points_from_mask(_group(record, "Nikon", "AFPointsUsed")),
        "focus_distance": _number(_group(record, "Nikon", "FocusDistance")),
        "white_balance": str(_group(record, "Nikon", "WhiteBalance") or "").strip() or None,
        "wb_rb": wb_rb,
        "picture_control": _group(record, "Nikon", "PictureControlName"),
        "shooting_mode": printed.get("ShootingMode"),
        "continuous": bool(int(shooting_mode) & 1) if isinstance(shooting_mode, int) else None,
        "light_value": _number(_group(record, "Composite", "LightValue")),
        "crop_mode": crop_mode,
        "raw_size": [int(x) for x in raw_size.split()]
        if isinstance(raw_size, str) and raw_size.count(" ") == 1
        else None,
        "error": flat.get("Error"),
    }
    return {k: v for k, v in meta.items() if v is not None and v != []}


def orientation(record: dict[str, Any]) -> int:
    value = _group(record, "IFD0", "Orientation")
    if value is None:
        value = _flat(record).get("Orientation")
    return value if isinstance(value, int) and 1 <= value <= 8 else 1


def shutter_count(record: dict[str, Any]) -> int | None:
    value = _group(record, "Nikon", "ShutterCount")
    return value if isinstance(value, int) else None


# --- writing metadata into an export, in the cache ------------------------------------------------------------------


def copy_metadata(
    raw: Path,
    jpeg: Path,
    cache: Path,
    *,
    lens: str | None = None,
    artist: str = "",
    copyright: str = "",
    executable: str = "exiftool",
) -> None:
    """Copy a raw file's metadata into a rendered JPEG that lives in the app's cache (plan §6.1, ADR 0001).

    RawTherapee keeps only part of the EXIF (no sub-seconds, no lens name) and bakes the orientation into the
    pixels, so the EXIF goes over from the raw, the orientation is set to normal, and the lens name, artist and
    copyright are written. This is the app's only metadata write, and it refuses any file outside the cache: the
    JPEG reaches the photo folder afterwards through `rie.fileops`.
    """
    path = shutil.which(executable)
    if path is None:
        raise ExifError(f"{executable} not found on PATH")
    jpeg, cache = jpeg.resolve(), cache.resolve()
    if not jpeg.is_relative_to(cache) or jpeg.suffix.lower() not in (".jpg", ".jpeg"):
        raise ExifError(f"refusing to write metadata into {jpeg}: only rendered JPEGs in the cache")
    values = {"LensModel": lens or "", "Artist": artist, "Copyright": copyright}
    if any("\n" in v or "\r" in v for v in values.values()):
        raise ExifError("metadata values cannot contain line breaks")
    args = [
        path,
        "-q",
        "-q",
        "-overwrite_original",
        "-charset",
        "filename=utf8",
        "-tagsFromFile",
        os.path.abspath(raw),
        "-exif:all",
        "--exif:Orientation",
        "-xmp:all",
        "-Orientation#=1",
    ]
    args += [f"-{tag}={value}" for tag, value in values.items() if value]
    proc = subprocess.run([*args, str(jpeg)], capture_output=True, text=True, timeout=60, check=False)
    if proc.returncode != 0:
        raise ExifError(f"exiftool could not copy the metadata: {(proc.stderr or proc.stdout).strip()[:200]}")
