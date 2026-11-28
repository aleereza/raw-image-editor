# ADR 0019: A missed focus far from a sharp AF point is the owner's call

- **Status:** accepted; the threshold is provisional (one shoot)
- **Date:** 2026-11-27 (Phase 1)
- **Code:** `src/rie/analysis/focus.py` (`af_measures`, `unclear_miss`, `AWAY`), `src/rie/cull/piles.py`, `src/rie/cull/rules.py` (`rules@4`), `src/rie/cull/model.py`, `tests/test_analysis.py`, `tests/test_cull.py`

## Context

In a family shoot of 376 photos, five setup frames of a carved-pumpkin prop were in Auto-reject as "focus missed: the background is sharper than the eyes". The camera's AF point was on the pumpkin, which is sharp. The only face the app found was a baby in a bouncer far behind it.

The focus step (ADR 0008) measures the face that holds the AF point, and when no face holds it, the biggest faces. Here that made a small face far from where the camera focused the subject. Its soft eyes then made the photo a clear technical failure, and technical failures go to Auto-reject on their own.

## Findings

Measured on the shoot's catalog: the blur maps, faces and verdicts the analysis stored. Re-running the focus step on the stored maps (float16) reproduces every stored subject and verdict. The cached 2048 px previews were checked by eye with the AF point and the faces drawn on them; the photos were not read again, and nothing about who people are was computed. The owner culled half of the shoot meanwhile, so that half has the owner's final calls.

- **The owner focuses with the centre point and reframes:** all 357 autofocus photos used one point, C6, the centre. 19 were focused by hand and record no AF point.
- **A face away from the AF point is usually the right subject:**
  - 209 photos have faces but none at the AF point. In 179 of them (86 %) the face reads sharp.
  - In those, the AF box sits on the wall behind a head, between two faces, or on a lap or chest.
  - By its size, such a face lies at the recorded focus distance as often as a face at the AF point does: the median ratio is 1.29 against 1.34, assuming a 0.14 m face.
- **The wrong subject is only the five pumpkin frames:**
  - The face is 4.5–5.2 % of the frame wide, 6.5–8.8 face sizes from the AF point, and about three times the focus distance by its size.
  - The eyes read 2.1–2.8 and the AF point 0.30–0.76, which is sharp.
- **Other missed focus away from the AF point is a question of sharpness, not of subject.** In a family on a carriage at f/1.8, the AF point is on their laps, and the faces and the AF point read soft together. A baby whose AF point sits on its chest, just under the face, is another such case.
- **Rules tried, sorting scratch copies of the catalog:**
  - The AF point as the subject whenever no face holds it: 208 photos change subject, and 12 become technical rejects, 10 of them with sharp eyes.
  - Faces narrower than 8 % of the frame counting only at the AF point: two carriage frames, whose faces are the subject, become rejects.
  - Faces more than twice the focus distance not counting: a photo with sharp eyes becomes a reject. The lens reports 0.79 m for a face about 1.6 m away.
  - The AF point as the subject when it is sharp and the faces are soft: the five, plus two carriage frames where the AF point on a baby's legs is sharp.
  - The same with a guard (a face more than 4 face sizes from the AF point, or narrower than 8 %, or more than twice the focus distance): only the five. As sharp single frames, they would go to Auto-keep, although the owner rejected all five as setup frames.
- **Margins of the guard:**
  - The five faces lie 6.5–8.8 face sizes from the AF point. The nearest other photos where the AF point is sharp and the faces soft lie 2.9–3.3 face sizes from it.
  - Because C6 is the centre, only faces narrower than about 11–15 % of the frame can lie more than 4 face sizes from it.
- **Agreement with the owner:** in the culled half, all 11 technical auto-rejects, the five included, were also the owner's final call.

## Decision

- **The AF point on its own:** when a folder is sorted, two measures come from the blur map the analysis stored, so no photo is read again and the focus step (`focus@1`) is unchanged:
  - the AF point's own blur (`af_blur`);
  - the distance from it to the nearest face box (`af_gap`), in face sizes, each axis divided by the face's extent.
- **Unclear miss (`focus.unclear_miss`):** a missed focus on the eyes or a face is unclear when the AF point is sharp (blur at most `SHARP`, 1.0) and no face lies within `AWAY` (4) face sizes of it. The camera focused on something else, on purpose or not.
- **Rules (`rules@4`):**
  - An unclear miss that `rules@3` would reject on its own is a technical reject with confidence 0.4 (a probability of 0.7), so it is the owner's call.
  - Its reason is "the AF point is sharp, but the eyes away from it are soft".
  - A missed focus beside the face, or with a soft AF point, stays a clear technical failure (0.85).
- **Personal model:** `af_blur` and `af_gap` become features, 37 in all.
- **Not changed:** the subject and the verdict, the burst ranking, and photos without an AF point (manual focus).

## Consequences

- **This shoot:**
  - The five setup frames move from Auto-reject to the owner's call, still with a reject suggestion. Nothing else changes, in either half.
  - In the culled half, automatic decisions go from 80 to 75 of 152. The app's suggestions match the owner's final calls on 86 photos, as before.
- **Other photos:**
  - A real miss also becomes the owner's call rather than an automatic reject when the camera focused on something far from a small face, such as a fence with a child behind it.
  - A back focus between two faces (the AF point on the wall between them) stays automatic, because the faces lie near the AF point.
- **Sorting:** reading and decoding the stored maps doubles the time of a sort but keeps it under a tenth of a second: 0.045 s to 0.09 s for the shoot's 376 photos on the reference machine (Ryzen 7 3700X, 32 GB). A sort runs when a folder is opened, when the trust dial changes and when a model is activated.
- **Model:** a version trained on other features is set aside until the next Done culling, as with ADR 0018. Outcomes recorded before this change lack the AF point's measures.
- **Open:**
  - **Manual focus:** without an AF point, as with the Tokina macro, a missed focus on a face is still automatic, so a macro shot with a person behind it would land in Auto-reject. Whether that should be the owner's call too, always or only for small faces, is left to the owner. This shoot's 19 manually focused photos all have sharp eyes.
  - **Composition:** the rule "a face is cut by the frame edge" looks at the largest face whether or not it is the subject. One frame of the pumpkin series got that suggestion from the same baby in the bouncer.
  - **The threshold:** `AWAY` rests on one scene. The golden set should check it, as should the owner's calls on such photos (`af_blur` and `af_gap` are recorded with each outcome).
