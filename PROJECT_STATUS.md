# Project status

Last updated: 2026-11-18

The state of the code. Notes about one installation, such as its photo drive and sample set, belong in the Git-ignored `local/` folder.

## Built (Phase 1, in progress)

- **Catalog** (`rie.catalog`): SQLite in WAL mode at `<paths.data>/catalog.db`, with plain SQL migrations in `src/rie/catalog/migrations/` tracked by `PRAGMA user_version`. Migration 0001 holds the Phase 1 tables: folders, images, sidecars, recipes, analyses, faces, groups, suggestions, decisions, the decision log, culling outcomes, the trust dial, culling models, the trash log and jobs. `rie open` backs the catalog up into `<paths.data>/backups/` and keeps the newest ten copies.
- **`rie open FOLDER`** scans a shoot folder into the catalog, reading only:
  - It lists the folder one level deep, skipping dot files and `._*` AppleDouble files. A camera JPEG with the same name as a NEF is the NEF's pair; a JPEG on its own is a photo too.
  - New and changed files are hashed: SHA-256 of the size and the first 64 KiB (the EXIF block, maker notes and thumbnail). A file whose hash matches a photo whose old file is gone is re-linked, keeping its rows; a whole moved or renamed folder is recognised the same way. Files that disappear are marked missing.
  - Metadata comes from one `exiftool -stay_open` process per folder, in chunks of 25: `-json -n -G1 -a` for everything, stored whole, plus a printed pass for the lens name, AF point names and shooting mode. The wrapper refuses write options and passes absolute paths only.
  - Re-opening an unchanged folder reads no file.
  - It then caches upright copies of the camera's embedded JPEGs under `<cache>/previews/<hash>/`: a 512 px grid thumbnail from the 1620 x 1080 `OtherImage` and a 2048 px loupe copy from the 6000 x 4000 `JpgFromRaw`, both read with one sequential read per file and made six at a time with libvips (`[previews]` sets the sizes, quality and workers). It also measures each frame's look (an 8 x 8 CIELAB layout, a difference hash, mean luminance and clipping) for grouping. A photo whose copies exist is skipped. On the reference machine, 29 sample NEFs from the internal disk took 0.8 s to scan and 1.3 s for previews.
  - 100% zoom serves the `JpgFromRaw` bytes as they are, with an EXIF orientation tag inserted so the browser turns portrait shots upright without re-encoding.
- **Analysis** (`rie.analysis`, ADR 0008), run by `rie open` after the previews, in `analysis.workers` processes (3 by default), each photo read once:
  - a blur map from the raw green channel (32 × 32 tiles of the half-resolution green, fine gradient energy against the same after a 2-pixel blur, minus a noise floor measured in flat tiles, with clipped highlights left out), plus the direction of any directional blur;
  - faces, eyes and smiles per ADR 0003: YuNet at 1600 px, a tiled pass for group shots kept only with landmark confirmation and containment suppression, a 640 px pass for close-ups, and the MediaPipe landmarker per face for blink, smile, head pose and iris positions;
  - a focus verdict (sharp, soft, missed, motion, shake or unknown) at the eyes of the focused face, else the AF point the camera used (the 51-point layout is estimated), else the sharpest detail, with the background's sharpness, the shutter against the handheld limit, and per-face sharpness;
  - the photo's SHA-256 content hash.
  On the reference machine this takes 0.8–1.3 s per photo per process; with three processes the 29 samples took 16.8 s. The step checks the drive before each photo and pauses when it is gone.
- **Grouping and burst ranking:** scenes and bursts from capture time, lens settings, light and the look descriptor; near-duplicates inside bursts; faces followed through each burst by position; every frame scored and ranked with a one-line reason ("#4 is better: 1 of 3 blinking, 62% as sharp as the sharpest frame"). The thresholds are provisional (ADR 0008).
- **Safe file operations** (`rie.fileops`, ADR 0007): the only code that changes a photo folder. It moves a file to its volume's freedesktop.org trash (`.Trash-<uid>` at the drive's top) after checking the trash is on the same device, records where the file went, restores it without overwriting anything, creates new files and updates `.pp3` sidecars through temporary names (a sidecar the app did not create only after a backup), and never deletes anything except a restored file's `.trashinfo`. A static test fails on any delete, rename or write outside the module that is not a reviewed cache or data-folder write, and an audit-hook test checks real runs. `rie check` shows the drive's trash size and free space and looks for anything that empties the trash automatically. `rie render -o` refuses outputs inside the photos folder.
- **Photo drive wired in** (`rie.locations`): a folder under the drive's mount point (or the photos folder) is stored as the drive's UUID plus a relative path. Before it is read, the drive's state is checked from the device and the mount table; an unplugged drive is reported without touching its mount point.

## Built (Phase 0)

- **Scaffolding:**
  - a uv project on Python 3.13 (ADR 0004);
  - ruff, pyright (standard mode) and pytest;
  - the `rie` command;
  - `config.example.toml`, `AGENTS.md` and `CLAUDE.md`;
  - a `.gitignore` that keeps models, samples, photos, renders and installation settings out of Git.
- **Settings:** `config.toml` (optional, ignored by Git) names the photo folder and drive UUID, the cache and data folders, RawTherapee's command, settings folder, base profiles and timeout, the preview size and JPEG options, and whether Claude's passes run. Unknown keys and bad values are errors.
- **`rie render NEF --recipe RECIPE.json`:**
  - It validates the recipe (schema `rie.recipe/1`) and compiles it into a per-image partial `.pp3`.
  - It stacks that profile on the versioned base profile `profiles/rawtherapee/d7100-base.pp3` (`d7100-base@1`) and renders a JPEG (quality 95, 4:4:4) with rawtherapee-cli.
  - RawTherapee runs with the app's own settings folder, and the JPEG goes through a temporary name.
  - Output goes to `<cache>/renders/<name>.jpg` unless `-o` names another file. `--preview` or `--size PX` resizes by the long edge.
  - `--profile-out` saves the per-image profile, which also works in a plain `rawtherapee-cli -p base -p image` call. `--dry-run` prints the profiles and the command.
  - It warns when the recipe was made for another RawTherapee version.
  - The parameters it supports are the ones confirmed in ADR 0001: custom or as-shot white balance, exposure compensation, highlight compression, shadows, vibrance, saturation, rotation, crop, lensfun corrections, AMaZE or RCD demosaicing, capture sharpening, and luma or chroma noise reduction.
  - Unknown parameters are errors, because RawTherapee would ignore them silently.
- **`rie check`:** rawtherapee-cli's version and settings folder, the D7100 camera profile, the base profile, exiftool, Claude Code, and the photo drive's state.
- **Drive detection** (`rie.drive`, ADR 0005): connected, not mounted, unplugged, wrong device or unconfigured. It is decided from `/dev/disk/by-uuid` and `/proc/self/mountinfo` without touching the photo folders. `rie check` uses it; no job does yet.
- **Phase 0 spikes**, each with an ADR in `docs/adr/` and a script in `scripts/spikes/`:
  - RawTherapee keys, timings and demosaicing;
  - NEF metadata;
  - faces, eyes and smiles;
  - Python version;
  - drive detection;
  - Claude Code headless with a throwaway MCP server.
- **Models:** `models/manifest.toml` lists YuNet and the MediaPipe Face Landmarker with their sources, hashes and licenses; `scripts/fetch_models.py` downloads and checks them.

## Not built yet

- The Phase 0 items that need the owner: the golden set (about 200 NEFs with labels) and the calibration-kit shots (gray card in daylight, shade, flash and home lighting).
- The rest of Phase 1 (on this branch, in progress):
  - grouping, the three piles, the culling UI and the trash at Done culling;
  - the edit agents, sidecars and export;
  - the real MCP server and the Claude runner;
  - taste learning and the look lab.

## Next steps

1. **Owner decisions** that Phase 0 surfaced are settled in plan v0.4 (§12):
   - The 4,205 existing RawTherapee sidecars are kept as the owner's locked edits, and each is backed up once before the app first adds to it (ADR 0002; rows 5 and 14).
   - Culling shows only the embedded JPEGs. Edited previews use a fast profile without capture sharpening or raw CA correction, rendered in the background (ADR 0001; row 17).
2. **Phase 1 starts** with the catalog and `rie open <folder>`:
   - the folder scan, skipping `._*` AppleDouble files;
   - RAW+JPEG pairing and content hashes;
   - one batched exiftool call per folder, with `-n` plus a printed `LensID`;
   - embedded previews read by byte range: `OtherImage` (1620 × 1080) for the grid, `JpgFromRaw` (6000 × 4000) for the loupe;
   - drive detection wired into the scan and the jobs;
   - the file-operations module of plan §3.5, before any code writes to the drive.
3. The rest of Phase 1, on this branch.

## Known limits

- The base profile has no tone curve, so renders look flatter than the camera's JPEG. The gentle neutral curve is Phase 2 work (§4.7).
- Render times on the reference machine:

  | Render | Time |
  |---|---|
  | 2048 px preview, base profile | 3.0–3.2 s |
  | 2048 px preview, with noise reduction | about 5 s |
  | full size | 3.7–4.0 s (5.7 s with noise reduction) |

  A render costs about 3 s however small the output, because RawTherapee demosaics and sharpens the whole frame.
- lensfun's entry for the Tokina 100 mm macro has no vignetting data, so its vignetting is not corrected.
- Renders keep only part of the EXIF: the sub-seconds and the lens name are lost, and the orientation is baked into the pixels.
- A recipe's `image` hash is not checked yet, and a different RawTherapee version only prints a warning.
- Automatic chroma noise reduction (`nr_chroma: "auto"`) writes keys that change the render, but it is not verified that they adapt to the image rather than acting like a fixed value.
- Drive detection has not been tried by unplugging the drive; unit tests cover each mount-table state.
- MediaPipe 1.x runs out of memory creating the Face Landmarker here, so the spikes pin 0.10.35.
