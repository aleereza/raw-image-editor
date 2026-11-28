# ADR 0017: Learned estimators: C5 for white balance, GeoCalib for straightening

- **Status:** accepted: `wb@3` (C5) and `geometry@3` (GeoCalib) replace `wb@1` and `geometry@2`; `geometry@4` loosens GeoCalib's line-confirmation uncertainty to `3.0°` (ADR 0020). Every threshold stays provisional until the golden set exists (plan §9).
- **Date:** 2026-11-25 (Phase 2)
- **Code:** `src/rie/agents/c5.py`, `src/rie/agents/gravity.py`, `src/rie/agents/wb.py`, `src/rie/agents/geometry.py`, `src/rie/edit.py`, `src/rie/evaluate.py`, `src/rie/vendor/`, `models/manifest.toml`, `tests/test_agents.py`, `tests/conftest.py`

## Context

ADR 0016 left two gaps. Straightening finds lines it can act on in 11 % of the photos the owner turned. White balance as applied meets the 2.5° median, but a few photos are far off and the classic estimates cannot tell when. The plan names GeoCalib for straightening (§4.5, item 3) and, for white balance v2, a learned estimator "trained on public datasets plus your gray-card shots" (§4.6). The owner asked whether a learned approach would do better, approved a trial on the 33 folders of `rie eval` (read only) and its downloads, judged examples of the results, and asked for both to be built into the app, with straightening applied automatically.

## The trial

On the desktop CPU, with the CPU build of PyTorch 2.15 (the GPU was not used), against the owner's edits in the 33 folders. Nothing was fitted to the owner's picks or turns: the sign of GeoCalib's roll came from the line agent's measurements, not from the owner.

- **GeoCalib** (`cvg/GeoCalib`, ECCV 2024; pinhole weights v1.0, CC BY 4.0) read the 2048 px as-shot renders of the 606 straightening photos of `rie eval` (309 the owner turned, 297 left level), once free and once with the focal length from the metadata as a prior. It takes about 1.5 s per photo.
- **C5** (Cross-Camera Convolutional Color Constancy, ICCV 2021; the released model with the gain multiplier, seven histograms of 64 bins, trained on other cameras) read the quarter-resolution linear camera RGB of the 539 white balance picks. Each photo went in with six unlabeled photos from its own folder, or from any folder (five random draws each, averaged), or alone, its own histogram filling every place.

White balance, 539 picks, angular error to the owner's pick:

| | Median | Mean | Over 5° | Better than the camera | More than 1° worse | Folder-weighted mean |
|---|---|---|---|---|---|---|
| Camera | 2.50° | 3.21° | 20 % | | | 3.20° |
| wb@1, its estimate | 2.20° | 2.84° | 16 % | 53 % | 20 % | 2.87° |
| wb@1 as applied (neutral) | 2.04° | 2.83° | 14 % | 42 % | 13 % | 2.82° |
| C5, six photos from the folder | 1.81° | 2.52° | 13 % | 60 % | 23 % | 2.62° |
| C5, six photos from any folder | 1.82° | 2.62° | 14 % | 61 % | 23 % | 2.70° |
| C5 alone | 1.89° | 2.60° | 13 % | 60 % | 23 % | 2.73° |

- C5's biggest gain is where the others fail: on the 74 picks where wb@1 as applied is more than 5° off, its median is 3.36° (wb@1 6.51°, the camera 7.22°), and 31 of them come within 2.5°.
- The room is real: the owner's picks sit a median 0.15° from the rest of their scene (many are copies).
- The spread of C5's probability map warns of its failures: on the tuning half of ADR 0016, picks with a spread of 4° or more (11) have a median error of 9.9°, against 1.1–2.8° below.

Straightening:

- GeoCalib's roll against the owner's 309 turns: median error 0.59° (doing nothing: 1.49°); within 1° on 63 %, 68 % with the focal prior. On the 224 turned photos without lines the agent can use: 0.87°.
- Its uncertainty predicts its error: an uncertainty of 0.5–1° gives a median error of 0.31°, 1–2° gives 0.59°, 2–4° gives 1.21° and more gives 1.44°.
- On the photos the owner left level, GeoCalib reads a median roll of 1.38°; where they have only vertical lines, the line agent reads 1.23° too. The owner looked at the three level photos GeoCalib was surest it should turn by 1° or more and judged its turned versions right. "Left level" in the old sidecars means "not turned", not "level", so the level photos of `rie eval` count some right turns as mistakes.

## Decision

- **wb@3 (C5):**
  - C5's estimate replaces the classic fusion. The histograms reproduce C5's own code (to 1e-15) in about 55 ms instead of a second.
  - C5 reads each photo beside up to seven photos spread through the folder (`rie edit`: the photos it edits; `rie eval`: every raw file of the folder, in capture order), using the six that are not itself; with fewer, the photo's own histogram fills the places.
  - The confidence comes from the spread of C5's probability map: 0.9 up to 2.5° (`C5_SURE`), 0.4 from 4.0° (`C5_UNSURE`, left as shot and reviewed), straight in between. wb@1's checks still lower it: mixed light, implausible skin, little usable light and an unusual green. Harmonization over scenes and the warmth kept for warm light are unchanged.
  - The classic estimate stays in the evidence, and takes over when the weights are missing.
  - It is `wb@3` because ADR 0016's `wb@2`, a white-patch blend, was tried and not adopted.
- **How the cut-offs were set:** on ADR 0016's tuning half (16 folders, 234 picks), with its replay (cached measurements, no faces, C5 beside the photos of the trial), four pairs from 2.5–4.0° to 4.0–6.0°; 2.5–4.0° had the lowest folder-weighted mean and the fewest photos made more than 1° worse than the camera. The test half (17 folders, 305 picks) was then looked at once. In the replay, "better" means by at least 0.25°:

| Replay, as applied, neutral | Median | Mean | Over 5° | Better than the camera | More than 1° worse | Folder-weighted mean |
|---|---|---|---|---|---|---|
| Tuning half, wb@1 | 1.92° | 2.54° | 11 % | 44 % | 15 % | 2.63° |
| Tuning half, wb@3 | 1.47° | 2.09° | 12 % | 61 % | 11 % | 2.46° |
| Test half, wb@1 | 2.14° | 3.06° | 16 % | 36 % | 11 % | 2.95° |
| Test half, wb@3 | 2.14° | 2.79° | 11 % | 49 % | 25 % | 2.73° |

  On the test half, wb@3's median is unchanged, but it cuts the photos more than 5° off by a third and the mean by 0.27°. It also leaves more photos noticeably worse than the camera: 75 of 305, typically 4.2° off where the camera was 1.1° off. No guard was added after seeing the test half, since that would fit it.

- **geometry@3 (GeoCalib):**
  - The lines are measured as in geometry@2.
  - Where they give no confident roll, GeoCalib's roll (with the focal length as a prior) is applied when its uncertainty is at most 1° (`GEOCALIB_SURE`), with a confidence of 0.9: applied automatically, as the owner asked. A portrait's (a face over 6 % of the frame) is 0.54: applied halfway and reviewed, since a portrait's tilt is often deliberate (plan §4.5). A face the turn would cut still reduces it.
  - A cautious roll from horizontals alone that GeoCalib confirms within 0.5° becomes confident.
  - GeoCalib runs only when the lines are not already confident, one photo at a time: on the GPU when it has 1 GiB free (`GPU_FREE_MIB`), otherwise on the CPU, where it uses every core. A GPU that runs out of memory sends it back to the CPU, and `rie edit` and `rie eval` free its GPU memory when they are done with it, so the GPU holds one model at a time.
- **Runtime:** PyTorch's CUDA 13.2 build (download.pytorch.org/whl/cu132, 539 MB, with about 2.5 GB of NVIDIA libraries from PyPI) and kornia become dependencies; without a usable GPU, the same build runs on the CPU. C5's network and GeoCalib's inference code are vendored in `src/rie/vendor/` with their licenses, because neither is on PyPI and GeoCalib's package pins `opencv-python` (ADR 0004); its README lists the commits and every change. The weights are in `models/manifest.toml`. C5 runs on PyTorch rather than ONNX Runtime (plan D5): PyTorch is there for GeoCalib anyway, and ONNX Runtime is not a dependency of the app.
- **Tests** run the classic paths unless marked `learned`; the `learned` tests run both real models and skip without the weights.

## As shipped

`rie eval` on all 33 folders took 44 minutes, four at a time (20 with `wb@1` and `geometry@2`), with no errors. White balance, 539 picks; C5 read every one:

| | Median | Mean | Within 2.5° | Over 5° | Better than the camera | More than 1° worse |
|---|---|---|---|---|---|---|
| Camera | 2.50° | 3.21° | 51 % | 20 % | | |
| wb@1 as applied, neutral | 2.04° | 2.83° | 60 % | 14 % | 42 % | 13 % |
| wb@3, its estimate | 1.76° | 2.50° | 65 % | 13 % | 62 % | 22 % |
| wb@3 as applied, neutral | 1.75° | 2.49° | 67 % | 10 % | 58 % | 19 % |
| wb@1 as applied, warmth kept (`rie edit`'s default) | 2.16° | 2.99° | 58 % | 15 % | 41 % | 15 % |
| wb@3 as applied, warmth kept | 2.00° | 2.75° | 59 % | 14 % | 54 % | 20 % |

On the test half, neutral: median 2.09° (`wb@1` 2.14°), 10 % over 5° (16 %), 54 % better than the camera (38 %) and 23 % more than 1° worse (11 %).

Straightening, 309 photos the owner turned and 297 the owner left level:

| | Turned photos acted on | Their median error | Same direction | Median error over all turned photos | Within 0.3° | Level photos turned |
|---|---|---|---|---|---|---|
| geometry@2 | 22 (7 %) | 0.48° | 95 % | 1.44° | 6 % | 5 (2 %) |
| geometry@3 | 86 (28 %) | 0.24° | 99 % | 1.04° | 20 % | 46 (15 %, median 0.93°) |

GeoCalib acted on 64 of those turned photos (median error 0.24°) and 40 of those level ones; the lines on 22 and 6. Doing nothing leaves the turned photos a median 1.49° off. On the test half, geometry@3 acts on 41 % of the turned photos, which end a median 0.60° off (geometry@2: 1.50°), and turns 23 % of the level ones.

## GeoCalib on the GPU

On the GTX 1650 (4 GB, with the desktop holding about 2 GB), the owner approved the CUDA build. GeoCalib on the 606 renders of `rie eval` takes 0.39–0.41 s per photo on the GPU against 2.1–2.4 s on the CPU at the same time, and holds at most 491 MiB of GPU memory. On the 132 photos where it is sure (uncertainty at most 1°), the GPU's turns differ from the CPU's by a median 0.013° and at most 0.074°; the 1° cut-off decides differently on at most one of the 606.

Through the app, the first photo also loads the model (about 5 s). `rie eval --agent geometry` with GeoCalib on the GPU took 9.9 minutes for the 606 photos, four at a time. It acts on the same photos as the CPU run but one, and its turns differ from that run's by at most 0.22°; the scores are unchanged (median error where it acts 0.25°, 15 % of the level photos turned).

## Consequences for the plan

- §4.6: the learned estimator of "v2" is C5, which needs no training for the D7100. Gray-card shots and the golden set become test data, not training data. Decision 21 holds: the old picks trained nothing, and two thresholds were set on half the folders, as in ADR 0016.
- §4.5: GeoCalib (item 3) is in, and applied automatically when it is sure. The horizon detector (item 2) is not needed for now. Old sidecars cannot measure "≤ 2 % of deliberate tilts corrected", since photos left as shot were not always level.
- D5: PyTorch runs two models: C5 on the CPU, GeoCalib on the GPU when there is room.
- `rie edit` takes about 0.4 s more per photo on which the lines are not confident (2 s without the GPU).
- ADR 0016's test half has now been looked at twice; further tuning needs new folders or the golden set.
