# raw-image-editor

An AI-assisted RAW photo workflow for a Nikon D7100 (NEF).

The machine does the technical edits: white balance, exposure and tone, color, straightening, lens corrections, and focus/blur checks. You make the human calls: which moment, which smile, which frame. Claude, through Claude Code on your own account, suggests the best frame in each burst and helps refine the edits.

**Status:** Phase 0 (foundations and spikes) is done. Phase 1 (folder scan, analysis and the culling UI) is built on this branch and waits for a real shoot to be culled with it. [PROJECT_STATUS.md](PROJECT_STATUS.md) says what is built.

- [Implementation plan](IMPLEMENTATION_PLAN.md): architecture, agents, safety, UI, evaluation, roadmap, and the decisions so far.
- [Architecture decision records](docs/adr/README.md): what the Phase 0 spikes measured and decided.
- [Setup](docs/setup.md): requirements, models, samples, and the photo drive.

## The idea in one line

open a folder → AI culling suggestions → **you decide what to keep** → everything you keep is edited → **you skim the exceptions** → full-size JPEGs in `jpg/`

## Try it

```bash
uv sync
uv run python scripts/fetch_models.py
(cd web && npm install && npm run build)
uv run rie check
uv run rie serve --open
```

`rie serve` opens the culling UI at http://localhost:7317: pick a shoot folder from the list and press Open. Keep the command running while you cull; `Ctrl+C` stops it. Opening a folder (there or with `rie open`) reads it without writing anything, caches the camera's embedded JPEGs, analyses focus and faces, groups bursts and works out what the app may reject or keep on its own. The culling page shows every photo by scene, with each burst as one tile and a mark on every photo for what Done culling will do with it; filters narrow it to what's left to decide or what the app decided. Press `?` in the UI for a guide to its signs and keys; every key also has a button in the Actions panel on the right. Rejects are only flags until **Done culling**, which moves them to the photo drive's own trash; `rie restore FOLDER` brings them back.

`uv run rie render /path/to/DSC_0001.NEF --recipe examples/recipe.json --preview` renders one raw file with RawTherapee into `~/.cache/rie/renders/`.

## Stack

Python 3.13 (uv) · RawTherapee 5.13 CLI as the render engine · rawpy/LibRaw · OpenCV / ONNX Runtime / MediaPipe · Claude Code (your account) + MCP server · FastAPI · SQLite · Svelte
