# raw-image-editor

An AI-assisted RAW photo workflow for a Nikon D7100 (NEF).

The machine does the technical edits: white balance, exposure and tone, color, straightening, lens corrections, and focus/blur checks. You make the human calls: which moment, which smile, which frame. Claude, through Claude Code on your own account, suggests the best frame in each burst and helps refine the edits.

**Status:** Phase 0 (foundations and spikes) is done; Phase 1 (folder scan, analysis and culling UI) is next. [PROJECT_STATUS.md](PROJECT_STATUS.md) says what is built.

- [Implementation plan](IMPLEMENTATION_PLAN.md): architecture, agents, safety, UI, evaluation, roadmap, and the decisions so far.
- [Architecture decision records](docs/adr/README.md): what the Phase 0 spikes measured and decided.
- [Setup](docs/setup.md): requirements, models, samples, and the photo drive.

## The idea in one line

open a folder → AI culling suggestions → **you decide what to keep** → everything you keep is edited → **you skim the exceptions** → full-size JPEGs in `jpg/`

## Try it

```bash
uv sync
uv run rie check
uv run rie render /path/to/DSC_0001.NEF --recipe examples/recipe.json --preview
```

The render goes to `~/.cache/rie/renders/` unless `-o` names another file; the raw file is only read.

## Stack

Python 3.13 (uv) · RawTherapee 5.13 CLI as the render engine · rawpy/LibRaw · OpenCV / ONNX Runtime / MediaPipe · Claude Code (your account) + MCP server · FastAPI · SQLite · Svelte
