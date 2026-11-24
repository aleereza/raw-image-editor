# ADR 0015: Review, export, looks and the RawTherapee round trip

- **Status:** accepted
- **Date:** 2026-11-21 (Phase 2, milestone 2)
- **Code:** `src/rie/catalog/migrations/0002_phase2.sql`, `src/rie/review.py`, `src/rie/export.py`, `src/rie/looklab.py`, `src/rie/managed.py`, `src/rie/api/review_api.py`, `src/rie/api/jobs.py`, `src/rie/sidecars.py`, `src/rie/fileops.py` (`make_folder`), `src/rie/scan/exif.py` (`copy_metadata`), `web/review.html`, `web/src/review/`, `tests/test_review.py`, `tests/test_review_api.py`, `tests/test_managed.py`

## Context

Milestone 1 (ADR 0013) built the edit agents and `rie edit`, with results only in a report in the cache. Milestone 2 makes them the owner's working state: edits in the catalog, the review UI of plan §6 (before/after, reason chips, nudges, locks, apply to scene, open in RawTherapee), the export of §6.1 into `jpg/` under §3.5's rules, the look lab of §4.12, and the RawTherapee GUI round trip of §3.3 and decision 14.

## Decision

### Catalog (migration 0002)

- `edit_runs` (one per `rie edit` or Edit again), `proposals` (every agent's proposal per photo and run: parameters, as-shot values, what was applied, confidence, band, reason, evidence, flags), and `edits` (per photo: look, whether it needs a look and why, QA, the cached before and after previews, when the owner marked it seen).
- Recipes stay in `recipes` and `recipe_history` (migration 0001): a run stores a new version (source `edit-run:<id>`), and so does every owner action (source `human:review`).
- `overrides`: every change the owner makes to a value the app set (parameter, the app's value and source, the owner's value, the action), the style model's training data (§4.11). Locking or unlocking without a change is recorded but is not an override.
- `exports`, `looks` (versioned definitions) and `sidecars.written` (what the app last wrote into a managed sidecar).

### The owner's actions

- Nudges (`[` `]` ±0.15 EV, `,` `.` ±10 mired, `{` `}` ±0.1°), reset to as shot (per group), lock and unlock, apply a group to the whole scene, and mark seen. Every change is locked, so the next edit run keeps it (§0), and makes a new recipe version.
- The exit metric of §10, the share of edited photos without an override, is `review.no_override_share` and shows on the review page as "untouched".

### The review page

- It is a page of its own, `/review.html#folder=<id>` (a second Vite entry), linked from the folders page, so Phase 2 leaves the culling page's `App.svelte` alone.
- Photos that need a look first, then all; the edited preview with `\` (or a click) for the as-shot one; a chip per agent with its value, confidence, band colour and reason; the reasons for review and QA's findings.
- A nudge changes the preview at once with a CSS approximation (brightness, an SVG colour matrix for warmth, a turn), then the photo's real render replaces it: one photo re-renders before the request answers, a scene in a background job on the event stream. Edit again and Export run as background jobs; the job runner now takes generic tasks.

### Export

- Each kept, edited photo is rendered at full size with the full profile into a temporary folder in the cache. exiftool copies the raw file's EXIF and XMP into that copy, sets the orientation to normal (RawTherapee turns the pixels), and writes the lens name and the `[export]` artist and copyright. `copy_metadata` refuses any file outside the cache, so exiftool never writes in a photo folder.
- `rie.fileops.make_folder` creates `jpg/` (never over a file), and `create_file` writes `jpg/<name>.jpg` under a temporary name. A JPEG already there is moved to the drive's trash first and recorded in `trash_entries`. A photo whose recipe has not changed, and whose JPEG still has the hash the app wrote, is skipped.
- On two sample copies: 6028 × 4012 JPEGs, 8.5 MB on average, 8 s for both with three renders at a time (ADR 0001: 3.7–5.7 s each).

### Looks

- The look lab renders the photo with its look and one step either way on four dials: contrast (12), warmth (12 mired), saturation (10) and lift (how far the tone curve is from straight, ±0.35 of the neutral curve's). The steps halve every round.
- A favourite is saved under a name; saving the name again adds a version. Using a look for a photo or its scene sets the tone and colour parameters from it (provenance `look:<name>@<version>`, locked) and shifts the white balance by its warmth; the owner's locked values win.

### Managed sidecars and the round trip

- Export, and `E` (open in RawTherapee) on the review page, write the photo's sidecar through `rie.fileops`:
  - a new one with the base profile and the recipe;
  - one the app made, rewritten only while its hash is still the one the app wrote (otherwise `rie open` imports the owner's changes first);
  - an owner's sidecar, backed up once to `<data>/sidecar-backups/<folder>/`, then only the keys it lacks are added: the owner's keys are never removed or changed.
- `sidecars.written` keeps the text the app wrote. When a sidecar the app wrote changes, `rie open` imports as the owner's locked values only what differs from it, so the app's own values do not become "the owner's".

## Consequences for the plan

- §6: the review view is a separate page instead of a view inside the culling app; it can be folded into one app later.
- §3.3, decision 14: sidecars are written at export and when opening a photo in RawTherapee, not for every photo as soon as it is edited. RawTherapee's preferences decide whether its GUI reads sidecars.
- §6.1: no output sharpening (ADR 0013).
- Left for later phases:
  - picking a look per scene automatically and evolving looks from repeated nudges (Phase 4);
  - learning from `overrides` (Phase 4);
  - Claude's editing pass (Phase 3).
- Apply to the burst is apply to the scene.
