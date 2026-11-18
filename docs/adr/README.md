# Architecture decision records

One file per decision, numbered in order. Each says what was checked, what was found (with the numbers), what was decided, and what it changes in [the implementation plan](../../IMPLEMENTATION_PLAN.md). Phase 0's spikes produced the first six; Phase 1 added the rest.

| ADR | Topic | Outcome |
|---|---|---|
| [0001](0001-rawtherapee-cli.md) | rawtherapee-cli and `.pp3` keys | D1 confirmed. Every recipe key verified on renders. 2048 px previews take 3.0–3.2 s (target was 2 s). `-f` does nothing in the CLI. AMaZE kept. The Tokina gets no lensfun vignetting correction. |
| [0002](0002-nef-metadata.md) | NEF metadata and folder contents | Full-size `JpgFromRaw` in every NEF. `AFPointsInFocus` never filled. Sub-seconds in tenths. 4,205 existing RawTherapee sidecars with real edits: kept as the owner's locked edits (plan v0.4). |
| [0003](0003-faces-eyes-smiles.md) | Faces, eyes and smiles | YuNet at 1600 px finds group faces in 59 ms. Tiling only with landmark confirmation. Blink scores need burst-relative thresholds. MediaPipe pinned at 0.10.35. |
| [0004](0004-python-version.md) | Python version | 3.13; the whole stack also works on 3.12 and 3.14. |
| [0005](0005-external-drive.md) | Photo drive detection | UUID link plus mount table, never touching the mount point. |
| [0006](0006-claude-code-headless.md) | Claude Code headless and MCP | D6 confirmed. Two bursts in 19 s, about $0.05 per burst API-equivalent. |
| [0007](0007-safe-file-operations.md) | Safe file operations | The freedesktop.org trash implemented in `rie.fileops` instead of send2trash, with an explicit same-device check, no-replace renames, and static and audit-hook tests that only this module changes photo folders. |
| [0008](0008-analysis-v1.md) | Analysis v1 | Blur per tile from the raw green channel (fine against 2-pixel-blurred gradient energy), faces per ADR 0003, focus verdicts at the eyes or AF point, grouping by time, settings and a cheap colour layout, burst ranking with burst-relative blinks. All thresholds provisional until the golden set exists. |
