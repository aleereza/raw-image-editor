# ADR 0014: Following people through a burst

- **Status:** accepted; thresholds provisional (ADR 0008)
- **Date:** 2026-11-21 (Phase 1)
- **Code:** `src/rie/analysis/burst.py` (`track_faces`, `face_gap`, `rank_burst`; `burst@2`), `src/rie/analysis/folder.py`, `web/src/components/Compare.svelte`, `tests/test_analysis.py`

## Context

Burst compare gives one face strip per track. The ranking counts a track as a person when the landmarker read it in at least two frames. Faces were followed from frame to frame by box overlap: a face continued a track when its box overlapped the track's last box by IoU 0.25 or more.

After ADR 0012, the shoot that had shown a phantom person still had many rows for faces seen in only one frame. Its ranking also said "2 of 4 faces hidden" in bursts of two people.

## Findings

Measured on the reference machine (Ryzen 7 3700X), on the 376-photo family shoot of ADR 0012. The data are its catalog and the `faces@2` boxes of the app's cached copies, enlarged to 3000 px. Bursts were checked by eye on downscaled sheets; nothing about who people are was computed.

- Overlap loses people who move: two boxes of one size, 0.6 of a face width apart, overlap by IoU 0.25. The shoot had 38 likely splits in its 77 bursts: a new track starting within one face width of a track that had just ended.
- A person split in two counts twice in the ranking, and each frame misses one of the halves. That gives "2 of 4 faces hidden", and the soft minimum scores each "missing" person at 0.5.
- The alternative tried: match each face to the track whose last box lies nearest, within 1.0 face size and with neither box more than twice the other's size, nearest pairs first. Distance is between box centres, each axis divided by the boxes' mean extent along it. With this rule:
  - frames saying faces were hidden went from 32 to 11;
  - rows for faces seen in only one frame went from 51 to 38;
  - the results level off between 1.0 and 1.5 face sizes (36–38 rows, 10–11 frames), while 0.75 leaves 40 rows and 21 frames;
  - no pair of people swaps left and right more than before: 2 pairs in 2 bursts, before and after;
  - a frame where the camera reframed, so that faces jump more than a face size, still starts new tracks.
- Following faces further also follows small strangers in the background, which the overlap rule lost by chance. One such face, 0.15 the width of the main faces, became a person in a burst of an adult holding the child, and it moved the app's pick. With the old rule, 4 people measured between 0.29 and 0.43 of their burst's widest person's face, and none measured below 0.25.
- With both changes, against the old code on the same faces:
  - compare shows 137 person rows instead of 189, and folds 39 other faces away;
  - the ranking counts 137 people instead of 138;
  - the app's pick changes in 4 of the 77 bursts, all where people had been split or lost between frames.

## Decision

- A face continues the track whose last box, from any earlier frame, lies nearest it. The box must lie within `TRACK_REACH` (1.0) face sizes, and the two boxes must be of about one size (`TRACK_SIZE`: at most twice as wide or as high). The nearest pairs are matched first, so a person missing from a frame keeps their track.
- A person is a track the landmarker read in at least two frames (one, for a single photo) whose median face width is at least `BACKGROUND` (a quarter) of the widest such track's in the burst.
- Each frame's burst result lists the burst's people (`person_tracks`). Burst compare shows one strip per person. The other faces the landmarker read, seen in one frame only or small in the background, fold under "Other faces". A folder grouped before this lists no people, and then compare shows every track, as before.
- The burst step's version becomes `burst@2`. A folder is grouped and sorted again when it is next opened; no photo is analysed again.

## Consequences

- Compare shows fewer rows, and fewer frames say that faces are hidden. The app's pick can change in bursts where people were split.
- The thresholds are provisional and were set on one shoot; the golden set should check them.
- A reframed frame still starts new tracks. Joining those would need the people's arrangement in the frame, never who they are; it is left open.
- From ADR 0012, two items stay open: a false whole-frame box still starts the tiled pass, and a stronger face detector has not been tried.
