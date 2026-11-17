# raw-image-editor

An AI-assisted RAW photo workflow for a Nikon D7100 (NEF).

The machine does the technical edits: white balance, exposure and tone, color, straightening, lens corrections, and focus/blur checks. You make the human calls: which moment, which smile, which frame.

**Status:** planning. Nothing is implemented yet.

- [Implementation plan (draft v0.2)](IMPLEMENTATION_PLAN.md): architecture, agents, UI, evaluation, roadmap, review decisions and open questions.

## The idea in one line

card → import → AI pre-cull → **you pick** → agents edit → **you skim the exceptions** → export

## Planned stack

Python (uv) · rawpy/LibRaw · RawTherapee 5.13 CLI as the render engine · OpenCV / ONNX Runtime · Claude API + MCP server · FastAPI · SQLite · Svelte
