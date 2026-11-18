# ADR 0002: What D7100 NEFs carry, and what the shoot folders hold

- **Status:** accepted. The owner's decision on the existing sidecars is in plan v0.4 (§12 rows 5 and 14): they are kept as locked edits, and each is backed up once before the app first adds to it.
- **Date:** 2026-11-17 (Phase 0, spike b)
- **Script:** `scripts/spikes/nef_metadata.py`

## Context

The plan (§4.1) relies on a full-size embedded JPEG for instant culling, on AF-point tags for focus checks, on sub-second timestamps for burst grouping, and on the focus distance for depth-of-field estimates. The spike read 895 files from five recent shoot folders on the photo drive (read-only: exiftool without write options, rawpy) and 29 sample copies.

## Findings

### Embedded images

| Image | Where | Size | Found |
|---|---|---|---|
| `JpgFromRaw` | SubIFD | 6000 × 4000 (full size, about 1.4 MB) | 29 of 29 samples |
| `OtherImage` | SubIFD2 | 1620 × 1080 | 29 of 29 |
| `PreviewImage` | PreviewIFD | 570 × 375 | 29 of 29 |

- The full-size JPEG carries its own EXIF orientation, which OpenCV and libvips apply by default. Code that also applies the NEF's orientation must decode with the JPEG's orientation ignored, or portrait shots end up sideways.
- Extraction speed: `exiftool -b -JpgFromRaw` per file took 0.165 s, mostly process start. After one batched exiftool call for the offsets (`JpgFromRawStart`, `JpgFromRawLength`), reading the bytes directly took 0.3 ms per file from the page cache; `rawpy.extract_thumb()` took 3 ms and returns the 6000 × 4000 JPEG.
- A half-size linear rawpy decode for analysis took 0.52 s.

### Metadata pass

`exiftool -json -n -G1 -a` over a whole folder from the USB drive took 24–51 ms per file (median 39 ms): 19.3 s for 376 files, 10.6 s for 233.

### Tags (894 NEFs)

| Tag | Filled | Notes |
|---|---|---|
| `SubSecTimeOriginal` | 894 | always a multiple of 0.1 s: tenths only |
| `ShutterCount` | 894 | |
| `LensID` | 894 | with `-n`, the 8-byte composite code (`9F 58 44 44 14 14 A1 06`); the lens name needs print conversion |
| `FocusMode` | 894 | AF-S 759, MANUAL 135 (the Tokina) |
| `AFAreaMode` | 894 | Single Area 877, Dynamic Area 17 |
| `PrimaryAFPoint` | 894 | |
| `AFPointsUsed` | 894 | a 7-byte bitmask of the 51 points |
| `AFPointsInFocus` | **0** | never filled by the D7100 |
| `AFPointsSelected` | **0** | never filled |
| `FocusDistance` | 894 | 35 mm: coarse logarithmic steps (0.71, 0.79, 0.89, 1.0, 1.19, 1.41, 1.78, 2.51, 3.76, 5.01 m). Tokina: 0.013–0.024 m, meaningless. |
| `ActiveD-Lighting`, `VibrationReduction`, `WhiteBalance`, `PictureControlName`, `NEFCompression`, `ImageSize` | 894 | here always Off, n/a, AUTO1, STANDARD, lossless, 6036 × 4020 (no 1.3× crop shots) |

One file in one folder had no metadata at all: a 4 KB macOS AppleDouble file named `._DSC_xxxx.NEF`.

### Existing RawTherapee sidecars

- The drive holds **4,205 `.pp3` files** within three folder levels, in most shoot folders, and 498 of them were written since 2026-09-01. The most recent shoot folders hold 30–370 each.
- A random sample of 60 sidecars from 2026 shows real edits:
  - a rating (`[General] Rank` 1–5) in 28;
  - changed exposure compensation in about 41;
  - custom white balance in 23;
  - a crop in 16, and a rotation in 10.
- Version and colour: all 60 were written by RawTherapee 5.8, and 41 of them use the camera matrix (`InputProfile=(camera)`) rather than the DCP.
- RawTherapee's own profile folder also holds about 15 custom processing profiles.

## Decision

- Phase 1 uses `OtherImage` (1620 × 1080) for the grid and thumbnails and `JpgFromRaw` (6000 × 4000) for the loupe and 100% zoom. Their offsets come from the one batched exiftool call per folder, and the bytes are read directly (or with `rawpy.extract_thumb()`).
- The metadata pass reads numeric values with `-n` and asks for `LensID` with print conversion, because the code alone does not name the lens.
- Burst grouping cannot order frames by time alone, since sub-seconds come in tenths and frames at 6 fps are 0.17 s apart. Within a burst, frames are ordered by `ShutterCount`.
- The focus inspector uses `PrimaryAFPoint` and `AFPointsUsed`; `AFPointsInFocus` does not exist on this camera. `FocusDistance` is only a hint for the 35 mm and is ignored for the Tokina, so depth of field (§4.3) cannot use it for macro shots.
- The folder scan skips `._*` AppleDouble files and other dot files.

## Consequences for the plan

- **Decision 5 and §3.3 do not match the drive.** The plan says there are no existing edits and "no older sidecars to bring in", but most shoot folders already hold RawTherapee sidecars with ratings, exposure, white balance, crops and rotations. Because the app never overwrites a sidecar it did not write, the managed-sidecar design (decision 14) could not write sidecars for most photos in those folders. The owner needs to choose before Phase 2. Some options:
  - Import existing sidecars as the owner's locked parameters and ratings, then take them over.
  - Keep the app's profiles in its cache for photos that already have a sidecar, and write sidecars only where none exists.
  - Back up and replace the existing sidecars.
- The existing sidecars are also a ready-made source of reference edits and ratings for the golden set (§9), which planned to ask for about 50 new reference edits.
- §4.1 lists `AFPointsInFocus`, which the D7100 never fills.
