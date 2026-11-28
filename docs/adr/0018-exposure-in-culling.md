# ADR 0018: Exposure in culling

- **Status:** accepted; thresholds provisional (one shoot and the samples)
- **Date:** 2026-11-27 (Phase 1)
- **Code:** `src/rie/analysis/exposure.py`, `src/rie/scan/previews.py` (`look@2`), `src/rie/pipeline.py`, `src/rie/cull/rules.py` (`rules@3`), `src/rie/analysis/burst.py` (`burst@3`), `src/rie/cull/model.py`, `src/rie/cull/actions.py` and `web/src/` (the exposure reason), `tests/`

## Context

While culling a family shoot of 376 photos, the owner found very under-exposed photos that the app had kept on its own.

Exposure had two rules, both meant for blank frames. "Almost black" needed a mean brightness under 3 % with 80 % of the pixels crushed; "mostly blown" needed 60 % of the pixels clipped. A dark but readable frame passed both, so its focus decided: sharp eyes made it a technical keep, and technical keeps go to Auto-keep. The burst ranking had no penalty for darkness, although plan §4.4 asks for one. When the owner overruled the app on such a photo, the question bar offered no reason that fit.

## Findings

Measured on the reference machine (Ryzen 7 3700X, 32 GB) from three sources: the app's cached grid thumbnails (512 px copies of the camera's JPEG), the 29 samples, and read-only copies of five NEFs from the shoot. Renders used RawTherapee 5.13. Nothing about who people are was computed.

- **Five failed flash frames:** the five photos were shot in manual mode with a remote flash. Three came 0.4–0.7 s after the previous flash frame, before the flash had recharged; the other two were a session's first frame.
  - Each dark frame looked unlike its neighbours, so grouping split it from its burst. The app then judged it as a sharp single shot and kept it on its own.
  - The owner rejected all five.
- **What separates them:** luminance here is computed in linear light and encoded as sRGB (0–1).
  - The brightest 1 % of these frames lies at 0.12–0.24, and their median at 0.022–0.085.
  - Every other photo of the shoot has its brightest 1 % at 0.47 or more, 2.0 EV brighter, and its median at 0.093 or more.
  - A normal photo, even a dim indoor one, has something near white, and a low-key scene keeps its lamp or window. The median alone does not tell these apart from a failed exposure.
- **The raw files:** the five frames are 2.9–4.6 EV under.
  - The measure is log2 of 18 % of the white level over the raw green median. It is about 1.4 on the well-exposed samples and 4.3–6.0 on the five.
  - The camera's tone curve lifts the darkest tones of its JPEG. log2(0.18 / linear JPEG median) minus 2.1 EV matches the raw within 0.5 EV on the five frames and on sample DSC_3048.
- **The samples:**
  - DSC_3048, labelled underexposed in Phase 0, has its brightest 1 % at 0.32 and its median at 0.07; it is about 3 EV under.
  - DSC_3027, dim but readable (ISO 1000), measures 0.36 and 0.17.
  - For frames that don't clip, the camera's JPEG keeps 1.2–2.1 EV less headroom than the raw.
- **What editing gives:**
  - Rendered at the exposure agent's limit (+2.5 EV), the five are still dark.
  - Lifted fully (2.9–4.6 EV), they look like ordinary photos at small sizes, with the noise of ISO 750 (the ISO 100 frame) up to ISO 12000.
  - Three of the five have a near-twin, shot with the flash less than a second earlier.
- **Over-exposure:** the shoot has none.
  - The brightest frames are high-key (median luminance 0.65–0.85), with at most 2.6 % clipped.
  - The most clipped frames (16–23 %) are saturated orange pumpkins and bright windows.

## Decision

- **Measures (`look@2`):** each photo's look adds the darkest 1 %, the median and the brightest 1 % of its luminance (`lum_p01`, `lum_p50`, `lum_p99`), measured on the grid thumbnail. When a folder is next opened, a photo whose previews are cached is measured again on its cached thumbnail, without reading the photo.
- **Verdicts (`rie.analysis.exposure`):**
  - A frame is very dark when its brightest 1 % is below 0.30 and its median below 0.12.
  - It is dark when they are below 0.40 and 0.15.
  - The reason gives a rough estimate of how far under the frame is, such as "very dark: about 4 EV under".
- **Rules (`rules@3`):**
  - A very dark frame is a technical reject with confidence 0.9 (a probability of 0.95). It is checked right after the almost-black and mostly-blown rules and before focus, so it lands in Auto-reject while technical failures are automatic.
  - A dark frame is a technical reject with confidence 0.4 (a probability of 0.7), checked after focus. That makes it the owner's call, because an edit may rescue it.
  - Over-exposure keeps its rule (more than 60 % blown out) until there are examples to set one on.
- **Bursts (`burst@3`):** a very dark frame keeps 0.3 of its technical score, and a dark one 0.7. The burst's reasons name it ("#2 is better: very dark").
- **Personal model:** the three measures become features, 35 in all.
- **Quick reason:** `exposure` (too dark or too bright) is added after the other five, on key 6 of the question bar. One reason covers both directions, since the photo's measures say which way it went. Nothing reads quick reasons yet: they are labels for checking these thresholds and for setting an over-exposure rule.

## Consequences

- **This shoot:** `rules@3` changes 5 of 376 suggestions. The five failed flash frames go from Auto-keep to Auto-reject, and no frame is merely dark.
- **The samples:** DSC_3048 becomes the owner's call, with a reject suggestion.
- **The thresholds** rest on one shoot. More shoots and the golden set should check them, and the exposure reasons the owner gives show where they are wrong.
- **The reason's EV figure** is rough (within 0.5 EV on six frames) and only meant for frames already judged dark.
- **Editing:** the exposure agent still lifts at most +2.5 EV, so a very dark photo the owner keeps stays dark after `rie edit`.
- **The model:** a version trained on the old features is set aside until the next Done culling.
  - The shoot's one version is set aside this way; it was trained at its Done culling and never promoted.
  - The shoot's outcomes keep the features they had, so its five rejects don't teach the model the new measures.
- **Opening a folder:** the next time each opened folder is opened, its looks are measured again (a few seconds), and the folder is then grouped and sorted again.
