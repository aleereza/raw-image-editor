# ADR 0013: Edit agents v1 — white balance model, tone keys, agents, routing, QA

- **Status:** accepted; every threshold is provisional until the golden set exists (plan §9); measured against the owner's own edits on 2026-11-22 (`rie eval`, below). ADR 0016 tunes on half of those folders: `geometry@2` no longer acts on vertical lines alone, `rie eval` now scores what `rie edit` applies, and the old picks cannot judge the warm-light degree, since they are neutral clicks.
- **Date:** 2026-11-21 (Phase 2, milestone 1)
- **Code:** `src/rie/engine/wbmodel.py`, `src/rie/agents/`, `src/rie/edit.py`, `src/rie/edit_report.py`, `src/rie/evaluate.py`, `src/rie/render.py` (`cached_render`), `scripts/spikes/rt_tone.py`, `tests/test_agents.py`, `tests/test_edit.py`

## Context

Phase 2 (plan §10) edits everything kept after culling: geometry, white balance, exposure and tone, and colour agents, scene harmonization, confidence routing, QA on the actual renders, the technical baseline and a neutral default look. ADR 0001's rule applies: a `.pp3` key goes into the recipe compiler only after its effect is measured on renders. Measurements ran on the reference machine (Ryzen 7 3700X, 32 GB) with the 29 Phase 0 sample copies, never the photo drive.

## Findings

### RawTherapee's white balance model

A recipe's custom white balance is RawTherapee's Temperature and Green, which RawTherapee turns into raw multipliers in its own way (plan §4.6 expected a lookup table calibrated on neutral targets). The model was rebuilt and checked with a **multiplier probe**: renders with `InputProfile=(none)`, `WorkingProfile=sRGB`, no lens or CA correction, capture sharpening or highlight reconstruction are camera RGB times the multipliers through the sRGB curve, so the ratio of two renders is the ratio of their multipliers (median over unclipped pixels).

| Case (DSC_2680, outdoor) | RawTherapee vs model, R / B multipliers |
|---|---|
| Camera's white balance, as Temperature and Green from the model | 1.0000 / 1.0001 |
| Blackbody branch, 3000 and 4000 K | within 0.1 % / 0.1 % |
| Daylight branch, 4001–8000 K, green 0.9–1.1, CIE basis read at the 10 nm sample below | 1.0–1.5 % off in blue |
| the same, CIE daylight basis interpolated linearly to 5 nm | within 0.3 % / 0.3 % |
| 10-degree observer instead of 2 | +1.5 % / −3 %: wrong for 5.13 profiles |

So: blackbody up to 4000 K, CIE daylight locus above (basis interpolated to 5 nm), CIE 1931 2° colour matching functions, XYZ to linear sRGB, Green divides G, and dcraw's normalized camera matrix (`cam_rgb`, `pre_mul`) to the camera's multipliers. The inverse is RawTherapee's bisection on blue/red, then green from green/red. 0.3 % is under one mired.

**Old sidecars:** a profile from RawTherapee 5.8 (`[Version] Version=349`, no `StandardObserver` key) is read by 5.13 with the **10-degree** observer: the probe gave identical multipliers for the 5.8 profile and for `StandardObserver=TEN_DEGREES`. This model's 10-degree variant is 1.3 % / 4 % off RawTherapee's, so `rie eval` measures the owner's picks with the probe instead of modelling them. A 5.8 pick of 5000 K / 1.02 is 5013 K / 1.067 in today's 2-degree terms.

### Tone and colour keys (`scripts/spikes/rt_tone.py`, DSC_2680 and DSC_3361)

Each variant against the base render; levels are sRGB-encoded luminance of base pixels at 0.10 … 0.90 and where they went.

| Key | 0.10 | 0.20 | 0.46 | 0.75 | 0.90 | ΔL* | ΔC* | clipped |
|---|---|---|---|---|---|---|---|---|
| base | 0.10 | 0.20 | 0.46 | 0.75 | 0.90 | 0 | 0 | 1.6 % |
| `Curve` S (0.25→0.15, 0.75→0.85), `CurveMode=FilmLike` | 0.04 | 0.11 | 0.44 | 0.84 | 0.94 | −5.3 | +2.5 | 2.0 % |
| the same, `Standard` | 0.04 | 0.11 | 0.44 | 0.84 | 0.95 | −5.4 | +2.7 | 2.0 % |
| the same, `Perceptual` | 0.04 | 0.11 | 0.44 | 0.85 | 0.95 | −5.4 | −0.2 | 2.2 % |
| `Contrast=30` | 0.07 | 0.18 | 0.52 | 0.83 | 0.94 | +1.0 | +2.4 | 1.8 % |
| `HistogramMatching=true` (the camera's curve) | 0.10 | 0.27 | 0.70 | 0.91 | 0.97 | +12.5 | +3.5 | 2.3 % |
| `Compensation=1` | 0.15 | 0.29 | 0.63 | 0.97 | 0.98 | +11.8 | +2.7 | 6.9 % |
| +1 EV, `HighlightCompr=100` (threshold 0) | 0.15 | 0.28 | 0.56 | 0.81 | 0.91 | +8.0 | +2.6 | 1.4 % |
| +1 EV, `HighlightCompr=100`, `HighlightComprThreshold=80` | 0.15 | 0.29 | 0.63 | 0.87 | 0.94 | +10.8 | +3.4 | 2.2 % |
| `Shadows & Highlights` `Shadows=30` | 0.14 | 0.26 | 0.49 | 0.76 | 0.90 | +4.7 | +0.7 | 1.6 % |
| neutral@1's curve, `Perceptual` | 0.10 | 0.23 | 0.56 | 0.84 | 0.94 | +5.3 | +1.1 | 2.0 % |

- The tone curve acts on sRGB-encoded values, and a natural cubic spline through its points reproduces it within 0.012. Perceptual mode keeps chroma; FilmLike and Standard add some.
- Highlight compression with threshold 0 also darkens the mid-tones (0.63 → 0.56); threshold 80 keeps them and still cuts clipping by two thirds.
- `[Vibrance] ProtectSkins=true` cut the vibrance's chroma gain on an indoor portrait from +2.8 to +1.1 (outdoor group: +6.8 to +6.4).
- New recipe parameters, all measured above: `tone.curve`, `tone.curve_mode`, `tone.contrast`, `tone.match_camera`, `exposure.highlight_threshold`, `color.protect_skin`. The base profile is unchanged (`d7100-base@1`).

### How bright a plain render is

On the 29 samples, the camera's JPEG against RawTherapee's render with the base profile at the camera's exposure: median brightness 0.44 against 0.29 (sRGB), and the middle of 83 detected faces at L\* 49 against 33. Several indoor shots are dark in the camera's JPEG too (faces at L\* 16–27).

### The agents on the samples (`rie edit --all`, cold render cache, 4 at a time)

| | Result |
|---|---|
| Photos | 29 edited, 108 s (3.7 s per photo; as-shot render 4.3 s, agents 1.5 s, edited render 6.8 s each, medians while 4 run) |
| Need no review | 11 of 29 (38 %); the samples are hard cases chosen in Phase 0 |
| White balance | changed 21; applied 20, halfway 7, as shot 2; median shift from the camera 11 mired; 7 of 8 scenes within 100 K after harmonization (the widest 421 K, a frame whose own light changed) |
| Exposure | changed 26; applied 25, halfway 4; median +0.45 EV, range −0.35 to +2.5; renders with clipped faces 0 % |
| Colour | vibrance on 25 photos, skin protected |
| Baseline | noise reduction on 12 (effective ISO above 1000) |
| Geometry | abstained on all 29: no family shot among them has lines that agree (a synthetic grid turned by 2° measures 2.0°) |
| QA | adjusted 2 (a clipped face, neutrals); 1 still flagged (neutrals off by 4.5) |

Viewed side by side, the edited previews of an underexposed indoor portrait, a backlit garden group and a flash group look natural.

### Against the owner's own edits (`rie eval`, 2026-11-22)

The owner's RawTherapee sidecars in 33 shoot folders (2019–2026), read only: 539 photos with a custom white balance (458 sidecars from RawTherapee 5.8, 80 from 5.4, 1 from 5.13), and 309 with a turn (`[Rotation] Degree`) against as many the owner left level. White balance took 17 minutes (two probe renders per photo), straightening 14 minutes (one 2048 px render per photo), four at a time on the reference machine. The catalog had faces for one of these photos, so the skin cue hardly ran, and the folders have no Phase 1 scenes, so nothing was harmonized. The owner often pastes one white balance across a scene: 60 % of the picks share their exact values with another photo of the folder.

White balance, as the angle to the owner's white:

| | Median | Within 2.5° | 90th percentile |
|---|---|---|---|
| Camera | 2.50° | 51 % | 6.31° |
| The agent's neutral estimate | 2.33° | 54 % | 6.57° |
| The same on the 315 photos at confidence ≥ 0.8 | 1.76° (camera 2.19°) | 70 % | 4.93° |
| What `rie edit` applies (warmth kept, routing) | 2.25° | 54 % | 5.98° |

- The median meets plan §4.6's 2.5°, but the agent only matches the camera: what `rie edit` applies is closer to the owner's pick than the camera's on 34 % of the photos, the same on 51 % and more than 1° further on 15 %. Confidence ranks the misses: more than 1° worse on 13 % at confidence ≥ 0.95, on 25–28 % at 0.80–0.90.
- Misses come by shoot. In two indoor family shoots the camera is 1.6–2.4° from the owner and the agent 4–6.4°, always cooler than the owner's pick; in another its green goes 0.1–0.2 past the owner's. Under tungsten light at a party (owner 2520 K, camera 3020 K) the agent comes within 0.5–3° but is unsure, so routing keeps it near the camera.
- Sidecars from 2019 (RawTherapee 5.4): camera 5.27°, agent 2.35°. Warm light (estimate below 4200 K, 151 photos): neutral estimate 3.42°, with warmth kept 3.63°, camera 4.19°; the kept warmth lands 2.8 mired warmer than the owner's picks (median), so the degree of 0.8 is about right.

Straightening; the owner's turns have a median of 1.49° and a 90th percentile of 3.21°, and 1 % are over 8°:

| Lines found | Photos the owner turned | Median error there | Photos the owner left level |
|---|---|---|---|
| Both kinds agree | 20 | 0.28° | 1 |
| Horizontals only | 13 | 0.58° | 1 |
| Verticals only | 23 | 0.22° | 37 (median turn 1.37°) |
| None, or they disagree (abstains) | 253 | 1.55° (the owner's turn) | 270 |

- Where the agent measures a roll it agrees with the owner: the same direction on 95 %, median error 0.24°, inside plan §4.5's 0.3°. It finds lines on only 18 % of the photos the owner turned, so over all 309 the median error is 1.25° (14 % within 0.3°).
- Vertical lines alone are not enough: they lean in 37 photos the owner left level (perspective, or tilts the owner kept) against 23 the owner turned. Routing holds most of them back: of the level photos, 21 would be turned halfway (median 0.47°) and queued, 18 queued as shot; of the turned ones, 2 are applied, 32 halfway and 22 queued as shot.

## Decision

- **`rie.engine.wbmodel`** holds RawTherapee's white balance model as above.
- **Agents** are deterministic, versioned functions `propose(ctx) -> Proposal` (`agents/*.py`, `name@1`), each with confidence, a one-sentence reason, the as-shot values and its evidence:
  - **baseline@1:** noise reduction by effective ISO (ISO × 2^push): none to 1000, chroma 10 to 2000, luma 10 / chroma 15 to 4000, 20 / 25 to 8000, 30 / 35 above. Lens corrections stay in the base profile; the agent reports what lensfun has for the lens (the Tokina by its lens ID: distortion and TCA, no vignetting). No output sharpening (the export is full size) and no hot-pixel filter yet.
  - **geometry@1:** LSD segments on the as-shot render (lens-corrected), length-weighted lean histograms for horizontals (weighted to the middle row) and verticals (middle column). Both kinds agreeing within 0.5°, or plenty of one kind (one frame diagonal of agreeing line), give a roll; otherwise it abstains. Rolls under 0.2° are left, 0.2–8° corrected, larger flagged as deliberate. A turn that would cut a trusted face out of RawTherapee's AutoFill area is reduced or flagged. Portraits count lines at 0.6. GeoCalib and a horizon detector are not used yet.
  - **wb@1:** candidates from the linear raw (as shot ×2, ×3 with flash; gray-edge 1.5; shades-of-gray 1; white patch 0.8; gray-world 0.5), weighted by skin plausibility of confirmed faces (CIELAB hue 20–75°, soft), fused by weighted median in mired and log green; the fused green moves only halfway from the camera's. Confidence from candidate spread (30 mired, 0.15 log green), mixed light (3 × 3 gray-edge spread over 80 mired), skin, usable pixels and an off-locus green. Within 6 mired and 1.2 % green of the camera's, the camera's is kept. Light below 4200 K keeps some warmth: the white balance CAT16 sees with a degree of adaptation falling to 0.8 at 3200 K.
  - **exposure@1:** predicts the edited render from the as-shot render (×2^EV through the look's curve). Faces (weighted, confirmed) go into L\* 48–72, a face already inside is left; without faces, 75 % of the way to a median of 0.46. New large-area clipping above 0.3 % of the frame gets highlight compression (60 / 100 / 150, threshold 80), and above 6 % half a stop less push. Backlit faces (L\* < 38 with a bright frame) get Shadows 25. A push against the camera's exposure compensation is halved; Active D-Lighting is given back (0.3–1.3 EV). Pushes over 2 EV are applied and flagged for noise.
  - **color@1:** vibrance 0.8 × (24 − median chroma), at most 25, capped at 8 with much saturated foliage and at 5 with many near-gamut colours, always with `protect_skin`.
  - **looks:** `neutral@1` is the default: `Curve=1;0;0;0.05;0.045;0.15;0.165;0.3;0.36;0.46;0.56;0.65;0.76;0.85;0.92;1;1;` in Perceptual mode.
- **Routing** (plan §4): confidence ≥ 0.8 applied, ≥ 0.5 applied halfway (mired for temperature, a ratio for green) and queued, below 0.5 left as shot and queued; an abstaining agent queues nothing, flags always queue. Changes under 12 mired (white balance) or 0.35 EV (exposure) are applied without review. The highlight threshold is never blended.
- **Harmonization** per Phase 1 scene: white balance to the confidence-weighted median (a frame keeps its own only with confidence ≥ 0.9, more than 25 mired apart, and its camera white balance also more than 15 mired apart the same way); exposure to the median rendering gain (EV + log2 of shutter × ISO / f²), except frames with another exposure compensation.
- **QA** on the edited preview: faces clipped (over 3 % of a face's middle) → −0.3 EV and highlight compression; shadows crushed (over 8 % below Y 0.002 in a frame that isn't dark) → shadows +15; out of gamut (over 2 %) → half the vibrance; neutral surfaces (raw cells neutral under the agent's estimate) more than 4 ΔE\*ab from their intended colour → one Newton step on the white balance; a turned photo still leaning over 0.3° → the rest of the turn. One adjustment and re-render; what still fails is queued.
- **Recipe composition:** the look, then the routed proposals, then the baseline, then the owner's locked values, which always win. Every parameter has provenance (agent, confidence, reason, band; or look, QA, human).
- **Renders** go through `cached_render`, keyed by the raw's quick hash, every stacked profile's text, RawTherapee's version and the JPEG options, into `<cache>/renders/cache/`. Previews stack the fast overlay (no capture sharpening, raw CA correction or noise reduction).
- **`rie edit FOLDER`** edits what Done culling keeps (or `--all`), reading the raw files only, and writes `<cache>/edits/folder-<id>/index.html` and `edits.json`. Milestone 1 stores nothing in the catalog.
- **`rie eval FOLDER...`** compares the white balance agent with the owner's custom white balance in existing sidecars (probe-measured), and straightening with the owner's `[Rotation] Degree`.

## Consequences for the plan

- §4.6: RawTherapee's conversion is a verified model plus QA's neutral check, not a lookup table; old 5.8 sidecars use the 10-degree observer. Against 539 of the owner's picks the agent meets the "≤ 2.5° median" (2.33°) but barely beats the camera (2.50°); the owner's picks are per scene and often a little warm.
- §4.5: GeoCalib (PyTorch) and a horizon detector are not in v1; on family photos the agent mostly abstains, which keeps deliberate tilts. Against 309 of the owner's turns it meets 0.3° where it measures a roll (0.24°) but measures one on only 18 %, and vertical lines alone are wrong more often than right.
- §4.7: highlight compression needs a threshold, or it darkens everything; the neutral look's curve carries part of the brightness gap to the camera's JPEG.
- §6 speed: edited previews cost about 3.7 s of wall time per photo with 4 at a time (two renders each), not the "about 1 s per image" the plan hoped for; RawTherapee's full-frame demosaicing dominates (ADR 0001).
- §9: the exit check (≥ 70 % of kept golden-set photos need no override) is ready to run once the owner has culled and reviewed a golden set; on the hard Phase 0 samples, 38 % need no review.
