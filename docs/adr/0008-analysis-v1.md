# ADR 0008: Analysis v1 — sharpness, faces, focus verdicts, grouping and burst ranking

- **Status:** accepted; every threshold is provisional until the golden set exists; the face step checks that the landmarker read the face in each box since ADR 0012 (`faces@2`)
- **Date:** 2026-11-18 (Phase 1)
- **Code:** `src/rie/analysis/` (`raw.py`, `sharpness.py`, `faces.py`, `focus.py`, `grouping.py`, `burst.py`, `run.py`, `folder.py`), `tests/test_analysis.py`

## Context

Phase 1 needs the local culling checks of plan §4.2–§4.4: is the subject sharp, are the eyes open, which frames belong together, and which frame of a burst is best. The golden set that §9 asks for before any threshold goes into the code does not exist yet; the owner still has to shoot and label it. The measurements below ran on the reference machine (Ryzen 7 3700X, CPU only) on the 29 Phase 0 sample copies.

## Findings

### Sharpness map

- Signal: the two green Bayer samples of each 2 × 2 block, averaged (3018 × 2010 for the D7100), scaled to the white level and turned upright with LibRaw's orientation. `rawpy` unpacks a NEF in 0.37 s from memory; the map takes 0.16 s more.
- Measure: per 32 × 32 tile (64 sensor pixels), the gradient energy of the square-rooted signal compared with the same after a Gaussian blur of 2 pixels. For a blurred step edge the ratio R gives the blur directly, s = 2 / sqrt(R² − 1). On synthetic step-edge patterns blurred by a known Gaussian, the estimate is within 25 % of the true sigma from 1 to 3 pixels; on dense 3-pixel texture it reads low (sigma 2 reads 0.6) but still grows with blur.
- Noise: a floor measured in the flattest tenth of the unclipped frame is subtracted. Clipped highlights first set the floor to zero (a backlit portrait read as perfectly sharp everywhere); tiles with more than 2 % of pixels at 97 % of the white level are now left out.
- Direction: the first version used plain diagonal differences, which made 45° and 135° look blurrier than the axes on every image. Steering the four directions from one pair of x and y derivatives removed that bias; a synthetic horizontal motion blur now reads as directional along 0° in more than 80 % of textured tiles.

### Faces (ADR 0003 followed)

- YuNet at 1600 px found the faces of every group shot; the tiled pass on a 3000 px copy added confirmed faces in the indoor group; the 640 px pass found the 100 mm close-ups that the 1600 px pass missed.
- Per image: 0.07–0.12 s for one face, 0.3–0.4 s for groups of 3–10 with the tiled pass, after a 0.04 s decode of the full-size JPEG at half size.
- The landmarker's iris points place eye regions for the sharpness check; it did not confirm a 100 mm face in one flash shot, which then has no blink or smile scores.

### Focus verdicts

- Subject: the face holding the AF point, else the biggest faces; else the AF point the camera used; else the sharpest detail (manual focus). Eyes are measured where the irises are.
- The D7100's 51 AF points are placed from Nikon's statement that they cover the 1.3× crop frame: 11 columns 0.067 of the frame width apart, 5 rows 0.115 of its height apart, centred. This is an estimate; an overlay on a sample confirmed the centre point and the turn for portrait frames, not the spacing.
- On the samples, eyes in sharp frames measured 0.1–0.7. In one f/1.8 family burst the faces measured 1.0–1.45 while the cart in front of them measured 0.5: three of four frames read as `missed` (front focus), the fourth as borderline `sharp`. Whether that matches the owner's eye is for the golden set to say.
- Analysis of one photo takes 0.8–1.3 s in one process; with three worker processes the 29 samples took 16.8 s including model loading, about 0.5 s per photo.

### Grouping

- Consecutive frames of one burst differed by at most 0.03 in the 8 × 8 CIELAB layout (RMS) and the D7100's tenths put them 0.2–0.6 s apart. Repeated shots of the same scene (self-timer, by hand) were 3–7 s apart and within 0.04. Different shots of one place differed by 0.06 or more.
- With the thresholds below, the samples group into the six bursts known from their source folders and 14 scenes.

## Decision

- The map, faces and verdict are computed per photo in worker processes (`analysis.workers`, 3 by default) from one read of the file, which also gives the photo's SHA-256 content hash. The catalog keeps the blur map (float16, about 15 KB), the faces and the verdict. A photo is analysed again only when its quick hash or the analysis version changes. The step checks the drive before each photo and pauses when it is gone.
- Provisional thresholds, in half-resolution pixels of blur: `SHARP` 1.0 and `SOFT` 1.8 (focus), plus a missed-focus ratio of 0.6, directional anisotropy 1.7, and shake when 55 % of textured tiles share a direction. The handheld limit is 1/(1.5 × focal length) s.
- Grouping: a burst is frames at most 0.7 s apart with the same focal length, aperture (15 %), light (1 EV) and flash, and a layout within 0.06; or look-alike frames (layout 0.04, difference hash 12 bits) up to 10 s apart. Scenes break at gaps over 120 s, flash changes, light jumps over 2.5 EV, and a layout change over 0.12 after more than 30 s. Near-duplicates are frames of one burst within 0.02 and 4 bits.
- Burst ranking: people are confirmed faces seen in at least two frames of the burst, followed by box overlap. A person's score is eyes (closed: blink at least 0.5 and 0.3 worse than their own best frame; 0.75 for a single frame) × facing (within 35° of yaw and pitch) × face sharpness relative to their sharpest frame; people combine through a soft minimum (power −3). Subject sharpness relative to the burst's sharpest frame, motion or shake, and clipped highlights scale the score, and the mean smile adds up to 0.1.
- Every threshold and weight sits in one module constant with this ADR as its source, so the golden-set fitting can replace them.

## Consequences for the plan

- §9's rule (no threshold without a golden-set number) is broken knowingly for Phase 1: culling has to work before the golden set exists. The verdicts that send photos to the auto-reject pile without asking are limited to clear cases (§4.4, ADR 0009), and the owner skims that pile.
- §4.2's embeddings (DINOv2 or SigLIP) are not used yet; the colour layout and difference hash group the samples correctly, but will not recognise one scene across a change of framing. Near-duplicates are only looked for inside bursts.
- §4.3's noise floor is per frame, not per ISO bucket, and depth of field from the focus distance is not used yet.
- The exit check "focus recall ≥ 95 % at precision ≥ 80 % on the golden set" can only be run once the owner has shot and labelled it.
