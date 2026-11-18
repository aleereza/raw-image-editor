# ADR 0001: Driving rawtherapee-cli 5.13 with stacked partial profiles

- **Status:** accepted. The preview choice it raised is settled in plan v0.4 (§12 row 17): culling shows the embedded JPEGs, and edited previews use a fast profile without capture sharpening.
- **Date:** 2026-11-17 (Phase 0, spike a)
- **Scripts:** `scripts/spikes/rt_keys.py`, `scripts/spikes/rt_timing.py`, `scripts/spikes/rt_demosaic.py`

## Context

Decision D1 makes RawTherapee 5.13 the render engine, driven by generated `.pp3` profiles stacked as `-p base.pp3 -p image.pp3`. Before Phase 1, the spike had to confirm that stacking works from the command line, measure render times, compare AMaZE with RCD demosaicing, and confirm the `.pp3` keys for white balance, exposure, rotation, crop, lens correction and the D7100 camera profile. Measurements ran on the reference machine (Ryzen 7 3700X, 8 cores) on read-only copies of D7100 NEFs (35 mm f/1.8G and Tokina 100 mm shots), JPEG quality 95 with 4:4:4 chroma.

## Findings

### Stacking, errors and settings

- `rawtherapee-cli -o OUT -p a.pp3 -p b.pp3 -c NEF` starts from neutral values and applies each `-p` profile in order, later keys winning. `-d` would add the default profile from the GUI preferences, and `-s` the sidecar beside the raw; the app uses neither.
- Partial profiles with `#` comment lines are accepted.
- **Unknown sections and misspelt keys are ignored silently.** A render with `[Exposure] Compensatoin=2` and a made-up section is pixel-identical to the base render. A value RawTherapee cannot parse (`Compensation=abc`) fails the whole render.
- Exit codes: 2 when the input is missing, 253 when a profile is missing or has an unreadable value (the message says "not found" in both cases), 254 when the output cannot be saved. A failed render leaves no output file.
- rawtherapee-cli reads its preferences (`options`) from `$RT_SETTINGS` and caches in `$RT_CACHE`. With only `-p` profiles, a render with an app-owned settings folder is pixel-identical to one with the owner's GUI settings. The app still points `RT_SETTINGS` at its own folder, so that nothing in the GUI's preferences can ever change a render.

### Keys confirmed against 5.13 (profile version 353)

Each key was checked by rendering with and without it and measuring the pixels (sRGB decoded to linear where it matters).

| Recipe parameter | `.pp3` keys | Measured |
|---|---|---|
| `white_balance.temperature_k`, `.tint` | `[White Balance] Setting=Custom`, `Temperature` (K), `Green` | 3500 → 5000 → 6500 K moved R/G 1.55 → 2.22 → 2.59 and B/G 0.88 → 0.57 → 0.41: a higher temperature renders warmer. Green 0.9 → 1.1 lowered R/G and B/G by about 10% each: a higher value renders greener. |
| `white_balance.as_shot` | `[White Balance] Setting=Camera` | the neutral default |
| `exposure.ev` | `[Exposure] Compensation` | +1 EV doubled the mean linear value (×1.998) |
| `exposure.highlight_compression` | `[Exposure] HighlightCompr` (0–500) | changes the render |
| `exposure.shadows` | `[Shadows & Highlights] Enabled`, `Shadows` | changes the render |
| `color.vibrance` | `[Vibrance] Enabled`, `Pastels`, `Saturated`, `PastSatTog` | changes the render |
| `color.saturation` | `[Exposure] Saturation` | changes the render |
| `geometry.rotate_deg` | `[Rotation] Degree` | positive turns the picture counter-clockwise (correlation 0.97 against a counter-clockwise reference, 0.82 clockwise). With `[Common Properties for Transformations] AutoFill=true` the frame keeps its size: the rotated image is scaled up to hide the corners. |
| `geometry.crop` | `[Crop] Enabled`, `X`, `Y`, `W`, `H`, `FixedRatio=false` | output exactly W × H. Coordinates refer to the displayed frame: EXIF orientation applied, after rotation. |
| `lens.profile`, `.distortion`, `.vignetting`, `.ca` | `[LensProfile] LcMode=lfauto` or `none`, `UseDistortion`, `UseVignette`, `UseCA` | 35 mm f/1.8G at f/1.8: corners 29% brighter (vignetting) and distortion corrected. Tokina: see below. |
| `detail.demosaic` | `[RAW Bayer] Method=amaze` or `rcd` | see below |
| `detail.capture_sharpening` | `[PostDemosaicSharpening] Enabled` (with `AutoRadius`) | changes the render |
| `detail.nr_luma`, `.nr_chroma` | `[Directional Pyramid Denoising] Enabled`, `Luma`, `CMethod`/`C2Method` (`AUT`/`AUTO` or `MAN`/`MANU`), `Chroma` | changes the render. Automatic chroma gave almost the same result as manual 15 on one ISO 640 shot; whether `AUT` really adapts is not verified. |
| output size (not a recipe parameter) | `[Resize] Enabled`, `AppliesTo=Cropped area`, `Method=Lanczos`, `DataSpecified=4`, `LongEdge`, `AllowUpscaling=false` | the long edge is exactly the requested size, for landscape and portrait shots |
| D7100 camera profile | `[Color Management] InputProfile=(cameraICC)` | pixel-identical to `InputProfile=file:/usr/share/rawtherapee/dcpprofiles/NIKON D7100.dcp`; the camera matrix `(camera)` differs by 1.3/255 on average |

The bundled `NIKON D7100.dcp` is dual-illuminant (Standard Light A and D65) with forward matrices, a 90 × 30 HueSatMap, an identity tone curve and no LookTable. `ApplyHueSatMap=true` applies its calibration; `ApplyLookTable` and `ToneCurve` have nothing to apply. That suits the natural, accurate look. The working space `Rec2020` and output `RTv4_sRGB` are 5.13's neutral defaults.

### Output

- A full-size render is 6028 × 4012 pixels: the sensor's 6036 × 4020 minus a 4-pixel demosaicing border. The camera's own JPEG is 6000 × 4000, so boxes found on the embedded JPEG need an offset of about 14 × 6 pixels to land on a render.
- The JPEG keeps a subset of the EXIF: make (`NIKON`), model (`D7100`), exposure, ISO and capture date. It drops `SubSecTimeOriginal` and the lens name (the Tokina appears as `(31)`), and bakes the orientation into the pixels.

### Lens profiles

- `lfauto` matches the AF-S DX 35mm f/1.8G, with distortion and vignetting data.
- For the Tokina it matches lensfun 0.3.4's entry calibrated on a D7100, which has distortion and TCA data but **no vignetting data**: corners stay as shot (ratio 1.003). lensfun's other Tokina entry, calibrated on a full-frame body, has vignetting data, but its name has the same words as the first, so `lfmanual` with either name gives the same result and cannot select it. The Tokina also reports focus distances of 1.3–2.4 cm (ADR 0002), which vignetting interpolation would use.

### Timings

Single process, median of two runs, base profile:

| Render | AMaZE | RCD |
|---|---|---|
| Full size | 3.7–4.0 s | 3.7–3.9 s |
| 2048 px (long edge) | 3.0–3.2 s | 3.0–3.2 s |

- **`-f` (fast export) does nothing useful in the CLI.** Its output is pixel-identical to the normal pipeline and takes the same time, with or without a resize in the profile, and with `fastexport_use_fast_pipeline` on or off. The GUI's fast-export settings (by default 900 px, fast demosaicing, camera matrix instead of the DCP) are not applied by the CLI.
- Where the 2048 px time goes (one 35 mm shot): base 3.02 s; without capture sharpening 2.28 s; without raw CA correction 2.80 s; without lens correction 2.82 s; without highlight reconstruction 2.97 s; without all four 1.73 s; with fast demosaicing as well 1.67 s. A 64 × 64 crop still takes 2.91 s: decoding, demosaicing and capture sharpening run on the whole frame whatever the output size.
- Per-image extras: Shadows & Highlights +0.3 s, chroma noise reduction +1.3 s, luma noise reduction +1.1 s more. The plan's example recipe (§3.3) renders in 5.1 s at 2048 px and 5.7 s at full size.
- Several renders at once (8 renders, AMaZE): 3.24, 2.27, 2.11 and 1.80 s per image at 2048 px with 1, 2, 3 and 4 processes; 3.88, 2.78, 2.59 and 2.28 s per image at full size.

### Demosaicing

AMaZE and RCD take the same time. On four shots with fine stripes in clothing (indoor and outdoor groups), AMaZE produced 3–8% less false colour overall (high-frequency chroma in CIELAB); in the windows where the two differed most, each won twice. At 100% the two look the same, and none of the sample shots showed real moiré.

## Decision

- Keep D1. Render with `rawtherapee-cli -q -Y -o <temporary name> -p <base> -p <image> [-p <size>] -j95 -js3 -c <NEF>`, with `RT_SETTINGS` and `RT_CACHE` pointing at the app's folder, and rename the JPEG into place when it is complete. Never pass `-d`, `-s` or `-f`. This is `rie.engine.rawtherapee.RawTherapee`.
- The base profile is `profiles/rawtherapee/d7100-base.pp3` (`d7100-base@1`): auto-matched DCP, Rec2020 working space, sRGB output, as-shot white balance, highlight reconstruction (colour propagation), lensfun auto (distortion and vignetting; TCA left to raw CA correction), raw CA correction, AMaZE, capture sharpening with automatic radius, AutoFill for rotation, and no histogram-matched tone curve.
- The recipe compiler (`rie.engine.recipe`) emits only the keys in the table and rejects unknown parameters, because RawTherapee would ignore them silently. A new key needs a measured check like the ones above, and a test.
- AMaZE stays the default; a recipe can choose RCD.

## Consequences for the plan

- **§9, preview target "2048 px in ≤ 2 s":** not met with the base profile: 3.0–3.2 s for one render, 1.8 s per image with four processes, 5 s with noise reduction. It is met only without capture sharpening, raw CA and lens correction (1.7 s). Choices for the owner: accept about 3 s and rely on the instant in-browser preview for nudges (§6), or render previews without capture sharpening (2.3 s), which differs from the export only at close zoom. The full-size target (≤ 6 s) holds: 3.7–4.0 s, 5.7 s with noise reduction.
- **§7, "evaluate the `-f` fast-export pipeline for previews":** drop it; it does nothing in the CLI.
- **§4.9, lens corrections:** the Tokina gets no vignetting correction from lensfun. Options: a custom lensfun entry, RawTherapee's manual vignetting correction, or flat-field shots.
- **§4.5, auto-crop after rotation:** RawTherapee's AutoFill already scales the rotated frame to hide the corners at the original aspect ratio, so the geometry agent's crop only has to deal with framing and faces.
- **§4.7, tone:** the base profile has no tone curve yet and renders flatter than the camera JPEG. The gentle neutral curve remains Phase 2 work.
- **§6.1, export metadata:** RawTherapee keeps only part of the EXIF. The export should copy the rest (sub-seconds, lens, artist and copyright) from the NEF into the JPEG with exiftool, writing only to the JPEG.
