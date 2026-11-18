# ADR 0003: Faces, eyes and smiles with YuNet and MediaPipe Face Landmarker

- **Status:** accepted
- **Date:** 2026-11-17 (Phase 0, spike c)
- **Script:** `scripts/spikes/faces.py`

## Context

The culling assistant (§4.4) finds faces with YuNet, run on tiles so small faces in group shots are not missed. It then runs MediaPipe Face Landmarker on each face crop for blinks (`eyeBlinkLeft/Right`), smiles (`mouthSmileLeft/Right`) and head pose. The spike ran both on 29 sample shots on the reference machine (Ryzen 7 3700X, CPU only):

- indoor f/1.8 shots of a baby;
- indoor and outdoor group shots of 3 to 9 people, and a burst of an outdoor group;
- 100 mm close-ups;
- a backlit f/1.8 portrait.

Results were checked by eye on annotated previews and on a sheet of every face crop with its scores; nothing about who people are was computed.

## Setup

- Input: the camera's full-size embedded JPEG (6000 × 4000), turned upright with the NEF's orientation.
- YuNet `face_detection_yunet_2023mar.onnx` through OpenCV 5.1's `FaceDetectorYN`, score threshold 0.6, NMS 0.3, in three ways:
  - the whole frame at 1600 px (long edge);
  - the whole frame at 2560 px;
  - tiled: a 4000 px copy cut into 1280 px tiles with 320 px overlap, plus the 1600 px pass, merged with non-maximum suppression.
- Face Landmarker (`face_landmarker.task`, float16) on a square crop of 1.8 × each tiled detection, resized to 384 px, with blendshapes and the facial transformation matrix (for yaw, pitch and roll).

## Findings

### MediaPipe version

Every MediaPipe 1.x release, from 1.0.1 to 1.2.3, ran out of memory while *creating* the Face Landmarker on this machine. It went past a 3 GB and a 5 GB cgroup limit, with the CPU delegate and with both published model files. MediaPipe 0.10.35, also a `py3-none` wheel that works on Python 3.13, creates it in about 250 MB. The spikes pin 0.10.35.

### Detection

- **Group shots:**
  - The 1600 px whole-frame pass found every face in the 9-person outdoor group (all 9 in one frame; 10 detections in two other frames of the same group).
  - It found the four adults in every frame of the indoor group (flash, 35 mm f/4). A baby held in profile was found in one of three frames at 1600 px and in the other two only by tiling.
  - In an outdoor group burst it found the three people on the sofa in every frame. A fourth person, whose face is cut by the frame edge, was found only by tiling.
  - Real faces in these group shots were 280–480 px wide at full size (75–130 px in the 1600 px pass).
- **Tiling** (0.53 s per image) added about one real face per group shot: a face at the frame edge, or a child partly hidden. It also added false positives and duplicates:
  - false positives 60–206 px wide on sunflowers, hay, foliage and patterned fabric;
  - partial-face boxes inside big close-up faces, which NMS does not merge because their overlap with the whole face is small.
- **Small faces:** all 6 detections under 120 px wide were false positives. The sample set had no real faces that small (groups of 20 or more people would).
- **Close-ups:** in 100 mm head shots where the face fills the frame, YuNet found nothing in two of four shots at any scale.

### Landmarks, blinks and smiles

- The landmarker returned landmarks for 87 of 113 tiled detections. It rejected almost every false positive and partial-face duplicate (two duplicates passed), so it doubles as a verifier for the tiled pass.
- It failed on the following real faces:
  - a face 1,560 px wide whose crop ran off the frame;
  - babies lying sideways or in profile;
  - half faces at the frame edge.
- Open eyes scored `eyeBlink` 0.00–0.20. Smiles scored 0.7–0.97 and neutral faces 0.00–0.1.
- Looking down or laughing raised `eyeBlink` to 0.3–0.68 while the eyes were open or squinting. A single blink threshold would flag those frames.
- Head yaw from the transformation matrix was plausible: about ±45° for profiles, near 0 for faces looking at the camera.

### Speed and memory (CPU)

- Decoding the embedded JPEG: 76 ms.
- YuNet: 59 ms at 1600 px, 160 ms at 2560 px, 525 ms tiled.
- Face Landmarker: 11 ms per face.
- A group shot of 10 faces takes about 0.25 s without tiling, well inside the 0.5 s per image analysis budget (§9).
- Peak memory: 1.5 GB for the whole run; YuNet alone about 0.7 GB at 2560 px.
- OpenCV 5.1's new DNN engine runs on the CPU (it ignores `setPreferableTarget`). Faces do not need the GPU.

## Decision

- Phase 1 runs YuNet on the whole frame at 1600 px for every shot.
- It adds the tiled pass for group shots (several faces found) and keeps a tiled detection only when:
  - the landmarker confirms it;
  - it does not lie mostly inside a bigger face (containment suppression).
- Close-ups (100 mm, or a face wider than a third of the frame) get an extra whole-frame pass at 640 px.
- The Face Landmarker runs once per face crop. Blink and smile thresholds are fitted on the golden set and compared within each burst (relative to the burst's best frame) rather than used as absolute cut-offs.
- MediaPipe stays pinned at 0.10.35 until a 1.x release creates the Face Landmarker without running out of memory.

## Consequences for the plan

- §4.4's tiling should not be unconditional: on its own it adds more false positives than faces. With landmark confirmation it is a useful second pass for group shots.
- "Eyes closed" needs burst-relative calibration, because laughing and looking down score like half-closed eyes.
- §7's model table says YuNet is Apache-2.0; its license is MIT. The Face Landmarker model is Apache-2.0. `models/manifest.toml` records both.
