# ADR 0009: Culling suggestions, the three piles and the first personal model

- **Status:** accepted; thresholds provisional (ADR 0008); ratings removed by ADR 0010 (`rules@2` drops rule 1, and the model drops the rating feature)
- **Date:** 2026-11-18 (Phase 1)
- **Code:** `src/rie/cull/` (`rules.py`, `piles.py`, `actions.py`, `model.py`), `tests/test_cull.py`

## Context

Plan §4.4 and §4.11 (decisions 18 and 19): every photo is sorted into auto-reject, auto-keep or your call, each kind of decision has a trust-dial switch that only the owner turns to automatic, every decision is logged from the first folder, and a small personal model learns from the owner's calls. Done culling moves rejects to the drive's trash (§3.5).

## Decision

### Suggestions

`rules@1` gives each photo a suggestion, a kind, a confidence and a reason, in this order:

| Order | Case | Suggestion | Kind | Confidence |
|---|---|---|---|---|
| 1 | Rated 1–5 in RawTherapee | keep | rated (the owner's own call) | 0.95 |
| 2 | Almost black (mean luminance under 3 % with 80 % crushed), mostly blown (60 % clipped) | reject | technical | 0.95, 0.9 |
| 3 | Soft, motion or shake with subject blur of at least 1.8; missed focus with subject blur 1.4 or more and a sharp background | reject | technical | 0.85 |
| 4 | In RawTherapee's trash | reject | technical | 0.5 |
| 5 | A near-duplicate of a better frame of its burst, at least 5 % behind it | reject | duplicates | 0.8 + gap, at most 0.95 |
| 6 | Closed eyes in a burst | reject | eyes | 0.75 |
| 7 | Any other frame of a burst that is not its best | reject | expression | 0.5–0.95 by how far behind |
| 8 | The biggest face cut by the frame edge | reject | composition | 0.35 |
| 9 | The best frame of a burst | keep | expression | 0.7 |
| 10 | A single frame with closed eyes | reject | eyes | 0.6 |
| 11 | A sharp single frame; any other single frame | keep | technical | 0.8; 0.4 |

The probability that the owner rejects a photo is 0.5 ± confidence / 2.

### Piles and the trust dial

- A photo goes to auto-reject when its suggestion is reject, its kind is automatic and the probability is at least 0.8; to auto-keep when its suggestion is keep, its kind is automatic and the probability is at most 0.2. Everything else is the owner's call.
- The dial starts as the plan says: technical failures and duplicates automatic, eyes, expression and composition on suggest. A good single frame is a technical keep, so it lands in auto-keep; the best frame of a burst is an expression keep and stays the owner's call.
- A photo rated in RawTherapee goes to auto-keep: that call is already the owner's.
- When a personal model is active, its probability replaces the rules' confidence. Where it points the other way from the rules, the photo is the owner's call and the reason says what each thinks.
- A kind is proposed for automatic once its suggestions matched the owner's final call at least 95 % of the time, over at least 20 photos, in the last three culled folders. Only the owner switches it.

### Decisions and Done culling

- Keep, reject, unflag, ratings, picks and quick reasons go to `decision_log` with the pile, suggestion and kind the photo had at that moment. Picking a frame keeps it and rejects the burst's other frames, except frames the owner kept; Shift keeps the others as they are.
- The final call at Done culling is the owner's decision if there is one, else the automatic pile's (accepted), else keep (an undecided photo is never trashed).
- Each reject's raw, paired camera JPEG and their RawTherapee sidecars move to the drive's trash through `rie.fileops`; every move is recorded in `trash_entries` as it happens, so a crash mid-way loses no record. Restoring brings files back without overwriting and marks the photo as kept.
- One `cull_outcomes` row per photo holds the final call, who made it, the suggestion, the model that made it, the features and the burst: the training data. Metrics per folder are the share decided automatically, how often the owner changed an automatic pile, the minutes spent (gaps between actions, at most 2 minutes each) and agreement per kind.

### The personal model

- Scikit-learn's `HistGradientBoostingClassifier` (depth 3, 150 iterations, missing values handled natively) on 33 features: focus measures and verdict, faces, eyes, gaze and smile, burst size, rank, gap and relative sharpness, duplicates, exposure, shooting settings, rating and RawTherapee's trash flag.
- It trains after each Done culling once 60 photos with at least 10 keeps and 10 rejects are culled. It is cross-validated with folds by burst, so frames of one burst are never on both sides.
- It becomes active only if, on the latest folder, its cross-validated agreement is at least that of the app's suggestions at the time and of the previous active version on the same photos. Every version stays in `cull_models` and `<paths.data>/models/` and can be rolled back.
- Features never measured in a training set are set to zero for training; scikit-learn's binning fails on a column that is entirely missing.

## Consequences for the plan

- §4.4's pairwise ranker for burst picks is not built yet; the reject model's probability already orders frames within a burst, and picks are recorded (`cull_outcomes.picked`) for when it is.
- Claude's suggestion is not a feature yet (Phase 3).
- The exit target "at least 30 % decided automatically and fewer than 5 % changed" can be measured from the first real folder; on the 29 samples, 62 % landed in automatic piles, which says nothing about agreement yet.
