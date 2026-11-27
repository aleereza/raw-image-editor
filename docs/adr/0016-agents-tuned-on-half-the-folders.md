# ADR 0016: Tuning the agents on half of the owner's folders: geometry@2, and wb@2 not adopted

- **Status:** accepted: `geometry@2` and the new `rie eval` are adopted; `wb@2` was tuned and tested but is not adopted, so `wb@1` stays. Every threshold stays provisional until the golden set exists (plan §9).
- **Date:** 2026-11-24 (Phase 2)
- **Code:** `src/rie/agents/geometry.py`, `src/rie/evaluate.py`, `tests/test_agents.py`, `tests/test_edit.py`

## Context

ADR 0013 measured the agents against the owner's own edits in 33 shoot folders. The white balance agent met plan §4.6's median, but what `rie edit` applied was more than 1° further from the owner's pick than the camera's on 15 % of 539 custom white balances. Straightening acted on few of the photos the owner turned, and vertical lines alone suggested turns on more photos the owner left level than on photos the owner turned. The owner approved tuning both agents on half of these folders and testing them on the other half (2026-11-24). Plan decision 21 says old white balance picks only test the white balance agent; this tuning is the owner's exception to it, and in the end nothing fitted to the picks ships (below).

## Method

- **Split, by folder and blind to every result:** folders in decreasing order of their share of the white balance picks plus their share of the turns went one by one to the half with the smaller total; the half holding the largest folder tunes, so the test half is the more varied one. Tuning half: 16 folders, 234 picks (140 of them from one shoot) and 175 turns. Test half: 17 folders, 305 picks (most of the 2019 sidecars) and 134 turns.
- **Replay:** what the agents measure was read once from the drive, read only: every candidate white balance of each pick, the metadata of all 2,763 raw files, and the line segments in the as-shot render of all 1,487 photos with a sidecar. A harness replays the real agent code, scene harmonization and routing on that cache; it reproduces the first eval's rows (white balance within 0.01°, straightening exactly).
- **Scores:** for white balance, the folder-weighted mean error (each photo counts 1 / √(photos in its folder), errors capped at 10°), with leave-one-folder-out inside the tuning half; for straightening, the degrees saved per photo over turned and level photos together. Each choice was made on the tuning half; the test half was looked at once, after every choice was frozen.
- **Neutral:** the owner's picks are neutral clicks, so they measure accuracy, not the warm-light intent (plan §4.6). White balance was tuned and scored neutral, and is reported with warmth kept as well.

## Findings on the tuning half

White balance:

- The white patch, the mean of the brightest 0.5 % of unclipped blocks, is the best single estimate of the owner's clicks: median 1.87°, against the camera's 2.31°, gray-edge's 2.60° and gray-world's 3.93°. A blend of 80 % white patch and 20 % camera, in mired and log green, with the camera's weight doubled when the flash fired, scored best by leave-one-folder-out: a score of 2.04° against wb@1's 2.41° (camera 3.18°).
- The owner moves the tint more than wb@1 does, not less (its green move would need × 1.3): the green should follow the estimate fully. Damping the tint, which the first eval suggested, is not supported.
- No confidence built on how far the estimates scatter predicted the blend's misses. They scatter most where the camera is furthest off and the white patch is right, so every such confidence held back more good corrections than bad ones: 58–80 % of the photos it held back, depending on the threshold, got worse. A white patch far from both the camera and gray-world (30 mired, or 0.08 in log green) while those two agree is coloured: on the 23 photos where that held, using the camera and gray-world instead took the median from 1.95° to 1.70°.
- Reading the flash from the metadata, which the first eval did not do, takes the median of wb@1's neutral estimate from 1.93° to 1.77°. Harmonizing over scenes changes little.
- The warmth kept for warm light cannot be tuned on these picks: they are neutral clicks. ADR 0013's "the degree of 0.8 is about right" read them as a style, which plan §4.6 rules out.

Straightening:

- The owner's turns have a median of 1.38°; 9 % are under 0.5°.
- Vertical lines alone turned 1 of the 3 turned photos where they were all the agent had, and 8 of the 17 such level photos.
- Asking less of the lines (half the length, 0.8° of agreement between horizontals and verticals) brought 9 turned photos closer to the owner's turn and turned 8 more level ones: a wash. Raising the smallest turn from 0.2° changed nothing measurable.

## Results on the test half (looked at once)

White balance, neutral, harmonized over scenes and routed (camera: median 2.81°):

| | wb@1 | wb@2 |
|---|---|---|
| Median | 2.14° | 2.08° |
| Within 2.5° | 57 % | 61 % |
| Better than the camera | 38 % | 63 % |
| More than 1° worse than the camera | 11 % | 18 % |
| Over 5° | 16 % | 12 % |
| Folder-weighted mean (the tuning score) | 2.95° | 3.01° |

With warmth kept, as `rie edit` does by default, the median goes from 2.38° to 1.94°, the share better than the camera from 37 % to 62 %, and the share more than 1° worse from 14 % to 20 %. wb@2 wins where the camera is far off: in the two 2019 shoots its median is 2.50° and 2.44° against the camera's 5.65° and 6.41°. It fails badly on a few photos, where the white patch is far off but the camera and gray-world disagree too (by 23–69 mired), so the safety rule does not fire: eight photos are 8–13° off where the camera is about 1° off, all applied without review (confidence 0.81–0.95); in one small folder the median goes from 1.37° to 9.54°.

Straightening, all 583 photos with a sidecar in the test folders:

| | geometry@1 | geometry@2 |
|---|---|---|
| Photos the owner turned that the agent turns | 21 of 134 (all the same way, median error 0.54°) | 10 (all the same way, 0.45°) |
| Photos the owner left level that the agent turns | 21 of 449 (median 0.63°) | 3 (median 0.23°) |
| Median error on the turned photos | 1.37° | 1.50° |

## Decision

- **wb@2 is not adopted; wb@1 stays.** On the test half it does not improve the score it was tuned for (2.95° to 3.01°), and it is more than 1° worse than the camera on more photos (11 % to 18 %): the very failure this work set out to reduce, with a few photos far off and applied without review. The patch is kept outside the repository with the session's notes. A later round with folders not used here needs a guard against a far-off white patch that does not hold back the corrections where the camera is wrong.
- **geometry@2:** vertical lines alone are measured (the roll and the reason are in the evidence) but never turn a photo, since they also lean whenever the camera points up or down. Everything else is geometry@1's. On the test half it turns 3 level photos instead of 21, at the cost of 11 of the 21 turns it made in the owner's direction; the owner asked for this trade, and it holds out of sample.
- **`rie eval` measures what `rie edit` applies:**
  - It reads the metadata of every raw file in the folder (exiftool, read only): the flash for the white balance agent, and Phase 1's scenes, grouped by capture time, flash and light (without the look, which needs the previews).
  - Each photo's white balance is harmonized over its scene and routed, neutral (`applied_neutral`, the accuracy measure) and with warmth kept (`applied`, `rie edit`'s default), next to the neutral estimate alone (`estimate`) and the camera's.
  - Straightening is scored as routed (`applied`), with the measured roll and lines.
  - The level photos come from each folder, as many as it has turned photos and spread through it, instead of the first ones across all folders, which came from the first few folders only.
  - Rows name their folder by its place in the list given, and the report records the agent versions.

As shipped (wb@1, geometry@2), `rie eval` on all 33 folders took 20 minutes, four at a time, and matches the harness photo by photo. White balance, 539 picks in 191 scenes:

| | Median | Within 2.5° | Over 5° | Better than the camera | More than 1° worse |
|---|---|---|---|---|---|
| Camera | 2.50° | 51 % | 20 % | | |
| The neutral estimate alone | 2.20° | 57 % | 16 % | 53 % | 20 % |
| As applied, neutral | 2.04° | 60 % | 14 % | 42 % | 13 % |
| As applied, warmth kept (`rie edit`'s default) | 2.16° | 58 % | 15 % | 41 % | 15 % |

The agent is the same as in ADR 0013; the estimate's median falls from 2.33° to 2.20° because the eval now gives it the flash. Straightening turns 22 of the 309 photos the owner turned (95 % the owner's way, median error 0.48°) and 5 of the 297 level photos sampled; over all turned photos the median error is 1.44° (6 % within 0.3°). Lines the agent acts on, both kinds or horizontals alone, appear in 34 of the 309 turned photos; vertical lines alone in 25, and none or disagreeing lines in 250.

## Consequences for the plan

- §4.6: wb@1 as applied meets the 2.5° median (2.16° with warmth kept, 2.04° neutral, camera 2.50°). The owner's clicks favour the white patch, and a blend with it beat the camera where the camera was far off, but a white patch that is wrong is very wrong; that guard is the next white balance step, tested on new folders.
- §4.5: lines the agent can act on appear in 11 % of the photos the owner turned, so the 0.3° median is out of reach without GeoCalib or a horizon detector (items 2 and 3).
- The test half is spent: further tuning needs folders not used here, such as new shoots or the golden set.
