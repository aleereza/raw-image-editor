# Agent instructions

This repository holds the code and documentation of an AI-assisted RAW photo workflow for a Nikon D7100: keyboard culling, automated technical edits, and RawTherapee 5.13 as the render engine. The photos never live here.

## Start here

1. Read `README.md`, `PROJECT_STATUS.md` and `IMPLEMENTATION_PLAN.md`. §12 of the plan lists the owner's decisions; do not reopen them. When work shows that the plan is wrong, say so in an ADR and to the owner instead of changing course silently.
2. Read the ADRs in `docs/adr/` for what Phase 0 confirmed or changed, and `docs/setup.md` for requirements and models.
3. Read `config.toml` if it exists (Git ignores it); without it, the defaults apply. `config.example.toml` documents every key.
4. Read `local/README.md` if it exists: notes about this installation, such as its photo drive and sample set. Git ignores `local/`, so searches that respect `.gitignore` skip it; open its files directly.
5. Inspect the working tree and keep the owner's uncommitted changes.

## Photos and privacy

- Treat the photo drive as read-only: never write, rename, move or delete anything in the photo folders. Opening a raw file for writing is never allowed. The only writes the plan foresees there are sidecars (ratings from Phase 1, edits from Phase 2), exports into `jpg/` (Phase 2), and moving rejects to the trash when the owner presses Done culling (Phase 1).
- Nothing is ever deleted (plan §3.5). Every file operation on the drive goes through one file-operations module, and the only removal it can do is moving a file to the drive's own trash. It refuses when that trash is not on the same drive, and it never empties the trash; only the owner does. Sidecars and exports are written under a temporary name and renamed into place. A file the app didn't create is never overwritten, except an existing RawTherapee sidecar, which is backed up to the internal disk before the app first adds to it. A re-export moves the old JPEG to the trash first. A test fails the build if any other code deletes, overwrites or renames files in the photo folders.
- Spikes and integration tests work on read-only copies in `~/.cache/rie/samples/` (set `RIE_SAMPLE_NEF` or `RIE_SAMPLES` to use others), never on the drive and never in the repository.
- Never commit photos, previews, renders, face crops, burst sheets or other derived images. Never put people's names into tracked files, tests or commit messages; shoot folder names often contain them. Use roles ("the child") and placeholders such as `/path/to/shoot/DSC_0001.NEF`.
- Nothing about who people are is computed. Claude sees only downscaled previews and burst sheets.

## Keep the repository general

- Real paths, drive UUIDs and notes about this machine go in `config.toml` and `local/`, not in tracked files.
- ADRs record Phase 0 measurements together with the hardware they ran on (a desktop CPU and a 4 GB GPU), so later measurements can be compared.

## Engine rules

- RawTherapee silently ignores `.pp3` keys it does not know. Add a key to the recipe compiler (`rie.engine.recipe`) only after measuring its effect on a render, as ADR 0001 did, and add a test.
- Run rawtherapee-cli only through `rie.engine.rawtherapee.RawTherapee`. It points `RT_SETTINGS` at the app's folder and writes through a temporary name. Never pass `-d`, `-s` or `-f`.
- A recipe names its base profile with a version (`d7100-base@1`). Change a base profile's keys only together with its version line and a new name or number.

## Models and GPU

- Weights live in `models/` (ignored by Git). `models/manifest.toml` records each file's source, SHA-256 and license, and `uv run python scripts/fetch_models.py` downloads and checks them.
- MediaPipe stays at 0.10.35 (ADR 0003). Everything must also run on the CPU, and the GPU has little free memory, so load one model at a time.

## Commands

- `uv sync` installs; add `--group spikes` for the Phase 0 spike scripts' packages (rawpy, OpenCV, ONNX Runtime, MediaPipe, pyvips, Pillow, the MCP SDK).
- `uv run rie check` checks rawtherapee-cli, the D7100 camera profile, the base profile, exiftool, Claude Code and the photo drive.
- `uv run rie render NEF --recipe RECIPE.json [--preview | --size PX] [-o OUT.jpg] [--profile-out PP3] [--dry-run]`. `examples/recipe.json` is the plan's example recipe.
- Before committing, run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright`. Tests marked `rawtherapee` render a real NEF and skip when rawtherapee-cli or a sample is missing.
- Spike scripts are in `scripts/spikes/`, each with its usage in the docstring. Their output goes outside the repository.

## Documentation

- `PROJECT_STATUS.md` tracks the code: what is built, what is not, next steps and known limits. Update it in the same change whenever commands, behaviour or limits change.
- Each decision gets an ADR in `docs/adr/NNNN-title.md`, listed in `docs/adr/README.md`.
- Write documentation in the present tense. Markdown: one paragraph per source line, no wrapping at a fixed width.

## Commits

Inspect the staged file list and diff before committing. Make sure nothing installation-specific is staged: no photos or derived images, names, real paths or UUIDs, `config.toml` or `local/`.
