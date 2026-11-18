# ADR 0004: Python 3.13

- **Status:** accepted
- **Date:** 2026-11-17 (Phase 0, spike d)

## Context

The system Python is 3.14, and the plan (§7) pins 3.12 or 3.13 with uv because ML wheels used to lag behind. The spike checked the packages the plan needs on each version. They are rawpy, onnxruntime-gpu, mediapipe, pyvips, and torch, which GeoCalib needs; opencv, numpy, the MCP SDK, colour-science, scikit-image and kornia were checked too.

## Findings

Checked on 2026-11-17 with uv 0.13.5. Each version was resolved with wheels only (`uv pip compile --only-binary :all:` for manylinux_2_28 x86_64, pyvips excepted), then installed into a clean virtual environment and imported:

| Package | Version | Linux wheels |
|---|---|---|
| rawpy | 0.28.0 | cp311–cp315 |
| onnxruntime-gpu | 1.32.0 | cp311–cp314; built for CUDA 13 and cuDNN 9, which the `cuda` and `cudnn` extras install as wheels |
| mediapipe | 1.2.3 and 0.10.35 | `py3-none`: any Python 3 (0.10.35 is pinned, see ADR 0003) |
| pyvips | 3.2.0 | source only; builds its cffi binding against the system libvips 8.18 and runs in API mode |
| torch | 2.15.0 | cp311–cp315 |
| opencv-python-headless | 5.1.0.95 | abi3 (cp37 and later) |
| numpy | 2.5.5 | cp312 and later |
| mcp | 2.3.0 | pure Python |

- All of them resolve on 3.12, 3.13 and 3.14.
- The import smoke test behaved the same on 3.12.15, 3.13.17 and 3.14.8:
  - rawpy decoded a NEF at half size in 0.67–0.69 s;
  - ONNX Runtime listed the TensorRT, CUDA and CPU providers and loaded its CUDA libraries;
  - MediaPipe's tasks API imported;
  - pyvips ran in API mode.
- torch was resolved but not installed for 3.13; the GPU check comes with GeoCalib in Phase 2.
- GeoCalib (`cvg/GeoCalib`, requires Python 3.9 or later) is pure Python. It depends on torch, torchvision, kornia, matplotlib and `opencv-python`. Installing it next to `opencv-python-headless` gives two packages that both provide `cv2`, so it should be installed without its dependencies, or the project should standardize on `opencv-python`.
- At the time of the spike, other programs used 2.2 GB of the GTX 1650's 4 GB, so models must fit in about 1.5 GB of GPU memory or fall back to the CPU.

## Decision

Python 3.13: `.python-version` holds `3.13`, and `pyproject.toml` requires `>=3.13,<3.14`. Both candidates work today. 3.13 gets security fixes until October 2029, a year longer than 3.12.

## Consequences for the plan

- §7's reason for pinning, that ML wheels lag behind, no longer holds: the whole stack also installs on 3.14.
- 3.14 was not chosen because the plan named 3.12 or 3.13, and secondary packages (pyiqa, GeoCalib's dependencies) were not checked on it.
