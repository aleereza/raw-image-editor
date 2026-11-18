"""Integration tests: real rawtherapee-cli renders of a real D7100 NEF (skipped when either is missing)."""

import copy
import hashlib
import json
import shutil
import subprocess

import numpy as np
import pytest
from PIL import Image

from rie import cli, config
from tests.conftest import sample_nef
from tests.test_recipe import PLAN_EXAMPLE

NEF = sample_nef()
pytestmark = [
    pytest.mark.rawtherapee,
    pytest.mark.skipif(shutil.which("rawtherapee-cli") is None, reason="rawtherapee-cli not installed"),
    pytest.mark.skipif(NEF is None or not NEF.is_file(), reason="no sample NEF (set RIE_SAMPLE_NEF)"),
]


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(f'[paths]\ncache = "{tmp_path / "cache"}"\n', encoding="utf-8")
    return path


def render(tmp_path, config_file, recipe, *extra):
    recipe_path = tmp_path / "recipe.json"
    recipe_path.write_text(json.dumps(recipe), encoding="utf-8")
    out = tmp_path / f"out-{len(list(tmp_path.glob('out-*')))}.jpg"
    assert NEF is not None
    assert (
        cli.main(
            ["--config", str(config_file), "render", str(NEF), "--recipe", str(recipe_path), "-o", str(out), *extra]
        )
        == 0
    )
    return out


def no_crop(recipe):
    recipe = copy.deepcopy(recipe)
    recipe["params"]["geometry"].pop("crop")
    return recipe


def test_plan_example_renders_a_jpeg_with_camera_metadata(tmp_path, config_file):
    assert NEF is not None
    before = hashlib.sha256(NEF.read_bytes()).hexdigest()
    listing = sorted(NEF.parent.iterdir())
    out = render(tmp_path, config_file, no_crop(PLAN_EXAMPLE), "--size", "1024")
    with Image.open(out) as im:
        assert im.format == "JPEG"
        assert max(im.size) == 1024
        exif = im.getexif()
        assert (exif.get(0x010F), exif.get(0x0110)) == ("NIKON", "D7100")  # Make and Model, from the raw
    assert hashlib.sha256(NEF.read_bytes()).hexdigest() == before
    assert sorted(NEF.parent.iterdir()) == listing  # nothing written beside the original


def test_exposure_and_white_balance_move_the_pixels_the_right_way(tmp_path, config_file):
    base = {"schema": "rie.recipe/1", "params": {"white_balance": {"temperature_k": 5000, "tint": 1.0}}}
    brighter = copy.deepcopy(base)
    brighter["params"]["exposure"] = {"ev": 1.0}
    warmer = copy.deepcopy(base)
    warmer["params"]["white_balance"]["temperature_k"] = 6500

    def mean_rgb(path):
        with Image.open(path) as im:
            return np.asarray(im.convert("RGB"), dtype=np.float64).reshape(-1, 3).mean(axis=0)

    ref = mean_rgb(render(tmp_path, config_file, base, "--size", "512"))
    up = mean_rgb(render(tmp_path, config_file, brighter, "--size", "512"))
    warm = mean_rgb(render(tmp_path, config_file, warmer, "--size", "512"))
    assert up.mean() > ref.mean() * 1.15
    assert warm[0] / warm[2] > ref[0] / ref[2]  # a higher temperature renders warmer (red over blue)


def test_crop_sets_the_output_size(tmp_path, config_file):
    recipe = {
        "schema": "rie.recipe/1",
        "params": {"geometry": {"rotate_deg": 1.0, "crop": {"x": 300, "y": 200, "w": 1200, "h": 800}}},
    }
    with Image.open(render(tmp_path, config_file, recipe)) as im:
        assert im.size == (1200, 800)


def test_dry_run_profile_is_accepted_by_rawtherapee(tmp_path, config_file):
    """The saved per-image profile stacks on the base profile in a plain rawtherapee-cli call."""
    profile = tmp_path / "image.pp3"
    render(tmp_path, config_file, no_crop(PLAN_EXAMPLE), "--size", "256", "--profile-out", str(profile))
    base = config.PROFILES_DIR / "d7100-base.pp3"
    assert NEF is not None
    out = tmp_path / "plain.jpg"
    proc = subprocess.run(
        ["rawtherapee-cli", "-q", "-Y", "-o", str(out), "-p", str(base), "-p", str(profile), "-j80", "-c", str(NEF)],
        capture_output=True,
        text=True,
        check=False,
        env={"RT_SETTINGS": str(tmp_path / "rt"), "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out.stat().st_size > 0
