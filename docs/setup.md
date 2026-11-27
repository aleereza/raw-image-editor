# Setup

What the tool needs, how to install it, which models it uses, and where test samples go. [PROJECT_STATUS.md](../PROJECT_STATUS.md) says what is built; the ADRs in [adr/](adr/README.md) hold the Phase 0 measurements.

## Requirements

| Component | Use here | Tested with |
|---|---|---|
| Linux | Drive detection reads `/dev/disk/by-uuid` and `/proc/self/mountinfo` | Arch Linux |
| RawTherapee (`rawtherapee-cli`) | The render engine; its bundled `NIKON D7100.dcp` is the input profile | 5.13 |
| lensfun database | Lens corrections, looked up by RawTherapee | 0.3.4 |
| exiftool | Metadata, embedded JPEG offsets | 13.55 |
| libvips | Thumbnails (Phase 1), through pyvips | 8.18 |
| uv | Creates the Python 3.13 environment from `uv.lock` | 0.13.5 |
| Claude Code | Culling and editing passes (Phase 3), signed in with `claude auth login` | 2.1.327 |
| Node.js and npm | Building the web UI once (`web/`); not needed to run it | Node 26, npm 11 |
| NVIDIA GPU and driver (optional) | GeoCalib (PyTorch, CUDA 13.2) when 1 GiB is free, and ONNX Runtime in the spikes; everything also runs on the CPU | GTX 1650, 4 GB, driver 615 |

`uv run rie check` reports each of these and the photo drive's state.

## Python environment

`uv sync` creates `.venv` with Python 3.13 (ADR 0004) and the locked packages: NumPy, pyvips, rawpy, OpenCV (headless), MediaPipe 0.10.35, scikit-learn, FastAPI, Uvicorn, colour-science, and PyTorch 2.15 with kornia for C5 and GeoCalib. PyTorch is the CUDA 13.2 build from PyTorch's own index (`[tool.uv.sources]` in `pyproject.toml`; 539 MB, plus about 2.5 GB of NVIDIA libraries from PyPI); without a usable GPU it runs on the CPU. C5's network and GeoCalib's inference code are vendored in `src/rie/vendor/` with their licenses (ADR 0017). The dependency groups are:

- `dev`: pytest, ruff, pyright, Pillow and httpx for the tests;
- `spikes` (`uv sync --group spikes`): rawpy 0.28, numpy, OpenCV 5.1 (headless), onnxruntime-gpu 1.32 with its CUDA 13 and cuDNN 9 wheels (about 3 GB), MediaPipe 0.10.35, pyvips 3.2, Pillow and the MCP SDK 2.3.

- **MediaPipe** stays at 0.10.35: every 1.x release ran out of memory while creating the Face Landmarker (ADR 0003).
- **libvips** warns that its OpenSlide module cannot load `libopenslide.so.1`. The warning is harmless; OpenSlide is only for microscopy slides.

## The web UI

The culling UI is a Svelte 5 app in `web/` (Vite, TypeScript). Build it once, and again after changes:

```bash
cd web
npm install
npm run build
```

`rie serve` then serves `web/dist/` together with the API on http://localhost:7317 (`[server]` in `config.toml` changes the address). For work on the UI, `npm run dev` serves it with hot reload on port 5173 and sends API calls to a running `rie serve`; `npm run check` type-checks it.

## Models

`uv run python scripts/fetch_models.py` downloads the files listed in [models/manifest.toml](../models/manifest.toml) into `models/` (ignored by Git) and checks their SHA-256:

| Model | File | License |
|---|---|---|
| YuNet face detector (OpenCV Zoo, 2023-03) | `models/yunet/face_detection_yunet_2023mar.onnx` | MIT |
| MediaPipe Face Landmarker (float16) | `models/mediapipe/face_landmarker.task` | Apache-2.0 |
| C5 with the gain multiplier, seven histograms of 64 bins (white balance) | `models/c5/C5_m_7_h_64_w_G.pth` | Apache-2.0 |
| GeoCalib, pinhole weights v1.0 (straightening) | `models/geocalib/geocalib-pinhole.tar` | CC BY 4.0 |

Without the C5 or GeoCalib weights, `rie edit` falls back to the classic white balance estimate and to lines alone.

## RawTherapee

The app gives rawtherapee-cli its own settings folder (`RT_SETTINGS`, by default `~/.cache/rie/rawtherapee`), so changes to RawTherapee's GUI preferences never change the app's renders. Base profiles live in [profiles/rawtherapee/](../profiles/rawtherapee/): `d7100-base.pp3` is `d7100-base@1`. ADR 0001 lists every `.pp3` key the recipe compiler writes and how each was checked.

## Samples for spikes and tests

Spikes and the integration tests use read-only copies of NEFs in `~/.cache/rie/samples/`, never the photo drive. To make a sample set, copy 20–30 varied NEFs there and run `chmod a-w` on them. Tests marked `rawtherapee` use the first NEF there, or the file named by `RIE_SAMPLE_NEF`, and skip without one.

## The photo drive

Set `drive.uuid` in `config.toml` to the filesystem UUID from `/dev/disk/by-uuid` (`lsblk -f` lists it). The app decides whether the drive is connected from the device and the mount table only; with a systemd automount, merely touching the mount point of an unplugged drive would block for the device timeout (ADR 0005).
