# Vendored code

Third-party inference code kept in the repository, because neither project is on PyPI and GeoCalib's package pins `opencv-python`, which would clash with `opencv-python-headless` (ADR 0004). Each folder carries its license. Ruff and Pyright skip this folder. The weights are not here: `models/manifest.toml` lists them, and `uv run python scripts/fetch_models.py` downloads them.

| Folder | Source | Commit | License | Used by |
|---|---|---|---|---|
| `geocalib/` | [cvg/GeoCalib](https://github.com/cvg/GeoCalib), the `geocalib` inference package | `1dedb29f94fc11b896c5f572005978aeebccd67f` | Apache-2.0 (weights: CC BY 4.0) | `rie.agents.gravity`, straightening (ADR 0017) |
| `c5/` | [mahmoudnafifi/C5](https://github.com/mahmoudnafifi/C5), `src/c5.py` and five helpers from `src/ops.py` | `dca92361d43c8e737697fb27ac6eb9aa5d640bc4` | Apache-2.0 | `rie.agents.c5`, white balance (ADR 0017) |

## Changes

The code is as published, except:

- `geocalib/`: imports are relative (`from .camera` instead of `from geocalib.camera`); `utils.py` imports torchvision only inside its optional torchvision resize backend, so the default kornia backend works without it, and its `write_image` is left out, so no vendored code can write a file (`tests/test_no_hard_deletes.py`); `__init__.py` only exports `GeoCalib` (the original also set up logging); the interactive demo and the plotting module are left out.
- `c5/network.py`: the network uses `torch.fft` for its circular convolution, because `torch.rfft` and `torch.irfft` were removed in PyTorch 1.8; `get_hist_boundary`, `vect_norm`, `get_uv_coord`, `from_coord_to_uv`, `uv_to_rgb`, `EPS` and the `numpy` import come from `src/ops.py` of the same commit, unchanged.

Changed lines are marked `# rie:`, except GeoCalib's imports.
