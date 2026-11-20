# Project status

Last updated: 2026-11-20

The state of the code. Notes about one installation, such as its photo drive and sample set, belong in the Git-ignored `local/` folder.

## Built (Phase 1)

Phase 1 ("fast, partly automatic culling", plan §10) is built on the branch `claude/phase-1-culling` and waits for the owner's review and a first real shoot. ADRs 0007–0009 record its decisions.

### Commands

- `rie open FOLDER [--no-analysis] [--workers N]` scans a shoot folder, caches previews, imports RawTherapee sidecars, analyses the photos, groups them and sorts them into the three piles. It only reads the folder.
- `rie serve [--open]` starts the culling UI at http://localhost:7317; folders can be opened from there too.
- `rie restore FOLDER [--photo NAME]` moves a folder's trashed rejects, or one photo's files, back from the trash.
- `rie check` also shows the drive's trash size and free space, and anything that empties the trash automatically.

### Opening a folder

- **Catalog** (`rie.catalog`): SQLite in WAL mode at `<paths.data>/catalog.db`, with plain SQL migrations in `src/rie/catalog/migrations/` tracked by `PRAGMA user_version`. Migration 0001 holds the Phase 1 tables: folders, images, sidecars, recipes, analyses, faces, groups, suggestions, decisions, the decision log, culling outcomes, the trust dial, culling models, the trash log and jobs. Each `rie open`, each job in `rie serve` and each Done culling backs the catalog up into `<paths.data>/backups/`, keeping the newest ten copies.
- **Scan** (`rie.scan`): the folder is listed one level deep, skipping dot files and `._*` AppleDouble files. A camera JPEG with the same name as a NEF is the NEF's pair; a JPEG on its own is a photo too.
  - New and changed files are hashed: SHA-256 of the size and the first 64 KiB (EXIF, maker notes, thumbnail). A file whose hash matches a photo whose old file is gone is re-linked with its decisions and analysis; a moved or renamed folder is recognised the same way. Files that disappear are marked missing. Re-opening an unchanged folder reads no file.
  - Metadata comes from one `exiftool -stay_open` process per folder, in chunks of 25: `-json -n -G1 -a` for everything, stored whole, plus a printed pass for the lens name, AF point names and shooting mode. The wrapper refuses write options and passes absolute paths only.
- **Photo drive** (`rie.locations`): a folder under the drive's mount point is stored as the drive's UUID plus a relative path. Its state is checked from the device and the mount table before anything reads it; an unplugged drive is reported without touching the mount point. Analysis pauses when the drive goes away and resumes when the folder is opened again; cached previews keep culling possible.
- **Previews** (`rie.scan.previews`): upright copies of the camera's embedded JPEGs, cached under `<cache>/previews/<hash>/`: a 512 px grid thumbnail from the 1620 × 1080 `OtherImage` and a 2048 px loupe copy from the 6000 × 4000 `JpgFromRaw`, read with one sequential read per file and made six at a time with libvips (`[previews]` in `config.toml`). Each frame's look (an 8 × 8 CIELAB layout, a difference hash, mean luminance and clipping) is measured for grouping. 100% zoom serves the `JpgFromRaw` bytes as they are, with an EXIF orientation tag inserted so the browser turns portrait shots upright without re-encoding.
- The folder is grouped and sorted right after the scan, again after the previews, and again after the analysis, so the grid can be browsed within seconds while the rest runs.

### Analysis (ADR 0008)

Run in `analysis.workers` processes (3 by default), each photo read once:

- a blur map from the raw green channel: 32 × 32 tiles of the half-resolution green, fine gradient energy against the same after a 2-pixel blur, minus a noise floor measured in flat tiles, with clipped highlights left out; plus the direction of any directional blur;
- faces, eyes and smiles per ADR 0003: YuNet at 1600 px, a tiled pass for group shots kept only with landmark confirmation and containment suppression, a 640 px pass for close-ups, and the MediaPipe landmarker per face for blink, smile, head pose and iris positions;
- a focus verdict (sharp, soft, missed, motion, shake or unknown) at the eyes of the focused face, else the AF point the camera used, else the sharpest detail, with the background's sharpness, the shutter against the handheld limit, and per-face sharpness;
- the photo's SHA-256 content hash;
- then, per folder: scenes and bursts from capture time, lens settings, light and the look descriptor; near-duplicates inside bursts; faces followed through each burst by position; every frame ranked with a one-line reason ("#4 is better: 1 of 3 blinking, 62% as sharp as the sharpest frame").

### RawTherapee sidecars

`rie open` reads each `.pp3` that is new or changed since the app last read it (`rie.sidecars`). The app only reads sidecars, and it ignores their ratings (ADR 0010).

- Values the recipe can express that differ from RawTherapee's neutral defaults become the owner's locked parameters in the photo's recipe, versioned in `recipe_history`: exposure compensation, highlight compression, shadows, saturation, vibrance, custom white balance, rotation and crop. Other changed settings (auto levels, sharpening, coarse rotation and so on) are listed and kept as they are.

### Culling (ADR 0009)

- The rule-based sort (`rules@2`) gives every photo a suggestion, a kind and a reason. Rejects are technical failures (an almost black or blown frame, a clearly soft, shaken or blurred subject, a clearly missed focus), near-duplicates of a better frame, closed eyes where a sibling has them open, other non-best frames of a burst (expression and moment), and a face cut by the frame edge (composition). Keeps are the best of a burst and good single shots. Ratings, the app's own or RawTherapee's, play no part (ADR 0010).
- The three piles follow the trust dial. A photo goes to auto-reject or auto-keep only when its kind is automatic and the probability of a reject is at least 0.8 or at most 0.2. Technical failures and duplicates start automatic; everything else starts on suggest. A kind is proposed for automatic once it matched the owner's final call 95 % of the time over the last three folders (20 photos or more); only the owner switches it.
- Decisions, burst picks (picking a frame rejects the others; Shift keeps more) and the optional quick reasons go to `decision_log` with the pile and suggestion the photo had at that moment.
- Done culling first shows how many rejects there are and how much space they take. It then moves each reject's raw, paired JPEG and sidecars to the drive's trash through `rie.fileops`, records where every file went, keeps undecided photos, writes one outcome per photo, backs up the catalog and retrains the personal model. The UI and `rie restore` bring files back.
- The personal model is scikit-learn's histogram gradient boosting on 32 features. It trains after Done culling once 60 photos (10 keeps and 10 rejects) are culled, and is cross-validated by burst. It becomes active only if it agrees with the owner on the latest folder at least as well as the app's suggestions and the previous version; every version is kept for rollback. A version trained before ADR 0010, which also used the rating, is set aside until the next Done culling trains a new one, and can't be rolled back to.
- Per folder, the app reports the share decided automatically, how often the owner changed the app's sort, the minutes spent, and agreement per kind.

### Culling UI (`rie serve`, `src/rie/api/`, `web/`)

FastAPI serves the API and a Svelte 5 app (built once with `npm run build` in `web/`). The top bar names the app, Raw Image Editor, which leads back to the folders, and then the page: Folders or Culling.

- **Folders:** the subfolders of the photos folder (newest name first), each opened with one click, with a path field for anything else; opening runs as a background job with progress. Also the folders opened before, the photo drive's state, the trash's size and free space, warnings about anything that empties the trash, and a button that restores a folder's rejects.
- **The server:** `rie serve --open` opens the browser once the server answers. `Ctrl+C` stops it within a second even while a page is open, and the page then shows a banner until the server is back. The analysis workers' native log lines (OpenCV, MediaPipe) go to `<cache>/logs/analysis.log`, not the terminal.
- **Cull:**
  - the three pile tabs with counts;
  - a grid grouped by scene, with bursts collapsed to their best frame and a count; a pile opens on the frame its first tile shows, and a stack shows whichever of its frames is selected;
  - a loupe that shows the cached 2048 px copy and prefetches and decodes the five photos on either side;
  - `Z` for 100% on the eyes or AF point (the full-size JPEG is prefetched after 0.3 s on a photo), moved by dragging or scrolling (a mouse wheel or two fingers on a touchpad) and never past the image's edges, and `F` for the focus heatmap with faces, eyes and the AF point;
  - an action panel on the right with a button for every key: Keep and Reject (in burst compare: pick this frame, or keep it too), clearing a decision, burst compare and the app's pick, the loupe, the focus map, zoom and moving around; it can be hidden, and the browser remembers that;
  - badges for decision, focus verdict, closed eyes, clipping and the burst's best, with the app's suggestion and reason under each photo;
  - the optional reason picker when the owner overrules the app, and Done culling with a preview of what moves to the trash.
- **Burst compare (`C`):** the burst's frames side by side, with one face strip per person (100% crops from the full-size JPEG, with eyes, smile and sharpness badges). `P` picks a frame and rejects the rest; `Shift+P` keeps another one too.
- **Keyboard and guide:** the map of plan §6; `A` accepts the app's pick for a burst; `D` shows flip and zoom timings. `?` opens a guide to the piles, every sign on a photo, the bottom bar, the focus map, burst compare and Done culling, next to the list of keys.

### Safe file operations (ADR 0007)

- `rie.fileops` is the only code that changes a photo folder:
  - It moves a file to its volume's freedesktop.org trash (`.Trash-<uid>` at the drive's top), after checking that the trash is on the same device, and records where the file went.
  - It restores a file without overwriting anything.
  - It creates new files and updates `.pp3` sidecars through temporary names; a sidecar the app did not create is updated only after a backup.
  - It never deletes anything except a restored file's `.trashinfo`.
- A static test fails on any delete, rename or write outside that module unless it is a reviewed cache or data-folder write. An audit-hook test watches real runs (scan, sidecar import, analysis, Done culling, restore) on copies.
- `rie render -o` refuses outputs inside the photos folder.

## Measured against the targets

Measured on the reference machine (Ryzen 7 3700X, 32 GB) with the 29 sample NEFs on the internal disk, not the USB drive; the UI in headless Chromium at 1920 × 1080.

| Target (plan §9, §10) | Measured | Notes |
|---|---|---|
| Next photo ≤ 50 ms | median 18 ms, 90th percentile 32 ms (8 ms when flipping fast) | the next five photos are prefetched and decoded |
| 100% zoom ≤ 150 ms | 7 ms after 0.3 s on a photo; 123–227 ms (median 142 ms) when `Z` follows a flip | the second case fetches and decodes 24 megapixels; on the USB drive add the read of 1.4–2.5 MB |
| Grid browsable within seconds; 500 photos in ≤ 30 s | first thumbnail on screen 1.4 s, all visible thumbnails 2.6 s after Open (29 photos) | scan 0.8 s and previews 1.3 s for 29 photos: about 50 ms per photo, so about 25 s for 500 before USB reads |
| Analysis ≤ 0.5 s per photo | 0.58 s per photo with 3 workers (16.8 s for 29, including model loading) | 0.8–1.3 s per photo per process; a USB drive adds about 0.25 s per 30 MB read, overlapped |
| ≥ 30 % decided automatically | 62 % of the samples landed in automatic piles | says nothing about agreement; the samples are not a shoot |
| Owner changes < 5 % of those | not measured | needs a real shoot |
| Focus recall ≥ 95 % at ≥ 80 % precision | not measured | needs the golden set |
| Originals untouched | tests: audit hook and file snapshots on copies | the owner's real run confirms it |

## Built (Phase 0)

- **Scaffolding:**
  - a uv project on Python 3.13 (ADR 0004);
  - ruff, pyright (standard mode) and pytest;
  - the `rie` command;
  - `config.example.toml`, `AGENTS.md` and `CLAUDE.md`;
  - a `.gitignore` that keeps models, samples, photos, renders and installation settings out of Git.
- **Settings:** `config.toml` (optional, ignored by Git) names the photo folder and drive UUID, the cache and data folders, RawTherapee's command, settings folder, base profiles and timeout, the preview and render sizes and JPEG options, the analysis workers, the free-space warning, the server's address, and whether Claude's passes run. Unknown keys and bad values are errors.
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
- **Drive detection** (`rie.drive`, ADR 0005): connected, not mounted, unplugged, wrong device or unconfigured. It is decided from `/dev/disk/by-uuid` and `/proc/self/mountinfo` without touching the photo folders.
- **Phase 0 spikes**, each with an ADR in `docs/adr/` and a script in `scripts/spikes/`: RawTherapee keys, timings and demosaicing; NEF metadata; faces, eyes and smiles; Python version; drive detection; Claude Code headless with a throwaway MCP server.
- **Models:** `models/manifest.toml` lists YuNet and the MediaPipe Face Landmarker with their sources, hashes and licenses; `scripts/fetch_models.py` downloads and checks them.

## Not built yet

- **Phase 1 checks that need the owner:**
  - culling a real 300-frame shoot end to end, against Geeqie;
  - the share decided automatically and the owner's change rate on that shoot;
  - focus recall on the golden set, which the owner has not shot or labelled yet (with the calibration-kit shots).
- **Parts of Phase 1 left for later:**
  - embeddings (DINOv2 or SigLIP) for scenes and near-duplicates across a whole shoot;
  - the pairwise burst ranker (picks are recorded for it);
  - a noise floor per ISO bucket, and depth of field from the focus distance;
  - per-scene smile weights;
  - Playwright smoke tests in the test suite (the UI was checked with a scratch script in headless Chromium).
- **Phases 2–4:** the edit agents, managed sidecars and export (Phase 2); the MCP server and Claude's passes (Phase 3); taste notes, the look lab and widening the trust dial (Phases 3–4).

## Next steps

1. **Owner:** review the branch, then cull one real shoot:

   ```bash
   uv sync
   uv run python scripts/fetch_models.py
   (cd web && npm install && npm run build)
   uv run rie open "/path/to/shoot folder"
   uv run rie serve --open
   ```

   If you rated photos in the app before ADR 0010, check in the RawTherapee GUI that a photo that got a rating-only sidecar then still looks as before (RawTherapee should apply its default profile and then the sidecar's keys).
2. **Golden set:** shoot and label it in the app (focus verdicts and burst picks), then fit the provisional thresholds of ADRs 0008 and 0009 and measure focus recall.
3. **Phase 2:** the edit agents, starting from the recipes and locked edits imported in Phase 1.

## Known limits

- **Thresholds are provisional:** every focus, grouping, ranking and sorting threshold was set on the 29 Phase 0 samples, not on the golden set (ADR 0008, ADR 0009).
- **AF points:** the 51-point layout is estimated from Nikon's statement that the points cover the 1.3× crop frame. Its spacing is not measured.
- **Faces:** the landmarker does not confirm some faces (babies in profile, a 100 mm flash close-up); those have no blink or smile scores and do not count as people in a burst.
- **Timings:** all were measured from the internal disk. On the USB hard disk, exiftool took about 39 ms per file in Phase 0, and every photo's analysis reads its whole file (about 30 MB).
- **Sidecars from earlier builds:** before ADR 0010, rating a photo in the app wrote `[General] Rank` into its sidecar, or created a sidecar holding only the rating. Those files stay as they are, and the RawTherapee GUI's handling of a rating-only sidecar has not been checked by eye.
- **Renders** (from Phase 0):
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
- **Drive:** detection has not been tried by unplugging the drive; unit tests cover each mount-table state.
- **MediaPipe:** 1.x runs out of memory creating the Face Landmarker here, so the project pins 0.10.35 (ADR 0003).
- **libvips:** it warns at start-up that its OpenSlide module cannot load; the warning is harmless.
