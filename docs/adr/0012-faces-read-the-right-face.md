# ADR 0012: The landmarker reads the face that belongs to the box

- **Status:** accepted
- **Date:** 2026-11-21 (Phase 1)
- **Code:** `src/rie/analysis/faces.py` (`FaceAnalyzer._landmarks`, `FaceAnalyzer._one_per_face`, `matching_face`, `same_face`; `faces@2`), `tests/test_analysis.py`

## Context

Burst compare showed a second person in a burst of one child. Its row showed the child's hands holding a wooden sign under the chin, with the child's own "eyes open" and smile. The catalog showed how it got there:

1. YuNet's whole-frame pass scored the child's bare foot as a face (0.86). The landmarker did not confirm it, and a whole-frame box stays anyway (ADR 0003).
2. Two boxes made the photo a group shot, so the tiled pass ran and found the sign and hands below the face (0.68).
3. The landmarker ran on a square crop 1.8 times the box, and any face it found there confirmed the box. The crop around the sign box held the child's real face, so the sign box got the child's irises, blink, smile and head pose.
4. The sign box overlapped the face by IoU 0.055 and lay 11 % inside it, under the 0.3 and 0.6 limits, so it stayed, started a track of its own, and compare gave it a row.

## Findings

Measured on the reference machine (Ryzen 7 3700X, CPU only), on one 376-photo family shoot and on the 29 Phase 0 samples. The shoot's photos were not read again: the measurements use its catalog and the app's cached 2048 px copies, enlarged to the analysis size of 3000 px where the tiled pass matters. At that size the old code reproduces the second person exactly. Boxes were drawn on downscaled previews and checked by eye; nothing about who people are was computed.

- In the shoot, 11 photos had two confirmed boxes whose irises lay within 2 % of the frame of each other:
  - 5 were tiled boxes beside a real face, mostly on hands or an object at a child's mouth;
  - 5 were an adult holding a child cheek to cheek: both boxes were real faces, and the adult's box carried the child's eyes, blink and smile, so the adult's own blinks were never measured;
  - 1 was unclear: two small faces close together in a crowd.
- Checking that the face found lies in the box (both irises inside the box grown 1.15 times) resolves most of these. Where a neighbour's irises fall just inside the box, the check that two boxes never read one face resolves the rest. A size check (the face's outline against the box) does not separate: a face found on hands overlapped its box by IoU 0.33, a baby held in profile (a sample) by 0.27. It is not used.
- The landmarker finds one face per crop. Allowed three (`num_faces=3`), its first face is the one-face answer whenever there is one (689 of 689 boxes). In the adults' crops its second face was the adult's own (5 of 5, outline IoU 0.78–0.88 with the box).
- Allowed three, it also answers where one face gives nothing: 30 of the shoot's 1,142 boxes, 18 of them with both irises inside the box. Of six checked by eye, four were a child's foot, two pumpkins and a shelf ornament, and two were faces in a crowd. Their outlines fit their boxes as well as real faces do, so no measure here tells them apart. Only a box whose first face belongs to a neighbour gets the second look.
- Turning a tilted crop upright by YuNet's eye angle confirmed no further face.
- In the samples, a close-up with a spoon at the mouth had two whole-frame boxes with IoU 0.298, just under YuNet's own NMS limit of 0.3, both read as the same face.
- On the whole shoot at 3000 px, against the old code:
  - the burst's compare rows went from 2 to 1;
  - pairs of boxes reading one face went from 13 to 0, and the adults cheek to cheek kept their own blink (0.17 and 0.11, against the child's 0.02 and 0.01 before);
  - boxes went from 1,148 to 1,141 and faces read from 699 to 691: the boxes beside a face and the second boxes on one face went, no whole-frame box that was unread before is read now, and one whole-frame box lost a face that lay outside it;
  - compare rows for faces seen in only one frame went from 57 to 51, in 25 bursts instead of 29, and the people the ranking counts stayed at 138.
- Time per photo: 0.312 s before and 0.316 s after on the shoot, 0.293 s and 0.283 s on the samples, with both versions timed alternately in one process. The second look runs for few boxes, and a close-up boxed twice no longer starts the tiled pass.

## Decision

- The first look is as before: the landmarker finds one face in the box's crop. The face counts only when both irises lie inside the box grown by `EYES_INSIDE` (1.15). When it lies outside, it is a neighbour's, and a second look that may find up to three faces (`CROP_FACES`) takes the one whose irises lie inside the box, nearest the detector's own eye points. Otherwise the box stays unconfirmed: a whole-frame box is kept without eyes or expression, and a tiled box is dropped, as before. A box where the first look finds no face gets no second look.
- Two confirmed boxes whose irises lie within `SAME_FACE` (a quarter of the eye distance) of each other read one face. The box whose own eye points sit farther from those irises (then the lower score) takes the second look for another face inside it; with none, it is a second box on the same face and is dropped. This runs after the whole-frame and close-up passes, before the group-shot check, and again after the tiled pass.
- Each analysis worker holds a second landmarker for the second look, about 40 MB more.
- The face step's version becomes `faces@2`, so every photo is analysed again when its folder is next opened.

## Consequences

- Burst compare loses the rows for boxes beside a face, and an adult and a child cheek to cheek each get their own blink and smile.
- Not changed, recorded for later decisions:
  - A false whole-frame box (a foot, a toy, decorations) still counts towards a group shot and starts the tiled pass. In the shoot, 432 of 1,122 whole-frame boxes were unconfirmed: such false boxes, and real faces the landmarker cannot read (a child in profile, lying sideways or looking down; an adult in profile or under a cap).
  - The landmarker sometimes finds a face on hands inside a box, with both irises inside it; such a box stays confirmed.
  - Faces are followed through a burst by box overlap (IoU 0.25 between frames), so a person who moves more than about half a face width becomes a new person, in compare and in the ranking's "faces hidden" count: the shoot had 38 likely splits in 77 bursts.
