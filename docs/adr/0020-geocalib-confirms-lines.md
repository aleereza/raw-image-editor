# ADR 0020: GeoCalib confirming lines at a looser uncertainty threshold

- **Status:** accepted; threshold provisional until the golden set exists (plan §9)
- **Date:** 2026-11-27 (Phase 2)
- **Code:** `src/rie/agents/geometry.py` (`geometry@4`, `GEOCALIB_CONFIRM`), `tests/test_agents.py`, `tests/test_edit.py`

## Context

While reviewing a real shoot of 54 edited photos, the owner noticed an obviously tilted portrait frame that `geometry@3` left untouched (`as_shot`). Horizontal lines had measured `-3.65°` (44 % confidence, below the 50 % conservative cutoff) and GeoCalib had measured `-3.74°` — agreeing within `0.10°` — yet `geometry@3` discarded GeoCalib's confirmation because its roll uncertainty (`±2.22°`) exceeded `GEOCALIB_SURE` (`1.0°`).

ADR 0017 used `GEOCALIB_SURE = 1.0°` both for acting on GeoCalib alone (when no usable lines exist) and for letting GeoCalib confirm a cautious roll already measured from lines. When two independent estimators (OpenCV line segments and GeoCalib) agree within `0.5°`, requiring GeoCalib's standalone uncertainty threshold throws away real corroboration.

## Findings

Measured by replaying the cached 33-folder `rie eval` report from ADR 0017 (606 photos: 309 turned by the owner, 297 left level; GPU run) and inspecting the 54 edited photos of the new shoot in the catalog. No raw files were read again.

- **Under `geometry@3`, line confirmation almost never fired:** across all 606 photos of `rie eval`, 15 had `lines = "horizontals"` and 9 had `lines = "both"` below `APPLY`. None of the 15 horizontal-only photos had GeoCalib uncertainty at or below `1.0°`: scenes with only horizontal lines naturally leave GeoCalib with higher roll uncertainty (`1.1°–4.8°`) than architecture with both axes.
- **On `rie eval` with `GEOCALIB_CONFIRM = 3.0°`** (confirming `horizontals` or `both` when `abs(reading.turn - roll) <= 0.5°` and `reading.uncertainty <= 3.0°`):
  - Three turned photos move from conservative (halfway) to full application, improving every one of them: error falls from `0.14°` to `0.07°` (horizontal, `±1.86°` uncertainty), `1.13°` to `0.53°` (both, `±1.52°`), and `0.77°` to `0.52°` (both, `±1.43°`).
  - One noisy high-uncertainty match (`±4.80°`, where full application would raise error from `0.10°` to `0.74°`) stays rejected by the `3.0°` cap.
  - Zero additional level photos are turned (all four level photos with cautious line estimates had GeoCalib disagree by `0.52°–3.54°`).
- **On the 54-photo shoot:**
  - The tilted `-3.65°` frame (GeoCalib `-3.74° ± 2.22°`, `0.10°` apart) moves from `as_shot` (44 %) to `apply` (90 %, `-3.65°`).
  - One nearly level horizontal frame (`+0.07°`, GeoCalib `-0.01° ± 2.64°`) stays at `0°` (`abs(roll) < 0.2°`), now confident instead of `as_shot`.
  - Two other horizontal frames where GeoCalib disagreed (`0.98°` and `1.67°` apart) remain `as_shot` for review.

## Decision

- **`geometry@4` separates standalone and confirmation uncertainty:**
  - `GEOCALIB_SURE = 1.0°` stays unchanged when GeoCalib acts without usable lines.
  - `GEOCALIB_CONFIRM = 3.0°` applies when lines (`both` or `horizontals`) already give a cautious roll and GeoCalib agrees within `AGREE_AXES` (`0.5°`).
- **Not changed:** vertical lines alone still never act on a photo (ADR 0016), and portrait scaling (`PORTRAIT_FACTOR = 0.6`), face-crop protection and the `0.2°–8.0°` roll bounds stay as in `geometry@3`.

## Consequences

- Cautious horizontal-line and low-support two-axis rolls that GeoCalib agrees with now straighten automatically instead of staying `as_shot` or halfway.
- ADR 0016's test half is already spent (ADR 0017), so `GEOCALIB_CONFIRM` rests on the cached replay and one new shoot and stays provisional until the golden set exists.
