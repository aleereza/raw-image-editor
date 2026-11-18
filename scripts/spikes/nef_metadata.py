"""Spike b: which metadata D7100 NEFs carry, and how fast the embedded JPEG comes out.

Usage: uv run --group spikes python scripts/spikes/nef_metadata.py SAMPLES_DIR [FOLDER ...]

Reads NEFs only (exiftool without write options, rawpy). SAMPLES_DIR is used for the embedded-JPEG checks and
timings; each FOLDER is read in one `exiftool -json -n -G1` call to count how often each tag is filled. Folder
names are not printed. Results are printed as JSON lines.
"""

from __future__ import annotations

import collections
import io
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

import rawpy
from PIL import Image

TAGS = [
    "SubSecTimeOriginal",
    "SubSecDateTimeOriginal",
    "ShutterCount",
    "LensID",
    "FocalLength",
    "FNumber",
    "ExposureTime",
    "ISO",
    "ExposureCompensation",
    "Flash",
    "ActiveD-Lighting",
    "VibrationReduction",
    "FocusMode",
    "AFAreaMode",
    "PrimaryAFPoint",
    "AFPointsUsed",
    "AFPointsInFocus",
    "AFPointsSelected",
    "FocusDistance",
    "FocusPosition",
    "WhiteBalance",
    "PictureControlName",
    "Orientation",
    "ImageSize",
    "CropHiSpeed",
    "NEFCompression",
    "BitsPerSample",
    "PhaseDetectAF",
    "ContrastDetectAF",
    "AFFineTune",
]


def exiftool_json(paths: list[Path], *args: str) -> list[dict]:
    proc = subprocess.run(["exiftool", "-json", *args, *map(str, paths)], capture_output=True, text=True, check=False)
    return json.loads(proc.stdout or "[]")


def jpeg_size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as im:
        return im.size


def embedded_jpegs(samples: list[Path]) -> None:
    rows = exiftool_json(
        samples,
        "-n",
        "-G1",
        "-a",
        "-JpgFromRawStart",
        "-JpgFromRawLength",
        "-PreviewImageStart",
        "-PreviewImageLength",
        "-OtherImageStart",
        "-OtherImageLength",
        "-ImageWidth",
        "-ImageHeight",
    )
    sizes: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for row in rows:
        path = Path(row["SourceFile"])
        blob = path.read_bytes()
        for name in ("JpgFromRaw", "PreviewImage", "OtherImage"):
            start = next((v for k, v in row.items() if k.endswith(f":{name}Start")), None)
            length = next((v for k, v in row.items() if k.endswith(f":{name}Length")), None)
            if start is None or length is None:
                sizes[name]["missing"] += 1
                continue
            data = blob[start : start + length]
            sizes[name]["x".join(map(str, jpeg_size(data)))] += 1
    print(json.dumps({"embedded_jpeg_sizes": {k: dict(v) for k, v in sizes.items()}, "files": len(rows)}))

    # Timings: exiftool -b per file, exiftool batch to files, a byte-range read, and rawpy's largest thumbnail.
    sample = samples[: min(10, len(samples))]
    t = time.perf_counter()
    for path in sample:
        subprocess.run(["exiftool", "-b", "-JpgFromRaw", str(path)], capture_output=True, check=True)
    per_call = (time.perf_counter() - t) / len(sample)
    offsets = exiftool_json(sample, "-n", "-JpgFromRawStart", "-JpgFromRawLength")
    t = time.perf_counter()
    for row in offsets:
        with open(row["SourceFile"], "rb") as fh:
            fh.seek(row["JpgFromRawStart"])
            data = fh.read(row["JpgFromRawLength"])
        assert data[:2] == b"\xff\xd8"
    byte_range = (time.perf_counter() - t) / len(sample)
    t = time.perf_counter()
    kinds = collections.Counter()
    for path in sample:
        with rawpy.imread(str(path)) as raw:
            thumb = raw.extract_thumb()
            kinds[f"{thumb.format.name} {'x'.join(map(str, jpeg_size(bytes(thumb.data))))}"] += 1
    via_rawpy = (time.perf_counter() - t) / len(sample)
    t = time.perf_counter()
    for path in sample:
        with rawpy.imread(str(path)) as raw:
            raw.postprocess(half_size=True, use_camera_wb=True, output_bps=16, no_auto_bright=True, gamma=(1, 1))
    half_decode = (time.perf_counter() - t) / len(sample)
    print(
        json.dumps(
            {
                "jpg_from_raw_seconds_per_file": {
                    "exiftool_b_one_process_each": round(per_call, 3),
                    "byte_range_after_one_exiftool_call": round(byte_range, 4),
                    "rawpy_extract_thumb": round(via_rawpy, 3),
                },
                "rawpy_thumb_kinds": dict(kinds),
                "rawpy_half_size_linear_decode_seconds": round(half_decode, 2),
            }
        )
    )


def tag_fill(folders: list[Path]) -> None:
    counts: collections.Counter = collections.Counter()
    values: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    by_lens: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    total = 0
    timings = []
    for folder in folders:
        nefs = sorted(p for p in folder.iterdir() if p.suffix.lower() == ".nef")
        t = time.perf_counter()
        rows = exiftool_json(nefs, "-n", "-G1", "-a")  # the plan's per-folder call: every tag
        timings.append({"files": len(nefs), "seconds": round(time.perf_counter() - t, 1)})
        for row in rows:
            flat = {k.split(":", 1)[-1]: v for k, v in row.items()}
            total += 1
            lens = str(flat.get("LensIDNumber", "?"))
            for tag in TAGS:
                if tag in flat and flat[tag] not in ("", None):
                    counts[tag] += 1
                    if tag in (
                        "AFAreaMode",
                        "PrimaryAFPoint",
                        "AFPointsUsed",
                        "AFPointsInFocus",
                        "FocusMode",
                        "ActiveD-Lighting",
                        "VibrationReduction",
                        "NEFCompression",
                        "ImageSize",
                        "PictureControlName",
                        "WhiteBalance",
                        "AFPointsSelected",
                    ):
                        values[tag][str(flat[tag])] += 1
            sub = flat.get("SubSecTimeOriginal")
            if sub is not None:
                values["SubSecTimeOriginal last digit"][str(sub)[-1:]] += 1
            fd = flat.get("FocusDistance")
            by_lens[lens][f"FocusDistance {f'{fd:.3g}' if isinstance(fd, int | float) else fd}"] += 1
    print(json.dumps({"exiftool_full_dump_per_folder": timings, "files": total}))
    print(json.dumps({"tag_fill": {tag: f"{counts[tag]}/{total}" for tag in TAGS}}))
    print(json.dumps({"tag_values": {k: dict(v.most_common(12)) for k, v in values.items()}}))
    print(json.dumps({"focus_distance_by_lens_id": {k: dict(v.most_common(10)) for k, v in by_lens.items()}}))
    print(json.dumps({"median_seconds_per_file": statistics.median(t["seconds"] / t["files"] for t in timings)}))


def main() -> None:
    samples_dir = Path(sys.argv[1])
    samples = sorted(samples_dir.glob("*.NEF"))
    embedded_jpegs(samples)
    tag_fill([Path(f) for f in sys.argv[2:]] or [samples_dir])


if __name__ == "__main__":
    main()
