# raw-image-editor

An AI-assisted RAW photo workflow for a Nikon D7100 (NEF).

The machine does the technical edits: white balance, exposure and tone, color, straightening, lens corrections, and focus/blur checks. You make the human calls: which moment, which smile, which frame. Claude, through Claude Code on your own account, suggests the best frame in each burst and helps refine the edits.

**Status:** planning. Nothing is implemented yet.

- [Implementation plan (draft v0.3)](IMPLEMENTATION_PLAN.md): architecture, agents, UI, evaluation, roadmap, and the decisions so far.

## The idea in one line

open a folder → AI culling suggestions → **you decide what to keep** → everything you keep is edited → **you skim the exceptions** → full-size JPEGs in `jpg/`

## Planned stack

Python (uv) · rawpy/LibRaw · RawTherapee 5.13 CLI as the render engine · OpenCV / ONNX Runtime · Claude Code (your account) + MCP server · FastAPI · SQLite · Svelte
